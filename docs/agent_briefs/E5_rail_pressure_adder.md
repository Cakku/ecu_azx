# Brief E5 — Rail-pressure setpoint adder and injection-window diagnostics, desk half (#36)

Issue **#36**, the software half, **trimmed**: an E-dependent setpoint adder
inside the stock ceiling plus the diagnostics that tell whether the pump
follows. The torque limiter of docs/05 §3.6 and any `KLPRMAX` raise are
**out of scope** until bench data exist (rail.md §12.3, §14.4). Wave E
pair 3, **after E2 is merged** (same files); parallel with E6. No `sudo`;
nothing is flashed.

Read `00_common_rules.md`, `docs/05_flexfuel_design.md` §3.2, §3.6 (+ B9
note), §4, `re/findings/rail.md` §1-§3, §7, §8, §11, §12, §14, §15,
`emu/models/window.py`, `tests/test_window_model.py`, `patches/ff_fuel/**`
as E2 left it, `emu/models/flexfuel.py`, the ff_fuel tests,
`re/findings/measuring_vars.md` §7.3 and §8, `re/findings/scheduler.md` §11.8
(E1's task-set result, if present).

## Facts (VERIFIED-STATIC unless marked)
- `hdrpsol_main` **0x45822C** builds the setpoint; its **only caller** is
  `bl` at 0x45CC08 in set A's **20 ms** task 0x45CAC4 (on-chip). Chain
  (rail.md §3.2-§3.5): map (`KFPRSOLHOM` 0x5D5324 in normal running; six
  variants by mode bits) + `KFPRSOLOFF` × fade → `prsoll_raw` → **0x8031F0**
  → floor 7000 / ceiling `KLPRMAX` 0x5D5546 = 22000 (35..110 bar) →
  **0x8031F2** → pump-volume rate limit → **0x8031EE** → `prsoll` 0x8031F4.
  Unit **0.005 bar/LSB**. `KFPRSOLHOM` tops out at 19000 = 95 bar: +15 bar
  of headroom inside the ceiling = 7.6 % more flow — a mixture-preparation
  and duty measure; the fuel mass comes from `rk` (§12.1).
- Insertion: **after `prsoll_raw` is formed and before the KLPRMAX/floor
  clamp** (the store to 0x8031F0 or the compare against 0x8031EC — read the
  disassembly and pick the instruction; document live registers and LR
  state; on-chip → `onchip_edit`). Adding there keeps the ceiling, the
  floor and the rate limiter in force.
- The dangerous case is the pump, not the map: `0x80316E` pinned at
  `VMSVMX` = 5000 means saturation; `prist` (0x8031DA) below **13.0 bar**
  (`PRWBHMX` 0x5D3CDC = 2600) arms the angle clamp, the driver cut-off and
  the fault charge limit **all at once** (§14). Acceptance signals: `prist`
  > 13 bar always, `0x80316E` < 5000, `0x8031F6` (spare volume) > 0.
- Window quantities (angle LSB 3/128 °CA): `wbho1s` 0x80307E (start of
  injection), `dwi` 0x803088 (duration as angle), margin = `wbho1s - dwi -
  2144` (2144 = 67 × 32 = 50.25 °CA); `ti_sum` 0x8030C4 (1 µs). Neither
  `dwi` nor `wbho1s` has a measuring id (§11). Modelled in
  `emu/models/window.py`.
- FFCAL001 (v3 after E2): reserved `ff_prail_add` 8 × u8 (0.1 MPa) at +0xDC
  with no axis — awkward. Append a proper table instead (below).
- Measuring block: **group 109** (0x6D; words 0x5C55F2, 0x5C57F0, 0x5C59EE,
  0x5C5BEC, `calibration_edit`) and **ids 2184-2187** (table words
  0x0A7878-0x0A7887, stock `00 03 8E C4`); re-check with
  `measuring_vars.py --free`. Formula 0x53 = `((A<<8)|B) × 0.01 bar` is
  CROSS-CHECKED (measuring_vars.md §7.3) — use it for pressures.

## Design decisions (deviate only with a reason in the report)
- **Producer in the 10 ms tick** (`src/ff_rail.c`): `prail_add =
  interp(ff_prail_curve, e_filt)` (u16, 0.005 bar/LSB, 17 points on the E
  grid like `ff_F_curve`, default all 0), clamped to `ff_prail_max` (default
  3000 = 15 bar), **0** when `ff_prail_enable == 0`, `cal_ok == 0`, or mode
  not OK/HOLD/OVERRIDE; drops to 0 **immediately** when the mode leaves
  OK/HOLD (#37 rule). Also every activation: `win_margin_min` (the minimum
  of `wbho1s - dwi - 2144` over the last `ff_diag_window_ms`, default
  1000 ms, s16 angle LSB), `msv_sat_ticks` (activations with 0x80316E ≥
  5000 in the same window, u8), `prist_min` (u16, same window). All in new
  `ff_state` fields appended after E2's.
- **Consumer**: a trampoline at the chosen instruction that adds `prail_add`
  (absolute load from the state block, magic checked) to `prsoll_raw` with
  saturation at 0xFFFF and re-does the displaced instruction; scratch
  registers only; ≤ 12 instructions. `prail_add = 0` → bit-identical.
- **FFCAL001 v4.** Append `ff_prail_enable` u8 (default **0**),
  `ff_prail_max` u16, `ff_diag_window_ms` u16, `ff_prail_curve[17]` u16.
  Leave the reserved 8-byte `ff_prail_add` in place, unread, and mark it
  "superseded by ff_prail_curve" in `ffcal001.py`'s notes and the rows.
  Bump version/length as before.
- **Diagnostics, group 109** (ids 2184-2187): field 1 `prail_add` in bar
  (0x53), field 2 `prist_min` in bar (0x53), field 3 `win_margin_min` in
  °CA (E1's angle formula, or a signed count), field 4 `msv_sat_ticks`
  (0x36 count).
- **Model**: extend `emu/models/flexfuel.py` for `prail_add` and the three
  window statistics; use `emu/models/window.py` for the margin arithmetic.

## Tasks
1. Pick and document the insertion instruction (disassembly listing, live
   registers, LR, the six map-selection paths all pass through it). Dated
   note in rail.md §12.1.
2. `src/ff_rail.c`, the trampoline, `ff_state` fields (append), `ffcal001.py`
   v4, `patch.json` (`onchip_edit`; `u32_syms`; group words), README (hook
   table, counts, RAM, v4 layout, and a plain sentence on how many words are
   on-chip now).
3. Tests (`tests/test_ff_rail_patch.py`): (a) `hdrpsol_main` on the applied
   image with `prail_add = 0` reproduces the stock 0x8031F0/0x8031F2/0x8031EE
   /0x8031F4 bit for bit for a grid of `nmot_w`, 0x803508, 0x7FD3F7 and
   every mode-bit path; (b) `prail_add = 2000` raises 0x8031F0 by exactly
   2000 and 0x8031F2 never exceeds 22000; (c) invalid state block → stock;
   (d) tick equals the model for `prail_add`, the FAULT drop, and the window
   statistics over a synthetic `wbho1s`/`dwi`/0x80316E/`prist` sequence;
   (e) enable off / on with neutral curve: whole-SRAM diff clean; (f) group
   109 via the dispatcher and `21 6D` via `ecu_sim.py --sim-dump`; (g) costs.
4. `test/procedure_e5.md`: the log list of rail.md §11 in `ff_fuel.json`
   terms (add them with `patch_offset`/addresses), the "pump saturated"
   decision rule, the 13 bar hard stop, and a calibration recipe that raises
   `ff_prail_curve` in 2 bar steps at E85 while `0x80316E` stays below 5000.
   Write a half-page design note (not code) for the torque limiter at the
   min-chain 0x0C7CF8 for a later brief.
5. Dated notes: docs/05 §3.6 and §4 (v4), rail.md §12, measuring_vars.md §8
   (group 109 / ids 2184-2187), `re/symbols.csv`. Commit on `agent/E5` after
   every step; comment on #36.

## Acceptance
Builds; applies `ALL OK`; clean `bindiff`; the hook word and blob disassembled
and free of r2/r13; suite green; the setpoint chain is bit-identical with the
adder off and with it on at zero; the ceiling holds under any calibration;
the diagnostics match the model; the README and procedure state the pump and
13 bar acceptance signals.
