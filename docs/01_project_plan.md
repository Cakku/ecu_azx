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

## 2. Where we are (2026-09-15)

- Full KESSv2 read of the car's ECU exists (`data/passat_azx_ori.bin`).
- The dump structure, CPU address map, small-data registers, checksum
  algorithm and the CAN/KWP tables are **verified** (`docs/02_memory_map.md`)
  and encoded in `tools/`. `tools/checksum.py` reproduces all 65 checksums.
- Earlier notes and Ghidra output (`med9_re/`) were made with a wrong memory
  map; treat as unverified.
- Hardware on hand: Pico 2 + MCP2515 CAN sender (sends a pot value as
  ID 0x123), Pi Zero CAN sniffer setup, KESSv2. No spare ECU, no BDM tool, no
  Windows machine documented.
- Nothing has been written to the car yet.

> **Update 2026-09-16.** Waves A and B of agent work are merged (`main`
> 647efe6): Phase 1 is closed (#7-#19), the Unicorn harness and regression
> tools exist (#21, #24), and every flex-fuel insertion point is located
> statically (`docs/05_flexfuel_design.md` dated notes). Hardware items
> (#1-#4, #22, #26-#28, #30-#31, #33, #45) are still pending. The next desk
> work is wave C/D in `docs/agent_briefs/README.md`: patch framework and
> Flash-1 source (#25/#27), static RAM survey (#23), KWP logger tested
> against an emulated ECU (#20), the task-period question (#44), then the
> ff_fuel MVP patch (#32/#37), diagnostics and persistence (#38/#39) and the
> calibration definition (#41).

> **Update 2026-09-17.** Waves C and D are merged (`main` 8c93421, 365
> tests). The desk side of Phases 2-4 is done as far as it can be without an
> ECU: patch framework and Flash-1 counter (#25 closed, #27 software), the
> static RAM survey and the patch RAM block 0x7FFB00 (#23), the KWP logger
> proven against an emulated ECU (#20), the ERCOSEK rasters (10x faster than
> assumed, #44), the **flex-fuel MVP patch `patches/ff_fuel`** with its E0
> bit-identity proven in the emulator (#32, rules of #37), VCDS measuring
> block 111 and E% persistence in EEPROM block 8 (#39, #38), and the first
> scaled calibration definition (#41, 157 named objects). Nothing is
> flashable yet: the RAM block is VERIFIED-STATIC only, and two of the three
> hook words are in the on-chip flash, which KESSv2 has not been shown to
> write. The next desk work is **wave E** (`docs/agent_briefs/README.md`):
> ignition, start and rail-pressure code as disabled, enable-gated extensions
> of the same patch (#34-#36), a second naming pass (#41), a simulator that
> runs the patch and an EEPROM device model so every bench procedure is
> rehearsed (#37-#39), the firmware's own flash-programming route read out of
> the dump to settle the on-chip question (#26-#28, #32), and the workflow
> walkthrough (#42). Hardware items unchanged: #1-#4, #22, #26-#28, #30-#31,
> #33, #40, #45.

## 3. Principles

1. **Evidence before action.** A fact is used only at the level it has been
   verified to (`docs/README.md` legend). Old notes are hypotheses.
2. **Bench before car, spare before own.** Every flash goes to a spare ECU on
   the bench first. The car's ECU is written only with files that already ran
   on the bench.
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

| Task | Deliverable | Exit criterion |
|---|---|---|
| Acquire a spare 03H906032 ECU (same or close software; the calibration must match ours, so check the part suffix and `1037382557`) | bench ECU | on the desk |
| Get a full **K-TAG BDM (MPC5xx, protocol 64)** read of the car's ECU: external flash, on-chip flash incl. 0x400000-0x403FFF, EEPROM | `data/backup_bdm/…` + SHA-256 in a manifest | files verified; `checksum.py verify` OK; on-chip region compared with the KESS tail |
| Bench harness: 12 V supply with current limit, ignition-switched line, CAN transceiver, OBD-style connector, Pico frame injection, PC CAN adapter | wiring doc + photo | ECU boots and answers a KWP TesterPresent on the bench |
| Windows machine for K-Suite (Intel laptop recommended; Windows-on-ARM VMs are risky for the USB drivers) | working KESS/K-TAG install | reads the spare ECU |
| Dev environment on the Mac (`docs/03_tooling.md`): Ghidra, Python venv, PowerPC GCC | documented setup | a hello-patch compiles and disassembles correctly |
| Download the MED9.1 Funktionsrahmen PDF (see tooling doc) into `documents/` (gitignored if large) | reference | |

### Phase 1: Static reverse engineering foundation

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

| Task | Deliverable | Exit criterion |
|---|---|---|
| Live RAM logger over TP2.0/KWP2000 (0x2C + 0x21, RequestUpload) from Mac or Pi (EliasTuning/MED9RamReader or pq-flasher derived) | `logging/` scripts | logs nmot/rl/ti/lambda on the bench ECU and in the car via OBD |
| Emulation harness for pure functions (Ghidra EmulatorHelper or Unicorn) with the verified map (DECRAM, ISB=1 peripherals, CS2 stub) | `emu/` | reproduces a map lookup result found in a log |
| Bench replay: Pico sends the ethanol frame; SavvyCAN/python-can records what the ECU transmits | procedure | RX slot activity visible in RAM via the logger |
| Regression check tooling: binary diff limited to intended bytes, checksum verify | `tools/` | part of every build |

### Phase 3: Patch pipeline and first flash

| Task | Deliverable | Exit criterion |
|---|---|---|
| Toolchain, linker script, hook helpers (`docs/06_patch_pipeline.md`) | `patches/` framework | hello-patch builds to a raw blob at a chosen address |
| **Flash 0: unmodified file** re-saved through our tools | bench ECU runs it | proves the flash route, checksum acceptance, no hidden signature |
| **Flash 1: no-op patch**: one free-RAM counter incremented from a periodic task, readable via the logger | bench ECU runs it | counter increments; stock behaviour otherwise (compare logs) |
| Repeat Flash 0/1 on the car's ECU with the BDM backup in hand | car runs normally | no DTCs, adaptation values unchanged |

### Phase 4: Flex-fuel MVP (fuel only)

| Task | Deliverable | Exit criterion |
|---|---|---|
| Pico firmware: frequency/pulse-width capture, plausibility, status byte, 10 Hz frame in the agreed format (`docs/05_flexfuel_design.md`) | firmware | bench-tested with a signal generator across 40-190 Hz |
| Sensor installed in the feed line, wiring, bus connection decided (powertrain CAN behind the gateway) | install notes | frame visible on the bus, no collisions |
| ECU patch: receive, validate, filter, fuel factor from a 1D curve, applied at the verified multiplication point; factor exactly 1.0 at E0 | patched file | E0 equivalence test passes on bench and car (identical logs to stock) |
| Staged blends E20 -> E50 -> E85 with wideband, watching lambda, fra/frau adaptation, ti vs window, rail pressure, knock | log set | adaptation stays near 1.0 at every blend; no lean excursions at WOT |

### Phase 5: Complete flex-fuel

Ignition blend curve and E-map, start/afterstart enrichment vs E% and
coolant temperature, rail pressure setpoint raise at high load, injection
window monitoring with torque limitation as the safety net, fail-safe rules,
persistence of E% across power loss (EEPROM or non-volatile RAM), diagnostics
(E%, fuel temperature, status and factor exposed in a measuring block; OBD
PID 0x52 optional), long-term evaluation over a winter.

### Phase 6: Tuning platform

Publish the calibration definition for `1037382557`, document the
logging/flash workflow, and support other hardware changes on the same
pipeline.

## 5. Milestones

| # | Milestone | Proves |
|---|---|---|
| M1 | BDM backup + spare ECU on bench answering KWP | we can always go back |
| M2 | Ghidra project with correct map; injection multiplication point named | the RE foundation |
| M3 | Live logger reads nmot/rl/ti in the car | symbol map is right |
| M4 | Flash 0 and Flash 1 run on bench and car | the write path is safe |
| M5 | E0 equivalence of the flex patch | patch is inert when it should be |
| M6 | E85 with adaptation near 1.0 and no knock retard increase | MVP works |
| M7 | Cold start at ambient < 5 C on E85 | full feature |

## 6. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Bricked ECU (interrupted OBD write, wrong file) | BDM backup first; spare ECU; K-TAG BDM as recovery; battery charger during writes |
| The 16 KB not in the KESS read hides something we depend on | BDM read and diff; do not write files that touch 0x400000-0x47FFFF until the region is understood |
| Undocumented signature beyond the block sums | Flash 0 test on the bench spare before any real change |
| KESSv2 is end-of-life; protocol 179 support may lapse | keep the current K-Suite install working; consider KESS3/K-TAG |
| Immobiliser IV / component protection on a swapped bench ECU | the bench ECU does not need to start an engine; for KWP logging immo state is irrelevant; never mix EEPROM/flash between ECUs |
| HPFP and injector window limits on E85 | log ti vs rpm and rail actual vs setpoint before WOT on high blends; add torque limiting when the window is exceeded |
| Fuel system material compatibility | inspect lines/seals; change fuel filter after first tanks; watch LPFP pressure |
| Sensor placement errors (air bubbles in return line) | feed-line placement; plausibility on rate of change |
| CAN ID collision or gateway filtering | bus survey with `pi_can_setup/find_active_ids.py`; frame goes on the powertrain CAN directly |
| RAM scarcity (32 KB + 32 KB, heavily used) | RAM usage survey (static refs + runtime RequestUpload dumps) before allocating |
| Timing budget of the hooked task | keep the patch integer-only, few hundred instructions, measured on the bench |
| Wrong r2 context when naming functions | decide r2 per function from the call graph before trusting decompiled constant loads |

## 7. Decisions taken

| Date | Decision | Why |
|---|---|---|
| 2026-09-15 | Canonical addresses are CPU addresses; external flash at 0x0, calibration referenced at 0x5Cxxxx, on-chip flash at 0x404000. File offsets are always prefixed "file". | matches the firmware's own use; avoids the 0x400000 confusion of the 2 MB-ECU tools |
| 2026-09-15 | Checksums are corrected with `tools/checksum.py`, not disabled | algorithm is verified; keeps corruption protection |
| 2026-09-15 | Ghidra with the stock `PowerPC:BE:32:default` language plus a setup script, not the `ppc_med9` extension | extension encodes the wrong peripheral map; blocks/registers are better set by script |
| 2026-09-15 | Ethanol frame follows the Zeitronix ECA-2 CAN layout (E% byte, temperature byte, status byte) | off-the-shelf analysers become drop-in replacements for the Pico |
| 2026-09-15 | Patch code is C compiled with a PowerPC EABI GCC, integer-only, no small-data sections, r2/r13 reserved | see `06_patch_pipeline.md` |
