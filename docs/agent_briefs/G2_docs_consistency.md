# Brief G2 — Documentation consistency: land `zwdelta_load` in `ignition.md`, the adaptation-channel hazard in `docs/05`, and fix the drift notes the last two waves flagged (doc debt after wave F)

Every wave has ended with two or three "belongs in file X" and "file Y still
says the old thing" notes that no brief owned. F4 found an ignition term no
ignition document mentions; F4 found a workshop hazard that "belongs in docs/05"
but was only written into `calibration_names.md`; E7 found `patches/ff_fuel`
procedure prose that drifted from the README's hook table. This brief clears
that debt so the reference docs are internally consistent before an ECU makes
them load-bearing. Wave G pair 2, parallel with G3. It **owns
`re/findings/ignition.md`, `docs/05_flexfuel_design.md` §3.4/§3.7,
`docs/06_patch_pipeline.md` and the non-flashing sections of
`docs/07_workflow.md`**; it does **not** touch `re/calibration_names.csv`,
`calibration_names.md`, `rail.md` (G4 this wave), `patches/ff_fuel/**` (G1),
`logging/ecu_sim.py` or `boot.md` (G3/G5). Desk work only, no new facts —
this brief **moves and reconciles** facts that are already VERIFIED, with dated
notes, never rewrites.

Read `00_common_rules.md` (the "do not rewrite docs wholesale; add or correct
in place with tag and date" rule), `re/findings/ignition.md`,
`re/findings/calibration_names.md` §10.1 and §10.5 (the two facts to move — read
only, do not edit; G4 owns this file), `docs/05_flexfuel_design.md` §3.4 and
§3.7, `patches/ff_fuel/README.md` (the current eight-hook table — the source of
truth for the procedure drift), `patches/ff_fuel/test/procedure.md` §1 (the
stale text — but see the ownership note: G1 owns `patches/ff_fuel/**`, so you
**flag** this one, you do not fix it), `docs/07_workflow.md` §3.4 (already fixed
by F1 — confirm, do not re-touch), `docs/README.md`, `docs/agent_briefs/README.md`
"Open after wave F".

## The three items
1. **`zwdelta_load` (0x7FD338) belongs in `ignition.md`** (F4,
   `calibration_names.md` §10.5). The fact, already VERIFIED-STATIC in F4's
   pass: `FUN_00459334` writes the s8 ignition term at 0x7FD338, read by
   `zwbas_per_bank` 0x41D10C at 0x41D120 — an ignition angle at 0.75 °CA/LSB on
   top of `zwgru`. It is `zwdelta_7FD338_weight_map` 0x5D5F81 (load/speed gate,
   zero below 47 % charge) × `zwdelta_7FD338_map` 0x5D5FFB (−6.0…+2.25 °CA over
   speed and 0x7FD339) + `zwdelta_7FD338_add_map` 0x5D6075 (−3.75…+7.5 °CA over
   0x7FD3F7 and load, largest cold). Add a dated section to `ignition.md` placing
   this term in the ignition chain, and — importantly for brief E1's ethanol
   blend — note that **anything adding advance shares its budget** with this
   cold term (the blend and this term both add to `zwgru`). Cross-reference
   `docs/05` §3.4 (the E1 ignition design) so the shared-budget caveat is where a
   calibrator will see it.
2. **The adaptation-channel hazard belongs in `docs/05`** (F4,
   `calibration_names.md` §10.1). The fact: KWP adaptation channels 4, 8 and 10
   (RAM 0x7FD065 / 0x7FD067 / 0x7FD066) are ±10 % fuel/cranking trims, tester-
   writable and EEPROM-persistent, and **a workshop "basic setting" resets them
   to 128, silently changing the fuelling** (F4, and 0x7FD066 = adaptation
   channel 10 restored from EEP_CONF block 8). Add a dated note to
   `docs/05_flexfuel_design.md` (§3.3 fuel or §3.8 persistence, wherever a
   reader deciding the flex strategy will meet it): a flex-fuel calibration must
   account for these trims, and a basic-setting reset during service will move
   the mixture out from under the flex factor. This is a design hazard, not a
   patch change — no code.
3. **The `patches/ff_fuel` procedure drift** (E7 note in `docs/07` §3.4).
   `patches/ff_fuel/test/procedure.md` §1 still says "two of the three hook
   words are in the on-chip flash" and lists only 0x42247C and 0x432940; since
   E1/E2/E5 the patch has eight hooks, seven on-chip. **G1 owns
   `patches/ff_fuel/**` this wave** — so do **not** edit that file. Instead,
   verify the drift still exists, quote it in your report, and add one line to
   the wave-G integration notes so G1 (or the integrator) fixes procedure.md §1
   against the README's hook table. (If G1 has already fixed it by the time you
   check, say so and drop the item.)

## Tasks
1. Add the `zwdelta_load` section to `ignition.md` (dated, tagged; the facts are
   VERIFIED-STATIC per F4 — cite F4 and the addresses, do not re-derive) and the
   cross-reference in `docs/05` §3.4.
2. Add the adaptation-channel hazard note to `docs/05` (dated), cross-referenced
   from §3.3/§3.8.
3. Confirm `docs/07` §3.4 is F1-correct (it should be — F1 rewrote it); confirm
   `docs/06` §4's "a patch may hook one word per task set" dated note is present
   (F1 integration fix). Fix any remaining internal cross-reference that points
   at a superseded statement (e.g. a "three hook words" count in `docs/05` or
   `docs/06` prose), in place with a dated note. Do **not** invent counts —
   the README hook table is the source of truth.
4. Report the procedure.md §1 drift for G1/the integrator; do not edit it.
5. `re/symbols.csv`: add rows only for the three `zwdelta_7FD338_*` map objects
   if they are not already present (check first). No naming pass — that is G4.
6. `python3 -m unittest discover -s tests` (should be unchanged — this is docs).
   Comment on #34 (the ignition item) and #38/#41 as appropriate; commit after
   every item.

## Acceptance
`ignition.md` carries `zwdelta_load` with its shared-advance caveat, cross-
referenced from `docs/05` §3.4; `docs/05` carries the adaptation-channel hazard;
the surviving "hook word count" drift is either fixed in the docs this brief
owns or flagged for G1 where it lives under `patches/ff_fuel`; no fact is newly
derived, no doc rewritten wholesale, every change dated and tagged; suite
unchanged; dump untouched. `re/calibration_names.csv`, `calibration_names.md`,
`rail.md`, `patches/ff_fuel/**`, `logging/ecu_sim.py` and `boot.md` are **not**
edited by this brief.
