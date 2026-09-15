#!/usr/bin/env python3
# Build the verified MED9.1.1 memory map in a Ghidra project.
# @category MED9
# @runtime PyGhidra
"""med9_setup.py -- create the verified Bosch MED9.1.1 memory map in Ghidra.

Implements the seven steps of ``docs/03_tooling.md`` section 2.1 against the
KESSv2 dump ``data/passat_azx_ori.bin`` (2,605,056 bytes, SHA-256
b15590d3...09b3).  Every address here comes from ``docs/02_memory_map.md``;
do not invent new ones, correct the document instead.

Two ways to run it (both give the same project):

1. As a Ghidra post-script under the PyGhidra-enabled headless analyzer::

     export GHIDRA_INSTALL_DIR=/usr/local/Cellar/ghidra/12.1.3/libexec
     mkdir -p ghidra_projects
     ./.venv/bin/python -m pyghidra.ghidra_launch \
         --install-dir "$GHIDRA_INSTALL_DIR" \
         ghidra.app.util.headless.AnalyzeHeadless ghidra_projects med9 \
         -import data/passat_azx_ori.bin \
         -loader BinaryLoader -loader-baseAddr 0x0 \
         -processor PowerPC:BE:32:default -cspec default \
         -noanalysis -scriptPath ghidra_scripts -postScript med9_setup.py

   (plain ``support/analyzeHeadless`` cannot run .py scripts: Ghidra 12
   answers "Ghidra was not started with PyGhidra".)

2. As a standalone PyGhidra driver, which creates/opens the project itself::

     ./.venv/bin/python ghidra_scripts/med9_setup.py data/passat_azx_ori.bin \
         --project-dir ghidra_projects --project-name med9

Options (script args in mode 1, command line flags in mode 2):
    --no-analysis        set the map up but do not run auto-analysis
    --no-prologue-scan   do not seed functions from stwu/mflr prologues
    --code-alias         also create the 0x480000 byte-mapped code alias
    --boot-r2            set r2 = 0x17FF0 over the boot module (see issue #8)
"""


import sys

# --------------------------------------------------------------------------
# Verified layout (docs/02_memory_map.md sections 2-5, 7).  CPU addresses.
# --------------------------------------------------------------------------

DUMP_SIZE = 0x27C000

# name, cpu_start, length, file offset
INITIALIZED = [
    ("EXT_FLASH", 0x000000, 0x200000, 0x000000),
    ("INT_FLASH", 0x404000, 0x07C000, 0x200000),
]

# name, cpu_start, mapped_source, length
BYTE_MAPPED = [
    ("CAL_ALIAS", 0x5C0000, 0x1C0000, 0x40000),
]
# Optional: the rest of the high alias.  Only 3 static references land here,
# and it shadows code, so it is off by default (--code-alias to enable).
CODE_ALIAS = ("CODE_ALIAS", 0x480000, 0x080000, 0x140000)

# name, cpu_start, length, volatile
UNINITIALIZED = [
    ("DECRAM",     0x6F8000, 0x000800, False),  # 2 KB on-chip RAM, boot copies code here
    ("USIU",       0x6FC000, 0x000400, True),   # SIUMCR, BR0-3/OR0-3, PLL, timers
    ("UC3F_CTL",   0x6FC800, 0x000020, True),   # on-chip flash control
    ("TPU3_A",     0x704000, 0x000400, True),
    ("TPU3_B",     0x704400, 0x000400, True),
    ("QADC_A",     0x704800, 0x000400, True),
    ("QADC_B",     0x704C00, 0x000400, True),
    ("QSMCM",      0x705000, 0x000800, True),
    ("MIOS14",     0x706000, 0x001000, True),
    ("TOUCAN_A",   0x707080, 0x000400, True),
    ("TOUCAN_B",   0x707480, 0x000400, True),
    ("TOUCAN_C",   0x707880, 0x000400, True),
    ("UIMB",       0x707F80, 0x000080, True),
    ("CALRAM_CTL", 0x780000, 0x000040, True),
    ("SRAM_INT",   0x7F8000, 0x008000, False),  # stack top 0x7FEFFC, r13 base 0x7FFFF0
    ("SRAM_EXT",   0x800000, 0x008000, False),  # CS1
    ("CS2_DEV",    0x900000, 0x040000, True),   # unknown device, BR2
    ("CS3_DEV",    0xA00000, 0x008000, True),   # unknown device, BR3
]

# Executable ranges (inclusive end) used for register context and scanning.
CODE_RANGES = [(0x000000, 0x1FFFFF), (0x404000, 0x47FFFF)]
# Where prologue scanning is allowed: docs section 5 puts the end of external
# flash code at 0x144950 and the constant block below 0x20000.
PROLOGUE_RANGES = [(0x020000, 0x14494F), (0x404000, 0x47FFFF)]

R13_VALUE = 0x7FFFF0        # r13 SDA base, boot_start and app_sda_setup_a/b/int
R2_APP = 0x5C9FF0           # application read-only SDA2 base
R2_BOOT = 0x017FF0          # boot module SDA2 base (issue #8 decides the range)
BOOT_R2_RANGE = (0x001000, 0x01FFFF)

# Entry points to seed disassembly with, name may be None for FUN_ default.
SEED_FUNCTIONS = [
    (0x000100, "reset_vector"),
    (0x00049C, "boot_trampoline"),
    (0x001004, "boot_start"),
    (0x0110F0, "fatal_exception_handler"),
    (0x011118, "decram_flashcs_routine_src"),
    (0x011E44, "boot_or_adjust"),
    (0x012004, "boot_memctrl_init"),
    (0x012328, "boot_main_init"),
    (0x020004, "romcheck_result_set"),
    (0x0986AC, "app_sda_setup_a"),
    (0x09E3E0, "app_sda_setup_b"),
    (0x404000, "int_flash_entry"),
    (0x405588, "app_sda_setup_int"),
    (0x4386D8, "kwp_handler_4386D8"),
    (0x4387CC, "kwp_handler_4387CC"),
    (0x427B50, "kwp_handler_427B50"),
]

# Labels that are not functions.
SEED_LABELS = [
    (0x000000, "tbl_etr_branch_table"),
    (0x017FF0, "r2_boot_sda2_base"),
    (0x080100, "code_directory"),
    (0x144950, "end_of_code"),
    (0x5C2000, "cal_directory"),
    (0x5C9FF0, "r2_app_sda2_base"),
    (0x6F8000, "decram_code_copy"),
    (0x7FEFFC, "initial_stack_top"),
    (0x7FFFF0, "r13_sda_base"),
    (0x7F824A, "romcheck_result_flags"),
    (0x7FE9E8, "boot_mode_flag"),
]

# Tables from docs/02_memory_map.md section 7 and section 6.
# (address, label, kind, count) -- kind selects the structure below.
TABLES = [
    (0x001FF0, "tbl_checksum_boot",          "checksum", 1),
    (0x01FFC0, "tbl_checksum_const",         "checksum", 4),
    (0x0A0000, "tbl_checksum_code",          "checksum", 54),
    (0x5C3300, "tbl_checksum_cal",           "checksum", 6),
    (0x02B870, "tbl_kwp_services",           "kwp",      24),
    (0x02BC38, "tbl_toucan_bases",           "u32",      3),
    (0x02BC50, "ptr_can_config",             "u32",      2),
    (0x02BC90, "tbl_can_rx",                 "can_rx",   22),
    (0x02BDF0, "tbl_can_tx",                 "can_tx",   17),
]

STRINGS = [
    (0x5C21F0, "str_bosch_project", 0x40),
    (0x5CEE20, "ecu_ident_block", 0x50),
]

KWP_TABLE = 0x02B870
KWP_ENTRIES = 24
KWP_ENTRY_SIZE = 20

# Acceptance checks reported at the end (brief A1 / issue #7).
ACCEPT_DECOMPILE = 0x020004
ACCEPT_FUNCTION = 0x4386D8

# Plate comment appended to everything this script names.  export_symbols.py
# reads the tag back out of the comment, so the knowledge base records where
# the confidence came from instead of guessing (docs/04_re_guidelines.md s1).
EVIDENCE = ("VERIFIED-STATIC -- ghidra_scripts/med9_setup.py, "
            "from docs/02_memory_map.md")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

class Setup(object):
    """Applies the map to one Program."""

    def __init__(self, program, monitor, opts):
        from ghidra.program.flatapi import FlatProgramAPI

        self.program = program
        self.monitor = monitor
        self.opts = opts
        self.flat = FlatProgramAPI(program, monitor)
        self.mem = program.getMemory()
        self.space = program.getAddressFactory().getDefaultAddressSpace()
        self.log = []

    # -- small utilities ---------------------------------------------------
    def addr(self, value):
        return self.space.getAddress(value)

    def say(self, text):
        print("[med9_setup] " + text)
        self.log.append(text)

    def u32(self, value):
        data = self.flat.getBytes(self.addr(value), 4)
        out = 0
        for b in data:
            out = (out << 8) | (b & 0xFF)
        return out

    def read_block(self, start, length):
        data = self.flat.getBytes(self.addr(start), length)
        try:                                  # jpype byte[] supports the buffer protocol
            return bytes(memoryview(data))
        except TypeError:
            return bytes(b & 0xFF for b in data)

    # -- step 1 + 2 + 3 + 4: memory blocks ---------------------------------
    def build_memory(self):
        file_bytes = list(self.mem.getAllFileBytes())
        if not file_bytes:
            raise RuntimeError(
                "program has no FileBytes; import with -loader BinaryLoader")
        fb = file_bytes[0]
        if fb.getSize() != DUMP_SIZE:
            raise RuntimeError(
                "unexpected image size %#x (expected %#x); this script is only "
                "valid for the 2,605,056 byte KESSv2 read" % (fb.getSize(), DUMP_SIZE))

        # The BinaryLoader creates a single block for the whole file.  Drop
        # every existing block and rebuild from the same FileBytes so the tail
        # lands at 0x404000 and not at 0x200000 (the known trap).
        for block in list(self.mem.getBlocks()):
            self.mem.removeBlock(block, self.monitor)

        for name, start, length, offset in INITIALIZED:
            block = self.mem.createInitializedBlock(
                name, self.addr(start), fb, offset, length, False)
            block.setRead(True)
            block.setWrite(False)
            block.setExecute(True)
            block.setComment("flash, file offset %#x..%#x" % (offset, offset + length - 1))
            self.say("block %-10s %08X..%08X  <- file %06X (%d KiB)"
                     % (name, start, start + length - 1, offset, length // 1024))

        mapped = list(BYTE_MAPPED)
        if self.opts.get("code_alias"):
            mapped.append(CODE_ALIAS)
        for name, start, source, length in mapped:
            block = self.mem.createByteMappedBlock(
                name, self.addr(start), self.addr(source), length, False)
            block.setRead(True)
            block.setWrite(False)
            block.setExecute(False)
            block.setComment("byte-mapped alias of %08X" % source)
            self.say("block %-10s %08X..%08X  -> alias of %08X"
                     % (name, start, start + length - 1, source))

        for name, start, length, volatile in UNINITIALIZED:
            block = self.mem.createUninitializedBlock(
                name, self.addr(start), length, False)
            block.setRead(True)
            block.setWrite(True)
            block.setExecute(False)
            block.setVolatile(volatile)
            self.say("block %-10s %08X..%08X  (uninitialised%s)"
                     % (name, start, start + length - 1,
                        ", volatile" if volatile else ""))

    # -- step 5: register context -----------------------------------------
    def set_context(self):
        from java.math import BigInteger

        ctx = self.program.getProgramContext()
        r13 = ctx.getRegister("r13")
        r2 = ctx.getRegister("r2")
        if r13 is None or r2 is None:
            raise RuntimeError("PowerPC:BE:32:default did not provide r2/r13")

        for start, end in CODE_RANGES:
            ctx.setValue(r13, self.addr(start), self.addr(end),
                         BigInteger.valueOf(R13_VALUE))
            ctx.setValue(r2, self.addr(start), self.addr(end),
                         BigInteger.valueOf(R2_APP))
        self.say("r13 = %#08X and r2 = %#08X over %s"
                 % (R13_VALUE, R2_APP,
                    ", ".join("%06X-%06X" % r for r in CODE_RANGES)))

        if self.opts.get("boot_r2"):
            start, end = BOOT_R2_RANGE
            ctx.setValue(r2, self.addr(start), self.addr(end),
                         BigInteger.valueOf(R2_BOOT))
            self.say("r2 = %#08X over the boot module %06X-%06X (opt-in, issue #8)"
                     % (R2_BOOT, start, end))

    # -- step 7 (data) before analysis so tables are not disassembled ------
    def _data_types(self):
        from ghidra.program.model.data import (
            ArrayDataType, ByteDataType, CategoryPath, DataTypeConflictHandler,
            PointerDataType, StructureDataType, UnsignedIntegerDataType,
            VoidDataType)

        dtm = self.program.getDataTypeManager()
        path = CategoryPath("/MED9")
        u32 = UnsignedIntegerDataType()
        u8 = ByteDataType()
        fptr = PointerDataType(VoidDataType(), 4)

        def struct(name, fields):
            s = StructureDataType(path, name, 0)
            for ftype, fname, comment in fields:
                s.add(ftype, fname, comment)
            return dtm.addDataType(s, DataTypeConflictHandler.REPLACE_HANDLER)

        types = {}
        types["checksum"] = struct("med9_checksum_desc", [
            (u32, "start", "first covered address"),
            (u32, "end", "last covered address, inclusive"),
            (u32, "sum", "32-bit sum of big-endian 16-bit words"),
            (u32, "not_sum", "bitwise complement of sum"),
        ])
        types["kwp"] = struct("med9_kwp_service", [
            (u8, "sid", "KWP2000 / OBD service id"),
            (ArrayDataType(u8, 3, 1), "pad", "always FF FF FF"),
            (u32, "flags", "session / access flags"),
            (fptr, "handler", "primary service handler"),
            (fptr, "handler2", "secondary handler, 0 if none"),
            (u32, "reserved", "always 0"),
        ])
        types["can_rx"] = struct("med9_can_rx_entry", [
            (u32, "index", "slot index"),
            (u32, "cfg", "0x01mmnn08: module, buffer, dlc"),
            (u32, "dlc", "payload length"),
            (u32, "can_id", "11-bit identifier, 0x7FF = unused slot"),
        ])
        types["can_tx"] = struct("med9_can_tx_entry", [
            (u32, "index", "slot index"),
            (u32, "can_id", "11-bit identifier"),
            (u32, "cfg", "0x0101xxxx configuration word"),
            (u8, "dlc", "payload length"),
            (ArrayDataType(u8, 3, 1), "pad", ""),
        ])
        types["u32"] = u32
        return types, ArrayDataType

    def apply_tables(self):
        from ghidra.program.model.data import TerminatedStringDataType

        types, ArrayDataType = self._data_types()
        for address, name, kind, count in TABLES:
            base = types[kind]
            element = base.getLength()
            total = element * count
            try:
                self.flat.clearListing(self.addr(address),
                                       self.addr(address + total - 1))
                array = ArrayDataType(base, count, element)
                self.flat.createData(self.addr(address), array)
                self.flat.createLabel(self.addr(address), name, True)
                self.flat.setPlateComment(self.addr(address), EVIDENCE)
                self.say("data  %-24s %08X  %d x %d B" % (name, address, count, element))
            except Exception as exc:          # noqa: BLE001 - report and continue
                self.say("data  %-24s %08X  FAILED: %s" % (name, address, exc))

        for address, name, length in STRINGS:
            try:
                self.flat.clearListing(self.addr(address),
                                       self.addr(address + length - 1))
                self.flat.createData(self.addr(address), TerminatedStringDataType())
                self.flat.createLabel(self.addr(address), name, True)
                self.flat.setPlateComment(self.addr(address), EVIDENCE)
                self.say("data  %-24s %08X  string" % (name, address))
            except Exception as exc:          # noqa: BLE001
                self.say("data  %-24s %08X  FAILED: %s" % (name, address, exc))

    # -- step 6: disassembly seeds ----------------------------------------
    def seed_vectors(self):
        """Disassemble the `ba` entries of the exception vector table."""
        seeded = 0
        for offset in range(0x0, 0x2000, 0x100):
            word = self.u32(offset)
            if (word & 0xFC000000) != 0x48000000:   # primary opcode 18: b/ba/bl/bla
                continue
            if offset == 0:
                continue                        # tbl_etr_branch_table already labels 0x0
            if self.flat.disassemble(self.addr(offset)):
                seeded += 1
                target = word & 0x03FFFFFC
                if word & 0x02000000:
                    target -= 0x04000000
                if not (word & 0x2):                # relative branch
                    target += offset
                self.flat.createLabel(self.addr(offset),
                                      "vector_%03X" % offset, False)
                self.flat.setPlateComment(
                    self.addr(offset),
                    "MPC5xx exception vector %#05x -> %08X\n%s"
                    % (offset, target & 0xFFFFFF, EVIDENCE))
        self.say("vector table: %d of 32 entries are branches and were disassembled"
                 % seeded)
        return seeded

    def seed_functions(self):
        made = 0
        for address, name in SEED_FUNCTIONS:
            a = self.addr(address)
            self.flat.disassemble(a)
            func = self.flat.getFunctionAt(a)
            if func is None:
                func = self.flat.createFunction(a, name)
            elif name:
                func.setName(name, _source_user())
            if func is not None:
                made += 1
                self.flat.setPlateComment(a, EVIDENCE)
        self.say("seed functions: %d of %d created or named"
                 % (made, len(SEED_FUNCTIONS)))
        return made

    def seed_kwp_handlers(self):
        """Create a function at every handler in the KWP dispatch table."""
        made = 0
        seen = {}
        for i in range(KWP_ENTRIES):
            entry = KWP_TABLE + i * KWP_ENTRY_SIZE
            sid = self.read_block(entry, 1)[0]
            for slot, offset in (("h1", 8), ("h2", 12)):
                target = self.u32(entry + offset)
                if target == 0:
                    continue
                if not self._in_code(target):
                    self.say("kwp: sid %02X %s -> %08X is outside flash, skipped"
                             % (sid, slot, target))
                    continue
                a = self.addr(target)
                self.flat.disassemble(a)
                func = self.flat.getFunctionAt(a)
                name = "kwp_sid_%02X_%s" % (sid, slot)
                while name in seen:
                    name += "_alt"
                seen[name] = target
                if func is None:
                    func = self.flat.createFunction(a, name)
                    if func is not None:
                        made += 1
                else:
                    func.setName(name, _source_user())
                    made += 1
                if func is not None:
                    self.flat.setPlateComment(
                        a, "KWP2000/OBD service %#04x handler (%s), tbl_kwp_services[%d]"
                           "\n%s" % (sid, slot, i, EVIDENCE))
        self.say("kwp dispatch table: %d handler functions created/named" % made)
        return made

    def _in_code(self, address):
        return any(start <= address <= end for start, end in CODE_RANGES)

    def seed_prologues(self):
        """Seed functions on the EABI prologue pattern stwu r1,-N(r1) / mflr r0.

        Both orders occur in this firmware.  The pattern is specific enough
        that it produces no hits inside the calibration or constant blocks,
        which is why the scan is restricted to the code ranges anyway.
        """
        import struct as _struct

        total = 0
        for start, end in PROLOGUE_RANGES:
            length = end - start + 1
            data = self.read_block(start, length)
            words = _struct.unpack(">%dI" % (length // 4), data[:length - length % 4])
            hits = []
            for i in range(len(words) - 1):
                w0, w1 = words[i], words[i + 1]
                stwu0 = (w0 & 0xFFFF0000) == 0x94210000
                stwu1 = (w1 & 0xFFFF0000) == 0x94210000
                if (stwu0 and w1 == 0x7C0802A6) or (w0 == 0x7C0802A6 and stwu1):
                    hits.append(start + i * 4)
            self.say("prologue scan %06X-%06X: %d candidates" % (start, end, len(hits)))
            for address in hits:
                a = self.addr(address)
                if self.flat.getFunctionAt(a) is not None:
                    continue
                self.flat.disassemble(a)
                if self.flat.createFunction(a, None) is not None:
                    total += 1
        self.say("prologue scan: %d functions created" % total)
        return total

    def seed_labels(self):
        for address, name in SEED_LABELS:
            try:
                self.flat.createLabel(self.addr(address), name, True)
                self.flat.setPlateComment(self.addr(address), EVIDENCE)
            except Exception as exc:          # noqa: BLE001
                self.say("label %s at %08X FAILED: %s" % (name, address, exc))

    # -- step 6 (analysis) -------------------------------------------------
    def analyze(self):
        from ghidra.app.script import GhidraScriptUtil
        from ghidra.program.util import GhidraProgramUtilities

        self.say("running auto-analysis (this takes a few minutes) ...")
        try:
            GhidraScriptUtil.acquireBundleHostReference()
        except Exception:                     # noqa: BLE001 - already held in headless
            pass
        try:
            self.flat.analyzeAll(self.program)
            GhidraProgramUtilities.markProgramAnalyzed(self.program)
        finally:
            try:
                GhidraScriptUtil.releaseBundleHostReference()
            except Exception:                 # noqa: BLE001
                pass
        self.say("auto-analysis finished")

    # -- acceptance report -------------------------------------------------
    def report(self):
        from ghidra.program.model.address import AddressSet

        fm = self.program.getFunctionManager()
        rm = self.program.getReferenceManager()
        lines = []
        lines.append("")
        lines.append("=" * 72)
        lines.append("MED9 setup acceptance report")
        lines.append("=" * 72)

        total = fm.getFunctionCount()
        for name, start, length, _offset in INITIALIZED:
            end = start + length - 1
            aset = AddressSet(self.addr(start), self.addr(end))
            count = 0
            it = fm.getFunctions(aset, True)
            while it.hasNext():
                it.next()
                count += 1
            lines.append("functions in %-10s %08X-%08X : %d" % (name, start, end, count))
        lines.append("functions total                           : %d" % total)

        for name, start, source, length in BYTE_MAPPED:
            end = start + length - 1
            aset = AddressSet(self.addr(start), self.addr(end))
            refs = 0
            targets = 0
            it = rm.getReferenceDestinationIterator(aset, True)
            while it.hasNext():
                a = it.next()
                targets += 1
                refs += rm.getReferenceCountTo(a)
            lines.append("references into %-10s %08X-%08X: %d refs to %d distinct addresses"
                         % (name, start, end, refs, targets))

        func = fm.getFunctionAt(self.addr(ACCEPT_FUNCTION))
        lines.append("function at %08X                      : %s"
                     % (ACCEPT_FUNCTION,
                        "%s, %d bytes" % (func.getName(), func.getBody().getNumAddresses())
                        if func else "MISSING"))

        lines.append("")
        lines.append("decompilation of %08X (romcheck_result_set):" % ACCEPT_DECOMPILE)
        lines.append(self.decompile(ACCEPT_DECOMPILE))
        lines.append("=" * 72)
        text = "\n".join(lines)
        print(text)
        return text

    def decompile(self, address):
        from ghidra.app.decompiler import DecompInterface
        from ghidra.util.task import TaskMonitor

        func = self.program.getFunctionManager().getFunctionContaining(self.addr(address))
        if func is None:
            return "  (no function at %08X)" % address
        iface = DecompInterface()
        try:
            iface.openProgram(self.program)
            result = iface.decompileFunction(func, 120, TaskMonitor.DUMMY)
            if not result.decompileCompleted():
                return "  (decompilation failed: %s)" % result.getErrorMessage()
            return result.getDecompiledFunction().getC()
        finally:
            iface.dispose()

    # -- driver ------------------------------------------------------------
    def run(self):
        self.build_memory()
        self.set_context()
        self.apply_tables()
        self.seed_vectors()
        self.seed_labels()
        self.seed_functions()
        self.seed_kwp_handlers()
        if not self.opts.get("no_prologue_scan"):
            self.seed_prologues()
        if not self.opts.get("no_analysis"):
            self.analyze()
        return self.report()


def _source_user():
    from ghidra.program.model.symbol import SourceType
    return SourceType.USER_DEFINED


def parse_options(args):
    opts = {
        "no_analysis": "--no-analysis" in args,
        "no_prologue_scan": "--no-prologue-scan" in args,
        "code_alias": "--code-alias" in args,
        "boot_r2": "--boot-r2" in args,
    }
    return opts


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------

def _run_as_ghidra_script():
    args = []
    try:
        args = list(getScriptArgs())          # noqa: F821 - injected by Ghidra
    except Exception:                         # noqa: BLE001
        pass
    program = currentProgram                  # noqa: F821 - injected by Ghidra
    try:
        mon = monitor                         # noqa: F821
    except NameError:
        from ghidra.util.task import TaskMonitor
        mon = TaskMonitor.DUMMY
    setup = Setup(program, mon, parse_options(args))
    tx = program.startTransaction("med9_setup")
    ok = False
    try:
        setup.run()
        ok = True
    finally:
        program.endTransaction(tx, ok)


def _run_standalone(argv):
    import pathlib

    positional = [a for a in argv if not a.startswith("--")]
    if not positional:
        print(__doc__)
        return 2
    binary = pathlib.Path(positional[0]).resolve()

    def flag(name, default):
        for i, a in enumerate(argv):
            if a == name and i + 1 < len(argv):
                return argv[i + 1]
            if a.startswith(name + "="):
                return a.split("=", 1)[1]
        return default

    project_dir = pathlib.Path(flag("--project-dir", "ghidra_projects")).resolve()
    project_name = flag("--project-name", "med9")
    project_dir.mkdir(parents=True, exist_ok=True)

    import pyghidra
    pyghidra.start(verbose=False)

    from ghidra.base.project import GhidraProject
    from ghidra.framework.model import ProjectLocator
    from ghidra.util.task import TaskMonitor
    from java.lang import ClassLoader
    import jpype

    loader = jpype.JClass("ghidra.app.util.opinion.BinaryLoader",
                          ClassLoader.getSystemClassLoader())
    from ghidra.program.util import DefaultLanguageService
    from ghidra.program.model.lang import LanguageID
    language = DefaultLanguageService.getLanguageService().getLanguage(
        LanguageID("PowerPC:BE:32:default"))
    compiler = language.getDefaultCompilerSpec()

    if ProjectLocator(str(project_dir), project_name).exists():
        project = GhidraProject.openProject(str(project_dir), project_name, True)
    else:
        project = GhidraProject.createProject(str(project_dir), project_name, False)

    program = None
    existing = project.getRootFolder().getFile(binary.name)
    if existing is not None:
        program = project.openProgram("/", binary.name, False)
    if program is None:
        from java.io import File
        program = project.importProgram(File(str(binary)), loader, language, compiler)
        project.saveAs(program, "/", binary.name, True)

    setup = Setup(program, TaskMonitor.DUMMY, parse_options(argv))
    tx = program.startTransaction("med9_setup")
    ok = False
    try:
        setup.run()
        ok = True
    finally:
        program.endTransaction(tx, ok)
    project.save(program)
    project.close()
    print("[med9_setup] saved project %s/%s" % (project_dir, project_name))
    return 0


def _running_inside_ghidra():
    """PyGhidra injects currentProgram/monitor into builtins, not globals()."""
    try:
        currentProgram                        # noqa: F821,B018 - injected by Ghidra
    except NameError:
        return False
    return True


if _running_inside_ghidra():
    _run_as_ghidra_script()
elif __name__ == "__main__":
    sys.exit(_run_standalone(sys.argv[1:]))
