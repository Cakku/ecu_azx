# Brief B7 — Ignition: KFZW/KFZW2 blend, KFZWOP, final zw, knock retard

Issue: **#15**. Prerequisites: A1, A2, A3 merged; B1/B5 helpful. No `sudo`.

Read `00_common_rules.md`, `docs/05_flexfuel_design.md` §3.4 and §7 first.

## Tasks
1. From measuring variables (ignition angle output, knock retard per
   cylinder `dwkrz`, `wkr`, cam position, `rl`, `nmot`) find the ignition
   module (ZWGRU-equivalent): base map lookups (KFZW, KFZW2 and the blend
   factor), the optimum map KFZWOP used by the torque model, corrections
   (temperature, idle, knock, warm-up), and the final output written to the
   TPU/ignition driver.
2. Name the **insertion point** for an additive ethanol offset: after the
   base blend, before knock retard and limits, so knock control still acts on
   top. Record task, registers, fixed-point format (degrees resolution).
3. Locate the knock control variables and the knock-related enrichment maps
   for later monitoring; confirm `dwkrz`-type variables with the A3 table.
4. Python model + emulator check as in B6 for the blend function.
5. `re/findings/ignition.md`, calibration draft rows, symbols; commit on
   `agent/B7`; comment on #15.

## Acceptance
Blend routine and insertion point named with evidence; KFZW/KFZW2/KFZWOP
addresses and axes listed; knock retard variables identified.
