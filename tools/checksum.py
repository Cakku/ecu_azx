#!/usr/bin/env python3
"""Verify or correct the MED9.1 block checksums in a Passat 3.2 FSI dump.

Descriptor format (16 bytes, big endian):  start, end (inclusive), sum, ~sum
where sum = 32-bit sum of the 16-bit big-endian words in [start, end].
Addresses inside a descriptor may be in any of the CPU aliases; they are
resolved with med9lib.cpu_to_file().

Descriptor tables (file offsets) found in this firmware:
    0x001FF0   1 entry    boot block 0x000000-0x001FFF
    0x01FFC0   4 entries  constant data 0x010000-0x01FFFF (16 KB blocks)
    0x0A0000  54 entries  code (ext flash 0x020000-0x1BFFFF, int flash 0x404000-0x47FFFF)
    0x1C3300   6 entries  calibration 0x5C2000-0x5FFFFF (high alias)

Usage:
    checksum.py verify FILE
    checksum.py fix FILE -o OUT      (rewrites every descriptor, prints changes)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import med9lib as m  # noqa: E402

TABLES = ((0x001FF0, 1), (0x01FFC0, 4), (0x0A0000, 54), (0x1C3300, 6))
DESC_SIZE = 16


def descriptors(data):
    for table, count in TABLES:
        for k in range(count):
            off = table + k * DESC_SIZE
            start, end, cs, ncs = (m.u32(data, off + i) for i in (0, 4, 8, 12))
            if start >= end or (cs + ncs) & 0xFFFFFFFF != 0xFFFFFFFF:
                raise ValueError(f"descriptor at {off:#x} is malformed: {start:#x} {end:#x} {cs:#x} {ncs:#x}")
            yield off, start, end, cs


def expected_sum(data, desc_off, start, end):
    """Checksum the ECU will compute for [start,end] with the descriptor's own
    sum/~sum words treated as unknown.  A (sum, ~sum) pair always contributes
    0x1FFFE to a 16-bit word sum, so it can be added back as a constant."""
    fs, fe = m.cpu_to_file(start), m.cpu_to_file(end)
    block = bytearray(data[fs:fe + 1])
    cs_off = desc_off + 8
    inside = fs <= cs_off and cs_off + 8 <= fe + 1
    if inside:
        rel = cs_off - fs
        block[rel:rel + 8] = b"\0" * 8
    total = m.sum16(block)
    if inside:
        total = (total + 0x1FFFE) & 0xFFFFFFFF
    return total


def verify(data, quiet=False):
    bad = 0
    for off, start, end, cs in descriptors(data):
        fs, fe = m.cpu_to_file(start), m.cpu_to_file(end)
        actual = m.sum16(data[fs:fe + 1])
        ok = actual == cs
        bad += not ok
        if not quiet or not ok:
            print(f"  desc {off:#08x}  {start:#08x}-{end:#08x}  stored {cs:08x}  actual {actual:08x}  {'OK' if ok else 'BAD'}")
    print(f"{'ALL OK' if bad == 0 else f'{bad} BAD'} ({sum(1 for _ in descriptors(data))} blocks)")
    return bad == 0


def fix(data):
    changed = 0
    for off, start, end, cs in descriptors(data):
        new = expected_sum(data, off, start, end)
        if new != cs:
            changed += 1
            print(f"  desc {off:#08x}  {start:#08x}-{end:#08x}  {cs:08x} -> {new:08x}")
            m.put_u32(data, off + 8, new)
            m.put_u32(data, off + 12, ~new)
    print(f"{changed} descriptor(s) updated")
    return changed


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("verify", "fix"))
    ap.add_argument("file")
    ap.add_argument("-o", "--output", help="output file for fix mode (default: FILE.fixed.bin)")
    ap.add_argument("-q", "--quiet", action="store_true", help="verify: only print failures")
    a = ap.parse_args()
    data = m.load_dump(a.file)
    if a.mode == "verify":
        sys.exit(0 if verify(data, a.quiet) else 1)
    fix(data)
    out = a.output or a.file + ".fixed.bin"
    Path(out).write_bytes(data)
    print(f"wrote {out}")
    if not verify(data, quiet=True):
        sys.exit(1)


if __name__ == "__main__":
    main()
