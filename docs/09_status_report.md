# Status report — flex-fuel feature

Written 2026-09-24 against `main` 4fcff77 (waves A-H merged and pushed; 882
tests OK; `logging/bench_rehearsal.py --fresh-eeprom` 84/84). It answers three
questions: where the project stands, whether the flex-fuel feature is ready
barring bench verification, and which questions are still open and what bench
or car work answers each. It records no new fact; every line points at the
document that carries the evidence. Re-issue it after the bench day.

## 1. Overall status

**The desk work is finished; everything still open needs the bench or the
car.** Nothing has been written to any ECU, and the hardware for doing so does
not exist yet: no spare ECU (#1), no BDM backup or demonstrated write-back
route (#2), no bench harness (#3), no Windows machine for K-Suite (#4). The
project history and the open-issue list are [`01_project_plan.md`](01_project_plan.md)
§2; the bench-day order is [`08_bench_playbook.md`](08_bench_playbook.md).

| Part | State | Where |
|---|---|---|
| Reverse engineering | Every insertion point of the feature is resolved to an address, VERIFIED-STATIC, with emulator runs where the sections say so. Desk leftovers are small: naming pass 6 (#49) and four loose ends listed in `docs/agent_briefs/README.md` ("Open after wave H"). | [`02_memory_map.md`](02_memory_map.md), [`05_flexfuel_design.md`](05_flexfuel_design.md), `re/findings/` |
| `patches/ff_fuel` | One patch, four features, eight hook words (seven in the on-chip flash), FFCAL001 v5 (334 B). Fuel scaling (#32) and E% persistence (#38) are **on** in the shipped file; the ignition blend (#34), start enrichment (#35), rail adder (#36) and OBD PID 0x52 (#39) ship **disabled** with neutral tables. E0 bit-identity is proven in the emulator for every feature. | `05` §3-§5, `patches/ff_fuel/README.md` |
| `patches/ff_counter` (Flash 1) | Hooks the 10 ms raster of both task sets; decision table rows A-I written. | `patches/ff_counter/test/procedure.md`, `08` step 6 |
| Pipeline and tooling | Build, `patch_apply`, `bindiff`, `checksum`, `make bench-kit` (all four bench images with a manifest), the KWP logger, and an emulated ECU that runs the firmware's own KWP, ISO-TP and patch code. The build reproduces byte for byte. | [`06_patch_pipeline.md`](06_patch_pipeline.md), [`07_workflow.md`](07_workflow.md) |
| Pico node | Firmware written (`pico_can_sender/src/ff_sensor.c`) with host tests; the signal-generator bench test (#29), the powertrain bus survey for id 0x0EC (#30) and the sensor install (#31) are open. | `pico_can_sender/README.md`, `05` §2 |
| Calibration definition | `re/med9_draft.xdf`, 1,079 tables and 409 constants; naming continues under #49. | `07` §1 |
| Documentation | 01-08 consolidated 2026-09-24: current facts in place, no layered notes. | [`README.md`](README.md) |

Rulings that govern the bench day (Carlo, 2026-09-24; `01` §7): no write of
any kind, Flash 0 included, before the #23 RAM snapshots; bench baselines are
taken engine-off; `ff_persist_enable` stays 1 and the bench step for it is a
*read* of EEPROM block 8 +19..+28 before the first `ff_fuel` flash.

## 2. Is the feature ready barring bench verification?

**The code is.** There is nothing left to write at the desk for the fuel MVP
or the three disabled features, and every procedure has been rehearsed against
the simulator. But "verification" undersells what the bench has to do, in
three ways.

**Four bench results could still force a design change.** They are tests of
the tool and the hardware, not of the patch, and no desk work can settle them:

| If the bench shows … | Then … | Settled by |
|---|---|---|
| KESSv2 protocol 179 does **not** write the on-chip flash 0x404000-0x47FFFF | seven of the eight hooks are unreachable over OBD and the patch is inert on every fuel, ignition, start and rail path. The way forward is the ladder of `re/findings/flash_programming.md` §7.3: K-TAG/BDM, or a tool that drives the firmware's own `10 85` route. No rebuild helps; only the raster hook has an external-flash twin | Flash 1's read-back of 0x432940 (`08` step 6b). Flash 0 cannot tell, because its file is the stock image |
| ERCOSEK task set **B** is live, or both sets run | `scheduler.md` §11.8 is wrong; the ignition, start and rail features (E1/E2/E5) assumed set A and every "once per 10 ms" claim is re-checked. Stop (S9) | the five raster counters (`08` step 3c) and Flash 1 rows B/C |
| TouCAN module **C** is a separate bus from module B | the Pico is wired to the wrong pair and no frame ever arrives; module B has no free message buffer, so reception needs a new design | `logging/sessions/can_bc_check.json` with the engine running (`05` §3.1, #22) |
| The RAM block 0x7FFB00 changes between the six snapshots | both patches move to the fall-back block (candidate 2, `re/findings/ram.md` §9); a desk brief, not a bench fix | `08` step 4 (#23) |

A fifth result changes one byte rather than the design: if block 8 payload
+19..+28 on the real EEPROM is anything but 0x00, the first `ff_fuel` image is
built with `ff_persist_enable` = 0 (`08` S12).

**One phase can only happen on the car.** The shipped calibration is the
formula and neutral tables: the F curve is untrimmed, the ignition map is all
zero, the start map all 1.0, the rail curve all zero. Filling them is #33-#36
and #40, one feature at a time, and the real engineering risk of the project
sits there: the injection window at high load, where the stock full-load and
component-protection enrichment (λ down to 0.70) stacks under `F(E)` (`05`
§3.3 and §3.6). The stock ECU does not cut fuel or torque when the window is
exceeded; it advances the start of injection silently.

**Two decisions are still the human's** (`05` §3.3 point 5): whether to
reduce the stock enrichment itself on ethanol as a new, disabled-by-default
feature, and whether the window margin at E85 under component protection is
acceptable or needs a torque limit at the 0x0C7CF8 min-chain.

So the honest verdict is **ready to go to the bench**, not ready to drive. The
bench day itself, if every row lands as predicted, closes #3, #20, #23, #26,
#27, the on-chip half of #32 and the live-set row of #44, and opens the door
to the car.

## 3. Open questions and the work that answers them

### 3.1 On the bench spare, in the order of `08_bench_playbook.md`

| # | Question | Why it matters | Answered by | Issue |
|---|---|---|---|---|
| 1 | Does the harness work: pins, termination, cyclic frames, TesterPresent over TP2.0, Pico frames visible? | nothing later is interpretable without it | steps 1-2 (`re/findings/hardware_prep.md` §3, `logging/README.md` §8) | #3 |
| 2 | Is this unit really software 1037382557? | otherwise its reads are not evidence for the patch | flash CRC read publishes 0x5562139F (step 2d, `flash_crc.json`) | #20 |
| 3 | What is the background-loop period T_bg? | closes the CRC-task question of G3 | slope of `bg_loop_count` at key-on and at idle (step 2d, 3c) | #20 |
| 4 | Which task set is live? | see §2 | five raster counters (step 3c); Flash 1 rows A-I | #44 |
| 5 | What do EEPROM blocks 10 and 8 hold before any write? | block 10 proves the programming route ran; block 8 +19..+28 decides `ff_persist_enable` (S12) and +2..+18 are the fuel trims | steps 3e-3f (`procedure_d2.md` §B1, `adaptation_channels.json`) | #26, #38 |
| 6 | Is the patch RAM block untouched at runtime? | gate for every write | six RequestUpload snapshots, `ram_snapshot_diff --free 64` (step 4) | #23 |
| 7 | Does the write route work, is KESS's checksum correction a no-op, is there an unknown check, does the read-back equal the file? | the route itself | Flash 0 (step 5): bindiff 0 ranges, `5A5A` marker, block 10 changed, CRC still 0x5562139F | #26 |
| 8 | Does KESSv2 write the on-chip flash? | see §2 | Flash 1 read-back of 0x432940 (step 6b) | #27, #32 |
| 9 | Does the counter rise at 100/s, and which stub ran? | the 10 ms raster end to end; C4's tick chain | Flash 1 slope and `ff_src_seen`, rows A-I (step 6c); regression `logcmp` exit 0 | #27, #44 |
| 10 | Does a programming session reset the adaptation channels? | a reset moves fuel trims 4/8/10 under `F(E)`; H2 predicts only routine 0xC5 does | `adaptation_channels.json` before and after Flash 0 and Flash 1 | #47, #28 |
| 11 | Does the frame land at 0x803F9C and does `ff_mode` leave FAULT? | first proof of reception | `procedure.md` §2-§3 with the Pico on the bench wire | #22, #32 |
| 12 | Is the patched image E0-equivalent to stock on hardware? | the safety argument | `procedure.md` §4 engine-off against the step-3d baseline, `logcmp --align-on` | #32 |
| 13 | Does the fault matrix hold on hardware? | the #37 asymmetry | `procedure.md` §5, six injected faults | #37 |
| 14 | Does the E% survive a power cut and seed the first activations? | persistence end to end | `procedure_d2.md` part B, after the block-8 read of item 5 | #38 |
| 15 | Do VCDS groups 111/108/69/109 read correctly, does the generic OBD route reach the handler, does PID 0x52 answer after a 5 s gap? | diagnostics | VCDS; `logging/obd_client.py` on 0x7DF (`08` step 7 item 4) | #39, #48 |

### 3.2 On the car, read-only logging first

| # | Question | Answered by | Issue |
|---|---|---|---|
| 16 | Is TouCAN module C on the same wire as module B? | `can_bc_check.json` with the engine running: module-C frames (ids 0x050, 0x0C2) and module-B frames (0x1A0) all live. **Settle before wiring the Pico** | #22 |
| 17 | Do the wave-B units hold: `ti` 1 µs/LSB, `zwist` 0.75 °CA, `dwkrz` cylinder order, `nmot`/`rl` scaling, `tans`, `gangi`, `rkte_w`? | `wave_b_confirm.json` and `groups` against VCDS, idle plus one load step (`08` step 3c) | #44 |
| 18 | How much injection-window margin is there at WOT, on gasoline? | `dwi` and `wbho1s` in a WOT pull; `win_margin_min` in group 109 | #44, #33 |
| 19 | Does `rlsol_req` ever exceed 100 % on this naturally aspirated engine, i.e. does the full-load enrichment ever fire? | ids 375/376 at WOT (`tuning_checklist.json`) | #46 |
| 20 | Does the ECU ever fuel with `bdemod_w` bit 0 clear (λ floor 0.700)? | ids 503/43 on a cold start; id 410 bit 2 set while id 43 stays 1.000 | #46 |
| 21 | Are the three fuel-trim channels 4/8/10 at 128 on this car? | `adaptation_channels.json` before trimming anything; record with the calibration | #33 |
| 22 | Does T_bg at idle match the key-on figure? | `bg_loop_count` slope (`flash_crc.json`) | #20 |

### 3.3 On the car, calibration — one feature at a time (S11)

| # | Question | Answered by | Issue |
|---|---|---|---|
| 23 | Does the F curve hold λ at E20, E50, E85: `fra/frau` within ±5 % of 1.0, no lean event at WOT? | `procedure.md` §6 with a wideband; trim `ff_F_curve` (a calibration change, chapter 1 of `07`) | #33 |
| 24 | Is the window margin at E85 acceptable at the **lowest** `lamsbg_w` a pull produces? | `win_margin_min` with ids 43/44, 376, 377/1555, 410 on every WOT pull; one long, hot pull at E50 to reach component protection **before** any step above E50 (`05` §3.3 point 4) | #33 |
| 25 | Does `prist` stay above 13 bar and does the pump keep volume (`0x80316E` below 5000) on high blends? | `prist_min`, `msv_sat_ticks` (group 109); the acceptance signals of `05` §3.6 | #33, #36 |
| 26 | What advance does each `ff_dzw_map` cell tolerate, calibrated warm, with `dwkrz` and the low-octane latch as acceptance signals? | `procedure_e1.md` §B3, against the `KFZWOP − KFZW` budget minus the live `zwdelta_load` 0x7FD338 | #34 |
| 27 | What does `f_st(E, tmst)` need across the temperature range, and does the start advance stay small? | `procedure_e2.md`; the cold-start series at 20, 10, 0, −10 °C over a season | #35, #40 |
| 28 | Does the rail adder buy atomisation without saturating the pump? | `procedure_e5.md` | #36 |
| 29 | Should the stock enrichment be reduced on ethanol, and does the window margin need a torque limit? | human decisions with their own evidence (a thermocouple or the `%ATM` model against the car; the 0x0C7CF8 design note in `procedure_e5.md` §6) | #33, #36 |
| 30 | Flash 0 and Flash 1 on the **car's** ECU: no DTCs, adaptation unchanged, counter visible over OBD | with the BDM backup of the car's unit in `data/backup_bdm/MANIFEST` and only files that ran on the spare | #28 |

### 3.4 Prerequisites that are neither bench nor car

| Item | State | Where |
|---|---|---|
| Recovery route before any ECU purchase: does the owned tool offer K-TAG Service Mode for `0261S02226`? | undecided; first action of Phase 0 | `re/findings/hardware_prep.md` §2.3, §2.10 |
| A cheap VR6 `03H906032` mule for harness and KWP work; a software-matching spare only when a flash is imminent | not bought | `hardware_prep.md` §1.4b (#1) |
| BDM backup of external flash, on-chip flash incl. 0x400000-0x403FFF, EEPROM, with a demonstrated write-back route | not taken; `data/backup_bdm/MANIFEST` does not exist | `hardware_prep.md` §2 (#2); `01` M1 |
| A tool that drives the firmware's serial (SCI) recovery loader; which pin SCI1 is bonded to | open | `re/findings/ram_loader.md` (F5); `06` §6.3 |
| Bench harness, current-limited supply, candleLight/gs_usb CAN adapter with switchable termination, Windows box | not built | `hardware_prep.md` §3, `logging/README.md` §6-§7 (#3, #4) |
| Pico node: 40-200 Hz signal-generator test of every status path; powertrain bus survey for 0x0EC; sensor in the feed line | firmware written; hardware steps open | `pico_can_sender/README.md` "Bench test", `05` §2 (#29-#31) |

### 3.5 Desk leftovers (small, none blocking)

Naming pass 6 (#49: `%TEB` adaptation `FUN_000ECE7C`, `%GK` `mix_*`, a
detector for index-read tables); the `ram_survey.py` label for 0x7FE5A4;
whether the adaptation reset repeats when power is cut before block 11's
key-off write-back; 0x13AE18 (a 0x7DF frame during a TP2.0 connection); the
0x7D0 range object; the triggers of catalyst clear-out and the OBD diagnoses,
traced to their inputs only (`re/findings/calibration_names.md` §12.5). H1's
two questions (items 19-20) are desk questions that only a log can settle.
