# hello_patch

The toolchain proof for issue #5. It is **not** meant to be flashed; it exists
so that "the cross compiler works" is a command that either passes or fails.

```bash
cd patches/examples/hello_patch
make check     # build + assert no r2/r13 use and no small-data section
make dump      # disassemble the raw blob at its link address
make clean
```

## What it proves

| Requirement (docs/04_re_guidelines.md §7) | How `make check` / `make dump` proves it |
|---|---|
| Freestanding big-endian PowerPC, no libc, no FP | `-ffreestanding -nostdlib -msoft-float`, link succeeds with no libgcc |
| No small-data sections | `readelf -S` shows no `.sdata/.sbss/.srodata/.sdata2`; the linker script `ASSERT`s the same and fails the link otherwise |
| r2 and r13 never touched | `make check` greps the disassembly; `make dump` re-checks the raw bytes with `tools/blobdis.py --check-sda` |
| Links at a chosen free-flash address | `.text` at 0x00145000, past the end-of-code marker 0x144950 |
| State in an explicitly placed RAM block | `.bss` at 0x00806000 (example address, see below) |
| Constants in flash, addressed absolutely | `hello_lookup` reaches its table with `lis 0x14 / addi 0x5048` |

Recorded output (2026-09-15, LLVM 23.1.1):

```
  [ 1] .text      PROGBITS  00145000  000048  AX
  [ 2] .rodata    PROGBITS  00145048  000010  AM
  [ 3] .bss       NOBITS    00806000  000000  WA
blob size 88 bytes
00145000  3C 60 00 80  lis      r3, 0x80
00145004  38 80 00 00  li       r4, 0
00145008  B0 83 60 00  sth      r4, 0x6000(r3)
0014500C  4E 80 00 20  blr
...
00145034  3C 80 00 14  lis      r4, 0x14
00145038  54 63 08 3C  slwi     r3, r3, 1
0014503C  38 84 50 48  addi     r4, r4, 0x5048
00145040  7C 64 1A 2E  lhzx     r3, r4, r3
OK: no reference to r2 or r13
```

## Toolchains

Default is LLVM, installed as a plain tarball under `/Users/carlo/toolchains`
(`brew install llvm` cannot work here, see docs/03_tooling.md §3). Override
with `make LLVM_DIR=...`.

devkitPPC is wired up but untested: `make TOOLCHAIN=gcc` uses the flag set
from docs/03_tooling.md §3. Installing it needs an administrator password
(see the same section).

## Placement

```bash
make PATCH_FLASH=0x00146000 PATCH_RAM=0x00806100
```

Both addresses are **examples**. Before a real patch:

- the flash range must be free and its 64 KiB block re-checksummed with
  `tools/checksum.py fix` (docs/06_patch_pipeline.md);
- the RAM must be proven unused, statically and across ignition cycles. The
  highest static reference into the external SRAM recorded so far is
  0x805784 (docs/02_memory_map.md §3).
