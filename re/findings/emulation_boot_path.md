# Boot path under emulation

Dump `data/passat_azx_ori.bin`, SHA-256
`b15590d3f1874ace3125c5d047c09a686db9b8bb498187663539ebab205609b3`.
Harness: `emu/` (Unicorn 2.1.4, CPU 603e) with the map of
`docs/02_memory_map.md` section 3. Date 2026-09-15, brief A5, issue #21.
Reproduce with

```bash
python3 -m emu.boot_trace                     # the two stages below
python3 -m unittest discover -s tests -v      # the assertions
```

No byte of the flash image is patched anywhere below; every value the firmware
could not otherwise get comes from a read hook on a peripheral stub page.

## 1. `boot_or_adjust` at 0x11E44 — what it actually does

```
00011E44  898de9f8  lbz    r12, -0x1608(r13)   ; r13 = 0x7FFFF0 -> RAM 0x7FE9E8
00011E48  2c0c0001  cmpwi  r12, 1
00011E4C  40820008  bne    0x11E54
00011E50  5463062c  rlwinm r3, r3, 0, 24, 22
00011E54  4e800020  blr
```

Decoding 0x5463062C by hand: opcode 21, rS = rA = r3, SH = 0, MB = 24, ME = 22.
MB > ME, so the PowerPC mask is bits [MB..31] together with bits [0..ME] —
every bit except **bit 23**, i.e. `0xFFFFFEFF`. With SH = 0 the instruction is
`r3 &= 0xFFFFFEFF`: it clears the single bit **0x100**, and only when the byte
at 0x7FE9E8 is exactly 1 (2 does nothing).

Emulated (`tests/test_emu.py::TestBootOrAdjust`):

| r3 in | flag 0 | flag 1 |
|---|---|---|
| 0xFF800650 | 0xFF800650 | **0xFF800650** |
| 0xFF800140 | 0xFF800140 | 0xFF800040 |
| 0xFFFC0110 | 0xFFFC0110 | 0xFFFC0010 |
| 0xFFFF8C20 | 0xFFFF8C20 | 0xFFFF8C20 |

**Correction to brief A5**, which expected `0xFF800650 -> 0xFF800550`: that is
not reachable. 0xFF800650 has bit 0x100 *clear* already
(0x650 = 0x400|0x200|0x40|0x10), and an AND mask cannot set a bit.
`docs/02_memory_map.md` section 7 only claimed "clears a bit", which is right;
the bit is now named there. **VERIFIED-STATIC** (decode) +
**VERIFIED-DYNAMIC** (emulated).

The nine call sites are 0x11F08, 0x12020, 0x12044, 0x12074, 0x120B4, 0x12128,
0x1214C, 0x1217C, 0x121B4. Eight take the argument from the clock-mode tables
at file 0x10020-0x1009C (`lwzx r3, r2+disp, r29*4`, r2 = boot 0x17FF0); one
(0x11F00) uses the literal `0xFFFC0110`. Every result is stored to
0x6FC104 (OR0), 0x6FC10C (OR1), 0x6FC114 (OR2) or 0x6FC11C (OR3), so the
function's job is to clear one field of a memory-controller **option
register**. In the MPC5xx OR layout bit 23 is BI, burst inhibit (field name
**COMMUNITY**, from the MPC5xx manual; the bit position and the stores are
VERIFIED-STATIC), so the flag byte at 0x7FE9E8 selects burst-capable external
memory timing.

For reference, the table at file 0x10020 (overlapping windows, stride 0xC,
indexed by the clock mode in r29): `FF800650 FF8006FF FF8006FF FF800140
FF800140 FF800150 FF800130 ... FFFC01x0 ... FFFF8C20 FFFF8C30`.

## 2. From the reset vector, no stubs at all

`0x100 (ba) -> 0x49C (b) -> 0x1004`. Stage 1 of `emu.boot_trace`:

| pc | What it touches | Note |
|---|---|---|
| 0x1004 / 0x1010 | **SPR 638 (IMMR)**, ISB field -> 1 | Unicorn's 603e has no SPR 638: the read returns 0 and the write is swallowed. The relocation is therefore invisible, which is why `emu/memmap.py` hard-codes the relocated addresses. |
| 0x1018 / 0x1020 | USIU **BR0** 0x6FC100, `|= 0x100` | bit 23 of BR0 is write protect (COMMUNITY); the same bit the DECRAM routine clears at 0x6F8098 and restores at 0x6F81F4 |
| 0x1038 | SPR 560 (IC_CST) | also unmodelled |
| 0x1044 | `mtmsr 0x3942` | works; FP enabled |
| 0x104C | USIU **SIPEND** 0x6FC010, `andis. 0x8000`, `blt` back | **read exactly once**: the zero stub already means "nothing pending", so this loop is not a blocker |
| 0x10F0 | `bl 0x12328` (main init) | |
| 0x12220 | RSR 0x6FC288 (halfword) | reset status |
| 0x12428 / 0x1242C | OR0 0x6FC104, PLPRCR 0x6FC284 | |
| 0x115DC / 0x115F4 | SCCR 0x6FC280 <- 0x03217100, PLPRCR 0x6FC284 <- 0x00015080 | clock setup |
| **0x116FC - 0x1170C** | **stalls here** | |

The stall:

```
000116FC  53807c20  rlwimi  r0, r28, 15, 16, 16   ; r28 = 1 -> set PLPRCR bit 0x8000
00011700  901e0000  stw     r0, 0(r30)            ; r30 = 0x6FC284 (PLPRCR)
00011704  819e0000  lwz     r12, 0(r30)
00011708  558c8fff  rlwinm. r12, r12, 17, 31, 31  ; test the same bit 0x8000
0001170C  4082fff0  bne     0x116FC
```

Write 1 to the bit, poll until it reads back 0 — the sticky PLL
lock-status bit (SPLSS in the MPC5xx PLPRCR; field name **COMMUNITY**). With a
zero-filled stub page the written 1 stays written, so the loop never ends:
80 k of the first 400 k emulated instructions are this loop.

**This is the first peripheral value the map does not provide**: USIU
**PLPRCR (0x6FC284) bit 0x8000**, read at pc **0x11704**. Not SIPEND, and not
a watchdog. **VERIFIED-DYNAMIC**.

## 3. With the PLL reported locked (one read hook, no patching)

```python
emu.stub_read(0x6FC284, lambda pc, a, s: emu.read_u32(0x6FC284) & ~0x8000)
```

The boot then gets through, in this order:

* full memory-controller programming: **BR0-BR3 / OR0-OR3** at
  0x6FC100-0x6FC11C, plus DMBR/DMOR 0x6FC140/0x6FC144, SIUMCR 0x6FC000 and
  0x6FC024/0x6FC02C/0x6FC038/0x6FC03C — i.e. exactly the USIU set
  `docs/02_memory_map.md` section 3 predicted;
* the **DECRAM routine** (section 4) runs as part of the boot;
* **UC3F flash control** 0x6FC800 / 0x6FC804 (`0xFF`, `0x03000000`);
* a RAM probe at pc 0x118E0 reads/writes **0x808000**, one word past the
  external SRAM. On the real part CS1 is 32 KB so that aliases back; the
  harness has nothing there and reports it (`Result.unmapped`);
* **0x800 bytes copied from file 0x10624-0x10E23 to 0x702000-0x7027FF** (loop
  0x18098-0x180A0, word count 0x200 taken from file 0x10E24). The content
  `3FFFFFFE 7FFFFEFE BFFF07FC FFFFFF3F ...` looks like TPU3 microcode
  (**HYPOTHESIS**: TPU3 code RAM at 0x702000);
* MIOS14 0x706000, TPU3_A 0x704000, TPU3_B 0x704400 and UIMB 0x707F80 register
  writes;
* then it **stalls a second time**, in a TPU3 parameter-RAM scan:

```
00014604  cmpwi  r5, 0                    ; r5 = number of channel groups
...
00014620  lbz    r26, -0x7d13(r2)         ; r2 = 0x17FF0 -> constant at file 0x102DD
00014624  stwu   r31, 4(r6)               ; r6 = 0x6F8620 (counter array in DECRAM)
00014634  lwzx   r12, r2-0x7e78, r28*4    ; TPU base from the table at file 0x10178
0001463C  addi   r30, r12, 0x100          ; -> TPU3_A parameter RAM 0x704100
00014640  lwz    r12, 0(r3)
00014644  lhz    r11, 8(r30)              ; +0x08 of the channel's 16-byte entry
00014650  lhz    r9,  0xc(r30)            ; +0x0C
00014660  lhz    r12, 6(r30)              ; +0x06, counted if >= 0xC
0001467C  addi   r30, r30, 0x10           ; next channel
00014680  bne    0x14640
...
000146AC  lwzu   r12, 4(r28)              ; compare each counter with the constant
000146C0  cmpw   r5, r30
000146C4  bne    0x14604                  ; retry the whole scan
```

It accumulates a rolling `x = 2x + halfword` checksum over the parameter RAM
and counts channels whose halfword at +6 is at least 0xC, retrying until every
group matches. With a zero TPU3 the count never matches; the loop repeats for
as long as it is given (10 061 passes in 5 M instructions). **Modelling TPU3
parameter RAM is what a further boot run needs.** **VERIFIED-DYNAMIC** for the
stall, **HYPOTHESIS** for calling it a TPU self-test.

## 4. The DECRAM path 0x120C8-0x120FC

```
000120C8  lis   r12, 0 ; addi r12, r12, 0x238        ; byte count
000120D0  rlwinm. r3, r12, 30, 18, 31                ; /4 -> 0x8E words
000120D8  lis   r5, 0x6f ; addi r5, r5, 0x7ffc       ; dest-4 = 0x6F7FFC
000120E0  lis   r4, 1  ; addi r4, r4, 0x1114         ; src-4  = 0x11114
000120EC  lwzu  r12, 4(r4) ; stwu r12, 4(r5) ; bdnz
000120F8  addi  r3, r29, 0
000120FC  bl    0x6F8000
```

Emulated (`tests/test_emu.py::TestDecramRoutine`):

* the 0x238 bytes at 0x6F8000-0x6F8237 are byte-for-byte `dump[0x11118:0x11350]`;
* control enters at **0x6F8000** and leaves at the `blr` at **0x6F8234**,
  returning to 0x12100; 3 059 of the 3 496 instructions of this stretch run
  inside DECRAM (most of them the `0x1F4`-iteration delay loop at
  0x6F8204-0x6F8218);
* it passes **0x6F80B8** — where `med9_re/old_work/emulator.py` died — with no
  unmapped access, for every value 0, 1, 2, 4, 8 of the flash-type nibble at
  RAM 0x7F800C. **None of those paths touches 0x900000**: the flash command
  sequences they write (0xAA/0x55/0xD0, 0xAA/0x55/0xC0/0x01/0xF0, ...) go to
  low addresses on CS0. The correction in `docs/02_memory_map.md` section 8 is
  right that the old emulator lacked DECRAM and the ISB=1 map; its explanation
  that the crash was a CS2 access is not supported by this code.

The routine's own USIU work, in order: read/modify **SIUMCR** (0x6FC000) at
0x6F8078-0x6F808C, clear **BR0 bit 0x100** (write protect) at 0x6F8090-0x6F809C,
run the flash command sequence for the type selected by 0x7F800C, set BR0 bit
0x100 again at 0x6F81F0-0x6F81F8, spin 500 iterations, restore SIUMCR from the
stack. Under flag 0x7F8018 > 0 it takes a different prologue at
0x6F802C-0x6F8048 that also touches BR0 and SIUMCR.

Note that the boot copies **two different routines** to 0x6F8000, both of which
it then calls:

| Copy loop | Source (file) | Length | Called from |
|---|---|---|---|
| 0x11FAC-0x11FC8 | 0x10FEC | 0xE4 | 0x11FCC |
| 0x120D8-0x120F4 | 0x11118 | 0x238 | 0x120FC |

`docs/02_memory_map.md` section 3 mentions only the second. The full boot run
therefore shows DECRAM accesses from the *first* routine at pcs that do not
match the disassembly of the second - check which one is resident before
reading a boot trace.

## 5. Open questions

1. What is the CS2 device at 0x900000 (20 static references)? Not reached on
   any DECRAM path exercised here.
2. Is 0x702000 really TPU3 code RAM, and are the 0x800 bytes at file 0x10624
   TPU microcode? Needs the MPC563/564 manual (`docs/03_tooling.md` section 8).
3. What value does the TPU3 parameter RAM have to hold for the scan at
   0x14604 to pass — i.e. what is the constant at file 0x102DD counting?
4. The SPR numbers the firmware uses that the 603e does not model — 158, 560,
   568, 638, 792, 824 — should be identified against the MPC5xx manual before
   anyone trusts an emulated run through the code that uses them.
