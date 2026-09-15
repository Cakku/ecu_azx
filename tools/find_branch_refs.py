#!/usr/bin/env python3
"""find_branch_refs.py -- who branches to (or stores a pointer to) an address.

`find_abs_refs.py` resolves `lis`+offset *data* references.  This is its
control-flow counterpart: it decodes every PowerPC I-form branch (`b`, `bl`,
`ba`, `bla`) in the dump and reports the ones whose target is one of the
addresses you asked about, plus every 32-bit word in the image that *is* that
address (a function pointer in a vtable or dispatch table).

It exists because Ghidra's auto-analysis leaves a lot of the MED9 image
undisassembled, so `getCallingFunctions()` silently misses callers -- most of
the CAN driver's entry points are only reachable through pointer tables.

Usage::

    python3 tools/find_branch_refs.py data/passat_azx_ori.bin 0x4379cc
    python3 tools/find_branch_refs.py data/passat_azx_ori.bin 0x135750 0x136624
    python3 tools/find_branch_refs.py data/passat_azx_ori.bin 0x4379cc --no-ptr

Addresses are CPU addresses; sites are printed as file offset + CPU address.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import med9lib  # noqa: E402


def branch_target(word, site_cpu):
    """Return (target, mnemonic) for an I-form branch, else None."""
    if (word >> 26) != 18:
        return None
    li = word & 0x03FFFFFC
    if li & 0x02000000:                      # sign extend 26 bits
        li -= 0x04000000
    absolute = (word >> 1) & 1
    link = word & 1
    target = li if absolute else site_cpu + li
    name = ("bla" if link else "ba") if absolute else ("bl" if link else "b")
    return target & 0xFFFFFFFF, name


def scan(data, targets, want_ptr=True):
    hits = {t: [] for t in targets}
    for off in range(0, len(data) - 3, 4):
        word = struct.unpack_from(">I", data, off)[0]
        site = med9lib.file_to_cpu(off)
        got = branch_target(word, site)
        if got and got[0] in hits:
            hits[got[0]].append((off, site, got[1]))
        if want_ptr and word in hits:
            hits[word].append((off, site, "ptr"))
    return hits


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("addresses", nargs="+", help="CPU addresses, hex")
    ap.add_argument("--no-ptr", action="store_true",
                    help="only branches, do not report 32-bit pointer words")
    args = ap.parse_args(argv)

    data = med9lib.load_dump(args.file)
    targets = [int(a, 16) for a in args.addresses]
    hits = scan(data, targets, want_ptr=not args.no_ptr)
    for t in targets:
        print("--- target 0x%06X ---" % t)
        if not hits[t]:
            print("    (no branch or pointer found)")
        for off, site, kind in hits[t]:
            print("    file 0x%06X  cpu 0x%06X  %s" % (off, site, kind))
    return 0


if __name__ == "__main__":
    sys.exit(main())
