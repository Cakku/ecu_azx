# Flash 1 bench procedure (issue #27)

Written **before** the flash, as `docs/04_re_guidelines.md` section 7 requires:
what will be read, what the numbers must be, and what each possible outcome
means. Nothing below has been run on an ECU — every expectation here is a
prediction, and the log is what turns it into a fact.

> **Do not flash the image built from the current `patch.json`.** Its RAM block
> is the placeholder 0x807F00, which brief C2 has since shown to be *inside*
> the destination of the flash driver the ECU copies to 0x804800 and runs
> during a programming session (README.md, PENDING #23). Brief C2's block
> 0x7FFB00/0x100 has to replace it first; `tools/patch_apply.py` prints a
> warning on every run while `"ram_status"` is not `"verified"`.

## 0. Prerequisites

| Item | Status |
|---|---|
| RAM block proven unused across ignition cycles | **blocked on #23 / brief C2.** C2's static half proposes 0x7FFB00/0x100 and rules out the 0x807F00 placeholder; the dynamic half (RAM dumps across ignition cycles) is still outstanding |
| A logger that can read arbitrary RAM (KWP2000 DDLI, service 0x2C + 0x21) | **blocked on #20**; the protocol itself is settled in `re/findings/kwp.md` sections 4 and 8 |
| KESSv2 flashing checklist | `docs/04_re_guidelines.md` section 6, issue #26 |
| A stock baseline log of the scenario in section 3 | record it *before* flashing |

Without the logger, sections 2 and 3 can also be done with VCDS advanced
measuring blocks for the stock variables, but the counter itself needs a
DDLI — it is not in any stock measuring block.

## 1. What is in the ECU after the flash

| Address | Type | Meaning |
|---|---|---|
| `PATCH_RAM + 0x00` | u32 | `ff_ticks` — activations of `task_100ms` since power-up |
| `PATCH_RAM + 0x04` | u16 | `ff_alive` — `0xFC01` once our code has run, otherwise untouched |
| `PATCH_RAM + 0x06` | u16 | `ff_reserved` — always 0 |

`PATCH_RAM` is whatever `patch.json`'s `build.ram` says at flash time (0x807F00
while the placeholder stands). One flash word changed: `0x12067C`,
`4B FF E9 B1` -> `48 02 F9 85`.

## 2. Define the DDLI and read the counter

`re/findings/kwp.md` section 4 has the exact request format this firmware
accepts, and section 8 recipe A the session handling. One entry of six bytes
from `PATCH_RAM` puts the counter and the alive pattern in the same response.
With the placeholder `PATCH_RAM = 0x807F00` (`a2 a1 a0` = `80 7F 00`):

```
10 89                            ; session 5: 0x21/0x2C, no security needed
2C F0 04                         ; clear F0 first - redefining without this is NRC 0x22
2C F0 03 01 06 80 7F 00          ; pos 1, 6 bytes @ 0x807F00   -> 6C F0
loop:  21 F0  -> 61 F0 <t3 t2 t1 t0> <a1 a0>
       3E 02  every ~2 s         ; keep alive
```

Notes from `kwp.md` that matter here: the DDLI address is 24-bit, which covers
all of RAM; `<pos>` must be 1 for the first (and only) entry; there is **no
0x23 readMemoryByAddress** in this firmware, so DDLI is the only way to read an
arbitrary address at rate.

The address bytes are the last three of the request, so they follow
`build.ram`: `80 7F 00` for the placeholder, **`7F FB 00`** once brief C2's
block 0x7FFB00 is adopted. Read them out of `patch.json` rather than from here
— this file will be wrong the day the block moves again.

**Sanity checks, in this order:**

1. `ff_alive == 0xFC01`. If it is not, our code has never run: the hook did not
   take, or the flash write did not land. Stop and re-read the image.
2. `ff_ticks` increases monotonically while the ignition is on.
3. `ff_ticks` **stops** increasing when the engine is off but KL15 is on only
   if `task_100ms` stops; it is a time raster, so it should keep counting.
4. Power-cycle: `ff_ticks` restarts near 0 and `ff_alive` is 0xFC01 again. That
   is the cold-start path in `src/ff_counter.c` doing its job.

A u32 read over KWP is not atomic with respect to a 10 Hz increment, so a
single sample can tear in its low byte. Take the slope over many samples
(section 4) rather than trusting two readings.

## 3. Scenario and expected log lines

Same scenario for the stock baseline and the Flash-1 run, engine at operating
temperature:

```
0-30 s     idle, no load
30-40 s    one load step (A/C on, or a steady 2000 rpm hold)
40-70 s    idle again
```

Log at least: `nmot_w`, `rl_for_fuel`, `ti_sum`, `tmot_w`, `prist_w`,
`prsoll_w`, `zwist_display_b1`, `dwi_inj_angle` (addresses in
`tolerance.json`), plus `ff_ticks` and `ff_alive` on the Flash-1 run.

Expected lines in the Flash-1 log, in the format of `logging/README.md`
section 1 — **prediction, not a recording**, for the 100 ms hypothesis:

```csv
# session: bench, Flash 1 (ff_counter), engine idling
# ecu: 03H906032 / 1037382557
# dump_sha256: <sha256 of the flashed image, from work/ff_counter.sha256>
# transport: KWP2000 0x2C/0x21 over TP2.0
time_s,var,value,unit
0.000,ff_alive,64513,-
0.000,ff_ticks,1483,-
0.200,ff_ticks,1485,-
0.400,ff_ticks,1487,-
0.600,ff_ticks,1489,-
0.800,ff_ticks,1491,-
1.000,ff_ticks,1493,-
```

i.e. **+2 counts per 200 ms sample interval, +10 per second**.

## 4. The period decision — this flash is also the measurement

`re/findings/scheduler.md` section 5.3 records 100 ms as a HYPOTHESIS; only
"<= 150 ms" is VERIFIED-STATIC, and section 10 lists the 100 ms / 1000 ms
versus 150 ms / 1500 ms question as open. Fit a straight line to `ff_ticks`
against `time_s` over at least 60 s of steady running:

| Slope | Period | Meaning |
|---|---|---|
| 10.0 +- 0.2 /s | 100 ms | the hypothesis of scheduler.md 5.3 holds — upgrade it to VERIFIED-DYNAMIC |
| 6.67 +- 0.15 /s | 150 ms | **the hypothesis is wrong.** The raster is 150 ms, and every "100 ms" and "1000 ms" in `scheduler.md` sections 4, 5.3 and 8 has to be re-derived; so does every latency estimate that depends on it (B6 options C/D, `rail.md` section 7) |
| anything else | unknown | the task is not a fixed-period raster, or it is activated from a source the static scan did not see (`scheduler.md` section 10) — record the actual slope and reopen the question |

The two candidates are 50 % apart, so the logger's own timebase does not have
to be good for this to decide. Write the result into `re/findings/scheduler.md`
as a dated note and tick the matching box in issue #44.

## 5. Regression: nothing else changed

```bash
python3 tools/logcmp.py patches/ff_counter/test/baseline.csv work/flash1.csv \
        -t patches/ff_counter/test/tolerance.json --json work/logcmp.json
```

Exit status 0 is the exit criterion of issue #27's "log comparison against
baseline shows no other change". Do **not** pass `--strict`: `ff_ticks` and
`ff_alive` exist only in the candidate log and are listed, not compared.

`baseline.csv` is the stock run of section 3 and is recorded on the bench, so
it is not in this directory yet. Record it before flashing — after the flash
there is no way back to a stock baseline except reflashing.

## 6. If something is wrong

| Symptom | First thing to check |
|---|---|
| `ff_alive` never becomes 0xFC01 | read the ECU back and `tools/bindiff.py` it against `work/ff_counter.bin`; KESS may have "corrected" a checksum we already had right (docs/06 section 6) |
| Counter runs but the engine misbehaves | the RAM block is not free after all — the placeholder is unproven. Reflash stock immediately and finish #23 |
| Counter increments in bursts or stalls | `task_100ms` is not a fixed-period raster; record the pattern, it answers `scheduler.md` section 10 |
| Any logged stock variable outside tolerance | before blaming the patch, re-run the stock baseline: the tolerances in `tolerance.json` are predictions, not measured repeatability |

## 7. Roll back

Write the original read back with KESSv2 (`docs/06_patch_pipeline.md`
section 6). `data/passat_azx_ori.bin` is that file and its SHA-256 is
`b15590d3f1874ace3125c5d047c09a686db9b8bb498187663539ebab205609b3`.
