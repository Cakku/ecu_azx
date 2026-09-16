# Brief D2 — Diagnostics in a measuring block and E% persistence, desk half (#39, #38)

Issues **#39** (VCDS-visible values) and **#38** (E% across power loss).
Wave D, after **D1 is merged** (shares its state block and FFCAL001). No
`sudo`; nothing flashed; the VCDS and battery-disconnect checks stay open.

Read `00_common_rules.md`, `docs/05_flexfuel_design.md` §3.7-§3.8,
`re/findings/kwp.md` §4.2 and §6, `re/findings/measuring_vars.md`,
`re/measuring_vars.csv`, `re/findings/eeprom.md` §3-§7, `tools/eeprom_map.py`,
`patches/ff_fuel/` (D1), `re/findings/start.md` §5-§6 (engine-state flags).

## Facts
- Measuring variables: `tbl_measuring_vars` (TKMWL) at **0xA5658**, 2200 x
  u32 handler pointers; 1535 ids point at the stub 0x38EC4 (formula 0x25,
  "not available") — the spare capacity. Dispatcher
  `measuring_var_dispatch` 0x45768; a handler emits `(formula, A, B)` through
  `measuring_result_emit` 0x38EB4 (formula -> 0x7FD06F, B -> 0x7FD070,
  A -> 0x7FD071). Group table `tbl_measuring_groups` at **0x5C5518**:
  `entry(field, group) = 0x5C5518 + field*0x1FE + group*2`, u16 variable id,
  4 fields x 255 groups; `21 <group>` returns the group. Both tables sit in
  checksummed blocks (0xA5658 in 0x0A0000-0x0AFFFF, 0x5C5518 in the
  calibration block) -> `checksum.py fix`.
- The VAG formula arithmetic is not decoded in this repo; A3 pinned two
  anchors: id 1 formula 0x01 A=0xC8 -> 40 rpm/LSB, id 2 formula 0x21
  A=0x85 -> %. Take the formula table from an open source (cite; COMMUNITY)
  and use only formulas you can check against those anchors or in the
  emulator.
- Persistence route (eeprom.md §5): EEP_CONF **block 8** (EEPROM 0x1C0 +
  copy 0x1E0, 32 B, one page, mirror RAM 0x7F9F80, one stock client at
  payload +14; +29 belongs to the manager, +30..31 checksum). Stage
  `nvm_block_request(8, 0, 1, 0, &byte, 0)` (0x06131C, returns 2), commit
  `nvm_block_request(8, 0, 0, 0, 0, &handle)` (returns 1, poll the handle),
  read back `nvm_block_request(8, 0, 1, 0, &dst, 0)` or RAM 0x7F9F80 after
  `nvm_read_all_blocks` (0x062280). **Never** use the raw SPI primitives.
  The factory content of payload +0 is unverified (bench EEPROM read).
  Fallback: block 24 (255 B, single copy). External SRAM retention across
  key cycles is HYPOTHESIS (KL30 unknown).
  > **Correction 2026-09-16 (C2, `re/findings/ram.md` §3, VERIFIED-STATIC):**
  > external-SRAM retention is **refuted**. `ram_clear_block` (0x06D8F8) and
  > `app_init` (0x04CCD4) zero 0x800004-0x80498F at every cold start, and
  > 0x804990-0x807FFF is the flash driver's programming copy. EEPROM block 8 is
  > the only persistence route; drop the battery-backed-RAM branch of #38.

## Tasks
1. **Measuring block (#39).** Choose four spare ids that no group references
   (scan 0x5C5518) and one unused group number (all four fields 0 or stubs);
   write handlers in `patches/ff_fuel/src/ff_diag.c` for `E_filt` (%),
   `T_fuel` (C), mode (enum) and F (x1000 or %). `patch.json` changes: the
   four TKMWL pointers (old = `00 03 8E C4`) and the four group-table words;
   confirm with `find_abs_refs.py` / `find_branch_refs.py` that nothing else
   reads those slots. Emulator test: `measuring_var_dispatch` (0x45768) on
   the applied image for each id -> the expected triple; `21 <group>` through
   `kwp_sid_21_h1` (0x35F6C) if the emulated path is tractable (B3's tools
   show the seeding), else document why not.
2. **Persistence (#38).** In D1's periodic task: restore `E_filt` from the
   store at init (before the first frame; also the FAULT decay target);
   write-on-change with hysteresis (`ff_persist_hyst`, default 5 %) and a
   rate limit (`ff_persist_min_s`, default 60 s). Investigate where stock
   code commits its blocks at key-off (callers of 0x06131C with the commit
   signature reachable from the shutdown path, "engine not running"
   0x7FEAD0) and record it; hook it only if the piggyback is trivial.
   Add `ff_persist_enable` (default 1) and block/offset parameters in
   FFCAL001. Emulator test: stage + commit through the real
   `nvm_block_request` with the QSPI (0x705000) stubbed — the mirror
   0x7F9F80 byte 0 receives the value, +29..+31 are touched only by the
   manager, the handle sequence matches eeprom.md §3; a second call inside
   the rate limit does nothing.
3. Reflect the formats in `logging/sessions/ff_fuel.json`; write the bench
   procedures for #39 (VCDS group number, expected fields) and #38 (the
   disconnect test) in `patches/ff_fuel/test/`.
4. Dated notes in docs/05 §3.7 / §3.8; `re/measuring_vars.csv` (the four new
   ids, tag static, source = patch); `re/symbols.csv`. Commit on `agent/D2`;
   comment on #39 and #38.

## Acceptance
On the applied image the emulator returns our values for the four ids and
the chosen group; the persistence path uses stock code only; the remaining
bench checks are written step by step.
