# Brief E2 — Start enrichment `f_st(E, tmst)` and the start-ignition offset, desk half (#35)

Issue **#35** (implementation, model, emulator tests, procedure; the start
series and the map values stay open). Wave E pair 2, **after E1 is merged**
(same files); runs in parallel with E4 (different files). No `sudo`; nothing
is flashed; no ECU is contacted.

Read `00_common_rules.md` (including the 2026-09-17 section), then
`docs/05_flexfuel_design.md` §3.2, §3.5 (+ B8 note), §4, `re/findings/start.md`
§2, §3, §4, §5, §7, §8, `emu/start_model.py`, `tests/test_start_model.py`,
`re/findings/injection.md` §8-§9, everything under `patches/ff_fuel/` as
E1 left it (README, `src/`, `ffcal001.py`, `patch.json`, `test/`),
`emu/models/flexfuel.py`, the ff_fuel tests, `re/findings/measuring_vars.md`
§8, `docs/06_patch_pipeline.md` §1, §3, §4.

## Facts (VERIFIED-STATIC unless marked)

- **S1, the cranking lever.** `%ESSTT` publishes `ksta * kstaa` (u16,
  **1024 = 1.0**) at RAM **0x80302C**; `gk_rk` (0x41AA48) multiplies it into
  the fuel at 0x41AA88 only while 0x7FEA33 == 0 (the start path), and the
  module forces 0x400 outside the start. The two stores are
  **0x41A680** `B3 ED 30 3C` = `sth r31,0x303C(r13)` in `FUN_0041a268` and
  **0x41A808** `B0 6D 30 3C` = `sth r3,0x303C(r13)` in the high-pressure twin
  `FUN_0041a690` (note: **r3, not r31**, in the twin). Both on-chip →
  `"onchip_edit": true`. `tools/find_branch_refs.py` finds **no** `bl` and no
  pointer to either function (2026-09-17): they are reached through a table.
  Establish the task they run in and the register/LR state at each store
  from the disassembly before writing the stub (start.md §3.3, §6 for B8's
  emulator set-up).
- **Z1, the start ignition.** While `B_stend` (0x7FE921; segment copy
  0x7FECCA) is clear, `zwbas_per_bank` replaces the whole per-bank angle by
  `zwstt` 0x802096 (s8, 0.75 °CA/LSB) — knock retard bypassed. `zwstt` is
  built by `FUN_00431294` (0x431294) and stored by **0x431384**
  `9B ED 20 A6` = `stb r31,0x20A6(r13)`; its single caller is 0x432B04 in
  set A's 10 ms task 0x4328E4. On-chip. Keep any advance small (+2..+4 ° =
  +3..+5 counts) and only below about 40 °C `tmst` (start.md §5).
- Temperatures: `tmst` **0x8021F6** (coolant latched at start), `tmot`
  0x8021EF, both u8 **0.75 °C/LSB, -48 °C**. `KFWKSTT`'s tmst breakpoints
  (0x5C6C62: -30, -24.75, -20.25, -15, -6.75, 0, 15, 20.25, 27.75, 39.75,
  60, 90 °C) are the natural axis. Engine state: `B_st` 0x7FE91D, start end
  0x7FE920, `B_stend` 0x7FE921, injections since start 0x7FD269, ignitions
  since start 0x7FCE14, after-start timer 0x8011D8.
- The stock factor already runs 2.1x at 90 °C and 22.8x at -30 °C; the
  injection window (`dwi` 0x803088, injection.md §8) is the binding limit
  below about -10 °C — a calibration matter, but clamp `f_st` in code.
- Persistence makes this feature possible: D2 restores `e_filt` **and**
  `e_key` from EEPROM block 8 at cold start before the first frame, so
  `f_st(e_filt, tmst)` sees the stored E% while cranking. Read `ff_fuel.c`
  / `ff_diag.c` to confirm what `e_filt`, `mode` and `f_q10` are during the
  first activations after a restore and make `f_st` consistent with `F`.
- Models: `emu/start_model.py` already takes `ethanol_factor` (S1) and
  `ethanol_offset` (Z1) and `tests/test_start_model.py` asserts
  `f_st(0) = 1024` is bit-identical at both hooks.
- FFCAL001 (v2 after E1): reserved `ff_fst_map` 6 × 6 u16 (1024 = 1.0) at
  +0x94, **no axes**. Extend by appending (v3).
- Measuring block: **group 69** (0x45; words 0x5C55A2, 0x5C57A0, 0x5C599E,
  0x5C5B9C, `calibration_edit`) and **ids 2188-2191** (table words
  0x0A7888-0x0A7897, stock `00 03 8E C4`); re-check with
  `measuring_vars.py --free`. 108 is E1's, 109 is E5's, 111 is D2's.

## Design decisions (deviate only with a reason in the report)

- **Producer in the 10 ms tick** (`src/ff_start.c`): `fst_q10 =
  clamp(interp(ff_fst_map, e_filt, tmst), 1024, ff_fst_max)` (u16; default
  `ff_fst_max` 2048) and `zwst_add = clamp(fzwst(e_filt) * ff_zwst_gain, 0,
  ff_zwst_max)` (s8 counts; default max 4 = 3 °, and **0 when tmst ≥
  `ff_zwst_tmax`**, default 40 °C = count 117). Both are 1024 / 0 when
  `ff_st_enable` / `ff_zwst_enable` is 0, when `cal_ok == 0`, or when mode
  is INIT/OFF. **Fuel (`fst_q10`) follows `e_filt`'s own FAULT rules** (hold,
  then decay) exactly like `F`; **ignition (`zwst_add`) drops to 0 the
  activation the mode leaves OK/HOLD** (the #37 rule, as in E1). Compute
  every activation; the values are only consumed during the start.
- **S1 stubs.** Two trampolines: at 0x41A680 the value is in r31, at
  0x41A808 in r3. Each: check the state-block magic, compute
  `min((v * fst_q10) >> 10, 0xFFFF)` into a dead register, and perform the
  displaced store **absolutely** (`lis r11,0x80 ; sth r12,0x302C(r11)` — no
  r13 in patch code, C2's rule); **never modify r31/r3 themselves** (they may
  be used after the store). Verify from the disassembly which of r0/r11/r12
  are dead at each site and whether LR is dead (function has saved it and
  reloads it later) — if not, use a frame. `fst_q10 = 1024` must take the
  early path that stores `v` unchanged.
- **Z1 stub** at 0x431384: add `zwst_add` to r31's value with the s8 clamp,
  store absolutely to 0x802096; same register discipline; the stock value
  itself is not modified in its register.
- **FFCAL001 v3.** Append: `ff_st_enable` u8 (default **0**), `ff_zwst_enable`
  u8 (default **0**), `ff_fst_max` u16, `ff_zwst_max` u8, `ff_zwst_tmax` u8
  (tmst counts), `ff_fst_e_axis[6]` u8 (%: 0, 20, 40, 60, 85, 100),
  `ff_fst_tmst_axis[6]` u8 (tmst counts; choose six of the KFWKSTT
  breakpoints, e.g. -30, -15, 0, 20.25, 39.75, 90 °C, and say why), a
  `ff_fzwst_curve[6]` s8 over the same E axis (counts; default 0). Bump the
  version and length; `ff_cal_ok()` accepts v3 only. Neutral tables keep the
  shipped file inert even with both enables set.
- **Diagnostics, group 69** (ids 2188-2191): field 1 `fst_q10` as %
  (`(fst_q10 * 100) >> 10`, formula 0x21 A = 100), field 2 `zwst_add` in
  °CA (the formula E1 chose for angles), field 3 `tmst` in °C (formula 0x05
  A = 10, from 0x8021F6 with the 0.75/-48 conversion done in the handler),
  field 4 0x80302C as % (`(x * 100) >> 10`, saturating at 255 → use a count
  formula if % does not fit; say which).
- **Model.** Extend `emu/models/flexfuel.py` (new `Cal` fields; the tick
  computes `fst_q10` and `zwst_add`) so the tick-by-tick comparison covers
  them; reuse `emu/start_model.py` for the stub arithmetic.

## Tasks

1. Establish the tasks and register state at 0x41A680, 0x41A808, 0x431384
   (disassembly + `tools/callgraph.py`/`sda_xref.py`; the B8 emulator set-up
   in start.md §6 shows how the functions were run). Record it in start.md as
   a dated §9 before writing code.
2. `src/ff_start.c`, the three trampolines in `hooks.S`, `ff_state` fields
   (append only), `ffcal001.py` v3, `patch.json` (three `onchip_edit` hooks;
   `u32_syms` + group words), README (hook table now seven words, six of
   them on-chip — say so plainly; counts; RAM; FFCAL001 v3).
3. Tests (`tests/test_ff_start_patch.py`): (a) S1 stubs with `fst_q10 = 1024`
   store `v` unchanged for v in {0, 1, 0x400, 0x7FFF, 0xFFFF} at both sites
   and leave every register but the scratch ones untouched; (b) `fst_q10 =
   1536` gives `(v * 1536) >> 10` saturated; (c) Z1 with `zwst_add = 0`
   stores the stock byte, with +4 the clamped sum; (d) invalid state block →
   stock behaviour; (e) tick equals the model over sequences that cover
   restore-then-first-frame, FAULT (fuel holds, ignition drops), tmst above
   and below `ff_zwst_tmax`; (f) enables off / enables on with neutral
   tables: whole-SRAM diff clean outside the state block; (g) group 69 via
   the dispatcher and `21 45` through `ecu_sim.py --sim-dump`; (h) costs.
   Run `tests/test_start_model.py` unchanged.
4. `test/procedure_e2.md`: the cold-start logging recipe (tmst, 0x80302C,
   `fst_q10`, injections/ignitions since start, lambda, `dwi`, start time),
   how to calibrate `ff_fst_map` from a start series starting at 1.2x warm
   and never above the window (`dwi`), the Z1 limits, and group 69's
   expected fields. Add the variables to `logging/sessions/ff_fuel.json`.
5. Dated notes: docs/05 §3.5 and §4 (v3), start.md §7 (sites taken),
   measuring_vars.md §8 (group 69 / ids 2188-2191), `re/symbols.csv`.
   Commit on `agent/E2` after every step; comment on #35.

## Acceptance
Builds; applies `ALL OK`; clean `bindiff`; all hook words disassembled and
free of r2/r13; suite green; both S1 sites and Z1 are bit-identical with the
features off and with them on at neutral calibration; the fuel factor follows
E's hold/decay while the start advance drops immediately; the README states
every changed word and the on-chip count.
