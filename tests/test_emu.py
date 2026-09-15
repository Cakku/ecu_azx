"""emu/: Unicorn harness on the verified MED9.1.1 map (issue #21, Unicorn half).

Three scenarios from brief A5:
  (a) boot_or_adjust at 0x11E44 with the RAM flag at 0x7FE9E8 clear and set
  (b) the reset path from 0x100: how far it gets and what it waits for
  (c) the DECRAM copy at 0x120C8-0x120FC and the routine that runs at 0x6F8000
"""
from __future__ import annotations

import unittest

from tests.common import DUMP, DumpUnchanged, requires_dump

import med9lib as m  # noqa: E402
from emu import Med9Emu  # noqa: E402
from emu import memmap as mm  # noqa: E402

# --- addresses under test (docs/02_memory_map.md sections 3 and 7) ----------
BOOT_OR_ADJUST = 0x11E44
OR_FLAG_BYTE = 0x7FE9E8            # r13-0x1608 with r13 = 0x7FFFF0
RESET_VECTOR = 0x100
DECRAM_COPY = 0x120C8              # the copy loop
DECRAM_COPY_END = 0x12100          # the instruction after `bl 0x6F8000`
DECRAM_SRC = 0x11118               # file == CPU here (low alias)
DECRAM_LEN = 0x238
PLPRCR = 0x6FC284                  # USIU PLL/low-power/reset control
PLPRCR_LOCK_BIT = 0x8000           # set by 0x116FC, polled at 0x11704


def pll_locked(emu: Med9Emu):
    """Read stub: the PLL lock-status bit the boot code polls always reads 0."""
    return lambda pc, addr, size: emu.read_u32(PLPRCR) & ~PLPRCR_LOCK_BIT


@requires_dump
class TestMemoryMap(DumpUnchanged):
    def test_map_agrees_with_med9lib(self):
        mm.check_consistency()                     # raises on overlap/misalignment

    def test_flash_bytes_land_at_the_documented_addresses(self):
        emu = Med9Emu(DUMP)
        data = m.load_dump(str(DUMP))
        # reset vector, on-chip flash base, calibration through the high alias
        self.assertEqual(emu.read(0x100, 4), bytes(data[0x100:0x104]))
        self.assertEqual(emu.read(0x404000, 4), bytes(data[0x200000:0x200004]))
        self.assertEqual(emu.read(0x5C9FF0, 16), bytes(data[0x1C9FF0:0x1CA000]))
        self.assertEqual(emu.read(0x1CEE20, 9), b"03H906032")

    def test_boot_registers(self):
        emu = Med9Emu(DUMP, r2="boot")
        r = emu.call(0x11E54, [])                  # a bare `blr`
        self.assertEqual(r.regs["r1"], 0x7FEFFC)
        self.assertEqual(r.regs["r13"], 0x7FFFF0)
        self.assertEqual(r.regs["r2"], 0x017FF0)
        self.assertEqual(r.regs["msr"] & 0x2000, 0x2000)   # MSR[FP]
        self.assertEqual(Med9Emu(DUMP, r2="app").call(0x11E54, []).regs["r2"], 0x5C9FF0)


@requires_dump
class TestBootOrAdjust(DumpUnchanged):
    """(a) 0x11E44.

    The whole function is four instructions:

        lbz    r12, -0x1608(r13)      ; RAM byte 0x7FE9E8
        cmpwi  r12, 1
        bne    0x11E54
        rlwinm r3, r3, 0, 0x18, 0x16  ; 0x5463062C
        blr

    Decoding 0x5463062C: rS=rA=r3, SH=0, MB=24, ME=22.  MB > ME, so the PPC
    mask is bits [MB..31] + [0..ME] = every bit except bit 23, i.e.
    0xFFFFFEFF.  The function therefore clears one bit, 0x100, and does so
    only when the flag byte is exactly 1.

    Brief A5 asked for r3 = 0xFF800650 to come back as 0xFF800550.  It cannot:
    0xFF800650 does not have bit 0x100 set (0x650 = 0x400|0x200|0x40|0x10), and
    an AND mask can never set a bit, so that input is returned unchanged in
    both cases.  The OR-table entries that do change are the ones holding
    0x1x0, e.g. 0xFF800140 -> 0xFF800040.  (docs/02_memory_map.md section 7
    itself only says "clears a bit", which is correct.)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.emu = Med9Emu(DUMP, r2="boot")

    def run_it(self, r3: int, flag: int) -> int:
        res = self.emu.call(BOOT_OR_ADJUST, [r3], mem={OR_FLAG_BYTE: bytes([flag])})
        self.assertTrue(res.ok, res.summary())
        self.assertEqual(res.issues, [])
        return res.r3

    def test_mask_derived_from_the_instruction_word(self):
        word = m.u32(m.load_dump(str(DUMP)), 0x11E50)
        self.assertEqual(word, 0x5463062C)
        sh, mb, me = (word >> 11) & 0x1F, (word >> 6) & 0x1F, (word >> 1) & 0x1F
        self.assertEqual((sh, mb, me), (0, 24, 22))
        mask = 0
        for bit in range(32):
            on = mb <= bit <= me if mb <= me else (bit >= mb or bit <= me)
            mask |= on << (31 - bit)
        self.assertEqual(mask, 0xFFFFFEFF)

    def test_flag_clear_returns_the_argument_unchanged(self):
        for v in (0xFF800650, 0xFF800140, 0xFFFC0110, 0xFFFF8C20):
            self.assertEqual(self.run_it(v, 0), v)

    def test_flag_set_clears_bit_0x100(self):
        self.assertEqual(self.run_it(0xFF800140, 1), 0xFF800040)
        self.assertEqual(self.run_it(0xFFFC0110, 1), 0xFFFC0010)   # the literal at 0x11F00
        self.assertEqual(self.run_it(0xFFFF8C20, 1), 0xFFFF8C20)   # bit already clear

    def test_doc_example_is_returned_unchanged_in_both_cases(self):
        # First entry of the OR table at file 0x10020 and the value brief A5
        # quotes.  The expected 0xFF800550 is not reachable by an AND mask;
        # see the class docstring.
        self.assertEqual(m.u32(m.load_dump(str(DUMP)), 0x10020), 0xFF800650)
        self.assertEqual(self.run_it(0xFF800650, 0), 0xFF800650)
        self.assertEqual(self.run_it(0xFF800650, 1), 0xFF800650)

    def test_flag_two_is_not_one(self):
        self.assertEqual(self.run_it(0xFF800140, 2), 0xFF800140)


@requires_dump
class TestDecramRoutine(DumpUnchanged):
    """(c) the copy at 0x120C8-0x120FC and the routine it starts at 0x6F8000."""

    def test_copy_lands_at_0x6f8000_and_runs(self):
        emu = Med9Emu(DUMP, r2="boot", trace=True, max_log=100_000)
        self.assertEqual(emu.read(0x6F8000, DECRAM_LEN), b"\0" * DECRAM_LEN)
        res = emu.run(DECRAM_COPY, until=DECRAM_COPY_END, regs={"r29": 0}, max_insns=200_000)

        self.assertTrue(res.ok, res.summary())
        self.assertEqual(res.issues, [])
        data = m.load_dump(str(DUMP))
        self.assertEqual(emu.read(0x6F8000, DECRAM_LEN),
                         bytes(data[DECRAM_SRC:DECRAM_SRC + DECRAM_LEN]))
        in_decram = [pc for pc in res.pc_trace if 0x6F8000 <= pc < 0x6F8800]
        self.assertGreater(len(in_decram), 100)
        self.assertEqual(in_decram[0], 0x6F8000)
        self.assertEqual(in_decram[-1], 0x6F8234)      # the final blr of the routine
        # it reaches past 0x6F80B8, where the old emulator died (docs/02 section 8)
        self.assertIn(0x6F80B8, set(in_decram))
        self.assertEqual(res.unmapped, [])

    def test_every_flash_type_branch_completes(self):
        """The routine dispatches on the low nibble of RAM byte 0x7F800C."""
        emu = Med9Emu(DUMP, r2="boot")
        for mode in (0, 1, 2, 4, 8):
            res = emu.run(DECRAM_COPY, until=DECRAM_COPY_END, regs={"r29": 0},
                          mem={0x7F800C: bytes([mode])}, max_insns=500_000)
            self.assertTrue(res.ok, f"mode {mode}: {res.summary()}")
            self.assertEqual(res.unmapped, [], f"mode {mode}")


@requires_dump
class TestBootPath(DumpUnchanged):
    """(b) run from the reset vector and see where it stops."""

    def test_reset_path_stalls_on_the_pll_lock_bit(self):
        emu = Med9Emu(DUMP, r2="boot", watch_spr=True, max_log=4000)
        res = emu.run(RESET_VECTOR, max_insns=60_000)
        self.assertEqual(res.stop_reason, "insn_limit")

        # It is spinning on PLPRCR, not on SIPEND: SIPEND (0x6FC010) reads 0
        # from the zero-filled stub, which is exactly "no pending interrupt",
        # so the loop at 0x1048-0x1054 exits after a single read.
        sipend = [a for a in res.accesses if a.addr == 0x6FC010]
        self.assertEqual(len(sipend), 1)
        self.assertEqual(sipend[0].pc, 0x104C)

        (pc, addr), n = res.hot_reads(1)[0]
        self.assertEqual(addr, PLPRCR)
        self.assertEqual(pc, 0x11704)
        self.assertGreater(n, 100)

        # SPR 638 (IMMR) is not modelled by Unicorn: the ISB=1 relocation the
        # firmware performs at 0x1004 has no effect, which is why emu/memmap.py
        # hard-codes the relocated addresses.
        self.assertIn("unmodelled SPR 638 read at 0x00001004", res.issues)
        self.assertIn("unmodelled SPR 638 written at 0x00001010", res.issues)

    def test_with_the_pll_stub_it_reaches_the_tpu_self_test(self):
        emu = Med9Emu(DUMP, r2="boot", max_log=4000)
        emu.stub_read(PLPRCR, pll_locked(emu))
        res = emu.run(RESET_VECTOR, max_insns=400_000)

        touched = {a.addr for a in res.accesses}
        # memory controller fully programmed (docs/02 section 3)
        for reg in (0x6FC100, 0x6FC104, 0x6FC108, 0x6FC10C,
                    0x6FC110, 0x6FC114, 0x6FC118, 0x6FC11C):
            self.assertIn(reg, touched, f"{reg:#x} (BR/OR) never touched")
        self.assertIn(0x6FC800, touched)          # UC3F flash control
        # the DECRAM routine really ran during the boot
        self.assertTrue(any(0x6F8000 <= a.pc < 0x6F8800 for a in res.accesses))
        # ... and the boot now waits for TPU3_A parameter RAM instead
        (pc, addr), _n = res.hot_reads(1)[0]
        self.assertTrue(0x704000 <= addr < 0x704400, f"{addr:#x} is not TPU3_A")
        self.assertTrue(0x14604 <= pc <= 0x146C4, f"{pc:#x} outside the TPU scan loop")


if __name__ == "__main__":
    unittest.main()
