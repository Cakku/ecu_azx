# Brief G5 — Simulator fidelity round 3: the real flash-CRC period, the fault-path / DTC read-back the bench day needs, and a fuller bench rehearsal (#20, #22 prep, #37 rehearsal)

`logging/ecu_sim.py` is still the only ECU the project has, and each wave has
found its next infidelity. E4 found `ddli_init` and the NVM pointers; F3 ran the
firmware's own init entries, gave `27 01` a virtual time base and predicted the
stock flash CRC 0x5562139F. Two gaps remain that the first bench day will hit.
First, the flash-CRC task's **period** is assumed (one activation per simulated
10 ms raster) — brief G3 settles the real rate from the init-array periodic
walk, and this brief applies it so `flash_crc.json`'s expectation matches a real
ECU. Second, nothing in the simulator or the rehearsal exercises the **fault
path / DTC read-back**: the flex-fuel FAULT matrix (#37) is rehearsed for the
patch's own state, but a bench tester also reads the ECU's stored DTCs and
freeze frames, and `bench_rehearsal.py` never touches that surface. This brief
makes both bench-checkable before the bench exists. Wave G pair 3, parallel with
G6; **it needs G3 merged** for the CRC period (if G3 is not merged when you
start, take the period from G3's branch/report and note the dependency). It
**owns `logging/ecu_sim.py`, `logging/bench_rehearsal.py`, `emu/` (additive),
`logging/sessions/flash_crc.json`, `tests/test_ecu_sim_*` and new test files**;
it does not edit `patches/**`, `boot.md`, `obd.md` or `re/calibration_names.*`.
Desk work only; simulated logs stay labelled `# simulated: true` under
`logging/samples/` and never enter a VERIFIED-DYNAMIC claim about the ECU.

Read `00_common_rules.md`, `re/findings/boot.md` §6.5 (and G3's new §6.5(d)
result — the periodic-walk rate), `re/findings/flash_programming.md` §5.3,
`re/findings/kwp.md` §1-§2 and §12 (the read-DTC / freeze-frame services in the
0x38 session group: 0x18 readDTCByStatus, 0x17, 0x3B; and 0x1A/0x21 ids),
`re/findings/eeprom.md` §7, §10 (the DFPMEEP fault-path blocks, §7 item 4),
`logging/ecu_sim.py` **in full** (`Med9Handlers`, `PatchRunner`, `FlashCrcTask`,
`INIT_ENTRIES`, `EcuSimulator`, `self_test`), `logging/bench_rehearsal.py`,
`logging/sessions/flash_crc.json`, `logging/sessions/tuning_checklist.json`,
`logging/med9kwp/kwp.py`, `emu/core.py`, `emu/time_base.py`, `emu/qspi_eeprom.py`,
`tests/test_ecu_sim_patch.py`, `tests/test_ecu_sim_initstate.py`,
`tests/test_med9kwp.py`, `tools/callgraph.py`, `tools/sda_xref.py`.

## Facts
- The CRC task hashes 2,462,208 bytes at 0x64 bytes/activation = 24,627
  activations; stock publishes 0x5562139F to 0x7F9178 (hi) / 0x7F917A (lo)
  (`flash_crc.json`, VERIFIED). `--flash-crc` today runs one activation per
  simulated 10 ms raster (246 simulated s). **G3 gives the real period N** of
  the walk that re-runs the CRC slice; the true publish time is 24,627 × N and
  the simulator/`flash_crc.json` must reflect it.
- The CRC task's engine-speed gate: it reads `nmot_w` (0x7FEE74) and returns
  while ≥ 0xFFFF, and state 7 sets bit 0 of 0x801200 so it runs **once per power
  cycle** — the simulator must model "already done on a warm ECU" as well as the
  cold run.
- DTC / freeze-frame services live in the 0x38 handler-config session group
  (sessions 3/4/5): 0x18 (readDTCByStatus), 0x17, 0x3B, plus 0x1A/0x21 ids
  (`kwp.md` §2, §12). `ecu_sim.py` implements the logging services (0x2C/0x21,
  0x27, 0x35/0x36/0x37) but not the fault services; a bench tester (VCDS) will
  read DTCs on first contact and the rehearsal should cover the round trip.
- The fault-path EEPROM blocks (DFPMEEP / IUMPR) are `eeprom.md` §7 item 4:
  blocks 4, 9, 13-23, 25-31, reached through run-time-indexed call sites.
  Reading a DTC does not require reversing those blocks — the KWP handler and
  the RAM DTC store are enough for the rehearsal; note the blocks, do not
  reverse them (out of scope).

## Tasks
1. **Apply the real flash-CRC period** (needs G3). Drive `FlashCrcTask` at the
   period G3 found (or make it a parameter defaulting to that period), update
   the `--flash-crc` documentation and `logging/sessions/flash_crc.json`'s note
   with the true publish time, and add the warm-ECU case (state already 7,
   nothing moves — `flash_crc.json` note item 1). Keep the stock value check
   0x5562139F and add the check that a patched image (ff_counter, ff_fuel)
   publishes a *different* value (recompute it the same way and assert it moves).
   Test: a cold simulator reaches state 2 and publishes 0x5562139F; a warm one
   is already done.
2. **Add the DTC / read-fault surface to the simulator.** Implement the KWP
   read-DTC path (0x18 at least; 0x17/0x3B if cheap) in `Med9Handlers`, backed
   by a small RAM DTC store the sim can seed, so `med9log.py`/a VCDS-style
   client gets a well-formed answer (empty DTC list on a clean sim, and a
   seeded DTC when asked). Drive it from the firmware's own handler where one
   exists (the F3 pattern — call the real handler under Unicorn), else model the
   response shape from `kwp.md` and label it clearly as a model, not the
   firmware. Tests: `27`-gated where the session table requires it; the answer
   parses; `--self-test` still PASS.
3. **Extend `bench_rehearsal.py`** with a fault-read step: after the flex-fuel
   FAULT matrix rows (which already exist), read DTCs over the simulated KWP and
   assert the round trip works, so the bench day's "clear DTCs, then read them
   back" sequence (`docs/07` §3.4 step 1) is rehearsed. Keep `--fresh-eeprom`
   green and report the score. Do not weaken any existing check.
4. **Regression tests** in `tests/test_ecu_sim_*`: the CRC period and warm/cold
   cases; the DTC round trip; the five dynamic ids still read byte-for-byte
   (E4's test); `27 01`/`27 02` from a cold sim (F3's test) unchanged;
   `med9log.py probe/groups/log --sim` unchanged from a user's view.
5. **Docs.** `logging/README.md` §4/§9 one line each for the new rehearsal step
   and the CRC period; dated note in `eeprom.md` §7 item 4 that the fault blocks
   are named but not reversed (rehearsal uses the RAM store); `kwp.md` §12 note
   if you implement a real fault service. `re/symbols.csv` for anything newly
   named. Comment on #20 and #22; commit after every finding.

## Acceptance
`--flash-crc` publishes 0x5562139F at the **real** period (from G3) and models
the warm-ECU no-op; a patched image's CRC is shown to differ; the simulator
answers a read-DTC request (empty and seeded); `bench_rehearsal.py --fresh-eeprom`
covers the clear-then-read-DTC step and its score is reported; `--self-test`
PASS; suite green; `patches/**`, `boot.md`, `obd.md` and `re/calibration_*`
untouched; dump untouched. If G3's period is not available, apply everything
else and leave the CRC period parameterised with the current default plus a
`# needs G3` note.
