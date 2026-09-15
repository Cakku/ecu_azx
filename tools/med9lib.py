"""Shared helpers for the Passat 3.2 FSI MED9.1 dump (03H906032 / 1037382557).

Everything in this module was verified against data/passat_azx_ori.bin as
described in docs/01_MEMORY_MAP.md.  Keep it in sync with that document.

Address spaces
--------------
The KESSv2 dump is a concatenation of two physical regions:

    file 0x000000-0x1FFFFF  external 2 MB flash   (CPU address 0x000000-0x1FFFFF, CS0)
    file 0x200000-0x27BFFF  "internal" flash      (CPU address 0x404000-0x47FFFF)

CS0 is programmed as an 8 MB region, so the 2 MB external flash is also
visible at 0x400000-0x5FFFFF wherever no on-chip module claims the address
(ISB=1 puts the internal map at 0x400000-0x7FFFFF).  In practice the
firmware uses the high alias for 0x480000-0x5FFFFF (code + calibration).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

EXT_FLASH_SIZE = 0x200000
INT_FLASH_FILE_OFFSET = 0x200000
INT_FLASH_BASE = 0x404000
INT_FLASH_END = 0x480000           # exclusive
HIGH_ALIAS_BASE = 0x400000
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
    Region("ext_flash_high_alias", 0x480000, 0x600000, 0x080000),
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
    if 0x480000 <= addr < 0x600000:
        return addr - HIGH_ALIAS_BASE
    raise ValueError(f"CPU address {addr:#x} is not backed by the dump")


def file_to_cpu(off: int, prefer_high: bool = False) -> int:
    """Map a file offset to its canonical CPU address.

    External flash offsets return the low alias unless prefer_high is set and
    the offset is >= 0x80000 (where the firmware itself uses 0x48xxxx/0x5xxxxx).
    """
    if 0 <= off < EXT_FLASH_SIZE:
        if prefer_high and off >= 0x80000:
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
