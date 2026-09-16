#!/usr/bin/env python3
"""re/calibration_draft.csv (+ re/calibration_names.csv) -> a TunerPro .xdf.

The draft CSV produced by ``ghidra_scripts/enumerate_maps.py`` holds CPU
addresses in the calibration window 0x5C0000-0x5FFFFF.  **TunerPro addresses
are file offsets**, so every address is mapped through ``med9lib.cpu_to_file``
(cpu - 0x400000 inside that window) and ``<baseoffset>`` stays 0.

Everything the ECU stores is big-endian, so no ``mmedtypeflags`` LSB-first bit
is set; signed value arrays get flag 0x01.  A 2-D table is emitted as an
``XDFTABLE`` with an X (column) axis, a Y (row) axis and a Z array whose row
stride is ``x_n * elem_size`` -- the layout ``val[iy * nx + ix]`` that the
Bosch interpolation helpers use.  A 1-D curve becomes a table with a single
row.  Scalars become ``XDFCONSTANT`` entries; they are off by default because
there are thousands of them, but a **named** scalar is always emitted.

Physical scaling lives in the hand-maintained sidecar
``re/calibration_names.csv`` (brief D3, issue #41), keyed by the ``addr``
column of the draft.  It carries the Bosch label, the confidence and evidence
for that label, the unit/scale/offset of the value and of both axes, the FR
module (which becomes the TunerPro category) and a description.  Anything it
does not name keeps a ``cand_*`` placeholder and the identity conversion
``equation="X"``; a wrong factor is worse than no factor.  The sidecar is a
sidecar and not extra columns in the draft because ``enumerate_maps.py``
rewrites the draft from the image and would drop hand-added columns.

Usage::

    python3 tools/draft_to_xdf.py re/calibration_draft.csv -o work/med9_draft.xdf
    python3 tools/draft_to_xdf.py re/calibration_draft.csv -o work/all.xdf \\
            --scalars --min-confidence hypothesis
    python3 tools/draft_to_xdf.py re/calibration_draft.csv -o work/full.xdf \\
            --min-confidence hypothesis --extra-rows patches/ff_fuel/ffcal001_rows.csv
    python3 tools/draft_to_xdf.py --self-test         # structural check only

Options:
    --names FILE         the scaling/name sidecar (default: calibration_names.csv
                         next to the draft); --no-names ignores it
    --extra-rows FILE    merge extra objects given in the draft's column format
                         (repeatable); a row whose addr already exists replaces
                         it.  Sidecar columns present in the file are honoured.
    --scalars            also emit the unnamed single calibration values
    --min-confidence     ``static`` (default), ``community`` or ``hypothesis``
    --kinds              comma-separated subset of the ``kind`` column
    --limit N            stop after N objects (for a quick look)
    --validate FILE      parse an existing .xdf and report its structure
"""
from __future__ import annotations

import argparse
import csv
import datetime
import math
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

# The columns of the draft, in order.  `--extra-rows` files must have them.
DRAFT_COLUMNS = ("addr", "kind", "x_axis_addr", "y_axis_addr", "x_n", "y_n",
                 "elem_size", "signed", "consumer_func", "name_or_blank",
                 "confidence", "evidence", "x_elem", "y_elem", "struct_addr",
                 "sites")

# The columns of re/calibration_names.csv.  `addr` is the key into the draft.
# `name_confidence` is the confidence of the *label*, `scale_confidence` of the
# unit/scale/offset; neither touches the draft's own `confidence`, which is the
# detector's confidence in the object and is what --min-confidence filters on.
NAME_COLUMNS = ("addr", "name", "name_confidence", "name_evidence", "unit",
                "scale", "offset", "x_unit", "x_scale", "x_offset", "y_unit",
                "y_scale", "y_offset", "scale_confidence", "fr_module",
                "fr_page", "description")

# Draft columns the sidecar may correct, for the cases where the detector could
# not read a shape out of the image (see re/README.md).  Everything else in the
# draft stays machine-generated.
SHAPE_COLUMNS = ("x_axis_addr", "x_n", "x_elem", "y_axis_addr", "y_n", "y_elem",
                 "signed")

# FR module -> the TunerPro category it becomes.  Bosch module names as in
# re/findings/fr_index.md; the page numbers are in the sidecar, per row.
FR_MODULES = {
    "RKTI": "RKTI - injection time (rk -> ti)",
    "ESSTT": "ESSTT - start quantity",
    "ESNSWL": "ESNSWL - after-start / warm-up",
    "AES": "AES - injection output",
    "AWEA": "AWEA - injection window / angles",
    "ESAUSG": "ESAUSG - injection output stage",
    "GK": "GK - mixture control (rk)",
    "ZWGRU": "ZWGRU - base ignition angle",
    "ZWSTT": "ZWSTT - start ignition angle",
    "ZWBAS": "ZWBAS - ignition angle assembly",
    "ZWMIN": "ZWMIN - latest permitted ignition angle",
    "ZWSEL": "ZWSEL - ignition angle selection / output",
    "NMAXMD": "NMAXMD - engine speed limiter",
    "KRKE": "KRKE - knock detection",
    "KRREG": "KRREG - knock control",
    "MDBAS": "MDBAS - torque structure",
    "MDZW": "MDZW - ignition efficiency",
    "HDRPSOL": "HDRPSOL - rail pressure setpoint",
    "HDR": "HDR - rail pressure controller",
    "HDRPIST": "HDRPIST - rail pressure actual value",
    "AMSV": "AMSV - MSV drive / pump volume",
    "VSTMSV": "VSTMSV - MSV feed-forward, rail model",
    "GGDSKV": "GGDSKV - rail pressure sensor",
    "BGTMOT": "BGTMOT - coolant temperature",
    "FFCAL": "FFCAL001 - flex-fuel calibration block",
}
UNNAMED_CATEGORY = "Unnamed candidates"


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


def parse_number(text, default=None):
    """'0.75' -> 0.75, '100/4096' -> 0.0244140625, '' -> default.

    Fractions are accepted so that the sidecar can say ``100/4096`` instead of
    a rounded decimal; that is how the ECU's own scaling is written down in
    re/findings/*.md and it keeps the CSV reviewable.
    """
    if text is None:
        return default
    text = str(text).strip()
    if not text:
        return default
    if "/" in text:
        num, _, den = text.partition("/")
        return float(num) / float(den)
    return float(text)


def fmt_number(value):
    """A short, exact-enough decimal for an XDF MATH equation."""
    if value == int(value):
        return str(int(value))
    text = "%.10g" % value
    return text


def equation(scale, offset):
    """The TunerPro conversion string for raw -> physical."""
    if scale == 1.0 and offset == 0.0:
        return "X"
    left = "X" if scale == 1.0 else "X*%s" % fmt_number(scale)
    if offset == 0.0:
        return left
    return "%s%s%s" % (left, "-" if offset < 0 else "+", fmt_number(abs(offset)))


def decimals(scale):
    """How many decimal places one raw count deserves in the display.

    One more than the step needs for a coarse step (0.75 degCA -> 2), the bare
    minimum for a fine one, and never more than 3: a 16-bit full-scale axis has
    a step of 0.0015 % and nobody wants to read that many zeroes.
    """
    step = abs(scale)
    if step == 0 or step >= 1:
        return 0
    places = int(math.ceil(-math.log10(step)))
    if step >= 0.01:
        places += 1
    return max(0, min(3, places))


class Scaling(object):
    """unit/scale/offset of one axis or value array."""

    def __init__(self, unit="", scale=None, offset=None):
        self.unit = (unit or "").strip() or "-"
        self.scale = 1.0 if scale is None else scale
        self.offset = 0.0 if offset is None else offset

    @property
    def identity(self):
        return self.scale == 1.0 and self.offset == 0.0

    def convert(self, raw):
        return raw * self.scale + self.offset

    def equation(self):
        return equation(self.scale, self.offset)

    def decimals(self):
        return decimals(self.scale)


def row_scaling(row, prefix=""):
    return Scaling(row.get(prefix + "unit"),
                   parse_number(row.get(prefix + "scale")),
                   parse_number(row.get(prefix + "offset")))


def add_math(parent, scaling=None):
    """The conversion block.  TunerPro requires a MATH on every axis."""
    text = "X" if scaling is None else scaling.equation()
    math_node = sub(parent, "MATH", equation=text)
    sub(math_node, "VAR", id="X")
    return math_node


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


def axis_element(table, axis_id, count, file_off, bits, signed, scaling=None):
    node = sub(table, "XDFAXIS", id=axis_id, uniqueid="0x0")
    if file_off is not None:
        embedded(node, file_off, bits, signed)
    sub(node, "indexcount", count)
    sub(node, "datatype", 0)
    sub(node, "unittype", 0)
    sub(node, "DALINK", index=0)
    if file_off is None:
        # No axis was recovered: label the positions 0..n-1 instead, and do
        # not pretend the labels carry a physical unit.
        scaling = None
        sub(node, "embedinfo", type=0)
        for i in range(count):
            sub(node, "LABEL", index=i, value=str(i))
    else:
        sub(node, "embedinfo", type=1)
    sub(node, "units", "-" if scaling is None else scaling.unit)
    sub(node, "decimalpl", 0 if scaling is None else scaling.decimals())
    add_math(node, scaling)
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

    def categories_for(self, row):
        """FR module first (that is how a calibrator looks for a map), then
        the detector's shape category, then 'Unnamed candidates'."""
        names = []
        module = (row.get("fr_module") or "").strip()
        if module:
            names.append(FR_MODULES.get(module, module))
        kind = row["kind"]
        if kind in KINDS:
            names.append(KINDS[kind][0])
        if not (row.get("name") or row.get("name_or_blank")):
            names.append(UNNAMED_CATEGORY)
        return names

    def add_categories(self, node, row):
        for index, name in enumerate(self.categories_for(row)[:3]):
            sub(node, "CATEGORYMEM", index=str(index),
                category="0x%X" % (self.category(name) + 1))

    def describe(self, row, kind, data_off, xn, yn, signed, bits):
        parts = []
        if row.get("description"):
            parts.append(row["description"].strip().rstrip(".") + ".")
        parts.append("%s at CPU %s (file 0x%06X), %dx%d %s%d."
                     % (kind, row["addr"], data_off, xn, yn,
                        "s" if signed else "u", bits))
        module = (row.get("fr_module") or "").strip()
        if module:
            page = (row.get("fr_page") or "").strip()
            parts.append("FR module %%%s%s." % (module, ", p%s" % page if page else ""))
        if row.get("name") and row.get("name_confidence"):
            parts.append("Name: %s." % row["name_confidence"].upper())
        if row.get("scale_confidence"):
            parts.append("Scaling: %s." % row["scale_confidence"].upper())
        elif not row_scaling(row).identity:
            parts.append("Scaling: UNTAGGED.")
        if row.get("consumer_func"):
            parts.append("Read by %s." % row["consumer_func"])
        if row.get("name_evidence"):
            parts.append("Name evidence: %s." % row["name_evidence"])
        if row.get("evidence"):
            parts.append(row["evidence"])
        return " ".join(parts)

    def table(self, row, name):
        kind = row["kind"]
        bits, signed = elem_bits("%s%d" % ("s" if row["signed"] == "1" else "u",
                                           int(row["elem_size"]) * 8), 8)
        xn = int(row["x_n"] or 1)
        yn = int(row["y_n"] or 1)
        data_off = m.cpu_to_file(int(row["addr"], 16))
        z_scale = row_scaling(row)

        node = sub(self.root, "XDFTABLE", uniqueid=self.next_uid(), flags="0x30")
        sub(node, "title", name)
        sub(node, "description",
            self.describe(row, kind, data_off, xn, yn, signed, bits))
        self.add_categories(node, row)

        xbits, xsigned = elem_bits(row["x_elem"])
        xoff = (m.cpu_to_file(int(row["x_axis_addr"], 16))
                if row["x_axis_addr"] else None)
        axis_element(node, "x", xn, xoff, xbits, xsigned, row_scaling(row, "x_"))

        ybits, ysigned = elem_bits(row["y_elem"])
        yoff = (m.cpu_to_file(int(row["y_axis_addr"], 16))
                if row["y_axis_addr"] else None)
        axis_element(node, "y", yn, yoff, ybits, ysigned, row_scaling(row, "y_"))

        z = sub(node, "XDFAXIS", id="z", uniqueid="0x0")
        embedded(z, data_off, bits, signed, rows=yn, cols=xn)
        sub(z, "units", z_scale.unit)
        sub(z, "indexcount", xn * yn)
        sub(z, "decimalpl", z_scale.decimals())
        raw_lo = -(1 << (bits - 1)) if signed else 0
        raw_hi = (1 << (bits - 1)) - 1 if signed else (1 << bits) - 1
        lo, hi = sorted((z_scale.convert(raw_lo), z_scale.convert(raw_hi)))
        sub(z, "min", fmt_number(lo))
        sub(z, "max", fmt_number(hi))
        sub(z, "outputtype", 1)
        add_math(z, z_scale)
        return node

    def constant(self, row, name):
        bits = int(row["elem_size"]) * 8
        signed = row["signed"] == "1"
        off = m.cpu_to_file(int(row["addr"], 16))
        scaling = row_scaling(row)
        node = sub(self.root, "XDFCONSTANT", uniqueid=self.next_uid(), flags="0x0")
        sub(node, "title", name)
        sub(node, "description",
            self.describe(row, row["kind"], off, 1, 1, signed, bits))
        self.add_categories(node, row)
        embedded(node, off, bits, signed)
        sub(node, "units", scaling.unit)
        sub(node, "decimalpl", scaling.decimals())
        sub(node, "outputtype", 1)
        add_math(node, scaling)
        return node

    def tostring(self):
        raw = ET.tostring(self.root, encoding="utf-8")
        pretty = minidom.parseString(raw).toprettyxml(indent="  ", encoding="UTF-8")
        return b"\n".join(line for line in pretty.split(b"\n") if line.strip())


def object_name(row, used):
    """A stable, unique name.  Real Bosch labels only where a CSV has one."""
    base = row.get("name") or row.get("name_or_blank")
    if not base:
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


# --------------------------------------------------------------------------
# Merging: the hand-maintained sidecar and any --extra-rows files.
# --------------------------------------------------------------------------
def read_csv(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_names(path):
    """addr (upper case, 0x-prefixed) -> the sidecar row."""
    names = {}
    for row in read_csv(path):
        key = (row.get("addr") or "").strip().upper()
        if not key or key.startswith("#"):
            continue
        names[key] = {k: (v or "").strip() for k, v in row.items()}
    return names


def apply_names(rows, names):
    """Merge the sidecar into the draft rows.  Returns (rows, unmatched).

    The sidecar wins over ``name_or_blank``: it is the file a human edits, the
    draft is regenerated from the image by ``enumerate_maps.py``.
    """
    seen = set()
    for row in rows:
        key = row["addr"].strip().upper()
        meta = names.get(key)
        if not meta:
            continue
        seen.add(key)
        for column, value in meta.items():
            if column in ("addr",) or not value:
                continue
            row[column] = value
    return rows, sorted(set(names) - seen)


def merge_extra_rows(rows, extra, source):
    """Add or replace objects from an --extra-rows file.  Returns (added, replaced)."""
    missing = [c for c in DRAFT_COLUMNS if extra and c not in extra[0]]
    if missing:
        raise ValueError("%s is missing the draft columns %s"
                         % (source, ", ".join(missing)))
    index = {row["addr"].strip().upper(): i for i, row in enumerate(rows)}
    added = replaced = 0
    for row in extra:
        row = {k: (v or "").strip() for k, v in row.items() if k is not None}
        key = row["addr"].strip().upper()
        if not key:
            continue
        row.setdefault("evidence", "")
        row["evidence"] = ("%s [%s]" % (row["evidence"], source)).strip()
        if key in index:
            rows[index[key]] = row
            replaced += 1
        else:
            index[key] = len(rows)
            rows.append(row)
            added += 1
    return added, replaced


def build(rows, title, description, want_scalars, min_conf, kinds, limit):
    builder = Builder(title, description)
    used = set()
    counts = {}
    for row in rows:
        kind = row["kind"]
        if kind not in KINDS:
            continue
        named = bool(row.get("name") or row.get("name_or_blank"))
        if kind == "scalar" and not want_scalars and not named:
            continue
        if kinds and kind not in kinds:
            continue
        if CONFIDENCE_ORDER.get(row["confidence"], 9) > min_conf:
            continue
        if not row["elem_size"]:
            continue
        name = object_name(row, used)
        if kind == "scalar":
            builder.constant(row, name)
        else:
            builder.table(row, name)
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
    titles = set()
    for node in tables + constants:
        uid = node.get("uniqueid")
        if uid in uids:
            problems.append("duplicate uniqueid %s" % uid)
        uids.add(uid)
        title = (node.find("title").text or "").strip() if node.find("title") is not None else ""
        if not title:
            problems.append("%s %s has no title" % (node.tag, uid))
        elif title in titles:
            problems.append("duplicate title %s" % title)
        titles.add(title)
        mems = node.findall("CATEGORYMEM")
        if not mems:
            problems.append("%s %s has no CATEGORYMEM" % (node.tag, uid))
        seen_index = set()
        for mem in mems:
            if "0x%X" % (int(mem.get("category"), 16) - 1) not in categories:
                problems.append("%s %s points at an undeclared category" % (node.tag, uid))
            if mem.get("index") in seen_index:
                problems.append("%s %s repeats CATEGORYMEM index %s"
                                % (node.tag, uid, mem.get("index")))
            seen_index.add(mem.get("index"))
    for node in root.iter("MATH"):
        if "X" not in (node.get("equation") or ""):
            problems.append("MATH equation %r does not use X" % node.get("equation"))
        if node.find("VAR") is None:
            problems.append("MATH %r has no VAR" % node.get("equation"))
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
            if axis.find("units") is None:
                problems.append("table %s %s axis has no units"
                                % (table.get("uniqueid"), axis_id))
    for node in constants:
        data = node.find("EMBEDDEDDATA")
        if data is None or data.get("mmedaddress") is None:
            problems.append("constant %s has no address" % node.get("uniqueid"))
    return len(tables), len(constants), problems


SELF_TEST_ROWS = [
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


def self_test():
    """Build a tiny definition from hand-made rows and validate it."""
    rows = [dict(row) for row in SELF_TEST_ROWS]
    names = {"0X5CBF3E": dict(addr="0x5CBF3E", name="KFTEST",
                              name_confidence="hypothesis",
                              name_evidence="self-test", unit="ms", scale="1/1000",
                              offset="", x_unit="1/min", x_scale="0.25",
                              y_unit="%", y_scale="100/4096",
                              scale_confidence="static", fr_module="RKTI",
                              fr_page="1826", description="self test map")}
    rows, unmatched = apply_names(rows, names)
    assert not unmatched, unmatched
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
    # the sidecar must reach the output: name, scaling and FR category
    assert b"<title>KFTEST</title>" in blob, "sidecar name missing"
    assert b'equation="X*0.001"' in blob, "value scaling missing"
    assert b'equation="X*0.25"' in blob, "x axis scaling missing"
    assert b'equation="X*0.0244140625"' in blob, "y axis scaling missing"
    assert b"RKTI - injection time" in blob, "FR category missing"
    print("self-test ok")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv", nargs="?", help="re/calibration_draft.csv")
    parser.add_argument("-o", "--out", help="output .xdf")
    parser.add_argument("--names", help="scaling/name sidecar CSV")
    parser.add_argument("--no-names", action="store_true",
                        help="ignore the sidecar even if it is there")
    parser.add_argument("--extra-rows", action="append", default=[],
                        metavar="FILE",
                        help="extra objects in the draft's column format (repeatable)")
    parser.add_argument("--scalars", action="store_true")
    parser.add_argument("--min-confidence", default="static",
                        choices=("static", "community", "hypothesis"))
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

    rows = read_csv(args.csv)

    names_path = args.names
    if names_path is None and not args.no_names:
        sibling = os.path.join(os.path.dirname(os.path.abspath(args.csv)),
                               "calibration_names.csv")
        names_path = sibling if os.path.isfile(sibling) else None
    named = 0
    if names_path and not args.no_names:
        names = load_names(names_path)
        rows, unmatched = apply_names(rows, names)
        named = len(names) - len(unmatched)
        print("%s: %d names merged, %d unmatched" % (names_path, named, len(unmatched)))
        for key in unmatched[:20]:
            print("  ! %s is not a row of %s" % (key, args.csv))

    for path in args.extra_rows:
        added, replaced = merge_extra_rows(rows, read_csv(path), os.path.basename(path))
        print("%s: %d rows added, %d replaced" % (path, added, replaced))

    description = (
        "Calibration definition for data/passat_azx_ori.bin "
        "(03H906032 / 1037382557, SHA-256 b15590d3...09b3), produced by "
        "ghidra_scripts/enumerate_maps.py + tools/draft_to_xdf.py on %s. "
        "Addresses are file offsets. Scaling comes from "
        "re/calibration_names.csv and is applied only where it was derived "
        "from the firmware; everything else is raw counts (equation X). "
        "Names starting with cand_ are placeholders, not Bosch labels, and "
        "every description carries the confidence of the name and of the "
        "scaling. Run tools/checksum.py fix after saving, and never flash "
        "without tools/checksum.py verify." % datetime.date.today().isoformat())
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
