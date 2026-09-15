# Agent briefs

Self-contained task briefs for autonomous (Opus-class) sub-agents. Each brief
maps to one or more GitHub issues, states prerequisites, deliverables and
acceptance criteria, and assumes the rules in `00_common_rules.md`.

## Wave A — can start now with current resources (Mac, dump, internet, gh)

| Brief | Issues | Needs | Parallel with |
|---|---|---|---|
| [A1 Environment and Ghidra project](A1_environment_and_ghidra.md) | #5 #7 #9 | Homebrew, ~1-2 h installs | everything |
| [A2 Reference documents and MPC5xx register facts](A2_reference_documents.md) | #6 (+ open questions in docs/02) | internet, poppler | everything |
| [A3 Measuring variables via MED9inf](A3_measuring_variables_med9inf.md) | #10 | clang | everything |
| [A4 Hardware desk prep](A4_hardware_desk_prep.md) | research parts of #1 #2 #3 #4 | internet | everything |
| [A5 Regression tools and Unicorn harness](A5_regression_tools_and_emulator.md) | #24, Unicorn half of #21 | Python venv | everything |
| [A6 Pico ethanol node firmware](A6_pico_ethanol_node_firmware.md) | #29 | Pico SDK toolchain (no sudo) | everything |

Not startable now (physical): the purchase/read/build parts of #1-#4, #20,
#22, #23, #26-#28, #30-#33 and later.

## Wave B — after A1 is merged (needs the Ghidra project)

| Brief | Issues | Also wants |
|---|---|---|
| [B1 r2 context and scheduler / hook point](B1_r2_context_and_scheduler.md) | #8 #11 | — |
| [B2 CAN receive path](B2_can_receive_path.md) | #12 | A2 register notes |
| [B3 KWP services](B3_kwp_services.md) | #13 | A3 variables |
| [B4 Variant byte and EEPROM](B4_variant_byte_and_eeprom.md) | #18 | A2 FR index |
| [B5 Calibration map detector](B5_calibration_table_detector.md) | #19 | — |
| [B6 Injection path](B6_injection_path.md) | #14 | A2, A3, B1, B5 |
| [B7 Ignition](B7_ignition.md) | #15 | A2, A3, B1, B5 |
| [B8 Start and warm-up](B8_start_and_warmup.md) | #16 | A2, A3, B6 |
| [B9 Rail pressure and injection window](B9_rail_pressure_and_window.md) | #17 | A2, A3, B6 |

B1-B5 can run in parallel right after A1. B6-B9 are heavier and benefit from
B1 and B5; run them as a second batch or accept some duplicated discovery.

## How to launch one

From Claude Code (Agent tool), one agent per brief, each in its own worktree
so parallel agents do not collide on `re/symbols.csv` and the docs:

```
subagent_type: general-purpose
model: opus
isolation: worktree
name: A1
prompt: |
  You are working in a git worktree of /Users/carlo/ecu_azx on branch agent/A1.
  Read docs/agent_briefs/00_common_rules.md, then docs/agent_briefs/A1_environment_and_ghidra.md,
  and execute that brief completely. Commit on your branch; do not push; do not
  modify data/passat_azx_ori.bin. Finish with the report format from the rules file.
```

Or paste the same text into a fresh `claude` session started inside a
worktree (`git worktree add ../ecu_azx-A1 -b agent/A1`).

## After an agent finishes

1. Read its report; check `python3 tools/checksum.py verify -q data/passat_azx_ori.bin`
   still prints `ALL OK` on the branch.
2. Review the diff (`git diff main..agent/A1`), especially `re/symbols.csv`
   rows (evidence present, tags right) and any edits to `docs/`.
3. Merge into `main`, push, then launch the dependent wave-B briefs.
4. Close the GitHub issue only when its exit criterion is met; otherwise
   leave the agent's comment as the status.
