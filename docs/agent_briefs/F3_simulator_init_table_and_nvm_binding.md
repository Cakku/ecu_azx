# Brief F3 — Simulator fidelity: run the firmware's own init entries, and find who binds the NVM device driver (#20 simulator, #38 open item, #22 prep)

`logging/ecu_sim.py` is the stand-in for the ECU until one is on the bench.
Two waves of briefs have found its remaining infidelities by accident: E4
found the `ddli_init` entry array pointers unset (a define wrote into the
exception branch table at address 0), and the NVM device function pointers
0x7FAB70 / 0x7FAB74 unbound; E6 then classified all **1,028 entries of the
one-shot init table** (`re/findings/boot.md` §6) and listed exactly which
entries a KWP session needs and which the simulator still contradicts
(§6.5). This brief applies §6.5, and spends a time-boxed effort on the one
binding that no init entry performs. Wave F pair 2, parallel with F2.
**You do not edit `logging/bench_rehearsal.py`** (F2 owns it) nor anything
under `patches/`; discrepancies there go in the report. Desk work only.

Read `00_common_rules.md`, `logging/README.md`, `logging/ecu_sim.py`
(`Med9Handlers.power_on`, `PatchRunner`, `NvmDeviceBinding`),
`emu/README.md`, `emu/core.py`, `emu/qspi_eeprom.py`, `emu/toucan.py`,
`re/findings/boot.md` §2, §3 and **§6 (all of it, §6.5 is your task
list)**, `re/findings/eeprom.md` §1.5, §7, §8.4, §10.3, §10.4,
`re/findings/kwp.md` §3, §4.1, §12.6, `re/findings/flash_programming.md`
§5.3, `tests/test_ecu_sim_patch.py`, `tests/test_qspi_eeprom.py`,
`tests/test_med9kwp.py`, `tools/callgraph.py`, `tools/find_abs_refs.py`,
`tools/sda_xref.py`, `tools/blobdis.py`.

## Facts (all VERIFIED-STATIC in `boot.md` §6 unless stated)
- The init table is a flat NULL-terminated array of 1,028 function pointers
  at 0x0B1A68, walked once at start-up; `ddli_init` (0x12E39C) is one entry,
  which is why E4 had to call it by hand.
- `power_on` today hand-seeds `kwp_session_current`, `kwp_security_state`,
  `kwp_sec_seed`, `kwp_sec_level_flags` = 3, `kwp_sec_lfsr_rounds` = 5 and
  the retry flag, then calls `ddli_init`. §6.5 says that is a post-`27 01`
  state, not a post-power-on one: **`kwp_sec_init` 0x036AB8 (index 71)**
  writes 0x7FB781 = 0, 0x7FB780 = 0, 0x7FB770 = 0 and loads the
  SecurityAccess lockout timer 0x7FB748 from the EEPROM mirror halfword
  0x7FA02C; the round count 5 is written by the seed path itself at
  0x03635C inside `kwp_sid_27_h1`.
- **`nvm_mode` 0x7FCD68 is 1 after start-up**, not 0: index 18
  (`nvm_set_sync_mode`, writes 2) then index 24 (`nvm_set_normal_mode`,
  writes 1) both run once; nothing else calls either.
- `flash_crc_init` 0x12E2D8 (index 75) clears 0x7FB6F4 and 0x801200; the
  runtime CRC-32 task 0x011CB10 publishes to 0x7F9178 and compares with
  nothing (`flash_programming.md` §5.3a).
- Entries a cold KWP stack needs: index 38 (0x12F138: four RAM buffer
  pointers at 0x8037E4/E8/EC, 0x8038D4), 83 and 84 (0x12F638, 0x12F664:
  eight pointers at 0x7FB074-0x7FB0A8, seeds 0x802CF8/0x802CFA = 0x444),
  86 and 89 (0x12FDA4, 0x134E5C: self-referential list heads via 0x4104C4).
- **None of the 1,028 entries writes 0x7FAB70 / 0x7FAB74.** The block
  manager calls the device only through those two BSS pointers (call sites
  0x05FD88 read, 0x06068C write; signature `rc = (*fp)(eepAddr, len, buf,
  statusPtr)`, non-zero rc = started, driver writes 1 to `*statusPtr` on
  success, `eeprom.md` §10.3). `sda_xref`/`find_abs_refs` find no store.
  E4's `NvmDeviceBinding` installs two trampolines onto the real primitives
  0x085BC0 / 0x085B54 so the manager runs; which routine the factory
  software binds there is `eeprom.md` §7 Q1, open.
- Two per-block device sub-states 0x7FADAC / 0x7FADAD are reset only after a
  block finishes; `emu.qspi_eeprom.cold_start()` seeds them (§10.4).

## Tasks
1. **Replace the hand-seeding with the firmware's own entries**, the way
   §6.5 recommends (not the whole table): call indices 71, 72, 38, 83, 84
   (and 86, 89 if a session touches those lists), then 18 and 24 so
   `nvm_mode` = 1, then `ddli_init`; keep any remaining hand-seeded cell
   listed in a table in the module docstring with the reason. Then prove
   the real `27 01` arms the LFSR (round count 5 appears at 0x7FB770 only
   after the seed request) and that the level-2 path C3 verified still
   works end to end. With the M95160 model attached, the lockout timer must
   come from the modelled EEPROM mirror; document what a factory-shaped
   image holds at 0x7FA02C.
2. **Run `flash_crc_task` in the simulated background** (time-boxed ≤ 2 h):
   one activation per simulated tick set, and expose 0x7F9178 so a logging
   session can watch the CRC low halfword move. If the task needs
   peripherals the emulator lacks, record what and stop.
3. **Who binds 0x7FAB70 / 0x7FAB74** (time-boxed ≤ 4 h, negative result
   acceptable). Approaches in order: (a) every store whose effective address
   is computed from a base register loaded from a *pointer word* or a
   struct base — scan for `stw` into 0x7FAB00-0x7FABFF through any register
   with the value tracked back through `lis/addi/lwz`; (b) block copies:
   loops that copy a flash table into RAM covering 0x7FAB70 (the boot-level
   SPI device table of `eeprom.md` §1.5 and the descriptor block
   `flash_programming.md` §6.1 are the first candidates); (c) candidate
   functions by signature — callers of 0x085A8C / 0x085B54 / 0x085BC0 that
   take (addr, len, buf, statusPtr) and return non-zero, listed with
   `callgraph.py`; (d) the RAM bootstrap loader and boot module
   (0x000000-0x02FFFF) as writers. Result: the binding VERIFIED-STATIC, or
   the excluded set written down in `eeprom.md` §10 with the commands. If
   found, bind the real driver in the simulator (keep the M95160 model
   underneath) and retire the trampolines.
4. **Fidelity regression tests** in `tests/test_ecu_sim_patch.py` (or a new
   file): the post-power-on state matches §6.5 cell by cell; `27 01`/`27 02`
   from a cold simulator; five dynamic ids still read back byte for byte
   (E4's test); `ecu_sim.py --self-test` PASS; `med9log.py probe/groups/log
   --sim` unchanged from a user's point of view. Run
   `logging/bench_rehearsal.py --fresh-eeprom` at the end and report its
   score — do not edit it; if a check fails because of your change, say
   which and why.
5. **Docs**: mark `boot.md` §6.5 items SETTLED in place with the date and
   your section; dated notes in `kwp.md` §12.6 and `eeprom.md` §7 / §10;
   `logging/README.md` §9 one line; `re/symbols.csv` rows for anything
   newly named. Comment on #20 and #38; commit after every finding.

## Acceptance
The simulator's power-on state is produced by the firmware's own init
entries except for a documented residue; `27 01` arms the LFSR through the
real seed handler; `nvm_mode` reads 1; the NVM binding question has either a
VERIFIED-STATIC answer or a recorded exclusion set; the rehearsal score is
reported; suite green; dump untouched.
