"""tools/bench_kit.py (brief H6, #26/#27): the bench-day kit, pinned to the docs.

The kit is built once into a temporary directory.  Its hashes are compared
with the values the patch READMEs quote, and its flash CRCs with the values
docs/08 quotes -- both parsed from the documents, so a document that drifts
from the build fails here, at the desk, and not on the bench.

    ./.venv/bin/python3 -m unittest tests.test_bench_kit -v
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.common import DUMP, DUMP_SHA256, REPO, DumpUnchanged, requires_dump, sha256

import bench_kit  # noqa: E402  (tests.common put tools/ on sys.path)

LLVM_DIR = Path(os.environ.get("LLVM_DIR",
                               "/Users/carlo/toolchains/LLVM-23.1.1-macOS-ARM64"))
requires_toolchain = unittest.skipUnless(
    (LLVM_DIR / "bin" / "clang").is_file(),
    f"no PowerPC cross compiler at {LLVM_DIR}; docs/03_tooling.md §3.1 - "
    f"the kit builds ff_counter and ff_fuel from source")

FF_COUNTER_README = REPO / "patches" / "ff_counter" / "README.md"
FF_FUEL_README = REPO / "patches" / "ff_fuel" / "README.md"
PLAYBOOK = REPO / "docs" / "08_bench_playbook.md"
DESCRIPTORS = [REPO / "patches" / "ff_counter" / "patch.json",
               REPO / "patches" / "ff_counter" / "patch.external.json",
               REPO / "patches" / "ff_fuel" / "patch.json"]


def _one(pattern: str, text: str, what: str) -> str:
    m = re.findall(pattern, text, re.S)
    assert len(m) == 1, f"{what}: expected exactly one match of {pattern!r}, got {m}"
    return m[0]


def readme_sha(readme: Path, command: str) -> str:
    """The `sha256:` line of the transcript that starts with `$ <command>`."""
    return _one(r"\$ " + re.escape(command) + r"\n(?:[^\n]*\n)*?sha256: ([0-9a-f]{64})",
                readme.read_text(), f"{readme.name} `{command}`")


def playbook_crc(anchor: str) -> int:
    """A bold 0x........ flash CRC following `anchor` on the same line."""
    return int(_one(re.escape(anchor) + r"\s*\*\*(0x[0-9A-Fa-f]{8})\*\*",
                    PLAYBOOK.read_text(), f"docs/08 {anchor!r}"), 16)


def data_snapshot() -> dict:
    return {str(p.relative_to(REPO)): sha256(p)
            for p in sorted((REPO / "data").rglob("*")) if p.is_file()}


class TestDocumentValues(unittest.TestCase):
    """The parsers find what the tests pin to; no build needed."""

    def test_readme_hashes_parse(self):
        self.assertTrue(readme_sha(FF_COUNTER_README, "make apply").startswith("3cd20443"))
        self.assertTrue(readme_sha(FF_COUNTER_README, "make HOOKS=external apply")
                        .startswith("9ecde359"))
        self.assertTrue(readme_sha(FF_FUEL_README, "make apply").startswith("c08a78a6"))

    def test_playbook_crcs_parse(self):
        self.assertEqual(playbook_crc("Its value must be"), 0x5562139F)
        self.assertEqual(playbook_crc("i.e."), 0x06C08AD4)


@requires_dump
class TestGuardRails(DumpUnchanged):

    def test_refuses_a_non_canonical_dump(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        fake = tmp / "passat_azx_ori.bin"
        data = bytearray(DUMP.read_bytes())
        data[0x1000] ^= 0xFF
        fake.write_bytes(data)
        saved = bench_kit.DUMP
        bench_kit.DUMP = fake
        try:
            self.assertEqual(bench_kit.main(["--out", str(tmp / "kit")]), 2)
        finally:
            bench_kit.DUMP = saved
        self.assertFalse((tmp / "kit").exists(), "wrote a kit from a wrong dump")

    def test_refuses_data_and_tracked_paths(self):
        for bad in (REPO / "data" / "kit", REPO / "docs" / "kit", REPO / "kit"):
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                bench_kit.check_out_dir(bad)
        self.assertEqual(bench_kit.check_out_dir(REPO / "work" / "bench_kit"),
                         (REPO / "work" / "bench_kit").resolve())

    def test_default_is_under_work(self):
        self.assertEqual(bench_kit.DEFAULT_OUT, REPO / "work" / "bench_kit")


@requires_dump
@requires_toolchain
class TestBuiltKit(DumpUnchanged):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.data_before = data_snapshot()
        cls.desc_before = {p: p.read_bytes() for p in DESCRIPTORS}
        cls.tmp = Path(tempfile.mkdtemp())
        cls.kit = cls.tmp / "kit"
        cls.proc = subprocess.run(
            [sys.executable, str(REPO / "tools" / "bench_kit.py"), "--out", str(cls.kit)],
            cwd=REPO, capture_output=True, text=True, timeout=900)
        mpath = cls.kit / "MANIFEST.json"
        cls.manifest = json.loads(mpath.read_text()) if mpath.is_file() else None
        cls.by_file = {e["file"]: e for e in (cls.manifest or {}).get("images", [])}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        super().tearDownClass()

    def test_build_succeeded(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stdout + self.proc.stderr)
        self.assertIn("bench kit: 4 image(s)", self.proc.stdout)
        self.assertEqual(sorted(self.by_file), [
            "00_stock_resaved.bin", "10_ff_counter_both.bin",
            "11_ff_counter_external.bin", "20_ff_fuel_shipped.bin"])
        self.assertTrue((self.kit / "README.md").is_file())

    def test_manifest_matches_the_files(self):
        for name, e in self.by_file.items():
            with self.subTest(file=name):
                f = self.kit / name
                self.assertEqual(e["sha256"], sha256(f))
                self.assertEqual(e["size"], f.stat().st_size)
                self.assertEqual(e["checksum_verify"], "ALL OK (65 blocks)")
                self.assertEqual(e["bindiff"]["counts"]["unexpected"], 0)
                self.assertTrue(e["docs08_step"].startswith("docs/08 step"))
                self.assertTrue(e["s_rows"])

    def test_stock_resave_is_the_dump(self):
        f = self.kit / "00_stock_resaved.bin"
        self.assertEqual(f.read_bytes(), DUMP.read_bytes())
        e = self.by_file["00_stock_resaved.bin"]
        self.assertEqual(e["sha256"], DUMP_SHA256)
        self.assertEqual(e["bindiff"]["ranges"], [])
        self.assertIsNone(e["patch"])

    def test_hashes_match_the_readmes(self):
        for name, readme, cmd in (
                ("10_ff_counter_both.bin", FF_COUNTER_README, "make apply"),
                ("11_ff_counter_external.bin", FF_COUNTER_README, "make HOOKS=external apply"),
                ("20_ff_fuel_shipped.bin", FF_FUEL_README, "make apply")):
            with self.subTest(file=name):
                self.assertEqual(self.by_file[name]["sha256"], readme_sha(readme, cmd),
                                 f"{name} and {readme.relative_to(REPO)} disagree: "
                                 f"the build or the README drifted")

    def test_flash_crcs_match_docs08(self):
        crc = {n: int(e["expected_flash_crc"], 16) for n, e in self.by_file.items()}
        self.assertEqual(crc["00_stock_resaved.bin"], playbook_crc("Its value must be"))
        self.assertEqual(crc["10_ff_counter_both.bin"], playbook_crc("i.e."))
        # No document quotes the other two yet (H6 report): they must at least
        # differ from stock (flash_crc.json item 6: "the value MUST change").
        for n in ("11_ff_counter_external.bin", "20_ff_fuel_shipped.bin"):
            self.assertNotEqual(crc[n], crc["00_stock_resaved.bin"], n)

    def test_patch_identity_and_ffcal001(self):
        e = self.by_file["10_ff_counter_both.bin"]["patch"]
        self.assertEqual((e["id"], e["variant"]), ("ff_counter", "both"))
        self.assertEqual(e["onchip_edits"], 1)
        e = self.by_file["11_ff_counter_external.bin"]["patch"]
        self.assertEqual((e["id"], e["variant"], e["onchip_edits"]),
                         ("ff_counter", "external", 0))
        fuel = self.by_file["20_ff_fuel_shipped.bin"]
        self.assertEqual(fuel["patch"]["id"], "ff_fuel")
        sys.path.insert(0, str(REPO / "patches" / "ff_fuel"))
        try:
            import ffcal001
        finally:
            sys.path.pop(0)
        self.assertEqual(fuel["ffcal001"]["version"], ffcal001.VERSION)
        self.assertEqual(fuel["ffcal001"]["length"], ffcal001.LENGTH)
        self.assertIsNone(self.by_file["10_ff_counter_both.bin"]["ffcal001"])

    def test_nothing_under_data_changed(self):
        self.assertEqual(data_snapshot(), self.data_before)
        self.assertEqual(sha256(DUMP), DUMP_SHA256)

    def test_descriptors_not_rewritten(self):
        for p, before in self.desc_before.items():
            self.assertEqual(p.read_bytes(), before, f"{p} changed during the build")


if __name__ == "__main__":
    unittest.main()
