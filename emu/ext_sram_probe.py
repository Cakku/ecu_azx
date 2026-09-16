#!/usr/bin/env python3
"""Emulate `ext_sram_probe` (0x011898) under both CS1 hardware models.

Task 3 of brief C2 (issue #23).  The probe decides at every reset whether the
external SRAM on CS1 is 32 KB or 64 KB and leaves the answer in RAM
**0x7F8012** (0x41 = 32 KB, 0x44 = 64 KB).  That single byte decides whether
anything above 0x807FFF is extra memory or just the 32 KB part showing through
again, because OR1 = 0xFFFC0000 opens a 256 KB window (docs/02_memory_map.md
section 3), so a 32 KB device is aliased eight times over 0x800000-0x83FFFF.

The Unicorn harness maps plain, non-aliased RAM, so out of the box it models
the **64 KB** part.  The 32 KB part is modelled here by a write hook that
mirrors 0x808000-0x808003 onto 0x800000-0x800003 and back, which is exactly
what the address decoder does when ADDR[14] is not connected to the device.

Usage:
    python3 -m emu.ext_sram_probe            # both models, with the access list
    python3 -m emu.ext_sram_probe --quiet
"""
from __future__ import annotations

import argparse
import struct
import sys

from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE

from .core import Med9Emu

PROBE = 0x011898
SIZE_BYTE = 0x7F8012          # 0x41 = 32 KB, 0x44 = 64 KB
BASE = 0x800000
ALIAS = 0x808000              # 32 KB part: same cells as BASE
SENTINEL = 0xDEADBEEF         # what we leave at 0x800000 to prove restoration


def _install_alias(emu: Med9Emu) -> None:
    """Make 0x808000-0x80800F and 0x800000-0x80000F the same four cells."""

    def on_write(uc, access, address, size, value, ud):
        if ALIAS <= address < ALIAS + 0x10:
            uc.mem_write(BASE + (address - ALIAS),
                         (value & 0xFFFFFFFF).to_bytes(size, "big"))
        elif BASE <= address < BASE + 0x10:
            try:
                uc.mem_write(ALIAS + (address - BASE),
                             (value & 0xFFFFFFFF).to_bytes(size, "big"))
            except Exception:                     # page not mapped yet
                pass

    def on_read(uc, access, address, size, value, ud):
        if ALIAS <= address < ALIAS + 0x10:
            uc.mem_write(address, bytes(uc.mem_read(BASE + (address - ALIAS), size)))

    emu.uc.hook_add(UC_HOOK_MEM_WRITE, on_write, begin=BASE, end=ALIAS + 0xFFF)
    emu.uc.hook_add(UC_HOOK_MEM_READ, on_read, begin=ALIAS, end=ALIAS + 0xFFF)


def run_model(dump: str, aliased: bool):
    """Run the probe once.  Returns (size_byte, touched, preserved)."""
    emu = Med9Emu(dump, r2="boot")
    emu.reset()
    if aliased:
        _install_alias(emu)
    emu.write(BASE, struct.pack(">I", SENTINEL))
    emu.write(SIZE_BYTE, b"\xEE")
    res = emu.call(PROBE, r2="boot", reset=False)
    size_byte = emu.read(SIZE_BYTE, 1)[0]
    preserved = struct.unpack(">I", emu.read(BASE, 4))[0]
    touched = sorted({a.addr & ~3 for a in res.accesses} |
                     {a.addr & ~3 for a in res.unmapped})
    return res, size_byte, preserved, touched


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dump", nargs="?", default="data/passat_azx_ori.bin")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    for aliased in (False, True):
        model = "32 KB part, CS1 window aliases it 8x" if aliased \
            else "64 KB part, no aliasing (harness default)"
        res, size_byte, preserved, touched = run_model(args.dump, aliased)
        print(f"--- {model} ---")
        print(f"  stop: {res.stop_reason}, {res.insns} instructions")
        print(f"  RAM 0x7F8012 = 0x{size_byte:02X}  "
              f"({'32 KB' if size_byte == 0x41 else '64 KB' if size_byte == 0x44 else '?'})")
        print(f"  0x800000 after the probe = 0x{preserved:08X}  "
              f"({'PRESERVED' if preserved == SENTINEL else 'CLOBBERED'})")
        if not args.quiet:
            print("  addresses touched:", ", ".join(f"0x{a:06X}" for a in touched)
                  or "(none logged)")
            for i in res.issues[:4]:
                print("  issue:", i)
        print()
    print("Consequence for patch placement: on a 32 KB part nothing above "
          "0x807FFF is\nextra RAM -- 0x808000-0x83FFFF is the same 32 KB seen "
          "again, so the KWP\nprogramming copy that runs to 0x808688 wraps "
          "onto 0x800000-0x800687.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
