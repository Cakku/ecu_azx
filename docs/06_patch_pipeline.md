# Patch pipeline

From C source to a file that is safe to flash, and back.

## 1. Patch representation

A patch is a directory `patches/<name>/` with:

- `patch.json`: metadata and the list of byte changes, each with CPU address,
  expected old bytes and new bytes (same idea as `MED9-Patches`, but with CPU
  addresses in **our** address space and the mapping done by `med9lib`):

```json
{ "name": "ff_counter", "base_sha256": "b15590d3…", "requires": ["ff_rt"],
  "changes": [
    {"addr": "0x0A7B10", "old": "4bf8c3d1", "new": "48098af1", "why": "bl -> ff_rx_task trampoline"},
    {"addr": "0x150000", "old": "ff…", "new": "<blob>", "why": "code"} ] }
```

- `src/*.c`, `src/*.S`, `Makefile` (three lines: `NAME` plus
  `include ../common/patch.mk`) that produce the blob and regenerate
  `patch.json`. The linker script is shared: `patches/common/patch.ld`.
- `test/`: emulator unit test and the bench procedure with expected log lines.
- `README.md`: what it does, hooks used, RAM used, calibration added.

Applying a patch checks the old bytes, writes the new ones, recomputes
checksums, verifies, and writes a diff report. Never edit the binary by hand.

#### Added 2026-09-16 (brief C1, issue #25) — the `build` section

`changes` is **generated, never hand-edited**: it has to agree with the
compiler's output byte for byte. What a patch author writes is the `build`
section; `tools/patch_gen.py` (or `make gen`) turns it into `changes`:

```json
{ "name": "ff_counter", "issue": 27, "base_sha256": "b15590d3…",
  "requires": [], "ram_status": "static",
  "build": {
    "flash": "0x00150000", "ram": "0x007FFB00", "ram_size": 256,
    "blob": "build/ff_counter.bin", "sym": "build/ff_counter.sym",
    "hooks": [ {"site": "0x0012067C", "kind": "bl", "target": "ff_counter_hook",
                "old": "4bffe9b1", "why": "…"} ] },
  "changes": [ "…generated…" ] }
```

* `flash` / `ram` / `ram_size` are also what `patch.mk` passes to the linker,
  so the descriptor and the placement cannot drift apart.
* `hooks[].target` is resolved from the `.sym` file; `kind` is `b`, `bl`, `ba`
  or `bla` and decides AA/LK.
* `ram_status` is `verified`, `static` (no static reference, `re/findings/ram.md`;
  the runtime snapshots of #23 pending — `ff_counter` since 2026-09-16),
  `placeholder` or `example`. Anything but `verified` makes
  `tools/patch_apply.py` print a do-not-flash warning.
* `requires` is recorded but not yet enforced by any tool.

#### Added 2026-09-16 (brief D1, issue #32) — `build.data` and the `onchip_edit` flag

`patches/ff_fuel` needed two things the framework did not have. Both are
generic; `patches/ff_counter` is unaffected.

**1. `build.data` — flat byte ranges that are not code.** A patch that ships a
new calibration block, or edits a table, declares it next to the hooks and
`tools/patch_gen.py` turns it into a change with the `old` bytes read out of
the stock image:

```json
"data": [
  {"addr": "0x005E2510", "file": "build/ffcal001.bin", "expect_blank": true,
   "why": "FFCAL001"},
  {"addr": "0x0002BD8C", "bytes": "000000ec", "old": "000007ff",
   "calibration_edit": true, "why": "tbl_can_rx slot 15: id 0x7FF -> 0x0EC"}
]
```

`file` is read relative to the patch directory (so the patch's own generator
can produce it), `bytes` is inline hex, `expect_blank` asserts the stock bytes
are all 0xFF, and an explicit `old` is compared with what is really there. The
unlock flags below are copied verbatim into the generated change.

> **Added 2026-09-16 (brief D2, issue #39) — `u32_syms`.** A third source for a
> `data` entry: a list of symbol names, resolved from the linker's `.sym` and
> packed as big-endian u32s.
>
> ```json
> {"addr": "0x000A78A8",
>  "u32_syms": ["ff_diag_e_pct", "ff_diag_f_pct",
>               "ff_diag_t_degc", "ff_diag_mode"],
>  "old": "00038ec400038ec400038ec400038ec4",
>  "why": "tbl_measuring_vars ids 2196-2199"}
> ```
>
> It exists because a stock **pointer table** that has to point into our blob
> (here the TKMWL measuring-variable handlers) cannot be written as literal
> bytes without going stale the moment the code moves — the same reason
> `hooks` resolves its target rather than taking a branch word. Each symbol
> must lie inside the blob and be 4-byte aligned, and all of them are added to
> `build.symbols`, so a test can assert what the table now points at.



**2. `onchip_edit` — the second unlock flag.** The old guard refused
0x400000-0x47FFFF outright, with the reason "not fully in our read". That is
true of the **first 16 KB only**: 0x400000-0x403FFF is absent from the dump
(`docs/02_memory_map.md` §2), while **0x404000-0x47FFFF is in the dump** at
file 0x200000+ and is covered by the code descriptor table at file 0x0A0000,
so `checksum.py fix` re-checksums a hook there correctly. But brief B6's fuel
hook (0x42247C, `rksplit`) and D1's task-set-A raster hook (0x432940) both live
there, so the guard is now split:

| Range | Unlocked by |
|---|---|
| 0x000000-0x00FFFF boot block / immobiliser | nothing |
| 0x1C0000-0x1DFFFF stock calibration | `"calibration_edit": true` |
| 0x400000-0x403FFF the 16 KB not in our read | nothing |
| 0x404000-0x47FFFF on-chip flash | `"onchip_edit": true` |
| 0x1CEE20-0x1CEE6F identification block | nothing, ever |

A flag unlocks exactly one range. `onchip_edit` additionally prints a warning
on **every** apply, because the block checksums being right does not prove that
**KESSv2 protocol 179 writes the on-chip flash at all** — that is an open
question for the bench (issue #32). Read the image back and compare before
trusting any on-chip write.

## 2. Build

```
patch.c ──gcc (flags in 03_tooling.md)──▶ patch.o ──ld -T patch.ld──▶ patch.elf
   ──objcopy -O binary──▶ patch.bin      ──nm -n──▶ patch.map (symbols for hooks/logging)
```

Linker script skeleton:

```
MEMORY {
  FLASH (rx) : ORIGIN = 0x00150000, LENGTH = 0x60000   /* free, all 0xFF, checksummed */
  RAM   (rw) : ORIGIN = 0x007FFB00, LENGTH = 0x100     /* C2, issue #23, see section 3 */
}
SECTIONS {
  .text   : { KEEP(*(.text.entry)) *(.text*) *(.rodata*) } > FLASH
  .data   : { *(.data*) } > RAM    /* must be empty: no initialised RAM data */
  .bss    : { *(.bss*) *(COMMON) } > RAM
  /DISCARD/ : { *(.eh_frame) *(.comment) *(.sdata*) *(.sbss*) }
}
ASSERT(SIZEOF(.data) == 0, "no initialised data allowed")
```

Always disassemble `patch.bin` with `objdump -D -b binary -m powerpc:common
-EB --adjust-vma=0x150000` and read it before injecting.

#### Added 2026-09-16 (brief C1, issue #25) — the real build

The skeleton above is now `patches/common/patch.ld`, shared by every patch and
driven by `patches/common/patch.mk`:

```bash
cd patches/ff_counter && make check && make dump && make gen && make apply
```

Differences from the skeleton, each of them load-bearing:

* **No `MEMORY` block and no defaults.** `PATCH_FLASH`, `PATCH_RAM` and
  `PATCH_RAM_SIZE` are required `--defsym`s.
  **Correction, VERIFIED-STATIC 2026-09-16:** the usual
  `PATCH_RAM = DEFINED(PATCH_RAM) ? PATCH_RAM : <default>;` idiom does **not**
  make `--defsym` an override in `ld.lld` 23.1.1. The script assignment wins
  while addresses are computed and `--defsym` only rewrites the symbol table
  afterwards, so the blob is linked at the default while the ELF claims
  otherwise — a silent wrong answer. `patches/common/patch.ld` therefore
  defines no defaults and an undefined `PATCH_*` fails the link.
* `.rodata` is its own output section, and `.sdata`/`.srodata` are declared
  only so that `ASSERT(SIZEOF(…) == 0)` can fail the link if they are not empty.
* `/DISCARD/` must **not** contain `.got`/`.got2`/`.plt` (lld segfaults,
  `03_tooling.md` §3.1), so `make check` compares the blob size with the
  linker's `__patch_flash_size` instead — an orphan section in the binary is
  caught by arithmetic rather than by hope.
* `.bss` is `(NOLOAD)` and starts with `KEEP(*(.bss.patch_state))`, so a
  patch's documented state block is at exactly `PATCH_RAM`.
* `llvm-objdump` has no `-b binary`; use `make dump`
  (`tools/blobdis.py --addr … --check-sda`).

Trampolines come from `patches/common/hooks.S` (`HOOK_TAIL`, `HOOK_FULL`),
stock addresses from the generated `patches/common/med9_stock.h`, integer types
from `patches/common/types.h`. `patches/examples/hello_patch/` is the template
to copy.

> **2026-09-17 (integration, wave E pair 2).** `patches/common/patch.mk` now makes every object depend on every header, `hooks.S`, `patch.mk` and `patch.json`. Before that, a worktree whose `build/` held objects from an earlier `ff_state.h` produced a blob that disagreed with the committed `patch.json` in six `cmplwi` words (the state-block length check in handlers whose `.c` files had not changed). `make clean` was the workaround; the dependency is the fix. `tests/test_patch_framework.py::test_patch_json_still_matches_a_fresh_build` is the test that caught it.

## 3. Placement policy

| Resource | Where | Notes |
|---|---|---|
| Code and constants | 0x150000-0x1AFFFF (external flash, low alias; also reachable as 0x550000+) | 384 KB of 0xFF inside the 64 KB checksum blocks 0x150000.. 0x1AFFFF; recompute checksums |
| Extra code if needed | 0x144954-0x14FFFF | tail of block 0x140000-0x14FFFF |
| New calibration | 0x5E2510-0x5EFFFF | inside calibration block 0x5E0000-0x5EFFFF; addressed through the high alias like the rest of the calibration |
| Never | 0x000000-0x00FFFF (boot, immobiliser pairing 0x6C00), 0x1C0000-0x1DFFFF (stock calibration, except deliberate map edits), 0x400000-0x403FFF (the 16 KB of on-chip flash that is not in our read) | 0x404000-0x47FFFF **is** in our read and is checksummed; it needs `"onchip_edit": true` per change (see §1, 2026-09-16) |
| **RAM** | **0x7FFB00-0x7FFBFF (256 B)**, inside the reference-free internal-SRAM region 0x7FF770-0x7FFFEB | VERIFIED-STATIC that no instruction in the image names any byte of 0x7FF770-0x7FFFEB; **dynamic confirmation pending #23**. Address it absolutely (`lis`/`addi`), never through r13. Not cleared at cold start, so the patch needs a magic + checksum header. See below. |

> **2026-09-17 (integration, after brief E6).** `tools/patch_apply.py` also refuses 0x010000-0x01FFFF and 0x080000-0x09FFFF, with no unlock flag: the firmware's own OBD programming service refuses both (`re/findings/flash_programming.md` §3), so a change there could only be written by BDM. Every range `patches/ff_fuel` touches today lies inside the whitelist that service enforces.

Branch reach: `b/bl` have ±32 MB range, so any placement is reachable with a
single instruction. Code at 0x15xxxx addresses calibration with
`lis 0x5E`, like the stock code.

> **2026-09-16 (C2, issue #23) — the RAM row is decided (static half).**
> Full derivation, evidence and the ranked alternatives:
> `re/findings/ram.md`; the map is `re/ram_map.csv`
> (`python3 tools/ram_survey.py data/passat_azx_ori.bin --csv re/ram_map.csv`).
>
> **Use `PATCH_RAM = 0x7FFB00`, `PATCH_RAM_SIZE = 0x100`** (brief C1's linker
> script and `patches/common/`). The block is in the middle of
> **0x7FF770-0x7FFFEB**, 2,172 bytes that carry no r13 displacement, no
> absolute `lis`+D-form, no pointer word in either flash region and no
> measuring-variable cell, and that lie **above** the task stack
> (0x7FF3C0-0x7FF76F, which grows down). Rules that come with it:
>
> * **Address it absolutely.** `tools/blobdis.py --check-sda` fails on any
>   reference to r2 or r13, base register included, so use
>   `lis r11,0x80 ; addi r11,r11,-0x500`.
> * **Initialise it.** The cold start does not fill 0x7FF770-0x7FFFEB, so the
>   contents are undefined at power-on. Put a magic word, a length and a
>   checksum at the head of the block and re-initialise on a mismatch. The
>   linker script's `ASSERT(SIZEOF(.data) == 0)` stays: there is no
>   initialised-data image for any RAM the patch uses.
> * **Do not rely on retention.** Persist the ethanol estimate in the SPI
>   EEPROM (`re/findings/eeprom.md` section 5, block 8 payload offset +0).
>
> The `MEMORY` block of the skeleton in section 2 spells the address out for
> readability. The real script, `patches/common/patch.ld` (brief C1), takes
> `PATCH_RAM` / `PATCH_RAM_SIZE` from the build and deliberately has **no**
> defaults, so a missing value fails the link instead of silently placing the
> block somewhere else. Integrator: keep the two in step, and do not
> reintroduce an `X = DEFINED(X) ? X : default;` idiom — under `ld.lld` the
> script assignment wins while addresses are computed and `--defsym` only
> rewrites the symbol table afterwards, so the default would be linked in
> silently (C1, 2026-09-16).
>
> **Two placements that the earlier version of this table suggested are now
> refuted (VERIFIED-STATIC):**
>
> * *"the top of external SRAM, 0x8057xx-0x807FFF, no static references"* —
>   0x804800-0x808687 is where `FUN_0008A12C` copies flash 0x081A00-0x085887
>   (0x3E88 B) during a KWP programming session **and then executes it**
>   (`bl 0x806EA0` at 0x0861B0). A patch writing there mid-session would
>   corrupt the running flash driver. The tail also wraps: 0x808000-0x808687
>   aliases onto 0x800000-0x800687 on a 32 KB CS1 part. And 0x805784 is the
>   base of a live RAM dispatch table (0x1C-byte entries, function pointer at
>   +0x18, called at 0x082C00).
> * *"unused r13 gaps"* — every reference-free gap larger than 128 bytes
>   inside the used `.bss` turned out to be the body of an array or buffer
>   whose base is the last referenced byte before it
>   (`ram_survey.py --indexed`). Do not take a gap in the map at face value.
>
> The external SRAM is **not** a retention area: `ram_clear_block` (0x06D8F8)
> and `app_init` (0x04CCD4) zero 0x800004-0x80498F at every cold start. Only
> 0x800000-0x800003 and the programming-copy area survive a reset.

## 4. Hook techniques

1. **Call redirect**: replace an existing `bl target` with `bl ff_trampoline`;
   the trampoline saves what it needs, runs our code, then tail-calls the
   original target (or calls it and returns). Cheapest and easiest to reason
   about; use it for the periodic-task hook.
2. **Table pointer replacement**: point a dispatch-table entry (KWP service
   table at 0x2B870, CAN slot handlers, measuring-block tables) at our
   function; the community KWP and mapswitch patches work this way.
3. **Instruction patch**: change a constant or a load/store target in place
   (e.g. a `bl` to a map-lookup wrapper that applies the factor). Use only
   when 1 and 2 are impossible; document the original instruction.
4. **Data-only patches**: calibration edits and our new calibration block.

Register discipline in trampolines: preserve r0, r3-r12, CR, LR, CTR and XER
as the hooked site expects (EABI volatile set); never touch r1 alignment,
r2, r13, r14-r31 unless saved; no FP registers.

Both trampolines are written once, in `patches/common/hooks.S` (2026-09-16,
brief C1): `HOOK_FULL` saves that whole set in an 80-byte frame, and
`HOOK_TAIL` saves only LR in a 16-byte frame for a site where the volatile set
is provably dead — which is the case between two argument-less `bl` in a flat
ERCOSEK raster task (`re/findings/scheduler.md` §7). Both end by
tail-branching to the original target, so the stock call still happens and
exactly one flash word changes.

> **Added 2026-09-22 (integration after brief F1, issue #27).** "Exactly one
> flash word" holds *per hook*, not per patch. A patch may instantiate one
> trampoline per ERCOSEK task set: `patches/ff_counter` now hooks the 10 ms
> raster of both sets (0x432940 in set A, 0x12067C in set B) exactly as
> `patches/ff_fuel` does, so Flash 1 changes two hook words, and §3's on-chip
> row (0x404000-0x47FFFF, `"onchip_edit": true`, read-back required) applies
> to `ff_counter` as well, not only to `ff_fuel`. `make HOOKS=external` still
> builds the single-word set-B patch (`patches/ff_counter/README.md`).

The tail branch is `ba` (AA=1), not `b`. **VERIFIED-STATIC 2026-09-16:** the
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
   `--json work/diff.json` to keep the report with the build. (Added
   2026-09-15, issue #24; it replaces the manual `cmp -l` reading and also
   checks the `old`/`new` bytes and `base_sha256` from `patch.json`.)
3. Identification block 0x1CEE20-0x1CEE6F unchanged.
4. Emulator unit test of the patch code passes (`emu/`, `emu/README.md`).
5. Disassembly of every hook site shows the intended instruction and target.
6. For bench: expected log lines written down before flashing.

#### The apply step (added 2026-09-16, brief C1, issue #25)

It exists now, and it is the only place an image is ever modified:

```bash
python3 tools/patch_apply.py data/passat_azx_ori.bin patches/ff_counter \
        -o work/ff_counter.bin            # --dry-run, --json
```

It never touches its input, never writes to `data/`, and writes **nothing at
all** unless every one of these passes:

1. the stock file's SHA-256 matches `base_sha256`;
2. no change lands in a forbidden region — 0x000000-0x00FFFF and
   0x400000-0x403FFF never, 0x1C0000-0x1DFFFF only if the change carries
   `"calibration_edit": true`, 0x404000-0x47FFFF only with
   `"onchip_edit": true` (added 2026-09-16, §1). The check folds every CPU alias to one
   canonical address first, so the calibration cannot be reached through
   0x5Cxxxx to get around it;
3. every change's `old` bytes are really there (so a patched image is refused,
   and so is the wrong base image);
4. `checksum.fix` then `checksum.verify` -> ALL OK (65 blocks);
5. the identification block 0x1CEE20-0x1CEE6F is byte-identical — this one
   cannot be unlocked by any flag;
6. `bindiff.diff(stock, patched, patch_json)` returns `report["ok"]`, i.e. every
   changed byte is a listed change or a descriptor word.

Outputs next to `-o`: `<name>.bin`, `<name>.diff.json` (the bindiff report kept
with the build) and `<name>.sha256`. A patch whose `ram_status` is not
`verified` produces a loud do-not-flash warning; that is the state
`patches/ff_counter` is in until the RAM survey (#23) lands.

## 6. Flash and roll back

Write with KESSv2 (protocol 179) from the Windows machine following the
checklist in `04_re_guidelines.md` section 6. KESS applies its own checksum
correction; because our file already verifies, its correction must be a
no-op. Read back after writing and compare; if the read-back differs from
what we wrote outside the descriptors, stop and investigate (that would mean
a check we do not know about). Roll back by writing the original read.

> **2026-09-17 (E6, blocker of #26 #27 #28 #32) — what the ECU's own route
> can write, and what to check on the first flash. VERIFIED-STATIC from
> `data/passat_azx_ori.bin`; see `re/findings/flash_programming.md`.**
>
> KESS flashes over OBD, so it drives *this firmware's* programming service.
> That service hard-codes the address ranges it accepts
> (`kwp_download_range_allowed`, 0x0889C8 — an **exact** start/end match, else
> NRC 0x42):
>
> | start | end | what |
> |---|---|---|
> | 0x020000 | 0x07FFFF | application code, part 1 |
> | 0x0A0000 | 0x1BFFFF | application code, part 2 |
> | **0x404000** | **0x47FFFF** | **on-chip flash — the whole array except small block 0** |
> | 0x080000 | 0x09FFFF | alias; the handler rewrites it to 0x1C0000-0x1DFFFF |
> | 0x1C0000 / 0x1E0000 | 0x1DFFFF / 0x1FFFFF / 0x1FFFFF | calibration, variant-gated |
>
> **So the on-chip hook words `patches/ff_fuel` uses (0x42247C, 0x432940,
> 0x41D40C, 0x41A680, 0x41A808, 0x431384) are inside a range the ECU itself
> can erase and program.** `"onchip_edit": true` stays a loud warning in
> `patch_apply.py`, but the open question is now only whether KESSv2 *offers*
> that range, not whether the ECU can take it — and the read-back below
> answers it.
>
> *Corrected 2026-09-23 (brief G2): the list above has six words. E5's rail
> hook **0x45845C** is on-chip as well, so `patches/ff_fuel` has **seven
> on-chip hook words out of eight**. The source of truth is the hook table in
> `patches/ff_fuel/README.md` ("The eight hook sites"). 0x45845C is inside
> 0x404000-0x47FFFF too, so the conclusion stands.*
>
> Two ranges the firmware refuses outright, so no tool driving the OBD route
> can touch them: **0x000000-0x01FFFF** (vectors, boot, the RAM loader image)
> and **0x080000-0x09FFFF** (the resident programming module: the code
> directory, the flash driver, `app_entry_crt0`). `patch_apply.py` already
> refuses 0x000000-0x00FFFF and 0x400000-0x403FFF; **0x010000-0x01FFFF and
> 0x080000-0x09FFFF deserve the same treatment** and are a follow-up.
>
> Read-back checklist for Flash 0, in addition to the bindiff above:
>
> 1. **Read back 0x404000-0x47FFFF** and `bindiff` it. This is the single
>    measurement that settles #32. Stock bytes there = KESS skipped the array.
>
>    *Corrected 2026-09-24 (H4): "Flash 0" in this checklist is the old name
>    for the first `patches/ff_fuel` write. Flash 0 is now the unmodified dump
>    (#26), whose on-chip read-back cannot tell "written" from "stock"; the
>    on-chip question is settled by Flash 1's read-back of 0x432940 (#27) and
>    then the `ff_fuel` flash (#32) — `docs/08` open question 1, `docs/07`
>    §3.4. Items 2-3 apply to every write, Flash 0 included.*
> 2. **Check the halfword at file 0x1E2500 is `5A 5A`.** It is the one
>    integrity marker the firmware acts on: without it the ECU reboots into
>    its flash loader instead of starting the application (recoverable, but it
>    looks like a brick if you do not expect it).
> 3. **Read EEP_CONF block 10** (EEPROM offset 0x260, 0x20 B) before and
>    after: the ECU stamps its identification string and a status word there on
>    every programming session, so a changed block 10 proves the ECU's own
>    route ran. It is inside the 1 KB that `35`/`36` expose at 0x480000
>    (`tools/flash_segments.py --segments`), so no EEPROM clip is needed.
> 4. Erase granularity, if a partial write is ever attempted: **48 KB / 16 KB
>    / 64 KB** on the on-chip array, 8 KB parameter blocks plus 64 KB main
>    blocks on the CS0 part. There is no smaller unit; programming is per
>    32-bit word.
>
> There is **no signature and no boot-time checksum verdict**: the runtime
> CRC-32 over 0x020000-0x1BFFFF / 0x404000-0x47FFFF / 0x5C2E00-0x5FFFFF is
> computed and *reported* (RAM 0x7F9178), never compared. The 65 block sums
> still have to be correct because the *tool* checks them, not the ECU.

> **2026-09-22 (F5, #26/#28, `re/findings/ram_loader.md`) — the recovery path if
> an OBD write is interrupted.** If a calibration write is cut short so the
> `5A5A5A5A` marker at 0x1E2500 is wrong, the ECU does **not** brick: on the next
> power-up it sets the boot magic itself and reboots into the **RAM bootstrap
> loader**, which can reprogram the calibration (and almost everything else, the
> resident programming module included) — but only over its **serial (SCI) line,
> not CAN**. So keep a serial-capable recovery tool on hand, and recognise the
> "reboots into the loader and stays there" symptom as *recoverable*, not a
> brick (`04_re_guidelines.md` §6). An interrupted *application* or *on-chip*
> write usually leaves the OBD `10 85` route intact instead, so re-flash over
> CAN. Only a corruption of the boot body (0x010000-0x01FFFF) or reset stub
> (0x000000-0x001FFF) needs BDM — and neither this route nor the loader can
> write those, so an OBD write can never cause it; treat a BDM tool as insurance
> for that case and for reading 0x400000-0x403FFF, per `01_project_plan.md` §6.

Each patch keeps a baseline log (stock) and a patched log over the same bench
scenario; a script compares the common variables and flags deviations. The E0
equivalence test in `05_flexfuel_design.md` is the same mechanism.

The script is `tools/logcmp.py` (2026-09-15, issue #24); the log CSV format and
the tolerance-file format are defined in `logging/README.md`. Per patch:

```bash
python3 tools/logcmp.py patches/<name>/test/baseline.csv work/run.csv \
        -t patches/<name>/test/tolerance.json --json work/logcmp.json
```

Exit status 1 means a variable moved outside its tolerance. Keep the tolerance
file with the patch, not with the tool: it encodes what that patch is allowed
to change.
