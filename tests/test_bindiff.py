"""tools/bindiff.py: a patched image must contain only the patch and the
checksum descriptors it touched (issue #24 exit criterion)."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tests.common import DUMP, DumpUnchanged, requires_dump

import bindiff  # noqa: E402  (tests.common put tools/ on sys.path)
import checksum  # noqa: E402
import med9lib as m  # noqa: E402

# Three deliberate edits, one per address space the tool has to map:
#   0x0A7B10  code, low alias, inside checksum block 0x0A0000-0x0A7FFF
#   0x150000  free flash (0xFF), inside checksum block 0x150000-0x15FFFF
#   0x5E2600  calibration through the HIGH alias (file 0x1E2600),
#             inside checksum block 0x5E0000-0x5EFFFF
EDITS = [
    (0x0A7B10, bytes.fromhex("60000000")),
    (0x150000, bytes.fromhex("deadbeefcafe")),
    (0x5E2600, bytes.fromhex("0102")),
]


def build_patched(tmp: Path) -> tuple[Path, Path]:
    """Write a patched copy of the dump plus the matching patch.json."""
    data = m.load_dump(str(DUMP))
    changes = []
    for addr, new in EDITS:
        off = m.cpu_to_file(addr)
        old = bytes(data[off:off + len(new)])
        assert old != new, f"test edit at {addr:#x} is a no-op"
        data[off:off + len(new)] = new
        changes.append({"addr": f"{addr:#08x}", "old": old.hex(), "new": new.hex(),
                        "why": "bindiff self-test"})
    with contextlib.redirect_stdout(io.StringIO()):
        checksum.fix(data)                  # descriptors follow the edits
    out = tmp / "patched.bin"
    out.write_bytes(bytes(data))
    pj = tmp / "patch.json"
    pj.write_text(json.dumps({
        "name": "bindiff_selftest",
        "base_sha256": "b15590d3f1874ace3125c5d047c09a686db9b8bb498187663539ebab205609b3",
        "changes": changes,
    }, indent=2))
    return out, pj


@requires_dump
class TestBindiff(DumpUnchanged):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.patched, self.patch_json = build_patched(self.tmp)

    def tearDown(self):
        self._tmp.cleanup()

    def test_original_against_itself_is_empty(self):
        a = m.load_dump(str(DUMP))
        ranges, report = bindiff.diff(a, a, None)
        self.assertEqual(ranges, [])
        self.assertTrue(report["ok"])
        self.assertIs(report["checksums_ok"], True)

    def test_patched_classifies_exactly_the_edits_and_descriptors(self):
        a = m.load_dump(str(DUMP))
        b = m.load_dump(str(self.patched))
        ranges, report = bindiff.diff(a, b, self.patch_json)

        self.assertEqual(report["counts"][bindiff.CLS_UNEXPECTED], 0, report["ranges"])
        self.assertEqual(report["issues"], [])
        self.assertTrue(report["ok"])
        self.assertIs(report["checksums_ok"], True)

        # Every patch-classified byte lies inside one of the edits, and every
        # edit contributed at least one changed byte.  (A range can be shorter
        # than the edit when old and new happen to share a byte.)
        patch_ranges = [r for r in ranges if r.cls == bindiff.CLS_PATCH]
        patched_bytes = {o for r in patch_ranges for o in range(r.file_start, r.file_end)}
        for addr, new in EDITS:
            off = m.cpu_to_file(addr)
            window = set(range(off, off + len(new)))
            self.assertTrue(window & patched_bytes, f"edit {addr:#x} not reported")
            patched_bytes -= window
        self.assertEqual(patched_bytes, set(), "patch bytes outside the listed edits")
        self.assertEqual({r.cpu_start for r in patch_ranges},
                         {0x0A7B10, 0x150000, 0x1E2600})   # 0x1E2600 == alias 0x5E2600

        # exactly the three affected blocks had their sum/~sum words rewritten
        desc_ranges = [r for r in ranges if r.cls == bindiff.CLS_DESCRIPTOR]
        self.assertEqual({r.detail for r in desc_ranges},
                         {"desc 0x0a0100 (table 0x0a0000 entry 16)",
                          "desc 0x0a0250 (table 0x0a0000 entry 37)",
                          "desc 0x1c3340 (table 0x1c3300 entry 4)"})
        desc_map = bindiff.descriptor_word_map()
        for r in desc_ranges:
            for o in range(r.file_start, r.file_end):
                self.assertIn(o, desc_map)    # i.e. a sum/~sum byte, never start/end

    def test_unlisted_change_is_unexpected_and_exit_code_is_1(self):
        data = bytearray(Path(self.patched).read_bytes())
        off = m.cpu_to_file(0x151000)         # not in patch.json
        data[off] ^= 0xFF
        rogue = self.tmp / "rogue.bin"
        rogue.write_bytes(bytes(data))

        ranges, report = bindiff.diff(m.load_dump(str(DUMP)), m.load_dump(str(rogue)),
                                      self.patch_json)
        bad = [r for r in ranges if r.cls == bindiff.CLS_UNEXPECTED]
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0].cpu_start, 0x151000)
        self.assertFalse(report["ok"])
        self.assertIs(report["checksums_ok"], False)   # the rogue byte broke the block

        with contextlib.redirect_stdout(io.StringIO()):
            rc = bindiff.main([str(DUMP), str(rogue), "-p", str(self.patch_json), "-q"])
        self.assertEqual(rc, 1)

    def test_cli_and_json_report(self):
        out = self.tmp / "report.json"
        with contextlib.redirect_stdout(io.StringIO()):
            rc = bindiff.main([str(DUMP), str(self.patched),
                               "-p", str(self.patch_json), "--json", str(out)])
        self.assertEqual(rc, 0)
        report = json.loads(out.read_text())
        self.assertTrue(report["ok"])
        self.assertEqual(report["stock"]["sha256"],
                         "b15590d3f1874ace3125c5d047c09a686db9b8bb498187663539ebab205609b3")
        self.assertEqual(report["counts"]["unexpected"], 0)

    def test_wrong_old_bytes_are_reported(self):
        doc = json.loads(self.patch_json.read_text())
        doc["changes"][0]["old"] = "ffffffff"
        bad = self.tmp / "bad_patch.json"
        bad.write_text(json.dumps(doc))
        _ranges, report = bindiff.diff(m.load_dump(str(DUMP)),
                                       m.load_dump(str(self.patched)), bad)
        self.assertFalse(report["ok"])
        self.assertTrue(any("old bytes" in i for i in report["issues"]), report["issues"])


@requires_dump
class TestChecksumStillOk(DumpUnchanged):
    """checksum.py semantics are untouched: fix() is a no-op on the original."""

    def test_fix_is_a_noop_on_the_original(self):
        data = m.load_dump(str(DUMP))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(checksum.fix(data), 0)
        self.assertEqual(sum(1 for _ in checksum.descriptors(data)), 65)


if __name__ == "__main__":
    unittest.main()
