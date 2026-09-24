# Brief H2 — Who resets the adaptation channels: the block 11 payload +11 flag, the fault-clear path, and what a reflash does to the fuel trims (#47; #26/#28/#38 desk half)

Brief G7 found a **stock, tester-free path that resets all 17 KWP adaptation
channels and rewrites EEP_CONF block 8**: `fault_clear_then_adaptation_reset`
0x0D1068 runs the fault-clear state machine (0x035300) and then
`adaptation_reset_all_commit` 0x038D64 when **0x7FEB59 ≠ 0 and bit 0 of block 11
payload +11 (mirror 0x7FA02B)** is set, then clears the bit and stages block 11
(`re/findings/eeprom.md` §5 note of 2026-09-24, "tangent, not pursued";
`re/symbols.csv` rows 0x038D64, 0x0D1068). On this dataset the tester cannot
reset the channels itself (access words 0x5CF004/08/0C = 0x40 admit channel 7
only), so this path is the one that matters for the three fuel trims (channels
4/8/10, ±10 %, `docs/05` §3.3) and for the E% store (#38, now at +19 and proven
to survive the reset). **Who sets that bit is open.** G7's candidate is the raw
EEPROM write at **0x087844 in the KWP programming module** — which would mean the
first boot after an OBD reflash resets the channels. That decides what Flash 0/1
(#26, #27) must record and how #28's exit "adaptation values unchanged" is checked.
Wave H pair 2, parallel with H5. Static RE plus one emulator run; desk only.

Read `00_common_rules.md`, `re/findings/eeprom.md` §3-§5 (**§5's c1-c5 table and
the 2026-09-24 notes in full**), §7 item 4 (G5's DTC/block-24 note), §9, §10,
`re/findings/flash_programming.md` (E6: the programming module 0x080000-0x09FFFF
relocated to 0x804800, `10 85`, SID 0x34/0x36/0x37, routine 0xC5 at 0x087494,
§5.3, §7.2), `re/findings/ram_loader.md` §3 (F5, only for the address filter),
`re/findings/kwp.md` §12.7 (G5: the `14` handler, 0x7FEB65 "engine running"
HYPOTHESIS), `re/findings/boot.md` §6.4 (init entries 34/39 `dfp_init`),
`tests/test_ff_diag_patch.py` (`TestPersistOffsetOffTheChannels` — how G7 ran
0x038D64 under Unicorn against `emu/qspi_eeprom.py`), `logging/ecu_sim.py`
(`INIT_ENTRIES`, the QSPI device, G5's `DtcStore` — read only),
`tools/eeprom_map.py --clients`, `tools/store_xref.py --window`, `tools/sda_xref.py`,
`tools/find_abs_refs.py`, `tools/blobdis.py`, `tools/callgraph.py`,
`ghidra_scripts/decompile.py`.

## Facts (VERIFIED-STATIC per G7/G5 unless stated)
- 0x038D64: for n < 0x11, channel bit n+1 set and n ≠ 6: RAM channel := default and
  `nvm_block_request(8, n+2, 1, 0, default…)`, then the commit `(8, 2, 0x11, 8, …)`
  writes the whole record. Called only from 0x0D10D0.
- 0x0D1068: gated on 0x7FEB59 ≠ 0 **and** bit 0 of 0x7FA02B; runs 0x035300 (the
  `14` fault-clear machine, which commits block 24 after zeroing a 251-byte
  buffer), then 0x038D64, clears the bit, stages block 11 +11 (0x0D1110).
- Block 11 (EEPROM 0x280, copy 0x2A0) is the block the immobiliser writes behind
  the manager's back (`eeprom.md` §3.5/§5) — so the flag lives next to
  immobiliser/pairing state; payload +11 ↔ EEPROM 0x28B.
- Candidate setter: a raw write to EEPROM 0x28B at 0x087844 (programming module).
  Value and trigger unknown (HYPOTHESIS).
- Init entries 34 and 39 (`dfp_init`) must have run for `14` to answer (G5).

## Tasks
1. **Writers of the flag and the gate.** Every writer of 0x7FA02B (direct stores:
   `store_xref.py --window 0x7FA020 0x7FA040`; through the manager:
   `nvm_block_request(11, 11, …)` sites via `eeprom_map.py --clients` and the
   run-time-indexed sites of §5; raw SPI writes to EEPROM 0x28B/0x2AB, incl.
   0x087844) and of 0x7FEB59. For each: the function, the value written, the
   condition. Tag VERIFIED-STATIC or record the exclusion set.
2. **The reflash question.** Read 0x087844's routine in the programming module
   (which SID / routine 0xC5 step / erase-program state writes it, with what
   value) and decide: does the OBD programming route (`10 85` → 0x34/0x36/0x37 →
   reboot) leave bit 0 set, so that the first application boot after Flash 0/1
   runs 0x0D1068? Same question for the RAM bootstrap loader (F5) and for the
   immobiliser pairing path if it is the writer. State the consequence for the
   fuel trims and for #28's exit criterion.
3. **The fault-clear side.** What 0x7FEB59 means (compare G5's 0x7FEB65
   hypothesis), what else 0x0D1068 does before 0x038D64, and whether a plain
   tester `14 FF 00` (no flag) can reach 0x038D64 — i.e. does a workshop DTC
   clear reset the fuel trims on this dataset? Tag.
4. **Emulator run** (new test file `tests/test_adaptation_reset_path.py`, the
   G7 pattern): seed the QSPI device with block 11 +11 bit 0 set, a non-zero
   0x7FEB59, a seeded DTC (G5's store) and E% at block 8 +19; run 0x0D1068 under
   Unicorn to completion; assert: fault memory cleared, block 24 committed,
   channels 1-17 at their defaults, block 8 record rewritten with the E% byte
   intact, bit 0 cleared and block 11 staged. Whole-SRAM diff outside those cells
   empty. Label modelled parts.
5. **Docs, sessions, bookkeeping.** `eeprom.md` new dated section (§11: "The
   adaptation-reset path") + the §5 tangent marked SETTLED/BOUNDED in place;
   `flash_programming.md` §7.2 dated note (what to read before/after a flash);
   `docs/08_bench_playbook.md` steps 5-6: **one** dated line each pointing at the
   new session; `kwp.md` §12.7 note if 0x7FEB59/0x7FEB65 are settled; new
   `logging/sessions/adaptation_channels.json` (DDLI of 0x7FD062-0x7FD06D, the
   block 8 mirror 0x7F9F80-0x7F9F9F, 0x7FA02B, 0x7FEB59; note says "run before and
   after every flash and after every DTC clear"); `re/symbols.csv` rows. Comment
   on #47, #26 and #38; commit after every finding.

## Ownership
`re/findings/eeprom.md`, `flash_programming.md` §7.2 note, `kwp.md` §12.7 note,
`docs/08` steps 5-6 (one line each), `logging/sessions/adaptation_channels.json`
(new), `tests/test_adaptation_reset_path.py` (new), `re/symbols.csv` rows. Do
**not** edit `logging/ecu_sim.py`, `logging/bench_rehearsal.py`, `tests/test_ecu_sim_*`
(H3 merged before you; H4 next), `patches/**`, `re/calibration_names.*`,
`injection.md`, `start.md`, docs/05 (H5 runs alongside you; H1 is merged).

## Acceptance
The setter(s) of block 11 +11 bit 0 and the meaning of 0x7FEB59 are VERIFIED-STATIC
or bounded with the exclusion set; the reflash consequence is stated and written
into `flash_programming.md` §7.2 and the playbook; the end-to-end emulator run
passes with the E% at +19 intact; the session file exists and is cited; suite
green; dump untouched; `ecu_sim.py`/`bench_rehearsal.py` untouched.
