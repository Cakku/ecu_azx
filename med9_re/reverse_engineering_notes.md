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

## CAN Data Structures
- **CAN ID Table**: `0x2BE00`
    - Confirmed entries: `0x280`, `0x288` (Motor), `0x380`, `0x7DF`.
    - Format: Likely an array of structs (16 bytes per entry?).
- **Lookup Logic**:
    - `0x2bc00`: Code using `cntlzw` (Count Leading Zeros). Likely a hash or index calculation function.
    - `0x2bc54`: Pointer table pointing to `0x2bf50` inside/near the CAN table.

## Emulator Patches (Applied in `emulator.py`)
| Address | Original Instruction | Patch | Description |
| :--- | :--- | :--- | :--- |
| `0x128` | `bne 0x138` | `b 0x138` | Skip startup checksum check |
| `0x116fc`| `bne 0x116fc` | `nop` | Bypass hardware polling loop 1 |
| `0x117a0`| `bne 0x117a0` | `nop` | Bypass hardware polling loop 2 |

## Next Steps
1.  Map `0x900000` region in `emulator.py` (size TBD, maybe 64KB+).
2.  Continue execution to see if it reaches `0x2bc00` (CAN table lookup).
3.  Once execution reaches CAN logic, identify the specific function handling the "New ID" reception.
