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
  RAM   (rw) : ORIGIN = 0x00806000, LENGTH = 0x1000    /* placeholder until the RAM survey */
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
| RAM | to be decided by the RAM survey; candidates are the top of external SRAM (0x8057xx-0x807FFF, no static references) and unused r13 gaps | a wrong choice corrupts adaptation values or the stack: survey first |

Branch reach: `b/bl` have ±32 MB range, so any placement is reachable with a
single instruction. Code at 0x15xxxx addresses calibration with
`lis 0x5E`, like the stock code.

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
2. `cmp -l stock.bin patched.bin` -> only the ranges in `patch.json` plus the
   descriptor `sum/~sum` words of the affected blocks.
3. Identification block 0x1CEE20-0x1CEE6F unchanged.
4. Emulator unit test of the patch code passes.
5. Disassembly of every hook site shows the intended instruction and target.
6. For bench: expected log lines written down before flashing.

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
