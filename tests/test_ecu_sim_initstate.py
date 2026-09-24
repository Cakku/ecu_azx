"""The simulator's power-on state, cell by cell, against `boot.md` section 6.5.

Brief F3 (issues #20 / #38). `logging/ecu_sim.py`'s `Med9Handlers.power_on`
used to hand-seed the KWP security cells; it now calls ten entries of the
firmware's own one-shot init table (`tbl_module_init`, 0x0B1A68) in the
table's order, the way `os_start` does. These tests pin

* that every entry runs and leaves exactly what `boot.md` section 6.4 says;
* that the three things section 6.5 said the simulator contradicted are now
  right: `kwp_sec_level_flags` and `kwp_sec_lfsr_rounds` are **0** after
  power-on, `nvm_mode` is **1**, the CRC state is 0;
* that the LFSR round count 5 appears **only after a real `27 01`**, written
  by `kwp_sid_27_h1` itself, and that the level-2 pair C3 verified still
  works end to end;
* the residue: the cells this file still writes by hand, so the table in the
  module docstring cannot drift away from the code;
* `emu/time_base.py`, without which the level-1 seed loop never exits;
* `FlashCrcTask`: the firmware's own CRC-32 state machine, its published
  value for the stock dump, and that the harness's own edits do not leak into
  it.

Nothing here writes to `data/` and nothing is ever flashed.
"""
from __future__ import annotations

import struct
import sys
import unittest

from tests.common import DUMP, REPO, DumpUnchanged, requires_dump

sys.path.insert(0, str(REPO / "logging"))

try:
    from ecu_sim import (AnimatedRam, DEFAULT_STATICS, FlashCrcTask,
                         INIT_ENTRIES, Med9Handlers)
    from emu import Med9Emu, qspi_eeprom as qe
    from emu.time_base import READ_TIME_BASE, VirtualTimeBase
    from med9kwp.kwp import key_level1, key_level2
    available = True
except Exception:                                            # pragma: no cover
    available = False
requires_sim = unittest.skipUnless(available, "unicorn / python-can missing")

# the cells boot.md 6.4 / 6.5 name, and what the firmware's own entries leave
SEC_LEVEL_FLAGS = 0x7FB781
SEC_RETRY = 0x7FB780
SEC_LFSR_ROUNDS = 0x7FB770
SEC_DELAY_TIMER = 0x7FB748
SEC_SEED = 0x7FB774
NVM_MODE = 0x7FCD68
CRC_STATE = 0x7FB6F4
CRC_DONE_FLAGS = 0x801200
SESSION_CURRENT = 0x803D3E
SECURITY_STATE = 0x803D3C

#: index 38, then 83/84, then 72 -- {address: the word the entry installs}
INIT_POINTERS = {
    0x8037E4: 0x7F8892, 0x8037E8: 0x7F8893, 0x8037EC: 0x7F889A,
    0x8038D4: 0x7F88AC,
    0x7FB074: 0x802C1E, 0x7FB078: 0x802C0C, 0x7FB07C: 0x802C1E,
    0x7FB088: 0x802BF8, 0x7FB094: 0x802C5E, 0x7FB098: 0x802C52,
    0x7FB09C: 0x802C5E, 0x7FB0A8: 0x802C94,
}
#: the stock image's own reported Flash-Pruefsumme (brief F3)
STOCK_CRC = 0x5562139F


def handlers(**kw) -> "Med9Handlers":
    kw.setdefault("animate", False)
    return Med9Handlers(str(DUMP), **kw)


@requires_dump
@requires_sim
class TestPowerOnRunsTheFirmwaresOwnEntries(DumpUnchanged):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.h = handlers()

    def test_every_init_entry_returned(self):
        self.assertEqual([i for i, _n, _a, ok in self.h.init_log if not ok], [],
                         self.h.log)
        self.assertEqual([i for i, _n, _a, _ok in self.h.init_log],
                         [i for i, _a, _n in INIT_ENTRIES])

    def test_the_entries_are_called_in_the_tables_own_order(self):
        idx = [i for i, _a, _n in INIT_ENTRIES]
        self.assertEqual(idx, sorted(idx), "os_start walks the array in order")

    def test_kwp_sec_init_left_the_security_cells_at_zero(self):
        """boot.md 6.5 item 1: index 71 does the opposite of the hand-seed."""
        self.assertEqual(self.h.emu.read(SEC_LEVEL_FLAGS, 1), b"\x00")
        self.assertEqual(self.h.emu.read(SEC_RETRY, 1), b"\x00")
        self.assertEqual(self.h.emu.read(SEC_LFSR_ROUNDS, 1), b"\x00")

    def test_nvm_mode_is_one(self):
        """boot.md 6.5 item 2: indices 18 then 24 both run."""
        self.assertEqual(struct.unpack(">H", self.h.emu.read(NVM_MODE, 2))[0], 1)

    def test_the_crc_state_is_armed_at_zero(self):
        """boot.md 6.5 item 3: index 75."""
        self.assertEqual(self.h.emu.read(CRC_STATE, 1), b"\x00")
        self.assertEqual(self.h.emu.read(CRC_DONE_FLAGS, 1)[0] & 1, 0)

    def test_the_kwp_ram_pointers_are_installed(self):
        """Indices 38, 83 and 84 -- boot.md 6.4, and the 0x444 seeds."""
        for addr, want in INIT_POINTERS.items():
            with self.subTest(addr=hex(addr)):
                got = struct.unpack(">I", self.h.emu.read(addr, 4))[0]
                self.assertEqual(got, want)
        self.assertEqual(self.h.emu.read(0x802CF8, 2), b"\x04\x44")
        self.assertEqual(self.h.emu.read(0x802CFA, 2), b"\x04\x44")

    def test_ddli_init_filled_the_entry_array_pointers(self):
        """Index 72; E4's test, restated against the table-driven power_on."""
        want = [0x80366C] + [0x80370C + i * 0x18 for i in range(9)]
        got = [struct.unpack(">I", self.h.emu.read(0x804038 + 8 * i + 4, 4))[0]
               for i in range(10)]
        self.assertEqual(got, want)

    def test_the_residue_is_exactly_what_the_docstring_lists(self):
        """The three KWP state bytes power_on still writes by hand."""
        self.assertEqual(self.h.emu.read(SESSION_CURRENT, 1), b"\x00")
        self.assertEqual(self.h.emu.read(SECURITY_STATE, 1), b"\x00")
        self.assertEqual(self.h.emu.read(SEC_SEED, 4), b"\x00" * 4)
        doc = sys.modules["ecu_sim"].__doc__
        for cell in ("kwp_session_current", "kwp_security_state",
                     "kwp_sec_seed", "0x7FAB70 / 0x7FAB74",
                     "0x7FADAC / 0x7FADAD"):
            with self.subTest(cell=cell):
                self.assertIn(cell, doc,
                              "the residue table must name every cell that is "
                              "still seeded outside the init entries")

    def test_a_second_power_on_is_a_real_power_cycle(self):
        h = handlers()
        h.emu.write(SESSION_CURRENT, b"\x03")
        h.emu.write(SEC_LFSR_ROUNDS, b"\x05")
        h.power_on()
        self.assertEqual(h.emu.read(SESSION_CURRENT, 1), b"\x00")
        self.assertEqual(h.emu.read(SEC_LFSR_ROUNDS, 1), b"\x00")


@requires_dump
@requires_sim
class TestSecurityAccessFromAColdSimulator(DumpUnchanged):
    """`27 01`/`27 02` and `27 03`/`27 04` with nothing hand-seeded."""

    def test_27_01_arms_the_lfsr_and_27_02_grants_level_1(self):
        h = handlers()
        h.handle(b"\x10\x89")
        self.assertEqual(h.emu.read(SEC_LFSR_ROUNDS, 1), b"\x00",
                         "the round count must not exist before the seed")
        answer = h.handle(b"\x27\x01")[-1]
        self.assertEqual(answer[:2], b"\x67\x01")
        self.assertEqual(h.emu.read(SEC_LFSR_ROUNDS, 1), b"\x05",
                         "kwp_sid_27_h1 writes 5 at 0x03635C")
        self.assertEqual(h.emu.read(SEC_LEVEL_FLAGS, 1), b"\x01",
                         "and sets bit 0 of kwp_sec_level_flags")
        seed = int.from_bytes(answer[2:6], "big")
        self.assertTrue(seed & 0xFF000000,
                        "the seed loop refuses a top byte of zero")
        key = key_level1(seed)
        self.assertEqual(h.handle(b"\x27\x02" + key.to_bytes(4, "big"))[-1],
                         b"\x67\x02\x34")
        self.assertEqual(h.security_state, 2)

    def test_a_wrong_level_1_key_is_refused_and_arms_the_lockout(self):
        h = handlers()
        h.handle(b"\x10\x89")
        seed = int.from_bytes(h.handle(b"\x27\x01")[-1][2:6], "big")
        bad = (key_level1(seed) ^ 0xFFFF) & 0xFFFFFFFF
        answers = h.handle(b"\x27\x02" + bad.to_bytes(4, "big"))
        self.assertEqual(answers[-1], b"\x7f\x27\x35")
        self.assertEqual(h.security_state, 0)
        self.assertGreater(
            struct.unpack(">H", h.emu.read(SEC_DELAY_TIMER, 2))[0], 0)

    def test_the_level_2_pair_still_works_end_to_end(self):
        """C3's path, with kwp_sec_level_flags no longer hand-seeded: the
        level-2 seed handler sets its own bit at 0x36558."""
        h = handlers(seed=0x12345678)
        h.handle(b"\x10\x89")
        answer = h.handle(b"\x27\x03")[-1]
        self.assertEqual(answer, b"\x67\x03\x12\x34\x56\x78")
        self.assertEqual(h.emu.read(SEC_LEVEL_FLAGS, 1)[0] & 2, 2)
        key = key_level2(0x12345678)
        self.assertEqual(h.handle(b"\x27\x04" + key.to_bytes(4, "big"))[-1],
                         b"\x67\x04\x34")
        self.assertEqual(h.security_state, 3)

    def test_the_lockout_timer_comes_from_the_modelled_eeprom_mirror(self):
        """A factory-shaped image holds 0x0000 at block 11 payload +0x0C."""
        import tempfile
        from pathlib import Path
        tmp = Path(tempfile.mkdtemp())
        path = tmp / "eeprom.bin"
        path.write_bytes(qe.factory_image(str(DUMP)))
        h = handlers(eeprom=str(path))
        self.assertEqual(h.emu.read(0x7FA02C, 2), b"\x00\x00",
                         "the factory record for block 11 is 0b 02 00 00 ...")
        self.assertEqual(h.emu.read(SEC_DELAY_TIMER, 2), b"\x00\x00")

        # and a mirror that is NOT zero is carried through by the real entry
        payload = bytearray(b"\x0b\x02" + b"\x00" * 28)
        payload[0x0C:0x0E] = b"\x01\x2C"          # 300 ticks
        path.write_bytes(qe.factory_image(str(DUMP),
                                          payloads={11: bytes(payload[:0x1E])}))
        h2 = handlers(eeprom=str(path))
        self.assertEqual(h2.emu.read(0x7FA02C, 2), b"\x01\x2c",
                         "the block-11 mirror carries it")
        self.assertEqual(h2.emu.read(SEC_DELAY_TIMER, 2), b"\x01\x2c",
                         "kwp_sec_init loads 0x7FB748 from it")
        h2.handle(b"\x10\x89")
        self.assertEqual(h2.handle(b"\x27\x01")[-1], b"\x7f\x27\x37",
                         "and a seed request is then refused with NRC 0x37")


@requires_dump
@requires_sim
class TestVirtualTimeBase(DumpUnchanged):
    def test_the_stock_time_base_is_frozen_at_zero(self):
        emu = Med9Emu(str(DUMP), r2="app")
        res = emu.call(READ_TIME_BASE, reset=False)
        self.assertTrue(res.ok)
        self.assertEqual((res.regs["r3"] << 32) | res.regs["r4"], 0)

    def test_the_virtual_one_moves_and_never_repeats(self):
        emu = Med9Emu(str(DUMP), r2="app")
        tb = VirtualTimeBase(emu)
        seen = []
        for t in (0.0, 0.5, 0.5, 0.5, 2.0):
            tb.advance(t)
            res = emu.call(READ_TIME_BASE, reset=False)
            self.assertTrue(res.ok, res.stop_reason)
            seen.append((res.regs["r3"] << 32) | res.regs["r4"])
        self.assertEqual(seen, sorted(seen))
        self.assertEqual(len(set(seen)), len(seen),
                         "the level-1 seed loop rejects a repeated value")
        for v in seen:
            self.assertTrue(v & 0xFF000000,
                            "and one whose low word's top byte is zero")

    def test_without_it_the_level_1_seed_handler_never_returns(self):
        h = handlers(time_base=False)
        h.handle(b"\x10\x89")
        with self.assertRaises(RuntimeError) as ctx:
            h.handle(b"\x27\x01")
        self.assertIn("did not return", str(ctx.exception))


@requires_dump
@requires_sim
class TestFlashCrcTask(DumpUnchanged):
    """The firmware's own CRC-32 state machine, driven activation by activation."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.h = handlers(flash_crc=True)
        cls.crc = cls.h.flash_crc.run_to_completion()

    def test_the_range_list_is_the_one_flash_programming_5_3_names(self):
        self.assertEqual(self.h.flash_crc.ranges,
                         [(0x020000, 0x1BFFFF), (0x404000, 0x47FFFF),
                          (0x5C2E00, 0x5FFFFF)])

    def test_the_published_value_is_the_stock_images_own(self):
        self.assertEqual(self.crc, STOCK_CRC)
        self.assertEqual(self.h.emu.read(0x7F9178, 2),
                         struct.pack(">H", STOCK_CRC >> 16))
        self.assertEqual(self.h.emu.read(0x7F917A, 2),
                         struct.pack(">H", STOCK_CRC & 0xFFFF))
        self.assertEqual(self.h.emu.read(0x7F9176, 1)[0] & 1, 1)
        self.assertEqual(self.h.emu.read(CRC_DONE_FLAGS, 1)[0] & 1, 1)
        self.assertEqual(self.h.flash_crc.errors, [])

    def test_it_matches_an_independent_crc32_of_the_same_ranges(self):
        import zlib
        import med9lib as ml
        data = ml.load_dump(str(DUMP))
        crc = 0
        for start, end in self.h.flash_crc.ranges:
            crc = zlib.crc32(bytes(data[ml.cpu_to_file(start):
                                        ml.cpu_to_file(end) + 1]), crc)
        self.assertEqual(crc, STOCK_CRC,
                         "one reflected CRC-32 over the concatenation")

    def test_the_activation_count_is_the_documented_one(self):
        self.assertEqual(self.h.flash_crc.activations, 24627)

    def test_it_publishes_in_background_loop_4926(self):
        """G3's period (boot.md 6.8): five activations per loop of task 0."""
        c = self.h.flash_crc
        self.assertEqual(c.published_loop, 4926)
        self.assertEqual(c.loops, 4926)
        self.assertEqual(c.loop_counter, 4926,
                         "0x7FD70C counts background loops")
        self.assertAlmostEqual(c.bg_loop_s, 0.050)
        self.assertAlmostEqual(c.publish_s, 4926 * 0.050, places=6)
        self.assertAlmostEqual(c.sim_t, c.publish_s, places=6)

    def test_a_warm_start_is_exactly_what_the_cold_run_left(self):
        """flash_crc.json item 1: state 7 and nothing moves."""
        cells = {0x7FB6F4: 1, 0x7FB6F5: 1, 0x7FB6F6: 2, 0x7FB6F8: 4,
                 0x7FB6FC: 4, 0x7FB700: 4, 0x7F9176: 1, 0x7F9178: 4,
                 0x801200: 1}
        warm = handlers(flash_crc_warm=True)
        for addr, size in cells.items():
            with self.subTest(addr=hex(addr)):
                self.assertEqual(warm.emu.read(addr, size),
                                 self.h.emu.read(addr, size))
        self.assertTrue(warm.flash_crc.done)
        self.assertEqual(warm.flash_crc.crc, STOCK_CRC)

    def test_the_harness_edit_inside_the_hashed_range_is_hidden(self):
        """emu/time_base.py rewrites 0x47846C, inside range 1."""
        shadow = self.h.flash_crc.shadow
        self.assertTrue(shadow, "the virtual time base must have been found")
        self.assertTrue(all(0x404000 <= a <= 0x47FFFF for a in shadow),
                        shadow)
        # the rewrite is still in place after the whole CRC ran
        self.assertNotEqual(self.h.emu.read(0x478470, 4),
                            bytes.fromhex("7C8C42E6"))

    @staticmethod
    def _hash_the_patched_words(hide: bool) -> int:
        """One activation over the 12 bytes the time base rewrote, alone.

        Cheaper than a second full run and it isolates the question: hashing
        0x47846C-0x478477 must give the image's own bytes when the shadow is
        on and the harness's when it is off.
        """
        h = handlers()
        task = FlashCrcTask(h.emu, hide_harness_writes=hide)
        task.activate()                             # state 0: build the table
        h.emu.write(0x7FB6F4, 1, 1)                 # state 1, hashing
        h.emu.write(0x7FB6F5, 1, 1)                 # inside range 1
        h.emu.write(0x7FB6F6, 100, 2)               # a full byte budget
        h.emu.write(0x7FB6F8, 0xFFFFFFFF, 4)        # the initial register
        h.emu.write(0x7FB700, 0x47846C, 4)          # from the first word ...
        h.emu.write(0x7FB6FC, 0x478477, 4)          # ... to the third
        task.activate()
        return struct.unpack(">I", h.emu.read(0x7FB6F8, 4))[0]

    def test_without_the_shadow_the_harness_changes_the_answer(self):
        with_shadow = self._hash_the_patched_words(True)
        without = self._hash_the_patched_words(False)
        self.assertNotEqual(with_shadow, without)

        import zlib
        import med9lib as ml
        data = ml.load_dump(str(DUMP))
        stock = bytes(data[ml.cpu_to_file(0x47846C):ml.cpu_to_file(0x478478)])
        self.assertEqual(with_shadow ^ 0xFFFFFFFF, zlib.crc32(stock),
                         "the shadowed activation hashes the image's bytes")

    def test_a_task_that_has_published_does_nothing_more(self):
        before = self.h.flash_crc.activations
        self.assertFalse(self.h.flash_crc.activate())
        self.assertEqual(self.h.flash_crc.activations, before)

    def test_it_is_off_unless_asked_for(self):
        self.assertIsNone(handlers().flash_crc)


@requires_dump
@requires_sim
class TestTheLoggerStillSeesTheSameEcu(DumpUnchanged):
    """E4's read-back test, and the services `--self-test` covers."""

    DYNAMIC = [(0x7FEE74, 2), (0x7FED38, 2), (0x8021EF, 1), (0x803038, 2),
               (0x7FD754, 4)]

    def test_five_dynamic_ids_read_back_byte_for_byte(self):
        h = handlers(animate=True,
                     ram=AnimatedRam(live_task_set="A",
                                     statics=dict(DEFAULT_STATICS)))
        h.handle(b"\x10\x89")
        for n, (addr, size) in enumerate(self.DYNAMIC):
            lid = 0xF0 + n
            h.handle(bytes([0x2C, lid, 0x04]))
            self.assertEqual(
                h.handle(bytes([0x2C, lid, 0x03, 0x01, size,
                                (addr >> 16) & 0xFF, (addr >> 8) & 0xFF,
                                addr & 0xFF]))[-1][:2],
                bytes([0x6C, lid]))
        for n, (addr, size) in enumerate(self.DYNAMIC):
            lid = 0xF0 + n
            with self.subTest(lid=hex(lid)):
                got = h.handle(bytes([0x21, lid]))[-1]
                self.assertEqual(got[:2], bytes([0x61, lid]))
                self.assertEqual(got[2:], h.emu.read(addr, size))

    def test_the_self_test_still_passes(self):
        import io
        import contextlib
        import ecu_sim
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok = ecu_sim.self_test(str(DUMP))
        self.assertTrue(ok, buf.getvalue())


if __name__ == "__main__":                                    # pragma: no cover
    unittest.main()
