# Boot module, module boundary and the r2 (SDA2) context

Agent B1, brief `docs/agent_briefs/B1_r2_context_and_scheduler.md`, issue #8.
Date: 2026-09-15. Dump `data/passat_azx_ori.bin`, SHA-256
`b15590d3f1874ace3125c5d047c09a686db9b8bb498187663539ebab205609b3`.

Tools written for this: `tools/callgraph.py` (static PowerPC call graph and
SDA-reference extractor) and `tools/r2_context.py` (assigns and checks the
SDA2 base per function). Ghidra side: `ghidra_scripts/b1_context_and_symbols.py`.

Reproduce the whole section with

```bash
python3 tools/callgraph.py data/passat_azx_ori.bin \
    --reach 0x1004 0x12328 --stop 0x986AC 0x9E3E0 0x405588
python3 tools/r2_context.py data/passat_azx_ori.bin --compare --violations
```

Tags: **VERIFIED-STATIC** = derived from the bytes of the dump;
**VERIFIED-DYNAMIC** = observed in the emulator; **HYPOTHESIS** = inference.

---

## 1. The boot module is exactly 119 functions and it is closed

`tools/callgraph.py` collects every relative `bl` target in both code regions
(2,569 of them), then walks each function as a CFG. Seeding the walk with
`boot_start` (0x1004) and `boot_main_init` (0x12328) and stopping at the three
application SDA-setup routines (`app_sda_setup_a` 0x986AC, `app_sda_setup_b`
0x9E3E0, `app_sda_setup_int` 0x405588) yields **119 functions**, all of them
between **0x001004 and 0x01978F**.

Two independent closure checks, both clean (**VERIFIED-STATIC**):

* of the **118** `bl` targets that lie anywhere in 0x001000-0x019800, **all
  118** are in the reachable set — there is no boot-range function that is
  only reachable indirectly;
* **no** boot function contains a `bl` or a tail `b` to any address outside
  0x001000-0x019800.

So the boot module is a closed subgraph. Its instructions occupy 7,999 words
in twelve contiguous ranges:

| Range | Bytes | |
|---|---|---|
| 0x001004-0x001287 | 644 | reset stub: `boot_start`, 0x10F8, 0x1228 |
| 0x0110F0-0x011117 | 40 | `fatal_exception_handler` |
| 0x011524-0x011CB3 | 1,936 | |
| 0x011CE0-0x01382B | 7,500 | memory controller, PLL, DECRAM, `boot_main_init` |
| 0x01383C-0x0138D3 | 152 | |
| 0x013D64-0x0155FF | 6,300 | |
| 0x015620-0x016843 | 4,644 | |
| 0x016860-0x0179EB | 4,492 | |
| 0x017A10-0x017B33 | 292 | |
| 0x017CF0-0x01810B | 1,052 | |
| 0x01814C-0x018227 | 220 | |
| 0x01831C-0x01978F | 5,236 | |

The gaps inside 0x110F0-0x1978F are data (jump tables and constants) or dead
padding; nothing in them is a `bl` target.

### 1.1 Correction to `med9_setup.py --boot-r2`

`ghidra_scripts/med9_setup.py` uses a blanket `BOOT_R2_RANGE = (0x001000,
0x01FFFF)`. That range is **too wide**: `tools/callgraph.py --entries` finds
**100 further function entries between 0x019948 and 0x01E848**, none of them
reachable from the boot seeds, all of them ordinary application code that
wants r2 = 0x5C9FF0. The blanket range would give all of them the boot base.
`ghidra_scripts/b1_context_and_symbols.py` replaces it with the twelve ranges
above. **VERIFIED-STATIC.**

## 2. Boot flow, corrected and extended

```
reset -> ETR table file 0x000008 -> ba 0x3C4 ...        (RCW[IP]=1 path)
         classic table file 0x000100 -> ba 0x49C -> b 0x1004   (RCW[IP]=0)

0x1004  boot_start
        IMMR ISB=1 (peripherals to 0x400000), BR0 |= 0x100, IC_CST,
        MSR = 0x3942, SIPEND poll,
        r1 = 0x7FEFFC, r13 = 0x7FFFF0, r2 = 0x017FF0
0x10F0  bl 0x12328 (boot_main_init)
0x10F4  ba 0x110F0          <- if boot_main_init ever returns: fatal spin
```

`boot_main_init` then, in order: RSR, PLL (`boot_pll_setup` 0x115DC and the
SPLSS spin at 0x116FC), the full BR/OR programming through `boot_or_adjust`
(0x11E44), DMBR/DMOR at 0x1250C, the three DECRAM-resident routines, the TPU3
microcode copy to 0x702000 and the TPU3 parameter scan at 0x14604
(`re/findings/emulation_boot_path.md`).

### 2.1 A third DECRAM-resident routine — new

`re/findings/emulation_boot_path.md` section 4 lists two routines copied to
0x6F8000 (file 0x10FEC, 0xE4 bytes; file 0x11118, 0x238 bytes). There is a
**third**:

```
00012F54  lis   r12,0 ; addi r12,r12,0x1D4        ; byte count 0x1D4
00012F5C  rlwinm. r31,r12,30,18,31                ; /4 -> 0x75 words
00012F64  lis   r30,0x6F ; addi r30,r30,0x7FFC    ; dest-4 = 0x6F7FFC
00012F6C  lis   r3,1 ; addi r3,r3,0x134C          ; src-4  = 0x1134C
00012F78  lwzu  r12,4(r3) ; stwu r12,4(r30) ; bdnz
00012F84  bl    0x6F8000
```

i.e. **file 0x011350-0x011523, 0x1D4 bytes -> 0x6F8000**. The three sources
tile exactly: 0x10FEC+0xE4 = 0x110D0, 0x11118+0x238 = 0x11350,
0x11350+0x1D4 = 0x11524. **VERIFIED-STATIC.**

That block is entered only when the branch at 0x12F50 is not taken, and it is
followed by `bl 0x11CE0` and an indirect call through the word at **0x1C0120**,
which is 0xFFFFFFFF in this dump (0x1C0000-0x1C1FFF is one of the ranges no
checksum covers). Taking that path would jump to 0xFFFFFFFF, so it is the
reprogramming / "customer hook" path and is dead in a stock image.
**VERIFIED-STATIC** for the bytes, **HYPOTHESIS** for the purpose.

### 2.2 `boot_swsr_service` at 0x0110D0 — new

```
000110D0  lis  r3,0x70 ; addi r3,r3,-0x4000       ; r3 = 0x6FC000 (USIU)
000110D8  li   r11,0x556C ; sth r11,0xE(r3)       ; SWSR
000110E0  lis  r10,0 ; ori r10,r10,0xAA39 ; sth r10,0xE(r3)
000110EC  blr
```

The MPC5xx watchdog service sequence 0x556C/0xAA39 written to **SWSR
(0x6FC00E)**. `boot_main_init` calls it through `mtlr`/`blrl` at 0x12480,
0x12538 and 0x125C0 — which is why those three indirect calls exist.
**VERIFIED-STATIC.**

### 2.3 How control reaches the application — new

```
00012DCC  lis  r11,0x1C ; addi r11,r11,0x100      ; 0x1C0100 (calibration dir)
00012DD8  lis  r10,8   ; addi r10,r10,0x100       ; 0x080100 (code directory)
00012DE8  stw  r10,-0x1600(r13)                   ; RAM 0x7FE9F0 = 0x80100
...
00013070  lwz  r9,-0x1600(r13)                    ; r9 = 0x80100
00013074  lwz  r9,0x20(r9)                        ; r9 = [0x80120] = 0x9E3B4
00013078  mtlr r9
0001307C  blrl                                    ; -> app_entry_crt0
00013080  b    0x1308C                            ; epilogue -> blr -> 0x10F4
                                                  ;   -> ba 0x110F0 (fatal)
```

`boot_select_code_directory` (0x12D4C) first tests the word at **0x005FB0**
against the magic **0x11223344**; if it matches it takes five addresses from
0x5FB4-0x5FC0 instead of the defaults. In this dump 0x5FB0 is 0xFFFFFFFF, so
the default branch runs and the code directory is `tbl_code_sections` at
0x080100. Slot +0x20 of that directory is **0x0009E3B4**, and that is the
application entry (`app_entry_crt0`): it sets r1 = 0x7FF768, zeroes
0x80002C-0x80007C and 0x800080-0x8000E8, and loads r13 = 0x7FFFF0 /
r2 = 0x5C9FF0 (the pair already known as `app_sda_setup_b`, 0x9E3E0).
**VERIFIED-STATIC.**

So the boot module never `bl`s into the application; the handover is the one
`blrl` at 0x1307C, through a directory slot.

### 2.4 A second ETR branch table at 0x080000 — new

File 0x080000-0x0800FF is a second BBC exception-table-relocation branch
table with the same 32 x 8-byte layout as the one at file 0x0 (MPC561RM
Table 4-1, `re/findings/mpc5xx_registers.md` section 5). Its targets are the
application's exception handlers at **0x4055A0-0x4062A4** in on-chip flash
(each one saves r0/CTR/XER/CR/LR/r3-r12, passes an index to 0x9C388, restores
and does `rfi`); unused slots go to the common 0x4055A0. Two slots leave
on-chip flash: **+0x28 (external interrupt) -> `ba 0x0B0B4C`** and
**+0xE8 -> `ba 0x0B4458`**.

0x0B0B4C is `li r3,0xCA; b 0xBA444`, and 0xBA444 (`app_fatal_exception_handler`)
stores the index at RAM 0x7F8450, sets MSR = 0x2942 and spins writing 0x2222
to 0x704002. The boot table at file 0x0 chains its own +0x28 slot to 0x80028,
i.e. to this table's external-interrupt slot.

**Consequence (HYPOTHESIS, strongly supported):** in *both* tables we can see,
the external-interrupt vector is fatal, yet the application clearly runs with
external interrupts enabled (it uses the RCPU `EIE`/`EID` special registers
SPR 80/81 around every critical section — see `scheduler.md`). The live ETR
table with ISB = 1 is at **0x400000-0x4000FF, inside the 16 KB the dump is
missing**, and that is the one with the real external-interrupt entry. One
more reason the K-TAG/BDM read must happen before any write.

## 3. r2 (SDA2) per function

Boot module: **r2 = 0x017FF0** (set at file 0x10E8), application:
**r2 = 0x5C9FF0**. Three independent arguments, not one guess verifying
another:

1. **Temporal.** The 0x5C0000-0x5FFFFF calibration window does not exist until
   DMBR/DMOR are written at file **0x1250C**, well inside `boot_main_init`.
   Any r2-relative access executed before that with r2 = 0x5C9FF0 would hit
   unmapped internal space. The boot module's own r2 data
   (`tbl_or_values_by_clockmode` at 0x10020) is read from 0x11F68 onwards,
   i.e. both before and after 0x1250C. **VERIFIED-STATIC.**
2. **Known data.** `addi r11,r2,-0x7FD0` at 0x12014 with r2 = 0x017FF0 gives
   **0x010020**, exactly the clock-mode OR table that `re/symbols.csv` already
   records and that the emulator watches being indexed
   (`emu/boot_trace.py`, **VERIFIED-DYNAMIC**). Under 0x5C9FF0 the same
   instruction would point at 0x5C2020, which is inside the
   *uncovered* calibration hole 0x5C2240-0x5C2DFF's neighbourhood and has no
   table structure.
3. **Exhaustive resolution.** `tools/r2_context.py` resolves every r2-relative
   D-form access in the image under its assigned base:

   ```
   # boot functions: 119  range 0x1004-0x19784
   # boot base=0x017ff0  ok=177 ff=10 unmapped=0 outside_window=0
   # app  base=0x5c9ff0  ok=3228 ff=110 unmapped=0 outside_window=0
   ```

   **No reference on either side falls outside its SDA2 window
   (base-0x8000 .. base+0x7FFF) or outside a mapped region.**

### 3.1 The 120 "0xFF" hits, all explained

The 0xFF test is a weak filter and every hit is accounted for; there are **no
unexplained violations**:

* **10 boot-side hits**, all at 0x12014, 0x1203C, 0x1204C, 0x12068, 0x120A0,
  0x120A8, 0x120BC, 0x1211C, 0x12174, 0x121AC, and all of them `addi` — they
  compute the *base pointer* of `tbl_or_values_by_clockmode`
  (0x010020-0x01008C) for a following `lwzx`. The table's entries are OR
  values such as `0xFF800650`, so their first byte legitimately is 0xFF.
* **110 application-side hits**: 12 `addi` base pointers and 98 byte/halfword
  loads whose target is a calibration cell in 0x5C2Exx-0x5C3xxx etc. that
  happens to hold 0xFF. 0xFF is an ordinary calibration value.

### 3.2 The `lis r2,0xD5; addi r2,r2,-0x3210` outlier at 0x86330 — SOLVED

`docs/02_memory_map.md` section 4 lists this as unexplained. The six
instructions before it give it away:

```
0008630C  lis r11,0x80 ; addi r11,r11,0x4800     ; 0x00804800  (external SRAM)
00086314  lis r10,8    ; addi r10,r10,0x1A00     ; 0x00081A00  (flash)
0008631C  subf r3,r10,r11                        ; delta = 0x782E00
00086320  stw  r3,-0x2B8C(r13) ; stw r3,-0x2B88(r13)
00086330  lis r2,0xD5 ; addi r2,r2,-0x3210       ; r2 = 0x00D4CDF0
00086338  bl  0x86284
```

**0x5C9FF0 + 0x782E00 = 0xD4CDF0 exactly.** The value is the application SDA2
base with the flash-to-RAM relocation delta folded in, emitted by the linker
together with the block it relocates. The caller at 0x861A8-0x861B8 makes the
purpose plain:

```
000861A8  bl 0x83A18        ; copy flash 0x081A00.. -> RAM 0x804800..
000861AC  bl 0x862F4        ; reloc_enter_ram_driver  (sets the delta and r2)
000861B0  bl 0x806EA0       ; run the relocated routine (flash original 0x840A0)
000861B4  bl 0x862A4        ; reloc_leave_ram_driver  (negates the delta,
                            ;   restores r2 = 0x5C9FF0 at 0x862DC)
```

**It is harmless and needs no r2 context of its own**: the relocated block
(flash 0x081A00-0x085400) contains **zero** r2-relative references — it
addresses everything through `addis rX,r13,0` / `addi rX,rX,disp` — and
0x862DC restores 0x5C9FF0 before the sequence returns. Nothing executed while
r2 = 0xD4CDF0 dereferences r2, so leaving r2 = 0x5C9FF0 over 0x86284-0x86350
in Ghidra is correct. **VERIFIED-STATIC.**

## 4. What was applied to the Ghidra project

`ghidra_scripts/b1_context_and_symbols.py` (post-script, argument = repo root):

* `ProgramContext.setValue(r2, ...)` = 0x5C9FF0 over 0x000000-0x1FFFFF and
  0x404000-0x47FFFF, then 0x017FF0 over the twelve boot ranges of section 1
  (31,996 bytes, 119 functions);
* nine read-back probes, all matching (0x1004/0x12328/0x11E44/0x1978C boot;
  0x19948/0x1E848/0x86330/0x1205A0/0x478634 app);
* 41 symbols with the confidence tag in the plate comment.

## 5. Open items

| Question | Status |
|---|---|
| What is in the live ETR table at 0x400000-0x4000FF (the real external-interrupt handler)? | needs the BDM read of the missing 16 KB |
| What writes 0x11223344 to 0x005FB0, and what are the five addresses at 0x5FB4-0x5FC0 for? | open; 0x5FB0 is blank in this dump |
| What is the routine relocated to 0x804800 (entry 0x806EA0, flash 0x840A0)? It is reached from 0x861A8 and is almost certainly the external-flash programming driver. | open, note for the patch pipeline |
| The word at 0x1C0120 read by the indirect call at 0x12F8C is blank; is it a reprogramming hook? | open |
