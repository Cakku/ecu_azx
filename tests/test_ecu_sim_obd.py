"""The generic-OBD CAN route, 0x7DF/0x7E0 -> 0x7E8 (brief H3, issue #48).

`re/findings/obd.md` section 11 traced the ISO 15765-2 path from TouCAN module
C buffer 15 to `kwp_service_dispatch` and back to buffer 13; `emu/obd_can.py`
runs it -- the firmware's own ISR, ISO-TP parser, connection layer and
dispatcher -- and `logging/ecu_sim.py --obd-can` puts it on a bus.  Layers:

1. the tester-side framing (`logging/med9kwp/isotp.py`), no dump needed;
2. the static facts the route rests on, read straight out of the image;
3. the route itself, emulated: the answer frame, the PCI handling, the
   session outcome (6, tester address 0x33), what is silent, the 5 s
   connection lifetime, a multi-frame answer with flow control;
4. the two proofs for G1's PID 0x52 over this route: switch off, every frame
   is the stock image's; switch on, only the 0x52 bit and the 0x52 answer
   differ, and the bit needs a new connection;
5. the same over a python-can bus with `logging/obd_client.py`, with TP2.0
   still answering beside it.

Everything is VERIFIED-DYNAMIC (emulated) at most; nothing talks to an ECU.
"""
from __future__ import annotations

import struct
import sys
import unittest

from tests.common import DUMP, REPO, DumpUnchanged, requires_dump

for _p in (str(REPO / "logging"), str(REPO / "patches" / "ff_fuel")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from med9kwp.isotp import (IsoTpError, Reassembler, flow_control,  # noqa: E402
                           single_frame)

try:
    import can  # noqa: F401
    HAVE_CAN = True
except Exception:                                            # pragma: no cover
    HAVE_CAN = False
requires_can = unittest.skipUnless(HAVE_CAN, "python-can is not installed")

PATCH = REPO / "patches" / "ff_fuel"


def _image() -> bytes:
    return DUMP.read_bytes()


def _word(data: bytes, addr: int) -> int:
    return struct.unpack(">I", data[addr:addr + 4])[0]


# ---------------------------------------------------------------- 1. framing
class TestIsoTpFraming(unittest.TestCase):
    def test_single_frame_is_padded_to_dlc_8(self):
        self.assertEqual(single_frame(b"\x01\x00"), bytes.fromhex("0201000000000000"))
        self.assertEqual(single_frame(b"\x01\x52", pad=0x55),
                         bytes.fromhex("0201525555555555"))
        with self.assertRaises(ValueError):
            single_frame(b"\x00" * 8)
        with self.assertRaises(ValueError):
            single_frame(b"")

    def test_flow_control(self):
        self.assertEqual(flow_control(), bytes.fromhex("3000000000000000"))

    def test_reassembly(self):
        r = Reassembler()
        self.assertEqual(r.feed(bytes.fromhex("0341529900000000")), b"\x41\x52\x99")
        self.assertIsNone(r.feed(bytes.fromhex("1010410011223344")))
        self.assertTrue(r.needs_flow_control)
        self.assertIsNone(r.feed(bytes.fromhex("2120556677884000")))
        self.assertEqual(r.feed(bytes.fromhex("22aabbccdd000000")),
                         bytes.fromhex("41001122334420556677884000aabbcc"))
        with self.assertRaises(IsoTpError):
            r.feed(bytes.fromhex("2200000000000000"))       # CF without FF
        with self.assertRaises(IsoTpError):
            r.feed(bytes.fromhex("0800000000000000"))       # SF N = 8


# ------------------------------------------------------------- 2. the image
@requires_dump
class TestTheStaticRoute(DumpUnchanged):
    """obd.md 11.1: the tables and the words the route runs through."""

    def test_module_c_mb15_range_objects(self):
        d = _image()
        want = {0xD7: (0x7D0, 0x0A953C), 0xD8: (0x7DF, 0x0B5534),
                0xD9: (0x7E0, 0x1420A0)}
        got = {}
        for i in range(18):
            rec = d[0x2C054 + 20 * i:0x2C068 + 20 * i]
            handle, lo, hi = rec[0], _word(rec, 4), _word(rec, 8)
            flags, mb, module = rec[0x0C], rec[0x0D], rec[0x0E]
            if (module, mb) == (2, 15) and lo == hi and lo != 0xFFFFFFFF:
                got[handle] = (lo, _word(rec, 0x10))
        self.assertEqual(got, want)
        self.assertEqual(d[0x2C1BC:0x2C1C2], bytes.fromhex("00010205090e"))

    def test_the_rx_map_makes_0x7df_single_frame_only_address_0x33(self):
        d = _image()
        self.assertEqual(d[0x2C574:0x2C57A], bytes.fromhex("d80400330100"),
                         "0x7DF: channel 4, normal addressing, 0x33, DLC 8, no FF")
        self.assertEqual(d[0x2C56E:0x2C574], bytes.fromhex("d90300100101"),
                         "0x7E0: channel 3, address 0x10, DLC 8, FF allowed")
        self.assertEqual(d[0x2C5F8:0x2C5FB], bytes([3, 4, 7]), "rx entries 3..6")

    def test_address_list_and_gates(self):
        d = _image()
        self.assertEqual(d[0x2C310:0x2C319], bytes.fromhex("0002c2f60002c2f802"))
        self.assertEqual(d[0x2C2F6:0x2C2FA], bytes.fromhex("10330004"),
                         "addresses 10 33 -> channel types 00 04")
        self.assertEqual(_word(d, 0x2C324 + 0x20), 0x2C29C, "0x7E0 gate")
        self.assertEqual(_word(d, 0x2C390 + 0x20), 0x386E4, "0x7DF gate")
        self.assertEqual(d[0x2C29C:0x2C2A4], bytes.fromhex("386000004e800020"),
                         "li r3,0 ; blr -- the physical channel never connects")
        self.assertEqual(d[0x1CEE6E], 1, "cal byte 0x5CEE6E enables 0x7DF")

    def test_the_writer_of_0x7f804b(self):
        d = _image()
        self.assertEqual(_word(d, 0x13F1D8), 0x3FE00080)     # lis r31,0x80
        self.assertEqual(_word(d, 0x13F1DC), 0x3BFF802C)     # addi r31,r31,-0x7FD4
        self.assertEqual(_word(d, 0x13F1E4), 0x993F001F)     # stb r9,0x1F(r31)
        self.assertEqual(_word(d, 0x037138), 0x2C040033)     # cmpwi r4,0x33
        self.assertEqual(_word(d, 0x037154), 0x38600006)     # li r3,6

    def test_single_frame_padding_is_zero(self):
        d = _image()
        self.assertEqual(_word(d, 0x142A9C), 0x3B600000)     # li r27,0
        self.assertEqual(_word(d, 0x142AA8), 0x9F7E0001)     # stbu r27,1(r30)


# ------------------------------------------------------------ 3. the route
@requires_dump
class TestTheRoute(DumpUnchanged):
    """One stock simulator with the route, driven frame by frame."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from ecu_sim import Med9Handlers
        cls.h = Med9Handlers(str(DUMP), animate=False, obd_can=True)
        cls.route = cls.h.obd
        cls.first = cls.route.request(b"\x01\x00")

    def test_init_entries_ran(self):
        self.assertEqual([(i, ok) for i, _a, ok in self.route.init_log],
                         [(44, True), (281, True)])
        self.assertEqual(self.route.mftb_rewritten, 44)
        self.assertEqual(self.route.errors, [])

    def test_01_00_is_answered_on_0x7e8_as_a_padded_single_frame(self):
        self.assertEqual(len(self.first), 1)
        can_id, data = self.first[0]
        self.assertEqual(can_id, 0x7E8)
        self.assertEqual(len(data), 8, "DLC 8")
        self.assertEqual(data[:3], b"\x06\x41\x00", "PCI 06, positive 41 00")
        self.assertEqual(data[7:], b"\x00", "padding 0x00")

    def test_the_session_is_6_and_the_tester_address_0x33(self):
        self.assertEqual(self.route.session, 6)
        self.assertEqual(self.route.tester_target, 0x33)
        self.assertEqual(self.route.channel_type, 4)
        self.assertTrue(self.route.connection_open)

    def _silent(self, frame: bytes, *, can_id=0x7DF, dlc=8) -> list:
        before = len(self.route.sent)
        self.route.receive(can_id, frame, dlc)
        self.route.run(0.1)
        return self.route.sent[before:]

    def test_the_pci_handling(self):
        cases = {
            "DLC 3 (ISO 15765-4 wants 8)": (bytes.fromhex("020100"), 3),
            "first frame on 0x7DF": (bytes.fromhex("1008010000000000"), 8),
            "SF length 0": (bytes.fromhex("0001000000000000"), 8),
            "SF length 8": (bytes.fromhex("0801000000000000"), 8),
            "PCI 0x40": (bytes.fromhex("4201000000000000"), 8),
        }
        for what, (frame, dlc) in cases.items():
            with self.subTest(what):
                self.assertEqual(self._silent(frame, dlc=dlc), [])
        again = self.route.request(b"\x01\x00")
        self.assertEqual(again, self.first, "the channel still listens")

    def test_the_physical_id_is_never_answered(self):
        self.assertEqual(self.route.request(b"\x01\x00", can_id=0x7E0), [])

    def test_the_hardware_filter(self):
        self.assertFalse(self.route.receive(0x700, single_frame(b"\x01\x00")))
        self.assertTrue(self.route.receive(0x7C1, single_frame(b"\x01\x00")))

    def test_what_is_silent_on_the_functional_channel(self):
        for req in (b"\x3e", b"\x10\x81", b"\x21\x01", b"\x1a\x9b",
                    b"\x01\x20", b"\x01\x52"):
            with self.subTest(req=req.hex()):
                self.assertEqual(self.route.request(req), [])


@requires_dump
class TestTheConnectionLifetime(DumpUnchanged):
    def test_5_s_of_silence_ends_the_connection_and_the_session(self):
        from ecu_sim import Med9Handlers
        h = Med9Handlers(str(DUMP), animate=False, obd_can=True)
        r = h.obd
        self.assertTrue(r.request(b"\x01\x00"))
        self.assertEqual(r.connections, 1)
        r.idle(4.8)
        self.assertTrue(r.request(b"\x01\x00"))
        self.assertEqual(r.connections, 1, "inside 5 s: the same connection")
        r.idle(5.3)
        self.assertFalse(r.connection_open)
        self.assertEqual(r.session, 0)
        self.assertTrue(r.request(b"\x01\x00"))
        self.assertEqual(r.connections, 2, "a new connection, a new h2 walk")
        self.assertEqual(r.session, 6)


@requires_dump
class TestAMultiFrameAnswer(DumpUnchanged):
    def test_01_00_20_40_is_a_first_frame_and_two_consecutive_frames(self):
        from ecu_sim import Med9Handlers
        from emu.obd_can import seed_pid_records
        h = Med9Handlers(str(DUMP), animate=False, obd_can=True)
        self.assertEqual(seed_pid_records(h.emu), 41)          # MODEL
        frames = h.obd.request(b"\x01\x00\x20\x40")
        self.assertEqual([f[1][0] >> 4 for f in frames], [1, 2, 2])
        self.assertEqual(frames[0][1][:2], b"\x10\x10", "FF, 16 bytes")
        fc = [f for f in h.obd.received if f[1] == 0x7E0]
        self.assertEqual(len(fc), 1, "the tester's flow control on 0x7E0")
        r = Reassembler()
        msg = None
        for _cid, data in frames:
            msg = r.feed(data) or msg
        self.assertEqual(len(msg), 16)
        self.assertEqual(msg[:2], b"\x41\x00")
        self.assertEqual(msg[6], 0x20)
        self.assertEqual(msg[11], 0x40)


# ----------------------------------------------------------- 4. the proofs
@requires_dump
class TestPid52OverTheCanRoute(DumpUnchanged):
    """G1's switch over 0x7DF: off = stock frames, on = only 0x52 differs."""

    REQUESTS = [bytes([0x01, p]) for p in range(0x59)] + [
        b"\x01\x00\x20\x40", b"\x01\x05\x0c", b"\x01\x52\x05"]

    @staticmethod
    def _sim(patched: bool, enable: int = 0, e_pct: int = 85):
        import ffcal001
        from bench_rehearsal import FFCAL_JSON, _ff_state_at
        from ecu_sim import FFCAL001_BASE, Med9Handlers
        from emu.obd_can import seed_pid_records
        kw = dict(patch_dir=str(PATCH)) if patched else {}
        h = Med9Handlers(str(DUMP), animate=False, obd_can=True, **kw)
        seed_pid_records(h.emu)                                 # MODEL
        if patched:
            params = ffcal001.load_params(FFCAL_JSON)
            params["ff_pid52_enable"] = enable
            h.emu.write(FFCAL001_BASE, ffcal001.build(params))
            h.emu.write(0x7FFB00, _ff_state_at(e_pct))          # MODEL
            for _ in range(5):
                h.runner._one_tick()                            # the patch's own
        return h

    @classmethod
    def _answers(cls, h) -> dict:
        return {r: h.obd.request(r) for r in cls.REQUESTS}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.stock = cls._answers(cls._sim(False))

    def test_switch_off_every_frame_is_the_stock_images(self):
        got = self._answers(self._sim(True, enable=0))
        for r in self.REQUESTS:
            with self.subTest(req=r.hex()):
                self.assertEqual(got[r], self.stock[r])
        self.assertEqual(got[b"\x01\x52"], [], "01 52 unsupported, silent")

    def test_switch_on_only_the_0x52_bit_and_answer_differ(self):
        h = self._sim(True, enable=1)
        got = self._answers(h)
        differ = sorted(r.hex() for r in self.REQUESTS if got[r] != self.stock[r])
        self.assertEqual(differ, ["01002040", "0140", "0152", "015205"],
                         "01 00 20 40 carries the 01 40 bitmap too")
        self.assertEqual(got[b"\x01\x52"], [(0x7E8, bytes.fromhex("034152d900000000"))])
        bit = got[b"\x01\x40"][0][1][5] ^ self.stock[b"\x01\x40"][0][1][5]
        self.assertEqual(bit, 0x40, "PID 0x52 = byte 2 of the 01 40 bitmap, 0x40")

    def test_enabling_mid_connection_needs_a_reconnect_for_the_bit(self):
        import ffcal001
        from bench_rehearsal import FFCAL_JSON
        from ecu_sim import FFCAL001_BASE
        h = self._sim(True, enable=0)
        before = h.obd.request(b"\x01\x40")
        params = ffcal001.load_params(FFCAL_JSON)
        params["ff_pid52_enable"] = 1
        h.emu.write(FFCAL001_BASE, ffcal001.build(params))
        for _ in range(5):
            h.runner._one_tick()
        self.assertEqual(h.obd.request(b"\x01\x40"), before, "same connection")
        self.assertEqual(h.obd.request(b"\x01\x52")[0][1][:4],
                         bytes.fromhex("034152d9"), "01 52 answers at once")
        h.obd.idle(6.0)
        after = h.obd.request(b"\x01\x40")
        self.assertEqual(after[0][1][5] ^ before[0][1][5], 0x40)


# ------------------------------------------------------------- 5. on a bus
@requires_dump
@requires_can
class TestOnABus(DumpUnchanged):
    def test_obd_client_and_tp20_side_by_side(self):
        import os
        from ecu_sim import EcuSimulator
        from med9kwp import KwpClient, Tp20Client, open_link, parse_bus_spec
        from obd_client import ObdClient
        channel = f"obd_test_{os.getpid()}"
        sim = EcuSimulator.on_virtual_bus(channel, obd_can=True, animate=False)
        with sim.background():
            # TP2.0 first: the two routes share kwp_session_current, and a
            # TP2.0 channel opened while the OBD connection is alive inherits
            # its session 6 in the simulator (logging/README.md)
            link = open_link(parse_bus_spec(f"virtual:{channel}"))
            try:
                tp = Tp20Client(link, dest=0x01, timeout=3.0)
                tp.connect()
                kwp = KwpClient(tp, timeout=3.0)
                self.assertEqual(kwp.raw(b"\x10\x89"), b"\x50\x89")
                tp.disconnect()
            finally:
                link.close()
            link = open_link(parse_bus_spec(f"virtual:{channel}"))
            try:
                client = ObdClient(link, timeout=2.0)
                self.assertEqual(client.query(b"\x01\x00")[:2], b"\x41\x00")
                self.assertIsNone(ObdClient(link, tx_id=0x7E0, timeout=0.5)
                                  .query(b"\x01\x00"))
            finally:
                link.close()
        sim.close()
        self.assertIsNone(sim.error)


@requires_dump
class TestTheDefaultIsUnchanged(DumpUnchanged):
    def test_without_the_flag_there_is_no_route(self):
        from ecu_sim import Med9Handlers
        h = Med9Handlers(str(DUMP), animate=False)
        self.assertIsNone(h.obd)
        with self.assertRaises(RuntimeError):
            h.obd_request(b"\x01\x00")


if __name__ == "__main__":                                    # pragma: no cover
    unittest.main()
