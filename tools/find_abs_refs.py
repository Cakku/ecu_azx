#!/usr/bin/env python3
"""Find code sites that form an absolute address with lis + (addi|ori|D-form
load/store) and report the resolved target.  Useful to cross-check Ghidra
references, to find who touches a calibration/RAM address, and to histogram
which address ranges the firmware uses.

Usage:
    find_abs_refs.py FILE --target 0x5C6338          # who references this address
    find_abs_refs.py FILE --range 0x707080 0x7071FF   # references into a range
    find_abs_refs.py FILE --hist                      # 64 KB histogram of all targets
Sites are printed as file offsets plus the CPU address of the site.
"""
from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import med9lib as m  # noqa: E402

DFORM = {14, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 50, 52, 54}
NAMES = {14: "addi", 32: "lwz", 34: "lbz", 36: "stw", 38: "stb", 40: "lhz", 42: "lha", 44: "sth", 48: "lfs", 52: "stfs", 24: "ori"}


def resolve(data):
    n = len(data)
    for i in range(0, n, 4):
        x = m.u32(data, i)
        if (x >> 26) != 15 or ((x >> 16) & 0x1F) != 0:
            continue                      # not lis
        rd, hi = (x >> 21) & 0x1F, x & 0xFFFF
        for j in range(1, 12):
            k = i + 4 * j
            if k >= n:
                break
            y = m.u32(data, k)
            op = y >> 26
            if op == 24 and ((y >> 21) & 0x1F) == rd:           # ori rA, rS=rd, uimm
                yield i, k, ((hi << 16) | (y & 0xFFFF)), "ori"
                break
            if op in DFORM and ((y >> 16) & 0x1F) == rd:        # D-form with rA = rd
                s = y & 0xFFFF
                s = s - 0x10000 if s >= 0x8000 else s
                yield i, k, ((hi << 16) + s) & 0xFFFFFFFF, NAMES.get(op, str(op))
                break
            if op in (14, 15, 32, 34, 40, 42, 48, 31) and ((y >> 21) & 0x1F) == rd:
                break                                            # rd overwritten


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--target", type=lambda s: int(s, 0))
    ap.add_argument("--range", nargs=2, type=lambda s: int(s, 0))
    ap.add_argument("--hist", action="store_true")
    a = ap.parse_args()
    data = m.load_dump(a.file)
    hist = collections.Counter()
    for site, use, addr, kind in resolve(data):
        hist[addr >> 16] += 1
        hit = (a.target is not None and addr == a.target) or (a.range and a.range[0] <= addr <= a.range[1])
        if hit:
            print(f"  site file {site:#08x} (cpu {m.file_to_cpu(site):#08x})  {kind:4s} -> {addr:#010x}")
    if a.hist:
        print("targets per 64 KB (count >= 5):")
        for hi, c in sorted(hist.items()):
            if c >= 5:
                print(f"  {hi << 16:#010x}  {c}")


if __name__ == "__main__":
    main()
