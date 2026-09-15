#!/usr/bin/env python3
"""Shared helpers for the re/symbols.csv knowledge base.

Column format is fixed by docs/04_re_guidelines.md section 4::

    cpu_addr, file_off, space, kind, name, size, evidence, confidence,
    source, notes

This module is imported by ``export_symbols.py`` and ``import_symbols.py``.
It deliberately has no Ghidra dependency so it can be unit-tested with plain
CPython.
"""
from __future__ import annotations

import csv
import io
import os

COLUMNS = [
    "cpu_addr", "file_off", "space", "kind", "name", "size",
    "evidence", "confidence", "source", "notes",
]

# Address spaces, from docs/02_memory_map.md section 3.  (name, start, end
# inclusive, file offset of start or None).
SPACES = [
    ("ext_flash", 0x000000, 0x1FFFFF, 0x000000),
    ("int_flash", 0x404000, 0x47FFFF, 0x200000),
    ("cal",       0x5C0000, 0x5FFFFF, 0x1C0000),
    ("periph",    0x6F8000, 0x78003F, None),
    ("sram_int",  0x7F8000, 0x7FFFFF, None),
    ("sram_ext",  0x800000, 0x807FFF, None),
    ("periph_cs", 0x900000, 0xA07FFF, None),
]

CONFIDENCE = ("static", "dynamic", "community", "hypothesis")

# Names Ghidra derives by itself.  They carry no knowledge of ours, so they go
# to functions.csv only and are never merged into symbols.csv.  Beyond the
# obvious FUN_/DAT_ prefixes this covers the ones that look hand-written but
# are not: thunks are named after their target, and the decompiler's switch
# recovery invents switchD/switchdataD/caseD/default labels.
DEFAULT_PREFIXES = ("FUN_", "DAT_", "LAB_", "SUB_", "UNK_", "EXT_", "OFF_",
                    "PTR_", "SWITCH_", "ARRAY_", "thunk_", "switchD",
                    "switchdataD", "caseD", "jumpTable", "default",
                    "s_", "u_", "byte_", "word_", "dword_")


def classify(cpu_addr: int):
    """Return (space, file_off or "") for a CPU address."""
    for name, start, end, file_start in SPACES:
        if start <= cpu_addr <= end:
            if file_start is None:
                return (name if name != "periph_cs" else "periph"), ""
            return name, "0x%06X" % (file_start + cpu_addr - start)
    return "unknown", ""


def is_default_name(name: str) -> bool:
    return any(name.startswith(prefix) for prefix in DEFAULT_PREFIXES)


def parse_addr(text: str) -> int:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty address")
    return int(text, 16) if text.lower().startswith("0x") else int(text, 16)


def read_csv(path: str):
    """Read symbols.csv into a list of dicts; missing file gives []."""
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return [{key: (row.get(key) or "") for key in COLUMNS} for row in rows]


def dump_csv(rows) -> str:
    """Serialise rows with the canonical column order and quoting."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, lineterminator="\n",
                            quoting=csv.QUOTE_MINIMAL)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in COLUMNS})
    return buffer.getvalue()


def write_csv(path: str, rows) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        handle.write(dump_csv(rows))


def merge(existing, new_rows):
    """Merge new_rows into existing, keyed by (cpu_addr, name).

    An existing row is never silently overwritten: a row whose name and
    address both match is left alone (its evidence is usually richer than
    what Ghidra can export), and everything else is appended.  Returns
    (merged_rows, added_count, skipped_count).
    """
    index = {}
    for row in existing:
        index[(row["cpu_addr"].lower(), row["name"])] = row

    merged = list(existing)
    added = 0
    skipped = 0
    for row in new_rows:
        key = (row["cpu_addr"].lower(), row["name"])
        if key in index:
            skipped += 1
            continue
        index[key] = row
        merged.append(row)
        added += 1
    return merged, added, skipped
