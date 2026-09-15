#!/usr/bin/env python3
"""Find every reference to an SDA variable, and every branch to a code address.

Two whole-image scans over the big-endian PowerPC words of
`data/passat_azx_ori.bin`, complementary to `tools/callgraph.py`:

* ``--var LO [HI]`` decodes every D-form load/store whose base register is
  r2 (SDA2, application base 0x5C9FF0) or r13 (SDA, 0x7FFFF0) and prints the
  ones that resolve into ``[LO, HI]``.  That is how the RAM variables of the
  rail-pressure chain were traced (`re/findings/rail.md`); `callgraph.py
  --xref-store` only resolves ``lis`` + D-form pairs, which misses every
  small-data access.
* ``--code ADDR...`` prints every ``b`` / ``bl`` site in the image whose
  target is one of ``ADDR``.  Unlike ``callgraph.py --callers`` this reports
  the call *site*, not the enclosing function, so a flat ERCOSEK task body
  can be read off directly.

The two SDA bases are the application ones; boot-module code (see
`tools/r2_context.py`) uses r2 = 0x017FF0 and is reported wrongly by
``--var``.  Pass ``--r2`` to override.

Usage:
    python3 tools/sda_xref.py data/passat_azx_ori.bin --var 0x8031DA
    python3 tools/sda_xref.py data/passat_azx_ori.bin --var 0x8031BC 0x8031FE
    python3 tools/sda_xref.py data/passat_azx_ori.bin --code 0x457BC8 0x4565F0
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import med9lib  # noqa: E402

# D-form memory opcodes that can carry an SDA base.
D_FORM = {
    32: "lwz", 33: "lwzu", 34: "lbz", 35: "lbzu", 36: "stw", 37: "stwu",
    38: "stb", 39: "stbu", 40: "lhz", 41: "lhzu", 42: "lha", 43: "lhau",
    44: "sth", 45: "sthu", 46: "lmw", 47: "stmw",
}

# Code regions of the dump, as CPU (start, length).
CODE_REGIONS = ((0x000000, 0x200000), (0x404000, 0x07C000))

APP_R2 = 0x5C9FF0
APP_R13 = 0x7FFFF0


def exts(value: int, bits: int) -> int:
    sign = 1 << (bits - 1)
    return (value ^ sign) - sign


def words(data: bytes):
    """Yield (cpu_addr, word) for every aligned word of the code regions."""
    for base, length in CODE_REGIONS:
        for off in range(0, length, 4):
            cpu = base + off
            fo = med9lib.cpu_to_file(cpu)
            yield cpu, int.from_bytes(data[fo:fo + 4], "big")


def scan_var(data: bytes, lo: int, hi: int, r2: int, r13: int) -> list:
    hits = []
    for cpu, w in words(data):
        mnemonic = D_FORM.get(w >> 26)
        if mnemonic is None:
            continue
        ra = (w >> 16) & 0x1F
        if ra == 2:
            target = r2 + exts(w & 0xFFFF, 16)
        elif ra == 13:
            target = r13 + exts(w & 0xFFFF, 16)
        else:
            continue
        if lo <= target <= hi:
            hits.append((cpu, mnemonic, (w >> 21) & 0x1F, ra,
                         exts(w & 0xFFFF, 16), target))
    return hits


def scan_code(data: bytes, targets: set) -> dict:
    hits = {t: [] for t in targets}
    for cpu, w in words(data):
        if (w >> 26) != 18:                       # I-form b / bl / ba / bla
            continue
        displacement = exts(w & 0x03FFFFFC, 26)
        target = displacement if (w >> 1) & 1 else cpu + displacement
        if target in hits:
            hits[target].append((cpu, "bl" if w & 1 else "b"))
    return hits


def main(argv) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("image")
    p.add_argument("--var", nargs="+", default=None,
                   help="CPU address, or LO HI range, of an SDA variable")
    p.add_argument("--code", nargs="+", default=None,
                   help="CPU addresses of branch targets")
    p.add_argument("--r2", default=hex(APP_R2))
    p.add_argument("--r13", default=hex(APP_R13))
    args = p.parse_args(argv)

    if not args.var and not args.code:
        p.error("give --var or --code")

    with open(args.image, "rb") as fh:
        data = fh.read()

    if args.var:
        lo = int(args.var[0], 16)
        hi = int(args.var[1], 16) if len(args.var) > 1 else lo + 1
        hits = scan_var(data, lo, hi, int(args.r2, 16), int(args.r13, 16))
        for cpu, mnemonic, rt, ra, d, target in sorted(hits):
            sign = "-" if d < 0 else ""
            print(f"0x{cpu:06X}  {mnemonic:<5} r{rt},{sign}0x{abs(d):X}(r{ra})"
                  f"   -> 0x{target:06X}")
        print(f"{len(hits)} reference(s) in 0x{lo:06X}..0x{hi:06X}")

    if args.code:
        targets = {int(a, 16) for a in args.code}
        hits = scan_code(data, targets)
        for t in sorted(targets):
            sites = ", ".join(f"0x{a:06X}({k})" for a, k in hits[t]) or "(none)"
            print(f"0x{t:06X} <- {sites}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
