"""patches/ff_fuel's E5 half: the rail-pressure adder and the window
diagnostics (#36, and the rail flavour of the #37 rule).

Seven layers, each able to fail on its own:

  1. the stock facts the hook rests on - the word at 0x45845C, the three
     branches into it and the one that jumps PAST it, the caller that puts
     `hdrpsol_main` in a task, `interp_2d_u16`'s closing `clrlwi` (which is
     what lets the stub add without masking), the `lhz` at 0x458480 that makes
     the KLPRMAX clamp re-read the cell, the four TKMWL words of ids 2184-2187
     and the eight group-109 words;
  2. apply: the hook word, the handler table and the group table on the image,
     and the stub read back out of the blob;
  3. `hdrpsol_main` (0x45822C) on the patched image against the STOCK image,
     over a grid of `nmot_w` x 0x803508 x 0x7FD3F7 x every mode-bit path -
     bit for bit with `prail_add` = 0, `+prail_add` with it forced, and the
     110.0 bar ceiling holding under any calibration;
  4. the stub on its own: what it stores, where it returns, which registers it
     leaves alone, and what an invalid block makes it do;
  5. the producer against `emu/models/flexfuel.py`, tick by tick, over a
     sequence that covers OK -> FAULT (the #37 drop) -> OK and the three
     window statistics over a synthetic wbho1s/dwi/0x80316E/prist stream;
  6. the two proofs the 2026-09-17 rules ask for - a whole-SRAM diff with the
     adder disabled AND with it enabled at the neutral curve;
  7. the diagnostics: measuring block 109 through `measuring_var_dispatch` and
     `21 6D` end to end through `logging/ecu_sim.py` on the patched image.

Plus the instruction counts the README records.

Nothing here writes to `data/` (DumpUnchanged asserts it) and nothing is ever
flashed.
"""
from __future__ import annotations

import struct
import sys
import unittest

from tests import test_ff_fuel_patch as tff
from tests.common import DUMP, REPO, DumpUnchanged, requires_dump

import find_branch_refs  # noqa: E402  (tests.common put tools/ on sys.path)
import measuring_vars as mv  # noqa: E402
import med9lib as m  # noqa: E402
import patch_gen  # noqa: E402

sys.path.insert(0, str(REPO / "logging"))
sys.path.insert(0, str(REPO / "patches" / "ff_fuel"))
import ffcal001  # noqa: E402
from med9kwp import vag_formulas  # noqa: E402

from emu.models import flexfuel as ff  # noqa: E402

# --- the facts under test (re/findings/rail.md sections 3.2-3.5, 12.1) ------
HDRPSOL = 0x45822C                          # %HDRPSOL, the setpoint module
HDRPSOL_CALL = 0x45CC08                     # its ONLY caller, set A's 20 ms task
RAIL_SITE = 0x45845C
RAIL_OLD = bytes.fromhex("b3cd3200")        # sth r30,0x3200(r13) -- NOT a branch
RAIL_NEXT = 0x458460                        # where the stub `blr`s back to
CLAMP_RELOAD = 0x458480                     # lhz r30,0x3200(r13): the clamp re-reads
INTERP_2D_U16 = 0x40C444
SAVEGPR_25 = 0x0B8228
SAVEGPR_TAIL = 0x0B8244                     # stw r0,4(r11) -- LR into the frame

#: The three plain `b` into the hooked word, and the one `bne` that jumps past
#: it (`0x7FD04D & 1`, "hold the previous setpoint").
BRANCHES_IN = (0x458260, 0x4583B4, 0x45843C)
HOLD_TEST = 0x45826C

RAM_PRSOLL_RAW = 0x8031F0                   # what the hooked store publishes
RAM_PRSOLL_CLAMPED = 0x8031F2               # after PRSOLMN / KLPRMAX
RAM_PRSOLL_RATE = 0x8031EE                  # after the pump-volume rate limiter
RAM_PRSOLL = 0x8031F4                       # the setpoint the controller uses
RAM_KLPRMAX = 0x8031EC
RAM_PRIST = 0x8031DA
RAM_VMSV = 0x80316E
RAM_WBHO1S = 0x80307E
RAM_DWI = 0x803088
RAM_WIN_MARGIN = 0x7FD290
RAM_NMOT_W = 0x7FEE74
RAM_LOAD = 0x803508                         # the torque structure's rlsol_w
RAM_TEMP = 0x7FD3F7                         # the u8 that indexes KLPRMAX and the fade
RAM_MODE_W = 0x7FB69A                       # u16 of mode bits
RAM_MODE_B = 0x80156F                       # bit 0 -> the HMM path
RAM_HOLD = 0x7FD04D                         # bit 0 -> keep the previous setpoint
RAM_HINT_LOAD = 0x7FD5D0
RAM_HINT_NMOT = 0x7FD5D4

CAL_CWPRSOL = 0x5D521E                      # 0x2E: bit 0 clear, bit 5 set
CAL_KLPRMAX = 0x5D5546                      # six u16, all 22000 = 110.0 bar
CAL_PRSOLMN = 0x5D5572                      # u16 = 7000 = 35.0 bar
CAL_VMSVMX = 0x5D4BC6                       # u16 = 5000

KLPRMAX_VALUE = 22000
PRSOLMN_VALUE = 7000

PATCH_RAM = tff.PATCH_RAM
OFF_PRAIL_ADD = 0x44

#: See the note in tests/test_ff_ign_patch.py: the harness's default stack top
#: is not the ECU's, and `hdrpsol_main` opens a 0x28-byte frame on top of the
#: millicode's saves.
TASK_STACK = 0x7FF768

# --- the measuring slots (re/findings/measuring_vars.md section 8.6) --------
TKMWL = 0x0A5658
STUB = 0x00038EC4
DISPATCH = 0x045768
GROUP_TABLE = 0x5C5518
IDS = (2184, 2185, 2186, 2187)
GROUP = 109
SYMS = ("ff_diag_prail", "ff_diag_prist_min",
        "ff_diag_win_margin", "ff_diag_msv_sat")
SLOTS = tuple(TKMWL + 4 * i for i in IDS)
GROUP_WORDS = tuple(GROUP_TABLE + f * 0x1FE + GROUP * 2 for f in range(4))

RESULT_FORMULA = 0x7FD06F
RESULT_B = 0x7FD070
RESULT_A = 0x7FD071

#: The six mode-bit paths of rail.md section 3.2, as (0x7FB69A, 0x80156F).
#: Labels follow G4's re-assignment (2026-09-23, calibration_names.md section
#: 11.4, rail.md section 13): 0x7FB69A is bdemod_w, bit coding HOM 0, HMM 1,
#: HOS 2, SCH 3, SKH 4, HSP 6, HKS 7. The map addresses per path are unchanged.
MODE_PATHS = (
    (0x0000, 0x00),      # KFPRSOLHOM 0x5D5324, the normal running map (+ KFPRSOLOFF)
    (0x0080, 0x00),      # bit 7 HKS -> KFPRSOLHKS 0x5D5224 (was labelled KFPRSOLKH)
    (0x0010, 0x00),      # bit 4 SKH -> KFPRSOLKH 0x5D53A4 (was labelled KFPRSOLHMM)
    (0x0004, 0x00),      # bit 2 HOS -> KFPRSOLKH 0x5D53A4, the second way in
    (0x0000, 0x01),      # 0x80156F bit 0 -> KFPRSOLKH 0x5D53A4, the third way in
    (0x0008, 0x00),      # bit 3 SCH -> KFPRSOLSCH 0x5D54A4 (+ KFPRSOLOFF; was labelled KFPRSOLHKS)
    (0x0002, 0x00),      # bit 1 HMM -> KFPRSOLHMM 0x5D52A4 (was labelled KFPRSOLSCH)
)

#: nmot_w (0.25 rpm), 0x803508 (16-bit full scale) and 0x7FD3F7 (u8 temperature)
NMOT_GRID = (0, 3000, 8000, 14000, 22800, 26080, 65535)
LOAD_GRID = (0, 6554, 26214, 39322, 65535)
TEMP_GRID = (0, 49, 63, 77, 200)


def rail_cal(**over) -> bytes:
    """An FFCAL001 image with the rail adder set up as the test wants."""
    params = ffcal001.load_params(tff.FF_FUEL / "ffcal001.json")
    return ffcal001.build(dict(params, **over))


def ramped_prail(top: int = 3000) -> list[int]:
    """A non-neutral `ff_prail_curve`: 0 at E0 rising linearly to `top` at E100."""
    n = ff.PRAIL_N
    return [round(i * top / (n - 1)) for i in range(n)]


# ------------------------------------------------------ 1. the stock facts --
@requires_dump
class TestStockFacts(DumpUnchanged):
    def setUp(self):
        self.data = m.load_dump(str(DUMP))

    def _read(self, cpu: int, n: int) -> bytes:
        off = m.cpu_to_file(cpu)
        return bytes(self.data[off:off + n])

    def test_the_site_holds_the_store_the_stub_re_does(self):
        self.assertEqual(self._read(RAIL_SITE, 4), RAIL_OLD)
        with self.assertRaises(patch_gen.PatchError):
            patch_gen.decode_branch(int.from_bytes(RAIL_OLD, "big"), RAIL_SITE)
        word = int.from_bytes(RAIL_OLD, "big")
        self.assertEqual(word >> 26, 44, "sth")
        self.assertEqual((word >> 21) & 0x1F, 30, "the value is in r30")
        self.assertEqual((word >> 16) & 0x1F, 13, "base is r13")
        self.assertEqual(word & 0xFFFF, 0x3200)
        self.assertEqual(0x7FFFF0 + 0x3200, RAM_PRSOLL_RAW)

    def test_hdrpsol_main_has_exactly_one_caller_and_it_is_in_task_set_a(self):
        hits = find_branch_refs.scan(self.data, [HDRPSOL], want_ptr=True)
        self.assertEqual([(s, k) for _o, s, k in hits[HDRPSOL]],
                         [(HDRPSOL_CALL, "bl")])

    def test_three_branches_reach_the_word_and_none_is_a_pointer(self):
        hits = find_branch_refs.scan(self.data, [RAIL_SITE], want_ptr=True)
        self.assertEqual(sorted((s, k) for _o, s, k in hits[RAIL_SITE]),
                         sorted((s, "b") for s in BRANCHES_IN))

    def test_the_hold_path_branches_past_the_store_not_into_it(self):
        """`0x7FD04D & 1` keeps the previous setpoint -- and skips the store.

        That is the opposite of E2's 0x41A680, whose early-out branched INTO
        its store and forced a gate inside the stub.  Here the stub simply
        never runs on that path (re/findings/rail.md section 12.1).
        """
        self.assertEqual(self._read(0x458264, 4), bytes.fromhex("898dd05d"),
                         "lbz r12,-0x2fa3(r13) -- 0x7FD04D")
        self.assertEqual(0x7FFFF0 - 0x2FA3, RAM_HOLD)
        word = struct.unpack(">I", self._read(HOLD_TEST, 4))[0]
        self.assertEqual(word >> 26, 16, "a conditional branch")
        target = HOLD_TEST + ((word & 0xFFFC) ^ 0x8000) - 0x8000
        self.assertEqual(target, RAIL_NEXT)
        self.assertGreater(target, RAIL_SITE, "it jumps PAST the store")

    def test_the_klprmax_clamp_re_reads_the_cell_from_memory(self):
        """The whole safety argument for this insertion point.

        `lhz r30,0x3200(r13)` at 0x458480 means the PRSOLMN floor and the
        KLPRMAX ceiling of rail.md section 3.3 bound whatever the patch stored,
        instead of re-using the register the store came from.
        """
        self.assertEqual(self._read(CLAMP_RELOAD, 4), bytes.fromhex("a3cd3200"))
        self.assertGreater(CLAMP_RELOAD, RAIL_SITE)
        # and the two constants it clamps against are what rail.md says
        self.assertEqual(set(struct.unpack(">6H", self._read(CAL_KLPRMAX, 12))),
                         {KLPRMAX_VALUE})
        self.assertEqual(struct.unpack(">H", self._read(CAL_PRSOLMN, 2))[0],
                         PRSOLMN_VALUE)

    def test_interp_2d_u16_returns_a_clean_halfword(self):
        """Why the stub may add without masking r30 first.

        `interp_2d_u16` (0x40C444) ends with `clrlwi r3,r8,0x10`, so every
        fall-through path into the hooked word carries a 0..0xFFFF value; the
        two `KFPRSOLOFF` paths clamp explicitly and the `CWPRSOL & 1` path is
        an `lhz`.
        """
        self.assertEqual(self._read(0x40C4C8, 8),
                         bytes.fromhex("55030 43e4e800020".replace(" ", "")))
        # the two explicit clamps: li 0 on the low side, 0xFFFF on the high side
        for addr in (0x458420, 0x4583A0):
            self.assertIn(self._read(addr, 4),
                          (bytes.fromhex("3be00000"), bytes.fromhex("7c1fe040")))

    def test_the_prologue_puts_lr_in_the_callers_frame(self):
        """`_savegpr_25` 0x0B8228 -> the shared tail `stw r0,4(r11)` at 0x0B8244.

        That is what makes LR dead at the hooked word, so the `bl` into the
        stub clobbers nothing.
        """
        self.assertEqual(self._read(HDRPSOL, 16), bytes.fromhex(
            "39610000"          # addi  r11,r1,0
            "9421ffd8"          # stwu  r1,-0x28(r1)
            "7c0802a6"          # mflr  r0
            "4bc5fff1"))        # bl    0xb8228
        self.assertEqual(HDRPSOL + 12 + patch_gen.decode_branch(
            struct.unpack(">I", self._read(HDRPSOL + 12, 4))[0],
            HDRPSOL + 12)[1] - (HDRPSOL + 12), SAVEGPR_25)
        self.assertEqual(self._read(SAVEGPR_TAIL, 4), bytes.fromhex("900b0004"))

    def test_the_four_tkmwl_slots_hold_the_not_available_stub(self):
        for slot in SLOTS:
            self.assertEqual(self._read(slot, 4), struct.pack(">I", STUB))

    def test_group_109_and_its_echo_are_empty(self):
        for f in range(4):
            for g in (GROUP, GROUP + 0x7F):
                word = GROUP_TABLE + f * 0x1FE + g * 2
                with self.subTest(field=f, group=g):
                    self.assertEqual(self._read(word, 2), b"\0\0")

    def test_the_slots_are_still_free_according_to_the_tool(self):
        """`measuring_vars.py --free`, as the 2026-09-17 rules require."""
        dump = mv.Dump(str(DUMP))
        disp, base, count = mv.find_dispatcher(dump.d)
        self.assertEqual((disp, base, count), (DISPATCH, TKMWL, 2200))
        ptrs = [dump.word(base + 4 * i) for i in range(count)]
        spare, free = mv.free_slots(dump, GROUP_TABLE, ptrs, STUB)
        for vid in IDS:
            self.assertIn(vid, spare, f"id {vid} is not spare any more")
        self.assertIn(GROUP, free, "group 109 is not free any more")

    def test_nothing_else_references_the_four_table_words(self):
        """The three negative searches measuring_vars.md section 8.6 records."""
        import find_abs_refs
        inside = [t for _i, _k, t, _kind in find_abs_refs.resolve(self.data)
                  if 0x0A7878 <= t <= 0x0A7887]
        self.assertEqual(inside, [], "a lis/D-form pair forms one of our words")
        hits = find_branch_refs.scan(self.data, list(SLOTS))
        for slot in SLOTS:
            self.assertEqual(hits[slot], [], f"{slot:#x} is branched to or stored")
        words = find_branch_refs.scan(self.data, list(GROUP_WORDS))
        for w in GROUP_WORDS:
            self.assertEqual(words[w], [], f"{w:#x} is branched to or stored")

    def test_the_window_quantities_are_where_rail_md_says(self):
        """`dwi`, `wbho1s` and the required margin, plus the VMSVMX clamp."""
        self.assertEqual(0x7FFFF0 - 0x2D60, RAM_WIN_MARGIN)
        self.assertEqual(struct.unpack(">H", self._read(CAL_VMSVMX, 2))[0], 5000)
        self.assertEqual(ff.VMSVMX_STOCK, 5000)
        # KLWBHO1SMX is flat at 67 counts = 50.25 degCA in this dataset
        n = self._read(0x5D3BE8, 1)[0]
        vals = set(self._read(0x5D3BE8 + 2 + 2 * n, n))
        self.assertEqual(vals, {ff.WIN_MARGIN_REQ_STOCK})
        self.assertEqual(ff.WIN_MARGIN_REQ_STOCK * ff.ANGLE_MAP_SCALE, 2144)


# ------------------------------------------------------------- 2. apply -----
class TestRailApply(tff.TestApply):
    """Everything tff.TestApply checks, plus what E5 added to the image."""

    def test_the_hook_word_points_at_the_stub(self):
        syms = tff.load_patch()["build"]["symbols"]
        off = m.cpu_to_file(RAIL_SITE)
        word = int.from_bytes(bytes(self.data[off:off + 4]), "big")
        self.assertEqual(patch_gen.decode_branch(word, RAIL_SITE),
                         ("bl", int(syms["ff_prail_hook"], 0)))

    def test_it_is_declared_on_chip(self):
        hooks = {h["site"]: h for h in tff.load_patch()["build"]["hooks"]}
        h = hooks[f"0x{RAIL_SITE:08X}"]
        self.assertTrue(h.get("onchip_edit"))
        self.assertTrue(0x404000 <= RAIL_SITE <= 0x47FFFF)

    def test_the_tkmwl_slots_point_at_our_handlers(self):
        syms = tff.load_patch()["build"]["symbols"]
        for slot, name in zip(SLOTS, SYMS):
            with self.subTest(id=name):
                off = m.cpu_to_file(slot)
                self.assertEqual(int.from_bytes(bytes(self.data[off:off + 4]),
                                                "big"), int(syms[name], 0))

    def test_the_group_words_name_our_ids(self):
        for word, vid in zip(GROUP_WORDS, IDS):
            off = m.cpu_to_file(word)
            self.assertEqual(struct.unpack_from(">H", bytes(self.data), off)[0],
                             vid)

    def test_the_echo_group_236_is_still_empty(self):
        for f in range(4):
            off = m.cpu_to_file(GROUP_TABLE + f * 0x1FE + (GROUP + 0x7F) * 2)
            self.assertEqual(bytes(self.data[off:off + 2]), b"\0\0")

    def test_ffcal001_is_version_5_and_ships_the_adder_off(self):
        """E5 made it v4; G1 (#39) appended the PID 0x52 gate as v5."""
        off = m.cpu_to_file(tff.CAL_BASE)
        blk = bytes(self.data[off:off + ffcal001.LENGTH])
        ffcal001.check(blk)
        self.assertEqual(struct.unpack_from(">H", blk, 0x08)[0], 5)
        self.assertEqual(struct.unpack_from(">H", blk, 0x0A)[0], 0x014E)
        self.assertEqual(blk[0x122], 0, "ff_prail_enable must ship 0")
        self.assertEqual(struct.unpack_from(">17H", blk, 0x128), (0,) * 17,
                         "ff_prail_curve must ship zero")

    def test_the_state_block_grew_past_its_own_end_only(self):
        syms = {k: int(v, 0) for k, v in
                tff.load_patch()["build"]["symbols"].items()}
        self.assertEqual(syms["ff_state"], PATCH_RAM)
        self.assertEqual(ff.STATE_LEN, 0x50)      # G1 (#39) appended 4 bytes
        self.assertEqual(ff.CORE2_OFF + ff.CORE2_LEN, ff.STATE_LEN)
        self.assertEqual(ff.STATE_LEN % 4, 0,
                         "ff_state_init() clears the block a word at a time")
        self.assertGreaterEqual(syms["ff_nvm_req"], PATCH_RAM + ff.STATE_LEN)
        self.assertGreaterEqual(syms["ff_persist_buf"], PATCH_RAM + ff.STATE_LEN)


# ------------------------------------------------- 3. hdrpsol on the image --
class RailBase(tff.EmuBase):
    """Helpers shared by the %HDRPSOL, stub and producer layers."""

    @staticmethod
    def _hdrpsol_ram(nmot: int, load: int, temp: int, mode_w: int, mode_b: int,
                     *, prev: int = 12000, hold: int = 0) -> dict:
        """Everything hdrpsol_main reads that a test varies, plus a clean slate.

        `prev` seeds the three cells the rate limiter and the output stage look
        back at, so stock and patched start from the same history.
        """
        return {
            RAM_NMOT_W: struct.pack(">H", nmot),
            RAM_LOAD: struct.pack(">H", load),
            RAM_TEMP: bytes([temp & 0xFF]),
            RAM_MODE_W: struct.pack(">H", mode_w),
            RAM_MODE_B: bytes([mode_b & 0xFF]),
            RAM_HOLD: bytes([hold & 0xFF]),
            RAM_HINT_LOAD: b"\0\0\0\0",
            RAM_HINT_NMOT: b"\0\0\0\0",
            RAM_PRSOLL_RAW: struct.pack(">H", prev),
            RAM_PRSOLL_RATE: struct.pack(">H", prev),
            RAM_PRSOLL: struct.pack(">H", prev),
            RAM_PRIST: struct.pack(">H", prev),
        }

    def seeded(self, *, prail_add: int = 0, magic: bool = True) -> bytes:
        """A state block carrying `prail_add`, checksum and all."""
        mdl = ff.FlexFuelModel()
        mdl.state.prail_add = prail_add
        mdl.seal()
        blk = bytearray(mdl.full_bytes())
        if not magic:
            blk[0] ^= 0xFF
        return bytes(blk)

    def hdrpsol(self, emu, **kw) -> tuple[int, int, int, int]:
        res = emu.call(HDRPSOL, mem=self._hdrpsol_ram(**kw), reset=False,
                       regs={"r1": TASK_STACK})
        self.assertTrue(res.ok, res.summary())
        return tuple(struct.unpack(">H", res.snapshot(a, 2))[0]
                     for a in (RAM_PRSOLL_RAW, RAM_PRSOLL_CLAMPED,
                               RAM_PRSOLL_RATE, RAM_PRSOLL))


class TestHdrpsolOnTheImage(RailBase):
    GRID = [(n, l, t, w, b)
            for n in NMOT_GRID for l in LOAD_GRID for t in TEMP_GRID
            for w, b in MODE_PATHS]

    #: A smaller cross-section for the slower comparisons.
    SHORT = GRID[::37]

    def _stock(self):
        from emu import Med9Emu
        stock = Med9Emu(DUMP)
        stock.reset()
        return stock

    def test_prail_add_0_is_bit_identical_on_every_path(self):
        """(a) the whole grid, patched vs the untouched dump."""
        stock = self._stock()
        patched = self.fresh()
        patched.write(PATCH_RAM, self.seeded(prail_add=0))
        for n, l, t, w, b in self.GRID:
            with self.subTest(nmot=n, load=l, temp=t, mode=(hex(w), b)):
                kw = dict(nmot=n, load=l, temp=t, mode_w=w, mode_b=b)
                self.assertEqual(self.hdrpsol(stock, **kw),
                                 self.hdrpsol(patched, **kw))

    def test_an_invalid_state_block_is_bit_identical_too(self):
        """(c) the first milliseconds after power-up."""
        stock = self._stock()
        patched = self.fresh()
        patched.write(PATCH_RAM, self.seeded(prail_add=3000, magic=False))
        for n, l, t, w, b in self.SHORT:
            with self.subTest(nmot=n, load=l, temp=t, mode=(hex(w), b)):
                kw = dict(nmot=n, load=l, temp=t, mode_w=w, mode_b=b)
                self.assertEqual(self.hdrpsol(stock, **kw),
                                 self.hdrpsol(patched, **kw))

    def test_the_hold_path_is_bit_identical_whatever_the_adder_says(self):
        """`0x7FD04D & 1` branches past the store, so the stub cannot run."""
        stock = self._stock()
        patched = self.fresh()
        patched.write(PATCH_RAM, self.seeded(prail_add=3000))
        for n, l, t, w, b in self.SHORT:
            with self.subTest(nmot=n, load=l):
                kw = dict(nmot=n, load=l, temp=t, mode_w=w, mode_b=b, hold=1)
                got = self.hdrpsol(patched, **kw)
                self.assertEqual(self.hdrpsol(stock, **kw), got)
                self.assertEqual(got[0], 12000, "the previous value is kept")

    def test_a_forced_adder_raises_prsoll_raw_by_exactly_that(self):
        """(b) 0x8031F0 moves by `prail_add`; 0x8031F2 never passes KLPRMAX."""
        stock = self._stock()
        for add in (200, 2000, 3000, 6000):
            patched = self.fresh()
            patched.write(PATCH_RAM, self.seeded(prail_add=add))
            mdl = ff.FlexFuelModel()
            mdl.state.prail_add = add
            for n, l, t, w, b in self.GRID[::7]:
                with self.subTest(add=add, nmot=n, load=l, temp=t, mode=hex(w)):
                    kw = dict(nmot=n, load=l, temp=t, mode_w=w, mode_b=b)
                    s_raw, _s_clamped, _s_rate, _s_out = self.hdrpsol(stock, **kw)
                    raw, clamped, _rate, _out = self.hdrpsol(patched, **kw)
                    self.assertEqual(raw, mdl.prsoll_raw_store(s_raw))
                    self.assertEqual(raw, min(s_raw + add, 0xFFFF))
                    self.assertLessEqual(clamped, KLPRMAX_VALUE,
                                         "the 110.0 bar ceiling must hold")
                    self.assertGreaterEqual(clamped, PRSOLMN_VALUE)

    def test_the_ceiling_holds_even_at_the_largest_representable_adder(self):
        """No calibration can put more in the rail than the stock ECU allows."""
        patched = self.fresh()
        patched.write(PATCH_RAM, self.seeded(prail_add=0xFFFF))
        for n, l, t, w, b in self.GRID[::11]:
            with self.subTest(nmot=n, load=l, temp=t, mode=hex(w)):
                raw, clamped, rate, out = self.hdrpsol(
                    patched, nmot=n, load=l, temp=t, mode_w=w, mode_b=b)
                self.assertEqual(raw, 0xFFFF, "saturated, not wrapped")
                self.assertEqual(clamped, KLPRMAX_VALUE)
                self.assertLessEqual(rate, KLPRMAX_VALUE)
                self.assertLessEqual(out, KLPRMAX_VALUE)


# ---------------------------------------------------------- 4. the stub -----
class TestRailStub(RailBase):
    def _call(self, emu, value: int):
        res = emu.call(self.syms["ff_prail_hook"], reset=False,
                       regs={"r1": TASK_STACK, "r30": value})
        self.assertTrue(res.ok, res.summary())
        return res

    def test_it_stores_the_sum_and_leaves_r30_alone(self):
        emu = self.fresh()
        emu.write(PATCH_RAM, self.seeded(prail_add=2000))
        for v in (0, 1, 7000, 19000, 0xFFFF):
            with self.subTest(v=v):
                res = self._call(emu, v)
                self.assertEqual(struct.unpack(">H",
                                               res.snapshot(RAM_PRSOLL_RAW, 2))[0],
                                 min(v + 2000, 0xFFFF))
                self.assertEqual(res.regs["r30"], v, "r30 is never written")
                self.assertEqual(res.regs["r1"], TASK_STACK, "no stack is used")

    def test_an_invalid_block_stores_exactly_what_the_stock_sth_would(self):
        emu = self.fresh()
        emu.write(PATCH_RAM, self.seeded(prail_add=3000, magic=False))
        for v in (0, 12345, 0xFFFF):
            with self.subTest(v=v):
                res = self._call(emu, v)
                self.assertEqual(struct.unpack(">H",
                                               res.snapshot(RAM_PRSOLL_RAW, 2))[0],
                                 v & 0xFFFF)

    def test_it_writes_nothing_but_the_one_cell(self):
        emu = self.fresh()
        emu.write(PATCH_RAM, self.seeded(prail_add=1500))
        before = emu.read(tff.SRAM_START, tff.SRAM_LEN)
        self._call(emu, 15000)
        after = emu.read(tff.SRAM_START, tff.SRAM_LEN)
        changed = {tff.SRAM_START + i for i, (a, b) in
                   enumerate(zip(before, after)) if a != b}
        self.assertEqual(changed, {RAM_PRSOLL_RAW, RAM_PRSOLL_RAW + 1})

    def test_the_cost_of_each_path(self):
        """The counts patches/ff_fuel/README.md records."""
        emu = self.fresh()
        emu.write(PATCH_RAM, self.seeded(prail_add=0, magic=False))
        self.assertEqual(self._call(emu, 19000).insns, 8, "no valid block")
        emu.write(PATCH_RAM, self.seeded(prail_add=2000))
        self.assertEqual(self._call(emu, 19000).insns, 12, "adding")
        self.assertEqual(self._call(emu, 0xFFFF).insns, 13, "saturating")

    def test_the_blob_uses_no_r2_or_r13_and_stores_absolutely(self):
        """`make dump --check-sda` in one assertion, over our 13 words."""
        import re

        import blobdis
        # The committed blob out of patch.json, not build/ff_fuel.bin: the
        # build directory exists only after a `make`, and this test must
        # pass in a fresh checkout (integration, 2026-09-17).
        blob = bytes.fromhex(next(c["new"] for c in tff.load_patch()["changes"]
                                  if c.get("kind") == "blob"))
        base = self.syms["ff_prail_hook"]
        start = base - tff.PATCH_FLASH
        insns = list(blobdis.disassemble(blob[start:start + 16 * 4], base))
        self.assertEqual(len(insns), 16,
                         "sixteen words in the blob: 8 / 12 / 13 executed")
        regs = set()
        for _addr, _raw, _mn, ops in insns:
            regs |= set(re.findall(r"\br(\d+)\b", ops))
        self.assertNotIn("2", regs, "r2 is the ECU's SDA2 base")
        self.assertNotIn("13", regs, "r13 is the ECU's SDA base")
        self.assertEqual(regs, {"11", "12", "30"})
        self.assertEqual(insns[0][2], "lis")
        self.assertEqual(insns[-1][2], "blr")
        stores = [i for i in insns if i[2] == "sth"]
        self.assertEqual(len(stores), 2, "one store per path")
        for _a, _r, _m, ops in stores:
            self.assertIn("0x31f0", ops.lower(), "the store is absolute")


# ------------------------------------------------------- 5. the producer ----
class TestProducer(tff.EmuBase):
    """The 10 ms half, against `emu/models/flexfuel.py`, tick by tick."""

    @staticmethod
    def _rail_ram(r: ff.RailIn) -> dict:
        return {
            RAM_WBHO1S: struct.pack(">H", r.wbho1s & 0xFFFF),
            RAM_DWI: struct.pack(">H", r.dwi & 0xFFFF),
            RAM_PRIST: struct.pack(">H", r.prist & 0xFFFF),
            RAM_VMSV: struct.pack(">H", r.msv & 0xFFFF),
            RAM_WIN_MARGIN: bytes([r.win_margin_req & 0xFF]),
        }

    def _stream(self, n: int):
        """A synthetic wbho1s / dwi / 0x80316E / prist sequence.

        It walks the margin down through zero, pins the MSV at VMSVMX for part
        of every window and drops `prist` low enough to be interesting, so all
        three statistics move.
        """
        out = []
        for i in range(n):
            out.append(ff.RailIn(
                wbho1s=14080 - 8 * (i % 700),
                dwi=200 + 11 * (i % 313),
                prist=14000 - 40 * (i % 251),
                msv=5000 if (i % 17) < 4 else 4200 + (i % 500),
                win_margin_req=ff.WIN_MARGIN_REQ_STOCK,
            ))
        return out

    def _replay(self, rails, cal_blk, *, e_pct=85, drop_from=None, drop_to=None):
        emu = self.fresh(cal=cal_blk)
        mdl = ff.FlexFuelModel(tff.cal_from_block(cal_blk))
        for n, rail in enumerate(rails):
            fresh_frame = n % 10 == 0
            silent = drop_from is not None and drop_from <= n < (drop_to or 0)
            rx = (ff.frame(e_pct=e_pct, counter=(n // 10) & 0xFF)
                  if fresh_frame and not silent else None)
            if rx is not None:
                self.arm_frame(emu, rx)
            else:
                self.no_frame(emu)
            for addr, val in self._rail_ram(rail).items():
                emu.write(addr, val)
            res = emu.call(self.syms["ff_fuel_hook_b"], reset=False,
                           regs={"r1": TASK_STACK})
            self.assertTrue(res.ok, f"activation {n}: {res.summary()}")
            mdl.tick(rx, rail=rail)
            self.assertEqual(
                emu.read(PATCH_RAM, ff.STATE_LEN)[ff.CORE2_OFF:].hex(),
                mdl.core2_bytes().hex(),
                f"core 2 differs from the model at activation {n}")
        return emu, mdl

    def test_the_shipped_calibration_never_moves_the_rail(self):
        rails = self._stream(300)
        _emu, mdl = self._replay(rails, self.cal_blk)
        self.assertEqual(mdl.state.prail_add, 0)
        self.assertGreater(mdl.state.e_filt, 0, "the estimate did move")

    def test_enabled_with_the_neutral_curve_is_also_inert(self):
        blk = rail_cal(ff_prail_enable=1)
        _emu, mdl = self._replay(self._stream(300), blk)
        self.assertEqual(mdl.state.prail_add, 0)

    def test_a_real_curve_tracks_the_model_tick_by_tick(self):
        blk = rail_cal(ff_prail_enable=1, ff_prail_curve=ramped_prail())
        _emu, mdl = self._replay(self._stream(2000), blk)
        self.assertGreater(mdl.state.prail_add, 0)
        self.assertLessEqual(mdl.state.prail_add, 3000)

    def test_the_adder_is_clamped_to_ff_prail_max(self):
        blk = rail_cal(ff_prail_enable=1, ff_prail_max=1200,
                       ff_prail_curve=ramped_prail(6000))
        _emu, mdl = self._replay(self._stream(2000), blk)
        self.assertEqual(mdl.state.prail_add, 1200)

    def test_the_code_ceiling_binds_whatever_the_block_says(self):
        """`ff_prail_max` above FF_PRAIL_HARD_MAX cannot be built at all..."""
        with self.assertRaises(ffcal001.CalError):
            rail_cal(ff_prail_max=ff.PRAIL_HARD_MAX + 1)
        # ...and if one were flashed anyway the code would clamp it:
        c = ff.Cal(prail_enable=1, prail_max=0xFFFF,
                   prail_curve=[0xFFFF] * ff.PRAIL_N)
        self.assertEqual(c.prail_ceiling(), ff.PRAIL_HARD_MAX)

    def test_fault_zeroes_the_adder_within_one_activation(self):
        """The #37 rule: no hold and no ramp for anything that adds pressure."""
        blk = rail_cal(ff_prail_enable=1, ff_prail_curve=ramped_prail())
        rails = self._stream(1400)
        emu, mdl = self._replay(rails[:1000], blk)
        self.assertGreater(mdl.state.prail_add, 0)
        held_f = None
        # now go silent: the timeout makes the next activations FAULT
        for n, rail in enumerate(rails[1000:1200]):
            self.no_frame(emu)
            for addr, val in self._rail_ram(rail).items():
                emu.write(addr, val)
            emu.call(self.syms["ff_fuel_hook_b"], reset=False,
                     regs={"r1": TASK_STACK})
            mdl.tick(None, rail=rail)
            st = emu.read(PATCH_RAM, ff.STATE_LEN)
            if st[0x0C] == ff.MODE_FAULT:
                self.assertEqual(struct.unpack_from(">H", st, OFF_PRAIL_ADD)[0],
                                 0, f"the adder must be 0 at activation {n}")
                # ...while the FUEL factor is HELD, which is the #37 asymmetry
                f_now = struct.unpack_from(">H", st, 0x0A)[0]
                if held_f is None:
                    held_f = f_now
                    self.assertGreater(held_f, ff.F_MIN, "it was rich before")
                self.assertEqual(f_now, held_f,
                                 f"the fuel factor moved at activation {n}")
        self.assertIsNotNone(held_f, "the sequence never reached FAULT")
        self.assertEqual(mdl.state.mode, ff.MODE_FAULT)
        self.assertEqual(mdl.state.prail_add, 0)

    def test_the_window_statistics_match_the_model_over_a_whole_window(self):
        blk = rail_cal(ff_prail_enable=1, ff_prail_curve=ramped_prail(),
                       ff_diag_window_ms=500)
        rails = self._stream(600)
        emu, mdl = self._replay(rails, blk)
        st = emu.read(PATCH_RAM, ff.STATE_LEN)
        self.assertEqual(struct.unpack_from(">h", st, 0x46)[0],
                         mdl.state.win_margin_min)
        self.assertEqual(struct.unpack_from(">H", st, 0x48)[0],
                         mdl.state.prist_min)
        self.assertEqual(st[0x43], mdl.state.msv_sat_ticks)
        self.assertEqual(struct.unpack_from(">H", st, 0x4A)[0],
                         mdl.state.diag_ticks)
        self.assertEqual(mdl.diag_window_ticks(), 50, "500 ms / 10 ms")

    def test_the_diagnostics_run_with_the_adder_disabled(self):
        """They observe stock cells, so they may not depend on the feature."""
        _emu, mdl = self._replay(self._stream(300), self.cal_blk)
        self.assertEqual(mdl.state.prail_add, 0)
        self.assertLess(mdl.state.prist_min, 0xFFFF)
        self.assertLess(mdl.state.win_margin_min, 0x7FFF)
        self.assertGreater(mdl.state.msv_sat_ticks, 0)

    def test_the_cost_of_one_activation(self):
        blk = rail_cal(ff_prail_enable=1, ff_prail_curve=ramped_prail())
        emu = self.fresh(cal=blk)
        first = emu.call(self.syms["ff_fuel_hook_b"], reset=False,
                         regs={"r1": TASK_STACK})
        self.assertTrue(first.ok, first.summary())
        self.assertLess(first.insns, 2600, "the cold-start activation got heavy")
        warm = emu.call(self.syms["ff_fuel_hook_b"], reset=False,
                        regs={"r1": TASK_STACK})
        self.assertLess(warm.insns, 1000, "the warm activation got heavy")


# ------------------------------------------- 6. the whole-SRAM proofs -------
class TestSramIsUntouched(tff.EmuBase):
    """The two proofs the 2026-09-17 rules ask for, at the hooked site.

    `hdrpsol_main` is run to completion on the stock image and on the patched
    one - once with `ff_prail_enable` = 0 and once with it 1 at the neutral
    curve - from the same reset state and the same seeded RAM, and the whole
    64 KB of SRAM is compared afterwards.  Nothing outside our own block may
    differ, and the four setpoint cells must be bit-identical.
    """

    MEM = None            # filled in setUpClass, after RailBase is defined

    def _stock_after(self, mem):
        from emu import Med9Emu
        stock = Med9Emu(DUMP)
        stock.reset()
        res = stock.call(HDRPSOL, mem=mem, reset=False,
                         regs={"r1": TASK_STACK})
        self.assertTrue(res.ok, res.summary())
        return res

    def _one(self, cal_blk, *, warm: int = 400):
        mem = RailBase._hdrpsol_ram(8000, 26214, 63, 0, 0)
        stock = self._stock_after(mem)
        before = stock.snapshot(tff.SRAM_START, tff.SRAM_LEN)

        patched = self.fresh(cal=cal_blk)
        for _ in range(warm):                 # build a real state block first
            self.no_frame(patched)
            patched.call(self.syms["ff_fuel_hook_b"], reset=False,
                         regs={"r1": TASK_STACK})
        res = patched.call(HDRPSOL, mem=mem, reset=False,
                           regs={"r1": TASK_STACK})
        self.assertTrue(res.ok, res.summary())
        after = res.snapshot(tff.SRAM_START, tff.SRAM_LEN)

        changed = {tff.SRAM_START + i for i in range(tff.SRAM_LEN)
                   if before[i] != after[i]}
        allowed = set(range(PATCH_RAM, PATCH_RAM + 0x100))
        allowed |= set(range(TASK_STACK - 0x3A8, TASK_STACK + 8))
        # D1's documented stock write: the warm-up activations above poll the
        # spare CAN receive slot, whose driver shadow is 0x803F98-0x803FA3 and
        # which nothing else in the image uses (patches/ff_fuel/README.md).
        allowed |= set(range(0x803F98, 0x803FA4))
        self.assertEqual(changed - allowed, set(),
                         f"{[hex(a) for a in sorted(changed - allowed)]}")
        return stock, res

    def test_one_setpoint_activation_moves_nothing_outside_our_block(self):
        self._one(self.cal_blk)

    def test_the_same_holds_with_the_feature_enabled_at_a_neutral_curve(self):
        self._one(rail_cal(ff_prail_enable=1))


# -------------------------------------------- 7. the diagnostics, group 109 --
class TestRailDispatcher(tff.EmuBase):
    @staticmethod
    def dispatch(emu, vid: int):
        res = emu.call(DISPATCH, args=[vid], reset=False)
        assert res.ok, res.summary()
        return (emu.read(RESULT_FORMULA, 1)[0],
                emu.read(RESULT_A, 1)[0],
                emu.read(RESULT_B, 1)[0])

    def warm(self, *, ticks=600):
        blk = rail_cal(ff_prail_enable=1, ff_prail_curve=ramped_prail())
        emu = self.fresh(cal=blk)
        mdl = ff.FlexFuelModel(tff.cal_from_block(blk))
        rail = ff.RailIn(wbho1s=14080, dwi=1200, prist=13000, msv=5000,
                         win_margin_req=ff.WIN_MARGIN_REQ_STOCK)
        for i in range(ticks):
            rx = (ff.frame(e_pct=85, counter=(i // 10) & 0xFF)
                  if i % 10 == 0 else None)
            if rx is not None:
                self.arm_frame(emu, rx)
            else:
                self.no_frame(emu)
            emu.write(RAM_WBHO1S, struct.pack(">H", rail.wbho1s))
            emu.write(RAM_DWI, struct.pack(">H", rail.dwi))
            emu.write(RAM_PRIST, struct.pack(">H", rail.prist))
            emu.write(RAM_VMSV, struct.pack(">H", rail.msv))
            emu.write(RAM_WIN_MARGIN, bytes([rail.win_margin_req]))
            emu.call(self.syms["ff_fuel_hook_b"], reset=False,
                     regs={"r1": TASK_STACK})
            mdl.tick(rx, rail=rail)
        return emu, mdl

    def test_the_four_ids_answer_with_the_model_triples(self):
        emu, mdl = self.warm()
        want = mdl.triples_pr()
        for vid, triple in zip(IDS, want):
            with self.subTest(id=vid):
                self.assertEqual(self.dispatch(emu, vid), triple)
        add, prist, margin, sat = (vag_formulas.decode(*x) for x in want)
        self.assertEqual(add.unit, "bar")
        self.assertAlmostEqual(add.value, mdl.state.prail_add * 0.005,
                               delta=0.006)
        self.assertAlmostEqual(prist.value, 13000 * 0.005, delta=0.006)
        self.assertEqual(sat.value, float(mdl.state.msv_sat_ticks))
        # 14080 - 1200 - 67*32 = 10736 angle LSB = 251.6 degCA
        self.assertAlmostEqual(margin.value, (10736 // 96) * 2.25, places=6)

    def test_an_invalid_block_answers_not_available_on_all_four(self):
        emu, _mdl = self.warm(ticks=20)
        emu.write(PATCH_RAM, b"\x00\x00\x00\x00")
        for vid in IDS:
            with self.subTest(id=vid):
                self.assertEqual(self.dispatch(emu, vid), (0x25, 0, 0))

    def test_a_negative_margin_is_reported_negative_and_floored(self):
        emu, mdl = self.warm(ticks=20)
        for lsb in (-4000, -96, -95, -1, 0, 1, 95, 96, 30000):
            with self.subTest(lsb=lsb):
                emu.write(PATCH_RAM + 0x46, lsb & 0xFFFF, 2)
                mdl.state.win_margin_min = lsb
                triple = self.dispatch(emu, IDS[2])
                self.assertEqual(triple, mdl.triples_pr()[2])
                want = min(max((lsb // 96) + 128, 0), 255)
                self.assertEqual(triple, (0x22, ff.FMT_A_WIN, want))

    def test_a_handler_writes_only_the_three_result_bytes(self):
        emu, _mdl = self.warm(ticks=20)
        before = emu.read(tff.SRAM_START, tff.SRAM_LEN)
        self.dispatch(emu, IDS[0])
        after = emu.read(tff.SRAM_START, tff.SRAM_LEN)
        moved = {tff.SRAM_START + i for i, (a, b) in
                 enumerate(zip(before, after)) if a != b}
        allowed = {RESULT_FORMULA, RESULT_B, RESULT_A, 0x7FD072}
        allowed |= set(range(0x7FB8E8, 0x7FB8EC))   # dispatcher's last-handler
        allowed |= set(range(tff.STACK_TOP - 0x100, tff.STACK_TOP + 8))
        self.assertEqual(moved - allowed, set(),
                         f"{sorted(hex(x) for x in moved - allowed)}")

    def test_each_handler_is_cheap(self):
        emu, _mdl = self.warm(ticks=20)
        for vid in IDS:
            res = emu.call(DISPATCH, args=[vid], reset=False)
            self.assertLess(res.insns, 100, f"id {vid} is heavy")


@requires_dump
class TestGroup109OverKwp(unittest.TestCase):
    """`21 6D` through the firmware's own SID 0x21 route, on the patched image."""

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

    def test_21_6d_returns_our_four_fields_and_four_empties(self):
        self.h.handle(b"\x10\x89")
        answer = self.h.handle(bytes([0x21, GROUP]))[-1]
        self.assertEqual(len(answer), 26, answer.hex())
        self.assertEqual(answer[0], 0x61)
        self.assertEqual(answer[1], GROUP)
        triples = [tuple(answer[2 + 3 * i:5 + 3 * i]) for i in range(8)]
        self.assertEqual([t[0] for t in triples[:4]], [0x53, 0x53, 0x22, 0x36])
        self.assertEqual(triples[4:], [(0x25, 0, 0)] * 4,
                         "the echo group 236 must stay empty")
        add, prist, margin, sat = (vag_formulas.decode(*x) for x in triples[:4])
        # the simulator animates the state block from the model with the
        # SHIPPED calibration, i.e. ff_prail_enable = 0, so field 1 reads 0 bar
        # -- which is exactly what a stock-behaviour image must show.
        self.assertEqual(add.value, 0.0, "the adder ships disabled")
        self.assertEqual(add.unit, "bar")
        self.assertEqual(prist.unit, "bar")
        self.assertGreaterEqual(sat.value, 0.0)
        self.assertIsInstance(margin.value, float)
