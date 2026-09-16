"""tools/ram_survey.py, tools/ram_snapshot_diff.py and emu/ext_sram_probe.py.

Brief C2, issue #23 (static half).  The two claims that matter for a patch are
asserted here:

  (a) the survey marks the structures that `re/findings/ram.md` section 4
      names, so a future edit that breaks the scan is caught;
  (b) the recommended block 0x7FFB00-0x7FFBFF carries **no static reference of
      any kind** and lies outside every known dynamic region.
"""
from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from tests.common import DUMP, DumpUnchanged, requires_dump

import med9lib as m  # noqa: E402
import ram_survey as rs  # noqa: E402
import ram_snapshot_diff as rsd  # noqa: E402

PATCH_RAM = 0x7FFB00
PATCH_RAM_SIZE = 0x100
FREE_REGION = (0x7FF770, 0x7FFFEC)          # exclusive end
PROG_COPY = (0x804800, 0x808688)            # exclusive end, before aliasing
PROG_COPY_LEN = 0x3E88                      # 0x085888 - 0x081A00


def build_survey():
    data = m.load_dump(str(DUMP))
    s = rs.Survey()
    rs.scan_r13(data, s)
    rs.scan_absolute(data, s)
    rs.scan_pointers(data, s)
    rs.scan_init(s)
    rs.scan_measuring_vars(s, str(Path(DUMP).parent.parent / "re" / "measuring_vars.csv"))
    return data, s


@requires_dump
class TestRamSurvey(DumpUnchanged):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.data, cls.s = build_survey()

    # -- (a) the known structures are marked ------------------------------
    def test_known_table_is_sane(self):
        for k in rs.KNOWN:
            self.assertLess(k.start, k.end, k.name)
            self.assertGreaterEqual(k.start, rs.RAM_LO, k.name)
            self.assertLessEqual(k.end, rs.RAM_HI, k.name)
            self.assertTrue(k.source.strip(), f"{k.name} has no source")
            self.assertIn(k.tag, ("VERIFIED-STATIC", "VERIFIED-DYNAMIC", "HYPOTHESIS"))

    def test_named_structures_are_found_at_their_addresses(self):
        cases = {
            0x7F8012: "ext_sram_size_code",
            0x7F802C: "crt0_bss_clear_1",
            0x7F9E3C: "kwp_protected_window",
            0x7F9F80: "eep_mirror",              # block 8's payload, eeprom.md 5
            0x7FE5FC: "os_task_activation_flags",
            0x7FF400: "os_task_stack",
            0x803EE8: "can_rx_shadow",           # slot 0 data, can.md 4
            0x804800: "kwp_prog_copy_dest",
            0x800100: "kwp_prog_copy_alias_tail",
        }
        for addr, name in cases.items():
            names = [k.name for k in rs.known_at(addr)]
            self.assertIn(name, names, f"0x{addr:06X} -> {names}")

    def test_can_rx_shadow_covers_all_22_slots(self):
        k = next(k for k in rs.KNOWN if k.name == "can_rx_shadow")
        self.assertEqual(k.end - k.start, 22 * 12)
        self.assertEqual(k.start + 12 * 21 + 4, 0x803FE4)   # slot 21 data, can.md 4

    def test_protected_window_matches_kwp_findings(self):
        k = next(k for k in rs.KNOWN if k.name == "kwp_protected_window")
        self.assertEqual((k.start, k.end - 1), (0x7F9E3C, 0x7FA47F))

    # -- the cold-start fills ---------------------------------------------
    def test_external_sram_is_cleared_at_cold_start(self):
        """The correction to re/findings/eeprom.md section 6."""
        covered = set()
        for lo, hi, _why in rs.COLDSTART_FILLS:
            covered.update(range(lo, hi))
        for a in (0x800004, 0x800D08, 0x803620, 0x80498F):
            self.assertIn(a, covered, f"0x{a:06X} should be cleared at cold start")
        for a in (0x800000, 0x800003, 0x804990, 0x807FFF):
            self.assertNotIn(a, covered, f"0x{a:06X} should NOT be cleared")

    def test_stack_is_filled_and_inside_the_descriptor(self):
        rng = [r for r in rs.COLDSTART_FILLS if r[0] == 0x7FF3C0]
        self.assertEqual(len(rng), 1)
        self.assertEqual(rng[0][1], 0x7FF76C)
        self.assertEqual(rs.STACK_TOP_APP, 0x7FF770)
        self.assertEqual(rs.STACK_LIMIT, 0x7FF3C0)

    # -- (b) the recommended block is clean --------------------------------
    def test_recommended_block_has_no_static_reference(self):
        s = self.s
        for a in range(PATCH_RAM, PATCH_RAM + PATCH_RAM_SIZE):
            i = a - rs.RAM_LO
            for arr, what in ((s.read, "r13 read"), (s.write, "r13 write"),
                              (s.addr, "r13 addi / measuring var"),
                              (s.aread, "absolute read"),
                              (s.awrite, "absolute write"),
                              (s.aaddr, "absolute addi"),
                              (s.ptr, "pointer word"),
                              (s.ptrcal, "calibration pointer word"),
                              (s.init, "cold-start fill")):
                self.assertEqual(arr[i], 0,
                                 f"0x{a:06X} has a {what} reference")

    def test_recommended_block_is_inside_the_free_region(self):
        self.assertGreaterEqual(PATCH_RAM, FREE_REGION[0])
        self.assertLessEqual(PATCH_RAM + PATCH_RAM_SIZE, FREE_REGION[1])
        k = next(k for k in rs.KNOWN if k.name == "free_above_stack")
        self.assertEqual((k.start, k.end), FREE_REGION)
        self.assertTrue(k.candidate)

    def test_recommended_block_avoids_every_blocking_structure(self):
        for a in range(PATCH_RAM, PATCH_RAM + PATCH_RAM_SIZE, 4):
            blocking = rs.known_at(a, blocking_only=True)
            self.assertEqual(blocking, [], f"0x{a:06X} is inside {blocking}")

    def test_recommended_block_is_outside_the_programming_copy(self):
        lo, hi = PROG_COPY
        alias_hi = 0x800000 + (hi - 0x808000)
        for a in (PATCH_RAM, PATCH_RAM + PATCH_RAM_SIZE - 1):
            self.assertFalse(lo <= a < hi)
            self.assertFalse(0x800000 <= a < alias_hi)

    def test_c1_placeholder_is_rejected(self):
        """0x807F00 (C1's placeholder) is inside the programming copy."""
        lo, hi = PROG_COPY
        self.assertTrue(lo <= 0x807F00 < hi)
        self.assertTrue(lo <= 0x807F00 + 0x40 <= hi)

    def test_free_region_shows_up_as_the_longest_run(self):
        runs = rs.free_runs(self.s, 0x40)
        longest = max(runs, key=lambda r: r[1] - r[0])
        self.assertGreaterEqual(longest[0], FREE_REGION[0])
        self.assertLessEqual(longest[1], FREE_REGION[1])
        self.assertGreater(longest[1] - longest[0], 0x700)

    # -- the indexed-region hunt -------------------------------------------
    def test_programming_copy_extent_is_recovered(self):
        ex = rs.hunt_extent(self.data, 0x804800, 0x08A14C)
        self.assertGreater(ex.size, PROG_COPY_LEN - 0x20)
        self.assertLessEqual(ex.size, PROG_COPY_LEN)
        self.assertTrue(ex.loop_bytes)

    def test_indexed_array_at_804548_covers_its_gap(self):
        """The 387-byte 'free run' after 0x804548 is that array's body."""
        ex = rs.hunt_extent(self.data, 0x804548, 0x05E4C4)
        self.assertTrue(ex.indexed or ex.size >= 0x10)
        self.assertEqual(rs.neighbour_bound(self.s, 0x804548), 0x184)

    # -- the scans themselves ----------------------------------------------
    def test_scan_counts(self):
        self.assertEqual(rs.CODE_REGIONS[0], (0x000000, 0x1C0000),
                         "the calibration block must stay out of the "
                         "instruction scan")
        self.assertGreater(sum(self.s.read), 0)
        self.assertGreater(sum(self.s.write), 0)

    def test_csv_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "ram_map.csv"
            rc = rs.main([str(DUMP), "--csv", str(out), "--quiet"])
            self.assertEqual(rc, 0)
            with out.open(encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(len(rows), 0x10000 // 32)
            self.assertEqual(rows[0]["start"], "0x7F8000")
            line = next(r for r in rows
                        if int(r["start"], 16) == PATCH_RAM)
            self.assertEqual(line["free_candidate"], "1")
            for col in ("r13_read", "r13_write", "abs_read", "abs_write",
                        "ptr_words", "ptr_cal", "init_bytes"):
                self.assertEqual(line[col], "0", col)


class TestRamSnapshotDiff(unittest.TestCase):
    def test_self_test_classification(self):
        snaps = rsd.synthetic()
        verdict = rsd.classify(snaps)
        self.assertEqual(len(verdict), 0x100)
        counts = {k: sum(1 for v in verdict.values() if v == k)
                  for k in ("changed", "constant", "blank")}
        self.assertEqual(counts, {"changed": 0x80, "constant": 0x40, "blank": 0x40})

    def test_blank_run_is_the_expected_one(self):
        verdict = rsd.classify(rsd.synthetic())
        self.assertEqual(rsd.runs(verdict, "blank", 16),
                         [(0x800080, 0x8000BF)])
        self.assertEqual(rsd.runs(verdict, "constant", 16),
                         [(0x800040, 0x80007F)])

    def test_cli_self_test(self):
        self.assertEqual(rsd.main(["--self-test"]), 0)

    def test_file_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            paths = []
            for snap in rsd.synthetic():
                p = Path(td) / f"{snap.name}.json"
                data = bytes(snap.bytes[a] for a in sorted(snap.bytes))
                p.write_text(json.dumps({
                    "session": snap.name,
                    "ranges": [{"start": "0x800000", "end": "0x8000FF",
                                "data": data.hex()}]}), encoding="utf-8")
                paths.append(str(p))
            self.assertEqual(rsd.main(paths + ["--free", "32"]), 0)

    def test_length_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            rsd.Snapshot("bad", {"ranges": [
                {"start": "0x800000", "end": "0x8000FF", "data": "0011"}]})

    def test_overlapping_ranges_are_rejected(self):
        with self.assertRaises(ValueError):
            rsd.Snapshot("bad", {"ranges": [
                {"start": "0x800000", "end": "0x800001", "data": "0000"},
                {"start": "0x800001", "end": "0x800002", "data": "0000"}]})


@requires_dump
class TestSnapshotRangeFile(DumpUnchanged):
    """logging/sessions/ram_snapshot.json is what C3's logger consumes."""

    def setUp(self):
        path = Path(DUMP).resolve().parent.parent / "logging" / "sessions" / "ram_snapshot.json"
        self.doc = json.loads(path.read_text(encoding="utf-8"))

    def test_ranges_are_sorted_and_consistent(self):
        prev = None
        for r in self.doc["ranges"]:
            lo, hi = int(r["start"], 16), int(r["end"], 16)
            self.assertEqual(hi - lo + 1, r["bytes"])
            self.assertTrue(prev is None or lo > prev)
            prev = hi

    def test_protected_window_is_excluded(self):
        for r in self.doc["ranges"]:
            lo, hi = int(r["start"], 16), int(r["end"], 16)
            self.assertFalse(lo <= 0x7FA47F and hi >= 0x7F9E3C,
                             f"{r['start']}-{r['end']} overlaps the NRC 0x31 window")

    def test_recommended_block_is_covered(self):
        covered = set()
        for r in self.doc["ranges"]:
            covered.update(range(int(r["start"], 16), int(r["end"], 16) + 1))
        for a in range(PATCH_RAM, PATCH_RAM + PATCH_RAM_SIZE):
            self.assertIn(a, covered)


@requires_dump
class TestExtSramProbe(DumpUnchanged):
    """emu/ext_sram_probe.py -- task 3 of brief C2."""

    def setUp(self):
        try:
            from emu.ext_sram_probe import run_model  # noqa: F401
        except ImportError as exc:                      # pragma: no cover
            self.skipTest(f"unicorn not available: {exc}")

    def test_non_aliased_part_reports_64_kb(self):
        from emu.ext_sram_probe import SENTINEL, run_model
        res, size_byte, preserved, _touched = run_model(str(DUMP), aliased=False)
        self.assertEqual(res.stop_reason, "returned")
        self.assertEqual(size_byte, 0x44)
        self.assertEqual(preserved, SENTINEL)

    def test_aliased_part_reports_32_kb(self):
        from emu.ext_sram_probe import SENTINEL, run_model
        res, size_byte, preserved, _touched = run_model(str(DUMP), aliased=True)
        self.assertEqual(res.stop_reason, "returned")
        self.assertEqual(size_byte, 0x41)
        self.assertEqual(preserved, SENTINEL)


if __name__ == "__main__":
    unittest.main()
