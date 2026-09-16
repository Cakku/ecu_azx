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

- `src/*.c`, `patch.ld`, `Makefile` (or the Python driver) that produce the
  blob and regenerate `patch.json`.
- `test/`: emulator unit test and the bench procedure with expected log lines.
- `README.md`: what it does, hooks used, RAM used, calibration added.

Applying a patch checks the old bytes, writes the new ones, recomputes
checksums, verifies, and writes a diff report. Never edit the binary by hand.

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

## 3. Placement policy

| Resource | Where | Notes |
|---|---|---|
| Code and constants | 0x150000-0x1AFFFF (external flash, low alias; also reachable as 0x550000+) | 384 KB of 0xFF inside the 64 KB checksum blocks 0x150000.. 0x1AFFFF; recompute checksums |
| Extra code if needed | 0x144954-0x14FFFF | tail of block 0x140000-0x14FFFF |
| New calibration | 0x5E2510-0x5EFFFF | inside calibration block 0x5E0000-0x5EFFFF; addressed through the high alias like the rest of the calibration |
| Never | 0x000000-0x00FFFF (boot, immobiliser pairing 0x6C00), 0x1C0000-0x1DFFFF (stock calibration, except deliberate map edits), 0x400000-0x47FFFF (on-chip flash: not fully in our read) | |
| **RAM** | **0x7FFB00-0x7FFBFF (256 B)**, inside the reference-free internal-SRAM region 0x7FF770-0x7FFFEB | VERIFIED-STATIC that no instruction in the image names any byte of 0x7FF770-0x7FFFEB; **dynamic confirmation pending #23**. Address it absolutely (`lis`/`addi`), never through r13. Not cleared at cold start, so the patch needs a magic + checksum header. See below. |

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

The apply step itself (`patches/` and its driver, issue #5 / #23) does not
exist yet. When it does, it must call `bindiff.diff(stock, patched, patch_json)`
and refuse to write a file whose report has `ok == False`; the function returns
`(ranges, report)` so the report can be stored next to the build.

## 6. Flash and roll back

Write with KESSv2 (protocol 179) from the Windows machine following the
checklist in `04_re_guidelines.md` section 6. KESS applies its own checksum
correction; because our file already verifies, its correction must be a
no-op. Read back after writing and compare; if the read-back differs from
what we wrote outside the descriptors, stop and investigate (that would mean
a check we do not know about). Roll back by writing the original read.

## 7. Regression

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
