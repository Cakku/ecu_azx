# Brief H1 — The stock lambda-setpoint path: does this ECU ever ask for λ < 1 on the fuel path, and how does the ethanol factor compose with it? (#46; #32/#33 desk half)

Brief G4 re-read `gk_rk` while naming `KFATLAMS` and found the step F4's §10.2
had overlooked: **`rk = (rk << 12) / 0x80304A` whenever 0x7FEA33 is set**, and
0x80304A is with high probability **`lamsbg_w`, the lambda setpoint** — a value
below 1.0 enriches (`re/findings/calibration_names.md` §11.8). F4's conclusion
"there is no stock enrichment on the fuel path" therefore does not stand: the
*mechanism* exists; **whether any calibrated input ever requests λ < 1** on this
dataset (component protection `%LAMBTS`, full load, catalyst heating, a BDE mode
change) was not traced. The flex-fuel factor `F(E)` (`patches/ff_fuel`, hook
0x42247C) multiplies `rk` downstream of that division, so a stock request and the
ethanol factor compose multiplicatively — the design note in `docs/05` §3.3 and the
E85 blend procedure (#33) need to know when the stock ECU is already rich. Wave H
pair 1, parallel with H3. Static RE plus one design note; desk only.

Read `00_common_rules.md`, `re/findings/calibration_names.md` §9-§11 (**§10.2 and
§11.8 in full**), `re/findings/injection.md` §9 (the `gk_rk` step list) and §12
(G4's additions), `re/findings/start.md` §5.1 (B8's reading of 0x803046/0x803042
as *efficiency* setpoints — suspect), `re/symbols.csv` rows for `eta_coordinator`
0x442C18, `eta_mean_w`, `eta_mean`, `lamsbg_select` 0x41AF2C, `lamsbg_*`,
`re/findings/fr_index.md` (the `%LAMSOLL`, `%LAMBTS`, `%LAMKO`, `%ATM` entries),
`docs/05_flexfuel_design.md` §3.3, `re/findings/tuning_checklist_draft.md`,
`logging/sessions/tuning_checklist.json` (the `lamsbg_w` row added at wave-G
integration), `tools/cal_show.py`, `tools/sda_xref.py`, `tools/callgraph.py`,
`ghidra_scripts/decompile.py`, and the Ghidra copy recipe in `injection.md` §0.

## Facts (VERIFIED-STATIC per G4 unless stated)
- `gk_rk` divides by 0x80304A when 0x7FEA33 is set; 0x7FEA33 is set at 0x41AE3C on
  the 0x7FE920 branch (meaning of the branch: open).
- `lamsbg_select` 0x41AF2C writes 0x80304A = 0x803046 in homogeneous mode
  (`bdemod_w` 0x7FB69A bit 0), else 0x80340C clamped to [0x802AC0, 0x802ABE].
- 0x803046 / 0x803044 are built per bank by `eta_coordinator` 0x442C18 from
  candidates: a base value (0x803050 = 1.0, or `lamsbg_mode_change` 0.970 during
  a BDE mode change), component-protection-style inputs 0x803058 and
  0x801CC6/0x801CC4 (under 0x801CD4 bits 1/3 and 0x7FEA38), 0x80341E/0x80341C,
  0x7FED90/0x7FED8E, 0x801D2C/0x801D2A, 0x803412/0x803410 — clamped to
  [0x802AC0, 0x802ABE]; fixed values on `dwbho1smn_w` bits 0/1
  (`lamsbg_subst_dwbho` 1.008) and 0x8033FA bit 13 (`lamsbg_fixed_b1/_b2` 1.000).
- `%ATM` keys `KFATLAMS` with 0x80304A over a λ axis; the 12-point x axis
  0.65 … 1.20 of the 0x5C76D5 ignition map reads as a λ axis as naturally as an
  efficiency axis (start.md §5.1's reading is therefore in doubt).
- `lamsbg_w` is measuring id 43 (in no stock VCDS group; logged raw by
  `tuning_checklist.json` since wave G). G4 expects 1.000 at stoichiometric.

## Tasks
1. **The gate.** Settle when 0x7FEA33 is set (the 0x7FE920 branch at 0x41AE3C):
   which operating condition or mode makes the division active, and whether it is
   active in normal homogeneous running. Tag it.
2. **The inputs.** For each candidate input of `eta_coordinator`, find the
   producer function, the map(s) and axes it reads, and the enabling condition;
   name them against the FR (`%LAMBTS` `KFLBTS`/`TABGBTS`-style component
   protection, `%LAMKO`, catalyst heating, full load `%LAMFAW`, start). Keep the
   `cand_` discipline; every `calibration_names.csv` row carries evidence and a
   tag; no "verified by another guess".
3. **The verdict.** State VERIFIED-STATIC, with the conditions, whether any
   calibrated input on this dataset requests λ < 1 (and how far: the minimum the
   maps can produce), or record the exclusion set. Correct in place:
   `calibration_names.md` §10.2 (conclusion) and §11.8 (→ SETTLED), `start.md`
   §5.1 (efficiency vs λ), `injection.md` §9 step list (name the division).
4. **The design note.** `docs/05_flexfuel_design.md` §3.3, dated: how `F(E)`
   composes with the stock setpoint (multiplicative, downstream), what that means
   for the injection-window margin at WOT on E85 (the #33 rule "not above E50
   until `ti` and rail margins are confirmed" gains its stock-enrichment term),
   and whether `ff_fuel` should ever *not* apply `F(E)` when the stock target is
   already rich (state the argument; do **not** change the patch — that is a
   later decision for the human).
5. **The log recipe.** `tuning_checklist_draft.md` (draft +1: a "lambda request"
   row in the logs column) and `tuning_checklist.json`: add the request inputs
   you named so a WOT pull and a catalyst-heating phase show which one moves
   `lamsbg_w`. `re/symbols.csv` rows for everything newly named. Comment on #46
   and #33; commit after every finding. No tests change unless you add a tool.

## Ownership (nobody else edits these while H1 runs)
`re/findings/calibration_names.md` and `re/calibration_names.csv` (H5 runs in the
*next* pair, not alongside you), `injection.md`, `start.md`, `docs/05` §3.3,
`tuning_checklist_draft.md`, `logging/sessions/tuning_checklist.json`,
`re/symbols.csv` rows. Do **not** edit `patches/**`, `logging/*.py`, `obd.md`,
`can.md` (H3, running alongside), `eeprom.md`, `kwp.md`, `boot.md`, docs/07/08.
Never edit `re/calibration_draft.csv` or `re/med9_draft.xdf` (integrator).

## Acceptance
The gate condition and every candidate input of `eta_coordinator` are named with
evidence; a VERIFIED-STATIC verdict (or exclusion set) on λ < 1 requests exists;
§10.2/§11.8/start.md §5.1 corrected in place; docs/05 §3.3 carries the composition
note; the checklist and session file carry the log recipe; suite unchanged; dump
untouched; `calibration_draft.csv` and the XDF untouched on the branch.
