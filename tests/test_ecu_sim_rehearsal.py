"""`logging/bench_rehearsal.py`'s own plumbing (brief G5).

* The EEPROM store of the ethanol percent is located at run time from
  `patches/ff_fuel/ffcal001.json` (`ff_persist_block`, `ff_persist_offset`)
  and the firmware's EEP_CONF table -- never hard-coded as "+2" -- so brief
  G7's move of the offset to 19 changes nothing in `logging/`.

Nothing here writes to `data/` or `patches/`, and nothing is ever flashed.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from tests.common import REPO, DumpUnchanged, requires_dump

sys.path.insert(0, str(REPO / "logging"))

try:
    import bench_rehearsal as br
    available = True
except Exception:                                            # pragma: no cover
    available = False
requires_rehearsal = unittest.skipUnless(available, "logging deps missing")

FFCAL_JSON = REPO / "patches" / "ff_fuel" / "ffcal001.json"


@requires_dump
@requires_rehearsal
class TestThePersistOffsetIsReadNotRestated(DumpUnchanged):
    def _with_offset(self, offset: int) -> dict:
        cal = json.loads(FFCAL_JSON.read_text(encoding="utf-8"))
        cal["ff_persist_offset"] = offset
        tmp = Path(tempfile.mkdtemp()) / "ffcal001.json"
        tmp.write_text(json.dumps(cal), encoding="utf-8")
        return br.persist_location(str(tmp))

    def test_the_shipped_calibration_is_what_the_rehearsal_uses(self):
        cal = json.loads(FFCAL_JSON.read_text(encoding="utf-8"))
        loc = br.persist_location()
        self.assertEqual(loc["block"], cal["ff_persist_block"])
        self.assertEqual(loc["offset"], cal["ff_persist_offset"])
        # block 8: mirror 0x7F9F80, EEPROM copy 0 at 0x1C0 (eeprom.md 3.3)
        self.assertEqual(loc["mirror"], 0x7F9F80 + loc["offset"])
        self.assertEqual(loc["eeprom"], 0x1C0 + loc["offset"])

    def test_a_moved_offset_moves_every_address(self):
        """G7's offset 19 (docs/05 3.8): payload +19, mirror 0x7F9F93."""
        loc = self._with_offset(19)
        self.assertEqual((loc["mirror"], loc["eeprom"]), (0x7F9F93, 0x1D3))
        self.assertEqual(loc["label"], "block 8 payload +19")

    def test_an_offset_outside_the_payload_is_refused(self):
        with self.assertRaises(SystemExit):
            self._with_offset(30)            # block 8 payload is 30 bytes

    def test_the_session_variable_is_found_by_address(self):
        name = br.persist_variable()
        loc = br.persist_location()
        import med9log
        sess = med9log.load_session(str(br.SESSION), str(br.PATCH_JSON))
        by_name = {v.name: v for v in sess.variables}
        self.assertIn(name, by_name, "ff_fuel.json logs the store's mirror")
        self.assertEqual(by_name[name].address, loc["mirror"])

    def test_the_device_byte_is_read_at_the_calibrated_address(self):
        loc = br.persist_location()
        raw = bytearray(b"\xFF" * 0x800)
        raw[loc["eeprom"]] = 77
        tmp = Path(tempfile.mkdtemp()) / "eeprom.bin"
        tmp.write_bytes(bytes(raw))
        self.assertEqual(br.persist_device_byte(str(tmp)), 77)
        self.assertIsNone(br.persist_device_byte(str(tmp) + ".missing"))


if __name__ == "__main__":                                    # pragma: no cover
    unittest.main()
