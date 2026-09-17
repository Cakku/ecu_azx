"""patches/ff_fuel's E1 half: the ignition blend (#34, the ignition rule of #37).

Six layers, each one able to fail on its own:

  1. the stock facts the hook rests on - the word at 0x41D40C, the four TKMWL
     words of ids 2192-2195, the eight group-108 words, and the searches that
     say nothing else reads any of them;
  2. apply: the hook word, the handler table and the group table on the image,
     and the ten instructions of the trampoline read back out of the blob;
  3. `zwgru_build` (0x41D38C) on the patched image against the STOCK image,
     over the grid `tests/test_zw_model.py` uses - bit for bit with the
     feature off, and `clamp_s8(stock + k)` with dzw_e forced to +-k;
  4. the trampoline on its own: where it returns, what it leaves in r10/r1,
     and what an invalid state block makes it do;
  5. the producer against `emu/models/flexfuel.py`, tick by tick, over a frame
     sequence that crosses OK -> FAULT -> OK, including the #37 immediate drop;
  6. the diagnostics: measuring block 108 through `measuring_var_dispatch` and
     `21 6C` end to end through `logging/ecu_sim.py` on the patched image.

Plus the two proofs the 2026-09-17 rules ask for - a whole-SRAM diff with the
feature disabled AND with it enabled at neutral calibration - and the
instruction counts the README records.

Nothing here writes to `data/` and nothing is ever flashed.
"""
from __future__ import annotations

import struct
import sys
import unittest

from tests import test_ff_fuel_patch as tff
from tests.common import DUMP, REPO, DumpUnchanged, requires_dump

import med9lib as m  # noqa: E402  (tests.common put tools/ on sys.path)
import patch_gen  # noqa: E402

sys.path.insert(0, str(REPO / "logging"))
sys.path.insert(0, str(REPO / "patches" / "ff_fuel"))
import ffcal001  # noqa: E402
from med9kwp import vag_formulas  # noqa: E402

from emu.models import flexfuel as ff  # noqa: E402
from emu.zw_model import (  # noqa: E402
    Ignition, axis_search_u16_hint, interp_2d_s8, sat_s8,
)

# --- the facts under test (re/findings/ignition.md sections 11 and 13.2) ----
ZW_SITE = 0x41D40C
ZW_OLD = bytes.fromhex("7c635214")          # add r3,r3,r10 -- NOT a branch
ZW_RETURN = 0x41D410                        # the stock s8 clamp
ZWGRU_BUILD = 0x41D38C
ZWGRU_TASK_CALL = 0x4224E0                  # task 41's `bl 0x41D38C`

RAM_NMOT_W = 0x7FEE74
RAM_RL_W = 0x7FEFB2
RAM_HINT_Y = 0x7FD5EC
RAM_HINT_X = 0x7FD5F0
RAM_RECALC_ENABLE = 0x7FEA72
RAM_DZWWL = 0x7FD313
RAM_DZW_EXTRA = 0x7FD337
RAM_ADAPT_04 = 0x800004
RAM_ADAPT_06 = 0x800006
RAM_ADAPT_07 = 0x800007
RAM_ZWGRU = 0x7FD315
RAM_DWKRZ = 0x7FCE57
RAM_ZW_LATCH = 0x7FD31B

PATCH_RAM = tff.PATCH_RAM
OFF_DZW_E = 0x29
OFF_FZW = 0x2A

#: The harness's default stack top (`emu/core.py` STACK_TOP = 0x7FEFFC) is NOT
#: where the ECU's stack is: `re/findings/ram.md` 4.1 and `scheduler.md` 7 put
#: the one ERCOSEK task stack at 0x7FF3C0-0x7FF76F, with r1 = 0x7FF768 out of
#: `app_entry_crt0`.  That matters here and nowhere before, because 0x7FEFFC
#: is only 0x4A bytes above **rl_w at 0x7FEFB2**: a cold-start activation uses
#: ~168 bytes of stack and would overwrite one of the two inputs of the blend
#: while it is reading it.  The engine never does that; only the harness would.
#: Every emulated activation below therefore runs on the real task stack.
TASK_STACK = 0x7FF768

# --- the measuring slots (re/findings/measuring_vars.md section 8.4) --------
TKMWL = 0x0A5658
STUB = 0x00038EC4
DISPATCH = 0x045768
GROUP_TABLE = 0x5C5518
IDS = (2192, 2193, 2194, 2195)
GROUP = 108
SYMS = ("ff_diag_fzw_pct", "ff_diag_dzw", "ff_diag_dwkrz", "ff_diag_zwlatch")
SLOTS = tuple(TKMWL + 4 * i for i in IDS)
GROUP_WORDS = tuple(GROUP_TABLE + f * 0x1FE + GROUP * 2 for f in range(4))

RESULT_FORMULA = 0x7FD06F
RESULT_B = 0x7FD070
RESULT_A = 0x7FD071

#: the nmot_w / rl_w grid tests/test_zw_model.py drives zwgru_build over
GRID = [(nmot_w, rl_w)
        for nmot_w in (1000, 2080, 2500, 8000, 12000, 15000, 20000, 26080, 30000)
        for rl_w in (200, 416, 700, 1280, 2000, 2560, 3424, 4256, 5000)]


def zw_cal(**over) -> bytes:
    """An FFCAL001 image with the ignition blend set up as the test wants."""
    params = ffcal001.load_params(tff.FF_FUEL / "ffcal001.json")
    return ffcal001.build(dict(params, **over))


def ramped_map() -> list[int]:
    """A non-neutral ff_dzw_map: +0 at the bottom left, +8 at the top right."""
    return [min(8, (iy + ix) // 2) for iy in range(8) for ix in range(8)]


# ------------------------------------------------------ 1. the stock facts --
@requires_dump
class TestStockFacts(DumpUnchanged):
    def setUp(self):
        self.data = m.load_dump(str(DUMP))

    def _read(self, cpu: int, n: int) -> bytes:
        off = m.cpu_to_file(cpu)
        return bytes(self.data[off:off + n])

    def test_the_site_holds_the_add_the_trampoline_re_does(self):
        self.assertEqual(self._read(ZW_SITE, 4), ZW_OLD)
        with self.assertRaises(patch_gen.PatchError):
            patch_gen.decode_branch(int.from_bytes(ZW_OLD, "big"), ZW_SITE)

    def test_the_clamp_that_saturates_our_offset_follows_it(self):
        """0x41D410 cmpwi r3,0x7f ... 0x41D428 li r3,-0x80 (ignition.md 11.1)."""
        self.assertEqual(self._read(ZW_RETURN, 4), bytes.fromhex("2c03007f"))
        self.assertEqual(self._read(0x41D420, 4), bytes.fromhex("2c03ff80"))
        self.assertEqual(self._read(0x41D42C, 4), bytes.fromhex("8001000c"),
                         "the function reloads the LR it saved at 0x41D394, "
                         "which is why a bl at the site needs no frame")
        self.assertEqual(self._read(0x41D394, 4), bytes.fromhex("9001000c"))

    def test_zwgru_build_is_called_once_per_ignition_event(self):
        kind, target = patch_gen.decode_branch(
            int.from_bytes(self._read(ZWGRU_TASK_CALL, 4), "big"), ZWGRU_TASK_CALL)
        self.assertEqual((kind, target), ("bl", ZWGRU_BUILD))

    def test_the_four_tkmwl_slots_hold_the_not_available_stub(self):
        self.assertEqual(self._read(SLOTS[0], 16), STUB.to_bytes(4, "big") * 4)

    def test_group_108_and_its_echo_are_empty(self):
        for g in (GROUP, GROUP + 0x7F):
            for f in range(4):
                with self.subTest(group=g, field=f + 1):
                    self.assertEqual(
                        self._read(GROUP_TABLE + f * 0x1FE + g * 2, 2), b"\0\0")

    def test_the_slots_are_still_free_according_to_the_tool(self):
        """`measuring_vars.py --free`, as the 2026-09-17 rules require."""
        import measuring_vars as mv
        dump = mv.Dump(str(DUMP))
        disp, base, count = mv.find_dispatcher(dump.d)
        self.assertEqual((disp, base, count), (DISPATCH, TKMWL, 2200))
        ptrs = [dump.word(base + 4 * i) for i in range(count)]
        spare, free = mv.free_slots(dump, GROUP_TABLE, ptrs, STUB)
        for vid in IDS:
            self.assertIn(vid, spare, f"id {vid} is not spare any more")
        self.assertIn(GROUP, free, "group 108 is not free any more")

    def test_nothing_else_references_the_four_table_words(self):
        """The three negative searches measuring_vars.md section 8.4 records."""
        import find_abs_refs
        import find_branch_refs
        inside = [t for _i, _k, t, _kind in find_abs_refs.resolve(self.data)
                  if 0x0A7898 <= t <= 0x0A78A7]
        self.assertEqual(inside, [], "a lis/D-form pair forms one of our words")
        hits = find_branch_refs.scan(self.data, list(SLOTS))
        for slot in SLOTS:
            self.assertEqual(hits[slot], [], f"{slot:#x} is branched to or stored")
        words = find_branch_refs.scan(self.data, list(GROUP_WORDS))
        for w in GROUP_WORDS:
            self.assertEqual(words[w], [], f"{w:#x} is branched to or stored")

    def test_the_low_octane_latch_bits_are_where_ignition_md_says(self):
        """bit0 set at 0x0F43D0, bit1 at 0x0F4450, both stored to 0x7FD31B."""
        self.assertEqual(self._read(0x0F43D0, 4), bytes.fromhex("61830001"))
        self.assertEqual(self._read(0x0F43E8, 4), bytes.fromhex("986dd32b"))
        self.assertEqual(self._read(0x0F4450, 4), bytes.fromhex("61830002"))
        self.assertEqual(self._read(0x0F4468, 4), bytes.fromhex("986dd32b"))

    def test_the_stock_knock_handlers_use_the_formula_we_copied(self):
        """(0x22, A=0x4B, B = byte + 0x80) at 0x039CD0 -- fields 2 and 3."""
        self.assertEqual(self._read(0x039CD0, 4), bytes.fromhex("38600022"))
        self.assertEqual(self._read(0x039CD8, 4), bytes.fromhex("3880004b"))
        self.assertEqual(self._read(0x039CDC, 4), bytes.fromhex("38a50080"))


# ----------------------------------------------------------- 2. the apply --
class TestIgnApply(tff.TestApply):
    """Re-uses the applied image; adds what E1 writes."""

    def test_the_hook_word_points_at_the_trampoline(self):
        syms = tff.load_patch()["build"]["symbols"]
        off = m.cpu_to_file(ZW_SITE)
        word = int.from_bytes(bytes(self.data[off:off + 4]), "big")
        self.assertEqual(patch_gen.decode_branch(word, ZW_SITE),
                         ("bl", int(syms["ff_zw_hook"], 0)))

    def test_the_tkmwl_slots_point_at_our_handlers(self):
        syms = tff.load_patch()["build"]["symbols"]
        for slot, name in zip(SLOTS, SYMS):
            with self.subTest(slot=hex(slot)):
                off = m.cpu_to_file(slot)
                self.assertEqual(
                    int.from_bytes(bytes(self.data[off:off + 4]), "big"),
                    int(syms[name], 0))

    def test_the_group_words_name_our_ids(self):
        for word, vid in zip(GROUP_WORDS, IDS):
            with self.subTest(word=hex(word)):
                off = m.cpu_to_file(word)
                self.assertEqual(
                    struct.unpack(">H", bytes(self.data[off:off + 2]))[0], vid)

    def test_the_echo_group_235_is_still_empty(self):
        for f in range(4):
            off = m.cpu_to_file(GROUP_TABLE + f * 0x1FE + (GROUP + 0x7F) * 2)
            self.assertEqual(bytes(self.data[off:off + 2]), b"\0\0")

    def test_ffcal001_carries_e1s_fields_where_v2_put_them(self):
        """E2 bumped the block to v3 by APPENDING; nothing of E1's moved."""
        off = m.cpu_to_file(tff.CAL_BASE)
        blk = bytes(self.data[off:off + ffcal001.LENGTH])
        ffcal001.check(blk)
        self.assertEqual(struct.unpack_from(">H", blk, 0x08)[0], ffcal001.VERSION)
        self.assertEqual(struct.unpack_from(">H", blk, 0x0A)[0], ffcal001.LENGTH)
        self.assertEqual(blk[0xE6], 0, "ff_zw_enable must ship 0")
        self.assertEqual(blk[0x54:0x94], bytes(64), "ff_dzw_map must ship zero")

    def test_the_trampoline_is_the_ten_documented_instructions(self):
        """Read back out of the image, not out of the ELF (README 'Blob')."""
        base = int(tff.load_patch()["build"]["symbols"]["ff_zw_hook"], 0)
        off = m.cpu_to_file(base)
        words = struct.unpack_from(">10I", bytes(self.data), off)
        self.assertEqual(words[0], int.from_bytes(ZW_OLD, "big"),
                         "the first instruction must be the displaced add")
        self.assertEqual(words[1], 0x3D600080, "lis r11,0x80")
        self.assertEqual(words[2] >> 16, 0x818B, "lwz r12,-0x500(r11)")
        self.assertEqual(words[2] & 0xFFFF, (PATCH_RAM - 0x00800000) & 0xFFFF)
        self.assertEqual(words[3], 0x6D8C4646, "xoris r12,r12,0x4646")
        self.assertEqual(words[4], 0x280C3031, "cmplwi r12,0x3031")
        self.assertEqual(words[5], 0x4C820020, "bnelr")
        self.assertEqual(words[6] >> 16, 0x898B, "lbz r12,dzw_e(r11)")
        self.assertEqual(words[6] & 0xFFFF,
                         (PATCH_RAM + OFF_DZW_E - 0x00800000) & 0xFFFF)
        self.assertEqual(words[7], 0x7D8C0774, "extsb r12,r12")
        self.assertEqual(words[8], 0x7C636214, "add r3,r3,r12")
        self.assertEqual(words[9], 0x4E800020, "blr")

    def test_the_trampoline_writes_no_register_the_site_still_needs(self):
        """Disassembled from the image: r1, r2, r13 and r10 are never a target."""
        import blobdis
        base = int(tff.load_patch()["build"]["symbols"]["ff_zw_hook"], 0)
        off = m.cpu_to_file(base)
        lines = list(blobdis.disassemble(bytes(self.data[off:off + 40]), base))
        self.assertEqual(len(lines), 10, "the stub is ten instructions")
        for _addr, _raw, mnem, ops in lines:
            with self.subTest(insn=f"{mnem} {ops}"):
                self.assertNotIn("r2", ops.split(",")[0],
                                 "r2 is the ECU's SDA2 base")
                self.assertNotIn("r13", ops.split(",")[0],
                                 "r13 is the ECU's SDA base")
                dst = ops.split(",")[0].strip()
                self.assertNotIn(dst, ("r1", "r2", "r13", "r10"),
                                 f"{mnem} writes {dst}")
        self.assertEqual([l[2] for l in lines],
                         ["add", "lis", "lwz", "xoris", "cmplwi", "bnelr",
                          "lbz", "extsb", "add", "blr"])


# --------------------------------- 3. zwgru_build, patched against stock ----
class ZwBase(tff.EmuBase):
    """Helpers shared by the zwgru_build and trampoline layers."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ign = Ignition(DUMP)

    @staticmethod
    def _ram(nmot_w: int, rl_w: int) -> dict:
        return {
            RAM_NMOT_W: struct.pack(">H", nmot_w),
            RAM_RL_W: struct.pack(">H", rl_w),
            RAM_HINT_Y: b"\0\0\0\0",
            RAM_HINT_X: b"\0\0\0\0",
            RAM_RECALC_ENABLE: b"\x01",
        }

    def seeded(self, dzw_e: int = 0, *, magic: bool = True) -> bytes:
        """The header+core of a state block carrying `dzw_e`."""
        mdl = ff.FlexFuelModel()
        mdl.state.dzw_e = dzw_e
        mdl.seal()
        blk = bytearray(mdl.block_bytes())
        if not magic:
            blk[0] ^= 0xFF
        return bytes(blk)

    def zwgru(self, emu, nmot_w: int, rl_w: int) -> int:
        res = emu.call(ZWGRU_BUILD, mem=self._ram(nmot_w, rl_w), reset=False)
        self.assertTrue(res.ok, res.summary())
        return struct.unpack(">b", res.snapshot(RAM_ZWGRU, 1))[0]


class TestZwgruBuild(ZwBase):
    def test_with_dzw_e_zero_the_patched_image_is_bit_identical(self):
        """(a) the whole grid, patched vs the untouched dump."""
        from emu import Med9Emu
        stock = Med9Emu(DUMP)
        patched = self.fresh()
        patched.write(PATCH_RAM, self.seeded(0))
        for nmot_w, rl_w in GRID:
            with self.subTest(nmot_w=nmot_w, rl_w=rl_w):
                a = self.zwgru(stock, nmot_w, rl_w)
                b = self.zwgru(patched, nmot_w, rl_w)
                self.assertEqual(a, b)
                self.assertEqual(b, self.ign.zwgru(nmot_w, rl_w,
                                                   dzw_kg=self._delta(stock)))

    def _delta(self, emu) -> int:
        """`zwgru_dzw_kg`'s result, which the model takes as an input."""
        return struct.unpack(">b", emu.read(0x7FD314, 1))[0]

    def test_a_forced_offset_is_a_plain_add_before_the_stock_clamp(self):
        """(b) dzw_e = +-k gives clamp_s8(stock + k) for every k that matters."""
        from emu import Med9Emu
        stock = Med9Emu(DUMP)
        emu = self.fresh()
        for k in (-128, -16, -8, -1, 0, 1, 2, 8, 16, 127):
            emu.write(PATCH_RAM, self.seeded(k))
            for nmot_w, rl_w in GRID[::7]:
                with self.subTest(k=k, nmot_w=nmot_w, rl_w=rl_w):
                    base = self.zwgru(stock, nmot_w, rl_w)
                    got = self.zwgru(emu, nmot_w, rl_w)
                    self.assertEqual(got, sat_s8(base + k))

    def test_an_invalid_state_block_adds_nothing(self):
        """(d) the first milliseconds after power-up, and any corruption."""
        from emu import Med9Emu
        stock = Med9Emu(DUMP)
        for fill in (b"\x00" * 0x40, b"\xFF" * 0x40, bytes(range(0x40)),
                     self.seeded(40, magic=False)):
            emu = self.fresh()
            emu.write(PATCH_RAM, fill)
            for nmot_w, rl_w in GRID[::11]:
                with self.subTest(fill=fill[:4].hex(), nmot_w=nmot_w):
                    self.assertEqual(self.zwgru(emu, nmot_w, rl_w),
                                     self.zwgru(stock, nmot_w, rl_w))

    def test_the_whole_ignition_event_moves_no_ram_outside_our_block(self):
        """The two proofs the 2026-09-17 rules ask for, at the hooked site.

        Task 41's `bl 0x41D38C` at 0x4224E0 is run to completion on the stock
        image and on the patched one - once with `ff_zw_enable` = 0 and once
        with it 1 at the neutral map - and the whole 64 KB of SRAM is compared.
        """
        from emu import Med9Emu
        ram = self._ram(13000, 2144)
        stock = Med9Emu(DUMP)
        stock.reset()
        res = stock.run(ZWGRU_TASK_CALL, until=ZWGRU_TASK_CALL + 4,
                        mem=ram, reset=False, regs={"r1": TASK_STACK})
        self.assertTrue(res.ok or res.pc == ZWGRU_TASK_CALL + 4, res.summary())
        before = res.snapshot(tff.SRAM_START, tff.SRAM_LEN)
        stock_zwgru = struct.unpack(">b", res.snapshot(RAM_ZWGRU, 1))[0]

        for tag, blk in (("disabled", self.cal_blk),
                         ("enabled, neutral map", zw_cal(ff_zw_enable=1))):
            with self.subTest(cal=tag):
                emu = self.fresh(cal=blk)
                emu.write(PATCH_RAM, self.seeded(0))
                res = emu.run(ZWGRU_TASK_CALL, until=ZWGRU_TASK_CALL + 4,
                              mem=ram, reset=False, regs={"r1": TASK_STACK})
                after = res.snapshot(tff.SRAM_START, tff.SRAM_LEN)
                self.assertEqual(struct.unpack(">b",
                                               res.snapshot(RAM_ZWGRU, 1))[0],
                                 stock_zwgru, "zwgru must be bit-identical")
                changed = {tff.SRAM_START + i for i in range(tff.SRAM_LEN)
                           if before[i] != after[i]}
                allowed = set(range(PATCH_RAM, PATCH_RAM + 0x100))
                allowed |= set(range(TASK_STACK - 0x3A8, TASK_STACK + 8))
                self.assertEqual(changed - allowed, set(),
                                 f"{[hex(a) for a in sorted(changed - allowed)]}")

    def test_the_offset_saturates_instead_of_wrapping(self):
        """The stock clamp at 0x41D410 is what makes a big offset safe."""
        emu = self.fresh()
        emu.write(PATCH_RAM, self.seeded(127))
        for nmot_w, rl_w in GRID[::5]:
            self.assertLessEqual(self.zwgru(emu, nmot_w, rl_w), 0x7F)
        emu.write(PATCH_RAM, self.seeded(-128))
        for nmot_w, rl_w in GRID[::5]:
            self.assertGreaterEqual(self.zwgru(emu, nmot_w, rl_w), -0x80)


# ------------------------------------------------ 4. the trampoline alone ---
class TestTrampoline(ZwBase):
    def test_it_returns_to_the_stock_clamp_with_r10_and_r1_untouched(self):
        """(c) LR is set to 0x41D410 by the `bl`; `blr` must land there."""
        emu = self.fresh()
        emu.write(PATCH_RAM, self.seeded(3))
        hook = self.syms["ff_zw_hook"]
        for r3, r10 in ((0, 0), (10, 5), (0x7F, 1), (-20 & 0xFFFFFFFF, 4)):
            with self.subTest(r3=r3, r10=r10):
                res = emu.run(hook, until=ZW_RETURN, reset=False,
                              regs={"r3": r3, "r10": r10, "lr": ZW_RETURN})
                self.assertEqual(res.pc, ZW_RETURN, res.summary())
                want = ((r3 + r10 + 3) & 0xFFFFFFFF)
                self.assertEqual(res.regs["r3"], want)
                self.assertEqual(res.regs["r10"], r10, "r10 must survive")
                self.assertEqual(res.regs["r1"], tff.STACK_TOP,
                                 "no frame: r1 must not move")

    def test_it_writes_no_memory_at_all(self):
        emu = self.fresh()
        emu.write(PATCH_RAM, self.seeded(-4))
        before = bytearray(emu.read(tff.SRAM_START, tff.SRAM_LEN))
        res = emu.run(self.syms["ff_zw_hook"], until=ZW_RETURN, reset=False,
                      regs={"r3": 30, "r10": 2, "lr": ZW_RETURN})
        self.assertEqual(res.pc, ZW_RETURN)
        after = emu.read(tff.SRAM_START, tff.SRAM_LEN)
        self.assertEqual(bytes(before), bytes(after),
                         "the segment-synchronous stub is read-only")

    def test_an_invalid_block_still_re_does_the_displaced_add(self):
        emu = self.fresh()
        emu.write(PATCH_RAM, self.seeded(40, magic=False))
        res = emu.run(self.syms["ff_zw_hook"], until=ZW_RETURN, reset=False,
                      regs={"r3": 11, "r10": 7, "lr": ZW_RETURN})
        self.assertEqual(res.pc, ZW_RETURN, res.summary())
        self.assertEqual(res.regs["r3"], 18, "11 + 7 and nothing else")

    def test_the_cost_per_ignition_event(self):
        """(h) README's hook table records both numbers."""
        emu = self.fresh()
        emu.write(PATCH_RAM, self.seeded(0, magic=False))
        bad = emu.run(self.syms["ff_zw_hook"], until=ZW_RETURN, reset=False,
                      regs={"r3": 1, "r10": 1, "lr": ZW_RETURN})
        self.assertEqual(bad.insns, 6, "no valid state block: 6 instructions")
        emu.write(PATCH_RAM, self.seeded(2))
        good = emu.run(self.syms["ff_zw_hook"], until=ZW_RETURN, reset=False,
                       regs={"r3": 1, "r10": 1, "lr": ZW_RETURN})
        self.assertEqual(good.insns, 10, "the whole stub is 10 instructions")


# ------------------------------------------------------- 5. the producer ----
class TestProducer(tff.EmuBase):
    """`ff_zw_update()` against emu/models/flexfuel.py, tick by tick."""

    def _replay(self, steps, cal_blk, *, nmot_w=8000, rl_w=2144):
        emu = self.fresh(cal=cal_blk)
        emu.write(RAM_NMOT_W, struct.pack(">H", nmot_w))
        emu.write(RAM_RL_W, struct.pack(">H", rl_w))
        mdl = ff.FlexFuelModel(tff.cal_from_block(cal_blk))
        seen = []
        for n, (rx, payload) in enumerate(steps):
            if rx:
                self.arm_frame(emu, payload)
            else:
                self.no_frame(emu)
            res = emu.call(self.syms["ff_fuel_hook_b"], reset=False,
                           regs={"r1": TASK_STACK})
            self.assertTrue(res.ok, f"activation {n}: {res.summary()}")
            mdl.tick(payload if rx else None, nmot_w=nmot_w, rl_w=rl_w)
            self.assertEqual(self.block(emu).hex(), mdl.block_bytes().hex(),
                             f"state differs from the model at activation {n}")
            seen.append(struct.unpack(">b",
                                      emu.read(PATCH_RAM + OFF_DZW_E, 1))[0])
        return emu, mdl, seen

    @staticmethod
    def _stream(n, *, every=10, e_pct=85, status=0, start=0):
        ctr = start
        out = []
        for k in range(n):
            if k % every == 0:
                ctr = (ctr + 1) & 0xFF
                out.append((True, ff.frame(e_pct=e_pct, counter=ctr,
                                           status=status)))
            else:
                out.append((False, None))
        return out

    def test_the_shipped_calibration_never_moves_the_angle(self):
        """(f), first half: ff_zw_enable = 0."""
        _emu, mdl, seen = self._replay(self._stream(400), self.cal_blk)
        self.assertEqual(set(seen), {0})
        self.assertEqual(mdl.state.fzw_q8, 0)
        self.assertGreater(mdl.state.e_filt, 0, "the fuel half still ran")

    def test_enabled_with_the_neutral_map_is_also_inert(self):
        """(f), second half: ff_zw_enable = 1, ff_dzw_map all zero."""
        blk = zw_cal(ff_zw_enable=1)
        _emu, mdl, seen = self._replay(self._stream(400), blk)
        self.assertEqual(set(seen), {0}, "a zero map must give a zero offset")
        self.assertGreater(mdl.state.fzw_q8, 0,
                           "f_zw is still reported, so a tester can see it")

    def test_a_real_map_tracks_the_model_tick_by_tick(self):
        """(e) OK -> FAULT -> OK, with the offset following e_filt."""
        blk = zw_cal(ff_zw_enable=1, ff_dzw_map=ramped_map(), ff_dzw_max=8)
        steps = (self._stream(2000, e_pct=85)          # climb to a real offset
                 + [(False, None)] * 300               # silence -> FAULT
                 + self._stream(300, e_pct=85, start=210))
        _emu, mdl, seen = self._replay(steps, blk)
        self.assertGreater(max(seen), 0, "the offset must have been applied")
        self.assertEqual(mdl.state.mode, ff.MODE_OK)
        self.assertEqual(seen[-1], mdl.state.dzw_e)

    def test_fault_zeroes_the_offset_within_one_activation(self):
        """The #37 ignition rule: no hold, no ramp - unlike the fuel factor."""
        blk = zw_cal(ff_zw_enable=1, ff_dzw_map=ramped_map())
        steps = self._stream(2000, e_pct=85) + [(False, None)] * 200
        _emu, mdl, seen = self._replay(steps, blk)
        self.assertEqual(mdl.state.mode, ff.MODE_FAULT)
        self.assertEqual(seen[-1], 0)
        # the very activation that entered FAULT already carried a 0
        entered = next(i for i in range(2000, len(seen)) if seen[i] == 0)
        self.assertGreater(seen[entered - 1], 0, "it was non-zero before")
        self.assertEqual(set(seen[entered:]), {0}, "and never comes back")
        # ... while the FUEL factor is still held, which is the asymmetry
        self.assertGreater(mdl.state.f_q10, ff.F_MIN)
        self.assertGreater(mdl.state.hold_ticks, 0)

    def test_hold_keeps_computing_from_the_frozen_estimate(self):
        blk = zw_cal(ff_zw_enable=1, ff_dzw_map=ramped_map())
        ok = self._stream(2000, e_pct=85)
        _emu, _mdl, seen_ok = self._replay(ok, blk)
        held = seen_ok[-1]
        self.assertGreater(held, 0)
        steps = ok + self._stream(200, e_pct=0, status=2, start=201)
        _emu, mdl, seen = self._replay(steps, blk)
        self.assertEqual(mdl.state.mode, ff.MODE_HOLD)
        self.assertEqual(seen[-1], held, "HOLD freezes E, so it freezes dzw_e")

    def test_the_offset_is_clamped_to_ff_dzw_max(self):
        for ceiling in (0, 1, 4, 8):
            blk = zw_cal(ff_zw_enable=1, ff_dzw_max=ceiling,
                         ff_dzw_map=[60] * 64)
            with self.subTest(ceiling=ceiling):
                _emu, mdl, seen = self._replay(self._stream(3000), blk)
                self.assertEqual(max(seen), ceiling)
                self.assertEqual(mdl.state.dzw_e, ceiling)

    def test_a_negative_map_retards_and_is_clamped_too(self):
        blk = zw_cal(ff_zw_enable=1, ff_dzw_max=4, ff_dzw_map=[-60] * 64)
        _emu, mdl, seen = self._replay(self._stream(3000), blk)
        self.assertEqual(min(seen), -4)
        self.assertEqual(mdl.state.dzw_e, -4)

    def test_the_two_axes_are_searched_the_way_the_ecu_searches_its_own(self):
        """The map is read at the cell the stock helpers would pick."""
        cal = tff.cal_from_block(zw_cal(ff_zw_enable=1,
                                        ff_dzw_map=ramped_map()))
        mdl = ff.FlexFuelModel(cal)
        for nmot_w in (0, 2080, 3000, 8000, 13000, 26080, 40000):
            for rl_w in (0, 416, 1000, 2144, 3000, 4256, 9000):
                with self.subTest(nmot_w=nmot_w, rl_w=rl_w):
                    ky = axis_search_u16_hint(cal.dzw_nmot_axis, nmot_w, 0)
                    kx = axis_search_u16_hint(cal.dzw_rl_axis, rl_w, 0)
                    self.assertEqual(ff.axis_key8(cal.dzw_nmot_axis, nmot_w), ky)
                    self.assertEqual(ff.axis_key8(cal.dzw_rl_axis, rl_w), kx)
                    self.assertEqual(
                        mdl.dzw_of(nmot_w, rl_w),
                        interp_2d_s8([v if v < 128 else v - 256
                                      for v in cal.dzw_map], 8, ky, kx))

    def test_the_map_is_read_at_the_cell_the_emulator_reads(self):
        """One non-trivial (nmot, rl) driven through the compiled code."""
        blk = zw_cal(ff_zw_enable=1, ff_dzw_map=ramped_map(), ff_dzw_max=16)
        for nmot_w, rl_w in ((2080, 416), (13000, 2144), (26080, 4256),
                             (9000, 1500), (40000, 9000)):
            with self.subTest(nmot_w=nmot_w, rl_w=rl_w):
                _emu, mdl, seen = self._replay(self._stream(3000), blk,
                                               nmot_w=nmot_w, rl_w=rl_w)
                self.assertEqual(seen[-1], mdl.state.dzw_e)

    def test_a_v1_calibration_block_disables_everything(self):
        """A v1 block under a v2 blob reads as corrupt: mode 0, dzw_e 0."""
        blk = bytearray(zw_cal(ff_zw_enable=1, ff_dzw_map=ramped_map()))
        struct.pack_into(">H", blk, 0x08, 1)
        struct.pack_into(">H", blk, ffcal001.CRC_OFF,
                         (~sum(blk[:ffcal001.CRC_OFF])) & 0xFFFF)
        emu = self.fresh(cal=bytes(blk))
        for _ in range(5):
            emu.call(self.syms["ff_fuel_hook_b"], reset=False,
                     regs={"r1": TASK_STACK})
        st = self.block(emu)
        self.assertEqual(st[0x13], 0, "cal_ok must be 0")
        self.assertEqual(st[0x0C], ff.MODE_OFF)
        self.assertEqual(st[OFF_DZW_E], 0)

    def test_the_cost_of_one_activation(self):
        """(h) the periodic side, with the blend doing real work."""
        blk = zw_cal(ff_zw_enable=1, ff_dzw_map=ramped_map())
        emu = self.fresh(cal=blk)
        emu.write(RAM_NMOT_W, struct.pack(">H", 13000))
        emu.write(RAM_RL_W, struct.pack(">H", 2144))
        first = emu.call(self.syms["ff_fuel_hook_b"], reset=False,
                         regs={"r1": TASK_STACK})
        self.arm_frame(emu, ff.frame(e_pct=85, counter=1))
        warm = emu.call(self.syms["ff_fuel_hook_b"], reset=False,
                        regs={"r1": TASK_STACK})
        self.assertLess(first.insns, 2600, "the cold-start activation got heavy")
        self.assertLess(warm.insns, 1000, "the warm activation got heavy")


# ------------------------------------------- 6. the diagnostics, group 108 --
class TestZwDispatcher(tff.EmuBase):
    @staticmethod
    def dispatch(emu, vid: int):
        res = emu.call(DISPATCH, args=[vid], reset=False)
        assert res.ok, res.summary()
        return (emu.read(RESULT_FORMULA, 1)[0],
                emu.read(RESULT_A, 1)[0],
                emu.read(RESULT_B, 1)[0])

    def warm(self, *, ticks=3000, dwkrz=(0,) * 6, latch=0):
        blk = zw_cal(ff_zw_enable=1, ff_dzw_map=ramped_map(), ff_dzw_max=8)
        emu = self.fresh(cal=blk)
        emu.write(RAM_NMOT_W, struct.pack(">H", 13000))
        emu.write(RAM_RL_W, struct.pack(">H", 2144))
        mdl = ff.FlexFuelModel(tff.cal_from_block(blk))
        for i in range(ticks):
            rx = ff.frame(e_pct=85, counter=(i // 10) & 0xFF) if i % 10 == 0 else None
            if rx is not None:
                self.arm_frame(emu, rx)
            else:
                self.no_frame(emu)
            emu.call(self.syms["ff_fuel_hook_b"], reset=False,
                     regs={"r1": TASK_STACK})
            mdl.tick(rx, nmot_w=13000, rl_w=2144)
        emu.write(RAM_DWKRZ, bytes(b & 0xFF for b in dwkrz))
        emu.write(RAM_ZW_LATCH, bytes([latch]))
        return emu, mdl

    def test_the_four_ids_answer_with_the_model_triples(self):
        emu, mdl = self.warm(dwkrz=(0, -2, 0, -5, -1, 0), latch=0)
        want = mdl.triples_zw(dwkrz=(0, -2, 0, -5, -1, 0), zw_latch=0)
        for vid, triple in zip(IDS, want):
            with self.subTest(id=vid):
                self.assertEqual(self.dispatch(emu, vid), triple)
        fzw, dzw, knock, latch = (vag_formulas.decode(*x) for x in want)
        self.assertEqual(fzw.unit, "%")
        self.assertAlmostEqual(dzw.value, mdl.state.dzw_e * 0.75, places=6)
        self.assertAlmostEqual(knock.value, 0.0, places=6,
                               msg="max() over the six bytes, not min()")
        self.assertEqual(latch.value, 0.0)

    def test_the_knock_field_reports_the_worst_cylinder(self):
        for dwkrz, worst in (((0,) * 6, 0),
                             ((-1, -2, -3, -4, -5, -6), -1),
                             ((-8, 0, -8, -8, -8, -8), 0),
                             ((-128,) * 6, -128)):
            with self.subTest(dwkrz=dwkrz):
                emu, _ = self.warm(ticks=20, dwkrz=dwkrz)
                f, a, b = self.dispatch(emu, 2194)
                self.assertEqual((f, a), (0x22, 0x4B))
                self.assertEqual(b, max(0, worst + 128))
                self.assertAlmostEqual(vag_formulas.decode(f, a, b).value,
                                       worst * 0.75, places=6)

    def test_the_latch_field_reports_only_bits_0_and_1(self):
        for latch, want in ((0, 0), (1, 1), (2, 2), (3, 3), (0xFF, 3),
                            (0xFC, 0)):
            with self.subTest(latch=latch):
                emu, _ = self.warm(ticks=20, latch=latch)
                self.assertEqual(self.dispatch(emu, 2195), (0x36, 0, want))

    def test_the_stock_fields_answer_even_without_a_valid_block(self):
        """Fields 3 and 4 are stock cells; a tester must still see them."""
        emu = self.fresh(garbage=b"\xA5" * 0x40)
        emu.write(RAM_DWKRZ, bytes([0xFB] * 6))      # -5
        emu.write(RAM_ZW_LATCH, b"\x01")
        self.assertEqual(self.dispatch(emu, 2192), (0x25, 0, 0))
        self.assertEqual(self.dispatch(emu, 2193), (0x25, 0, 0))
        self.assertEqual(self.dispatch(emu, 2194), (0x22, 0x4B, 123))
        self.assertEqual(self.dispatch(emu, 2195), (0x36, 0, 1))

    def test_a_handler_writes_only_the_three_result_bytes(self):
        emu, _ = self.warm(ticks=50)
        before = emu.read(tff.SRAM_START, tff.SRAM_LEN)
        for vid in IDS:
            emu.call(DISPATCH, args=[vid], reset=False)
        after = emu.read(tff.SRAM_START, tff.SRAM_LEN)
        moved = {tff.SRAM_START + i
                 for i in range(len(before)) if before[i] != after[i]}
        allowed = {RESULT_FORMULA, RESULT_A, RESULT_B, 0x7FD072}
        allowed |= set(range(0x7FB8E8, 0x7FB8EC))    # last-handler scratch
        allowed |= set(range(tff.STACK_TOP - 0x100, tff.STACK_TOP + 8))
        self.assertEqual(moved - allowed, set(),
                         f"{[hex(a) for a in sorted(moved - allowed)]}")

    def test_a_handler_costs_less_than_a_hundred_instructions(self):
        emu, _ = self.warm(ticks=20)
        for vid in IDS:
            with self.subTest(id=vid):
                res = emu.call(DISPATCH, args=[vid], reset=False)
                self.assertTrue(res.ok)
                self.assertLess(res.insns, 100, res.summary())


@requires_dump
class TestGroup108OverKwp(unittest.TestCase):
    """`21 6C` through the firmware's own SID 0x21 route, on the patched image."""

    @classmethod
    def setUpClass(cls):
        try:
            from emu import Med9Emu  # noqa: F401
        except Exception:                                    # pragma: no cover
            raise unittest.SkipTest("unicorn / emu package missing")
        import shutil
        import tempfile
        from pathlib import Path

        import patch_apply
        cls.tmp = Path(tempfile.mkdtemp())
        cls._rm = shutil.rmtree
        cls.image = cls.tmp / "ff_fuel.bin"
        data, _r, _w = patch_apply.apply_patch(DUMP, tff.FF_FUEL)
        cls.image.write_bytes(bytes(data))
        from ecu_sim import Med9Handlers
        cls.h = Med9Handlers(str(cls.image), animate=True)

    @classmethod
    def tearDownClass(cls):
        cls._rm(cls.tmp, ignore_errors=True)

    def test_21_6c_returns_our_four_fields_and_four_empties(self):
        self.h.handle(b"\x10\x89")
        answer = self.h.handle(bytes([0x21, GROUP]))[-1]
        self.assertEqual(len(answer), 26, answer.hex())
        self.assertEqual(answer[0], 0x61)
        self.assertEqual(answer[1], GROUP)
        triples = [tuple(answer[2 + 3 * i:5 + 3 * i]) for i in range(8)]
        self.assertEqual([t[0] for t in triples[:4]], [0x21, 0x22, 0x22, 0x36])
        self.assertEqual(triples[4:], [(0x25, 0, 0)] * 4,
                         "group 235 must stay empty")
        fzw, dzw, knock, latch = (vag_formulas.decode(*x) for x in triples[:4])
        # the simulator animates the state block from the model with the
        # SHIPPED calibration, i.e. ff_zw_enable = 0, so both of our fields
        # read zero -- which is exactly what a stock-behaviour image must show.
        self.assertEqual(fzw.value, 0.0)
        self.assertEqual(dzw.value, 0.0)
        # fields 3 and 4 are stock RAM, whatever the simulator left in it
        raw = self.h.emu.read(RAM_DWKRZ, 6)
        worst = max(b - 256 if b & 0x80 else b for b in raw)
        self.assertAlmostEqual(knock.value, worst * 0.75, places=6)
        self.assertEqual(latch.value,
                         float(self.h.emu.read(RAM_ZW_LATCH, 1)[0] & 3))

    def test_the_two_stock_fields_follow_the_cells_they_read(self):
        """Poke the simulator's RAM and watch `21 6C` follow."""
        self.h.handle(b"\x10\x89")
        self.h.emu.write(RAM_DWKRZ, bytes([0xFF, 0xFA, 0xFF, 0xFD, 0xFF, 0xFF]))
        self.h.emu.write(RAM_ZW_LATCH, b"\x03")
        answer = self.h.handle(bytes([0x21, GROUP]))[-1]
        triples = [tuple(answer[2 + 3 * i:5 + 3 * i]) for i in range(8)]
        self.assertEqual(triples[2], (0x22, 0x4B, 127), "max(-1,-6,-1,-3) = -1")
        self.assertEqual(triples[3], (0x36, 0, 3))
        self.assertAlmostEqual(vag_formulas.decode(*triples[2]).value, -0.75,
                               places=6)

    def test_the_two_blocks_do_not_disturb_each_other(self):
        self.h.handle(b"\x10\x89")
        a = self.h.handle(bytes([0x21, 111]))[-1]
        b = self.h.handle(bytes([0x21, GROUP]))[-1]
        c = self.h.handle(bytes([0x21, 111]))[-1]
        self.assertEqual(a[1], 111)
        self.assertEqual(b[1], GROUP)
        self.assertEqual(a[0], c[0])


if __name__ == "__main__":
    unittest.main()
