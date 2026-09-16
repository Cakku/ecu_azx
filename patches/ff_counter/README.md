# ff_counter — Flash 1, the no-op counter patch

Issue **#27**. The first code of ours that runs in the ECU. It proves the hook
point, the RAM allocation, the checksum pipeline and the logger *together*,
while the engine behaves exactly as it did before: `ff_counter_tick()` touches
nothing but its own 8-byte RAM block.

> ## PENDING #23 — do not flash this build
> `PATCH_RAM = 0x807F00` is a **placeholder**. It is above the highest static
> reference into the external SRAM recorded so far (0x805784,
> `docs/02_memory_map.md` §3), but "no static reference" is not "unused": the
> RAM survey of issue #23 / brief **C2** has to confirm it across ignition
> cycles first. `patch.json` carries `"ram_status": "placeholder"` and
> `tools/patch_apply.py` prints a warning on every run.
>
> To adopt C2's answer: change `build.ram` (and `build.ram_size` if the block
> is smaller than 0x40) in `patch.json`, set `"ram_status": "verified"` with the
> evidence in `ram_status_note`, then `make gen && make apply`. Nothing else
> changes — the addresses are not repeated anywhere else in the patch.

```bash
cd patches/ff_counter
make check     # build + assert no r2/r13, no small data, sizes agree
make dump      # disassemble the raw blob (the listing below)
make gen       # regenerate patch.json's `changes` from the blob
make apply     # -> work/ff_counter.bin + .diff.json + .sha256
python3 -m unittest tests.test_patch_framework        # from the repo root
```

## What it does

| | |
|---|---|
| **Hook** | `0x12067C`, one word, `bl 0x11F02C` -> `bl 0x150000` |
| **Task** | `task_100ms` (0x1205A0, TCB 23) — `re/findings/scheduler.md` §8 |
| **Trampoline** | `HOOK_TAIL` (`patches/common/hooks.S`): saves LR only, 16-byte frame |
| **Tail** | `ba 0x11F02C` — the stock leaf still runs, in the same task, in order |
| **RAM** | 8 bytes used of the 0x40-byte block at `PATCH_RAM` |
| **Flash** | 96 bytes at 0x150000 (free area 0x150000-0x1AFFFF, all 0xFF) |
| **Calibration** | none |
| **Stock RAM touched** | none |

### The RAM block

| Offset | Type | Name | Meaning |
|---|---|---|---|
| +0x00 | u32 | `ff_ticks` | activations of `task_100ms` since power-up |
| +0x04 | u16 | `ff_alive` | `0xFC01` once our code has run |
| +0x06 | u16 | `ff_reserved` | always 0 |

The layout is fixed by `struct ff_counter_state` in `.bss.patch_state`, which
`patches/common/patch.ld` places first in `.bss`, so `ff_state == PATCH_RAM`.

Nothing zeroes a patch's `.bss` — there is no startup code and the ECU's own
RAM init does not know the block exists. The first tick after power-up
therefore recognises itself by the missing `0xFC01` and zeroes the counter.
Residual risk: a 1-in-65536 chance that the uninitialised half-word already
reads `0xFC01`, which costs a counter starting from an arbitrary value and
nothing else.

### Why this hook site

`re/findings/scheduler.md` §8 (VERIFIED-STATIC):

```
00120674  4B F9 D9 D9  bl 0x0BE04C
00120678  48 00 03 FD  bl 0x120A74
0012067C  4B FF E9 B1  bl 0x11F02C     <-- hooked
00120680  4B FF F3 2D  bl 0x11F9AC
00120684  4B FF F0 89  bl 0x11F70C
```

* the target `0x11F02C` is a four-instruction leaf that calls nothing;
* both neighbours are argument-less `bl`s, so r3-r12, CR, CTR and XER are dead
  across the site and `HOOK_TAIL` only has to save LR;
* LR is already saved by the task prologue;
* it is a single word, inside checksum block 0x120000-0x12FFFF.

**The period is a HYPOTHESIS.** Only "<= 150 ms" is VERIFIED-STATIC. This flash
is also the measurement: `test/procedure.md` §4 turns the counter's slope into
the answer, which settles the open item in `scheduler.md` §10 and one box of
issue #44.

## Blob disassembly

`make dump` — the raw bytes at the address the CPU will fetch them from, not
the ELF (`tools/blobdis.py`, capstone; `llvm-objdump` has no `-b binary`).
Recorded 2026-09-16, LLVM 23.1.1, `PATCH_FLASH=0x150000`,
`PATCH_RAM=0x807F00`:

```
                                  ; --- ff_counter_hook: HOOK_TAIL trampoline
00150000  94 21 FF F0  stwu     r1, -0x10(r1)   ; 16-byte frame, back chain at 0(r1)
00150004  7C 08 02 A6  mflr     r0
00150008  90 01 00 0C  stw      r0, 0xc(r1)     ; save LR: bl below destroys it
0015000C  48 00 00 15  bl       0x150020        ; ff_counter_tick()
00150010  80 01 00 0C  lwz      r0, 0xc(r1)
00150014  7C 08 03 A6  mtlr     r0              ; LR back -> the leaf's blr returns to the task
00150018  38 21 00 10  addi     r1, r1, 0x10
0015001C  48 11 F0 2E  ba       0x11f02c        ; the stock leaf, AA=1 (see hooks.S)
                                  ; --- ff_counter_tick()
00150020  3C 60 00 80  lis      r3, 0x80        ; r3 = 0x800000
00150024  38 83 7F 00  addi     r4, r3, 0x7f00  ; r4 = PATCH_RAM
00150028  A0 84 00 04  lhz      r4, 4(r4)       ; ff_alive
0015002C  28 04 FC 01  cmplwi   r4, 0xfc01
00150030  41 82 00 20  beq      0x150050        ; already alive -> just increment
00150034  38 80 00 00  li       r4, 0           ; cold start:
00150038  3C A0 00 80  lis      r5, 0x80
0015003C  94 85 7F 00  stwu     r4, 0x7f00(r5)  ; ff_ticks = 0, r5 = PATCH_RAM
00150040  B0 85 00 06  sth      r4, 6(r5)       ; ff_reserved = 0
00150044  3C 80 00 00  lis      r4, 0
00150048  60 84 FC 01  ori      r4, r4, 0xfc01
0015004C  B0 85 00 04  sth      r4, 4(r5)       ; ff_alive = 0xFC01
00150050  80 83 7F 00  lwz      r4, 0x7f00(r3)  ; ff_ticks
00150054  38 84 00 01  addi     r4, r4, 1
00150058  90 83 7F 00  stw      r4, 0x7f00(r3)
0015005C  4E 80 00 20  blr

OK: no reference to r2 or r13
```

96 bytes, no `.rodata`, no branch that depends on engine state: 21 instructions
on a warm tick, 15 of them in `ff_counter_tick`. Worst case equals best case.

### The hook word

```
site   0x12067C
old    4B FF E9 B1   bl 0x11F02C     (LI = -0x1650)
new    48 02 F9 85   bl 0x150000     (LI = +0x2F984)
```

I-form: `0x48000000 | (LI & 0x03FFFFFC) | LK`, `LI = target - site`, AA = 0,
reach +-32 MB. `tools/patch_gen.py` encodes it and checks both the alignment
and the reach; `tests/test_patch_framework.py` pins the resulting word.

The same five words of `task_100ms`, disassembled out of the two images rather
than out of the descriptor — `tools/blobdis.py <image> --file-off 0x120674
--len 0x14` (file offset == CPU address in the low alias):

```
                stock                              work/ff_counter.bin
00120674  4B F9 D9 D9  bl 0xbe04c        00120674  4B F9 D9 D9  bl 0xbe04c
00120678  48 00 03 FD  bl 0x120a74       00120678  48 00 03 FD  bl 0x120a74
0012067C  4B FF E9 B1  bl 0x11f02c  -->  0012067C  48 02 F9 85  bl 0x150000
00120680  4B FF F3 2D  bl 0x11f9ac       00120680  4B FF F3 2D  bl 0x11f9ac
00120684  4B FF F0 89  bl 0x11f70c       00120684  4B FF F0 89  bl 0x11f70c
```

One word, and the leaf it used to call is untouched in the patched image —
`blobdis.py work/ff_counter.bin --file-off 0x11F02C --len 0x10`:

```
0011F02C  39 80 00 00  li   r12, 0
0011F030  99 8D E8 99  stb  r12, -0x1767(r13)      ; RAM 0x7FE889
0011F034  B1 8D 0E 28  sth  r12, 0xe28(r13)        ; RAM 0x800E18
0011F038  4E 80 00 20  blr
```

## Applying it

```
$ make apply
ff_counter: 3 patch range(s) (99 B), 4 descriptor range(s) (10 B), 0 unexpected
checksums: ALL OK (65 blocks); identification block unchanged
sha256: 84b916b26cd8ac9b978f22a23a30076ec2fa5b0937ecc1df33185f245b239666
WARNING: ff_counter: "ram_status": "placeholder" ...
```

Three patch ranges rather than two because two of the blob's own bytes are
0xFF and therefore did not change. The four descriptor ranges are the sum/~sum
words of blocks 0x120000-0x12FFFF and 0x150000-0x15FFFF.

The sha256 above is of a build with the **placeholder** RAM address; it changes
when C2's block replaces it.

## Tests

`tests/test_patch_framework.py`, under `Med9Emu` on the patched image:

* calling the trampoline with a seeded RAM image increments `ff_ticks` by
  exactly 1 and leaves `ff_alive` alone;
* `0x7FE889` and `0x800E18` come back cleared — the stock leaf really ran;
* `r1` is restored to 0x7FEFFC and the only RAM that moves is the 8-byte
  counter block plus `HOOK_TAIL`'s own 16-byte frame below the entry `r1`;
* a cold start (RAM all zero) yields `ff_ticks == 1`, `ff_alive == 0xFC01`;
* running `0x12067C..0x120680` on the stock and the patched image differs only
  in those same bytes — 5 instructions stock, 29 patched.

## Bench

`test/procedure.md` — what to read, the expected log lines, the period decision
table, and the rollback. `test/tolerance.json` — the limits for
`tools/logcmp.py` over the stock variables of issue #44.
