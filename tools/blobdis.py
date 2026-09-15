#!/usr/bin/env python3
"""Disassemble a raw big-endian PowerPC blob at a chosen CPU address.

Needed because `llvm-objdump` cannot read a headerless binary (it has no
`-b binary`, unlike GNU objdump), and the last check before a patch is written
to flash must look at the bytes exactly as the CPU will fetch them, not at the
ELF they came from (docs/04_re_guidelines.md section 6).

Usage:
    python3 tools/blobdis.py FILE --addr 0x145000
    python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x20004 --len 0x40
    python3 tools/blobdis.py FILE --addr 0x145000 --check-sda

`--check-sda` exits non-zero if any instruction reads or writes r2 or r13,
the ECU's small-data base registers (docs/02_memory_map.md section 4): patch
code must never touch them.

Requires `capstone` 5.0.x from requirements.txt (pin 5.x; 6.0 changes the
PowerPC API).
"""
from __future__ import annotations

import argparse
import sys

try:
    from capstone import CS_ARCH_PPC, CS_MODE_32, CS_MODE_BIG_ENDIAN, Cs
except ImportError:                                        # pragma: no cover
    sys.exit("capstone is missing: pip install -r requirements.txt")

SDA_REGISTERS = ("r2", "r13")


def disassemble(data: bytes, address: int):
    md = Cs(CS_ARCH_PPC, CS_MODE_32 | CS_MODE_BIG_ENDIAN)
    md.detail = False
    offset = 0
    for insn in md.disasm(data, address):
        while offset < insn.address - address:             # undecodable words
            word = data[offset:offset + 4]
            yield address + offset, word, ".long", "0x%08X" % int.from_bytes(word, "big")
            offset += 4
        yield insn.address, insn.bytes, insn.mnemonic, insn.op_str
        offset = insn.address - address + insn.size


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("file")
    parser.add_argument("--addr", type=lambda s: int(s, 0), default=0,
                        help="CPU address the first byte is fetched from")
    parser.add_argument("--file-off", type=lambda s: int(s, 0), default=0,
                        help="start this many bytes into the file")
    parser.add_argument("--len", dest="length", type=lambda s: int(s, 0),
                        default=None, help="number of bytes to disassemble")
    parser.add_argument("--check-sda", action="store_true",
                        help="fail if r2 or r13 is referenced")
    args = parser.parse_args(argv)

    with open(args.file, "rb") as handle:
        data = handle.read()
    data = data[args.file_off:]
    if args.length is not None:
        data = data[:args.length]

    address = args.addr or args.file_off
    violations = []
    for insn_addr, raw, mnemonic, operands in disassemble(data, address):
        print("%08X  %-12s %-8s %s"
              % (insn_addr, raw.hex(" ").upper(), mnemonic, operands))
        if args.check_sda:
            fields = operands.replace("(", " ").replace(")", " ").replace(",", " ")
            if any(token in SDA_REGISTERS for token in fields.split()):
                violations.append((insn_addr, mnemonic, operands))

    if args.check_sda:
        if violations:
            print("\nFAIL: %d instruction(s) touch the ECU small-data registers:"
                  % len(violations), file=sys.stderr)
            for insn_addr, mnemonic, operands in violations:
                print("  %08X  %s %s" % (insn_addr, mnemonic, operands), file=sys.stderr)
            return 1
        print("\nOK: no reference to r2 or r13")
    return 0


if __name__ == "__main__":
    sys.exit(main())
