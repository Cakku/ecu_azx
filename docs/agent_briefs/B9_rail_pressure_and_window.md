# Brief B9 — Rail pressure control and the injection window

Issue: **#17**. Prerequisites: A1, A2, A3 merged; B6 helpful. No `sudo`.

Read `00_common_rules.md`, `docs/05_flexfuel_design.md` §3.6 first.

## Tasks
1. Find the high-pressure control module: rail pressure setpoint maps
   (KFPRSOL-family; axes likely driver torque request and engine speed, with
   variants for operating modes), setpoint limits, the pressure controller
   (P/I terms, pump actuator duty output), the pump volume limit
   (VHDP-equivalent) and its role as rate limiter.
2. Find the injection-window check: how the ECU compares the required
   injection duration against the available angle window, which variable
   flags the exceedance and what intervention follows (torque/throttle
   limitation, DTC). Record the variables for logging.
3. Name the setpoint maps and the intervention path as targets for the
   flex-fuel rail raise and torque limiter (`docs/05` §3.6).
4. `re/findings/rail.md`, calibration draft rows, symbols; commit on
   `agent/B9`; comment on #17.

## Acceptance
Setpoint maps with axes and scaling listed; pump limit and window check
located with the intervention path; logging variables identified.
