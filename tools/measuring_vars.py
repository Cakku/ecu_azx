#!/usr/bin/env python3
"""Extract the MED9 measuring-variable (TKMWL) table from the flash dump.

The firmware answers KWP2000 ReadDataByLocalIdentifier (SID 0x21) "measuring
block" requests through a dispatcher function that indexes a table of handler
pointers with the 16-bit variable id.  Each handler produces the three bytes
VCDS shows for one measured value: a VAG formula id and the two data bytes
A and B.  Nearly all handlers read the value from a small-data (r13-relative)
RAM location, which is what we want to recover.

What this script does (all of it derived from the dump, nothing hard-coded
except the 360trev/MED9inf prologue signature):

1. Find the dispatcher with the MED9inf ``MemoryVars_Signature`` mask, and
   decode its ``cmplwi``/``lis``/``addi`` to get the element count and the
   table base.
2. Read the handler pointer table.
3. Interpret every handler with a small constant/provenance-tracking PowerPC
   walker (r13 = 0x7FFFF0) to recover, for each variable id, the r13-relative
   RAM address and access width that feeds the reported value, plus the VAG
   formula id and the constant byte the handler emits.
4. Optionally dump the measuring-block *group* table (the calibration table
   the SID 0x21 path indexes with the VCDS group number) which maps
   group/field -> variable id.

Usage:
    tools/measuring_vars.py data/passat_azx_ori.bin                 # summary
    tools/measuring_vars.py data/passat_azx_ori.bin --csv re/measuring_vars.csv
    tools/measuring_vars.py data/passat_azx_ori.bin --groups        # group table
    tools/measuring_vars.py data/passat_azx_ori.bin --explain 1     # one handler
"""
from __future__ import annotations

import argparse
import collections
import csv
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import med9lib as m  # noqa: E402

R13 = m.R13_SDA                     # 0x7FFFF0, docs/02 section 4
R2_APP = 0x5C9FF0                   # application read-only SDA base, docs/02 section 4
GROUP_TABLE_R2_OFF = -0x4AD8        # addi r29,r2,-0x4AD8 at 0x35760 / 0x357F8

# Verified RAM windows (docs/02 section 3).  Anything outside is a decode error.
RAM_WINDOWS = ((0x7F8000, 0x800000), (0x800000, 0x808000))

# 360trev/MED9inf MemoryVars_Signature / _Mask (work/MED9inf/main.c), verbatim,
# truncated to the part of the mask that is not all-zero.
MEMVARS_SIG = bytes.fromhex(
    "9421fff07c0802a693e1000c90010014280300004080002c3d800000398c0000"
    "546b103a7fec582e7fe803a63940000093ed0000994d00004e80002148000014"
    "386000253880000038a400004bff00008001001483e1000c7c0803a638210010"
    "4e800020"
)
MEMVARS_MASK = bytes.fromhex(
    "ffffffffffffffffffffffffffffffffffff0000ffffffffffffff00ffff0000"
    "ffffffffffffffffffffffffffffffffffff0000ffff0000ffffffffffffffff"
    "ffffffffffffffffffffffffffff0000ffffffffffffffffffffffffffffffff"
    "ffffffff"
)

# The firmware carries no names for these variables (no ASCII anywhere in the
# handler block or the tables), so a name can only come from outside evidence.
# The few below combine the VAG display formula the handler itself emits with
# the position the variable occupies in a measuring block group whose meaning
# is documented for the whole VAG range; they keep the `cand_` prefix from
# docs/04 section 3 and are COMMUNITY/HYPOTHESIS, never static.
CANDIDATE_NAMES = {
    1:  ("cand_mw_nmot",
         "grp 001.1 engine speed [COMMUNITY] + fmt 0x01 A=0xC8 -> 40 rpm/LSB"),
    2:  ("cand_mw_rl",
         "grp 002.2 engine load [COMMUNITY] + fmt 0x21 A=0x85 -> %"),
    10: ("cand_mw_ml",
         "grp 003.2 mass air flow [COMMUNITY] + fmt 0x19 -> g/s"),
    14: ("cand_mw_nmot_fine",
         "grp 050.1 [HYPOTHESIS] + fmt 0x01 A=0x32 -> 10 rpm/LSB; RAM byte "
         "adjacent to id 1"),
    15: ("cand_mw_nsoll",
         "grp 050.2 idle speed setpoint [HYPOTHESIS] + fmt 0x01 A=0x32"),
    80: ("cand_mw_tmot",
         "grp 001.2 coolant temperature [COMMUNITY] + fmt 0x05 A=0x0A -> degC"),
}

LOADS = {32: ("lwz", 4), 33: ("lwzu", 4), 34: ("lbz", 1), 35: ("lbzu", 1),
         40: ("lhz", 2), 41: ("lhzu", 2), 42: ("lha", 2), 43: ("lhau", 2),
         48: ("lfs", 4), 50: ("lfd", 8)}
STORES = {36: ("stw", 4), 37: ("stwu", 4), 38: ("stb", 1), 39: ("stbu", 1),
          44: ("sth", 2), 45: ("sthu", 2), 52: ("stfs", 4), 54: ("stfd", 8)}


def in_ram(addr: int) -> bool:
    return any(lo <= addr < hi for lo, hi in RAM_WINDOWS)


def simm(x: int) -> int:
    x &= 0xFFFF
    return x - 0x10000 if x >= 0x8000 else x


class Val:
    """A register value: an optional constant plus the set of RAM sources
    (address, width, mnemonic) it was derived from."""

    __slots__ = ("const", "srcs")

    def __init__(self, const=None, srcs=frozenset()):
        self.const = const
        self.srcs = frozenset(srcs)

    def __repr__(self):
        c = "?" if self.const is None else hex(self.const)
        return f"Val({c},{sorted(self.srcs)})"


UNK = Val()


def combine(*vals):
    return Val(None, frozenset().union(*[v.srcs for v in vals]))


class Dump:
    def __init__(self, path):
        self.d = m.load_dump(path)

    def word(self, cpu):
        return m.u32(self.d, m.cpu_to_file(cpu))

    def half(self, cpu):
        return struct.unpack_from(">H", self.d, m.cpu_to_file(cpu))[0]


def find_dispatcher(data):
    """Locate the measuring-variable dispatcher via the MED9inf signature.

    Returns (cpu_addr_of_function, table_base_cpu, element_count).
    """
    n, sl = len(data), len(MEMVARS_SIG)
    for off in range(0, n - sl, 4):
        for i in range(sl):
            if data[off + i] & MEMVARS_MASK[i] != MEMVARS_SIG[i]:
                break
        else:
            count = m.u32(data, off + 0x10) & 0xFFFF          # cmplwi r3, count
            hi = m.u32(data, off + 0x18) & 0xFFFF             # lis  r12, hi
            lo = simm(m.u32(data, off + 0x1C))                # addi r12, r12, lo
            return m.file_to_cpu(off), (hi << 16) + lo, count
    raise SystemExit("measuring-variable dispatcher signature not found")


def dispatcher_scratch(dump, disp):
    """RAM bytes the dispatcher clears right before it calls the handler.

    In this firmware that is the single ``li rX,0`` / ``stb rX, d(r13)`` pair in
    the dispatcher prologue (a flag/bitfield byte handlers accumulate into),
    which the MED9inf signature leaves unmasked except for its displacement.
    Only stores of a register the prologue set to zero are reported.
    """
    out, zero = [], set()
    for i in range(0, 0x40, 4):
        x = dump.word(disp + i)
        op, rd, ra = x >> 26, (x >> 21) & 31, (x >> 16) & 31
        if op == 14 and ra == 0:                       # li rD, imm
            (zero.add if simm(x) == 0 else zero.discard)(rd)
        elif op in STORES and ra == 13 and rd in zero:
            out.append((R13 + simm(x)) & 0xFFFFFFFF)
        elif op in LOADS or op in (15, 24, 7, 21, 31):
            zero.discard(rd)
    return out


def walk(dump, entry, emit, scratch=(), budget=800):
    """Symbolically walk one handler.  Returns dict with loads / emits / notes.

    ``scratch`` lists RAM addresses the dispatcher clears before the call; they
    are pre-seeded with 0 so that a handler that accumulates bits in one of
    them is not mistaken for a handler that reads a measured value from it.
    """
    loads = []                       # (addr, width, mnemonic), first sighting order
    seen_loads = set()
    emits = []                       # (formula, A, B) as Val triples
    notes = set()
    regs0 = {i: UNK for i in range(32)}
    regs0[13] = Val(R13)
    regs0[2] = Val(R2_APP)
    work = [(entry, regs0, {a: Val(0) for a in scratch})]
    visited = collections.Counter()
    steps = 0
    while work:
        pc, regs, mem = work.pop()
        while True:
            steps += 1
            if steps > budget:
                notes.add("budget-exhausted")
                break
            visited[pc] += 1
            if visited[pc] > 3:
                break
            try:
                x = dump.word(pc)
            except ValueError:
                notes.add("pc-out-of-dump")
                break
            op = x >> 26
            nxt = pc + 4

            if op == 18:                                       # b / bl / ba / bla
                li = x & 0x03FFFFFC
                if li & 0x02000000:
                    li -= 0x04000000
                tgt = li if (x >> 1) & 1 else pc + li
                if tgt == emit:
                    emits.append((regs[3], regs[4], regs[5]))
                    if x & 1:                                  # bl: execution resumes
                        pc = nxt
                        continue
                    break
                if x & 1:                                      # ordinary call
                    notes.add(f"calls {tgt:#x}")
                    for r in (0, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12):
                        regs[r] = UNK
                    pc = nxt
                    continue
                pc = tgt
                continue

            if op == 16:                                       # bc
                bd = x & 0xFFFC
                if bd & 0x8000:
                    bd -= 0x10000
                tgt = bd if (x >> 1) & 1 else pc + bd
                if visited[tgt] <= 3:
                    work.append((tgt, dict(regs), dict(mem)))
                pc = nxt
                continue

            if op == 19:                                       # bclr / bcctr
                if ((x >> 1) & 0x3FF) in (16, 528):
                    break
                pc = nxt
                continue

            if op in LOADS:
                mnem, width = LOADS[op]
                rd, ra = (x >> 21) & 31, (x >> 16) & 31
                base = regs[ra].const if ra else 0
                if base is None:
                    regs[rd] = UNK
                else:
                    addr = (base + simm(x)) & 0xFFFFFFFF
                    if in_ram(addr):
                        if addr in mem:
                            regs[rd] = mem[addr]
                        else:
                            if (addr, width) not in seen_loads:
                                seen_loads.add((addr, width))
                                loads.append((addr, width, mnem))
                            regs[rd] = Val(None, {(addr, width, mnem)})
                    else:
                        regs[rd] = UNK
                        if addr < 0x600000:
                            notes.add(f"rom-load {addr:#x}")
                pc = nxt
                continue

            if op in STORES:
                rs, ra = (x >> 21) & 31, (x >> 16) & 31
                base = regs[ra].const if ra else 0
                if base is not None:
                    mem[(base + simm(x)) & 0xFFFFFFFF] = regs[rs]
                pc = nxt
                continue

            if op in (14, 15):                                 # addi / addis (li/lis)
                rd, ra = (x >> 21) & 31, (x >> 16) & 31
                k = simm(x) << (16 if op == 15 else 0)
                src = Val(0) if ra == 0 else regs[ra]
                c = None if src.const is None else (src.const + k) & 0xFFFFFFFF
                regs[rd] = Val(c, src.srcs)
                pc = nxt
                continue

            if op in (24, 25):                                 # ori / oris
                ra, rs = (x >> 16) & 31, (x >> 21) & 31
                k = (x & 0xFFFF) << (16 if op == 25 else 0)
                c = None if regs[rs].const is None else regs[rs].const | k
                regs[ra] = Val(c, regs[rs].srcs)
                pc = nxt
                continue

            if op == 7:                                        # mulli
                rd, ra = (x >> 21) & 31, (x >> 16) & 31
                c = None if regs[ra].const is None else regs[ra].const * simm(x)
                regs[rd] = Val(c, regs[ra].srcs)
                pc = nxt
                continue

            if op in (20, 21, 23, 28, 29):                     # rlwimi/rlwinm/rlwnm/andi.
                ra, rs = (x >> 16) & 31, (x >> 21) & 31
                regs[ra] = Val(None, regs[rs].srcs)
                pc = nxt
                continue

            if op == 31:
                sub = (x >> 1) & 0x3FF
                rd, ra, rb = (x >> 21) & 31, (x >> 16) & 31, (x >> 11) & 31
                if sub in (23, 87, 279, 343, 407, 151, 183, 215, 439):   # X-form ld/st
                    for r in (ra, rb):
                        if regs[r].const is not None and in_ram(regs[r].const):
                            notes.add(f"indexed-access base {regs[r].const:#x}")
                    if sub in (23, 87, 279, 343):
                        regs[rd] = UNK
                elif sub in (266, 40, 235, 491, 459, 104, 136, 138):     # add/subf/mul/div/neg
                    regs[rd] = combine(regs[ra], regs[rb])
                elif sub in (444, 28, 476, 316, 824, 922, 954, 60, 412, 284):
                    regs[ra] = combine(regs[rd], regs[rb])     # or/and/nor/xor/srawi/extsh
                else:
                    regs[rd] = UNK
                pc = nxt
                continue

            if op in (10, 11):                                 # cmpli / cmpi
                pc = nxt
                continue

            regs[(x >> 21) & 31] = UNK
            pc = nxt
    return {"loads": loads, "emits": emits, "notes": notes}


def classify(res):
    """Pick the emit whose data byte carries a measured value.

    Returns (ram_source_or_None, formula_Val_or_None, A_Val_or_None).  The emit
    helper's third argument (B) is the data byte, so prefer the first emit
    whose B was derived from RAM; fall back to one whose A was, then to the
    first load the handler executed.
    """
    for f, a, b in res["emits"]:
        if b.srcs:
            return sorted(b.srcs)[0], f, a
    for f, a, b in res["emits"]:
        if a.srcs:
            return sorted(a.srcs)[0], f, a
    first = res["emits"][0] if res["emits"] else (None, None, None)
    return (res["loads"][0] if res["loads"] else None), first[0], first[1]


def byte_const(v):
    return "" if v is None or v.const is None else f"{v.const & 0xFF:#04x}"


def build_rows(dump, ptrs, stub, emit, scratch=(), include_unimplemented=False):
    rows, stats = [], collections.Counter()
    for vid, p in enumerate(ptrs):
        if p == stub:
            stats["unimplemented"] += 1
            if include_unimplemented:
                rows.append([vid, "", "", "", "",
                             f"handler {p:#x} (not implemented / returns fmt 0x25)"])
            continue
        res = walk(dump, p, emit, scratch)
        prim, f, av = classify(res)
        formula, aconst = byte_const(f), byte_const(av)
        extra = [f"{ad:#x}/{wd}" for ad, wd, _mn in res["loads"]
                 if prim is None or ad != prim[0]]
        ev = [f"handler {p:#x}"]
        if extra:
            ev.append("also reads " + ",".join(extra[:6]) +
                      ("+more" if len(extra) > 6 else ""))
        rom = sorted(n for n in res["notes"] if n.startswith("rom-load"))
        if rom and prim is None:
            ev.append("reads flash " + ",".join(r.split()[1] for r in rom[:4]))
        for n in sorted(res["notes"]):
            if n.startswith(("calls", "indexed", "budget", "pc-out")):
                ev.append(n)
        ev.append("tools/measuring_vars.py")
        scaling = " ".join(s for s in (f"fmt={formula}" if formula else "",
                                       f"A={aconst}" if aconst else "") if s)
        if prim is None:
            stats["no-ram-source"] += 1
            rows.append([vid, "", "", "", scaling, "; ".join(ev)])
            continue
        addr, width, _mn = prim
        if not in_ram(addr):
            stats["outside-ram"] += 1
        stats["located"] += 1
        name, why = CANDIDATE_NAMES.get(vid, ("", ""))
        if name:
            stats["named"] += 1
            ev.insert(1, why)
        rows.append([vid, f"{addr:#08x}", width, name, scaling, "; ".join(ev)])
    return rows, stats


def group_entries(dump, gt):
    """(group, [4 variable ids]) for every non-empty measuring block group."""
    for g in range(255):
        ids = [dump.half(gt + f * 0x1FE + g * 2) for f in range(4)]
        if any(ids):
            yield g, ids


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--csv", help="write the re/measuring_vars.csv row format here")
    ap.add_argument("--groups", action="store_true",
                    help="dump the measuring-block group table (group -> 4 var ids)")
    ap.add_argument("--group-table", type=lambda s: int(s, 0), default=None,
                    help=f"CPU address of the group table (default r2{GROUP_TABLE_R2_OFF:+#x})")
    ap.add_argument("--explain", type=lambda s: int(s, 0), action="append",
                    help="print the full analysis of one variable id")
    ap.add_argument("--all", action="store_true",
                    help="also emit the 'not implemented' slots")
    a = ap.parse_args()

    dump = Dump(a.file)
    disp, base, count = find_dispatcher(dump.d)
    print(f"dispatcher      cpu {disp:#08x} (file {m.cpu_to_file(disp):#08x})")
    print(f"handler table   cpu {base:#08x} (file {m.cpu_to_file(base):#08x}), "
          f"{count} entries x 4 B = {count * 4:#x} B, ends {base + count * 4:#08x}")

    ptrs = [dump.word(base + 4 * i) for i in range(count)]
    stub = collections.Counter(ptrs).most_common(1)[0][0]
    w = dump.word(stub + 12)                  # the stub's final `b <emit helper>`
    li = w & 0x03FFFFFC
    if li & 0x02000000:
        li -= 0x04000000
    emit = stub + 12 + li
    print(f"unused stub     cpu {stub:#08x}, used by {ptrs.count(stub)} of {count} ids")
    print(f"emit helper     cpu {emit:#08x} (stores the formula/A/B triple)")
    scratch = dispatcher_scratch(dump, disp)
    print("scratch cleared " + ", ".join(f"{s:#08x}" for s in scratch))

    rows, stats = build_rows(dump, ptrs, stub, emit, scratch, a.all)
    print(f"\nimplemented ids   : {count - stats['unimplemented']}")
    print(f"RAM source found  : {stats['located']}")
    print(f"no RAM source     : {stats['no-ram-source']}")
    print(f"outside RAM window: {stats['outside-ram']}")
    print(f"named (cand_, not static): {stats['named']}")

    if a.explain:
        for vid in a.explain:
            res = walk(dump, ptrs[vid], emit, scratch)
            print(f"\nid {vid}: handler {ptrs[vid]:#x}")
            for ad, wd, mn in res["loads"]:
                print(f"   load {mn:5s} {ad:#08x} ({wd} B)   r13{ad - R13:+#x}")
            for f, av, bv in res["emits"]:
                print(f"   emit fmt={f} A={av} B={bv}")
            for n in sorted(res["notes"]):
                print(f"   note {n}")

    if a.groups:
        gt = a.group_table if a.group_table is not None else R2_APP + GROUP_TABLE_R2_OFF
        print(f"\ngroup table cpu {gt:#08x} (file {m.cpu_to_file(gt):#08x}); "
              f"entry(field, group) = base + field*0x1FE + group*2")
        for g, ids in group_entries(dump, gt):
            print(f"  group {g:3d}: " + " ".join(f"{i:5d}" for i in ids))

    if a.csv:
        with open(a.csv, "w", newline="") as fh:
            wcsv = csv.writer(fh)
            wcsv.writerow(["var_id", "ram_addr", "size", "name_or_blank",
                           "scaling_or_blank", "evidence"])
            wcsv.writerows(rows)
        print(f"\nwrote {len(rows)} rows to {a.csv}")


if __name__ == "__main__":
    main()
