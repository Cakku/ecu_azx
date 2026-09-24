# Flash 1 bench procedure (issue #27)

Written **before** the flash, as `docs/04_re_guidelines.md` section 7 requires:
what will be read, what the numbers must be, and what each possible outcome
means. Nothing below has been run on an ECU — every expectation here is a
prediction, and the log is what turns it into a fact.

> **Rewritten 2026-09-22 (brief F1, issues #27 / #44).** Flash 1 now hooks the
> 10 ms raster of **both** task sets — 0x432940 in set A (**on-chip flash**)
> and 0x12067C in set B — and records which one ran in `ff_src_seen`. Sections
> 3 and 4 are the decision table that follows; section 1 has the new RAM
> layout. The variant `make HOOKS=external` still builds the old single-word
> patch and is discussed in section 4.5.

> **RAM block (integration 2026-09-16):** `build.ram` is **0x7FFB00** (0x100 B),
> the block `re/findings/ram.md` §8.1 recommends — VERIFIED-STATIC that nothing
> in the image references it, **dynamic confirmation pending** (the runtime
> half of #23: RequestUpload snapshots across key cycles, compared with
> `tools/ram_snapshot_diff.py`). `tools/patch_apply.py` prints a warning on
> every run while `"ram_status"` is not `"verified"`; do not flash before
> those snapshots are done. (Brief C1 was written against the placeholder
> 0x807F00, which C2 showed to be inside the flash driver's programming copy.)

## 0. Prerequisites

| Item | Status |
|---|---|
| RAM block proven unused across ignition cycles | **static half done (C2, merged 2026-09-16): 0x7FFB00/0x100 has no static reference of any kind.** The dynamic half (RAM dumps across ignition cycles, `tools/ram_snapshot_diff.py`) is still outstanding |
| A logger that can read arbitrary RAM (KWP2000 DDLI, service 0x2C + 0x21) | **blocked on #20**; the protocol itself is settled in `re/findings/kwp.md` sections 4 and 8 |
| KESSv2 flashing checklist | `docs/04_re_guidelines.md` section 6, issue #26 |
| **Flash 0 done, and its on-chip read-back recorded** | `re/findings/flash_programming.md` §7.2 item 1. This is now an input to section 4, not an afterthought: without it, a counter that does not move has two explanations instead of one |
| A stock baseline log of the scenario in section 3 | record it *before* flashing |

> **2026-09-24 (ruling, Carlo; dated note H4):** the #23 snapshots gate
> **every** write, Flash 0 included — not only the patches whose `ram_status`
> is `static` (`docs/07` §6.4, `docs/08` step 4). So by the time this
> procedure runs the dynamic half of the first row is done; if it is not,
> no flash of any kind has happened yet either.

Without the logger, sections 2 and 3 can also be done with VCDS advanced
measuring blocks for the stock variables, but the counter itself needs a
DDLI — it is not in any stock measuring block.

## 1. What is in the ECU after the flash

| Address | Type | Meaning |
|---|---|---|
| `PATCH_RAM + 0x00` | u32 | `ff_ticks` — activations of the live 10 ms raster since power-up |
| `PATCH_RAM + 0x04` | u16 | `ff_alive` — `0xFC01` once our code has run, otherwise untouched |
| `PATCH_RAM + 0x06` | u8 | `ff_src_seen` — which hook has run: **1 = set A, 2 = set B, 3 = both** |
| `PATCH_RAM + 0x07` | u8 | `ff_reserved` — always 0 |

`PATCH_RAM` is whatever `patch.json`'s `build.ram` says at flash time (0x7FFB00
since C2's block was adopted on 2026-09-16). **Two** flash words changed:

| Site | Task set | Old | New | Flash |
|---|---|---|---|---|
| `0x432940` | **A**, 10 ms (`task_100ms_int` 0x4328E4) | `4B C8 B0 A5` | `4B D1 D6 C1` | **on-chip** 0x404000-0x47FFFF |
| `0x12067C` | **B**, 10 ms (`task_100ms` 0x1205A0) | `4B FF E9 B1` | `48 02 F9 A5` | external |

The new words come from `patch.json`; read them from there rather than from
here, and confirm them in the read-back with
`tools/bindiff.py <read-back> work/ff_counter.bin -p patches/ff_counter/patch.json`.

## 2. Define the DDLI and read the counter

`re/findings/kwp.md` section 4 has the exact request format this firmware
accepts, and section 8 recipe A the session handling. One entry of eight bytes
from `PATCH_RAM` puts the counter, the alive pattern and the source byte in the
same response. With `PATCH_RAM = 0x7FFB00` (`a2 a1 a0` = `7F FB 00`):

```
10 89                            ; session 5: 0x21/0x2C, no security needed
2C F0 04                         ; clear F0 first - redefining without this is NRC 0x22
2C F0 03 01 08 7F FB 00          ; pos 1, 8 bytes @ 0x7FFB00   -> 6C F0
loop:  21 F0  -> 61 F0 <t3 t2 t1 t0> <a1 a0> <src> <rsv>
       3E 02  every ~2 s         ; keep alive
```

Notes from `kwp.md` that matter here: the DDLI address is 24-bit, which covers
all of RAM; `<pos>` must be 1 for the first (and only) entry; there is **no**
0x23 readMemoryByAddress in this firmware, so DDLI is the only way to read an
arbitrary address at rate.

The address bytes are the last three of the request, so they follow
`build.ram`: **`7F FB 00`** for the current block 0x7FFB00. Read them out of
`patch.json` rather than from here — this file will be wrong the day the block
moves again.

**Sanity checks, in this order:**

1. `ff_alive == 0xFC01`. If it is not, neither hook has ever run. That is now a
   *statement about the flash*, not about the task sets, because both sets are
   hooked: go to section 4, outcome **D** or **E**.
2. `ff_src_seen` is 1, 2 or 3 — never 0 once `ff_alive` is set, and never any
   other value. Anything else means something is writing our block.
3. `ff_ticks` increases monotonically while the ignition is on.
4. `ff_ticks` keeps counting with the engine off and KL15 on: it is a time
   raster, not an engine-synchronous one.
5. Power-cycle: `ff_ticks` restarts near 0, `ff_alive` is 0xFC01 again and
   `ff_src_seen` is re-derived from scratch. That is the cold-start path in
   `src/ff_counter.c` doing its job.

A u32 read over KWP is not atomic with respect to a 100 Hz increment, so a
single sample can tear in its low byte. Take the slope over many samples
(section 4) rather than trusting two readings.

## 3. What to read, and in what order

Same scenario for the stock baseline and the Flash-1 run, engine at operating
temperature:

```
0-30 s     idle, no load
30-40 s    one load step (A/C on, or a steady 2000 rpm hold)
40-70 s    idle again
```

> **Engine-off bench baselines, by ruling (Carlo, 2026-09-24; dated note H4,
> G6 open question 4).** A bench mule has no crank or cam signal and stays
> "engine not running" (`re/findings/hardware_prep.md` §3.5), so the scenario
> above cannot be run there. On the bench the stock baseline and the Flash-1
> run are both taken **engine-off: KL15 on, no crank**, for the same 70 s —
> the raster counters still run (§2 check 4), which is all §3a, §3c, §4 and
> §5 need. The idle-plus-load-step comparison with a running engine is
> issue **#28**, on the car.

The bench day has **three** measurements, and section 4 needs all three. Take
them in this order, because each one narrows what the next can mean:

### 3a. The five stock raster counters (before anything else)

They are stock cells, so they can be read on the **stock** ECU as well —
`logging/sessions/wave_b_confirm.json` logs exactly these. u32 each; expect
every rate about **0.25 % low**, because the OS truncates the 285.714 ns tick
to 285 ns (`re/findings/scheduler.md` §11.1).

| Cell | Raster | Expected if that set is live |
|---|---|---|
| 0x7FD75C | set A, 1 ms | +1000 /s |
| 0x7FD754 | set A, 10 ms | +100 /s |
| 0x7FD760 | set B, 1 ms | +1000 /s |
| 0x7FD778 | set B, 2 ms | +500 /s |
| 0x7FD758 | set B, 10 ms | +100 /s |

Whichever set's counters move is the live set. `scheduler.md` §11.8 predicts
**set A moves and the three set-B cells stay frozen**; that prediction is
falsifiable here, independently of our patch.

### 3b. The Flash 0 on-chip read-back (recorded, not measured today)

`re/findings/flash_programming.md` §7.2 item 1: after Flash 0, read back
**0x404000-0x47FFFF** and `bindiff` it against the file that was written.

* **written** — the bytes match what was flashed: KESSv2 drives the ECU's own
  programming route for the on-chip array, and our set-A hook word is really in
  the ECU;
* **stock** — the array still holds the factory bytes: KESS silently skipped
  it, and the set-A hook word was never written, whatever the tool reported.

Record which of the two it was. Without it, outcomes **D** and **E** of section
4 cannot be told apart.

> **Correction 2026-09-24 (integration, from brief G6, `docs/08_bench_playbook.md`
> step 5).** The Flash 0 read-back **cannot** tell "written" from "stock": the
> file written in Flash 0 *is* the stock image, so the on-chip array reads back
> identical whether KESSv2 wrote it or skipped it. Keep the Flash 0 read-back as
> the test of the read-back tool itself and of the `5A 5A` marker, but take
> column **3b** of the decision table from **Flash 1's own read-back of
> 0x404000-0x47FFFF** `bindiff`ed against `work/ff_counter.bin`: *written* = the
> set-A hook word at 0x432940 is in the ECU, *stock* = it is not (`docs/07` §3.4
> already requires that read-back). Rows A-I are unchanged in meaning.

### 3c. The counter and the source byte

Log at least: `nmot_w`, `rl_for_fuel`, `ti_sum`, `tmot_w`, `prist_w`,
`prsoll_w`, `zwist_display_b1`, `dwi_inj_angle` (addresses in
`tolerance.json`), plus `ff_ticks`, `ff_alive` and `ff_src_seen` on the Flash-1
run. `logging/sessions/flash1_counter.json` is that variable list.

Expected lines in the Flash-1 log, in the format of `logging/README.md`
section 1 — **prediction, not a recording**, for the 10 ms raster settled by C4
(`re/findings/scheduler.md` §11) with **task set A** live (§11.8):

```csv
# session: bench, Flash 1 (ff_counter), engine idling
# ecu: 03H906032 / 1037382557
# dump_sha256: <sha256 of the flashed image, from work/ff_counter.sha256>
# transport: KWP2000 0x2C/0x21 over TP2.0
time_s,var,value,unit
0.000,ff_alive,64513,-
0.000,ff_src_seen,1,-
0.000,ff_ticks,14830,-
0.200,ff_ticks,14850,-
0.400,ff_ticks,14870,-
0.600,ff_ticks,14890,-
0.800,ff_ticks,14910,-
1.000,ff_ticks,14930,-
```

i.e. **+20 counts per 200 ms sample interval, +100 per second** (until
2026-09-16 this read +10 per second, from the 100 ms hypothesis).

## 4. The decision table — this flash is also the measurement

> *2026-09-24 (H4): on the bench the log this table reads is the engine-off
> KL15 run of §3's note; the running-engine repeat is #28.*

> **Rewritten 2026-09-22 after brief E1 (issue #34) and brief F1.** C4 settled
> the *period*: 0x1205A0 and 0x4328E4 are both **10 ms** rasters
> (VERIFIED-STATIC from the activation chain, VERIFIED-DYNAMIC from
> `emu/os_clock.py`). E1 then settled the *live set* statically —
> `scheduler.md` §11.8: **task set A**, because `prsoll` and `zwstt` are
> produced only by set-A processes and the engine cannot run without them.
> What this flash measures is therefore no longer "which set", but **the
> dynamic confirmation of §11.8 and, at the same time, whether the OBD route
> writes the on-chip flash** — two answers from one 60-second log.

**Method, unchanged from C1:** fit a straight line to `ff_ticks` against
`time_s` over at least 60 s of steady running and keep the **±2 %** tolerance.
The candidate slopes are an order of magnitude apart, so the logger's own
timebase does not have to be good. And **compare against two stock runs
first**: record the scenario twice on the stock image before flashing, so that
`tolerance.json`'s limits are measured repeatability rather than predictions
(section 5, `docs/05_flexfuel_design.md` E0).

Read the row that matches all three measurements of section 3:

| # | 3a stock counters | 3b on-chip read-back | 3c slope | 3c `ff_src_seen` | Verdict |
|---|---|---|---|---|---|
| **A** | set A moving | written | 100 ±2 /s | **1** | **The expected outcome.** Set A is live and our on-chip hook word runs: upgrade `scheduler.md` §11.8 from VERIFIED-STATIC to **VERIFIED-DYNAMIC**, record the 10 ms raster as confirmed end to end, tick the task-period row of #44 — and note that the ECU's on-chip flash **was** written over OBD, which settles #32 for `patches/ff_fuel` as well |
| **B** | set B moving | either | 100 ±2 /s | **2** | **§11.8 is WRONG — say so loudly.** Task set B is the live one, which contradicts a reachability argument this project has built on: `prsoll` and `zwstt` would then have no producer, so either the walk missed an indirect call (§11.8's first caveat) or the set switch at 0x11DA64 happens later than we think. Stop, reopen #34 and #44 with the log attached, and re-read `re/findings/scheduler.md` §11.8 before any further flash. `patches/ff_fuel`'s E1/E2/E5 features assume set A and would all be dead |
| **C** | one set moving | written | 200 ±4 /s | **3** | Both hooks fire, so both sets run in the live configuration. Neither §11.7 nor §11.8 expects this; the counter is still correct (it counts activations, not periods), but every "once per 10 ms" claim in the patches has to be re-checked. Record the two slopes separately by power-cycling and reading `ff_src_seen` early, then reopen #44 |
| **D** | **set A moving** | **stock bytes** | **0** | 0 (`ff_alive` never set) | **KESSv2 skipped the on-chip array**, so the set-A word was never written — and because set A is the live set, the external word at 0x12067C is *also* never executed. **No counter patch of any kind can run on this ECU until the on-chip route is available**, and the same is true of six of `ff_fuel`'s eight hooks: the fuel, ignition, both start and the rail stubs all live in 0x404000-0x47FFFF and have **no external-flash alternative** (`flash_programming.md` §7.3). This is not a patch bug and not a hook-site bug; it is a tool-capability result. Next step is §7.3's ladder: a BDM/K-TAG write, or a tool that drives the firmware's own service for that range. Do **not** rebuild the patch and do **not** move the hook — there is nowhere to move it to |
| **E** | either | **written** | **0** | 0 (`ff_alive` never set) | **Stop and investigate before any other flash.** The bytes are in the ECU and neither stub ran: the read-back and the execution disagree, which means something we do not understand (an execution alias that is not the one we patched, a second copy of the raster task, a checksum or protection check we have not found, or a bad `bindiff` baseline). Re-read the whole image, `bindiff` it against `work/ff_counter.bin`, and disassemble 0x432940 and 0x12067C **out of the read-back** before drawing any conclusion |
| **F** | set A moving | written | 100 ±2 /s | **2** | The external hook runs although set A is the live set — mutually contradictory. Suspect the logger (wrong address, stale DDLI) before the ECU: re-read `ff_src_seen` after a power cycle |
| **G** | neither set moving | either | any | any | The logger is reading the wrong place, or the ECU is not running the application (check the `5A 5A` marker at file 0x1E2500, `flash_programming.md` §7.2 item 2). Fix that before reading anything else |
| **H** | any | any | 10 ±0.2 /s | any | C4's tick chain is wrong somewhere after all, and the old 100 ms reading was right. Stop and reopen #44 with the log attached |
| **I** | any | any | anything else | any | The task is not a fixed-period raster, or a divider we did not see is in the path (`scheduler.md` §11.3). Record the actual slope and the pattern (bursts? stalls?) and reopen the question |

Write the result into `re/findings/scheduler.md` as a dated note, and into the
issue (#44 for the period and the live set, #32 for the on-chip write, #27 for
Flash 1 itself).

### 4.5 The `HOOKS=external` variant, and when it would help

`make HOOKS=external` rebuilds Flash 1 with the set-B word **alone** — the
patch exactly as it stood before brief F1 (README.md, "Two build variants").
It exists for one case only: outcome **B** (set B is live) combined with an
on-chip array that cannot be written. In every other case it is strictly worse
than the default build, and in outcome **D** it is useless: with set A live,
a set-B-only patch never executes.

There is deliberately **no set-A-only variant**. The external word costs one
flash word, one trampoline and nothing at runtime, and it is the only hook that
survives a skipped on-chip write — so the default build always carries it.

## 5. Regression: nothing else changed

> *2026-09-24 (H4, ruling): on the bench both `baseline.csv` and `flash1.csv`
> are engine-off KL15 logs (§3's note); variables that only move with the
> engine running will sit still in both and compare trivially. The
> engine-running regression is #28, on the car.*

```bash
python3 tools/logcmp.py patches/ff_counter/test/baseline.csv work/flash1.csv \
        -t patches/ff_counter/test/tolerance.json \
        --align-on raster_setA_10ms_count:100 --uncovered report \
        --json work/logcmp.json
```

Exit status 0 is the exit criterion of issue #27's "log comparison against
baseline shows no other change". Do **not** pass `--strict`: `ff_ticks`,
`ff_alive` and `ff_src_seen` exist only in the candidate log and are listed,
not compared.

> **Two runs are two power-ups (added 2026-09-22, brief F2).** `--align-on`
> shifts the candidate so the two runs' ECU clocks agree, using the live raster
> activation counter at 100/s — section 3a says which set is moving, so align
> on `raster_setA_10ms_count` or `raster_setB_10ms_count` accordingly, and
> **never** on a counter the flash itself produces (`ff_ticks` starts at 0 in
> the candidate run and does not exist in the baseline). A counter missing from
> either log is an error, exit 2, not a silent unaligned comparison.
> `--uncovered report` is what "listed, not compared" means on the command
> line: the three Flash-1 variables are named in the report, not judged against
> the file's `default` limit of 1.0.

`baseline.csv` is the stock run of section 3 and is recorded on the bench, so
it is not in this directory yet. Record it — **twice** — before flashing: after
the flash there is no way back to a stock baseline except reflashing, and two
runs are what turn `tolerance.json`'s predictions into measured repeatability.
The second run is not spare tape; it is the input of

```bash
python3 tools/logcmp.py derive work/stock_run1.csv work/stock_run2.csv \
        --align-on raster_setA_10ms_count:100 --exclude 'raster_*' \
        -o work/tolerance_measured.json
```

which writes this scenario's measured spread (max|d| and mean|d| over the
common time range × 1.5, `--factor` to change it) with the free-running
counters as explicit "not compared" rows. Retighten `tolerance.json` row by row
from it — keep this file's reasoning and units, take the numbers — and say in
the commit which pair of runs they came from (docs/07 §5.5).

## 6. If something is wrong

| Symptom | First thing to check |
|---|---|
| `ff_alive` never becomes 0xFC01 | section 4 rows **D** and **E**: the on-chip read-back decides which. Then `tools/bindiff.py` the ECU read-back against `work/ff_counter.bin`; KESS may have "corrected" a checksum we already had right (docs/06 section 6) |
| `ff_src_seen` is 0 while `ff_ticks` moves | impossible by construction — the OR happens before the increment in `src/ff_counter.c`. Suspect the DDLI entry length (8 bytes, not 6) or a stale definition |
| `ff_src_seen` is 3 | both task sets are running: section 4 row **C** |
| Counter runs but the engine misbehaves | the RAM block is not free after all — 0x7FFB00 is verified statically only. Reflash stock immediately and finish the dynamic half of #23 |
| Counter increments in bursts or stalls | the raster is not fixed-period; record the pattern, it answers `scheduler.md` section 10 |
| The slope is exactly twice what is expected | `ff_src_seen` = 3: both hooks fire. Section 4 row **C** |
| Any logged stock variable outside tolerance | before blaming the patch, re-run the stock baseline: the tolerances in `tolerance.json` are predictions until two stock runs have been recorded |

## 7. Roll back

Write the original read back with KESSv2 (`docs/06_patch_pipeline.md`
section 6). `data/passat_azx_ori.bin` is that file and its SHA-256 is
`b15590d3f1874ace3125c5d047c09a686db9b8bb498187663539ebab205609b3`.

Note for outcome **D**: rolling back does not require the on-chip array to be
writable, because in that outcome nothing was written to it — the external word
at 0x12067C is the only byte that changed, and it is in a range the firmware's
own programming route accepts (`flash_programming.md` §0).
