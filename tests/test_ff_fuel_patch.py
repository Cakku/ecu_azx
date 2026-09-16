"""patches/ff_fuel on the applied image, in the Unicorn harness (D1, #32/#37).

The layers, so a missing cross compiler only costs the first one:

  1. the stock facts the patch is built on - every hook word, every blank area
  2. apply: ALL OK (65 blocks), clean bindiff, the three hook words in place
  3. the segment stub: F = 1024 leaves `rk` alone bit for bit, F = 1536 scales
     it, and the tail branch reaches rksplit with r1 and LR untouched
  4. the periodic hook against `emu/models/flexfuel.py`, tick by tick, with
     TouCAN C message buffer 6 modelled from `re/findings/can.md` section 4 -
     the real `can_init_mb` and `can_rx_poll` run, nothing is stubbed
  5. cold start from garbage, ff_mode 0 and 2, and the proof that one periodic
     activation moves no RAM outside the state block, the driver shadow of our
     own slot and the stack

Nothing here writes to `data/` (DumpUnchanged asserts it) and nothing is ever
flashed.
"""
from __future__ import annotations

import json
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path

from tests.common import DUMP, REPO, DumpUnchanged, requires_dump

import bindiff  # noqa: E402  (tests.common put tools/ on sys.path)
import checksum as cs  # noqa: E402
import med9lib as m  # noqa: E402
import patch_apply  # noqa: E402
import patch_gen  # noqa: E402

sys.path.insert(0, str(REPO / "patches" / "ff_fuel"))
import ffcal001  # noqa: E402
from emu.models import flexfuel as ff  # noqa: E402

FF_FUEL = REPO / "patches" / "ff_fuel"

# --- facts under test (patches/ff_fuel/README.md) ---------------------------
RK_SITE, RK_OLD = 0x42247C, bytes.fromhex("4bff9f25")     # bl rksplit
HOOK_A_SITE, HOOK_A_OLD = 0x432940, bytes.fromhex("4bc8b0a5")   # bl 0xBD9E4
HOOK_B_SITE, HOOK_B_OLD = 0x12067C, bytes.fromhex("4bffe9b1")   # bl 0x11F02C
RKSPLIT = 0x41C3A0
NOP_LEAF = 0x0BD9E4
CLR_LEAF = 0x11F02C
PATCH_FLASH = 0x152000
PATCH_RAM = 0x7FFB00
STATE_LEN = 0x40
CORE_END = 0x2C                      # header + core, what the model pins
RK = 0x803038
CAN_SHADOW_ID = 0x803F98             # slot 15 id echo
CAN_SHADOW_DATA = 0x803F9C           # slot 15 payload
CAL_BASE = 0x5E2510

# TouCAN C, message buffer 6 (re/findings/can.md section 4; the base table at
# 0x2BC38 holds CANMCR = module base + 0x80, so module C is 0x707880).
TOUCAN_C_MCR = 0x707880
IFLAG = TOUCAN_C_MCR + 0x24          # 0x7078A4
MB6 = TOUCAN_C_MCR + 0x80 + 6 * 0x10  # 0x707960
MB6_DATA = MB6 + 6                   # 0x707966
MB6_BIT = 1 << 6

SRAM_START, SRAM_LEN = 0x7F8000, 0x10000
STACK_TOP = 0x7FEFFC
HOOK_TAIL_FRAME = 16

try:
    from emu import Med9Emu
    emu_available = True
except Exception:                                            # pragma: no cover
    emu_available = False
requires_emu = unittest.skipUnless(emu_available, "unicorn / emu package missing")


def load_patch() -> dict:
    return json.loads((FF_FUEL / "patch.json").read_text())


def cal_from_block(blk: bytes) -> ff.Cal:
    """A model `Cal` describing an FFCAL001 image, so the two cannot drift."""
    can_id, timeout, hold, tau, slew, tick = struct.unpack_from(">6H", blk, 0x0C)
    mode, e_ovr, stall = struct.unpack_from(">3B", blk, 0x18)
    return ff.Cal(can_id=can_id, timeout_ms=timeout, hold_s=hold,
                  filter_tau_ms=tau, slew_pct_s=slew, tick_ms=tick,
                  mode=mode, e_override=e_ovr, stall_max=stall,
                  f_curve=list(struct.unpack_from(">17H", blk, 0x20)))


@requires_dump
class TestStockFacts(DumpUnchanged):
    """What the patch assumes about the unmodified image."""

    def setUp(self):
        self.data = m.load_dump(str(DUMP))

    def _word(self, cpu: int) -> bytes:
        off = m.cpu_to_file(cpu)
        return bytes(self.data[off:off + 4])

    def test_the_three_hook_sites_hold_the_documented_words(self):
        for site, old in ((RK_SITE, RK_OLD), (HOOK_A_SITE, HOOK_A_OLD),
                          (HOOK_B_SITE, HOOK_B_OLD)):
            with self.subTest(site=hex(site)):
                self.assertEqual(self._word(site), old)
                kind, target = patch_gen.decode_branch(
                    int.from_bytes(old, "big"), site)
                self.assertEqual(kind, "bl")
        self.assertEqual(patch_gen.decode_branch(
            int.from_bytes(RK_OLD, "big"), RK_SITE)[1], RKSPLIT)
        self.assertEqual(patch_gen.decode_branch(
            int.from_bytes(HOOK_A_OLD, "big"), HOOK_A_SITE)[1], NOP_LEAF)
        self.assertEqual(patch_gen.decode_branch(
            int.from_bytes(HOOK_B_OLD, "big"), HOOK_B_SITE)[1], CLR_LEAF)

    def test_the_set_a_tail_target_is_an_empty_function(self):
        """scheduler.md section 8.1: one `blr`, and one caller in the image."""
        self.assertEqual(self._word(NOP_LEAF), bytes.fromhex("4e800020"))
        self.assertEqual(self._word(NOP_LEAF - 4), bytes.fromhex("4e800020"),
                         "the previous function must end right before it")

    def test_the_can_slot_15_id_word_is_still_unused(self):
        off = m.cpu_to_file(0x2BD8C)
        self.assertEqual(bytes(self.data[off:off + 4]), bytes.fromhex("000007ff"))
        self.assertEqual(bytes(self.data[off - 4:off]), bytes.fromhex("00000004"),
                         "the byte-order selector must stay != 2 (can.md 7)")

    def test_the_flash_and_calibration_areas_are_blank(self):
        blob_off = m.cpu_to_file(PATCH_FLASH)
        size = load_patch()["build"]["blob_size"]
        self.assertEqual(set(self.data[blob_off:blob_off + size]), {0xFF})
        cal_off = m.cpu_to_file(CAL_BASE)
        self.assertEqual(set(self.data[cal_off:cal_off + ffcal001.LENGTH]), {0xFF})


@requires_dump
class TestApply(DumpUnchanged):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp = Path(tempfile.mkdtemp())
        cls.data, cls.report, cls.warnings = patch_apply.apply_patch(DUMP, FF_FUEL)
        cls.image = cls.tmp / "ff_fuel.bin"
        cls.image.write_bytes(bytes(cls.data))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        super().tearDownClass()

    def test_it_applies_cleanly(self):
        self.assertTrue(self.report["ok"], self.report["issues"])
        self.assertEqual(self.report["bytes"]["unexpected"], 0)
        self.assertTrue(cs.verify(m.load_dump(str(self.image)), quiet=True))

    def test_bindiff_sees_only_the_patch_and_its_descriptors(self):
        ranges, report = bindiff.diff(m.load_dump(str(DUMP)),
                                      m.load_dump(str(self.image)),
                                      str(FF_FUEL / "patch.json"))
        self.assertTrue(report["ok"], report["issues"])
        self.assertEqual(report["counts"]["unexpected"], 0)

    def test_the_hook_words_point_at_the_trampolines(self):
        syms = load_patch()["build"]["symbols"]
        for site, name in ((RK_SITE, "ff_fuel_rk_hook"),
                           (HOOK_A_SITE, "ff_fuel_hook_a"),
                           (HOOK_B_SITE, "ff_fuel_hook_b")):
            with self.subTest(site=hex(site)):
                off = m.cpu_to_file(site)
                word = int.from_bytes(bytes(self.data[off:off + 4]), "big")
                self.assertEqual(patch_gen.decode_branch(word, site),
                                 ("bl", int(syms[name], 0)))

    def test_the_can_id_word_now_carries_0x0ec(self):
        off = m.cpu_to_file(0x2BD8C)
        self.assertEqual(bytes(self.data[off:off + 4]), bytes.fromhex("000000ec"))

    def test_ffcal001_is_in_the_image_and_validates(self):
        off = m.cpu_to_file(CAL_BASE)
        blk = bytes(self.data[off:off + ffcal001.LENGTH])
        ffcal001.check(blk)
        self.assertEqual(blk, (FF_FUEL / "build" / "ffcal001.bin").read_bytes())
        # and through the high alias the patch actually uses
        self.assertEqual(m.cpu_to_file(CAL_BASE), m.cpu_to_file(0x1E2510))

    def test_the_identification_block_is_untouched(self):
        stock = m.load_dump(str(DUMP))
        off = m.cpu_to_file(patch_apply.IDENT_START)
        n = patch_apply.IDENT_END - patch_apply.IDENT_START
        self.assertEqual(bytes(self.data[off:off + n]), bytes(stock[off:off + n]))

    def test_the_on_chip_and_ram_warnings_are_both_raised(self):
        self.assertTrue(any('"static"' in w for w in self.warnings), self.warnings)
        self.assertEqual(sum("on-chip flash" in w for w in self.warnings), 2,
                         "both on-chip hook sites must warn")


@requires_dump
@requires_emu
class EmuBase(DumpUnchanged):
    """One patched image, built once, for every emulated test below."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp = Path(tempfile.mkdtemp())
        cls.image = cls.tmp / "ff_fuel.bin"
        data, _report, _w = patch_apply.apply_patch(DUMP, FF_FUEL)
        cls.image.write_bytes(bytes(data))
        cls.syms = {k: int(v, 0) for k, v in load_patch()["build"]["symbols"].items()}
        cls.cal_blk = (FF_FUEL / "build" / "ffcal001.bin").read_bytes()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        super().tearDownClass()

    # -- the TouCAN model ------------------------------------------------
    @staticmethod
    def arm_frame(emu, payload: bytes, dlc: int = 8) -> None:
        """What the hardware does when a matching frame arrives on MB6."""
        emu.write(IFLAG, MB6_BIT, 2)
        emu.write(MB6, dlc, 2)              # CODE/BUSY clear, LENGTH = dlc
        emu.write(MB6_DATA, payload)

    @staticmethod
    def no_frame(emu) -> None:
        emu.write(IFLAG, 0, 2)

    def fresh(self, *, cal: bytes | None = None, garbage: bytes | None = None):
        emu = Med9Emu(self.image)
        emu.reset()
        if cal is not None:
            emu.write(CAL_BASE, cal)
        if garbage is not None:
            emu.write(PATCH_RAM, garbage)
        self.no_frame(emu)
        return emu

    @staticmethod
    def block(emu) -> bytes:
        return emu.read(PATCH_RAM, CORE_END)


# ---------------------------------------------------- 3. the segment stub ---
class TestRkStub(EmuBase):
    def _seeded(self, f_q10: int) -> bytes:
        mdl = ff.FlexFuelModel()
        mdl.state.f_q10 = f_q10
        mdl.seal()
        return mdl.block_bytes()

    def test_f_1024_leaves_rk_untouched(self):
        emu = self.fresh()
        emu.write(PATCH_RAM, self._seeded(1024))
        for rk in (0, 1, 0x7FFF, 0xFFFF):
            with self.subTest(rk=rk):
                emu.write(RK, rk, 2)
                res = emu.call(self.syms["ff_fuel_rk_hook"], reset=False)
                self.assertTrue(res.ok, f"{res.stop_reason}: {res.issues}")
                self.assertEqual(struct.unpack(">H", emu.read(RK, 2))[0], rk)

    def test_an_uninitialised_state_block_leaves_rk_untouched(self):
        """The first milliseconds after power-up, before any periodic tick."""
        emu = self.fresh(garbage=bytes(range(0x40)))
        for rk in (0, 1234, 0xFFFF):
            emu.write(RK, rk, 2)
            emu.call(self.syms["ff_fuel_rk_hook"], reset=False)
            self.assertEqual(struct.unpack(">H", emu.read(RK, 2))[0], rk)

    def test_f_1536_scales_and_saturates(self):
        emu = self.fresh()
        emu.write(PATCH_RAM, self._seeded(1536))
        mdl = ff.FlexFuelModel()
        mdl.state.f_q10 = 1536
        for rk in (0, 1, 1000, 0x7FFF, 0xAAAA, 0xFFFF):
            with self.subTest(rk=rk):
                emu.write(RK, rk, 2)
                emu.call(self.syms["ff_fuel_rk_hook"], reset=False)
                got = struct.unpack(">H", emu.read(RK, 2))[0]
                self.assertEqual(got, min((rk * 1536) >> 10, 0xFFFF))
                self.assertEqual(got, mdl.rk_scale(rk))

    def test_a_calibration_beyond_the_ceiling_is_clamped_in_code(self):
        emu = self.fresh()
        emu.write(PATCH_RAM, self._seeded(0xFFFF))
        emu.write(RK, 1000, 2)
        emu.call(self.syms["ff_fuel_rk_hook"], reset=False)
        self.assertEqual(struct.unpack(">H", emu.read(RK, 2))[0],
                         (1000 * ff.F_MAX) >> 10)

    def test_rksplit_is_entered_with_r1_and_lr_unchanged(self):
        emu = self.fresh()
        emu.write(PATCH_RAM, self._seeded(1536))
        res = emu.run(self.syms["ff_fuel_rk_hook"], until=RKSPLIT,
                      regs={"lr": 0x00123450}, reset=False)
        self.assertEqual(res.pc, RKSPLIT, res.issues)
        self.assertEqual(res.regs["lr"], 0x00123450,
                         "rksplit's blr must still return into task_segment_a")
        self.assertEqual(res.regs["r1"], STACK_TOP)

    def test_the_tail_branch_is_a_ba_to_rksplit(self):
        emu = self.fresh()
        tail = self.syms["ff_fuel_rk_hook"] + 0x1C
        self.assertEqual(emu.read(tail, 4),
                         patch_gen.encode_branch(tail, RKSPLIT, "ba").to_bytes(4, "big"))

    def test_it_counts_its_own_invocations(self):
        emu = self.fresh()
        emu.write(PATCH_RAM, self._seeded(1024))
        for i in range(1, 4):
            emu.call(self.syms["ff_fuel_rk_hook"], reset=False)
            self.assertEqual(struct.unpack(">I", emu.read(PATCH_RAM + 0x2C, 4))[0], i)

    def test_it_touches_no_ram_but_rk_and_its_own_counter(self):
        emu = self.fresh()
        emu.write(PATCH_RAM, self._seeded(1536))
        emu.write(RK, 2000, 2)
        before = bytearray(emu.read(SRAM_START, SRAM_LEN))
        res = emu.run(self.syms["ff_fuel_rk_hook"], until=RKSPLIT, reset=False)
        self.assertTrue(res.pc == RKSPLIT)
        after = emu.read(SRAM_START, SRAM_LEN)
        changed = {SRAM_START + i for i in range(SRAM_LEN) if before[i] != after[i]}
        allowed = {RK, RK + 1}
        allowed |= set(range(PATCH_RAM + 0x2C, PATCH_RAM + 0x30))   # rk_calls
        allowed |= set(range(STACK_TOP - HOOK_TAIL_FRAME, STACK_TOP))
        self.assertEqual(changed - allowed, set(),
                         f"{[hex(a) for a in sorted(changed - allowed)]}")


# ------------------------------------------------ 4. the periodic hook ------
class TestPeriodicHook(EmuBase):
    """The real can_init_mb and can_rx_poll run; only the hardware is modelled."""

    def _replay(self, steps, *, hook="ff_fuel_hook_b", src=ff.SRC_B,
                cal: bytes | None = None):
        """Drive the patch and the model through the same activations."""
        blk = cal if cal is not None else self.cal_blk
        emu = self.fresh(cal=None if cal is None else blk)
        mdl = ff.FlexFuelModel(cal_from_block(blk))
        for n, (rx, payload) in enumerate(steps):
            if rx:
                self.arm_frame(emu, payload)
            else:
                self.no_frame(emu)
            res = emu.call(self.syms[hook], reset=False)
            self.assertTrue(res.ok, f"activation {n}: {res.stop_reason} {res.issues}")
            mdl.tick(payload if rx else None, src=src)
            self.assertEqual(self.block(emu).hex(), mdl.block_bytes().hex(),
                             f"state differs from the model at activation {n}")
        return emu, mdl

    @staticmethod
    def _stream(n, *, every=10, e_pct=85, status=0, start=0, freeze=False):
        """`n` activations with a frame every `every` of them."""
        ctr = start
        out = []
        for k in range(n):
            if k % every == 0:
                if not freeze:
                    ctr = (ctr + 1) & 0xFF
                out.append((True, ff.frame(e_pct=e_pct, counter=ctr, status=status)))
            else:
                out.append((False, None))
        return out

    def test_power_up_is_fault_at_f_1024(self):
        emu, mdl = self._replay([(False, None)] * 5)
        self.assertEqual(mdl.state.mode, ff.MODE_FAULT)
        self.assertEqual(mdl.state.f_q10, ff.F_MIN)
        self.assertEqual(struct.unpack_from(">H", self.block(emu), 0x0A)[0], 1024)

    def test_can_init_mb_armed_the_buffer(self):
        """ff_state_init calls can_init_mb(15): CODE = EMPTY and the id is set."""
        emu = self.fresh()
        emu.call(self.syms["ff_fuel_hook_b"], reset=False)
        self.assertEqual(struct.unpack(">H", emu.read(MB6 + 2, 2))[0], 0x0EC << 5)
        self.assertEqual(struct.unpack(">H", emu.read(MB6, 2))[0] & 0x00F0, 0x40)
        self.assertEqual(struct.unpack(">I", emu.read(CAN_SHADOW_ID, 4))[0], 0x0EC)

    def test_a_frame_reaches_the_driver_shadow_and_the_state(self):
        payload = ff.frame(e_pct=73, t_fuel_c=31, counter=5, fw=2)
        emu, mdl = self._replay([(False, None), (True, payload)])
        self.assertEqual(emu.read(CAN_SHADOW_DATA, 8), payload)
        st = self.block(emu)
        self.assertEqual(st[0x0E], 73)          # ff_e_raw
        self.assertEqual(st[0x0F], 31 + 40)     # ff_t_fuel
        self.assertEqual(st[0x10], 5)           # ff_frame_ctr
        self.assertEqual(st[0x11], 2)           # ff_fw_ver
        self.assertEqual(st[0x0C], ff.MODE_OK)
        self.assertEqual(mdl.state.mode, ff.MODE_OK)

    def test_a_long_run_tracks_the_model_tick_by_tick(self):
        emu, mdl = self._replay(self._stream(400, e_pct=85))
        self.assertEqual(mdl.state.mode, ff.MODE_OK)
        self.assertGreater(mdl.state.e_filt, 0)
        self.assertGreater(mdl.state.f_q10, ff.F_MIN)
        self.assertEqual(struct.unpack_from(">I", self.block(emu), 0x20)[0], 400)

    def test_contaminated_then_recovery(self):
        ok = self._stream(200, e_pct=60)
        bad = self._stream(120, e_pct=0, status=2, start=20)
        emu, mdl = self._replay(ok)
        held = (mdl.state.e_filt, mdl.state.f_q10)
        emu, mdl = self._replay(ok + bad)
        self.assertEqual(mdl.state.mode, ff.MODE_HOLD)
        self.assertEqual((mdl.state.e_filt, mdl.state.f_q10), held,
                         "HOLD must freeze the estimate, not follow E0")
        emu, mdl = self._replay(ok + bad + self._stream(60, e_pct=60, start=32))
        self.assertEqual(mdl.state.mode, ff.MODE_OK)
        self.assertEqual(mdl.state.faults, 0, "contaminated fuel is not a FAULT")

    def test_a_frozen_counter_becomes_a_fault(self):
        steps = self._stream(200, e_pct=60) + self._stream(60, e_pct=60,
                                                           start=20, freeze=True)
        emu, mdl = self._replay(steps)
        self.assertEqual(mdl.state.mode, ff.MODE_FAULT)
        self.assertGreaterEqual(self.block(emu)[0x1C], mdl.cal.stall_max)

    def test_silence_becomes_a_fault_and_arms_the_hold(self):
        steps = self._stream(200, e_pct=60) + [(False, None)] * 150
        emu, mdl = self._replay(steps)
        st = self.block(emu)
        self.assertEqual(st[0x0C], ff.MODE_FAULT)
        self.assertEqual(mdl.state.mode, ff.MODE_FAULT)
        hold = struct.unpack_from(">H", st, 0x16)[0]
        self.assertGreater(hold, 5800, "the 60 s hold must be armed")
        # and F is still the held value, not 1024
        self.assertGreater(struct.unpack_from(">H", st, 0x0A)[0], ff.F_MIN)

    def test_a_sensor_fault_status_is_a_fault(self):
        steps = (self._stream(200, e_pct=60)
                 + [(True, ff.frame(e_pct=60, counter=99, status=1))])
        _emu, mdl = self._replay(steps)
        self.assertEqual(mdl.state.mode, ff.MODE_FAULT)

    def test_the_set_a_hook_behaves_identically(self):
        steps = self._stream(120, e_pct=50)
        emu_a, mdl_a = self._replay(steps, hook="ff_fuel_hook_a", src=ff.SRC_A)
        emu_b, mdl_b = self._replay(steps, hook="ff_fuel_hook_b", src=ff.SRC_B)
        a, b = bytearray(self.block(emu_a)), bytearray(self.block(emu_b))
        self.assertEqual(a[0x1D], ff.SRC_A)     # ff_src_owner
        self.assertEqual(b[0x1D], ff.SRC_B)
        # everything except the owner/seen bytes and the checksum must match
        for i in (0x06, 0x07, 0x1D, 0x1E):
            a[i] = b[i] = 0
        self.assertEqual(a, b)

    def test_the_set_a_hook_tail_branches_to_the_empty_leaf(self):
        emu = self.fresh()
        res = emu.call(self.syms["ff_fuel_hook_a"], reset=False)
        self.assertTrue(res.ok, res.issues)
        tail = self.syms["ff_fuel_hook_a"] + 0x1C
        self.assertEqual(emu.read(tail, 4),
                         patch_gen.encode_branch(tail, NOP_LEAF, "ba").to_bytes(4, "big"))

    def test_the_set_b_hook_still_runs_the_stock_leaf(self):
        """0x11F02C clears 0x7FE889 and 0x800E18; it must still happen."""
        emu = self.fresh()
        emu.write(0x7FE889, 0xAA, 1)
        emu.write(0x800E18, 0xBEEF, 2)
        emu.call(self.syms["ff_fuel_hook_b"], reset=False)
        self.assertEqual(emu.read(0x7FE889, 1), b"\x00")
        self.assertEqual(emu.read(0x800E18, 2), b"\x00\x00")

    def test_only_one_hook_ticks_when_both_fire(self):
        emu = self.fresh()
        for _ in range(20):
            emu.call(self.syms["ff_fuel_hook_a"], reset=False)
            emu.call(self.syms["ff_fuel_hook_b"], reset=False)
        st = self.block(emu)
        self.assertEqual(struct.unpack_from(">I", st, 0x20)[0], 20)
        self.assertEqual(st[0x1D], ff.SRC_A)
        self.assertEqual(st[0x1E], ff.SRC_A | ff.SRC_B)

    def test_ownership_moves_when_the_first_source_goes_quiet(self):
        emu = self.fresh()
        for _ in range(5):
            emu.call(self.syms["ff_fuel_hook_a"], reset=False)
        for _ in range(ff.OWNER_SWITCH):
            emu.call(self.syms["ff_fuel_hook_b"], reset=False)
        st = self.block(emu)
        self.assertEqual(st[0x1D], ff.SRC_B)
        self.assertEqual(struct.unpack_from(">I", st, 0x20)[0], 6)


# --------------------------------------------- 5. cold start, modes, purity --
class TestColdStartAndModes(EmuBase):
    def test_the_first_activation_initialises_the_block_from_garbage(self):
        for fill in (b"\x00" * STATE_LEN, b"\xFF" * STATE_LEN,
                     bytes(range(STATE_LEN)),
                     bytes.fromhex("46463031") + b"\xA5" * (STATE_LEN - 4)):
            with self.subTest(fill=fill[:4].hex()):
                emu = self.fresh(garbage=fill)
                emu.call(self.syms["ff_fuel_hook_b"], reset=False)
                st = self.block(emu)
                self.assertEqual(struct.unpack_from(">I", st, 0)[0], 0x46463031)
                self.assertEqual(struct.unpack_from(">H", st, 4)[0], STATE_LEN)
                self.assertEqual(struct.unpack_from(">H", st, 0x0A)[0], ff.F_MIN)
                self.assertEqual(st[0x0C], ff.MODE_FAULT)
                self.assertEqual(st[0x13], 1, "cal_ok")
                self.assertEqual(struct.unpack_from(">I", st, 0x20)[0], 1)
                # the annex is zeroed too
                self.assertEqual(emu.read(PATCH_RAM + 0x2C, 0x14),
                                 b"\x00" * 0x14)

    def test_a_corrupt_checksum_re_initialises(self):
        emu = self.fresh()
        for _ in range(50):
            emu.call(self.syms["ff_fuel_hook_b"], reset=False)
        self.assertEqual(struct.unpack_from(">I", self.block(emu), 0x20)[0], 50)
        emu.write(PATCH_RAM + 6, 0x1234, 2)          # wrong csum
        emu.call(self.syms["ff_fuel_hook_b"], reset=False)
        self.assertEqual(struct.unpack_from(">I", self.block(emu), 0x20)[0], 1)

    def test_mode_0_forces_f_to_1024_and_never_polls(self):
        params = ffcal001.load_params(FF_FUEL / "ffcal001.json")
        blk = ffcal001.build(dict(params, ff_mode=0))
        emu = self.fresh(cal=blk)
        self.arm_frame(emu, ff.frame(e_pct=85, counter=1))
        for _ in range(20):
            emu.call(self.syms["ff_fuel_hook_b"], reset=False)
        st = self.block(emu)
        self.assertEqual(st[0x0C], ff.MODE_OFF)
        self.assertEqual(struct.unpack_from(">H", st, 0x0A)[0], ff.F_MIN)
        self.assertEqual(st[0x12], 0, "cal_mode")
        self.assertEqual(struct.unpack(">H", emu.read(IFLAG, 2))[0], MB6_BIT,
                         "mode 0 must not poll: the IFLAG bit is still set")
        self.assertEqual(emu.read(CAN_SHADOW_DATA, 8), b"\x00" * 8)
        # and rk is untouched
        emu.write(RK, 4321, 2)
        emu.call(self.syms["ff_fuel_rk_hook"], reset=False)
        self.assertEqual(struct.unpack(">H", emu.read(RK, 2))[0], 4321)

    def test_mode_2_follows_the_override_with_no_frame_at_all(self):
        params = ffcal001.load_params(FF_FUEL / "ffcal001.json")
        blk = ffcal001.build(dict(params, ff_mode=2, ff_e_override=50))
        emu = self.fresh(cal=blk)
        mdl = ff.FlexFuelModel(cal_from_block(blk))
        for n in range(200):
            emu.call(self.syms["ff_fuel_hook_b"], reset=False)
            mdl.tick(None)
            self.assertEqual(self.block(emu).hex(), mdl.block_bytes().hex(), n)
        st = self.block(emu)
        self.assertEqual(st[0x0C], ff.MODE_OVERRIDE)
        self.assertGreater(struct.unpack_from(">H", st, 0x08)[0], 0)
        self.assertGreater(struct.unpack_from(">H", st, 0x0A)[0], ff.F_MIN)
        self.assertEqual(emu.read(CAN_SHADOW_DATA, 8), b"\x00" * 8)

    def test_an_all_ff_calibration_behaves_like_mode_0(self):
        emu = self.fresh(cal=b"\xFF" * ffcal001.LENGTH)
        for _ in range(5):
            emu.call(self.syms["ff_fuel_hook_b"], reset=False)
        st = self.block(emu)
        self.assertEqual(st[0x13], 0, "cal_ok must be 0")
        self.assertEqual(st[0x0C], ff.MODE_OFF)
        self.assertEqual(struct.unpack_from(">H", st, 0x0A)[0], ff.F_MIN)

    def test_a_corrupt_calibration_checksum_behaves_like_mode_0(self):
        blk = bytearray(self.cal_blk)
        blk[0x20] ^= 0x01
        emu = self.fresh(cal=bytes(blk))
        emu.call(self.syms["ff_fuel_hook_b"], reset=False)
        self.assertEqual(self.block(emu)[0x13], 0)
        self.assertEqual(self.block(emu)[0x0C], ff.MODE_OFF)

    def test_one_activation_moves_only_our_own_ram(self):
        """Stock vs patched over the same hooked site, whole SRAM compared."""
        snaps, insns = {}, {}
        for tag, image in (("stock", DUMP), ("patched", self.image)):
            emu = Med9Emu(image)
            emu.reset()
            self.no_frame(emu)
            res = emu.run(HOOK_B_SITE, until=HOOK_B_SITE + 4, reset=False)
            self.assertTrue(res.ok, f"{tag}: {res.stop_reason} {res.issues}")
            self.assertEqual(res.regs["r1"], STACK_TOP, tag)
            snaps[tag], insns[tag] = res.snapshot(SRAM_START, SRAM_LEN), res.insns

        changed = {SRAM_START + i for i in range(SRAM_LEN)
                   if snaps["stock"][i] != snaps["patched"][i]}
        allowed = set(range(PATCH_RAM, PATCH_RAM + STATE_LEN))
        allowed |= set(range(CAN_SHADOW_ID, CAN_SHADOW_ID + 12))   # our own slot
        allowed |= set(range(STACK_TOP - 0x100, STACK_TOP))        # the stack
        self.assertEqual(changed - allowed, set(),
                         f"the patch moved RAM it must not: "
                         f"{[hex(a) for a in sorted(changed - allowed)]}")
        self.assertTrue(changed & set(range(PATCH_RAM, PATCH_RAM + STATE_LEN)))
        self.assertEqual(insns["stock"], 5)     # bl + the 4-instruction leaf

    def test_the_stack_stays_well_inside_the_task_stack(self):
        """The ERCOSEK task stack is 0x7FF3C0-0x7FF76F (ram.md section 4.1)."""
        emu = self.fresh()
        emu.call(self.syms["ff_fuel_hook_b"], reset=False)   # the deep one: init
        below = emu.read(STACK_TOP - 0x400, 0x400 - 0x100)
        self.assertEqual(set(below), {0},
                         "one activation used more than 256 bytes of stack")

    def test_the_cost_of_one_activation(self):
        """Recorded in README.md; a regression here is a real-time problem."""
        emu = self.fresh()
        first = emu.call(self.syms["ff_fuel_hook_b"], reset=False)
        self.arm_frame(emu, ff.frame(e_pct=85, counter=1))
        warm = emu.call(self.syms["ff_fuel_hook_b"], reset=False)
        self.assertLess(first.insns, 2000, "the cold-start activation got heavy")
        self.assertLess(warm.insns, 800, "the warm activation got heavy")
        rk = emu.call(self.syms["ff_fuel_rk_hook"], reset=False)
        self.assertLess(rk.insns, 200, "the segment stub got heavy")


if __name__ == "__main__":
    unittest.main()
