# Memory map and dump structure

Target: `data/passat_azx_ori.bin`, SHA-256
`b15590d3f1874ace3125c5d047c09a686db9b8bb498187663539ebab205609b3`,
2,605,056 bytes (0x27C000), read over OBD with Alientech KESSv2.

Unless marked otherwise every statement here is **VERIFIED-STATIC** and was
produced by the scripts in `tools/` or by disassembling the bytes quoted.
Re-run `python3 tools/layout_report.py data/passat_azx_ori.bin` to reproduce
the basics.

## 1. ECU identification

| Item | Value | Where |
|---|---|---|
| VW hardware number | `03H906032` | file 0x1CEE20 (also 0x802A8, 0x1D7884) |
| Engine label | `P3.2 FSI-EU4` | file 0x1CEE2D |
| Bosch hardware | `0261S02226` | 0x1CEE47, 0x65D2 |
| Bosch software | `1037382557` | 0x1CEE52, 0x8029E |
| Bosch project string | `56/1/MED91/3/6W6432.D//D9133_43K6P0/D9133_43K6P0/080506/` | 0x1C21F0 (dataset D9133_43K6P0, dated 2006-05-08) |
| Other version fields | `9387`, `4.7.6 A`, `P0000` | 0x1CEE42.. |
| Alientech classification | Bosch **MED9.1.1**, KESSv2 protocol 179, K-TAG BDM(MPC5xx) protocol 64 | COMMUNITY (K-Suite vehicle list) |

The "MED9.1.1" label matters: the 2 MB "MED9.1" units (2.0 TFSI) have a
flashless MPC562, whereas 2.5 MB reads like ours carry 512 KB of on-chip
flash, which points at the MPC563/MPC564 class. The boot code checks the
IMMR part number for 0x35 (MPC561/562) and takes a different path for other
parts, so the same firmware supports both hardware variants; on the non-0x35
arm it enables the on-chip flash (FLEN=1, section 2) and the firmware's own
programming route addresses it, so this unit's software expects the flash
part (VERIFIED-STATIC from the code). Read the MCU marking when the ECU is
opened to confirm the silicon.

## 2. What the dump contains

| File offset | Size | CPU address | Content |
|---|---|---|---|
| 0x000000-0x1FFFFF | 2 MB | 0x000000-0x1FFFFF | External flash (chip select CS0). Code, constant data, calibration. |
| 0x200000-0x27BFFF | 496 KB | **0x404000-0x47FFFF** | On-chip flash, minus its first 16 KB. Code only (no strings). |

Evidence for the tail mapping: of the 3,057 relative `bl` calls inside the
tail, 800 target the external flash, and **660 of those land exactly on
function entries** found by the earlier Ghidra pass when the tail is placed at
0x404000; zero do at 0x400000 or any other tested base. Three KWP handler
pointers stored as 0x4386D8, 0x4387CC, 0x427B50 resolve to clean
`stwu/mflr` prologues under the same mapping. The ten checksum descriptors for
0x404000-0x47FFFF all match with this mapping (section 6).

The region 0x400000-0x403FFF is **not in the dump**, and both the silicon and
the firmware explain why (A2 #6, E6; `re/findings/mpc5xx_registers.md` §5 and
§8, `re/findings/flash_programming.md`, `tools/flash_segments.py`):

* It is **UC3F small block 0** (MPC561/MPC563 Reference Manual, Fig. 21-8
  "512-Kbyte Array Configuration", p. 21-19): the first 16 KB of the array,
  independently erasable and independently protectable, and the host of the
  **shadow row** — the non-volatile store for the hard-reset configuration
  word (`UC3FCFIG`: FLEN / ISB / ETRE / OERC / DME / IP) and the censorship
  bits (§21.2.3, Table 21-6, p. 21-17). With ISB=1 that block is exactly
  0x400000-0x403FFF. It also holds the live exception-vector branch table
  (section 3). Contents: HYPOTHESIS until a BDM read exists.
* The firmware carries a three-entry **flash-device table** (0x082980 in the
  application's programming module, 0x01E71C in the RAM bootstrap loader):
  device 0 = `0x000000-0x3FFFFF` (CS0), device 1 = **`0x404000-0x47FFFF`**
  (on-chip UC3F), device 2 = `0xC00000-0xC7FFFF` (a second UC3F, absent on
  this hardware). Device 1 is live whenever RAM 0x7F8014 == 0x20, which the
  boot code sets on **every** MPC563 start (`mtspr 638, 0x0802`, i.e.
  FLEN=1/ISB=1, at file 0x123F0; the MPC561 arm at 0x12380 writes 0x0002 and
  0x10 instead — the part-number fork section 1 mentions).
* The array is erased in **ten** blocks from 0x400000: 16 KB (small block
  0), 48 KB at 0x404000, 48 KB at 0x410000, 16 KB at 0x41C000 (small block
  1), then six 64 KB blocks to 0x47FFFF — exactly Fig. 21-8. The geometry
  table is at 0x0825E4 and the matching UC3FCTL block-select bits at
  0x082684.
* **The OBD programming service accepts `0x404000-0x47FFFF` as a download and
  erase range** (`kwp_download_range_allowed`, 0x0889C8), and the driver
  relocated to RAM 0x804800 implements the full UC3F interlock sequence (SES →
  interlock write → EHV → poll HVS/PEGOOD). **Small block 0 is never reachable
  that way**: no whitelist row starts at 0x400000 and the erase loop only
  starts at the range start. So the missing 16 KB is not "skipped by
  KESSv2" — it is outside what the ECU's own route can address at all. The
  ECU's own checksum descriptors likewise cover 0x404000-0x47FFFF only
  (section 6).
* Two boot routines take the protection off and put it back: `uc3f_unprotect`
  (0x011CE0, `UC3FMCR[PROTECT] = 0` + `UC3FMCRE[SBPROTECT] = 0` + CS0
  write-protect off) and `uc3f_protect` (0x011D58). A normal boot ends
  **protected**; the programming boot path unprotects.

Community reads of the same part number via other tools are 2,621,440 bytes,
i.e. include the 16 KB. **Get a K-TAG/BDM read before the first write** — for
the 16 KB itself, which nothing in the firmware can produce (the plan, M1).

`med9_re/passat_azx_flash.bin` is simply the first 2 MB of the dump.

## 3. Address space seen by the CPU

The first instructions after reset (file 0x1004) read SPR 638 (IMMR), clear
the ISB field and set it to 1: `andi. r10,r10,0xFFF1` clears exactly the ISB
mask 0x0000000E and `ori r10,r10,0x2` sets bit 30, and ISB = `001` selects
base 0x0040 0000 (MPC561/MPC563 Reference Manual Rev. 1.2, §6.2.2.1.2,
Fig. 6-13 / Table 6-12, p. 6-28…6-29; `re/findings/mpc5xx_registers.md` §1).
So the MPC5xx internal memory map is relocated to **0x400000-0x7FFFFF**, and
all peripheral addresses below are the datasheet addresses plus 0x400000.
FLEN (bit 20, on-chip flash enable) is *preserved*, so it comes from the reset
configuration word. The same table gives PARTNUM (bits 0:7): **MPC561 = 0x35,
MPC563 = 0x36**, and only the MPC563/MPC564 have the 512 KB UC3F flash — so
the `cmpwi r3,0x35` at file 0x1236C is the "is this the flashless part?" test
and our silicon takes the other branch. The file
`ppc_med9/data/languages/ppc_med9.pspec` uses the unrelocated addresses and is
therefore wrong for this ECU.

| CPU range | What | Evidence |
|---|---|---|
| 0x000000-0x1FFFFF | External flash, primary mapping. BBC exception branch table 0x0-0xFF and the classic vectors to 0x1FFF (below), boot code from 0x1004, constant data **and code** 0x10000-0x1FFFF (the boot module's body plus 100 application functions at 0x019948-0x01E848, section 4), code 0x20000-0x14494F, calibration 0x1C0000-0x1DFFFF. | BR0 base 0x000000 written at 0x12010/0x12034/0x12064 |
| 0x200000-0x3FFFFF | Alias of the external flash (CS0 is an 8 MB region). Unused by code. | OR0 address mask 0xFF80xxxx from table at file 0x10020-0x1004C |
| 0x400000-0x47FFFF | On-chip flash (512 KB, UC3F). Holds the KWP/flash-programming services, a second copy of the application start-up, the application exception handlers (0x4055A0-0x4062A4) and the shared **Bosch/ASCET runtime library at 0x40C000-0x411FFF** (section 7). 0x400000-0x403FFF is small block 0 and is not in the dump (section 2). | section 2 |
| 0x480000-0x5BFFFF, 0x600000-0x6F7FFF | **Nothing responds.** Internal "Reserved for Flash" space; the memory controller does not serve addresses inside the internal 4 MB block. The three static references that land in 0x480000-0x5BFFFF are dead. | MPC561RM §10.8 p. 10-28 |
| **0x5C0000-0x5FFFFF** | **Dual-mapped window onto external flash 0x1C0000-0x1FFFFF** (CS0), set up by DMBR/DMOR at file 0x1250C. The firmware uses it for **calibration: 7,055 absolute references into 0x5C0000-0x5E2FFF** (2,196 of them form a table pointer, the rest are direct loads of single values). | `find_abs_refs.py --hist`; DMBR decode below |
| 0x6F8000-0x6F87FF | DECRAM (2 KB on-chip RAM). Boot copies **three** routines here in turn and runs each, to reprogram the flash chip select: 0xE4 bytes from file 0x10FEC (loop 0x11FAC-0x11FC8, called at 0x11FCC), 0x238 bytes from file 0x11118 (loop 0x120D8-0x120F4, called at 0x120FC) and 0x1D4 bytes from file 0x11350 (loop 0x12F78-0x12F80, called at 0x12F84); the three sources tile 0x10FEC-0x11523 exactly. The second also uses 0x6F8620+ as a scratch counter array during the TPU scan at 0x14604. | disassembly; the second routine runs cleanly in the emulator for every flash-type nibble at RAM 0x7F800C (`tests/test_emu.py::TestDecramRoutine`, VERIFIED-DYNAMIC) |
| 0x6FC000-0x6FC3FF | USIU: SIUMCR 0x6FC000, SIPEND 0x6FC010, BR0-OR3 0x6FC100-0x6FC11C, DMBR/DMOR 0x6FC140/0x6FC144, **TBSCR 0x6FC200 and TBREF1 0x6FC208** (the scheduler's clock: the time base with reference B), PISCR 0x6FC240 (the PIT, used only as a software-triggerable interrupt; PITC 0x6FC244 is never written), SCCR 0x6FC280, **PLPRCR 0x6FC284**, RSR 0x6FC288. The OS is **ETAS ERCOSEK V4.1.16** (string at file 0x345D0 and 0x9C3F0). | 145 refs; boot emulated (`emu/boot_trace.py`, VERIFIED-DYNAMIC); `re/findings/scheduler.md` |
| 0x6FC800 | UC3F flash control | 5 refs |
| 0x700000-0x707FFF | IMB modules: DPTRAM control 0x700000 / array 0x702000 — the boot copies **0x800 bytes from file 0x10624-0x10E23 to 0x702000-0x7027FF** (loop 0x18098-0x180A0, word count 0x200 read from file 0x10E24); the content (`3FFFFFFE 7FFFFEFE BFFF07FC ...`) looks like TPU3 microcode (HYPOTHESIS) — TPU3 A/B 0x704000/0x704400, QADC A/B 0x704800/0x704C00, QSMCM 0x705000 (49 refs; its QSPI drives the EEPROM), PPM 0x705C00, MIOS14 0x706000, **TouCAN A/B/C module base 0x707000/0x707400/0x707800** (the table at file 0x2BC38 holds base+0x80 = CANMCR; the 16 message buffers of a module start at base+0x100), UIMB 0x707F80. 0x708000-0x77FFFF is "Reserved for IMB". | table of the three CAN register bases at file 0x2BC38; MPC561RM Fig. 1-4 p. 1-13, Table 16-10 p. 16-17; `emu/boot_trace.py` |
| 0x780000-0x7800FF | CALRAM / READI control | 1 ref |
| 0x7F8000-0x7FFFFF | On-chip SRAM 32 KB — the manual's **CALRAM**, with 0x7FF000-0x7FFFFF a 4 KB overlay section. r1 = 0x7FEFFC at file 0x10DC is the **boot** stack top only; the application's task stack is **0x7FF3C0-0x7FF76F** (section 4). Patch RAM 0x7FFB00-0x7FFBFF (`06_patch_pipeline.md` §3). | MPC561RM §22 p. 22-1; `re/findings/ram.md` |
| 0x800000-0x807FFF | External SRAM 32 KB (CS1), aliased 8× up to 0x83FFFF. Ordinary `.bss`: **0x800004-0x80498F is cleared at every cold start** (`ram_clear_block` 0x06D8F8 from `app_init` 0x04CCD4), and 0x804800-0x808687 is where a KWP programming session copies the flash driver and **runs** it (the tail wraps onto 0x800000-0x800687). Not a retention area. RAM init table at file 0x1C2E78 gives 0x800000..0x807FF8; highest static reference 0x805784, a live dispatch table. | BR1 base 0x800000 at 0x12118; `re/findings/ram.md` §3 |
| 0x900000-0x93FFFF | CS2 external device, 20 refs. Unknown. | BR2 at 0x1216C |
| 0xA00000-0xA07FFF | CS3 external device, 2 refs. Unknown. | BR3 at 0x121A8 |

**Exception vectors** (A2 #6, B1 #8; `re/findings/mpc5xx_registers.md` §5,
`re/findings/boot.md`). MSR is set to 0x3942/0x2942 everywhere (IP=1, FP=1,
EE=0 at those points), but nothing is ever fetched from 0xFFF00000: the BBC's
**Exception Table Relocation** (MPC561RM §4.3.1, Table 4-1/4-2, p. 4-8…4-10)
rewrites every `0xFFF0 0x00` vector fetch to `Page_Offset + <small offset>`,
**two words (8 bytes) per entry**, with `Page_Offset = 0x400000 × ISB` for
`BBCMCR[OERC] = 00`. **File 0x000000-0x0000FF is exactly such a branch
table** (`tbl_etr_branch_table`): 32 slots of 8 bytes, every offset Table 4-1
defines populated (+0x008 system reset → `ba 0x3C4`, +0x028 external interrupt
→ `ba 0x80028`, +0x010 machine check → `ba 0x164`, …, +0x0F8 → `ba 0x3A4`),
the undefined gaps filled with `ba 0x144`. ETRE (BBCMCR bit 19) and OERC (bits
24:25) come from the reset configuration word and are never written by
software — the boot code read-modify-writes BBCMCR (SPR 560) at file
0x1118-0x1138 but only sets bit 30 (DCAE). **File 0x080000-0x0800FF is a
second ETR table** of the same layout (`tbl_etr_branch_table_app`), pointing
at the application exception handlers at 0x4055A0-0x4062A4 in on-chip flash.
Both tables send the external interrupt to a fatal spin, while the application
demonstrably runs with MSR[EE] = 1 (it uses the RCPU `EIE`/`EID` SPRs 80/81) —
so **the live table is the one at 0x400000-0x4000FF, inside the 16 KB the dump
is missing** (section 2), another argument for the BDM read. The classic
0x100-spaced table (reset 0x100 → `ba 0x49C` → 0x1004; the other used vectors
→ 0x110F0, a fatal-error handler that spins) is kept for the case RCW[IP]=0
and is incomplete (file 0x500, the external interrupt vector, is zero). The
external address bus is only ADDR[8:31] (24 pins, MPC561RM §9.1 p. 9-1) and
BR0/OR0 match no 0xFFFxxxxx address, so a 0xFFF00xxx fetch could not reach CS0
even if it were issued.

**Chip selects** (A2 #6; MPC561RM Fig. 10-23/10-24, Table 10-8/10-10,
p. 10-32…10-36; `re/findings/mpc5xx_registers.md` §3 and §4). Decode of the
values this firmware writes:

| Reg | Value | Region | Port | Wait states |
|---|---|---|---|---|
| BR0 | 0x000103 / 0x00090B | base 0x000000, valid, **write-protected**, burst inhibited | 32-bit / **16-bit** | — |
| OR0 | 0xFF800650, 0xFF8006FF, 0xFF800150/140/130/120 | AM=0xFF800000 ⇒ **8 MB, 0x000000-0x7FFFFF** in every clock mode | | SCY 5, 15, 5, 4, 3, 2 |
| BR1 | 0x800403 | base 0x800000, valid | **8-bit** | — |
| OR1 | 0xFFFC0100…0xFFFC0180 | AM=0xFFFC0000 ⇒ **256 KB, 0x800000-0x83FFFF** (32 KB SRAM aliased 8×) | | SCY 0…8 |
| BR2 | 0x900823 | base 0x900000, valid, **byte enables (WEBS=1)** | **16-bit** | — |
| OR2 | 0xFFFC0110 | **256 KB, 0x900000-0x93FFFF** | | SCY 1 |
| BR3 | 0xA00003 | base 0xA00000, valid | 32-bit | — |
| OR3 | 0xFFFF8C20 / 0xFFFF8C30 | AM=0xFFFF8000 ⇒ **32 KB, 0xA00000-0xA07FFF** | | SCY 2 / 3 |

`0x0FFF1F00` is **not** a valid OR2 (its AM would mask address bits 0-3 and
repeat the region every 256 MB); treat it as a mis-attributed constant.

CS0 is an 8 MB region, but an address inside the internal 4 MB block
0x400000-0x7FFFFF is served internally and *ignored by the memory controller*
(§10.8 p. 10-28), so the flash does not shine through wherever no internal
module responds. The one window that really does reach CS0 is opened by the
dual-mapping registers: `DMOR = 0x70000000`, `DMBR = 0x70000001` at file
0x1250C-0x12514 give BA=AM=`0b111000`, DMCS=000 (CS0), DME=1, ATM=000 (code
*and* data). Eqn. 10-1 (p. 10-25) `bus_address[0:16] == {0000000, ISB[0:2],
0, BA[1:6]}` with ISB=1 yields base **0x5C0000**, size 2^18 ⇒
**0x5C0000-0x5FFFFF → external flash 0x1C0000-0x1FFFFF**. That is precisely
the calibration range the firmware addresses (r2 = 0x5C9FF0, section 4) and
that the checksum table at file 0x1C3300 covers (section 6), which
independently confirms the mapping. `tools/med9lib.py` (`REGIONS`,
`cpu_to_file()`, `file_to_cpu(prefer_high=True)`) encodes exactly this: a
high-alias address exists only for file offsets ≥ 0x1C0000.

## 4. Small data area registers

| Register | Value | Set at (file) | Used for |
|---|---|---|---|
| r1 | 0x7FEFFC | 0x10D8 | the **boot** stack. The application runs on the ERCOSEK task stack **0x7FF3C0-0x7FF76F**: `app_entry_crt0` sets r1 = 0x7FF768, and the RAM below 0x7FEFFC is reused as application data (kernel stack descriptor at flash 0x09B6F8; cold-start fill of exactly 0x7FF3C0-0x7FF76B by `FUN_0012C25C`). |
| r13 | **0x7FFFF0** | 0x10E0, 0x986AC, 0x9E3E0, tail 0x201588 (cpu 0x405588) | read/write small data. **64,717 D-form loads/stores plus 2,640 `addi`/`ori`**, i.e. RAM **0x7F8000-0x8076D6** spanning both SRAMs, measured over 0x000000-0x1BFFFF and 0x404000-0x47FFFF only (C2 #23). **The calibration block 0x1C0000-0x1FFFFF must not be disassembled**: treating it as code invents 51 `lbzu/lfdu/stfsu rX,d(r13)` *update*-form accesses, which no compiler emits (they would overwrite r13), and a bogus "highest r13 reference 0x807CB9". |
| r2 | **0x017FF0** during boot | 0x10E8 | read-only small data in the constant block 0x10000-0x1FFFF |
| r2 | **0x5C9FF0** in the application | 0x86010, 0x862DC, 0x986B4, 0x9E3E4, tail 0x201590 | read-only small data in calibration 0x5C1FF0-0x5D1FEF (this is the value the MED9.1 community documents) |

### 4.1 The boot module, exactly (B1, issue #8; `re/findings/boot.md` §1, §3)

**The boot module is 119 functions in 0x001004-0x01978F**, and only those
run under r2 = 0x017FF0. Seeding a static call-graph walk (`tools/callgraph.py`)
at `boot_start` (0x1004) and `boot_main_init` (0x12328) and stopping at
`app_sda_setup_a/b/int` (0x986AC, 0x9E3E0, 0x405588) reaches 119 functions;
**all 118** `bl` targets that lie in 0x001000-0x019800 are among them, and
**no** boot function calls or tail-branches outside that window, so the
subgraph is closed. Its instructions occupy twelve ranges (0x001004-0x001287,
0x0110F0-0x011117, 0x011524-0x011CB3, 0x011CE0-0x01382B, 0x01383C-0x0138D3,
0x013D64-0x0155FF, 0x015620-0x016843, 0x016860-0x0179EB, 0x017A10-0x017B33,
0x017CF0-0x01810B, 0x01814C-0x018227, 0x01831C-0x01978F). These are what
`ghidra_scripts/b1_context_and_symbols.py` gives r2 = 0x017FF0; everything
else in 0x000000-0x1FFFFF and 0x404000-0x47FFFF keeps 0x5C9FF0. Code *does*
live in the block 0x10000-0x1FFFF: the boot module's own body plus **100
application functions between 0x019948 and 0x01E848**, none of them reachable
from the boot seeds — which is why `med9_setup.py --boot-r2`, a blanket
0x1000-0x1FFFF, is too generous and stays off (`03_tooling.md` §2.1).

**Check.** `tools/r2_context.py` resolves every r2-relative D-form access in
the image under its assigned base: `unmapped = 0` and `outside_window = 0` on
both sides (177 boot references, 3,338 application references). The 120 that
land on an 0xFF byte are all accounted for: the 10 boot ones are `addi`
instructions computing the *base pointer* of `tbl_or_values_by_clockmode`
(0x010020), whose OR entries start with 0xFF by construction; the 110
application ones are calibration cells that hold 0xFF.

**The one apparent outlier, `lis r2,0xD5; addi r2,r2,-0x3210` at 0x86330
(r2 = 0xD4CDF0), is inert.** Six instructions earlier the same function
computes `0x804800 - 0x081A00 = 0x782E00`, and **0x5C9FF0 + 0x782E00 =
0xD4CDF0 exactly**: it is the application SDA2 base with a flash-to-RAM
relocation delta folded in, emitted by the linker for the routine that is
copied from flash 0x081A00 to external SRAM 0x804800 and run there
(`bl 0x806EA0` at 0x861B0, flash original 0x840A0 — the external-flash
programming driver). The relocated block 0x081A00-0x085400 contains zero
r2-relative references, and 0x862DC restores r2 = 0x5C9FF0 on the way out.
Leaving r2 = 0x5C9FF0 over 0x86284-0x86350 is therefore correct.

### 4.2 The RAM, surveyed (C2, issue #23; `re/findings/ram.md`, `re/ram_map.csv`)

`tools/ram_survey.py` gives a per-byte picture of 0x7F8000-0x807FFF
(`python3 tools/ram_survey.py data/passat_azx_ori.bin --csv re/ram_map.csv`).
What it settled:

* **Stack.** The application stack is **0x7FF3C0-0x7FF76F** and grows down;
  0x7FEFFC is the boot stack only (table above).
* **The external SRAM is cleared at every cold start**, 0x800004-0x80498F, by
  `ram_clear_block` 0x06D8F8 and `app_init` 0x04CCD4. It is ordinary `.bss`,
  not a retention area; only 0x800000-0x800003 and the programming-copy area
  survive a reset. **0x804800-0x808688 is the KWP programming copy's
  destination** and is *executed* there; with a 32 KB CS1 part the tail wraps
  onto 0x800000-0x800687. Nothing of a patch may live there.
* Every reference-free gap larger than 128 bytes inside the used `.bss` is
  the body of an array or buffer whose base is the last referenced byte
  before it (`ram_survey.py --indexed`); a gap in the map is not free space.
* **Patch RAM: 0x7FFB00, 0x100 bytes**, in the middle of **0x7FF770-0x7FFFEB**,
  2,172 bytes above the task stack that carry no r13 displacement, no
  absolute `lis`+D-form, no pointer word in either flash region and no
  measuring-variable cell. VERIFIED-STATIC; the runtime RequestUpload
  snapshots that make it VERIFIED-DYNAMIC are `08_bench_playbook.md` step 4,
  and nothing is flashed before them. Rules of use: `06_patch_pipeline.md` §3.

## 5. Structure markers and directories

Blocks start with a `5A5A5A5A` marker: 0x6600, 0x20000, 0x80100, 0x9E4FC,
0x144950, 0x1C2000, 0x1E2500 (others are inside data tables). Notable:

- 0x20000: `0001C000 0001FFFF 11655904 EE9AA6FB 5A5A5A5A` is the last
  checksum descriptor of the constant block followed by the marker; the code
  that follows (0x20004) writes the ROM-check result bytes at r13-0x7DA6..-0x7DA5
  (RAM 0x7F824A/0x7F824B). The community "crc_check" patch for 1K8907115F
  changes exactly these constants (`li r3,0`/`li r4,0xFF` -> 0x55/0xAA).
- 0x80100: `5A5A5A5A 33333333` + directory of code-section addresses
  (0x080180, 0x080140, 0x0801C0, 0x080200, 0x09E4FC, 0x144950, 0x09E3B4,
  0x09E374, 0x089F10). 0x80180 holds the magic `AFFE0815`. **Slot +0x20 of
  the directory (0x080120) is 0x0009E3B4, the application entry
  `app_entry_crt0`**: `boot_main_init` reaches it with
  `lwz r9,-0x1600(r13); lwz r9,0x20(r9); mtlr r9; blrl` at 0x13070-0x1307C,
  the single handover from boot to application; if it ever returns,
  `boot_start` falls into `ba 0x110F0` (fatal spin). File 0x080000-0x0800FF,
  just before it, is the application's exception branch table (section 3).
- 0x144950: `02030600 5A5A5A5A` then 0xFF: end of code. This is the
  signature the MED9Toolchain uses to find free flash.
- 0x1C2000: `5A5A5A5A CCCCCCCC` + calibration directory (0x1C2080 with
  `AFFE0815`, 0x1C2040, 0x1C20C0, 0x1C2100, 0x1E2500).
- 0x1CEE20: ECU identification block (section 1).

Free flash (all 0xFF): 0x144954-0x1BFFFF except a few bytes near 0x1BFFE4,
and 0x1E2510-0x1FFFFF. See `06_patch_pipeline.md` for the allocation policy.

## 6. Integrity checks (block checksums)

Algorithm: **32-bit sum of big-endian 16-bit words over [start, end]
inclusive**, stored with its bitwise complement. Descriptor = 16 bytes
`start, end, sum, ~sum`. All 65 descriptors verify on the original
(`python3 tools/checksum.py verify -q data/passat_azx_ori.bin`).

| Table (file) | Entries | Covers | Notes |
|---|---|---|---|
| 0x001FF0 | 1 | 0x000000-0x001FFF | boot/vector block |
| 0x01FFC0 | 4 | 0x010000-0x01FFFF in 16 KB blocks | constant data; the table lies inside its last block |
| 0x0A0000 | 54 | 0x020004-0x028A1F, 0x020000-0x02FFFF, 32 KB blocks to 0x13FFFF, 64 KB blocks 0x140000-0x1BFFFF, then **0x404000-0x47FFFF** in 16/32/64 KB blocks | code (external and on-chip flash); the table lies inside block 0xA0000-0xA7FFF |
| 0x1C3300 | 6 | 0x5C2E00-0x5C32FF, 0x5C2000-0x5C223F, 0x5C2E00-0x5CFFFF, 0x5D0000-0x5DFFFF, 0x5E0000-0x5EFFFF, 0x5F0000-0x5FFFFF | calibration, addressed via the high alias |

Because a `sum/~sum` pair always contributes 0x1FFFE to a 16-bit word sum,
descriptors that lie inside their own block are still solvable in one pass;
`tools/checksum.py fix` does this and is a no-op on the original.
Uncovered ranges: 0x002000-0x00FFFF (boot code around 0x1000-0x6600 and the
0x6C00 immobiliser pairing area reported by the community), 0x1C0000-0x1C1FFF,
0x5C2240-0x5C2DFF, the missing 0x400000-0x403FFF.

**Runtime use (E6; `re/findings/flash_programming.md` §3.2, §5.3).** The 65
sum/~sum descriptors are **never recomputed at run time**: every reference to
the 54-entry table at 0xA0000 (0x020764, 0x02081C, 0x089E68, 0x09DD94,
0x09DE28, 0x09DEBC) is inside the programming module, i.e. the tool-facing
side. What the firmware does compute is a **CRC-32** — reflected polynomial
0xEDB88320, table built at RAM 0x800288 — over the three ranges listed at file
0xA3A10, `{0x020000,0x1BFFFF}, {0x404000,0x47FFFF}, {0x5C2E00,0x5FFFFF}`,
hashed 0x64 bytes at a time in the background loop by `flash_crc_task`
(0x011CB10), once per power cycle. **The result is published to RAM 0x7F9178
and never compared against anything**: a reported value, not a gate. It is
useful all the same — the stock image publishes **0x5562139F**, which tells a
bench unit's software apart (`logging/sessions/flash_crc.json`,
`08_bench_playbook.md` step 2d), and a patched image publishes its own value
(`ecu_sim.py --print-flash-crc`). The words at file 0x1E73C and 0x829A0 that
look like `00404000 0047FFFF` descriptors followed by RAM pointers are **entry
1 of the flash-device table** (section 2) — start, end and five driver entry
points — not checksum descriptors.

**The one check that does stop the ECU** is the calibration marker:
`app_check_cal_marker` (0x06DD3C, called from 0x04CF6C in `app_init`'s chain)
requires the halfword at **0x5E2500 = file 0x1E2500** to be `0x5A5A`;
otherwise it writes 0xBB44E169 to RAM 0x7F8000 and hangs, so the watchdog
resets the ECU straight back into the flash loader (recoverable, not a brick;
`06_patch_pipeline.md` §6). So there is **no signature and no boot-time
verdict on flash content**: the block sums have to be right because the
*tool* checks them, not because the ECU would. The first flash is still an
unmodified, checksum-fixed file on the bench ECU (`08_bench_playbook.md`
step 5); it proves the write route and the tool, not the firmware.

**EEPROM.** An ST M95160-class 2 KB part on PCS0 of the QSMCM QSPI (SPI mode
0, 8 bits per transfer, SCK ≈ 1.25 MHz), laid out as 32 EEP_CONF blocks by
the 12-byte-record table at file 0xB2FF0 (`tbl_eep_conf`) with **no gap and
no spare block**; each block carries its own checksum, the 16-bit sum of the
payload bytes stored bit-complemented — not the flash algorithm above, so do
not point `tools/checksum.py` at EEPROM images (`tools/eeprom_map.py --check`
does the EEPROM variant). Layout and block manager: `re/findings/eeprom.md`
(VERIFIED-STATIC from the firmware); the part itself has not been read.
Community tools: E2PA, EliasTuning/MED9-EEPROM-Tool.

## 7. Tables located so far

| File / CPU address | Table | Detail |
|---|---|---|
| **0x2B820** | KWP2000 / OBD service dispatch, **28 entries x 20 bytes**: `SID FF FF FF, session-mask, handler, handler2, extra`. The dispatcher reads the table address and count from a configuration structure; a byte-pattern walk from 0x2B870 finds only the last 24 entries and misses SIDs 0x12, 0x3E, 0x1A, 0x83 (B3, #13) | SIDs 12 3E 1A 83 14 21 3B 2C 18 17 81 10 31 32 35 36 37 27 82 20, OBD 01 02 03 04 06 07 08 09. Gated by diagnostic session only (entry+4 = 1<<session), not by SecurityAccess. **No 0x23 ReadMemoryByAddress, no 0x3D.** 0x2C DDLI (ids 0xF0-0xF9) + 0x21 need only a session; 0x35/0x36 RequestUpload/TransferData need session 0x86 (security level 2, key = seed + 0x11170, constant at 0xA331C, VERIFIED-DYNAMIC by emulation; level 1 = 5-round LFSR mask 0x5FBD5DBD). Dispatcher `kwp_service_dispatch` at 0x13E98C. Handlers for 0x3E 0x1A 0x83 0x81 0x82 0x20 0x31 0x32 0x06 live in on-chip flash. TesterPresent takes no sub-function (`3E`, not `3E 01`); `21 G` answers group *G* and *G*+0x7F, only 1..127 requestable. Full detail: `re/findings/kwp.md`. |
| 0x2B820, OBD entries | J1979 modes 0x01-0x09 are nine entries of the same table, gated to internal sessions 4 and 6. Mode 01 is `obd_mode01_h1` (0x5D0F4); a PID needs a byte in the dense class table at 0x0A39B4 (index = PID) **and** a slot in one of five (record-pointer, PID, support-mask) lists in calibration 0x5C5D24-0x5C5E1B, all five exactly full (41 entries). The support bitmaps are RAM 0x801215-0x801220, rebuilt per diagnostic connection by `obd_pid_support_build` (0x5CBE8) | Requests reach it only on the **functional** id 0x7DF (a physical 0x7E0 request is received and never answered, gate `li r3,0` at 0x2C29C); answers on 0x7E8; silence means "no"; the connection times out 5 s after the last answer. `re/findings/obd.md` (F6, G1, H3). |
| 0x088174 | second, 13-entry KWP stack of the **programming session** (`10 85` reboots the ECU into it), SID 0x34 `RequestDownload` | accepted address ranges are hard-coded in `kwp_download_range_allowed` (0x0889C8), exact start/end match else NRC 0x42: 0x020000-0x07FFFF, 0x0A0000-0x1BFFFF, **0x404000-0x47FFFF**, 0x080000-0x09FFFF (rewritten to 0x1C0000-0x1DFFFF), and the calibration ranges 0x1C0000/0x1E0000 (variant-gated). 0x000000-0x01FFFF and 0x080000-0x09FFFF as such are refused. `re/findings/flash_programming.md` §3. |
| 0x082980 (application programming module), 0x01E71C (RAM bootstrap loader) | **flash-device table**, 3 entries x 0x1C: `{start, end, five driver entry points}` | device 0 = 0x000000-0x3FFFFF (CS0), device 1 = 0x404000-0x47FFFF (UC3F), device 2 = 0xC00000-0xC7FFFF (absent). `tools/flash_segments.py --devices`. Section 2. |
| 0xA3A10 | **CRC-32 range list** for `flash_crc_task` | `{0x020000,0x1BFFFF}, {0x404000,0x47FFFF}, {0x5C2E00,0x5FFFFF}, {0,0}`; result published to RAM 0x7F9178, never compared. Section 6. |
| 0x0 and 0x080000 | **BBC exception branch tables** (`tbl_etr_branch_table`, `tbl_etr_branch_table_app`), 32 slots x 8 bytes | boot and application handlers respectively; the live one is at 0x400000, not in the dump. Section 3. |
| 0xB2FF0 | **`tbl_eep_conf`**, 32 EEP_CONF records x 12 bytes covering EEPROM 0x000-0x7FF | driven by `nvm_block_request` (0x06131C); block 8 (EEPROM 0x1C0, copy 0x1E0, mirror RAM 0x7F9F80) carries the 17 tester adaptation channels at payload +2..+18 and the flex-fuel E% store at +19. `re/findings/eeprom.md`, `tools/eeprom_map.py`. |
| 0x2BC38 | TouCAN **register** base table | 0x707080, 0x707480, 0x707880. These are the CANMCR addresses = module base + 0x80; the module bases are 0x707000/0x707400/0x707800 and the 16 message buffers of module *x* start at base+0x100 (MPC561RM Table 16-10 p. 16-17). |
| 0x2BC50 | pointer 0x0002BF50 (CAN configuration structure) | low-alias address |
| 0x2BC90 | **CAN receive table**, header + 21 entries x 16 bytes `index, 0x01mmnn08 (module/slot/dlc), 4, CAN-ID` | IDs 0x1A0 0x5A0 0x4A0 0x440 0x540 0x320 0x442 0x1AC 0x0C2 0x050(dlc 4) 0x51A 0x5E0 0x390 0x38A(dlc 4) **0x7FF 0x7FF** 0x2A0 0x368 **0x7FF 0x7FF** 0x5C0. The four 0x7FF entries are unused receive slots. |
| 0x2BDF0 | **CAN transmit table**, header + 16 entries `index, CAN-ID, 0x0101xxxx, dlc` | 0x7C7 0x280 0x288 0x380 0x480 0x488 0x580 0x588 0x48A 0x38A(4) 0x284(6) 0x56A 0x7C4 0x7C5 0x7C5 0x7C6. These are the Motor_x frames the ECU sends (the old notes called this the "registered CAN IDs" and mislabeled several). 0x7C4 matches the CCP DTO id reported for MED9.1. |
| 0xA5658 | **TKMWL measuring-variable table**, 2200 x 4 B handler pointers (0xA5658-0xA78B7) | Indexed by the dispatcher at 0x45768 (`lis r12,0xA; addi r12,r12,0x5658; lwzx r31,r12,id*4; mtlr; blrl`), which 360trev/MED9inf finds by signature. 665 ids are implemented, 1535 point at the "not available" stub 0x38EC4. Each handler leaves a VAG (formula, A, B) triple in RAM 0x7FD06F-0x7FD071 through the helper at 0x38EB4. Reached only from the KWP SID 0x21 route (0x35F6C -> 0xA2CC4 -> 0x3583C -> 0x3574C -> 0x45768) and from 0x357E0, which the on-chip flash calls. `tools/measuring_vars.py`, `re/measuring_vars.csv`, `re/findings/measuring_vars.md`. |
| 0x1C5518 / **0x5C5518** | **Measuring-block group table**, 4 fields x 255 groups of u16 variable ids | `entry(field, group) = 0x5C5518 + field*0x1FE + group*2`; `addi r29,r2,-0x4AD8` at 0x35760 and 0x357F8 with the application r2 = 0x5C9FF0. Full listing in `re/findings/measuring_groups.txt`. |
| 0x12004-0x121D8 | memory controller init (BR/OR from clock-mode tables at file 0x10020-0x1009C) | |
| 0x40C000-0x411FFF (file 0x208000-0x20DFFF) | **Bosch/ASCET runtime library** in the on-chip flash: 44 interpolation helpers (1-D curve, 2-D map, shared-axis "group" forms, axis search, value-array interpolators) plus saturating arithmetic, debounce counters, ramps and filters | Called 1,343 times from the external-flash application across the region boundary. Every calibration table in the dump is reached through one of them. Full list with argument conventions and data layout: `re/findings/calibration_maps.md`; inventory in `re/calibration_draft.csv` (2026-09-15, B5, issue #19). |
| 0x11E44 | `boot_or_adjust`: OR value adjust | `rlwinm r3,r3,0,24,22` (0x5463062C) = `r3 &= 0xFFFFFEFF`, i.e. **clears bit 0x100 (bit 23)**, and only when RAM byte 0x7FE9E8 is exactly 1. Called from the nine sites in 0x11F08-0x121B4; the result is stored to OR0/OR1/OR2/OR3 (0x6FC104/0x10C/0x114/0x11C), so bit 0x100 is the OR-register burst-inhibit field (field name COMMUNITY, MPC5xx UM; bit position VERIFIED-STATIC). Emulated both ways, 2026-09-15 (VERIFIED-DYNAMIC, `emu/`, `tests/test_emu.py`). Note the table entry 0xFF800650 already has that bit clear, so it is returned unchanged. |

## 8. Corrections to `med9_re/old_work`

- "Flash 0x0-0x200000 is the whole image": the dump has a second region
  (on-chip flash at 0x404000). Analysing only the first 2 MB misses the KWP
  services and half of the application start-up.
- "Internal RAM 0x3F8000, peripherals 0xC0000000": wrong; ISB=1 puts them at
  0x7F8000 and 0x6FC000/0x70xxxx. The `ppc_med9` Ghidra spec repeats the error.
- "r2 = 0x17FF0": true only during boot; the application uses 0x5C9FF0.
- "CAN ID table at 0x2BE00 = received IDs": it is the transmit table; the
  receive table is at 0x2BC90.
- "Crash at 0x6F80B8 in unmapped 0x900000": the code at 0x6F8000 is the
  DECRAM-resident routine copied from 0x11118; the emulator lacked DECRAM,
  the ISB=1 peripheral map and a stub for the CS2 device.
- The "free space" list is right about 0x144954+ but those ranges are inside
  checksummed 64 KB blocks; every write there needs `tools/checksum.py fix`.
- Function-size based guesses about "fueling functions" are unverified.

## 9. Revision log

Facts in this document are corrected in place (`04_re_guidelines.md` §8). This
table records what changed and when, so a decision that relied on an older
statement can be traced to it. The evidence for each line is in the findings
file named; the section that carries the fact today is in brackets.

| Date | Brief | What changed | Evidence |
|---|---|---|---|
| 2026-09-15 | A2 (#6) | 0x480000-0x5FFFFF and 0x600000-0x6F7FFF are **not** flash aliases; only 0x5C0000-0x5FFFFF reaches CS0, through DMBR/DMOR [§3]. The table at file 0x0 is a BBC ETR branch table, not a classic PowerPC vector table, and the "0xFFF00xxx hits CS0" hypothesis is refuted [§3]. The 16 KB at 0x400000 is UC3F small block 0 with the shadow row [§2]. The 0x2BC38 table holds CANMCR addresses, not module bases [§7]. `med9lib.REGIONS` / `cpu_to_file()` were narrowed in the same commit; `re/symbols.csv` gained `tbl_etr_branch_table`. | `mpc5xx_registers.md` |
| 2026-09-15 | A3 (#10), A5 (#21/#24) | The TKMWL table starts at 0xA5658, not 0xA5654 (the toolchain signature matched one instruction early); the result helper is 0x38EB4, not 0x38EA8 [§7]. Both had been HYPOTHESIS and were never used for a decision. The old "crash at 0x6F80B8 in unmapped 0x900000" was the old emulator's, not this code's: the DECRAM routine runs cleanly for every flash-type nibble [§3, §8]. | `measuring_vars.md`, `tests/test_emu.py` |
| 2026-09-15 | B1 (#8, #11) | The boot module is exactly 119 functions in twelve ranges; 0x10000-0x1FFFF is constant data *and* code; three DECRAM routines, not two; the r2 = 0xD4CDF0 outlier at 0x86330 explained; 0x80120 is the application entry; a second ETR table at file 0x80000; the scheduler clock is TBSCR/TBREF1, the PIT is a software interrupt only, the OS is ERCOSEK V4.1.16 [§3, §4, §5]. | `boot.md`, `scheduler.md` |
| 2026-09-15 | B3 (#13) | The KWP dispatch table is 0x2B820 × 28 entries, not 0x2B870 × 24 (the byte-pattern walk missed SIDs 0x12, 0x3E, 0x1A, 0x83); the flags word is a diagnostic-session mask, not a security requirement; `seed + 0x11170` is level 2 and VERIFIED-DYNAMIC [§7]. | `kwp.md`, `tools/kwp_seckey_verify.py`, `tools/kwp_upload_verify.py` |
| 2026-09-15 | B5 (#19) | The on-chip flash also holds the Bosch/ASCET runtime library 0x40C000-0x411FFF with all 44 table-lookup helpers; the calibration table layouts (`val[iy*nx + ix]`, second axis argument = X); 1,066 tables detected, 49.0 % coverage of 0x5C2000-0x5E2FFF; the free space 0x5E2510-0x5FFFFF confirmed all 0xFF [§3, §5, §7]. | `calibration_maps.md`, `calibration_coverage.md` |
| 2026-09-16 | C2 (#23) | r13 figures re-measured over code only (64,717 accesses, top 0x8076D6; the old 64,727 / 0x8073E9 came from disassembling calibration); the application stack is 0x7FF3C0-0x7FF76F and 0x7FEFFC is boot-only; the external SRAM is cleared at cold start (also corrects `eeprom.md` §6); patch RAM 0x7FFB00 [§3, §4]. | `ram.md` |
| 2026-09-17 | E6 (#26-#28, #32) | The "runtime checker" lists at 0x1E73C / 0x829A0 are the flash-device table; 0xA3A10 drives a CRC-32 that is published, not compared; the 65 block sums are never recomputed at run time; the only boot-time gate is the `5A5A` marker at 0x1E2500; the OBD route can program 0x404000-0x47FFFF but never small block 0, so the missing 16 KB is not "skipped by the tool" [§2, §6]. | `flash_programming.md` |
| 2026-09-24 | consolidation | The dated notes and this document's "Corrections" appendix were folded into the sections above; no fact changed. | git history |
