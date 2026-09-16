"""emu/models/flexfuel.py and patches/ff_fuel/ffcal001.py (brief D1, #32/#37).

The model is the specification of the patch: `tests/test_ff_fuel_patch.py`
runs the compiled code against it in the Unicorn harness, so everything the
patch must do is pinned here first, in plain Python.

Four groups:

  1. the docs/05 section 3.3 fuel-mass formula and the 17-point curve built
     from it - F(0) exactly 1.000 is what makes E0 bit-identical;
  2. the fixed-point curve lookup, including the clamp the patch applies
     whatever the calibration says;
  3. the state machine of docs/05 section 3.2 and the #37 fault matrix:
     OK / HOLD / FAULT, the 60 s hold, the decay, timeout, counter stall,
     contaminated fuel, recovery, and the two off-normal modes;
  4. the FFCAL001 block: layout against src/ff_state.h, round-trip through the
     ECU-side validation, and the descriptor rows the integrator appends to
     re/calibration_draft.csv.
"""
from __future__ import annotations

import csv
import io
import json
import re
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.common import REPO

sys.path.insert(0, str(REPO / "patches" / "ff_fuel"))
import ffcal001  # noqa: E402
from emu.models import flexfuel as ff  # noqa: E402

FF_FUEL = REPO / "patches" / "ff_fuel"
STATE_H = FF_FUEL / "src" / "ff_state.h"
DRAFT_CSV = REPO / "re" / "calibration_draft.csv"


def run(model: ff.FlexFuelModel, n: int, *, rx_every: int | None = None,
        frame_kw: dict | None = None, counter_step: int = 1,
        src: int = ff.SRC_B) -> ff.State:
    """`n` activations, optionally with a frame every `rx_every` of them."""
    ctr = model.state.frame_ctr
    for k in range(n):
        rx = None
        if rx_every and k % rx_every == 0:
            ctr = (ctr + counter_step) & 0xFF
            rx = ff.frame(counter=ctr, **(frame_kw or {}))
        model.tick(rx, src=src)
    return model.state


# ------------------------------------------------------------ 1. the formula --
class TestFuelMassFormula(unittest.TestCase):
    def test_e0_is_exactly_one(self):
        self.assertEqual(ff.fuel_mass_factor(0.0), 1.0)

    def test_e100_is_the_stoichiometric_ratio(self):
        self.assertAlmostEqual(ff.fuel_mass_factor(1.0), 14.7 / 9.0, places=12)

    def test_the_documented_1_50_is_at_78_percent_not_85(self):
        """docs/05 section 3.3 says '1.50 at E85'; its own formula says 1.5429
        at a volume fraction of 0.85 and 1.500 at 0.78 - which is exactly the
        75-81 % that section 2 quotes for pump 'E85'.  The curve is indexed by
        the SENSOR's volume fraction, so 0.85 must carry 1.5429."""
        self.assertAlmostEqual(ff.fuel_mass_factor(0.78), 1.500, places=3)
        self.assertAlmostEqual(ff.fuel_mass_factor(0.85), 1.5429, places=4)

    def test_it_is_strictly_increasing(self):
        vals = [ff.fuel_mass_factor(i / 200.0) for i in range(201)]
        self.assertTrue(all(b > a for a, b in zip(vals, vals[1:])))


class TestFCurve(unittest.TestCase):
    def setUp(self):
        self.curve = ff.f_curve_from_formula()

    def test_seventeen_points_one_every_6_25_percent(self):
        self.assertEqual(len(self.curve), 17)
        self.assertEqual(ff.E_FILT_MAX // (len(self.curve) - 1), ff.CURVE_STEP)

    def test_f0_is_exactly_1024(self):
        self.assertEqual(self.curve[0], ff.F_MIN)

    def test_monotonic_and_inside_the_ceiling(self):
        self.assertTrue(all(b >= a for a, b in zip(self.curve, self.curve[1:])))
        self.assertLessEqual(max(self.curve), ff.F_MAX)

    def test_e100_matches_the_formula(self):
        self.assertEqual(self.curve[16], round(14.7 / 9.0 * 1024))


# ------------------------------------------------------- 2. the interpolation --
class TestCurveLookup(unittest.TestCase):
    def setUp(self):
        self.m = ff.FlexFuelModel()

    def test_breakpoints_are_hit_exactly(self):
        for i, v in enumerate(self.m.cal.f_curve):
            self.assertEqual(self.m.f_of(i * ff.CURVE_STEP), v, f"point {i}")

    def test_above_full_scale_saturates(self):
        self.assertEqual(self.m.f_of(ff.E_FILT_MAX), self.m.cal.f_curve[16])
        self.assertEqual(self.m.f_of(0xFFFF), self.m.cal.f_curve[16])

    def test_midpoint_is_the_linear_interpolation(self):
        a, b = self.m.cal.f_curve[0], self.m.cal.f_curve[1]
        self.assertEqual(self.m.f_of(50), a + (b - a) * 50 // 100)

    def test_the_result_is_clamped_whatever_the_calibration_says(self):
        cal = ff.Cal(f_curve=[9000] * 17)
        self.assertEqual(ff.FlexFuelModel(cal).f_of(800), ff.F_MAX)
        cal = ff.Cal(f_curve=[7] * 17)
        self.assertEqual(ff.FlexFuelModel(cal).f_of(800), ff.F_MIN)

    def test_rk_scaling(self):
        m = ff.FlexFuelModel()
        m.state.f_q10 = 1536
        for rk in (0, 1, 1000, 0x7FFF, 0xFFFF):
            self.assertEqual(m.rk_scale(rk), min((rk * 1536) >> 10, 0xFFFF))

    def test_f_1024_never_changes_rk(self):
        m = ff.FlexFuelModel()
        self.assertEqual(m.state.f_q10, ff.F_MIN)
        for rk in (0, 1, 0x7FFF, 0xFFFF):
            self.assertEqual(m.rk_scale(rk), rk)


# ----------------------------------------------------- 3. the state machine --
class TestStateMachine(unittest.TestCase):
    def test_power_up_is_fault_at_e0_with_f_1024(self):
        m = ff.FlexFuelModel()
        st = m.state
        self.assertEqual((st.mode, st.e_filt, st.f_q10),
                         (ff.MODE_FAULT, 0, ff.F_MIN))
        self.assertEqual(m.can_init_calls, 1, "can_init_mb(15) at init")
        st = run(m, 5)
        self.assertEqual(st.mode, ff.MODE_FAULT)
        self.assertEqual(st.f_q10, ff.F_MIN)

    def test_the_first_good_frame_switches_to_ok(self):
        m = ff.FlexFuelModel()
        m.tick(ff.frame(e_pct=50, counter=1))
        self.assertEqual(m.state.mode, ff.MODE_OK)
        self.assertEqual(m.state.frames, 1)

    def test_the_slew_limit_is_2_percent_per_second(self):
        m = ff.FlexFuelModel()
        run(m, 1000, rx_every=10, frame_kw={"e_pct": 100})   # 10 s at 10 ms
        # 2 %/s for 10 s, minus the first activation that only sets up the mode
        self.assertAlmostEqual(m.state.e_filt / 16.0, 20.0, delta=0.15)

    def test_the_filter_takes_over_near_the_target(self):
        """Within one slew step of the target the first-order filter rules, and
        a tau of 3000 ms at 10 ms per activation is K = 1/300."""
        m = ff.FlexFuelModel()
        m.state.e_filt, m.state.e_frac = 16 * 50, 0
        m.seal()                                             # keep the header valid
        m.tick(ff.frame(e_pct=51, counter=1))                # 1 % to go = 16
        moved = (m.state.e_filt * ff.FRAC + m.state.e_frac) - 50 * 16 * ff.FRAC
        self.assertEqual(moved, 16 * ff.FRAC * 10 // 3000)

    def test_it_converges_all_the_way(self):
        m = ff.FlexFuelModel()
        run(m, 20000, rx_every=10, frame_kw={"e_pct": 85})
        self.assertEqual(m.state.e_filt, 85 * 16)
        self.assertEqual(m.state.f_q10, m.f_of(85 * 16))
        self.assertAlmostEqual(m.state.f_q10 / 1024.0, 1.5429, delta=0.002)

    def test_contaminated_status_holds_the_estimate(self):
        m = ff.FlexFuelModel()
        run(m, 3000, rx_every=10, frame_kw={"e_pct": 60})
        held_e, held_f = m.state.e_filt, m.state.f_q10
        self.assertEqual(m.state.mode, ff.MODE_OK)
        run(m, 500, rx_every=10, frame_kw={"e_pct": 0, "status": 2})
        self.assertEqual(m.state.mode, ff.MODE_HOLD)
        self.assertEqual((m.state.e_filt, m.state.f_q10), (held_e, held_f))
        # and it recovers when the status clears
        run(m, 100, rx_every=10, frame_kw={"e_pct": 60})
        self.assertEqual(m.state.mode, ff.MODE_OK)

    def test_a_sensor_fault_status_is_a_fault(self):
        for status in (1, 3):
            with self.subTest(status=status):
                m = ff.FlexFuelModel()
                run(m, 500, rx_every=10, frame_kw={"e_pct": 50})
                m.tick(ff.frame(e_pct=50, counter=99, status=status))
                self.assertEqual(m.state.mode, ff.MODE_FAULT)

    def test_a_stalled_counter_is_a_fault_after_three_frames(self):
        m = ff.FlexFuelModel()
        run(m, 500, rx_every=10, frame_kw={"e_pct": 50})
        self.assertEqual(m.state.mode, ff.MODE_OK)
        for i in range(1, 4):
            m.tick(ff.frame(e_pct=50, counter=m.state.frame_ctr))
            self.assertEqual(m.state.stall, i)
            expect = ff.MODE_FAULT if i >= m.cal.stall_max else ff.MODE_OK
            self.assertEqual(m.state.mode, expect, f"repeat {i}")

    def test_silence_longer_than_the_timeout_is_a_fault(self):
        m = ff.FlexFuelModel()
        run(m, 500, rx_every=10, frame_kw={"e_pct": 50})
        self.assertEqual(m.state.mode, ff.MODE_OK)
        left = (m.cal.timeout_ms // m.cal.tick()) - m.state.age_ticks
        run(m, left)                                  # exactly 1000 ms, still OK
        self.assertEqual(m.state.age_ticks * m.cal.tick(), m.cal.timeout_ms)
        self.assertEqual(m.state.mode, ff.MODE_OK)
        m.tick(None)                                  # 1010 ms
        self.assertEqual(m.state.mode, ff.MODE_FAULT)
        self.assertEqual(m.state.faults, 1)

    def test_fault_holds_f_for_sixty_seconds_then_decays_to_e_key(self):
        m = ff.FlexFuelModel()
        run(m, 4000, rx_every=10, frame_kw={"e_pct": 80})       # 40 s of E80
        run(m, (m.cal.timeout_ms // m.cal.tick()) - m.state.age_ticks)
        m.tick(None)                                            # -> FAULT, now
        self.assertEqual(m.state.mode, ff.MODE_FAULT)
        held_e, held_f = m.state.e_filt, m.state.f_q10
        self.assertGreater(held_f, 1300)
        # 6000 activations = 60.00 s; the entry activation is the first of
        # them, so the counter reads 5999 once it has run.
        self.assertEqual(m.state.hold_ticks, 5999)
        run(m, 5999)                                            # to the last one
        self.assertEqual(m.state.hold_ticks, 0)
        self.assertEqual((m.state.e_filt, m.state.f_q10), (held_e, held_f),
                         "F must be held for the whole ff_hold_s window")
        run(m, 2)                                               # past 60 s
        self.assertLess(m.state.e_filt, held_e, "the decay must have started")

        # ... at ff_slew, and all the way down to e_key (E0 until D2)
        before = m.state.e_filt
        run(m, 100)                                             # 1 s
        self.assertAlmostEqual((before - m.state.e_filt) / 16.0, 2.0, delta=0.05)
        run(m, 6000)
        self.assertEqual(m.state.e_filt, m.state.e_key)
        self.assertEqual(m.state.f_q10, ff.F_MIN)

    def test_recovery_after_a_fault_returns_to_ok(self):
        m = ff.FlexFuelModel()
        run(m, 500, rx_every=10, frame_kw={"e_pct": 50})
        run(m, 200)                                             # timeout
        self.assertEqual(m.state.mode, ff.MODE_FAULT)
        m.tick(ff.frame(e_pct=50, counter=(m.state.frame_ctr + 1) & 0xFF))
        self.assertEqual(m.state.mode, ff.MODE_OK)

    def test_an_implausible_ethanol_value_is_a_fault(self):
        m = ff.FlexFuelModel()
        run(m, 500, rx_every=10, frame_kw={"e_pct": 50})
        m.tick(ff.frame(e_pct=101, counter=200))
        self.assertEqual(m.state.mode, ff.MODE_FAULT)

    def test_a_wrong_id_echo_is_a_fault(self):
        m = ff.FlexFuelModel()
        m.tick(ff.frame(e_pct=50, counter=1), can_id_echo=0x1A0)
        self.assertEqual(m.state.mode, ff.MODE_FAULT)

    def test_mode_0_forces_f_to_1024_and_never_polls(self):
        m = ff.FlexFuelModel(ff.Cal(mode=0))
        self.assertEqual(m.can_init_calls, 0)
        run(m, 500, rx_every=10, frame_kw={"e_pct": 85})
        self.assertEqual(m.state.mode, ff.MODE_OFF)
        self.assertEqual(m.state.f_q10, ff.F_MIN)
        self.assertEqual(m.poll_calls, 0)
        self.assertEqual(m.rk_scale(2000), 2000)

    def test_an_invalid_calibration_behaves_like_mode_0(self):
        m = ff.FlexFuelModel(ff.Cal(valid=False))
        run(m, 50)
        self.assertEqual(m.state.mode, ff.MODE_OFF)
        self.assertEqual(m.state.f_q10, ff.F_MIN)
        self.assertEqual(m.state.cal_ok, 0)

    def test_mode_2_follows_the_override_without_any_frame(self):
        m = ff.FlexFuelModel(ff.Cal(mode=2, e_override=50))
        run(m, 20000)
        self.assertEqual(m.state.mode, ff.MODE_OVERRIDE)
        self.assertEqual(m.state.e_filt, 50 * 16)
        self.assertEqual(m.poll_calls, 0)
        self.assertEqual(m.can_init_calls, 0)
        self.assertEqual(m.state.f_q10, m.f_of(50 * 16))

    def test_an_out_of_range_override_is_clamped(self):
        m = ff.FlexFuelModel(ff.Cal(mode=2, e_override=200))
        run(m, 20000)
        self.assertEqual(m.state.e_filt, ff.E_FILT_MAX)

    def test_the_block_re_initialises_when_the_header_is_wrong(self):
        m = ff.FlexFuelModel()
        run(m, 500, rx_every=10, frame_kw={"e_pct": 60})
        self.assertGreater(m.state.e_filt, 0)
        m.state.csum ^= 0xFFFF                       # corrupt it
        m.tick(None)
        self.assertEqual(m.state.e_filt, 0)
        self.assertEqual(m.state.f_q10, ff.F_MIN)
        self.assertEqual(m.state.ticks, 1)

    def test_a_pathological_calibration_cannot_break_the_arithmetic(self):
        m = ff.FlexFuelModel(ff.Cal(tick_ms=0, filter_tau_ms=0, slew_pct_s=0,
                                    stall_max=0, hold_s=65535))
        run(m, 300, rx_every=10, frame_kw={"e_pct": 85})
        self.assertLessEqual(m.state.e_filt, ff.E_FILT_MAX)
        self.assertTrue(ff.F_MIN <= m.state.f_q10 <= ff.F_MAX)


class TestHookArbitration(unittest.TestCase):
    """Only one ERCOSEK task set is live and the dump cannot say which
    (scheduler.md 11.7), so both 10 ms rasters are hooked."""

    def test_the_first_source_takes_ownership(self):
        m = ff.FlexFuelModel()
        m.tick(None, src=ff.SRC_A)
        self.assertEqual(m.state.src_owner, ff.SRC_A)
        self.assertEqual(m.state.src_seen, ff.SRC_A)
        self.assertEqual(m.state.ticks, 1)

    def test_a_foreign_source_does_not_tick(self):
        m = ff.FlexFuelModel()
        run(m, 10, src=ff.SRC_A)
        self.assertEqual(m.state.ticks, 10)
        m.tick(None, src=ff.SRC_B)
        self.assertEqual(m.state.ticks, 10, "the non-owner must not tick")
        self.assertEqual(m.state.src_seen, ff.SRC_A | ff.SRC_B)

    def test_if_both_fire_the_rate_stays_one_tick_per_period(self):
        m = ff.FlexFuelModel()
        for _ in range(100):
            m.tick(None, src=ff.SRC_A)
            m.tick(None, src=ff.SRC_B)
        self.assertEqual(m.state.ticks, 100)
        self.assertEqual(m.state.src_owner, ff.SRC_A)

    def test_ownership_moves_when_the_owner_goes_quiet(self):
        """os_init installs set A; 0x11DAF4 may switch to set B afterwards."""
        m = ff.FlexFuelModel()
        run(m, 5, src=ff.SRC_A)
        for _ in range(ff.OWNER_SWITCH):
            m.tick(None, src=ff.SRC_B)
        self.assertEqual(m.state.src_owner, ff.SRC_B)
        run(m, 10, src=ff.SRC_B)
        self.assertEqual(m.state.ticks, 5 + 1 + 10)


# ------------------------------------------------------------- 4. FFCAL001 --
class TestFfcal001(unittest.TestCase):
    def setUp(self):
        self.params = ffcal001.load_params(FF_FUEL / "ffcal001.json")
        self.blk = ffcal001.build(self.params)

    def test_the_block_validates_the_way_the_ecu_does(self):
        ffcal001.check(self.blk)
        self.assertEqual(self.blk[0:8], b"FFCAL001")
        self.assertEqual(len(self.blk), ffcal001.LENGTH)

    def test_a_corrupted_byte_fails_the_checksum(self):
        bad = bytearray(self.blk)
        bad[0x20] ^= 0x01
        with self.assertRaises(ffcal001.CalError):
            ffcal001.check(bytes(bad))

    def test_the_shipped_mode_is_1_and_a_mode_0_file_can_be_built(self):
        self.assertEqual(self.blk[0x18], 1)
        alt = ffcal001.build(dict(self.params, ff_mode=0))
        self.assertEqual(alt[0x18], 0)
        ffcal001.check(alt)

    def test_the_curve_must_start_at_1024(self):
        with self.assertRaises(ffcal001.CalError):
            ffcal001.build(dict(self.params, ff_F_curve=[1100] * 17))

    def test_a_non_monotonic_curve_is_refused(self):
        curve = ff.f_curve_from_formula()
        curve[5], curve[6] = curve[6], curve[5]
        with self.assertRaises(ffcal001.CalError):
            ffcal001.build(dict(self.params, ff_F_curve=curve))

    def test_the_time_constants_are_physical(self):
        """The whole point of C4's 10x correction: FFCAL001 carries ms / s /
        %-per-second and the patch converts with ff_tick_ms."""
        v = dict(zip(("timeout", "hold", "tau", "slew", "tick"),
                     struct.unpack_from(">5H", self.blk, 0x0E)))
        self.assertEqual(v, {"timeout": 1000, "hold": 60, "tau": 3000,
                             "slew": 2, "tick": 10})
        cal = ff.Cal()
        self.assertEqual(cal.slew(), 327)      # 0.02 % per 10 ms activation
        self.assertEqual(cal.tick() / cal.tau(), 10 / 3000)   # K ~ 1/300

    def test_the_curve_in_the_block_is_the_model_curve(self):
        curve = struct.unpack_from(">17H", self.blk, 0x20)
        self.assertEqual(list(curve), ff.f_curve_from_formula())

    def test_the_reserved_tables_are_neutral(self):
        self.assertEqual(set(self.blk[0x42:0x53]), {0})           # ff_fzw_curve
        self.assertEqual(set(self.blk[0x54:0x94]), {0})           # ff_dzw_map
        self.assertEqual(set(struct.unpack_from(">36H", self.blk, 0x94)), {1024})
        self.assertEqual(set(self.blk[0xDC:0xE4]), {0})           # ff_prail_add

    def test_the_layout_matches_ff_state_h(self):
        """The C header and the generator are two copies of one layout."""
        text = STATE_H.read_text()

        def macro(name: str) -> int:
            mo = re.search(rf"#define\s+{name}\s+(0x[0-9A-Fa-f]+)u?", text)
            self.assertIsNotNone(mo, f"{name} missing from ff_state.h")
            return int(mo.group(1), 16)

        self.assertEqual(macro("FF_CAL_BASE"), ffcal001.CAL_BASE)
        self.assertEqual(macro("FF_CAL_LENGTH"), ffcal001.LENGTH)
        self.assertEqual(macro("FF_CAL_O_CRC"), ffcal001.CRC_OFF)
        by_name = {n: off for off, n, *_ in ffcal001.SCALARS}
        for cname, pname in (("FF_CAL_O_CAN_ID", "ff_can_id"),
                             ("FF_CAL_O_TIMEOUT", "ff_timeout_ms"),
                             ("FF_CAL_O_HOLD_S", "ff_hold_s"),
                             ("FF_CAL_O_TAU_MS", "ff_filter_tau_ms"),
                             ("FF_CAL_O_SLEW", "ff_slew_pct_s"),
                             ("FF_CAL_O_TICK_MS", "ff_tick_ms"),
                             ("FF_CAL_O_MODE", "ff_mode"),
                             ("FF_CAL_O_E_OVR", "ff_e_override"),
                             ("FF_CAL_O_STALL_MAX", "ff_stall_max"),
                             ("FF_CAL_O_PERSIST", "ff_persist_enable"),
                             ("FF_CAL_O_P_HYST", "ff_persist_hyst_pct"),
                             ("FF_CAL_O_P_BLOCK", "ff_persist_block"),
                             ("FF_CAL_O_P_OFFSET", "ff_persist_offset"),
                             ("FF_CAL_O_P_RATE_S", "ff_persist_rate_s")):
            self.assertEqual(macro(cname), by_name[pname], cname)
        by_table = {n: off for off, n, *_ in ffcal001.TABLES}
        for cname, pname in (("FF_CAL_O_F_CURVE", "ff_F_curve"),
                             ("FF_CAL_O_FZW_CURVE", "ff_fzw_curve"),
                             ("FF_CAL_O_DZW_MAP", "ff_dzw_map"),
                             ("FF_CAL_O_FST_MAP", "ff_fst_map"),
                             ("FF_CAL_O_PRAIL_ADD", "ff_prail_add")):
            self.assertEqual(macro(cname), by_table[pname], cname)

    def test_every_d2_parameter_of_docs_05_section_4_is_reserved(self):
        names = {n for _o, n, *_ in ffcal001.SCALARS}
        names |= {n for _o, n, *_ in ffcal001.TABLES}
        for wanted in ("ff_persist_enable", "ff_persist_hyst_pct",
                       "ff_persist_block", "ff_persist_offset",
                       "ff_persist_rate_s",
                       "ff_fzw_curve", "ff_dzw_map", "ff_fst_map",
                       "ff_prail_add"):
            self.assertIn(wanted, names)

    def test_the_block_fits_the_free_calibration_area(self):
        self.assertGreaterEqual(ffcal001.CAL_BASE, 0x5E2510)
        self.assertLess(ffcal001.CAL_BASE + ffcal001.LENGTH, 0x5F0000)


class TestDescriptorRows(unittest.TestCase):
    """D3 owns re/calibration_draft.csv, so D1 emits rows for the integrator."""

    def setUp(self):
        self.params = ffcal001.load_params(FF_FUEL / "ffcal001.json")
        self.rows = ffcal001.rows(self.params)

    def test_the_header_is_exactly_the_draft_header(self):
        with DRAFT_CSV.open(newline="") as fh:
            header = next(csv.reader(fh))
        self.assertEqual(ffcal001.CSV_HEADER, header)

    def test_the_checked_in_rows_file_is_current(self):
        path = FF_FUEL / "ffcal001_rows.csv"
        self.assertTrue(path.is_file(), "run patches/ff_fuel/ffcal001.py")
        with path.open(newline="") as fh:
            on_disk = list(csv.DictReader(fh))
        self.assertEqual(on_disk, self.rows,
                         "ffcal001_rows.csv is stale: run ffcal001.py")

    def test_it_does_not_touch_the_files_brief_d3_owns(self):
        for owned in (DRAFT_CSV, REPO / "re" / "med9_draft.xdf"):
            self.assertNotIn(str(owned), (FF_FUEL / "ffcal001.py").read_text())

    def test_every_row_is_inside_the_block(self):
        for r in self.rows:
            addr = int(r["addr"], 16)
            self.assertGreaterEqual(addr, ffcal001.CAL_BASE)
            self.assertLess(addr, ffcal001.CAL_BASE + ffcal001.LENGTH)
            self.assertTrue(r["name_or_blank"].startswith("ff_"))
            self.assertEqual(r["confidence"], "static")
            self.assertTrue(r["evidence"])

    def test_the_rows_survive_the_xdf_generator(self):
        """draft_to_xdf.py is what the integrator runs after appending them."""
        import draft_to_xdf                                   # noqa: E402
        with tempfile.TemporaryDirectory() as tmp:
            merged = Path(tmp) / "draft.csv"
            with merged.open("w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=ffcal001.CSV_HEADER)
                w.writeheader()
                for r in self.rows:
                    w.writerow(r)
            out = Path(tmp) / "ffcal.xdf"
            rc = draft_to_xdf.main([str(merged), "-o", str(out), "--scalars"])
            self.assertEqual(rc, 0)
            self.assertEqual(draft_to_xdf.main(["--validate", str(out)]), 0)
            self.assertIn("ff_F_curve", out.read_text())


class TestGeneratorCli(unittest.TestCase):
    def test_print_and_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "ffcal001.bin"
            rc = ffcal001.main(["-o", str(out), "--no-rows"])
            self.assertEqual(rc, 0)
            self.assertEqual(out.read_bytes(),
                             ffcal001.build(ffcal001.load_params(
                                 FF_FUEL / "ffcal001.json")))

    def test_set_overrides_one_scalar(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "m0.bin"
            self.assertEqual(
                ffcal001.main(["--set", "ff_mode=0", "-o", str(out), "--no-rows"]), 0)
            self.assertEqual(out.read_bytes()[0x18], 0)

    def test_an_unknown_parameter_is_refused(self):
        buf = io.StringIO()
        old, sys.stderr = sys.stderr, buf
        try:
            self.assertEqual(ffcal001.main(["--set", "nonsense=1", "--print"]), 1)
        finally:
            sys.stderr = old
        self.assertIn("nonsense", buf.getvalue())


class TestLoggerSession(unittest.TestCase):
    """logging/sessions/ff_fuel.json must describe the block this patch has."""

    def setUp(self):
        self.session = json.loads(
            (REPO / "logging" / "sessions" / "ff_fuel.json").read_text())
        self.patch = json.loads((FF_FUEL / "patch.json").read_text())

    def test_it_points_at_this_patch(self):
        self.assertEqual(self.session["patch"], "patches/ff_fuel/patch.json")
        self.assertEqual(self.session["dump_sha256"],
                         self.patch["base_sha256"])

    def test_every_patch_variable_carries_a_patch_offset(self):
        ram = int(self.patch["build"]["ram"], 0)
        size = int(self.patch["build"]["ram_size"])
        n = 0
        for v in self.session["vars"]:
            if "patch_offset" not in v:
                continue
            n += 1
            self.assertEqual(int(v["addr"], 16), ram + v["patch_offset"],
                             v["name"])
            self.assertLess(v["patch_offset"], size)
        self.assertGreaterEqual(n, 8, "the state block is barely logged")


if __name__ == "__main__":
    unittest.main()
