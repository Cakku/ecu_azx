# logging/ — log format and regression comparison

The bench and drive logs that prove a patch changed nothing it should not
(`docs/06_patch_pipeline.md` section 7, `docs/05_flexfuel_design.md` E0
equivalence test). `tools/logcmp.py` reads this format.

## 1. The CSV format (long form) — the format loggers must write

```csv
# session: bench idle + load step, 2026-09-15
# ecu: 03H906032 / 1037382557
# dump_sha256: b15590d3f1874ace3125c5d047c09a686db9b8bb498187663539ebab205609b3
# transport: KWP2000 0x2C/0x21 over TP2.0
time_s,var,value,unit
0.000,nmot_w,780.1664,rpm
0.000,rl_w,17.0552,%
0.050,nmot_w,785.9819,rpm
```

Rules:

| Field | Meaning |
|---|---|
| `time_s` | seconds since the start of the log, decimal, non-negative. One sample per row; rows for the same timestamp may appear in any order. |
| `var` | variable name. Bosch Funktionsrahmen name when confident (`nmot_w`, `rl_w`, `ti_1_w`), otherwise the `re/symbols.csv` name, `cand_` prefix while unconfirmed (`docs/04_re_guidelines.md` section 3). |
| `value` | decimal number, `.` as the separator, no thousands grouping, no unit suffix. Enumerations and bit flags are logged as their numeric value. |
| `unit` | optional 4th column, free text, metadata only — `logcmp` ignores it. |

* Lines starting with `#` are comments. `# key: value` lines before the header
  become the log's metadata; record at least `ecu`, `dump_sha256` and
  `transport` so a log can be tied to the exact software it came from.
* Variables are sampled independently, at whatever rate the transport gives
  (KWP `0x2C`+`0x21` manages roughly 40 samples/s **in total**, so a set of six
  variables lands near 6 Hz each). The long form is the format precisely
  because per-variable rates differ and the set changes between sessions.
* One file per session. No interpolation, no gap filling, no re-ordering by the
  logger — write the samples as they arrive.

### Wide form

`tools/logcmp.py` also accepts `time_s` followed by one column per variable
(what most VCDS/CSV exports produce), and converts it on load. Empty cells are
skipped. Write the long form for new loggers; the wide form exists so that
third-party exports can be compared without conversion.

## 2. Comparing two logs

```bash
python3 tools/logcmp.py baseline.csv candidate.csv -t tolerance.json
python3 tools/logcmp.py baseline.csv candidate.csv -t tolerance.json --json report.json
```

For every variable present in **both** logs, the candidate is resampled onto
the baseline's timestamps inside the overlapping time window and the deviation
`candidate - baseline` is summarised as mean, mean-absolute, max-absolute (with
the time it occurred) and RMS. Variables present in only one log are listed,
not compared (`--strict` makes that a failure). Exit status is 1 if any
variable exceeds its tolerance, so it can gate a build.

Two logs from two different runs are never sample-aligned, so the numbers are
only as good as the scenario: **run the same bench scenario both times**, and
expect a deviation wherever the signal moves fast (the resampling error is
roughly the signal's slope times the time skew between the runs). Steady-state
sections are where the comparison actually has power.

### Tolerance file

JSON (recommended) or CSV with columns `var,max_abs,mean_abs,rel,interp`
(`var` = `*` sets the defaults).

```json
{
  "default": { "max_abs": 1.0, "mean_abs": 0.5, "interp": "linear" },
  "vars": {
    "nmot_w":  { "max_abs": 60.0, "mean_abs": 20.0 },
    "ti_1_w":  { "max_abs": 0.08, "mean_abs": 0.02 },
    "B_stend": { "max_abs": 0.0,  "interp": "hold" }
  }
}
```

| Key | Meaning |
|---|---|
| `max_abs` | limit on the largest absolute deviation |
| `mean_abs` | limit on the mean absolute deviation — the one that catches a small constant offset |
| `rel` | limit as a fraction of the largest absolute baseline value of that variable; the effective `max_abs` is the larger of the two |
| `interp` | `linear` (default) or `hold`. Use `hold` for enumerations, bit flags and anything that steps rather than ramps. |
| `max_abs: null` | **not compared** — "expected to differ, do not judge". The way a file says a variable was considered rather than forgotten (free-running counters, a patch's own state block on a stock run). |

Tolerances belong next to the thing they judge: the bench tolerances for a
patch live in `patches/<name>/test/`, not here.

**Added 2026-09-22 (F2).** Two runs are two power-ups, so align them on the
ECU's own clock before comparing, and derive the limits from two runs instead
of guessing them (docs/07 §§5.4-5.5):

```bash
python3 tools/logcmp.py base.csv cand.csv -t tolerance.json \
        --align-on raster_setA_10ms_count:100 --uncovered report
python3 tools/logcmp.py derive stock1.csv stock2.csv -o tolerance.json \
        --align-on raster_setA_10ms_count:100 --exclude 'raster_*' ff_ticks
```

`--uncovered {fail,report,ignore}` says what happens to variables the file does
not name: judge them against `default` (`fail`, the historical behaviour),
leave them out and list them (`report`), or leave them out silently. `derive`
writes the `_derived` provenance block — the two runs, the factor, the
alignment, the common time range, what was excluded — which the loader ignores.

## 3. Synthetic samples

`samples/` holds a deterministic 10 s bench scenario (idle, a trapezoidal load
step from 4 s to 8 s, idle) used by `tests/test_logcmp.py`:

| File | What it is |
|---|---|
| `baseline.csv` | the stock run |
| `candidate_ok.csv` | a repeat run: sensor noise and a 17 ms time skew, inside tolerance |
| `candidate_bad.csv` | the same run with `ti_1_w` deliberately +8 % |
| `tolerance.json` | limits for this scenario |
| `stock_run1.csv` | two runs of the same scenario on the same software, from two separate **power-ups**: each carries `raster_setA_10ms_count` from its own offset, and run 2's logger started 0.25 s of ECU time further into the scenario (added 2026-09-22, F2) |
| `stock_run2.csv` | |

```bash
python3 tools/logcmp.py logging/samples/baseline.csv logging/samples/candidate_ok.csv \
    -t logging/samples/tolerance.json          # RESULT: OK,     exit 0
python3 tools/logcmp.py logging/samples/baseline.csv logging/samples/candidate_bad.csv \
    -t logging/samples/tolerance.json          # FAIL ti_1_w,    exit 1
python3 tools/logcmp.py logging/samples/stock_run1.csv logging/samples/stock_run2.csv \
    -t logging/samples/tolerance.json          # 5 of 7 fail on the offset alone
python3 tools/logcmp.py logging/samples/stock_run1.csv logging/samples/stock_run2.csv \
    -t logging/samples/tolerance.json \
    --align-on raster_setA_10ms_count:100      # RESULT: OK,     exit 0
python3 logging/make_samples.py                # regenerate them (byte-identical)
```

They are synthetic — the numbers come from `make_samples.py`, not from an ECU.
They exist to test the tooling, not to describe the engine.

## 3b. RAM snapshots — `sessions/` (issue #23, added 2026-09-16 by C2)

A different kind of recording: not a time series of decoded variables but a
raw copy of the ECU's RAM, taken with KWP **RequestUpload 0x35 +
TransferData 0x36** (62 data bytes per block, `re/findings/kwp.md` section 5).
It exists to prove that the RAM a patch wants to use is not written by
anything at run time — the static survey in `re/findings/ram.md` cannot see a
pointer the code computes.

* **`sessions/ram_snapshot.json`** — the *ranges* to read, produced by the
  static survey: 63,932 bytes covering 0x7F8000-0x807FFF except the protected
  window 0x7F9E3C-0x7FA47F, which `kwp_upload_range_check` (0x0A3160) rejects
  with NRC 0x31. It also lists `priority_ranges` for a short first pass.
* **`sessions/<session>.json`** — one file per recording, written by the
  logger. Format and the six sessions to record (key-on, idle, after a drive,
  three key cycles) are in the docstring of `tools/ram_snapshot_diff.py` and
  in `re/findings/ram.md` section 9.

```bash
python3 tools/ram_snapshot_diff.py logging/sessions/*.json --free 64 \
        --csv work/ramdiff.csv
python3 tools/ram_snapshot_diff.py --self-test     # synthetic, no ECU needed
```

---

# The logger (brief C3, issue #20, added 2026-09-16)

`logging/med9log.py` is the tool; `logging/med9kwp/` is the protocol stack it
sits on; `logging/ecu_sim.py` is an emulated ECU that answers with the
firmware's **own** KWP handlers, so every command below can be rehearsed on
this Mac before anything is plugged into a car.

```
logging/
  med9log.py          the CLI: log / dump / groups / probe
  ecu_sim.py          emulated MED9 on a virtual CAN bus (--self-test, --sim)
  med9kwp/
    can_transport.py  python-can bus from an "interface:channel" string
    tp20.py           VW TP2.0: channel setup, segmentation, ACK, keep-alive
    kwp.py            KWP2000: sessions, 0x27, 0x2C/0x21, 0x35/0x36/0x37
    vag_formulas.py   (formula, A, B) -> a number, with a per-entry tag
  sessions/           what to log, one file per question
```

## 4. Rehearse everything against the simulator first

```bash
python3 logging/ecu_sim.py --self-test          # 23 steps (18 before G5), RESULT: PASS
python3 logging/med9log.py probe  --sim
python3 logging/med9log.py groups --sim 1 3 106 231
python3 logging/med9log.py log    --sim --seconds 2 \
        --session logging/sessions/wave_b_confirm.json -o work/sim.csv
python3 logging/med9log.py dump   --sim --ranges logging/sessions/ram_snapshot.json \
        -o work/sim_snap.bin --session-name key-on
python3 -m unittest tests.test_med9kwp           # 49 tests, no hardware
```

The simulator animates the five raster counters and C1's Flash-1 block at the
rates brief C4 measured (1 ms / 2 ms / 10 ms, so +1000, +500 and +100 per
second). `logging/ecu_sim.py --task-set A` makes the **other** OS task set
live, which freezes the set-B counters *and* the Flash-1 block — rehearse that
before the bench, because on a real ECU it looks exactly like a failed flash
and is not one (`sessions/flash1_counter.json` check 1).

`--sim` runs the simulator **in this process** on a python-can `virtual` bus;
that bus does not cross process boundaries, so starting `ecu_sim.py` in a
second terminal on `virtual:` will not work (put it on a real adapter if you
want that). Nothing it produces is a recording of an ECU, and both the CSV and
the snapshot manifest say so in a `# simulated:` / `"simulated"` line —
**never put a simulated snapshot into the #23 comparison set.**

> **2026-09-24 (G5, #22 prep).** The simulator also answers the fault services
> with the firmware's own handlers — `18 00 FF 00` (read DTCs), `17 hi lo`
> (status of one) and `14 FF 00` (clear; needs `--eeprom`, it commits EEP_CONF
> block 24 and answers `7F 14 78` then `54 FF 00`) — and `--seed-dtc P0601`
> puts a fault into the firmware's RAM fault memory (a labelled model of the
> fault-path manager); `bench_rehearsal.py --only dtc` rehearses the bench
> day's "clear DTCs, then read them back".

### Session files

```json
{"rate_hint_hz": 40,
 "vars": [{"name": "nmot_w", "addr": "0x7FEE74", "size": 2, "signed": false,
           "scale": 0.25, "offset": 0, "unit": "rpm",
           "source": "re/findings/scheduler.md"}]}
```

`value = raw * scale + offset`. A variable may give `"symbol": "<name>"`
instead of `"addr"`, resolved from `re/symbols.csv` or `re/measuring_vars.csv`;
`"split_bytes": true` writes one row per byte (for CAN buffers);
`"patch_offset": N` makes the address `patch.json`'s `build.ram` + N when
`--patch` is given, so a session file does not go stale when a patch moves.

The logger merges adjacent variables into one DDLI chunk and bridges holes of
up to 8 bytes, because the firmware allows only **20 chunks on dynamic id 0xF0
and 3 on each of 0xF1-0xF9** (`re/findings/kwp.md` section 4.1). More than
that spills onto the next id, which is then polled in the same sample; past
47 chunks it refuses and says so. `wave_b_confirm.json`'s 26 variables need
17 chunks and 56 bytes per sample (counts corrected 2026-09-17, brief E7).

| File | The question it answers |
|---|---|
| `sessions/wave_b_confirm.json` | every row of #44, plus the five raster counters that confirm brief C4's task periods and say **which of the two OS task sets is live** |
| `sessions/can_bc_check.json` | do TouCAN modules B and C share one wire? (`can.md` section 3) |
| `sessions/flash1_counter.json` | did Flash 1 run, and at what rate? (`patches/ff_counter/test/procedure.md`) |
| `sessions/ram_snapshot.json` | the *ranges* for `dump`, from brief C2 |
| `sessions/tuning_checklist.json` | the baseline log of issue #43 (brief F4): 36 variables in 28 DDLI chunks, taken before and after any hardware or calibration change and compared with `tools/logcmp.py`. It is the *logs* column of `re/findings/tuning_checklist_draft.md`, and it carries the six cells no measuring variable exposes — `zwdelta_load` 0x7FD338 and the three tester adaptation channels among them |

## 5. Where the protocol comes from, and the licences

KWP2000 is all from **`re/findings/kwp.md`**, which came out of this ECU's own
code. **TP2.0 is not in the firmware findings at all** — the ECU's transport
layer was never disassembled (`kwp.md` section 9) — so `tp20.py` is a clean
re-implementation from public descriptions of the protocol:

| Source | What was taken | Licence |
|---|---|---|
| [jazdw.net/tp20](https://jazdw.net/tp20) | the frame tables and the worked example | none stated |
| [OVMS `VW-TP-2.0.txt`](https://github.com/openvehicles/Open-Vehicle-Monitoring-System-3/blob/master/vehicle/OVMS.V3/components/vehicle/docs/VW-TP-2.0.txt) | the T1/T3 bit layout | Apache-2.0 (doc) |
| [icanhack.nl VW TP 2.0](https://icanhack.nl/knowledge-base/diagnostics/vw-tp20/) | ACK sequence semantics, 0xA3 answered with 0xA1 | none stated |
| [I-CAN-hack/pq-flasher](https://github.com/I-CAN-hack/pq-flasher) | named as a reference only | MIT |
| [EliasTuning/MED9RamReader](https://github.com/EliasTuning/MED9RamReader), [KWP2000-CAN](https://github.com/EliasTuning/KWP2000-CAN) | named as references only | MIT |
| [notyal/vwcanread](https://github.com/notyal/vwcanread) | named as a reference only | **GPL-3.0 — do not copy from it** |
| [jazdw/vag-blocks](https://github.com/jazdw/vag-blocks) | the measuring-block formula *facts* in `vag_formulas.py` | **GPL-3.0 — no code copied** |

**No third-party source file is vendored and no code was copied from any of
them**; see `NOTICE.md`. Two of the projects are GPL-3.0, which this
repository (PolyForm Noncommercial) could not carry.

Implementation sanity check: the frames this stack puts on the wire are
byte-identical to the published worked example — channel setup
`01 C0 00 10 00 03 01` answered `00 D0 00 03 40 07 01`, parameters
`A0 0F 8A FF 32 FF` answered `A1 0F 8A FF 32 FF`, so we transmit on 0x740 and
the ECU on 0x300, block size 15, T1 100 ms, T3 5 ms.

## 6. Which CAN adapter to buy

**Recommendation: a candleLight/gs_usb-class USB-CAN adapter** — a
**CANable 2.0** or equivalent (Openlight Labs, MKS, CANtact Pro; roughly
30-60 EUR). Why:

* it is a plain USB device driven entirely from user space, so it needs no
  kernel extension and no SocketCAN — which this Mac does not have
  (`docs/03_tooling.md` section 6);
* `python-can`'s `gs_usb` backend talks to it directly, so the same command
  line works on the Mac and on Linux;
* CANable-class boards can also be reflashed with **slcan** firmware, which is
  a second, independent route (`--bus slcan:/dev/tty.usbmodemXXXX`) if the
  gs_usb path misbehaves;
* **choose a board whose 120 Ω termination can be switched or unsoldered.**
  The engine ECU is itself the bus's central termination (~66 Ω,
  `re/findings/hardware_prep.md` section 3.4), so on the bench the adapter's
  termination must be **off**; a fixed 120 Ω gives ≈43 Ω, below the ISO 11898
  minimum load. It usually still works at desk distances, but do not buy the
  fixed kind on purpose.

Extra Python packages, **not in `requirements.txt`** because nothing in this
repository needs them to run its tests:

```bash
brew install libusb && pip install 'python-can[gs-usb]'   # gs_usb route
pip install pyserial                                      # slcan route
#                                                           socketcand: nothing
```

**Fallback, and a good second opinion: the Raspberry Pi already described in
`pi_can_setup/`.** A Pi Zero W with an MCP2515 hat running `socketcand`
exposes the bus over TCP, and `python-can`'s `socketcand` backend needs **no
extra package at all**:

```bash
python3 logging/med9log.py probe --bus socketcand:raspberrypi.local:29536:can0
```

The Pi route also feeds SavvyCAN at the same time, which is the right tool for
watching raw traffic while the logger runs. Its disadvantages are latency
(WiFi) and one more thing to power. Buy the USB adapter; keep the Pi.

Do **not** buy an ELM327 clone. It cannot do TP2.0 channel setup at the frame
level, and a generic OBD dongle will not give raw 0x200 access.

## 7. Wiring

### On the bench

`re/findings/hardware_prep.md` section 3 is the reference and must be read
first — it has the T94 pinout (**CAN-L 67, CAN-H 68**, grounds 1/2/4/61,
+12 V 3/5/6 and 87/92, all COMMUNITY and to be rung out with a meter before
power), the current-limited-supply advice and the termination note.

Short summary for the CAN side only: twisted pair from the adapter to T94
pins 67/68, keep it under a metre, adapter termination **off**, and KL15 must
be present or the powertrain bus stays asleep
(`hardware_prep.md` section 3.4).

### In the car

The OBD-II socket (driver's footwell) carries the **diagnosis CAN**:

| OBD-II pin | Signal |
|---|---|
| 6 | CAN-H (diagnosis bus) |
| 14 | CAN-L (diagnosis bus) |
| 16 | +12 V permanent |
| 4, 5 | chassis / signal ground |

That is **not** the powertrain bus: the gateway (J533) sits between them and
forwards diagnostic traffic. TP2.0 channel setup to logical address 0x01 is
exactly the traffic it is built to forward, so the logger should work through
the OBD socket unchanged — **but that is an assumption until it is tried**
(HYPOTHESIS; `docs/03_tooling.md` section 6 already records that the gateway
*does* filter CCP 0x7C3/0x7C4). Ignition on, engine idling, nothing else
plugged into the OBD port.

## 8. First contact, step by step

Do these in order and stop at the first one that fails.

```bash
# 0. on the Mac, with nothing connected: prove the software works
python3 logging/ecu_sim.py --self-test
python3 logging/med9log.py probe --sim

# 1. adapter alone, ECU powered, KL15 on: is there any traffic at all?
python3 -m can.viewer -i gs_usb -c 0 -b 500000        # ctrl-C to stop
#    expect the cyclic powertrain frames of re/findings/can.md section 4,
#    every 10-25 ms.  Nothing at all => wiring, bitrate or KL15.

# 2. the first real answer: channel setup + session + TesterPresent
python3 logging/med9log.py probe --bus gs_usb:0
#    expect a few ms of round trip and
#    "we transmit on 0x740, module on 0x300".
#    This is the exit criterion of issue #3.

# 3. one known variable, cross-checked against VCDS group 001
python3 logging/med9log.py groups --bus gs_usb:0 1
#    field 1 is engine speed, field 2 coolant temperature; both must agree
#    with VCDS to within a count.

# 4. the real thing
python3 logging/med9log.py log --bus gs_usb:0 --seconds 70 \
        --session logging/sessions/wave_b_confirm.json \
        -o logs/2026-xx-xx_wave_b.csv
```

Expect roughly **40 samples/s in total** for a small record; a 48-byte record
costs eight CAN frames each way plus the ECU's own turnaround, so per-variable
rates fall proportionally. The CSV is long-form (section 1), so a slow variable
simply has fewer rows.

**A RAM snapshot takes about a minute** (63,932 bytes, 1,032 TransferData
blocks). Do the six of `re/findings/ram.md` section 9 in one sitting, one
command each, `--session-name` naming which of the six it is:

```bash
python3 logging/med9log.py dump --bus gs_usb:0 \
    --ranges logging/sessions/ram_snapshot.json \
    -o logging/sessions/snap_key-on.bin --session-name key-on
#  ...then idle, after-drive, key-cycle-1, key-cycle-2, key-cycle-3
python3 tools/ram_snapshot_diff.py logging/sessions/snap_*.json --free 64
```

### Things that will go wrong, and what they mean

| Symptom | Meaning |
|---|---|
| `no channel-setup answer ... on 0x201` | the ECU is not on this wire, KL15 is off, the bitrate is wrong, or CAN-H/CAN-L are swapped |
| setup refused with 0xD6-0xD8 | the module has no free channel: another tester (VCDS, a dongle) is connected, or a previous channel was never closed. Power-cycle KL15 |
| `7F 3E 12` to a TesterPresent | you sent `3E 01`. This ECU wants a **bare `3E`** (`kwp.md` section 12.2) |
| `7F 21 33` / `7F 2C 33` | the session lapsed. Redefine the dynamic ids after every `10 89` — a session change wipes them (`kwp.md` section 4.1) |
| `7F 35 31` | the range touches the protected window 0x7F9E3C-0x7FA47F. `med9log.py dump` splits around it automatically |
| `7F 27 37` | a wrong key armed the lockout timer. Wait, then give **one** key per seed |
| everything times out after a while | the TP2.0 channel died; the logger sends a channel test when idle, but a long pause plus a busy bus can still drop it. Reconnect |

## 9. If the gateway does not route 0x200

If step 2 above works on the bench but not through the OBD socket, the gateway
is not forwarding the channel-setup broadcast. In rough order of effort:

1. **Check the address.** The gateway forwards by *logical address*, and 0x01
   is the engine. `--address 0x01` is the default; try a VCDS "Engine"
   connection at the same time to confirm the car's gateway is awake at all.
2. **Watch what a known-good tester does.** With the Pi in the OBD socket and
   SavvyCAN recording (`pi_can_setup/`), connect VCDS to the engine and capture
   the channel setup. Whatever ids it negotiates, `--rx-id` will accept:
   `--rx-id 0x300` is only our proposal, and the ECU answers with the pair it
   wants. If the gateway rewrites them, the capture shows it.
3. **Go to the powertrain bus directly.** The Antrieb-CAN is reachable at the
   engine ECU connector (T94 pins 67/68, section 7) and at the gateway's own
   powertrain pins. This is the route that certainly works, because it is the
   bench route; the cost is running a cable into the engine bay. It is also
   the only way to reach CCP (0x7C3/0x7C4) and the only place the Pico
   flex-fuel node's frame will be visible (`docs/03_tooling.md` section 6).
4. **Do not** try to reconfigure the gateway. Its routing tables are coded per
   car and a wrong write is a warranty-shaped hole in the afternoon.

None of this is settled: **the whole "in the car" half of section 7 and all of
section 9 are HYPOTHESIS until the first OBD session happens.**


> **2026-09-17 (integration, after E7's dry-run).** Two clock fixes in `med9log.py`: with `--sim-patch`, the simulated node (`--sim-node`) now paces itself on the ECU's simulated clock instead of the wall clock times `--time-scale`, and a `--sim-seconds` run ends when the ECU's own clock reaches the target (wall deadline kept as a 5x safety cap). Before, a catch-up cut short by `PatchRunner`'s wall budget made the node outrun the ECU ("~10 frames/s" read 15.9) and a step written in ECU seconds could end before its fault was due. `bench_rehearsal.py --fresh-eeprom` passes 69/69 with both.

## 9. Bench rehearsal — running the patch inside the simulator (E4, 2026-09-17)

Every bench procedure in `patches/ff_fuel/test/` was written before any
hardware existed and none of them had been executed. They can now all be run
against `logging/ecu_sim.py`, which applies the patch to a temporary image and
drives **the patch's own hooks** on a simulated 10 ms raster, with a simulated
Pico on the same in-process bus and a simulated SPI EEPROM behind the block
manager. Nothing about this is a measurement: it debugs the procedure, the
session file and the tolerances, not the ECU.

### The three commands

```bash
# 1. the whole chain in one process: node -> ECU -> logger
./.venv/bin/python3 logging/med9log.py log --sim \
    --sim-patch patches/ff_fuel --eeprom work/eeprom.bin \
    --sim-node --node-e-pct 85 \
    --session logging/sessions/ff_fuel.json --patch patches/ff_fuel/patch.json \
    --sim-seconds 60 --time-scale 5 -o work/rehearsal.csv

# 2. the measuring blocks, without a logger
./.venv/bin/python3 logging/med9log.py groups --sim \
    --sim-patch patches/ff_fuel --eeprom work/eeprom.bin 111 108

# 3. all fourteen procedure steps, graded (eleven before G5 added dtc and pid52, H3 pid52_obd)
./.venv/bin/python3 logging/bench_rehearsal.py --fresh-eeprom
```

`bench_rehearsal.py` writes `logging/samples/ff_fuel_sim_*.csv` — every one of
them carries `# simulated: true` — and prints a table of the numbered checks
of `logging/sessions/ff_fuel.json`. Those files are **never** part of a bench
comparison set (C3's rule); they are there so a reader can see what a passing
run looks like.

The node can also be run on its own, which is what you want on the real bench
before the ECU is involved:

```bash
./.venv/bin/python3 logging/ethanol_frame_send.py --bus gs_usb:0 --e-pct 85
./.venv/bin/python3 logging/ethanol_frame_send.py --bus gs_usb:0 \
    --e-ramp 0:85:40 --fault-after 40 --status 2
```

### Wall-clock time and simulated time

The 10 ms hook costs about 0.5 ms of host CPU, so the simulator can run the
ECU faster than real time. `--time-scale X` asks for X simulated seconds per
wall second (5 is comfortable on an M2; the segment hook and the NVM pump add
roughly another half).

* **`--seconds` is wall-clock**, because that is what the logger measures and
  what the CSV's `time_s` column holds. **`--sim-seconds` is ECU seconds** and
  simply divides by the scale — it is the knob a procedure written in ECU
  seconds wants.
* The ethanol node's clock is scaled with the ECU's, so 10 Hz stays 10 Hz *in
  ECU time*, and `--node-stop-after` / `--node-fault-after` / `--node-e-ramp`
  are all in ECU seconds.
* The simulator answers TP2.0 from the same thread that runs the hooks, so a
  catch-up is bounded by a **wall-clock budget** (5 ms, `MAX_CATCHUP_WALL_S`)
  as well as by simulated time. Without it a 0.5 s catch-up at
  `--time-scale 5` is fifty activations — 40 ms of host CPU idle, more than
  twice that with a test suite running beside it — and the tester loses its
  channel after T1 = 100 ms × 4 tries. `PatchRunner.lagged` counts how often
  the budget cut a catch-up short; `ecu_sim.py` prints it on exit.
* Every animated cell and every hook read the same simulated instant: with a
  patch running, "now" is the runner's own clock, which advances in whole
  10 ms activations and can fall **behind** the wall clock on a slow host.
  Nothing desynchronises when it does; the run is simply slower than the ECU
  would be, and `# sim_time_scale:` in the header records what was asked for.
* Therefore **every rate criterion must be evaluated against the ECU's own
  clock, not against `time_s`** — the live raster counter (100 per second,
  `re/findings/scheduler.md` section 11) is in the session file for exactly
  this. `logging/bench_rehearsal.py::ecu_slope` is the two-line helper, and
  the same division is what makes "2 %/s" mean anything in a scaled log.

### Comparing two runs — the missing line of the E0 recipe

Two logs are two separate power-ups, and the tens of milliseconds between
"the ECU powered on" and "the tester finished the DDLI setup" are not the same
twice. On the rpm ramp that offset alone is worth a third of the `nmot_w`
budget in `patches/ff_fuel/test/tolerance.json` and says nothing about the
software. Both logs carry the live raster counter, so the offset is
**measurable**: shift the candidate's time axis by
`(raster_cand - raster_base) / 100` before comparing.
`bench_rehearsal.py::_align_on_raster` did it, and with that one step
`tools/logcmp.py` passes on identical animation and fails on `rk_fuel_mass`
alone when `rk` is perturbed by 3 %. A bench comparison of two drives needs
the same step.

**2026-09-22 (F2):** that step is now `tools/logcmp.py --align-on
raster_setA_10ms_count:100`, with `--uncovered report` for the variables
`tolerance.json` does not name and `logcmp.py derive run1 run2` for the
tolerances themselves; `bench_rehearsal.py` calls the tool's `align_on` and
`compare(..., uncovered="report")` instead of its own copies, and
`--fresh-eeprom` is still **69/69** (docs/07 §§5.4-5.5).

### What the simulator now models, and what it still does not

| Modelled | Where |
|---|---|
| the patch's 10 ms raster hook of the live task set | `ecu_sim.PatchRunner` |
| the segment hook, at `rpm * cylinders / 120`, with `rk` re-produced upstream first | same |
| the stock NVM queue pump, which both 10 ms background tasks call | same |
| TouCAN C message buffer 6 fed from the python-can bus | `emu/toucan.py` |
| the QSPI queue and an M95160 on PCS0, with a file behind it | `emu/qspi_eeprom.py` |
| the EEP_CONF block manager's start-up read and its device pointers | same |
| **not** the ignition stub at 0x41D40C | it is a mid-function trampoline; `ff_dzw_e` is produced by the 10 ms half anyway |
| **not** any other OS task | the stock baseline runs only what the hook sites replace (`--sim-stock-tasks`) |
| the firmware's own one-shot init entries at power-on | `ecu_sim.INIT_ENTRIES` (F3; `boot.md` section 6.5) |
| a running PowerPC time base for `read_time_base` | `emu/time_base.py` (F3) — without it the real `27 01` never returns |
| the firmware's flash CRC-32 task, five activations per background loop T_bg (G5; was one per 10 ms, F3) | `ecu_sim.FlashCrcTask`, `--flash-crc [T_BG_MS]` / `med9log --sim-flash-crc` |
| the fault services 18 / 17 / 14 over the firmware's RAM fault memory; the seeding and the post-clear erase are labelled models | `ecu_sim.DtcStore`, `--seed-dtc` (G5) |
| the generic-OBD CAN route: 0x7DF/0x7E0 single frames into TouCAN C MB15, the firmware's own ISR, ISO 15765-2 parser, connection layer and dispatcher, answers out of MB13 on 0x7E8; the TouCAN buffers, the `mftb` redirect and the raster calls are the harness (H3) | `emu/obd_can.py`, `--obd-can`, `logging/obd_client.py` |
| **not** every other init-table entry | 1,028 of them, most touching peripherals the emulator does not model; the residue is a table in `ecu_sim.py`'s docstring |

> **2026-09-22 (F3, #20/#38).** `power_on` no longer hand-seeds the KWP
> security cells: it calls ten entries of the firmware's own init table, so
> `kwp_sec_lfsr_rounds` is 0 until a real `27 01` writes 5 and `nvm_mode`
> reads 1. `--flash-crc` runs the flash checksum task; for the stock dump it
> publishes **0x5562139F** to 0x7F9178/0x7F917A after 24,627 activations
> (246 simulated seconds), and `logging/sessions/flash_crc.json` logs the
> cursor and the running register, which move every activation.

> **2026-09-24 (G5, #20).** The CRC period is G3's, not a raster: `--flash-crc
> [T_BG_MS]` runs five activations (500 bytes) and one tick of the loop counter
> 0x7FD70C per background loop T_bg, and the value appears in loop 4,926 =
> 4,926 × T_bg (default 50 ms = 246 s, a HYPOTHESIS inside the VERIFIED-STATIC
> bound 0.51-300.75 ms, i.e. 2.5 s-24.7 min on a car); `--flash-crc-warm`
> models the warm ECU (state 7, nothing moves), `--print-flash-crc --sim-patch
> DIR` recomputes a patched image's value. `bench_rehearsal.py` now has
> thirteen steps: `dtc` (read, clear, read back, reconnect) after the FAULT
> rows and `pid52` (enable, reconnect, `01 40` advertises 0x52).

> **2026-09-24 (H3, #48/#39).** **The scan-tool route.** `ecu_sim.py
> --obd-can` (or `Med9Handlers(obd_can=True)`) answers ISO 15765-4 requests on
> **0x7DF** with single frames on **0x7E8** — `02 01 00 00 00 00 00 00` →
> `06 41 00 b0 b1 b2 b3 00` — and it is **not a model of the protocol**: the
> frame goes into the emulated module-C buffer 15 and the firmware's own TouCAN
> ISR, ISO 15765-2 parser (`isotp_rx_indication` 0x1420A0), connection layer,
> `kwp_service_dispatch` and `isotp_transmit` produce the answer in buffer 13
> (`re/findings/obd.md` §11), after the firmware's init entries 44 and 281. What
> *is* the harness, and labelled as such in `emu/obd_can.py`: the TouCAN buffers
> and IFLAG bits (a transmit is "acknowledged" at once), the 44 `mftb` reads of
> the diagnostic module redirected to the virtual time base, and the 2 ms / 10
> ms raster calls, with idle stretches compressed. What that means for a test:
> the request runs in **session 6** (tester address 0x33, set by the firmware's
> h2 walk), only single frames with DLC 8 are accepted on 0x7DF, **0x7E0 is never
> answered** (its connection gate is `li r3,0`), anything unsupported is
> **silence** rather than a negative response, and the connection closes 5
> simulated seconds after the last answer — a "reconnect" (the moment the
> support bitmaps are rebuilt) is a request after more than 5 s of silence.
> The client is `logging/obd_client.py` (`--sim [--sim-patch DIR] 01 00`,
> `--pids`, `--bus slcan:...` for the car), the rehearsal step is `pid52_obd`
> (`bench_rehearsal.py --only pid52_obd`, 5 checks: switch off = stock frames,
> session 6, bitmap per connection, reconnect, `03 41 52 A` for E0/E85). One
> simulator limit: the two routes share `kwp_session_current`, and a TP2.0
> channel opened while an OBD connection is alive inherits session 6, because
> the modelled TP2.0 side does not run the firmware's connection-open that
> would rewrite 0x803D6A / 0x7F804B (on the ECU a 0x7DF frame during a TP2.0
> connection ends that connection instead, `obd_func_rx_ind` 0x0B5534).
