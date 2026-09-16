# Brief C2 — RAM usage survey, static half (#23)

Issue **#23**, static part; the runtime-snapshot part waits for the logger
(C3) and a bench ECU. Wave C, first pair (parallel with C1). Desk work, no
`sudo`. Prerequisite: `main` at or after 647efe6.

Read `00_common_rules.md`, `docs/02_memory_map.md` §3-§4,
`docs/06_patch_pipeline.md` §3, `re/findings/boot.md` (start-up copies,
`app_entry_crt0`), `re/findings/eeprom.md` §5-§7 (mirrors, external SRAM
retention, the 0x7F8012 size probe), `re/findings/kwp.md` §5 (protected
window, the programming routine copied to 0x804800), `re/findings/can.md` §4
(`can_rx_shadow`), `re/findings/scheduler.md` §4 and §7 (TCB flags, kernel
object, task and ISR stack frames), `tools/README.md` (`sda_xref.py --var`,
`callgraph.py --xref-store`, `find_abs_refs.py --hist`,
`find_branch_refs.py` pointer words, `measuring_vars.py`).

## Facts
- On-chip SRAM 0x7F8000-0x7FFFFF (boot stack top 0x7FEFFC; `app_entry_crt0`
  sets r1 = 0x7FF768 per boot.md — reconcile the two). External SRAM
  0x800000-0x807FFF on CS1; OR1 opens a 256 KB window, so a 32 KB part is
  aliased 8x. `ext_sram_probe` (0x011898) writes RAM 0x7F8012 = 0x41 (32 KB)
  or 0x44 (64 KB).
- r13 = 0x7FFFF0; 64,727 r13-relative accesses span 0x7F8000-0x8073E9
  (docs/02 §4). `tools/sda_xref.py --var LO HI` decodes them. Absolute
  `lis`+D-form: `callgraph.py --xref-store`, `find_abs_refs.py`. Pointer
  words in flash: `find_branch_refs.py` (check that it scans the on-chip
  flash 0x404000-0x47FFFF as well; extend it if not).
- Known users without a per-byte static reference: the stack; the KWP
  programming routine copied from flash 0x081A00 to **0x804800**
  (`FUN_0008A12C`, `bl 0x806EA0` at 0x861B0 — measure the copy length; the
  flash source is ~0x3A00 bytes, which would run past 0x807FFF); the
  protected window **0x7F9E3C-0x7FA47F** (RequestUpload NRC 0x31; holds the
  EEPROM mirrors 0x7F9F80 and 0x7FA2A0); `can_rx_shadow` 0x803EE4 (22 x 12);
  `kwp_io_struct` 0x803DA4; TCB flags 0x7FE5FC-0x7FE644; kernel object
  pointer at r13-0x1A4C (0x7FE5A4); the 665 measuring-variable cells in
  `re/measuring_vars.csv`; the tester pointer table 0x0A3AE0 (indexed
  access, start.md §8).
- Start-up: RAM init table at file 0x1C2E78 (0x800000..0x807FF8); the copies
  at 0x09E438 and 0x08A1AC have an empty `.data` image for the external
  SRAM (eeprom.md), i.e. the region is **not cleared** and a patch must treat
  its RAM as undefined at power-on. Confirm or refute this; D1 depends on it.

## Tasks
1. `tools/ram_survey.py data/passat_azx_ori.bin [--csv re/ram_map.csv]
   [--line 32]`: one row per 32-byte line of 0x7F8000-0x807FFF with counts of
   r13 D-form reads / writes, absolute `lis`+D-form accesses, pointer words
   in flash (both flash regions), init-table coverage, membership of a known
   structure (a table inside the script with the facts above and their
   sources), and a `free_candidate` flag. Print a one-character-per-256-byte
   page map and the longest reference-free runs. Dependencies: capstone
   only (already required).
2. **Indirect access hunt.** For every RAM base produced by a pointer word or
   a `lis`+`addi`, look in the owning function for indexed or auto-increment
   stores (`stbx/sthx/stwx`, `stbu/sthu/stwu`, loops) and bound the extent;
   report `base + extent` as "indexed region (extent HYPOTHESIS)". The copy
   to 0x804800 and `can_rx_shadow` are the two known examples to calibrate
   the method on. Time-box 2 h; list what could not be bounded.
3. **Emulate `ext_sram_probe`** with `emu.Med9Emu`: which addresses it
   touches and what 0x7F8012 becomes for a 32 KB device (the harness's RAM
   is not aliased — model the alias with a read hook if needed). Record the
   consequence for placement: nothing above 0x807FFF is extra RAM.
4. **Stack estimate.** From `callgraph.py`, the deepest `stwu` chain from a
   task entry (task prologues, the 0x48-byte ISR frames of scheduler.md §7,
   the deepest leaf) gives a worst-case depth below 0x7FEFFC / 0x7FF768;
   mark it "stack (estimate)" in the map.
5. `re/findings/ram.md`: method, the map, every known region with its
   source and tag, the candidate blocks ranked (size, distance to the nearest
   reference, whether the programming copy overlaps it — acceptable only for
   state that need not survive a flash session), the recommendation (need:
   64 B for ff_fuel + 16 B for ff_counter; prefer a 256 B block), and the
   **dynamic protocol** for the other half of #23: RequestUpload snapshots
   (ranges avoiding 0x7F9E3C-0x7FA47F) at key-on, idle, after a drive, and
   after key-off/on three times; `tools/ram_snapshot_diff.py` (write it now
   against synthetic snapshots: flags bytes that changed between snapshots,
   and bytes that are non-zero/non-0xFF but never changed).
6. `logging/sessions/ram_snapshot.json` with the ranges, for C3's logger
   (format: `{"ranges": [{"start": "0x7F8000", "end": "0x7F9E3B"}, ...]}`).
7. Update docs/06 §3 (the RAM row) with the recommended block, tagged
   VERIFIED-STATIC for the static half and "dynamic confirmation pending
   #23"; if C1 is merged set its `PATCH_RAM` defaults, else state the value
   C1 must adopt in your report.
8. `re/symbols.csv`: named RAM regions that are missing (stack, kernel
   object, TCB flags, programming copy, ...). `tests/test_ram_survey.py`:
   the tool marks the known structures and the recommended block has zero
   static references. Commit on `agent/C2`; comment on #23.

## Acceptance
`ram_survey.py` reproduces the map; the recommended block has no static
reference of any kind and lies outside every known dynamic region; `ram.md`
says exactly what the runtime snapshots still have to show.
