# Brief B8 — Start and warm-up enrichment

Issue: **#16**. Prerequisites: A1, A2, A3 merged; B6 helpful (shares the fuel
chain). No `sudo`.

Read `00_common_rules.md`, `docs/05_flexfuel_design.md` §3.5 first.

## Tasks
1. Identify the engine-state logic (start detected, running, afterstart
   timer) and the start fuel path: cranking quantity maps over coolant
   temperature (and engine speed/intake temperature), afterstart enrichment
   decay, warm-up factor, and where they multiply into the fuel chain found
   in B6 (or into the same variable if B6 is not merged yet).
2. Record maps/curves, axes, scaling, and the multiplication points suitable
   for a 2D ethanol/temperature factor.
3. Identify the start ignition angle handling (ZWST-equivalent) in case
   start timing needs an offset.
4. Model + emulator check for the enrichment factor computation.
5. `re/findings/start.md`, calibration draft rows, symbols; commit on
   `agent/B8`; comment on #16.

## Acceptance
Start/afterstart/warm-up maps and their multiplication points named with
evidence; tmot variable confirmed against A3.
