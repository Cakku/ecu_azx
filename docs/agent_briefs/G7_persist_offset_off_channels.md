# Brief G7 — Move the E% store off the adaptation-channel bytes of EEP_CONF block 8 (#38; follow-up found during wave G)

Brief G2 noticed, and the integrator confirmed from the dump, that the byte the
flex-fuel patch uses to persist the ethanol percent — **EEP_CONF block 8,
payload +2** (`ff_persist_offset` = 2 since E4's correction) — is **adaptation
channel 1's slot**. F4 had already recorded the loop that proves it
(`adaptation_restore_all` 0x12E3F8, `re/symbols.csv`); nobody had put the two
facts side by side. The collision is benign for the engine on this dataset
(channel 1 is clamped to 0/0, see the facts), but it leaves the stored E%
exposed to a workshop **channel-0 reset**, and it makes D2/E4's "free bytes"
table wrong. This brief moves the store to a byte that really is free and
proves it in the emulator. It is the **only** brief after G1 allowed in
`patches/ff_fuel/**`; it runs after G2 and G5 are merged (G2 owns docs/05 §3.8
until then; G5 makes `bench_rehearsal.py` read the offset from FFCAL001 instead
of hard-coding 2). Desk work only; nothing is flashed.

Read `00_common_rules.md` (the 2026-09-17 `patches/ff_fuel` rules), then
`re/findings/eeprom.md` §3-§5, §8-§10 **in full**, `re/findings/calibration_names.md`
§10.1 (the adaptation-channel service: 0x82 writes a channel, 0x83 commits
block 8), `docs/05_flexfuel_design.md` §3.8 (G2's hazard note of 2026-09-23),
`patches/ff_fuel/README.md` (persistence section, FFCAL001 table row +1E),
`patches/ff_fuel/ffcal001.py`, `src/ff_diag.c` (the persistence stage/commit),
`emu/qspi_eeprom.py`, `emu/models/flexfuel.py`, `tests/test_ff_diag_patch.py`
(E4/E5's end-to-end #38 tests and the offset regression test),
`logging/sessions/ff_fuel.json`.

## Facts (VERIFIED-STATIC unless stated)
- `adaptation_restore_all` 0x12E3F8 runs `nvm_block_request(0x08, n + 0x02, 1, 1,
  PTR[0x0A3ADC + 4n], 0)` for n = 0..0x10 at every power-up (F4, symbols row;
  descriptor bytes at 0x0A3AD8 = `08 02 01 00`; `PTR[0]` = 0x7FD06B). So block 8
  payload **+2..+18 are adaptation channels 1..17**; the "one stock client at
  +14" of `eeprom.md` §4 is channel 13's slot. E4's block-8 default record
  `08 01 | 00 80 80 80 80 00 00 80 00 80 80 FF …` lists the channel defaults
  from +2 on (ch 12 default 255 at +13 matches F4's table).
- The KWP adaptation service **commits block 8** (sub-function 0x83, F4 §10.1),
  so `eeprom.md` §9's "no commit site for block 8" holds only for constant-offset
  call sites; the tester path commits the whole mirror, including the patch's
  staged byte.
- **Channel 1 is inert on this dataset** (integrator, 2026-09-24,
  `tools/blobdis.py` over 0x46B040-0x46B1F0 and 0x410ACC): the only reader,
  0x46B0BC in the function at 0x46B09C, passes the s8 at 0x7FD06B as the *value*
  argument of `clamp(value, lo, hi)` at 0x410ACC with `lo` = s8 0x5C608F = 0 and
  `hi` = s8 0x5C608E = 0 (`xxd` at file 0x1C608E → `00 00`), so the result is 0
  whatever the byte holds. A restored E% therefore changes nothing in the engine
  control. The remaining exposures are (a) a tester **channel-0 reset (0x82) +
  commit (0x83)** writes the defaults over +2..+18 and so **zeroes the stored E%**
  → the patch reads E0 at the next power-up; (b) a tester write to channel 1 is
  clamped to 0 with the same effect; (c) a tester reading channel 1 sees the E%.
- Block 8 is a 0x20-byte record with `flags & 3 = 1`: the manager owns payload
  **+29**, the checksum is +30..+31 (`eeprom.md` §3.4, §5 note 1). With +2..+18
  taken, the only candidates are **+19..+28** (10 bytes). D2's constant-offset
  scan found no client there; the run-time-indexed clients (F4's loop and the
  0x82/0x83 service) stop at channel 17 = +18. Both statements must be
  re-checked by this brief before the byte is used.
- `ff_persist_offset` is FFCAL001 u8 at +0x1E, default 2 (D2 shipped 0; E4
  moved it to 2). Changing a **default value** is not a layout change: no
  VERSION bump, nothing moves; but `ffcal001.bin` and therefore the patched
  image change, so every test or doc that quotes the image SHA-256 follows.
  `ff_persist_buf` (0x7FFB50) is the stage byte; the record's checksum is the
  manager's.

## Tasks
1. **Prove +19 free** (or the lowest free byte if not): (a) the flash default
   record for block 8 (`eeprom.md` §3.3 table, §10.5 method) at +19..+28; (b) no
   constant-offset client (`eeprom.md` §4 method, re-run); (c) the 0x82/0x83
   service writes only +(k+1) for k ≤ 17 (read the write path F4 named in
   §10.1); (d) `nvm_read_all_blocks` validates only the +0/+1 stamp, not the
   payload (E4 §10.5). Record the exclusion set with commands. If +19..+28 turn
   out not to be free, stop after this task with the evidence and recommend
   the fallback of `eeprom.md` §5 (block 24 +3) — do not force it.
2. **Move the default**: `ff_persist_offset` 2 → 19 in `ffcal001.py`; regenerate
   `ffcal001.json` / `ffcal001_rows.csv`; update `emu/models/flexfuel.py` if it
   mirrors the offset, `logging/sessions/ff_fuel.json` notes, the patch README
   (FFCAL001 row +1E, the persistence section, the image SHA where quoted) and
   every test that asserts the offset or the image SHA — in the **same commit**.
   No VERSION bump; say so in the README with the reasoning above.
3. **Emulator proofs** in `tests/test_ff_diag_patch.py` (extend, do not weaken):
   (a) the E4 end-to-end #38 path (cold start → stage → commit → power cycle →
   read-back through `nvm_read_all_blocks`) passes at +19; (b) run the real
   `adaptation_restore_all` 0x12E3F8 under Unicorn on a block-8 image carrying
   E% at +19: no channel byte 0x7FD062-0x7FD06D changes and 0x7FD06B stays 0;
   (c) the regression that documents *why* the default moved: emulate a channel-0
   reset (defaults written over +2..+18, then commit) — at offset 19 the E% byte
   survives, at offset 2 it is zeroed. Whole-SRAM diff as the other features.
4. **Docs, dated corrections in place**: `eeprom.md` §4 (block 8 row), §5 (table
   row and Recommendation: +2 → +19), §9 (0x83 commits block 8), §10.5 (the
   free-offsets sentence); `docs/05` §3.8 — mark G2's hazard **SETTLED (date,
   G7, section)** with the move and the clamp fact; `patches/ff_fuel/README.md`;
   `re/symbols.csv` rows for the channel-1 reader function 0x46B09C and the
   clamp 0x410ACC if absent. If `logging/bench_rehearsal.py` still hard-codes
   `+2` when you start (G5 was asked to read it from FFCAL001), change that one
   constant and say so — nothing else in `logging/`.
5. `bench_rehearsal.py --fresh-eeprom` score unchanged; suite green; comment on
   #38; commit after every finding.

## Acceptance
The E% store sits at a block-8 payload byte outside +2..+18 whose freedom is
proven with an exclusion set; the #38 end-to-end path, the restore-loop
non-interference and the channel-0-reset survival are shown in the emulator;
`eeprom.md` §4/§5/§9/§10.5 and `docs/05` §3.8 are corrected in place with dates;
FFCAL001 keeps v5 (default change only) and every SHA/offset assertion follows;
suite green; dump untouched. The integrator regenerates the XDF (the FFCAL001
rows carry the new default).
