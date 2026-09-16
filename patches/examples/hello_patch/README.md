# hello_patch

The toolchain proof for issue #5 and, since brief C1, the **template patch
directory** for issue #25. It is **not** meant to be flashed; it exists so that
"the cross compiler and the patch framework work" is a command that either
passes or fails.

```bash
cd patches/examples/hello_patch
make check     # build + assert the stock header is current, no r2/r13 use,
               # no small-data section, blob size == linked flash size
make dump      # disassemble the raw blob at its link address
make gen       # regenerate patch.json's `changes` from the blob
make apply     # apply to a copy of the stock dump under work/
make clean
```

## Layout — copy this for a new patch

```
patches/<name>/
    Makefile        NAME := <name>  +  include ../common/patch.mk
    patch.json      metadata, "build" section; `changes` is GENERATED
    src/*.c         patch code
    src/*.S         one HOOK_TAIL / HOOK_FULL per hook (hello has none)
    test/           emulator test inputs, bench procedure, tolerance.json
    README.md       what it does, hooks, RAM, calibration, blob disassembly
```

Everything shared lives in [`patches/common/`](../../common/): `patch.ld`,
`patch.mk`, `hooks.S`, `hooks.h`, `types.h` and the generated `med9_stock.h`.

## What it proves

| Requirement (docs/04_re_guidelines.md §7) | How `make check` / `make dump` proves it |
|---|---|
| Freestanding big-endian PowerPC, no libc, no FP | `-ffreestanding -nostdlib -msoft-float`, link succeeds with no libgcc |
| No small-data sections | `readelf -S` shows no `.sdata/.sbss/.srodata/.sdata2`; `patch.ld` `ASSERT`s the same and fails the link otherwise |
| r2 and r13 never touched | `make check` greps the disassembly; `make dump` re-checks the raw bytes with `tools/blobdis.py --check-sda` |
| No initialised data | `ASSERT(SIZEOF(.data) == 0)`: nothing copies `.data` to RAM on an ECU |
| Nothing unexpected in the blob | `make check` compares the blob size with the linker's `__patch_flash_size` |
| Links at a chosen free-flash address | `.text` at 0x00150000, inside the free area 0x150000-0x1AFFFF |
| State in an explicitly placed RAM block | `hello_state` in `.bss.patch_state`, placed first in `.bss` at `PATCH_RAM` |
| Constants in flash, addressed absolutely | `hello_lookup` reaches its table with `lis 0x15 / addi 0x0048` |
| The stock addresses cannot drift | `make check` runs `tools/gen_stock_header.py --check` |

Recorded output (2026-09-16, LLVM 23.1.1, framework build, `PATCH_RAM = 0x7FFB00`
from `re/findings/ram.md` — until the same day the example used 0x806000):

```
  [ 1] .text      PROGBITS  00150000  000048  AX
  [ 2] .rodata    PROGBITS  00150048  000010  AM
  [ 3] .bss       NOBITS    007ffb00  000004  WA
blob 88 B == linked flash size, .bss 4 B <= 16 B
00150000  3C 60 00 80  lis      r3, 0x80
00150004  38 80 00 00  li       r4, 0
00150008  B0 83 FB 00  sth      r4, -0x500(r3)
0015000C  4E 80 00 20  blr
...
00150034  3C 80 00 15  lis      r4, 0x15
00150038  54 63 08 3C  slwi     r3, r3, 1
0015003C  38 84 00 48  addi     r4, r4, 0x48
00150040  7C 64 1A 2E  lhzx     r3, r4, r3
OK: no reference to r2 or r13
```

`make apply` on a copy of the stock dump: 1 patch range (88 B), 2 descriptor
ranges (6 B), 0 unexpected, `checksum.py verify` ALL OK (65 blocks).
The patch has **no hooks**, so applying it changes no instruction of stock
code — the blob just sits in blank flash.

## Toolchains

Default is LLVM, installed as a plain tarball under `/Users/carlo/toolchains`
(`brew install llvm` cannot work here, see docs/03_tooling.md §3). Override
with `make LLVM_DIR=...`.

devkitPPC is wired up but untested: `make TOOLCHAIN=gcc` uses the flag set
from docs/03_tooling.md §3. Installing it needs an administrator password
(see the same section).

## Placement

```bash
make PATCH_FLASH=0x00160000 PATCH_RAM=0x00806100
```

Placement normally comes from `patch.json`'s `build` section, so the linker and
the patch descriptor cannot disagree; the command line still wins.

> **Corrected 2026-09-16 (brief C1).** Before the framework, the command line
> above did **not** work. `hello.ld` used
> `PATCH_RAM = DEFINED(PATCH_RAM) ? PATCH_RAM : 0x00806000;`, and in `ld.lld`
> that script assignment is evaluated *before* `--defsym` is applied: the blob
> was linked at the script default while the ELF symbol table reported the
> overridden value. It went unnoticed because the defaults and the overrides in
> the recorded examples happened to be the same addresses.
> `patches/common/patch.ld` therefore has **no defaults**: all three `PATCH_*`
> symbols are required and an undefined one fails the link.

Both addresses here are **examples** (`"ram_status": "example"` in
`patch.json`). Before a real patch:

- the flash range must be free and its 64 KiB block re-checksummed —
  `tools/patch_apply.py` does that and refuses the result otherwise;
- the RAM must be proven unused, statically and across ignition cycles
  (issue #23). The highest static reference into the external SRAM recorded so
  far is 0x805784 (docs/02_memory_map.md §3).
