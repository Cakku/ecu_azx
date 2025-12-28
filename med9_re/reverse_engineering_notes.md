# MED9.1 Reverse Engineering Notes

## Memory Map Matches
- **Flash**: `0x000000 - 0x200000` (2MB) - *Cleaned 2MB image used*
- **Internal RAM**: `0x3F8000 - 0x400000`
- **External RAM / Stack**: `0x7F0000 - 0x810000`
    - Stack Pointer (`r1`): `0x7FEFFC`
    - Small Data Area (`r13`): `0x7FFFF0`
    - Small Data Area 2 (`r2`): `0x17FF0` (Flash based?)
- **Peripheral / IO**: `0xC0000000`
- **Unknown External Region**: `0x900000` (Accessed by code at `0x6f80b8`)

## Validated Execution Flow
1.  **Reset Vector**: `0x100` -> Jump to `0x49c` -> Jump to `0x1004` (Start)
2.  **Startup Checks**:
    - `0x128`: Checksum/Integrity check. Fails in emulation. **PATCHED**.
3.  **Hardware Initialization**:
    - `0x116fc`: Polling loop (waiting for bit clear). **PATCHED**.
    - `0x117a0`: Second polling loop. **PATCHED**.
4.  **Crash Point**:
    - PC: `0x6f80b8` (Inside Dummy/Calib region `0x500000-0x7F0000`)
    - Instruction: `lwz r11, 0(r6)` where `r6=0x900000`.
    - Cause: Unmapped memory at `0x900000`.

## CAN Communication System

### CAN ID Table Structure
- **Base Address**: `0x2BE00`
- **Entry Size**: 16 bytes per CAN ID
- **Format**: 
  ```
  Offset +0-3:  Index (01 00 00 00, 02 00 00 00, ...)
  Offset +4-7:  CAN ID (00 00 07 c7, 00 00 02 80, ...)
  Offset +8-11: Configuration bytes (01 01 00 31, 01 01 00 11, ...)
  Offset +12-15: Data length and flags (08 00 00 00, ...)
  ```

### Registered CAN IDs
| Index | CAN ID | Address  | Description |
|-------|--------|----------|-------------|
| 01    | 0x7C7  | 0x2BE00  | Unknown (possibly instrument cluster) |
| 02    | 0x280  | 0x2BE10  | Motor 1 (Engine data) |
| 03    | 0x288  | 0x2BE20  | Motor 2 (Engine data) |
| 04    | 0x380  | 0x2BE30  | Transmission |
| 05    | 0x480  | 0x2BE40  | ABS/ESP |
| 06    | 0x488  | 0x2BE50  | ABS/ESP 2 |
| 07    | 0x580  | 0x2BE60  | Unknown |
| 08    | 0x588  | 0x2BE70  | Unknown |
| 09    | 0x48A  | 0x2BE80  | Unknown |
| 0A    | 0x38A  | 0x2BE90  | Unknown (4 bytes) |
| 0B    | 0x284  | 0x2BEA0  | Unknown (6 bytes) |
| 0C    | 0x56A  | 0x2BEB0  | Unknown |
| 0D    | 0x7C4  | 0x2BEC0  | Diagnostics |
| 0E    | 0x7C5  | 0x2BED0  | Diagnostics |
| 10    | 0x7C6  | 0x2BEF0  | Diagnostics |

### CAN Lookup Logic
- **Location**: `0x2BC00`
- **Algorithm**: Uses PowerPC `cntlzw` (Count Leading Zeros) instruction for efficient lookup
- **Shifted Register Format**: CAN IDs stored in hardware register format (ID << 18 for standard 11-bit IDs)
  - Example: 0x280 -> 0x0A000000 in shifted format
- **Lookup Pointers**: `0x2BC54` contains pointer table to `0x2BF50` (CAN table area)

### CAN Message Processing Functions
| Address    | Function Name  | Description | Size |
|------------|----------------|-------------|------|
| 0x2B3E4    | FUN_0002b3e4   | Main CAN message processor - handles received CAN data | 1076 bytes |
| 0x2B15C    | FUN_0002b15c   | CAN utility function | 272 bytes |
| 0x2B26C    | FUN_0002b26c   | CAN utility function | 376 bytes |
| 0x238B4    | FUN_000238b4   | Called by CAN processor (lookup/indexing) | Unknown |
| 0x239C8    | FUN_000239c8   | Called by CAN processor (data processing) | Unknown |

**FUN_0002b3e4 Call Chain**: Called from line 16476 in decompiled code

### CAN Data Storage (RAM Variables)
- Multiple references to `unaff_r13 + offset` indicate CAN data stored relative to r13 (Small Data Area)
- CAN status flags found at offsets like:
  - `r13 - 0x2BE4`: Status/flags
  - `r13 - 0x2BE5`: Status/flags  
  - `r13 - 0x2BE7`: Status/flags
  - `r13 - 0x2BE9`: Status/flags
  - `r13 - 0x2BEA`: Status/flags
  - `r13 - 0x2BEC`: Status/flags
  - `r13 - 0x2BEF`: Status/flags

## Fueling System

### Candidate Fueling Calculation Functions
Large functions in main code area (0x10000-0x100000) - likely contain fueling calculations:

| Address    | Function Name  | Size (bytes) | Address Range | Notes |
|------------|----------------|--------------|---------------|-------|
| 0x4E554    | FUN_0004e554   | 17,236       | 0x4E554-0x528E7 | **Largest function - primary candidate for main loop/fueling** |
| 0x243CC    | FUN_000243cc   | 6,280        | 0x243CC-0x25C84 | Large calculation function |
| 0xB5EB4    | FUN_000b5eb4   | 3,436        | 0xB5EB4-0xB6580 | Mid-size calculation function |
| 0xBF090    | FUN_000bf090   | 3,044        | 0xBF090-0xBFC74 | Mid-size calculation function |
| 0x226C8    | FUN_000226c8   | 2,944        | 0x226C8-0x23308 | Mid-size calculation function |
| 0xE9188    | FUN_000e9188   | 2,912        | 0xE9188-0xE9CE8 | Mid-size calculation function |

### Fueling RAM Variables (Buffers/Working Memory)
High-frequency RAM variable accesses identified (beyond flash area 0x200000):

| Address    | Access Count | Type | Notes |
|------------|--------------|------|-------|
| 0x803BAC   | 24           | Array/Buffer | **Most accessed - likely injection time/parameter buffer** |
| 0x7FD79C   | 7            | Array/Buffer | Calculation buffer |
| 0x7FD9F0   | 6            | Array/Buffer | Calculation buffer |
| 0x7FDAB0   | 6            | Array/Buffer | Calculation buffer |
| 0x803D1C   | 6            | Array/Buffer | Parameter buffer |
| 0x803DDC   | 6            | Array/Buffer | Parameter buffer |
| 0x803BCC   | 5            | Array/Buffer | Related to 0x803BAC |
| 0x7FCDD0   | 5            | Array/Buffer | Calculation buffer |

**Note**: These RAM addresses are accessed heavily in fueling-related functions and likely contain:
- Injection pulse width calculations
- Fuel correction factors  
- Intermediate calculation results
- Lambda control values

### Map/Table Data Structures
36 unique map/table structures identified in flash. Further analysis needed to:
- Identify LAMFA (Lambda factor) maps
- Locate ignition timing maps
- Find air mass/load maps
- Identify temperature compensation tables

### Free ROM Space for Code Injection
Multiple free space regions identified for ethanol adaptation code:

| Address    | Size (bytes) | Size (KB) | Notes |
|------------|--------------|-----------|-------|
| **0x144954** | **505,484** | **493 KB** | **Best option - large continuous space** |
| 0x1E2504   | 121,596      | 118 KB    | Good alternative |
| 0x73810    | 51,184       | 50 KB     | Mid-size option |
| 0x6604     | 39,420       | 38 KB     | Mid-size option |
| 0x275050   | 28,592       | 28 KB     | Smaller option |
| 0x2000     | 9,724        | 9 KB      | Small option |
| 0x1BFFE4   | 8,220        | 8 KB      | Small option |
| 0x1E828    | 6,040        | 6 KB      | Small option |

**Recommended**: Use address **0x144954** (493 KB free space) for ethanol adaptation function.

### Fueling Variables (To Be Identified)
- [ ] LAMFA (Lambda factor) map location
- [x] Injection time/pulse width buffer: 0x803BAC (RAM)
- [ ] Global fueling modifier variable
- [ ] Ethanol content variable (for new implementation - add to free CAN ID slot)
- [ ] Air/fuel ratio targets
- [ ] Engine load/air mass variables

## Emulator Patches (Applied in `emulator.py`)
| Address | Original Instruction | Patch | Description |
| :--- | :--- | :--- | :--- |
| `0x128` | `bne 0x138` | `b 0x138` | Skip startup checksum check |
| `0x116fc`| `bne 0x116fc` | `nop` | Bypass hardware polling loop 1 |
| `0x117a0`| `bne 0x117a0` | `nop` | Bypass hardware polling loop 2 |

## Next Steps
1. Analyze FUN_0004e554 (largest function) to identify fueling calculations
2. Map `0x900000` region in `emulator.py` (size TBD, maybe 64KB+)
3. Search for map data patterns (2D/3D lookup tables) in flash
4. Identify injection pulse width calculation code
5. Trace CAN message handler to find where CAN data is stored in RAM
6. Locate lambda/air-fuel ratio control logic
7. Find suitable free space in ROM for ethanol adaptation code
