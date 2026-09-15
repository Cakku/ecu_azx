#!/usr/bin/env python3
"""EEP_CONF (SPI EEPROM block layout) reader for the MED9.1.1 dump.

Decodes the block descriptor table the ECU's non-volatile block manager uses
(`FUN_0006131C` at cpu 0x06131C) and, with ``--clients``, recovers which
payload bytes of which block the firmware actually touches, by resolving the
constant arguments at every call site of that manager.

Everything it prints is derived from the dump; the constants below are the
addresses established in ``re/findings/eeprom.md`` (agent B4, issue #18).

Block record, 12 bytes:

===== ==== ==========================================================
Off   Type Meaning
===== ==== ==========================================================
+0    u16  RAM mirror offset from 0x7F9E80; 0xFFFF = no mirror
+2    u16  EEPROM byte address of copy 0
+4    u16  unused by the code paths read so far
+6    u16  offset into the flash default-value table at 0xB3238
+8    u16  flags (bit2 mirror, bit5 verify, bit6 re-init, bit7 preload;
           bits0-1 != 0 => the manager owns payload byte len-3)
+10   u8   block length in bytes, INCLUDING the 2-byte checksum
+11   u8   zero in every record
===== ==== ==========================================================

Copy *n* of a block lives at ``eepAddr + n * ceil(len/32)*32``.
Its checksum is the 16-bit sum of payload bytes ``[0, len-2)`` stored
bit-complemented, big endian, at offset ``len-2``.

Usage::

    python3 tools/eeprom_map.py data/passat_azx_ori.bin
    python3 tools/eeprom_map.py data/passat_azx_ori.bin --clients
    python3 tools/eeprom_map.py data/passat_azx_ori.bin --check FILE.bin
"""
from __future__ import annotations

import argparse
import struct
import sys

# --- addresses established in re/findings/eeprom.md -------------------------
TABLE = 0xB2FF0          # file offset of the 32-record block table
N_BLOCKS = 32
PAGE = 0x20              # DAT_000B3196
MIRROR_BASE = 0x7F9E80   # *(0xB3184)
DEFAULTS = 0xB3238       # *(0xB318C)
DEVICE_SIZE = 0x800      # M95160, 2 KB
MANAGER = 0x06131C       # FUN_0006131C(blk, off, len, mode, buf, handle)

EXT_END = 0x200000
TAIL_BASE = 0x404000


def f2c(off: int) -> int:
    """File offset -> CPU address (docs/02_memory_map.md section 2)."""
    return off if off < EXT_END else TAIL_BASE + (off - EXT_END)


# --- block table ------------------------------------------------------------
class Block:
    __slots__ = ("idx", "mirror", "addr", "f4", "dflt", "flags", "length")

    def __init__(self, idx, data):
        o = TABLE + idx * 12
        (self.mirror, self.addr, self.f4,
         self.dflt, self.flags) = struct.unpack_from(">HHHHH", data, o)
        self.length = data[o + 10]
        self.idx = idx

    @property
    def payload(self):
        return self.length - 2

    @property
    def pages(self):
        return (self.length + PAGE - 1) // PAGE

    @property
    def mirror_cpu(self):
        return None if self.mirror == 0xFFFF else MIRROR_BASE + self.mirror

    def copy_addr(self, n):
        return self.addr + n * self.pages * PAGE

    @property
    def owns_replv(self):
        """The manager keeps payload byte len-3 for itself when flags&3 != 0."""
        return (self.flags & 3) != 0


def read_blocks(data):
    return [Block(i, data) for i in range(N_BLOCKS)]


def print_table(blocks):
    print("EEP_CONF block table (file 0x%05X), mirror base 0x%06X, "
          "page 0x%02X, defaults at file 0x%05X"
          % (TABLE, MIRROR_BASE, PAGE, DEFAULTS))
    print()
    print("blk  eeprom  len  pages copies span          mirror     flags  replv")
    order = sorted(blocks, key=lambda b: b.addr)
    for k, b in enumerate(order):
        end = order[k + 1].addr if k + 1 < len(order) else DEVICE_SIZE
        span = end - b.addr
        copies = span // (b.pages * PAGE) if b.pages else 0
        m = "-" if b.mirror_cpu is None else "0x%06X" % b.mirror_cpu
        print("%3d  0x%03X   0x%02X %5d %6d 0x%03X-0x%03X  %-10s 0x%04X %s"
              % (b.idx, b.addr, b.length, b.pages, copies,
                 b.addr, end - 1, m, b.flags,
                 "yes" if b.owns_replv else "no"))

    used = 0
    for k, b in enumerate(order):
        end = order[k + 1].addr if k + 1 < len(order) else DEVICE_SIZE
        span = end - b.addr
        copies = span // (b.pages * PAGE) if b.pages else 1
        used += copies * b.length
    print()
    print("device 0x%03X bytes; blocks span 0x%03X-0x%03X with no gap; "
          "0x%X bytes are payload+checksum, 0x%X are page padding."
          % (DEVICE_SIZE, order[0].addr, DEVICE_SIZE - 1, used,
             DEVICE_SIZE - used))


# --- checksum ---------------------------------------------------------------
def block_checksum(payload: bytes) -> int:
    """16-bit sum of the payload, stored bit-complemented (FUN_00061A48)."""
    s = 0
    for byte in payload:
        s = (s + byte) & 0xFFFF
    return (~s) & 0xFFFF


def check_image(blocks, path):
    """Verify every block copy of a 2 KB EEPROM image read from a real ECU."""
    raw = open(path, "rb").read()
    if len(raw) < DEVICE_SIZE:
        sys.exit("%s is %d bytes, expected at least %d" % (path, len(raw), DEVICE_SIZE))
    order = sorted(blocks, key=lambda b: b.addr)
    bad = 0
    for k, b in enumerate(order):
        if b.idx == 0:
            continue                      # block 0 is exempt (FUN_000619AC)
        end = order[k + 1].addr if k + 1 < len(order) else DEVICE_SIZE
        copies = (end - b.addr) // (b.pages * PAGE)
        for n in range(max(copies, 1)):
            a = b.copy_addr(n)
            payload = raw[a:a + b.payload]
            stored = struct.unpack_from(">H", raw, a + b.payload)[0]
            want = block_checksum(payload)
            ok = stored == want
            bad += not ok
            print("blk %2d copy %d @0x%03X  stored 0x%04X  computed 0x%04X  %s"
                  % (b.idx, n, a, stored, want, "OK" if ok else "BAD"))
    print("\n%s" % ("ALL OK" if not bad else "%d bad block copies" % bad))
    return 1 if bad else 0


# --- client map -------------------------------------------------------------
_KEEP = {14, 24}          # addi, ori


def _resolve_args(data, site, back=0x70):
    """Constant-fold li/lis/addi/ori/mr backwards from a call site.

    Returns {reg_number: value} for whatever could be resolved.  Pure integer
    decoding, no disassembler dependency.
    """
    regs = {}
    start = max(0, site - back)
    start -= start % 4
    for p in range(start, site, 4):
        w = struct.unpack_from(">I", data, p)[0]
        op = w >> 26
        rd = (w >> 21) & 0x1F
        ra = (w >> 16) & 0x1F
        imm = w & 0xFFFF
        simm = imm - 0x10000 if imm & 0x8000 else imm
        if op == 14:                                   # addi / li
            if ra == 0:
                regs[rd] = simm & 0xFFFFFFFF
            elif ra in regs:
                regs[rd] = (regs[ra] + simm) & 0xFFFFFFFF
            else:
                regs.pop(rd, None)
        elif op == 15:                                 # addis / lis
            if ra == 0:
                regs[rd] = (imm << 16) & 0xFFFFFFFF
            elif ra in regs:
                regs[rd] = (regs[ra] + (imm << 16)) & 0xFFFFFFFF
            else:
                regs.pop(rd, None)
        elif op == 24:                                 # ori (incl. `mr` alias)
            if ra in regs:
                regs[rd] = (regs[ra] | imm) & 0xFFFFFFFF
            else:
                regs.pop(rd, None)
        elif op == 31 and ((w >> 1) & 0x3FF) == 444:   # or/or. -> mr
            rs, rb = rd, (w >> 11) & 0x1F
            # `or rA,rS,rB` writes rA (field at bits 16-20 for X-form)
            dest = ra
            if rs == rb and rs in regs:
                regs[dest] = regs[rs]
            else:
                regs.pop(dest, None)
        elif op in (32, 33, 34, 35, 40, 41, 42, 43):   # loads clobber rd
            regs.pop(rd, None)
        elif op == 31:                                 # other X-form writes rA
            regs.pop(ra, None)
        elif op == 18:                                 # a call clobbers r3..r12
            for r in range(3, 13):
                regs.pop(r, None)
    return regs


def find_manager_calls(data, target=MANAGER):
    """The manager is reached by `lis/addi; mtlr; blrl`, never by `bl`."""
    sites = []
    for off in range(0, len(data) - 8, 4):
        w = struct.unpack_from(">I", data, off)[0]
        if (w >> 26) != 15 or ((w >> 16) & 0x1F) != 0:
            continue
        rd = (w >> 21) & 0x1F
        hi = (w & 0xFFFF) << 16
        for k in (1, 2, 3):
            w2 = struct.unpack_from(">I", data, off + 4 * k)[0]
            if (w2 >> 26) != 14 or ((w2 >> 21) & 0x1F) != rd or ((w2 >> 16) & 0x1F) != rd:
                continue
            lo = w2 & 0xFFFF
            if lo & 0x8000:
                lo -= 0x10000
            if ((hi + lo) & 0xFFFFFFFF) == target:
                for j in range(k + 1, 18):
                    if struct.unpack_from(">I", data, off + 4 * j)[0] == 0x4E800021:
                        sites.append(off + 4 * j)
                        break
            break
    return sites


def print_clients(data, blocks):
    sites = find_manager_calls(data)
    used = {}
    print("%d call sites of the block manager at cpu 0x%06X\n" % (len(sites), MANAGER))
    print("call site      cpu          blk  offset  len  mode")
    for s in sites:
        r = _resolve_args(data, s)
        blk, off, ln, mode = r.get(3), r.get(4), r.get(5), r.get(6)
        print("file 0x%06X  cpu 0x%06X  %-4s %-7s %-4s %s"
              % (s, f2c(s),
                 "?" if blk is None else blk,
                 "?" if off is None else "0x%X" % off,
                 "?" if ln is None else ln,
                 "?" if mode is None else mode))
        if blk is not None and off is not None and ln is not None \
                and blk < N_BLOCKS and ln < 256:
            used.setdefault(blk, set()).update(range(off, off + ln))

    print("\nper-block payload usage (payload = len-2; the last 2 bytes are "
          "the checksum)\n")
    for b in blocks:
        if b.idx not in used:
            continue
        u = sorted(x for x in used[b.idx] if x < b.payload)
        free = [k for k in range(b.payload) if k not in used[b.idx]]
        if b.owns_replv and b.payload - 1 in free:
            free.remove(b.payload - 1)
        runs = []
        if free:
            a = c = free[0]
            for k in free[1:]:
                if k == c + 1:
                    c = k
                else:
                    runs.append((a, c))
                    a = c = k
            runs.append((a, c))
        print("  blk %2d  eep 0x%03X  payload %3d  used %s"
              % (b.idx, b.addr, b.payload, u if u else "none"))
        print("          free: %s"
              % (", ".join("+%d" % a if a == c else "+%d..+%d" % (a, c)
                           for a, c in runs) or "none"))
    unseen = [b.idx for b in blocks if b.idx not in used]
    print("\nblocks with no constant-index client: %s"
          % ", ".join(str(i) for i in unseen))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", help="the ECU dump (data/passat_azx_ori.bin)")
    ap.add_argument("--clients", action="store_true",
                    help="also map which block bytes the firmware uses")
    ap.add_argument("--check", metavar="EEPROM.bin",
                    help="verify the block checksums of a 2 KB EEPROM image")
    args = ap.parse_args()

    data = open(args.file, "rb").read()
    blocks = read_blocks(data)
    print_table(blocks)
    if args.clients:
        print()
        print_clients(data, blocks)
    if args.check:
        print()
        return check_image(blocks, args.check)
    return 0


if __name__ == "__main__":
    sys.exit(main())
