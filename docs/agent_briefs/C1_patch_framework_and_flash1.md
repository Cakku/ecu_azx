# Brief C1 — Patch build framework and the Flash-1 counter patch (software)

Issues: **#25** (framework) and the software half of **#27** (Flash 1: the
no-op counter patch). Wave C, first pair (parallel with C2). Desk work only;
nothing is flashed. No `sudo`. Prerequisite: `main` at or after 647efe6
(waves A and B merged).

Read `00_common_rules.md`, then `docs/06_patch_pipeline.md` (all),
`docs/04_re_guidelines.md` §7, `docs/03_tooling.md` §3-§3.1,
`patches/examples/hello_patch/` (Makefile, `hello.ld`, README),
`tools/README.md` (`bindiff`, `checksum`, `blobdis`, `med9lib`),
`emu/README.md`, and `re/findings/scheduler.md` §7-§8 (the hook site).

## Facts you start from
- Toolchain: LLVM 23.1.1 tarball at `/Users/carlo/toolchains/LLVM-23.1.1-macOS-ARM64`
  (`LLVM_DIR` in the hello_patch Makefile). If it is missing, install it with
  the curl/tar lines of docs/03 §3.1 (no sudo). clang rejects the GCC-only
  flags (`-msdata`, `-ffixed-r2`); the lld quirks are listed in §3.1.
- Free flash for code: 0x150000-0x1AFFFF, all 0xFF, inside checksummed 64 KB
  blocks. Default `PATCH_FLASH` = **0x150000**. Code there addresses
  calibration with `lis 0x5E` like the stock code.
- Flash-1 hook: **0x12067C**, original word `4B FF E9 B1` = `bl 0x11F02C`,
  inside `task_100ms` (0x1205A0, TCB 23). r3-r12 are dead at the site (both
  neighbours are argument-less `bl`s), LR is already saved by the task
  prologue. The leaf 0x11F02C clears RAM 0x7FE889 (byte) and 0x800E18 (half)
  and returns. Period 100 ms is HYPOTHESIS (<= 150 ms VERIFIED-STATIC).
- Checksums: 65 blocks, `tools/checksum.py fix` / `verify`. 0x12067C is in
  block 0x120000-0x12FFFF; 0x150000 in 0x150000-0x15FFFF.
- Regression: `tools/bindiff.py` classifies every changed byte as patch /
  descriptor / unexpected; API `bindiff.diff(stock, patched, patch_json) ->
  (ranges, report)`; the apply tool must refuse when `report.ok` is false.
- Branch encoding, I-form: `0x48000000 | (LI & 0x03FFFFFC) | LK`, `LI = target
  - site`, reach ±32 MB, AA = 0. Check the reach and the alignment.
- Identification block 0x1CEE20-0x1CEE6F must never change.

## Tasks
1. **Framework** in `patches/common/`:
   - `patch.ld`: generic linker script driven by `--defsym PATCH_FLASH /
     PATCH_RAM / PATCH_RAM_SIZE`; `.text/.rodata` in FLASH, `.bss` in RAM,
     `ASSERT(SIZEOF(.data) == 0)`, no small-data sections; keep the lld
     quirks (no synthetic sections in `/DISCARD/`, space before `:`).
   - `patch.mk`, included by every patch Makefile: `all` (.o .elf .bin .lss
     .sym), `check` (r2/r13 grep, section check), `dump`
     (`tools/blobdis.py --check-sda`), `gen` (task 2), `apply` (task 3).
   - `hooks.S` + `hooks.h`: trampoline macros in assembly.
     `HOOK_TAIL(name, c_func, orig)` for argument-less `bl` sites where
     r3-r12 are dead: `stwu r1,-16(r1); mflr r0; stw r0,12(r1); bl c_func;
     lwz r0,12(r1); mtlr r0; addi r1,r1,16; b orig`.
     `HOOK_FULL(name, c_func, orig)` preserving r0, r3-r12, CR, LR, CTR, XER
     (docs/06 §4) for sites with live registers. Document the frame layout,
     keep r1 8-byte aligned, never touch r2, r13, r14-r31 or FP registers.
   - `med9_stock.h`: stock functions and RAM cells patches use, as
     address macros with the `re/symbols.csv` name and tag in a comment:
     `can_init_mb` 0x135750, `can_rx_poll` 0x4379C8, `nvm_block_request`
     0x06131C, `measuring_result_emit` 0x38EB4, `rksplit` 0x41C3A0,
     `clr_ram_7FE889_800E18` 0x11F02C; RAM `rk` 0x803038,
     `can_rx_buf_spare0` 0x803F9C. Generate it with a small
     `tools/gen_stock_header.py` from `re/symbols.csv` (a list of names in
     the script) so it cannot drift from the knowledge base.
   - `types.h`: u8/u16/u32/s8/s16/s32 with size `_Static_assert`s; no libc.
2. **Descriptor generation.** Extend `patch.json` (docs/06 §1) with a
   `"build"` section: `{"flash": "0x150000", "ram": "0x...", "ram_size": 64,
   "blob": "build/<name>.bin", "hooks": [{"site": "0x12067C", "kind": "bl",
   "target": "ff_counter_hook", "old": "4bffe9b1", "why": "..."}]}`.
   `tools/patch_gen.py` (or `patch_apply.py --gen`) resolves targets from
   the `.sym` file, encodes the branch words, adds the blob as one change
   whose `old` is all 0xFF (assert the stock bytes really are 0xFF there) and
   rewrites the `changes` list. `changes` is generated, never hand-edited.
3. **Apply tool** `tools/patch_apply.py stock.bin patches/<name> -o
   work/<name>.bin`: check `base_sha256`; check every `old`; write `new`;
   `checksum.fix`; `checksum.verify` must be ALL OK; `bindiff.diff` must be
   ok (else exit 1 and write nothing); identification block unchanged; write
   `work/<name>.bin`, `work/<name>.diff.json`, `work/<name>.sha256`. Flags
   `--dry-run`, `--json`. Refuse changes inside 0x000000-0x00FFFF or
   0x400000-0x47FFFF, and inside 0x1C0000-0x1DFFFF unless the change carries
   `"calibration_edit": true` (docs/06 §3).
4. **`patches/ff_counter/`** (Flash 1, #27): `ff_counter_tick()` increments a
   u32 counter and writes a u16 "alive" pattern in the patch RAM block; hook
   0x12067C via `HOOK_TAIL` ending in `b 0x11F02C`. RAM: use the block from
   `re/findings/ram.md` if C2 is merged; otherwise `PATCH_RAM = 0x807F00`,
   size 0x40, marked **PENDING #23** in the README and as
   `"ram_status": "placeholder"` in `patch.json` (the apply tool prints a
   warning for that flag). `test/`: expected log lines (counter +1 per
   100 ms read with the DDLI logger; if it is +1 per 150 ms, the 100 ms
   hypothesis of scheduler.md §5.3 is wrong — say so in the procedure, this
   flash doubles as the period test), a `tolerance.json` over the stock
   variables of issue #44 for the stock-vs-Flash-1 log comparison.
5. **Migrate `hello_patch`** onto the framework at 0x150000; `make check`
   keeps its meaning.
6. **Tests** `tests/test_patch_framework.py`: build (skip with a clear
   message if `LLVM_DIR` is missing); apply hello and ff_counter to temp
   copies; ALL OK; bindiff ok; applying twice fails on `old`; wrong
   `base_sha256` fails; the blob region of the stock image is 0xFF. With
   `emu.Med9Emu(<patched copy>)`: call the stub with the harness's magic LR
   and seeded RAM -> counter incremented by exactly 1, 0x7FE889 and 0x800E18
   cleared (the original leaf ran), no other byte of 0x7F8000-0x807FFF
   changed (snapshot diff), r1 restored. Then run stock and patched images
   from 0x12067C to 0x120680 and diff RAM: identical except the counter block.
7. Disassemble every hook word and the whole blob with `blobdis.py
   --check-sda`; paste the listing into the patch README.
8. Update `docs/06_patch_pipeline.md` §5 (the apply step exists now) and
   `tools/README.md`. Commit on `agent/C1`; comment on #25 and #27.

## Acceptance
hello-patch builds to a raw blob at 0x150000; `patch_apply.py` applies it to
a copy; `checksum.py verify` prints ALL OK; the bindiff report lists only the
intended ranges plus descriptors; ff_counter builds, applies and passes the
emulator tests; the whole unit-test suite passes; nothing under `data/`
changed.

## Not in scope
Flashing (#26-#28), the RAM survey (C2), the fuel patch (D1).
