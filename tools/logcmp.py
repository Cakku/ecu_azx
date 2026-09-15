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

Usage:
    logcmp.py baseline.csv candidate.csv -t tolerance.json
    logcmp.py baseline.csv candidate.csv -t tolerance.csv --json report.json
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

INTERP_LINEAR = "linear"
INTERP_HOLD = "hold"
DEFAULT_TOL = {"max_abs": 0.0, "mean_abs": None, "rel": None, "interp": INTERP_LINEAR}


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
            min_samples: int = 1) -> tuple[list[VarResult], dict]:
    common = sorted(set(base) & set(cand))
    only_base = sorted(set(base) - set(cand))
    only_cand = sorted(set(cand) - set(base))
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
        "pass": sum(r.verdict == "PASS" for r in results),
        "fail": sum(r.verdict == "FAIL" for r in results),
        "skip": sum(r.verdict == "SKIP" for r in results),
    }
    summary["ok"] = summary["fail"] == 0
    return results, summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("baseline")
    ap.add_argument("candidate")
    ap.add_argument("-t", "--tolerance", help="tolerance file (.json or .csv)")
    ap.add_argument("--json", metavar="OUT", help="write the JSON report here ('-' for stdout)")
    ap.add_argument("--strict", action="store_true",
                    help="also fail if a variable is missing from one of the logs")
    ap.add_argument("-q", "--quiet", action="store_true", help="print only failures and the summary")
    a = ap.parse_args(argv)

    base, cand = load_log(a.baseline), load_log(a.candidate)
    default, per = load_tolerances(a.tolerance)
    results, summary = compare(base, cand, default, per)
    if a.strict and (summary["only_in_baseline"] or summary["only_in_candidate"]):
        summary["ok"] = False

    report = {
        "baseline": {"path": str(a.baseline), "vars": len(base),
                     "span_s": list(base.span), "meta": base.meta},
        "candidate": {"path": str(a.candidate), "vars": len(cand),
                      "span_s": list(cand.span), "meta": cand.meta},
        "tolerance": str(a.tolerance) if a.tolerance else None,
        "default_tolerance": default,
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
        print(f"{summary['common']} common variable(s): {summary['pass']} pass, "
              f"{summary['fail']} fail, {summary['skip']} skipped; "
              f"{len(summary['only_in_baseline'])} baseline-only, "
              f"{len(summary['only_in_candidate'])} candidate-only")
        print("RESULT: OK" if summary["ok"] else "RESULT: FAILED")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
