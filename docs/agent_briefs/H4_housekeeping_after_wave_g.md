# Brief H4 — Housekeeping after wave G: the labels, counts, names and help texts the last wave left inconsistent, and two time-boxed scheduler leftovers (#42 "fix gaps" desk half, #23 static half)

Wave G's reports each ended with two or three "belongs to another owner" notes.
None is a finding; all of them make the docs and tools disagree with each other,
which is exactly what a bench day cannot afford. This brief clears them in one
pass. Wave H pair 3, parallel with H6; **after H3 and H2 are merged** (it touches
`obd.md` and `kwp.md` lines they own). Desk only; no new facts except the two
time-boxed items in task 8, which are RE.

Read `00_common_rules.md` (the "correct in place with a dated note" rule), then
each item's pointer below before touching it.

## Items
1. **`tools/ram_survey.py` + `re/ram_map.csv` + `ram.md`.** `ram_survey.py` (line
   ~138) still declares `Known(0x7FE5A0, 0x7FE5A4, "os_stack_ptr_chain_ext", …)`;
   G3 showed 0x7FE59C/0x7FE5A0 are the ERCOSEK **process cursor** (next/current
   process slot; `scheduler.md` §13, `boot.md` §6.8; `ram.md` already carries the
   dated table correction). Fix the label (and 0x7FE59C if the survey lists it),
   regenerate `re/ram_map.csv` with the tool's own command (`tools/README.md`),
   confirm only those rows changed, keep the tests green.
2. **`ram.md` §9** says a RAM snapshot is 1,031 TransferData blocks of 62 bytes;
   G6 measured 1,032 with `med9log dump --sim`. Re-derive from the ranges in
   `logging/sessions/ram_snapshot.json` and correct whichever is wrong, dated.
3. **Old "Flash 0 = ff_fuel" naming.** `docs/07_workflow.md` §3 intro (the
   sentence "The first write is called **Flash 0** …") and
   `logging/sessions/flash_crc.json` note item 6 ("After Flash 1
   (patches/ff_counter) or Flash 0 (patches/ff_fuel)") predate the current
   naming: Flash 0 = the unmodified file re-saved through our tools (#26), Flash 1
   = `patches/ff_counter` (#27), `ff_fuel` = the E0-equivalence flash after them
   (#32; `docs/08` step 7). Correct both in place with a dated note; grep the
   repo for other "Flash 0" + `ff_fuel` pairings and list what you find.
4. **`logging/med9log.py`** `--sim-flash-crc` help (line ~783) still says "246
   simulated seconds"; since G5 the simulator publishes after 4,926 background
   loops of T_bg (default 50 ms, bounded 0.51-300.75 ms). Fix the text; add
   `--sim-t-bg-ms` and `--sim-seed-dtc` pass-throughs to ecu_sim's options (G5's
   suggestion) with a test each; `probe/groups/log --sim` unchanged from a user's
   view. **Coordinate:** H3 may have added an `obd` subcommand to this file — you
   run after H3 is merged; do not touch that subcommand.
5. **One name per address.** 0x13E974 is `kwp_service_config_set` in
   `re/symbols.csv` and `kwp_register_table` in `obd.md`. The CSV is the knowledge
   base: keep its name, add a dated one-line note where `obd.md` uses the other.
   Scan `symbols.csv` for other addresses whose name differs from the findings
   files (`grep` the address in `re/findings/*.md`), fix the same way; list them.
6. **Init entry 38.** `boot.md` §6.4 names 0x12F138 `kwp_tp_buf_init`; G5 showed it
   installs pointers into fault-memory entry 0 (0x8037E4 = 0x7F8892, 0x8037E8 =
   0x7F8893, 0x8037EC = 0x7F889A, 0x8038D4 = 0x7F88AC; `kwp.md` §12.7 side note).
   Disassemble 0x12F138, rename with evidence (e.g. `dfp_entry0_ptr_init`) in
   `symbols.csv`, `boot.md` §6.4 (dated correction in the table) and the
   `kwp.md` §12.7 note (resolved), `logging/ecu_sim.py`'s `INIT_ENTRIES` comment
   if it names the entry (one word; no behaviour change).
7. **Procedure consistency — both rulings are taken (Carlo, 2026-09-24).**
   (a) **The strict rule stands:** no write of any kind, Flash 0 included, before
   the #23 snapshots; `docs/07` §6.4 carries the dated note since 2026-09-24 and
   `docs/08`'s stop row already says so. Check `docs/06` §6, `docs/07` §3.2-§3.3
   and `patches/ff_counter/test/procedure.md` §0 for any "patches only" wording
   of the gate and align them, dated. Also confirm no "until G7 is merged" or
   "persist off" wording survives in `docs/08` (rewritten at planning) or the
   `ff_fuel` procedures — `ff_persist_enable` stays 1 by ruling, the bench reads
   block 8 +19..+28 first. (b) **Engine-off bench baselines, by ruling** — G6 open
   question 4: the Flash 1 and `ff_fuel` §4 baselines ask for
   "idle plus a load step", which needs a running engine; a bench mule has none
   (`hardware_prep.md` §3.5). Add a dated note to `patches/ff_counter/test/procedure.md`
   §4/§5 and `patches/ff_fuel/test/procedure.md` §4: the bench baselines are
   engine-off (KL15, no crank; the raster counters still run), the running
   comparison is #28 on the car. Procedure prose only; no patch code.
8. **Two scheduler leftovers, time-boxed 1 h each** (`scheduler.md` §13, G3):
   (a) task 20 (process list 0x0B1ED4 = `{0x0B5878, 0x40BEEC → b 0x13E650,
   0x0B5978}`) has no static activator — its thunk 0x0B09A0 is uncalled and its
   handle 0x4787C0 is loaded nowhere else; look for an indirect activation (ISR
   table, the kernel hook list 0x478E04) or record the exclusion set;
   (b) the semantics of 0x477B48 (task self re-activation, `boot.md` §6.8, named
   HYPOTHESIS). Dated notes in `scheduler.md` §13; symbols if settled.
9. `./.venv/bin/python3 -m unittest discover -s tests` green; comment on #42
   (docs gaps closed, list) and #23 (ram_survey label); commit after every item.

## Ownership
`tools/ram_survey.py`, `re/ram_map.csv`, `re/findings/ram.md`, `docs/07` (all
sections, dated notes only), `docs/08` (the stop row and step wording only),
`logging/med9log.py` (except an `obd` subcommand from H3), `logging/sessions/flash_crc.json`
(note text), `re/findings/boot.md` §6.4, `re/findings/scheduler.md` §13, one-line
name notes in `obd.md`/`kwp.md`, `patches/ff_counter/test/procedure.md` and
`patches/ff_fuel/test/procedure.md` (prose notes only), `re/symbols.csv`,
`tests/test_med9log*.py`. Do **not** edit `logging/ecu_sim.py` (beyond the one
comment word), `bench_rehearsal.py`, patch sources, `calibration_names.*`,
`eeprom.md`, docs/05.

## Acceptance
Every item above is fixed in place with a dated note or explicitly reported as
"left because …"; `ram_map.csv` regenerated with only the cursor rows changed; the
help text and the two new `--sim-*` options tested; names consistent between
`symbols.csv` and the findings files (list in the report); the two procedure
notes present; the two scheduler items settled or bounded; suite green; dump
untouched.
