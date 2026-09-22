#!/usr/bin/env python3
"""Compare a baseline ECU log with a candidate log over their common variables.

Log format (long form, defined in logging/README.md):

    # ecu: 03H906032 / 1037382557
    time_s,var,value
    0.000,nmot_w,812.5
    0.000,rl_w,18.4
    0.041,nmot_w,815.0

The wide form (`time_s` followed by one column per variable) is accepted too
and converted on load, so VCDS/CSV exports can be fed in directly.

For every variable present in both logs the candidate is resampled onto the
baseline timestamps inside the overlapping time window (linear interpolation,
or zero-order hold for enumerations and flags) and the deviation
`candidate - baseline` is summarised as mean / mean-absolute / max-absolute /
RMS.  A tolerance file gives the limits; the exit status is 1 if any variable
exceeds them, so this can gate a build (docs/06_patch_pipeline.md section 7).

Two runs are two power-ups.  The logger's `t = 0` is its own first sample and
the tens of milliseconds between "the ECU powered on" and "the tester finished
the DDLI set-up" are not the same twice, so before anything is compared the
candidate's time axis has to be moved onto the baseline's.  Both logs carry a
free-running raster activation counter, which IS the ECU's own clock (100
activations per second, `re/findings/scheduler.md` section 11), so the offset
is measurable rather than guessable::

    shift = (raster_cand[0] - raster_base[0]) / rate   # seconds of ECU time
            - (t_cand[0] - t_base[0])                  # seconds of logger time

`--align-on VAR[:RATE]` does exactly that (brief F2; the implementation comes
from `logging/bench_rehearsal.py::_align_on_raster`, brief E4).

Usage:
    logcmp.py baseline.csv candidate.csv -t tolerance.json
    logcmp.py baseline.csv candidate.csv -t tolerance.csv --json report.json
    logcmp.py baseline.csv candidate.csv -t tolerance.json \\
              --align-on raster_setA_10ms_count:100 --uncovered report
    logcmp.py derive stock_run1.csv stock_run2.csv -o tolerance.json \\
              --align-on raster_setA_10ms_count:100 --exclude 'raster_*' ff_ticks

Sub-commands:
    compare (the default; the sub-command name may be left out)
    derive   two runs of the SAME procedure on the SAME software -> a tolerance
             file whose limits are measured repeatability instead of guesses
             (C1's rule for Flash 1, issue #27; docs/05 "E0 equivalence").

Tolerance-file keys (`logging/README.md` section 2 has the format):
    default          limits for every variable the file does not name
    vars             per-variable limits: max_abs / mean_abs / rel / interp
    max_abs = null   "expected to differ, do not judge" -- the file's way of
                     saying a variable was considered, not forgotten
    _unit, _note     free text; ignored by the loader
    _derived         written by `derive`: the two runs, the factor, the
                     alignment, the common time range and what was excluded.
                     Ignored by the loader; it is provenance for the reader.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import fnmatch
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

INTERP_LINEAR = "linear"
INTERP_HOLD = "hold"
DEFAULT_TOL = {"max_abs": 0.0, "mean_abs": None, "rel": None, "interp": INTERP_LINEAR}

#: activations per second of the live 10 ms raster counter (scheduler.md §11)
DEFAULT_ALIGN_RATE = 100.0

#: what happens to variables the tolerance file does not name
UNCOVERED_MODES = ("fail", "report", "ignore")


class AlignError(ValueError):
    """The logs cannot be aligned: the alignment variable is missing or empty."""


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------
@dataclass
class Series:
    name: str
    t: list[float] = field(default_factory=list)
    v: list[float] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.t)

    def value_at(self, t: float, interp: str) -> float | None:
        """Sample the series at time t; None outside its own time range."""
        ts, vs = self.t, self.v
        if not ts or t < ts[0] or t > ts[-1]:
            return None
        lo, hi = 0, len(ts) - 1
        while lo < hi:                       # last index with ts[i] <= t
            mid = (lo + hi + 1) // 2
            if ts[mid] <= t:
                lo = mid
            else:
                hi = mid - 1
        if interp == INTERP_HOLD or lo == len(ts) - 1 or ts[lo] == t:
            return vs[lo]
        t0, t1 = ts[lo], ts[lo + 1]
        if t1 == t0:
            return vs[lo]
        f = (t - t0) / (t1 - t0)
        return vs[lo] + f * (vs[lo + 1] - vs[lo])


class Log(dict):
    """{variable name: Series}, plus the `#`-comment preamble in .meta."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.meta: dict[str, str] = {}
        self.path = ""

    @property
    def span(self) -> tuple[float, float]:
        t0 = min((s.t[0] for s in self.values() if s.t), default=0.0)
        t1 = max((s.t[-1] for s in self.values() if s.t), default=0.0)
        return t0, t1


def load_log(path: str | Path) -> Log:
    log = Log()
    log.path = str(path)
    rows: list[list[str]] = []
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            body = line.lstrip("#").strip()
            if ":" in body:
                k, v = body.split(":", 1)
                log.meta[k.strip()] = v.strip()
            continue
        rows.append(next(csv.reader([raw])))
    if not rows:
        raise ValueError(f"{path}: no data rows")
    header = [c.strip() for c in rows[0]]
    if header[0].lower() not in ("time_s", "time", "t"):
        raise ValueError(f"{path}: first column must be time_s, got {header[0]!r}")
    long_form = len(header) >= 3 and header[1].lower() in ("var", "variable", "name")
    for r in rows[1:]:
        if not r or not r[0].strip():
            continue
        t = float(r[0])
        if long_form:
            name = r[1].strip()
            txt = r[2].strip()
            if txt == "":
                continue
            log.setdefault(name, Series(name))
            log[name].t.append(t)
            log[name].v.append(float(txt))
        else:
            for name, txt in zip(header[1:], r[1:]):
                txt = txt.strip()
                if txt == "":
                    continue
                log.setdefault(name, Series(name))
                log[name].t.append(t)
                log[name].v.append(float(txt))
    for s in log.values():                    # timestamps must be sorted
        if any(b < a for a, b in zip(s.t, s.t[1:])):
            order = sorted(range(len(s.t)), key=lambda i: s.t[i])
            s.t = [s.t[i] for i in order]
            s.v = [s.v[i] for i in order]
    return log


def load_tolerances(path: str | Path | None) -> tuple[dict, dict[str, dict]]:
    """Return (default limits, per-variable limits).  JSON or CSV."""
    default = dict(DEFAULT_TOL)
    per: dict[str, dict] = {}
    if path is None:
        return default, per
    p = Path(path)
    if p.suffix.lower() == ".json":
        doc = json.loads(p.read_text())
        default.update(doc.get("default", {}))
        for name, lim in doc.get("vars", {}).items():
            per[name] = {**default, **lim}
        return default, per
    with p.open() as fh:
        for row in csv.DictReader(fh):
            name = (row.get("var") or "").strip()
            if not name:
                continue
            lim = {}
            for key in ("max_abs", "mean_abs", "rel"):
                val = (row.get(key) or "").strip()
                if val != "":
                    lim[key] = float(val)
            it = (row.get("interp") or "").strip()
            if it:
                lim["interp"] = it
            if name == "*":
                default.update(lim)
            else:
                per[name] = lim
    for name in per:
        per[name] = {**default, **per[name]}
    return default, per


# ---------------------------------------------------------------------------
# alignment -- the missing line of every log comparison (brief E4, brief F2)
# ---------------------------------------------------------------------------
def shift_log(log: Log, shift: float) -> Log:
    """A copy of `log` with `shift` seconds added to every timestamp."""
    out = type(log)()
    out.meta, out.path = log.meta, log.path
    for name, s in log.items():
        moved = type(s)(name)
        moved.t = [t + shift for t in s.t]
        moved.v = list(s.v)
        out[name] = moved
    return out


def align_on(base: Log, cand: Log, var: str,
             rate: float = DEFAULT_ALIGN_RATE) -> tuple[Log, float]:
    """Shift the candidate's time axis so the two ECUs' own clocks agree.

    `var` is a free-running counter that both logs carry and that advances at
    `rate` counts per second of ECU time -- the live raster activation counter
    (`raster_setA_10ms_count`, 100/s).  Its value at each log's first sample
    says how long that ECU had been powered when the logger started, so the
    difference of the two is the power-up offset, in ECU seconds.

    Returns (shifted candidate, shift in seconds).  Raises `AlignError` if
    either log lacks the variable or has no samples of it: silently comparing
    two unaligned runs is exactly the failure this option exists to stop.
    """
    if rate <= 0:
        raise AlignError(f"alignment rate must be positive, got {rate!r}")
    for log, which in ((base, "baseline"), (cand, "candidate")):
        s = log.get(var)
        where = log.path or which
        if s is None:
            raise AlignError(f"{which} log {where} has no variable {var!r} "
                             "-- it cannot be used for alignment")
        if not s.t:
            raise AlignError(f"{which} log {where} has no samples of {var!r}")
    b, c = base[var], cand[var]
    shift = (c.v[0] - b.v[0]) / rate - (c.t[0] - b.t[0])
    return shift_log(cand, shift), shift


def parse_align_on(spec: str) -> tuple[str, float]:
    """`VAR` or `VAR:RATE` -> (variable, counts per second)."""
    var, _sep, rate = spec.partition(":")
    var = var.strip()
    if not var:
        raise AlignError(f"--align-on: no variable in {spec!r}")
    if not rate.strip():
        return var, DEFAULT_ALIGN_RATE
    try:
        return var, float(rate)
    except ValueError:
        raise AlignError(f"--align-on: {rate!r} is not a rate in counts/second")


# ---------------------------------------------------------------------------
# comparison
# ---------------------------------------------------------------------------
@dataclass
class VarResult:
    var: str
    n: int
    verdict: str                  # PASS / FAIL / SKIP
    reasons: list[str] = field(default_factory=list)
    mean: float = 0.0
    mean_abs: float = 0.0
    max_abs: float = 0.0
    max_abs_time: float = 0.0
    rms: float = 0.0
    base_mean: float = 0.0
    cand_mean: float = 0.0
    limit_max_abs: float | None = None
    limit_mean_abs: float | None = None
    interp: str = INTERP_LINEAR

    def line(self) -> str:
        if self.verdict == "SKIP":
            return f"{self.verdict:<5} {self.var:<24} {self.reasons[0] if self.reasons else ''}"
        lim = "" if self.limit_max_abs is None else f" (limit {self.limit_max_abs:g})"
        return (f"{self.verdict:<5} {self.var:<24} n={self.n:<6} "
                f"mean={self.mean:+.4g} mean|d|={self.mean_abs:.4g} "
                f"max|d|={self.max_abs:.4g}@{self.max_abs_time:.3f}s{lim}")


def compare(base: Log, cand: Log, default: dict, per: dict[str, dict],
            min_samples: int = 1,
            uncovered: str = "fail") -> tuple[list[VarResult], dict]:
    """Compare two logs variable by variable.

    `uncovered` says what happens to the variables both logs carry but the
    tolerance file does not name:

    * ``fail``   -- compare them against the file's `default` limits, which is
      what a tolerance file without those rows means (the historical
      behaviour, and the default here);
    * ``report`` -- leave them out of the comparison and list them in
      ``summary["uncovered"]``.  A session file reads plain RAM, so a stock
      log carries the patch's own `ff_*` variables reading 0, and the
      free-running raster counters can never be compared between two
      power-ups: judging those against a default limit says nothing;
    * ``ignore`` -- leave them out and say nothing.
    """
    if uncovered not in UNCOVERED_MODES:
        raise ValueError(f"uncovered must be one of {UNCOVERED_MODES}, "
                         f"got {uncovered!r}")
    common = sorted(set(base) & set(cand))
    only_base = sorted(set(base) - set(cand))
    only_cand = sorted(set(cand) - set(base))
    not_named = [n for n in common if n not in per]
    if uncovered != "fail":
        common = [n for n in common if n in per]
    results: list[VarResult] = []

    for name in common:
        lim = per.get(name, default)
        interp = lim.get("interp", INTERP_LINEAR) or INTERP_LINEAR
        bs, cs_ = base[name], cand[name]
        if not bs.t or not cs_.t:
            results.append(VarResult(name, 0, "SKIP", ["one log has no samples"], interp=interp))
            continue
        t_lo = max(bs.t[0], cs_.t[0])
        t_hi = min(bs.t[-1], cs_.t[-1])
        if t_hi < t_lo:
            results.append(VarResult(name, 0, "SKIP", ["logs do not overlap in time"], interp=interp))
            continue
        devs: list[tuple[float, float]] = []
        bvals: list[float] = []
        cvals: list[float] = []
        for t, bv in zip(bs.t, bs.v):
            if t < t_lo or t > t_hi:
                continue
            cv = cs_.value_at(t, interp)
            if cv is None:
                continue
            devs.append((t, cv - bv))
            bvals.append(bv)
            cvals.append(cv)
        if len(devs) < min_samples:
            results.append(VarResult(name, len(devs), "SKIP",
                                     [f"only {len(devs)} comparable sample(s)"], interp=interp))
            continue
        n = len(devs)
        ds = [d for _t, d in devs]
        mean = sum(ds) / n
        mean_abs = sum(abs(d) for d in ds) / n
        rms = math.sqrt(sum(d * d for d in ds) / n)
        t_max, d_max = max(devs, key=lambda x: abs(x[1]))
        limit_max = lim.get("max_abs")
        if lim.get("rel") is not None:
            scale = max((abs(v) for v in bvals), default=0.0) or 1.0
            rel_limit = lim["rel"] * scale
            limit_max = rel_limit if limit_max is None else max(limit_max, rel_limit)
        limit_mean = lim.get("mean_abs")
        reasons: list[str] = []
        if limit_max is not None and abs(d_max) > limit_max:
            reasons.append(f"max|d| {abs(d_max):.6g} > {limit_max:g} at t={t_max:.3f}s")
        if limit_mean is not None and mean_abs > limit_mean:
            reasons.append(f"mean|d| {mean_abs:.6g} > {limit_mean:g}")
        results.append(VarResult(
            name, n, "FAIL" if reasons else "PASS", reasons,
            mean=mean, mean_abs=mean_abs, max_abs=abs(d_max), max_abs_time=t_max, rms=rms,
            base_mean=sum(bvals) / n, cand_mean=sum(cvals) / n,
            limit_max_abs=limit_max, limit_mean_abs=limit_mean, interp=interp))

    summary = {
        "common": len(common),
        "only_in_baseline": only_base,
        "only_in_candidate": only_cand,
        "uncovered_mode": uncovered,
        "uncovered": not_named,
        "pass": sum(r.verdict == "PASS" for r in results),
        "fail": sum(r.verdict == "FAIL" for r in results),
        "skip": sum(r.verdict == "SKIP" for r in results),
    }
    summary["ok"] = summary["fail"] == 0
    return results, summary


# ---------------------------------------------------------------------------
# deriving a tolerance file from two runs of the same software
# ---------------------------------------------------------------------------
NO_LIMITS = {"max_abs": None, "mean_abs": None, "rel": None,
             "interp": INTERP_LINEAR}

DERIVE_COMMENT = [
    "Tolerances DERIVED from two runs of the same procedure on the SAME",
    "software by `tools/logcmp.py derive` (brief F2). Every limit below is",
    "that pair's own run-to-run spread times the factor in `_derived`: what",
    "the ECU repeated, not what somebody expected it to repeat. C1's rule for",
    "Flash 1 (issue #27) and docs/05_flexfuel_design.md 'E0 equivalence' both",
    "ask for exactly this step before a patched run is judged.",
    "",
    "A limit is only as good as the pair it came from. Two runs of a",
    "DIFFERENT scenario, or a warm run against a cold one, produce a file",
    "that passes everything. Re-derive whenever the scenario changes, and",
    "keep the two source logs next to this file.",
    "",
    "`max_abs: null` means NOT COMPARED: free-running counters (the raster",
    "counters, ff_ticks) and anything else whose value depends on how long",
    "the ECU had been powered. They are listed rather than dropped so that a",
    "reader can see they were considered.",
    "",
    "The `default` block is deliberately 0.0 / 0.0: a variable this pair",
    "never saw is not judged by a guess -- it fails until the file is",
    "re-derived. Pass `--uncovered report` to list such variables instead.",
]


def _matches_any(name: str, patterns) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


def _round(x: float) -> float:
    """Four significant digits: a tolerance file is read by people."""
    return float(f"{x:.4g}")


def derive_tolerances(base: Log, cand: Log, factor: float = 1.5,
                      exclude=(), align: dict | None = None,
                      min_samples: int = 2) -> dict:
    """Two baseline logs -> a tolerance document (the JSON structure).

    Limits are the measured spread over the common time range times `factor`:
    `max_abs` = max|d| x factor, `mean_abs` = mean|d| x factor.  Variables
    matching `exclude` (exact names or fnmatch patterns) are written as
    explicit "not compared" rows -- `max_abs: null` -- so the file says they
    were considered.  `align` is recorded, not applied: align the candidate
    with `align_on` first.
    """
    exclude = list(exclude)
    rows, summary = compare(base, cand, dict(NO_LIMITS), {},
                            min_samples=min_samples)
    common = sorted(set(base) & set(cand))
    excluded = [n for n in common if _matches_any(n, exclude)]
    unmatched = [p for p in exclude
                 if not any(fnmatch.fnmatchcase(n, p) for n in common)]

    out: dict[str, dict] = {}
    skipped: list[str] = []
    t_lo, t_hi = None, None
    for r in rows:
        if r.var in excluded:
            continue
        if r.verdict == "SKIP":
            skipped.append(f"{r.var}: {r.reasons[0] if r.reasons else 'skipped'}")
            continue
        out[r.var] = {
            "max_abs": _round(r.max_abs * factor),
            "mean_abs": _round(r.mean_abs * factor),
            "_note": f"measured: max|d| {r.max_abs:.4g}, mean|d| "
                     f"{r.mean_abs:.4g} over n={r.n} samples, x{factor:g}",
        }
    for name in excluded:
        out[name] = {
            "max_abs": None, "mean_abs": None,
            "_note": "not compared: excluded with --exclude. A free-running "
                     "counter or another value that cannot repeat between two "
                     "power-ups; on the raster counters this is also the "
                     "signal the comparison is aligned on",
        }
    b_lo, b_hi = base.span
    c_lo, c_hi = cand.span
    t_lo, t_hi = max(b_lo, c_lo), min(b_hi, c_hi)
    return {
        "_comment": list(DERIVE_COMMENT),
        "_derived": {
            "tool": "tools/logcmp.py derive",
            "date": datetime.date.today().isoformat(),
            "runs": [base.path, cand.path],
            "factor": factor,
            "align": align,
            "common_time_range_s": [_round(t_lo), _round(t_hi)],
            "compared": len(out) - len(excluded),
            "excluded": excluded,
            "exclude_patterns": exclude,
            "exclude_patterns_matching_nothing": unmatched,
            "only_in_one_run": sorted(summary["only_in_baseline"]
                                      + summary["only_in_candidate"]),
            "too_few_samples": skipped,
        },
        "default": {"max_abs": 0.0, "mean_abs": 0.0, "interp": INTERP_LINEAR},
        "vars": out,
    }


# ---------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------
def _add_align_options(ap: argparse.ArgumentParser) -> None:
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--align-on", metavar="VAR[:RATE]",
                   help="shift the candidate so the two ECU clocks agree, "
                        "using this free-running counter (RATE counts per "
                        f"second, default {DEFAULT_ALIGN_RATE:g}); e.g. "
                        "raster_setA_10ms_count:100")
    g.add_argument("--align-shift", type=float, metavar="SECONDS",
                   help="add this many seconds to every candidate timestamp "
                        "instead (a manual alignment)")


def _align(base: Log, cand: Log, a) -> tuple[Log, dict | None]:
    """Apply --align-on / --align-shift; returns (candidate, alignment dict)."""
    if a.align_on:
        var, rate = parse_align_on(a.align_on)
        cand, shift = align_on(base, cand, var, rate)
        return cand, {"var": var, "rate": rate, "shift_s": shift}
    if a.align_shift is not None:
        return shift_log(cand, a.align_shift), {
            "var": None, "rate": None, "shift_s": a.align_shift}
    return cand, None


def _align_line(align: dict | None) -> str:
    if not align:
        return ""
    what = align["var"] or "(manual --align-shift)"
    rate = f" at {align['rate']:g}/s" if align["rate"] else ""
    return (f"ALIGN {what:<24} candidate shifted by "
            f"{align['shift_s'] * 1000:+.1f} ms{rate}")


def main_compare(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="logcmp.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("baseline")
    ap.add_argument("candidate")
    ap.add_argument("-t", "--tolerance", help="tolerance file (.json or .csv)")
    ap.add_argument("--json", metavar="OUT", help="write the JSON report here ('-' for stdout)")
    ap.add_argument("--strict", action="store_true",
                    help="also fail if a variable is missing from one of the logs")
    ap.add_argument("--uncovered", choices=UNCOVERED_MODES, default="fail",
                    help="variables the tolerance file does not name: compare "
                         "them against its default limits (fail, the default), "
                         "leave them out and list them (report), or leave them "
                         "out silently (ignore)")
    _add_align_options(ap)
    ap.add_argument("-q", "--quiet", action="store_true", help="print only failures and the summary")
    a = ap.parse_args(argv)

    base, cand = load_log(a.baseline), load_log(a.candidate)
    try:
        cand, align = _align(base, cand, a)
    except AlignError as exc:
        print(f"logcmp: {exc}", file=sys.stderr)
        return 2
    default, per = load_tolerances(a.tolerance)
    results, summary = compare(base, cand, default, per, uncovered=a.uncovered)
    if a.strict and (summary["only_in_baseline"] or summary["only_in_candidate"]):
        summary["ok"] = False
    summary["align"] = align
    summary["shift_s"] = align["shift_s"] if align else 0.0

    report = {
        "baseline": {"path": str(a.baseline), "vars": len(base),
                     "span_s": list(base.span), "meta": base.meta},
        "candidate": {"path": str(a.candidate), "vars": len(cand),
                      "span_s": list(cand.span), "meta": cand.meta},
        "tolerance": str(a.tolerance) if a.tolerance else None,
        "default_tolerance": default,
        "alignment": align,
        "variables": [asdict(r) for r in results],
        "summary": summary,
    }
    if a.json:
        text = json.dumps(report, indent=2)
        if a.json == "-":
            print(text)
        else:
            Path(a.json).write_text(text + "\n")

    if a.json != "-":
        if align:
            print(_align_line(align))
        for r in results:
            if a.quiet and r.verdict == "PASS":
                continue
            print(r.line())
            for why in r.reasons:
                print(f"      {why}")
        for name in summary["only_in_baseline"]:
            print(f"ONLY  {name:<24} baseline only")
        for name in summary["only_in_candidate"]:
            print(f"ONLY  {name:<24} candidate only")
        if a.uncovered == "report":
            for name in summary["uncovered"]:
                print(f"UNCOV {name:<24} not named in the tolerance file, not compared")
        print(f"{summary['common']} common variable(s): {summary['pass']} pass, "
              f"{summary['fail']} fail, {summary['skip']} skipped; "
              f"{len(summary['only_in_baseline'])} baseline-only, "
              f"{len(summary['only_in_candidate'])} candidate-only"
              + (f"; {len(summary['uncovered'])} uncovered"
                 if a.uncovered == "report" else ""))
        print("RESULT: OK" if summary["ok"] else "RESULT: FAILED")
    return 0 if summary["ok"] else 1


def main_derive(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="logcmp.py derive",
        description="Two runs of the same procedure on the same software -> a "
                    "tolerance file whose limits are measured repeatability.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run1")
    ap.add_argument("run2")
    ap.add_argument("-o", "--out", default="-",
                    help="write the tolerance file here ('-' for stdout, the default)")
    ap.add_argument("--factor", type=float, default=1.5,
                    help="limit = measured spread x this (default 1.5)")
    ap.add_argument("--exclude", nargs="*", default=[], metavar="NAME_OR_GLOB",
                    help="variables that cannot be compared between two "
                         "power-ups (the raster counters, ff_ticks); written "
                         "into the file as explicit 'not compared' rows")
    ap.add_argument("--min-samples", type=int, default=2,
                    help="a variable with fewer comparable samples is left out (default 2)")
    _add_align_options(ap)
    ap.add_argument("-q", "--quiet", action="store_true")
    a = ap.parse_args(argv)

    base, cand = load_log(a.run1), load_log(a.run2)
    try:
        cand, align = _align(base, cand, a)
    except AlignError as exc:
        print(f"logcmp: {exc}", file=sys.stderr)
        return 2
    doc = derive_tolerances(base, cand, factor=a.factor, exclude=a.exclude,
                            align=align)
    text = json.dumps(doc, indent=2)
    if a.out == "-":
        print(text)
        return 0
    Path(a.out).write_text(text + "\n")
    if a.quiet:
        return 0
    d = doc["_derived"]
    if align:
        print(_align_line(align))
    for name, lim in doc["vars"].items():
        if lim["max_abs"] is None:
            print(f"EXCL  {name:<24} not compared")
        else:
            print(f"LIMIT {name:<24} max_abs={lim['max_abs']:<10g} "
                  f"mean_abs={lim['mean_abs']:g}")
    for line in d["too_few_samples"]:
        print(f"SKIP  {line}")
    for name in d["only_in_one_run"]:
        print(f"ONLY  {name:<24} in one run only, no limit written")
    print(f"{d['compared']} variable(s) measured over "
          f"{d['common_time_range_s'][0]:g}-{d['common_time_range_s'][1]:g} s "
          f"x{a.factor:g}, {len(d['excluded'])} excluded -> {a.out}")
    return 0


def main(argv=None) -> int:
    """`logcmp.py [compare] ...` or `logcmp.py derive ...`.

    The sub-command is optional so that every command already written down in
    the procedures keeps working.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "derive":
        return main_derive(argv[1:])
    if argv and argv[0] == "compare":
        argv = argv[1:]
    return main_compare(argv)


if __name__ == "__main__":
    raise SystemExit(main())
