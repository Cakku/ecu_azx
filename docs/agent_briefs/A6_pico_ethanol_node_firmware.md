# Brief A6 — Pico ethanol sensor node firmware

Issue: **#29**. Software only; hardware tests are left to the human. No `sudo`.

Read `00_common_rules.md` and `docs/05_flexfuel_design.md` §2 first, then
`pico_can_sender/` (Pico 2 / RP2350, MCP2515 over SPI0, SSD1306 display,
existing libs under `lib/`).

## Tasks
1. Toolchain: `brew install cmake ninja` and the Arm GNU toolchain
   (`brew install --cask gcc-arm-embedded` or the Pico SDK's own install; no
   sudo). Fetch the Pico SDK (version per `pico_sdk_import.cmake`) into
   `work/` and make the existing project build. Record the exact steps in
   `pico_can_sender/README.md` (macOS section; keep the Windows section).
2. Replace the potentiometer demo with the sensor node:
   - GPIO input with edge-timestamped IRQ (both edges) measuring period and
     low-pulse width; 4-sample ring average; debounce 0.25 ms; 250 ms update.
   - Plausibility per the design: 45-155 Hz valid, 155-200 Hz contaminated,
     otherwise/no edges for 500 ms fault; E% = (f - 50), temperature
     `41.5 * low_ms - 81.25`.
   - Frame id 0x0EC (compile-time constant, also settable at runtime via a
     small config struct), DLC 8, 10 Hz: byte0 E%, byte1 temp+40, byte2
     freq/2, byte3 rolling counter, byte4-5 0, byte6 firmware version,
     byte7 status (0 OK, 1 fault, 2 contaminated, 3 not ready).
   - Keep the OLED: show E%, temperature, status, CAN OK.
   - Pull-up note in README (2.2-3.5 kOhm to 5 V for the sensor's open
     collector; level shifting to the Pico input).
3. Put the pure measurement/state logic in a header-only or separate C file
   compiled both for the Pico and for the host; add `pico_can_sender/test/`
   with a host test (`cc` on macOS) feeding synthetic edge timestamps for
   40, 49, 50, 100, 150, 156, 185 Hz and no signal, asserting E%, temperature
   and status per the design's test table. Run it.
4. Provide a `tools/ethanol_frame_decode.py` (python-can/cantools optional)
   that decodes the frame from a SavvyCAN/candump line for later bench use,
   and a `data/ethanol_node.dbc` fragment describing the frame.
5. Commit on `agent/A6`. Comment on #29 with build instructions and the
   generator test plan the human should run on hardware.

## Not in scope
Flashing the Pico, real sensor tests, ECU side.
