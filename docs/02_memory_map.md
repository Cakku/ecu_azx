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
parts, so the same firmware supports both hardware variants. Read the MCU
marking when the ECU is opened (COMMUNITY + inference).

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

The region 0x400000-0x403FFF is **not in the dump**. The firmware references
0x400000, 0x403FFF and 0x404000 as block boundaries and the checksum tables
start at 0x404000, so the missing 16 KB is most likely a protected boot/loader
sector that KESSv2 skips (HYPOTHESIS). Community reads of the same part number
via other tools are 2,621,440 bytes, i.e. include it. **Get a K-TAG BDM read
before the first write** (see the plan).

> **2026-09-15 (A2, issue #6) — the missing 16 KB explained. VERIFIED-STATIC
> (silicon) + HYPOTHESIS (contents).** The MPC561/MPC563 reference manual,
> Fig. 21-8 "512-Kbyte Array Configuration", p. 21-19, shows that the UC3F
> array's first 16 KB is **"small block 0"**, an independently erasable and
> independently protectable block that also **hosts the shadow row** — the
> non-volatile store for the hard-reset configuration word (`UC3FCFIG`, incl.
> FLEN / ISB / ETRE / OERC / DME / IP) and the censorship bits (§21.2.3,
> Table 21-6, p. 21-17). With ISB=1 that block is exactly 0x400000-0x403FFF.
> The ECU's own checksum descriptors cover 0x404000-0x47FFFF, i.e. the whole
> array *except* small block 0 — consistent with the ECU's flash routine never
> touching it and with the tool refusing to read it.
> Second reason to get the BDM read: the live exception-vector branch table is
> most likely inside those 16 KB too — see the note in section 3.
> Details: `re/findings/mpc5xx_registers.md` §8 and §5.

`med9_re/passat_azx_flash.bin` is simply the first 2 MB of the dump.

## 3. Address space seen by the CPU

The first instructions after reset (file 0x1004) read SPR 638 (IMMR), clear
the ISB field and set it to 1: the MPC5xx internal memory map is relocated to
**0x400000-0x7FFFFF**. All peripheral addresses below are the datasheet
addresses plus 0x400000. The file `ppc_med9/data/languages/ppc_med9.pspec`
uses the unrelocated addresses and is therefore wrong for this ECU.

> **2026-09-15 (A2, issue #6) — ISB encoding confirmed. VERIFIED-STATIC.**
> IMMR is SPR 638, ISB is bits 28:30 and `001` selects base **0x0040 0000**
> (MPC561/MPC563 Reference Manual Rev. 1.2, §6.2.2.1.2, Fig. 6-13 / Table 6-12,
> p. 6-28…6-29). `andi. r10,r10,0xFFF1` clears exactly the ISB mask 0x0000000E
> and `ori r10,r10,0x2` sets bit 30 ⇒ ISB=1. FLEN (bit 20, on-chip flash
> enable) is *preserved*, so it comes from the reset configuration word.
> The same table gives PARTNUM (bits 0:7): **MPC561 = 0x35, MPC563 = 0x36**,
> and only the MPC563/MPC564 have the 512 KB UC3F flash — so the `cmpwi r3,0x35`
> at file 0x1236C is the "is this the flashless part?" test and our silicon
> takes the other branch. Full decode: `re/findings/mpc5xx_registers.md` §1.

| CPU range | What | Evidence |
|---|---|---|
| 0x000000-0x1FFFFF | External flash, primary mapping. Exception vectors at 0x0-0x1FFF, boot code 0x1004, constant data 0x10000-0x1FFFF, code 0x20000-0x14494F, calibration 0x1C0000-0x1DFFFF. | BR0 base 0x000000 written at 0x12010/0x12034/0x12064 |
| 0x200000-0x3FFFFF | Alias of the external flash (CS0 is an 8 MB region). Unused by code. | OR0 address mask 0xFF80xxxx from table at file 0x10020-0x1004C |
| 0x400000-0x47FFFF | On-chip flash (512 KB). Holds the KWP/flash-programming services and a second copy of the application start-up. | section 2 |
| **0x480000-0x5FFFFF** | Alias of external flash offset 0x080000-0x1FFFFF. The firmware uses it only for **calibration: 7,055 absolute references into 0x5C0000-0x5E2FFF**, 3 into the rest. | `find_abs_refs.py --hist` |
| 0x600000-0x6F7FFF | Flash alias, unused. | |
| 0x6F8000-0x6F87FF | DECRAM (2 KB on-chip RAM). Boot copies **two** routines here in turn and runs each: 0xE4 bytes from file 0x10FEC (loop 0x11FAC-0x11FC8, called at 0x11FCC) and 0x238 bytes from file 0x11118 (loop 0x120D8-0x120F4, called at 0x120FC), to reprogram the flash chip select. The second also uses 0x6F8620+ as a scratch counter array during the TPU scan at 0x14604. | disassembly; second routine emulated 2026-09-15 (`tests/test_emu.py::TestDecramRoutine`) |
| 0x6FC000-0x6FC3FF | USIU (SIUMCR, memory controller BR0-3/OR0-3, DMBR/DMOR, PLL, timers). Confirmed by emulating the boot, 2026-09-15: SIUMCR 0x6FC000, SIPEND 0x6FC010, BR0-OR3 0x6FC100-0x6FC11C, DMBR/DMOR 0x6FC140/0x6FC144, SCCR 0x6FC280, **PLPRCR 0x6FC284**, RSR 0x6FC288 (VERIFIED-DYNAMIC, `emu/boot_trace.py`). | 145 refs |
| 0x6FC800 | UC3F flash control | 5 refs |
| 0x700000-0x70FFFF | IMB modules: TPU3 A/B 0x704000/0x704400, QADC A/B 0x704800/0x704C00, QSMCM 0x705000 (49 refs), MIOS14 0x706000, **TouCAN A/B/C 0x707080/0x707480/0x707880**, UIMB 0x707F80. The boot also copies **0x800 bytes from file 0x10624-0x10E23 to 0x702000-0x7027FF** (loop 0x18098-0x180A0, word count 0x200 read from file 0x10E24). The content (`3FFFFFFE 7FFFFEFE BFFF07FC ...`) looks like TPU3 microcode (HYPOTHESIS: TPU3 code RAM). | table of the three CAN bases at file 0x2BC38; 0x702000 from `emu/boot_trace.py` and disassembly, 2026-09-15 |
| 0x780000 | CALRAM control | 1 ref |
| 0x7F8000-0x7FFFFF | On-chip SRAM 32 KB. Stack top 0x7FEFFC. | r1 set at file 0x10DC |
| 0x400000-0x47FFFF | On-chip flash (512 KB, UC3F). Holds the KWP/flash-programming services and a second copy of the application start-up. | section 2 |
| **0x5C0000-0x5FFFFF** | **Dual-mapped window onto external flash 0x1C0000-0x1FFFFF** (CS0), set up by DMBR/DMOR at file 0x1250C. The firmware uses it for **calibration: 7,055 absolute references into 0x5C0000-0x5E2FFF**. | `find_abs_refs.py --hist`; DMBR decode, see note below |
| 0x480000-0x5BFFFF, 0x600000-0x6F7FFF | **Nothing responds.** Internal "Reserved for Flash" space; the memory controller does not serve addresses inside the internal 4 MB block. | MPC561RM §10.8 p. 10-28 |
| 0x6F8000-0x6F87FF | DECRAM (2 KB on-chip RAM). Boot copies a 0x238-byte routine from file 0x11118 here and runs it to reprogram the flash chip select (file 0x120C8-0x120FC). | disassembly |
| 0x6FC000-0x6FC3FF | USIU (SIUMCR, memory controller BR0-3/OR0-3, DMBR/DMOR, PLL, timers). | 145 refs |
| 0x6FC800 | UC3F flash control | 5 refs |
| 0x700000-0x707FFF | IMB modules: DPTRAM control 0x700000 / array 0x702000, TPU3 A/B 0x704000/0x704400, QADC A/B 0x704800/0x704C00, QSMCM 0x705000 (49 refs), PPM 0x705C00, MIOS14 0x706000, **TouCAN A/B/C module base 0x707000/0x707400/0x707800** (the table at file 0x2BC38 holds base+0x80 = CANMCR), UIMB 0x707F80. 0x708000-0x77FFFF is "Reserved for IMB". | table of the three CAN register bases at file 0x2BC38; MPC561RM Fig. 1-4 p. 1-13 |
| 0x780000-0x7800FF | CALRAM / READI control | 1 ref |
| 0x7F8000-0x7FFFFF | On-chip SRAM 32 KB — the manual calls it **CALRAM**, with 0x7FF000-0x7FFFFF a 4 KB overlay section. Stack top 0x7FEFFC. | r1 set at file 0x10DC; MPC561RM §22 p. 22-1 |
| 0x800000-0x807FFF | External SRAM 32 KB (CS1). RAM init table at file 0x1C2E78 gives 0x800000..0x807FF8; highest static reference 0x805784. | BR1 base 0x800000 at 0x12118 |
| 0x900000-0x93FFFF | CS2 external device, 20 refs. Unknown (accessed by the DECRAM routine, which is why the old emulator crashed at PC 0x6F80B8). | BR2 at 0x1216C |
| 0xA00000-0xA07FFF | CS3 external device, 2 refs. Unknown. | BR3 at 0x121A8 |

MSR is set to 0x3942/0x2942 everywhere (IP=1, FP=1, EE=0 at those points).
IP=1 means exception vectors are fetched from 0xFFF00000 at run time; how
that maps onto the flash has not been checked (HYPOTHESIS: address bits above
the external bus width are ignored, so 0xFFF00xxx hits CS0). The vector table
at file 0x0 uses absolute `ba` branches (reset 0x100 -> 0x49C -> 0x1004; all
other used vectors -> 0x110F0, a fatal-error handler that spins).

> **2026-09-15 (A2, issue #6) — MSR[IP]=1 solved; the old hypothesis is wrong.
> VERIFIED-STATIC.** Nothing is ever fetched from 0xFFF00000. The BBC's
> **Exception Table Relocation** (MPC561RM §4.3.1, Table 4-1/4-2, p. 4-8…4-10)
> rewrites every `0xFFF0 0x00` vector fetch to `Page_Offset + <small offset>`,
> **two words (8 bytes) per entry**, with `Page_Offset = 0x400000 × ISB` for
> `BBCMCR[OERC] = 00`. **File 0x000000-0x0000FF is exactly such a branch
> table**: 32 slots of 8 bytes, every offset Table 4-1 defines populated
> (+0x008 system reset → `ba 0x3C4`, +0x028 external interrupt →
> `ba 0x80028`, +0x010 machine check → `ba 0x164`, …, +0x0F8 → `ba 0x3A4`),
> the undefined gaps filled with `ba 0x144`.
> ETRE (BBCMCR bit 19) and OERC (bits 24:25) come from the reset configuration
> word and are never written by software — the boot code read-modify-writes
> BBCMCR (SPR 560) at file 0x1118-0x1138 but only sets bit 30 (DCAE).
> **Consequence:** once ISB=1 the live table is at 0x400000-0x4000FF, i.e.
> inside the 16 KB the dump is missing (section 2). The `ba 0x49C -> 0x1004`
> word at file 0x100 is the *classic-layout* reset entry, kept for the case
> RCW[IP]=0; that classic table is incomplete (file 0x500, the external
> interrupt vector, is zero), which is further proof the ETR table is the one
> in use. The external address bus is only ADDR[8:31] (24 pins, MPC561RM §9.1
> p. 9-1) and BR0/OR0 match no 0xFFFxxxxx address, so the old "address bits are
> ignored, 0xFFF00xxx hits CS0" guess is **refuted**.
> Details: `re/findings/mpc5xx_registers.md` §5.

> **2026-09-15 (A2, issue #6) — chip selects decoded. VERIFIED-STATIC.**
> BR/OR field layout: MPC561RM Fig. 10-23/10-24, Table 10-8/10-10,
> p. 10-32…10-36. Decode of the values this firmware writes:
>
> | Reg | Value | Region | Port | Wait states |
> |---|---|---|---|---|
> | BR0 | 0x000103 / 0x00090B | base 0x000000, valid, **write-protected**, burst inhibited | 32-bit / **16-bit** | — |
> | OR0 | 0xFF800650, 0xFF8006FF, 0xFF800150/140/130/120 | AM=0xFF800000 ⇒ **8 MB, 0x000000-0x7FFFFF** in every clock mode | | SCY 5, 15, 5, 4, 3, 2 |
> | BR1 | 0x800403 | base 0x800000, valid | **8-bit** | — |
> | OR1 | 0xFFFC0100…0xFFFC0180 | AM=0xFFFC0000 ⇒ **256 KB, 0x800000-0x83FFFF** (32 KB SRAM aliased 8×) | | SCY 0…8 |
> | BR2 | 0x900823 | base 0x900000, valid, **byte enables (WEBS=1)** | **16-bit** | — |
> | OR2 | 0xFFFC0110 | **256 KB, 0x900000-0x93FFFF** | | SCY 1 |
> | BR3 | 0xA00003 | base 0xA00000, valid | 32-bit | — |
> | OR3 | 0xFFFF8C20 / 0xFFFF8C30 | AM=0xFFFF8000 ⇒ **32 KB, 0xA00000-0xA07FFF** | | SCY 2 / 3 |
>
> `0x0FFF1F00` is **not** a valid OR2 (its AM would mask address bits 0-3 and
> repeat the region every 256 MB); treat it as a mis-attributed constant.
>
> The "8 MB CS0 region" half of the old model is **confirmed**; the "flash
> aliased at 0x400000-0x5FFFFF wherever no internal module responds" half is
> **refuted**: §10.8 p. 10-28 says an address inside the internal block is
> served internally and *ignored by the memory controller*. The one window that
> really does reach CS0 is opened by the dual-mapping registers:
> `DMOR = 0x70000000`, `DMBR = 0x70000001` at file 0x1250C-0x12514 give
> BA=AM=`0b111000`, DMCS=000 (CS0), DME=1, ATM=000 (code *and* data). Eqn. 10-1
> (p. 10-25) `bus_address[0:16] == {0000000, ISB[0:2], 0, BA[1:6]}` with ISB=1
> yields base **0x5C0000**, size 2^18 ⇒ **0x5C0000-0x5FFFFF → external flash
> 0x1C0000-0x1FFFFF**. That is precisely the calibration range the firmware
> addresses (r2 = 0x5C9FF0, section 4) and that the checksum table at file
> 0x1C3300 covers (section 6), which independently confirms the mapping.
> Details: `re/findings/mpc5xx_registers.md` §3 and §4.

## 4. Small data area registers

| Register | Value | Set at (file) | Used for |
|---|---|---|---|
| r1 | 0x7FEFFC | 0x10D8 | stack |
| r13 | **0x7FFFF0** | 0x10E0, 0x986AC, 0x9E3E0, tail 0x201588 (cpu 0x405588) | read/write small data. 64,727 r13-relative accesses, offsets -0x7FF0..+0x73F9, i.e. RAM 0x7F8000-0x8073E9 spanning both SRAMs. |
| r2 | **0x017FF0** during boot | 0x10E8 | read-only small data in the constant block 0x10000-0x1FFFF |
| r2 | **0x5C9FF0** in the application | 0x86010, 0x862DC, 0x986B4, 0x9E3E4, tail 0x201590 | read-only small data in calibration 0x5C1FF0-0x5D1FEF (this is the value the MED9.1 community documents) |

Which functions run under which r2 must be established from the call graph in
Ghidra (both bases land on dense data, so no static shortcut). Default to
0x5C9FF0 and override for the boot module reachable from 0x1004.

> **2026-09-15 (B1, issue #8) — the boot module is delimited, and the 0x86330
> outlier is explained. VERIFIED-STATIC.** Full derivation and the commands:
> `re/findings/boot.md` §1 and §3.
>
> **The boot module is exactly 119 functions in 0x001004-0x01978F.** Seeding a
> static call-graph walk (`tools/callgraph.py`) at `boot_start` (0x1004) and
> `boot_main_init` (0x12328) and stopping at `app_sda_setup_a/b/int` (0x986AC,
> 0x9E3E0, 0x405588) reaches 119 functions; **all 118** `bl` targets that lie
> in 0x001000-0x019800 are among them, and **no** boot function calls or
> tail-branches outside that window, so the subgraph is closed. Its
> instructions occupy twelve ranges (0x001004-0x001287, 0x0110F0-0x011117,
> 0x011524-0x011CB3, 0x011CE0-0x01382B, 0x01383C-0x0138D3, 0x013D64-0x0155FF,
> 0x015620-0x016843, 0x016860-0x0179EB, 0x017A10-0x017B33, 0x017CF0-0x01810B,
> 0x01814C-0x018227, 0x01831C-0x01978F). These are what
> `ghidra_scripts/b1_context_and_symbols.py` gives r2 = 0x017FF0; everything
> else in 0x000000-0x1FFFFF and 0x404000-0x47FFFF keeps 0x5C9FF0.
>
> **`med9_setup.py --boot-r2` is too generous** and should not be used on its
> own: its blanket `BOOT_R2_RANGE = (0x001000, 0x01FFFF)` also covers **100
> application functions between 0x019948 and 0x01E848**, none of them reachable
> from the boot seeds. Code *does* live in the block documented as "constant
> data 0x10000-0x1FFFF": the boot module's own body plus those 100 functions.
>
> **Check.** `tools/r2_context.py` resolves every r2-relative D-form access in
> the image under its assigned base: `unmapped = 0` and `outside_window = 0` on
> both sides (177 boot references, 3,338 application references). The 120 that
> land on an 0xFF byte are all accounted for: the 10 boot ones are `addi`
> instructions computing the *base pointer* of `tbl_or_values_by_clockmode`
> (0x010020), whose OR entries start with 0xFF by construction; the 110
> application ones are calibration cells that hold 0xFF.
>
> **The outlier `lis r2,0xD5; addi r2,r2,-0x3210` at 0x86330 (r2 = 0xD4CDF0).**
> Six instructions earlier the same function computes `0x804800 - 0x081A00 =
> 0x782E00`, and **0x5C9FF0 + 0x782E00 = 0xD4CDF0 exactly**. It is the
> application SDA2 base with a flash-to-RAM relocation delta folded in, emitted
> by the linker for the routine that is copied from flash 0x081A00 to external
> SRAM 0x804800 and run there (`bl 0x806EA0` at 0x861B0, flash original
> 0x840A0 — almost certainly the external-flash programming driver). It is
> **inert**: the relocated block 0x081A00-0x085400 contains zero r2-relative
> references, and 0x862DC restores r2 = 0x5C9FF0 on the way out. Leaving
> r2 = 0x5C9FF0 over 0x86284-0x86350 is therefore correct.

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
  0x09E374, 0x089F10). 0x80180 holds the magic `AFFE0815`.
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

Runtime use: descriptor lists for the runtime checker are at file 0x1E73C and
0x829A0 (`00404000 0047FFFF` followed by RAM pointers 0x7FCC8C/0x7FCCC4 and
0x805690/0x8056D4) and 0xA3A18 (`00404000 0047FFFF 005C2E00 005FFFFF`).
Whether any additional signature (the "RSA" some tuners mention for MED9)
exists is **unknown**; nothing resembling one was found. This is a risk item
in the plan: the first flash must be an unmodified file passed through our
checksum tool, on the bench ECU.

EEPROM (ST M95160-class, 2 KB, on the SPI bus) has its own block checksums
(COMMUNITY; tools: E2PA, EliasTuning/MED9-EEPROM-Tool). Not read yet.

## 7. Tables located so far

| File / CPU address | Table | Detail |
|---|---|---|
| **0x2B820** | KWP2000 / OBD service dispatch, **28 entries x 20 bytes**: `SID FF FF FF, session-mask, handler, handler2, extra` (corrected 2026-09-15 by agent B3; the earlier row said 0x2B870 / 24 entries and missed SIDs 0x12, 0x3E TesterPresent, 0x1A, 0x83) | SIDs 12 3E 1A 83 14 21 3B 2C 18 17 81 10 31 32 35 36 37 27 82 20, OBD 01 02 03 04 06 07 08 09. Gated by diagnostic session only (entry+4 = 1<<session), not by SecurityAccess. **No 0x23 ReadMemoryByAddress, no 0x3D.** 0x2C DDLI (ids 0xF0-0xF9) + 0x21 need only a session; 0x35/0x36 RequestUpload/TransferData need session 0x86 (security level 2, key = seed + 0x11170, constant at 0xA331C; level 1 = 5-round LFSR mask 0x5FBD5DBD). Dispatcher `kwp_service_dispatch` at 0x13E98C. Handlers for 0x3E 0x1A 0x83 0x81 0x82 0x20 0x31 0x32 0x06 live in on-chip flash. Full detail: `re/findings/kwp.md`. |
| 0x2BC38 | TouCAN **register** base table | 0x707080, 0x707480, 0x707880. These are the CANMCR addresses = module base + 0x80; the module bases are 0x707000/0x707400/0x707800 and the 16 message buffers of module *x* start at base+0x100 (2026-09-15, A2; MPC561RM Table 16-10 p. 16-17). |
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

## 9. Corrections to this document

### 2026-09-15 — agent B5, issue #19 (evidence: `re/findings/calibration_maps.md`)

Additions, not corrections to a stated fact, but they change how section 2
should be read.

- Section 2 says the on-chip flash 0x404000-0x47FFFF "holds the KWP/flash
  programming services and a second copy of the application start-up". It also
  holds **the shared Bosch/ASCET runtime library at 0x40C000-0x411FFF**, and
  that is where all 44 table-lookup helpers live. The application in external
  flash calls them with ordinary `bl` (±32 MB reach). **VERIFIED-STATIC**,
  Ghidra decompilation; 1,343 call sites, 1,306 of them with fully constant
  arguments.
- The calibration data layout is now known. Self-describing tables are
  `{ n; axis[n]; val[n] }` and `{ ny; nx; yaxis[ny]; xaxis[nx]; val[ny*nx] }`
  in u8/s8/u16/s16; group tables pass the axis and value arrays separately and
  read `n` out of the shared axis block. **The value array is indexed
  `val[iy*nx + ix]`, so the second axis argument is the X (stride 1) axis.**
- Of the 7,055 references into 0x5C0000-0x5FFFFF (section 3), **2,196 form a
  pointer** (`lis`+`addi`, i.e. a table argument) and the remaining ~4,850 are
  direct `lbz`/`lhz`/`lha` loads of single calibration values. Counting
  r2-relative loads as well, 8,870 loads reach 5,488 distinct scalar addresses.
- 1,066 distinct tables, curves and axes detected: 151 + 83 two-dimensional
  with resolved axes, 195 more value arrays from direct interpolator calls,
  312 + 172 curves, 153 standalone breakpoint blocks. Coverage of
  0x5C2000-0x5E2FFF is 49.0 % (63 % of the part below the zero-filled reserve
  at 0x5DB0EE). Details: `re/findings/calibration_coverage.md`.
- **Free space confirmed by reading the bytes: 0x5E2510-0x5FFFFF (file
  0x1E2510-0x1FFFFF), 121,584 bytes, is all 0xFF, and the highest address any
  detected table, axis or scalar load reaches is 0x5E2502** (the block marker
  at 0x5E2500). The FFCAL001 block of `06_patch_pipeline.md` section 3 is
  therefore free as planned. **VERIFIED-STATIC**.

### 2026-09-15 — agent A3, issue #10 (evidence: `re/findings/measuring_vars.md`) and agent A5, issues #21/#24 (evidence: `tests/test_emu.py`)

- Section 7 said *"0xA5654 TKMWL measuring-variable table (candidate,
  MED9Toolchain signature `blr 00 03`)"*. **The table starts at 0xA5658.**
  The byte signature matched the `4E800020` (`blr`) that ends the preceding
  function plus the first two bytes of the first table entry (`0003 8EC4`),
  i.e. it was one instruction early. The dispatcher's own `lis`/`addi` pair at
  0x45780 gives 0xA5658, and `tools/find_abs_refs.py --range 0xA5650 0xA78C0`
  finds that single reference and no other.
- Section 7 said *"0x38EA8 measuring-block return helper (candidate, toolchain
  signature)"*. **The result helper is at 0x38EB4**; 0x38EA8 is the tail of an
  unrelated flag routine that ends with its own `blr` at 0x38EB0.
- Both candidates were HYPOTHESIS and were never used for a decision; they are
  now VERIFIED-STATIC at the corrected addresses.
- "Crash at 0x6F80B8" (again): re-run in the new harness on 2026-09-15, the
  DECRAM routine executes 0x6F8000-0x6F8234 and returns cleanly for every value
  of the flash-type nibble at RAM 0x7F800C, with no access to 0x900000 on any
  of those paths. Whatever the old emulator was doing at 0x6F80B8
  (`cmpwi r11, 4`) it was not following this code.  Evidence:
  `tests/test_emu.py::TestDecramRoutine` (VERIFIED-DYNAMIC).

### 2026-09-15 — agent A2, issue #6 (MPC561/MPC563 Reference Manual Rev. 1.2)

Evidence and full derivations: `re/findings/mpc5xx_registers.md`.

- **"0x480000-0x5FFFFF is an alias of external flash 0x080000-0x1FFFFF"** and
  **"0x600000-0x6F7FFF flash alias, unused"** (section 3) were wrong. The
  memory controller never serves an address that lies inside the internal
  4 MB block (§10.8, p. 10-28), so CS0 does not shine through there. Only
  **0x5C0000-0x5FFFFF** is reachable, through the DMBR/DMOR dual mapping, and
  it lands on external flash **0x1C0000-0x1FFFFF**. The arithmetic
  `cpu - 0x400000 = file` that `tools/med9lib.py` used is right; the *extent*
  was too wide. `med9lib.REGIONS` and `cpu_to_file()` are narrowed accordingly
  in the same commit, and `file_to_cpu(prefer_high=True)` now only returns the
  high address for offsets >= 0x1C0000.
- **"The vector table at file 0x0 uses absolute `ba` branches"** (section 3)
  described the right bytes but the wrong structure. It is a **BBC exception
  table relocation branch table** — 32 entries, 8 bytes apart, layout fixed by
  MPC561RM Table 4-1 — not a classic 0x100-spaced PowerPC vector table. The
  `ba` at file 0x100 belongs to the classic layout and is only reached when the
  reset configuration word has IP=0. `re/symbols.csv` corrected
  (`tbl_etr_branch_table`).
- **"IP=1 … HYPOTHESIS: address bits above the external bus width are ignored,
  so 0xFFF00xxx hits CS0"** (section 3) is **refuted**. Vector fetches are
  rewritten by the BBC before they ever reach a bus; with ISB=1 they resolve
  to 0x400000+offset.
- Not a correction but a sharpening: the table at file 0x2BC38 holds TouCAN
  **CANMCR** addresses (module base + 0x80), not module bases (section 7).

### 2026-09-15 — agent B3, issue #13 (evidence: `re/findings/kwp.md`, emulation tests `tools/kwp_seckey_verify.py`, `tools/kwp_upload_verify.py`)

- Section 7 said the KWP dispatch table is *"0x2B870, 24 entries"*. The dispatcher reads its configuration from a structure whose first word is **0x2B820** and whose count is **28**; the four entries before 0x2B870 (SIDs 0x12, 0x3E, 0x1A, 0x83) were missed by the byte-pattern walk that produced the original row. Confirmed by dumping file 0x2B820-0x2B86F.
- The `flags` word is a diagnostic-session bit mask, not a security requirement; SecurityAccess is only needed for the upload services. The community `seed + 0x11170` key is level 2 and is now VERIFIED-DYNAMIC.
### 2026-09-15 — agent B1, issues #8 and #11 (evidence: `re/findings/boot.md`, `re/findings/scheduler.md`)

- Section 3 called 0x010000-0x01FFFF "constant data". It is **constant data
  *and* code**: the boot module's body (0x0110F0-0x01978F) plus 100
  application functions at 0x019948-0x01E848. Section 4 now carries the exact
  boot ranges.
- Section 4's "one outlier … is unexplained" is **resolved** (see the note in
  that section): 0xD4CDF0 = 0x5C9FF0 + (0x804800 - 0x081A00).
- Section 3 listed two routines copied to DECRAM. There are **three**: file
  0x10FEC (0xE4 B), file 0x11118 (0x238 B) and **file 0x11350 (0x1D4 B**, copy
  loop 0x12F78-0x12F80, called at 0x12F84). The three sources tile
  0x10FEC-0x11523 exactly.
- Section 3's list of blocks starting with `5A5A5A5A` and the 0x80100
  directory: slot **+0x20 of the directory (0x080120) is 0x0009E3B4, the
  application entry**. `boot_main_init` reaches it with
  `lwz r9,-0x1600(r13); lwz r9,0x20(r9); mtlr r9; blrl` at 0x13070-0x1307C —
  the single handover point from boot to application. If it ever returns,
  `boot_start` falls into `ba 0x110F0` (fatal spin).
- **File 0x080000-0x0800FF is a second BBC ETR branch table** (same layout as
  the one at file 0x0), pointing at the application exception handlers at
  0x4055A0-0x4062A4 in on-chip flash. Both tables send the external interrupt
  to a fatal spin, while the application demonstrably runs with MSR[EE] = 1
  (it uses the RCPU `EIE`/`EID` SPRs 80/81) — so the live table really is the
  one at 0x400000, in the missing 16 KB. Another argument for the BDM read.
- New for section 3's peripheral list, from issue #11: the scheduler's clock
  is the USIU **Time Base with reference B** — `TBSCR` 0x6FC200, `TBREF1`
  0x6FC208 — and the **PIT** (`PISCR` 0x6FC240) is used only as a
  software-triggerable interrupt at a selectable level. `PITC` (0x6FC244) is
  never written. The OS is **ETAS ERCOSEK V4.1.16** (string at file 0x345D0
  and 0x9C3F0).
