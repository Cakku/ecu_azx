# Brief A4 — Desk preparation for the hardware tasks

Issues: **#1, #2, #3, #4** (research and preparation parts only). Runs now.
No purchases, no `sudo`. Use WebSearch/WebFetch.

Read `00_common_rules.md` first.

## Deliverable
`re/findings/hardware_prep.md` with sourced answers (URL per claim, rate
confidence) and a comment on each of the four issues with the relevant part.

## Tasks
1. **Spare ECU compatibility (#1)**: list known 03H906032 suffixes (A, AB, C,
   DQ, ... ) with their Bosch numbers, software numbers, engine (3.2 AXZ vs
   3.6 BLV/BWS/BHK), market and gearbox where known. State which suffixes are
   plausible bench substitutes for ours (`03H906032` with SW 1037382557,
   Bosch 0261S02226, dataset D9133_43K6P0) and what must match for our
   patches to transfer unchanged (same software number) versus what only needs
   the same code base. Where are such units sold (EU/Finland used-parts
   sources, eBay categories, price range)? Note immobiliser implications for a
   bench unit (none for KWP logging; matters only for running an engine).
2. **BDM backup route (#2)**: options to get a full BDM read incl. EEPROM:
   own K-TAG (genuine vs clone, K-Suite subscription state, BDM MPC5xx
   positioning frame part), alternative tools that read MPC5xx BDM (list with
   evidence), or a tuning shop service in Finland/EU. Expected file sizes
   (2,097,152 external; 524,288 on-chip; EEPROM 2 KB or 4 KB). Risks (pad
   damage) and how the frame avoids them. Where the BDM pads are on MED9.1
   boards (photo sources).
3. **Bench harness (#3)**: MED9.1.1 connector pinout for the Passat B6 3.2 FSI
   (two connectors; find KL30, KL15, ground pins, powertrain CAN high/low,
   K-line if any, and the pins for the main relay) from public wiring diagrams
   or MED9.1 bench-mode guides; the powertrain CAN bitrate (500 kbit) and
   termination needs on the bench; typical quiescent/active current; whether
   the ECU needs a crank/cam signal or CAN partners to stay awake for KWP.
   Produce a wiring table and a parts list (PSU, connector/pins or breakout,
   transceiver, adapter choice for the Mac).
4. **Windows machine (#4)**: what K-Suite 2.x/3.x needs (OS versions, USB
   driver situation on Windows 11, known VM issues), cheapest sensible
   options, and a checklist to verify the install with the spare ECU.
5. Also gather: VCDS access to the engine ECU on this car (address 01,
   measuring blocks available), and whether an OBD-port CAN adapter sees the
   powertrain bus directly on a 2007 Passat B6 or only through the gateway.

## Not in scope
Anything with the dump; buying anything.
