# ff_fuel bench and car procedure (issues #32 and #37)

Written **before** any flash, as `docs/04_re_guidelines.md` §7 requires: what
will be read, what the numbers must be, and what each outcome means. Nothing
below has been run on an ECU. Every expectation here is a prediction from
`emu/models/flexfuel.py` and the emulator tests; the log is what turns it into
a fact.

> **Do not flash yet.** Two things block it, and `tools/patch_apply.py` prints
> both on every run:
>
> 1. **`"ram_status": "static"`** — 0x7FFB00/0x100 is VERIFIED-STATIC only
>    (`re/findings/ram.md` §8.1). The runtime half of #23 (RequestUpload
>    snapshots across key cycles, `tools/ram_snapshot_diff.py`) is outstanding.
> 2. **Two of the three hook words are in the on-chip flash**
>    (0x42247C and 0x432940, region 0x404000-0x47FFFF). The block checksums are
>    handled and `verify` says ALL OK, but **whether KESSv2 protocol 179 writes
>    that region at all has never been demonstrated on this ECU.** Prove it
>    with the read-back of §1 before trusting anything else in this file.

## 0. Prerequisites

| Item | Status |
|---|---|
| RAM block proven unused across ignition cycles | static half done (C2); **dynamic half open (#23)** |
| KESSv2 writes and reads back 0x404000-0x47FFFF | **open, §1 below is the test** |
| A logger that can read arbitrary RAM (KWP 0x2C + 0x21) | blocked on #20; protocol in `re/findings/kwp.md` §4 and §8 |
| The Pico node sending id 0x0EC at 10 Hz | `pico_can_setup` / `docs/05` §2; `tools/ethanol_frame_decode.py --live` confirms the frame before the ECU is involved |
| TouCAN module C is on the same wire as module B | **HYPOTHESIS**, `re/findings/can.md` §7; `logging/sessions/can_bc_check.json` settles it and it must be settled first — if C is a separate bus the Pico is wired to the wrong pair and nothing in §3 will ever arrive |
| A stock baseline log of the scenario in §4 | record it **before** flashing |
| Wideband in the exhaust | required for §6, not for §2-§5 |

## 1. Flash, then prove the flash

```bash
cd patches/ff_fuel && make check && make dump && make gen && make apply
# -> work/ff_fuel.bin, work/ff_fuel.diff.json, work/ff_fuel.sha256
```

Write `work/ff_fuel.bin` with KESSv2 (`docs/04_re_guidelines.md` §6), then
**read the ECU back and diff it**:

```bash
python3 tools/bindiff.py data/passat_azx_ori.bin work/readback.bin \
        -p patches/ff_fuel/patch.json --json work/readback.diff.json
```

Exit 0 means every changed byte is one of ours or a checksum descriptor.

**The on-chip question is answered here.** Look at the two words in the
read-back specifically:

```bash
python3 tools/blobdis.py work/readback.bin --file-off 0x21E47C --addr 0x42247C --len 4
python3 tools/blobdis.py work/readback.bin --file-off 0x22E940 --addr 0x432940 --len 4
```

| Read-back | Meaning |
|---|---|
| both are `bl 0x152000` / `bl 0x152020` | KESS writes the on-chip flash; the fuel hook and the set-A raster hook are live |
| both unchanged (`4B FF 9F 25`, `4B C8 B0 A5`) | KESS wrote only the external flash. The patch is then **inert on the fuel path** — the set-B raster hook at 0x12067C still runs and still fills the state block, so §2 and §3 still work as a receive test, but `rk` is never scaled. Record it and reopen #32 with a BDM/BSL plan |
| one changed, one not | stop; that is a partial write and the image is not what either tool thinks it is |

Roll back by writing `data/passat_azx_ori.bin`
(`b15590d3f1874ace3125c5d047c09a686db9b8bb498187663539ebab205609b3`).

## 2. Read the state block

`logging/sessions/ff_fuel.json` is the variable list; it carries
`patch_offset` for every `ff_*` entry, so

```bash
python3 logging/med9log.py log --session logging/sessions/ff_fuel.json \
        --patch patches/ff_fuel/patch.json --bus gs_usb:0 --seconds 120 \
        -o logs/2026-xx-xx_ff_fuel.csv
```

resolves the addresses from `build.ram` and cannot go stale when the block
moves. The DDLI recipe itself is `patches/ff_counter/test/procedure.md` §2 with
`a2 a1 a0` = `7F FB 00`.

**Checks, in this order. Stop at the first one that fails.**

1. `ff_magic == 1179599921` (0x46463031) and `ff_length == 64`.
   If both are 0, no periodic hook has run — go to check 2 before blaming the
   flash.
2. **`ff_src_seen` answers `re/findings/scheduler.md` §11.7 in one sample:**

   | `ff_src_seen` | `ff_src_owner` | Meaning |
   |---|---|---|
   | 1 | 1 | only the **task-set-A** hook (0x432940) fires: set A is live |
   | 2 | 2 | only the **task-set-B** hook (0x12067C) fires: set B is live |
   | 3 | 1 or 2 | both fire. The OS should make this impossible; the patch still ticks exactly once per raster period (`ff_src_foreign` counts the other one), but **reopen #44** |
   | 0 | 0 | neither hook runs at all: the flash did not take |

   Cross-check against the stock raster counters in the same log
   (`raster_setA_10ms_count` 0x7FD754, `raster_setB_10ms_count` 0x7FD758): the
   one that moves must be the set `ff_src_seen` names. Write the result into
   `scheduler.md` §11.7 as VERIFIED-DYNAMIC and tick the row in #44.
3. `ff_ticks` rises by **100 /s** (~0.25 % low; the OS truncates 285.714 ns to
   285 ns) and tracks the live raster counter 1:1. A u32 read is not atomic
   against a 100 Hz increment, so fit a line over many samples.
4. `ff_cal_ok == 1` and `ff_cal_mode == 1`. If `ff_cal_ok` is 0, FFCAL001 did
   not survive the flash — compare 0x5E2510 in the read-back against
   `patches/ff_fuel/build/ffcal001.bin`.
5. Power-cycle: `ff_ticks` restarts near 0, `ff_magic` is back, `ff_frames` is
   0 and `ff_mode` is 3 (FAULT). That is `ff_state_init()` doing its job on a
   block the cold start does not fill.

## 3. Reception, with the Pico running

With the node powered and sending id 0x0EC at 10 Hz:

| Variable | Expected |
|---|---|
| `can_rx_spare0_b0` (0x803F9C) | the ethanol % on the wire, straight out of the driver shadow |
| `can_rx_spare0_ctr` (0x803F9F) | the rolling counter, +10 /s modulo 256 |
| `ff_frames` | **+10 /s** |
| `ff_e_raw` | equal to `can_rx_spare0_b0` |
| `ff_status` | 0 |
| `ff_mode` | **1 (OK)** within one second of the first frame |
| `ff_age_ticks` | oscillating 1..10, never above 100 |
| `ff_stall` | 0 |
| `ff_frame_bad` | 0 |

If `ff_frames` stays 0 while `can_rx_spare0_b0` moves, the id echo check
rejected the frame: read 0x803F98, it must be 0x0000_00EC.
If **both** stay 0, reception never happened — the module-B/C bus question
(`can.md` §7) or the id-word edit at 0x2BD8C is the cause, not this patch.

## 4. E0 equivalence — the acceptance test of #32

Same scenario, stock image first, then the patched one, engine at operating
temperature, **with the Pico disconnected or sending E0**:

```
0-30 s     idle, no load
30-40 s    one load step (A/C on, or a steady 2000 rpm hold)
40-70 s    idle again
70-120 s   a gentle drive cycle, part load, no WOT
```

At E0, `ff_f_q10` is exactly **1024** and the patched `ff_rk_scale()` takes its
early return without writing 0x803038 at all, so the two logs must be the same
run twice:

```bash
python3 tools/logcmp.py patches/ff_fuel/test/baseline.csv logs/2026-xx-xx_ff_fuel.csv \
        -t patches/ff_fuel/test/tolerance.json --json work/logcmp.json
```

Exit 0 is the criterion. Do **not** pass `--strict` — the `ff_*` variables
exist only in the candidate log. `baseline.csv` is the stock run and is
recorded on the bench, so it is not in this directory yet; record it **before**
flashing, because afterwards the only way back to a stock baseline is
reflashing.

Expected log lines on the patched run (prediction, not a recording):

```csv
# session: bench, ff_fuel at E0 (no Pico on the bus)
# ecu: 03H906032 / 1037382557
# dump_sha256: <from work/ff_fuel.sha256>
# transport: KWP2000 0x2C/0x21 over TP2.0
time_s,var,value,unit
0.000,ff_magic,1179599921,-
0.000,ff_length,64,B
0.000,ff_src_seen,2,-
0.000,ff_mode,3,-
0.000,ff_e_filt,0.00,%
0.000,ff_f_q10,1.0000,-
0.000,ff_frames,0,count
0.000,ff_ticks,14830,count
0.200,ff_ticks,14850,count
0.400,ff_ticks,14870,count
1.000,ff_ticks,14930,count
```

`ff_mode` 3 with `ff_f_q10` 1.0000 is the correct no-sensor state: FAULT, E0,
gasoline fuelling. It is also exactly what the car must do if the node is ever
unplugged.

## 5. Fault-injection matrix — the acceptance test of #37

Engine idling and warm, Pico running at a steady E-value first (let `ff_e_filt`
settle, which takes about `E/2` seconds at the 2 %/s slew — 40 s from E0 to
E80). Then, one step at a time, wait for the stated time and record the row.

| # | Action | After | `ff_mode` | `ff_f_q10` | Other |
|---|---|---|---|---|---|
| 0 | steady state, status 0 | — | 1 OK | `ff_F_curve(ff_e_filt)` | `ff_frames` +10/s, `ff_stall` 0 |
| 1 | **unplug the flex-fuel sensor** from the Pico (Pico keeps sending, status becomes 1) | < 0.2 s | 3 FAULT | **unchanged, held** | `ff_faults` +1, `ff_frame_bad` 1, `ff_hold_ticks` counts down from 5999 |
| 1b | keep waiting | 60 s | 3 FAULT | starts falling | `ff_hold_ticks` 0, `ff_e_filt` falls at **2 %/s** |
| 1c | keep waiting | +E/2 s | 3 FAULT | **1024** | `ff_e_filt` = `ff_e_key` = 0 |
| 1d | plug the sensor back in | < 0.2 s | 1 OK | rises again at 2 %/s | `ff_frame_bad` 0, `ff_faults` unchanged |
| 2 | **stop the Pico** (power it down: no frames at all) | 1.0-1.1 s | 3 FAULT | **unchanged, held** | `ff_age_ticks` reaches 101, `ff_faults` +1, `ff_frame_bad` stays 0 |
| 2b | keep waiting | 60 s then E/2 s | 3 FAULT | -> 1024 | same decay as 1b/1c |
| 2c | power the Pico back up | < 0.2 s | 1 OK | rises at 2 %/s | — |
| 3 | **contaminated fuel**: make the Pico report status 2 | < 0.2 s | 2 HOLD | **frozen** | `ff_e_filt` frozen, `ff_faults` **unchanged** (HOLD is not a FAULT), `ff_frames` still +10/s |
| 3b | back to status 0 | < 0.2 s | 1 OK | follows again | — |
| 4 | **counter stall**: make the Pico repeat one counter value | 0.3 s (3 frames) | 3 FAULT | held | `ff_stall` >= 3, `ff_frame_bad` 1, `ff_faults` +1 |
| 4b | let the counter run again | < 0.2 s | 1 OK | — | `ff_stall` 0 |
| 5 | **implausible value**: Pico reports 101 % | < 0.2 s | 3 FAULT | held | `ff_frame_bad` 1 |
| 6 | **not ready**: status 3 at power-up | — | 3 FAULT | 1024 | `ff_frames` stays 0 |

Rows 1, 2 and 4 are the three that matter for safety, and they all end the same
way: **the fuel factor is held, never dropped**, so a transient dropout cannot
lean the engine out. That asymmetry is deliberate (docs/05 §3.2). The ignition
and rail blends, which must drop to the gasoline map *immediately*, do not
exist yet — they are §3.4 and §3.6 and a later patch.

Every row above is also an emulator test in `tests/test_ff_fuel_patch.py` and
`tests/test_flexfuel_model.py`. If the car disagrees with the table, the model
is wrong and it must be fixed there first.

## 6. Blended runs (car, wideband) — beyond the MVP acceptance

`tolerance.json` is for §4 only: at E20/E50/E85 the fuelling signals are
*supposed* to move, so comparing them with a gasoline baseline proves nothing.
What to watch instead, from `docs/05` §6 and the B6/B9 corrections:

* the wideband: `fr`/`frm` (measuring ids 29/28 and 33/34) back within ±5 % of
  1.0 after an adaptation reset. Those four are in `re/measuring_vars.csv`;
  the additive `fra` (0x801D1A) is **not** and needs a DDLI entry.
* `dwi_inj_angle` (0x803088): `rk` x 1.54 at E85 raises it by about half, and
  the hard clamp is `wbho1s (0x80307E) - dwi - 2144` (`rail.md` §8). Log both.
* `prist_w` above **13 bar** at all times (`rail.md` §14) — below it, three
  limp-home limits arm at once.
* `dwkrz` (0x7FCE57-0x7FCE5C, VCDS groups 020-024) unchanged: ethanol must not
  cost advance. Also the low-octane latch bits at 0x7FD31B bits 0/1 must stay
  clear.
* no lean event at WOT. Build up E20 -> E50 -> E85 and do not go to full load
  until the part-load lambda holds.

## 7. If something is wrong

| Symptom | First thing to check |
|---|---|
| `ff_magic` never appears | is the hook in the live task set? `ff_src_seen` and the two stock raster counters answer it. If set A is live and only 0x12067C was written (external flash only), that is the on-chip question of §1 |
| `ff_cal_ok == 0` | FFCAL001 did not land; compare 0x5E2510 in the read-back with `build/ffcal001.bin`. `ff_mode` will be 4 (OFF) and `ff_f_q10` 1024 — safe, but the patch does nothing |
| `ff_frames` stays 0 while the bus shows 0x0EC | id echo (0x803F98) or the `can_init_mb` call; §3 |
| The engine misbehaves at E0 | reflash stock **immediately**. At E0 the patch does not write `rk` at all, so a change there means the RAM block is not free (#23) or the on-chip write damaged something else |
| `ff_f_q10` jumps rather than ramps | `ff_e_frac` is not being carried; that is the sub-count the 0.32-count slew step lives in. Read it and compare with the model |
| A logged stock variable outside tolerance at E0 | before blaming the patch, re-run the stock baseline: `tolerance.json` holds predictions, not measured repeatability |

## 8. Building a mode-0 (inert) file

For a flash that proves the plumbing without touching fuelling at all — useful
as the very first flash on the car:

```bash
cd patches/ff_fuel
./../../.venv/bin/python3 ffcal001.py --set ff_mode=0 -o build/ffcal001.bin --no-rows
make gen && make apply          # work/ff_fuel.bin now carries ff_mode = 0
```

`ff_mode = 0` forces `ff_f_q10` to 1024 for ever, never polls the CAN slot and
never arms the message buffer, while the state block, `ff_ticks`, `ff_src_seen`
and `ff_rk_calls` all still fill in. Rebuild with the shipped
`ffcal001.json` (`./../../.venv/bin/python3 ffcal001.py`) to get mode 1 back —
and re-run `make gen`, or `patch.json` will still describe the mode-0 block.

## 9. Roll back

Write `data/passat_azx_ori.bin` back with KESSv2
(`docs/06_patch_pipeline.md` §6). Its SHA-256 is
`b15590d3f1874ace3125c5d047c09a686db9b8bb498187663539ebab205609b3`.
