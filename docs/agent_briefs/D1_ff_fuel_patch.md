# Brief D1 — ff_fuel: the flex-fuel MVP patch, desk half (#32; state machine of #37)

Issue **#32** (implementation, unit tests, procedures; flashing and the E0
logs stay open) and the software half of **#37** (FAULT/HOLD rules). Wave D:
start only after **C1 and C2 are merged** (framework and RAM block). C3/C4
help but are not required. No `sudo`; nothing is flashed.

Read `00_common_rules.md`, `docs/05_flexfuel_design.md` §3.1-§3.3 and §4-§6
including the dated B2/B6 notes, `docs/06_patch_pipeline.md`,
`docs/04_re_guidelines.md` §7, `patches/common/` and `patches/ff_counter/`
(C1), `re/findings/ram.md` (C2), `re/findings/can.md` §4-§7,
`re/findings/injection.md` §6-§8 and §10, `emu/models/injection.py`,
`tests/test_injection_model.py`, `pico_can_sender/README.md` (the frame),
and `re/findings/scheduler.md` §5 and §8 (check whether C4 settled the
100 ms period).

## Facts (VERIFIED-STATIC unless marked)
- Reception: slot 15 = TouCAN C message buffer 6 (buffer at 0x707960); id
  word **0x2BD8C** `00 00 07 FF` -> `00 00 00 EC` (2 bytes change; block
  descriptor 0x0A0010); `can_init_mb(15)` (0x135750) once at start-up;
  `can_rx_poll(15)` (0x4379C8) returns 8 = fresh frame, 0x40 = nothing new;
  payload at **0x803F9C..0x803FA3** in wire order, id echo at 0x803F98. No
  stock timeout exists for the new slot. That modules B and C share one wire
  is HYPOTHESIS (bench check: C3's `can_bc_check.json`).
- Frame: b0 E% 0-100, b1 T+40, b2 f/2, b3 rolling counter, b6 firmware
  version, b7 status 0 OK / 1 fault / 2 contaminated / 3 not ready.
- Fuel hook: **0x42247C** `bl 0x41C3A0` (`4B FF 9F 25`) in `task_segment_a`
  (0x4223B0, TCB 5, engine-synchronous, id 40); r3-r12 dead; `rk` is u16 at
  RAM **0x803038** with one writer and all readers inside `rksplit`. Stub:
  `rk = min((rk * F_q10) >> 10, 0xFFFF)` then `b 0x41C3A0` (LR untouched,
  `rksplit` returns into the task). F_q10 = 1024 is bit-identical to stock.
  `KRKATE` is mass-based, so F is the §3.3 mass factor unchanged (1.50 at
  E85).
- Periodic hook: **0x12067C** in `task_100ms` (100 ms HYPOTHESIS, <= 150 ms
  verified). Alternative with a VERIFIED 20 ms period: 0x11EC44 `bl 0x630C0`
  in `task_20ms`.
  > **Integration note 2026-09-16 (C4 merged, `scheduler.md` §11-§12,
  > `docs/05` §8):** all rasters are 10x faster than the names say. 0x12067C
  > (`task_100ms`, 0x1205A0) is a **10 ms** raster; `task_20ms` 0x11EC34 is
  > **2 ms**; the true 100 ms raster is task-set-A id 18 / set-B id 31
  > (`tools/ercosek_tasks.py --periods`). Both hook candidates above belong to
  > **task set B**, and which set is live is still open (§11.7; a hook in a
  > dead set never runs). Wait for the five-counter bench read, or hook the
  > set-A twin 0x4328E4 (10 ms) instead — state the choice and why. Express
  > every tick constant per real raster period: at 10 ms the §3.2 filter needs
  > `K ≈ 1/320` (or run it from a true 100 ms task) and the 2 %/s slew is
  > 0.02 %/activation. `can_rx_poll` at 10 ms is fine (Pico frame is 100 ms).
- New calibration: FFCAL001 at **0x5E2510** (0xFF up to 0x5FFFFF; checksum
  block 0x5E0000-0x5EFFFF; address it with `lis 0x5E` like stock code).
  Layout per docs/05 §4: header `FFCAL001`, version u16, length u16, then
  the parameters.
- RAM: the block recommended in `re/findings/ram.md`. Its content is
  **undefined at power-on** unless C2 proved otherwise: detect a valid state
  block by magic word + checksum and initialise it if invalid.
  > **Integration note 2026-09-16 (C2 merged):** the block is
  > **0x7FFB00-0x7FFBFF** (256 B, `re/findings/ram.md` §8.1; VERIFIED-STATIC
  > that nothing references it, dynamic confirmation pending #23). C2 confirmed
  > it is *not* filled at cold start, so the header above is required. Address
  > it absolutely (`lis`/`addi`), never through r13. The external SRAM is
  > **cleared at every cold start** (0x800004-0x80498F) and 0x804990-0x807FFF
  > is the flash driver's programming copy: nothing there survives a key
  > cycle, so persistence goes through EEPROM block 8 (D2). `ff_counter` uses
  > +0x00..+0x07 of the same block when flashed alone; state where ff_fuel
  > puts its own state (reusing +0x00 is fine, the two are never co-flashed).
- Engine state available to the patch: `B_stend` 0x7FE921, "engine not
  running" 0x7FEAD0, `tmst` 0x8021F6 (0.75 C/LSB, -48 C).

## Design decisions to implement (deviate only with a reason in the report)
- Fixed point: `E_filt` u16 in 1/16 % (0..1600); the 17-point curve has a
  breakpoint every 6.25 % = 100 counts, so `index = E_filt / 100`,
  `frac = E_filt % 100`, integer only. `F_q10` u16, clamped **in code** to
  [1024, 2048] whatever the calibration says. Time in periodic ticks (100 ms,
  or 20 ms if you move the hook — then scale the tick constants).
- State machine (docs/05 §3.2): OK / HOLD (status 2) / FAULT (status 1 or 3,
  age > `ff_timeout_ms`, or counter unchanged for 3 received frames). In
  FAULT hold F for `ff_hold_s` (60 s), then decay `E_filt` toward the stored
  key-on value (E0 until D2 adds persistence) at `ff_slew`. Filter
  `K = ff_filter_k` (1/32 per tick), slew limit `ff_slew` (2 %/s). Power-up:
  E0, FAULT until the first valid frame, F = 1024.
- `ff_mode` u8 in FFCAL001: 0 = off (F forced to 1024, no CAN poll),
  1 = normal, 2 = bench override (E from `ff_e_override`, no CAN needed; for
  the bench ECU without a sensor). Shipped default **1**; document how to
  build a mode-0 file.
- Only the multiply runs in the segment task; everything else in the
  periodic hook. Count the instructions of both stubs for the README.
- Reserve every docs/05 §4 parameter the MVP does not use yet
  (`ff_fzw_curve`, `ff_dzw_map`, `ff_fst_map`, `ff_prail_add`) in the block
  with neutral values, so later patches extend the block instead of moving it.
- Init: call `can_init_mb(15)` from the periodic hook on the first valid
  state-block initialisation (idempotent, can.md §7), not by editing
  `can_rx_arm_all`.

## Tasks
1. `patches/ff_fuel/`: `src/ff_fuel.c` (+ `ff_state.h`, shared with D2),
   `hooks.S` on C1's macros, `ffcal001.py` (builds the calibration block from
   a JSON of parameters; F curve from the §3.3 formula with F(0) = 1024
   exactly; also emits descriptor rows for `re/calibration_draft.csv` /
   the XDF), `patch.json` (the id-word edit marked `"calibration_edit":
   true`, the two hook words, the blob, the FFCAL001 block), `Makefile`,
   `README.md`, `test/`.
2. `emu/models/flexfuel.py`: reference model (frame validation, state
   machine, filter and slew, curve, scaling) and
   `tests/test_flexfuel_model.py` (formula values, F(0) == 1024, monotonic
   curve, HOLD/FAULT transitions, decay, the 60 s hold).
3. Emulator tests `tests/test_ff_fuel_patch.py` on the applied image (C1's
   apply tool into a temp dir): (a) the segment stub with F = 1024 leaves
   0x803038 untouched for rk in {0, 1, 0x7FFF, 0xFFFF} and arrives at
   0x41C3A0 with r1 and LR unchanged; with F = 1536 it gives
   `(rk * 1536) >> 10` saturated; (b) the periodic hook with TouCAN C MB6
   modelled through `stub_read` (IFLAG bit and buffer bytes at 0x707960.. per
   can.md §4) for a sequence of frames -> RAM state equals the Python model
   tick by tick, including counter stall, timeout, contaminated and
   recovery (if the real `can_rx_poll` cannot run in the harness, stub the
   call and say so); (c) the first run initialises the state block from
   garbage; (d) `ff_mode` 0 and 2 behave as specified; (e) stock vs patched
   RAM diff after one periodic call: only the state block differs.
4. Procedures in `test/`: expected log lines (F, E_filt, mode at the state
   block addresses; write `logging/sessions/ff_fuel.json`), the E0
   equivalence `tolerance.json` over `rk` 0x803038, `ti_sum` 0x8030C4, `zw`
   0x7FEF87, `prist` 0x8031DA, `nmot_w` 0x7FEE74, `rl` 0x7FED38 (add lambda /
   `fra` if you can identify them in `re/measuring_vars.csv`, else list as
   open), and the fault-injection matrix of #37 (sensor unplugged, Pico
   stopped, contaminated status, counter stall) with the expected mode and F
   per step.
5. Dated notes in docs/05 §3.2 / §3.3 (what was implemented, the fixed-point
   formats, deviations); docs/06 if the framework needed changes;
   `re/calibration_draft.csv` rows for FFCAL001 (confidence static, consumer
   = the patch). Commit on `agent/D1`; comment on #32 and #37.

## Acceptance
Builds; applies with ALL OK and a clean bindiff; every hook word and the blob
disassembled and free of r2/r13; all tests pass; the F = 1024 path is proven
bit-identical in the emulator; the README states RAM use, instruction counts
and the exact bytes changed.
