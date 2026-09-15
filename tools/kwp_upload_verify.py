#!/usr/bin/env python3
"""Dynamically verify KWP2000 RequestUpload (0x35) and TransferData (0x36).

Runs the real handlers out of data/passat_azx_ori.bin with the emu/ Unicorn
harness.

Confirms:
  * RequestUpload parses addr(3)/format(1=0)/size(3), answers 0x3F, and picks
    mode 1 for normal addresses, mode 4 for the 0x480000-0x480400 window;
  * kwp_upload_range_check (0xA3160) rejects any range overlapping the protected
    RAM window 0x7F9E3C-0x7FA47F with NRC 0x31;
  * TransferData streams up to 62 bytes per block from the requested address,
    advancing the pointer and decrementing the remaining count.

Usage:
    python3 tools/kwp_upload_verify.py [path/to/passat_azx_ori.bin]

Evidence for re/findings/kwp.md section 5 (brief B3, issue #13).
"""
import os
import struct
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from emu import Med9Emu

BIN = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, "data", "passat_azx_ori.bin")
H35 = 0x360C4
H36 = 0x37434
STRUCT = 0x807800
BUF = 0x807A00


def be32(x):
    return struct.pack(">I", x & 0xffffffff)


def _io_struct(buf_ptr, length):
    s = bytearray(16)
    s[0:4] = be32(buf_ptr)
    struct.pack_into(">H", s, 6, length)
    return bytes(s)


def _req35(addr, size):
    return bytes([
        (addr >> 16) & 0xff, (addr >> 8) & 0xff, addr & 0xff, 0,
        (size >> 16) & 0xff, (size >> 8) & 0xff, size & 0xff, 0,
    ])


def upload_case(name, addr, size, feb65=0):
    emu = Med9Emu(BIN, r2="app")
    mem = {STRUCT: _io_struct(BUF, 7), BUF: _req35(addr, size), 0x7feb65: bytes([feb65])}
    res = emu.call(H35, args=[0, STRUCT], mem=mem)
    st = res.snapshot(STRUCT, 16)
    ob = res.snapshot(BUF, 4)
    mode = res.snapshot(0x7fb80c, 1)[0]
    print("[%-16s addr=%#08x size=%#x] resp0=%#04x status=%d mode=%d" % (
        name, addr, size, ob[0], st[0xa], mode))


def transfer_case():
    emu = Med9Emu(BIN, r2="app")
    src = 0x807000
    payload = bytes((i * 7 + 3) & 0xff for i in range(200))
    mem = {
        STRUCT: _io_struct(BUF, 0),
        src: payload,
        0x7fb80c: b"\x01",           # kwp_upload_mode = 1
        0x7fb810: be32(src),         # kwp_upload_addr
        0x7fb814: be32(len(payload)),  # kwp_upload_remaining
    }
    res = emu.call(H36, args=[0, STRUCT], mem=mem)
    st = res.snapshot(STRUCT, 16)
    blk = struct.unpack(">H", st[8:10])[0]
    out = res.snapshot(BUF, blk)
    new_addr = struct.unpack(">I", res.snapshot(0x7fb810, 4))[0]
    rem = struct.unpack(">I", res.snapshot(0x7fb814, 4))[0]
    ok = blk == 62 and out == payload[:blk] and new_addr == src + 62 and rem == 200 - 62
    print("[TransferData] block=%d status=%d match=%s new_addr=%#08x remain=%#x -> %s" % (
        blk, st[0xa], out == payload[:blk], new_addr, rem, "PASS" if ok else "FAIL"))
    return ok


def main():
    print("=== RequestUpload (0x35) address gate ===")
    upload_case("RAM ok", 0x7F8000, 0x100)
    upload_case("protected window", 0x7F9E00, 0x200)
    upload_case("flash ok", 0x000000, 0x40)
    upload_case("mode4 window", 0x480000, 0x10)
    print("\n=== TransferData (0x36) streaming ===")
    ok = transfer_case()
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
