#!/usr/bin/env python3
# Export Ghidra functions, labels and plate comments to re/.
# @category MED9
# @runtime PyGhidra
"""export_symbols.py -- Ghidra project -> re/ghidra_export/ + re/symbols.csv.

Two outputs, because they have different jobs:

* ``re/ghidra_export/functions.csv`` is a full dump of **every** function in
  the program, auto-named ``FUN_`` ones included.  It is the session artefact:
  it makes function counts and coverage diffable between runs.
* ``re/symbols.csv`` is the curated knowledge base (docs/04_re_guidelines.md
  section 4).  Only symbols whose name was **changed from the Ghidra default**
  are merged into it, and an existing row is never overwritten -- the evidence
  recorded by hand is richer than anything Ghidra can export.

Confidence of a new row comes from the function's plate comment: if it
contains VERIFIED-STATIC / VERIFIED-DYNAMIC / COMMUNITY / HYPOTHESIS the
matching tag is used, otherwise ``hypothesis``.  Write the tag into the plate
comment while you work and the export stays honest.

Run it as a Ghidra post-script::

    ./.venv/bin/python -m pyghidra.ghidra_launch \
        --install-dir "$GHIDRA_INSTALL_DIR" \
        ghidra.app.util.headless.AnalyzeHeadless <projdir> med9 \
        -process passat_azx_ori.bin -noanalysis \
        -scriptPath ghidra_scripts -postScript export_symbols.py <repo-root>

or standalone::

    ./.venv/bin/python ghidra_scripts/export_symbols.py \
        --project-dir <projdir> --project-name med9 --repo .
"""
from __future__ import annotations

import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from med9_symbols import (COLUMNS, classify, is_default_name, merge,  # noqa: E402
                          read_csv, write_csv)

TAG_TO_CONFIDENCE = [
    ("VERIFIED-DYNAMIC", "dynamic"),
    ("VERIFIED-STATIC", "static"),
    ("COMMUNITY", "community"),
    ("HYPOTHESIS", "hypothesis"),
]

FUNCTIONS_CSV = os.path.join("re", "ghidra_export", "functions.csv")
SYMBOLS_CSV = os.path.join("re", "symbols.csv")


def confidence_from_comment(comment):
    if comment:
        upper = comment.upper()
        for tag, value in TAG_TO_CONFIDENCE:
            if tag in upper:
                return value
    return "hypothesis"


def first_line(text):
    if not text:
        return ""
    return text.strip().splitlines()[0].strip()


class Exporter(object):
    def __init__(self, program, repo_root):
        self.program = program
        self.repo_root = repo_root
        self.today = datetime.date.today().isoformat()
        self.program_name = str(program.getName())

    def _row(self, cpu_addr, kind, name, size, comment):
        space, file_off = classify(cpu_addr)
        evidence = "ghidra %s %s @%06X" % (self.program_name, self.today, cpu_addr)
        note = first_line(comment)
        return {
            "cpu_addr": "0x%06X" % cpu_addr,
            "file_off": file_off,
            "space": space,
            "kind": kind,
            "name": name,
            "size": ("0x%X" % size) if size else "",
            "evidence": evidence,
            "confidence": confidence_from_comment(comment),
            "source": "ghidra",
            "notes": note,
        }

    def functions(self):
        """Every function, in address order."""
        listing = self.program.getListing()
        rows = []
        iterator = self.program.getFunctionManager().getFunctions(True)
        while iterator.hasNext():
            func = iterator.next()
            entry = func.getEntryPoint()
            cpu_addr = int(entry.getOffset())
            comment = listing.getComment(3, entry)      # 3 = CodeUnit.PLATE_COMMENT
            if comment is None:
                comment = func.getComment()
            # Thunks stay in functions.csv (they are coverage) but are kept out
            # of symbols.csv: Ghidra names a thunk after its target, so merging
            # it would put the real function's name at a wrapper address.
            kind = "thunk" if func.isThunk() else "func"
            rows.append(self._row(cpu_addr, kind, str(func.getName()),
                                  int(func.getBody().getNumAddresses()),
                                  str(comment) if comment else ""))
        return rows

    def labels(self):
        """User-defined labels that are not function entry points."""
        from ghidra.program.model.symbol import SourceType, SymbolType

        listing = self.program.getListing()
        fm = self.program.getFunctionManager()
        rows = []
        iterator = self.program.getSymbolTable().getAllSymbols(False)
        while iterator.hasNext():
            symbol = iterator.next()
            if symbol.getSource() == SourceType.DEFAULT:
                continue
            if symbol.getSymbolType() != SymbolType.LABEL:
                continue
            address = symbol.getAddress()
            if address is None or not address.isMemoryAddress():
                continue
            if fm.getFunctionAt(address) is not None:
                continue
            cpu_addr = int(address.getOffset())
            space, _ = classify(cpu_addr)
            kind = "var" if space.startswith("sram") or space == "periph" else "const"
            name = str(symbol.getName())
            if name.startswith("tbl_"):
                kind = "table"
            data = listing.getDataAt(address)
            size = int(data.getLength()) if data is not None else 0
            comment = listing.getComment(3, address)
            rows.append(self._row(cpu_addr, kind, name, size,
                                  str(comment) if comment else ""))
        return rows

    def run(self):
        function_rows = self.functions()
        label_rows = self.labels()

        functions_path = os.path.join(self.repo_root, FUNCTIONS_CSV)
        write_csv(functions_path, function_rows)
        print("[export_symbols] %d functions -> %s"
              % (len(function_rows), functions_path))

        candidates = [row for row in function_rows + label_rows
                      if row["kind"] != "thunk" and not is_default_name(row["name"])]
        candidates.sort(key=lambda row: int(row["cpu_addr"], 16))

        symbols_path = os.path.join(self.repo_root, SYMBOLS_CSV)
        existing = read_csv(symbols_path)
        merged, added, skipped = merge(existing, candidates)
        merged.sort(key=lambda row: (int(row["cpu_addr"], 16), row["name"]))
        write_csv(symbols_path, merged)
        print("[export_symbols] %d named symbols: %d new, %d already known -> %s"
              % (len(candidates), added, skipped, symbols_path))
        print("[export_symbols] symbols.csv now holds %d rows in %d columns"
              % (len(merged), len(COLUMNS)))
        return len(function_rows), added


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
    Exporter(currentProgram, _repo_root_from(args)).run()   # noqa: F821


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

    project = GhidraProject.openProject(project_dir, project_name, True)
    try:
        program = project.openProgram("/", program_name, True)
        Exporter(program, repo_root).run()
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
