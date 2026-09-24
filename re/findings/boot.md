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
0x7F802C-0x7F807B and 0x7F8080-0x7F80E7, and loads r13 = 0x7FFFF0 /
r2 = 0x5C9FF0 (the pair already known as `app_sda_setup_b`, 0x9E3E0).
**VERIFIED-STATIC.**

> **2026-09-16 (C2, issue #23) — correction and extension. VERIFIED-STATIC.**
> The two zero ranges were written above as "0x80002C-0x80007C and
> 0x800080-0x8000E8"; they are in the **internal** SRAM. 0x09E3C0-0x09E434
> computes `0x800000 - 0x7FD4 = 0x7F802C` … `0x800000 - 0x7F84 = 0x7F807C` and
> `0x800000 - 0x7F80 = 0x7F8080` … `0x800000 - 0x7F18 = 0x7F80E8`, i.e.
> **0x7F802C-0x7F807B** and **0x7F8080-0x7F80E7**. `re/symbols.csv` is
> corrected too.
>
> These two loops are only the start. `app_entry_crt0` goes on through
> `app_init` (0x04CCD4, reached by the thunk 0x0BA9A8) and `ram_clear_block`
> (0x06D8F8) to fill **fourteen** ranges, including the whole of the external
> SRAM 0x800004-0x80498F and the task stack 0x7FF3C0-0x7FF76B. The list is in
> `re/findings/ram.md` section 3.
>
> **The two stack tops reconciled.** The boot stack top 0x7FEFFC (file 0x10DC)
> is dead the moment this `blrl` lands: `app_entry_crt0` re-bases r1 to
> 0x7FF768 and `boot_main_init` never returns (0x10F4 is `ba 0x110F0`). The
> RAM under the old boot stack is reused as ordinary application data —
> 0x7FE588-0x7FEFDF is in the cold-start fill list and 0x7FEF00-0x7FEFFC
> carries dense r13 traffic. The live stack is **0x7FF3C0-0x7FF76F**, per the
> kernel stack descriptor at flash 0x09B6F8; `ram.md` section 4.2.

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
| What is the routine relocated to 0x804800 (entry 0x806EA0, flash 0x840A0)? It is reached from 0x861A8 and is almost certainly the external-flash programming driver. | **SETTLED (2026-09-17, E6, `re/findings/flash_programming.md` §3 and §4).** It is the flash programming driver for **both** devices: a three-entry device table (0x082980) whose device 1 is the on-chip UC3F 0x404000-0x47FFFF, five command sets selected by `flash_dev_probe` (0x081C18), a block-geometry table (0x0825E4) and the full UC3F interlock sequence in `flash_erase_block_start` (0x081F78) / `flash_program` (0x082208) / `flash_poll_uc3f` (0x081BB4). |
| The word at 0x1C0120 read by the indirect call at 0x12F8C is blank; is it a reprogramming hook? | **partly settled (2026-09-17, E6).** It is the third arm of `boot_mode_select` (0x012ED4), reached only when the test at 0x012894 says yes, and the two instructions in front of it are the third DECRAM copy and `bl 0x011CE0` = `uc3f_unprotect` — so yes, it is a reprogramming hook. What would write 0x1C0120 is still open. `flash_programming.md` §5.1 |

> **2026-09-17 (E6, blocker of #26 #27 #28 #32) — the boot has a second exit,
> and §3.2's "zero r2-relative references" is wrong. VERIFIED-STATIC.**
>
> **(a) `boot_mode_select` (0x012ED4).** §2.3 above follows the path that ends
> at the `blrl` at 0x01307C. That `blrl` is only reached when **three** mode
> tests all say no. `boot_mode_select` writes 0x5A78AA23 to RAM 0x7F8004,
> calls `boot_select_code_directory` (0x012D4C) and then tries, in order,
> 0x01270C (the byte at RAM 0x7F8010 plus the two CS2 pointers in DECRAM
> 0x6F8404/0x6F8408), `boot_check_reprog_magic` (0x012780: the word at RAM
> **0x7F8000** against **0xBB44E169**, 0xA5BCD193, 0xBD5593F3, 0xE45CD91A,
> 0x356BD372) and 0x012894 (the 0x1C0120 arm). On a hit it calls
> `boot_enter_prog_mode` (0x012658) — which runs the third DECRAM routine and
> **tail-calls `uc3f_unprotect` (0x011CE0)**, clearing
> `UC3FMCR[PROTECT]`/`UC3FMCRE[SBPROTECT]` and the CS0 write-protect — stores
> 0xDEADBEEF to DECRAM 0x6F840C and branches to **`bl 0x7F8728`**.
>
> **(b) 0x7F8728 is a RAM-resident flash loader, and it is where the "100
> further function entries between 0x019948 and 0x01E848" of §1.1 really
> live.** The boot copies flash **0x019798-0x02A827 (0x5090 B) to 0x7F8728**
> (loop 0x0126DC-0x0126F8) and `boot_swsr_service` (0x0110D0, 0x48 B) to
> **0x7FD7B8** (loop 0x0126AC-0x0126C8); the two tile exactly into
> 0x7F8728-0x7FD7FF. So that block is not ordinary application code that
> happens to want `r2 = 0x5C9FF0` — it executes at `0x7F8728 + (addr -
> 0x019798)` and carries its own flash-device table at flash 0x01E71C.
> `r2_context.py`'s clean result for it should be re-read with that in mind.
>
> *Added 2026-09-22 (integration, brief F5):* the loader's entry conditions,
> transport (QSMCM SCI1, not CAN), command table, address blacklist and the
> per-failure recovery table are in `re/findings/ram_loader.md`.
>
> **(c) §3.2 is wrong about r2.** "The relocated block (flash
> 0x081A00-0x085400) contains **zero** r2-relative references" — it contains
> four, all in the two-instruction form `addis rX,r2,-0x54` + a D-form
> displacement, which `tools/r2_context.py` does not classify as an r2 access
> because the base register is not r2: 0x082D1C, 0x082D64, 0x082D90 (the flash
> device table at 0x082980) and 0x082130 (the UC3F block-select table at
> 0x082684). Under `r2 = 0xD4CDF0` they resolve to the RAM copy at 0x804800,
> which is exactly what that base is for. The *conclusion* of §3.2 still
> stands — the block also runs in place from flash with `r2 = 0x5C9FF0`, and
> then the same instructions resolve to the flash originals — but the reason
> given ("nothing executed while r2 = 0xD4CDF0 dereferences r2") is not true.
> Detail: `re/findings/flash_programming.md` §3.1.

---

## 6. The one-shot init table (E6, 2026-09-17, follow-up to E4 #38)

Brief E6 was asked to sweep "the one-shot init table at 0x0B1A80-0x0B1C00".
It exists, it is bigger than that window, and this section says exactly what
it is, what it does and what the simulator misses by not running it.

Reproduce with the scratch script quoted in §6.4; the addresses below come
from `tools/callgraph.py --entries`, `tools/find_branch_refs.py`,
`tools/find_abs_refs.py` and `tools/blobdis.py`.

### 6.1 It is one flat, NULL-terminated array of 1,028 function pointers

**VERIFIED-STATIC.** The table starts at **0x0B1A68** (the word after the
`blr` at 0x0B1A64) and runs to **0x0B2A74**; the word at **0x0B2A78 is
0x00000000**, and the next words (`00000004 00001FEE 00000002 000802A8 …`)
are an unrelated structure. So:

| | |
|---|---|
| base | **0x0B1A68** (`tbl_module_init`) |
| entries | **1,028** (994 distinct targets; 16 targets appear twice) |
| terminator | the NULL word at **0x0B2A78** |
| targets | 1,011 in external flash, 17 in on-chip flash |
| 64 of them | a bare `blr` — empty init stubs, which is what makes this an init list rather than a dispatch table |

The whole table has **exactly two** references in the image: the pointer word
at **0x47901C** (found with `tools/find_branch_refs.py … 0x0B1A68`), and
nothing else — no `lis`/`addi` pair anywhere resolves into
0x0B1A00-0x0B2B00 except two unrelated sites (0x04D064 → 0x0B2AD0 and
0x0B4E14 → 0x0B2678). That is why `eeprom.md` §9 and `kwp.md` §4.1 could say
of `nvm_set_sync_mode`, `nvm_set_normal_mode` and `ddli_init` that they "have
no caller": their only caller is this walk.

> **SETTLED (2026-09-23, G3, §6.8).** The 1,028 words are two things laid end
> to end, not one list: indices **0-282** are the start-up process list (282
> init functions, then `os_dispatch_loop` 0x475DE0 at index 282, which never
> hands back), and indices **283-1027** (0x0B1ED4-0x0B2A74) are the twelve task
> process lists C4 already named `tbl_os_process_lists` (scheduler.md §11.2).
> The "second consumer" 0x0B4E14 → 0x0B2678 is the runtime-measurement module
> choosing **task 0's** process list (index **772**, not 771) as its default
> target; the list is re-walked by the ERCOSEK dispatcher every time the
> background task 0 loops. The 1,028-count, the NULL at 0x0B2A78 and every
> per-entry row of §6.4 stand.

### 6.2 Who walks it

0x47901C is the **ERCOSEK application descriptor**: `app_init`'s tail does

```
0004D09C  lis  r3,0x48 ; lwz r3,-0x6FD8(r3)     ; r3 = [0x479028] = 0x47901C
0004D0A4  bl   0x477990                          ; os_start(desc)
0004D0E4  b    0x0BA444                          ; must never return (index 0xC9)
```

and `os_start` (0x477990) reads `lwz r12,8(r30)` = `[0x479024]` = 0x478E20,
the on-chip kernel configuration block `re/symbols.csv` already knows. The
descriptor's fields are

```
0x47901C +0x00  0x000B1A68   tbl_module_init      <- this table
         +0x04  0x00478F74   time-table A descriptor (scheduler.md 11)
         +0x08  0x00478E20   tbl_os_kernel_config_int
         +0x0C  0x0047901C   self
```

and the walk itself is eight instructions inside `os_start`:

```
00477A2C  lwz   r11,-0x1A64(r13)      ; the OS object pointer
00477A34  lwz   r11,0x64(r11)         ; -> tbl_module_init
00477A38  lwz   r30,0(r11)
00477A3C  cmpwi r30,0 ; beq done
00477A44  mtlr  r30 ; blrl            ; call entry[i]()
00477A50  addi  r31,r31,1
00477A58  slwi  r10,r31,2 ; lwzx r30,r11,r10
00477A64  bne   0x477A44               ; until the NULL entry
```

so **every entry is called once, in order, with no arguments, before the
first task ever runs**. The only word in the whole image whose value is
0x0B1A68 is the one at 0x47901C, which is what `[OS object + 0x64]` resolves
to; that last link is the one runtime pointer in the chain, so the walk itself
is **VERIFIED-STATIC** and its identification with *this* table is
VERIFIED-STATIC + one-pointer inference.

> **Correction (2026-09-23, G3, §6.8(a)).** The one runtime pointer resolves
> differently. `[r13-0x1A64]` is written once, at 0x477874, as *record + 8* of
> the type-1 record the TLV walker 0x477818 finds at 0x478E20, so the OS object
> is **K = 0x478E28** and `[K+0x64]` = `[0x478E8C]` = **0x478E04**: a
> NULL-terminated list of **six kernel functions** (0x4772FC, 0x4767F4,
> 0x4765D4, 0x476874, 0x477C5C, 0x477CD4; NULL at 0x478E1C). That is what the
> eight instructions at 0x477A2C-0x477A64 walk. `tbl_module_init` is walked by
> the *next* loop, 0x477A6C-0x477A90, through the process cursor 0x7FE5A0,
> which 0x477970/0x477978 seeds from descriptor +0 (= 0x0B1A68). Each entry is
> still called once, in order, with no arguments — but only up to index 282.

### 6.3 What the 1,028 entries actually do

Walking each target linearly to its first `blr`/`b` (24,766 instructions in
total) and tracking `lis`/`addi`/`ori` register values:

| effect | entries |
|---|---|
| seed RAM cells with small constants | 320 |
| store a **data** pointer into RAM | 43 |
| call a sub-routine | 374 |
| **store a function-entry address into RAM** | **0** |
| **pass a function-entry address to a call** | **0** |

> **The init table binds no function pointers at all.** This is the answer to
> the question E4 raised: the NVM device function pointers **0x7FAB70 /
> 0x7FAB74** are *not* filled by it, which is consistent with `eeprom.md`
> §10.3's "no instruction in the image stores to them". Whatever installs the
> real QSPI driver is not in this table and is still open — the candidates left
> are the flash-loader path, a variant-specific module the linker dropped, or
> a write through a computed pointer this linear scan cannot see.

### 6.4 The window the brief named, 0x0B1A80-0x0B1C00 (indices 6-101)

`idx` is the array index from 0x0B1A68; `n` is the instruction count to the
first `blr`; the effect column lists the first four RAM constants, data
pointers and calls.

| idx | slot | init function | n | effect (first four of each) |
|---|---|---|---|---|
| 6 | 0x0B1A80 | `0x063C68` | 18 | 0x7FAD60=0x0 |
| 7 | 0x0B1A84 | `0x063AE4` | 72 | - |
| 8 | 0x0B1A88 | `0x05DA9C` | 137 | calls 0x0B8224 |
| 9 | 0x0B1A8C | `0x064BEC` | 87 | 0x7FCCC0=0x0 / calls 0x0B8218, 0x063D4C, 0x0B819C |
| 10 | 0x0B1A90 | `0x063FF8` | 34 | - |
| 11 | 0x0B1A94 | `0x065518` | 104 | 0x7FCCD8=0x1 / calls 0x0B88CC |
| 12 | 0x0B1A98 | `0x065B1C` | 118 | calls 0x0B822C |
| 13 | 0x0B1A9C | `0x0B8FF0` | 82 | - |
| 14 | 0x0B1AA0 | `0x0660FC` | 153 | calls 0x0B8214 |
| 15 | 0x0B1AA4 | `0x0659E4` | 58 | calls 0x0B8A64, 0x0B88CC, 0x0B8B00 |
| 16 | 0x0B1AA8 | `0x065288` | 66 | calls 0x0B8224, 0x063C3C, 0x063C3C |
| 17 | 0x0B1AAC | `0x12DDFC` | 14 | 0x7FCFED=0x34 |
| 18 | 0x0B1AB0 | `0x0BA0F4` | 4 | 0x7FCD68=0x2 |
| 19 | 0x0B1AB4 | `0x063094` | 11 | - |
| 20 | 0x0B1AB8 | `0x0618EC` | 11 | - |
| 21 | 0x0B1ABC | `0x061F7C` | 51 | calls 0x0B8228 |
| 22 | 0x0B1AC0 | `0x061918` | 11 | - |
| 23 | 0x0B1AC4 | `0x061B1C` | 4 | 0x7FCC9C=0x0 |
| 24 | 0x0B1AC8 | `0x0BA104` | 4 | 0x7FCD68=0x1 |
| 25 | 0x0B1ACC | `0x134678` | 18 | calls 0x0B4D2C |
| 26 | 0x0B1AD0 | `0x0B4BA0` | 10 | 0x7FD898=0x0, 0x7FC964=0x0, 0x7FC978=0x0, 0x7FC968=0x0 |
| 27 | 0x0B1AD4 | `0x0A2248` | 1 | - |
| 28 | 0x0B1AD8 | `0x134614` | 1 | - |
| 29 | 0x0B1ADC | `0x1341F8` | 12 | 0x7F9BE6=0x0 |
| 30 | 0x0B1AE0 | `0x134124` | 46 | 0x7FEB62=0x0, 0x7FEB5C=0x0, 0x7FEB60=0x0, 0x7FEB5F=0x0 / calls 0x46C080, 0x46C080, 0x46C080, 0x46C080 |
| 31 | 0x0B1AE4 | `0x132384` | 26 | 0x7FCE12=0x3f, 0x7FADD3=0x0, 0x7FADD5=0x1 / calls 0x06BE28, 0x06B5FC, 0x06B744, 0x4743E8 |
| 32 | 0x0B1AE8 | `0x134250` | 35 | 0x7FEB68=0x1, 0x7FC290=0x7 / calls 0x0B0810, 0x0B0790, 0x0B0790, 0x0B2AE0 |
| 33 | 0x0B1AEC | `0x1344A4` | 7 | 0x7FD414=0x2a, 0x7FD412=0x48, 0x7FD411=0x3c |
| 34 | 0x0B1AF0 | `0x12F10C` | 7 | - |
| 35 | 0x0B1AF4 | `0x12F49C` | 48 | calls 0x0B822C |
| 36 | 0x0B1AF8 | `0x12F578` | 35 | 0x7FBA70=0x0, 0x7FBA71=0x0, 0x7FEBE8=0x1 |
| 37 | 0x0B1AFC | `0x12F604` | 1 | - |
| 38 | 0x0B1B00 | `0x12F138` | 33 | ptr 0x8037E4<-0x7F8892, 0x8037E8<-0x7F8893, 0x8037EC<-0x7F889A, 0x8038D4<-0x7F88AC |
| 39 | 0x0B1B04 | `0x12EC00` | 75 | 0x7FBA50=0x0, 0x7F91D4=0x1, 0x7F91CC=0x0, 0x7F91D6=0x0 / calls 0x0B8234, 0x12F1BC, 0x0AA614 |
| 40 | 0x0B1B08 | `0x132C30` | 13 | - |
| 41 | 0x0B1B0C | `0x133D8C` | 25 | - |
| 42 | 0x0B1B10 | `0x12F2C8` | 80 | calls 0x0B8238 |
| 43 | 0x0B1B14 | `0x135F48` | 21 | - |
| 44 | 0x0B1B18 | `0x12E570` | 24 | calls 0x12E5D0, 0x12E794, 0x12E710, 0x12E928 |
| 45 | 0x0B1B1C | `0x12E8CC` | 7 | - |
| 46 | 0x0B1B20 | `0x133C80` | 10 | - |
| 47 | 0x0B1B24 | `0x1330A8` | 11 | - |
| 48 | 0x0B1B28 | `0x133220` | 16 | calls 0x133C18 |
| 49 | 0x0B1B2C | `0x132E98` | 3 | 0x7FEAD2=0x1 |
| 50 | 0x0B1B30 | `0x132E8C` | 3 | 0x7FC170=0xa00 |
| 51 | 0x0B1B34 | `0x133010` | 8 | 0x7FEE78=0x1 |
| 52 | 0x0B1B38 | `0x132EA4` | 11 | 0x7FEAD3=0x1, 0x7F9439=0x0 |
| 53 | 0x0B1B3C | `0x133260` | 18 | 0x7FEADF=0x1, 0x7F9446=0x0, 0x7F9444=0x0, 0x7F9442=0x0 |
| 54 | 0x0B1B40 | `0x132D78` | 28 | 0x7FCE89=0x0, 0x7FCE8B=0x0, 0x7FCE8C=0x0, 0x7FCE8F=0x0 / calls 0x0675E0 |
| 55 | 0x0B1B44 | `0x1332A8` | 17 | 0x7FC1D7=0x1 / calls 0x0B8228, 0x0675E0 |
| 56 | 0x0B1B48 | `0x1334CC` | 3 | 0x7FCED3=0x2 |
| 57 | 0x0B1B4C | `0x1334D8` | 32 | - |
| 58 | 0x0B1B50 | `0x132FD4` | 8 | - |
| 59 | 0x0B1B54 | `0x131274` | 24 | calls 0x40D7FC |
| 60 | 0x0B1B58 | `0x1321FC` | 9 | 0x801FAC=0x0, 0x80317C=0x0 |
| 61 | 0x0B1B5C | `0x13230C` | 4 | - |
| 62 | 0x0B1B60 | `0x132220` | 6 | - |
| 63 | 0x0B1B64 | `0x12E2E8` | 38 | 0x7F917C=0x67 |
| 64 | 0x0B1B68 | `0x12F2A0` | 10 | 0x7FD1B0=0x0, 0x7FD1AE=0x0, 0x7F91D9=0x0, 0x7F91D8=0x0 |
| 65 | 0x0B1B6C | `0x12F088` | 5 | - |
| 66 | 0x0B1B70 | `0x12EAE8` | 16 | 0x801325=0x1 / calls 0x05599C, 0x055A98 |
| 67 | 0x0B1B74 | `0x133D80` | 3 | 0x7FEB56=0x1 |
| 68 | 0x0B1B78 | `0x130B18` | 8 | 0x8019A6=0x80 |
| 69 | 0x0B1B7C | `0x130B38` | 7 | 0x801A59=0x1 |
| 70 | 0x0B1B80 | `0x12D6E8` | 9 | 0x800EF4=0x0, 0x800EF5=0x0, 0x800EF6=0x1, 0x800EF7=0x0 |
| 71 | 0x0B1B84 | `0x036AB8` | 8 | 0x7FB781=0x0, 0x7FB780=0x0, 0x7FB770=0x0 |
| 72 | 0x0B1B88 | `0x12E39C` | 19 | ptr 0x80403C<-0x80366C |
| 73 | 0x0B1B8C | `0x0358EC` | 10 | - |
| 74 | 0x0B1B90 | `0x12E3E8` | 3 | 0x80123D=0x0 |
| 75 | 0x0B1B94 | `0x12E2D8` | 4 | 0x801200=0x0, 0x7FB6F4=0x0 |
| 76 | 0x0B1B98 | `0x12E4C0` | 9 | 0x7F917F=0x1 |
| 77 | 0x0B1B9C | `0x12E3F4` | 60 | 0x7F917F=0x1 / calls 0x0B8238, 0x0B81DC |
| 78 | 0x0B1BA0 | `0x1314B0` | 16 | 0x801D83=0x7e, 0x7FBF74=0x30, 0x7FEA51=0x0 / calls 0x0660FC, 0x05E03C |
| 79 | 0x0B1BA4 | `0x132D5C` | 7 | - |
| 80 | 0x0B1BA8 | `0x132CD0` | 15 | 0x8000EE=0x3, 0x8000EF=0x3, 0x8000F0=0x3 |
| 81 | 0x0B1BAC | `0x1311E0` | 5 | 0x7FEA44=0x1, 0x7FD28D=0xff |
| 82 | 0x0B1BB0 | `0x134F5C` | 18 | - |
| 83 | 0x0B1BB4 | `0x12F638` | 11 | ptr 0x7FB078<-0x802C0C, 0x7FB07C<-0x802C1E, 0x7FB074<-0x802C1E, 0x7FB088<-0x802BF8 |
| 84 | 0x0B1BB8 | `0x12F664` | 72 | 0x802CFA=0x444, 0x802CF8=0x444 / ptr 0x7FB098<-0x802C52, 0x7FB094<-0x802C5E, 0x7FB09C<-0x802C5E, 0x7FB0A8<-0x802C94 |
| 85 | 0x0B1BBC | `0x131158` | 8 | 0x7FEA38=0x1 |
| 86 | 0x0B1BC0 | `0x12FDA4` | 19 | ptr 0x7FBC09<-0x7FBC0A, 0x7FBC0B<-0x7FBC0C / calls 0x4104C4, 0x4104C4 |
| 87 | 0x0B1BC4 | `0x12E170` | 3 | 0x7FE919=0x0 |
| 88 | 0x0B1BC8 | `0x134E50` | 3 | 0x8025D8=0x3 |
| 89 | 0x0B1BCC | `0x134E5C` | 12 | ptr 0x7FCBA8<-0x7FCBA9 / calls 0x4104C4 |
| 90 | 0x0B1BD0 | `0x1346D4` | 10 | - |
| 91 | 0x0B1BD4 | `0x12D03C` | 31 | 0x7FAD4C=0x2, 0x7FCDFC=0x1, 0x7FE87C=0x1, 0x7FED08=0x0 / calls 0x064384 |
| 92 | 0x0B1BD8 | `0x134C68` | 13 | - |
| 93 | 0x0B1BDC | `0x13106C` | 6 | 0x7FEA1E=0x0 |
| 94 | 0x0B1BE0 | `0x131084` | 3 | 0x801CF2=0x80 |
| 95 | 0x0B1BE4 | `0x1350B0` | 4 | - |
| 96 | 0x0B1BE8 | `0x13248C` | 4 | 0x7FD334=0xff, 0x7FD335=0xff |
| 97 | 0x0B1BEC | `0x131148` | 4 | - |
| 98 | 0x0B1BF0 | `0x13111C` | 7 | 0x7FD271=0x80 |
| 99 | 0x0B1BF4 | `0x131138` | 4 | - |
| 100 | 0x0B1BF8 | `0x132BA8` | 20 | 0x803280=0x0, 0x80327C=0x0, 0x80327E=0x0 / ptr 0x7FC138<-0x7FC132, 0x7FC13C<-0x7FC124, 0x7FC140<-0x7FC132 |
| 101 | 0x0B1BFC | `0x13503C` | 9 | - |

Named in `re/symbols.csv` from this window: index 17 `psg_ident_init`
(0x12DDFC), 18 `nvm_set_sync_mode`, 24 `nvm_set_normal_mode`, 26
`imo_state_init` (0x0B4BA0), 30 `dtc_freeze_init` (0x134124), 31
`dtc_mem_init` (0x132384), 32 `dtc_readiness_init` (0x134250), 38
`kwp_tp_buf_init` (0x12F138), 39 `kwp_chan_init` (0x12EC00) *[`dfp_init` in `re/symbols.csv` since G5 — the fault-memory manager's start-up, not a KWP channel init; H4, 2026-09-24]*, 71
`kwp_sec_init` (0x036AB8), 72 `ddli_init` (already named by E4), 75
`flash_crc_init` (0x12E2D8), 76/77 `prog_state_init` (0x12E4C0 / 0x12E3F4).
The remaining entries are anonymous per-module `init` functions of the
ASCET/COSYM generated code; naming all 1,028 would add noise, not knowledge.
The names above are **HYPOTHESIS** as names — they come from the RAM cells
each one writes, not from a string — while the address, the index and the
effect are VERIFIED-STATIC.

### 6.5 What `logging/ecu_sim.py` still skips — for the simulator's owner

`Med9Handlers.power_on` today writes `kwp_session_current`,
`kwp_security_state`, `kwp_sec_seed`, `kwp_sec_level_flags` = 3,
`kwp_sec_lfsr_rounds` = 5, the retry flag, and calls `ddli_init`; the NVM
device is bound by `emu.qspi_eeprom`. Measured against the real walk, that is
right in spirit and wrong in three details, all **VERIFIED-STATIC**:

1. **SETTLED (2026-09-22, F3, §6.7 and `logging/ecu_sim.py`'s module
   docstring).** `power_on` now calls 0x036AB8 and lets the real `27 01` arm
   the LFSR. **`kwp_sec_init` (0x036AB8) is init-table index 71, and it does
   the opposite of the hand-seeding.** It writes `kwp_sec_level_flags` (0x7FB781)
   **= 0**, 0x7FB780 = 0 and `kwp_sec_lfsr_rounds` (0x7FB770) **= 0**, then
   loads `kwp_sec_delay_timer` (0x7FB748) from the **EEPROM mirror halfword at
   0x7FA02C** (`lis r11,0x80; lhz r11,-0x5FD4(r11)`), i.e. the SecurityAccess
   lockout survives a power cycle through the EEPROM. The round count **5**
   is written later, by the seed path itself (`li r10,5; stb r10,-0x4880(r13)`
   at **0x03635C**, inside `kwp_sid_27_h1`'s level-1 arm, together with
   `0x7FB781 |= 1`). So the simulator can either call 0x036AB8 and let the
   real `27 01` arm the LFSR, or keep the shortcut and record that it emulates
   a post-`27 01` state, not a post-power-on one. `kwp.md` §12.6 assumed "the
   application sets them"; it is the *seed handler* that does.
2. **SETTLED (2026-09-22, F3).** The simulator calls indices 18 and 24 and
   `nvm_mode` reads 1. **`nvm_mode` (0x7FCD68) is 1 after start-up, not 0.**
   *[`nvm_mode` = `nvm_sync_mode` in `re/symbols.csv`; H4, 2026-09-24]* Index 18
   (`nvm_set_sync_mode`, writes 2) and index 24 (`nvm_set_normal_mode`,
   writes 1) are *both* called, in that order, so the manager comes up in
   **normal (asynchronous) mode**. `eeprom.md` §9 item 3 left the trigger of
   the synchronous mode as HYPOTHESIS; this shows both setters do run once at
   start-up and that nothing else in the image calls either, so the
   synchronous shutdown mode is never entered in a stock image.
3. **SETTLED (2026-09-22, F3, §6.7).** Index 75 is called too, and
   `flash_crc_task` now runs in the simulated background behind `--flash-crc`.
   **`flash_crc_init` (0x12E2D8, index 75) clears the CRC state byte
   0x7FB6F4 and 0x801200.** A simulator that wants `flash_crc_task`
   (0x011CB10) to run at all has to start from state 0; on a cold emulator the
   cell is already 0, so this one is free — but it is the reason the CRC is a
   *task*, not a boot-time check (`flash_programming.md` §5.3a).

Three more that matter for anything that drives the KWP stack from a cold
emulator: index 38 (0x12F138) installs four RAM buffer pointers at
0x8037E4/E8/EC and 0x8038D4; indices 83 and 84 (0x12F638, 0x12F664) install
eight more at 0x7FB074-0x7FB0A8 and seed 0x802CF8/0x802CFA = 0x444; indices 86
and 89 (0x12FDA4, 0x134E5C) install self-referential list heads
(0x7FBC09 -> 0x7FBC0A, 0x7FCBA8 -> 0x7FCBA9) through 0x4104C4. **None of the
1,028 entries writes 0x7FAB70/0x7FAB74**, so `NvmDeviceBinding` stays
necessary exactly as E4 built it.

**The cheapest correct fix** is not to call the whole table (1,028 calls, many
of which touch peripherals the emulator does not model) but to call the
handful of entries a session needs — 71, 72, 38, 83, 84 — and to keep the rest
of the hand-seeding, documented as such. That decision is the simulator
owner's; this section is the input, and `logging/` was not touched by this
brief.

> **SETTLED 2026-09-22 (F3, #20/#38).** `Med9Handlers.power_on` now calls ten
> entries — **18, 24, 38, 71, 72, 75, 83, 84, 86, 89**, in the table's own
> order — and every one of them returns cleanly under Unicorn. The residue
> that is still seeded by hand is a table in `logging/ecu_sim.py`'s module
> docstring. §6.7 has what running them showed.

**(d) For the simulator's owner — the flash-CRC period (2026-09-23, G3,
§6.8; brief G5 applies it, `logging/` was not touched by G3).** Index 75 stays
right (it is a start-up entry, below index 282), but `flash_crc_task` is **not**
a start-up entry and **not** a 10 ms raster: it is five processes of the
background task 0, so one background loop T_bg hashes **500 bytes** (five
activations of 0x64). **N = T_bg / 5 per activation; the value 0x5562139F
appears in loop 4,926 = 4,926 × T_bg after `os_init`** — VERIFIED-STATIC bounds
**0.51 ms ≤ T_bg ≤ 300.75 ms**, i.e. **2.5 s ≤ t_publish ≤ 1,481 s**. T_bg
itself is set by the CPU's idle time and is a bench value (§6.8(e)). The
current `--flash-crc` default (one activation per 10 ms, 246 s) is T_bg = 50 ms,
inside the bounds; a simulator should take T_bg as a parameter and move
0x7FB700 by 500 per loop, not by 100 per 10 ms.

### 6.6 Reproduction

```bash
# the table, its extent and the NULL terminator
./.venv/bin/python3 -c "import struct;d=open('data/passat_azx_ori.bin','rb').read();print([hex(struct.unpack('>I',d[a:a+4])[0]) for a in (0x0B1A64,0x0B1A68,0x0B2A74,0x0B2A78)])"
# its only reference
./.venv/bin/python3 tools/find_branch_refs.py data/passat_azx_ori.bin 0x0B1A68
./.venv/bin/python3 tools/find_abs_refs.py data/passat_azx_ori.bin --range 0x0B1A00 0x0B2B00
# the walker, the descriptor and os_start's hand-over
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x277990 --addr 0x477990 --len 0xE0
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x4D090 --addr 0x4D090 --len 0x20
# the two init functions the simulator's power_on contradicts
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x36AB8 --addr 0x36AB8 --len 0x20
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x36330 --addr 0x36330 --len 0x34
```

The per-entry classification of §6.3 and §6.4 was produced by a scratch script
that walks each target linearly to its first `blr`/`b` with capstone, tracking
`lis`/`addi`/`ori` register values and recording every `stw`/`sth`/`stb` whose
destination lands in 0x7F8000-0x807FFF. The method is stated here in full
rather than kept as a tool, because it is a one-off sweep and `tools/` already
carries the reusable half (`callgraph.py --entries` supplies the function-entry
set the "is this a function pointer?" test uses).

## 6.7 What running the init entries showed (F3, 2026-09-22, #20 / #38)

`logging/ecu_sim.py`'s `power_on` calls ten entries of `tbl_module_init` in
index order. Running them, rather than reading them, settled three things
§6.4 and §6.5 could not.

### (a) Every one of the ten returns under Unicorn, and the cells match §6.4

| idx | entry | after the call |
|---|---|---|
| 18, 24 | `nvm_set_sync_mode`, `nvm_set_normal_mode` | `nvm_mode` 0x7FCD68 = **1** *(`nvm_sync_mode` in `re/symbols.csv`; H4, 2026-09-24)* |
| 38 | `kwp_tp_buf_init` | 0x8037E4 = 0x7F8892, 0x8037E8 = 0x7F8893, 0x8037EC = 0x7F889A, 0x8038D4 = 0x7F88AC |
| 71 | `kwp_sec_init` | 0x7FB781 = 0, 0x7FB780 = 0, 0x7FB770 = **0**, 0x7FB748 = 0 |
| 72 | `ddli_init` | 0x80403C = 0x80366C, then 0x80370C + 0x18·n |
| 75 | `flash_crc_init` | 0x7FB6F4 = 0, 0x801200 = 0 |
| 83, 84 | the eight pointers 0x7FB074-0x7FB0A8 | 0x7FB074 = 0x802C1E … 0x7FB0A8 = 0x802C94, 0x802CF8 = 0x802CFA = 0x444 |
| 86, 89 | the list heads through 0x4104C4 | 0x7FBC09 = 0x7FBC0B = 0x7FCBA8 = 0 |

VERIFIED-DYNAMIC (emulated). Note the last row: §6.4's classifier printed
these as "ptr 0x7FBC09 ← 0x7FBC0A", but the disassembly is
`addi r3,r13,-0x43E6` (the *argument* 0x7FBC0A) → `bl 0x4104C4` →
`stb r3,-0x43E7(r13)`, i.e. the **byte return value** of 0x4104C4 is stored,
not a pointer. With the calibration bytes at 0x5D09EE/0x5D09EF as they are in
this dump the routine returns 0. A small correction to the table, not to the
conclusion.

### (b) The LFSR round count really is the seed handler's, and `27 01` needs a clock

With index 71 called and nothing hand-seeded, `0x7FB770` is **0** after
power-on and becomes **5** only after a `27 01` — together with bit 0 of
`kwp_sec_level_flags`, both written at 0x036340-0x03635C inside
`kwp_sid_27_h1`'s level-1 arm (`ori r12,r12,1` / `stb` at 0x036348-0x03634C,
`li r10,5` / `stb r10,-0x4880(r13)` at 0x036358-0x03635C). `27 02` with `key_level1(seed)` then grants level 1.
VERIFIED-DYNAMIC, `tests/test_ecu_sim_initstate.py`.

Getting there needed one thing §6.5 could not have known: the level-1 seed
path **spins forever on a frozen time base**. 0x36328-0x3633C re-reads
`read_time_base` (0x478460) until the value differs from the previous seed
*and* its low word's top byte is non-zero; Unicorn's 603e never advances
TBU/TBL, so `27 01` stopped at the instruction limit inside `read_time_base`.
`emu/time_base.py` rewrites the three time-base reads inside that one routine
into `lwz` against a scratch pair and drives them from the simulator's clock.
As a side note about the *part*: the same loop means a real ECU whose time
base has just been zeroed refuses to produce a level-1 seed for the first
0x01000000 ticks ≈ 4.8 s.

### (c) `kwp_sec_init` reads the mirror **before** the mirror exists

§6.5 item 1 says the SecurityAccess lockout "survives a power cycle through
the EEPROM", because index 71 loads 0x7FB748 from the mirror halfword
0x7FA02C. The order says otherwise, and this is a correction:

* `app_init` zeroes **0x7F8490-0x7FA630** at 0x04CF90-0x04CFC4 — 0x7FA02C is
  inside it — and only then, straight-line at **0x04D0A4**, calls
  `os_start`, which walks the init table (§6.2). So index 71 runs on a
  just-cleared cell.
* the EEPROM start-up block read is **not** in the init table:
  `nvm_read_all_blocks` (0x06227C) *[0x06227C is `nvm_read_all_blocks_entry` in
  `re/symbols.csv`; `nvm_read_all_blocks` is its `stwu` at 0x062280; H4, 2026-09-24]* has one reference, the `addi` at 0x06259C
  inside 0x061BF4, and `tools/sda_xref.py --code 0x061BF4` gives two callers,
  **0x120FB8** (set B) and **0x45CD48** (set A) — task bodies, i.e. *after*
  `os_start`.

So on the part `kwp_sec_delay_timer` is **0 after every power-on** whatever the
EEPROM holds, and the mirror's value only matters if something re-runs index
71 later. VERIFIED-STATIC. It makes no practical difference here: a
factory-shaped image holds **0x0000** at block 11 payload +0x0C (block 11's
default record is `0b 02 00 00 …`, `tools/eeprom_map.py`), so the timer is 0
either way. The simulator attaches the EEPROM before the init entries, so it
reads the mirror; the difference is recorded in its module docstring.

### (d) `flash_crc_task` has no `bl` caller anywhere

`tools/sda_xref.py --code 0x11CB10` finds only five `b` thunks at
0x11CD24-0x11CD34, and the only words in the image pointing at those thunks
are `tbl_module_init` slots **0x0B2684-0x0B2694, indices 775-779**. So the
"runtime CRC task" of `flash_programming.md` §5.3a is activated through this
table, five times over during the init walk — 500 bytes of the 2,462,208 it
has to hash. Something re-walks a slice of the array afterwards: 0x0B4E24
stores **0x0B2678** (index 771) into the cursor 0x7FC9D8, which 0x0B5878 then
compares against the time-table cell 0x7FE5A0. That is the second consumer
`§6.1` noticed and could not place (`0x0B4E14 → 0x0B2678`). **The array at
0x0B1A68 is therefore not purely a one-shot init list**; at least the run from
index 771 is also walked as a periodic process list. What rate that walk has
is **open** — F3's time box went to the NVM question — and it is what decides
how long a real ECU takes to publish its flash checksum.
VERIFIED-STATIC for the references, HYPOTHESIS for the periodic reading.

> **SETTLED (2026-09-23, G3, §6.8).** The periodic reading is right, the
> "five times during the init walk" is not: the start-up walk stops at index
> 282, so `flash_crc_task` is **never** called at start-up. Slots 775-779 belong
> to the process list of **task id 0** (0x0B2678-0x0B26AC, priority 0, the
> set-A background task), which runs all 13 processes back to back and then
> re-activates itself. **N = T_bg / 5**, where T_bg is one background loop —
> it is not a raster. T_bg is bounded **0.51 ms ≤ T_bg ≤ 300.75 ms**
> (deadline timer 1, fatal code 0x74), so the CRC publishes after
> **4,926 loops = 4,926 × T_bg, between 2.5 s and 1,481 s (24.7 min)** after
> `os_init`. The bench reads T_bg from the slope of the cursor 0x7FB700
> (500 bytes per loop). The (d) index "771" is 772.

Reproduce:

```bash
./.venv/bin/python3 tools/sda_xref.py data/passat_azx_ori.bin --code 0x11CB10 0x061BF4
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x4CF80 --addr 0x4CF80 --len 0x140
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x11CB10 --addr 0x11CB10 --len 0x214
./.venv/bin/python3 -m unittest tests.test_ecu_sim_initstate
```

## 6.8 The second walker of 0x0B1A68, and the flash-CRC period (G3, 2026-09-23, #20)

Brief G3 task 1. Everything below is **VERIFIED-STATIC** unless tagged; the
reproduction block is (f). r13 = 0x7FFFF0, r2 = 0x5C9FF0.

### (a) The two start-up walks in `os_start`, and where the first one really points

`os_start` (0x477990) has **two** loops, not one:

```
00477A2C  lwz  r11,-0x1A64(r13)    ; K = the OS object
00477A34  lwz  r11,0x64(r11)       ; [K+0x64]
00477A38..00477A64                 ; call every word until NULL          <- loop 1
00477A6C  lwz  r12,-0x1A50(r13)    ; p = process cursor 0x7FE5A0
00477A70  addi r12,r12,4 ; stw r12,-0x1A50(r13)
00477A78  addi r12,r12,-4 ; lwz r12,0(r12) ; mtlr ; blrl   ; call *p
00477A88  lwz  r11,-0x1A50(r13) ; cmpwi r11,0 ; bne 0x477A6C              <- loop 2
```

* **K** is written exactly once in the on-chip kernel: `addi r12,r3,8; stw
  r12,-0x1A64(r13)` at **0x477870-0x477874**, the type-1 arm of the TLV walker
  0x477818 that `os_start`'s helper 0x477918 runs over descriptor +8
  (= 0x478E20, record header `00000001 0000007C`). So **K = 0x478E28**
  (consistent with scheduler.md §11.4's "config +0x68 becomes K+0x60"), and
  `[K+0x64]` = `[0x478E8C]` = **0x478E04** = `{0x4772FC, 0x4767F4, 0x4765D4,
  0x476874, 0x477C5C, 0x477CD4, 0}`. Loop 1 is the kernel's own init hooks.
* **Loop 2** is the one that walks `tbl_module_init`: 0x477970/0x477978 (`lwz
  r9,0(r30); stw r9,-0x1A50(r13)`, r30 = the descriptor 0x47901C) seed the
  cursor 0x7FE5A0 with descriptor +0 = **0x0B1A68**.

### (b) The start-up walk ends at index 282, inside the dispatcher

Index 282 (0x0B1ED0) is **0x475DE0**, `os_dispatch_loop`: it loads SIMASK2/3
from `[K+0x4C]` (0x478E6C → interrupts on), then loops

```
00475E34  [0x7FE5A4] = [0x7FE5A8]
00475E3C  r31 = [0x7FE59C]              ; next-process pointer
00475E40  [0x7FE5A0] = r31              ; current-process slot
00475E48  [0x7FE59C] = r31 + 4
00475E50  call *[0x7FE5A0]
00475E60  lwz r31,-0x1A3C(r13) ; cmplwi r31,0xFF ; ble 0x475E30
```

so it overwrites the start-up cursor with whatever the scheduler picks and
returns only when 0x7FE5B4 exceeds 0xFF. The only store that does that is
`ori r12,r12,0xFF00` at **0x478560-0x478564**, in the shutdown/restart branch
of the idle process 0x4784D4 (0x7FE59C is seeded with the idle list
0x803A1C = `{0x4784D4, 0}` at 0x477778-0x4777F4). **Indices 283-1027 are never
reached by the start-up walk.** They are exactly the twelve task process lists
(`tbl_os_process_lists`, 0x0B1ED4-0x0B2A74, each `{0x0B5878, procs…, 0x0B5978,
os_TerminateTask}` for ids 20, 25, 18, 22, 17 and 0; task 8 and the five set-B
lists have no wrappers), which this
sweep re-derives from the 37 descriptors:

| idx | list | task | prio | period |
|---|---|---|---|---|
| 283-286 | 0x0B1ED4 | 20 | 9 | event |
| 287-341 | 0x0B1EE4 | 25 | 4 | 50 ms |
| 342-556 | 0x0B1FC0 | 18 | 3 | 100 ms |
| 557-668 | 0x0B231C | 22 | 2 | 200 ms |
| 669-771 | 0x0B24DC | 17 | 1 | 1000 ms |
| **772-785** | **0x0B2678** | **0** | **0** | **background (re-activates itself)** |
| 786-909 | 0x0B26B0 | 8 | 1 | event |
| 910-1027 | 0x0B28A0 … 0x0B2A40 | 37, 31, 34, 30, 29 | | set B |

### (c) Task 0 is the set-A background loop, and `flash_crc_task` is five of its processes

Task 0 (handle 0x478870, `[0x478888]` = 0x478870) has priority 0, the lowest,
and the list

```
0x0B2678  0x0B5878  rtm_list_enter  (runtime-measurement prologue, (d))
0x0B267C  0x11D9F0
0x0B2680  0x11CD5C
0x0B2684  0x11CD24 ┐
0x0B2688  0x11CD28 │
0x0B268C  0x11CD2C │ five `b 0x11CB10` thunks = flash_crc_task, 0x64 bytes each
0x0B2690  0x11CD30 │
0x0B2694  0x11CD34 ┘
0x0B2698  0x11D654
0x0B269C  0x11CE20
0x0B26A0  0x11E63C
0x0B26A4  0x11DA64  bg_task_tail
0x0B26A8  0x0B5978  rtm_list_exit
0x0B26AC  0x4764FC  os_TerminateTask
```

* **Activated once**, by `os_init` at 0x11B0EC-0x11B0F4 (`lwz r3,[0x478888];
  bl os_ActivateTask`), straight after `os_set_deadline_timer(1, 0x100FD7)` at
  0x11B0E8. `tools/find_abs_refs.py --range 0x478868 0x47888C` finds no other
  loader of the handle than the thunk 0x0B09E8, which nothing calls.
* **Re-activates itself.** `bg_task_tail` 0x11DA64 increments the loop counter
  **0x7FD70C** (`-0x28E4(r13)`), re-arms `os_set_deadline_timer(1, 0x100FD7 =
  1,052,631 ticks = 300.75 ms)` at 0x11DA8C, and — while the set-B request byte
  0x7FEB5E is 0 — ends in `bl 0x11CD38; bl 0x477B48` (0x11DB30-0x11DB34).
  0x477B48 looks up the *current* task (`[K+0x68] + [0x7FE5A4]*0xC`) and sets
  the byte after its activation counter to 1 (`stb r11,1(r12)` at 0x477B94):
  `os_reactivate_self` (name and exact OSEK semantics HYPOTHESIS). The set-B
  twin, task 29, ends the same way (`b 0x477B48` at 0x124854, deadline 1 at
  0x124844).
* **Why it must loop — the ceiling.** Deadline timer 1 is armed at exactly
  three sites (0x11B0E8 `os_init`, 0x11DA8C task 0, 0x124844 task 29 — every
  `bl 0x477204` in the image, scanned) and checked at exactly one, 0x40BDE8 in
  `os_deadline_supervisor`, which the set-A 10 ms task reaches every 10 ms
  (0x432BC0 `bl 0x4328B4` → `b 0x40BDC0` at 0x4328E0). On expiry it bumps
  0x7F849C and calls the fatal handler `0xBA444(0x74)`. So in set A a
  background loop that does not come round within **300.75 ms** (plus at most
  one 10 ms check interval) ends in fatal code 0x74: **T_bg ≤ 300.75 ms** on
  any ECU that keeps running. This argument does not depend on what 0x477B48
  does — a task 0 that ran once would trip it 300 ms after `os_init`.
* **The floor.** Running the eleven non-wrapper processes back to back in the
  emulator (method in (f)) takes **28,560 instructions per loop** in steady
  state (0x11D654 18,044; 0x11E63C 2,624; each CRC thunk 1,533) and moves the
  CRC cursor 0x7FB700 by **0x1F4 = 500 bytes per loop** (0x20190 → 0x20384 →
  0x20578 …), VERIFIED-DYNAMIC (emulated). At most one instruction per 56 MHz
  clock (the RCPU dispatches one per cycle — HYPOTHESIS about the core, not
  measured) makes that **T_bg ≥ 0.51 ms**, before any preemption by the
  1/2/5/10/20 ms rasters.

**Result.** `flash_crc_task` runs **5 × per background loop**, so
**N = T_bg / 5** and the publish of 0x5562139F happens in loop
⌈24,627 / 5⌉ = **4,926**:

| | T_bg | N | t_publish = 4,926 × T_bg |
|---|---|---|---|
| floor (no preemption, 1 IPC) | 0.51 ms | 0.10 ms | **2.5 s** |
| `ecu_sim --flash-crc` today | 50 ms | 10 ms | 246 s |
| ceiling (deadline timer 1) | 300.75 ms | 60.2 ms | **1,481 s = 24.7 min** |

So **the stock task always reaches state 2 within 24.7 min of `os_init`** on a
set-A ECU that does not reset, i.e. within a normal drive. A realistic idle
fraction puts it much nearer the floor (seconds to a minute), but that is a
HYPOTHESIS until the bench reads T_bg. On set B (never live, scheduler.md
§11.8) the CRC would not run at all: task 29's list has no CRC thunk.

### (d) What 0x0B4E24 and 0x0B5878 are: a runtime-measurement module, not a walker

The code F3 found is an ETAS-style runtime measurement of one process list,
built on the debug comparators (SPR 144 CMPA, 158 ICTRL, 149 DER; exception
entry 0x0B5610 through the `b` at 0x0B4458, ends in `rfi`):

| Address | Name (HYPOTHESIS) | Where it runs | What it does |
|---|---|---|---|
| 0x0B5140 | `rtm_init` | start-up index 0 | defaults: 0x7FC9F7 = 6, the min/max cells = 0xFFFFFFFF |
| 0x0B4DD0 | `rtm_ctrl_update` | called by 0x0B5188 (0x0B5194) | on the rising edge of the cal enable byte 0x5C8BAA (= 1 in this dump; `lbz r12,-0x1446(r2)`) writes **0x7FC9D8 = 0x0B2678** (0x0B4E14-0x0B4E24), 0x7FC9F7 = cal 0x5C8BA8 (= 4), 0x7FC9FB = 2; other modes select `[0x5C8BB4]` or the CPU-burn loop 0x0B5864 instead |
| 0x0B5188 | `rtm_task_100ms` | task 18 list slot 0x0B2310 (idx 554), **100 ms** | control / statistics step |
| 0x0B5878 | `rtm_list_enter` | slot 0 of the lists of ids 20, 25, 18, 22, 17, 0 (not 8, not set B) | if the mode bit is on, compares the **current process slot** `[0x7FE5A0]` with 0x7FC9D8 (0x0B588C-0x0B5894) — equal exactly when the list being entered is task 0's — and arms the measurement |
| 0x0B5978 | `rtm_list_exit` | second-last slot of the same lists | closes it |

So 0x7FC9D8 (`rtm_target_list`) holds a **process-list address** chosen as the
measurement target, and 0x7FE5A0 is the dispatcher's current-process slot
((b)), not a time-table cell. Nothing walks the init array "from index 771":
the ERCOSEK dispatcher walks task 0's list each loop and the measurement module
watches it by default. Its results sit in 0x7FC9CC-0x7FCA58, in no measuring
block (`tools/measuring_vars.py --all` lists none of them).

### (e) The dynamic check that turns the bounds into one number

Any one of these on the bench (all readable with DDLI `2C F0 03 …` in session
0x89, kwp.md §4; `logging/sessions/flash_crc.json` already logs the first):

1. **The CRC cursor 0x7FB700** (u32): T_bg = 500 bytes / (slope in bytes/s).
2. **The background loop counter 0x7FD70C** (u32): T_bg = 1 / (slope in
   counts/s). Independent of the CRC state machine, and keeps counting after
   state 7.
3. Or time-stamp the first read of 0x7F9178 ≠ 0 after power-on:
   T_bg = t_publish / 4,926.

T_bg is the CPU's idle time, so expect it to vary with engine speed; log it at
key-on/engine-off and at idle.

### (f) Reproduction

Note: §6.6's `--file-off 0x277990` for `os_start` is a typo that disassembles
erased flash; the on-chip file offset is CPU − 0x204000 (0x273990), as below.

```bash
# the kernel object K and the six-hook list that loop 1 walks
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x273818 --addr 0x477818 --len 0x100
./.venv/bin/python3 -c "import struct;d=open('data/passat_azx_ori.bin','rb').read();f=lambda a:hex(struct.unpack('>I',d[a-0x204000:a-0x204000+4])[0]);print([f(a) for a in (0x478E20,0x478E8C)],[f(0x478E04+4*i) for i in range(7)])"
# os_start's two loops, the dispatcher (start-up index 282) and the idle process
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x273918 --addr 0x477918 --len 0x178
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x271DE0 --addr 0x475DE0 --len 0xAC
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x2744D4 --addr 0x4784D4 --len 0x98
# task 0: descriptor, list, the one activation and the self re-activation
./.venv/bin/python3 tools/ercosek_tasks.py data/passat_azx_ori.bin --tasks
./.venv/bin/python3 tools/find_abs_refs.py data/passat_azx_ori.bin --range 0x478868 0x47888C
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x11B0D8 --addr 0x11B0D8 --len 0x20
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x11DA64 --addr 0x11DA64 --len 0xE4
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x273B48 --addr 0x477B48 --len 0x6C
./.venv/bin/python3 tools/sda_xref.py data/passat_azx_ori.bin --code 0x477B48 0x477204 0x47736C
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x207DC0 --addr 0x40BDC0 --len 0xC8
# the runtime-measurement module
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x0B4DD0 --addr 0x0B4DD0 --len 0x130
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x0B5878 --addr 0x0B5878 --len 0x50
./.venv/bin/python3 tools/find_branch_refs.py data/passat_azx_ori.bin 0x0B5188 0x0B5140 0x0B5610
```

The per-loop instruction count and the 500-byte cursor step (method stated in
full rather than kept as a tool): build `logging/ecu_sim.py`'s
`Med9Handlers(animate=False)` (which runs the ten start-up entries), read the
13 words at 0x0B2678, `emu.call(p, reset=False)` each one except
0x0B5878/0x0B5978, repeat five times, and print `Result.insns` and the u32 at
0x7FB700 after each loop. `bg_task_tail` 0x11DA64 stops in the emulator after
153 instructions (at an OS call the harness does not model); it is counted as
153, which changes the total by well under 1 %.
