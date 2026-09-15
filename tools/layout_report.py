#!/usr/bin/env python3
"""Print a structural report of a MED9.1 dump: per-block fill/entropy, block
markers, identification strings, boot register setup and checksum tables.

Usage: layout_report.py FILE
"""
from __future__ import annotations

import collections
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import med9lib as m  # noqa: E402


def entropy(b: bytes) -> float:
    c = collections.Counter(b)
    n = len(b)
    return -sum(v / n * math.log2(v / n) for v in c.values())


def main(path: str) -> None:
    data = m.load_dump(path)
    print(f"{path}: {len(data):#x} bytes")
    print("\nPer-64KB block (file offset, CPU address, %FF, entropy)")
    for off in range(0, len(data), 0x10000):
        b = data[off:off + 0x10000]
        ff = 100 * b.count(0xFF) / len(b)
        kind = "empty" if ff > 99 else ("sparse" if ff > 60 else "data/code")
        print(f"  {off:07x}  cpu {m.file_to_cpu(off, prefer_high=True):#08x}  FF {ff:5.1f}%  H {entropy(b):4.2f}  {kind}")

    print("\nBlock markers 5A5A5A5A (isolated, word aligned)")
    i = 0
    while (i := data.find(b"\x5a\x5a\x5a\x5a", i)) >= 0:
        if i % 4 == 0 and data[i - 4:i] != b"\x5a\x5a\x5a\x5a" and data[i + 4:i + 8] != b"\x5a\x5a\x5a\x5a":
            print(f"  {i:#08x}  before: {data[i-16:i].hex(' ')}  after: {data[i+4:i+12].hex(' ')}")
        i += 4

    print("\nIdentification strings")
    for pat in (rb"0[0-9][A-Z0-9]9060[0-9]{2}[ A-Z]{0,3}", rb"0261S\d{5}", rb"10\d{8}", rb"\d\d/\d/MED9[^\x00]{4,60}", rb"D9133_[A-Z0-9_]+", rb"P?3\.2 FSI[^\x00]*"):
        for mt in re.finditer(pat, data):
            print(f"  {mt.start():#08x}  {mt.group().decode(errors='replace').strip()}")

    print("\nBoot register setup (file 0x1000-0x10F0)")
    print(f"  IMMR ISB set to 1 at 0x1004 -> internal map at 0x400000 : {'yes' if data[0x1008:0x1010] == bytes.fromhex('714afff1614a0002') else 'NOT FOUND'}")
    print(f"  r1  = 0x7FEFFC (lis 0x80; addi -0x1004)                 : {'yes' if data[0x10d8:0x10e0] == bytes.fromhex('3d600080382beffc') else 'NOT FOUND'}")
    print(f"  r13 = 0x7FFFF0 (lis 0x80; addi -0x10)                   : {'yes' if data[0x10e0:0x10e8] == bytes.fromhex('3da0008039adfff0') else 'NOT FOUND'}")
    print(f"  r2  = 0x017FF0 (lis 1; addi 0x7ff0)                     : {'yes' if data[0x10e8:0x10f0] == bytes.fromhex('3c40000138427ff0') else 'NOT FOUND'}")

    print("\nChecksum descriptor tables (start, end, sum, ~sum)")
    for table, count in ((0x001FF0, 1), (0x01FFC0, 4), (0x0A0000, 54), (0x1C3300, 6)):
        first = m.u32(data, table), m.u32(data, table + 4)
        last = m.u32(data, table + 16 * (count - 1)), m.u32(data, table + 16 * (count - 1) + 4)
        print(f"  table {table:#08x}: {count:2d} entries  {first[0]:#08x}-{first[1]:#08x} .. {last[0]:#08x}-{last[1]:#08x}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/passat_azx_ori.bin")
