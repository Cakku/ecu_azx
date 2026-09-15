#!/usr/bin/env python3
"""Dynamically verify the KWP2000 SecurityAccess (SID 0x27) key algorithms.

Runs the real handler kwp_sid_27_h1 (CPU 0x36210) out of
data/passat_azx_ori.bin with the emu/ Unicorn harness, seeding only the RAM
globals the send-key success path needs (seed, level flags, security state and
the handler I/O struct). No ECU and no time-base are involved: the success path
never reads the time base, so the check is deterministic.

Confirms:
  * level 1 (sub 0x02): key = 5-round Galois LFSR of the seed, mask 0x5FBD5DBD
  * level 2 (sub 0x04): key = seed + 0x11170   (the community-reported pair)

Usage:
    python3 tools/kwp_seckey_verify.py [path/to/passat_azx_ori.bin]

Evidence for re/findings/kwp.md section 3 (brief B3, issue #13).
"""
import os
import struct
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from emu import Med9Emu

BIN = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, "data", "passat_azx_ori.bin")
H27 = 0x36210
STRUCT = 0x807800
BUF = 0x807900


def be32(x):
    return struct.pack(">I", x & 0xffffffff)


def key_level1(seed):
    x = seed & 0xffffffff
    for _ in range(5):
        if x < 0x80000000:
            x = (x * 2) & 0xffffffff
        else:
            x = ((x * 2 | 1) ^ 0x5fbd5dbd) & 0xffffffff
    return x


def key_level2(seed):
    return (seed + 0x11170) & 0xffffffff


def _io_struct(buf_ptr, length):
    s = bytearray(16)
    s[0:4] = be32(buf_ptr)
    struct.pack_into(">H", s, 6, length)
    return bytes(s)


def run_case(name, sub, key, seed, flagbit, state_before, rounds=None):
    emu = Med9Emu(BIN, r2="app")
    buf = bytearray(16)
    buf[0] = sub
    buf[1:5] = be32(key)
    mem = {
        STRUCT: _io_struct(BUF, 5),
        BUF: bytes(buf),
        0x7fb774: be32(seed),        # kwp_sec_seed
        0x7fb781: bytes([flagbit]),  # kwp_sec_level_flags
        0x7fb780: b"\x00",           # retry flag
        0x803d3c: bytes([state_before]),  # kwp_security_state
    }
    if rounds is not None:
        mem[0x7fb770] = bytes([rounds])   # kwp_sec_lfsr_rounds
    res = emu.call(H27, args=[0, STRUCT], mem=mem)
    st = res.snapshot(STRUCT, 16)
    ob = res.snapshot(BUF, 8)
    state_after = res.snapshot(0x803d3c, 1)[0]
    print("[%-12s] ok=%s resp=%s status=%d resp_len=%d state_after=%d" % (
        name, res.ok, ob[:5].hex(), st[0xa],
        struct.unpack('>H', st[8:10])[0], state_after))
    return st[0xa], state_after


def main():
    seed = 0x12345678
    print("=== Level 2 (sub 0x04): key = seed + 0x11170 ===")
    k2 = key_level2(seed)
    print("seed=%#010x  key=%#010x" % (seed, k2))
    ok = run_case("L2 correct", 0x04, k2, seed, 0x02, 0x00) == (1, 3)
    run_case("L2 wrong", 0x04, k2 ^ 1, seed, 0x02, 0x00)

    print("\n=== Level 1 (sub 0x02): key = LFSR5(seed) mask 0x5FBD5DBD ===")
    k1 = key_level1(seed)
    print("seed=%#010x  key=%#010x" % (seed, k1))
    ok = (run_case("L1 correct", 0x02, k1, seed, 0x01, 0x00, rounds=5) == (1, 2)) and ok
    run_case("L1 wrong", 0x02, k1 ^ 1, seed, 0x01, 0x00, rounds=5)

    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
