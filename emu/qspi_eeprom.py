#!/usr/bin/env python3
"""QSMCM/QSPI queue engine + M95160 SPI EEPROM, as an `emu` device model.

`emu/core.py` maps the peripheral blocks as zero pages and only *logs* writes,
so until now the EEPROM answered nothing: brief D2 could stage a block and
queue a commit, but the request record never left status 1 because no device
ever drove MISO (`re/findings/eeprom.md` section 9, `kwp.md` section 12.6).
This module closes that hole with two pieces that mirror what the dump says
the hardware is (brief E4, issue #38):

:class:`M95160`
    the byte-level device: WREN/WRDI/RDSR/WRSR/READ/WRITE, the WEL and WIP
    status bits, 16-bit addressing, 32-byte pages that wrap inside the page on
    a write, and a 2 KB array that loads from and saves to a file.
:class:`QspiEeprom`
    the QSPI queue engine of `eeprom.md` section 1.3: a write to SPCR1 that
    sets SPE runs queue entries NEWQP..ENDQP, pushes each TXRAM byte through
    the device while PCS0 is low, fills RXRAM, clears SPE and sets SPSR.SPIF.

Register facts, all VERIFIED-STATIC from `eeprom_qspi_xfer` (0x085920) and the
two boot self-tests (0x017A84, 0x017CF0); the disassembly lines are quoted in
`re/findings/eeprom.md` section 10:

====================  ==========================================
0x705018 SPCR0 u16    MSTR/BITS/CPOL/CPHA/SPBR (not modelled)
0x70501A SPCR1 u16    bit15 SPE; the hardware CLEARS it at the end
                      of the queue (0x017AB8 spins on it)
0x70501C SPCR2 u16    ENDQP bits 8-12, NEWQP bits 0-4 (0x0859AC
                      writes ``((n-1) & 0x1F) << 8``)
0x70501E SPCR3 u8     bit2 LOOPQ (0x017D74 ``ori r11,r11,4``)
0x70501F SPSR  u8     bit7 SPIF (0x0859E8 keeps bit 24 of the word)
0x705140 RXRAM        32 x u16
0x705180 TXRAM        32 x u16
0x7051C0 CMDRAM       32 x u8: CONT 0x80 | BITSE 0x40 | DT 0x20 |
                      DSCK 0x10 | PCS3..PCS0 levels
====================  ==========================================

**Why the queue does not run inside the write hook.** Unicorn calls a
`UC_HOOK_MEM_WRITE` callback *before* it stores the value, so anything the
model wrote to SPCR1 would be undone by the pending store of the very
instruction that armed it.  The model therefore only arms itself there and
runs the queue on the next QSPI access -- which is always the driver's own
`lbz SPSR` poll, in both the boot (0x017994) and the application (0x0859E4)
drivers.  :meth:`QspiEeprom.run_pending` forces it from Python.

Usage::

    from emu import Med9Emu
    from emu.qspi_eeprom import QspiEeprom, M95160

    emu = Med9Emu("work/ff_fuel.bin")
    dev = QspiEeprom.install(emu, image="work/eeprom.bin")
    emu.call(0x62280, reset=False)          # nvm_read_all_blocks
    dev.device.save("work/eeprom.bin")

CLI::

    python3 -m emu.qspi_eeprom --blank work/eeprom.bin      # synthetic image
    python3 -m emu.qspi_eeprom --self-test                  # boot self-tests
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent.parent / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

# --- QSMCM registers (re/findings/mpc5xx_registers.md section 7) -------------
QSMCM_BASE = 0x705000
SPCR0 = 0x705018
SPCR1 = 0x70501A
SPCR2 = 0x70501C
SPCR3 = 0x70501E
SPSR = 0x70501F
RXRAM = 0x705140
TXRAM = 0x705180
CMDRAM = 0x7051C0
QSPI_END = 0x7051E0
QUEUE_LEN = 32

SPE = 0x8000            # SPCR1
SPIF = 0x80             # SPSR
LOOPQ = 0x04            # SPCR3
CONT = 0x80             # CMDRAM
PCS0 = 0x01             # CMDRAM, the level driven on PCS0: 0 = EEPROM selected

# --- M95160 instruction set (eeprom.md sections 1.6 and 2) ------------------
WREN, WRDI, RDSR, WRSR, READ, WRITE = 0x06, 0x04, 0x05, 0x01, 0x03, 0x02
ST_WIP, ST_WEL = 0x01, 0x02

DEVICE_SIZE = 0x800
PAGE = 0x20

# --- the firmware entry points this module drives or is driven by -----------
BOOT_SPI_SELECT = 0x017E14      # FUN_00017E14, fills 0x7F83A0/A4/A8
BOOT_WEL_TEST = 0x017A84        # FUN_00017A84, WRDI/RDSR/WREN/RDSR/WRDI
BOOT_LOOPBACK_TEST = 0x017CF0   # FUN_00017CF0, SPCR3.LOOPQ + a 5-word pattern
APP_SPI_CONFIG = 0x085888       # eeprom_spi_config
APP_WRITE_BYTE = 0x085A8C       # eeprom_write_byte(addr, data)
APP_WRITE_BYTES = 0x085B54      # eeprom_write_bytes(addr, n, src)
APP_READ_BYTES = 0x085BC0       # eeprom_read_bytes(addr, n, dst)


class M95160:
    """An ST M95160: 2 KB, 32-byte pages, 16-bit address, SPI mode 0.

    One transaction is `select()`, a run of `xfer(byte) -> byte`, `deselect()`.
    A WRITE only reaches the array on `deselect()`, which is what the real
    part does and what makes the CONT bit of the command RAM matter.
    """

    def __init__(self, data: bytes | bytearray | None = None, *,
                 wip_polls: int = 0):
        self.mem = bytearray(data if data is not None else b"\xFF" * DEVICE_SIZE)
        if len(self.mem) != DEVICE_SIZE:
            raise ValueError(f"an M95160 image is {DEVICE_SIZE} bytes, "
                             f"got {len(self.mem)}")
        #: how many RDSR polls still report WIP after a page write.  0 is the
        #: emulator default (there is no time base, kwp.md 12.6); a bench-like
        #: 5 ms write is any positive number.
        self.wip_polls = wip_polls
        self.status = 0x00
        self.writes = 0
        self.reads = 0
        self._wip_left = 0
        self._reset_transaction()

    # -- image -----------------------------------------------------------
    @classmethod
    def load(cls, path, **kw) -> "M95160":
        raw = Path(path).read_bytes()
        if len(raw) < DEVICE_SIZE:
            raise ValueError(f"{path} is {len(raw)} bytes, expected {DEVICE_SIZE}")
        return cls(raw[:DEVICE_SIZE], **kw)

    def save(self, path) -> None:
        Path(path).write_bytes(bytes(self.mem))

    # -- transaction -----------------------------------------------------
    def _reset_transaction(self) -> None:
        self._op = None
        self._phase = 0
        self._addr = 0
        self._buf: list[int] = []

    def select(self) -> None:
        self._reset_transaction()

    def deselect(self) -> None:
        if self._op == WRITE and (self.status & ST_WEL) and self._buf:
            base = self._addr & ~(PAGE - 1)
            off = self._addr & (PAGE - 1)
            for byte in self._buf[:PAGE]:
                self.mem[base + off] = byte
                off = (off + 1) & (PAGE - 1)
            self.writes += 1
            self.status &= ~ST_WEL
            self._wip_left = self.wip_polls
            if self._wip_left:
                self.status |= ST_WIP
        elif self._op == WRSR and self._buf and (self.status & ST_WEL):
            # block-protect and SRWD bits only; WIP/WEL are not writable
            self.status = (self.status & 0x03) | (self._buf[0] & 0x8C)
            self.status &= ~ST_WEL
        self._reset_transaction()

    def xfer(self, out: int) -> int:
        """One 8-bit transfer: `out` goes to MOSI, the return value is MISO."""
        out &= 0xFF
        if self._op is None:
            self._op = out
            self._phase = 0
            if out == WREN:
                if not (self.status & ST_WIP):
                    self.status |= ST_WEL
            elif out == WRDI:
                self.status &= ~ST_WEL
            return 0xFF
        if self._op == RDSR:
            value = self.status
            if self.status & ST_WIP:
                self._wip_left -= 1
                if self._wip_left <= 0:
                    self.status &= ~ST_WIP
            return value
        if self._op in (READ, WRITE):
            if self._phase == 0:
                self._addr = (out & 0xFF) << 8
                self._phase = 1
                return 0xFF
            if self._phase == 1:
                self._addr |= out & 0xFF
                self._addr &= DEVICE_SIZE - 1
                self._phase = 2
                return 0xFF
            if self._op == READ:
                if self.status & ST_WIP:
                    return 0xFF
                value = self.mem[self._addr]
                self._addr = (self._addr + 1) & (DEVICE_SIZE - 1)
                self.reads += 1
                return value
            self._buf.append(out)
            return 0xFF
        if self._op == WRSR:
            self._buf.append(out)
            return 0xFF
        return 0xFF          # an opcode this part does not implement


class QspiEeprom:
    """The QSMCM queue engine, wired to one :class:`M95160` on PCS0."""

    def __init__(self, device: M95160 | None = None):
        self.device = device or M95160()
        self.selected = False
        self.queues = 0
        self.transfers = 0
        self.log: list[tuple[int, int, int, int]] = []   # (i, cmd, tx, rx)
        self.keep_log = False
        self._armed = False

    # -- installation ----------------------------------------------------
    @classmethod
    def install(cls, emu, *, image=None, device: M95160 | None = None,
                wip_polls: int = 0) -> "QspiEeprom":
        """Attach a model to `emu` over the QSPI register block."""
        if device is None:
            device = (M95160.load(image, wip_polls=wip_polls) if image
                      else M95160(wip_polls=wip_polls))
        self = cls(device)
        emu.add_device(QSMCM_BASE + 0x18, QSPI_END, self)
        return self

    # -- the emu device protocol ----------------------------------------
    def on_write(self, emu, addr: int, size: int, value: int) -> None:
        if self._armed:
            self.run_pending(emu)
        if addr <= SPCR1 < addr + size:
            byte = (value >> (8 * (addr + size - 1 - SPCR1))) & 0xFF
            if byte & 0x80:
                self._armed = True

    def on_read(self, emu, addr: int, size: int) -> None:
        if self._armed:
            self.run_pending(emu)

    # -- the queue -------------------------------------------------------
    def run_pending(self, emu) -> bool:
        """Run the queue if SPE is set; return True if it did."""
        self._armed = False
        spcr1 = struct.unpack(">H", emu.read(SPCR1, 2))[0]
        if not (spcr1 & SPE):
            return False
        spcr2 = struct.unpack(">H", emu.read(SPCR2, 2))[0]
        newqp = spcr2 & 0x1F
        endqp = (spcr2 >> 8) & 0x1F
        loopback = bool(emu.read(SPCR3, 1)[0] & LOOPQ)

        i = newqp
        for _ in range(QUEUE_LEN):
            cmd = emu.read(CMDRAM + i, 1)[0]
            tx = struct.unpack(">H", emu.read(TXRAM + 2 * i, 2))[0]
            if loopback:
                rx = tx
            else:
                rx = self._transfer(cmd, tx & 0xFF)
            emu.write(RXRAM + 2 * i, rx & 0xFFFF, 2)
            self.transfers += 1
            if self.keep_log:
                self.log.append((i, cmd, tx & 0xFF, rx & 0xFF))
            if i == endqp:
                break
            i = (i + 1) & 0x1F
        self._deselect()                 # end of queue: PCS goes back to idle
        self.queues += 1

        emu.write(SPCR1, spcr1 & ~SPE, 2)
        emu.write(SPSR, emu.read(SPSR, 1)[0] | SPIF, 1)
        return True

    def _transfer(self, cmd: int, tx: int) -> int:
        if cmd & PCS0:                   # PCS0 driven high: the part is idle
            self._deselect()
            return 0
        if not self.selected:
            self.device.select()
            self.selected = True
        rx = self.device.xfer(tx)
        if not (cmd & CONT):
            self._deselect()
        return rx

    def _deselect(self) -> None:
        if self.selected:
            self.device.deselect()
            self.selected = False


# ---------------------------------------------------------------------------
# a synthetic device image the block manager accepts
# ---------------------------------------------------------------------------
def blank_image(dump_path="data/passat_azx_ori.bin", fill: int = 0xFF,
                payloads: dict[int, bytes] | None = None) -> bytes:
    """A 2 KB image whose every block copy carries a valid EEP_CONF checksum.

    `payloads` overrides single blocks' payloads (`{8: b"\\x55"}` writes 0x55
    at block 8 payload +0).  `tools/eeprom_map.py --check` on the result
    prints ALL OK, which is what makes it usable as the start-up image for
    `nvm_read_all_blocks` (0x62280).
    """
    import eeprom_map as em
    import med9lib as ml

    data = ml.load_dump(str(dump_path))
    blocks = em.read_blocks(data)
    raw = bytearray([fill & 0xFF]) * em.DEVICE_SIZE
    order = sorted(blocks, key=lambda b: b.addr)
    for k, b in enumerate(order):
        end = order[k + 1].addr if k + 1 < len(order) else em.DEVICE_SIZE
        copies = max((end - b.addr) // (b.pages * em.PAGE), 1)
        want = (payloads or {}).get(b.idx)
        for n in range(copies):
            a = b.copy_addr(n)
            if want is not None:
                raw[a:a + len(want)] = want[:b.payload]
            if b.idx == 0:
                continue                     # exempt (FUN_000619AC)
            payload = bytes(raw[a:a + b.payload])
            struct.pack_into(">H", raw, a + b.payload, em.block_checksum(payload))
    return bytes(raw)


# ---------------------------------------------------------------------------
# the EEP_CONF block manager's device layer
# ---------------------------------------------------------------------------
#: `nvm_dev_read_fp` / `nvm_dev_write_fp` -- the two RAM function pointers the
#: block manager reaches the device through (eeprom.md section 3.5 and the new
#: section 10).  Both are BSS and **no instruction in the image stores to
#: them**, so in the emulator they are 0 and the manager's `blrl` walks to
#: address 0, which is the reset vector and ends in the OS halt spin at
#: 0x110F0 -- exactly what brief D2 saw after state 0x53.
NVM_DEV_READ_FP = 0x7FAB70          # read call site 0x05FD88
NVM_DEV_WRITE_FP = 0x7FAB74         # write call site 0x06068C
NVM_READ_ALL_BLOCKS = 0x06227C      # NOT 0x62280: eeprom.md 3.5 named the
NVM_WRITE_ALL_BLOCKS = 0x06273C     # instruction after `mr r11,r1` (section 10)

#: default scratch for the two trampolines; external SRAM above everything the
#: application and `logging/ecu_sim.py` use.
TRAMPOLINE_BASE = 0x807C00

_TRAMP_WORDS = (
    0x7C0802A6,   # mflr   r0
    0x9421FFE0,   # stwu   r1,-32(r1)
    0x90010024,   # stw    r0,36(r1)
    0x93E10008,   # stw    r31,8(r1)
    0x7CDF3378,   # mr     r31,r6          ; the status pointer
    None,         # lis    r12,<target hi>
    None,         # ori    r12,r12,<target lo>
    0x7D8903A6,   # mtctr  r12
    0x4E800421,   # bctrl                  ; the real primitive runs
    0x39800001,   # li     r12,1
    0x999F0000,   # stb    r12,0(r31)      ; *status = 1  (done, no error)
    0x80010024,   # lwz    r0,36(r1)
    0x7C0803A6,   # mtlr   r0
    0x83E10008,   # lwz    r31,8(r1)
    0x38210020,   # addi   r1,r1,32
    0x4E800020,   # blr                    ; r3 is the primitive's own count
)


def _trampoline(target: int) -> bytes:
    words = list(_TRAMP_WORDS)
    words[5] = 0x3D800000 | ((target >> 16) & 0xFFFF)
    words[6] = 0x618C0000 | (target & 0xFFFF)
    return b"".join(struct.pack(">I", w) for w in words)


class NvmDeviceBinding:
    """Bind the block manager's device pointers to the real §2 primitives.

    The binding itself is **HYPOTHESIS** -- which driver the factory software
    installs at 0x7FAB70/0x7FAB74 is `eeprom.md` section 7 open question 1 and
    is still open.  What is VERIFIED-STATIC is the *signature* the two call
    sites require, and this class implements exactly that:

    ``rc = (*fp)(eepAddr, len, buf, statusPtr)`` -- a non-zero `rc` means
    "transfer started" (0x05FDBC branches to the error path on zero) and the
    driver later writes **1** into `*statusPtr` for success (0x05FE84).

    Both trampolines are 16 instructions in emulator scratch RAM; they call
    `eeprom_read_bytes` (0x085BC0) and `eeprom_write_bytes` (0x085B54)
    unchanged, so the bytes really travel through the QSPI queue and the
    :class:`M95160`.  Nothing is written into the image.
    """

    def __init__(self, emu, base: int = TRAMPOLINE_BASE):
        self.emu = emu
        self.read_tramp = base
        self.write_tramp = base + 0x40
        emu.write(self.read_tramp, _trampoline(APP_READ_BYTES))
        emu.write(self.write_tramp, _trampoline(APP_WRITE_BYTES))
        emu.write(NVM_DEV_READ_FP, self.read_tramp, 4)
        emu.write(NVM_DEV_WRITE_FP, self.write_tramp, 4)
        emu.call(APP_SPI_CONFIG, reset=False)

    def rebind(self) -> None:
        """Re-apply after a `Med9Emu.reset()` (which zeroes all of RAM)."""
        self.__init__(self.emu, self.read_tramp)


def install_eeprom(emu, *, image=None, device: M95160 | None = None,
                   wip_polls: int = 0, bind: bool = True):
    """The whole EEPROM stack on one emulator: `(QspiEeprom, NvmDeviceBinding)`."""
    qspi = QspiEeprom.install(emu, image=image, device=device,
                              wip_polls=wip_polls)
    binding = NvmDeviceBinding(emu) if bind else None
    return qspi, binding


# ---------------------------------------------------------------------------
# the firmware's own proof that the model behaves like the part
# ---------------------------------------------------------------------------
def self_test(dump_path="data/passat_azx_ori.bin") -> bool:
    """Drive the model with the firmware's own EEPROM code and report."""
    from emu import Med9Emu

    ok = True
    rows: list[tuple[str, str, bool]] = []

    def step(label, good, detail=""):
        nonlocal ok
        ok = ok and bool(good)
        rows.append((label, detail, bool(good)))

    boot = Med9Emu(dump_path, r2="boot")
    dev = QspiEeprom.install(boot)
    boot.call(BOOT_SPI_SELECT, reset=False)
    res = boot.call(BOOT_WEL_TEST, reset=False)
    step("boot WEL test (0x017A84)", res.ok and res.regs["r3"] == 1,
         f"r3={res.regs['r3']}")
    res = boot.call(BOOT_LOOPBACK_TEST, args=[0x807000], reset=False)
    step("boot loopback test (0x017CF0)",
         res.ok and res.regs["r3"] == 1 and boot.read(0x807000, 1) == b"\0",
         f"r3={res.regs['r3']} err={boot.read(0x807000, 1).hex()}")

    app = Med9Emu(dump_path, r2="app")
    dev = QspiEeprom.install(app)
    app.call(APP_SPI_CONFIG, reset=False)
    app.call(APP_WRITE_BYTE, args=[0x1C0, 0x5A], reset=False)
    step("eeprom_write_byte (0x085A8C)", dev.device.mem[0x1C0] == 0x5A,
         f"device[0x1C0]={dev.device.mem[0x1C0]:#04x}")
    dev.device.mem[0x000:0x040] = bytes(range(0x40))
    app.call(APP_READ_BYTES, args=[0x000, 0x40, 0x807100], reset=False)
    step("eeprom_read_bytes (0x085BC0), 0x40 B over three queues",
         app.read(0x807100, 0x40) == bytes(range(0x40)), "")
    app.write(0x807200, bytes([0xDE, 0xAD, 0xBE, 0xEF]))
    app.call(APP_WRITE_BYTES, args=[0x300, 4, 0x807200], reset=False)
    step("eeprom_write_bytes (0x085B54)",
         bytes(dev.device.mem[0x300:0x304]) == bytes([0xDE, 0xAD, 0xBE, 0xEF]), "")

    width = max(len(r[0]) for r in rows)
    for label, detail, good in rows:
        print(f"  {label:<{width}}  {detail:<28} {'ok' if good else 'FAIL'}")
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return ok


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dump", default="data/passat_azx_ori.bin")
    ap.add_argument("--blank", metavar="OUT",
                    help="write a synthetic 2 KB image with valid checksums")
    ap.add_argument("--set", action="append", default=[], metavar="BLK:OFF=HEX",
                    help="seed a block payload, e.g. 8:0=55")
    ap.add_argument("--self-test", action="store_true",
                    help="run the boot loopback and WEL tests against the model")
    a = ap.parse_args(argv)

    if a.blank:
        payloads: dict[int, bytes] = {}
        for spec in a.set:
            where, hexed = spec.split("=", 1)
            blk, off = (int(x, 0) for x in where.split(":"))
            payloads[blk] = (b"\xFF" * off) + bytes.fromhex(hexed)
        Path(a.blank).write_bytes(blank_image(a.dump, payloads=payloads))
        print(f"wrote {a.blank}")
        return 0

    if a.self_test:
        return 0 if self_test(a.dump) else 1

    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
