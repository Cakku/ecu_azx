"""Unicorn memory map for the Passat 3.2 FSI MED9.1.1 (03H906032 / 1037382557).

Every region below is taken from `docs/02_memory_map.md` section 3 (the
ISB=1 map the boot code installs at file 0x1004 by writing SPR 638) and from
`tools/med9lib.py`, which stays the single source of truth for the
file-offset <-> CPU-address mapping.

Unicorn maps on 4 KB granularity, so a few regions are rounded up; `real_end`
records the documented end so the harness can flag accesses that are only
reachable because of the rounding.

Usage:
    from emu.memmap import REGIONS, build_pages
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent.parent / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import med9lib as m  # noqa: E402

PAGE = 0x1000

# Kinds:
#   flash   - backed by bytes of the dump (read-mostly, but writable so that
#             self-modifying boot sequences are visible instead of faulting)
#   ram     - zero-filled read/write memory that really exists on the ECU
#   periph  - zero-filled stub for a peripheral block; every access is logged
#   absent  - address range the CPU can reach but that is NOT in our dump
#             (the protected 16 KB at 0x400000); zero-filled and logged
KIND_FLASH = "flash"
KIND_RAM = "ram"
KIND_PERIPH = "periph"
KIND_ABSENT = "absent"


@dataclass(frozen=True)
class MemRegion:
    name: str
    start: int                 # CPU address, page aligned
    size: int                  # page-aligned size handed to Unicorn
    kind: str
    file_start: int | None = None   # dump offset for KIND_FLASH
    file_size: int | None = None
    real_end: int | None = None     # exclusive; documented end if < start+size
    note: str = ""

    @property
    def end(self) -> int:
        return self.start + self.size

    def contains(self, addr: int) -> bool:
        return self.start <= addr < self.end


# ---------------------------------------------------------------------------
# The map.  Order matters only for readability; lookups are by address.
# ---------------------------------------------------------------------------
REGIONS: tuple[MemRegion, ...] = (
    MemRegion("ext_flash", 0x000000, 0x200000, KIND_FLASH,
              file_start=0x000000, file_size=0x200000,
              note="external 2 MB flash, CS0, primary mapping (vectors, boot, code, calibration)"),
    MemRegion("int_flash_missing", 0x400000, 0x4000, KIND_ABSENT,
              note="protected boot/loader sector; KESSv2 does not read it (docs/02 section 2)"),
    MemRegion("int_flash", m.INT_FLASH_BASE, 0x7C000, KIND_FLASH,
              file_start=m.INT_FLASH_FILE_OFFSET, file_size=0x7C000,
              note="on-chip flash 0x404000-0x47FFFF: KWP services + 2nd copy of the start-up"),
    MemRegion("ext_flash_alias", 0x480000, 0x180000, KIND_FLASH,
              file_start=0x080000, file_size=0x180000,
              note="high alias of external flash 0x080000-0x1FFFFF; calibration lives at 0x5C0000+"),
    MemRegion("decram", 0x6F8000, PAGE, KIND_RAM, real_end=0x6F8800,
              note="2 KB on-chip DECRAM; boot copies a 0x238-byte routine from file 0x11118 here"),
    MemRegion("usiu", 0x6FC000, PAGE, KIND_PERIPH, real_end=0x6FC400,
              note="USIU: SIUMCR 0x6FC000, SIPEND 0x6FC010, PLPRCR 0x6FC284, BR0/OR0 0x6FC100/0x6FC104 ... "
                   "(page also covers UC3F flash control at 0x6FC800)"),
    MemRegion("imb", 0x700000, 0x10000, KIND_PERIPH,
              note="IMB3 modules: TPU3 0x704000/0x704400, QADC 0x704800/0x704C00, QSMCM 0x705000, "
                   "MIOS14 0x706000, TouCAN A/B/C 0x707080/0x707480/0x707880, UIMB 0x707F80"),
    MemRegion("calram_ctl", 0x780000, PAGE, KIND_PERIPH,
              note="CALRAM control (1 static reference)"),
    MemRegion("sram", 0x7F8000, 0x10000, KIND_RAM,
              note="0x7F8000-0x7FFFFF on-chip SRAM (stack top 0x7FEFFC, r13 SDA 0x7FFFF0) + "
                   "0x800000-0x807FFF external SRAM on CS1"),
    MemRegion("cs2_device", 0x900000, 0x40000, KIND_PERIPH,
              note="unknown CS2 device; the DECRAM routine talks to it (old emulator died here)"),
    MemRegion("cs3_device", 0xA00000, 0x8000, KIND_PERIPH,
              note="unknown CS3 device, 2 static references"),
)

# MSR[IP]=1 fetches exception vectors from 0xFFF00000.  docs/02 section 3 lists
# this as a HYPOTHESIS ("address bits above the external bus width are ignored,
# so 0xFFF00xxx hits CS0").  We model it as a read-only mirror of the first
# 64 KB of external flash so that a stray exception lands on the real vector
# table instead of an unmapped fetch; it is optional and off the critical path.
HIGH_VECTOR_BASE = 0xFFF00000
HIGH_VECTOR_SIZE = 0x10000

# Address the harness parks in LR so that `call()` knows when a function
# returned.  It is mapped as a 4 KB stub containing nothing executable.
RETURN_MAGIC = 0xE0000000
RETURN_MAGIC_SIZE = PAGE


# Named peripheral blocks inside the stub regions, from docs/02_memory_map.md
# section 3 (USIU offsets are the MPC5xx datasheet offsets + 0x400000 because
# ISB=1).  Used only to make log lines readable.
PERIPH_BLOCKS: tuple[tuple[int, int, str], ...] = (
    (0x6FC000, 0x400, "USIU"),
    (0x6FC800, 0x400, "UC3F flash control"),
    (0x700000, 0x2000, "IMB3/UIMB control"),
    (0x702000, 0x800, "TPU3 code RAM (HYPOTHESIS)"),
    (0x702800, 0x1800, "IMB3 (unidentified)"),
    (0x704000, 0x400, "TPU3_A"),
    (0x704400, 0x400, "TPU3_B"),
    (0x704800, 0x400, "QADC_A"),
    (0x704C00, 0x400, "QADC_B"),
    (0x705000, 0x1000, "QSMCM"),
    (0x706000, 0x1000, "MIOS14"),
    (0x707080, 0x400, "TouCAN_A"),
    (0x707480, 0x400, "TouCAN_B"),
    (0x707880, 0x400, "TouCAN_C"),
    (0x707F80, 0x80, "UIMB"),
    (0x780000, 0x1000, "CALRAM control"),
    (0x900000, 0x40000, "CS2 device"),
    (0xA00000, 0x8000, "CS3 device"),
)

USIU_REGS = {
    0x6FC000: "SIUMCR", 0x6FC004: "SYPCR", 0x6FC00C: "SWSR", 0x6FC010: "SIPEND",
    0x6FC014: "SIMASK", 0x6FC018: "SIEL", 0x6FC01C: "SIVEC", 0x6FC020: "TESR",
    0x6FC100: "BR0", 0x6FC104: "OR0", 0x6FC108: "BR1", 0x6FC10C: "OR1",
    0x6FC110: "BR2", 0x6FC114: "OR2", 0x6FC118: "BR3", 0x6FC11C: "OR3",
    0x6FC140: "DMBR", 0x6FC144: "DMOR",
    0x6FC280: "SCCR", 0x6FC284: "PLPRCR", 0x6FC288: "RSR",
}


def periph_name(addr: int) -> str:
    """'PLPRCR' / 'TPU3_A+0x108' / '' - best-effort label for a peripheral address."""
    if addr in USIU_REGS:
        return USIU_REGS[addr]
    for base, size, name in PERIPH_BLOCKS:
        if base <= addr < base + size:
            return f"{name}+{addr - base:#05x}" if addr != base else name
    return ""


def region_of(addr: int) -> MemRegion | None:
    for r in REGIONS:
        if r.contains(addr):
            return r
    if HIGH_VECTOR_BASE <= addr < HIGH_VECTOR_BASE + HIGH_VECTOR_SIZE:
        return MemRegion("high_vectors", HIGH_VECTOR_BASE, HIGH_VECTOR_SIZE,
                         KIND_FLASH, file_start=0, file_size=HIGH_VECTOR_SIZE,
                         note="MSR[IP]=1 vector mirror (HYPOTHESIS)")
    if RETURN_MAGIC <= addr < RETURN_MAGIC + RETURN_MAGIC_SIZE:
        return MemRegion("return_magic", RETURN_MAGIC, RETURN_MAGIC_SIZE, KIND_RAM)
    return None


def describe(addr: int) -> str:
    """'0x006fc010 (usiu+0x010 SIPEND)' - used in the harness log lines."""
    r = region_of(addr)
    if r is None:
        return f"{addr:#010x} (UNMAPPED)"
    name = periph_name(addr)
    return f"{addr:#010x} ({r.name}+{addr - r.start:#05x}" + (f" {name})" if name else ")")


def check_consistency() -> None:
    """Assert the map agrees with med9lib for every flash-backed region."""
    for r in REGIONS:
        if r.kind != KIND_FLASH:
            continue
        assert m.cpu_to_file(r.start) == r.file_start, r.name
        assert m.cpu_to_file(r.start + r.size - 1) == r.file_start + r.file_size - 1, r.name
    starts = sorted((r.start, r.end, r.name) for r in REGIONS)
    for (s1, e1, n1), (s2, _e2, n2) in zip(starts, starts[1:]):
        assert e1 <= s2, f"regions {n1} and {n2} overlap"
        assert s1 % PAGE == 0 and e1 % PAGE == 0, n1
