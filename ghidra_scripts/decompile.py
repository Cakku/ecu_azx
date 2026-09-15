#!/usr/bin/env python3
# Decompile / disassemble functions of the MED9 program from the command line.
# @category MED9
# @runtime PyGhidra
"""decompile.py -- dump decompiled C and/or disassembly for MED9 addresses.

Standalone helper around an existing Ghidra project (built by
``med9_setup.py``).  It is the read-only counterpart of the symbol scripts:
it never modifies the program, so several agents can point it at their own
copy of the project at the same time.

Usage::

    ./.venv/bin/python ghidra_scripts/decompile.py \
        --project-dir /tmp/ghidra_B2 --project-name med9 \
        0x135600 0x1345b8                # decompile the containing functions

    ... --asm 0x135750 --count 60        # raw disassembly, 60 instructions
    ... --callers 0x135600               # who calls the function at an address
    ... --refs 0x2bc90                   # references to a data address
    ... --xrefs-func 0x135600            # callers and callees of a function

An address that is not inside a function is disassembled instead (or, with
``--create``, turned into a function first -- the only writing mode).
"""
from __future__ import annotations

import argparse
import os
import sys


def _parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("addresses", nargs="*", help="CPU addresses, hex")
    p.add_argument("--project-dir", default="/tmp/ghidra_B2")
    p.add_argument("--project-name", default="med9")
    p.add_argument("--program", default="passat_azx_ori.bin")
    p.add_argument("--asm", action="append", default=[],
                   help="disassemble from this address instead of decompiling")
    p.add_argument("--count", type=int, default=40,
                   help="instructions to disassemble per --asm address")
    p.add_argument("--callers", action="append", default=[],
                   help="list callers of the function containing this address")
    p.add_argument("--refs", action="append", default=[],
                   help="list references TO this address")
    p.add_argument("--xrefs-func", action="append", default=[],
                   help="list callers and callees of the containing function")
    p.add_argument("--create", action="store_true",
                   help="create a function at an address that has none")
    p.add_argument("--timeout", type=int, default=120)
    return p.parse_args(argv)


def _addr(prog, text):
    return prog.getAddressFactory().getDefaultAddressSpace().getAddress(
        int(str(text), 16) if not str(text).startswith("0x") else int(str(text), 16))


def main(argv):
    args = _parse_args(argv)
    import pyghidra
    pyghidra.start(verbose=False)

    from ghidra.base.project import GhidraProject
    from ghidra.app.decompiler import DecompInterface
    from ghidra.util.task import TaskMonitor
    from ghidra.program.model.symbol import RefType  # noqa: F401

    project = GhidraProject.openProject(args.project_dir, args.project_name, True)
    prog = project.openProgram("/", args.program, not args.create)
    try:
        fm = prog.getFunctionManager()
        listing = prog.getListing()
        ref_mgr = prog.getReferenceManager()

        ifc = DecompInterface()
        ifc.openProgram(prog)

        tx = prog.startTransaction("decompile.py") if args.create else None
        try:
            for text in args.addresses:
                a = _addr(prog, text)
                fn = fm.getFunctionContaining(a)
                if fn is None and args.create:
                    from ghidra.app.cmd.function import CreateFunctionCmd
                    CreateFunctionCmd(a).applyTo(prog, TaskMonitor.DUMMY)
                    fn = fm.getFunctionContaining(a)
                print("=" * 72)
                if fn is None:
                    print("no function at %s -- disassembling" % a)
                    _dump_asm(listing, a, args.count)
                    continue
                print("FUNCTION %s  %s .. %s" % (
                    fn.getName(), fn.getEntryPoint(), fn.getBody().getMaxAddress()))
                res = ifc.decompileFunction(fn, args.timeout, TaskMonitor.DUMMY)
                if res.decompileCompleted():
                    print(res.getDecompiledFunction().getC())
                else:
                    print("decompile failed: %s" % res.getErrorMessage())
        finally:
            if tx is not None:
                prog.endTransaction(tx, True)

        for text in args.asm:
            a = _addr(prog, text)
            print("=" * 72)
            print("DISASSEMBLY from %s" % a)
            _dump_asm(listing, a, args.count)

        for text in args.callers:
            a = _addr(prog, text)
            fn = fm.getFunctionContaining(a)
            print("=" * 72)
            if fn is None:
                print("no function containing %s" % a)
                continue
            print("callers of %s (%s):" % (fn.getName(), fn.getEntryPoint()))
            for c in fn.getCallingFunctions(TaskMonitor.DUMMY):
                print("   %s  %s" % (c.getEntryPoint(), c.getName()))

        for text in args.xrefs_func:
            a = _addr(prog, text)
            fn = fm.getFunctionContaining(a)
            print("=" * 72)
            if fn is None:
                print("no function containing %s" % a)
                continue
            print("%s (%s)" % (fn.getName(), fn.getEntryPoint()))
            print("  callers:")
            for c in fn.getCallingFunctions(TaskMonitor.DUMMY):
                print("     %s  %s" % (c.getEntryPoint(), c.getName()))
            print("  callees:")
            for c in fn.getCalledFunctions(TaskMonitor.DUMMY):
                print("     %s  %s" % (c.getEntryPoint(), c.getName()))

        for text in args.refs:
            a = _addr(prog, text)
            print("=" * 72)
            print("references to %s:" % a)
            it = ref_mgr.getReferencesTo(a)
            n = 0
            while it.hasNext():
                r = it.next()
                src = r.getFromAddress()
                fn = fm.getFunctionContaining(src)
                print("   %s  %-18s  %s" % (
                    src, r.getReferenceType(), fn.getName() if fn else "-"))
                n += 1
            if not n:
                print("   (none)")
    finally:
        project.close()
    return 0


def _dump_asm(listing, addr, count):
    ins = listing.getInstructionAt(addr)
    if ins is None:
        ins = listing.getInstructionAfter(addr)
    for _ in range(count):
        if ins is None:
            print("   (no instruction)")
            break
        print("   %s  %-28s" % (ins.getAddress(), ins.toString()))
        ins = ins.getNext()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
