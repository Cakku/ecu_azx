"""Shared helpers for the repository test suite.

Run everything with:

    python3 -m unittest discover -s tests -v

The tests need `data/passat_azx_ori.bin`; they never write to it.  Temporary
copies go to a per-test temporary directory.
"""
from __future__ import annotations

import hashlib
import sys
import unittest
import warnings
from pathlib import Path

# med9lib.load_dump uses a bare open(); harmless, but it makes every test that
# loads the dump print a ResourceWarning.  Filtered here rather than changing
# med9lib, whose semantics other briefs depend on.
warnings.filterwarnings("ignore", category=ResourceWarning)

REPO = Path(__file__).resolve().parent.parent
DUMP = REPO / "data" / "passat_azx_ori.bin"
DUMP_SHA256 = "b15590d3f1874ace3125c5d047c09a686db9b8bb498187663539ebab205609b3"

for p in (REPO, REPO / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def dump_available() -> bool:
    return DUMP.is_file()


requires_dump = unittest.skipUnless(dump_available(), f"{DUMP} not present")


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class DumpUnchanged(unittest.TestCase):
    """Base class that fails loudly if a test touched the original dump."""

    @classmethod
    def setUpClass(cls):
        if dump_available():
            cls._sha_before = sha256(DUMP)

    @classmethod
    def tearDownClass(cls):
        if dump_available():
            assert sha256(DUMP) == cls._sha_before, "the original dump was modified!"
