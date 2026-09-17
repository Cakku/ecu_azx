#!/usr/bin/env python3
"""Run every ff_fuel bench procedure against the simulator and grade it.

`patches/ff_fuel/test/procedure.md` sections 2-5 and `procedure_d2.md` A1-A4
and B2-B3 were written before any hardware existed and nobody had executed
them.  This script executes all of them against `logging/ecu_sim.py`
(`--sim-patch`, `--eeprom`), with `logging/ethanol_frame_send.py` standing in
for the Pico on the same in-process bus and `logging/med9log.py` doing the
reading -- so the procedures, the session file and the tolerances are debugged
before the ECU exists (brief E4).

It writes `logging/samples/ff_fuel_sim_*.csv`, every one of them carrying
`# simulated: true`, and prints a check table.  **Nothing here is a
measurement.** A simulated log never enters a bench comparison set (C3's
rule); it proves the procedure, the plumbing and the arithmetic.

Usage::

    python3 logging/bench_rehearsal.py                 # everything, ~3 min
    python3 logging/bench_rehearsal.py --only e0 e85
    python3 logging/bench_rehearsal.py --list
"""
from __future__ import annotations

import argparse
import csv
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
for _p in (str(REPO), str(REPO / "logging"), str(REPO / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import logcmp  # noqa: E402
import med9log  # noqa: E402

SAMPLES = REPO / "logging" / "samples"
SESSION = REPO / "logging" / "sessions" / "ff_fuel.json"
PATCH = REPO / "patches" / "ff_fuel"
PATCH_JSON = PATCH / "patch.json"
TOLERANCE = PATCH / "test" / "tolerance.json"

#: simulated seconds per wall second.  The 10 ms hook costs about 0.5 ms of
#: host CPU, so five simulated seconds per wall second leaves plenty of room
#: (`emu/README.md`); raise it and `runner.sim_t` simply falls behind.
SCALE = 5.0


# ---------------------------------------------------------------------------
# one step
# ---------------------------------------------------------------------------
class Step:
    def __init__(self, name, why, sim_seconds, *, patch=True, node=True,
                 eeprom=False, scale=SCALE, rate=3.0, **node_opts):
        self.name = name
        self.why = why
        self.sim_seconds = sim_seconds
        self.patch = patch
        self.node = node
        self.eeprom = eeprom
        self.scale = scale
        #: samples per wall second.  The logger will happily run flat out, but
        #: these files are checked in, so a rehearsal log is paced: 3 Hz over
        #: a 5x clock is still a sample every 0.3 s of ECU time, finer than
        #: any criterion in the procedures.
        self.rate = rate
        self.node_opts = node_opts

    @property
    def path(self) -> Path:
        return SAMPLES / f"ff_fuel_sim_{self.name}.csv"

    def argv(self, eeprom_file: str | None) -> list[str]:
        a = ["log", "--sim", "--session", str(SESSION),
             "--patch", str(PATCH_JSON), "-o", str(self.path),
             "--sim-seconds", str(self.sim_seconds),
             "--time-scale", str(self.scale), "--rate", str(self.rate)]
        if self.patch:
            a += ["--sim-patch", str(PATCH)]
        else:
            a += ["--sim-stock-tasks"]
        if self.eeprom and eeprom_file:
            a += ["--eeprom", eeprom_file]
        if self.node:
            a += ["--sim-node"]
            for k, v in self.node_opts.items():
                flag = "--node-" + k.replace("_", "-")
                a += [flag] if v is True else [flag, str(v)]
        return a


STEPS = [
    Step("stock", "procedure.md 4: the STOCK baseline for the E0 comparison",
         12, patch=False, node=False, scale=1.0),
    Step("e0", "procedure.md 2 + 4: the patch with no node on the bus",
         12, node=False, scale=1.0),
    Step("e85", "procedure.md 3: reception, E85, the estimate ramping at 2 %/s",
         60, e_pct=85),
    Step("fault1", "procedure.md 5 row 1: sensor unplugged (status 1) at 40 s",
         70, e_pct=85, status=1, fault_after=40),
    Step("fault2", "procedure.md 5 row 2: the node stops sending at t = 40 s",
         70, e_pct=85, stop_after=40),
    Step("fault3", "procedure.md 5 row 3: contaminated fuel (status 2) at 40 s",
         70, e_pct=85, status=2, fault_after=40),
    Step("fault4", "procedure.md 5 row 4: the rolling counter freezes at 40 s",
         60, e_pct=85, stall=True, fault_after=40),
    Step("fault5", "procedure.md 5 row 5: implausible E (101 %) at 40 s",
         50, e_pct=85, implausible=True, fault_after=40),
    Step("fault6", "procedure.md 5 row 6: status 3 (not ready) from power-up",
         30, e_pct=85, not_ready=True),
    Step("d2_persist",
         "procedure_d2.md B2: the first commit, past the 60 s rate limit",
         150, e_pct=85, eeprom=True),
    Step("d2_restart",
         "procedure_d2.md B3: the same EEPROM after a simulated power cut",
         12, node=False, eeprom=True),
]
BY_NAME = {s.name: s for s in STEPS}


# ---------------------------------------------------------------------------
# reading a log back
# ---------------------------------------------------------------------------
def series(path: Path) -> dict[str, list[tuple[float, float]]]:
    out: dict[str, list[tuple[float, float]]] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.reader(fh) if r and not r[0].lstrip().startswith("#")]
    for t, name, value, *_ in rows[1:]:
        try:
            out.setdefault(name, []).append((float(t), float(value)))
        except ValueError:
            pass
    return out


def last(s, name, default=None):
    return s[name][-1][1] if s.get(name) else default


def first(s, name, default=None):
    return s[name][0][1] if s.get(name) else default


def seen(s, name):
    return {v for _t, v in s.get(name, [])}


def slope(s, name) -> float:
    """Change per WALL second."""
    pts = s.get(name) or []
    if len(pts) < 2 or pts[-1][0] == pts[0][0]:
        return 0.0
    return (pts[-1][1] - pts[0][1]) / (pts[-1][0] - pts[0][0])


def ecu_slope(s, name) -> float:
    """Change per SIMULATED second, measured on the ECU's own raster counter.

    `time_s` in the CSV is wall-clock, and `--time-scale` makes one wall
    second several ECU seconds, so every "%/s" and "per second" criterion in
    the procedures has to be divided by the ECU's own clock -- which is in the
    log as the live raster counter, 100 activations per second.
    """
    pts, clk = s.get(name) or [], s.get(RASTER) or []
    if len(pts) < 2 or len(clk) < 2:
        return 0.0
    ecu = (clk[-1][1] - clk[0][1]) / 100.0
    if ecu <= 0:
        return 0.0
    return (pts[-1][1] - pts[0][1]) / ecu


# ---------------------------------------------------------------------------
# the checks, per step
# ---------------------------------------------------------------------------
def checks_for(name: str, s: dict) -> list[tuple[str, bool, str]]:
    """-> [(what, ok, detail)], the numbered checks of ff_fuel.json."""
    out = []

    def chk(what, ok, detail=""):
        out.append((what, bool(ok), detail))

    if name == "stock":
        chk("no ff_state on a stock image", last(s, "ff_magic") in (0.0, None),
            f"ff_magic={last(s, 'ff_magic')}")
        return out

    magic = last(s, "ff_magic")
    chk("1 ff_magic/ff_length", magic == 0x46463031 and last(s, "ff_length") == 64,
        f"magic={magic} len={last(s, 'ff_length')}")
    chk("2 ff_src_seen names one task set",
        last(s, "ff_src_seen") in (1.0, 2.0)
        and last(s, "ff_src_owner") == last(s, "ff_src_seen"),
        f"seen={last(s, 'ff_src_seen')} owner={last(s, 'ff_src_owner')}")
    raster = "raster_setA_10ms_count" if last(s, "ff_src_seen") == 1 \
        else "raster_setB_10ms_count"
    chk("3 ff_ticks tracks the live raster 1:1",
        abs(last(s, "ff_ticks", 0) - last(s, raster, -1)) <= 2,
        f"ff_ticks={last(s, 'ff_ticks')} {raster}={last(s, raster)}")
    chk("3b ff_cal_ok / ff_cal_mode",
        last(s, "ff_cal_ok") == 1 and last(s, "ff_cal_mode") == 1)

    if name == "e0":
        chk("4 no node: FAULT and F = 1.000",
            last(s, "ff_mode") == 3 and abs(last(s, "ff_f_q10") - 1.0) < 1e-6,
            f"mode={last(s, 'ff_mode')} F={last(s, 'ff_f_q10')}")
    if name == "e85":
        chk("5 reception: OK, ~10 frames/s, E climbing",
            last(s, "ff_mode") == 1 and 8 <= ecu_slope(s, "ff_frames") <= 12
            and last(s, "ff_e_filt") > first(s, "ff_e_filt"),
            f"mode={last(s, 'ff_mode')} frames/s={ecu_slope(s, 'ff_frames'):.1f} "
            f"E={last(s, 'ff_e_filt'):.2f} %")
        chk("5b the driver shadow carries the frame",
            last(s, "can_rx_spare0_b0") == 85 and last(s, "ff_e_raw") == 85)
        chk("5c the slew limit holds (<= 2 %/s of ECU time)",
            ecu_slope(s, "ff_e_filt") <= 2.05,
            f"{ecu_slope(s, 'ff_e_filt'):.3f} %/s")
        chk("5d ff_rk_calls rises with engine speed",
            40 <= ecu_slope(s, "ff_rk_calls") <= 200,
            f"{ecu_slope(s, 'ff_rk_calls'):.0f} segments/s of ECU time")
        chk("8a ff_dzw_e is 0 with the shipped calibration",
            seen(s, "ff_dzw_e") <= {0.0},
            f"values={sorted(seen(s, 'ff_dzw_e'))}")
        chk("8a dwkrz never positive",
            all(max(seen(s, f"dwkrz_{i}") or {0}) <= 0 for i in range(6)))
    if name.startswith("fault"):
        row = int(name[-1])
        mode, held = last(s, "ff_mode"), last(s, "ff_f_q10")
        want = {1: 3, 2: 3, 3: 2, 4: 3, 5: 3, 6: 3}[row]
        chk(f"5.{row} ff_mode == {want}", mode == want, f"mode={mode}")
        if row == 3:
            chk("5.3 HOLD is not a FAULT and F is frozen",
                last(s, "ff_faults") == 0 and held > 1.0,
                f"faults={last(s, 'ff_faults')} F={held}")
        elif row == 6:
            chk("5.6 nothing was ever accepted", last(s, "ff_frames") == 0)
        elif row == 5:
            chk("5.5 the fuel factor is HELD, not dropped", held > 1.0,
                f"F={held}")
        else:
            chk(f"5.{row} the fuel factor is HELD, not dropped", held > 1.0,
                f"F={held}")
        if row == 4:
            chk("5.4 ff_stall reached the threshold", last(s, "ff_stall") >= 3,
                f"stall={last(s, 'ff_stall')}")
        if row == 5:
            chk("5.5 the frame is latched bad", last(s, "ff_frame_bad") == 1)
    if name == "d2_persist":
        chk("8 one commit finished, none failed",
            last(s, "ff_persist_writes") == 1 and last(s, "ff_persist_fails") == 0,
            f"writes={last(s, 'ff_persist_writes')} "
            f"fails={last(s, 'ff_persist_fails')} "
            f"err={last(s, 'ff_persist_err')}")
        chk("7 the mirror read over DDLI agrees with ff_e_persist",
            last(s, "eep_blk8_mirror_b0") == last(s, "ff_e_persist"),
            f"mirror+0={last(s, 'eep_blk8_mirror_b0')} "
            f"e_persist={last(s, 'ff_e_persist')} -- KNOWN OPEN: with the NVM "
            "stack running, the first DDLI chunk of the protected window does "
            "not read back its address (see the E4 report)")
        chk("7 the queue is idle in almost every sample",
            sum(1 for _t, v in s.get("nvm_queue_state", [])
                if v not in (0x20, 0x21)) <= max(2, len(s.get("nvm_queue_state", [])) // 10),
            f"states={sorted(seen(s, 'nvm_queue_state'))}")
    if name == "d2_restart":
        chk("B3 a distinctive E60-E80 value came back (NOT the block id)",
            50 <= last(s, "ff_e_persist", 0) <= 100,
            f"e_key={last(s, 'ff_e_key')} e_persist={last(s, 'ff_e_persist')} "
            f"err={last(s, 'ff_persist_err')} -- 8 means the mirror was "
            "reloaded from the flash defaults, i.e. ff_persist_offset = 0 "
            "sat on the block-id byte (eeprom.md 10.5)")
    return out


def _csum_always_ok(s) -> bool:
    """The session only logs +0, +14, +29 and the checksum, so this is weak.

    It checks the one thing the log can: the checksum word never changes
    without the payload byte changing with it.
    """
    pairs = list(zip(s.get("eep_blk8_mirror_b0", []),
                     s.get("eep_blk8_mirror_csum", [])))
    if not pairs:
        return False
    combos = {(b[1], c[1]) for b, c in pairs}
    return len({b for b, _c in combos}) == len(combos)


# ---------------------------------------------------------------------------
def run_step(step: Step, eeprom_file: str | None) -> None:
    SAMPLES.mkdir(parents=True, exist_ok=True)
    print(f"\n=== {step.name}: {step.why}")
    rc = med9log.main(step.argv(eeprom_file))
    if rc != 0:
        raise SystemExit(f"{step.name} failed with {rc}")


def _named_only(log, names):
    """The log restricted to the variables `tolerance.json` actually names.

    `tolerance.json` says the `ff_*` variables "exist only in the patched
    log", but the session file reads plain RAM, so on a stock image they are
    present and read 0 -- and then they fall to the file's `default` limit of
    1.0 and drown the report.  The same is true of the raster counters, which
    are free-running counts nobody can compare between two runs.  Restricting
    the comparison to the named variables is what the file means; the ones it
    does not name are reported as uncovered.
    """
    out = type(log)()
    out.meta, out.path = log.meta, log.path
    for name in names:
        if name in log:
            out[name] = log[name]
    return out


RASTER = "raster_setA_10ms_count"


def _align_on_raster(base, cand):
    """Shift the candidate's time axis so the two ECUs' own clocks agree.

    Two runs are two separate power-ups: the logger's t = 0 is its own first
    sample, and the tens of milliseconds between "the ECU powered on" and
    "the tester finished the DDLI setup" are not the same twice.  On a ramp
    of 110 rpm/s that offset alone is worth more than half of the `nmot_w`
    budget, and it says nothing about the software.

    Both logs carry the live raster activation counter, which IS the ECU's
    clock (100 per second, `re/findings/scheduler.md` section 11), so the
    offset is measurable rather than guessable.  A bench comparison of two
    drives needs exactly the same step -- this is the missing line of the E0
    comparison recipe.
    """
    if RASTER not in base or RASTER not in cand:
        return cand, 0.0
    b, c = base[RASTER], cand[RASTER]
    if not b.t or not c.t:
        return cand, 0.0
    # seconds of ECU time at each log's first sample
    shift = (c.v[0] - b.v[0]) / 100.0 - (c.t[0] - b.t[0])
    out = type(cand)()
    out.meta, out.path = cand.meta, cand.path
    for name, series in cand.items():
        moved = type(series)(name)
        moved.t = [t + shift for t in series.t]
        moved.v = list(series.v)
        out[name] = moved
    return out, shift


def compare_e0(strict_fail_gain: float = 1.03) -> list[tuple[str, bool, str]]:
    """procedure.md section 4 + the E0 comparison recipe of brief E0."""
    out = []
    base, cand = BY_NAME["stock"].path, BY_NAME["e0"].path
    if not (base.exists() and cand.exists()):
        return [("logcmp E0 equivalence", False, "run the stock and e0 steps first")]
    default, per = logcmp.load_tolerances(str(TOLERANCE))
    named = set(per)
    blog, clog = logcmp.load_log(base), logcmp.load_log(cand)
    uncovered = sorted((set(blog) & set(clog)) - named
                       - {n for n in (set(blog) & set(clog)) if n.startswith("ff_")})
    clog, shift = _align_on_raster(blog, clog)
    rows, _summary = logcmp.compare(_named_only(blog, named),
                                    _named_only(clog, named), default, per)
    bad = [r.var for r in rows if r.verdict == "FAIL"]
    out.append(("logcmp stock vs patched-at-E0 passes", not bad,
                ", ".join(bad) or f"aligned by {shift * 1000:+.0f} ms of ECU time"))
    out.append(("tolerance.json covers what the session logs", not uncovered,
                "not in tolerance.json: " + ", ".join(uncovered)
                if uncovered else ""))

    perturbed = Path(tempfile.mkdtemp()) / "rk_plus_3pct.csv"
    _scale_variable(cand, perturbed, "rk_fuel_mass", strict_fail_gain)
    plog, _shift2 = _align_on_raster(blog, logcmp.load_log(perturbed))
    rows2, _s2 = logcmp.compare(_named_only(blog, named),
                                _named_only(plog, named), default, per)
    bad2 = [r.var for r in rows2 if r.verdict == "FAIL"]
    out.append((f"logcmp FAILS when rk is perturbed by "
                f"{(strict_fail_gain - 1) * 100:.0f} %",
                bad2 == ["rk_fuel_mass"], ", ".join(bad2) or "nothing flagged"))
    shutil.rmtree(perturbed.parent, ignore_errors=True)
    return out


def _scale_variable(src: Path, dst: Path, name: str, gain: float) -> None:
    with open(src, newline="", encoding="utf-8") as fh, \
            open(dst, "w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out)
        for raw in fh:
            if raw.lstrip().startswith("#"):
                out.write(raw)
                continue
            row = next(csv.reader([raw]))
            if len(row) >= 3 and row[1] == name:
                try:
                    row[2] = f"{float(row[2]) * gain:.6g}"
                except ValueError:
                    pass
            writer.writerow(row)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", nargs="*", default=None,
                    help="run only these steps (see --list)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--eeprom", default=str(REPO / "work" / "eeprom_rehearsal.bin"))
    ap.add_argument("--fresh-eeprom", action="store_true",
                    help="delete the EEPROM image first, so B2 starts virgin")
    ap.add_argument("--no-compare", action="store_true")
    a = ap.parse_args(argv)

    if a.list:
        for s in STEPS:
            print(f"  {s.name:<12} {s.sim_seconds:>4} simulated s   {s.why}")
        return 0

    steps = [BY_NAME[n] for n in a.only] if a.only else STEPS
    os.makedirs(os.path.dirname(a.eeprom), exist_ok=True)
    if a.fresh_eeprom and os.path.exists(a.eeprom):
        os.remove(a.eeprom)
    for step in steps:
        run_step(step, a.eeprom)

    print("\n" + "=" * 72)
    results: list[tuple[str, str, bool, str]] = []
    for step in steps:
        if not step.path.exists():
            continue
        for what, ok, detail in checks_for(step.name, series(step.path)):
            results.append((step.name, what, ok, detail))
    if not a.no_compare and (a.only is None or
                             {"stock", "e0"} <= set(a.only)):
        for what, ok, detail in compare_e0():
            results.append(("logcmp", what, ok, detail))

    width = max((len(r[1]) for r in results), default=10)
    failed = 0
    for step, what, ok, detail in results:
        failed += not ok
        print(f"  {step:<12} {what:<{width}}  {'ok  ' if ok else 'FAIL'}"
              + (f"  {detail}" if detail else ""))
    print(f"\n{len(results) - failed}/{len(results)} checks passed")
    print("every log carries `# simulated: true` and lives in logging/samples/")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
