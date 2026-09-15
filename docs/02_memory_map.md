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

`med9_re/passat_azx_flash.bin` is simply the first 2 MB of the dump.

## 3. Address space seen by the CPU

The first instructions after reset (file 0x1004) read SPR 638 (IMMR), clear
the ISB field and set it to 1: the MPC5xx internal memory map is relocated to
**0x400000-0x7FFFFF**. All peripheral addresses below are the datasheet
addresses plus 0x400000. The file `ppc_med9/data/languages/ppc_med9.pspec`
uses the unrelocated addresses and is therefore wrong for this ECU.

| CPU range | What | Evidence |
|---|---|---|
| 0x000000-0x1FFFFF | External flash, primary mapping. Exception vectors at 0x0-0x1FFF, boot code 0x1004, constant data 0x10000-0x1FFFF, code 0x20000-0x14494F, calibration 0x1C0000-0x1DFFFF. | BR0 base 0x000000 written at 0x12010/0x12034/0x12064 |
| 0x200000-0x3FFFFF | Alias of the external flash (CS0 is an 8 MB region). Unused by code. | OR0 address mask 0xFF80xxxx from table at file 0x10020-0x1004C |
| 0x400000-0x47FFFF | On-chip flash (512 KB). Holds the KWP/flash-programming services and a second copy of the application start-up. | section 2 |
| **0x480000-0x5FFFFF** | Alias of external flash offset 0x080000-0x1FFFFF. The firmware uses it only for **calibration: 7,055 absolute references into 0x5C0000-0x5E2FFF**, 3 into the rest. | `find_abs_refs.py --hist` |
| 0x600000-0x6F7FFF | Flash alias, unused. | |
| 0x6F8000-0x6F87FF | DECRAM (2 KB on-chip RAM). Boot copies a 0x238-byte routine from file 0x11118 here and runs it to reprogram the flash chip select (file 0x120C8-0x120FC). | disassembly |
| 0x6FC000-0x6FC3FF | USIU (SIUMCR, memory controller BR0-3/OR0-3, DMBR/DMOR, PLL, timers). | 145 refs |
| 0x6FC800 | UC3F flash control | 5 refs |
| 0x700000-0x70FFFF | IMB modules: TPU3 A/B 0x704000/0x704400, QADC A/B 0x704800/0x704C00, QSMCM 0x705000 (49 refs), MIOS14 0x706000, **TouCAN A/B/C 0x707080/0x707480/0x707880**, UIMB 0x707F80. | table of the three CAN bases at file 0x2BC38 |
| 0x780000 | CALRAM control | 1 ref |
| 0x7F8000-0x7FFFFF | On-chip SRAM 32 KB. Stack top 0x7FEFFC. | r1 set at file 0x10DC |
| 0x800000-0x807FFF | External SRAM 32 KB (CS1). RAM init table at file 0x1C2E78 gives 0x800000..0x807FF8; highest static reference 0x805784. | BR1 base 0x800000 at 0x12118 |
| 0x900000-0x93FFFF | CS2 external device, 20 refs. Unknown (accessed by the DECRAM routine, which is why the old emulator crashed at PC 0x6F80B8). | BR2 at 0x1216C |
| 0xA00000-0xA07FFF | CS3 external device, 2 refs. Unknown. | BR3 at 0x121A8 |

MSR is set to 0x3942/0x2942 everywhere (IP=1, FP=1, EE=0 at those points).
IP=1 means exception vectors are fetched from 0xFFF00000 at run time; how
that maps onto the flash has not been checked (HYPOTHESIS: address bits above
the external bus width are ignored, so 0xFFF00xxx hits CS0). The vector table
at file 0x0 uses absolute `ba` branches (reset 0x100 -> 0x49C -> 0x1004; all
other used vectors -> 0x110F0, a fatal-error handler that spins).

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
One outlier `lis r2,0xD5; addi r2,r2,-0x3210` at 0x86330 (r2 = 0xD4CDF0) is
unexplained.

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
| 0x2B870 | KWP2000 / OBD service dispatch, 24 entries x 20 bytes: `SID FF FF FF, flags, handler, handler2, 0` | SIDs 0x14 0x21 0x3B 0x2C 0x18 0x17 0x81 0x10 0x31 0x32 0x35 0x36 0x37 0x27 0x82 0x20, OBD 0x01-0x04 0x06-0x09. **No 0x23 ReadMemoryByAddress, no 0x3D.** 0x2C DynamicallyDefineLocalId + 0x21 ReadDataByLocalId + 0x35 RequestUpload are present (live RAM logging route). Handlers for 0x81 0x82 0x20 0x31 0x32 0x06 live in on-chip flash. |
| 0x2BC38 | TouCAN module base table | 0x707080, 0x707480, 0x707880 |
| 0x2BC50 | pointer 0x0002BF50 (CAN configuration structure) | low-alias address |
| 0x2BC90 | **CAN receive table**, header + 21 entries x 16 bytes `index, 0x01mmnn08 (module/slot/dlc), 4, CAN-ID` | IDs 0x1A0 0x5A0 0x4A0 0x440 0x540 0x320 0x442 0x1AC 0x0C2 0x050(dlc 4) 0x51A 0x5E0 0x390 0x38A(dlc 4) **0x7FF 0x7FF** 0x2A0 0x368 **0x7FF 0x7FF** 0x5C0. The four 0x7FF entries are unused receive slots. |
| 0x2BDF0 | **CAN transmit table**, header + 16 entries `index, CAN-ID, 0x0101xxxx, dlc` | 0x7C7 0x280 0x288 0x380 0x480 0x488 0x580 0x588 0x48A 0x38A(4) 0x284(6) 0x56A 0x7C4 0x7C5 0x7C5 0x7C6. These are the Motor_x frames the ECU sends (the old notes called this the "registered CAN IDs" and mislabeled several). 0x7C4 matches the CCP DTO id reported for MED9.1. |
| 0xA5654 | TKMWL measuring-variable table (candidate, MED9Toolchain signature `blr 00 03`) | to be confirmed with 360trev/MED9inf |
| 0x38EA8 | measuring-block return helper (candidate, toolchain signature) | HYPOTHESIS |
| 0x12004-0x121D8 | memory controller init (BR/OR from clock-mode tables at file 0x10020-0x1009C) | |
| 0x11E44 | OR value adjust (clears a bit when RAM byte 0x7FE9E8 == 1) | |

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
