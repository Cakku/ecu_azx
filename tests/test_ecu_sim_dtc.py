"""The fault services on the simulator: 18 / 17 / 14 (brief G5, #22 prep).

`logging/ecu_sim.py` routes `18` (readDiagnosticTroubleCodesByStatus), `17`
(readStatusOfDiagnosticTroubleCodes) and `14` (clearDiagnosticInformation)
to the **firmware's own handlers** out of the dispatch table (0x35064,
0x36024, 0x35410), which read the firmware's RAM fault memory at 0x7F8890
(`re/findings/kwp.md` 12.7).  What is modelled -- and tested as a model -- is
the seeding (`DtcStore.seed`, standing in for the fault-path manager), the
re-dispatch of a pending service, and the erase after a positive `14`
(`DtcStore.after_clear`).

Nothing here writes to `data/` and nothing is ever flashed.
"""
from __future__ import annotations

import struct
import sys
import tempfile
import unittest
from pathlib import Path

from tests.common import DUMP, REPO, DumpUnchanged, requires_dump

sys.path.insert(0, str(REPO / "logging"))

try:
    from ecu_sim import (DFP_LOCK, DTC_TABLE_PTR, FAULT_MEMORY, FAULT_USED,
                         INIT_ENTRIES, Med9Handlers, dtc_code, dtc_text,
                         parse_read_dtc)
    from med9kwp.kwp import key_level2
    available = True
except Exception:                                            # pragma: no cover
    available = False
requires_sim = unittest.skipUnless(available, "unicorn / python-can missing")

READ_ALL = b"\x18\x00\xff\x00"
CLEAR_ALL = b"\x14\xff\x00"


def handlers(**kw) -> "Med9Handlers":
    kw.setdefault("animate", False)
    return Med9Handlers(str(DUMP), **kw)


def with_eeprom(**kw) -> "Med9Handlers":
    path = Path(tempfile.mkdtemp()) / "eeprom.bin"
    return handlers(eeprom=str(path), **kw)


class TestDtcText(unittest.TestCase):
    def test_sae_round_trip(self):
        for text, code in (("P0601", 0x0601), ("P1429", 0x1429),
                           ("U0305", 0xC305), ("C1234", 0x5234),
                           ("B0001", 0x8001)):
            with self.subTest(text=text):
                self.assertEqual(dtc_code(text), code)
                self.assertEqual(dtc_text(code), text)

    def test_the_answer_parser_insists_on_the_shape(self):
        self.assertEqual(parse_read_dtc(b"\x58\x00"), [])
        self.assertEqual(parse_read_dtc(bytes.fromhex("5801060131")),
                         [(0x0601, 0x31)])
        for bad in (b"", b"\x7f\x18\x12", b"\x58\x01\x06\x01"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_read_dtc(bad)


@requires_dump
@requires_sim
class TestReadDtcIsTheFirmwares(DumpUnchanged):
    def test_the_dispatch_table_names_the_three_handlers(self):
        h = handlers()
        h1 = {e.sid: (e.h1, e.session_mask) for e in h.table
              if e.sid in (0x14, 0x17, 0x18)}
        self.assertEqual(h1, {0x14: (0x035410, 0x38), 0x17: (0x036024, 0x38),
                              0x18: (0x035064, 0x38)})

    def test_a_clean_simulator_has_no_dtcs(self):
        h = handlers()
        h.handle(b"\x10\x89")
        self.assertEqual(h.handle(READ_ALL), [b"\x58\x00"])

    def test_the_session_gate_is_the_tables(self):
        """Mask 0x38: sessions 3, 4, 5 -- not the default session 0."""
        h = handlers()
        self.assertEqual(h.handle(READ_ALL), [b"\x7f\x18\x33"])
        for sub in (0x83, 0x89):
            with self.subTest(session=hex(sub)):
                h.handle(bytes([0x10, sub]))
                self.assertEqual(h.handle(READ_ALL), [b"\x58\x00"])

    def test_the_upload_session_needs_27_first(self):
        """Session 4 (10 86) is behind SecurityAccess level 2 (kwp.md 2)."""
        h = handlers(seed=0x12345678)
        h.handle(b"\x10\x89")
        self.assertEqual(h.handle(b"\x10\x86"), [b"\x7f\x10\x33"])
        h.handle(b"\x27\x03")
        h.handle(b"\x27\x04" + key_level2(0x12345678).to_bytes(4, "big"))
        self.assertEqual(h.handle(b"\x10\x86"), [b"\x50\x86"])
        self.assertEqual(h.handle(READ_ALL), [b"\x58\x00"])

    def test_a_seeded_dtc_is_reported_with_its_status(self):
        h = handlers(dtcs=["P0601"])
        h.handle(b"\x10\x89")
        answer = h.handle(READ_ALL)[-1]
        self.assertEqual(parse_read_dtc(answer), [(0x0601, 0x31)])
        # status: kind 0 -> bit 0, 0x20 always, 0x10 = readiness bit clear
        self.assertEqual(h.handle(b"\x17\x06\x01"), [b"\x57\x01\x06\x01\x31"])

    def test_the_kind_picks_the_paths_code_and_the_status_bit(self):
        h = handlers()
        path, kind = h.dtc.seed(path=11, kind=2)
        self.assertEqual(h.dtc.code_of(11, 2), 0x1429)
        h.handle(b"\x10\x89")
        self.assertEqual(parse_read_dtc(h.handle(READ_ALL)[-1]),
                         [(0x1429, 0x34)])

    def test_two_dtcs_in_store_order(self):
        h = handlers(dtcs=["P0601", "P1429"])
        h.handle(b"\x10\x89")
        self.assertEqual([c for c, _s in parse_read_dtc(h.handle(READ_ALL)[-1])],
                         [0x0601, 0x1429])
        self.assertEqual(h.dtc.used, 2)
        self.assertEqual([e["path"] for e in h.dtc.entries()], [1, 11])

    def test_the_handler_checks_its_request(self):
        h = handlers()
        h.handle(b"\x10\x89")
        self.assertEqual(h.handle(b"\x18\x02\xff\x00"), [b"\x7f\x18\x12"],
                         "only status byte 00 is accepted (0x35090)")
        self.assertEqual(h.handle(b"\x18\x00\xff"), [b"\x7f\x18\x12"],
                         "request length must be 3 (0x35084)")

    def test_an_unknown_code_cannot_be_seeded(self):
        with self.assertRaises(KeyError):
            handlers().dtc.seed("P3FFF")


@requires_dump
@requires_sim
class TestClearDtcIsTheFirmwares(DumpUnchanged):
    def test_init_entries_34_and_39_are_what_14_needs(self):
        idx = [i for i, _a, _n in INIT_ENTRIES]
        self.assertIn(34, idx)
        self.assertIn(39, idx)
        h = handlers()
        self.assertEqual(h.emu.read(DFP_LOCK, 8), bytes.fromhex("00000000ffffffff"),
                         "dfp_init leaves the lock free")
        self.assertEqual(struct.unpack(">I", h.emu.read(DTC_TABLE_PTR, 4))[0],
                         0x5DA6DE, "cal 0x5CF642 = 2 selects the second table")

    def test_clear_pends_commits_and_answers_positive(self):
        h = with_eeprom(dtcs=["P0601", "P1429"])
        h.handle(b"\x10\x89")
        writes = h.eeprom.writes
        self.assertEqual(h.handle(CLEAR_ALL), [b"\x7f\x14\x78", b"\x54\xff\x00"])
        self.assertGreater(h.eeprom.writes, writes,
                           "the handler committed an EEP_CONF block (24)")
        self.assertEqual(h.handle(READ_ALL), [b"\x58\x00"])
        self.assertEqual(h.dtc.clears, 1)
        self.assertEqual(h.emu.read(FAULT_USED, 2), b"\x00\x00")
        # and a second clear works again (the modelled DFPM released the lock)
        self.assertEqual(h.handle(CLEAR_ALL)[-1], b"\x54\xff\x00")

    def test_without_the_lock_the_firmware_refuses(self):
        """{0, 0} is not {x, ~x}: 0x43D7AC fails and 14 answers NRC 0x10."""
        h = with_eeprom(dtcs=["P0601"])
        h.emu.write(DFP_LOCK, bytes(8))
        h.handle(b"\x10\x89")
        self.assertEqual(h.handle(CLEAR_ALL), [b"\x7f\x14\x10"])
        self.assertEqual(len(parse_read_dtc(h.handle(READ_ALL)[-1])), 1)

    def test_without_an_eeprom_the_clear_never_completes(self):
        h = handlers(dtcs=["P0601"])
        h.handle(b"\x10\x89")
        self.assertEqual(h.handle(CLEAR_ALL), [b"\x7f\x14\x78", b"\x7f\x14\x10"])
        self.assertIn("no --eeprom", h.log[-1])
        self.assertEqual(len(parse_read_dtc(h.handle(READ_ALL)[-1])), 1,
                         "nothing was erased")

    def test_a_single_group_that_is_not_stored_is_acknowledged(self):
        """14 <group> with no matching entry: the handler answers positively."""
        h = with_eeprom(dtcs=["P0601"])
        h.handle(b"\x10\x89")
        self.assertEqual(h.handle(b"\x14\x12\x34"), [b"\x54\x12\x34"])
        self.assertEqual(len(parse_read_dtc(h.handle(READ_ALL)[-1])), 1)


@requires_dump
@requires_sim
class TestOverTheBus(DumpUnchanged):
    """The logger's own client: 78 then the final answer, and a reconnect."""

    def test_clear_then_read_back_over_tp20(self):
        from ecu_sim import EcuSimulator
        from med9kwp import KwpClient, Tp20Client, open_link, parse_bus_spec
        h = with_eeprom(dtcs=["P0601"])
        sim = EcuSimulator.on_virtual_bus("g5_dtc_bus", handlers=h)
        with sim.background():
            for attempt in range(2):
                link = open_link(parse_bus_spec("virtual:g5_dtc_bus"))
                tp = Tp20Client(link, dest=0x01, timeout=3.0)
                tp.connect()
                kwp = KwpClient(tp, timeout=3.0, pending_timeout=10.0)
                kwp.start_session(0x89)
                if attempt == 0:
                    self.assertEqual(parse_read_dtc(kwp.raw(READ_ALL)),
                                     [(0x0601, 0x31)])
                    self.assertEqual(kwp.raw(CLEAR_ALL), b"\x54\xff\x00")
                self.assertEqual(kwp.raw(READ_ALL), b"\x58\x00")
                tp.disconnect()
                link.close()
        sim.close()
        self.assertIsNone(sim.error)
        self.assertEqual(h.connections, 2,
                         "the firmware's h2 walk ran once per connection")


if __name__ == "__main__":                                    # pragma: no cover
    unittest.main()
