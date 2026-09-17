# Brief E3 — Calibration definition, naming pass 2 (#41; prepares #43)

Issue **#41**, continued: brief D3 named 157 objects and left leads. Wave E
pair 1, runs in parallel with E1 (E1 never touches `re/calibration_*.csv`;
you never touch `patches/`). Desk work; Ghidra in a private copy
(`re/findings/injection.md` §0, `calibration_names.md` §8). No `sudo`.

Read `00_common_rules.md`, `re/README.md` (the TunerPro section and the
"contributing a name" recipe), `re/findings/calibration_names.md` (all, §7
is your task list), `re/findings/calibration_maps.md`,
`re/findings/calibration_coverage.md`, `re/findings/fr_index.md`,
`re/findings/rail.md` §3, §10, §12.3, `re/findings/injection.md` §9,
`re/findings/start.md` §4.2, `re/findings/measuring_vars.md` §7.4,
`tools/draft_to_xdf.py`, `tools/sda_xref.py`, `ghidra_scripts/decompile.py`.

## Facts
- `re/calibration_draft.csv` (6,556 rows, machine-generated, **do not edit**)
  + `re/calibration_names.csv` (157 rows, hand-maintained, **yours**), merged
  by `tools/draft_to_xdf.py`. Columns of the sidecar: `addr, name,
  name_confidence, name_evidence, unit, scale, offset, x_unit, x_scale,
  x_offset, y_unit, y_scale, y_offset, scale_confidence, fr_module, fr_page,
  description, x_axis_addr, x_n, x_elem, y_axis_addr, y_n, y_elem, signed`.
  991 of 1,066 tables/curves/axes still carry `cand_*`.
- D3's method that worked: pick a consumer that reads several maps,
  decompile it once, and the whole group gets axes and an FR module. The
  shared nmot × rl keys 0x7FD820 / 0x7FD84C (writer `FUN_000BDB58`) label
  every map read with them (§3).
- The FR PDF is local (`documents/MED9.1_TFSI_Funktionsrahmen.pdf`;
  `pdftotext -layout -f N -l N`); it is for the 2.0 TFSI, so a label is
  `hypothesis` until the role is read out of the disassembly **and** the FR
  declares exactly one label with that role and matching axes (§0).
- Open contradiction to settle: D3 found the u8 `rl` (0x7FEF74) is
  100/128 %/LSB (`SRL11OPUW` = `SRL12ZUUW` / 32, §2.1), while C3's reading of
  VCDS display formula 0x21 with A = 133 gives 100 % at 133 counts
  (measuring_vars.md §7.4). The ECU's own measuring handler for the `rl` id
  can be run in the emulator exactly as C3 did for formula 0x05 (§7.1) —
  that cross-check decides it.

## Tasks (priority order; each row with confidence, evidence, FR page)
1. **The torque ↔ load pair** (`MDBAS` `KFMIRL` / `KFMIOP` shape): the three
   16 × 12 u16 maps 0x5C91F2 / 0x5C9372 / 0x5C94F2 blended by
   `FUN_00434C14` with 0x7FD448 into 0x803522, and 0x5CA252 (16 × 11 on the
   KFZWOP grid, four consumers). Name them, their axes, and the RAM outputs
   (`mifa_w`/`mizsol`-style names are HYPOTHESIS until proven).
2. **The charge request and its limiters**: 0x5C8FAE (8 × 8 u16,
   `FUN_004336E0`, produces **0x803508** — the x input of every rail setpoint
   map, rail.md §3.1), and the charge limiters 0x80234C / 0x803358 / 0x803360
   feeding the min-chain at 0x0C7CF8 (`FUN_000FBE74` and friends; rail.md
   §10, §12.3). Deliver the map behind each limit with its axis and unit.
3. **The lambda path**: `LAMSOLL`/`lamsbg_w`, `KFLBTS`, the WOT enrichment.
   Enter from the measuring variables (`re/measuring_vars.csv`: the lambda
   ids A3 found) and from the exhaust-temperature model (0x4594E8 reads the
   knock retard; ignition.md §13.3), not from `rk`. If the request really
   arrives through the torque/efficiency cascade (start.md §4.2), name that
   cascade's maps instead and say so.
4. 0x5C9938 (inverse-map shape, `FUN_000E069C`), the nine 14 × 14 u8 maps
   0x5C430D…0x5C4A1D (`FUN_00424CD0`, three-temperature-threshold state),
   0x5D863C (`FUN_0045FAA8`).
5. Settle the u8 `rl` scaling contradiction in the emulator; write the result
   in measuring_vars.md §7.4 and calibration_names.md §2.1 as a dated note
   (whichever side wins, the other note is corrected in place).
6. Every map E1/E2/E5 will need is already named (KFZW, KFZWOP, KFKSTT,
   KFWKSTT, KFZWSTT, KFPRSOL*, KLPRMAX); check their axis rows carry units
   and scales so the XDF shows them physically. Do **not** add FFCAL001 rows
   (they come from `patches/ff_fuel/ffcal001_rows.csv`).
7. Goal: **≥ 100 new named objects** with a unit, or a documented reason per
   lead why not. Update `calibration_names.md` with a §9 "pass 2" (method,
   counts before/after, the leads closed, new leads), append `re/symbols.csv`
   rows (union merge at integration), dated notes in the findings files whose
   leads you close, and extend `tests/test_draft_to_xdf.py` if the tool
   needed a change (e.g. a new column). Do **not** regenerate
   `re/med9_draft.xdf`; run `python3 tools/draft_to_xdf.py
   re/calibration_draft.csv -o work/x.xdf --min-confidence hypothesis
   --extra-rows patches/ff_fuel/ffcal001_rows.csv && python3
   tools/draft_to_xdf.py --validate work/x.xdf` before the report.
8. For #43: a half-page `re/findings/tuning_checklist_draft.md` listing, per
   hardware change (intake, exhaust, cams, injectors), which of the maps
   named so far matter — HYPOTHESIS level, a starting point only.
   Commit on `agent/E3` after every group; comment on #41.

## Acceptance
The sidecar grows by ≥ 100 named rows (or the report explains each lead),
the definition builds and validates, the test suite passes, the `rl`
contradiction is settled with the emulator run cited, and every new name
carries its evidence and FR page.
