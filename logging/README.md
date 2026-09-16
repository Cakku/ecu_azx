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

Tolerances belong next to the thing they judge: the bench tolerances for a
patch live in `patches/<name>/test/`, not here.

## 3. Synthetic samples

`samples/` holds a deterministic 10 s bench scenario (idle, a trapezoidal load
step from 4 s to 8 s, idle) used by `tests/test_logcmp.py`:

| File | What it is |
|---|---|
| `baseline.csv` | the stock run |
| `candidate_ok.csv` | a repeat run: sensor noise and a 17 ms time skew, inside tolerance |
| `candidate_bad.csv` | the same run with `ti_1_w` deliberately +8 % |
| `tolerance.json` | limits for this scenario |

```bash
python3 tools/logcmp.py logging/samples/baseline.csv logging/samples/candidate_ok.csv \
    -t logging/samples/tolerance.json          # RESULT: OK,     exit 0
python3 tools/logcmp.py logging/samples/baseline.csv logging/samples/candidate_bad.csv \
    -t logging/samples/tolerance.json          # FAIL ti_1_w,    exit 1
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
python3 logging/ecu_sim.py --self-test          # 18 services, RESULT: PASS
python3 logging/med9log.py probe  --sim
python3 logging/med9log.py groups --sim 1 3 106 231
python3 logging/med9log.py log    --sim --seconds 2 \
        --session logging/sessions/wave_b_confirm.json -o work/sim.csv
python3 logging/med9log.py dump   --sim --ranges logging/sessions/ram_snapshot.json \
        -o work/sim_snap.bin --session-name key-on
python3 -m unittest tests.test_med9kwp           # 43 tests, no hardware
```

`--sim` runs the simulator in this process on a python-can `virtual` bus.
Nothing it produces is a recording of an ECU, and both the CSV and the
snapshot manifest say so in a `# simulated:` / `"simulated"` line — **never put
a simulated snapshot into the #23 comparison set.**

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
47 chunks it refuses and says so. `wave_b_confirm.json`'s 24 variables need
18 chunks and 48 bytes per sample.

| File | The question it answers |
|---|---|
| `sessions/wave_b_confirm.json` | every row of #44, plus the three raster counters that settle the task periods |
| `sessions/can_bc_check.json` | do TouCAN modules B and C share one wire? (`can.md` section 3) |
| `sessions/flash1_counter.json` | did Flash 1 run, and at what rate? (`patches/ff_counter/test/procedure.md`) |
| `sessions/ram_snapshot.json` | the *ranges* for `dump`, from brief C2 |

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

