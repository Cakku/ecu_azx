# Brief A5 — Regression tooling and Unicorn emulation harness

Issues: **#24** (regression tooling, Phase 2) and the Unicorn half of **#21**
(emulation harness). Pure Python; runs now. No `sudo`.

Read `00_common_rules.md` first. Study `tools/med9lib.py` and
`tools/checksum.py`; reuse them.

## Tasks
1. **`tools/bindiff.py`** (#24): compare two dump images (same size). Output a
   report of changed byte ranges as CPU addresses (via `med9lib`), each range
   classified as: inside a range listed in a given `patch.json` (format in
   `docs/06_patch_pipeline.md` §1), inside a checksum descriptor's sum/~sum
   words (table addresses in `checksum.py`), or **unexpected**. Exit code 1
   if anything is unexpected. Include a `--json` output. Test: original vs a
   copy with a few bytes changed and checksums fixed -> exactly those ranges
   plus the descriptors, nothing else.
2. **`tools/logcmp.py`** (#24): define the logger CSV format now
   (`time_s, var, value` long format or one column per variable; document it
   in `logging/README.md`) and implement comparison of a baseline and a
   candidate log over common variables with per-variable mean/max deviation,
   tolerance file, and a pass/fail summary. Include synthetic test data.
3. **`emu/` Unicorn harness** (#21, Unicorn part): package that
   - loads the dump with the verified map: EXT_FLASH at 0, its alias at
     0x400000-0x5FFFFF for the ranges not claimed by internal modules,
     INT_FLASH at 0x404000, RAM 0x7F8000-0x807FFF, DECRAM 0x6F8000, USIU
     0x6FC000, IMB 0x700000-0x70FFFF, CS2 0x900000 and CS3 0xA00000 as
     zero-filled stubs, all peripheral reads/writes logged via hooks;
   - sets r1 = 0x7FEFFC, r13 = 0x7FFFF0, r2 selectable (0x17FF0 / 0x5C9FF0),
     MSR with FP enabled;
   - `call(addr, args=[...], regs={...}, mem={...}, max_insns=...)` returns
     r3 and a memory snapshot; unmapped access and unknown SPR access are
     reported, not fatal.
   - Tests: (a) call `boot_or_adjust` at 0x11E44 with r3 = 0xFF800650 and RAM
     byte 0x7FE9E8 = 0/1; expect 0xFF800650 / 0xFF800550 per the disassembly
     in `docs/02` §7 (verify the bit yourself from the `rlwinm` operands);
     (b) run from reset 0x100 and record how far the boot path gets, which
     USIU registers it touches (compare with `docs/02` §3), and where it first
     needs a peripheral value you do not model (e.g. PLL lock, SIPEND); patch
     nothing in the flash image — stub via hooks only; (c) execute the
     DECRAM-copied routine path (0x120C8-0x120FC) and confirm the copy lands
     at 0x6F8000 and runs.
   - Document Unicorn limitations you hit (FPU, SPRs) in `emu/README.md` and
     `docs/03_tooling.md` §5.
4. Commit on `agent/A5`. Comment on #24 and #21 (state clearly that the Ghidra
   EmulatorHelper half of #21 is left for after brief A1).

## Not in scope
Ghidra; patches; any change to `checksum.py` semantics.
