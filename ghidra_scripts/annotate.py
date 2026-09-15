#!/usr/bin/env python3
# Apply a CSV of names/plate comments to the MED9 program.
# @category MED9
# @runtime PyGhidra
"""annotate.py -- bulk-apply names and plate comments from a CSV.

The symbol round trip in this repo runs Ghidra -> ``re/symbols.csv``
(``export_symbols.py``), and the confidence tag travels in the **plate
comment**.  So a working session has to get its findings *into* Ghidra first.
Doing that by hand in the GUI is not reproducible and not possible headless;
this script is the missing direction:

    address,name,kind,comment

* ``address`` -- CPU address, hex.
* ``name``    -- the symbol name; empty leaves the existing name alone.
* ``kind``    -- ``func`` creates a function at the address if there is none
  (and names the function rather than a stray label), anything else just
  creates/renames a label.
* ``comment`` -- plate comment.  Put the VERIFIED-STATIC / VERIFIED-DYNAMIC /
  COMMUNITY / HYPOTHESIS tag in here; ``export_symbols.py`` reads it.

Usage::

    ./.venv/bin/python ghidra_scripts/annotate.py --csv my_names.csv \
        --project-dir /tmp/ghidra_B2 --project-name med9

Rows whose address is already named the same way are left untouched, so the
script is idempotent and safe to re-run.
"""
from __future__ import annotations

import argparse
import csv
import sys


def _parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--csv", required=True, help="address,name,kind,comment")
    p.add_argument("--project-dir", default="/tmp/ghidra_B2")
    p.add_argument("--project-name", default="med9")
    p.add_argument("--program", default="passat_azx_ori.bin")
    return p.parse_args(argv)


def main(argv):
    args = _parse_args(argv)

    rows = []
    with open(args.csv, newline="") as fh:
        for row in csv.DictReader(fh):
            if not row.get("address"):
                continue
            rows.append(row)

    import pyghidra
    pyghidra.start(verbose=False)

    from ghidra.base.project import GhidraProject
    from ghidra.program.model.symbol import SourceType
    from ghidra.app.cmd.function import CreateFunctionCmd
    from ghidra.util.task import TaskMonitor
    from ghidra.program.model.listing import CodeUnit

    project = GhidraProject.openProject(args.project_dir, args.project_name, True)
    prog = project.openProgram("/", args.program, False)
    space = prog.getAddressFactory().getDefaultAddressSpace()
    fm = prog.getFunctionManager()
    listing = prog.getListing()
    symtab = prog.getSymbolTable()

    tx = prog.startTransaction("annotate.py")
    named = commented = created = skipped = 0
    try:
        for row in rows:
            addr = space.getAddress(int(row["address"], 16))
            if prog.getMemory().getBlock(addr) is None:
                print("[annotate] %s: no memory block, skipped" % addr)
                skipped += 1
                continue
            name = (row.get("name") or "").strip()
            kind = (row.get("kind") or "").strip()
            comment = (row.get("comment") or "").strip()

            fn = fm.getFunctionAt(addr)
            if kind == "func" and fn is None:
                CreateFunctionCmd(addr).applyTo(prog, TaskMonitor.DUMMY)
                fn = fm.getFunctionAt(addr)
                if fn is not None:
                    created += 1
            if name:
                if fn is not None:
                    if fn.getName() != name:
                        fn.setName(name, SourceType.USER_DEFINED)
                        named += 1
                else:
                    sym = symtab.getPrimarySymbol(addr)
                    if sym is None or sym.getName() != name:
                        symtab.createLabel(addr, name, SourceType.USER_DEFINED)
                        named += 1
            if comment:
                listing.setComment(addr, CodeUnit.PLATE_COMMENT, comment)
                commented += 1
        ok = True
    finally:
        prog.endTransaction(tx, True)
    project.save(prog)
    project.close()
    print("[annotate] %d rows: %d named, %d commented, %d functions created, %d skipped"
          % (len(rows), named, commented, created, skipped))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
