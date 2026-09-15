"""tools/draft_to_xdf.py: the generated definition must be well-formed, must
use *file* offsets and must describe the Bosch value layout val[iy*nx+ix]
(issue #19, brief B5)."""
from __future__ import annotations

import csv
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from tests.common import REPO, DumpUnchanged  # noqa: F401  (REPO puts tools/ on sys.path)

import draft_to_xdf as x  # noqa: E402
import med9lib as m  # noqa: E402

DRAFT = REPO / "re" / "calibration_draft.csv"

# One 14 x 10 u16 map whose header, axes and data were read out of the image,
# and one signed 8-bit curve with a shared axis.
ROWS = [
    dict(addr="0x5CBF3E", kind="map_2d", x_axis_addr="0x5CBF22",
         y_axis_addr="0x5CBF0A", x_n="14", y_n="10", elem_size="2", signed="0",
         consumer_func="FUN_000cd054@0x0CD054", name_or_blank="",
         confidence="static", evidence="unit test", x_elem="u16", y_elem="u16",
         struct_addr="0x5CBF08", sites="5"),
    dict(addr="0x5CCC3A", kind="curve_1d_shared", x_axis_addr="0x5CCC2F",
         y_axis_addr="", x_n="11", y_n="", elem_size="1", signed="1",
         consumer_func="FUN_000be9ec@0x0BE9EC", name_or_blank="",
         confidence="static", evidence="unit test", x_elem="u8", y_elem="",
         struct_addr="", sites="1"),
    dict(addr="0x5C8000", kind="scalar", x_axis_addr="", y_axis_addr="",
         x_n="1", y_n="", elem_size="2", signed="0",
         consumer_func="FUN_00000000@0x000000", name_or_blank="",
         confidence="static", evidence="unit test", x_elem="", y_elem="",
         struct_addr="", sites="1"),
]


def build(rows=ROWS, scalars=True, min_conf=2):
    builder, counts = x.build(rows, "t", "t", scalars, min_conf, None, 0)
    return builder.tostring(), counts


class TestStructure(unittest.TestCase):
    def test_self_test_passes(self):
        self.assertEqual(x.self_test(), 0)

    def test_validator_accepts_generated_output(self):
        blob, _ = build()
        tables, constants, problems = x.validate(blob)
        self.assertEqual((tables, constants), (2, 1))
        self.assertEqual(problems, [])

    def test_addresses_are_file_offsets(self):
        blob, _ = build()
        root = ET.fromstring(blob)
        table = root.find("XDFTABLE")
        z = table.find("XDFAXIS[@id='z']/EMBEDDEDDATA")
        self.assertEqual(int(z.get("mmedaddress"), 16),
                         m.cpu_to_file(0x5CBF3E))
        self.assertEqual(root.find("XDFHEADER/baseoffset").text, "0")

    def test_rows_and_columns_follow_the_bosch_layout(self):
        """val[iy * nx + ix]: nx columns, ny rows, packed row major."""
        blob, _ = build()
        z = ET.fromstring(blob).find("XDFTABLE/XDFAXIS[@id='z']/EMBEDDEDDATA")
        self.assertEqual(z.get("mmedcolcount"), "14")     # x_n
        self.assertEqual(z.get("mmedrowcount"), "10")     # y_n
        self.assertEqual(z.get("mmedmajorstridebits"), "0")
        self.assertEqual(z.get("mmedminorstridebits"), "0")

    def test_signedness_travels_in_mmedtypeflags(self):
        blob, _ = build()
        tables = ET.fromstring(blob).findall("XDFTABLE")
        unsigned = tables[0].find("XDFAXIS[@id='z']/EMBEDDEDDATA")
        signed = tables[1].find("XDFAXIS[@id='z']/EMBEDDEDDATA")
        self.assertEqual(int(unsigned.get("mmedtypeflags"), 16) & 1, 0)
        self.assertEqual(int(signed.get("mmedtypeflags"), 16) & 1, 1)
        # big endian: the LSB-first bit must never be set
        for node in ET.fromstring(blob).iter("EMBEDDEDDATA"):
            self.assertEqual(int(node.get("mmedtypeflags"), 16) & 2, 0)

    def test_one_dimensional_curve_becomes_a_single_row_table(self):
        blob, _ = build()
        z = ET.fromstring(blob).findall("XDFTABLE")[1].find(
            "XDFAXIS[@id='z']/EMBEDDEDDATA")
        self.assertEqual(z.get("mmedrowcount"), "1")
        self.assertEqual(z.get("mmedcolcount"), "11")

    def test_confidence_filter(self):
        rows = [dict(ROWS[0], confidence="hypothesis")]
        _, counts = x.build(rows, "t", "t", False, 0, None, 0)
        self.assertEqual(counts, {})
        _, counts = x.build(rows, "t", "t", False, 2, None, 0)
        self.assertEqual(counts, {"map_2d": 1})

    def test_names_are_unique(self):
        blob, _ = build(ROWS + [dict(ROWS[0])])
        titles = [t.text for t in ET.fromstring(blob).iter("title")]
        self.assertEqual(len(titles), 4)
        self.assertEqual(len(titles), len(set(titles)))


class TestAgainstTheCommittedDraft(DumpUnchanged):
    """The draft CSV in the repository must still produce a valid definition,
    and every table in it must lie inside the dump."""

    @unittest.skipUnless(DRAFT.is_file(), "re/calibration_draft.csv missing")
    def test_committed_draft_builds_and_validates(self):
        with DRAFT.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertGreater(len(rows), 500)
        builder, counts = x.build(rows, "t", "t", False, 2, None, 0)
        blob = builder.tostring()
        tables, constants, problems = x.validate(blob)
        self.assertEqual(problems, [])
        self.assertGreater(tables, 500)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "d.xdf"
            path.write_bytes(blob)
            self.assertEqual(x.main([str(DRAFT), "-o", str(path),
                                     "--min-confidence", "hypothesis"]), 0)

    @unittest.skipUnless(DRAFT.is_file(), "re/calibration_draft.csv missing")
    def test_every_address_maps_through_med9lib(self):
        with DRAFT.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                for key in ("addr", "x_axis_addr", "y_axis_addr", "struct_addr"):
                    if row[key]:
                        off = m.cpu_to_file(int(row[key], 16))
                        self.assertLess(off, m.DUMP_SIZE)


if __name__ == "__main__":
    unittest.main()
