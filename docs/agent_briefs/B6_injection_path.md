# Brief B6 — Injection path: fuel mass to injection time

Issue: **#14**. Prerequisites: A1 merged; A2 FR index; A3 measuring variables;
ideally B1 (which task computes injection) and B5 (map addresses). No `sudo`.

Read `00_common_rules.md`, `docs/05_flexfuel_design.md` §3.3 and §7, and the
FR index entries for injection first.

## Tasks
1. From the measuring variables (relative fuel mass `rk`/`rkte`, injection
   time `ti`/`te` per bank, rail pressure actual/setpoint, `rl`, `nmot`) find
   their writer functions in the Ghidra project.
2. Reconstruct the chain per bank: rk -> corrections (lambda target, start,
   warm-up, adaptation fra/frau, component protection) -> fuel mass per stroke
   -> conversion to injection time: injector constant (KRKATE-equivalent;
   determine whether it is mass- or volume-based and its unit/scaling),
   rail-pressure correction (curve/map over pressure), battery-voltage dead
   time (TVUB), FKKVS-type correction, minimum/maximum time and injection
   window limits, split/multiple injection if present.
3. Name the **candidate multiplication point(s)** for a flex-fuel factor:
   the instruction(s) where fuel mass is scaled before time conversion.
   Record the calling context (task from B1, registers live, fixed-point
   format of the value). Prefer a point that affects all injection modes
   (start, homogeneous, stratified if used).
4. Verify statically: decompile the chain into a Python model
   (`emu/models/injection.py`) and, with the A5 Unicorn harness or Ghidra's
   emulator, run the ECU function on sample inputs and compare with the
   model (bit-exact). Note any FPU use.
5. Record maps/curves with addresses in `re/calibration_draft.csv`
   (coordinate with B5's format) and everything in `re/findings/injection.md`;
   add symbols; commit on `agent/B6`; comment on #14.

## Acceptance
Multiplication point named with evidence and context; model reproduces the
ECU function on sample inputs; map addresses for KRKATE-equivalent, TVUB,
FKKVS, rail-pressure correction and the ti limits are listed.
