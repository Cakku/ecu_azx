# Brief E1 — Ignition blend `f_zw(E) * dzw_E(nmot, rl)`, desk half (#34; ignition rule of #37)

Issue **#34** (implementation, model, emulator tests, procedure; the road
calibration of `dzw_E` and the knock logging stay open) and the ignition
half of **#37**'s rule "ignition and rail blends drop to gasoline
immediately". Wave E pair 1, runs in parallel with E3 (different files).
No `sudo`; nothing is flashed; no ECU is contacted.

Read `00_common_rules.md` (including the 2026-09-17 section), then
`docs/05_flexfuel_design.md` §3.2 (D1 note), §3.4 (+ B7 note), §4 (D1 note),
`re/findings/ignition.md` §3, §4, §6, §9-§13, `re/findings/scheduler.md` §7-§9
and §11.7, `emu/zw_model.py` and `tests/test_zw_model.py`, everything under
`patches/ff_fuel/` (README first, then `src/ff_state.h`, `src/ff_fuel.c`,
`src/ff_diag.c`, `src/hooks.S`, `ffcal001.py`, `patch.json`, `test/`),
`emu/models/flexfuel.py`, `tests/test_ff_fuel_patch.py`,
`tests/test_ff_diag_patch.py`, `re/findings/measuring_vars.md` §8,
`docs/06_patch_pipeline.md` §1 (D1/D2 notes), §3, §4.

## Facts (VERIFIED-STATIC unless marked)

- **Insertion point: the word at 0x41D40C** (`7C 63 52 14` = `add r3,r3,r10`)
  in `zwgru_build` (0x41D38C), called once per ignition event from task 41
  (TCB 6, entry 0x4224BC, `bl 0x41D38C` at 0x4224E0). Live at that word:
  r3 (accumulator), r10 (last term), r1. r0, r11, r12 are dead. LR was saved
  by the function itself (`stw r0,0xC(r1)` at 0x41D394) and is reloaded at
  0x41D42C (`lwz r0,0xC(r1)`), so a `bl` at 0x41D40C clobbers a dead LR and
  needs no frame. The s8 clamp at 0x41D410-0x41D428 follows; the result is
  stored to `zwgru` 0x7FD315 (ignition.md §11.1). **On-chip flash** →
  `"onchip_edit": true`.
- The offset is applied **before** the bank offsets, the knock retard
  (`zwbas_per_bank` 0x41D10C), `zwmin` and the output clamp (§11), so every
  stock limit still acts on top. Format **s8, 0.75 °CA/LSB, positive =
  advance**: +6 ° = +8 counts. Physical budget: `KFZWOP - KFZW` is 1.5 ° at
  1800 rpm / 35 % and ~9 ° at 4500 rpm / 90 % (§12 table). `dzw_E` must stay
  inside it.
- During the start (`B_stend` 0x7FE921 clear, segment copy 0x7FECCA) the
  whole angle is replaced by `zwstt` (start.md §5): the `zwgru` offset is
  bypassed then. That is brief E2's Z1; not yours.
- Stock precedent for the shape you build: `0x80208E` is an s8 RAM delta
  computed in a slow task and added segment-synchronously in 0x41D10C
  (ignition.md §13.2). Copy the shape: **produce in the 10 ms tick, consume in
  the segment stub.**
- Inputs: `nmot_w` 0x7FEE74 (u16, 0.25 rpm/LSB), `rl_w` 0x7FED38 (u16,
  100/4096 %/LSB). Stock helpers `axis_search_u16_hint` (0x40C9CC) and
  `interp_2d_s8` (0x40C3B4) are modelled bit-exactly in `emu/zw_model.py` and
  proven in `tests/test_zw_model.py`; `interp_2d_s8(val, nx, key_y, key_x)`
  indexes `val[iy*nx + ix]`. The KFZW axis keys 0x7FD5EC/0x7FD5F0 are only
  valid inside task 41 after 0x41D334 ran — **do not read them from the 10 ms
  tick**; search your own axes.
- Acceptance signals for the road: `dwkrz[6]` 0x7FCE57-0x7FCE5C stays 0
  (VCDS 020-024), mean retard 0x7FCE76, and the low-octane latch
  **0x7FD31B bits 0/1 must never set** (§13.2). Final angle `zw` 0x7FEF87
  (s8, 0.75 °).
- FFCAL001 v1 (232 B at 0x5E2510, `ffcal001.py` is the authority): reserved
  `ff_fzw_curve` 17 × u8 (1/256) at +0x42 and `ff_dzw_map` 8 × 8 s8 at
  +0x54 — **no axes were reserved**. 0x5E2510-0x5FFFFF is erased (121,584 B,
  `calibration_coverage.md`), so the block can grow. `ff_cal_ok()` checks
  magic, version == 1, length and the checksum.
- `struct ff_state` is 0x40 B at 0x7FFB00; `ff_nvm_req` occupies +0x40..+0x4B,
  `ff_persist_buf` follows; the block is 0x100 B. The 10 ms tick is
  `ff_tick()` in `ff_fuel.c` (both sets hooked, one owner ticks); modes are
  INIT/OK/HOLD/FAULT/OFF/OVERRIDE; `e_filt` is 1/16 %.
- Measuring block: **group 108** (words 0x5C55F0, 0x5C57EE, 0x5C59EC,
  0x5C5BEA, all `calibration_edit`) and **ids 2192-2195** (table words
  0x0A7898-0x0A78A7, stock `00 03 8E C4`, stub + unnamed — re-check with
  `python3 tools/measuring_vars.py data/passat_azx_ori.bin --free`). D2 left
  108 for exactly this (measuring_vars.md §8.2). 109 belongs to E5, 69 to E2.

## Design decisions (deviate only with a reason in the report)

- **Producer.** New `src/ff_ign.c`, called from `ff_tick()` every activation:
  `dzw_e = clamp(round(f_zw(e_filt) * dzw_map(nmot_w, rl_w) / 256),
  -ff_dzw_max, +ff_dzw_max)` as **s8 counts**, written to a new `ff_state`
  field. `f_zw` interpolates `ff_fzw_curve` on the 17-point E grid exactly
  like `ff_f_of()`; `dzw_map` is a bilinear 8 × 8 s8 lookup over two new
  axes. `dzw_e` is **0** whenever: `ff_zw_enable == 0`, `cal_ok == 0`, mode
  is not OK/HOLD/OVERRIDE, or `e_filt == 0`. On the activation the mode
  leaves OK/HOLD (FAULT, OFF, INIT) `dzw_e` becomes 0 **immediately** — no
  hold, no ramp (the #37 rule; fuel keeps its 60 s hold, ignition does not).
  HOLD keeps computing from the frozen `e_filt`.
- **Consumer.** A hand-written trampoline in `hooks.S` (not `HOOK_TAIL`, not a
  C call): re-do `add r3,r3,r10`, check the state-block magic, load `dzw_e`
  with **absolute addressing** (`lis r11,0x80 ; lbz r12,-0x500+off(r11)`;
  the r13 form in ignition.md §11.1 is forbidden by C2's rule), `extsb`,
  `add r3,r3,r12`, `blr`. Uses only r0/r11/r12; r10 unchanged; ≤ 12
  instructions. Returns to 0x41D410 via LR; the stock s8 clamp still runs.
  Hook technique 3 of docs/06 §4 (`patch_gen` accepts any `old` word).
- **FFCAL001 v2.** Append, never move: `ff_zw_enable` u8 (default **0**),
  `ff_dzw_max` u8 (default 8 counts = 6 °), `ff_dzw_nmot_axis[8]` u16 in
  `nmot_w` units, `ff_dzw_rl_axis[8]` u16 in `rl_w` units (pick breakpoints
  from the KFZW axes 0x5C7736 / 0x5C7758 so cells align with stock rows),
  padding to keep 2-byte alignment. Bump `VERSION` to 2 and `LENGTH`;
  `ff_cal_ok()` accepts version 2 with the new length (state what happens
  when a v1 block is found: mode 0, as today). Update `ffcal001.py`,
  `ffcal001.json`, `ff_state.h`, `ffcal001_rows.csv`, the model, the tests
  and `logging/sessions/ff_fuel.json` in the same commit. The default
  `ff_fzw_curve` becomes the docs/05 §3.4 shape (0 at E0, 256 from E40-50 on)
  and `ff_dzw_map` stays all 0, so **the shipped file is inert even with
  `ff_zw_enable = 1`**.
- **Diagnostics, group 108** (ids 2192-2195): field 1 `f_zw` in % (formula
  0x21, A = 100), field 2 `dzw_e` in °CA (use the formula the stock final-angle
  variable for 0x7FEF87 uses if `logging/med9kwp/vag_formulas.py` decodes it,
  else 0x36 as a signed count and say so), field 3 `max(dwkrz[0..5])`
  (same formula as field 2), field 4 `0x7FD31B & 3` (formula 0x36). Same
  handler pattern as `ff_diag.c`; extend `build.data` with a second
  `u32_syms` entry and four group words.
- **Model.** Extend `emu/models/flexfuel.py` (`Cal` gains the new fields; the
  tick computes `dzw_e`; add `fzw_of()` and `dzw_of()`), so
  `tests/test_ff_fuel_patch.py`'s tick-by-tick comparison covers it without a
  second model.

## Tasks

1. **Time-boxed side task (≤ 45 min), first:** `re/findings/scheduler.md`
   §11.7 asks which task set is live. 2026-09-17 check: `hdrpsol_main`
   0x45822C (the rail setpoint) has exactly one caller, 0x45CC08 in set A's
   20 ms task 0x45CAC4, and `zwstt` builder 0x431294 has exactly one caller,
   0x432B04 in set A's 10 ms task 0x4328E4 (`tools/find_branch_refs.py`).
   If no set-B task reaches any writer of `prsoll` 0x8031F4 or of `zwstt`
   0x802096 (`tools/sda_xref.py --var`, `tools/callgraph.py --reach` from
   0x1205A0 / 0x120FAC / the set-B descriptors of `ercosek_tasks.py`), the
   engine cannot run on set B and **set A is live by necessity**. Record the
   result with the commands as a dated §11.8 (VERIFIED-STATIC if it holds,
   with the caveat that a bench read still confirms it); do not change any
   hook. If it does not hold, write what you found and move on.
2. `patches/ff_fuel/src/ff_ign.c`, the trampoline in `hooks.S`, the `ff_state`
   field(s) after +0x40's neighbours (never move an existing offset; keep the
   annex/core split documented in `ff_state.h`), `ffcal001.py` v2,
   `patch.json` (`onchip_edit` on the new hook; the two `build.data` entries),
   README sections (hook table now four words, three of them on-chip;
   instruction counts; RAM; FFCAL001 v2 table).
3. Model + tests: `tests/test_ff_fuel_patch.py` (or a new
   `tests/test_ff_ign_patch.py`): (a) stub with `dzw_e = 0` reproduces
   `zwgru_build` bit for bit over the grid `tests/test_zw_model.py` uses;
   (b) `dzw_e = ±k` gives `clamp_s8(stock + k)`; (c) return lands at
   0x41D410 with r3 as expected and r10/r1 untouched; (d) invalid state block
   → adds 0; (e) tick: `dzw_e` equals the model over a frame sequence that
   crosses OK → FAULT → OK, including the immediate drop; (f) `ff_zw_enable =
   0` and, separately, enable = 1 with the neutral map: whole-SRAM diff shows
   no difference outside the state block; (g) group 108 through the
   dispatcher and `21 6C` through `ecu_sim.py --sim-dump`; (h) cost per
   segment and per activation, asserted.
4. `test/procedure_e1.md`: what group 108 and the logger show with the
   feature off and on; the knock-logging recipe (dwkrz, 0x7FCE76, 0x7FD31B,
   zw) for calibrating `dzw_E` from +0 towards +2 ° only in cells where
   `dwkrz` stays 0; the `KFZWOP - KFZW` budget table as the hard ceiling.
   Add the new variables to `logging/sessions/ff_fuel.json` with
   `patch_offset`.
5. Dated notes: docs/05 §3.4 (implemented, formats, deviations), docs/05 §4
   (FFCAL001 v2), ignition.md §11 (absolute addressing; the site is taken),
   measuring_vars.md §8 (group 108 / ids 2192-2195 taken), `re/symbols.csv`
   rows for anything new. Commit on `agent/E1` after every step; comment on
   #34 and #37.

## Acceptance
Builds; applies with `ALL OK (65 blocks)` and a clean `bindiff`; every hook
word and the blob disassembled and free of r2/r13; the whole suite passes;
`zwgru` is bit-identical with the feature off and with the feature on at
neutral calibration; FAULT zeroes the offset within one activation; the
README states the four changed words, RAM use, instruction counts and the
FFCAL001 v2 layout.
