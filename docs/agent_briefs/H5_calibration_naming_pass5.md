# Brief H5 — Calibration naming pass 5: the `%ATM` pipe segments, the `tmot` model, the remaining injection shaping; checklist draft 4 (#49, #43)

Four passes have named **462 of 1,066** detected tables, curves and axes (157 →
259 → 359 → 462; `re/findings/calibration_names.md` §11.9 counts). #41 closed with
its exit criterion met; **#49** carries the passes that follow, under the same
rules. G4 left three blocks half-named that a tuner or the flex-fuel work will
touch: the `%ATM` exhaust-gas-temperature model (53 of its objects named, the
pipe-segment wall-loss curves, heat capacities and `FATMV*` tables not), the
`tmot` model block `FUN_000F90C8` (~50 thresholds whose units are now known:
0.75 °C/LSB − 48 and 3/128 K), and the remaining `%RKTI` / `%ZGST` injection
shaping and `%GK` running-mixture maps. Wave H pair 2, parallel with H2; **after
H1 is merged** (H1 edits `calibration_names.md` §10.2/§11.8 and adds rows for the
lambda-request maps — read them, do not redo them). Desk only.

Read `00_common_rules.md`, `re/README.md` ("Using it in TunerPro"),
`re/findings/calibration_names.md` **§9 (method) and §11 (G4's pass, esp. §11.6
%ATM, §11.7 %BBGANG, §11.9 counts and reproduction commands) in full**, plus H1's
new section, `re/findings/calibration_coverage.md`, `calibration_maps.md`,
`fr_index.md` (`%ATM`, `%BGTMOT`, `%RKTI`, `%ZGST`, `%GK`, `%TEB`),
`re/findings/injection.md` §11-§12, `re/findings/measuring_vars.md` §10,
`re/findings/tuning_checklist_draft.md` (draft 3), `tools/cal_show.py`
(`--guess`, `--raw`), `tools/draft_to_xdf.py` (`FR_MODULES` gained `TEB`/`BBGANG`
at wave-G integration — add a module key only when you name a block of it),
`tests/test_draft_to_xdf.py` (the RENAMED rule), `tests/test_cal_show.py`, and
the Ghidra copy recipe in `injection.md` §0.

## Targets, in order
1. **`%ATM` completion** (`FUN_001043C8`, 0x1043C8-0x1081FF, both banks): the
   pipe-segment objects — wall-loss curves, heat capacities, `FATMV*` velocity
   tables, the exotherm sections. Settle G4's §11.6 HYPOTHESIS on which section is
   the pre-catalyst model and which the Grenzkat reference (the consumer that
   compares against a threshold decides it). Units: u16 K at 3/128 K per LSB
   (G4, VERIFIED-STATIC) — check each new object lands on whole °C the same way.
2. **The `tmot` model** (`FUN_000F90C8`): the ~50 thresholds and substitute
   values; name against `%BGTMOT`/`%TMOT`-family FR labels; measuring ids where
   they exist (`re/measuring_vars.csv`).
3. **Injection shaping left over**: remaining `%RKTI` / `%ZGST` objects
   (`injection.md` §11 list), the `%GK` running-mixture maps F4 started (§10.3),
   the `%TEB` delay/mixing labels G4 left HYPOTHESIS (§11.5), and the
   vehicle-speed unit behind `nvquot_w` (§11.7).
4. **≥ 80 newly named objects** in total, every row with evidence and a tag;
   `cand_` discipline; descriptive names where no Bosch label is claimed; never
   "verify" one guess with another. Append to `re/calibration_names.csv` only;
   dated §12 in `calibration_names.md` with the counts table; mark settled "Open"
   rows in place.
5. **Checklist draft 4** (`tuning_checklist_draft.md`): the new objects in the
   maps / logs / limits columns; the `%ATM` thresholds as limits for the exhaust
   hardware changes of #43. Still HYPOTHESIS and a reading list.
6. `re/symbols.csv` rows for RAM cells newly named; `draft_to_xdf.py … -o
   work/med9_draft.xdf --extra-rows patches/ff_fuel/ffcal001_rows.csv
   --min-confidence hypothesis --validate` to **work/** (never over `re/`);
   suite green; comment on #49 and #43; commit after every finding.

## Ownership
`re/calibration_names.csv`, `re/findings/calibration_names.md`,
`tuning_checklist_draft.md`, dated notes in `injection.md` / `start.md` /
`measuring_vars.md`, `tests/test_cal_show.py`, `tests/test_draft_to_xdf.py`,
`tools/draft_to_xdf.py` `FR_MODULES` (additive keys only). Do **not** edit
`re/calibration_draft.csv`, `re/med9_draft.xdf` (integrator), `patches/**`,
`eeprom.md`, `flash_programming.md`, `kwp.md`, docs/08 (H2 runs alongside you),
`obd.md`, `logging/**`.

## Acceptance
≥ 80 new objects named with evidence and tags; §11.6's exotherm question settled or
its exclusion recorded; `tmot` model thresholds named; checklist draft 4;
`draft_to_xdf --validate` on the work copy passes; suite green; XDF and draft
untouched on the branch; dump untouched. The integrator regenerates the XDF and
carries any RENAMED labels into the draft at merge.
