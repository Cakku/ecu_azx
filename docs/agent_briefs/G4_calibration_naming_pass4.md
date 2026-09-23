# Brief G4 — Calibration naming pass 4: settle the two open units (0x7FD3E5, `cand_KFMIRLINV`), name ≥ 80 more objects, and close the #43 checklist gaps (#41, #43)

The calibration definition has grown pass by pass: 157 (D3) → 259 (E3) → 359
(F4) named objects; **876 of 1,066 tables/curves/axes are still `cand_*`**
(`re/findings/calibration_names.md` §10.0). Two specific units were left open
by F4 and each was called "a short job for the next brief". This brief does
those two first, then continues the naming pass, and folds the results into the
tuning checklist (#43). Wave G pair 1, parallel with G1. It **owns
`re/calibration_names.csv` and `re/findings/calibration_names.md`**; no other
wave-G brief edits those. It does **not** regenerate `re/med9_draft.xdf` (the
integrator does that at merge time). Desk work only.

Read `00_common_rules.md`, `re/README.md` ("Using it in TunerPro"),
`re/findings/calibration_names.md` **§9 and §10 in full** (the method and the
open loose ends), `re/findings/calibration_coverage.md`,
`re/findings/calibration_maps.md`, `re/findings/fr_index.md`,
`re/findings/start.md` §8 (the 0x7FD3E5 / 0x7FD3F7 open row),
`re/findings/injection.md` §11, `re/findings/rail.md` §13,
`re/findings/measuring_vars.md` §7, `re/findings/tuning_checklist_draft.md`,
`tools/cal_show.py` (`--guess`, `--raw`), `tools/draft_to_xdf.py`,
`tools/callgraph.py`, `tools/sda_xref.py`, `tests/test_draft_to_xdf.py`,
`tests/test_cal_show.py`, and the Ghidra copy recipe in
`re/findings/injection.md` §0.

## The two units to settle first (F4 named both as the next brief's job)
1. **0x7FD3E5 — probably a battery voltage at 1/16 V per LSB**
   (`calibration_names.md` §10.6, and §10 near line 734). F4's plan, verbatim:
   decompile its writers **0x0F8FD4, 0x11A990** (and the third writer F4
   names) and read the fixed point off the store. It feeds the (all-zero) 1D
   `zwstt` term and the 0x5D3610 map (`start.md` §8), and `start.md` §4 has the
   1D additive term over it (axis 0x5C7BA5 = 40,80,120,160,200,240). Settle the
   unit VERIFIED-STATIC from the writers, or record the exclusion set. If it is
   a voltage, name it (`tans`/`ubatt`-family per the FR) and correct the
   `start.md` §8 open row **SETTLED** in place.
2. **`cand_KFMIRLINV` 0x5C9938's value unit** (`calibration_names.md` §10.6).
   F4's remaining lead: **0x8015AF**, the divisor written at 0x0E0D5C and
   0x104224 with reference value 200; naming 0x8015AF (an ambient or manifold
   pressure, most likely) fixes the unit of the whole block. Decompile those
   two writers, name 0x8015AF, and back-propagate the unit to `KFMIRLINV`.
   Settle it or record the exclusion.

## The naming pass (continue F4's method)
- Target **≥ 80 newly named objects** (name + unit + scale where provable),
  every row with its evidence column filled and a status tag. Favour objects
  that a tuner actually touches, in this order: the remaining `%RKTI` / `%ZGST`
  injection shaping (`injection.md`), the `KFPRSOL*` variant selection names
  (`rail.md` §13 lists the HYPOTHESIS names for the six-way `0x7FB69A` switch —
  upgrade what the selection logic proves), the `%GK` running-mixture maps
  F4 started (§10.3), and the operating-mode maps keyed on 0x80223B (§10.6,
  now VERIFIED-STATIC as the mode index — the eight modes themselves are still
  unnamed and one drive log settles them; name what is static).
- Keep the `cand_` discipline: a `cand_` prefix means the Bosch label is a
  candidate; a descriptive name (`mix_*`, `rl_*`, `axis_*`, `zwdelta_*`) means
  no Bosch label is claimed. Do not "verify" one guess with another
  (`04_re_guidelines.md`).
- Append new descriptor rows to **`re/calibration_names.csv`** only (never
  `re/calibration_draft.csv` — that is the integrator's merge, and F4/E3 kept
  the two separate). Add dated sections to `calibration_names.md`; mark any
  "Open" rows you settle **SETTLED (date, G4, section)** in place.

## The #43 checklist (draft 3)
`re/findings/tuning_checklist_draft.md` is draft 2 (F4). Add the objects this
pass names to the right rows of its three columns (maps / logs / limits), and
add the two now-settled units where they matter (0x7FD3E5 to the ignition/start
rows, `KFMIRLINV` wherever its block is a limit). Keep it labelled HYPOTHESIS
and a reading list — it is not a calibration procedure until the bench and a
car exist (#43's exit). Do not tick #43's "apply to the first hardware change".

## Tasks, in order
1. Settle 0x7FD3E5 (writers 0x0F8FD4 / 0x11A990 / the third) — unit or
   exclusion; correct `start.md` §8.
2. Settle `cand_KFMIRLINV` via 0x8015AF (writers 0x0E0D5C / 0x104224) — unit or
   exclusion; correct `calibration_names.md` §10.6.
3. Name ≥ 80 more objects, evidence and tags per row, into
   `re/calibration_names.csv`; dated sections in `calibration_names.md`.
4. Fold the results into `tuning_checklist_draft.md` (draft 3).
5. `re/symbols.csv` rows for any RAM cells newly named (0x8015AF, 0x7FD3E5 if
   settled, …). Run `python3 tools/draft_to_xdf.py re/calibration_draft.csv -o
   work/med9_draft.xdf --extra-rows patches/ff_fuel/ffcal001_rows.csv
   --min-confidence hypothesis --validate` **to work/** (never over `re/`) to
   confirm your rows do not break the generator; do not commit the XDF.
6. `python3 -m unittest discover -s tests`. Comment on #41 and #43; commit
   after every finding.

## Acceptance
Both open units are VERIFIED-STATIC or have a recorded exclusion set with the
commands; ≥ 80 new objects named with evidence and tags; the tuning checklist
is draft 3 with the new objects and units; `draft_to_xdf --validate` on a
work-copy passes; suite green; `re/med9_draft.xdf` untouched on the branch;
dump untouched.
