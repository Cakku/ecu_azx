#!/usr/bin/env python3
"""Static PowerPC call graph and SDA-reference extractor for the MED9.1.1 dump.

Two passes over the raw big-endian PowerPC words of `data/passat_azx_ori.bin`:

1. every `bl` (I-form, LK=1, AA=0) target in the image is collected; that set
   is the candidate function-entry set.
2. each function is walked as a CFG from its entry: `bc` splits, `b` to a
   known entry is a tail call, `b` elsewhere is an intra-function jump,
   `blr`/`bctr`/`rfi` end a path.  Per function the walk records the callees,
   the indirect calls (`bctrl`/`blrl`) it could not resolve, and every
   D-form access or `addi` that uses r2 or r13 as base.

Only relative branches are resolved; `bctr` jump tables truncate discovery and
are reported (`--indirect`).

Usage:
    python3 tools/callgraph.py data/passat_azx_ori.bin --reach 0x1004 0x12328 \
        --stop 0x986AC 0x9E3E0 0x405588
    python3 tools/callgraph.py data/passat_azx_ori.bin --func 0x12328 --sda
    python3 tools/callgraph.py data/passat_azx_ori.bin --callers 0x11E44
    python3 tools/callgraph.py data/passat_azx_ori.bin --xref-store 0x6FC200 0x6FC248
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import med9lib  # noqa: E402

# D-form memory opcodes: opcode -> mnemonic
D_FORM = {
    32: "lwz", 33: "lwzu", 34: "lbz", 35: "lbzu", 36: "stw", 37: "stwu",
    38: "stb", 39: "stbu", 40: "lhz", 41: "lhzu", 42: "lha", 43: "lhau",
    44: "sth", 45: "sthu", 46: "lmw", 47: "stmw", 48: "lfs", 49: "lfsu",
    50: "lfd", 51: "lfdu", 52: "stfs", 53: "stfsu", 54: "stfd", 55: "stfdu",
}


def exts(value: int, bits: int) -> int:
    sign = 1 << (bits - 1)
    return (value ^ sign) - sign


class Image:
    """The dump, addressed by CPU address, with the two code regions mapped."""

    def __init__(self, path: str):
        with open(path, "rb") as fh:
            self.data = fh.read()
        if len(self.data) != med9lib.DUMP_SIZE:
            raise SystemExit(f"{path}: expected {med9lib.DUMP_SIZE} bytes")
        self.code = ((0x0, 0x200000, 0x0),
                     (med9lib.INT_FLASH_BASE, med9lib.INT_FLASH_END,
                      med9lib.INT_FLASH_FILE_OFFSET))

    def is_code(self, addr: int) -> bool:
        return any(s <= addr < e for s, e, _ in self.code)

    def word(self, addr: int):
        for s, e, f in self.code:
            if s <= addr < e - 3:
                o = f + (addr - s)
                return int.from_bytes(self.data[o:o + 4], "big")
        return None


def scan_bl_targets(img: Image) -> set:
    """Pass 1: every relative `bl` target in both code regions."""
    entries = set()
    for s, e, f in img.code:
        for addr in range(s, e, 4):
            w = int.from_bytes(img.data[f + (addr - s):f + (addr - s) + 4], "big")
            if w >> 26 == 18 and (w & 1) and not (w & 2):     # bl, AA=0
                t = addr + exts((w >> 2) & 0xFFFFFF, 24) * 4
                if img.is_code(t):
                    entries.add(t)
    return entries


class Func:
    __slots__ = ("entry", "insns", "callees", "tailcalls", "indirect",
                 "sda", "last", "bctr")

    def __init__(self, entry: int):
        self.entry = entry
        self.insns = set()
        self.callees = set()
        self.tailcalls = set()
        self.indirect = []       # pcs of bctrl/blrl
        self.bctr = []           # pcs of computed jumps (bctr, no LK)
        self.sda = []            # (pc, mnem, base_reg, disp)
        self.last = entry


def walk(img: Image, entry: int, entries: set, limit: int = 0x20000) -> Func:
    """Pass 2: CFG walk of one function."""
    fn = Func(entry)
    work = deque([entry])
    while work:
        pc = work.popleft()
        while True:
            if pc in fn.insns or not img.is_code(pc):
                break
            w = img.word(pc)
            if w is None:
                break
            fn.insns.add(pc)
            fn.last = max(fn.last, pc)
            op = w >> 26
            nxt = pc + 4
            if op == 18:                                   # b / ba / bl / bla
                aa, lk = w & 2, w & 1
                t = exts((w >> 2) & 0xFFFFFF, 24) * 4
                t = t if aa else pc + t
                if lk:
                    if img.is_code(t):
                        fn.callees.add(t)
                    pc = nxt
                    continue
                if t in entries and t != entry:
                    fn.tailcalls.add(t)
                    break
                if img.is_code(t) and abs(t - entry) < limit:
                    pc = t
                    continue
                if img.is_code(t) and t != entry:
                    # far `b` to code that is not a known entry: still a tail call
                    fn.tailcalls.add(t)
                break
            if op == 16:                                   # bc / bca / bcl / bcla
                aa, lk = w & 2, w & 1
                bo = (w >> 21) & 0x1F
                t = exts((w >> 2) & 0x3FFF, 14) * 4
                t = t if aa else pc + t
                if lk:
                    if img.is_code(t):
                        fn.callees.add(t)
                    pc = nxt
                    continue
                if img.is_code(t) and abs(t - entry) < limit and t not in entries:
                    work.append(t)
                elif t in entries and t != entry:
                    fn.tailcalls.add(t)
                if (bo & 0x14) == 0x14:                    # branch always
                    break
                pc = nxt
                continue
            if op == 19:
                xo = (w >> 1) & 0x3FF
                lk = w & 1
                if xo in (16, 528):                        # bclr / bcctr
                    bo = (w >> 21) & 0x1F
                    if lk:
                        fn.indirect.append(pc)
                        pc = nxt
                        continue
                    if xo == 528:
                        fn.bctr.append(pc)
                    if (bo & 0x14) == 0x14:                # unconditional
                        break
                    pc = nxt
                    continue
                pc = nxt
                continue
            if op == 17:                                   # sc
                pc = nxt
                continue
            if op == 31 and ((w >> 1) & 0x3FF) == 50:      # rfi
                break
            if op in D_FORM or op == 14:                   # D-form / addi
                ra = (w >> 16) & 0x1F
                if ra in (2, 13):
                    disp = exts(w & 0xFFFF, 16)
                    mnem = D_FORM.get(op, "addi")
                    fn.sda.append((pc, mnem, ra, disp))
            pc = nxt
    return fn


def reachable(img: Image, entries: set, seeds, stops) -> dict:
    stops = set(stops)
    out = {}
    work = deque(seeds)
    while work:
        e = work.popleft()
        if e in out or e in stops:
            continue
        fn = walk(img, e, entries)
        out[e] = fn
        for c in fn.callees | fn.tailcalls:
            if c not in out and c not in stops:
                work.append(c)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("--reach", nargs="+", type=lambda x: int(x, 0),
                    help="seed entries for a reachability walk")
    ap.add_argument("--stop", nargs="*", type=lambda x: int(x, 0), default=[],
                    help="entries at which the walk stops (not descended into)")
    ap.add_argument("--func", type=lambda x: int(x, 0), help="walk one function")
    ap.add_argument("--callers", type=lambda x: int(x, 0))
    ap.add_argument("--sda", action="store_true", help="print r2/r13 accesses")
    ap.add_argument("--indirect", action="store_true")
    ap.add_argument("--entries", action="store_true", help="dump the bl-target set")
    ap.add_argument("--xref-store", nargs=2, type=lambda x: int(x, 0),
                    help="find lis + D-form pairs building an address in a range")
    args = ap.parse_args()

    img = Image(args.image)
    entries = scan_bl_targets(img)

    if args.entries:
        for e in sorted(entries):
            print(f"{e:#08x}")
        return 0

    if args.callers is not None:
        for s, e, f in img.code:
            for addr in range(s, e, 4):
                w = int.from_bytes(img.data[f + (addr - s):f + (addr - s) + 4], "big")
                if w >> 26 == 18 and (w & 1) and not (w & 2):
                    if addr + exts((w >> 2) & 0xFFFFFF, 24) * 4 == args.callers:
                        print(f"{addr:#08x}")
        return 0

    if args.xref_store:
        lo, hi = args.xref_store
        for s, e, f in img.code:
            base = {}
            for addr in range(s, e, 4):
                w = int.from_bytes(img.data[f + (addr - s):f + (addr - s) + 4], "big")
                op = w >> 26
                if op == 15 and ((w >> 16) & 0x1F) == 0:      # lis rD,imm
                    base[(w >> 21) & 0x1F] = (addr, (w & 0xFFFF) << 16)
                    continue
                if op in D_FORM or op == 14:
                    ra = (w >> 16) & 0x1F
                    rd = (w >> 21) & 0x1F
                    if ra in base and addr - base[ra][0] <= 0x40:
                        t = (base[ra][1] + exts(w & 0xFFFF, 16)) & 0xFFFFFFFF
                        if lo <= t < hi:
                            print(f"{addr:#08x} {D_FORM.get(op, 'addi'):6s} "
                                  f"r{rd},{exts(w & 0xFFFF, 16):#x}(r{ra})"
                                  f"  -> {t:#08x}  (lis at {base[ra][0]:#08x})")
                    if op == 14 and rd == ra:                # addi rX,rX,d
                        if ra in base:
                            base[ra] = (base[ra][0],
                                        (base[ra][1] + exts(w & 0xFFFF, 16))
                                        & 0xFFFFFFFF)
                        continue
                    base.pop(rd, None)
        return 0

    if args.func is not None:
        fn = walk(img, args.func, entries)
        print(f"func {fn.entry:#08x}  insns={len(fn.insns)} last={fn.last:#08x}")
        print("  callees:  " + " ".join(f"{c:#x}" for c in sorted(fn.callees)))
        print("  tailcall: " + " ".join(f"{c:#x}" for c in sorted(fn.tailcalls)))
        if args.indirect:
            print("  indirect: " + " ".join(f"{c:#x}" for c in fn.indirect))
            print("  bctr:     " + " ".join(f"{c:#x}" for c in fn.bctr))
        if args.sda:
            for pc, m, ra, d in fn.sda:
                print(f"  {pc:#08x} {m:6s} r{ra} {d:#x}")
        return 0

    if args.reach:
        fns = reachable(img, entries, args.reach, args.stop)
        print(f"# {len(fns)} functions reachable from "
              + " ".join(f"{s:#x}" for s in args.reach))
        for e in sorted(fns):
            fn = fns[e]
            print(f"{e:#08x} insns={len(fn.insns):5d} last={fn.last:#08x} "
                  f"callees={len(fn.callees)} indirect={len(fn.indirect)} "
                  f"bctr={len(fn.bctr)}")
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
