#!/usr/bin/env python3
# Apply re/symbols.csv to a Ghidra program.
# @category MED9
# @runtime PyGhidra
"""import_symbols.py -- re/symbols.csv -> names, functions and plate comments.

The Ghidra project is not committed; ``re/symbols.csv`` is (see
docs/04_re_guidelines.md section 4).  This script rebuilds the naming of a
fresh project from it, so a new checkout plus ``med9_setup.py`` plus this
gives back the working surface.

For every row:

* ``kind == func``  -- disassemble the entry point if needed, create the
  function if it does not exist, and set its name.
* anything else     -- create a primary label.
* the plate comment is set to ``<notes>`` plus the evidence and confidence
  tags, so the next export can read the confidence back (round trip).

Addresses that no memory block covers are reported and skipped; that is the
signal that the memory map and the CSV disagree, which matters more than the
individual symbol.

Run it as a Ghidra post-script::

    ./.venv/bin/python -m pyghidra.ghidra_launch \
        --install-dir "$GHIDRA_INSTALL_DIR" \
        ghidra.app.util.headless.AnalyzeHeadless <projdir> med9 \
        -process passat_azx_ori.bin -noanalysis \
        -scriptPath ghidra_scripts -postScript import_symbols.py <repo-root>

or standalone::

    ./.venv/bin/python ghidra_scripts/import_symbols.py \
        --project-dir <projdir> --project-name med9 --repo .
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from med9_symbols import read_csv  # noqa: E402

SYMBOLS_CSV = os.path.join("re", "symbols.csv")

CONFIDENCE_TO_TAG = {
    "static": "VERIFIED-STATIC",
    "dynamic": "VERIFIED-DYNAMIC",
    "community": "COMMUNITY",
    "hypothesis": "HYPOTHESIS",
}


class Importer(object):
    def __init__(self, program, monitor, repo_root):
        from ghidra.program.flatapi import FlatProgramAPI

        self.program = program
        self.flat = FlatProgramAPI(program, monitor)
        self.space = program.getAddressFactory().getDefaultAddressSpace()
        self.memory = program.getMemory()
        self.repo_root = repo_root

    def addr(self, value):
        return self.space.getAddress(value)

    def plate_comment(self, row):
        parts = []
        if row.get("notes"):
            parts.append(row["notes"])
        tag = CONFIDENCE_TO_TAG.get(row.get("confidence", "").strip(), "HYPOTHESIS")
        evidence = row.get("evidence", "").strip()
        source = row.get("source", "").strip()
        detail = "%s -- evidence: %s" % (tag, evidence or "(none recorded)")
        if source:
            detail += " (source: %s)" % source
        parts.append(detail)
        return "\n".join(parts)

    def run(self):
        from ghidra.program.model.symbol import SourceType

        path = os.path.join(self.repo_root, SYMBOLS_CSV)
        rows = read_csv(path)
        if not rows:
            print("[import_symbols] nothing to do: %s is missing or empty" % path)
            return 0, 0, 0

        named_funcs = 0
        labels = 0
        skipped = []
        for row in rows:
            text = (row.get("cpu_addr") or "").strip()
            name = (row.get("name") or "").strip()
            if not text or not name:
                continue
            try:
                cpu_addr = int(text, 16)
            except ValueError:
                skipped.append((text, name, "unparsable address"))
                continue

            address = self.addr(cpu_addr)
            if not self.memory.contains(address):
                skipped.append((text, name, "no memory block covers it"))
                continue

            comment = self.plate_comment(row)
            try:
                if (row.get("kind") or "").strip() == "func":
                    func = self.flat.getFunctionAt(address)
                    if func is None:
                        self.flat.disassemble(address)
                        func = self.flat.createFunction(address, name)
                    if func is None:
                        skipped.append((text, name, "could not create a function"))
                        continue
                    func.setName(name, SourceType.USER_DEFINED)
                    named_funcs += 1
                else:
                    self.flat.createLabel(address, name, True,
                                          SourceType.USER_DEFINED)
                    labels += 1
                self.flat.setPlateComment(address, comment)
            except Exception as exc:          # noqa: BLE001 - report and continue
                skipped.append((text, name, str(exc)))

        print("[import_symbols] %s: %d functions named, %d labels created, %d skipped"
              % (path, named_funcs, labels, len(skipped)))
        for text, name, reason in skipped:
            print("[import_symbols]   skipped %s %s: %s" % (text, name, reason))
        return named_funcs, labels, len(skipped)


def _repo_root_from(args):
    for arg in args:
        if not arg.startswith("-"):
            return os.path.abspath(arg)
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(here)


def _run_as_ghidra_script():
    try:
        args = list(getScriptArgs())          # noqa: F821 - injected by Ghidra
    except Exception:                         # noqa: BLE001
        args = []
    program = currentProgram                  # noqa: F821
    try:
        mon = monitor                         # noqa: F821
    except NameError:
        from ghidra.util.task import TaskMonitor
        mon = TaskMonitor.DUMMY
    importer = Importer(program, mon, _repo_root_from(args))
    tx = program.startTransaction("import_symbols")
    ok = False
    try:
        importer.run()
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
    program_name = flag("--program", "passat_azx_ori.bin")
    repo_root = os.path.abspath(flag("--repo", _repo_root_from([])))

    import pyghidra
    pyghidra.start(verbose=False)
    from ghidra.base.project import GhidraProject
    from ghidra.util.task import TaskMonitor

    project = GhidraProject.openProject(project_dir, project_name, False)
    try:
        program = project.openProgram("/", program_name, False)
        importer = Importer(program, TaskMonitor.DUMMY, repo_root)
        tx = program.startTransaction("import_symbols")
        ok = False
        try:
            importer.run()
            ok = True
        finally:
            program.endTransaction(tx, ok)
        project.save(program)
    finally:
        project.close()
    return 0


def _running_inside_ghidra():
    try:
        currentProgram                        # noqa: F821,B018
    except NameError:
        return False
    return True


if _running_inside_ghidra():
    _run_as_ghidra_script()
elif __name__ == "__main__":
    sys.exit(_run_standalone(sys.argv[1:]))
