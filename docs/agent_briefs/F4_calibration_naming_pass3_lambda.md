# Brief F4 — Calibration definition, naming pass 3: the lambda path, the next hundred objects, and the #43 checklist with its logging and limit columns (#41, #43)

Two passes have named 259 of the ~1,066 detected calibration objects
(`re/calibration_names.csv`; `re/findings/calibration_names.md` §9.0). The
one path the flex-fuel work needs and still cannot see is the **lambda
target**: `LAMSOLL` / `lamsbg_w`, `KFLBTS`, the WOT enrichment. E3 narrowed
it to a single thread (§9.5): the fuel path's lambda entry is the Q7 scalar
`fgru_trim` 0x801CF2, built without any map by `FUN_000E8D9C` from
`cand_KFGRUTRIM` 0x5D350C (= 128) and the byte **0x7FD066**, which has no
writer that `sda_xref --var` or `find_abs_refs --target` can see — it is
written through a pointer. Pull that thread first. Wave F pair 1, parallel
with F1. Desk work only; do not regenerate `re/med9_draft.xdf`.

Read `00_common_rules.md`, `re/README.md`, `re/findings/calibration_names.md`
(all, §7 and §9 closely), `re/findings/calibration_maps.md`,
`re/findings/injection.md` §4-§6 and §9, `re/findings/measuring_vars.md`
§7-§8 (the "measuring handlers are a scaling oracle" method),
`re/findings/rail.md` §3, §9-§10, `re/findings/ignition.md` §13,
`re/findings/tuning_checklist_draft.md`, `re/measuring_vars.csv`,
`tools/draft_to_xdf.py`, `tools/enumerate_maps.py`, `tools/sda_xref.py`,
`tools/find_abs_refs.py`, `tools/callgraph.py`, `tests/test_draft_to_xdf.py`,
`documents/` (the MED9.1 Funktionsrahmen index from brief A2: sections
`%LAMBTS`, `%LAMFAW`, `%LAMKO`, `%ATM`, `%BGSRM`, `%LLR`, `%KR*`).

## Facts
- Counts after E3: 259 named (57 `static` labels, 197 `static` scalings),
  958 of 1,066 tables/curves/axes still `cand_*` (`calibration_names.md`
  §9.0). The sidecar is hand-maintained; `enumerate_maps.py` rewrites the
  draft and blanks names, so names live only in the sidecar. Both CSVs must
  keep **LF** line endings (E3 lost a commit to `csv.DictWriter`'s CRLF).
- Excluded already (§9.5): the lambda request is not in the fuel path
  (`gk_rk` 0x41AA48 multiplies by `fgru_trim`, whose only producer is
  `FUN_000E8D9C`: `mul_shr15_sat(cand_KFGRUTRIM, 0x7FD066 × 64 + 0x6000)`),
  and not reachable from the knock retard (0x4594E8 is a comparison in the
  knock load window, already corrected in `ignition.md` §13.3 by E2).
- Leads E3 left: (a) the writer of 0x7FD066; (b) `%LAMBTS` through the
  exhaust-temperature model (`ATM`, FR p2259); (c) the controller outputs
  `fr_w` 0x802DF8 / 0x802E00 traced backwards to the setpoint input.
- Loose ends from §9: which event 0x7FE95B marks (it reloads the `KFMIRL`
  blend counter with `cand_ZRLMIRLUM` = 100); the unit of
  `cand_KFMIRLINV` 0x5C9938's value (axes proven); the eight-valued index
  0x80223B (measuring id 130, VCDS 051.3 / 068.3, format 0x36).
- The method that paid off (§9): pick a consumer function that reads
  several objects, decompile it once, read axes from the image, fix the
  fixed point from the ECU's own measuring handler for the same variable.
  The shared nmot × rl breakpoint blocks (§3) give axes to any map read
  with the 0x7FD820 / 0x7FD84C key pair.
- #43's deliverable is a checklist "per modification type: which maps,
  which logs, which limits to watch". The draft has the maps and the limits
  in prose; it has **no logging column** (measuring ids / RAM cells) and its
  limits are not separated out.
- Evidence rules: a Bosch label taken from the 2.0 TFSI FR is `hypothesis`
  even when the map is understood; a `static` label needs the ECU's own
  arithmetic or an unambiguous FR structure match (D3's two-tag legend).

## Tasks
1. **The writer of 0x7FD066.** Enumerate byte stores through non-SDA base
   registers whose base traces to 0x7FD000-0x7FD0FF (`lis 0x7FD0`/`0x7FD1`
   + `addi`, or a pointer word in flash — `find_abs_refs --range 0x7FD040
   0x7FD080` also for *pointer words*, not only instruction immediates), and
   array writers whose element stride would land on 0x7FD066 (per-bank or
   per-cylinder lambda structures). Follow the writer to its maps. Deliver
   the lambda setpoint chain from map to `fgru_trim` with every RAM cell
   and calibration object named, VERIFIED-STATIC, or the exclusion set.
2. **`%LAMBTS` and `%ATM`**, then **`fr_w` backwards** (leads b and c) —
   until the target maps are found or each is time-boxed out (≤ 3 h each).
3. **The three loose ends** (0x7FE95B, `cand_KFMIRLINV` unit, 0x80223B):
   settle or record what was tried.
4. **≥ 100 further named objects** by the consumer-group method, in this
   priority: the lambda / enrichment family (whatever task 1-2 opens),
   `%ATM`, catalyst heating and start-related groups (`%BGSRM`, the
   `KFWKSTT`/`KFWKSTN` neighbours, because E85 cold start is Phase 5's
   hardest problem), idle (`%LLR`), knock control (`%KR*`: `cand_KFSWKFZK`
   family is already named — its neighbours are not). Tag honestly.
5. **#43 checklist, second draft.** Restructure
   `re/findings/tuning_checklist_draft.md` so that every modification type
   has three columns: *maps* (named object, address, what moves), *logs*
   (VCDS group/field or measuring id from `re/measuring_vars.csv`, or the RAM
   cell + session file for the DDLI logger, with the check to apply), and
   *limits to watch* (the protection or clamp that bites, with its address
   and current value). Keep every row HYPOTHESIS unless the finding files
   say otherwise; do not promote it to `docs/`.
6. **Tests and build**: `tests/test_draft_to_xdf.py` — the committed CSV pair
   builds and validates (`draft_to_xdf.py --validate`), 0 unmatched sidecar
   rows, any new FR-module categories covered; report the table/constant
   counts the integrator will see. `re/symbols.csv` rows for new functions
   and RAM cells; dated notes in `injection.md` §9 and `measuring_vars.md`;
   new `calibration_names.md` §10. Comment on #41 and #43; commit after
   every group named.

## Acceptance
The lambda-target chain is either named end to end with evidence or its
exclusion set is written down with the exact commands; ≥ 100 new sidecar
rows with units and tagged scalings; the definition builds and validates;
the #43 draft has the maps / logs / limits columns for all five
modification types; CSVs are LF; suite green; dump untouched.
