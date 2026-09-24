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

Two steps are KWP conversations rather than `med9log log` runs (brief G5,
2026-09-24): `dtc` reads the stored faults with `18 00 FF 00`, clears them with
`14 FF 00` and reads them back -- the bench day's first step, `docs/07`
section 3.4 step 1 -- and `pid52` turns G1's `ff_pid52_enable` on and shows
that `01 40` advertises PID 0x52 only after a reconnect, because the support
bitmaps are rebuilt once per connection.  Their transcripts are
`ff_fuel_sim_dtc.csv` / `ff_fuel_sim_pid52.csv`, and what in them is modelled
rather than the firmware says so in a `# modelled:` line.

`pid52_obd` (brief H3, 2026-09-24) is the same PID 0x52 rehearsal the way a
generic scan tool does it: ISO 15765-4 single frames on **0x7DF**, answers on
**0x7E8**, through the firmware's own ISO 15765-2 parser and dispatcher
(`logging/ecu_sim.py --obd-can`, `emu/obd_can.py`, `re/findings/obd.md` 11).
With the switch off every answer is byte-for-byte the stock image's; with it
on, `01 40` advertises 0x52 only after a reconnect (a request after more than
5 s of silence opens a new connection) and `02 01 52` answers `03 41 52 A`
for E0 and E85.  Transcript `ff_fuel_sim_pid52_obd.csv`.

Usage::

    python3 logging/bench_rehearsal.py                 # everything, ~3 min
    python3 logging/bench_rehearsal.py --only e0 e85
    python3 logging/bench_rehearsal.py --list
"""
from __future__ import annotations

import argparse
import csv
import functools
import json
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
from emu.models import flexfuel as _ff  # noqa: E402

#: read from the reference model rather than restated, so the check survives a
#: later wave appending to `struct ff_state` (E2 took it from 64 to 68)
FF_MAGIC = _ff.FlexFuelModel.MAGIC
FF_LENGTH = _ff.FlexFuelModel.LENGTH

SAMPLES = REPO / "logging" / "samples"
SESSION = REPO / "logging" / "sessions" / "ff_fuel.json"
PATCH = REPO / "patches" / "ff_fuel"
PATCH_JSON = PATCH / "patch.json"
FFCAL_JSON = PATCH / "ffcal001.json"
TOLERANCE = PATCH / "test" / "tolerance.json"
DUMP = REPO / "data" / "passat_azx_ori.bin"


# ---------------------------------------------------------------------------
# where the patch stores the ethanol percent -- read, never restated
# ---------------------------------------------------------------------------
@functools.lru_cache(maxsize=None)
def persist_location(ffcal_json: str = str(FFCAL_JSON)) -> dict:
    """Block, payload offset and addresses of the stored E%, at run time.

    `ff_persist_block` / `ff_persist_offset` come out of the patch's own
    calibration source (`patches/ff_fuel/ffcal001.json`), and the block's RAM
    mirror and EEPROM address out of the firmware's EEP_CONF table
    (`tools/eeprom_map.py`), so a later brief that moves the store (G7 takes
    the offset from 2 to 19: block 8 payload +2..+18 are the 17 KWP
    adaptation channels, docs/05 section 3.8, note of 2026-09-23) changes
    nothing here.  Brief G5.
    """
    import eeprom_map                                          # noqa: E402
    import med9lib                                             # noqa: E402

    cal = json.loads(Path(ffcal_json).read_text(encoding="utf-8"))
    block = int(cal["ff_persist_block"])
    offset = int(cal["ff_persist_offset"])
    blk = eeprom_map.read_blocks(bytes(med9lib.load_dump(str(DUMP))))[block]
    if blk.mirror_cpu is None:
        raise SystemExit(f"EEP_CONF block {block} has no RAM mirror")
    if not 0 <= offset < blk.payload:
        raise SystemExit(f"ff_persist_offset {offset} is outside block "
                         f"{block}'s {blk.payload}-byte payload")
    return {"block": block, "offset": offset,
            "mirror": blk.mirror_cpu + offset,
            "eeprom": blk.copy_addr(0) + offset,
            "label": f"block {block} payload +{offset}"}


@functools.lru_cache(maxsize=None)
def persist_variable(session: str = str(SESSION),
                     patch_json: str = str(PATCH_JSON)) -> str | None:
    """The session variable that logs the persist byte's mirror, or None."""
    want = persist_location()["mirror"]
    sess = med9log.load_session(session, patch_json)
    for v in sess.variables:
        if v.address == want and v.size == 1 and not v.split_bytes:
            return v.name
    return None


def persist_device_byte(eeprom_file: str | None) -> int | None:
    """The stored byte in copy 0 on the simulated M95160, or None."""
    if not eeprom_file or not os.path.exists(eeprom_file):
        return None
    raw = Path(eeprom_file).read_bytes()
    at = persist_location()["eeprom"]
    return raw[at] if at < len(raw) else None

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


class KwpStep(Step):
    """A step that is a KWP conversation, not a `med9log log` run (brief G5).

    `script(step, eeprom_file)` drives the simulator over the in-process
    TP2.0 bus with the same `med9kwp` client the logger uses and writes a
    transcript in the logger's CSV shape (`time_s,var,value,unit`, plus one
    `# kwp:` comment per exchange), labelled `# simulated: true`.
    """

    def __init__(self, name, why, script, sim_seconds=0, **kw):
        super().__init__(name, why, sim_seconds, node=False, **kw)
        self.script = script


def _insert_after(name: str, step: Step) -> None:
    STEPS.insert(next(i for i, s in enumerate(STEPS) if s.name == name) + 1,
                 step)


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
def checks_for(name: str, s: dict, *, eeprom_file: str | None = None
               ) -> list[tuple[str, bool, str]]:
    """-> [(what, ok, detail)], the numbered checks of ff_fuel.json."""
    out = []

    def chk(what, ok, detail=""):
        out.append((what, bool(ok), detail))

    if name == "stock":
        chk("no ff_state on a stock image", last(s, "ff_magic") in (0.0, None),
            f"ff_magic={last(s, 'ff_magic')}")
        return out
    if name == "dtc":
        seeded = int(last(s, "dtc_seeded", 0))
        chk("D1 18 00 FF 00 answers a well-formed list",
            last(s, "dtc_before_well_formed") == 1,
            f"{last(s, 'dtc_before_count')} DTC(s)")
        chk("D1 the stored faults are all reported",
            last(s, "dtc_before_count") == seeded and seeded > 0,
            ", ".join(f"{int(last(s, f'dtc_before_{i}_code', 0)):#06x}/"
                      f"{int(last(s, f'dtc_before_{i}_status', 0)):#04x}"
                      for i in range(seeded)))
        chk("D2 17 <DTC> reads the status of one of them",
            last(s, "dtc_status_query_ok") == 1)
        chk("D3 14 FF 00 pends, commits and answers 54 FF 00",
            last(s, "clear_positive") == 1,
            f"{last(s, 'clear_seconds', 0):.2f} wall s")
        chk("D4 read back after the clear: 58 00",
            last(s, "dtc_after_well_formed") == 1
            and last(s, "dtc_after_count") == 0)
        chk("D5 still empty on a new connection",
            last(s, "dtc_reconnect_count") == 0
            and last(s, "connections") == 2,
            f"connections={last(s, 'connections')}")
        return out
    if name == "pid52":
        # stock answers 01 40 negatively (obd.md 7: [0x122C] bit 0 clear),
        # which is "0x41-0x60 not supported" and so also "0x52 not advertised"
        chk("P1 shipped (ff_pid52_enable = 0): 0x52 neither advertised nor answered",
            last(s, "shipped_0140_bit52") == 0
            and last(s, "shipped_0152_positive") == 0,
            f"01 40 positive={last(s, 'shipped_0140_positive')}")
        chk("P2 the bitmap is per connection: no change before a reconnect",
            last(s, "same_connection_0140_bit52") == 0,
            f"(01 52 itself answers at once: "
            f"{last(s, 'same_connection_0152_positive')})")
        chk("P3 after a reconnect 01 40 advertises 0x52 and 01 52 answers",
            last(s, "reconnected_0140_bit52") == 1
            and last(s, "reconnected_0152_positive") == 1,
            f"A={last(s, 'reconnected_0152_a')}")
        return out
    if name == "pid52_obd":
        chk("O1 switch off: every 0x7E8 frame byte-for-byte the stock image's",
            last(s, "shipped_frames_equal_stock") == 1
            and last(s, "shipped_0152_answered") == 0)
        chk("O2 a 0x7DF request runs in session 6, tester address 0x33",
            last(s, "shipped_session") == 6 and last(s, "stock_session") == 6
            and last(s, "shipped_tester_target") == 0x33,
            f"session={last(s, 'shipped_session')}")
        chk("O3 enabled: 01 40 unchanged on the same connection",
            last(s, "same_connection_0140_bit52") == 0
            and last(s, "same_connection_open") == 1)
        chk("O4 after 5 s of silence a new connection: 01 40 advertises 0x52",
            last(s, "closed_after_idle") == 1
            and last(s, "reconnected_new_connection") == 1
            and last(s, "reconnected_0140_bit52") == 1)
        chk("O5 02 01 52 -> 03 41 52 A on 0x7E8 for E0 and E85",
            last(s, "e0_0152_frame_ok") == 1 and last(s, "e85_0152_frame_ok") == 1,
            f"A(E0)={last(s, 'e0_0152_a')} A(E85)={last(s, 'e85_0152_a')}")
        return out

    magic = last(s, "ff_magic")
    chk("1 ff_magic/ff_length",
        magic == FF_MAGIC and last(s, "ff_length") == FF_LENGTH,
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
        loc, var = persist_location(), persist_variable()
        stored = persist_device_byte(eeprom_file)
        e_persist = last(s, "ff_e_persist")
        if var is not None:
            chk("7 the mirror read over DDLI agrees with ff_e_persist",
                last(s, var) == e_persist,
                f"{var} ({loc['label']}, {loc['mirror']:#08x})="
                f"{last(s, var)} e_persist={e_persist}")
        else:
            chk("7 the mirror read over DDLI agrees with ff_e_persist", False,
                f"{SESSION.name} logs no 1-byte variable at {loc['mirror']:#08x}"
                f" ({loc['label']}, from ffcal001.json) -- add one; the device"
                f" image holds {stored}")
        chk("7c the EEPROM device holds ff_e_persist at the calibrated offset",
            stored is not None and stored == e_persist,
            f"{loc['label']} = EEPROM {loc['eeprom']:#05x}: {stored} "
            f"e_persist={e_persist}")
        chk("7 the queue is idle in almost every sample",
            sum(1 for _t, v in s.get("nvm_queue_state", [])
                if v not in (0x20, 0x21)) <= max(2, len(s.get("nvm_queue_state", [])) // 10),
            f"states={sorted(seen(s, 'nvm_queue_state'))}")
    if name == "d2_restart":
        chk("B3 a distinctive E60-E80 value came back (NOT the block id)",
            50 <= last(s, "ff_e_persist", 0) <= 100,
            f"e_key={last(s, 'ff_e_key')} e_persist={last(s, 'ff_e_persist')} "
            f"err={last(s, 'ff_persist_err')} -- 8 means the mirror was "
            "reloaded from the flash defaults, i.e. the store sat on the "
            "block-id byte (eeprom.md 10.5); the store is "
            f"{persist_location()['label']} per ffcal001.json")
    return out


def _csum_always_ok(s) -> bool:
    """The session only logs a few payload bytes and the checksum, so this is weak.

    It checks the one thing the log can: the checksum word never changes
    without the stored byte (wherever ffcal001.json puts it) changing with it.
    """
    pairs = list(zip(s.get(persist_variable() or "", []),
                     s.get("eep_blk8_mirror_csum", [])))
    if not pairs:
        return False
    combos = {(b[1], c[1]) for b, c in pairs}
    return len({b for b, _c in combos}) == len(combos)


# ---------------------------------------------------------------------------
def run_step(step: Step, eeprom_file: str | None) -> None:
    SAMPLES.mkdir(parents=True, exist_ok=True)
    print(f"\n=== {step.name}: {step.why}")
    if isinstance(step, KwpStep):
        step.script(step, eeprom_file)
        return
    rc = med9log.main(step.argv(eeprom_file))
    if rc != 0:
        raise SystemExit(f"{step.name} failed with {rc}")


# ---------------------------------------------------------------------------
# KWP conversations: the fault read-back and the PID 0x52 reconnect (G5)
# ---------------------------------------------------------------------------
class Transcript:
    """The logger's CSV shape for a scripted KWP conversation."""

    def __init__(self, step: Step, notes: list[str]):
        self.step = step
        self.t0 = None
        self.rows: list[tuple[float, str, float, str]] = []
        self.lines = [f"# session: {step.name} (a scripted KWP conversation, "
                      "logging/bench_rehearsal.py)",
                      "# transport: KWP2000 over TP2.0 (in-process virtual bus)",
                      "# simulated: true",
                      "# simulated_by: logging/ecu_sim.py -- NOT a recording "
                      "of an ECU"] + [f"# {n}" for n in notes]
        import time as _time
        self._clock = _time.monotonic

    def now(self) -> float:
        if self.t0 is None:
            self.t0 = self._clock()
        return round(self._clock() - self.t0, 4)

    def value(self, name: str, value, unit: str = "-") -> None:
        self.rows.append((self.now(), name, float(value), unit))

    def exchange(self, kwp, request: bytes, *, expect_negative=False) -> bytes:
        """Send one request with the logger's client; record both ends."""
        from med9kwp import NegativeResponse
        t = self.now()
        try:
            answer = kwp.raw(request)
            text = answer.hex(" ")
        except NegativeResponse as exc:
            answer = bytes([0x7F, exc.sid, exc.nrc])
            text = answer.hex(" ")
            if not expect_negative:
                text += "  (unexpected)"
        self.lines.append(f"# kwp: t={t:.3f} -> {request.hex(' ')}  <- {text}")
        return answer

    def write(self) -> None:
        with open(self.step.path, "w", newline="", encoding="utf-8") as fh:
            for line in self.lines:
                fh.write(line + "\n")
            w = csv.writer(fh)
            w.writerow(["time_s", "var", "value", "unit"])
            for t, name, value, unit in self.rows:
                w.writerow([f"{t:.4f}", name, f"{value:g}", unit])
        print(f"{len(self.rows)} values, {len(self.lines)} header lines -> "
              f"{self.step.path}")


class _Bench:
    """One simulator on its own in-process bus, and a tester that can reconnect."""

    _n = 0

    def __init__(self, handlers):
        from ecu_sim import EcuSimulator
        _Bench._n += 1
        self.channel = f"rehearsal_{os.getpid()}_{_Bench._n}"
        self.sim = EcuSimulator.on_virtual_bus(self.channel, handlers=handlers)
        self.handlers = handlers
        self._ctx = self.sim.background()
        self.tp = self.link = None

    def __enter__(self):
        self._ctx.__enter__()
        return self

    def connect(self):
        from med9kwp import KwpClient, Tp20Client, open_link, parse_bus_spec
        self.disconnect()
        self.link = open_link(parse_bus_spec(f"virtual:{self.channel}"))
        self.tp = Tp20Client(self.link, dest=0x01, timeout=3.0)
        self.tp.connect()
        return KwpClient(self.tp, timeout=3.0, pending_timeout=10.0)

    def disconnect(self):
        if self.tp is not None:
            try:
                self.tp.disconnect()
            except Exception:                               # pragma: no cover
                pass
            self.link.close()
            self.tp = self.link = None

    def __exit__(self, *exc):
        self.disconnect()
        self._ctx.__exit__(*exc)
        self.sim.close()
        if self.sim.error is not None:                      # pragma: no cover
            raise self.sim.error


#: the faults the "car" arrives with in the DTC step.  Codes out of the
#: firmware's own table at 0x5D9F06 (fault path 1 kind 0 and path 11 kind 2);
#: what they mean on the car does not matter here, only the round trip.
REHEARSAL_DTCS = ("P0601", "P1429")


def script_dtc(step: Step, eeprom_file: str | None) -> None:
    """docs/07 section 3.4 step 1: read the stored faults, clear them, read back.

    The patched image with its hooks running and an EEPROM (a copy of the
    rehearsal's, so B2/B3 are not disturbed: `14` commits EEP_CONF block 24).
    The two stored faults are SEEDED -- a model of the fault-path manager,
    `ecu_sim.DtcStore.seed` -- and the erase after a positive `14` is
    `DtcStore.after_clear`, also a model; every KWP answer is the firmware's
    handler (`kwp_sid_18_h1` 0x35064, `kwp_sid_17_h1` 0x36024,
    `kwp_sid_14_h1` 0x35410).
    """
    from ecu_sim import (AnimatedRam, DEFAULT_STATICS, Med9Handlers,
                         parse_read_dtc)
    tmp = Path(tempfile.mkdtemp(prefix="rehearsal_dtc_"))
    eep = tmp / "eeprom.bin"
    if eeprom_file and os.path.exists(eeprom_file):
        shutil.copyfile(eeprom_file, eep)
    handlers = Med9Handlers(
        str(DUMP), patch_dir=str(PATCH), eeprom=str(eep), time_scale=step.scale,
        seed=0x12345678, dtcs=REHEARSAL_DTCS,
        ram=AnimatedRam(live_task_set="A", statics=dict(DEFAULT_STATICS)))
    tr = Transcript(step, [
        f"sim_patch: {PATCH}",
        f"modelled: the stored faults {', '.join(REHEARSAL_DTCS)} "
        "(DtcStore.seed) and the erase after a positive 14 "
        "(DtcStore.after_clear); the 18/17/14 answers are the firmware's"])

    def read(kwp, tag):
        answer = tr.exchange(kwp, b"\x18\x00\xff\x00")
        try:
            dtcs = parse_read_dtc(answer)
            tr.value(f"{tag}_well_formed", 1)
        except ValueError:
            dtcs = []
            tr.value(f"{tag}_well_formed", 0)
        tr.value(f"{tag}_count", len(dtcs))
        for i, (code, status) in enumerate(dtcs):
            tr.value(f"{tag}_{i}_code", code)
            tr.value(f"{tag}_{i}_status", status)
        return dtcs

    with _Bench(handlers) as bench:
        kwp = bench.connect()
        tr.exchange(kwp, b"\x10\x89")
        before = read(kwp, "dtc_before")
        tr.value("dtc_seeded", len(REHEARSAL_DTCS))
        if before:
            code = before[0][0]
            answer = tr.exchange(kwp, b"\x17" + code.to_bytes(2, "big"))
            tr.value("dtc_status_query_ok", int(answer[:1] == b"\x57"
                                                and answer[2:4] == code.to_bytes(2, "big")))
        started = tr.now()
        answer = tr.exchange(kwp, b"\x14\xff\x00")
        tr.value("clear_positive", int(answer == b"\x54\xff\x00"))
        tr.value("clear_seconds", tr.now() - started, "s")
        read(kwp, "dtc_after")
        # a new connection: the firmware's h2 walk runs, the clear must hold
        kwp = bench.connect()
        tr.exchange(kwp, b"\x10\x89")
        read(kwp, "dtc_reconnect")
        tr.value("connections", handlers.connections)
    tr.lines.append(f"# block_24_writes: EEPROM device writes {handlers.eeprom.writes}")
    tr.write()
    shutil.rmtree(tmp, ignore_errors=True)


def script_pid52(step: Step, eeprom_file: str | None) -> None:
    """G1's OBD PID 0x52: enable it, reconnect, and `01 40` advertises it.

    The support bitmaps are rebuilt by `kwp_service_h2_walk` once per new
    diagnostic connection (obd.md 10.1, G3), so turning `ff_pid52_enable` on
    shows only after a reconnect.  Session 4 (`10 86`, after `27 03/04`)
    reaches the OBD services (obd.md 8 item 4).  The calibration change is
    written straight into the emulated FFCAL001 -- a stand-in for flashing
    a calibration with the byte set, labelled in the transcript.
    """
    sys.path.insert(0, str(PATCH))
    import ffcal001                                            # noqa: E402
    from ecu_sim import (AnimatedRam, DEFAULT_STATICS, FFCAL001_BASE,
                         Med9Handlers)
    from med9kwp.kwp import key_level2
    handlers = Med9Handlers(
        str(DUMP), patch_dir=str(PATCH), time_scale=step.scale,
        seed=0x12345678,
        ram=AnimatedRam(live_task_set="A", statics=dict(DEFAULT_STATICS)))
    tr = Transcript(step, [
        f"sim_patch: {PATCH}",
        "modelled: ff_pid52_enable = 1 written into the emulated FFCAL001 "
        "(stands in for a calibration flash); the 01 40 / 01 52 answers and "
        "the bitmap rebuild on reconnect are the firmware's"])

    def obd_session(kwp):
        tr.exchange(kwp, b"\x10\x89")
        seed = int.from_bytes(tr.exchange(kwp, b"\x27\x03")[2:6], "big")
        tr.exchange(kwp, b"\x27\x04" + key_level2(seed).to_bytes(4, "big"))
        tr.exchange(kwp, b"\x10\x86")

    def probe(kwp, tag):
        bitmap = tr.exchange(kwp, b"\x01\x40", expect_negative=True)
        tr.value(f"{tag}_0140_positive", int(bitmap[:2] == b"\x41\x40"))
        tr.value(f"{tag}_0140_bit52",
                 int(len(bitmap) >= 5 and bool(bitmap[4] & (0x80 >> ((0x52 - 1) & 7)))))
        pid = tr.exchange(kwp, b"\x01\x52", expect_negative=True)
        tr.value(f"{tag}_0152_positive", int(pid[:2] == b"\x41\x52"))
        if pid[:2] == b"\x41\x52" and len(pid) >= 3:
            tr.value(f"{tag}_0152_a", pid[2])

    with _Bench(handlers) as bench:
        kwp = bench.connect()
        obd_session(kwp)
        probe(kwp, "shipped")
        params = ffcal001.load_params(FFCAL_JSON)
        params["ff_pid52_enable"] = 1
        handlers.emu.write(FFCAL001_BASE, ffcal001.build(params))
        tr.lines.append("# step: ff_pid52_enable := 1 in the emulated FFCAL001")
        probe(kwp, "same_connection")
        kwp = bench.connect()
        obd_session(kwp)
        probe(kwp, "reconnected")
        tr.value("connections", handlers.connections)
    tr.write()


#: the requests of the switch-off proof, as a scan tool sends them on 0x7DF
OBD_PROOF_REQUESTS = (b"\x01\x00", b"\x01\x20", b"\x01\x40", b"\x01\x52",
                      b"\x01\x05", b"\x01\x0c", b"\x01\x00\x20\x40")
#: E percent -> the J1979 byte A = round(E * 255 / 100) (obd.md 5, G1)
OBD_E_LEVELS = ((0, 0x00), (85, 0xD9))


def _ff_state_at(e_pct: int) -> bytes:
    """MODEL: `struct ff_state` as the patch holds it after the Pico reported E.

    The same stand-in `tests/test_ff_obd_patch.py` uses: the reference model
    with `e_filt` = E and a long FAULT hold, so the patch's own 10 ms tick
    keeps E while it builds the PID 0x52 record.  Labelled in the transcript.
    """
    st = _ff.FlexFuelModel(_ff.Cal())
    st.state.e_filt = e_pct * 16
    st.state.hold_ticks = 0xFFFF
    st.seal()
    return st.full_bytes()


def script_pid52_obd(step: Step, eeprom_file: str | None) -> None:
    """G1's PID 0x52 over the generic scan-tool route, 0x7DF -> 0x7E8 (H3).

    Two simulators: the stock image, and the patched image with its hooks
    running.  Both get the same MODELLED sensor state -- every stock PID
    record valid (`emu.obd_can.seed_pid_records`) and, on the patched one,
    an ff_state at a given E (`_ff_state_at`) -- and are then asked the same
    ISO 15765-4 questions.  Everything between the 0x7DF frame and the 0x7E8
    frame is the firmware's (obd.md 11); the harness pieces are in
    `emu/obd_can.py`.
    """
    sys.path.insert(0, str(PATCH))
    import ffcal001                                            # noqa: E402
    from ecu_sim import (AnimatedRam, DEFAULT_STATICS, FFCAL001_BASE,
                         Med9Handlers)
    from emu.obd_can import seed_pid_records
    tr = Transcript(step, [
        f"sim_patch: {PATCH}",
        "modelled: every stock mode-01 PID record valid (seed_pid_records), "
        "ff_state at E0/E85 (_ff_state_at), ff_pid52_enable := 1 written "
        "into the emulated FFCAL001 (stands in for a calibration flash); "
        "the frames, the connection, session 6 and the bitmap rebuild are "
        "the firmware's (emu/obd_can.py lists the harness pieces)"])
    tr.lines[1] = ("# transport: ISO 15765-4 single frames, 0x7DF -> 0x7E8 "
                   "(ecu_sim --obd-can, the firmware's ISO 15765-2 route)")

    def new(patched: bool):
        kw = dict(patch_dir=str(PATCH)) if patched else {}
        h = Med9Handlers(str(DUMP), time_scale=step.scale, obd_can=True,
                         ram=AnimatedRam(live_task_set="A",
                                         statics=dict(DEFAULT_STATICS)), **kw)
        seed_pid_records(h.emu)
        return h

    def ask(h, tag, request):
        frames = h.obd_request(request)
        rx = " | ".join(f"{c:03X} {d.hex(' ')}" for c, d in frames) or "(no answer)"
        tr.lines.append(f"# obd: {tag}: 7DF {bytes([len(request)]).hex()} "
                        f"{request.hex(' ')}  <- {rx}")
        return frames

    def settle(h, e_pct=None):
        """The patch's own 10 ms tick, a few times (it builds the record)."""
        if e_pct is not None:
            h.emu.write(0x7FFB00, _ff_state_at(e_pct))
        for _ in range(5):
            h.runner._one_tick()

    def bit52(frames):
        if not frames or frames[0][1][1:3] != b"\x41\x40":
            return 0
        return int(bool(frames[0][1][5] & (0x80 >> ((0x52 - 1) & 7))))

    stock = new(False)
    want = {r: ask(stock, "stock", r) for r in OBD_PROOF_REQUESTS}
    tr.value("stock_session", stock.obd.session)

    h = new(True)
    settle(h, 85)
    got = {r: ask(h, "shipped", r) for r in OBD_PROOF_REQUESTS}
    tr.value("shipped_frames_equal_stock", int(got == want))
    tr.value("shipped_0152_answered", int(bool(got[b"\x01\x52"])))
    tr.value("shipped_session", h.obd.session)
    tr.value("shipped_tester_target", h.obd.tester_target)

    params = ffcal001.load_params(FFCAL_JSON)
    params["ff_pid52_enable"] = 1
    h.emu.write(FFCAL001_BASE, ffcal001.build(params))
    tr.lines.append("# step: ff_pid52_enable := 1 in the emulated FFCAL001")
    settle(h, 85)
    conns = h.obd.connections
    same = ask(h, "same_connection", b"\x01\x40")
    tr.value("same_connection_0140_bit52", bit52(same))
    tr.value("same_connection_open", int(h.obd.connections == conns))
    h.obd_idle(6.0)
    tr.value("closed_after_idle", int(not h.obd.connection_open))
    tr.lines.append("# step: 6 simulated s of bus silence; the connection "
                    "times out (5 s) and the next request opens a new one")
    again = ask(h, "reconnected", b"\x01\x40")
    tr.value("reconnected_0140_bit52", bit52(again))
    tr.value("reconnected_new_connection", int(h.obd.connections == conns + 1))
    for e_pct, a in OBD_E_LEVELS:
        settle(h, e_pct)
        frames = ask(h, f"E{e_pct}", b"\x01\x52")
        data = frames[0][1] if frames else b""
        tr.value(f"e{e_pct}_0152_frame_ok",
                 int(len(frames) == 1 and frames[0][0] == 0x7E8
                     and data == bytes([0x03, 0x41, 0x52, a, 0, 0, 0, 0])))
        tr.value(f"e{e_pct}_0152_a", data[3] if len(data) > 3 else -1)
    tr.value("session", h.obd.session)
    tr.value("connections", h.obd.connections)
    tr.write()


_insert_after("fault6", KwpStep(
    "dtc", "docs/07 3.4 step 1: read stored DTCs, clear them, read them back",
    script_dtc))
_insert_after("d2_restart", KwpStep(
    "pid52", "G1/#39: ff_pid52_enable on, reconnect, 01 40 advertises PID 0x52",
    script_pid52))
_insert_after("pid52", KwpStep(
    "pid52_obd", "H3/#48: PID 0x52 as a scan tool reads it, 0x7DF -> 0x7E8",
    script_pid52_obd))
BY_NAME = {s.name: s for s in STEPS}


#: the live 10 ms raster activation counter -- the ECU's own clock, 100 per
#: second (`re/findings/scheduler.md` section 11).  Aligning two runs on it is
#: `tools/logcmp.py --align-on raster_setA_10ms_count:100`; the E4 code that
#: used to live here is now `logcmp.align_on` (brief F2), so the bench and the
#: rehearsal run the same implementation.
RASTER = "raster_setA_10ms_count"


def compare_e0(strict_fail_gain: float = 1.03) -> list[tuple[str, bool, str]]:
    """procedure.md section 4 + the E0 comparison recipe of brief E0.

    `uncovered="report"` is what `tolerance.json` means: the session file
    reads plain RAM, so a stock log carries the patch's `ff_*` variables
    reading 0, and the free-running raster counters can never be compared
    between two power-ups.  The variables the file does not name are listed,
    not judged against its default limit.
    """
    out = []
    base, cand = BY_NAME["stock"].path, BY_NAME["e0"].path
    if not (base.exists() and cand.exists()):
        return [("logcmp E0 equivalence", False, "run the stock and e0 steps first")]
    default, per = logcmp.load_tolerances(str(TOLERANCE))
    blog, clog = logcmp.load_log(base), logcmp.load_log(cand)
    try:
        clog, shift = logcmp.align_on(blog, clog, RASTER)
    except logcmp.AlignError as exc:
        return [("logcmp E0 equivalence", False, str(exc))]
    rows, summary = logcmp.compare(blog, clog, default, per, uncovered="report")
    uncovered = summary["uncovered"]
    bad = [r.var for r in rows if r.verdict == "FAIL"]
    out.append(("logcmp stock vs patched-at-E0 passes", not bad,
                ", ".join(bad) or f"aligned by {shift * 1000:+.0f} ms of ECU time"))
    out.append(("tolerance.json covers what the session logs", not uncovered,
                "not in tolerance.json: " + ", ".join(uncovered)
                if uncovered else ""))

    perturbed = Path(tempfile.mkdtemp()) / "rk_plus_3pct.csv"
    _scale_variable(cand, perturbed, "rk_fuel_mass", strict_fail_gain)
    plog, _shift2 = logcmp.align_on(blog, logcmp.load_log(perturbed), RASTER)
    rows2, _s2 = logcmp.compare(blog, plog, default, per, uncovered="report")
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
        for what, ok, detail in checks_for(step.name, series(step.path),
                                           eeprom_file=a.eeprom):
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
