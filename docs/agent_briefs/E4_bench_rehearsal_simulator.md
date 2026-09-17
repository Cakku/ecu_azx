# Brief E4 — Bench rehearsal in the simulator: EEPROM device model, live ff_fuel tick, frame sender (#37 #38 #39 rehearsal; #22 prep)

Every bench procedure written in waves C and D (`patches/ff_fuel/test/
procedure.md` §2-§5, `procedure_d2.md` A1-A4 and B2-B3, `patches/ff_counter/
test/procedure.md`) has been executed by nobody. This brief makes them
runnable against `logging/ecu_sim.py`, so the logger sessions, the
tolerances and the procedures are debugged before the ECU exists, and so
D2's "the device write cannot be emulated" limit is removed. Wave E pair 2,
parallel with E2. **You do not edit `patches/**` or
`logging/sessions/ff_fuel.json`**: discrepancies you find there go in the
report for the integrator. No `sudo`; nothing is flashed.

Read `00_common_rules.md`, `logging/README.md`, `logging/ecu_sim.py`,
`logging/med9log.py`, `logging/med9kwp/*`, `tests/test_med9kwp.py`,
`emu/README.md`, `emu/core.py`, `emu/memmap.py`, `re/findings/eeprom.md`
§1-§3, §7-§9, `re/findings/mpc5xx_registers.md` §7 (QSMCM),
`re/findings/kwp.md` §12.6 (emulator limits), `re/findings/can.md` §4,
`tests/test_ff_fuel_patch.py` (its TouCAN C MB6 model and `EmuBase`),
`tests/test_ff_diag_patch.py`, `patches/ff_fuel/README.md`, `test/*.md`,
`logging/sessions/*.json`, `tools/ethanol_frame_decode.py`,
`pico_can_sender/README.md`, `tools/logcmp.py`, `tools/eeprom_map.py`.

## Facts
- `Med9Emu` (`emu/core.py`) maps the whole address space, backs peripherals
  with zero pages plus `stub_read(addr, value_or_callable)`; there is no
  write-side device model yet (`_hook_write` only logs). Unicorn's time base
  never advances (kwp.md §12.6).
- The QSPI driver (eeprom.md §1.3): `FUN_00085920(q, n, rx)` copies `n`
  3-byte `{u16 data; u8 command}` entries to TXRAM (0x705180 + 2i) and
  CMDRAM (0x7051C0 + i), sets ENDQP in SPCR2, SPE in SPCR1 (|= 0x8000),
  spins on SPSR.SPIF, clears it, copies RXRAM (0x705140 + 2i) back. Command
  byte = CONT 0x80 | BITSE 0x40 | DT 0x20 | DSCK 0x10 | PCS levels; the
  EEPROM is an **M95160** (2 KB, 32-byte pages, WREN 06 / WRDI 04 / RDSR 05 /
  WRSR 01 / READ 03 / WRITE 02, WEL bit 1, WIP bit 0) on PCS0, SPI mode 0,
  8-bit transfers. Boot self-tests §1.6 spell out the queues.
- EEP_CONF block manager: `nvm_block_request` 0x06131C, queue pump
  `nvm_queue_pump` 0x060A68 / wrapper 0x061944 (called from both 10 ms
  tasks), `nvm_checksum_generate` 0x061A48, `nvm_read_all_blocks` 0x062280,
  `nvm_state_complete` 0x612B4; block 8 = EEPROM 0x1C0 + copy 0x1E0, 32 B,
  mirror 0x7F9F80; request record 9 B, status 1/2/0x80/0x82 (eeprom.md §3,
  §8). D2 observed the commit reach state 0x23 and then "state 0x53 walks
  into the OS halt spin at 0x110F0" — with no device answering. Find out
  whether a working device model takes it to status 2 instead.
- `ecu_sim.py` runs the real KWP handlers on one persistent `Med9Emu`,
  animates a few RAM cells (`AnimatedRam`), can load a patched image
  (`--sim-dump`) and answers `21 6F`. It does **not** run any raster task or
  hook, so `ff_state` never changes in the simulator today.
- The ff_fuel patched image: `python3 tools/patch_apply.py
  data/passat_azx_ori.bin patches/ff_fuel -o work/ff_fuel.bin` (needs the
  LLVM toolchain for `make`; the checked-in `patch.json` `changes` are
  complete, so `patch_apply` alone works without building). Hooks:
  `ff_fuel_hook_b` at 0x12067C (set B 10 ms), `ff_fuel_hook_a` at 0x432940
  (set A), `ff_fuel_rk_hook` at 0x42247C; symbols in `build.symbols` of
  `patch.json`. `tests/test_ff_fuel_patch.py::EmuBase` already models TouCAN
  C MB6 (IFLAG 0x7078A4 bit 6, control word 0x707960, payload 0x707966) and
  drives the hooks tick by tick.
- Frame (docs/05 §2, `data/ethanol_node.dbc`): id 0x0EC, b0 E%, b1 T+40,
  b2 f/2, b3 rolling counter, b6 fw version, b7 status 0/1/2/3.
- Simulated logs must carry a `simulated` metadata key and never enter the
  six-session RAM-snapshot comparison set (C3's rule).

## Tasks
1. **`emu/qspi_eeprom.py` — a QSMCM QSPI + M95160 device model.** A write
   hook on 0x705000-0x7051FF that, when SPE is set, walks the queue
   ENDQP+1 entries, drives a byte-level M95160 state machine per PCS0
   transfer (CONT keeps the chip selected across entries), fills RXRAM and
   sets SPIF; `WIP` may clear immediately or after N status polls (option).
   Backed by a 2 KB `bytearray` that can be loaded from / saved to a file
   (`--eeprom`). Add a minimal `Med9Emu.add_device(range, model)` (or
   `stub_write`) API in `emu/core.py` **additively** — every existing test
   stays green. Prove it with the firmware's own code: the boot loopback and
   WEL tests (eeprom.md §1.6) return success; `eeprom_read_bytes` /
   `eeprom_write_byte` (§2) round-trip; `nvm_read_all_blocks` fills the
   mirror from a synthetic EEPROM image with valid checksums
   (`tools/eeprom_map.py --check` validates the image you generate).
2. **Close D2's limit.** On the applied ff_fuel image, with the device model
   installed, run the D2 persistence sequence (`tests/test_ff_diag_patch.py`
   shows how): stage → commit → pump the queue from the real 10 ms hook
   until the request record's status leaves 1. Expected: **2**, both copies
   (0x1C0 and 0x1E0) hold the byte with a valid checksum, +14 and +29
   untouched. Then a **cold restart**: a fresh `Med9Emu` with that EEPROM
   image, `nvm_read_all_blocks`, then the patch's first activation → `e_filt`
   and `e_key` equal the stored value. If the driver still walks into the
   halt spin at 0x110F0, trace why (which device answer it waits for) and
   record it; eeprom.md §7 Q1 (which driver is bound at 0x7FAB70/0x7FAB74)
   may fall out of the trace. Write the result as eeprom.md §10.
3. **`logging/ecu_sim.py` learns to run the patch.** `--sim-patch
   patches/ff_fuel` (implies `--sim-dump` of a freshly applied image in a
   temp dir): a simulated clock that calls the 10 ms hook of the live task
   set (`--task-set A|B`, default A) 100 times per simulated second and the
   `rk` hook per simulated segment (derive segments from the animated rpm),
   with the TouCAN C MB6 model fed from the bus — frames with id 0x0EC
   arriving on the same python-can bus the simulator serves KWP on are
   copied into the MB6 model (move the model from the test into
   `emu/toucan.py` and import it from both). Also `--eeprom FILE` for the
   device model, so `21 6F` field 4 and the `eep_blk8_*` variables of
   `ff_fuel.json` are real. Keep `--self-test` and every existing option
   working. Time in the simulator is simulated: document how wall-clock
   and simulated time relate and make `med9log.py log --seconds` behave
   sensibly against it.
4. **`logging/ethanol_frame_send.py`** — the Mac-side stand-in for the Pico:
   sends the 0x0EC frame at 10 Hz on any python-can bus (`--bus virtual:med9`
   or `gs_usb:0`) with `--e-pct`, `--temp`, `--status`, `--fw`, a rolling
   counter, and the fault-injection knobs the #37 matrix needs: `--stall`
   (repeat the counter), `--stop-after S`, `--e-ramp A:B:S`, `--implausible`
   (E > 100), `--not-ready`. Reuse `tools/ethanol_frame_decode.py`'s layout
   (import, do not copy). Tests with a virtual bus.
5. **Dry-run the procedures.** With the simulator, the sender and the logger
   on one virtual bus, execute `procedure.md` §2 (state block), §3
   (reception), §5 (all six fault-matrix steps) and `procedure_d2.md` A1-A4
   and B2-B3 (the disconnect = stop the simulator, restart it with the same
   `--eeprom` file). Save the logs under `logging/samples/ff_fuel_sim_*.csv`
   with `# simulated: true`. Run `tools/logcmp.py` between a stock-image and
   a patched-image simulated run with `patches/ff_fuel/test/tolerance.json`:
   the tolerance file must pass on identical animation and fail when you
   perturb `rk` by 3 % — that validates the E0 comparison recipe. Every
   place a procedure, a session file or a formula turned out wrong goes into
   a numbered list in the report (you do not edit those files).
6. Tests: `tests/test_qspi_eeprom.py`, `tests/test_ethanol_frame_send.py`,
   `tests/test_ecu_sim_patch.py` (simulator + sender + logger on a virtual
   bus, one short end-to-end run asserting checks 1-5 of `ff_fuel.json`'s
   comment; a persistence round trip through the EEPROM file). Whole suite
   green; nothing beyond `requirements.txt`.
7. Docs: `logging/README.md` (a "bench rehearsal" section with the exact
   three commands), `emu/README.md` (device models), docs/03 §5 dated note,
   eeprom.md §10. Commit on `agent/E4` after every task; comment on #38, #39
   and #37 (what was rehearsed, what was found).

## Acceptance
`python3 logging/med9log.py log --sim --sim-patch patches/ff_fuel --eeprom
work/eeprom.bin ...` with `ethanol_frame_send.py` on the same virtual bus
produces a log in which checks 1-8 of `logging/sessions/ff_fuel.json` pass;
the persistence path reaches status 2 in the emulator and survives a
simulated disconnect; all six fault-matrix steps reproduce their expected
mode/F; the suite is green; the report lists every discrepancy found in the
procedures and session files.
