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


class TestCli(unittest.TestCase):
    def run_cli(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
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
            for name in ("baseline.csv", "candidate_ok.csv", "candidate_bad.csv"):
                self.assertEqual((Path(td) / name).read_text(), (SAMPLES / name).read_text(),
                                 f"{name} differs from logging/make_samples.py output")


if __name__ == "__main__":
    unittest.main()
