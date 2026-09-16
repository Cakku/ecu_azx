#!/usr/bin/env python3
"""ram_survey.py -- static usage survey of the two MED9.1.1 SRAMs.

Builds a per-byte picture of 0x7F8000-0x807FFF (on-chip SRAM 0x7F8000-0x7FFFFF
plus external SRAM 0x800000-0x807FFF on CS1) out of four independent static
scans of ``data/passat_azx_ori.bin``, so that a patch can be given RAM that no
stock code touches:

1. **r13 small-data D-form accesses.** r13 = 0x7FFFF0 in boot *and* application
   (file 0x10E0, 0x986AC, 0x9E3E0, 0x405588), so a single base covers every
   ``lwz/stw/lbz/stb/...  rX,disp(r13)`` in the image.  Reads and writes are
   counted separately and the *width* of each access is marked, so a byte that
   is only ever touched by the halfword at 0x8031DA shows as two used bytes.
   ``addi rX,r13,disp`` is counted apart, as ``addr``: it forms a pointer and
   the bytes it names are usually the base of an indexed region (task 2 of the
   brief).
2. **Absolute ``lis`` + D-form / ``addi`` / ``ori`` pairs**, the same resolver
   ``tools/find_abs_refs.py`` uses, restricted to targets inside the RAM.
3. **Pointer words in flash**: every aligned 32-bit word of *both* flash
   regions (external 0x000000-0x1FFFFF and on-chip 0x404000-0x47FFFF) whose
   value lands in the RAM.  These are the bases of run-time-indexed accesses,
   e.g. the tester pointer table at 0x0A3AE0.
4. **Start-up coverage**: the two ``crt0`` .bss clears and the memory-test
   descriptor lists, from disassembly (see ``re/findings/ram.md`` section 2).

On top of that a table of *known structures* (``KNOWN``, every entry with its
source) marks the regions that have no per-byte static reference: the stacks,
the KWP programming copy at 0x804800, the EEPROM mirror, ``can_rx_shadow`` and
so on.  A 32-byte line is a ``free_candidate`` only when it has no reference of
any kind, is not covered by a start-up range and is not inside a known
structure.

Usage::

    python3 tools/ram_survey.py data/passat_azx_ori.bin
    python3 tools/ram_survey.py data/passat_azx_ori.bin --csv re/ram_map.csv
    python3 tools/ram_survey.py data/passat_azx_ori.bin --line 16 --runs 20
    python3 tools/ram_survey.py data/passat_azx_ori.bin --json work/ram.json

Dependencies: none beyond the standard library and ``tools/med9lib.py``.
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import med9lib as m  # noqa: E402

RAM_LO = 0x7F8000
RAM_HI = 0x808000                      # exclusive
RAM_SIZE = RAM_HI - RAM_LO             # 0x10000

# The external SRAM is a 32 KB part behind OR1 = 0xFFFC0000, i.e. a 256 KB
# window: 0x800000-0x83FFFF repeats it eight times (docs/02_memory_map.md 3).
EXT_LO, EXT_HI = 0x800000, 0x808000
ALIAS_WINDOW_HI = 0x840000

R13 = 0x7FFFF0                         # boot and application alike

# Regions that are disassembled.  0x1C0000-0x1FFFFF is the calibration block
# (docs/02_memory_map.md 2): decoding it as PowerPC invents r13 accesses -- 51
# of them are `lbzu/lfdu/stfsu ...(r13)` update forms, which no compiler emits
# because they would clobber the SDA base.  Excluding it drops the r13
# load/store count from 64,788 to the 64,7xx of docs/02 section 4.
CODE_REGIONS = ((0x000000, 0x1C0000), (0x404000, 0x07C000))
# Pointer words are looked for in *all* flash, including the calibration block:
# the memory-test descriptor lists at 0x5C2E14-0x5C2E8C live there.
PTR_REGIONS = ((0x000000, 0x200000), (0x404000, 0x07C000))
CAL_LO, CAL_HI = 0x1C0000, 0x200000

# ---------------------------------------------------------------- opcode maps
# width in bytes; lmw/stmw are computed from rT.
LOADS = {32: 4, 33: 4, 34: 1, 35: 1, 40: 2, 41: 2, 42: 2, 43: 2,
         46: 4, 48: 4, 49: 4, 50: 8, 51: 8}
STORES = {36: 4, 37: 4, 38: 1, 39: 1, 44: 2, 45: 2, 47: 4,
          52: 4, 53: 4, 54: 8, 55: 8}
MULTI = {46, 47}                       # lmw / stmw
ADDRFORM = {14: "addi", 24: "ori"}     # pointer formation, no memory access

NAMES = {14: "addi", 24: "ori", 32: "lwz", 33: "lwzu", 34: "lbz", 35: "lbzu",
         36: "stw", 37: "stwu", 38: "stb", 39: "stbu", 40: "lhz", 41: "lhzu",
         42: "lha", 43: "lhau", 44: "sth", 45: "sthu", 46: "lmw", 47: "stmw",
         48: "lfs", 49: "lfsu", 50: "lfd", 51: "lfdu", 52: "stfs", 53: "stfsu",
         54: "stfd", 55: "stfdu"}


def exts16(v: int) -> int:
    return v - 0x10000 if v & 0x8000 else v


def access_kind(opcode: int, rt: int):
    """Return (kind, width) for a D-form opcode, or None."""
    if opcode in LOADS:
        w = 4 * (32 - rt) if opcode in MULTI else LOADS[opcode]
        return "read", max(w, 1)
    if opcode in STORES:
        w = 4 * (32 - rt) if opcode in MULTI else STORES[opcode]
        return "write", max(w, 1)
    if opcode in ADDRFORM:
        return "addr", 1
    return None


# ------------------------------------------------------------ known structures
@dataclass(frozen=True)
class Known:
    start: int
    end: int                            # exclusive
    name: str
    tag: str
    source: str
    dynamic: bool = False               # written at run time, not statically
    candidate: bool = False             # named, but believed free (see ram.md)


# Every entry carries the file/finding it comes from.  Keep this table and
# re/findings/ram.md in step.
KNOWN = (
    Known(0x7F8012, 0x7F8013, "ext_sram_size_code", "VERIFIED-STATIC",
          "ext_sram_probe 0x011898 writes 0x41 (32 KB) / 0x44 (64 KB); eeprom.md 6"),
    Known(0x7F802C, 0x7F807C, "crt0_bss_clear_1", "VERIFIED-STATIC",
          "app_entry_crt0 0x09E3C0-0x09E434: zero loop 0x7F802C..0x7F807B"),
    Known(0x7F8080, 0x7F80E8, "crt0_bss_clear_2", "VERIFIED-STATIC",
          "app_entry_crt0 0x09E408-0x09E434: zero loop 0x7F8080..0x7F80E7"),
    Known(0x7F9E3C, 0x7FA480, "kwp_protected_window", "VERIFIED-DYNAMIC",
          "kwp_upload_range_check 0x0A3160 rejects overlap with NRC 0x31; kwp.md 5.1"),
    Known(0x7F9E80, 0x7FA480, "eep_mirror", "VERIFIED-STATIC",
          "ptr_eep_mirror_base 0x0B3184 = 0x7F9E80, 0x600 bytes; eeprom.md 3"),
    Known(0x7FD2CC, 0x7FD2EC, "immo_eeprom_mirror", "VERIFIED-STATIC",
          "eeprom_read_immo_block 0x085F44 reads EEPROM 0x280, 32 B; eeprom.md"),
    Known(0x7FE588, 0x7FE58C, "os_stack_ptr_chain_onchip", "VERIFIED-STATIC",
          "scheduler.md 7: r13-0x1A68"),
    Known(0x7FE5A0, 0x7FE5A4, "os_stack_ptr_chain_ext", "VERIFIED-STATIC",
          "scheduler.md 7: r13-0x1A50"),
    Known(0x7FE5A4, 0x7FE5A8, "os_kernel_object_ptr", "VERIFIED-STATIC",
          "scheduler.md 7: r13-0x1A4C"),
    Known(0x7FE5FC, 0x7FE645, "os_task_activation_flags", "VERIFIED-STATIC",
          "tbl_os_task_control_blocks 0x478634 field +0x14; scheduler.md 4"),
    Known(0x7FE588, 0x7FE838, "os_kernel_ram", "HYPOTHESIS",
          "the kernel configuration block 0x09B5EC-0x09B76C names 0x7FE588, "
          "0x7FE5F8, 0x7FE64C, 0x7FE7B4..0x7FE7DC, 0x7FE818/20/28/34 -- treated "
          "as one contiguous kernel area; ram.md 4"),
    # The stack, from the kernel stack descriptor at 0x09B6F8 and crt0.
    Known(0x7FF01C, 0x7FF3C0, "stack_overshoot_estimate", "HYPOTHESIS",
          "the deepest static stwu chain from a task entry is 0x588 B "
          "(0x4328E4, the 100 ms task) and one ISR frame adds 0x48, so r1 can "
          "reach 0x7FF1A0 -- 0x220 below the descriptor limit 0x7FF3C0.  The "
          "unreferenced RAM continues down to 0x7FF01B, which is taken as the "
          "real stack floor; `ram_survey.py --stack`, ram.md 5"),
    Known(0x7FF3C0, 0x7FF770, "os_task_stack", "VERIFIED-STATIC",
          "kernel stack descriptor 0x09B6F8 = {0x7FFFEC, 0x7FF770, 0x7FF730, "
          "0x7FF3C0, 0x36C}; app_entry_crt0 0x09E3C4 sets r1 = 0x7FF768 = "
          "0x7FF770-8 and the stack grows down; ram.md 4"),
    Known(0x7FF770, 0x7FFFEC, "free_above_stack", "HYPOTHESIS",
          "0x87C B above the stack top named by the same kernel descriptor "
          "(first word 0x7FFFEC = top of OS RAM). Zero references of any kind, "
          "no cold-start fill, and the memory-test descriptor 0x5C2E50 lists "
          "it as testable while it excludes the stack and the kernel area. "
          "RECOMMENDED patch RAM; ram.md 6", candidate=True),
    Known(0x7FFFF0, 0x800000, "r13_sda_anchor", "VERIFIED-STATIC",
          "r13 = 0x7FFFF0 (file 0x10E0, 0x986AC, 0x9E3E0, 0x405588)"),
    # External SRAM.
    Known(0x803DA4, 0x803DB4, "kwp_io_struct", "VERIFIED-STATIC",
          "kwp.md 1.4: +0 buffer ptr, +6 req len, +8 resp len, +0xA status, "
          "+0xB SID, +0xC sub-function"),
    Known(0x803DB8, 0x803DBA, "kwp_response_pending_flags", "VERIFIED-STATIC",
          "kwp.md 1.3"),
    Known(0x803EE4, 0x803FEC, "can_rx_shadow", "VERIFIED-STATIC",
          "can.md 4: 22 x 12 B {u32 id; u8 data[8]}"),
    Known(0x804800, 0x808000, "kwp_prog_copy_dest", "VERIFIED-STATIC",
          "FUN_0008A12C copies flash 0x081A00-0x085887 (0x3E88 B) to 0x804800; "
          "runs to 0x808688", dynamic=True),
    Known(0x800000, 0x800688, "kwp_prog_copy_alias_tail", "VERIFIED-STATIC",
          "the same copy overruns 0x807FFF; with a 32 KB part the CS1 window "
          "aliases 0x808000-0x808687 onto 0x800000-0x800687", dynamic=True),
)


# ------------------------------------------------- indexed-region hunt (task 2)
# A base address that a `lis`+`addi` pair puts in a register names one byte in
# the survey, but the code usually walks a whole structure from it.  This is a
# small forward abstract interpreter over the owning function: it tracks the
# integer value of every register it can, and reports the largest offset the
# code reaches from the base, either as a constant displacement or as
# loop_count * stride.  Calibrated on the two regions whose extent is known
# independently: the KWP programming copy to 0x804800 (0x3E88 B, from its flash
# source bounds) and `can_rx_shadow` 0x803EE4 (22 x 12 B, from can.md 4).

X_LOAD = {23: 4, 55: 4, 87: 1, 119: 1, 279: 2, 311: 2, 343: 2, 375: 2}
X_STORE = {151: 4, 183: 4, 215: 1, 247: 1, 407: 2, 439: 2}


@dataclass
class Extent:
    base: int
    site: int
    max_disp: int = 0          # largest constant displacement reached, + width
    loop_bytes: int = 0        # loop_count * stride, when both are known
    indexed: bool = False      # an X-form access whose index is not a constant
    neighbour: int = 0         # bytes to the next statically referenced byte
    note: str = ""

    @property
    def size(self) -> int:
        return max(self.max_disp, self.loop_bytes)

    @property
    def tag(self) -> str:
        if self.loop_bytes:
            return "indexed region (extent HYPOTHESIS, loop-bounded)"
        if self.indexed:
            return "indexed region (extent UNBOUNDED, <= neighbour)"
        return "indexed region (extent HYPOTHESIS, displacement-bounded)"


def _const_step(regs: dict, w: int) -> None:
    """Apply one instruction to the constant-register map (best effort).

    Field names follow the PowerPC book: for D-form ``rt`` is bits 6:10 (D or
    S) and ``ra`` is bits 11:15; for the X-form logical/shift instructions the
    *destination* is ``ra`` and the source is ``rt``.
    """
    op = w >> 26
    rt, ra, rb = (w >> 21) & 0x1F, (w >> 16) & 0x1F, (w >> 11) & 0x1F
    if op == 15:                                             # lis / addis
        v = (w & 0xFFFF) << 16
        if ra == 0:
            regs[rt] = v
        elif ra in regs:
            regs[rt] = regs[ra] + v
        else:
            regs.pop(rt, None)
        return
    if op == 14:                                             # addi / li
        if ra == 0:
            regs[rt] = exts16(w & 0xFFFF)
        elif ra in regs:
            regs[rt] = regs[ra] + exts16(w & 0xFFFF)
        else:
            regs.pop(rt, None)
        return
    if op == 7:                                              # mulli rD,rA,SIMM
        if ra in regs:
            regs[rt] = regs[ra] * exts16(w & 0xFFFF)
        else:
            regs.pop(rt, None)
        return
    if op == 24:                                             # ori rA,rS,UIMM
        if rt in regs:
            regs[ra] = regs[rt] | (w & 0xFFFF)
        else:
            regs.pop(ra, None)
        return
    if op == 21:                                             # rlwinm rA,rS,SH,MB,ME
        sh, mb, me = rb, (w >> 6) & 0x1F, (w >> 1) & 0x1F
        if rt in regs and me == 31 and mb == 32 - sh and sh:  # srwi rA,rS,32-SH
            regs[ra] = (regs[rt] & 0xFFFFFFFF) >> (32 - sh)
        else:
            regs.pop(ra, None)
        return
    if op == 31:
        xo = (w >> 1) & 0x3FF
        if xo == 444:                                        # or rA,rS,rB (mr)
            if rt == rb and rt in regs:
                regs[ra] = regs[rt]
            elif rt in regs and rb in regs:
                regs[ra] = regs[rt] | regs[rb]
            else:
                regs.pop(ra, None)
            return
        if xo in (40, 40 | 512):                             # subf rD,rA,rB
            if ra in regs and rb in regs:
                regs[rt] = regs[rb] - regs[ra]
            else:
                regs.pop(rt, None)
            return
        if xo in (266, 266 | 512):                           # add rD,rA,rB
            if ra in regs and rb in regs:
                regs[rt] = regs[ra] + regs[rb]
            else:
                regs.pop(rt, None)
            return
        if xo == 824:                                        # srawi rA,rS,SH
            if rt in regs:
                regs[ra] = regs[rt] >> rb
            else:
                regs.pop(ra, None)
            return
        if xo == 202:                                        # addze rD,rA
            if ra in regs:
                regs[rt] = regs[ra]
            else:
                regs.pop(rt, None)
            return
        if xo in (235, 235 | 512, 75):                       # mullw / mulhw
            if ra in regs and rb in regs:
                regs[rt] = regs[ra] * regs[rb]
            else:
                regs.pop(rt, None)
            return
        if xo in (0, 32, 467, 512, 4):        # cmp/cmpl/mtspr/mcrxr/tw: no GPR
            return
        if xo in X_STORE:                                    # stores write no GPR
            return
        regs.pop(rt, None)
        return
    if op in LOADS or op in STORES:
        if op in LOADS:
            regs.pop(rt, None)


def hunt_extent(data: bytes, base: int, site: int, window: int = 96) -> Extent:
    """Walk forward from the instruction at `site` that produced `base`."""
    ex = Extent(base=base, site=site)
    try:
        fo = m.cpu_to_file(site)
    except ValueError:
        return ex
    w0 = struct.unpack_from(">I", data, fo)[0]
    holder = (w0 >> 21) & 0x1F                # register that now holds `base`
    off = {holder: 0}                         # reg -> offset from base
    regs: dict = {}                           # reg -> absolute constant
    ctr = None
    # Prime the constant map from the instructions before the site: loop counts
    # are usually computed before the destination pointer is formed.
    back = min(window, fo // 4)
    for i in range(back, 0, -1):
        try:
            regs_w = struct.unpack_from(">I", data, fo - 4 * i)[0]
        except struct.error:
            break
        _const_step(regs, regs_w)
    for i in range(1, window + 1):
        try:
            w = struct.unpack_from(">I", data, fo + 4 * i)[0]
        except struct.error:
            break
        op = w >> 26
        rt, ra, rb = (w >> 21) & 0x1F, (w >> 16) & 0x1F, (w >> 11) & 0x1F
        if op == 19:                                       # blr / bctr / rfi
            xo = (w >> 1) & 0x3FF
            if xo in (16, 528) and not (w & 1):
                break
        if op == 31 and ((w >> 1) & 0x3FF) == 50:
            break
        if op == 18 and (w & 1):                           # bl: volatiles die
            for r in range(3, 13):
                off.pop(r, None)
                regs.pop(r, None)
            _const_step(regs, w)
            continue
        if op == 31 and ((w >> 1) & 0x3FF) == 467 and ((w >> 11) & 0x3FF) == 0x120:
            ctr = regs.get(rt)                             # mtctr rS
        # offset propagation
        if op == 14 and ra in off:                         # addi rD,rBase,k
            off[rt] = off[ra] + exts16(w & 0xFFFF)
        elif op == 31 and ((w >> 1) & 0x3FF) == 444 and rt == rb and rt in off:
            off[ra] = off[rt]                              # mr rA,rS
        elif op == 31 and ((w >> 1) & 0x3FF) == 266 and ra in off:
            off[rt] = off[ra]                              # add rD,rBase,rIdx
            ex.indexed = True
        # memory accesses
        got = access_kind(op, rt) if op in LOADS or op in STORES else None
        if got and ra in off:
            _kind, width = got
            d = off[ra] + exts16(w & 0xFFFF)
            ex.max_disp = max(ex.max_disp, d + width)
            if op in (33, 35, 37, 39, 41, 43, 45):          # update forms
                stride = exts16(w & 0xFFFF)
                off[ra] = off[ra] + stride
                if ctr and stride > 0:
                    ex.loop_bytes = max(ex.loop_bytes, ctr * stride)
        if op == 31:
            xo = (w >> 1) & 0x3FF
            if xo in X_STORE or xo in X_LOAD:
                if ra in off or rb in off:
                    ex.indexed = True
        _const_step(regs, w)
        if op == 14 and ra in off:                         # keep addi in regs
            regs.pop(rt, None)
    return ex


def referenced(s: Survey, addr: int) -> bool:
    i = addr - RAM_LO
    return bool(s.read[i] or s.write[i] or s.addr[i] or s.aread[i]
                or s.awrite[i] or s.aaddr[i] or s.ptr[i] or s.ptrcal[i])


def neighbour_bound(s: Survey, base: int) -> int:
    """Distance from `base` to the next *other* statically referenced byte.

    A contiguous structure starting at `base` cannot be longer than this
    without overlapping something the code already names, so it is a hard
    upper bound on an otherwise unbounded indexed region.
    """
    a = base + 1
    while a < RAM_HI and not referenced(s, a):
        a += 1
    return a - base


def indexed_bases(data: bytes, s: Survey, min_sites: int = 1) -> list:
    """Every RAM base formed by an absolute lis+addi, with its bounded extent."""
    out = []
    for target, lst in s.sites.items():
        sites = [a for a, _mn, kind, how in lst if how == "abs" and kind == "addr"]
        if len(sites) < min_sites:
            continue
        best = None
        for site in sites:
            ex = hunt_extent(data, target, site)
            if best is None or ex.size > best.size or (ex.indexed and not best.indexed):
                best = ex
        best.neighbour = neighbour_bound(s, target)
        best.note = f"{len(sites)} lis+addi site(s)"
        out.append(best)
    out.sort(key=lambda e: e.base)
    return out


# ----------------------------------------------------- stack estimate (task 4)
# tbl_os_task_control_blocks, scheduler.md 4.  CORRECTION: the stride is NOT a
# uniform 0x24 -- the seven ISR tasks use 0x20 and there are two gaps -- so the
# rows are found by their +0x04 anchor word instead.
TCB_TABLE = 0x478634
TCB_ANCHOR = 0x004764FC
TCB_SCAN = (0x478600, 0x478C00)
ISR_FRAME = 0x48                # exception prologue frame, scheduler.md 7
STACK_TOP_APP = 0x7FF770        # r1 = 0x7FF768 = top-8 (app_entry_crt0)
STACK_LIMIT = 0x7FF3C0          # kernel stack descriptor 0x09B6F8+0x0C


def frame_size(img, insns) -> int:
    """Largest `stwu r1,-N(r1)` / `addi r1,r1,-N` this function performs."""
    worst = 0
    for pc in insns:
        w = img.word(pc)
        if w is None:
            continue
        op = w >> 26
        if op in (37, 14) and ((w >> 21) & 0x1F) == 1 and ((w >> 16) & 0x1F) == 1:
            d = exts16(w & 0xFFFF)
            if d < 0:
                worst = max(worst, -d)
    return worst


def stack_estimate(image_path: str, max_depth: int = 400):
    """Deepest `stwu` chain from every task entry.  Returns (rows, meta)."""
    import callgraph as cg                       # tools/, same directory

    img = cg.Image(image_path)
    entries = cg.scan_bl_targets(img)
    tasks = []
    for a in range(TCB_SCAN[0], TCB_SCAN[1], 4):
        if img.word(a) != TCB_ANCHOR:
            continue
        w = img.word(a - 4)
        if w is not None and img.is_code(w) and w not in tasks:
            tasks.append(w)
    seeds = set(tasks) | entries
    funcs = {}

    def get(e):
        if e not in funcs:
            fn = cg.walk(img, e, seeds)
            funcs[e] = (frame_size(img, fn.insns), fn.callees | fn.tailcalls,
                        len(fn.indirect))
        return funcs[e]

    memo, onstack, indirect_hits = {}, set(), set()

    def depth(e, level=0):
        if e in memo:
            return memo[e]
        if e in onstack or level > max_depth:
            return (0, [e])                       # recursion / too deep
        onstack.add(e)
        own, callees, nind = get(e)
        if nind:
            indirect_hits.add(e)
        best, path = 0, []
        for c in callees:
            d, p = depth(c, level + 1)
            if d > best:
                best, path = d, p
        onstack.discard(e)
        memo[e] = (own + best, [e] + path)
        return memo[e]

    rows = []
    for t in tasks:
        d, path = depth(t)
        rows.append((t, d, path))
    rows.sort(key=lambda r: r[1], reverse=True)
    return rows, {"tasks": len(tasks), "functions": len(funcs),
                  "indirect": len(indirect_hits)}


# --------------------------------------------------------------- the scanners
@dataclass
class Survey:
    read: bytearray = field(default_factory=lambda: bytearray(RAM_SIZE))
    write: bytearray = field(default_factory=lambda: bytearray(RAM_SIZE))
    addr: bytearray = field(default_factory=lambda: bytearray(RAM_SIZE))
    aread: bytearray = field(default_factory=lambda: bytearray(RAM_SIZE))
    awrite: bytearray = field(default_factory=lambda: bytearray(RAM_SIZE))
    aaddr: bytearray = field(default_factory=lambda: bytearray(RAM_SIZE))
    ptr: bytearray = field(default_factory=lambda: bytearray(RAM_SIZE))
    ptrcal: bytearray = field(default_factory=lambda: bytearray(RAM_SIZE))
    init: bytearray = field(default_factory=lambda: bytearray(RAM_SIZE))
    sites: dict = field(default_factory=dict)       # addr -> [(site, mnem, kind)]

    def bump(self, arr, addr: int, width: int) -> None:
        for a in range(addr, addr + width):
            if RAM_LO <= a < RAM_HI:
                i = a - RAM_LO
                if arr[i] < 255:
                    arr[i] += 1


def words(data: bytes):
    """Yield (cpu, file_off, word) for every aligned word of the code regions."""
    for base, length in CODE_REGIONS:
        fo0 = m.cpu_to_file(base)
        for off in range(0, length, 4):
            yield base + off, fo0 + off, struct.unpack_from(">I", data, fo0 + off)[0]


def scan_r13(data: bytes, s: Survey) -> int:
    n = 0
    for cpu, _fo, w in words(data):
        op = w >> 26
        if (w >> 16) & 0x1F != 13:
            continue
        rt = (w >> 21) & 0x1F
        got = access_kind(op, rt)
        if got is None:
            continue
        kind, width = got
        target = R13 + exts16(w & 0xFFFF)
        if not (RAM_LO <= target < RAM_HI):
            continue
        s.bump({"read": s.read, "write": s.write, "addr": s.addr}[kind], target, width)
        s.sites.setdefault(target, []).append((cpu, NAMES.get(op, str(op)), kind, "r13"))
        n += 1
    return n


def scan_absolute(data: bytes, s: Survey) -> int:
    """lis rD,hi  ...  <D-form|addi|ori> rX,lo(rD)  -- find_abs_refs.py's resolver."""
    n = 0
    size = len(data)
    code_offs = []
    for base, length in CODE_REGIONS:
        fo0 = m.cpu_to_file(base)
        code_offs.append(range(fo0, fo0 + length, 4))
    for fo in (o for rng in code_offs for o in rng):
        x = struct.unpack_from(">I", data, fo)[0]
        if (x >> 26) != 15 or ((x >> 16) & 0x1F) != 0:
            continue                                  # not lis
        rd, hi = (x >> 21) & 0x1F, x & 0xFFFF
        for j in range(1, 12):
            k = fo + 4 * j
            if k >= size:
                break
            y = struct.unpack_from(">I", data, k)[0]
            op = y >> 26
            rt = (y >> 21) & 0x1F
            if op == 24 and rt == rd:                 # ori rA,rS=rd,uimm
                target = (hi << 16) | (y & 0xFFFF)
                kind, width = "addr", 1
            elif ((y >> 16) & 0x1F) == rd and access_kind(op, rt) is not None:
                kind, width = access_kind(op, rt)
                target = ((hi << 16) + exts16(y & 0xFFFF)) & 0xFFFFFFFF
            elif op in (14, 15, 32, 34, 40, 42, 48, 31) and rt == rd:
                break                                 # rd overwritten
            else:
                continue
            if RAM_LO <= target < RAM_HI:
                s.bump({"read": s.aread, "write": s.awrite,
                        "addr": s.aaddr}[kind], target, width)
                s.sites.setdefault(target, []).append(
                    (m.file_to_cpu(k), NAMES.get(op, str(op)), kind, "abs"))
                n += 1
            break
    return n


def scan_pointers(data: bytes, s: Survey) -> list:
    """Aligned 32-bit words in either flash region whose value is a RAM address."""
    hits = []
    for base, length in PTR_REGIONS:
        fo0 = m.cpu_to_file(base)
        for off in range(0, length, 4):
            v = struct.unpack_from(">I", data, fo0 + off)[0]
            if RAM_LO <= v < RAM_HI:
                site = base + off
                cal = CAL_LO <= site < CAL_HI
                s.bump(s.ptrcal if cal else s.ptr, v, 1)
                hits.append((site, v, "cal" if cal else "code"))
    return hits


# Every RAM range the cold-start path fills with a constant, with the function
# that does it.  Derived by walking the 160 functions reachable from
# `app_entry_crt0` (0x09E3B4) and extracting the `lis`+`addi` bounds of each
# `stwu rV,4(rP)` / `bdnz` fill loop; see re/findings/ram.md section 3.
# CORRECTION to re/findings/eeprom.md section 6: the external SRAM *is*
# cleared, from 0x800004 to 0x80498F.
COLDSTART_FILLS = (
    (0x7F802C, 0x7F807C, "app_entry_crt0 0x09E3B4 @ 0x09E3D0"),
    (0x7F8080, 0x7F80E8, "app_entry_crt0 0x09E3B4 @ 0x09E410"),
    (0x7F80EC, 0x7F80FC, "FUN_0012C25C @ 0x12C348"),
    (0x7F8104, 0x7F8233, "app_init 0x04CCD4 @ 0x04CD8C"),
    (0x7F8490, 0x7FA630, "app_init 0x04CCD4 @ 0x04CF94"),
    (0x7FA630, 0x7FAAC0, "app_init 0x04CCD4 @ 0x04CFCC"),
    (0x7FAAC4, 0x7FB330, "app_init 0x04CCD4 @ 0x04CEDC"),
    (0x7FB330, 0x7FDA90, "app_init 0x04CCD4 @ 0x04CDC4"),
    (0x7FDA90, 0x7FE0C0, "ram_clear_block 0x06D8F8 @ 0x06DAC0"),
    (0x7FE588, 0x7FEFE0, "app_init 0x04CCD4 @ 0x04CDFC"),
    (0x7FF3C0, 0x7FF76C, "FUN_0012C25C @ 0x12C40C (the task stack)"),
    (0x800004, 0x800D08, "app_init 0x04CCD4 @ 0x04CF1C"),
    (0x800D08, 0x803620, "ram_clear_block 0x06D8F8 @ 0x06DB24"),
    (0x803620, 0x804990, "ram_clear_block 0x06D8F8 @ 0x06DB88"),
)

# The four start/end lists at 0x5C2E14 / 0x5C2E24 / 0x5C2E50 / 0x5C2E78 with
# the 0xAAAAAAAA / 0x55555555 patterns.  No instruction reads them
# (re/findings/eeprom.md 6); kept for reference only, they are NOT counted as
# coverage.
MEMTEST_DESCRIPTORS = (
    (0x7F8104, 0x7F8234, "0x5C2E14"),
    (0x7F8104, 0x7F8369, "0x5C2E24"),
    (0x7FAAD0, 0x7FE0BD, "0x5C2E50"),
    (0x7FF770, 0x7FFFED, "0x5C2E50"),
    (0x7F8490, 0x7FAAC1, "0x5C2E50"),
    (0x800004, 0x807FF9, "0x5C2E78"),
)


def scan_init(s: Survey) -> None:
    for lo, hi, _why in COLDSTART_FILLS:
        for a in range(max(lo, RAM_LO), min(hi, RAM_HI)):
            s.init[a - RAM_LO] = 1


def scan_measuring_vars(s: Survey, path: str) -> int:
    """Mark the RAM cells of `re/measuring_vars.csv` (A3, issue #20).

    A measuring variable is read by the KWP 0x21 handlers through a table, so
    the cell may have no ordinary reference of its own; treat every cell as
    used.  Missing file = skip (the scan is still valid, just less complete).
    """
    n = 0
    try:
        fh = open(path, encoding="utf-8")
    except OSError:
        return -1
    with fh:
        header = fh.readline()
        if "ram_addr" not in header:
            return -1
        for line in fh:
            parts = line.split(",")
            if len(parts) < 3:
                continue
            try:
                addr = int(parts[1], 16)
                size = max(int(parts[2]), 1)
            except ValueError:
                continue
            if RAM_LO <= addr < RAM_HI:
                s.bump(s.addr, addr, size)
                n += 1
    return n


def known_at(addr: int, blocking_only: bool = False) -> list:
    return [k for k in KNOWN if k.start <= addr < k.end
            and not (blocking_only and k.candidate)]


# ------------------------------------------------------------------- reporting
def line_rows(s: Survey, line: int) -> list:
    rows = []
    for base in range(RAM_LO, RAM_HI, line):
        i = base - RAM_LO
        r13r = sum(s.read[i:i + line])
        r13w = sum(s.write[i:i + line])
        r13a = sum(s.addr[i:i + line])
        absr = sum(s.aread[i:i + line])
        absw = sum(s.awrite[i:i + line])
        absa = sum(s.aaddr[i:i + line])
        ptr = sum(s.ptr[i:i + line])
        ptrc = sum(s.ptrcal[i:i + line])
        ini = sum(s.init[i:i + line])
        names = sorted({k.name for a in range(base, base + line) for k in known_at(a)})
        blocking = {k.name for a in range(base, base + line)
                    for k in known_at(a, blocking_only=True)}
        refs = r13r + r13w + r13a + absr + absw + absa + ptr + ptrc
        free = refs == 0 and not blocking
        rows.append({
            "start": base, "end": base + line - 1,
            "r13_read": r13r, "r13_write": r13w, "r13_addr": r13a,
            "abs_read": absr, "abs_write": absw, "abs_addr": absa,
            "ptr_words": ptr, "ptr_cal": ptrc, "init_bytes": ini,
            "known": "+".join(names), "free_candidate": int(free),
        })
    return rows


PAGE = 256


def page_map(s: Survey) -> str:
    """One character per 256-byte page of 0x7F8000-0x807FFF."""
    out = []
    for base in range(RAM_LO, RAM_HI, PAGE):
        i = base - RAM_LO
        w = sum(s.write[i:i + PAGE]) + sum(s.awrite[i:i + PAGE])
        r = sum(s.read[i:i + PAGE]) + sum(s.aread[i:i + PAGE])
        a = (sum(s.addr[i:i + PAGE]) + sum(s.aaddr[i:i + PAGE])
             + sum(s.ptr[i:i + PAGE]) + sum(s.ptrcal[i:i + PAGE]))
        names = {k.name for x in range(base, base + PAGE, 4) for k in known_at(x)}
        if w and r:
            c = "#"
        elif w:
            c = "w"
        elif r:
            c = "r"
        elif a:
            c = "p"
        elif names:
            c = "K"
        else:
            c = "."
        out.append(c)
    return "".join(out)


def free_runs(s: Survey, min_len: int = 16) -> list:
    """Maximal runs with no reference of any kind and no known structure."""
    runs, start = [], None
    for a in range(RAM_LO, RAM_HI):
        i = a - RAM_LO
        used = (s.read[i] or s.write[i] or s.addr[i] or s.aread[i] or s.awrite[i]
                or s.aaddr[i] or s.ptr[i] or s.ptrcal[i]
                or known_at(a, blocking_only=True))
        if not used and start is None:
            start = a
        elif used and start is not None:
            if a - start >= min_len:
                runs.append((start, a))
            start = None
    if start is not None and RAM_HI - start >= min_len:
        runs.append((start, RAM_HI))
    return runs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("--csv", help="write the per-line map here")
    ap.add_argument("--json", help="write the machine-readable summary here")
    ap.add_argument("--line", type=int, default=32, help="bytes per row (default 32)")
    ap.add_argument("--runs", type=int, default=12, help="how many free runs to print")
    ap.add_argument("--min-run", type=lambda v: int(v, 0), default=16,
                    help="shortest free run to report (default 16)")
    ap.add_argument("--stack", action="store_true",
                    help="deepest stwu chain per task entry (task 4 of C2)")
    ap.add_argument("--measuring-vars", default="re/measuring_vars.csv",
                    help="CSV of measuring-variable RAM cells to mark as used")
    ap.add_argument("--indexed", action="store_true",
                    help="hunt indexed/auto-increment regions from every "
                         "lis+addi RAM base (task 2 of brief C2)")
    ap.add_argument("--indexed-min", type=lambda v: int(v, 0), default=0x10,
                    help="only print extents of at least this many bytes")
    ap.add_argument("--base", type=lambda v: int(v, 0), action="append",
                    help="restrict --indexed to these bases (repeatable)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if args.stack:
        rows, meta = stack_estimate(args.image)
        print("# stack estimate: deepest chain of `stwu r1,-N(r1)` prologues "
              "from each of the\n# %d task entries of tbl_os_task_control_blocks "
              "(0x%06X).  Indirect calls\n# (bctrl/blrl) truncate a chain, so "
              "this is a LOWER bound on the worst case:\n# %d of the %d "
              "functions walked contain one."
              % (meta["tasks"], TCB_TABLE, meta["indirect"], meta["functions"]))
        print("%-10s %-8s %s" % ("task", "depth", "deepest chain (head)"))
        for t, d, path in rows:
            chain = " -> ".join("0x%06X" % a for a in path[:6])
            print("0x%06X %-8s %s%s" % (t, "0x%X" % d, chain,
                                        " ..." if len(path) > 6 else ""))
        worst = rows[0][1] if rows else 0
        print()
        print("worst task chain          0x%X B" % worst)
        print("+ one 0x%X B ISR frame     0x%X B" % (ISR_FRAME, worst + ISR_FRAME))
        print("stack top (app)           0x%06X  (r1 = 0x7FF768)" % STACK_TOP_APP)
        print("lowest r1 reached         0x%06X" % (STACK_TOP_APP - worst - ISR_FRAME))
        print("kernel stack limit        0x%06X  (descriptor 0x09B6F8+0x0C)"
              % STACK_LIMIT)
        head = STACK_TOP_APP - worst - ISR_FRAME - STACK_LIMIT
        if head >= 0:
            print("headroom above the limit  0x%X B" % head)
        else:
            print("OVERSHOOT below the limit 0x%X B -- either those call paths "
                  "are mutually\nexclusive at run time (the walk assumes every "
                  "`bl` is taken) or the stack\nreally runs below 0x%06X.  The "
                  "RAM is unreferenced down to 0x7FF01B, which\nis taken as the "
                  "stack floor; see re/findings/ram.md section 5."
                  % (-head, STACK_LIMIT))
        return 0

    data = m.load_dump(args.image)
    s = Survey()
    n_r13 = scan_r13(data, s)
    n_abs = scan_absolute(data, s)
    ptrs = scan_pointers(data, s)
    scan_init(s)
    n_mv = scan_measuring_vars(s, args.measuring_vars)

    rows = line_rows(s, args.line)
    runs = free_runs(s, args.min_run)
    runs_sorted = sorted(runs, key=lambda r: r[1] - r[0], reverse=True)

    if args.indexed:
        want = set(args.base) if args.base else None
        print("# indexed-region hunt: every RAM base built by an absolute "
              "lis+addi, with\n# the largest offset the owning code reaches "
              "from it.  Heuristic: the walk\n# is linear, so a loop count "
              "taken from the wrong side of a branch can be\n# off by one "
              "iteration.  Calibration: 0x804800 -> 0x3E80 (true 0x3E88).")
        print("# Only bases followed by unreferenced space matter for "
              "placement, so the\n# default listing is sorted by the "
              "neighbour bound (the free run that starts\n# just after the "
              "base).  A base with a large neighbour bound AND a non-zero\n"
              "# extent is a region that reaches into that free run.")
        print("%-10s %-10s %-8s %-10s %s"
              % ("base", "site", "extent", "neighbour", "kind / note"))
        found = indexed_bases(data, s)
        found.sort(key=lambda e: e.neighbour, reverse=True)
        for ex in found:
            if want is not None and ex.base not in want:
                continue
            if want is None and ex.neighbour < args.indexed_min:
                continue
            print("0x%06X 0x%06X %-8s %-10s %s  [%s]" % (
                ex.base, ex.site, "0x%X" % ex.size, "0x%X" % ex.neighbour,
                ex.tag, ex.note))
        return 0

    if not args.quiet:
        print(f"# ram_survey {args.image}")
        print(f"# RAM 0x{RAM_LO:06X}-0x{RAM_HI - 1:06X}, {args.line}-byte lines, "
              f"{len(rows)} rows")
        print(f"# r13 D-form accesses into RAM : {n_r13}")
        print(f"# absolute lis+D-form into RAM : {n_abs}")
        n_cal = sum(1 for _s, _v, k in ptrs if k == "cal")
        print(f"# pointer words in flash       : {len(ptrs)} "
              f"({len(ptrs) - n_cal} in code, {n_cal} in the calibration block)")
        print(f"# measuring-variable cells      : "
              f"{n_mv if n_mv >= 0 else 'not loaded (' + args.measuring_vars + ')'}")
        touched = sum(1 for i in range(RAM_SIZE)
                      if s.read[i] or s.write[i] or s.addr[i] or s.aread[i]
                      or s.awrite[i] or s.aaddr[i] or s.ptr[i] or s.ptrcal[i])
        print(f"# bytes with >=1 static reference: {touched} "
              f"({100.0 * touched / RAM_SIZE:.1f} %)")
        print(f"# free_candidate lines          : "
              f"{sum(r['free_candidate'] for r in rows)} / {len(rows)}")
        print()
        print("page map, one char per 256 B; "
              "# read+write  w write  r read  p pointer/addi only  K known  . free")
        pm = page_map(s)
        for k in range(0, len(pm), 64):
            print(f"  0x{RAM_LO + k * PAGE:06X}  {pm[k:k + 64]}")
        print()
        print(f"longest reference-free runs (>= {args.min_run} B); `prev` is the "
              f"last referenced\nbyte before the run and the extent its owner "
              f"reaches -- a run inside that\nextent is an indexed array, not "
              f"free space:")
        for lo, hi in runs_sorted[:args.runs]:
            prev = lo - 1
            while prev > RAM_LO and not referenced(s, prev):
                prev -= 1
            ex = None
            for site, _mn, kind, how in s.sites.get(prev, []):
                if how == "abs" and kind == "addr":
                    cand = hunt_extent(data, prev, site)
                    if ex is None or cand.size > ex.size:
                        ex = cand
            if ex is None:
                info = "prev 0x%06X (no lis+addi base)" % prev
            else:
                reach = prev + ex.size
                info = ("prev 0x%06X extent 0x%X -> 0x%06X%s"
                        % (prev, ex.size, reach,
                           "  ** COVERS THIS RUN **" if reach > lo else ""))
            print(f"  0x{lo:06X}-0x{hi - 1:06X}  {hi - lo:6d} B   {info}")
        print()
        print("cold-start constant fills (evidence in re/findings/ram.md 3):")
        for lo, hi, why in COLDSTART_FILLS:
            print(f"  0x{lo:06X}-0x{hi - 1:06X}  {hi - lo:6d} B   {why}")
        gaps, a = [], RAM_LO
        covered = sorted(COLDSTART_FILLS)
        for lo, hi, _ in covered:
            if lo > a:
                gaps.append((a, lo))
            a = max(a, hi)
        if a < RAM_HI:
            gaps.append((a, RAM_HI))
        print("NOT filled at cold start (undefined at power-on):")
        for lo, hi in gaps:
            if hi - lo >= 4:
                print(f"  0x{lo:06X}-0x{hi - 1:06X}  {hi - lo:6d} B")
        print()
        print("known structures:")
        for k in sorted(KNOWN, key=lambda k: k.start):
            print(f"  0x{k.start:06X}-0x{k.end - 1:06X}  {k.name:<28s} "
                  f"{k.tag:<16s} {k.source}")

    if args.csv:
        with open(args.csv, "w", encoding="utf-8") as fh:
            cols = ["start", "end", "r13_read", "r13_write", "r13_addr",
                    "abs_read", "abs_write", "abs_addr", "ptr_words", "ptr_cal",
                    "init_bytes", "known", "free_candidate"]
            fh.write(",".join(cols) + "\n")
            for r in rows:
                fh.write("0x%06X,0x%06X,%d,%d,%d,%d,%d,%d,%d,%d,%d,%s,%d\n" % (
                    r["start"], r["end"], r["r13_read"], r["r13_write"],
                    r["r13_addr"], r["abs_read"], r["abs_write"], r["abs_addr"],
                    r["ptr_words"], r["ptr_cal"], r["init_bytes"], r["known"],
                    r["free_candidate"]))
        if not args.quiet:
            print(f"\nwrote {args.csv}")

    if args.json:
        doc = {
            "image": os.path.basename(args.image),
            "ram": {"start": hex(RAM_LO), "end": hex(RAM_HI - 1)},
            "counts": {"r13": n_r13, "absolute": n_abs, "pointer_words": len(ptrs)},
            "free_runs": [{"start": hex(lo), "end": hex(hi - 1), "size": hi - lo}
                          for lo, hi in runs_sorted],
            "known": [{"start": hex(k.start), "end": hex(k.end - 1), "name": k.name,
                       "tag": k.tag, "source": k.source, "dynamic": k.dynamic}
                      for k in sorted(KNOWN, key=lambda k: k.start)],
        }
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
        if not args.quiet:
            print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
