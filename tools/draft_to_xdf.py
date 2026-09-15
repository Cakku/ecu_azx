#!/usr/bin/env python3
"""re/calibration_draft.csv -> a TunerPro definition (.xdf).

The draft CSV produced by ``ghidra_scripts/enumerate_maps.py`` holds CPU
addresses in the calibration window 0x5C0000-0x5FFFFF.  **TunerPro addresses
are file offsets**, so every address is mapped through ``med9lib.cpu_to_file``
(cpu - 0x400000 inside that window) and ``<baseoffset>`` stays 0.

Everything the ECU stores is big-endian, so no ``mmedtypeflags`` LSB-first bit
is set; signed value arrays get flag 0x01.  A 2-D table is emitted as an
``XDFTABLE`` with an X (column) axis, a Y (row) axis and a Z array whose row
stride is ``x_n * elem_size`` -- the layout ``val[iy * nx + ix]`` that the
Bosch interpolation helpers use.  A 1-D curve becomes a table with a single
row.  Scalars become ``XDFCONSTANT`` entries and are off by default because
there are thousands of them.

No scaling is applied: every axis and value is raw counts (``MATH
equation="X"``).  The factors are not known yet -- that is brief B6-B9 work --
and a wrong factor is worse than none.

Usage::

    python3 tools/draft_to_xdf.py re/calibration_draft.csv -o work/med9_draft.xdf
    python3 tools/draft_to_xdf.py re/calibration_draft.csv -o work/all.xdf \\
            --scalars --min-confidence hypothesis
    python3 tools/draft_to_xdf.py --self-test         # structural check only

Options:
    --scalars            also emit the detected single calibration values
    --min-confidence     ``static`` (default) or ``hypothesis``
    --kinds              comma-separated subset of the ``kind`` column
    --limit N            stop after N objects (for a quick look)
    --validate FILE      parse an existing .xdf and report its structure
"""
from __future__ import annotations

import argparse
import csv
import datetime
import os
import sys
import xml.etree.ElementTree as ET
from xml.dom import minidom

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import med9lib as m  # noqa: E402

XDF_VERSION = "1.70"

# kind -> (category name, is 2-D)
KINDS = {
    "map_2d": ("Maps (2D, own axes)", True),
    "map_2d_shared": ("Maps (2D, shared axes)", True),
    "map_2d_data": ("Maps (2D, axes inferred)", True),
    "curve_1d": ("Curves (1D, own axis)", False),
    "curve_1d_shared": ("Curves (1D, shared axis)", False),
    "axis": ("Axes", False),
    "scalar": ("Constants", False),
}
CONFIDENCE_ORDER = {"static": 0, "dynamic": 0, "community": 1, "hypothesis": 2}


def elem_bits(text, default=8):
    """'u16' -> (16, False); 's8' -> (8, True)."""
    if not text:
        return default, False
    return int(text[1:]), text[0] == "s"


def sub(parent, tag, text=None, **attrs):
    node = ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})
    if text is not None:
        node.text = str(text)
    return node


def add_math(parent):
    """The identity conversion.  TunerPro requires a MATH block on every axis."""
    math = sub(parent, "MATH", equation="X")
    sub(math, "VAR", id="X")


def embedded(parent, file_off, bits, signed, rows=None, cols=None):
    """One EMBEDDEDDATA element.

    ``mmedtypeflags`` bit 0x01 is "signed" and bit 0x02 is "LSB first"; the ECU
    is big-endian so 0x02 is never set.  Both strides stay 0, which is
    TunerPro's "packed, row major" and is what the Bosch layout
    ``val[iy * nx + ix]`` is.
    """
    attrs = {
        "mmedtypeflags": "0x%02X" % (0x01 if signed else 0x00),
        "mmedaddress": "0x%X" % file_off,
        "mmedelementsizebits": bits,
        "mmedmajorstridebits": 0,
        "mmedminorstridebits": 0,
    }
    if rows is not None:
        attrs["mmedrowcount"] = rows
    if cols is not None:
        attrs["mmedcolcount"] = cols
    return sub(parent, "EMBEDDEDDATA", **attrs)


def axis_element(table, axis_id, count, file_off, bits, signed):
    node = sub(table, "XDFAXIS", id=axis_id, uniqueid="0x0")
    if file_off is not None:
        embedded(node, file_off, bits, signed)
    sub(node, "indexcount", count)
    sub(node, "datatype", 0)
    sub(node, "unittype", 0)
    sub(node, "DALINK", index=0)
    if file_off is None:
        # No axis was recovered: label the positions 0..n-1 instead.
        sub(node, "embedinfo", type=0)
        for i in range(count):
            sub(node, "LABEL", index=i, value=str(i))
    else:
        sub(node, "embedinfo", type=1)
    sub(node, "units", "-")
    add_math(node)
    return node


class Builder(object):
    def __init__(self, title, description):
        self.root = ET.Element("XDFFORMAT", version=XDF_VERSION)
        header = sub(self.root, "XDFHEADER")
        sub(header, "flags", "0x1")
        sub(header, "fileversion", "1.0")
        sub(header, "deftitle", title)
        sub(header, "description", description)
        sub(header, "author", "ecu_azx / ghidra_scripts/enumerate_maps.py")
        sub(header, "baseoffset", "0")
        sub(header, "DEFAULTS", datasizeinbits="8", sigdigits="2",
            outputtype="1", signed="0", lsbfirst="0", float="0")
        sub(header, "REGION", type="0xFFFFFFFF", startaddress="0x0",
            size="0x%X" % m.DUMP_SIZE, regionflags="0x0", name="Binary File",
            desc="Binary File")
        self.header = header
        self.categories = {}
        self.uid = 0

    def category(self, name):
        if name not in self.categories:
            index = len(self.categories)
            self.categories[name] = index
            sub(self.header, "CATEGORY", index="0x%X" % index, name=name)
        return self.categories[name]

    def next_uid(self):
        self.uid += 1
        return "0x%X" % self.uid

    def table(self, row, name, category):
        kind = row["kind"]
        bits, signed = elem_bits("%s%d" % ("s" if row["signed"] == "1" else "u",
                                           int(row["elem_size"]) * 8), 8)
        xn = int(row["x_n"] or 1)
        yn = int(row["y_n"] or 1)
        data_off = m.cpu_to_file(int(row["addr"], 16))

        node = sub(self.root, "XDFTABLE", uniqueid=self.next_uid(), flags="0x30")
        sub(node, "title", name)
        sub(node, "description",
            "%s at CPU %s (file 0x%06X), %dx%d %s%d. %s"
            % (kind, row["addr"], data_off, xn, yn,
               "s" if signed else "u", bits, row["evidence"]))
        sub(node, "CATEGORYMEM", index="0", category="0x%X" % (category + 1))

        xbits, xsigned = elem_bits(row["x_elem"])
        xoff = (m.cpu_to_file(int(row["x_axis_addr"], 16))
                if row["x_axis_addr"] else None)
        axis_element(node, "x", xn, xoff, xbits, xsigned)

        ybits, ysigned = elem_bits(row["y_elem"])
        yoff = (m.cpu_to_file(int(row["y_axis_addr"], 16))
                if row["y_axis_addr"] else None)
        axis_element(node, "y", yn, yoff, ybits, ysigned)

        z = sub(node, "XDFAXIS", id="z", uniqueid="0x0")
        embedded(z, data_off, bits, signed, rows=yn, cols=xn)
        sub(z, "units", "-")
        sub(z, "indexcount", xn * yn)
        sub(z, "decimalpl", 0)
        sub(z, "min", 0)
        sub(z, "max", (1 << (bits - 1)) - 1 if signed else (1 << bits) - 1)
        sub(z, "outputtype", 1)
        add_math(z)
        return node

    def constant(self, row, name, category):
        bits = int(row["elem_size"]) * 8
        signed = row["signed"] == "1"
        off = m.cpu_to_file(int(row["addr"], 16))
        node = sub(self.root, "XDFCONSTANT", uniqueid=self.next_uid(), flags="0x0")
        sub(node, "title", name)
        sub(node, "description", "%s (file 0x%06X). %s"
            % (row["addr"], off, row["evidence"]))
        sub(node, "CATEGORYMEM", index="0", category="0x%X" % (category + 1))
        embedded(node, off, bits, signed)
        sub(node, "units", "-")
        sub(node, "decimalpl", 0)
        sub(node, "outputtype", 1)
        add_math(node)
        return node

    def tostring(self):
        raw = ET.tostring(self.root, encoding="utf-8")
        pretty = minidom.parseString(raw).toprettyxml(indent="  ", encoding="UTF-8")
        return b"\n".join(line for line in pretty.split(b"\n") if line.strip())


def object_name(row, used):
    """A stable, unique name.  Real Bosch labels only where the CSV has one."""
    if row["name_or_blank"]:
        base = row["name_or_blank"]
    else:
        prefix = {"map_2d": "KF", "map_2d_shared": "GKF", "map_2d_data": "KFD",
                  "curve_1d": "KL", "curve_1d_shared": "GKL",
                  "axis": "SST", "scalar": "K"}.get(row["kind"], "T")
        base = "cand_%s_%s" % (prefix, row["addr"][2:])
    name = base
    n = 1
    while name in used:
        n += 1
        name = "%s_%d" % (base, n)
    used.add(name)
    return name


def build(rows, title, description, want_scalars, min_conf, kinds, limit):
    builder = Builder(title, description)
    used = set()
    counts = {}
    for row in rows:
        kind = row["kind"]
        if kind not in KINDS:
            continue
        if kind == "scalar" and not want_scalars:
            continue
        if kinds and kind not in kinds:
            continue
        if CONFIDENCE_ORDER.get(row["confidence"], 9) > min_conf:
            continue
        if not row["elem_size"]:
            continue
        category = builder.category(KINDS[kind][0])
        name = object_name(row, used)
        if kind == "scalar":
            builder.constant(row, name, category)
        else:
            builder.table(row, name, category)
        counts[kind] = counts.get(kind, 0) + 1
        if limit and sum(counts.values()) >= limit:
            break
    return builder, counts


# --------------------------------------------------------------------------
# Structural validation.  TunerPro itself only runs on Windows, so the check
# that travels with the repository is this one: parse the file back and assert
# the element/attribute skeleton a v1.70 XDF must have.
# --------------------------------------------------------------------------
REQUIRED_HEADER = ("flags", "fileversion", "deftitle", "author", "baseoffset",
                   "DEFAULTS", "REGION")


def validate(path_or_bytes):
    if isinstance(path_or_bytes, bytes):
        root = ET.fromstring(path_or_bytes)
    else:
        root = ET.parse(path_or_bytes).getroot()
    problems = []
    if root.tag != "XDFFORMAT":
        problems.append("root element is %s, not XDFFORMAT" % root.tag)
    if root.get("version") != XDF_VERSION:
        problems.append("version is %r" % root.get("version"))
    header = root.find("XDFHEADER")
    if header is None:
        problems.append("no XDFHEADER")
    else:
        for tag in REQUIRED_HEADER:
            if header.find(tag) is None:
                problems.append("XDFHEADER has no %s" % tag)
    categories = {c.get("index") for c in root.iter("CATEGORY")}
    tables = root.findall("XDFTABLE")
    constants = root.findall("XDFCONSTANT")
    uids = set()
    for node in tables + constants:
        uid = node.get("uniqueid")
        if uid in uids:
            problems.append("duplicate uniqueid %s" % uid)
        uids.add(uid)
        if node.find("title") is None or not (node.find("title").text or "").strip():
            problems.append("%s %s has no title" % (node.tag, uid))
        mem = node.find("CATEGORYMEM")
        if mem is None:
            problems.append("%s %s has no CATEGORYMEM" % (node.tag, uid))
        elif "0x%X" % (int(mem.get("category"), 16) - 1) not in categories:
            problems.append("%s %s points at an undeclared category" % (node.tag, uid))
    for table in tables:
        axes = {a.get("id") for a in table.findall("XDFAXIS")}
        if axes != {"x", "y", "z"}:
            problems.append("table %s has axes %s" % (table.get("uniqueid"), sorted(axes)))
        z = table.find("XDFAXIS[@id='z']")
        data = z.find("EMBEDDEDDATA") if z is not None else None
        if data is None:
            problems.append("table %s has no z EMBEDDEDDATA" % table.get("uniqueid"))
            continue
        rows = int(data.get("mmedrowcount"))
        cols = int(data.get("mmedcolcount"))
        bits = int(data.get("mmedelementsizebits"))
        start = int(data.get("mmedaddress"), 16)
        if bits not in (8, 16, 32):
            problems.append("table %s element size %d" % (table.get("uniqueid"), bits))
        if start + rows * cols * bits // 8 > m.DUMP_SIZE:
            problems.append("table %s runs past the end of the file"
                            % table.get("uniqueid"))
        for axis_id, want in (("x", cols), ("y", rows)):
            axis = table.find("XDFAXIS[@id='%s']" % axis_id)
            got = int((axis.find("indexcount").text or "0"))
            if got != want:
                problems.append("table %s %s axis indexcount %d != %d"
                                % (table.get("uniqueid"), axis_id, got, want))
            if axis.find("MATH") is None:
                problems.append("table %s %s axis has no MATH"
                                % (table.get("uniqueid"), axis_id))
    for node in constants:
        data = node.find("EMBEDDEDDATA")
        if data is None or data.get("mmedaddress") is None:
            problems.append("constant %s has no address" % node.get("uniqueid"))
    return len(tables), len(constants), problems


def self_test():
    """Build a tiny definition from hand-made rows and validate it."""
    rows = [
        dict(addr="0x5CBF3E", kind="map_2d", x_axis_addr="0x5CBF22",
             y_axis_addr="0x5CBF0A", x_n="14", y_n="10", elem_size="2",
             signed="0", consumer_func="test", name_or_blank="",
             confidence="static", evidence="self-test", x_elem="u16",
             y_elem="u16", struct_addr="0x5CBF08", sites="1"),
        dict(addr="0x5C4233", kind="curve_1d", x_axis_addr="0x5C4225",
             y_axis_addr="", x_n="8", y_n="", elem_size="1", signed="1",
             consumer_func="test", name_or_blank="KLTEST",
             confidence="static", evidence="self-test", x_elem="u8",
             y_elem="", struct_addr="0x5C4224", sites="1"),
        dict(addr="0x5D0000", kind="map_2d_data", x_axis_addr="",
             y_axis_addr="", x_n="6", y_n="4", elem_size="1", signed="0",
             consumer_func="test", name_or_blank="", confidence="hypothesis",
             evidence="self-test", x_elem="", y_elem="", struct_addr="",
             sites="1"),
        dict(addr="0x5C8000", kind="scalar", x_axis_addr="", y_axis_addr="",
             x_n="1", y_n="", elem_size="2", signed="0", consumer_func="test",
             name_or_blank="", confidence="static", evidence="self-test",
             x_elem="", y_elem="", struct_addr="", sites="1"),
    ]
    builder, counts = build(rows, "self test", "self test", True, 2, None, 0)
    blob = builder.tostring()
    tables, constants, problems = validate(blob)
    print("self-test: %d tables, %d constants, %d problems"
          % (tables, constants, len(problems)))
    for problem in problems:
        print("  !", problem)
    assert tables == 3 and constants == 1, (tables, constants)
    assert not problems, problems
    # the file offsets must be the calibration ones, not the CPU addresses
    assert b'mmedaddress="0x1CBF3E"' in blob, "cpu -> file mapping missing"
    print("self-test ok")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv", nargs="?", help="re/calibration_draft.csv")
    parser.add_argument("-o", "--out", help="output .xdf")
    parser.add_argument("--scalars", action="store_true")
    parser.add_argument("--min-confidence", default="static",
                        choices=("static", "hypothesis"))
    parser.add_argument("--kinds", help="comma-separated subset of the kind column")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--title", default="MED9.1.1 03H906032 draft (machine-generated)")
    parser.add_argument("--validate", help="validate an existing .xdf and exit")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if args.validate:
        tables, constants, problems = validate(args.validate)
        print("%s: %d tables, %d constants, %d problems"
              % (args.validate, tables, constants, len(problems)))
        for problem in problems[:40]:
            print("  !", problem)
        return 1 if problems else 0
    if not args.csv or not args.out:
        parser.error("need the draft CSV and -o OUT (or --self-test/--validate)")

    with open(args.csv, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    description = (
        "Machine-generated draft for data/passat_azx_ori.bin "
        "(03H906032 / 1037382557, SHA-256 b15590d3...09b3), produced by "
        "ghidra_scripts/enumerate_maps.py + tools/draft_to_xdf.py on %s. "
        "Addresses are file offsets. NO SCALING IS APPLIED: every value is raw "
        "counts. Names starting with cand_ are placeholders, not Bosch labels. "
        "Do not flash anything based on this without checking the map in "
        "Ghidra first." % datetime.date.today().isoformat())
    kinds = set(args.kinds.split(",")) if args.kinds else None
    builder, counts = build(rows, args.title, description, args.scalars,
                            CONFIDENCE_ORDER[args.min_confidence], kinds,
                            args.limit)
    blob = builder.tostring()
    directory = os.path.dirname(args.out)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with open(args.out, "wb") as handle:
        handle.write(blob)
    tables, constants, problems = validate(blob)
    print("%s: %d tables, %d constants (%s)"
          % (args.out, tables, constants,
             ", ".join("%s=%d" % kv for kv in sorted(counts.items()))))
    if problems:
        print("STRUCTURAL PROBLEMS:")
        for problem in problems[:40]:
            print("  !", problem)
        return 1
    print("structural validation passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
