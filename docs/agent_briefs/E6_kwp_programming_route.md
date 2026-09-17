# Brief E6 — The ECU's own flash-programming route and on-chip flash writability, static (#26 #27 #28 #32 blocker)

Two of `patches/ff_fuel`'s three hook words (0x42247C, 0x432940) and every
hook E1/E2/E5 add live in the **on-chip UC3F flash 0x404000-0x47FFFF**.
Whether KESSv2 protocol 179 writes that array has never been shown on this
ECU (D1's #32 comment; `docs/06` §1 `onchip_edit`). KESS flashes over OBD,
i.e. through **this firmware's own KWP programming service**, so the dump
can say what that service is able to erase and program. Wave E pair 3,
parallel with E5. Static work plus emulation; no ECU is contacted; **KESS
itself, its protocols and its files are out of scope** — only our firmware.

Read `00_common_rules.md`, `docs/02_memory_map.md` §2, §3, §6,
`re/findings/mpc5xx_registers.md` §8 (UC3F) and §5, `re/findings/kwp.md` §1,
§2 (session `10 85`), §3, §5, §9, §12, `re/findings/ram.md` §6 (the
programming copy 0x081A00-0x085887 → 0x804800), `re/findings/boot.md`,
`re/findings/eeprom.md` §1.1 (which QSMCM sites are the flash-tool serial
paths), `tools/checksum.py`, `tools/callgraph.py`, `tools/find_abs_refs.py`,
`emu/README.md`, `logging/ecu_sim.py` (how the KWP handlers are driven).

## Facts
- KWP dispatch table at 0x2B820 (28 entries): the programming-related SIDs
  present are 0x10 (`10 85` = programming session, needs security level 1,
  "sets up UC3F"), 0x27, 0x31 StartRoutine, 0x32 StopRoutine, 0x34/0x36/0x37
  (check: 0x34 RequestDownload is **not** listed in docs/02 §7's SID list —
  find how data gets *into* the ECU: 0x36 TransferData in download mode, or a
  handler in the relocated copy). Handlers for 0x31/0x32 and others live in
  on-chip flash (docs/02 §7).
- `FUN_0008A12C` copies flash 0x081A00-0x085887 (0x3E88 B) to RAM 0x804800
  and the code then **executes** it (`bl 0x806EA0` at 0x0861B0, r2 =
  0xD4CDF0); callers 0x086A28, 0x087494, 0x088828 (ram.md §6). That RAM copy
  is the flash driver: flash cannot be programmed from code running in the
  same flash.
- UC3F control registers 0x6FC800-0x6FC80B have **5 references** (file
  0x11D04, 0x11D7C, 0x1D4FC, 0x81CD4, 0x82CBC); program/erase needs the
  interlock sequence SES → interlock write → EHV (mpc5xx_registers.md §8).
  The external flash on CS0 is a separate device with its own command
  sequence (identify the part from the unlock/command writes).
- `kwp_transfer_mode4` (0xA33B4) handles a 1 KB window 0x480000-0x480400
  through a 32-entry segment table at file 0xB2FF2, filling 0xFF for
  unmapped sub-ranges (kwp.md §5.1) — likely the programming-time view of
  the flash. Security: level 1 = 5-round LFSR mask 0x5FBD5DBD, level 2 =
  seed + 0x11170 (kwp.md §3).
- The 65 checksum blocks and their descriptors are known (`tools/checksum.py`,
  docs/02 §6); what the **firmware** verifies at boot or after programming,
  and what it does on a mismatch, is not written down anywhere yet.

## Tasks
1. **Map the programming flow** from `10 85` onward: which SIDs/sub-functions
   the programming session enables (the mask at entry+4), the 0x31 routines
   (erase? checksum? which arguments), how download addresses and lengths
   are checked (the mode-4 segment table decoded into a table of
   `segment → physical range → device`), and when 0x8A12C relocates the
   driver. Name every function in `re/symbols.csv`.
2. **What the relocated driver can program.** Disassemble 0x081A00-0x085887
   (it runs at 0x804800 with r2 = 0xD4CDF0 — check `tools/r2_context.py`):
   find the external-flash command sequences (unlock/program/erase; identify
   the CS0 part) **and whether any path touches the UC3F registers
   0x6FC800+ or writes into 0x404000-0x47FFFF with the interlock sequence**.
   The five UC3F references above are the entry point of that search:
   classify each (boot-time configuration, censorship check, programming).
   Result, VERIFIED-STATIC: the list of address ranges the OBD route can
   erase and program, with erase granularity per device.
3. **Boot-time verification.** Find the code that reads the descriptor table
   at file 0x0A0000 / the 65 block checksums (or any other signature) at
   boot or after programming; what it covers, what happens on mismatch
   (DTC? no start? "programming incomplete" flag in EEPROM?). This is what
   makes Flash 0 (#26) predictable. Check whether the identification block
   0x1CEE20-0x1CEE6F or the EEPROM holds a "flash counter"/"programmed by"
   record the tool updates (compare with `eeprom_map.py --clients`).
4. **Optional, time-boxed (≤ 2 h):** run the relocated driver in the
   emulator (`Med9Emu.run` with a device model for the CS0 flash command
   register writes, and the UC3F registers stubbed) to confirm the command
   sequences dynamically, the way `ecu_sim.py` proves the KWP handlers.
   Never point it at `data/`.
5. **Consequences.** Write `re/findings/flash_programming.md` with: the flow,
   the segment table, the programmable ranges, the verification, and a
   decision section: (a) if the OBD route can program UC3F 0x404000+, say so
   and list what the bench must still confirm (a read-back after Flash 0,
   `procedure.md` §1); (b) if it cannot, list per current and planned hook
   (0x42247C rk, 0x432940 set-A raster, 0x41D40C ignition, 0x41A680/0x41A808
   start fuel, 0x431384 start ignition, E5's rail site) the best
   external-flash alternative (`injection.md` §6.1 candidates B/C; the
   set-B raster is already external; think about in-place rewrites in the
   external-flash callers of those on-chip functions) or "BDM/K-TAG only".
   Dated notes in docs/02 §2/§6, docs/06 §6, kwp.md §9; `re/symbols.csv`
   rows; a tool if you wrote one (`tools/flash_segments.py` to dump the
   segment table, with a test). Commit on `agent/E6` after every finding;
   comment on #32 and #26.

## Acceptance
`flash_programming.md` answers, with evidence, which physical ranges the
firmware's own programming service can program; the boot-time verification
behaviour is documented; every finding is tagged; the decision section tells
the human what the first bench flash must check and what the fallback is.
