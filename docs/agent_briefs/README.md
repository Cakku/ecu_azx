# Agent briefs

Self-contained task briefs for autonomous (Opus-class) sub-agents. Each brief
maps to one or more GitHub issues, states prerequisites, deliverables and
acceptance criteria, and assumes the rules in `00_common_rules.md`.

## Status (2026-09-16)

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

## Wave C — startable now, two at a time

| Pair | Brief | Issues | Produces |
|---|---|---|---|
| 1 | [C1 Patch build framework and Flash-1 counter patch](C1_patch_framework_and_flash1.md) | #25, software half of #27 | `patches/common/`, `tools/patch_apply.py`, `patches/ff_counter/` |
| 1 | [C2 RAM usage survey, static half](C2_ram_survey_static.md) | #23 (static) | `tools/ram_survey.py`, `re/findings/ram.md`, the patch RAM block |
| 2 | [C3 KWP logger + emulated ECU](C3_kwp_logger_and_ecu_sim.md) | #20 (software), prepares #44 #23 | `logging/med9kwp/`, `logging/med9log.py`, `logging/ecu_sim.py`, session files |
| 2 | [C4 Task periods of 0x45CAC4 / 0x4328E4](C4_task_periods.md) | #44 (task-period row), scheduler open item | settled rasters, `emu/os_clock.py` |

C1 and C2 are independent (C1 uses a placeholder RAM address if C2 is not
merged yet). C3 and C4 are independent of each other and of pair 1; C3 picks
up C2's `ram_snapshot.json` and C1's counter address if they are merged.

## Wave D — after wave C is merged

| Order | Brief | Issues | Needs |
|---|---|---|---|
| 1 | [D1 ff_fuel MVP patch, desk half](D1_ff_fuel_patch.md) | #32, software half of #37 | C1 + C2 merged (C4 helps) |
| 2 | [D2 Diagnostics and persistence](D2_diagnostics_and_persistence.md) | #39 #38 | D1 merged |
| any | [D3 Calibration definition](D3_calibration_definition.md) | #41 | alone on `re/calibration_draft.csv`; a filler when only one slot is in use |

Still hardware-only (no brief): #1-#4, #22, #26-#28, #30-#31, #33, #45, the
runtime half of #23 and #44, the generator test of #29, and the calibration
work of #34-#36 (their code halves follow D1 once the E0 logs exist).

## How to launch one

From Claude Code (Agent tool), one agent per brief, each in its own worktree
so parallel agents do not collide on `re/symbols.csv` and the docs:

```
subagent_type: general-purpose
model: opus
isolation: worktree
name: C1
prompt: |
  You are working in a git worktree of /Users/carlo/ecu_azx on branch agent/C1.
  Read docs/agent_briefs/00_common_rules.md, then docs/agent_briefs/C1_patch_framework_and_flash1.md,
  and execute that brief completely. Commit on your branch after every
  finding; do not push; do not modify data/passat_azx_ori.bin. Run
  `python3 -m unittest discover -s tests` before you finish. End with the
  report format from the rules file.
```

Or paste the same text into a fresh `claude` session started inside a
worktree (`git worktree add ../ecu_azx-C1 -b agent/C1`).

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
   leave the agent's comment as the status (#25, #27, #20, #23, #32, #37,
   #38, #39 all keep a hardware half open after these briefs).
