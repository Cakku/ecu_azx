#!/usr/bin/env python3
"""Regenerate the synthetic logs in logging/samples/ used to test tools/logcmp.py.

They are deterministic (a fixed LCG, no `random` seed dependence) and describe
a 10 s bench idle with a short load step, in the long CSV format defined in
logging/README.md.  Three files are produced:

    baseline.csv        the "stock" run
    candidate_ok.csv    a repeat run: sensor noise only, inside tolerance
    candidate_bad.csv   the same run with ti_1_w deliberately +8 %, which
                        tolerance.json must flag (issue #24 exit criterion)

Usage:
    python3 logging/make_samples.py
"""
from __future__ import annotations

import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "samples"

DURATION = 10.0
FAST_HZ = 20.0          # nmot_w, rl_w, ti_1_w, lamsoni_w, wkr_w
SLOW_HZ = 2.0           # tmot_w, B_stend


class Lcg:
    """Deterministic uniform noise in [-1, 1]; no dependence on the stdlib RNG."""

    def __init__(self, seed: int):
        self.s = seed & 0xFFFFFFFF

    def __call__(self) -> float:
        self.s = (1103515245 * self.s + 12345) & 0x7FFFFFFF
        return (self.s / 0x7FFFFFFF) * 2.0 - 1.0


def profile(t: float) -> dict[str, float]:
    """Idle, a trapezoidal load step (ramp up 4-5 s, hold, ramp down 7-8 s), idle.

    Continuous by construction: logcmp resamples the candidate onto the
    baseline timestamps, so a jump discontinuity would show up as a huge
    deviation that says nothing about the software.
    """
    if t < 4.0:
        ramp = 0.0
    elif t < 5.0:
        ramp = t - 4.0
    elif t < 7.0:
        ramp = 1.0
    elif t < 8.0:
        ramp = 1.0 - (t - 7.0)
    else:
        ramp = 0.0
    nmot = 780.0 + 1500.0 * ramp + 20.0 * math.sin(t * 6.0)
    rl = 17.0 + 45.0 * ramp
    return {
        "nmot_w": nmot,
        "rl_w": rl,
        "ti_1_w": 0.9 + 0.035 * rl,
        "lamsoni_w": 1.0 - 0.12 * ramp,
        "wkr_w": 0.0 + 3.0 * ramp,
        "tmot_w": 82.0 + 0.35 * t,
        "B_stend": 1.0,
    }


FAST = ("nmot_w", "rl_w", "ti_1_w", "lamsoni_w", "wkr_w")
SLOW = ("tmot_w", "B_stend")
NOISE = {"nmot_w": 6.0, "rl_w": 0.4, "ti_1_w": 0.008, "lamsoni_w": 0.004,
         "wkr_w": 0.15, "tmot_w": 0.2, "B_stend": 0.0}
UNITS = {"nmot_w": "rpm", "rl_w": "%", "ti_1_w": "ms", "lamsoni_w": "-",
         "wkr_w": "degKW", "tmot_w": "degC", "B_stend": "bit"}


def write(path: Path, seed: int, title: str, gain: dict[str, float] | None = None,
          t_offset: float = 0.0) -> None:
    rng = Lcg(seed)
    gain = gain or {}
    rows: list[tuple[float, str, float]] = []
    n_fast = int(DURATION * FAST_HZ)
    for i in range(n_fast):
        t = i / FAST_HZ
        p = profile(t)
        for name in FAST:
            v = p[name] * gain.get(name, 1.0) + NOISE[name] * rng()
            rows.append((t + t_offset, name, v))
    n_slow = int(DURATION * SLOW_HZ)
    for i in range(n_slow):
        t = i / SLOW_HZ
        p = profile(t)
        for name in SLOW:
            v = p[name] * gain.get(name, 1.0) + NOISE[name] * rng()
            rows.append((t + t_offset, name, v))
    rows.sort(key=lambda r: (r[0], r[1]))
    with path.open("w") as fh:
        fh.write(f"# {title}\n")
        fh.write("# ecu: 03H906032 / 1037382557 (synthetic, not a real recording)\n")
        fh.write("# dump_sha256: b15590d3f1874ace3125c5d047c09a686db9b8bb498187663539ebab205609b3\n")
        fh.write("# transport: synthetic\n")
        fh.write("time_s,var,value,unit\n")
        for t, name, v in rows:
            fh.write(f"{t:.3f},{name},{v:.4f},{UNITS[name]}\n")
    print(f"wrote {path} ({len(rows)} rows)")


def main() -> None:
    OUT.mkdir(exist_ok=True)
    write(OUT / "baseline.csv", 1, "baseline: stock software, bench idle + load step")
    write(OUT / "candidate_ok.csv", 2,
          "candidate: same software, repeat run (noise only)", t_offset=0.017)
    write(OUT / "candidate_bad.csv", 2,
          "candidate: ti_1_w deliberately +8 % (must be flagged)",
          gain={"ti_1_w": 1.08}, t_offset=0.017)


if __name__ == "__main__":
    main()
