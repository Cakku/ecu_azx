# Brief D3 — Calibration definition for 1037382557 (#41): naming pass and a scaled XDF

Issue **#41** (Phase 6; the "opens in TunerPro" check is the human's). Desk
work; **runs alone with respect to `re/calibration_draft.csv`** — no other
agent may edit that file at the same time. No `sudo`; Ghidra optional (copy
the project, `re/findings/injection.md` §0).

Read `00_common_rules.md`, `re/README.md`, `re/findings/calibration_maps.md`,
`re/findings/calibration_coverage.md`, `re/findings/fr_index.md`,
`tools/draft_to_xdf.py` and `tests/test_draft_to_xdf.py`, and the map
sections of `re/findings/{injection,ignition,start,rail}.md`.

## Facts
- `re/calibration_draft.csv`: 6,556 rows (1,066 tables/curves/axes plus
  scalars), columns `addr,kind,x_axis_addr,y_axis_addr,x_n,y_n,elem_size,
  signed,consumer_func,name_or_blank,confidence,evidence,x_elem,y_elem,
  struct_addr,sites`; **78 rows carry a name** today (waves B6-B9).
  `tools/draft_to_xdf.py` emits raw counts, no scaling, file offsets.
- `re/symbols.csv` names 572 functions and objects; `consumer_func` links a
  table to its reader, and a named reader gives the FR module of the table.
- The FR (`documents/MED9.1_TFSI_Funktionsrahmen.pdf`, local, 55 MB;
  `pdftotext` per fr_index.md) lists map names, axes and units per module.

## Tasks
1. Add `unit, scale, offset, x_unit, x_scale, x_offset, y_unit, y_scale,
   y_offset, fr_module, description`. Either extend the draft (and make
   `ghidra_scripts/enumerate_maps.py` preserve hand-added columns by merging
   on `addr`) or add a sidecar `re/calibration_names.csv` merged by
   `draft_to_xdf.py`; choose the sidecar if that is simpler and say so.
2. Naming pass in priority order, each row with confidence and evidence (FR
   page and matching axes/units, or the consumer's identity):
   (a) every map the project has touched — check axes, units and scales
   against the findings (0.75 C/LSB -48 C, 0.005 bar, 1 us, 1024 = 1.0,
   s8 x 0.75 deg, ...);
   (b) the major fuelling / ignition / limiter maps: lambda targets, the
   torque <-> load pair, charge and torque limits, speed/rpm limiters, WOT
   enrichment, the knock maps (`KFSWKFZK/KFSWKFZKR/KFDZK`, ignition.md), rail
   (`KFPRSOL*`, `KLPRMAX`, `VHDPMX`, `VMSVMX`), start (`KFKSTT`, `KFWKSTT`,
   `KFZWSTT`); FR names stay HYPOTHESIS until axis units and consumer agree;
   (c) the shared nmot / rl breakpoint blocks named after their FR axis
   names (`SNM16ZUUW` / `SRL12ZUUW` style), which labels every map using
   them.
3. `tools/draft_to_xdf.py`: emit `<MATH equation="X*scale+offset">`, units,
   axis labels, descriptions and categories per FR module; add the FFCAL001
   block from `patches/ff_fuel/ffcal001.py`'s descriptor if D1 is merged
   (else from docs/05 §4 as hypothesis); keep `--validate` and add tests for
   scaling and categories.
4. `re/README.md`: using the XDF in TunerPro (file offsets, run
   `tools/checksum.py fix` after saving, `bindiff` + `verify` before any
   flash), the confidence legend, how to contribute a name.
5. Do **not** regenerate `re/med9_draft.xdf` in your branch (the integrator
   does at merge time); commit the CSV/sidecar and the tool. Commit on
   `agent/D3`; comment on #41.

## Acceptance
Every map named in `re/findings/*.md` appears with name, scale, unit and
consumer; the major fuelling / ignition / limiter maps are named with tags;
the generator's tests pass and a generated XDF validates.
