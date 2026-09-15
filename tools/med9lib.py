"""Shared helpers for the Passat 3.2 FSI MED9.1 dump (03H906032 / 1037382557).

Everything in this module was verified against data/passat_azx_ori.bin as
described in docs/01_MEMORY_MAP.md.  Keep it in sync with that document.

Address spaces
--------------
The KESSv2 dump is a concatenation of two physical regions:

    file 0x000000-0x1FFFFF  external 2 MB flash   (CPU address 0x000000-0x1FFFFF, CS0)
    file 0x200000-0x27BFFF  "internal" flash      (CPU address 0x404000-0x47FFFF)

CS0 is programmed as an 8 MB region (OR0[AM] = 0xFF800000), so the 2 MB
external flash also answers at 0x200000-0x3FFFFF.  It does *not* show through
at 0x400000-0x7FFFFF: with ISB=1 that is the internal 4 MB block, and the
memory controller never serves an address mapped internally (MPC561RM sec.
10.8).  The one exception is the dual-mapping window set up by DMBR/DMOR at
file 0x1250C:

    CPU 0x5C0000-0x5FFFFF  ->  external flash 0x1C0000-0x1FFFFF   (calibration)

which is where r2 = 0x5C9FF0 and all 7,055 calibration references point.
Corrected 2026-09-15 (agent A2, issue #6); see re/findings/mpc5xx_registers.md
sec. 3-4 and docs/02_memory_map.md sec. 9.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

EXT_FLASH_SIZE = 0x200000
INT_FLASH_FILE_OFFSET = 0x200000
INT_FLASH_BASE = 0x404000
INT_FLASH_END = 0x480000           # exclusive
HIGH_ALIAS_BASE = 0x400000         # cpu - 0x400000 = file, inside the window below
CAL_DUALMAP_BASE = 0x5C0000        # DMBR/DMOR window onto external flash 0x1C0000
CAL_DUALMAP_END = 0x600000         # exclusive
DUMP_SIZE = 0x27C000

# Small data area base registers set by the boot code at file 0x10D8-0x10EC.
R13_SDA = 0x7FFFF0                 # read/write small data (RAM)
R2_SDA2 = 0x017FF0                 # read-only small data (flash, low alias)


@dataclass(frozen=True)
class Region:
    name: str
    cpu_start: int
    cpu_end: int                   # exclusive
    file_start: int | None         # None = not present in dump


REGIONS = (
    Region("ext_flash_low", 0x000000, 0x200000, 0x000000),
    Region("int_flash_missing", 0x400000, 0x404000, None),
    Region("int_flash", INT_FLASH_BASE, INT_FLASH_END, INT_FLASH_FILE_OFFSET),
    Region("int_reserved_flash", 0x480000, 0x5C0000, None),
    Region("cal_dual_mapped", CAL_DUALMAP_BASE, 0x600000, 0x1C0000),
    Region("int_reserved_flash2", 0x600000, 0x6F8000, None),
    Region("decram", 0x6F8000, 0x6F8800, None),
    Region("usiu", 0x6FC000, 0x6FC400, None),
    Region("imb_peripherals", 0x700000, 0x710000, None),
    Region("sram_internal", 0x7F8000, 0x800000, None),
    Region("sram_external", 0x800000, 0x808000, None),
    Region("cs2_device", 0x900000, 0x940000, None),
    Region("cs3_device", 0xA00000, 0xA08000, None),
)


def cpu_to_file(addr: int) -> int:
    """Map a CPU address (any alias) to a dump file offset. Raises if unmapped."""
    if 0 <= addr < EXT_FLASH_SIZE:
        return addr
    if INT_FLASH_BASE <= addr < INT_FLASH_END:
        return INT_FLASH_FILE_OFFSET + (addr - INT_FLASH_BASE)
    if CAL_DUALMAP_BASE <= addr < CAL_DUALMAP_END:
        return addr - HIGH_ALIAS_BASE
    raise ValueError(f"CPU address {addr:#x} is not backed by the dump")


def file_to_cpu(off: int, prefer_high: bool = False) -> int:
    """Map a file offset to its canonical CPU address.

    External flash offsets return the low alias unless prefer_high is set and
    the offset is >= 0x1C0000, i.e. inside the calibration block that the
    firmware itself addresses through the dual-mapped window at 0x5Cxxxx.
    """
    if 0 <= off < EXT_FLASH_SIZE:
        if prefer_high and off >= (CAL_DUALMAP_BASE - HIGH_ALIAS_BASE):
            return off + HIGH_ALIAS_BASE
        return off
    if INT_FLASH_FILE_OFFSET <= off < DUMP_SIZE:
        return INT_FLASH_BASE + (off - INT_FLASH_FILE_OFFSET)
    raise ValueError(f"file offset {off:#x} outside dump")


def load_dump(path: str) -> bytearray:
    data = bytearray(open(path, "rb").read())
    if len(data) != DUMP_SIZE:
        raise ValueError(f"{path}: expected {DUMP_SIZE:#x} bytes, got {len(data):#x}")
    return data


def u32(buf, off: int) -> int:
    return struct.unpack_from(">I", buf, off)[0]


def put_u32(buf: bytearray, off: int, val: int) -> None:
    struct.pack_into(">I", buf, off, val & 0xFFFFFFFF)


def sum16(buf) -> int:
    """Bosch block checksum: 32-bit sum of big-endian 16-bit words (mod 2^32)."""
    n = len(buf) // 2
    return sum(struct.unpack_from(f">{n}H", buf, 0)) & 0xFFFFFFFF
