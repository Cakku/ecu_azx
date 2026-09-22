"""tools/logcmp.py on the synthetic logs in logging/samples/ (issue #24)."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tests.common import REPO

import logcmp  # noqa: E402  (tests.common put tools/ on sys.path)

SAMPLES = REPO / "logging" / "samples"
BASE = SAMPLES / "baseline.csv"
OK = SAMPLES / "candidate_ok.csv"
BAD = SAMPLES / "candidate_bad.csv"
TOL = SAMPLES / "tolerance.json"
RUN1 = SAMPLES / "stock_run1.csv"        # two runs of the same scenario,
RUN2 = SAMPLES / "stock_run2.csv"        # two power-ups: run 2 starts 0.25 s
RASTER = "raster_setA_10ms_count"        # of ECU time further in


class TestLoad(unittest.TestCase):
    def test_long_format(self):
        log = logcmp.load_log(BASE)
        self.assertEqual(sorted(log), ["B_stend", "lamsoni_w", "nmot_w", "rl_w",
                                       "ti_1_w", "tmot_w", "wkr_w"])
        self.assertEqual(log.meta["ecu"],
                         "03H906032 / 1037382557 (synthetic, not a real recording)")
        self.assertEqual(len(log["nmot_w"]), 200)       # 10 s at 20 Hz
        self.assertEqual(len(log["tmot_w"]), 20)        # 10 s at 2 Hz
        self.assertAlmostEqual(log.span[0], 0.0)
        self.assertAlmostEqual(log.span[1], 9.95)

    def test_wide_format_gives_the_same_series(self):
        long_log = logcmp.load_log(BASE)
        with tempfile.TemporaryDirectory() as td:
            wide = Path(td) / "wide.csv"
            names = ["nmot_w", "rl_w"]
            lines = ["time_s," + ",".join(names)]
            for t, v in zip(long_log["nmot_w"].t, long_log["nmot_w"].v):
                rl = long_log["rl_w"].value_at(t, logcmp.INTERP_LINEAR)
                lines.append(f"{t:.3f},{v:.4f},{rl:.4f}")
            wide.write_text("\n".join(lines) + "\n")
            w = logcmp.load_log(wide)
        self.assertEqual(sorted(w), names)
        self.assertEqual(len(w["nmot_w"]), len(long_log["nmot_w"]))
        self.assertAlmostEqual(w["nmot_w"].v[10], long_log["nmot_w"].v[10], places=3)

    def test_interpolation_modes(self):
        s = logcmp.Series("x", [0.0, 1.0], [10.0, 20.0])
        self.assertAlmostEqual(s.value_at(0.25, logcmp.INTERP_LINEAR), 12.5)
        self.assertAlmostEqual(s.value_at(0.25, logcmp.INTERP_HOLD), 10.0)
        self.assertIsNone(s.value_at(-0.1, logcmp.INTERP_LINEAR))
        self.assertIsNone(s.value_at(1.1, logcmp.INTERP_LINEAR))


class TestCompare(unittest.TestCase):
    def setUp(self):
        self.default, self.per = logcmp.load_tolerances(TOL)

    def test_equal_logs_have_zero_deviation(self):
        log = logcmp.load_log(BASE)
        results, summary = logcmp.compare(log, logcmp.load_log(BASE), self.default, self.per)
        self.assertTrue(summary["ok"])
        for r in results:
            self.assertEqual(r.verdict, "PASS")
            self.assertEqual(r.max_abs, 0.0)
            self.assertEqual(r.mean_abs, 0.0)

    def test_repeat_run_passes(self):
        results, summary = logcmp.compare(logcmp.load_log(BASE), logcmp.load_log(OK),
                                          self.default, self.per)
        self.assertTrue(summary["ok"], [r.line() for r in results if r.verdict != "PASS"])
        self.assertEqual(summary["fail"], 0)
        self.assertEqual(summary["common"], 7)

    def test_deliberate_change_is_flagged_on_exactly_that_variable(self):
        results, summary = logcmp.compare(logcmp.load_log(BASE), logcmp.load_log(BAD),
                                          self.default, self.per)
        self.assertFalse(summary["ok"])
        failed = [r.var for r in results if r.verdict == "FAIL"]
        self.assertEqual(failed, ["ti_1_w"])
        bad = next(r for r in results if r.var == "ti_1_w")
        self.assertGreater(bad.mean, 0.1)          # the +8 % offset, signed
        self.assertTrue(any("mean|d|" in why for why in bad.reasons))

    def test_missing_variable_is_reported_and_strict_mode_fails(self):
        base = logcmp.load_log(BASE)
        cand = logcmp.load_log(OK)
        del cand["wkr_w"]
        results, summary = logcmp.compare(base, cand, self.default, self.per)
        self.assertEqual(summary["only_in_baseline"], ["wkr_w"])
        self.assertEqual(summary["common"], 6)
        self.assertTrue(summary["ok"])             # not a failure without --strict
        self.assertNotIn("wkr_w", [r.var for r in results])

    def test_non_overlapping_logs_are_skipped(self):
        base = logcmp.load_log(BASE)
        cand = logcmp.load_log(OK)
        for s in cand.values():
            s.t = [t + 1000.0 for t in s.t]
        results, summary = logcmp.compare(base, cand, self.default, self.per)
        self.assertEqual(summary["skip"], summary["common"])
        self.assertTrue(all("overlap" in r.reasons[0] for r in results))

    def test_rel_tolerance(self):
        default = {"max_abs": None, "rel": 0.001, "interp": logcmp.INTERP_LINEAR}
        results, summary = logcmp.compare(logcmp.load_log(BASE), logcmp.load_log(OK),
                                          default, {})
        self.assertFalse(summary["ok"])            # 0.1 % of nmot_w is ~2 rpm
        nmot = next(r for r in results if r.var == "nmot_w")
        self.assertAlmostEqual(nmot.limit_max_abs, 0.001 * max(
            abs(v) for v in logcmp.load_log(BASE)["nmot_w"].v), places=3)


class TestAlignment(unittest.TestCase):
    """The power-up offset: the missing line of every log comparison (F2)."""

    def setUp(self):
        self.default, self.per = logcmp.load_tolerances(TOL)
        self.base, self.cand = logcmp.load_log(RUN1), logcmp.load_log(RUN2)

    def test_the_two_runs_differ_only_by_the_power_up_offset(self):
        b, c = self.base[RASTER], self.cand[RASTER]
        self.assertEqual((b.v[0], c.v[0]), (14830.0, 14855.0))
        self.assertEqual((b.t[0], c.t[0]), (0.0, 0.0))

    def test_unaligned_comparison_fails_on_the_ramp(self):
        results, summary = logcmp.compare(self.base, self.cand,
                                          self.default, self.per)
        self.assertFalse(summary["ok"])
        failed = [r.var for r in results if r.verdict == "FAIL"]
        self.assertIn("nmot_w", failed)
        nmot = next(r for r in results if r.var == "nmot_w")
        self.assertGreater(nmot.max_abs, 300.0)   # 0.25 s of a 1500 rpm/s ramp

    def test_aligned_comparison_passes(self):
        cand, shift = logcmp.align_on(self.base, self.cand, RASTER, 100.0)
        self.assertAlmostEqual(shift, 0.25, places=9)
        results, summary = logcmp.compare(self.base, cand, self.default, self.per)
        self.assertTrue(summary["ok"],
                        [r.line() for r in results if r.verdict != "PASS"])
        nmot = next(r for r in results if r.var == "nmot_w")
        self.assertLess(nmot.max_abs, 20.0)       # only the sensor noise is left

    def test_alignment_moves_every_series_and_nothing_else(self):
        cand, shift = logcmp.align_on(self.base, self.cand, RASTER)
        self.assertEqual(sorted(cand), sorted(self.cand))
        self.assertEqual(cand.meta, self.cand.meta)
        for name, moved in cand.items():
            orig = self.cand[name]
            self.assertEqual(moved.v, orig.v)
            self.assertEqual(moved.t, [t + shift for t in orig.t])
        self.assertEqual(self.cand[RASTER].t[0], 0.0)   # the input is untouched

    def test_default_rate_is_the_10ms_raster(self):
        self.assertEqual(logcmp.DEFAULT_ALIGN_RATE, 100.0)
        _c, with_default = logcmp.align_on(self.base, self.cand, RASTER)
        _c, explicit = logcmp.align_on(self.base, self.cand, RASTER, 100.0)
        self.assertEqual(with_default, explicit)

    def test_missing_or_empty_variable_is_loud(self):
        with self.assertRaises(logcmp.AlignError):
            logcmp.align_on(self.base, self.cand, "no_such_counter")
        cand = logcmp.load_log(RUN2)
        del cand[RASTER]
        with self.assertRaises(logcmp.AlignError):
            logcmp.align_on(self.base, cand, RASTER)
        cand[RASTER] = logcmp.Series(RASTER)
        with self.assertRaises(logcmp.AlignError):
            logcmp.align_on(self.base, cand, RASTER)
        with self.assertRaises(logcmp.AlignError):
            logcmp.align_on(self.base, self.cand, RASTER, 0.0)

    def test_parse_align_on(self):
        self.assertEqual(logcmp.parse_align_on("x:50"), ("x", 50.0))
        self.assertEqual(logcmp.parse_align_on("x"), ("x", 100.0))
        with self.assertRaises(logcmp.AlignError):
            logcmp.parse_align_on(":100")
        with self.assertRaises(logcmp.AlignError):
            logcmp.parse_align_on("x:fast")

    def test_manual_shift_matches_the_measured_one(self):
        moved = logcmp.shift_log(self.cand, 0.25)
        auto, _shift = logcmp.align_on(self.base, self.cand, RASTER)
        for name in moved:
            self.assertEqual(moved[name].t, auto[name].t)


class TestUncovered(unittest.TestCase):
    """--uncovered {fail,report,ignore} (F2 task 2)."""

    def setUp(self):
        self.base, self.cand = logcmp.load_log(RUN1), logcmp.load_log(RUN2)
        self.cand, _shift = logcmp.align_on(self.base, self.cand, RASTER)
        self.default, self.per = logcmp.load_tolerances(TOL)
        self.assertNotIn(RASTER, self.per)      # the file does not name it

    def test_fail_mode_judges_it_against_the_default_limit(self):
        results, summary = logcmp.compare(self.base, self.cand,
                                          self.default, self.per)
        raster = next(r for r in results if r.var == RASTER)
        self.assertEqual(raster.verdict, "PASS")   # aligned, so it agrees
        self.assertEqual(raster.limit_max_abs, self.default["max_abs"])
        self.assertEqual(summary["uncovered"], [RASTER])
        self.assertEqual(summary["uncovered_mode"], "fail")
        self.assertEqual(summary["common"], 8)

    def test_fail_mode_is_how_a_free_running_counter_fails(self):
        """Without the alignment the counter is compared, and it fails."""
        cand = logcmp.load_log(RUN2)
        results, summary = logcmp.compare(self.base, cand, self.default, self.per)
        raster = next(r for r in results if r.var == RASTER)
        self.assertEqual(raster.verdict, "FAIL")
        self.assertFalse(summary["ok"])

    def test_report_mode_lists_them_and_leaves_them_out(self):
        results, summary = logcmp.compare(self.base, self.cand, self.default,
                                          self.per, uncovered="report")
        self.assertNotIn(RASTER, [r.var for r in results])
        self.assertEqual(summary["uncovered"], [RASTER])
        self.assertEqual(summary["common"], 7)
        self.assertTrue(summary["ok"])

    def test_ignore_mode_says_nothing_in_the_text_report(self):
        results, summary = logcmp.compare(self.base, self.cand, self.default,
                                          self.per, uncovered="ignore")
        self.assertNotIn(RASTER, [r.var for r in results])
        self.assertEqual(summary["uncovered_mode"], "ignore")

    def test_an_unknown_mode_is_refused(self):
        with self.assertRaises(ValueError):
            logcmp.compare(self.base, self.cand, self.default, self.per,
                           uncovered="maybe")


class TestDerive(unittest.TestCase):
    """`logcmp.py derive`: two stock runs -> measured limits (F2 task 3)."""

    def setUp(self):
        self.base = logcmp.load_log(RUN1)
        self.cand, self.shift = logcmp.align_on(logcmp.load_log(RUN1),
                                                logcmp.load_log(RUN2), RASTER)

    def derive(self, **kw):
        kw.setdefault("exclude", [RASTER])
        return logcmp.derive_tolerances(self.base, self.cand, **kw)

    def test_limits_are_the_measured_spread_times_the_factor(self):
        doc = self.derive(factor=1.5)
        results, _summary = logcmp.compare(self.base, self.cand,
                                           dict(logcmp.NO_LIMITS), {})
        measured = {r.var: r for r in results}
        for name, lim in doc["vars"].items():
            if lim["max_abs"] is None:
                continue
            self.assertAlmostEqual(lim["max_abs"],
                                   float(f"{measured[name].max_abs * 1.5:.4g}"),
                                   places=9, msg=name)
            self.assertAlmostEqual(lim["mean_abs"],
                                   float(f"{measured[name].mean_abs * 1.5:.4g}"),
                                   places=9, msg=name)

    def test_a_known_spread_comes_back(self):
        """A synthetic pair with a spread of exactly 2.0 on one variable."""
        a, b = logcmp.Log(), logcmp.Log()
        a["x"] = logcmp.Series("x", [0.0, 1.0, 2.0], [10.0, 10.0, 10.0])
        b["x"] = logcmp.Series("x", [0.0, 1.0, 2.0], [10.0, 12.0, 11.0])
        doc = logcmp.derive_tolerances(a, b, factor=2.0, exclude=[])
        self.assertEqual(doc["vars"]["x"]["max_abs"], 4.0)          # 2.0 x 2
        self.assertEqual(doc["vars"]["x"]["mean_abs"], 2.0)         # 1.0 x 2

    def test_the_factor_scales_every_limit(self):
        one = self.derive(factor=1.0)["vars"]
        three = self.derive(factor=3.0)["vars"]
        for name, lim in one.items():
            if lim["max_abs"] is None:
                continue
            want = lim["max_abs"] * 3.0        # limits are rounded to 4 digits
            self.assertAlmostEqual(three[name]["max_abs"], want,
                                   delta=max(1e-9, abs(want) * 1e-3), msg=name)

    def test_excluded_variables_become_explicit_not_compared_rows(self):
        doc = self.derive()
        self.assertIn(RASTER, doc["vars"])
        self.assertIsNone(doc["vars"][RASTER]["max_abs"])
        self.assertIsNone(doc["vars"][RASTER]["mean_abs"])
        self.assertIn("not compared", doc["vars"][RASTER]["_note"])
        self.assertEqual(doc["_derived"]["excluded"], [RASTER])

    def test_exclude_takes_globs_and_reports_the_ones_matching_nothing(self):
        doc = self.derive(exclude=["raster_*", "ff_ticks"])
        self.assertEqual(doc["_derived"]["excluded"], [RASTER])
        self.assertEqual(doc["_derived"]["exclude_patterns_matching_nothing"],
                         ["ff_ticks"])

    def test_the_derived_file_round_trips_through_load_tolerances(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "tolerance.json"
            out.write_text(json.dumps(self.derive(), indent=2))
            default, per = logcmp.load_tolerances(out)
        self.assertEqual(default, {"max_abs": 0.0, "mean_abs": 0.0,
                                   "rel": None, "interp": logcmp.INTERP_LINEAR})
        self.assertIsNone(per[RASTER]["max_abs"])
        self.assertEqual(per["nmot_w"]["interp"], logcmp.INTERP_LINEAR)
        results, summary = logcmp.compare(self.base, self.cand, default, per)
        self.assertTrue(summary["ok"],           # by construction: spread x 1.5
                        [r.line() for r in results if r.verdict != "PASS"])
        raster = next(r for r in results if r.var == RASTER)
        self.assertEqual(raster.verdict, "PASS")
        self.assertIsNone(raster.limit_max_abs)

    def test_a_variable_in_one_run_only_gets_no_limit(self):
        cand = logcmp.load_log(RUN2)
        del cand["wkr_w"]
        doc = logcmp.derive_tolerances(self.base, cand, exclude=[])
        self.assertNotIn("wkr_w", doc["vars"])
        self.assertEqual(doc["_derived"]["only_in_one_run"], ["wkr_w"])

    def test_the_provenance_block_says_where_the_numbers_came_from(self):
        doc = logcmp.derive_tolerances(
            self.base, self.cand, exclude=[RASTER],
            align={"var": RASTER, "rate": 100.0, "shift_s": self.shift})
        d = doc["_derived"]
        self.assertEqual(d["runs"], [str(RUN1), str(RUN2)])
        self.assertEqual(d["factor"], 1.5)
        self.assertEqual(d["align"]["shift_s"], 0.25)
        self.assertEqual(d["common_time_range_s"], [0.25, 9.95])
        self.assertEqual(d["compared"], 7)
        self.assertTrue(any("measured" in line or "DERIVED" in line
                            for line in doc["_comment"]))


class TestCli(unittest.TestCase):
    def run_cli(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = logcmp.main(argv)
        return rc, buf.getvalue()

    def test_exit_codes(self):
        rc, out = self.run_cli([str(BASE), str(OK), "-t", str(TOL)])
        self.assertEqual(rc, 0, out)
        self.assertIn("RESULT: OK", out)
        rc, out = self.run_cli([str(BASE), str(BAD), "-t", str(TOL)])
        self.assertEqual(rc, 1)
        self.assertIn("FAIL  ti_1_w", out)

    def test_json_report(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "r.json"
            rc, _ = self.run_cli([str(BASE), str(BAD), "-t", str(TOL), "--json", str(out)])
            self.assertEqual(rc, 1)
            report = json.loads(out.read_text())
        self.assertFalse(report["summary"]["ok"])
        self.assertEqual(report["baseline"]["vars"], 7)
        names = [v["var"] for v in report["variables"] if v["verdict"] == "FAIL"]
        self.assertEqual(names, ["ti_1_w"])

    def test_align_on_turns_a_failure_into_a_pass(self):
        rc, out = self.run_cli([str(RUN1), str(RUN2), "-t", str(TOL)])
        self.assertEqual(rc, 1)
        self.assertIn("FAIL  nmot_w", out)

        rc, out = self.run_cli([str(RUN1), str(RUN2), "-t", str(TOL),
                                "--align-on", f"{RASTER}:100"])
        self.assertEqual(rc, 0, out)
        self.assertIn("RESULT: OK", out)
        self.assertIn(f"ALIGN {RASTER}", out)
        self.assertIn("+250.0 ms", out)          # the shift, in the header

    def test_the_shift_is_in_the_json_summary(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "r.json"
            rc, _ = self.run_cli([str(RUN1), str(RUN2), "-t", str(TOL),
                                  "--align-on", RASTER, "--json", str(out)])
            report = json.loads(out.read_text())
        self.assertEqual(rc, 0)
        self.assertAlmostEqual(report["summary"]["shift_s"], 0.25, places=9)
        self.assertEqual(report["summary"]["align"]["var"], RASTER)
        self.assertEqual(report["summary"]["align"]["rate"], 100.0)
        self.assertEqual(report["alignment"], report["summary"]["align"])

    def test_a_missing_alignment_variable_is_loud(self):
        rc, out = self.run_cli([str(BASE), str(OK), "-t", str(TOL),
                                "--align-on", RASTER])
        self.assertEqual(rc, 2)                  # not 0, and not a comparison
        self.assertIn("has no variable", out)
        self.assertNotIn("RESULT:", out)

    def test_manual_align_shift(self):
        rc, out = self.run_cli([str(RUN1), str(RUN2), "-t", str(TOL),
                                "--align-shift", "0.25"])
        self.assertEqual(rc, 0, out)
        self.assertIn("manual", out)
        self.assertIn("+250.0 ms", out)

    def test_align_on_and_align_shift_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            self.run_cli([str(RUN1), str(RUN2), "--align-on", RASTER,
                          "--align-shift", "0.25"])

    def test_uncovered_report_lists_what_the_file_does_not_name(self):
        rc, out = self.run_cli([str(RUN1), str(RUN2), "-t", str(TOL),
                                "--align-on", RASTER, "--uncovered", "report"])
        self.assertEqual(rc, 0, out)
        self.assertIn(f"UNCOV {RASTER}", out)
        self.assertIn("1 uncovered", out)
        self.assertIn("7 common variable(s)", out)

        rc, out = self.run_cli([str(RUN1), str(RUN2), "-t", str(TOL),
                                "--align-on", RASTER, "--uncovered", "ignore"])
        self.assertEqual(rc, 0, out)
        self.assertNotIn("UNCOV", out)
        self.assertNotIn("uncovered", out)

    def test_derive_then_compare_against_the_derived_file(self):
        """The E0 recipe of docs/07 section 5, end to end."""
        with tempfile.TemporaryDirectory() as td:
            tol = Path(td) / "tolerance_measured.json"
            rc, out = self.run_cli(["derive", str(RUN1), str(RUN2),
                                    "--align-on", f"{RASTER}:100",
                                    "--exclude", "raster_*", "-o", str(tol)])
            self.assertEqual(rc, 0, out)
            self.assertIn("LIMIT nmot_w", out)
            self.assertIn(f"EXCL  {RASTER}", out)
            doc = json.loads(tol.read_text())
            self.assertLess(doc["vars"]["nmot_w"]["max_abs"], 60.0)  # tighter
            rc, out = self.run_cli([str(RUN1), str(RUN2), "-t", str(tol),
                                    "--align-on", f"{RASTER}:100"])
        self.assertEqual(rc, 0, out)
        self.assertIn("RESULT: OK", out)

    def test_derive_writes_json_to_stdout_by_default(self):
        rc, out = self.run_cli(["derive", str(RUN1), str(RUN2),
                                "--align-on", RASTER, "--exclude", RASTER])
        self.assertEqual(rc, 0)
        doc = json.loads(out)
        self.assertEqual(sorted(doc), ["_comment", "_derived", "default", "vars"])

    def test_derive_refuses_to_guess_when_it_cannot_align(self):
        rc, out = self.run_cli(["derive", str(BASE), str(OK),
                                "--align-on", RASTER])
        self.assertEqual(rc, 2)
        self.assertIn("has no variable", out)

    def test_the_compare_sub_command_name_is_optional(self):
        rc, out = self.run_cli(["compare", str(BASE), str(OK), "-t", str(TOL)])
        self.assertEqual(rc, 0, out)
        rc2, out2 = self.run_cli([str(BASE), str(OK), "-t", str(TOL)])
        self.assertEqual((rc, out), (rc2, out2))

    def test_strict_fails_on_missing_variable(self):
        with tempfile.TemporaryDirectory() as td:
            trimmed = Path(td) / "trimmed.csv"
            keep = [ln for ln in BASE.read_text().splitlines()
                    if not ln.startswith("#") and ",wkr_w," not in ln]
            trimmed.write_text("\n".join(keep) + "\n")
            rc, out = self.run_cli([str(BASE), str(trimmed), "-t", str(TOL), "--strict"])
        self.assertEqual(rc, 1)
        self.assertIn("baseline only", out)


class TestSamplesAreReproducible(unittest.TestCase):
    def test_generator_output_matches_the_committed_files(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "make_samples", REPO / "logging" / "make_samples.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as td:
            mod.OUT = Path(td)
            with contextlib.redirect_stdout(io.StringIO()):
                mod.main()
            for name in ("baseline.csv", "candidate_ok.csv", "candidate_bad.csv",
                         "stock_run1.csv", "stock_run2.csv"):
                self.assertEqual((Path(td) / name).read_text(), (SAMPLES / name).read_text(),
                                 f"{name} differs from logging/make_samples.py output")


if __name__ == "__main__":
    unittest.main()
