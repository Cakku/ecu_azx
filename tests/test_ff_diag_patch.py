"""patches/ff_fuel's D2 half: measuring block 111 (#39) and the E% store (#38).

Five layers, each one able to fail on its own:

  1. the stock facts the slot choice rests on - the four TKMWL words, the eight
     group words, and the three independent searches that say nothing else
     reads them;
  2. apply: the table now points at our handlers and the group names our ids;
  3. `measuring_var_dispatch` (0x45768) on the applied image, per id, against
     `emu/models/flexfuel.py`;
  4. `21 6F` end to end through `logging/ecu_sim.py` on the patched image -
     the firmware's own SID 0x21 route, our handlers, 25 bytes on the wire,
     decoded by `logging/med9kwp/vag_formulas.py`;
  5. the E% store through the real `nvm_block_request` (0x6131C) with the QSPI
     left as the emulator's zero stub.

Nothing here writes to `data/` and nothing is ever flashed.
"""
from __future__ import annotations

import struct
import sys
import unittest

from tests import test_ff_fuel_patch as tff
from tests import test_qspi_eeprom as tq  # E4's #38 end-to-end, re-run at +19
from tests.common import DUMP, REPO, DumpUnchanged, requires_dump

import measuring_vars as mv  # noqa: E402  (tests.common put tools/ on sys.path)
import med9lib as m  # noqa: E402

sys.path.insert(0, str(REPO / "logging"))
from med9kwp import vag_formulas  # noqa: E402

from emu.models import flexfuel as ff  # noqa: E402

# --- the facts under test (re/findings/measuring_vars.md section 8) ---------
TKMWL = 0x0A5658
STUB = 0x00038EC4
DISPATCH = 0x045768
EMIT = 0x038EB4
GROUP_TABLE = 0x5C5518
IDS = (2196, 2197, 2198, 2199)
GROUP = 111
SYMS = ("ff_diag_e_pct", "ff_diag_f_pct", "ff_diag_t_degc", "ff_diag_mode")
SLOTS = tuple(TKMWL + 4 * i for i in IDS)
GROUP_WORDS = tuple(GROUP_TABLE + f * 0x1FE + GROUP * 2 for f in range(4))

RESULT_FORMULA = 0x7FD06F
RESULT_B = 0x7FD070
RESULT_A = 0x7FD071

# --- EEP_CONF block 8 (re/findings/eeprom.md sections 3.3 and 8) ------------
NVM = 0x06131C
NVM_PUMP = 0x060A68
BLK8_MIRROR = 0x7F9F80
BLK8_LEN = 0x20
NVM_QUEUE_STATE = 0x7FADAB
PATCH_RAM = tff.PATCH_RAM


#: EEP_CONF block 8's payload starts with a {block id, version} stamp that
#: `nvm_read_all_blocks` (0x06227C) validates against the flash default table
#: at 0x060458-0x060470; a mismatch discards the block and reloads the
#: defaults (re/findings/eeprom.md section 10.5, brief E4).  That is why
#: `ff_persist_offset` is not 0, and why the two bytes below must never be
#: written by this patch.
#:
#: G7 (2026-09-24, #38): payload +2..+18 are the 17 tester adaptation
#: channels (channel k at +(k+1); `adaptation_restore_all` 0x12E3F8 and the
#: KWP adaptation service 0x038708), so E4's +2 was channel 1 and a
#: channel-0 reset zeroed the store.  The offset is **19**, the lowest byte no
#: stock path writes (eeprom.md section 5, note of 2026-09-24).
BLK8_STAMP = bytes((8, 1))
PERSIST_OFF = 19
ADAP_CHANNELS = range(2, 19)          # payload +2..+18
PERSIST_OFF_E4 = 2                    # where E4 put it: adaptation channel 1


def blk8_stored(pct: int, off: int = PERSIST_OFF) -> bytes:
    """A block-8 image whose stamp is intact and whose E% sits at `off`."""
    b = bytearray(BLK8_STAMP + b"\xFF" * (BLK8_LEN - 2 - 2))
    b[off] = pct
    return blk8(bytes(b))


def blk8(payload: bytes) -> bytes:
    """A block-8 image with the manager's own checksum (eeprom.md 3.4)."""
    b = bytearray(payload[:BLK8_LEN - 2].ljust(BLK8_LEN - 2, b"\xFF")) + b"\0\0"
    struct.pack_into(">H", b, BLK8_LEN - 2, (~sum(b[:BLK8_LEN - 2])) & 0xFFFF)
    return bytes(b)


def blk8_csum_ok(raw: bytes) -> bool:
    return ((sum(raw[:BLK8_LEN - 2])
             + struct.unpack(">H", raw[BLK8_LEN - 2:])[0]) & 0xFFFF) == 0xFFFF


# ------------------------------------------------- 1. the stock facts ------
@requires_dump
class TestSlotsAreFree(DumpUnchanged):
    def setUp(self):
        self.data = m.load_dump(str(DUMP))

    def _read(self, cpu: int, n: int) -> bytes:
        off = m.cpu_to_file(cpu)
        return bytes(self.data[off:off + n])

    def test_the_four_tkmwl_slots_hold_the_not_available_stub(self):
        self.assertEqual(self._read(SLOTS[0], 16),
                         STUB.to_bytes(4, "big") * 4,
                         "ids 2196-2199 must be four contiguous stub pointers")

    def test_they_are_the_last_four_entries_of_the_table(self):
        self.assertEqual(SLOTS[-1] + 4, TKMWL + 4 * 2200)

    def test_group_111_and_its_echo_are_empty(self):
        for g in (GROUP, GROUP + 0x7F):
            for f in range(4):
                with self.subTest(group=g, field=f + 1):
                    self.assertEqual(
                        self._read(GROUP_TABLE + f * 0x1FE + g * 2, 2), b"\0\0")

    def test_the_tool_agrees_with_the_choice(self):
        """`measuring_vars.py --free` is the reproducible form of section 8."""
        dump = mv.Dump(str(DUMP))
        disp, base, count = mv.find_dispatcher(dump.d)
        self.assertEqual((disp, base, count), (DISPATCH, TKMWL, 2200))
        ptrs = [dump.word(base + 4 * i) for i in range(count)]
        spare, free = mv.free_slots(dump, GROUP_TABLE, ptrs, STUB)
        self.assertEqual(spare[-4:], list(IDS))
        self.assertIn(GROUP, free)
        self.assertEqual(max(free), GROUP, "111 is the highest free group")

    def test_nothing_else_reaches_the_group_table(self):
        """Only the two group readers form its base; no D-form access at all."""
        sites = []
        for off in range(0, len(self.data) - 3, 4):
            w = int.from_bytes(bytes(self.data[off:off + 4]), "big")
            if (w >> 26) == 14 and ((w >> 16) & 0x1F) == 2 \
                    and (w & 0xFFFF) == 0xB528:          # addi rD,r2,-0x4AD8
                sites.append(m.file_to_cpu(off))
        self.assertEqual(sites, [0x035760, 0x0357F8])

    def test_the_result_helper_is_where_the_handlers_branch_to(self):
        self.assertEqual(self._read(EMIT, 16),
                         bytes.fromhex("986dd07f988dd08198add0804e800020"))


# ------------------------------------------------------- 2. the apply ------
class TestDiagApply(tff.TestApply):
    """Re-uses D1's applied image; adds what D2 writes into the two tables."""

    def test_the_tkmwl_slots_point_at_our_handlers(self):
        syms = tff.load_patch()["build"]["symbols"]
        for slot, name in zip(SLOTS, SYMS):
            with self.subTest(slot=hex(slot)):
                off = m.cpu_to_file(slot)
                self.assertEqual(int.from_bytes(bytes(self.data[off:off + 4]),
                                                "big"),
                                 int(syms[name], 0))

    def test_the_group_words_name_our_ids(self):
        for word, vid in zip(GROUP_WORDS, IDS):
            with self.subTest(word=hex(word)):
                off = m.cpu_to_file(word)
                self.assertEqual(
                    struct.unpack(">H", bytes(self.data[off:off + 2]))[0], vid)

    def test_the_echo_group_is_still_empty(self):
        for f in range(4):
            off = m.cpu_to_file(GROUP_TABLE + f * 0x1FE + (GROUP + 0x7F) * 2)
            self.assertEqual(bytes(self.data[off:off + 2]), b"\0\0")

    def test_the_ram_symbols_are_inside_the_declared_block(self):
        build = tff.load_patch()["build"]
        ram, size = int(build["ram"], 0), build["ram_size"]
        for name in ("ff_state", "ff_persist_buf", "ff_nvm_req"):
            with self.subTest(name=name):
                addr = int(build["symbols"][name], 0)
                self.assertTrue(ram <= addr < ram + size, hex(addr))
        self.assertEqual(int(build["symbols"]["ff_state"], 0), ram)


# ------------------------------------------------- the emulated layers -----
class DiagEmuBase(tff.EmuBase):
    """One applied image; helpers for the dispatcher and the block manager."""

    def warm(self, e_pct: int = 85, t_fuel_c: int = 25, ticks: int = 400):
        """An emulator and a model driven to the same state, side by side."""
        emu = self.fresh()
        mdl = ff.FlexFuelModel(tff.cal_from_block(self.cal_blk))
        emu.write(BLK8_MIRROR, blk8(BLK8_STAMP + b"\xFF" * (BLK8_LEN - 4)))
        for i in range(ticks):
            rx = (ff.frame(e_pct=e_pct, t_fuel_c=t_fuel_c,
                           counter=(i // 10) & 0xFF) if i % 10 == 0 else None)
            if rx is not None:
                self.arm_frame(emu, rx)
            else:
                self.no_frame(emu)
            emu.call(self.syms["ff_fuel_hook_b"], reset=False)
            mdl.tick(rx)
        return emu, mdl

    @staticmethod
    def reseal(emu) -> None:
        """Recompute the core checksum after a test poked a core field.

        Without it the next activation finds the header wrong and runs
        `ff_state_init()`, which is correct behaviour and a confusing test.
        """
        core = (emu.read(PATCH_RAM + ff.CORE_OFF, ff.CORE_LEN)
                + emu.read(PATCH_RAM + ff.CORE2_OFF, ff.CORE2_LEN))
        emu.write(PATCH_RAM + 0x06, (~sum(core)) & 0xFFFF, 2)

    @staticmethod
    def dispatch(emu, vid: int):
        """`measuring_var_dispatch(vid)` -> the (formula, A, B) triple."""
        res = emu.call(DISPATCH, args=[vid], reset=False)
        assert res.ok, res.summary()
        return (emu.read(RESULT_FORMULA, 1)[0],
                emu.read(RESULT_A, 1)[0],
                emu.read(RESULT_B, 1)[0])


# --------------------------------------------------- 3. the dispatcher -----
class TestDispatcher(DiagEmuBase):
    def test_the_four_ids_answer_with_the_model_triples(self):
        emu, mdl = self.warm()
        got, want = emu.read(PATCH_RAM, tff.STATE_LEN), mdl.full_bytes()
        self.assertEqual(got[:0x28], want[:0x28],
                         "header and core must agree tick for tick")
        self.assertEqual(got[0x34:0x3A], want[0x34:0x3A],
                         "and so must the three words the handlers read")
        want = mdl.triples()
        for vid, triple in zip(IDS, want):
            with self.subTest(id=vid):
                self.assertEqual(self.dispatch(emu, vid), triple)
        # and the values a tester would read off them.  400 activations at the
        # 2 %/s slew limit is 8 %, not 85: the filter is still on its way up,
        # which is exactly what the field has to be able to show.
        e, f, t, mode = (vag_formulas.decode(*x) for x in want)
        self.assertEqual(e.unit, "%")
        self.assertAlmostEqual(e.value, float(mdl.state.diag_e_pct), places=3)
        self.assertAlmostEqual(e.value, 8.0, places=3)
        self.assertAlmostEqual(t.value, 25.0, places=3)
        self.assertEqual(t.tag, vag_formulas.CROSSCHECKED)
        self.assertAlmostEqual(f.value, mdl.state.f_q10 * 100 // 1024, places=3)
        self.assertEqual(mode.value, 256 * mdl.state.persist_state
                         + mdl.state.mode)
        self.assertEqual(mdl.state.mode, ff.MODE_OK)

    def test_an_invalid_state_block_reads_as_not_available(self):
        emu = self.fresh(garbage=b"\xA5" * 0x40)
        for vid in IDS:
            with self.subTest(id=vid):
                self.assertEqual(self.dispatch(emu, vid), (0x25, 0, 0))

    def test_the_temperature_field_is_blank_until_the_first_frame(self):
        emu = self.fresh()
        emu.call(self.syms["ff_fuel_hook_b"], reset=False)     # cold start
        self.assertEqual(self.dispatch(emu, 2198), (0x25, 0, 0))
        self.assertEqual(self.dispatch(emu, 2196)[0], 0x21,
                         "ethanol is reportable from the first activation")

    def test_the_temperature_field_spans_the_whole_byte_range(self):
        for t_c, want_b in ((-40, 60), (0, 100), (25, 125), (155, 255),
                            (200, 255)):
            with self.subTest(degc=t_c):
                emu, _ = self.warm(t_fuel_c=t_c, ticks=20)
                self.assertEqual(self.dispatch(emu, 2198), (0x05, 10, want_b))

    def test_a_handler_costs_less_than_a_hundred_instructions(self):
        emu, _ = self.warm(ticks=20)
        for vid in IDS:
            with self.subTest(id=vid):
                res = emu.call(DISPATCH, args=[vid], reset=False)
                self.assertTrue(res.ok)
                self.assertLess(res.insns, 100, res.summary())

    def test_a_handler_writes_only_the_three_result_bytes(self):
        """The KWP task must not be able to disturb the control path."""
        emu, _ = self.warm(ticks=50)
        before = emu.read(0x7F8000, 0x10000)
        emu.call(DISPATCH, args=[IDS[0]], reset=False)
        after = emu.read(0x7F8000, 0x10000)
        moved = {0x7F8000 + i for i in range(len(before)) if before[i] != after[i]}
        allowed = {RESULT_FORMULA, RESULT_A, RESULT_B, 0x7FD072}
        allowed |= set(range(0x7FB8E8, 0x7FB8EC))   # dispatcher's last-handler
        # the harness's own frame: r1 starts at STACK_TOP and emu.call writes
        # the return-magic link just above it
        allowed |= set(range(tff.STACK_TOP - 0x100, tff.STACK_TOP + 8))
        self.assertEqual(moved - allowed, set())


# ------------------------------------------- 4. 21 <group> over TP2.0/KWP ---
@requires_dump
class TestGroupOverKwp(unittest.TestCase):
    """The firmware's own SID 0x21 route, on the patched image."""

    @classmethod
    def setUpClass(cls):
        try:
            from emu import Med9Emu  # noqa: F401
        except Exception:                                    # pragma: no cover
            raise unittest.SkipTest("unicorn / emu package missing")
        import patch_apply
        import tempfile
        from pathlib import Path
        cls.tmp = Path(tempfile.mkdtemp())
        cls.image = cls.tmp / "ff_fuel.bin"
        data, _r, _w = patch_apply.apply_patch(DUMP, tff.FF_FUEL)
        cls.image.write_bytes(bytes(data))
        from ecu_sim import Med9Handlers
        cls.h = Med9Handlers(str(cls.image), animate=True)

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_simulator_recognises_a_patched_image(self):
        self.assertTrue(self.h.ram.flexfuel,
                        "FFCAL001 at 0x5E2510 must switch the animated block")

    def test_21_6f_returns_our_four_fields_and_four_empties(self):
        self.h.handle(b"\x10\x89")
        answer = self.h.handle(bytes([0x21, GROUP]))[-1]
        self.assertEqual(len(answer), 26, answer.hex())
        self.assertEqual(answer[0], 0x61)
        self.assertEqual(answer[1], GROUP)
        triples = [tuple(answer[2 + 3 * i:5 + 3 * i]) for i in range(8)]
        self.assertEqual([t[0] for t in triples[:4]], [0x21, 0x21, 0x05, 0x36])
        self.assertEqual(triples[4:], [(0x25, 0, 0)] * 4,
                         "group 238 must stay empty")
        e, f, t, mode = (vag_formulas.decode(*x) for x in triples[:4])
        self.assertAlmostEqual(e.value, 85.0, places=3)
        self.assertAlmostEqual(t.value, 25.0, places=3)
        self.assertGreater(f.value, 100.0)
        self.assertEqual(mode.value, float(ff.MODE_OK))

    def test_a_group_above_0x7f_is_still_refused(self):
        self.h.handle(b"\x10\x89")
        self.assertEqual(self.h.handle(b"\x21\x8c")[-1], b"\x7f\x21\x31")


# ----------------------------------------------------- 5. the E% store -----
class TestPersistence(DiagEmuBase):
    def nvm(self, emu, blk, off, ln, mode, buf, req):
        res = emu.call(NVM, args=[blk, off, ln, mode, buf, req], reset=False)
        self.assertTrue(res.ok, res.summary())
        return res.regs["r3"]

    def test_the_call_shapes_behave_as_eeprom_md_8_says(self):
        emu = self.fresh()
        emu.write(BLK8_MIRROR, blk8(b"\x11" * 30))
        scratch = 0x807700
        emu.write(scratch, b"\x00")
        self.assertEqual(self.nvm(emu, 8, 0, 1, 1, scratch, 0), 2, "read back")
        self.assertEqual(emu.read(scratch, 1), b"\x11")
        emu.write(scratch, b"\x42")
        self.assertEqual(self.nvm(emu, 8, 0, 1, 0, scratch, 0), 2, "stage")
        raw = emu.read(BLK8_MIRROR, BLK8_LEN)
        self.assertEqual(raw[0], 0x42)
        self.assertEqual(raw[1:29], b"\x11" * 28, "no other payload byte moved")
        self.assertEqual(raw[29], 0x11, "+29 belongs to the manager")
        self.assertTrue(blk8_csum_ok(raw),
                        "the stage maintains the block checksum itself")

    def test_the_cold_start_restores_e_filt_and_e_key(self):
        for stored, want_pct in ((0, 0), (5, 5), (85, 85), (100, 100)):
            with self.subTest(stored=stored):
                emu = self.fresh()
                emu.write(BLK8_MIRROR, blk8_stored(stored))
                emu.call(self.syms["ff_fuel_hook_b"], reset=False)
                st = emu.read(PATCH_RAM, tff.STATE_LEN)
                self.assertEqual(struct.unpack_from(">H", st, 0x24)[0],
                                 want_pct * 16, "e_key")
                self.assertEqual(struct.unpack_from(">H", st, 0x30)[0],
                                 want_pct, "e_persist")
                self.assertEqual(st[0x33], 2, "persist_err = the read's rc")
                # e_filt started there and the FAULT decay target is the same,
                # so it does not move
                self.assertEqual(struct.unpack_from(">H", st, 0x08)[0],
                                 want_pct * 16, "e_filt")

    def test_a_garbage_store_is_ignored(self):
        for stored in (0xFF, 101, 200):
            with self.subTest(stored=stored):
                emu = self.fresh()
                emu.write(BLK8_MIRROR, blk8_stored(stored))
                emu.call(self.syms["ff_fuel_hook_b"], reset=False)
                st = emu.read(PATCH_RAM, tff.STATE_LEN)
                self.assertEqual(struct.unpack_from(">H", st, 0x08)[0], 0)
                self.assertEqual(struct.unpack_from(">H", st, 0x24)[0], 0)
                self.assertEqual(struct.unpack_from(">H", st, 0x30)[0], 0xFFFF,
                                 "e_persist stays 'unknown', so the first "
                                 "commit is not suppressed by the hysteresis")

    def test_persistence_off_never_touches_the_block_manager(self):
        import ffcal001
        params = ffcal001.load_params(tff.FF_FUEL / "ffcal001.json")
        blk = ffcal001.build(dict(params, ff_persist_enable=0))
        emu = self.fresh(cal=blk)
        emu.write(BLK8_MIRROR, blk8_stored(42))
        before = emu.read(BLK8_MIRROR, BLK8_LEN)
        for i in range(400):
            rx = ff.frame(e_pct=85, counter=(i // 10) & 0xFF) if i % 10 == 0 else None
            self.arm_frame(emu, rx) if rx else self.no_frame(emu)
            emu.call(self.syms["ff_fuel_hook_b"], reset=False)
        st = emu.read(PATCH_RAM, tff.STATE_LEN)
        self.assertEqual(emu.read(BLK8_MIRROR, BLK8_LEN), before)
        self.assertEqual(struct.unpack_from(">H", st, 0x24)[0], 0, "e_key")
        self.assertEqual(st[0x32], 0, "persist_state stays idle")
        self.assertEqual(st[0x33], 0, "persist_err stays 0")

    def test_the_rate_limit_holds_off_the_first_commit(self):
        emu, _ = self.warm(ticks=400)
        st = emu.read(PATCH_RAM, tff.STATE_LEN)
        self.assertEqual(st[0x32], 0, "still idle: the 60 s window is open")
        self.assertEqual(struct.unpack_from(">H", st, 0x3A)[0], 6000 - 400)
        self.assertEqual(emu.read(BLK8_MIRROR + PERSIST_OFF, 1), b"\xFF")

    def test_a_commit_stages_and_queues_through_stock_code_only(self):
        emu, mdl = self.warm(ticks=400)
        emu.write(NVM_QUEUE_STATE, b"\x20")
        emu.write(PATCH_RAM + 0x3A, 0, 2)              # open the rate limit
        emu.call(self.syms["ff_fuel_hook_b"], reset=False)

        st = emu.read(PATCH_RAM, tff.STATE_LEN)
        self.assertEqual(st[0x32], 2, "persist_state = committing")
        self.assertEqual(st[0x33], 1, "persist_err = the commit's rc")
        e_pct = struct.unpack_from(">H", st, 0x30)[0]
        self.assertEqual(e_pct, struct.unpack_from(">H", st, 0x34)[0])
        self.assertGreater(e_pct, 0)

        raw = emu.read(BLK8_MIRROR, BLK8_LEN)
        self.assertEqual(raw[PERSIST_OFF], e_pct, "the mirror carries the new E%")
        self.assertEqual(raw[:2], BLK8_STAMP,
                         "the {block id, version} stamp must survive (E4, #38)")
        self.assertEqual(raw[2:PERSIST_OFF] + raw[PERSIST_OFF + 1:30],
                         b"\xFF" * 27,
                         "+2..+29 untouched but for the E% byte - the "
                         "adaptation channels +2..+18 above all (G7)")
        self.assertTrue(blk8_csum_ok(raw))

        req = int(tff.load_patch()["build"]["symbols"]["ff_nvm_req"], 0)
        rec = emu.read(req, 9)
        self.assertEqual(struct.unpack(">I", rec[0:4])[0], 0, "buf = 0")
        self.assertEqual(tuple(rec[4:9]), (8, 0, 0, 3, 1),
                         "blk 8, off 0, len 0, shape 3 (commit), status queued")
        self.assertEqual(struct.unpack(">I", emu.read(0x7FC434, 4))[0], req,
                         "the manager holds a pointer to our record")

        # the rate limit is armed again, and the next activation does nothing
        self.assertEqual(struct.unpack_from(">H", st, 0x3A)[0], 6000)
        emu.call(self.syms["ff_fuel_hook_b"], reset=False)
        self.assertEqual(emu.read(req, 9), rec)

    def test_the_pump_takes_the_request_as_far_as_the_device(self):
        """One pump call moves the queue to the block-write state (0x23)."""
        emu, _ = self.warm(ticks=400)
        emu.write(NVM_QUEUE_STATE, b"\x20")
        emu.write(PATCH_RAM + 0x3A, 0, 2)
        emu.call(self.syms["ff_fuel_hook_b"], reset=False)
        res = emu.call(NVM_PUMP, args=[], reset=False)
        self.assertTrue(res.ok, res.summary())
        self.assertEqual(emu.read(NVM_QUEUE_STATE, 1), b"\x23",
                         "the checksum verified and the device write started")
        req = int(tff.load_patch()["build"]["symbols"]["ff_nvm_req"], 0)
        self.assertEqual(emu.read(req + 8, 1), b"\x01", "still in flight")

    def test_completion_is_read_back_from_the_record(self):
        """`nvm_state_complete` (0x612B4) writes one byte; that is modelled."""
        for status, want_state, writes, fails in ((2, 3, 1, 0), (0x80, 4, 0, 1),
                                                  (0x82, 4, 0, 1)):
            with self.subTest(status=status):
                emu, _ = self.warm(ticks=400)
                emu.write(NVM_QUEUE_STATE, b"\x20")
                emu.write(PATCH_RAM + 0x3A, 0, 2)
                emu.call(self.syms["ff_fuel_hook_b"], reset=False)
                req = int(tff.load_patch()["build"]["symbols"]["ff_nvm_req"], 0)
                self.assertEqual(emu.read(req + 8, 1), b"\x01")
                emu.write(req + 8, bytes([status]))
                emu.call(self.syms["ff_fuel_hook_b"], reset=False)
                st = emu.read(PATCH_RAM, tff.STATE_LEN)
                self.assertEqual(st[0x32], want_state)
                self.assertEqual(st[0x33], status)
                self.assertEqual(struct.unpack_from(">H", st, 0x3C)[0], writes)
                self.assertEqual(struct.unpack_from(">H", st, 0x3E)[0], fails)

    def test_the_hysteresis_suppresses_a_small_move(self):
        emu, _ = self.warm(e_pct=85, ticks=400)
        emu.write(NVM_QUEUE_STATE, b"\x20")
        emu.write(PATCH_RAM + 0x3A, 0, 2)
        emu.call(self.syms["ff_fuel_hook_b"], reset=False)
        req = int(tff.load_patch()["build"]["symbols"]["ff_nvm_req"], 0)
        emu.write(req + 8, b"\x02")                     # the commit finished
        emu.call(self.syms["ff_fuel_hook_b"], reset=False)
        stored = struct.unpack_from(">H", emu.read(PATCH_RAM, tff.STATE_LEN), 0x30)[0]

        # 4 % more is inside the 5 % hysteresis: nothing is queued again
        emu.write(PATCH_RAM + 0x08, (stored + 4) * 16, 2)
        emu.write(PATCH_RAM + 0x34, stored + 4, 2)
        emu.write(PATCH_RAM + 0x3A, 0, 2)
        self.reseal(emu)
        emu.write(req + 8, b"\x00")
        emu.call(self.syms["ff_fuel_hook_b"], reset=False)
        self.assertEqual(emu.read(req + 8, 1), b"\x00", "no new request")
        self.assertEqual(emu.read(PATCH_RAM, tff.STATE_LEN)[0x32], 3, "still DONE")

    def test_the_hysteresis_lets_a_big_move_through(self):
        emu, _ = self.warm(e_pct=85, ticks=400)
        emu.write(NVM_QUEUE_STATE, b"\x20")
        emu.write(PATCH_RAM + 0x3A, 0, 2)
        emu.call(self.syms["ff_fuel_hook_b"], reset=False)
        req = int(tff.load_patch()["build"]["symbols"]["ff_nvm_req"], 0)
        emu.write(req + 8, b"\x02")
        emu.call(self.syms["ff_fuel_hook_b"], reset=False)
        stored = struct.unpack_from(">H", emu.read(PATCH_RAM, tff.STATE_LEN), 0x30)[0]

        emu.write(PATCH_RAM + 0x08, max(stored - 20, 0) * 16, 2)
        emu.write(PATCH_RAM + 0x34, max(stored - 20, 0), 2)
        emu.write(PATCH_RAM + 0x3A, 0, 2)
        self.reseal(emu)
        emu.call(self.syms["ff_fuel_hook_b"], reset=False)
        st = emu.read(PATCH_RAM, tff.STATE_LEN)
        self.assertEqual(st[0x32], 2, "a new commit is in flight")
        self.assertEqual(emu.read(BLK8_MIRROR + PERSIST_OFF, 1)[0],
                         max(stored - 20, 0))

    def test_a_fault_never_overwrites_the_stored_value(self):
        emu, _ = self.warm(e_pct=85, ticks=400)
        emu.write(NVM_QUEUE_STATE, b"\x20")
        self.no_frame(emu)
        for _ in range(200):                   # ff_timeout_ms = 1000 -> FAULT
            emu.call(self.syms["ff_fuel_hook_b"], reset=False)
        self.assertEqual(emu.read(PATCH_RAM, tff.STATE_LEN)[0x0C], ff.MODE_FAULT)
        before = emu.read(BLK8_MIRROR, BLK8_LEN)
        for _ in range(400):                   # E decays towards e_key
            emu.write(PATCH_RAM + 0x3A, 0, 2)  # with the rate limit wide open
            emu.call(self.syms["ff_fuel_hook_b"], reset=False)
        st = emu.read(PATCH_RAM, tff.STATE_LEN)
        self.assertEqual(st[0x0C], ff.MODE_FAULT)
        self.assertEqual(emu.read(BLK8_MIRROR, BLK8_LEN), before)
        self.assertEqual(st[0x32], 0, "persist_state never left idle")

    def test_the_block_id_and_version_stamp_are_never_written(self):
        """E4 (#38), re/findings/eeprom.md 10.5 — the bug D2 shipped.

        Payload +0/+1 of EEP_CONF block 8 is a `{block id, version}` stamp
        that `nvm_read_all_blocks` (0x06227C) compares against the flash
        default table at 0x060458-0x060470.  A mismatch makes the manager
        DISCARD the block and reload the defaults, so an E% stored at +0
        never survives a key cycle and reads back as 0x08 - a plausible 8 %,
        not the 0xFF that means "nothing known".  `ff_persist_offset` = 2
        fixed it (and G7 moved it on to 19, past the adaptation channels), and this asserts the two bytes stay put through a whole
        commit cycle, from the cold-start read to the completed write.
        """
        import ffcal001
        cal = ffcal001.load_params(tff.FF_FUEL / "ffcal001.json")
        self.assertEqual(cal["ff_persist_offset"], PERSIST_OFF,
                         "the shipped calibration must not store on the stamp")

        emu, _ = self.warm(e_pct=85, ticks=400)
        emu.write(NVM_QUEUE_STATE, b"\x20")
        emu.write(PATCH_RAM + 0x3A, 0, 2)
        emu.call(self.syms["ff_fuel_hook_b"], reset=False)
        req = int(tff.load_patch()["build"]["symbols"]["ff_nvm_req"], 0)
        emu.write(req + 8, b"\x02")                    # the commit finished
        emu.call(self.syms["ff_fuel_hook_b"], reset=False)

        raw = emu.read(BLK8_MIRROR, BLK8_LEN)
        self.assertEqual(raw[:2], BLK8_STAMP,
                         "the stamp moved - the block would be discarded")
        self.assertNotEqual(raw[PERSIST_OFF], 0xFF, "the E% really was stored")
        self.assertTrue(blk8_csum_ok(raw))
        st = emu.read(PATCH_RAM, tff.STATE_LEN)
        self.assertEqual(st[0x32], 3, "persist_state = done")
        self.assertEqual(struct.unpack_from(">H", st, 0x3C)[0], 1, "one write")

    def test_the_stage_shape_writes_exactly_one_payload_byte(self):
        """Whatever the offset, the patch stages ONE byte and no other."""
        for off in (0, PERSIST_OFF_E4, 13, PERSIST_OFF):
            with self.subTest(offset=off):
                emu = self.fresh()
                emu.write(BLK8_MIRROR, blk8(bytes(range(BLK8_LEN - 2))))
                before = bytearray(emu.read(BLK8_MIRROR, BLK8_LEN))
                scratch = 0x807700
                emu.write(scratch, b"\x5A")
                self.assertEqual(self.nvm(emu, 8, off, 1, 0, scratch, 0), 2)
                raw = bytearray(emu.read(BLK8_MIRROR, BLK8_LEN))
                self.assertEqual(raw[off], 0x5A)
                raw[off] = before[off]
                raw[BLK8_LEN - 2:] = before[BLK8_LEN - 2:]   # the checksum moved
                self.assertEqual(bytes(raw), bytes(before),
                                 "a stage touched a byte it was not given")


# --------------- 6. G7: the E% store off the adaptation channels (#38) -----
#: `adaptation_restore_all` (F4, re/symbols.csv 0x12E3F8).  Called at the
#: `mr r11,r1` one word earlier, as every `_savegpr` function here must be
#: (re/findings/eeprom.md section 10.1).
ADAP_RESTORE_ALL = 0x12E3F4
#: the KWP adaptation service (F4, 0x038708; entry `mr r11,r1` at 0x038704).
#: Request record: +0 sub-function 0x81/0x82/0x83, +1 channel (0x81) or high
#: byte (0x82), +2 value (0x82), +4 status out (2 = done, 1 = commit queued).
ADAP_SERVICE = 0x038704
#: "restore every channel to its default and commit block 8"; its caller is
#: 0x0D10D0, after the fault-clear state machine 0x035300 (eeprom.md 5, note
#: of 2026-09-24).  No access check at all, unlike the tester path.
ADAP_RESET_ALL = 0x038D64
ADAP_DESCRIPTOR = 0x0A3AD8            # 08 02 | 17 RAM pointers from +4
ADAP_RAM = (0x7FD062, 12)             # the twelve implemented channel cells
ADAP_CH1_RAM = 0x7FD06B               # channel 1 = payload +2, clamped 0/0
#: three access words the service tests channel bit (ch - 1) against; all
#: 0x00000040 on this dataset, which admits channel 7 only
ADAP_ACCESS = (0x5CF004, 0x5CF008, 0x5CF00C)
ADAP_HANDLE = 0x8001D8                # r13 + 0x1E8: the service's 9-byte record
SVC_REC = 0x807600                    # scratch for the request record
BLK8_DEV = (0x1C0, 0x1E0)
BLK8_FLASH_DEFAULT = 0x0B32DC         # file offset: 0xB3238 + table[8].dflt
NVM_PAGE_BUF = 0x8043C8               # the manager's page buffer (word at file 0xB3190)
NVM_CSUM_CELLS = 0x7FCC62             # 2 x u16 the device state machines keep
STACK_LO = 0x7FE000                   # the emulator's stack page (r1 reset below 0x7FEFFC)


def _emu_imports():
    from emu import Med9Emu
    import emu.qspi_eeprom as q
    return Med9Emu, q


@requires_dump
@tff.requires_emu
class TestPersistOffsetOffTheChannels(DumpUnchanged):
    """G7 (#38): block 8 payload +2..+18 are the 17 adaptation channels.

    Three emulator proofs on the stock dump with the QSPI device model of
    `emu/qspi_eeprom.py` (re/findings/eeprom.md section 10): the real
    `adaptation_restore_all` ignores an E% at +19; the real channel reset
    paths overwrite +2 and never +19; and the whole-SRAM (and whole-device)
    difference between an E% of 85 and the default 0 at +19 is that byte and
    the block checksum, nothing else.
    """

    def boot(self, payload: dict, *, restore: bool = True):
        Med9Emu, q = _emu_imports()
        emu = Med9Emu(str(DUMP), r2="app")
        dev = q.M95160(q.factory_image(str(DUMP), payloads={8: payload}))
        q.install_eeprom(emu, device=dev)
        self.assertTrue(q.cold_start(emu), "the queue never reached idle")
        if restore:
            res = emu.call(ADAP_RESTORE_ALL, reset=False, max_insns=4_000_000)
            self.assertTrue(res.ok, res.summary())
        return emu, dev

    def service(self, emu, sub: int, ch: int = 0, val: int = 0) -> bytes:
        emu.write(SVC_REC, bytes([sub, ch, val, 0, 0xEE, 0, 0, 0]))
        res = emu.call(ADAP_SERVICE, args=[SVC_REC], reset=False,
                       max_insns=4_000_000)
        self.assertTrue(res.ok, res.summary())
        return emu.read(SVC_REC, 5)

    def pump_until_done(self, emu) -> None:
        _M, q = _emu_imports()
        for _ in range(60):
            res = emu.call(q.NVM_PUMP_WRAPPER, reset=False, max_insns=4_000_000)
            self.assertTrue(res.ok, res.summary())
            if emu.read(ADAP_HANDLE + 8, 1)[0] != 1:
                break
        self.assertEqual(emu.read(ADAP_HANDLE + 4, 4), bytes([8, 2, 0x11, 8]),
                         "block 8, offset 2, 17 bytes: the channel range")
        self.assertEqual(emu.read(ADAP_HANDLE + 8, 1), b"\x02",
                         "the stock commit completed")

    def defaults(self) -> bytes:
        data = m.load_dump(str(DUMP))
        return bytes(data[BLK8_FLASH_DEFAULT:BLK8_FLASH_DEFAULT + BLK8_LEN - 2])

    # -- the static facts the move rests on --------------------------------
    def test_the_shipped_offset_is_past_the_channels_and_before_replv(self):
        import ffcal001
        cal = ffcal001.load_params(tff.FF_FUEL / "ffcal001.json")
        self.assertEqual(cal["ff_persist_offset"], PERSIST_OFF)
        self.assertEqual(cal["ff_persist_block"], 8)
        self.assertNotIn(PERSIST_OFF, ADAP_CHANNELS)
        self.assertTrue(2 <= PERSIST_OFF < BLK8_LEN - 3,
                        "never the stamp, never the manager's ReplV byte +29")
        self.assertEqual(ffcal001.VERSION, 5, "a default change, no bump")

        data = m.load_dump(str(DUMP))
        off = m.cpu_to_file(ADAP_DESCRIPTOR)
        self.assertEqual(bytes(data[off:off + 2]), b"\x08\x02",
                         "block 8, channel k at index k + 1")
        self.assertEqual(struct.unpack_from(">I", data, off + 4)[0],
                         ADAP_CH1_RAM, "PTR[0] is channel 1")
        loop = m.cpu_to_file(0x12E4B0)
        self.assertEqual(bytes(data[loop:loop + 4]), bytes.fromhex("2c1f0011"),
                         "cmpwi r31,0x11: the restore loop stops at channel 17")
        self.assertEqual(self.defaults()[19:29], bytes(10),
                         "the flash default record holds 0x00 at +19..+28")

    # -- (b) the restore loop ------------------------------------------------
    def test_the_restore_loop_ignores_an_e_pct_at_19(self):
        emu, _ = self.boot({PERSIST_OFF: bytes([85])})
        self.assertEqual(emu.read(BLK8_MIRROR + PERSIST_OFF, 1), b"\x55",
                         "the start-up read kept the byte")
        self.assertEqual(emu.read(ADAP_CH1_RAM, 1), b"\x00",
                         "channel 1 stays at its default 0")
        ref, _ = self.boot({})
        self.assertEqual(emu.read(*ADAP_RAM), ref.read(*ADAP_RAM),
                         "no channel byte 0x7FD062-0x7FD06D moved")
        dflt = self.defaults()
        chan = [2 + i for i in range(17)]
        ptrs = [struct.unpack_from(">I", m.load_dump(str(DUMP)),
                                   m.cpu_to_file(ADAP_DESCRIPTOR) + 4 + 4 * i)[0]
                for i in range(17)]
        for idx, ptr in zip(chan, ptrs):
            if ADAP_RAM[0] <= ptr < ADAP_RAM[0] + ADAP_RAM[1]:
                with self.subTest(payload=idx, ram=hex(ptr)):
                    self.assertEqual(emu.read(ptr, 1)[0], dflt[idx])

    def test_whole_sram_the_e_pct_at_19_reaches_nothing_but_its_byte(self):
        """E% 85 vs the default 0 at +19, after the start-up read and the
        restore loop: the whole SRAM differs in the mirror byte and in the
        block checksum only."""
        a, _ = self.boot({PERSIST_OFF: bytes([85])})
        b, _ = self.boot({})
        sa = a.read(tff.SRAM_START, tff.SRAM_LEN)
        sb = b.read(tff.SRAM_START, tff.SRAM_LEN)
        changed = {tff.SRAM_START + i for i in range(tff.SRAM_LEN) if sa[i] != sb[i]}
        csum = {BLK8_MIRROR + BLK8_LEN - 2, BLK8_MIRROR + BLK8_LEN - 1}
        self.assertTrue(changed - csum <= {BLK8_MIRROR + PERSIST_OFF},
                        sorted(hex(x) for x in changed))
        self.assertIn(BLK8_MIRROR + PERSIST_OFF, changed)

    def test_the_old_offset_2_is_copied_into_channel_1(self):
        """Why the default moved: at +2 the restore loop hands the E% to
        0x7FD06B (harmless here only because its reader clamps to 0/0)."""
        emu, _ = self.boot({PERSIST_OFF_E4: bytes([85])})
        self.assertEqual(emu.read(ADAP_CH1_RAM, 1), b"\x55")

    # -- (c) the channel resets ----------------------------------------------
    def _reset_all(self, off: int):
        emu, dev = self.boot({off: bytes([85])})
        res = emu.call(ADAP_RESET_ALL, reset=False, max_insns=4_000_000)
        self.assertTrue(res.ok, res.summary())
        self.pump_until_done(emu)
        return emu, dev

    def _check_device(self, dev, off: int, want: int):
        dflt = self.defaults()
        for base in BLK8_DEV:
            with self.subTest(copy=hex(base), offset=off):
                raw = bytes(dev.mem[base:base + BLK8_LEN])
                self.assertTrue(blk8_csum_ok(raw))
                self.assertEqual(raw[:2], BLK8_STAMP)
                self.assertEqual(raw[off], want)
                for i in ADAP_CHANNELS:
                    if i != off:
                        self.assertEqual(raw[i], dflt[i], f"channel byte +{i}")

    def test_the_stock_reset_all_zeroes_offset_2_and_spares_19(self):
        """0x038D64 (no access check): defaults over +2..+18, then commit."""
        _emu, dev = self._reset_all(PERSIST_OFF)
        self._check_device(dev, PERSIST_OFF, 85)
        _emu, dev = self._reset_all(PERSIST_OFF_E4)
        self._check_device(dev, PERSIST_OFF_E4, 0)       # E85 became E0

    def test_after_the_reset_all_a_power_cut_still_restores_19(self):
        _emu, dev = self._reset_all(PERSIST_OFF)
        cold, _ = self.boot_image(bytes(dev.mem))
        self.assertEqual(cold.read(BLK8_MIRROR + PERSIST_OFF, 1), b"\x55")

    def boot_image(self, image: bytes):
        Med9Emu, q = _emu_imports()
        emu = Med9Emu(str(DUMP), r2="app")
        dev = q.M95160(image)
        q.install_eeprom(emu, device=dev)
        self.assertTrue(q.cold_start(emu))
        return emu, dev

    def test_whole_device_and_sram_after_the_reset_all(self):
        """E% 85 vs 0 at +19 through the reset-all + commit: the device and
        the SRAM differ in the E% byte and the checksum of each copy only."""
        a, da = self._reset_all(PERSIST_OFF)
        emu_b, db = self.boot({})
        res = emu_b.call(ADAP_RESET_ALL, reset=False, max_insns=4_000_000)
        self.assertTrue(res.ok, res.summary())
        self.pump_until_done(emu_b)
        dev_changed = {i for i in range(len(da.mem)) if da.mem[i] != db.mem[i]}
        allowed = set()
        for base in BLK8_DEV:
            allowed |= {base + PERSIST_OFF, base + BLK8_LEN - 2, base + BLK8_LEN - 1}
        self.assertTrue(dev_changed <= allowed, sorted(hex(x) for x in dev_changed))
        self.assertIn(BLK8_DEV[0] + PERSIST_OFF, dev_changed)
        sa = a.read(tff.SRAM_START, tff.SRAM_LEN)
        sb = emu_b.read(tff.SRAM_START, tff.SRAM_LEN)
        changed = {tff.SRAM_START + i for i in range(tff.SRAM_LEN) if sa[i] != sb[i]}
        # The device write leaves copies of the block behind: the manager's
        # page buffer (pointer at file 0xB3190), the per-copy checksum cells
        # of its device state machines, and the stack of the QSPI transfer.
        # Each may differ only where the block itself differs.
        allowed = set()
        for base in (BLK8_MIRROR, NVM_PAGE_BUF):
            allowed |= {base + PERSIST_OFF, base + BLK8_LEN - 2, base + BLK8_LEN - 1}
        allowed |= set(range(NVM_CSUM_CELLS, NVM_CSUM_CELLS + 4))
        stack = {x for x in changed if STACK_LO <= x < tff.STACK_TOP + 4}
        self.assertTrue(changed - stack <= allowed,
                        sorted(hex(x) for x in changed - stack))
        ra = a.read(BLK8_MIRROR, BLK8_LEN)
        rb = emu_b.read(BLK8_MIRROR, BLK8_LEN)
        for x in stack:
            with self.subTest(stack=hex(x)):
                i = x - tff.SRAM_START
                self.assertIn(sa[i], (ra[PERSIST_OFF], ra[-2], ra[-1]))
                self.assertIn(sb[i], (rb[PERSIST_OFF], rb[-2], rb[-1]))

    def test_a_tester_channel_0_reset_and_commit(self):
        """KWP service 0x038708: 0x81 channel 0, 0x82, 0x83, pump.

        With this dataset's access words (0x40 = channel 7 only) the reset
        changes no channel at all, at either offset - the commit still writes
        the block.  With the words opened in the emulator (a dataset whose
        tester may reset every channel) +2 is zeroed and +19 survives."""
        for opened in (False, True):
            for off, want_opened in ((PERSIST_OFF, 85), (PERSIST_OFF_E4, 0)):
                with self.subTest(access_open=opened, offset=off):
                    emu, dev = self.boot({off: bytes([85])})
                    if opened:
                        for a in ADAP_ACCESS:
                            emu.write(a, 0xFFFFFFFF, 4)
                    self.assertEqual(self.service(emu, 0x81, 0)[4], 2)
                    self.assertEqual(self.service(emu, 0x82, 0)[4], 2)
                    self.assertEqual(self.service(emu, 0x83)[4], 1, "queued")
                    self.pump_until_done(emu)
                    self.assertEqual(self.service(emu, 0x83)[4], 2, "done")
                    self._check_device(dev, off, want_opened if opened else 85)


class TestPersistenceThroughTheDeviceAt19(tq.TestPersistenceThroughTheDevice):
    """E4's #38 end-to-end path (tests/test_qspi_eeprom.py) at the shipped
    G7 offset: stage -> commit -> pump -> status 2 -> both device copies ->
    power cut -> the patch reads the E% back.  Plus the regression that is
    the reason for the move: the stock reset-all between the commit and the
    power cut does not take the E% with it."""

    OFFSET = PERSIST_OFF

    def _commit(self):
        from emu.models import flexfuel as ffm
        _M, q = _emu_imports()
        raw = q.factory_image(str(DUMP), payloads={8: {self.OFFSET: b"\xFF"}})
        emu, dev = self.boot(raw)
        for i in range(600):
            self.tick(emu, ffm.frame(e_pct=85, counter=(i // 10) & 0xFF)
                      if i % 10 == 0 else None)
        emu.write(PATCH_RAM + 0x3A, 0, 2)
        self.tick(emu)
        for _ in range(40):
            self.tick(emu)
            if emu.read(self.syms["ff_nvm_req"] + 8, 1)[0] != 1:
                break
        self.tick(emu)
        st = emu.read(PATCH_RAM, 0x40)
        self.assertEqual(st[0x32], 3, "persist_state = DONE")
        return emu, dev, struct.unpack_from(">H", st, 0x30)[0], raw

    def test_the_commit_leaves_every_channel_byte_alone(self):
        _emu, dev, stored, raw = self._commit()
        for base in BLK8_DEV:
            got = bytes(dev.mem[base:base + BLK8_LEN])
            self.assertEqual(got[self.OFFSET], stored)
            self.assertEqual(got[2:19], raw[base + 2:base + 19],
                             "+2..+18 are the adaptation channels")

    def test_a_reset_all_after_the_commit_does_not_lose_the_e_pct(self):
        emu, dev, stored, _raw = self._commit()
        res = emu.call(ADAP_RESET_ALL, reset=False, max_insns=4_000_000)
        self.assertTrue(res.ok, res.summary())
        _M, q = _emu_imports()
        for _ in range(60):
            emu.call(q.NVM_PUMP_WRAPPER, reset=False, max_insns=4_000_000)
            if emu.read(ADAP_HANDLE + 8, 1)[0] != 1:
                break
        self.assertEqual(emu.read(ADAP_HANDLE + 8, 1), b"\x02")
        self.assertEqual(dev.mem[BLK8_DEV[0] + self.OFFSET], stored)

        cold, _dev2 = self.boot(bytes(dev.mem))
        self.tick(cold, pump=False)
        st = cold.read(PATCH_RAM, 0x40)
        self.assertEqual(struct.unpack_from(">H", st, 0x24)[0], stored * 16,
                         "e_key: the E% survived the channel reset")
        self.assertEqual(struct.unpack_from(">H", st, 0x30)[0], stored)


if __name__ == "__main__":                                    # pragma: no cover
    unittest.main()
