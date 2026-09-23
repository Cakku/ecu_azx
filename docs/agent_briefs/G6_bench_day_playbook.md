# Brief G6 — The bench-day playbook: one ordered runbook that stitches first-contact, the wave-B confirmations, the RAM snapshots, and the Flash 0/1 decision into the sequence the human runs when the ECU arrives (#20, #23, #26, #27, #44 desk half)

Everything the first bench day needs exists, but it is spread across a dozen
files: the first-contact sequence in `logging/README.md` §8, the wave-B logging
confirmations in the #44 checklist, the RAM-snapshot procedure in
`re/findings/ram.md` §9 and `logging/sessions/ram_snapshot.json`, the Flash 0/1
decision table in `patches/ff_counter/test/procedure.md` §4, the flash-CRC
read-back in `logging/sessions/flash_crc.json`, and the flashing chapter in
`docs/07` §3. A person with the ECU on the desk should not have to assemble that
themselves. This brief writes the **single ordered playbook** — what to run,
in what order, what each step proves, what a bad result means, and which GitHub
issue it closes — pointing at the existing procedures rather than duplicating
them. It is the deliverable that makes the hardware money productive on day one.
Wave G pair 3 filler, parallel with G5. **Pure documentation**: it writes one
new findings file and adds a `docs/README.md` row; it does not change any tool,
patch, or existing procedure, and it derives no new facts. Desk work only.

Read `00_common_rules.md`, then read (do not edit) the sources it will stitch:
`docs/01_project_plan.md` §4 Phase 0 (the M1/M1b/M1c order),
`re/findings/hardware_prep.md` §2.10 (BDM recommendation), §3.9 (bench bring-up
order), §3.2 (bench pinout), `logging/README.md` §6-§9 (adapter, wiring, first
contact, gateway), `logging/sessions/wave_b_confirm.json`,
`logging/sessions/ram_snapshot.json`, `logging/sessions/flash_crc.json`,
`re/findings/ram.md` §9, `patches/ff_counter/test/procedure.md` §4 (the Flash
0/1 decision table, rows A-I), `patches/ff_fuel/test/procedure.md` and
`procedure_d2/e1/e2/e5.md`, `re/findings/flash_programming.md` §7.2 (the
read-back checklist), `docs/07_workflow.md` §3 (flashing) and §4-§5 (logging,
review), the #44 checklist (`gh issue view 44`), and #20/#23/#26/#27/#28.

## What the playbook must contain, in order
The runbook is the sequence a person follows from "ECU on the desk, nothing
connected" to "Flash 1 verified", each step naming the exact command(s) from the
existing files, the pass criterion, the failure meanings, and the issue it
advances. Do not restate the procedures — link to them by file and section and
give only the ordering, the decision points, and the "why this before that".

1. **Bench bring-up** (`hardware_prep.md` §3.9): label/photograph, ohm-out the
   pins, power in stages, sniff CAN, first TesterPresent. Exit = #3 criterion.
2. **First contact over the logger** (`logging/README.md` §8 steps 0-2):
   `ecu_sim --self-test` and `med9log probe --sim` on the Mac first, then
   `probe --bus` on the bench. The "things that will go wrong" table is the
   troubleshooting reference.
3. **The wave-B confirmations** (#44): `groups` against VCDS for `nmot`/`tmot`,
   then the `wave_b_confirm.json` log — which ticks the #44 rows that are marked
   "needs a live read" (task-set counters, `ti`/rail/ignition scaling). Say
   which #44 checkbox each read closes.
4. **The RAM snapshots** (#23 dynamic half, `ram.md` §9,
   `ram_snapshot.json`): the six-state capture sequence, then
   `ram_snapshot_diff.py --free`. This is what upgrades the patch RAM block
   0x7FFB00 from VERIFIED-STATIC to VERIFIED-DYNAMIC and clears the
   `ram_status: static` flash-blocker on `ff_counter` and `ff_fuel`.
5. **Flash 0** (#26, `docs/07` §3.2-§3.3, `flash_programming.md` §7.2): the
   unmodified file re-saved through our tools, written, read back, and the
   0x404000-0x47FFFF on-chip region compared (the step that answers whether
   KESSv2 writes the on-chip array at all). Also read the stock flash CRC
   0x5562139F over OBD (`flash_crc.json`) as an independent read-back.
6. **Flash 1** (#27, `patches/ff_counter/test/procedure.md` §4): the both-sets
   counter patch, then the decision table rows A-I over (stock raster counters ×
   on-chip read-back × slope × `ff_src_seen`). Spell out which row proves what:
   row A upgrades `scheduler.md` §11.8 to VERIFIED-DYNAMIC and settles the
   on-chip half of #32; row D is "KESS skipped the on-chip array" and sends you
   to the `HOOKS=external` build only if set B is also live.
7. **Where it goes next**: `ff_fuel` Flash 0-equivalence (#32), then one feature
   at a time (#34-#36), then #28 (repeat on the car with the BDM backup in
   hand). Just the pointers — those are their own procedures.

Add a compact **"which issue closes when"** table mapping each step to the
GitHub issue and its exit criterion, and a **"stop here if"** list (the
safety gates: no BDM backup yet → do not flash the car; `ram_status` still
`static` → do not flash at all; on-chip read-back mismatch → do not trust the
image). These must agree with `docs/01` §3 principles 2 and 5 and
`docs/07` §6.4.

## Tasks
1. Write `re/findings/bench_playbook.md` (or `docs/08_bench_playbook.md` — pick
   one, note the choice; a `re/findings` file matches the "findings" convention,
   a `docs/08` file matches "07 is the one you follow" — prefer `docs/08` and
   add it to `docs/README.md`'s table). Every step cites the file+section it
   drives; no procedure is duplicated.
2. Cross-link: add one line to `docs/07_workflow.md` §3's intro pointing at the
   playbook as the day-one ordering (a dated note, not a rewrite), and the
   `docs/README.md` table row.
3. Sanity-check every command you cite actually exists (run the `--sim` ones on
   the Mac to confirm they still work; the bench ones you cannot run — mark them
   clearly as bench-only, as the existing docs do).
4. `python3 -m unittest discover -s tests` (unchanged — docs only). Comment on
   #26 and #27 that the ordered playbook exists; commit after every section.

## Acceptance
A person with no context can follow `bench_playbook.md` from bring-up to Flash 1
verified, each step pointing at the real procedure, with pass/fail meanings, the
issue it closes, and the safety stops; no existing procedure is duplicated or
changed; every cited `--sim` command runs; `docs/README.md` and `docs/07` §3
link to it; suite unchanged; dump untouched.
