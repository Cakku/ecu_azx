# Project plan

## 1. Goals

**Primary.** Give the 2007 Passat 3.2 FSI (Bosch MED9.1.1) a proper
flex-fuel capability: the ECU reads ethanol content from a CAN frame produced
by a Raspberry Pi Pico attached to a GM/Continental flex-fuel sensor and
adjusts fuelling, ignition, cold start and rail pressure continuously from E0
to E85, with safe behaviour when the sensor or the bus fails.

**Secondary.** Turn the reverse-engineering work into a tuning platform for
this software version: a definition of the calibration maps (XDF/A2L-style),
a patch pipeline for custom code, and live logging, so later hardware changes
(intake, exhaust, cams, injectors) can be tuned on the same tooling.

**Non-goals.** Defeating the immobiliser or component protection, emissions
defeat, or supporting other ECUs. Anything that needs those is out of scope.
Whether a converted car remains road-legal (inspection, registration of the
fuel change) is the owner's responsibility and is not covered here.

## 2. Where we are (2026-09-24)

**The desk work is done; everything still open needs the bench or the car.**
Eight waves of agent work (A-H, `docs/agent_briefs/README.md`) are merged
into `main` (4fcff77): 882 tests OK, `checksum.py verify` ALL OK (65 blocks).

- The full KESSv2 read of the car's ECU is `data/passat_azx_ori.bin`.
  **Nothing has been written to any ECU.**
- Static RE is done to the depth the flex-fuel feature needs: dump structure,
  CPU address map, small-data registers, checksum algorithm, CAN/KWP/OBD
  tables, boot module, ERCOSEK rasters, the injection, ignition, start, rail,
  lambda-request and EEPROM paths, and the firmware's own flash-programming
  routes (`docs/02_memory_map.md`, `docs/05_flexfuel_design.md`,
  `re/findings/`). Phase 1 (#7-#19) closed 2026-09-16.
- **`patches/ff_fuel`** is one patch with four features — fuel scaling (#32),
  ignition blend (#34), start enrichment (#35), rail-pressure adder plus
  injection-window diagnostics (#36) — eight hook words, seven of them in the
  on-chip flash; its calibration block FFCAL001 v5 (334 B at 0x5E2510); VCDS
  measuring groups 111 / 108 / 69 / 109; E% persistence in EEPROM block 8
  payload +19 (#38); OBD PID 0x52, run-time gated (#39). Fuel scaling and
  persistence are on in the shipped file; the other three features and PID
  0x52 ship disabled (`ff_*_enable` = 0 with neutral tables), and the E0
  bit-identity of every feature is proven in the emulator. **`patches/ff_counter`**
  is Flash 1 (#27): a counter hooked into the 10 ms raster of both task sets.
- The KWP logger `logging/med9log.py` (#20) and the emulated ECU
  `logging/ecu_sim.py`, which answers with the firmware's own KWP, ISO-TP and
  patch code; every bench procedure is rehearsed against it
  (`logging/bench_rehearsal.py` 84/84). The bench day is
  [`08_bench_playbook.md`](08_bench_playbook.md); the operating procedures are
  [`07_workflow.md`](07_workflow.md).
- The calibration definition `re/med9_draft.xdf`: 1,079 tables and 409
  constants (naming passes 1-5; #41 closed, #49 continues).
- Hardware on hand: Pico 2 + MCP2515 CAN sender, Pi Zero CAN sniffer, KESSv2.
  **No spare ECU (#1), no BDM backup (#2; `data/backup_bdm/MANIFEST` does not
  exist), no bench harness (#3), no Windows machine (#4).**

What blocks the first flash is hardware only. The patch RAM block 0x7FFB00 is
VERIFIED-STATIC and needs the six RequestUpload snapshots of #23 (nothing is
written before they are in, §7). Whether KESSv2 protocol 179 writes the
on-chip flash is a property of the tool that Flash 1's read-back of 0x432940
answers (`docs/08` step 6; Flash 0 writes the stock image, so its read-back
cannot tell). Open issues, each with a bench or car half: #1-#4, #20, #22,
#23, #26-#40, #42-#49. The small desk leftovers are naming pass 6 (#49) and
the two open questions of #46.

| Wave | Merged into `main` | Delivered |
|---|---|---|
| A, B | 2026-09-16 (647efe6) | Phase 1 (#7-#19): Ghidra project and setup script, symbol knowledge base, MPC5xx register decode, boot module, KWP, CAN, injection, ignition, start, rail and EEPROM paths, calibration-map inventory; Unicorn harness (#21); `bindiff`/`logcmp` (#24) |
| C, D | 2026-09-17 (8c93421) | patch framework (#25); Flash 1 counter (#27, software); static RAM survey and the patch block 0x7FFB00 (#23, static half); KWP logger proven against the emulated ECU (#20); ERCOSEK rasters, ten times faster than assumed (#44); `ff_fuel` MVP with E0 bit-identity (#32/#37); measuring block 111 and EEPROM persistence (#39/#38); first calibration definition (#41) |
| E | 2026-09-17 (2652ede) | ignition, start and rail features as disabled extensions of `ff_fuel` (#34-#36); simulator that runs the patch with a Pico and an SPI-EEPROM model; the firmware's own OBD programming route read from the dump (on-chip flash writable, no boot-time integrity gate); `07_workflow.md` (#42) |
| F | 2026-09-23 (a264682) | `ff_counter` hooks both task sets (F1); `logcmp --align-on` / `derive` (F2); simulator init entries and NVM binding (F3); lambda path and #43 checklist columns (F4); the RAM bootstrap loader and the recovery question (F5); OBD PID 0x52 located (F6) |
| G | 2026-09-24 (c25bc35) | PID 0x52 implemented (G1); doc debt (G2); CRC-task period (G3); naming pass 4 (G4); simulator fidelity (G5); `08_bench_playbook.md` (G6); E% store moved to block 8 +19 (G7) |
| H | 2026-09-24 (4fcff77) | stock λ < 1 request path settled (H1, #46); adaptation-reset flag (H2, #47); generic OBD route over 0x7DF (H3, #48); housekeeping and the two bench rulings written in (H4); naming pass 5 (H5); `make bench-kit` (H6) |

## 3. Principles

1. **Evidence before action.** A fact is used only at the level it has been
   verified to (`docs/README.md` legend). Old notes are hypotheses.
2. **Bench before car, spare before own.** Every flash goes to a spare ECU on
   the bench first. The car's ECU is written only with files that already ran
   on the bench. The unit used for harness, power-up and KWP work can be
   **any** VR6 `03H906032`; only the flash rehearsal needs a
   software-matching one (`re/findings/hardware_prep.md` §1.4b).
3. **Reversible and minimal.** Patches live in free flash, hook at as few
   places as possible, and default to stock behaviour (factor 1.0 at E0 and on
   any fault). A patched file at E0 must behave identically to stock.
4. **Checksums are corrected, never disabled.** We know the algorithm; the
   ROM check stays active as a guard against corrupt flashes.
5. **One variable at a time.** Fuel scaling first, then ignition, then start,
   then rail pressure, each validated with logs before the next.
6. **Everything reproducible.** Findings come with the script or Ghidra
   address that produced them; tools are re-run after every change to the
   binary.

## 4. Phases

### Phase 0: Foundation and safety (before any RE work depends on it)

*Status 2026-09-24: the dev-environment row is done (`docs/03_tooling.md`);
every hardware row is open (#1-#4). The plan was restructured on 2026-09-22
(`re/findings/hardware_prep.md` §1.4b): the recovery route comes before any
ECU purchase, and a cheap VR6 mule is enough for harness and KWP work.*

| Task | Deliverable | Exit criterion |
|---|---|---|
| **Settle the recovery route before buying any ECU** (`re/findings/hardware_prep.md` §1.4b). First action: check whether K-Suite **Service Mode** (§2.3) covers `0261S02226` — if it does, no BDM frame is needed at all | decision recorded in the findings doc | Service Mode support confirmed or ruled out |
| Bench mule: **any** VR6 `03H906032` — 3.6, Touareg, Cayenne, Phaeton, Q7, or a unit sold *defekt/ungeprüft* (€20-80, §1.4b) | bench ECU | on the desk |
| Software-matching spare: `03H906032` / `0261S02226` / SW `1037382557`. **Standing search; buy when a flash is imminent, not before** — a donor on another SW number cannot be made into a byte-identical twin over OBD (§1.4b step 1) | flash-rehearsal ECU | on the desk before Flash 0 goes to the car |
| Get a full **bench/BDM read** of the car's ECU: external flash, on-chip flash incl. 0x400000-0x403FFF, EEPROM. Routes cheapest first (`re/findings/hardware_prep.md`): Service Mode §2.3, a shop with a master tool §2.7 (€50-150), clone BDM100-class frame §2.4 (€30-90), K-TAG protocol 64 §2.2 | `data/backup_bdm/…` + SHA-256 in a manifest | files verified; `checksum.py verify` OK; on-chip region compared with the KESS tail |
| Bench harness: 12 V supply with current limit, ignition-switched line, CAN transceiver, OBD-style connector, Pico frame injection, PC CAN adapter | wiring doc + photo | ECU boots and answers a KWP TesterPresent on the bench |
| Windows machine for K-Suite (Intel laptop recommended; Windows-on-ARM VMs are risky for the USB drivers) | working KESS/K-TAG install | reads the bench mule |
| Dev environment on the Mac (`docs/03_tooling.md`): Ghidra, Python venv, PowerPC GCC | documented setup | a hello-patch compiles and disassembles correctly |
| Download the MED9.1 Funktionsrahmen PDF (see tooling doc) into `documents/` (gitignored if large) | reference | |

### Phase 1: Static reverse engineering foundation

*Status: closed 2026-09-16 (#7-#19). Every row has a findings file under
`re/findings/`; the results are summarised in `docs/02_memory_map.md` §7 and
`docs/05_flexfuel_design.md` §3. The one exception is the last row's
"live" confirmation of a CAN buffer, which is a bench item (#22).*

| Task | Deliverable | Exit criterion |
|---|---|---|
| Ghidra project with the verified memory map (blocks, aliases, r2/r13 values, on-chip flash) via `ghidra_scripts/med9_setup.py` | project + script | decompiler resolves r13/r2 globals; xrefs into calibration resolve |
| Symbol knowledge base `re/symbols.csv` with export/import scripts | csv + scripts | round-trips Ghidra <-> csv |
| Run 360trev/MED9inf on the dump: variable table (TKMWL) and names | `re/measuring_vars.csv` | nmot, rl, ti, lambda, tmot identified with RAM addresses |
| Name the boot path, scheduler and task periods (10/20/100 ms tasks) | findings note | the periodic task where a patch would run is identified |
| CAN receive path: CAN_CONF table 0x2BC90 -> TouCAN slot -> RAM buffer -> consumer functions; which TouCAN is the powertrain bus | findings note | for one known frame (0x1A0 Bremse_1) the RAM buffer is identified and later confirmed live |
| KWP: dispatcher at 0x2B870, security access (0x27) algorithm, DDLI (0x2C) and 0x21 handlers, RequestUpload (0x35) | findings note | needed for logging |
| Injection path: from relative fuel mass to injection time (KRKATE-equivalent constant, TVUB, FKKVS, rail pressure influence, ti window limits) | findings note with map addresses | the multiplication point for a fuel factor is identified |
| Ignition: KFZW/KFZW2 blend, KFZWOP, knock retard variables | findings note | insertion point for an ethanol offset identified |
| Start/warm-up enrichment maps and their axes | findings note | |
| Rail pressure setpoint maps (KFPRSOL*), pump volume limit (VHDP-equivalent) | findings note | |
| vkKraQu (fuel-quality variant byte) presence check; EEPROM access routines | findings note | |
| Calibration area map: table detector over 0x5C2000-0x5E2FFF with axes and consumers | draft XDF/CSV | at least the maps above are named |

### Phase 2: Dynamic verification infrastructure

*Status: the desk half is done — the logger (#20) proven against the
emulated ECU, the Unicorn harness (#21), `bindiff`/`logcmp` (#24). The
bench half — the logger on hardware (#20), the Pico frame in a real RX slot
(#22) — is open.*

| Task | Deliverable | Exit criterion |
|---|---|---|
| Live RAM logger over TP2.0/KWP2000 (0x2C + 0x21, RequestUpload) from Mac or Pi (EliasTuning/MED9RamReader or pq-flasher derived) | `logging/` scripts | logs nmot/rl/ti/lambda on the bench ECU and in the car via OBD |
| Emulation harness for pure functions (Ghidra EmulatorHelper or Unicorn) with the verified map (DECRAM, ISB=1 peripherals, CS2 stub) | `emu/` | reproduces a map lookup result found in a log |
| Bench replay: Pico sends the ethanol frame; SavvyCAN/python-can records what the ECU transmits | procedure | RX slot activity visible in RAM via the logger |
| Regression check tooling: binary diff limited to intended bytes, checksum verify | `tools/` | part of every build |

### Phase 3: Patch pipeline and first flash

*Status: the software is done (framework #25, `patches/ff_counter` #27,
`make bench-kit` builds all four bench-day images). Flash 0, Flash 1 and the
repeat on the car (#26, #27, #28) have not been run; the order and the gates
are `docs/08_bench_playbook.md` steps 5-6.*

| Task | Deliverable | Exit criterion |
|---|---|---|
| Toolchain, linker script, hook helpers (`docs/06_patch_pipeline.md`) | `patches/` framework | hello-patch builds to a raw blob at a chosen address |
| **Flash 0: unmodified file** re-saved through our tools | bench ECU runs it | proves the flash route, checksum acceptance, no hidden signature |
| **Flash 1: no-op patch**: one free-RAM counter incremented from a periodic task, readable via the logger | bench ECU runs it | counter increments; stock behaviour otherwise (compare logs) |
| Repeat Flash 0/1 on the car's ECU with the BDM backup in hand | car runs normally | no DTCs, adaptation values unchanged |

### Phase 4: Flex-fuel MVP (fuel only)

*Status: the ECU patch is written and its E0 bit-identity is proven in the
emulator (#32); the bench and car halves (#32 E0 equivalence, #33 blends) and
the Pico firmware (#29), bus survey (#30) and sensor install (#31) are open.*

| Task | Deliverable | Exit criterion |
|---|---|---|
| Pico firmware: frequency/pulse-width capture, plausibility, status byte, 10 Hz frame in the agreed format (`docs/05_flexfuel_design.md`) | firmware | bench-tested with a signal generator across 40-190 Hz |
| Sensor installed in the feed line, wiring, bus connection decided (powertrain CAN behind the gateway) | install notes | frame visible on the bus, no collisions |
| ECU patch: receive, validate, filter, fuel factor from a 1D curve, applied at the verified multiplication point; factor exactly 1.0 at E0 | patched file | E0 equivalence test passes on bench and car (identical logs to stock) |
| Staged blends E20 -> E50 -> E85 with wideband, watching lambda, fra/frau adaptation, ti vs window, rail pressure, knock | log set | adaptation stays near 1.0 at every blend; no lean excursions at WOT |

### Phase 5: Complete flex-fuel

Ignition blend curve and E-map (#34), start enrichment vs E% and coolant
temperature (#35), rail pressure setpoint raise at high load with
injection-window monitoring (#36), fail-safe rules (#37), persistence of E%
across power loss (#38), diagnostics in a measuring block plus OBD PID 0x52
(#39), long-term evaluation over a winter (#40).

*Status: the code for #34-#39 is written, tested and shipped disabled where
the ships-disabled rule applies (`docs/05_flexfuel_design.md` §3.4-§3.8).
Two things this phase originally assumed do not exist in this software and
were dropped: a separate afterstart/warm-up enrichment factor (there is
none; the start map's decay carries the ethanol correction, §3.5) and a
torque limiter on injection-window overrun (the stock ECU has none; adding
one is a later design, §3.6). The persistence route is EEPROM block 8; the
external SRAM is cleared at every cold start and is not retention RAM (§3.8).
The calibration values need the car and a wideband.*

### Phase 6: Tuning platform

Publish the calibration definition for `1037382557`, document the
logging/flash workflow, and support other hardware changes on the same
pipeline.

*Status: `re/med9_draft.xdf` is published (#41 closed; naming continues under
#49), the workflow is `07_workflow.md` (#42, desk half), #43 is open.*

## 5. Milestones

| # | Milestone | Proves |
|---|---|---|
| M1 | Full BDM backup (external + on-chip incl. 0x400000-0x403FFF + EEPROM) verified against `passat_azx_ori.bin`, **and a demonstrated route to write it back** — our own tool or a shop's | we can always go back |
| M1b | Any VR6 `03H906032` on the bench answering KWP TesterPresent | the harness, the Windows box and the KWP tooling work |
| M1c | A software-matching `0261S02226` / `1037382557` spare on the desk | Flash 0/1 can be rehearsed on a true twin — needed only as M4 approaches (`re/findings/hardware_prep.md` §1.4b) |
| M2 | Ghidra project with correct map; injection multiplication point named | the RE foundation |
| M3 | Live logger reads nmot/rl/ti in the car | symbol map is right |
| M4 | Flash 0 and Flash 1 run on bench and car | the write path is safe |
| M5 | E0 equivalence of the flex patch | patch is inert when it should be |
| M6 | E85 with adaptation near 1.0 and no knock retard increase | MVP works |
| M7 | Cold start at ambient < 5 C on E85 | full feature |

M2 is met (2026-09-16: Ghidra project with the verified map, `rk` at RAM
0x803038 and the hook word 0x42247C named). Every other milestone needs
hardware.

## 6. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Bricked ECU (interrupted OBD write, wrong file) | Quantified 2026-09-22 (F5, `re/findings/ram_loader.md`): the firmware carries a **second** programming route, a RAM-resident bootstrap loader (RAM 0x7F8728) that is entered automatically when the calibration marker at 0x1E2500 is not `5A5A5A5A` (the firmware sets the boot magic itself and reboots into it). It speaks a **serial (SCI) line, not CAN**, and its address filter is a blacklist, so it rewrites almost everything including the resident programming module 0x080000-0x09FFFF that the OBD route refuses. So an interrupted **calibration** or **application** write is recoverable over the connector, not a brick. The loader protects the reset stub (0x0-0x1FFF) and the boot body (0x10000-0x1FFFF): only a BDM slip can corrupt those, and only BDM can repair them. Mitigation: BDM backup first and a route that can write it back — our own K-TAG/BDM frame **or a shop with a master tool** (`re/findings/hardware_prep.md` §2.7); a tool that can drive the loader's serial line (verify which pin SCI1 is bonded to on the bench); battery charger during writes. A spare ECU is a convenience, not the recovery path |
| The 16 KB not in the KESS read hides something we depend on | Understood statically (`docs/02` §2-§3): it is UC3F small block 0, holding the shadow row (reset configuration word, censorship bits) and the live exception-vector branch table. The ECU's own OBD route cannot address it and nothing in this project writes it. Mitigation: BDM read for the backup; never let a BDM tool write 0x400000-0x403FFF |
| Undocumented signature beyond the block sums | Closed for the boot path (E6, `re/findings/flash_programming.md`): there is no signature and no boot-time verdict on flash content; the 65 block sums matter because the *tool* checks them. Flash 0 on the bench spare still proves the write route and that KESS's own checksum correction is a no-op |
| KESSv2 is end-of-life; protocol 179 support may lapse | keep the current K-Suite install working; consider KESS3/K-TAG |
| Immobiliser IV / component protection on a swapped bench ECU | the bench ECU does not need to start an engine; for KWP logging immo state is irrelevant; never mix EEPROM/flash between ECUs |
| HPFP and injector window limits on E85 | The stock ECU does **not** cut fuel or torque on a window overrun — it advances the start of injection silently (`docs/05` §3.6) — and the stock full-load and component-protection enrichment (λ down to 0.70) stacks under `F(E)` (`docs/05` §3.3). Mitigation: measuring group 109 (`win_margin_min`, `prist_min`, `msv_sat_ticks`) and `lamsbg_w` logged on every WOT pull; judge the margin at the lowest λ a pull produces; no blend above E50 until the margins are confirmed; a torque limit, if wanted, is a new hook at 0x0C7CF8 |
| Fuel system material compatibility | inspect lines/seals; change fuel filter after first tanks; watch LPFP pressure |
| Sensor placement errors (air bubbles in return line) | feed-line placement; plausibility on rate of change |
| CAN ID collision or gateway filtering | bus survey with `pi_can_setup/find_active_ids.py`; frame goes on the powertrain CAN directly |
| RAM scarcity (32 KB + 32 KB, heavily used) | Static survey done (C2, #23): patch block 0x7FFB00-0x7FFBFF inside the reference-free region 0x7FF770-0x7FFFEB, above the task stack. The runtime RequestUpload snapshots (`docs/08` step 4) are the gate before any write |
| Timing budget of the hooked task | keep the patch integer-only, few hundred instructions, measured on the bench |
| Wrong r2 context when naming functions | the boot module is delimited exactly (B1, `docs/02` §4): r2 = 0x17FF0 over its 119 functions, 0x5C9FF0 everywhere else |

## 7. Decisions taken

| Date | Decision | Why |
|---|---|---|
| 2026-09-15 | Canonical addresses are CPU addresses; external flash at 0x0, calibration referenced at 0x5Cxxxx, on-chip flash at 0x404000. File offsets are always prefixed "file". | matches the firmware's own use; avoids the 0x400000 confusion of the 2 MB-ECU tools |
| 2026-09-15 | Checksums are corrected with `tools/checksum.py`, not disabled | algorithm is verified; keeps corruption protection |
| 2026-09-15 | Ghidra with the stock `PowerPC:BE:32:default` language plus a setup script, not the `ppc_med9` extension | extension encodes the wrong peripheral map; blocks/registers are better set by script |
| 2026-09-15 | Ethanol frame follows the Zeitronix ECA-2 CAN layout (E% byte, temperature byte, status byte) | off-the-shelf analysers become drop-in replacements for the Pico |
| 2026-09-15 | Patch code is C compiled with a PowerPC EABI toolchain (LLVM 23 in practice, `03_tooling.md` §3), integer-only, no small-data sections, r2/r13 reserved | see `06_patch_pipeline.md` |
| 2026-09-15 | Ethanol frame on slot 15 of the CAN receive table (id 0x0EC), no mask changes | the slot mechanism needs no dispatcher hook (`05_flexfuel_design.md` §3.1) |
| 2026-09-16 | Patch RAM is 0x7FFB00-0x7FFBFF, addressed absolutely, with a magic/length/checksum header | the only reference-free region above the task stack; not cleared at cold start (`06_patch_pipeline.md` §3) |
| 2026-09-16 | E% is persisted in EEPROM block 8 through the stock block manager, never through raw SPI | the external SRAM is cleared at every cold start; the manager owns the block checksum (`05_flexfuel_design.md` §3.8) |
| 2026-09-17 | Every feature added after the fuel MVP ships **disabled**: an `ff_<feature>_enable` byte at 0 *and* a neutral table; FFCAL001 changes append and nothing moves | the flashable file behaves like the proven fuel-only MVP until a human turns one byte on; a v(n) block under a v(n+1) blob reads as corrupt and forces mode 0 |
| 2026-09-22 | Recovery route before any ECU purchase; any VR6 `03H906032` as the bench mule; the software-matching spare only when a flash is imminent (M1/M1b/M1c) | `re/findings/hardware_prep.md` §1.4b |
| 2026-09-24 | **No write of any kind — Flash 0 included — before the #23 runtime RAM snapshots are in** | the rule is "snapshots first", not "patches only" (`docs/08` step 4 before step 5) |
| 2026-09-24 | Bench baselines are taken engine-off (KL15 on, no crank); the engine-running comparison is #28 on the car | a bench ECU has no crank or cam signal; the raster counters still run |
| 2026-09-24 | The E% store moves to block 8 payload +19 (G7); `ff_persist_enable` stays 1 in the shipped image; the bench step is a *read* of +19..+28 before the first `ff_fuel` flash | +2..+18 are the adaptation channels; persistence is part of the D2 fuel-path design, so the ships-disabled rule does not apply to it |
