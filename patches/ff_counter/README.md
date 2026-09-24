# ff_counter — Flash 1, the no-op counter patch

Issue **#27**. The first code of ours that runs in the ECU. It proves the hook
point, the RAM allocation, the checksum pipeline and the logger *together*,
while the engine behaves exactly as it did before: the two tick functions touch
nothing but their own 8-byte RAM block.

> ## Rewritten 2026-09-22 (brief F1): it hooks **both** task sets
> Brief C4 showed that the hooked task is a **10 ms** raster, and brief E1
> then showed — statically, `re/findings/scheduler.md` §11.8 — that **task set
> A is the live set**: `prsoll` and `zwstt` are produced only by set-A
> processes and the engine cannot run without them. C1's single hook at
> 0x12067C is in **set B**, so Flash 1 as it stood would have shown a counter
> that never moves.
>
> It now hooks the 10 ms raster of **both** sets, one word each, and records
> which one ran in a new `ff_src_seen` byte. That turns Flash 1 into the
> experiment that answers two questions from one 60-second log: the dynamic
> confirmation of §11.8, and — together with the Flash 0 read-back — whether
> the OBD route writes the on-chip flash at all (#32).

> ## RAM block 0x7FFB00 — `ram_status: static`; do not flash before the runtime half of #23
> `PATCH_RAM = 0x7FFB00` (0x100 B) is the block `re/findings/ram.md` §8.1
> recommends: VERIFIED-STATIC that no instruction in the image references any
> byte of 0x7FF770-0x7FFFEB, above the task stack 0x7FF3C0-0x7FF76F, outside
> the KWP programming copy 0x804800-0x808687 and outside the protected window
> 0x7F9E3C-0x7FA47F (so the logger can read it). It is **not** filled at cold
> start, which is what the `ff_alive` marker is for. The runtime snapshots of
> #23 are still pending, so `tools/patch_apply.py` warns on every run.
>
> History: brief C1 was written against the placeholder 0x807F00. Brief C2 then
> showed that `FUN_0008A12C` copies 0x3E88 bytes from flash 0x081A00 to RAM
> **0x804800** during a KWP programming session and the ECU *executes* that copy
> (`bl 0x806EA0` at 0x0861B0); 0x804800 + 0x3E88 = 0x808688, so 0x807F00 sat
> 0x3700 bytes inside the destination. C1 confirmed it from the dump
> (VERIFIED-STATIC, 2026-09-16); the address was changed at integration the
> same day. C2 also refutes the old "top of external SRAM is free" note in
> `docs/06_patch_pipeline.md` §3: 0x805784 is a live RAM dispatch table.
>
> C2's second rule — *initialise the block, its contents are undefined at
> power-on* — is already met: the tick has always assumed nothing zeroes its
> `.bss` and detects its own cold start (see below). If a stronger marker than
> one u16 is wanted, widening `ff_alive` to u32 is a two-line change.

```bash
cd patches/ff_counter
make check                  # build + assert no r2/r13, no small data, sizes agree
make dump                   # disassemble the raw blob (the listing below)
make gen                    # regenerate patch.json's `changes` from the blob
make apply                  # -> work/ff_counter.bin + .diff.json + .sha256
make HOOKS=external apply    # the other variant -> work/external/ff_counter.bin
./../../.venv/bin/python3 -m unittest tests.test_ff_counter_patch \
    tests.test_patch_framework                      # from the repo root
```

## Two build variants

| `make …` | Hook words | Descriptor | Blob | Output |
|---|---|---|---|---|
| **(default)** `HOOKS=both` | **0x432940** (set A, on-chip) **and** 0x12067C (set B, external) | `patch.json` | 224 B | `work/ff_counter.bin` |
| `HOOKS=external` | 0x12067C alone | `patch.external.json` | 96 B | `work/external/ff_counter.bin` |

Each variant has its own build directory (`build/both/`, `build/external/`), so
neither can ever be linked from the other's objects. `patch.json` — the default
— is the descriptor meant for Flash 1.

**`HOOKS=external` is the pre-F1 patch byte for byte**: blob sha256
`4645f45b1eeef4795aceab574938abcb51dbef6c70e36b074ca9d0ebfb4d95f5` (96 B),
patched image `9ecde359ef91eabfa332ad80c3ad5667f5d5535ea72d0a0306da78a59ab375e2`,
and a `changes` list identical to the one that was on `main` before this brief.
`tests/test_ff_counter_patch.py` pins all three. It is worth building in **one**
case only — `re/findings/scheduler.md` §11.8 turns out to be wrong (set B is
live) **and** the on-chip array cannot be written. In every other case it is
strictly worse, and if §11.8 is right it never executes at all.

There is deliberately **no set-A-only variant**: the external word costs one
flash word and nothing at runtime, and it is the only hook that survives a
skipped on-chip write.

> ### If the on-chip array cannot be written, no counter patch can run
> `re/findings/scheduler.md` §11.8 (set A is live) and
> `re/findings/flash_programming.md` §7.3 (task set A lives entirely in the
> on-chip flash; the set-A raster hook has **no** external-flash alternative)
> together say something blunt: if the Flash 0 read-back shows KESSv2 skipped
> 0x404000-0x47FFFF, then the set-A word was never written, **and** the set-B
> word — which was written — sits in a task set that does not run. **No build
> of this patch can then produce a moving counter**, and rebuilding or moving
> the hook cannot help, because there is nowhere in external flash to move it
> to. The fallbacks are the ladder in `flash_programming.md` §7.3: a BDM/K-TAG
> write, or a tool that drives the firmware's own programming service for that
> range. `test/procedure.md` §4 row **D** is this outcome, written out in full.
>
> The same sentence applies to six of `patches/ff_fuel`'s eight hooks.

## What it does

| | |
|---|---|
| **Hooks** | two, one word each — see the table below |
| **Trampoline** | `HOOK_TAIL` (`patches/common/hooks.S`) for both: saves LR only, 16-byte frame |
| **RAM** | 8 bytes used of the 0x100-byte block at `PATCH_RAM` = 0x7FFB00 |
| **Flash** | 224 bytes at 0x150000 (free area 0x150000-0x1AFFFF, all 0xFF; `ff_fuel` starts at 0x152000, so the two never overlap) |
| **Calibration** | none |
| **Stock RAM touched** | none |

### The two hook sites

| Site | On-chip | Old word | New word | Insns (warm / cold) | Task | Tail | External-flash alternative |
|---|---|---|---|---|---|---|---|
| **0x432940** | **yes** | `4B C8 B0 A5` `bl 0x0BD9E4` | `4B D1 D6 C1` `bl 0x150000` | **21 / 29** | `task_100ms_int` 0x4328E4, id 19, **10 ms, task set A** | `ba 0x0BD9E4` (an empty leaf) | **none.** Task set A lives entirely in the on-chip flash (`flash_programming.md` §7.3). The set-B row below *is* the fallback, at the cost of losing set A — and if §11.8 is right, that fallback never runs |
| **0x12067C** | no | `4B FF E9 B1` `bl 0x11F02C` | `48 02 F9 A5` `bl 0x150020` | **24 / 32** | `task_100ms` 0x1205A0, TCB 23, id 32, **10 ms, task set B** | `ba 0x11F02C` (`clr_ram_7FE889_800E18`) | — (it is itself the external word) |

**One of the two is in the on-chip flash** 0x404000-0x47FFFF, so it carries
`"onchip_edit": true` and `make apply` prints **one** on-chip warning for the
default build and none for `HOOKS=external` (docs/06 §1, brief D1). The
instruction counts are measured in the emulator
(`tests/test_ff_counter_patch.py::test_the_cost_of_one_activation`) and include
the trampoline and the tail target: 8 for `HOOK_TAIL`, 12 (warm) or 20 (cold)
for the tick, plus 1 for set A's empty leaf and 4 for set B's.

`patches/ff_fuel` hooks exactly the same two words with the same argument;
these stubs are the cheap half of that patch's D1 pair, and the two patches are
never co-flashed (they share the RAM block).

### Why these two sites

`re/findings/scheduler.md` §8 and §8.1 (both VERIFIED-STATIC):

```
0043293C  4B C8 B1 29  bl 0x0BDA64            00120674  4B F9 D9 D9  bl 0x0BE04C
00432940  4B C8 B0 A5  bl 0x0BD9E4   <--      00120678  48 00 03 FD  bl 0x120A74
00432944  4B FF F8 99  bl 0x4321DC            0012067C  4B FF E9 B1  bl 0x11F02C  <--
                                              00120680  4B FF F3 2D  bl 0x11F9AC
000BD9E0  4E 80 00 20  blr   ; prev fn        00120684  4B FF F0 89  bl 0x11F70C
000BD9E4  4E 80 00 20  blr   ; the whole fn
000BD9E8  94 21 FF F8  stwu r1,-8(r1)
```

* **Both targets are leaves.** 0x0BD9E4 is an *empty* function — one `blr` —
  and `tools/sda_xref.py data/passat_azx_ori.bin --code 0xBD9E4` finds exactly
  one caller in the whole image, 0x432940 itself, so the stock work the tail
  branch preserves is literally nothing. 0x11F02C is four instructions
  (`li r12,0` / `stb` 0x7FE889 / `sth` 0x800E18 / `blr`) and calls nothing.
* **The dead-register set is the same at both words: r0, r3-r12, CR, CTR and
  XER.** Both task bodies are the flat list of argument-less `bl`s that
  §7 describes; nothing sets a register before either word and nothing consumes
  a return value after it, and the entry blocks of both neighbours' targets
  (0x0BDA64 / 0x4321DC and 0x0BE04C / 0x11F9AC) write r3-r12 before they read
  them. Only **LR** has to be saved, which is exactly `HOOK_TAIL`'s 16-byte
  frame; r1, r2, r13 and r14-r31 are never touched.
* **Both are unconditional and at the top level** of their task body, so each
  runs exactly once per 10 ms activation of its set.
* Each is a single word: 0x12067C is inside checksum block 0x120000-0x12FFFF
  and 0x432940 inside the on-chip code descriptor #49, and `checksum.py fix`
  covers both.

**The period is settled at 10 ms** (brief C4, issue #44, 2026-09-16;
`scheduler.md` §11, VERIFIED-STATIC from the activation chain and
VERIFIED-DYNAMIC from `emu/os_clock.py`), so the counter runs at **100
counts/s** — about 6,000 per minute — whichever set is live. If both ever ran,
it would be 200/s with `ff_src_seen` = 3, which `test/procedure.md` §4 reads as
its own outcome rather than as a fault.

### The RAM block

| Offset | Type | Name | Meaning |
|---|---|---|---|
| +0x00 | u32 | `ff_ticks` | activations of the live 10 ms raster since power-up |
| +0x04 | u16 | `ff_alive` | `0xFC01` once our code has run |
| +0x06 | u8 | `ff_src_seen` | which hook ran: **1** = set A, **2** = set B, **3** = both |
| +0x07 | u8 | `ff_reserved` | always 0 |

The layout is fixed by `struct ff_counter_state` in `.bss.patch_state`, which
`patches/common/patch.ld` places first in `.bss`, so `ff_state == PATCH_RAM`.
The `HOOKS=external` build keeps the pre-F1 shape — a u16 `ff_reserved` at
+0x06 — because there is only one source to record; that is part of what makes
it byte-identical.

Nothing zeroes a patch's `.bss` — there is no startup code and the ECU's own
RAM init does not know the block exists. The first tick after power-up
therefore recognises itself by the missing `0xFC01` and zeroes the counter
**and** `ff_src_seen`, so a stale source byte can never survive a power cycle.
Residual risk: a 1-in-65536 chance that the uninitialised half-word already
reads `0xFC01`, which costs a counter starting from an arbitrary value and
nothing else.

**No arbitration, on purpose.** `patches/ff_fuel` needs an owner byte because a
double tick would double its filter rate; this patch computes nothing, so a
tick from the "wrong" set costs one count and the truth is in `ff_src_seen`
either way.

## Blob disassembly

`make dump` — the raw bytes at the address the CPU will fetch them from, not
the ELF (`tools/blobdis.py`, capstone; `llvm-objdump` has no `-b binary`).
Recorded 2026-09-22, LLVM 23.1.1, `PATCH_FLASH=0x150000`,
`PATCH_RAM=0x7FFB00`, `HOOKS=both`:

```
                                  ; --- ff_counter_hook_a: set A, 0x432940
00150000  94 21 FF F0  stwu     r1, -0x10(r1)   ; 16-byte frame, back chain at 0(r1)
00150004  7C 08 02 A6  mflr     r0
00150008  90 01 00 0C  stw      r0, 0xc(r1)     ; save LR (return into 0x4328E4)
0015000C  48 00 00 35  bl       0x150040        ; ff_counter_tick_a
00150010  80 01 00 0C  lwz      r0, 0xc(r1)
00150014  7C 08 03 A6  mtlr     r0
00150018  38 21 00 10  addi     r1, r1, 0x10
0015001C  48 0B D9 E6  ba       0xbd9e4         ; nop_leaf_bd9e4, one blr
                                  ; --- ff_counter_hook_b: set B, 0x12067C
00150020  94 21 FF F0  stwu     r1, -0x10(r1)
00150024  7C 08 02 A6  mflr     r0
00150028  90 01 00 0C  stw      r0, 0xc(r1)
0015002C  48 00 00 65  bl       0x150090        ; ff_counter_tick_b
00150030  80 01 00 0C  lwz      r0, 0xc(r1)
00150034  7C 08 03 A6  mtlr     r0
00150038  38 21 00 10  addi     r1, r1, 0x10
0015003C  48 11 F0 2E  ba       0x11f02c        ; the stock leaf clr_ram_7FE889_800E18
                                  ; --- ff_counter_tick_a
00150040  3C 60 00 80  lis      r3, 0x80        ; 0x800000 - 0x500 = 0x7FFB00 = PATCH_RAM
00150044  38 83 FB 00  addi     r4, r3, -0x500
00150048  A0 A4 00 04  lhz      r5, 4(r4)       ; ff_alive
0015004C  28 05 FC 01  cmplwi   r5, 0xfc01
00150050  41 82 00 24  beq      0x150074        ; warm tick: skip the init
00150054  38 A0 00 00  li       r5, 0
00150058  3C C0 00 80  lis      r6, 0x80
0015005C  94 A6 FB 00  stwu     r5, -0x500(r6)  ; ff_ticks = 0 (r6 -> PATCH_RAM)
00150060  98 A6 00 06  stb      r5, 6(r6)       ; ff_src_seen = 0
00150064  98 A6 00 07  stb      r5, 7(r6)       ; ff_reserved = 0
00150068  3C A0 00 00  lis      r5, 0
0015006C  60 A5 FC 01  ori      r5, r5, 0xfc01
00150070  B0 A6 00 04  sth      r5, 4(r6)       ; ff_alive = 0xFC01
00150074  88 A4 00 06  lbz      r5, 6(r4)       ; ff_src_seen |= 1  (task set A)
00150078  60 A5 00 01  ori      r5, r5, 1
0015007C  98 A4 00 06  stb      r5, 6(r4)
00150080  80 83 FB 00  lwz      r4, -0x500(r3)  ; ff_ticks++
00150084  38 84 00 01  addi     r4, r4, 1
00150088  90 83 FB 00  stw      r4, -0x500(r3)
0015008C  4E 80 00 20  blr
                                  ; --- ff_counter_tick_b: identical except
00150090  ...                     ;     for `ori r5, r5, 2` at 0x1500C8
001500DC  4E 80 00 20  blr
```

Absolute addressing throughout (`lis 0x80` / `-0x500`), no r2 or r13
(`--check-sda` OK). 224 bytes, no `.rodata`, no branch that depends on engine
state: the two ticks are straight-line code, so the worst case equals the best
case. The compiler duplicated the shared tick body into both entry points at
`-Os`; that costs 80 bytes of the 384 KB free area and buys two independent
paths, so a mistake in one cannot silently apply to the other.

### The hook words

```
site   0x432940                        site   0x12067C
old    4B C8 B0 A5   bl 0x0BD9E4       old    4B FF E9 B1   bl 0x11F02C
new    4B D1 D6 C1   bl 0x150000       new    48 02 F9 A5   bl 0x150020
```

I-form: `0x48000000 | (LI & 0x03FFFFFC) | LK`, `LI = target - site`, AA = 0,
reach ±32 MB. `tools/patch_gen.py` encodes both and checks the alignment and
the reach; `tests/test_ff_counter_patch.py` pins the resulting words against
the symbols rather than against a literal, so a blob that moves cannot make the
test pass by accident.

The five words of `task_100ms`, disassembled out of the two images rather than
out of the descriptor — `tools/blobdis.py <image> --file-off 0x120674
--len 0x14` (file offset == CPU address in the low alias):

```
                stock                              work/ff_counter.bin
00120674  4B F9 D9 D9  bl 0xbe04c        00120674  4B F9 D9 D9  bl 0xbe04c
00120678  48 00 03 FD  bl 0x120a74       00120678  48 00 03 FD  bl 0x120a74
0012067C  4B FF E9 B1  bl 0x11f02c  -->  0012067C  48 02 F9 A5  bl 0x150020
00120680  4B FF F3 2D  bl 0x11f9ac       00120680  4B FF F3 2D  bl 0x11f9ac
00120684  4B FF F0 89  bl 0x11f70c       00120684  4B FF F0 89  bl 0x11f70c
```

and the set-A site, `--addr 0x432938 --file-off 0x22E938 --len 0x10`:

```
00432938  4B C8 AC 71  bl 0xbd5a8        00432938  4B C8 AC 71  bl 0xbd5a8
0043293C  4B C8 B1 29  bl 0xbda64        0043293C  4B C8 B1 29  bl 0xbda64
00432940  4B C8 B0 A5  bl 0xbd9e4   -->  00432940  4B D1 D6 C1  bl 0x150000
00432944  4B FF F8 99  bl 0x4321dc       00432944  4B FF F8 99  bl 0x4321dc
```

Both original targets are untouched in the patched image.

## Applying it

```
$ make apply
ff_counter: 5 patch range(s) (229 B), 6 descriptor range(s) (16 B), 0 unexpected
checksums: ALL OK (65 blocks); identification block unchanged
sha256: 3cd20443c068ed009b7d48b32210790eb320cb159489fc36cc1ef1ea67696498
flash CRC (firmware three-range CRC-32, `ecu_sim.py --print-flash-crc`): 0x06C08AD4 (G6/H6, 2026-09-24)
WARNING: ff_counter: "ram_status": "static" - the RAM block at 0x007FFB00 is VERIFIED-STATIC only (re/findings/ram.md): no instruction references it, but the runtime snapshots of issue #23 are still pending. Do not flash this image.
WARNING: change at 0x432940+0x4 writes the MPC561 on-chip flash (0x404000-0x47FFFF). The block checksums are handled and the firmware's own OBD programming route whitelists the range (re/findings/flash_programming.md), but a KESSv2 write of it has not been demonstrated: read the image back and compare before trusting it.
```

```
$ make HOOKS=external apply
ff_counter: 3 patch range(s) (99 B), 4 descriptor range(s) (10 B), 0 unexpected
checksums: ALL OK (65 blocks); identification block unchanged
sha256: 9ecde359ef91eabfa332ad80c3ad5667f5d5535ea72d0a0306da78a59ab375e2
flash CRC (firmware three-range CRC-32, `ecu_sim.py --print-flash-crc`): 0x84278F4C (H6 bench kit, 2026-09-24)
WARNING: ff_counter: "ram_status": "static" - ... Do not flash this image.
```

Five patch ranges rather than three because two of the blob's own bytes are
0xFF and therefore did not change. The six descriptor ranges are the sum/~sum
words of blocks 0x120000-0x12FFFF, 0x150000-0x15FFFF and the on-chip block that
holds 0x432940 (code descriptor #49). The sha256 of the default build changes
whenever the RAM block, the sources or the toolchain change; the
`HOOKS=external` one must **not** change, and a test says so.

## Tests

`tests/test_ff_counter_patch.py` (31 tests) and `tests/test_patch_framework.py`.

* the stock facts: both hook words, 0x0BD9E4 as a one-`blr` function, and which
  of the two sites is in the on-chip range;
* the two descriptors: the default carries both hooks with `onchip_edit` on the
  set-A one, the external one carries 0x12067C alone with no unlock flag
  anywhere, and the two agree on flash / ram / ram_size / base_sha256;
* apply, both variants: ALL OK (65 blocks), clean bindiff, exactly one on-chip
  warning for the default and none for the other, and the external image's
  sha256 is the pre-F1 one;
* `make HOOKS=external` rebuilt from source still produces the 96-byte blob
  `4645f45b…`, and `HOOKS=<anything else>` fails the build;
* under `Med9Emu` on the patched image: each stub increments `ff_ticks` by
  exactly 1 and sets **exactly its own bit**; a cold start (RAM zero, or
  garbage) yields `ticks = 1`, `alive = 0xFC01`, `src_seen` = that stub's bit
  and `reserved = 0`; twenty alternating activations give `ticks = 20` and
  `src_seen = 3`; the set-A stub does *not* clear 0x7FE889 / 0x800E18 and the
  set-B stub does; the set-A tail branch is a `ba 0x0BD9E4` and the empty leaf's
  `blr` is the last instruction executed;
* running `0x432940..0x432944` (and `0x12067C..0x120680`) on the stock and the
  patched image diffs all 64 KB of SRAM and differs only in the 8-byte counter
  block and `HOOK_TAIL`'s own 16-byte frame below the entry `r1` — 2 instructions
  stock against 30 patched at the set-A site and 5 against 33 at the set-B site
  (the emulator resets RAM to zero, so both patched runs take the cold-start
  path: 1 + 8 + 20 + the tail target's 1 or 4);
* both stubs return to `site + 4` with `r1` and `LR` exactly as the `bl` left
  them.

## Bench

`test/procedure.md` — what to read, the expected log lines, the decision table
that maps (stock raster counters × on-chip read-back × slope × `ff_src_seen`)
onto a verdict, and the rollback. `test/tolerance.json` — the limits for
`tools/logcmp.py` over the stock variables of issue #44.
