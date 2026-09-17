# ff_fuel bench + road procedure, E2 half — the start enrichment (#35, both #37 rules)

Brief **E2**, 2026-09-17. The fourth companion of `procedure.md`, which covers
the MVP itself (#32/#37); do **not** start here. §1 of `procedure.md` (the
on-chip read-back) and §2 (the state block) must both have passed, and
`procedure_d2.md` part A should have passed too, because group 69 is read the
same way and a failure there is not a start problem.

Written **before** any flash, as `docs/04_re_guidelines.md` §7 requires.
Nothing here has been run on an ECU. Every expectation is a prediction from
`tests/test_ff_start_patch.py`, `emu/models/flexfuel.py` and
`emu/start_model.py`; the bench and the cold mornings are what turn them into
facts.

> Both blockers of `procedure.md` still apply — `"ram_status": "static"` and
> the undemonstrated KESSv2 write of the on-chip flash — and E2 makes the
> second one worse again: **all three new hook words are on-chip**, so six of
> the seven now live in 0x404000-0x47FFFF and only 0x12067C does not.
> **Do not flash yet.**

> ### The one safety line to read before anything else
>
> **During the start the knock retard is bypassed.** `zwbas_per_bank`
> substitutes `zwstt` for the whole per-bank angle, so `dwkrz` is not applied
> and nothing downstream takes a start advance back. Part C is written around
> that: it moves one count at a time, it stops at 4 counts (3.00 °CA), and it
> never runs above 40 °C. If you only do half of this procedure, do part B.

## 0. What is new in the image, compared with E1

| | |
|---|---|
| **Flash** | the blob at 0x152000 is now 6,380 B (was 4,932) |
| **New hook words** | **0x41A680** `B3 ED 30 3C` (`sth r31,0x303C(r13)`) → `bl 0x152088`; **0x41A808** `B0 6D 30 3C` (`sth r3,0x303C(r13)`) → `bl 0x152090`; **0x431384** `9B ED 20 A6` (`stb r31,0x20A6(r13)`) → `bl 0x1520F0`. All three on-chip |
| **New flash edits** | 16 B at 0x0A7888 (`tbl_measuring_vars` ids 2188-2191) and four u16 at 0x5C55A2 / 0x5C57A0 / 0x5C599E / 0x5C5B9C (`tbl_measuring_groups` group 69) |
| **New RAM** | **four bytes.** `ff_fst_q10` at +0x40 and `ff_zwst_add` at +0x42 were appended past the annex, so the state block is **68 B** and its checksum now covers two ranges. `ff_persist_buf` moved to 0x7FFB44 and `ff_nvm_req` to 0x7FFB48; `.bss` is 85 B of 256 |
| **Calibration** | FFCAL001 is now **v3, 290 B**. `ff_st_enable` = **0**, `ff_zwst_enable` = **0**, `ff_fst_max` = 2048, `ff_zwst_max` = 4, `ff_zwst_tmax` = 117 (39.75 °C), `ff_fst_map` = **all 1024**, `ff_fzwst_curve` = **all 0**, plus the two 6-point axes |
| **New stock RAM read** | `tmst` 0x8021F6, **`B_stend` 0x7FE921** (the S1 gate), `ksta_adapted` 0x80302C (block 69 field 4) |
| **New stock RAM written** | `ksta_adapted` 0x80302C and `zwstt` 0x802096 — but only because the stubs *are* the stores the stock code would have executed |

A rehearsal of the whole E2 half without an ECU, which should be run first:

```bash
cd patches/ff_fuel && make check gen apply          # -> work/ff_fuel.bin
cd ../.. && ./.venv/bin/python3 -m unittest tests.test_ff_start_patch
./.venv/bin/python3 logging/med9log.py groups --sim --sim-dump work/ff_fuel.bin 69
```

---

# Part A — with both features OFF (the shipped file)

An **equivalence test**, not a feature test. Shipping `ff_st_enable` = 0 and
`ff_zwst_enable` = 0 means the flashed file must be indistinguishable from the
E1 file as far as the engine is concerned, while three new hook words are
already in the flash — and one of them, 0x41A680, is already executing on
**every segment of the running engine**, not only during a start.

## A1. Group 69, ignition on, engine off

VCDS → Engine 01 → Measuring Blocks → **69** (or `21 45`).

| Field | Expect | If not |
|---|---|---|
| 1 `f_st` | **100 %** | anything else means `ff_st_enable` is not 0 or the state block is wrong; check group 111 field 4 (the mode) first |
| 2 start advance | **0.00 °CA** | same |
| 3 `tmst` | the coolant temperature **at the last start**, ±1 °C | a value far from the gauge means the engine has not started since power-up (`tmst` is a latch, not a live reading) |
| 4 `ksta_adapted` | **1024** exactly | the ECU forces 1.0 outside the start; anything else here, with the features off, is a bug in the stub and you stop |

Field 4 is a **raw count**, not a percent: divide by 1024 for the factor. It
reads 1024 with the engine running and rises to four figures during cranking.

## A2. The hooks really are executing

`ff_fst_q10` and `ff_zwst_add` are in the state block, so DDLI shows them at
10 ms:

```bash
./.venv/bin/python3 logging/med9log.py log --session logging/sessions/ff_fuel.json \
    --patch patches/ff_fuel/patch.json --bus gs_usb:0 --seconds 60 \
    -o logs/$(date +%F)_e2_partA.csv
```

* `ff_length` must read **68**. If it reads 64 you flashed the E1 blob.
* `ff_fst_q10` must be **1024** in every sample and `ff_zwst_add` **0**.
* `ff_ticks` must still rise at ~100/s — E2 added 18 instructions per
  activation, which must not show up as a missed raster anywhere.

There is **no counter that proves the S1 stubs ran**, deliberately: they write
nothing of their own. The proof is A3.

## A3. E0 equivalence run — and the one that is not obvious

Two comparisons, both against the stock baseline of `procedure.md` §5:

1. **A start.** Log `ksta_adapted` and `zwstt` against `anztist` and `zdgz`
   from key-on through the first 30 combustion events. Every sample must be
   identical to the stock baseline, and identical to what
   `./.venv/bin/python3 -m emu.start_model` prints for that `tmst`.
2. **The running engine.** This is the one the E1 procedure had no equivalent
   of. `0x41A680` is *not* unreachable once the start is over: `%ESSTT`'s
   `B_stend` early-out publishes the neutral 0x400 **through** that very store
   (`re/findings/start.md` §9.3). So log `ksta_adapted` for two minutes of
   ordinary driving and assert it is **exactly 1024 in every sample**. If it is
   ever 1024 × something, the `B_stend` gate is not working and the patch is
   enriching the whole engine.

`tools/logcmp.py` with `test/tolerance.json` on both runs; `rk_fuel_mass`,
`ti_sum` and `zwist_display_b1` must all be inside the E0 tolerances.

---

# Part B — the fuel half ON (`ff_st_enable` = 1)

Do this **before** part C. The fuel half is the one that matters (ethanol needs
the mass; the advance is a refinement), it is the one with a stock limit
downstream, and its failure mode is a long crank rather than a damaged engine.

## B1. One cell on the bench, before any real start

Set `ff_mode` = 2 and `ff_e_override` = 85 so the estimate does not depend on
the Pico, build a block with `ff_st_enable` = 1 and **one** non-neutral cell,
and crank with the fuel pump relay pulled (or the injectors disconnected —
whatever your bench allows) so nothing burns:

```bash
./.venv/bin/python3 patches/ff_fuel/ffcal001.py --set ff_mode=2 \
    --set ff_e_override=85 --set ff_st_enable=1 \
    -o build/ffcal001_b1.bin --no-rows
```

with `ff_fst_map` edited so the +20.25 °C column of the E85 row reads 1229
(1.20×) and everything else stays 1024. Then crank and watch group 69:

| Field | Expect |
|---|---|
| 1 `f_st` | **120 %** while `tmst` is near +20 °C |
| 4 `ksta_adapted` | the stock value of the same crank × 1.20, ±1 count |

`ffcal001.py` will refuse to build the file if row 0 is not all 1024, if any
cell is below 1024 or above 2560, or if `ff_fst_max` is outside 1024..2560 —
so a typo becomes a build error, not a start.

## B2. The fault matrix — the fuel half of #37

Same shape as `procedure.md` §6, with group 69 field 1 as the observable:

| Step | `ff_mode` | `ff_fst_q10` | Why |
|---|---|---|---|
| Pico unplugged mid-run | FAULT after `ff_timeout_ms` | **held** at its last value for `ff_hold_s`, then decaying with `ff_e_filt` | the fuel factor is never dropped: a transient dropout must not lean the crank out |
| status byte 2 (contaminated) | HOLD | frozen with `ff_e_filt` | |
| `ff_mode` = 0 in FFCAL001 | OFF | **1024** | |
| a v2 FFCAL001 under the v3 blob | OFF, `ff_cal_ok` = 0 | **1024** | the strict version check |
| power-up, before the first frame | FAULT | 1024 until `ff_e_filt` moves | |

The asymmetry is the point, and part C's table is its mirror image.

## B3. Calibrating `ff_fst_map` from a start series

**Start warm and work down.** The stock cranking factor is already 2.1× at
+90 °C and 22.8× at -30 °C (`start.md` §6), so `f_st` is a *correction on top
of* a map that is already doing the cold work.

1. **Baseline.** For each temperature you can reach, log a gasoline start with
   `ff_st_enable` = 0: `tmst`, `ksta_adapted`, `anztist`, `ti_sum`,
   `dwi_inj_angle`, time to a stable idle, and `fr_w_b1` for the 30 s after.
   Plot `ksta_adapted` against **`anztist`**, not against seconds — that is the
   stock map's own axis.
2. **The first ethanol number is 1.2×, warm.** The mass ratio at E85 is 1.54,
   but the start is not a stoichiometric event: the stock map's cold multiplier
   already covers most of what a cold cylinder wall takes, and E85's problem at
   a warm start is volatility, not mass. Start at **1229 (1.20×)** in the
   +20.25 °C and +39.75 °C columns of the E85 row and move up only if the crank
   is longer than the gasoline baseline.
3. **Work down one column at a time**, -15 °C, then -30 °C, and re-log the
   whole series each time. A column that shortens the crank by more than ~2
   combustion events is done; a column that makes it *longer* is too rich.
4. **The binding constraint below about -10 °C is the injection window, not
   the mixture.** Watch `dwi_inj_angle` (0x803088) and `rl_max_arb`
   (0x80235A): at 22.8× stock the window is already most of the cycle, and
   `f_st` multiplies it. **The rule: if `dwi_inj_angle` stops rising when
   `ksta_adapted` rises, the window is saturated and any further `f_st` is
   fuel the engine never gets.** Stop there and record the number; do not
   raise `ff_fst_max`.
5. **Never below 1024.** `f_st` only enriches. The build refuses anything else.

Fill the map from the E85 row outwards; the E0 row must stay 1024 and the
rows between are linear interpolation, so two calibrated rows (E0 and E85) plus
sensible intermediates is a complete map.

---

# Part C — the ignition half ON (`ff_zwst_enable` = 1)

**Only after part B is finished and only below 40 °C.** The shipped
`ff_zwst_tmax` is 117 (39.75 °C) and there is no reason to raise it: a warm
start does not need advance and a hot one must not have it.

## C1. The limits, and why they are what they are

| | |
|---|---|
| `ff_fzwst_curve` | s8 counts of 0.75 °CA, on `ff_fst_e_axis`; **0 at E0**, and the build refuses otherwise |
| `ff_zwst_max` | ships **4** counts = 3.00 °CA. This is the calibration ceiling |
| `FF_ZWST_HARD_MAX` | **8** counts = 6.00 °CA, in code, whatever the calibration says |
| `ff_zwst_tmax` | ships **117** = 39.75 °C, and it is a breakpoint of `ff_fst_tmst_axis`, so the fuel map and the advance gate change at exactly the same temperature |
| downstream | **nothing.** The knock retard is bypassed; `zwmin` follows `zwstt` in this dataset (`start.md` §9.3); only the s8 clamp and the -54…+58.5 °CA output clamp remain |

`start.md` §5 puts the useful range at +2…+4 °CA, i.e. **+3…+5 counts**, and
the shipped ceiling of 4 sits inside it. Start at **1 count** at E85 and add
one count per cold morning.

## C2. The bench check, before a real cold start

With `ff_zwst_enable` = 1, `ff_fzwst_curve` = `[0, 0, 0, 0, 2, 2]` (2 counts
from E85 up) and `ff_mode` = 2 / `ff_e_override` = 85:

| Observable | Expect |
|---|---|
| group 69 field 2 | **+1.50 °CA** while `tmst` < 117 counts |
| `ff_zwst_add` | 2 |
| `zwstt` | the stock value of the same `zdgz`/`tmst` cell **+ 2 counts** |
| `zwist_display_b1` | the same +1.50 °CA, unless a clamp binds |
| `tmst` forced above 117 | field 2 and `ff_zwst_add` both **0** |

The last row is the one to check twice: warm the engine past 40 °C, stop, and
restart — field 2 must read 0.00 °CA on the restart even though the ethanol
estimate is unchanged.

## C3. The fault matrix — the ignition half of #37

The mirror image of B2, and the whole point of the asymmetry:

| Step | `ff_mode` | `ff_zwst_add` | `ff_fst_q10` |
|---|---|---|---|
| Pico unplugged mid-crank | FAULT | **0 on the first activation**, no hold, no ramp | **held** |
| status byte 2 | HOLD | keeps computing from the frozen estimate | frozen |
| mode leaves OK/HOLD/OVERRIDE | — | **0 within 10 ms** | held then decaying |
| `tmst` ≥ `ff_zwst_tmax` | any | **0** | unaffected — the fuel map is not gated on temperature |

Log at 10 ms and read `ff_zwst_add` rather than the measuring block: a VCDS
group is far too slow to see a one-activation drop.

## C4. What must never happen

* `ff_zwst_add` above 8. The code clamps, the build refuses, and a reading
  above 8 means the state block is corrupt — stop and dump it.
* Any advance at `tmst` ≥ 117 counts.
* A start that fires **before** its gasoline baseline by more than the
  calibrated advance. If `zwstt` moves by more than `ff_zwst_add`, something
  other than this patch moved it.
* Knock during the start. There is no retard to catch it; listen, and back the
  curve off a count if you hear anything.

---

## 5. What this procedure does **not** cover

* **The map values.** Issue #35 keeps its calibration half open: this
  procedure produces the start series, it does not contain the numbers.
* **The `tnst_w` tick period.** `0x8011D8` is incremented by
  `afterstart_timer` while the engine turns, but the dump does not say how
  often. Measure its slope against `raster_setB_1ms_count` in the first log and
  record it in `start.md`.
* **Whether the OBD route can write the on-chip flash.** Six of the seven hook
  words now depend on it; brief E6 answers it from the dump and
  `procedure.md` §1 is the read-back that proves it.
* **The high-pressure start.** `esstt_ksta_hdr` (and therefore the 0x41A808
  stub) runs on a second map set; which of the two twins is live in a given
  start is a bench observation nobody has made. Log `ksta_adapted` and compare
  it against both `emu.start_model` tables.

## 6. Logging session

`logging/sessions/ff_fuel.json` carries everything above: `ff_fst_q10`,
`ff_zwst_add`, `tmst`, `ksta_adapted`, `zwstt`, `B_st`, `B_stend`, `anztist`,
`zdgz`, `tnst_w`, `fr_w_b1`, `dwi_inj_angle`, `rlsol_req` and `rl_max_arb`,
plus everything D1, D2 and E1 already needed. Every `ff_*` entry carries a
`patch_offset`, so

```bash
./.venv/bin/python3 logging/med9log.py log --session logging/sessions/ff_fuel.json \
    --patch patches/ff_fuel/patch.json --bus gs_usb:0 --seconds 120 \
    -o logs/$(date +%F)_ff_start.csv
```

resolves the addresses out of `patch.json` and cannot go stale the day the
block moves again.
