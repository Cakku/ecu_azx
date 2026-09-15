"""Unicorn emulation harness for the Passat 3.2 FSI MED9.1.1 dump (issue #21).

See emu/README.md.  The Ghidra `EmulatorHelper` half of issue #21 is a
separate deliverable and needs the Ghidra project from brief A1.
"""
from .core import Access, Med9Emu, Result, SprAccess  # noqa: F401
from .memmap import REGIONS, MemRegion, describe, periph_name, region_of  # noqa: F401

__all__ = ["Med9Emu", "Result", "Access", "SprAccess", "periph_name",
           "REGIONS", "MemRegion", "describe", "region_of"]
