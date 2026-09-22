"""The TP2.0/KWP logger against the emulated ECU (brief C3, issue #20).

Everything here runs `logging/ecu_sim.py`, which answers with the **real**
KWP handlers out of `data/passat_azx_ori.bin`, on a python-can `virtual` bus
in this process.  No hardware, no SocketCAN, no ECU.

    python3 -m unittest tests.test_med9kwp -v

The whole file skips with a message if python-can is missing.
"""
from __future__ import annotations

import json
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests.common import DUMP, DumpUnchanged, REPO, requires_dump

for _p in (str(REPO / "logging"),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import can  # noqa: F401
    HAVE_CAN = True
    CAN_WHY = ""
except Exception as exc:                                     # pragma: no cover
    HAVE_CAN = False
    CAN_WHY = f"python-can is not installed ({exc}); pip install -r requirements.txt"

requires_can = unittest.skipUnless(HAVE_CAN, CAN_WHY)

if HAVE_CAN:
    from med9kwp import (
        KwpClient, NegativeResponse, Tp20Client, Tp20Params, decode_timing,
        encode_timing, key_level1, key_level2, open_link, parse_bus_spec,
    )
    from med9kwp.kwp import DdliChunk, split_around_protected
    from med9kwp import vag_formulas
    import med9log
    from ecu_sim import AnimatedRam, DEFAULT_STATICS, Med9Handlers

_CHANNEL = 0


def _next_channel(prefix: str) -> str:
    global _CHANNEL
    _CHANNEL += 1
    return f"{prefix}{_CHANNEL}"


class _SimCase(DumpUnchanged):
    """Base: one simulator on its own virtual channel per test."""

    SIM_KW: dict = {}

    def setUp(self):
        from ecu_sim import EcuSimulator
        channel = _next_channel(type(self).__name__.lower())
        self.channel = channel
        self.bus_spec = f"virtual:{channel}"
        self.sim = EcuSimulator.on_virtual_bus(channel, seed=0x12345678,
                                               **self.SIM_KW)
        self._ctx = self.sim.background()
        self._ctx.__enter__()
        self.link = open_link(parse_bus_spec(f"virtual:{channel}"))
        self.tp = Tp20Client(self.link, dest=0x01, timeout=3.0)
        self.tp.connect()
        self.kwp = KwpClient(self.tp, timeout=3.0)

    def tearDown(self):
        try:
            self.tp.disconnect()
        except Exception:
            pass
        self.link.close()
        self._ctx.__exit__(None, None, None)
        self.sim.close()
        self.assertIsNone(self.sim.error)


# ---------------------------------------------------------------------------
# pure units, no bus
# ---------------------------------------------------------------------------
@requires_can
class TestTimingAndKeys(unittest.TestCase):
    def test_timing_bytes_round_trip_the_reference_values(self):
        # the widely published example is T1 = 0x8A, T3 = 0x32
        self.assertAlmostEqual(decode_timing(0x8A), 0.100)
        self.assertAlmostEqual(decode_timing(0x32), 0.005)
        self.assertEqual(encode_timing(0.100), 0x8A)
        self.assertEqual(encode_timing(0.005), 0x32)

    def test_timing_units_are_the_four_documented_ones(self):
        self.assertAlmostEqual(decode_timing(0x01), 0.0001)
        self.assertAlmostEqual(decode_timing(0x41), 0.001)
        self.assertAlmostEqual(decode_timing(0x81), 0.01)
        self.assertAlmostEqual(decode_timing(0xC1), 0.1)

    def test_security_keys_match_kwp_md(self):
        # the values quoted in re/findings/kwp.md section 3.2
        self.assertEqual(key_level2(0x12345678), 0x123567E8)
        self.assertEqual(key_level1(0x12345678), 0xF9F07478)

    def test_protected_window_split(self):
        # 0x7F9E3C-0x7FA47F must never appear in a RequestUpload
        pieces = split_around_protected(0x7F8000, 0x10000)
        self.assertEqual(pieces, [(0x7F8000, 0x1E3C), (0x7FA480, 0xDB80)])
        for start, size in pieces:
            self.assertTrue(start + size <= 0x7F9E3C or start > 0x7FA47F)
        # entirely inside the window -> nothing readable
        self.assertEqual(split_around_protected(0x7F9E40, 0x10), [])

    def test_ddli_chunk_validation(self):
        with self.assertRaises(ValueError):
            DdliChunk(0x1000000, 1)          # not a 24-bit address
        with self.assertRaises(ValueError):
            DdliChunk(0x7F8000, 0)


@requires_can
class TestChunkPlanner(unittest.TestCase):
    def _vars(self, spec):
        return [med9log.Variable(name=f"v{i}", address=a, size=s)
                for i, (a, s) in enumerate(spec)]

    def test_adjacent_variables_merge_into_one_chunk(self):
        plan = med9log.plan_chunks(self._vars([(0x7FFB00, 4), (0x7FFB04, 2),
                                               (0x7FFB06, 2)]))
        self.assertEqual(plan.total_chunks, 1)
        self.assertEqual(plan.record_bytes, 8)
        self.assertEqual(plan.placement["v0"], (0xF0, 0))
        self.assertEqual(plan.placement["v2"], (0xF0, 6))

    def test_wave_b_session_fits_one_dynamic_id(self):
        session = med9log.load_session(
            REPO / "logging" / "sessions" / "wave_b_confirm.json")
        plan = med9log.plan_chunks(session.variables)
        self.assertEqual(plan.ids, [0xF0])
        self.assertLessEqual(plan.total_chunks, 20)
        self.assertEqual(plan.merged_gap_bytes, 0)

    def test_more_than_twenty_chunks_spill_onto_the_next_id(self):
        spec = [(0x7F8000 + 0x100 * i, 1) for i in range(23)]
        plan = med9log.plan_chunks(self._vars(spec))
        # 0xF0 takes 20, 0xF1 takes 3 (kwp.md 4.1)
        self.assertEqual(plan.ids, [0xF0, 0xF1])
        self.assertEqual(len(plan.chunks[0xF0]), 20)
        self.assertEqual(len(plan.chunks[0xF1]), 3)

    def test_small_gaps_are_bridged_to_save_a_slot(self):
        # 22 one-byte variables four bytes apart: two gaps get bridged so the
        # whole set still fits on 0xF0 alone.
        spec = [(0x7F8000 + 4 * i, 1) for i in range(22)]
        plan = med9log.plan_chunks(self._vars(spec), max_ids=1)
        self.assertEqual(plan.ids, [0xF0])
        self.assertEqual(plan.total_chunks, 20)
        self.assertEqual(plan.merged_gap_bytes, 6)

    def test_too_many_chunks_is_a_clear_refusal(self):
        # 0x100 apart: bridging would cost 255 wasted bytes per gap, so the
        # planner refuses instead of quietly building a huge record.
        spec = [(0x7F8000 + 0x100 * i, 1) for i in range(60)]
        with self.assertRaises(SystemExit) as ctx:
            med9log.plan_chunks(self._vars(spec), max_ids=2)
        self.assertIn("dynamic ids", str(ctx.exception))
        self.assertIn("Split the session file", str(ctx.exception))


@requires_can
class TestGroupArgumentParsing(unittest.TestCase):
    def test_vcds_style_zero_padded_group_numbers(self):
        # the brief and VCDS both write "001 002 106"; int(x, 0) rejects those
        self.assertEqual(med9log.parse_group("001"), 1)
        self.assertEqual(med9log.parse_group("002"), 2)
        self.assertEqual(med9log.parse_group("106"), 106)
        self.assertEqual(med9log.parse_group("0x6a"), 0x6A)


@requires_can
@requires_dump
class TestSessionFiles(unittest.TestCase):
    def test_every_session_file_loads_and_plans(self):
        folder = REPO / "logging" / "sessions"
        for path in sorted(folder.glob("*.json")):
            doc = json.loads(path.read_text())
            if "vars" not in doc:
                continue                       # ram_snapshot.json holds ranges
            with self.subTest(session=path.name):
                session = med9log.load_session(path)
                plan = med9log.plan_chunks(session.variables)
                self.assertLessEqual(plan.record_bytes, 0xFF)
                for v in session.variables:
                    self.assertTrue(0x7F8000 <= v.address <= 0x807FFF,
                                    f"{v.name} at {v.address:#x} is not in RAM")

    def test_flash1_addresses_follow_the_patch(self):
        patch = REPO / "patches" / "ff_counter" / "patch.json"
        session = med9log.load_session(
            REPO / "logging" / "sessions" / "flash1_counter.json", str(patch))
        ram = int(json.loads(patch.read_text())["build"]["ram"], 16)
        by_name = {v.name: v for v in session.variables}
        self.assertEqual(by_name["ff_ticks"].address, ram)
        self.assertEqual(by_name["ff_alive"].address, ram + 4)
        # Brief F1 (2026-09-22): +6 became the u8 ff_src_seen (1 = task set A,
        # 2 = set B, 3 = both) and ff_reserved moved to +7.
        self.assertEqual(by_name["ff_src_seen"].address, ram + 6)
        self.assertEqual(by_name["ff_src_seen"].size, 1)
        self.assertEqual(by_name["ff_reserved"].address, ram + 7)

    def test_symbols_resolve_from_symbols_csv(self):
        self.assertEqual(med9log._resolve_symbol("cnt_raster_1ms_b"),
                         (0x7FD760, 4))
        self.assertEqual(med9log._resolve_symbol("nmot_w"), (0x7FEE74, 2))

    def test_symbols_resolve_from_measuring_vars_csv(self):
        self.assertEqual(med9log._resolve_symbol("cand_mw_tmot"), (0x8021EF, 1))

    def test_an_unknown_symbol_is_a_clear_refusal(self):
        with self.assertRaises(SystemExit) as ctx:
            med9log._resolve_symbol("no_such_variable")
        self.assertIn("re/symbols.csv", str(ctx.exception))

    def test_the_raster_counters_cover_both_task_sets(self):
        """C4: only one task set is live, so both must be logged (#44)."""
        for name in ("wave_b_confirm", "flash1_counter"):
            with self.subTest(session=name):
                session = med9log.load_session(
                    REPO / "logging" / "sessions" / f"{name}.json")
                by_addr = {v.address for v in session.variables}
                self.assertTrue({0x7FD754, 0x7FD758, 0x7FD75C, 0x7FD760,
                                 0x7FD778} <= by_addr)


# ---------------------------------------------------------------------------
# the real handlers, no bus
# ---------------------------------------------------------------------------
@requires_can
@requires_dump
class TestHandlers(DumpUnchanged):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.h = Med9Handlers(str(DUMP), seed=0x12345678, animate=False)

    def setUp(self):
        self.h.power_on()

    def test_tester_present_takes_no_subfunction(self):
        """kwp.md 12.2 -- the correction that matters for the bench."""
        self.assertEqual(self.h.handle(b"\x10\x89"), [b"\x50\x89"])
        self.assertEqual(self.h.handle(b"\x3e"), [b"\x7e"])
        self.assertEqual(self.h.handle(b"\x3e\x01"), [b"\x7f\x3e\x12"])
        self.assertEqual(self.h.handle(b"\x3e\x02"), [b"\x7f\x3e\x12"])

    def test_session_gate_comes_from_the_firmware_table(self):
        # 0x21 needs session 3/4/5 (mask 0x38), 0x35 only session 4 (mask 0x10)
        self.assertEqual(self.h.handle(b"\x21\xf0")[0][:2], b"\x7f\x21")
        self.h.handle(b"\x10\x89")
        self.assertEqual(self.h.handle(b"\x35\x7f\x80\x00\x00\x00\x01\x00"),
                         [b"\x7f\x35\x33"])

    def test_dynamic_id_reads_live_ram(self):
        self.h.handle(b"\x10\x89")
        self.h.emu.write(0x7FFB00, bytes.fromhex("0000162EFC010000"))
        self.assertEqual(self.h.handle(b"\x2c\xf0\x04"), [b"\x6c\xf0"])
        self.assertEqual(self.h.handle(b"\x2c\xf0\x03\x01\x06\x7f\xfb\x00"),
                         [b"\x6c\xf0"])
        self.assertEqual(self.h.handle(b"\x21\xf0"),
                         [bytes.fromhex("61f00000162efc01")])
        # and it really is live: change RAM, read again
        self.h.emu.write(0x7FFB00, bytes.fromhex("0000162F"))
        self.assertEqual(self.h.handle(b"\x21\xf0"),
                         [bytes.fromhex("61f00000162ffc01")])

    def test_session_change_wipes_the_dynamic_ids(self):
        """kwp.md 4.1: the real kwp_sid_2C_h2 clears all ten slots."""
        self.h.handle(b"\x10\x89")
        self.h.handle(b"\x2c\xf0\x04")
        self.h.handle(b"\x2c\xf0\x03\x01\x02\x7f\xee\x74")
        self.assertEqual(self.h.handle(b"\x21\xf0")[0][:2], b"\x61\xf0")
        self.h.handle(b"\x10\x89")
        self.assertEqual(self.h.handle(b"\x21\xf0"), [b"\x7f\x21\x12"])

    def test_upload_session_needs_security(self):
        self.h.handle(b"\x10\x89")
        self.assertEqual(self.h.handle(b"\x10\x86"), [b"\x7f\x10\x33"])
        answer = self.h.handle(b"\x27\x03")[0]
        seed = int.from_bytes(answer[2:6], "big")
        self.assertEqual(seed, 0x12345678)
        self.assertEqual(
            self.h.handle(b"\x27\x04" + key_level2(seed).to_bytes(4, "big")),
            [b"\x67\x04\x34"])
        self.assertEqual(self.h.handle(b"\x10\x86"), [b"\x50\x86"])

    def test_wrong_key_defers_then_refuses(self):
        self.h.handle(b"\x10\x89")
        answer = self.h.handle(b"\x27\x03")[0]
        seed = int.from_bytes(answer[2:6], "big")
        bad = (key_level2(seed) ^ 1).to_bytes(4, "big")
        self.assertEqual(self.h.handle(b"\x27\x04" + bad),
                         [b"\x7f\x27\x78", b"\x7f\x27\x35"])
        self.assertEqual(self.h.security_state, 0)

    def test_protected_window_refusal_is_the_real_range_check(self):
        self.h.handle(b"\x10\x89")
        answer = self.h.handle(b"\x27\x03")[0]
        seed = int.from_bytes(answer[2:6], "big")
        self.h.handle(b"\x27\x04" + key_level2(seed).to_bytes(4, "big"))
        self.h.handle(b"\x10\x86")
        # one byte before the window: allowed
        self.assertEqual(self.h.handle(b"\x35\x7f\x9e\x3b\x00\x00\x00\x01"),
                         [b"\x75\x3f"])
        self.h.handle(b"\x37")
        # the first byte of the window: NRC 0x31, from kwp_upload_range_check
        self.assertEqual(self.h.handle(b"\x35\x7f\x9e\x3c\x00\x00\x00\x01"),
                         [b"\x7f\x35\x31"])
        # the last byte of the window
        self.assertEqual(self.h.handle(b"\x35\x7f\xa4\x7f\x00\x00\x00\x01"),
                         [b"\x7f\x35\x31"])
        # one byte after it: allowed again
        self.assertEqual(self.h.handle(b"\x35\x7f\xa4\x80\x00\x00\x00\x01"),
                         [b"\x75\x3f"])


@requires_can
@requires_dump
class TestGroups(DumpUnchanged):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.h = Med9Handlers(str(DUMP), seed=1, animate=False)
        cls.h.handle(b"\x10\x89")

    def _group(self, g: int):
        answer = self.h.handle(bytes([0x21, g]))[0]
        self.assertEqual(answer[:2], bytes([0x61, g]))
        body = answer[2:]
        return [tuple(body[3 * i:3 * i + 3]) for i in range(len(body) // 3)]

    def test_group_answer_carries_eight_triples(self):
        """kwp.md 12.3: 25 bytes = the group plus group+0x7F."""
        triples = self._group(1)
        self.assertEqual(len(triples), 8)

    def test_groups_above_127_are_refused(self):
        self.assertEqual(self.h.handle(b"\x21\x8c"), [b"\x7f\x21\x31"])
        self.assertEqual(self.h.handle(b"\x21\xff"), [b"\x7f\x21\x11"])

    def test_second_half_is_group_plus_0x7f(self):
        # group 130 = ids 80, 480, 0, 0 (re/findings/measuring_groups.txt);
        # id 80 is also group 001 field 2, so the two triples must be identical
        self.h.emu.write(0x8021EF, bytes([0x60]))
        g1 = self._group(1)
        g3 = self._group(3)
        self.assertEqual(g1[1], g3[4])
        self.assertEqual(g1[1][0], 0x05)

    def test_formula_05_matches_tmot_scaling(self):
        """measuring_vars.md 7.1: 0.1*A*(B-100) == 0.75*x - 48 degC."""
        for x in (0x00, 0x30, 0x40, 0x60, 0x80, 0xA0, 0xC0):
            with self.subTest(tmot_raw=x):
                self.h.emu.write(0x8021EF, bytes([x]))
                formula, a, b = self._group(1)[1]
                self.assertEqual(formula, 0x05)
                self.assertEqual(a, 0x0A)
                decoded = vag_formulas.decode(formula, a, b)
                self.assertAlmostEqual(decoded.value, 0.75 * x - 48.0, places=6)
                self.assertEqual(decoded.unit, "degC")
                self.assertEqual(decoded.tag, vag_formulas.CROSSCHECKED)

    def test_formula_05_saturates_at_143_degrees(self):
        self.h.emu.write(0x8021EF, bytes([0xFF]))
        _formula, _a, b = self._group(1)[1]
        self.assertEqual(b, 243)

    def test_formula_01_is_40_rpm_per_count(self):
        for raw, rpm in ((0, 0), (20, 800), (75, 3000)):
            with self.subTest(raw=raw):
                self.h.emu.write(0x7FCE95, bytes([raw]))
                formula, a, b = self._group(1)[0]
                self.assertEqual((formula, a), (0x01, 0xC8))
                self.assertAlmostEqual(vag_formulas.decode(formula, a, b).value,
                                       rpm)

    def test_formula_53_matches_prist_scaling(self):
        """measuring_vars.md 7.3: group 106 field 1 is prist at 0.01 bar."""
        for raw in (0, 2000, 12000, 40000):
            with self.subTest(prist_raw=raw):
                self.h.emu.write(0x8031DA, struct.pack(">H", raw))
                formula, a, b = self._group(106)[0]
                self.assertEqual(formula, 0x53)
                decoded = vag_formulas.decode(formula, a, b)
                self.assertAlmostEqual(decoded.value, raw * 0.005, places=6)
                self.assertEqual(decoded.unit, "bar")

    def test_group_231_carries_both_rail_pressures(self):
        self.h.emu.write(0x8031DA, struct.pack(">H", 12000))   # prist 60 bar
        self.h.emu.write(0x8031F4, struct.pack(">H", 12200))   # prsoll 61 bar
        triples = self._group(104)          # 104 + 127 = 231
        self.assertAlmostEqual(vag_formulas.decode(*triples[5]).value, 61.0)
        self.assertAlmostEqual(vag_formulas.decode(*triples[6]).value, 60.0)


# ---------------------------------------------------------------------------
# over the bus
# ---------------------------------------------------------------------------
@requires_can
@requires_dump
class TestTransport(_SimCase):
    def test_channel_setup_uses_the_documented_ids(self):
        self.assertEqual(self.tp.channel.tx_id, 0x740)
        self.assertEqual(self.tp.channel.rx_id, 0x300)
        self.assertEqual(self.sim.server.channels_opened, 1)

    def test_parameters_were_exchanged(self):
        self.assertIsNotNone(self.tp.channel.peer_params)
        self.assertEqual(self.tp.channel.params.block_size, 0x0F)

    def test_a_long_message_is_segmented_and_reassembled(self):
        """A 62-byte TransferData answer needs ten CAN frames."""
        self.kwp.start_session(0x89)
        seed = self.kwp.unlock_level2()
        self.assertEqual(seed, 0x12345678)
        self.kwp.start_session(0x86)
        self.kwp.request_upload(0x7F8000, 0x3E)
        block = self.kwp.transfer_data()
        self.assertEqual(len(block), 0x3E)

    def test_dropped_ack_is_retried(self):
        self.kwp.start_session(0x89)
        self.sim.server.drop_next_acks(1)
        before = self.tp.channel.tx_seq
        self.kwp.tester_present()            # must survive the lost ACK
        self.assertGreater(self.tp.channel.tx_seq, before)
        self.kwp.tester_present()            # and the channel still works

    def test_keep_alive_holds_the_channel_open(self):
        self.kwp.start_session(0x89)
        self.assertTrue(self.tp.keep_alive())

    def test_disconnect_closes_the_channel(self):
        self.tp.disconnect()
        deadline = time.monotonic() + 2.0
        while self.sim.server.connected and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse(self.sim.server.connected)


@requires_can
@requires_dump
class TestKeepAliveAcrossTimeout(_SimCase):
    """The brief's 5 s keep-alive check, made meaningful by a real P3 timer."""

    SIM_KW = {"session_timeout_s": 1.0}

    def test_session_survives_five_seconds_of_keep_alive(self):
        self.kwp.start_session(0x89)
        self.kwp.clear_dynamic_id(0xF0)
        self.kwp.define_dynamic_id(0xF0, [(0x7FEE74, 2)])
        started = time.monotonic()
        while time.monotonic() - started < 5.0:
            time.sleep(0.4)
            self.kwp.tester_present()
        self.assertEqual(len(self.kwp.read_dynamic_id(0xF0)), 2)
        self.assertEqual(self.sim.handlers.session_timeouts, 0)

    def test_without_keep_alive_the_session_drops(self):
        self.kwp.start_session(0x89)
        self.kwp.clear_dynamic_id(0xF0)
        self.kwp.define_dynamic_id(0xF0, [(0x7FEE74, 2)])
        time.sleep(1.5)
        self.tp.keep_alive()                 # channel alive, KWP session not
        with self.assertRaises(NegativeResponse) as ctx:
            self.kwp.read_dynamic_id(0xF0)
        self.assertEqual(ctx.exception.nrc, 0x33)
        self.assertEqual(self.sim.handlers.session_timeouts, 1)


@requires_can
@requires_dump
class TestUploadOverTheBus(_SimCase):
    def _unlock(self):
        self.kwp.start_session(0x89)
        self.kwp.unlock_level2()
        self.kwp.start_session(0x86)

    def test_one_kilobyte_matches_the_emulator_ram(self):
        pattern = bytes((i * 7 + 3) & 0xFF for i in range(1024))
        self.sim.handlers.emu.write(0x806000, pattern)
        self._unlock()
        self.assertEqual(self.kwp.upload(0x806000, 1024), pattern)

    def test_a_range_crossing_the_protected_window_is_split_and_refused(self):
        self._unlock()
        # the client refuses to even ask
        with self.assertRaises(ValueError):
            self.kwp.request_upload(0x7F9E00, 0x800)
        # the ECU refuses it too, from kwp_upload_range_check
        payload = bytes([0x7F, 0x9E, 0x00, 0x00, 0x00, 0x08, 0x00])
        with self.assertRaises(NegativeResponse) as ctx:
            self.kwp.request(0x35, payload)
        self.assertEqual(ctx.exception.nrc, 0x31)
        # and the split the tool would use works on both sides
        pieces = split_around_protected(0x7F9E00, 0x800)
        self.assertEqual(pieces, [(0x7F9E00, 0x3C), (0x7FA480, 0x180)])
        for start, size in pieces:
            self.assertEqual(len(self.kwp.upload(start, size)), size)


@requires_can
@requires_dump
class TestLoggerEndToEnd(_SimCase):
    """`med9log.py log` against the simulator, then through tools/logcmp.py."""

    #: this session logs ff_ticks / ff_alive, and since brief F3 the stand-in
    #: only fills 0x7FFB00 for an image that carries patches/ff_counter -- a
    #: stock image must leave those bytes alone.  `flash1=True` says "pretend
    #: Flash 1 is in", which is what this test has always assumed.
    SIM_KW = {"ram": AnimatedRam(flash1=True, statics=dict(DEFAULT_STATICS))}

    def _session_file(self, folder: Path) -> Path:
        doc = {
            "ecu": "03H906032 / 1037382557",
            "rate_hint_hz": 40,
            "vars": [
                {"name": "nmot_w", "addr": "0x7FEE74", "size": 2,
                 "scale": 0.25, "unit": "rpm"},
                {"name": "tmot", "addr": "0x8021EF", "size": 1,
                 "scale": 0.75, "offset": -48.0, "unit": "degC"},
                {"name": "ff_ticks", "addr": "0x7FFB00", "size": 4,
                 "scale": 1, "unit": "count"},
                {"name": "ff_alive", "addr": "0x7FFB04", "size": 2,
                 "scale": 1, "unit": "-"},
                {"name": "raster_10ms", "addr": "0x7FD760", "size": 4,
                 "scale": 1, "unit": "count"},
                {"name": "prist_w", "addr": "0x8031DA", "size": 2,
                 "scale": 0.005, "unit": "bar"},
            ],
        }
        path = folder / "six.json"
        path.write_text(json.dumps(doc))
        return path

    def test_six_variables_logged_for_two_seconds(self):
        import logcmp

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            session = self._session_file(folder)
            out = folder / "log.csv"
            # drive the CLI exactly as the human would, but on our simulator
            self.assertEqual(self._run_log(session, out), 0)

            log = logcmp.load_log(out)
            self.assertEqual(log.meta["ecu"], "03H906032 / 1037382557")
            self.assertEqual(log.meta["transport"],
                             "KWP2000 0x2C/0x21 over TP2.0")
            self.assertEqual(
                set(log), {"nmot_w", "tmot", "ff_ticks", "ff_alive",
                           "raster_10ms", "prist_w"})
            for name, series in log.items():
                self.assertGreater(len(series), 5, name)

            # the constants must come back exactly
            self.assertTrue(all(v == 0xFC01 for v in log["ff_alive"].v))
            self.assertTrue(all(abs(v - 60.0) < 1e-9 for v in log["prist_w"].v))
            # the animated cells must move, and in the right direction
            self.assertGreater(log["raster_10ms"].v[-1], log["raster_10ms"].v[0])
            self.assertGreater(log["ff_ticks"].v[-1], log["ff_ticks"].v[0])
            self.assertTrue(any(v != log["nmot_w"].v[0] for v in log["nmot_w"].v))
            # ...and match the simulator's own idea of the value
            ram = self.sim.handlers.read_ram(0x7FEE74, 2)
            self.assertAlmostEqual(struct.unpack(">H", ram)[0] * 0.25,
                                   log["nmot_w"].v[-1], delta=200.0)

    def test_raw_rows_carry_the_unscaled_counts(self):
        import logcmp

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            out = folder / "log.csv"
            self.assertEqual(self._run_log(self._session_file(folder), out,
                                           extra=["--raw"]), 0)
            log = logcmp.load_log(out)
            self.assertIn("prist_w_raw", log)
            self.assertTrue(all(v == 12000 for v in log["prist_w_raw"].v))
            self.assertTrue(all(v == 60.0 for v in log["prist_w"].v))

    def _run_log(self, session: Path, out: Path, extra=()) -> int:
        """Run `med9log.py log` against THIS test's already-running simulator."""
        return med9log.main(["log", "--session", str(session), "-o", str(out),
                             "--seconds", "2", "--bus", self.bus_spec, *extra])


@requires_can
@requires_dump
class TestDumpCommand(_SimCase):
    """`med9log.py dump` writes what tools/ram_snapshot_diff.py reads."""

    def _dump(self, folder: Path, name: str, out: str) -> Path:
        ranges = folder / "ranges.json"
        ranges.write_text(json.dumps({
            "ecu": "03H906032 / 1037382557",
            "ranges": [{"start": "0x806000", "end": "0x8061FF"},
                       {"start": "0x806400", "end": "0x8064FF"}]}))
        rc = med9log.main(["dump", "--ranges", str(ranges), "-o",
                           str(folder / out), "--session-name", name,
                           "--bus", self.bus_spec])
        self.assertEqual(rc, 0)
        return folder / out

    def test_manifest_and_binary_agree_and_diff_runs(self):
        sys.path.insert(0, str(REPO / "tools"))
        import ram_snapshot_diff

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self.sim.handlers.emu.write(0x806000, bytes(0x200))
            self.sim.handlers.emu.write(0x806400, b"\x5a" * 0x100)
            first = self._dump(folder, "key-on", "a.bin")

            # change one byte, take a second snapshot
            self.sim.handlers.emu.write(0x806010, b"\x99")
            second = self._dump(folder, "idle", "b.bin")

            for path in (first, second):
                manifest = json.loads(path.with_suffix(".json").read_text())
                blob = path.read_bytes()
                self.assertEqual(len(blob), manifest["bytes"])
                self.assertEqual(
                    blob, b"".join(bytes.fromhex(r["data"])
                                   for r in manifest["ranges"]))
                self.assertEqual(manifest["transport"].split(",")[0],
                                 "KWP2000 0x35 RequestUpload + 0x36 TransferData")

            snaps = [ram_snapshot_diff.Snapshot.load(p.with_suffix(".json"))
                     for p in (first, second)]
            verdict = ram_snapshot_diff.classify(snaps)
            self.assertEqual(len(verdict), 0x300)
            self.assertEqual(verdict[0x806010], "changed")
            self.assertEqual(verdict[0x806011], "blank")
            self.assertEqual(verdict[0x806400], "constant")

    def test_session_names_land_in_the_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path = self._dump(folder, "after-drive", "c.bin")
            manifest = json.loads(path.with_suffix(".json").read_text())
            self.assertEqual(manifest["session"], "after-drive")
            self.assertTrue(manifest["taken"].endswith("Z"))
            self.assertEqual(manifest["bin"], "c.bin")


@requires_can
@requires_dump
class TestAnimatedRam(DumpUnchanged):
    def test_the_rpm_ramp_is_deterministic(self):
        ram = AnimatedRam()
        self.assertAlmostEqual(ram.rpm(0.0), 800.0)
        self.assertAlmostEqual(ram.rpm(10.0), 3000.0)
        self.assertAlmostEqual(ram.rpm(20.0), 800.0)
        self.assertAlmostEqual(ram.rpm(5.0), 1900.0)

    def _counters(self, live_set: str) -> dict[int, int]:
        # flash1=True: the stand-in only fills 0x7FFB00 for an image that
        # carries patches/ff_counter, and DUMP is the stock one.
        handlers = Med9Handlers(str(DUMP), animate=False,
                                ram=AnimatedRam(live_task_set=live_set,
                                                flash1=True))
        handlers.ram.apply(handlers.emu, 1.0)          # one simulated second
        out = {a: struct.unpack(">I", handlers.read_ram(a, 4))[0]
               for a in (0x7FD754, 0x7FD758, 0x7FD75C, 0x7FD760, 0x7FD778,
                         0x7FFB00)}
        out[0x7FFB04] = struct.unpack(">H", handlers.read_ram(0x7FFB04, 2))[0]
        out[0x7FFB06] = handlers.read_ram(0x7FFB06, 1)[0]
        out[0x7FFB07] = handlers.read_ram(0x7FFB07, 1)[0]
        return out

    def test_set_b_live_counts_at_the_c4_rates(self):
        c = self._counters("B")
        self.assertEqual(c[0x7FD760], 1000)      # set B 1 ms
        self.assertEqual(c[0x7FD778], 500)       # set B 2 ms
        self.assertEqual(c[0x7FD758], 100)       # set B 10 ms
        self.assertEqual(c[0x7FFB00], 100)       # ff_ticks tracks it 1:1
        self.assertEqual(c[0x7FFB06], 2)         # ff_src_seen = set B
        self.assertEqual(c[0x7FD754], 0)         # set A frozen
        self.assertEqual(c[0x7FD75C], 0)

    def test_set_a_live_still_counts_the_flash1_block(self):
        """Brief F1: Flash 1 hooks BOTH 10 ms rasters, so the block runs
        whichever set is live and ff_src_seen names it.  A frozen block is
        now a statement about the flash -- flash1_counter.json check 1."""
        c = self._counters("A")
        self.assertEqual(c[0x7FD75C], 1000)      # set A 1 ms
        self.assertEqual(c[0x7FD754], 100)       # set A 10 ms
        self.assertEqual(c[0x7FD758], 0)         # set B frozen
        self.assertEqual(c[0x7FFB00], 100)       # our counter is NOT frozen
        self.assertEqual(c[0x7FFB06], 1)         # ff_src_seen = set A

    def test_a_stock_image_leaves_the_flash1_block_alone(self):
        """A live counter on an unpatched image is a false 'the patch is in'
        -- and bench_rehearsal.py's stock step reads exactly those bytes."""
        h = Med9Handlers(str(DUMP), animate=False,
                         ram=AnimatedRam(live_task_set="A"))
        self.assertFalse(h.ram.flash1, "no ff_counter blob at 0x150000")
        h.ram.apply(h.emu, 12.0)
        self.assertEqual(h.read_ram(0x7FFB00, 8), bytes(8))

    def test_the_flash1_block_matches_the_patch_layout(self):
        """+0x04 ff_alive = 0xFC01, +0x06 ff_src_seen, +0x07 ff_reserved."""
        for live_set, want_src in (("A", 1), ("B", 2)):
            with self.subTest(task_set=live_set):
                c = self._counters(live_set)
                self.assertEqual(c[0x7FFB04], 0xFC01)
                self.assertEqual(c[0x7FFB06], want_src)
                self.assertEqual(c[0x7FFB07], 0)


if __name__ == "__main__":
    unittest.main()
