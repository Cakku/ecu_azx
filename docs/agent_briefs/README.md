# Agent briefs

Self-contained task briefs for autonomous (Opus-class) sub-agents. Each brief
maps to one or more GitHub issues, states prerequisites, deliverables and
acceptance criteria, and assumes the rules in `00_common_rules.md`.

## Status (2026-09-16, updated 2026-09-17)

Waves A and B are merged into `main` (647efe6). Phase 1 (static RE) is
closed: 13/13 issues. Phase 0 is blocked on hardware (#1-#4); Phase 2 has
#21 and #24 closed, #20/#22/#23/#44 open; Phases 3-6 are untouched except
the hello-patch half of #25. Everything below is **desk work** that advances
Phases 2-4 while the spare ECU, the BDM backup and the bench are pending.

**Update 2026-09-16 (later): wave C is complete on `integration/wave-C`**
(the human fast-forwards `main` after review). Pair 1: C1 (`patches/common/`,
`tools/patch_gen.py`, `tools/patch_apply.py`, `patches/ff_counter/`, #25 done,
#27 software half) and C2 (`tools/ram_survey.py`, `re/findings/ram.md`,
`logging/sessions/ram_snapshot.json`, #23 static half). Pair 2: C3
(`logging/med9kwp/`, `logging/med9log.py`, `logging/ecu_sim.py`, the session
files, #20 software half) and C4 (`tools/ercosek_tasks.py`, `emu/os_clock.py`,
`scheduler.md` §11-§12: the rasters are 10x faster than wave B assumed,
0x45CAC4 = 20 ms, 0x4328E4 = 10 ms, #44 task-period row settled statically).
Integration changes: `ff_counter` and the `hello_patch` template moved from the
placeholder 0x807F00 (inside the KWP programming copy, C2 §6) to C2's block
**0x7FFB00/0x100** with `"ram_status": "static"`; the Flash-1 procedure now
reads the five raster counters first because the hook task belongs to task
set B and which set is live is open (C4 §11.7); D1/D2 carry dated notes
(external SRAM is cleared at cold start, EEPROM is the only persistence;
filter constants per real raster). Wave D can start once `main` is
fast-forwarded: D1 first, D3 as the filler.

**Update 2026-09-16 (wave D pair 1):** D1 (`patches/ff_fuel/`, #32 + software
half of #37: both task sets' 10 ms tasks hooked, `onchip_edit` unlock added to
`tools/patch_apply.py` because the fuel hook 0x42247C is on-chip flash) and D3
(`re/calibration_names.csv`, scaled `tools/draft_to_xdf.py`, #41 naming pass)
are merged on `integration/wave-D`; `re/med9_draft.xdf` regenerated with D1's
FFCAL001 rows. **D2** (`patches/ff_fuel/src/ff_diag.c`: VCDS group 111 with
TKMWL ids 2196-2199, E% persistence in EEP_CONF block 8 through
`nvm_block_request` only, #39 + #38 software halves; `eeprom.md` corrected:
read-back is mode 1, the handle is a 9-byte record, and there is **no key-off
commit** in the image) is merged too. **Wave D is complete on
`integration/wave-D`**; the human merges it into `main`. Nothing is flashable
yet: `ram_status` stays `static` until the #23 snapshots, and whether KESSv2
writes the on-chip flash (the fuel hook 0x42247C) is the first bench question.

**Update 2026-09-17: wave D is merged into `main` (8c93421).** 365 tests OK,
checksums ALL OK, `re/med9_draft.xdf` regenerated once with D1's FFCAL001
rows; patched ff_fuel image sha256 `40e22a23…4cae7`. Issues #32, #37, #38,
#39 and #41 carry integration notes and keep their bench halves open; no
milestone is complete. **Wave E** (below) is the next desk work: it finishes
the Phase 5 patch code (ignition, start, rail) as inert, enable-gated
extensions of `patches/ff_fuel`, continues the calibration definition, makes
every bench procedure runnable against the simulator, and answers the
on-chip-flash question from the dump. Nothing in wave E needs the ECU.

Rule learned in wave B: **run at most two agents at a time.** Four in
parallel hit the API rate limit and lost their work. Agents commit after
every finding for the same reason.

## Wave A — done (merged 21c03df)

| Brief | Issues |
|---|---|
| [A1 Environment and Ghidra project](A1_environment_and_ghidra.md) | #5 #7 #9 |
| [A2 Reference documents and MPC5xx register facts](A2_reference_documents.md) | #6 |
| [A3 Measuring variables via MED9inf](A3_measuring_variables_med9inf.md) | #10 |
| [A4 Hardware desk prep](A4_hardware_desk_prep.md) | research parts of #1-#4 |
| [A5 Regression tools and Unicorn harness](A5_regression_tools_and_emulator.md) | #24, Unicorn half of #21 |
| [A6 Pico ethanol node firmware](A6_pico_ethanol_node_firmware.md) | #29 (hardware test open) |

## Wave B — done (merged aef2e50)

| Brief | Issues |
|---|---|
| [B1 r2 context and scheduler / hook point](B1_r2_context_and_scheduler.md) | #8 #11 |
| [B2 CAN receive path](B2_can_receive_path.md) | #12 |
| [B3 KWP services](B3_kwp_services.md) | #13 |
| [B4 Variant byte and EEPROM](B4_variant_byte_and_eeprom.md) | #18 |
| [B5 Calibration map detector](B5_calibration_table_detector.md) | #19 |
| [B6 Injection path](B6_injection_path.md) | #14 |
| [B7 Ignition](B7_ignition.md) | #15 |
| [B8 Start and warm-up](B8_start_and_warmup.md) | #16 |
| [B9 Rail pressure and injection window](B9_rail_pressure_and_window.md) | #17 |

The hardware-dependent leftovers of wave B were collected into #44 (confirm
by logging) and #45 (bench edits).

## Wave C — done (merged 5c5123a)

| Pair | Brief | Issues | Produces |
|---|---|---|---|
| 1 | [C1 Patch build framework and Flash-1 counter patch](C1_patch_framework_and_flash1.md) | #25, software half of #27 | `patches/common/`, `tools/patch_apply.py`, `patches/ff_counter/` |
| 1 | [C2 RAM usage survey, static half](C2_ram_survey_static.md) | #23 (static) | `tools/ram_survey.py`, `re/findings/ram.md`, the patch RAM block |
| 2 | [C3 KWP logger + emulated ECU](C3_kwp_logger_and_ecu_sim.md) | #20 (software), prepares #44 #23 | `logging/med9kwp/`, `logging/med9log.py`, `logging/ecu_sim.py`, session files |
| 2 | [C4 Task periods of 0x45CAC4 / 0x4328E4](C4_task_periods.md) | #44 (task-period row), scheduler open item | settled rasters, `emu/os_clock.py` |

C1 and C2 are independent (C1 uses a placeholder RAM address if C2 is not
merged yet). C3 and C4 are independent of each other and of pair 1; C3 picks
up C2's `ram_snapshot.json` and C1's counter address if they are merged.

## Wave D — done (merged 8c93421; D1 + D3, then D2)

| Order | Brief | Issues | Needs |
|---|---|---|---|
| 1 | [D1 ff_fuel MVP patch, desk half](D1_ff_fuel_patch.md) | #32, software half of #37 | C1 + C2 merged (C4 helps) |
| 2 | [D2 Diagnostics and persistence](D2_diagnostics_and_persistence.md) | #39 #38 | D1 merged |
| any | [D3 Calibration definition](D3_calibration_definition.md) | #41 | alone on `re/calibration_draft.csv`; a filler when only one slot is in use |

While D1 and D3 run in parallel, **D3 owns `re/calibration_draft.csv`**: D1
does not edit it and instead delivers its FFCAL001 descriptor rows in
`patches/ff_fuel/ffcal001_rows.csv` (same columns), which the integrator
appends at merge time. D1 also has to decide the periodic hook without the
bench answer to the task-set question (C4 §11.7): hook the 10 ms tasks of
both sets if the set-A twin 0x4328E4 offers an equally clean site, else keep
the site a build parameter and document both.

Still hardware-only (no brief): #1-#4, #22, #26-#28, #30-#31, #33, #45, the
runtime half of #23 and #44, the generator test of #29, and the calibration
work of #34-#36 (their code halves follow D1 once the E0 logs exist).
Issue bookkeeping after wave C (2026-09-16): #25 closed; #27, #20, #44 have
their completed rows ticked; #11 carries the period correction; no milestone
is complete yet (every remaining Phase 2/3 item needs the bench).

## Wave E — done, merged into `main` 2652ede (2026-09-17): Phase 5 code, definition pass 2, simulator rehearsal, the on-chip question

**Update 2026-09-22.** `integration/wave-E` was merged into `main` on
2026-09-17 (2652ede, including the `med9log --sim` pacing fix 086d663). The
GitHub issues and milestone descriptions still say "on `integration/wave-E`;
the human merges" — the bookkeeping list under wave F below brings them up
to date. The same day's `docs/01_project_plan.md` change (6cd45b3) restructured
the hardware plan: M1 is the BDM backup **plus a demonstrated route to write
it back**, M1b any VR6 `03H906032` mule, M1c the software-matching spare,
bought only when a flash is imminent (`re/findings/hardware_prep.md` §1.4b);
first human action is the K-Suite Service Mode check (§2.3).

**Status 2026-09-17 (late): all seven briefs ran and are merged on
`integration/wave-E`** (E3 75ee087, E1 4b2c63d, E2 19aa6c2, E4 0ee67ce +
692ceed, E5 d0dff85, E6 374ed9c, E7 305ec37, then the integration fixes);
608+ tests OK, checksums ALL OK, XDF regenerated after each patch brief
(1079 tables, 179 constants). The human merges it into `main`. Results in
one line each:

* **E1** ignition blend at 0x41D40C, FFCAL001 v2, group 108; `scheduler.md`
  §11.8: **task set A is live** by necessity (static).
* **E3** 157 → 259 named objects; the u8 `rl` scaling settled in the emulator
  (`rl_w >> 5`; VCDS A = 133 is a display normalisation); lambda path still
  not found (lead: pointer-written byte 0x7FD066).
* **E2** start enrichment at 0x41A680 / 0x41A808 and start ignition at
  0x431384, FFCAL001 v3, group 69; the suite caught that `%ESSTT`'s early-out
  **branches into** the hooked store, so the stubs gate on `B_stend`.
* **E4** QSPI + M95160 device model, `ecu_sim.py --sim-patch` runs the patch
  with a simulated Pico (`ethanol_frame_send.py`) and `bench_rehearsal.py`
  executes every procedure; **found D2's `ff_persist_offset = 0` sat on
  block 8's `{id, version}` stamp** (fixed to 2 by E5); the emulator now runs
  `ddli_init` (the ten dynamic ids used to share one entry array).
* **E5** rail setpoint adder at 0x45845C (before the KLPRMAX clamp, which
  re-reads the cell), window diagnostics in group 109, FFCAL001 v4; torque
  limiter deliberately not implemented (design note in `procedure_e5.md`).
* **E6** `re/findings/flash_programming.md`: the ECU's **own OBD programming
  route whitelists and can program 0x404000-0x47FFFF**; no boot-time
  integrity gate; the only hard check is the `5A5A` marker at file 0x1E2500
  (recoverable); `10 85` reboots into a second KWP stack; the one-shot init
  table (1,028 entries) binds no function pointers. `tools/flash_segments.py`.
* **E7** `docs/07_workflow.md`, every command run; found the duplicated
  session variables and four stale doc statements, fixed at integration.

Integration fixes worth knowing: `patch.mk` header dependencies (a stale
object had produced a blob disagreeing with `patch.json`), `patch_apply.py`
refuses 0x010000-0x01FFFF and 0x080000-0x09FFFF (the OBD route refuses them),
`re/symbols.csv` merged as a union each time (one duplicate removed).

**Open after wave E (desk):** `tools/logcmp.py` cannot do the raster-counter
alignment step (`bench_rehearsal.py::_align_on_raster` is the implementation
to lift); the lambda-target path (`LAMSOLL` / `KFLBTS`) and the ~700 remaining
`cand_*` objects; the torque limiter at the min-chain 0x0C7CF8; the RAM
bootstrap loader (0x7F8728) and its own whitelist; the init-table entries the
simulator still skips (`boot.md` §6 lists five worth calling); which driver
the factory software binds at 0x7FAB70/0x7FAB74. **Open for the bench:**
everything listed under "still hardware-only" below, now with `docs/07` and
`flash_programming.md` §7.2 as the checklist for the first flash — eight hook
words, seven on-chip, all inside the whitelist the firmware enforces.


| Pair | Brief | Issues | Needs | Owns (nobody else edits these while it runs) |
|---|---|---|---|---|
| 1 | [E1 Ignition blend](E1_ignition_blend.md) | #34 software half, ignition rule of #37 | wave D merged | `patches/ff_fuel/**`, `emu/models/flexfuel.py`, `tests/test_ff_*`, `logging/sessions/ff_fuel.json`, docs/05 §3.4/§4, `ignition.md`, `scheduler.md` §11.8 |
| 1 | [E3 Calibration naming pass 2](E3_calibration_naming_pass2.md) | #41 (continued), prepares #43 | — | `re/calibration_names.csv`, `re/findings/calibration_names.md`, `tests/test_draft_to_xdf.py`, dated notes in `rail.md`/`injection.md`/`measuring_vars.md` §7.4 |
| 2 | [E2 Start enrichment](E2_start_enrichment.md) | #35 software half | **E1 merged** | the same set as E1, plus `start.md` |
| 2 | [E4 Bench rehearsal in the simulator](E4_bench_rehearsal_simulator.md) | rehearses #37 #38 #39, prepares #22 | — | `emu/` (additive), `logging/ecu_sim.py`, `logging/med9kwp/`, new `logging/*.py`, `logging/samples/`, `tests/test_med9kwp.py` + new tests, `eeprom.md` §10. **Reads** `patches/**` and `ff_fuel.json`, never edits them |
| 3 | [E5 Rail-pressure adder](E5_rail_pressure_adder.md) | #36 software half (trimmed) | **E2 merged** | the same set as E1, plus `rail.md` |
| 3 | [E6 KWP programming route, on-chip writability](E6_kwp_programming_route.md) | blocker of #26 #27 #28 #32 | — | new `re/findings/flash_programming.md`, `kwp.md` §9, docs/02 §2/§6, docs/06 §6, optional `tools/flash_segments.py` |
| filler | [E7 Workflow walkthrough](E7_workflow_doc.md) | #42 step 1 | **E4 merged** | new `docs/07_workflow.md`, `docs/README.md`, `tools/README.md`, docs/03 §7, docs/01 §2 |

Why this order. E1 → E2 → E5 are **one patch growing** (`patches/ff_fuel`):
each appends to FFCAL001 (v2 → v3 → v4), to `struct ff_state` and to
`hooks.S`, so they cannot run side by side; E1 comes first because the
ignition rule is the last unimplemented line of #37 and its insertion point
is the cleanest. E3, E4 and E6 touch none of those files and fill the second
slot. E6 is late only because it is research, not code; move it forward if
the bench date approaches. Every feature ships **disabled** (`ff_*_enable`
= 0) with neutral tables, so the flashable file stays the proven fuel-only
MVP until the human enables one feature at a time (docs/01 §3 principle 5).

Shared budget, decided here so the three patch briefs do not collide:
measuring **groups 111 (D2) / 108 (E1) / 69 (E2) / 109 (E5)** and **ids
2196-2199 (D2) / 2192-2195 (E1) / 2188-2191 (E2) / 2184-2187 (E5)**; each
brief re-checks with `tools/measuring_vars.py --free`.

The on-chip caveat applies to all of it: after wave E, six or seven of the
patch's hook words are in 0x404000-0x47FFFF. E6 tells us from the dump
whether the OBD route can write them; the bench read-back after Flash 0
(`patches/ff_fuel/test/procedure.md` §1) is still the proof.

Still hardware-only (no brief): #1-#4, #22, #26-#28, #30-#31, #33, #40, #45,
the runtime half of #23 and #44, the generator test of #29, the TunerPro
check of #41, and the calibration values of #34-#36.

## Wave F — next (planned 2026-09-22): make Flash 1 decisive, close the desk gaps, keep the bench un-blocked

Wave F is the desk work that is still worth doing before an ECU exists. It
does not add features to `patches/ff_fuel` (every Phase 5 feature is coded
and disabled; their calibration needs a car). It (a) fixes the one thing that
would have made the first bench day a wasted one — the Flash 1 counter hooks
a task set that E1 showed is not the live one — (b) closes the desk items
listed under "Open after wave E", and (c) answers the recovery question the
2026-09-22 hardware plan asks before any purchase (can a damaged ECU be
written back over the connector, or only over BDM). Every item is checked
against the emulator, nothing is flashed. Still two agents at a time.

| Pair | Brief | Issues | Needs | Owns (nobody else edits these while it runs) |
|---|---|---|---|---|
| 1 | [F1 Flash 1 hooks both task sets](F1_flash1_both_task_sets.md) | #27, #44 live-set row, prepares #26 | — | `patches/ff_counter/**`, `tests/test_patch_framework.py` (+ a new test file), `logging/sessions/flash1_counter.json`, `scheduler.md` §8 dated note |
| 1 | [F4 Calibration naming pass 3: lambda path, #43 columns](F4_calibration_naming_pass3_lambda.md) | #41, #43 | — | `re/calibration_names.csv`, `calibration_names.md`, `tuning_checklist_draft.md`, `tools/draft_to_xdf.py`, `tests/test_draft_to_xdf.py`, dated notes in `injection.md`/`measuring_vars.md`, new `re/findings/lambda.md` if needed |
| 2 | [F2 logcmp alignment and derived tolerances](F2_logcmp_alignment.md) | #42 gap, #32 E0 recipe, #27 compare step | F1 merged (for the ff_counter procedure wording; otherwise report only) | `tools/logcmp.py`, its tests, `logging/bench_rehearsal.py`, `docs/07_workflow.md` §5, `logging/README.md` §9 line, both `test/procedure.md` §4, `tools/README.md` row |
| 2 | [F3 Simulator: firmware init entries, NVM binding](F3_simulator_init_table_and_nvm_binding.md) | #20 simulator, #38 open item, #22 prep | — | `logging/ecu_sim.py`, `logging/med9kwp/` (if needed), `emu/` (additive), `tests/test_ecu_sim_patch.py` + new, `boot.md` §6.5 marks, `kwp.md` §12.6, `eeprom.md` §7/§10 notes |
| 3 | [F5 RAM bootstrap loader](F5_ram_bootstrap_loader.md) | #26, #28, #2 risk table; `flash_programming.md` §8 | — | new `re/findings/ram_loader.md`, `flash_programming.md` §8 marks + §10, `tools/flash_segments.py`, its test, one paragraph each in docs/01 §6 and docs/06 §6 |
| 3 (filler) | [F6 OBD PID 0x52](F6_obd_pid_52.md) | #39 optional half | — | `patches/ff_fuel/**`, `emu/models/flexfuel.py`, `tests/test_ff_*`, `logging/sessions/ff_fuel.json`, docs/05 §3.7, new `re/findings/obd.md` |

Why this order. **F1 first** because it is the only item that changes what
the first bench day proves: with set A live (`scheduler.md` §11.8) the
current Flash 1 counter never moves, and the day would end with an
ambiguous zero. F4 is independent and long, so it fills pair 1's second
slot. F2 and F3 both live in `logging/` but in disjoint files (F2:
`bench_rehearsal.py` and `logcmp.py`; F3: `ecu_sim.py` and `emu/`); F3 runs
the rehearsal as a check and does not edit it. F5 is research, like E6 was,
and can move forward if a shop visit or a K-Suite check is scheduled — its
decision section is what the human needs in hand when talking to a shop
about a write-back route. F6 is optional and is the only wave-F brief
allowed in `patches/ff_fuel`.

**Deferred on purpose.** The torque/charge limiter on the injection window
(#36 step 3) stays a design note (`patches/ff_fuel/test/procedure_e5.md` §6)
until `procedure_e5.md` Part A has produced a margin table on a real engine —
E5's argument, not repeated here. Calibration values for #34-#36 and every
bench/road step need the ECU and the car.

**Human side, in the order docs/01 §4 Phase 0 now gives it** (nothing an
agent can do): image the K-Suite machine and check whether **Service Mode**
covers MED9.1 (`hardware_prep.md` §2.3, #4); if not, a shop bench read with
write-back capability (§2.7, #2); a cheap VR6 `03H906032` mule for harness
and KWP work (§1.4b, #1, #3); a gs_usb-class CAN adapter with switchable
termination (`logging/README.md` §6); then Flash 0 (#26) with the read-back
checklist of `flash_programming.md` §7.2 and Flash 1 (#27) with F1's
decision table.

**Issue bookkeeping to post first** (the GitHub side is behind `main` since
2652ede; `work/issue_bookkeeping_after_wave_E.sh` has the exact `gh`
commands, review before running):
1. On #26 #27 #32 #34 #35 #36 #37 #38 #39 #41 #42 #44: wave E is merged
   into `main` 2652ede (2026-09-17); the "human merges" sentences are done.
2. **#27**: E1's result (set A live) means the counter as built never runs
   on the ECU; brief F1 planned. Without this note the issue reads as
   "software half done", which is no longer true.
3. **#22**: E4's `ethanol_frame_send.py`, `ecu_sim.py --sim-patch` and the
   TouCAN model are the desk half of this issue; nothing on the issue says so.
4. **#43**: E3's `re/findings/tuning_checklist_draft.md` exists (step 1
   started, not ticked); F4 adds the logs and limits columns.
5. **#20**: E4's two logger fixes (`%.6g` truncation, `ff_magic` decimal)
   and the 086d663 `--sim` pacing fix; E7 ran every logging command.
6. **#1, #2, #3, #4**: the 2026-09-22 restructure (M1/M1b/M1c, cheap-donor
   strategy, Service Mode first, shop as the recovery route) is only in
   `docs/01` and `hardware_prep.md` §1.4b; the issue bodies still describe
   "one spare, same software" and "K-TAG BDM". A comment each, and #1/#2
   bodies re-worded by the human.
7. Milestone descriptions: Phases 2-6 say "on `integration/wave-E`" → merged
   2652ede; Phase 0 gets the 2026-09-22 restructure. Counts are right
   (Phase 1 13/13 closed; 4/2, 4/2, 4/1, 5/0, 7/0, 3/0 for the others), and
   no issue is wrongly open or closed.

## How to launch one

From Claude Code (Agent tool), one agent per brief, each in its own worktree
so parallel agents do not collide on `re/symbols.csv` and the docs:

```
subagent_type: general-purpose
model: opus
isolation: worktree
name: F1
prompt: |
  You are working in a git worktree of /Users/carlo/ecu_azx. First run
  `git branch -m agent/F1` and `ln -s /Users/carlo/ecu_azx/.venv .venv`, and
  use ./.venv/bin/python3 for every Python command (bare python3 is the
  wrong interpreter). Read docs/agent_briefs/00_common_rules.md, then
  docs/agent_briefs/F1_flash1_both_task_sets.md, and execute that brief completely.
  Commit on your branch after every finding; do not push; do not modify
  data/passat_azx_ori.bin. Run `./.venv/bin/python3 -m unittest discover -s
  tests` before you finish. End with the report format from the rules file.
```

Or paste the same text into a fresh `claude` session started inside a
worktree (`git worktree add ../ecu_azx-F1 -b agent/F1`).

## After an agent finishes

1. Read its report; check `python3 tools/checksum.py verify -q data/passat_azx_ori.bin`
   still prints `ALL OK` on the branch, and that the unit-test suite passes.
2. Review the diff (`git diff main..agent/C1`), especially `re/symbols.csv`
   rows (evidence present, tags right) and any edits to `docs/`.
3. Merge into `main` through an integration branch when two agents touched
   the same files: `re/symbols.csv` merges as a union of rows (dedupe on
   address + name), README/docs files as a union of sections; regenerate
   `re/med9_draft.xdf` with `tools/draft_to_xdf.py` at merge time only.
4. Push, then launch the next pair.
5. Close the GitHub issue only when its exit criterion is met; otherwise
   leave the agent's comment as the status (#27, #20, #23, #32, #34-#39, #41
   all keep a hardware half open after these briefs).
