"""tools/draft_to_xdf.py: the generated definition must be well-formed, must
use *file* offsets and must describe the Bosch value layout val[iy*nx+ix]
(issue #19, brief B5); and the physical scaling, the FR categories and the
--extra-rows merge of re/calibration_names.csv must reach the output (issue
#41, brief D3)."""
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
NAMES = REPO / "re" / "calibration_names.csv"

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


def rows_copy():
    return [dict(r) for r in ROWS]


NAME_ROW = dict(addr="0x5CBF3E", name="KFZWTEST", name_confidence="hypothesis",
                name_evidence="unit test", unit="degCA", scale="0.75", offset="",
                x_unit="1/min", x_scale="0.25", x_offset="",
                y_unit="%", y_scale="100/4096", y_offset="",
                scale_confidence="static", fr_module="ZWGRU", fr_page="3085",
                description="unit test map")


class TestScaling(unittest.TestCase):
    def test_parse_number_takes_fractions(self):
        self.assertEqual(x.parse_number("100/4096"), 100.0 / 4096)
        self.assertEqual(x.parse_number("0.75"), 0.75)
        self.assertEqual(x.parse_number("-48"), -48.0)
        self.assertIsNone(x.parse_number(""))
        self.assertEqual(x.parse_number("", 1.0), 1.0)

    def test_equation_forms(self):
        self.assertEqual(x.equation(1.0, 0.0), "X")
        self.assertEqual(x.equation(0.75, 0.0), "X*0.75")
        self.assertEqual(x.equation(1.0, -48.0), "X-48")
        self.assertEqual(x.equation(0.75, -48.0), "X*0.75-48")
        self.assertEqual(x.equation(100.0 / 4096, 0.0), "X*0.0244140625")

    def test_value_and_both_axes_carry_the_sidecar_scaling(self):
        rows, unmatched = x.apply_names(rows_copy(), {"0X5CBF3E": dict(NAME_ROW)})
        self.assertEqual(unmatched, [])
        blob, _ = x.build(rows, "t", "t", True, 2, None, 0)[0].tostring(), None
        table = ET.fromstring(blob).find("XDFTABLE")
        self.assertEqual(table.find("title").text, "KFZWTEST")
        eq = {a.get("id"): a.find("MATH").get("equation")
              for a in table.findall("XDFAXIS")}
        self.assertEqual(eq["z"], "X*0.75")
        self.assertEqual(eq["x"], "X*0.25")
        self.assertEqual(eq["y"], "X*0.0244140625")
        units = {a.get("id"): a.find("units").text for a in table.findall("XDFAXIS")}
        self.assertEqual(units, {"x": "1/min", "y": "%", "z": "degCA"})

    def test_min_and_max_are_physical_not_raw(self):
        """A signed 8-bit map at 0.75 degCA/LSB runs -96 .. +95.25, not -128..127."""
        rows = rows_copy()
        rows[0]["signed"] = "1"
        rows[0]["elem_size"] = "1"
        rows, _ = x.apply_names(rows, {"0X5CBF3E": dict(NAME_ROW)})
        blob = x.build(rows, "t", "t", True, 2, None, 0)[0].tostring()
        z = ET.fromstring(blob).find("XDFTABLE/XDFAXIS[@id='z']")
        self.assertEqual(z.find("min").text, "-96")
        self.assertEqual(z.find("max").text, "95.25")

    def test_offset_reaches_the_equation(self):
        row = dict(NAME_ROW, unit="degC", scale="0.75", offset="-48")
        rows, _ = x.apply_names(rows_copy(), {"0X5CBF3E": row})
        blob = x.build(rows, "t", "t", True, 2, None, 0)[0].tostring()
        z = ET.fromstring(blob).find("XDFTABLE/XDFAXIS[@id='z']")
        self.assertEqual(z.find("MATH").get("equation"), "X*0.75-48")
        # the row is u16, so the physical range is 0*0.75-48 .. 65535*0.75-48
        self.assertEqual(z.find("min").text, "-48")
        self.assertEqual(z.find("max").text, "49103.25")

    def test_unnamed_objects_keep_the_identity_conversion(self):
        blob, _ = build()
        for node in ET.fromstring(blob).iter("MATH"):
            self.assertEqual(node.get("equation"), "X")

    def test_an_axis_without_an_address_is_not_given_a_unit(self):
        """A map_2d_data row with no axes gets index labels; a unit there would
        be a claim about numbers that are not in the image."""
        row = dict(addr="0x5D0000", kind="map_2d_data", x_axis_addr="",
                   y_axis_addr="", x_n="6", y_n="4", elem_size="1", signed="0",
                   consumer_func="t", name_or_blank="", confidence="static",
                   evidence="unit test", x_elem="", y_elem="", struct_addr="",
                   sites="1")
        rows, _ = x.apply_names([row], {"0X5D0000": dict(NAME_ROW, addr="0x5D0000")})
        blob = x.build(rows, "t", "t", True, 2, None, 0)[0].tostring()
        table = ET.fromstring(blob).find("XDFTABLE")
        for axis_id in ("x", "y"):
            axis = table.find("XDFAXIS[@id='%s']" % axis_id)
            self.assertEqual(axis.find("units").text, "-")
            self.assertEqual(axis.find("MATH").get("equation"), "X")
        self.assertEqual(table.find("XDFAXIS[@id='z']/units").text, "degCA")

    def test_decimal_places_follow_the_step(self):
        self.assertEqual(x.decimals(1), 0)
        self.assertEqual(x.decimals(40), 0)
        self.assertEqual(x.decimals(0.75), 2)
        self.assertEqual(x.decimals(0.25), 2)
        self.assertEqual(x.decimals(0.005), 3)
        self.assertEqual(x.decimals(1.0 / 1024), 3)


class TestSidecarMerge(unittest.TestCase):
    def test_sidecar_may_correct_a_shape_the_detector_could_not_read(self):
        """The KFPRSOL* case: enumerate_maps.py leaves x_n/y_n empty."""
        row = dict(addr="0x5D5324", kind="map_2d_data", x_axis_addr="",
                   y_axis_addr="", x_n="", y_n="", elem_size="2", signed="0",
                   consumer_func="t", name_or_blank="", confidence="static",
                   evidence="unit test", x_elem="", y_elem="", struct_addr="",
                   sites="2")
        meta = dict(addr="0x5D5324", name="KFPRSOLHOM", name_confidence="hypothesis",
                    name_evidence="unit test", unit="bar", scale="0.005",
                    x_axis_addr="0x5D557A", x_n="8", x_elem="u16",
                    y_axis_addr="0x5D558C", y_n="8", y_elem="u16")
        rows, _ = x.apply_names([row], {"0X5D5324": meta})
        blob = x.build(rows, "t", "t", False, 2, None, 0)[0].tostring()
        z = ET.fromstring(blob).find("XDFTABLE/XDFAXIS[@id='z']/EMBEDDEDDATA")
        self.assertEqual((z.get("mmedcolcount"), z.get("mmedrowcount")), ("8", "8"))
        self.assertEqual(x.validate(blob)[2], [])

    def test_unmatched_sidecar_rows_are_reported(self):
        _, unmatched = x.apply_names(rows_copy(), {"0X5FFFFE": dict(NAME_ROW,
                                                                   addr="0x5FFFFE")})
        self.assertEqual(unmatched, ["0X5FFFFE"])

    def test_a_named_scalar_is_emitted_without_the_scalars_flag(self):
        rows, _ = x.apply_names(rows_copy(),
                                {"0X5C8000": dict(NAME_ROW, addr="0x5C8000",
                                                  name="TIMINP", unit="us",
                                                  scale="1", x_unit="", x_scale="",
                                                  y_unit="", y_scale="")})
        _, counts = x.build(rows, "t", "t", False, 2, None, 0)
        self.assertEqual(counts.get("scalar"), 1)
        _, counts = x.build(rows_copy(), "t", "t", False, 2, None, 0)
        self.assertIsNone(counts.get("scalar"))


class TestCategories(unittest.TestCase):
    def test_fr_module_becomes_the_first_category(self):
        rows, _ = x.apply_names(rows_copy(), {"0X5CBF3E": dict(NAME_ROW)})
        builder, _ = x.build(rows, "t", "t", True, 2, None, 0)
        blob = builder.tostring()
        root = ET.fromstring(blob)
        cats = {c.get("index"): c.get("name") for c in root.iter("CATEGORY")}
        table = root.find("XDFTABLE")
        mems = table.findall("CATEGORYMEM")
        first = cats["0x%X" % (int(mems[0].get("category"), 16) - 1)]
        self.assertEqual(first, x.FR_MODULES["ZWGRU"])
        second = cats["0x%X" % (int(mems[1].get("category"), 16) - 1)]
        self.assertEqual(second, x.KINDS["map_2d"][0])

    def test_an_unnamed_object_lands_in_the_candidate_category(self):
        builder, _ = x.build(rows_copy(), "t", "t", True, 2, None, 0)
        blob = builder.tostring()
        root = ET.fromstring(blob)
        cats = {c.get("index"): c.get("name") for c in root.iter("CATEGORY")}
        names = [cats["0x%X" % (int(m.get("category"), 16) - 1)]
                 for m in root.find("XDFTABLE").findall("CATEGORYMEM")]
        self.assertIn(x.UNNAMED_CATEGORY, names)

    def test_every_fr_module_of_the_sidecar_has_a_readable_category(self):
        if not NAMES.is_file():
            self.skipTest("re/calibration_names.csv missing")
        with NAMES.open(newline="", encoding="utf-8") as handle:
            modules = {(r["fr_module"] or "").strip() for r in csv.DictReader(handle)}
        for module in sorted(modules - {""}):
            self.assertIn(module, x.FR_MODULES,
                          "FR module %s has no category title" % module)


class TestExtraRows(unittest.TestCase):
    """Brief D1 delivers its FFCAL001 descriptor rows separately; the
    integrator merges them at merge time with --extra-rows."""

    EXTRA = dict(addr="0x5E2510", kind="curve_1d", x_axis_addr="", y_axis_addr="",
                 x_n="17", y_n="", elem_size="2", signed="0",
                 consumer_func="ff_fuel_factor", name_or_blank="ff_F_curve",
                 confidence="static", evidence="FFCAL001", x_elem="", y_elem="",
                 struct_addr="", sites="1")

    def test_extra_rows_are_appended_and_tagged(self):
        rows = rows_copy()
        added, replaced = x.merge_extra_rows(rows, [dict(self.EXTRA)], "ffcal001_rows.csv")
        self.assertEqual((added, replaced), (1, 0))
        self.assertEqual(len(rows), len(ROWS) + 1)
        self.assertIn("[ffcal001_rows.csv]", rows[-1]["evidence"])
        blob = x.build(rows, "t", "t", False, 2, None, 0)[0].tostring()
        titles = [t.text for t in ET.fromstring(blob).iter("title")]
        self.assertIn("ff_F_curve", titles)
        self.assertEqual(x.validate(blob)[2], [])

    def test_an_extra_row_replaces_a_draft_row_with_the_same_address(self):
        rows = rows_copy()
        added, replaced = x.merge_extra_rows(
            rows, [dict(self.EXTRA, addr="0x5CBF3E", x_n="3", y_n="",
                        kind="curve_1d", name_or_blank="replaced")], "f.csv")
        self.assertEqual((added, replaced), (0, 1))
        self.assertEqual(len(rows), len(ROWS))
        self.assertEqual(rows[0]["name_or_blank"], "replaced")

    def test_a_file_missing_the_draft_columns_is_rejected(self):
        with self.assertRaises(ValueError) as caught:
            x.merge_extra_rows(rows_copy(), [{"addr": "0x5E2510"}], "bad.csv")
        self.assertIn("x_n", str(caught.exception))

    def test_extra_rows_may_carry_sidecar_columns(self):
        rows = rows_copy()
        x.merge_extra_rows(rows, [dict(self.EXTRA, unit="-", scale="1/1024",
                                       fr_module="FFCAL")], "f.csv")
        blob = x.build(rows, "t", "t", False, 2, None, 0)[0].tostring()
        table = [t for t in ET.fromstring(blob).findall("XDFTABLE")
                 if t.find("title").text == "ff_F_curve"][0]
        self.assertEqual(table.find("XDFAXIS[@id='z']/MATH").get("equation"),
                         "X*0.0009765625")


class TestValidation(unittest.TestCase):
    def test_a_math_block_without_x_is_a_problem(self):
        blob = build()[0].replace(b'equation="X"', b'equation="42"', 1)
        self.assertTrue(any("does not use X" in p for p in x.validate(blob)[2]))

    def test_a_duplicate_title_is_a_problem(self):
        rows = rows_copy() + [dict(rows_copy()[0])]
        blob = x.build(rows, "t", "t", False, 2, None, 0)[0].tostring()
        # object_name makes them unique, so force a clash to prove the check works
        blob = blob.replace(b"cand_KF_5CBF3E_2", b"cand_KF_5CBF3E")
        self.assertTrue(any("duplicate title" in p for p in x.validate(blob)[2]))

    def test_an_undeclared_category_is_a_problem(self):
        blob = build()[0].replace(b'<CATEGORYMEM index="0" category="0x1"/>',
                                  b'<CATEGORYMEM index="0" category="0xFF"/>', 1)
        self.assertTrue(any("undeclared category" in p for p in x.validate(blob)[2]))

    def test_a_table_that_runs_past_the_end_of_the_file_is_a_problem(self):
        rows = rows_copy()
        rows[0]["addr"] = "0x47FFF0"      # the last page of the on-chip flash
        blob = x.build(rows, "t", "t", False, 2, None, 0)[0].tostring()
        self.assertTrue(any("past the end" in p for p in x.validate(blob)[2]))


class TestCommittedSidecar(DumpUnchanged):
    """re/calibration_names.csv must stay consistent with the draft."""

    @unittest.skipUnless(NAMES.is_file() and DRAFT.is_file(), "CSV missing")
    def test_every_sidecar_row_is_a_row_of_the_draft(self):
        with DRAFT.open(newline="", encoding="utf-8") as handle:
            draft = {r["addr"].upper(): r for r in csv.DictReader(handle)}
        with NAMES.open(newline="", encoding="utf-8") as handle:
            names = list(csv.DictReader(handle))
        self.assertGreater(len(names), 100)
        seen = set()
        for row in names:
            addr = row["addr"].upper()
            self.assertIn(addr, draft, "%s is not a row of the draft" % addr)
            self.assertNotIn(addr, seen, "%s appears twice" % addr)
            seen.add(addr)
            self.assertTrue(row["name"], "%s has no name" % addr)
            self.assertIn(row["name_confidence"], x.CONFIDENCE_ORDER)
            self.assertTrue(row["name_evidence"], "%s has no evidence" % addr)
            if row["scale"] or row["x_scale"] or row["y_scale"]:
                self.assertIn(row["scale_confidence"], x.CONFIDENCE_ORDER,
                              "%s scales without a scale_confidence" % addr)
            for key in ("scale", "offset", "x_scale", "x_offset", "y_scale",
                        "y_offset"):
                if row[key]:
                    self.assertIsInstance(x.parse_number(row[key]), float)
            # a sidecar name must never silently contradict the draft
            if draft[addr]["name_or_blank"]:
                self.assertEqual(row["name"], draft[addr]["name_or_blank"],
                                 "%s: sidecar and draft disagree on the name" % addr)

    @unittest.skipUnless(NAMES.is_file() and DRAFT.is_file(), "CSV missing")
    def test_the_committed_pair_builds_and_validates(self):
        with DRAFT.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        rows, unmatched = x.apply_names(rows, x.load_names(str(NAMES)))
        self.assertEqual(unmatched, [])
        builder, counts = x.build(rows, "t", "t", False, 2, None, 0)
        blob = builder.tostring()
        tables, constants, problems = x.validate(blob)
        self.assertEqual(problems, [])
        self.assertGreater(tables, 1000)
        self.assertGreater(constants, 20)      # the named scalars
        root = ET.fromstring(blob)
        titles = [t.text for t in root.iter("title")]
        for name in ("KFZW", "KFPRSOLHOM", "KFWKSTT", "cand_KFZWMN", "NMAXDV"):
            self.assertIn(name, titles)
        # scaling really reaches the committed maps
        kfzw = [t for t in root.findall("XDFTABLE")
                if t.find("title").text == "KFZW"][0]
        self.assertEqual(kfzw.find("XDFAXIS[@id='z']/MATH").get("equation"), "X*0.75")
        self.assertEqual(kfzw.find("XDFAXIS[@id='y']/MATH").get("equation"), "X*0.25")

    @unittest.skipUnless(NAMES.is_file(), "CSV missing")
    def test_main_merges_the_sidecar_automatically(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "d.xdf"
            self.assertEqual(x.main([str(DRAFT), "-o", str(out),
                                     "--min-confidence", "hypothesis"]), 0)
            blob = out.read_bytes()
            self.assertIn(b"<title>KFZW</title>", blob)
            self.assertIn(b'equation="X*0.75"', blob)
            out2 = Path(tmp) / "raw.xdf"
            self.assertEqual(x.main([str(DRAFT), "-o", str(out2), "--no-names",
                                     "--min-confidence", "hypothesis"]), 0)
            self.assertNotIn(b'equation="X*0.75"', out2.read_bytes())


if __name__ == "__main__":
    unittest.main()
