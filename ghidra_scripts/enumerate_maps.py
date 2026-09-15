#!/usr/bin/env python3
# Enumerate MED9.1.1 calibration tables from interpolation-helper call sites.
# @category MED9
# @runtime PyGhidra
"""enumerate_maps.py -- machine-generated draft of the calibration maps.

The Bosch MED9 application does not inline its table lookups: every curve and
map goes through one of ~40 small helpers that live together in the on-chip
flash at 0x40C000-0x411FFF (issue #19, brief B5).  Two shapes exist:

*self-describing* tables carry their dimensions in the first one or two
elements and are passed as a single pointer::

    lookup_1d_u16(struct, x)          struct = { u16 n; u16 axis[n]; u16 val[n] }
    lookup_2d_u16(struct, vy, vx)     struct = { u16 ny; u16 nx;
                                                 u16 yaxis[ny]; u16 xaxis[nx];
                                                 u16 val[ny*nx] }

*group* tables share their axes with other tables, so the sizes arrive as
immediates and the axis and value arrays as separate pointers::

    lookup_1d_g_u8_u16(n, axis, val, x)
    lookup_2d_g_u8_u16_u16(ny, yaxis, nx, xaxis, val, vy, vx)

In both cases the value array is indexed ``val[iy * nx + ix]``: the *second*
axis argument is the one with stride 1, i.e. the X (column) axis in TunerPro
terms, and the first is the Y (row) axis.  See ``re/findings/calibration_maps.md``
for the derivation of every helper.

What this script does:

1. walks every call site of every helper in ``HELPERS``;
2. recovers the constant arguments by simulating the basic block that ends at
   the call (``lis``/``addi``/``li``/``ori``/``mr``, plus ``addi rX,r2,disp``
   with r2 from the Ghidra register context, 0x5C9FF0 in the application);
3. reads the dimensions out of the calibration image for the self-describing
   forms and validates the result (fits in 0x5C0000-0x5FFFFF, plausible n,
   monotonically rising axis);
4. also records every direct ``lbz``/``lhz``/``lha`` of a calibration byte or
   word as a scalar candidate;
5. writes ``re/calibration_draft.csv`` (one row per distinct object),
   ``re/findings/calibration_call_sites.csv`` (one row per call site) and
   ``re/findings/calibration_coverage.md`` (what is left over).

Run it as a Ghidra post-script::

    ./.venv/bin/python -m pyghidra.ghidra_launch \
        --install-dir "$GHIDRA_INSTALL_DIR" \
        ghidra.app.util.headless.AnalyzeHeadless <projdir> med9 \
        -process passat_azx_ori.bin -noanalysis \
        -scriptPath ghidra_scripts -postScript enumerate_maps.py <repo-root>

or standalone::

    ./.venv/bin/python ghidra_scripts/enumerate_maps.py \
        --project-dir /tmp/ghidra_B5 --project-name med9 --repo .

Options (script args in both forms):
    --label        also put ``cand_*`` labels on the detected tables in the
                   Ghidra program (off by default: export_symbols.py would
                   then push a thousand rows into the shared re/symbols.csv)
    --name-helpers name the interpolation helpers and give them plate
                   comments with the confidence tag (on by default)
    --no-scalars   skip the direct-load scan
"""
from __future__ import annotations

import os
import sys

# --------------------------------------------------------------------------
# Verified layout (docs/02_memory_map.md sections 3 and 9).  CPU addresses.
# --------------------------------------------------------------------------
CAL_START = 0x5C0000
CAL_END = 0x600000                 # exclusive
CAL_FILE_DELTA = 0x400000          # cpu - 0x400000 = file offset
R2_APP = 0x5C9FF0

# Region the brief asks to account for, plus the block reserved for our own
# calibration (docs/06_patch_pipeline.md section 3).
COVERAGE_START = 0x5C2000
COVERAGE_END = 0x5E3000            # exclusive
FFCAL_START = 0x5E2510

# Structures that are documented and are *not* maps (docs/02_memory_map.md
# sections 1, 5, 6, 7).  Used by the coverage report.
KNOWN_NON_MAP = [
    (0x5C2000, 0x5C2200, "calibration directory, 5A5A5A5A/CCCCCCCC + pointers"),
    (0x5C3300, 0x5C3360, "calibration block checksum table, 6 x 16 B"),
    (0x5C5518, 0x5C5D0C, "measuring-block group table, 4 x 255 x u16"),
    (0x5CEE20, 0x5CEE70, "ECU identification block"),
]

# --------------------------------------------------------------------------
# The interpolation helpers.  Derived by decompiling every function in
# 0x40C000-0x411FFF; see re/findings/calibration_maps.md for the evidence.
#
#   form   "kl"       1-D, self-describing        (struct, x)
#          "kl_g"     1-D, shared axis            (n, axis, val, x)
#          "kf"       2-D, self-describing        (struct, vy, vx)
#          "kf_g"     2-D, shared axes            (ny, yaxis, nx, xaxis, val, vy, vx)
#          "axis"     axis search only            (struct, x)
#          "axis_h"   axis search with hint       (struct, x, prev_key)
#          "core2d"   value-array interpolator    (val, nx, key_y, key_x)
#   ax     (signed, size) of the axis elements, self-describing forms
#   ay/ax  ditto for the group forms (ay = first/outer, ax = second/inner)
#   val    (signed, size) of the value elements
#   interp False for the two "nearest breakpoint" variants
# --------------------------------------------------------------------------
U8, S8, U16, S16 = (False, 1), (True, 1), (False, 2), (True, 2)

HELPERS = {
    # --- 1-D, self-describing: { n; axis[n]; val[n] } ---------------------
    0x40EDE4: dict(name="lookup_1d_u8", form="kl", ax=U8, val=U8, interp=True),
    0x40EEB0: dict(name="lookup_1d_s8", form="kl", ax=S8, val=S8, interp=True),
    0x40EFAC: dict(name="lookup_1d_u16", form="kl", ax=U16, val=U16, interp=True),
    0x40F098: dict(name="lookup_1d_s16", form="kl", ax=S16, val=S16, interp=True),
    0x40F184: dict(name="lookup_1d_u8_noint", form="kl", ax=U8, val=U8, interp=False),
    0x40F22C: dict(name="lookup_1d_u16_noint", form="kl", ax=U16, val=U16, interp=False),
    # --- 1-D, shared axis: (n, axis, val, x) ------------------------------
    0x40F454: dict(name="lookup_1d_g_u8_s8", form="kl_g", ax=U8, val=S8, interp=True),
    0x40F51C: dict(name="lookup_1d_g_s8_u8", form="kl_g", ax=S8, val=U8, interp=True),
    0x40F600: dict(name="lookup_1d_g_u8_u16", form="kl_g", ax=U8, val=U16, interp=True),
    0x40F6C4: dict(name="lookup_1d_g_s16_u16", form="kl_g", ax=S16, val=U16, interp=True),
    0x40F7A0: dict(name="lookup_1d_g_u16_s16", form="kl_g", ax=U16, val=S16, interp=True),
    0x40F87C: dict(name="lookup_1d_g_u16_s8", form="kl_g", ax=U16, val=S8, interp=True),
    0x40F95C: dict(name="lookup_1d_g_u16_u8", form="kl_g", ax=U16, val=U8, interp=True),
    # --- 2-D, self-describing: { ny; nx; yaxis[ny]; xaxis[nx]; val[ny*nx] }
    0x40D18C: dict(name="lookup_2d_u8", form="kf", ax=U8, val=U8, interp=True),
    0x40D2F8: dict(name="lookup_2d_u16", form="kf", ax=U16, val=U16, interp=True),
    0x40D4A4: dict(name="lookup_2d_s16", form="kf", ax=S16, val=S16, interp=True),
    0x40D650: dict(name="lookup_2d_u16_s16", form="kf", ax=U16, val=S16, interp=True),
    0x40D7FC: dict(name="lookup_2d_u8_noint", form="kf", ax=U8, val=U8, interp=False),
    # --- 2-D, shared axes: (ny, yaxis, nx, xaxis, val, vy, vx) ------------
    0x40D964: dict(name="lookup_2d_g_u8_s8_s8", form="kf_g", ay=U8, ax=S8, val=S8, interp=True),
    0x40DAF0: dict(name="lookup_2d_g_u8_s8_s16", form="kf_g", ay=U8, ax=S8, val=S16, interp=True),
    0x40DC7C: dict(name="lookup_2d_g_u8_u8_s8", form="kf_g", ay=U8, ax=U8, val=S8, interp=True),
    0x40DDE4: dict(name="lookup_2d_g_u8_u16_u8", form="kf_g", ay=U8, ax=U16, val=U8, interp=True),
    0x40DF64: dict(name="lookup_2d_g_s8_u16_u8", form="kf_g", ay=S8, ax=U16, val=U8, interp=True),
    0x40E108: dict(name="lookup_2d_g_u16_u8_u8", form="kf_g", ay=U16, ax=U8, val=U8, interp=True),
    0x40E288: dict(name="lookup_2d_g_s16_u8_s16", form="kf_g", ay=S16, ax=U8, val=S16, interp=True),
    0x40E408: dict(name="lookup_2d_g_s16_u8_u16", form="kf_g", ay=S16, ax=U8, val=U16, interp=True),
    0x40E588: dict(name="lookup_2d_g_u16_s8_s8", form="kf_g", ay=U16, ax=S8, val=S8, interp=True),
    0x40E72C: dict(name="lookup_2d_g_u8_u16_u16", form="kf_g", ay=U8, ax=U16, val=U16, interp=True),
    0x40E8AC: dict(name="lookup_2d_g_s16_u16_u16", form="kf_g", ay=S16, ax=U16, val=U16, interp=True),
    0x40EA44: dict(name="lookup_2d_g_s8_u8_u8", form="kf_g", ay=S8, ax=U8, val=U8, interp=True),
    0x40EBD0: dict(name="lookup_2d_g_s8_u8_s16", form="kf_g", ay=S8, ax=U8, val=S16, interp=True),
    # --- axis search on a self-describing axis { n; axis[n] } -------------
    0x40C578: dict(name="axis_search_u8", form="axis", ax=U8),
    0x40C614: dict(name="axis_search_s8", form="axis", ax=S8),
    0x40C6D8: dict(name="axis_search_u16", form="axis", ax=U16),
    0x40C78C: dict(name="axis_search_s16", form="axis", ax=S16),
    0x40C840: dict(name="axis_search_u8_hint", form="axis_h", ax=U8),
    0x40C8F0: dict(name="axis_search_s8_hint", form="axis_h", ax=S8),
    0x40C9CC: dict(name="axis_search_u16_hint", form="axis_h", ax=U16),
    0x40CA94: dict(name="axis_search_s16_hint", form="axis_h", ax=S16),
    # --- value-array interpolators, called with a pre-computed key --------
    0x40C334: dict(name="interp_2d_u8", form="core2d", val=U8, interp=True),
    0x40C3B4: dict(name="interp_2d_s8", form="core2d", val=S8, interp=True),
    0x40C444: dict(name="interp_2d_u16", form="core2d", val=U16, interp=True),
    0x40C4D0: dict(name="interp_2d_s16", form="core2d", val=S16, interp=True),
    0x40C55C: dict(name="interp_2d_u8_noint", form="core2d", val=U8, interp=False),
}

# argument register (0-based over r3..) for each named slot, per form
ARGS = {
    "kl":     dict(ptr=0),
    "kf":     dict(ptr=0),
    "axis":   dict(ptr=0),
    "axis_h": dict(ptr=0),
    "kl_g":   dict(n=0, axis=1, val=2),
    "kf_g":   dict(ny=0, yaxis=1, nx=2, xaxis=3, val=4),
    "core2d": dict(val=0, nx=1),
}

MAX_N = 1024                 # largest plausible breakpoint count
MAX_STRUCT = 0x8000          # largest plausible table, bytes
MAX_ROWS = 20                # cap for an inferred row count (max seen: 16)

# The twelve columns the brief asks for, plus four that the XDF generator
# needs and that would otherwise have to be re-derived from the evidence text.
CSV_COLUMNS = ["addr", "kind", "x_axis_addr", "y_axis_addr", "x_n", "y_n",
               "elem_size", "signed", "consumer_func", "name_or_blank",
               "confidence", "evidence",
               "x_elem", "y_elem", "struct_addr", "sites"]
SITE_COLUMNS = ["site", "helper", "helper_addr", "kind", "addr", "x_axis_addr",
                "y_axis_addr", "x_n", "y_n", "elem_size", "signed",
                "consumer_func", "confidence", "note"]


def h(value):
    return "0x%06X" % value


def elem_name(pair):
    """(signed, size) -> 'u8' / 's16' / ''."""
    if not pair:
        return ""
    return ("s" if pair[0] else "u") + str(pair[1] * 8)


# --------------------------------------------------------------------------
# Constant-argument recovery
# --------------------------------------------------------------------------
class RegSim(object):
    """Simulate the integer address arithmetic of a straight run of code.

    Only the instructions the code generator uses to build a constant are
    modelled; every other instruction marks its destination registers unknown,
    which is what keeps false positives out.
    """

    # Read-only regions whose bytes a constant load may be folded from.
    ROM = ((0x000000, 0x200000), (0x404000, 0x480000), (CAL_START, CAL_END))
    LOADS = {"lbz": 1, "lhz": 2, "lha": -2, "lwz": 4}

    def __init__(self, program):
        from ghidra.program.model.block import BasicBlockModel
        from ghidra.util.task import TaskMonitor

        self.program = program
        self.listing = program.getListing()
        self.memory = program.getMemory()
        self.space = program.getAddressFactory().getDefaultAddressSpace()
        self.bbm = BasicBlockModel(program)
        self.monitor = TaskMonitor.DUMMY
        self.ctx = program.getProgramContext()
        self.r2reg = self.ctx.getRegister("r2")

    def _load(self, addr, width):
        """Fold a load from read-only flash into a constant.

        The generated code passes the breakpoint count of a shared-axis table
        by reading it out of the table (``lbz r3,0x0(r31)``), so without this
        the group forms never resolve.  Only flash is folded; r13-relative RAM
        stays unknown because r13 is never seeded.
        """
        size = abs(width)
        if not any(lo <= addr and addr + size <= hi for lo, hi in self.ROM):
            return None
        try:
            value = 0
            for i in range(size):
                value = (value << 8) | (self.memory.getByte(
                    self.space.getAddress(addr + i)) & 0xFF)
        except Exception:                                     # noqa: BLE001
            return None
        if width < 0 and value >= (1 << (size * 8 - 1)):
            value -= 1 << (size * 8)
        return value & 0xFFFFFFFF

    def _r2_at(self, addr):
        if self.r2reg is not None:
            value = self.ctx.getRegisterValue(self.r2reg, addr)
            if value is not None and value.getUnsignedValue() is not None:
                return int(str(value.getUnsignedValue()))
        return R2_APP

    def _window(self, call_addr, extended):
        """Instructions to simulate, in address order, ending before the call."""
        func = self.program.getFunctionManager().getFunctionContaining(call_addr)
        low = func.getEntryPoint() if func is not None else None
        block = self.bbm.getFirstCodeBlockContaining(call_addr, self.monitor)
        start = block.getMinAddress() if block is not None else call_addr
        if extended:
            back = call_addr.subtract(0x180)
            if back.compareTo(start) < 0:
                start = back
        if low is not None and start.compareTo(low) < 0:
            start = low
        out = []
        ins = self.listing.getInstructionAt(start)
        if ins is None:
            ins = self.listing.getInstructionAfter(start)
        while ins is not None and ins.getAddress().compareTo(call_addr) < 0:
            out.append(ins)
            ins = ins.getNext()
        return out

    @staticmethod
    def _regnum(obj):
        try:
            name = str(obj.getName())
        except Exception:                                     # noqa: BLE001
            return None
        if name.startswith("r") and name[1:].isdigit():
            return int(name[1:])
        return None

    def _scalar(self, ins, index):
        value = ins.getScalar(index)
        return None if value is None else int(value.getSignedValue())

    def _opreg(self, ins, index):
        objs = ins.getOpObjects(index)
        for obj in objs:
            num = self._regnum(obj)
            if num is not None:
                return num
        return None

    @staticmethod
    def _regnum_of_op(ins, index):
        for obj in ins.getOpObjects(index):
            num = RegSim._regnum(obj)
            if num is not None:
                return num
        return None

    @staticmethod
    def _mem_operand(ins, index):
        """(displacement, base register) of a ``disp(rA)`` operand.

        Ghidra renders the whole of ``0x0(r31)`` as one operand, so
        ``getScalar()`` returns nothing and the parts have to come out of
        ``getOpObjects()``.
        """
        disp = None
        base = None
        for obj in ins.getOpObjects(index):
            num = RegSim._regnum(obj)
            if num is not None:
                base = num
                continue
            try:
                disp = int(obj.getSignedValue())
            except Exception:                                 # noqa: BLE001
                pass
        return disp, base

    def run(self, call_addr, extended=False):
        regs = {2: self._r2_at(call_addr)}
        for ins in self._window(call_addr, extended):
            self.step(ins, regs)
        return regs

    def step(self, ins, regs):
        """Apply one instruction to the register file, in place."""
        if True:
            mnem = str(ins.getMnemonicString())
            handled = False
            if mnem in self.LOADS:
                dst = self._opreg(ins, 0)
                disp, base = self._mem_operand(ins, 1)
                if dst is not None:
                    value = None
                    if base is not None and disp is not None and regs.get(base) is not None:
                        value = self._load((regs[base] + disp) & 0xFFFFFFFF,
                                           self.LOADS[mnem])
                    regs[dst] = value
                    handled = True
            elif mnem in ("lis", "li", "addi", "addis", "subi", "ori", "or",
                          "mr", "subis", "oris"):
                dst = self._opreg(ins, 0)
                if dst is not None:
                    value = None
                    if mnem == "lis":
                        imm = self._scalar(ins, 1)
                        if imm is not None:
                            value = (imm & 0xFFFF) << 16
                    elif mnem == "li":
                        value = self._scalar(ins, 1)
                    elif mnem in ("mr",):
                        src = self._opreg(ins, 1)
                        value = regs.get(src)
                    elif mnem == "or":
                        src = self._opreg(ins, 1)
                        src2 = self._opreg(ins, 2)
                        value = regs.get(src) if src == src2 else None
                    else:
                        src = self._opreg(ins, 1)
                        imm = self._scalar(ins, 2)
                        base = regs.get(src)
                        if base is not None and imm is not None:
                            if mnem == "addi":
                                value = base + imm
                            elif mnem == "subi":
                                value = base - imm
                            elif mnem == "addis":
                                value = base + ((imm & 0xFFFF) << 16)
                            elif mnem == "subis":
                                value = base - ((imm & 0xFFFF) << 16)
                            elif mnem == "ori":
                                value = base | (imm & 0xFFFF)
                            elif mnem == "oris":
                                value = base | ((imm & 0xFFFF) << 16)
                    regs[dst] = None if value is None else (value & 0xFFFFFFFF)
                    handled = True
            if not handled:
                for obj in ins.getResultObjects():
                    num = self._regnum(obj)
                    if num is not None:
                        regs[num] = None


# --------------------------------------------------------------------------
# Calibration image access
# --------------------------------------------------------------------------
class Cal(object):
    def __init__(self, program):
        import jpype

        space = program.getAddressFactory().getDefaultAddressSpace()
        size = CAL_END - CAL_START
        buf = jpype.JArray(jpype.JByte)(size)
        program.getMemory().getBytes(space.getAddress(CAL_START), buf)
        self.data = bytes((int(b) & 0xFF) for b in buf)

    def inside(self, addr, length=1):
        return CAL_START <= addr and addr + length <= CAL_END

    def read(self, addr, signed, size):
        off = addr - CAL_START
        if size == 1:
            value = self.data[off]
            return value - 0x100 if signed and value > 0x7F else value
        value = (self.data[off] << 8) | self.data[off + 1]
        return value - 0x10000 if signed and value > 0x7FFF else value

    def slice(self, addr, length):
        off = addr - CAL_START
        return self.data[off:off + length]

    def monotonic(self, addr, n, signed, size):
        """True if the n breakpoints rise strictly.  The single strongest
        structural test we have: a Bosch axis is always sorted."""
        prev = None
        for i in range(n):
            value = self.read(addr + i * size, signed, size)
            if prev is not None and value <= prev:
                return False
            prev = value
        return True


# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------
class Obj(object):
    """One detected calibration object."""

    def __init__(self, kind, addr):
        self.kind = kind
        self.addr = addr                 # value/data array (what an XDF needs)
        self.struct_addr = None          # header, for the self-describing forms
        self.x_axis = None
        self.y_axis = None
        self.x_n = None
        self.y_n = None
        self.x_elem = None               # (signed, size)
        self.y_elem = None
        self.elem = None                 # (signed, size) of the values
        self.interp = True
        self.helpers = set()
        self.consumers = set()
        self.sites = []
        self.confidence = "static"
        self.notes = set()
        self.extra = []                  # (addr, length) of count bytes etc.

    @property
    def size(self):
        if self.elem is None:
            return 0
        count = 1
        if self.x_n:
            count *= self.x_n
        if self.y_n:
            count *= self.y_n
        return count * self.elem[1]

    def spans(self):
        """Every byte range this object owns, as *separate* intervals.

        A shared axis can sit far away from the value array it serves, so one
        (min, max) pair would claim everything in between and make the coverage
        report useless.
        """
        out = []
        lo = self.struct_addr if self.struct_addr is not None else self.addr
        hi = self.addr + self.size
        if hi > lo:
            out.append((lo, hi))
        for addr, n, elem in ((self.x_axis, self.x_n, self.x_elem),
                              (self.y_axis, self.y_n, self.y_elem)):
            if addr is not None and n and elem is not None:
                out.append((addr, addr + n * elem[1]))
        out.extend((addr, addr + length) for addr, length in self.extra)
        return out


class Enumerator(object):
    def __init__(self, program, repo_root, opts):
        self.program = program
        self.repo_root = repo_root
        self.opts = opts
        self.cal = Cal(program)
        self.sim = RegSim(program)
        self.listing = program.getListing()
        self.space = program.getAddressFactory().getDefaultAddressSpace()
        self.fm = program.getFunctionManager()
        self.rm = program.getReferenceManager()
        self.objects = {}                # (kind, addr) -> Obj
        self.site_rows = []
        self.axis_sites = {}             # function entry -> [(site, info)]
        self.stats = dict(sites=0, resolved=0, unresolved=0, rejected=0,
                          internal=0)

    # -- small helpers -----------------------------------------------------
    def func_name(self, addr):
        func = self.fm.getFunctionContaining(addr)
        if func is None:
            return ""
        return "%s@%s" % (func.getName(), h(int(func.getEntryPoint().getOffset())))

    def obj(self, kind, addr):
        key = (kind, addr)
        if key not in self.objects:
            self.objects[key] = Obj(kind, addr)
        return self.objects[key]

    def count_header(self, axis, n, size):
        """A shared axis usually is a self-describing { n; axis[n] } whose count
        the caller loads with ``lbz r3,0x0(rX)``.  Claim that byte too, so the
        coverage report does not report a one-byte hole in front of every axis.
        """
        head = axis - size
        if self.cal.inside(head, size) and self.cal.read(head, False, size) == n:
            return [(head, size)]
        return []

    # -- per-form decoding -------------------------------------------------
    def decode(self, spec, regs):
        """Return (Obj-shaped dict, note) or (None, reason)."""
        form = spec["form"]
        slots = ARGS[form]
        arg = lambda name: regs.get(3 + slots[name])          # noqa: E731

        if form in ("kl", "kf", "axis", "axis_h"):
            ptr = arg("ptr")
            if ptr is None:
                return None, "pointer not constant"
            if not self.cal.inside(ptr, 4):
                return None, "pointer outside calibration"
            asigned, asize = spec["ax"]
            if form in ("axis", "axis_h"):
                n = self.cal.read(ptr, False, asize)
                if not 1 <= n <= MAX_N:
                    return None, "implausible n=%d" % n
                end = ptr + asize + n * asize
                if not self.cal.inside(ptr, end - ptr):
                    return None, "axis runs past calibration"
                return dict(kind="axis", addr=ptr + asize, struct_addr=ptr,
                            x_axis=ptr + asize, x_n=n, x_elem=spec["ax"],
                            elem=spec["ax"], interp=True), ""
            if form == "kl":
                n = self.cal.read(ptr, False, asize)
                if not 1 <= n <= MAX_N:
                    return None, "implausible n=%d" % n
                axis = ptr + asize
                val = axis + n * asize
                vsigned, vsize = spec["val"]
                end = val + n * vsize
                if end - ptr > MAX_STRUCT or not self.cal.inside(ptr, end - ptr):
                    return None, "curve runs past calibration"
                return dict(kind="curve_1d", addr=val, struct_addr=ptr,
                            x_axis=axis, x_n=n, x_elem=spec["ax"],
                            elem=spec["val"], interp=spec["interp"]), ""
            # form == "kf"
            ny = self.cal.read(ptr, False, asize)
            nx = self.cal.read(ptr + asize, False, asize)
            if not (1 <= ny <= MAX_N and 1 <= nx <= MAX_N):
                return None, "implausible n=%dx%d" % (ny, nx)
            yaxis = ptr + 2 * asize
            xaxis = yaxis + ny * asize
            val = xaxis + nx * asize
            vsigned, vsize = spec["val"]
            end = val + ny * nx * vsize
            if end - ptr > MAX_STRUCT or not self.cal.inside(ptr, end - ptr):
                return None, "map runs past calibration"
            return dict(kind="map_2d", addr=val, struct_addr=ptr,
                        x_axis=xaxis, x_n=nx, x_elem=spec["ax"],
                        y_axis=yaxis, y_n=ny, y_elem=spec["ax"],
                        elem=spec["val"], interp=spec["interp"]), ""

        if form == "kl_g":
            n, axis, val = arg("n"), arg("axis"), arg("val")
            if None in (n, axis, val):
                return None, "arguments not constant"
            if not 1 <= n <= MAX_N:
                return None, "implausible n=%d" % n
            asigned, asize = spec["ax"]
            vsigned, vsize = spec["val"]
            if not (self.cal.inside(axis, n * asize) and self.cal.inside(val, n * vsize)):
                return None, "axis or values outside calibration"
            return dict(kind="curve_1d_shared", addr=val,
                        x_axis=axis, x_n=n, x_elem=spec["ax"],
                        elem=spec["val"], interp=spec["interp"],
                        extra=self.count_header(axis, n, asize)), ""

        if form == "kf_g":
            ny, yaxis = arg("ny"), arg("yaxis")
            nx, xaxis = arg("nx"), arg("xaxis")
            val = arg("val")
            if None in (ny, yaxis, nx, xaxis, val):
                return None, "arguments not constant"
            if not (1 <= ny <= MAX_N and 1 <= nx <= MAX_N):
                return None, "implausible n=%dx%d" % (ny, nx)
            ysigned, ysize = spec["ay"]
            xsigned, xsize = spec["ax"]
            vsigned, vsize = spec["val"]
            if not (self.cal.inside(yaxis, ny * ysize)
                    and self.cal.inside(xaxis, nx * xsize)
                    and self.cal.inside(val, ny * nx * vsize)):
                return None, "axis or values outside calibration"
            return dict(kind="map_2d_shared", addr=val,
                        x_axis=xaxis, x_n=nx, x_elem=spec["ax"],
                        y_axis=yaxis, y_n=ny, y_elem=spec["ay"],
                        elem=spec["val"], interp=spec["interp"],
                        extra=(self.count_header(xaxis, nx, xsize)
                               + self.count_header(yaxis, ny, ysize))), ""

        # form == "core2d": axes were searched elsewhere, only the value array
        # and the row length are visible here.
        val, nx = arg("val"), arg("nx")
        if val is None:
            return None, "value pointer not constant"
        if not self.cal.inside(val, 2):
            return None, "value pointer outside calibration"
        if nx is not None and not 1 <= nx <= MAX_N:
            nx = None
        return dict(kind="map_2d_data", addr=val, x_n=nx,
                    elem=spec["val"], interp=spec["interp"]), ""

    # -- main walk ---------------------------------------------------------
    def walk_helpers(self):
        """Two passes: the self-describing and group forms first, so that the
        bare value-array interpolators can be matched against the axis searches
        their caller did earlier in the same function."""
        for want_core in (False, True):
            for helper_addr, spec in sorted(HELPERS.items()):
                if (spec["form"] == "core2d") != want_core:
                    continue
                entry = self.space.getAddress(helper_addr)
                refs = [r for r in self.rm.getReferencesTo(entry)
                        if r.getReferenceType().isCall()]
                for ref in refs:
                    site = ref.getFromAddress()
                    self.stats["sites"] += 1
                    func = self.fm.getFunctionContaining(site)
                    if func is not None and int(func.getEntryPoint().getOffset()) in HELPERS:
                        # A 2-D wrapper tail-calling its own value interpolator.
                        # Not a table reference: its arguments are the wrapper's
                        # own parameters.
                        self.stats["internal"] += 1
                        self.site_rows.append(dict(
                            site=h(int(site.getOffset())), helper=spec["name"],
                            helper_addr=h(helper_addr), kind="", addr="",
                            x_axis_addr="", y_axis_addr="", x_n="", y_n="",
                            elem_size="", signed="",
                            consumer_func=self.func_name(site),
                            confidence="library-internal",
                            note="call from inside the interpolation library"))
                        continue
                    self.handle_site(site, helper_addr, spec)
            for sites in self.axis_sites.values():
                sites.sort()

    def correlate_axes(self, site, info):
        """Fill a bare ``interp_2d_*`` call's axes from the axis searches the
        same function performed before it.  The row length the interpolator is
        given identifies the X axis; whatever other axis the function searched
        is then the Y axis.  HYPOTHESIS by construction -- the association is
        positional, not dataflow."""
        func = self.fm.getFunctionContaining(site)
        if func is None:
            return False
        key = int(func.getEntryPoint().getOffset())
        here = int(site.getOffset())
        # The generated code searches both axes immediately before calling the
        # interpolator; anything further back belongs to a different table.
        candidates = [a for a in self.axis_sites.get(key, ())
                      if here - 0x200 <= a[0] < here]
        if not candidates:
            return False
        recent = candidates[-4:]
        nx = info.get("x_n")
        xmatch = [a for a in recent if nx is not None and a[1]["x_n"] == nx]
        if len(xmatch) != 1:
            return False
        xinfo = xmatch[0][1]
        info["x_axis"] = xinfo["x_axis"]
        info["x_elem"] = xinfo["x_elem"]
        others = [a for a in recent if a is not xmatch[0]]
        if len(others) == 1:
            yinfo = others[0][1]
            info["y_axis"] = yinfo["x_axis"]
            info["y_n"] = yinfo["x_n"]
            info["y_elem"] = yinfo["x_elem"]
        return True

    def handle_site(self, site, helper_addr, spec):
        regs = self.sim.run(site, extended=False)
        info, why = self.decode(spec, regs)
        widened = False
        if info is None:
            regs = self.sim.run(site, extended=True)
            info2, why2 = self.decode(spec, regs)
            if info2 is not None:
                info, why, widened = info2, why2, True
        correlated = False
        if info is not None and spec["form"] == "core2d":
            correlated = self.correlate_axes(site, info)
        if info is not None and spec["form"] in ("axis", "axis_h"):
            func = self.fm.getFunctionContaining(site)
            if func is not None:
                self.axis_sites.setdefault(
                    int(func.getEntryPoint().getOffset()), []).append(
                        (int(site.getOffset()), info))
        consumer = self.func_name(site)
        site_cpu = int(site.getOffset())
        if info is None:
            self.stats["unresolved" if "not constant" in why else "rejected"] += 1
            self.site_rows.append(dict(
                site=h(site_cpu), helper=spec["name"], helper_addr=h(helper_addr),
                kind="", addr="", x_axis_addr="", y_axis_addr="", x_n="", y_n="",
                elem_size="", signed="", consumer_func=consumer,
                confidence="unresolved", note=why))
            return

        self.stats["resolved"] += 1
        obj = self.obj(info["kind"], info["addr"])
        for key in ("struct_addr", "x_axis", "y_axis", "x_n", "y_n",
                    "x_elem", "y_elem", "elem", "interp"):
            if info.get(key) is not None:
                setattr(obj, key, info[key])
        for addr, length in info.get("extra") or ():
            if (addr, length) not in obj.extra:
                obj.extra.append((addr, length))
        obj.helpers.add(spec["name"])
        if consumer:
            obj.consumers.add(consumer)
        obj.sites.append(site_cpu)
        if widened:
            obj.notes.add("args recovered over an extended window")
            obj.confidence = "hypothesis"
        if correlated:
            obj.notes.add("axes matched positionally to the axis searches in "
                          "the same function")
            obj.confidence = "hypothesis"
        # axis sanity
        for addr, n, elem, tag in ((obj.x_axis, obj.x_n, obj.x_elem, "x"),
                                   (obj.y_axis, obj.y_n, obj.y_elem, "y")):
            if addr is None or not n or elem is None:
                continue
            if n > 1 and not self.cal.monotonic(addr, n, elem[0], elem[1]):
                obj.notes.add("%s axis is not strictly rising" % tag)
                obj.confidence = "hypothesis"
        self.site_rows.append(dict(
            site=h(site_cpu), helper=spec["name"], helper_addr=h(helper_addr),
            kind=obj.kind, addr=h(obj.addr),
            x_axis_addr=h(obj.x_axis) if obj.x_axis else "",
            y_axis_addr=h(obj.y_axis) if obj.y_axis else "",
            x_n=obj.x_n or "", y_n=obj.y_n or "",
            elem_size=obj.elem[1] if obj.elem else "",
            signed="1" if obj.elem and obj.elem[0] else "0",
            consumer_func=consumer,
            confidence=obj.confidence,
            note="widened window" if widened else ""))

    def infer_row_counts(self):
        """Give the bare ``interp_2d_*`` maps a row count.

        A call to one of the value-array interpolators only reveals the row
        length (the stride).  Where the row count could not be recovered from
        an axis search, take the distance to the next object that *is* anchored
        and round it down to a whole number of rows.  HYPOTHESIS: it assumes
        the maps are packed without padding, which is how the rest of the block
        is laid out.
        """
        anchors = set()
        for obj in self.objects.values():
            if obj.kind == "scalar":
                continue
            for lo, _hi in obj.spans():
                anchors.add(lo)
        for lo, _hi, _why in KNOWN_NON_MAP:
            anchors.add(lo)
        anchors.add(CAL_END)
        ordered = sorted(anchors)
        import bisect

        sized = sorted((o for o in self.objects.values()
                        if o.kind != "scalar" and o.x_n and o.y_n and o.elem),
                       key=lambda o: o.addr)
        sized_at = [o.addr for o in sized]

        done = 0
        for obj in sorted((o for o in self.objects.values()
                           if o.kind == "map_2d_data" and not o.y_n
                           and o.x_n and o.elem), key=lambda o: o.addr):
            row = obj.x_n * obj.elem[1]
            nxt = ordered[bisect.bisect_right(ordered, obj.addr)]
            rows = (nxt - obj.addr) // row
            note = ("row count inferred from the %d bytes before the next "
                    "detected object" % (nxt - obj.addr))
            # Maps of one family sit back to back with a constant pitch.  If the
            # object immediately before has the same row length and element type
            # and ends exactly here, it is a much better estimate than the
            # distance to the next anchor, which can run into padding.
            i = bisect.bisect_left(sized_at, obj.addr) - 1
            if i >= 0:
                prev = sized[i]
                if (prev.x_n == obj.x_n and prev.elem == obj.elem
                        and prev.addr + prev.size == obj.addr
                        and 1 <= prev.y_n <= rows):
                    rows = prev.y_n
                    note = ("row count taken from the identically shaped map at "
                            "%s, which ends exactly here" % h(prev.addr))
            if rows > MAX_ROWS:
                note += "; capped at %d rows (largest count seen on a map whose " \
                        "axes are known is 16)" % MAX_ROWS
                rows = MAX_ROWS
            if rows < 1 or rows * row > MAX_STRUCT:
                continue
            obj.y_n = rows
            obj.confidence = "hypothesis"
            obj.notes.add(note)
            done += 1
            i = bisect.bisect_left(sized_at, obj.addr)
            sized.insert(i, obj)
            sized_at.insert(i, obj.addr)
        return done

    # -- direct scalar loads ----------------------------------------------
    def walk_scalars(self):
        """Every lbz/lhz/lha whose effective address is a calibration constant.

        These are the Bosch *Festwerte* -- single calibratable values, not
        maps -- and they are what the remaining ~4,800 of the 7,055 references
        in docs/02 section 3 are.
        """
        loads = {"lbz": (False, 1), "lhz": (False, 2), "lha": (True, 2),
                 "lwz": (False, 4)}
        # One linear pass with a register file that is dropped at every branch
        # target and after every non-fall-through instruction, and whose
        # volatile half is dropped after every call.  Much cheaper than running
        # the block simulator per load, and equivalent in practice.
        targets = set()
        for ins in self.listing.getInstructions(True):
            for flow in ins.getFlows():
                targets.add(int(flow.getOffset()))

        seen_sites = 0
        regs = {}
        drop = True
        for ins in self.listing.getInstructions(True):
            addr = ins.getAddress()
            offset = int(addr.getOffset())
            if drop or offset in targets:
                regs = {2: self.sim._r2_at(addr)}
            drop = False
            mnem = str(ins.getMnemonicString())
            spec = loads.get(mnem)
            if spec is not None:
                dst = RegSim._regnum_of_op(ins, 0)
                disp, base = RegSim._mem_operand(ins, 1)
                value = None
                if disp is not None and base is not None and regs.get(base) is not None:
                    target = (regs[base] + disp) & 0xFFFFFFFF
                    if self.cal.inside(target, spec[1]):
                        seen_sites += 1
                        obj = self.obj("scalar", target)
                        obj.elem = spec
                        obj.x_n = 1
                        obj.sites.append(offset)
                        name = self.func_name(addr)
                        if name:
                            obj.consumers.add(name)
                    value = self.sim._load(target, -spec[1] if spec[0] else spec[1])
                if dst is not None:
                    regs[dst] = value
            else:
                self.sim.step(ins, regs)
            flow = ins.getFlowType()
            if flow.isCall():
                for reg in range(3, 13):
                    regs.pop(reg, None)
            elif not flow.isFallthrough():
                drop = True
        return seen_sites

    # -- output ------------------------------------------------------------
    def rows(self):
        out = []
        for (_kind, _addr), obj in sorted(self.objects.items(),
                                          key=lambda kv: (kv[0][1], kv[0][0])):
            sites = sorted(set(obj.sites))
            evidence = "enumerate_maps.py; %s; %d call site(s): %s" % (
                "/".join(sorted(obj.helpers)) or "direct load",
                len(sites), " ".join(h(s) for s in sites[:6]))
            if len(sites) > 6:
                evidence += " ..."
            if obj.struct_addr is not None and obj.struct_addr != obj.addr:
                evidence += "; struct %s" % h(obj.struct_addr)
            if not obj.interp:
                obj.notes.add("no interpolation between breakpoints")
            if obj.notes:
                evidence += "; " + "; ".join(sorted(obj.notes))
            consumers = sorted(obj.consumers)
            out.append({
                "addr": h(obj.addr),
                "kind": obj.kind,
                "x_axis_addr": h(obj.x_axis) if obj.x_axis else "",
                "y_axis_addr": h(obj.y_axis) if obj.y_axis else "",
                "x_n": obj.x_n or "",
                "y_n": obj.y_n or "",
                "elem_size": obj.elem[1] if obj.elem else "",
                "signed": ("1" if obj.elem[0] else "0") if obj.elem else "",
                "consumer_func": " ".join(consumers[:4]) + (" ..." if len(consumers) > 4 else ""),
                "name_or_blank": "",
                "confidence": obj.confidence,
                "evidence": evidence,
                "x_elem": elem_name(obj.x_elem),
                "y_elem": elem_name(obj.y_elem),
                "struct_addr": h(obj.struct_addr) if obj.struct_addr else "",
                "sites": len(sites),
            })
        return out

    def write_csv(self, path, columns, rows):
        import csv

        directory = os.path.dirname(path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

    # -- coverage ----------------------------------------------------------
    def coverage(self):
        spans = []
        for obj in self.objects.values():
            for lo, hi in obj.spans():
                if hi > lo:
                    spans.append((lo, hi))
        for lo, hi, _why in KNOWN_NON_MAP:
            spans.append((lo, hi))
        spans.sort()
        merged = []
        for lo, hi in spans:
            if merged and lo <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])
        gaps = []
        cursor = COVERAGE_START
        for lo, hi in merged:
            if hi <= COVERAGE_START or lo >= COVERAGE_END:
                continue
            lo = max(lo, COVERAGE_START)
            if lo > cursor:
                gaps.append((cursor, lo))
            cursor = max(cursor, min(hi, COVERAGE_END))
        if cursor < COVERAGE_END:
            gaps.append((cursor, COVERAGE_END))
        return merged, gaps

    def gap_kind(self, lo, hi):
        blob = self.cal.slice(lo, hi - lo)
        if not blob:
            return "empty"
        zeros = blob.count(0x00)
        ones = blob.count(0xFF)
        if ones == len(blob):
            return "erased (all 0xFF)"
        if zeros == len(blob):
            return "all zero"
        if zeros > len(blob) * 0.5:
            return "%d%% zero" % (100 * zeros // len(blob))
        if ones > len(blob) * 0.5:
            return "%d%% 0xFF" % (100 * ones // len(blob))
        printable = sum(1 for b in blob if 0x20 <= b < 0x7F)
        if printable > len(blob) * 0.8:
            return "text"
        return "data"

    def write_coverage(self, path):
        import datetime

        merged, gaps = self.coverage()
        covered = sum(min(hi, COVERAGE_END) - max(lo, COVERAGE_START)
                      for lo, hi in merged
                      if hi > COVERAGE_START and lo < COVERAGE_END)
        total = COVERAGE_END - COVERAGE_START
        lines = []
        lines.append("# Calibration coverage of 0x%06X-0x%06X\n" % (COVERAGE_START, COVERAGE_END - 1))
        lines.append("Agent B5, issue #19, %s. Generated by "
                     "`ghidra_scripts/enumerate_maps.py`; re-run it to refresh.\n"
                     % datetime.date.today().isoformat())
        lines.append("Covered by a detected structure or a documented non-map block: "
                     "**%d of %d bytes (%.1f%%)**.  %d of %d helper call sites "
                     "resolved (%d more are internal to the library).\n"
                     % (covered, total, 100.0 * covered / total,
                        self.stats["resolved"], self.stats["sites"],
                        self.stats["internal"]))

        lines.append("\n## Where the calibration is dense\n")
        lines.append("| CPU 8 KB block | file | covered | |")
        lines.append("|---|---|---|---|")
        step = 0x2000
        for base in range(COVERAGE_START, COVERAGE_END, step):
            end = min(base + step, COVERAGE_END)
            got = sum(min(hi, end) - max(lo, base) for lo, hi in merged
                      if hi > base and lo < end)
            pct = 100.0 * got / (end - base)
            lines.append("| %s-%s | 0x%06X | %5.1f%% | %s |"
                         % (h(base), h(end - 1), base - CAL_FILE_DELTA, pct,
                            "#" * int(pct / 5)))

        lines.append("\n## Blocks that are known not to be maps\n")
        lines.append("| CPU range | what |")
        lines.append("|---|---|")
        for lo, hi, why in KNOWN_NON_MAP:
            lines.append("| %s-%s | %s |" % (h(lo), h(hi - 1), why))

        lines.append("\n## Free space for FFCAL001 (docs/06_patch_pipeline.md section 3)\n")
        free_end = FFCAL_START
        while free_end < CAL_END and self.cal.read(free_end, False, 1) == 0xFF:
            free_end += 1
        lines.append("`enumerate_maps.py` checked the bytes directly: "
                     "**%s-%s is erased (all 0xFF), %d bytes** "
                     "(file 0x%06X-0x%06X).  No detected table, axis or scalar "
                     "load touches any address at or above %s, so the whole of "
                     "it is free for our own calibration block.\n"
                     % (h(FFCAL_START), h(free_end - 1), free_end - FFCAL_START,
                        FFCAL_START - CAL_FILE_DELTA, free_end - 1 - CAL_FILE_DELTA,
                        h(FFCAL_START)))
        highest = max([o.addr + o.size for o in self.objects.values()] or [0])
        lines.append("Highest address any detected object reaches: **%s**.\n" % h(highest))

        lines.append("\n## Uncovered ranges\n")
        lines.append("%d ranges, %d bytes in total.  \"data\" means the bytes are "
                     "neither all 0xFF nor all zero: most of these are tables whose "
                     "call site could not be resolved, the unread rows of a map the "
                     "row-count inference declined, or Bosch structures that are not "
                     "reached through the interpolation library at all.\n"
                     % (len(gaps), sum(hi - lo for lo, hi in gaps)))
        lines.append("| CPU start | CPU end | file start | bytes | content |")
        lines.append("|---|---|---|---|---|")
        for lo, hi in gaps:
            lines.append("| %s | %s | 0x%06X | %d | %s |"
                         % (h(lo), h(hi - 1), lo - CAL_FILE_DELTA, hi - lo,
                            self.gap_kind(lo, hi)))
        lines.append("")
        return "\n".join(lines), gaps

    # -- Ghidra annotation -------------------------------------------------
    def name_helpers(self):
        from ghidra.program.model.symbol import SourceType

        renamed = 0
        for addr, spec in sorted(HELPERS.items()):
            func = self.fm.getFunctionAt(self.space.getAddress(addr))
            if func is None:
                continue
            func.setName(spec["name"], SourceType.USER_DEFINED)
            self.listing.setComment(self.space.getAddress(addr), 3, helper_plate(addr, spec))
            renamed += 1
        return renamed

    def label_objects(self):
        from ghidra.program.model.symbol import SourceType

        table = self.program.getSymbolTable()
        made = 0
        for obj in self.objects.values():
            if obj.kind == "scalar":
                continue
            name = "cand_%s_%06X" % (obj.kind, obj.addr)
            try:
                table.createLabel(self.space.getAddress(obj.addr), name,
                                  SourceType.USER_DEFINED)
                made += 1
            except Exception:                                 # noqa: BLE001
                pass
        return made

    # -- driver ------------------------------------------------------------
    def run(self):
        print("[enumerate_maps] %d interpolation helpers" % len(HELPERS))
        self.walk_helpers()
        print("[enumerate_maps] %d helper call sites: %d resolved, %d internal "
              "to the library, %d with non-constant arguments, %d rejected by "
              "validation"
              % (self.stats["sites"], self.stats["resolved"],
                 self.stats["internal"], self.stats["unresolved"],
                 self.stats["rejected"]))
        print("[enumerate_maps] %d bare interpolator maps given an inferred "
              "row count" % self.infer_row_counts())
        if not self.opts.get("no_scalars"):
            n = self.walk_scalars()
            print("[enumerate_maps] %d direct calibration loads" % n)

        rows = self.rows()
        by_kind = {}
        for row in rows:
            by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + 1
        print("[enumerate_maps] %d distinct objects: %s"
              % (len(rows), ", ".join("%s=%d" % kv for kv in sorted(by_kind.items()))))

        draft = os.path.join(self.repo_root, "re", "calibration_draft.csv")
        self.write_csv(draft, CSV_COLUMNS, rows)
        print("[enumerate_maps] -> %s" % draft)

        sites = os.path.join(self.repo_root, "re", "findings",
                             "calibration_call_sites.csv")
        self.write_csv(sites, SITE_COLUMNS, self.site_rows)
        print("[enumerate_maps] -> %s (%d rows)" % (sites, len(self.site_rows)))

        text, gaps = self.write_coverage(None)
        path = os.path.join(self.repo_root, "re", "findings",
                            "calibration_coverage.md")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        print("[enumerate_maps] -> %s (%d uncovered ranges)" % (path, len(gaps)))

        if self.opts.get("name_helpers", True):
            print("[enumerate_maps] named %d helpers in the program"
                  % self.name_helpers())
        if self.opts.get("label"):
            print("[enumerate_maps] %d cand_ labels" % self.label_objects())
        return rows


def helper_plate(addr, spec):
    form = spec["form"]
    what = {
        "kl": "1-D curve lookup, self-describing: { n; axis[n]; val[n] }",
        "kl_g": "1-D curve lookup, shared axis: (n, axis, val, x)",
        "kf": "2-D map lookup, self-describing: { ny; nx; yaxis[ny]; xaxis[nx]; val[ny*nx] }",
        "kf_g": "2-D map lookup, shared axes: (ny, yaxis, nx, xaxis, val, vy, vx)",
        "axis": "axis search, self-describing { n; axis[n] }: returns (index<<16)|frac",
        "axis_h": "axis search with previous-index hint: returns (index<<16)|frac",
        "core2d": "value-array interpolator: (val, nx, key_y, key_x), val[iy*nx+ix]",
    }[form]
    detail = []
    if "ay" in spec:
        detail.append("row axis %s" % elem_name(spec["ay"]))
    if "ax" in spec:
        detail.append("%saxis %s" % ("column " if "ay" in spec else "",
                                     elem_name(spec["ax"])))
    if "val" in spec:
        detail.append("values %s" % elem_name(spec["val"]))
    if spec.get("interp") is False:
        detail.append("no interpolation, returns the breakpoint value")
    # First line only is what export_symbols.py puts in the notes column, so
    # make it the useful one; the tag can be anywhere in the comment.
    parts = ["%s -- %s%s" % (spec["name"], what,
                             (" (%s)" % ", ".join(detail)) if detail else "")]
    parts.append("VERIFIED-STATIC. Bosch MED9 interpolation library, on-chip "
                 "flash 0x40C000-0x411FFF.")
    parts.append("Evidence: Ghidra decompilation of %s; "
                 "re/findings/calibration_maps.md; agent B5, issue #19." % h(addr))
    return "\n".join(parts)


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------
def parse_opts(argv):
    return {
        "label": "--label" in argv,
        "no_scalars": "--no-scalars" in argv,
        "name_helpers": "--no-name-helpers" not in argv,
    }


def repo_root_from(argv):
    for arg in argv:
        if not arg.startswith("-"):
            return os.path.abspath(arg)
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(here)


def _run_as_ghidra_script():
    try:
        argv = list(getScriptArgs())                          # noqa: F821
    except Exception:                                          # noqa: BLE001
        argv = []
    program = currentProgram                                   # noqa: F821
    enumerator = Enumerator(program, repo_root_from(argv), parse_opts(argv))
    tx = program.startTransaction("enumerate_maps")
    ok = False
    try:
        enumerator.run()
        ok = True
    finally:
        program.endTransaction(tx, ok)


def _run_standalone(argv):
    def flag(name, default):
        for i, arg in enumerate(argv):
            if arg == name and i + 1 < len(argv):
                return argv[i + 1]
            if arg.startswith(name + "="):
                return arg.split("=", 1)[1]
        return default

    project_dir = os.path.abspath(flag("--project-dir", "ghidra_projects"))
    project_name = flag("--project-name", "med9")
    repo = os.path.abspath(flag("--repo", repo_root_from(
        [a for a in argv if not a.startswith("--")])))

    import pyghidra
    pyghidra.start(verbose=False)
    from ghidra.base.project import GhidraProject

    project = GhidraProject.openProject(project_dir, project_name, True)
    program = project.openProgram("/", "passat_azx_ori.bin", False)
    enumerator = Enumerator(program, repo, parse_opts(argv))
    tx = program.startTransaction("enumerate_maps")
    ok = False
    try:
        enumerator.run()
        ok = True
    finally:
        program.endTransaction(tx, ok)
    project.save(program)
    project.close()
    return 0


def _running_inside_ghidra():
    try:
        currentProgram                                         # noqa: F821,B018
    except NameError:
        return False
    return True


if _running_inside_ghidra():
    _run_as_ghidra_script()
elif __name__ == "__main__":
    sys.exit(_run_standalone(sys.argv[1:]))
