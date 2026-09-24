# Patch pipeline

From C source to a file that is safe to flash, and back.

## 1. Patch representation

A patch is a directory `patches/<name>/` with:

- `patch.json`: metadata, the `build` section (what the author writes) and the
  list of byte changes (`changes`, **generated, never hand-edited**), each with
  CPU address, expected old bytes and new bytes — the same idea as
  `MED9-Patches`, but with CPU addresses in **our** address space and the
  mapping done by `med9lib`.
- `src/*.c`, `src/*.S` and a three-line `Makefile` (`NAME := <name>` plus
  `include ../common/patch.mk`) that produce the blob and regenerate
  `patch.json`. The linker script is shared: `patches/common/patch.ld`.
- `test/`: the bench procedures with expected log lines and the patch's
  `tolerance.json`. The emulator unit tests live in `tests/` with the rest of
  the suite.
- `README.md`: what it does, hooks used, RAM used, calibration added, and the
  disassembly of every hook site.

Applying a patch checks the old bytes, writes the new ones, recomputes
checksums, verifies, and writes a diff report (§5). Never edit the binary by
hand.

### 1.1 The `build` section

`changes` has to agree with the compiler's output byte for byte, so it is
produced by `tools/patch_gen.py` (`make gen`) from the `build` section. The
shape, from `patches/ff_counter`:

```json
{ "name": "ff_counter", "issue": 27, "base_sha256": "b15590d3…",
  "requires": [], "ram_status": "static",
  "build": {
    "flash": "0x00150000", "ram": "0x007FFB00", "ram_size": 256,
    "blob": "build/ff_counter.bin", "sym": "build/ff_counter.sym",
    "hooks": [ {"site": "0x00432940", "kind": "bl", "target": "ff_counter_hook_a",
                "old": "4bc8b0a5", "onchip_edit": true, "why": "…"},
               {"site": "0x0012067C", "kind": "bl", "target": "ff_counter_hook_b",
                "old": "4bffe9b1", "why": "…"} ],
    "data": [ … ] },
  "changes": [ "…generated…" ] }
```

* `flash` / `ram` / `ram_size` are also what `patch.mk` passes to the linker,
  so the descriptor and the placement cannot drift apart.
* `hooks[].target` is resolved from the `.sym` file; `kind` is `b`, `bl`, `ba`
  or `bla` and decides AA/LK. `patch_gen.py` encodes the branch word with its
  reach and alignment checked and reads the `old` bytes out of the stock image.
* `data` holds flat byte ranges that are not code — a new calibration block,
  a table edit, a pointer-table entry. Three sources:

  ```json
  "data": [
    {"addr": "0x005E2510", "file": "build/ffcal001.bin", "expect_blank": true,
     "why": "FFCAL001"},
    {"addr": "0x0002BD8C", "bytes": "000000ec", "old": "000007ff",
     "calibration_edit": true, "why": "tbl_can_rx slot 15: id 0x7FF -> 0x0EC"},
    {"addr": "0x000A78A8",
     "u32_syms": ["ff_diag_e_pct", "ff_diag_f_pct", "ff_diag_t_degc", "ff_diag_mode"],
     "old": "00038ec400038ec400038ec400038ec4",
     "why": "tbl_measuring_vars ids 2196-2199"}
  ]
  ```

  `file` is read relative to the patch directory (so the patch's own generator
  can produce it), `bytes` is inline hex, `u32_syms` is a list of symbol names
  resolved from the linker's `.sym` and packed as big-endian u32s — for a stock
  **pointer table** that has to point into the blob (the TKMWL
  measuring-variable handlers, here) and would go stale the moment the code
  moved if written as literal bytes; each symbol must lie inside the blob and
  be 4-byte aligned. `expect_blank` asserts the stock bytes are all 0xFF, and
  an explicit `old` is compared with what is really there. The unlock flags
  below are copied verbatim into the generated change.
* `ram_status` is `verified`, `static` (no static reference to the block,
  `re/findings/ram.md`; the runtime snapshots of #23 pending), `placeholder`
  or `example`. Anything but `verified` makes `tools/patch_apply.py` print a
  do-not-flash warning. **Both current patches are `static`** until the
  snapshots of `08_bench_playbook.md` step 4 are in.
* `requires` is recorded but not enforced by any tool.

### 1.2 The region guards and their unlock flags

`tools/patch_apply.py` folds every CPU alias to one canonical address first,
so the calibration cannot be reached through 0x5Cxxxx to get around a guard.

| Range | Unlocked by |
|---|---|
| 0x000000-0x00FFFF boot block / immobiliser | nothing |
| 0x010000-0x01FFFF boot body and the RAM-loader image | nothing — the firmware's own OBD programming service refuses it too (`re/findings/flash_programming.md` §3), so a change there could only be written by BDM |
| 0x080000-0x09FFFF resident programming module | nothing, same reason |
| 0x1C0000-0x1DFFFF stock calibration | `"calibration_edit": true` on that change |
| 0x400000-0x403FFF the 16 KB not in our read | nothing |
| 0x404000-0x47FFFF on-chip flash | `"onchip_edit": true` on that change |
| 0x1CEE20-0x1CEE6F identification block | nothing, ever |

A flag unlocks exactly one range. 0x404000-0x47FFFF **is** in the dump (file
0x200000+) and is covered by the code descriptor table at file 0x0A0000, so
`checksum.py fix` re-checksums a hook there correctly; the guard exists because
a KESSv2 write of that region has not been demonstrated, so `onchip_edit`
prints a warning on **every** apply — seven for a clean `ff_fuel` apply, one
per on-chip hook word. Read the image back and compare before trusting any
on-chip write (§6).

## 2. Build

```
patch.c ──clang (flags in 03_tooling.md §3)──▶ patch.o ──ld.lld -T patch.ld──▶ patch.elf
   ──llvm-objcopy -O binary──▶ patch.bin      ──llvm-nm -n──▶ patch.sym (symbols for hooks/logging)
```

The whole pipeline is four `make` targets, in this order:

```bash
cd patches/ff_counter && make check && make dump && make gen && make apply
```

`patches/common/patch.mk` drives the build and `patches/common/patch.ld` is
the shared linker script. Its load-bearing properties:

* **No `MEMORY` block and no defaults.** `PATCH_FLASH`, `PATCH_RAM` and
  `PATCH_RAM_SIZE` are required `--defsym`s taken from `patch.json`'s `build`
  section, and an undefined one fails the link. The usual
  `X = DEFINED(X) ? X : <default>;` idiom does **not** make `--defsym` an
  override in `ld.lld` 23.1.1: the script assignment wins while addresses are
  computed and `--defsym` only rewrites the symbol table afterwards, so the
  blob would be linked at the default while the ELF claims otherwise — a
  silent wrong answer (VERIFIED-STATIC, C1). Do not reintroduce the idiom.
* `.text` then `.rodata` go to `PATCH_FLASH`; `.rodata` is its own output
  section. `.sdata`/`.srodata`/`.data` are declared only so that
  `ASSERT(SIZEOF(…) == 0)` can fail the link if they are not empty: there is
  no initialised-data image for any RAM the patch uses.
* `.bss` is `(NOLOAD)` at `PATCH_RAM` and starts with
  `KEEP(*(.bss.patch_state))`, so a patch's documented state block is at
  exactly `PATCH_RAM`.
* `/DISCARD/` holds `.eh_frame` and `.comment` but must **not** contain
  `.got`/`.got2`/`.plt` (lld segfaults, `03_tooling.md` §3), so `make check`
  compares the blob size with the linker's `__patch_flash_size` instead — an
  orphan section in the binary is caught by arithmetic rather than by hope.
* Every object depends on every header, `hooks.S`, `patch.mk` and
  `patch.json`. Without that, a worktree whose `build/` held objects from an
  earlier `ff_state.h` once produced a blob that disagreed with the committed
  `patch.json` in six `cmplwi` words;
  `tests/test_patch_framework.py::test_patch_json_still_matches_a_fresh_build`
  is the test that catches it.

`make check` fails on a stale `med9_stock.h`, on any reference to r2 or r13,
on a non-empty small-data section, and if the blob is not exactly the size
the linker says. `make dump` disassembles the **raw blob** with
`tools/blobdis.py --addr <PATCH_FLASH> --check-sda` (`llvm-objdump` has no
`-b binary`); read it before injecting, and paste the hook extracts into the
patch's `README.md`. `make gen` regenerates `changes`; on an unchanged tree it
leaves `patch.json` byte-identical, which `git status` confirms.

Trampolines come from `patches/common/hooks.S` (`HOOK_TAIL`, `HOOK_FULL`),
stock addresses from the generated `patches/common/med9_stock.h` (never a
literal stock address in patch code: add the `re/symbols.csv` name to
`WANTED` in `tools/gen_stock_header.py` and regenerate), integer types from
`patches/common/types.h`. `patches/examples/hello_patch/` is the template to
copy; the six steps of a new patch are in `patches/README.md`.

## 3. Placement policy

| Resource | Where | Notes |
|---|---|---|
| Code and constants | 0x150000-0x1AFFFF (external flash, low alias; also reachable as 0x550000+) | 384 KB of 0xFF inside the 64 KB checksum blocks 0x150000..0x1AFFFF; recompute checksums. `ff_counter` links at 0x150000, `ff_fuel` at 0x152000; `ff_fuel`'s relocated OBD PID list sits in the blank block 0x160000-0x16FFFF |
| Extra code if needed | 0x144954-0x14FFFF | tail of block 0x140000-0x14FFFF |
| New calibration | 0x5E2510-0x5EFFFF | inside calibration block 0x5E0000-0x5EFFFF; addressed through the high alias like the rest of the calibration. FFCAL001 (334 B) is at 0x5E2510 |
| Never | 0x000000-0x01FFFF (boot, immobiliser pairing 0x6C00, the RAM-loader image), 0x080000-0x09FFFF (resident programming module), 0x1C0000-0x1DFFFF (stock calibration, except deliberate map edits), 0x400000-0x403FFF (the 16 KB of on-chip flash that is not in our read) | the first two are also refused by the firmware's own OBD route; `patch_apply.py` refuses all four (§1.2). 0x404000-0x47FFFF **is** in our read and is checksummed; it needs `"onchip_edit": true` per change |
| **RAM** | **0x7FFB00-0x7FFBFF (256 B)** — `PATCH_RAM = 0x7FFB00`, `PATCH_RAM_SIZE = 0x100` | in the middle of the reference-free internal-SRAM region **0x7FF770-0x7FFFEB**: 2,172 bytes with no r13 displacement, no absolute `lis`+D-form, no pointer word in either flash region and no measuring-variable cell, **above** the task stack 0x7FF3C0-0x7FF76F, which grows down (C2, #23, `re/findings/ram.md`, `re/ram_map.csv`). VERIFIED-STATIC; **dynamic confirmation is the six snapshots of `08_bench_playbook.md` step 4, and nothing is flashed before them.** `ff_fuel` uses 96 B of it today, `ff_counter` 7 B |

Rules that come with the RAM block:

* **Address it absolutely.** `tools/blobdis.py --check-sda` fails on any
  reference to r2 or r13, base register included; the compiler emits
  `lis r11,0x80 ; addi r11,r11,-0x500`.
* **Initialise it.** The cold start does not fill 0x7FF770-0x7FFFEB, so the
  contents are undefined at power-on. `ff_fuel` puts a magic word, a length
  and a checksum at the head of its block and re-initialises on a mismatch;
  `ff_counter` uses its `ff_alive` = 0xFC01 marker the same way and re-derives
  every byte at each cold start.
* **Do not rely on retention.** The ethanol estimate is persisted in the SPI
  EEPROM (`05_flexfuel_design.md` §3.8: block 8 payload +19), not in RAM.

Two placements that are **not** usable (VERIFIED-STATIC, C2):

* the top of the external SRAM, 0x8057xx-0x807FFF — 0x804800-0x808687 is where
  `FUN_0008A12C` copies flash 0x081A00-0x085887 (0x3E88 B) during a KWP
  programming session **and then executes it** (`bl 0x806EA0` at 0x0861B0). A
  patch writing there mid-session would corrupt the running flash driver; the
  tail also wraps onto 0x800000-0x800687 on a 32 KB CS1 part, and 0x805784 is
  the base of a live RAM dispatch table (0x1C-byte entries, function pointer
  at +0x18, called at 0x082C00). The external SRAM is not a retention area
  either: `ram_clear_block` (0x06D8F8) and `app_init` (0x04CCD4) zero
  0x800004-0x80498F at every cold start;
* "unused r13 gaps" — every reference-free gap larger than 128 bytes inside
  the used `.bss` is the body of an array or buffer whose base is the last
  referenced byte before it (`ram_survey.py --indexed`).

Branch reach: `b/bl` have ±32 MB range, so any placement is reachable with a
single instruction. Code at 0x15xxxx addresses calibration with
`lis 0x5E`, like the stock code.

## 4. Hook techniques

1. **Call redirect**: replace an existing `bl target` with `bl ff_trampoline`;
   the trampoline saves what it needs, runs our code, then tail-calls the
   original target. Cheapest and easiest to reason about; the raster hooks and
   the fuel hook work this way.
2. **Table pointer replacement**: point a dispatch-table entry (the KWP service
   table at 0x2B820, CAN slot handlers, the measuring-variable table) at our
   function; the community KWP and mapswitch patches work this way, and so do
   the patch's measuring-variable handlers (`u32_syms`, §1.1).
3. **Instruction patch**: replace one stock instruction with a `bl` to a stub
   that re-does that instruction and adds ours (the ignition, start and rail
   hooks: an `add` or a `sth`/`stb` store), or change a constant or a table
   word in place (the CAN receive id at 0x2BD8C). Use only when 1 and 2 are
   impossible; document the original instruction.
4. **Data-only patches**: calibration edits and our new calibration block.

Register discipline in trampolines: preserve r0, r3-r12, CR, LR, CTR and XER
as the hooked site expects (EABI volatile set); never touch r1 alignment,
r2, r13, r14-r31 unless saved; no FP registers.

Both trampolines are written once, in `patches/common/hooks.S` (C1):
`HOOK_FULL` saves that whole set in an 80-byte frame, and `HOOK_TAIL` saves
only LR in a 16-byte frame for a site where the volatile set is provably
dead — which is the case between two argument-less `bl` in a flat ERCOSEK
raster task (`re/findings/scheduler.md` §7). Both end by tail-branching to the
original target, so the stock call still happens and exactly one flash word
changes **per hook**. A patch may hook the same raster in both ERCOSEK task
sets — `patches/ff_counter` and `patches/ff_fuel` both take the 10 ms raster
of set A (0x432940, on-chip) and set B (0x12067C, external) and record which
one ran — so Flash 1 changes two hook words (F1, #27; `make HOOKS=external`
still builds the single-word set-B patch, `patches/ff_counter/README.md`).

The tail branch is `ba` (AA=1), not `b`. **VERIFIED-STATIC (C1):** the
GNU/LLVM PowerPC assembler reads a *numeric* branch operand as a
**displacement**, so `b 0x0011F02C` assembles to 0x4811F02C — a branch to
pc + 0x11F02C — and `.set` does not help; only a linker-resolved undefined
symbol produces the intended relative word. `ba 0x0011F02C` (0x4811F02E) takes
the target address literally and needs no relocation.

## 5. Verification steps for every build

1. `checksum.py verify -q` -> `ALL OK (65 blocks)`.
2. `python3 tools/bindiff.py stock.bin patched.bin -p patches/<name>/patch.json`
   -> exit 0, i.e. every changed byte is either a change listed in
   `patch.json` or a descriptor `sum/~sum` word of an affected block. Anything
   else is reported as **unexpected** and the exit status is 1. Add
   `--json work/diff.json` to keep the report with the build. `bindiff` also
   checks the `old`/`new` bytes and `base_sha256` from `patch.json` (#24).
3. Identification block 0x1CEE20-0x1CEE6F unchanged.
4. Emulator unit tests of the patch code pass (`tests/test_ff_*.py`,
   `emu/README.md`). The standard for a feature is two bit-identity runs of the
   hooked stock code — disabled, and enabled at neutral calibration — with a
   whole-SRAM diff that moves nothing outside the state block.
5. Disassembly of every hook site shows the intended instruction and target.
6. For bench: expected log lines written down before flashing.

### The apply step

`tools/patch_apply.py` is the only place an image is ever modified:

```bash
python3 tools/patch_apply.py data/passat_azx_ori.bin patches/ff_counter \
        -o work/ff_counter.bin            # --dry-run, --json
```

It never touches its input, never writes to `data/`, and writes **nothing at
all** unless every one of these passes:

1. the stock file's SHA-256 matches `base_sha256`;
2. no change lands in a forbidden region without its unlock flag (§1.2);
3. every change's `old` bytes are really there (so a patched image is refused,
   and so is the wrong base image);
4. `checksum.fix` then `checksum.verify` -> ALL OK (65 blocks);
5. the identification block 0x1CEE20-0x1CEE6F is byte-identical — this one
   cannot be unlocked by any flag;
6. `bindiff.diff(stock, patched, patch_json)` returns `report["ok"]`, i.e. every
   changed byte is a listed change or a descriptor word.

Outputs next to `-o`: `<name>.bin`, `<name>.diff.json` (the bindiff report kept
with the build) and `<name>.sha256`. A patch whose `ram_status` is not
`verified` produces a loud do-not-flash warning — the state both patches are
in until the #23 snapshots are taken.

`make bench-kit` (`tools/bench_kit.py`) runs the apply step for all four
bench-day images into `work/bench_kit/` with a `MANIFEST.json` (SHA-256,
changed ranges, expected flash CRC) and a kit `README.md`; it refuses a
non-canonical dump, any output under `data/` or outside `work/`, and an apply
that rewrote a descriptor.

## 6. Flash and roll back

**The gate (ruling, 2026-09-24).** No write of any kind — **Flash 0
included** — before issue #23's runtime RAM snapshots are in
(`08_bench_playbook.md` step 4 before step 5; `07_workflow.md` §6.4). The
`ram_status` warning of §5 is the tool's per-patch check, not the whole gate:
the rule is "snapshots first", not "patches only".

Write with KESSv2 (protocol 179) from the Windows machine following the
checklist in `04_re_guidelines.md` section 6 and the order of
`08_bench_playbook.md`. KESS applies its own checksum correction; because our
file already verifies, its correction must be a no-op — if KESS reports that
it corrected something, stop and find out what. Read back after writing and
compare; if the read-back differs from what we wrote outside the descriptors,
stop and investigate (that would mean a check we do not know about). Roll
back by writing the original read.

### 6.1 What the ECU's own route can write

KESS flashes over OBD, so it drives *this firmware's* programming service
(E6, VERIFIED-STATIC, `re/findings/flash_programming.md`): session `10 85`
reboots the ECU into a second KWP stack with SID 0x34 `RequestDownload`, and
that service hard-codes the address ranges it accepts
(`kwp_download_range_allowed`, 0x0889C8 — an **exact** start/end match, else
NRC 0x42):

| start | end | what |
|---|---|---|
| 0x020000 | 0x07FFFF | application code, part 1 |
| 0x0A0000 | 0x1BFFFF | application code, part 2 |
| **0x404000** | **0x47FFFF** | **on-chip flash — the whole array except small block 0** |
| 0x080000 | 0x09FFFF | alias; the handler rewrites it to 0x1C0000-0x1DFFFF |
| 0x1C0000 / 0x1E0000 | 0x1DFFFF / 0x1FFFFF | calibration, variant-gated |

Two ranges the firmware refuses outright, so no tool driving the OBD route
can touch them: **0x000000-0x01FFFF** (vectors, boot, the RAM loader image)
and **0x080000-0x09FFFF** (the resident programming module: the code
directory, the flash driver, `app_entry_crt0`). `patch_apply.py` refuses the
same ranges (§1.2). Erase granularity, if a partial write is ever attempted:
**48 KB / 16 KB / 64 KB** blocks on the on-chip array, 8 KB parameter blocks
plus 64 KB main blocks on the CS0 part; programming is per 32-bit word.

**So every on-chip hook word is inside a range the ECU itself can erase and
program**: `patches/ff_fuel` has **seven of its eight** there (0x42247C,
0x432940, 0x41D40C, 0x41A680, 0x41A808, 0x431384, 0x45845C; the source of
truth is the hook table in `patches/ff_fuel/README.md`), `patches/ff_counter`
one of two (0x432940). The open question is only whether KESSv2 *offers* that
range, not whether the ECU can take it — and the read-back answers it.

There is **no signature and no boot-time checksum verdict**: the runtime
CRC-32 over 0x020000-0x1BFFFF / 0x404000-0x47FFFF / 0x5C2E00-0x5FFFFF is
computed and *reported* (RAM 0x7F9178), never compared. The 65 block sums
still have to be correct because the *tool* checks them, not the ECU.

### 6.2 Read-back checklist, for every write

In addition to the `bindiff -p` of §5:

1. **Read back 0x404000-0x47FFFF and `bindiff` it against the written file.**
   Stock bytes there = KESS skipped the array. **Flash 0 cannot answer this**
   (its file *is* the stock image, so written and skipped read the same); the
   on-chip question is settled by **Flash 1's read-back of 0x432940** (#27,
   `08_bench_playbook.md` step 6) and then by the `ff_fuel` flash's seven words
   (#32). What Flash 0's read-back does prove is that nothing on-chip was
   damaged.
2. **Check the halfword at file 0x1E2500 is `5A 5A`.** It is the one
   integrity marker the firmware acts on: without it the ECU reboots into
   its flash loader instead of starting the application (recoverable, §6.3,
   but it looks like a brick if you do not expect it).
3. **Read EEP_CONF block 10** (EEPROM offset 0x260, 0x20 B) before and after:
   the ECU stamps its identification string and a status word there on every
   programming session, so a changed block 10 proves the ECU's own route ran.
   It is inside the 1 KB that `35`/`36` expose at 0x480000
   (`tools/flash_segments.py --segments`), so no EEPROM clip is needed.
4. **Log the adaptation channels** (`logging/sessions/adaptation_channels.json`)
   before and after. A bare OBD download does not reset the fuel trims; a
   session that runs routine 0xC5 (component-protection adaptation) resets all
   17 channels on the next boot (H2, #47, `re/findings/eeprom.md` §11).
5. **Power-cycle and read the flash CRC** (`logging/sessions/flash_crc.json`).
   It is the firmware's own hash of what is in the flash, independent of
   KESS's read routine: stock 0x5562139F, `ff_counter` 0x06C08AD4, `ff_fuel`
   0x65BD7A90 (`make bench-kit` computes the expected value for each image).

### 6.3 The recovery path if an OBD write is interrupted

If a calibration write is cut short so the `5A5A5A5A` marker at 0x1E2500 is
wrong, the ECU does **not** brick: on the next power-up it sets the boot magic
itself and reboots into the **RAM bootstrap loader** (F5, #26/#28,
`re/findings/ram_loader.md`), which can reprogram the calibration (and almost
everything else, the resident programming module included) — but only over
its **serial (SCI) line, not CAN**. So keep a serial-capable recovery tool on
hand, and recognise the "reboots into the loader and stays there" symptom as
*recoverable*, not a brick (`04_re_guidelines.md` §6). An interrupted
*application* or *on-chip* write usually leaves the OBD `10 85` route intact
instead, so re-flash over CAN. Only a corruption of the boot body
(0x010000-0x01FFFF) or reset stub (0x000000-0x001FFF) needs BDM — and neither
this route nor the loader can write those, so an OBD write can never cause it;
treat a BDM tool as insurance for that case and for reading 0x400000-0x403FFF,
per `01_project_plan.md` §6.

### 6.4 The log comparison

Each patch keeps a baseline log (stock) and a patched log over the same bench
scenario; `tools/logcmp.py` (#24) compares the common variables and flags
deviations. The E0 equivalence test in `05_flexfuel_design.md` is the same
mechanism. The log CSV format and the tolerance-file format are defined in
`logging/README.md`. Per patch:

```bash
python3 tools/logcmp.py patches/<name>/test/baseline.csv work/run.csv \
        -t patches/<name>/test/tolerance.json --align-on raster_setA_10ms_count:100 \
        --json work/logcmp.json
```

Exit status 1 means a variable moved outside its tolerance. `--align-on`
removes the power-up offset between the two runs using the ECU's own raster
counter, and `logcmp.py derive` measures the tolerances from two stock runs
(`07_workflow.md` §5.4-§5.5). Keep the tolerance file with the patch, not with
the tool: it encodes what that patch is allowed to change.
