"""Unicorn harness for the MED9.1.1 firmware (issue #21, Unicorn half).

Loads `data/passat_azx_ori.bin` into the verified ISB=1 address space
(`emu/memmap.py`, from docs/02_memory_map.md section 3), sets the boot
register values the firmware itself sets (r1, r13, r2, MSR) and runs either a
single function (`call`) or a free-running stretch of code (`run`).

Nothing is ever patched in the image: peripherals are modelled with
zero-filled stub pages plus read hooks, so a test can hand the code the value
it is waiting for without touching flash.

Usage:
    from emu import Med9Emu
    emu = Med9Emu("data/passat_azx_ori.bin")
    res = emu.call(0x11E44, args=[0xFF800140], mem={0x7FE9E8: b"\\x01"})
    print(hex(res.r3))          # -> 0xff800040

CLI:
    python3 -m emu.core data/passat_azx_ori.bin --call 0x11E44 --arg 0xFF800140
"""
from __future__ import annotations

import struct
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from unicorn import (
    UC_ARCH_PPC,
    UC_HOOK_CODE,
    UC_HOOK_INSN_INVALID,
    UC_HOOK_INTR,
    UC_HOOK_MEM_READ,
    UC_HOOK_MEM_UNMAPPED,
    UC_HOOK_MEM_WRITE,
    UC_MODE_32,
    UC_MODE_BIG_ENDIAN,
    Uc,
    UcError,
)
from unicorn.ppc_const import (
    UC_CPU_PPC32_603E_V4_1,
    UC_PPC_REG_0,
    UC_PPC_REG_1,
    UC_PPC_REG_2,
    UC_PPC_REG_3,
    UC_PPC_REG_13,
    UC_PPC_REG_CR,
    UC_PPC_REG_CTR,
    UC_PPC_REG_LR,
    UC_PPC_REG_MSR,
    UC_PPC_REG_PC,
    UC_PPC_REG_XER,
)

from . import memmap as mm

_TOOLS = Path(__file__).resolve().parent.parent / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))
import med9lib as m  # noqa: E402

GPR = tuple(UC_PPC_REG_0 + i for i in range(32))
assert GPR[1] == UC_PPC_REG_1 and GPR[13] == UC_PPC_REG_13

# Boot values written by the firmware itself (docs/02 section 4).
STACK_TOP = 0x7FEFFC          # file 0x10D8-0x10DC
R13_SDA = m.R13_SDA           # 0x7FFFF0, file 0x10E0-0x10E4
R2_BOOT = m.R2_SDA2           # 0x017FF0, file 0x10E8-0x10EC
R2_APP = 0x5C9FF0             # file 0x86010 etc.
MSR_BOOT = 0x3942             # file 0x1040-0x1044: FP=1, ME=1, FE0/FE1=1, IP=1, RI=1, EE=0

# mfspr/mtspr are XFX form under primary opcode 31.
_XO_MFSPR = 339
_XO_MTSPR = 467
# SPRs the QEMU 603e core inside Unicorn implements.  Everything else reads
# back as 0 and swallows the write without trapping, which is why the harness
# reports them (see emu/README.md).  Caveat: 528-543 are IBAT/DBAT on a 603e
# but MPC5xx region/control registers (MI_GRA, L2U_*, BBCMCR ...) on this part,
# so a write there lands in the wrong model rather than being lost - the value
# is still not observable by the firmware, which is what matters here.
MODELLED_SPRS = {1, 8, 9, 18, 19, 22, 26, 27, 268, 269, 272, 273, 274, 275,
                 284, 285, 287, 528, 529, 530, 531, 532, 533, 534, 535, 536,
                 537, 538, 539, 540, 541, 542, 543, 1008, 1009, 1010}


@dataclass
class Access:
    """One peripheral / absent-memory access."""
    pc: int
    addr: int
    size: int
    value: int
    write: bool
    region: str

    def __str__(self) -> str:
        d = "W" if self.write else "R"
        return (f"{d}{self.size} pc={self.pc:#010x} {mm.describe(self.addr)} "
                f"= {self.value:#0{2 + 2 * self.size}x}")


@dataclass
class SprAccess:
    pc: int
    spr: int
    write: bool
    modelled: bool

    def __str__(self) -> str:
        d = "mtspr" if self.write else "mfspr"
        return f"{d} {self.spr} at {self.pc:#010x}" + ("" if self.modelled else "  [NOT MODELLED by Unicorn]")


@dataclass
class Result:
    """Outcome of a `call()` or `run()`."""
    stop_reason: str
    pc: int
    insns: int
    regs: dict[str, int] = field(default_factory=dict)
    accesses: list[Access] = field(default_factory=list)
    sprs: list[SprAccess] = field(default_factory=list)
    unmapped: list[Access] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    pc_trace: list[int] = field(default_factory=list)
    read_counts: Counter = field(default_factory=Counter)
    _emu: "Med9Emu | None" = None

    @property
    def r3(self) -> int:
        return self.regs["r3"]

    @property
    def ok(self) -> bool:
        return self.stop_reason == "returned"

    def snapshot(self, addr: int, size: int) -> bytes:
        """Memory as it stands after the run."""
        assert self._emu is not None
        return self._emu.read(addr, size)

    def hot_reads(self, n: int = 5) -> list[tuple[tuple[int, int], int]]:
        """(pc, addr) pairs read most often - a spin on a peripheral shows here."""
        return self.read_counts.most_common(n)

    def summary(self) -> str:
        lines = [f"stop={self.stop_reason} pc={self.pc:#010x} insns={self.insns} r3={self.regs.get('r3', 0):#010x}"]
        for i in self.issues:
            lines.append(f"  ! {i}")
        return "\n".join(lines)


class Med9Emu:
    """Unicorn PPC emulator carrying the verified MED9.1.1 memory map."""

    def __init__(self, dump_path: str | Path = "data/passat_azx_ori.bin", *,
                 r2: str | int = "app", msr: int = MSR_BOOT,
                 log_periph: bool = True, watch_spr: bool = False,
                 trace: bool = False, max_log: int = 20000,
                 map_high_vectors: bool = True):
        mm.check_consistency()
        self.dump = m.load_dump(str(dump_path))
        self.log_periph = log_periph
        self.watch_spr = watch_spr
        self.trace = trace
        self.max_log = max_log
        self.map_high_vectors = map_high_vectors
        self.default_r2 = self._resolve_r2(r2)
        self.default_msr = msr
        self._read_stubs: dict[int, object] = {}
        self._cur: Result | None = None
        self._stop_pc: int | None = None
        self.uc = Uc(UC_ARCH_PPC, UC_MODE_32 | UC_MODE_BIG_ENDIAN)
        self.uc.ctl_set_cpu_model(UC_CPU_PPC32_603E_V4_1)
        self._map_all()
        self._install_hooks()
        self.reset()

    # -- setup ------------------------------------------------------------
    @staticmethod
    def _resolve_r2(r2: str | int) -> int:
        if isinstance(r2, int):
            return r2
        return {"boot": R2_BOOT, "app": R2_APP}[r2]

    def _map_all(self) -> None:
        for r in mm.REGIONS:
            self.uc.mem_map(r.start, r.size)
        if self.map_high_vectors:
            self.uc.mem_map(mm.HIGH_VECTOR_BASE, mm.HIGH_VECTOR_SIZE)
        self.uc.mem_map(mm.RETURN_MAGIC, mm.RETURN_MAGIC_SIZE)

    def reset(self) -> None:
        """Flash back to the dump, RAM/peripherals back to zero, registers cleared."""
        for r in mm.REGIONS:
            if r.kind == mm.KIND_FLASH:
                self.uc.mem_write(r.start, bytes(self.dump[r.file_start:r.file_start + r.file_size]))
            else:
                self.uc.mem_write(r.start, b"\0" * r.size)
        if self.map_high_vectors:
            self.uc.mem_write(mm.HIGH_VECTOR_BASE, bytes(self.dump[0:mm.HIGH_VECTOR_SIZE]))
        self.uc.mem_write(mm.RETURN_MAGIC, b"\0" * mm.RETURN_MAGIC_SIZE)
        for i, reg in enumerate(GPR):
            self.uc.reg_write(reg, 0)
        for reg in (UC_PPC_REG_LR, UC_PPC_REG_CTR, UC_PPC_REG_CR, UC_PPC_REG_XER):
            self.uc.reg_write(reg, 0)
        self.uc.reg_write(UC_PPC_REG_MSR, self.default_msr)
        self.uc.reg_write(UC_PPC_REG_1, STACK_TOP)
        self.uc.reg_write(UC_PPC_REG_13, R13_SDA)
        self.uc.reg_write(UC_PPC_REG_2, self.default_r2)

    # -- peripheral stubs -------------------------------------------------
    def stub_read(self, addr: int, value) -> None:
        """Make a peripheral read at `addr` yield `value`.

        `value` is an int (written as a big-endian word of the access size) or
        a callable `(pc, addr, size) -> int`.  Stubs never touch flash.
        """
        self._read_stubs[addr] = value

    def clear_stubs(self) -> None:
        self._read_stubs.clear()

    # -- memory -----------------------------------------------------------
    def read(self, addr: int, size: int) -> bytes:
        return bytes(self.uc.mem_read(addr, size))

    def read_u32(self, addr: int) -> int:
        return struct.unpack(">I", self.read(addr, 4))[0]

    def write(self, addr: int, data: bytes | int, size: int = 1) -> None:
        if isinstance(data, int):
            data = data.to_bytes(size, "big")
        self.uc.mem_write(addr, bytes(data))

    # -- hooks ------------------------------------------------------------
    def _install_hooks(self) -> None:
        for r in mm.REGIONS:
            if r.kind in (mm.KIND_PERIPH, mm.KIND_ABSENT):
                self.uc.hook_add(UC_HOOK_MEM_READ, self._hook_read, begin=r.start, end=r.end - 1)
                self.uc.hook_add(UC_HOOK_MEM_WRITE, self._hook_write, begin=r.start, end=r.end - 1)
        self.uc.hook_add(UC_HOOK_MEM_UNMAPPED, self._hook_unmapped)
        self.uc.hook_add(UC_HOOK_INTR, self._hook_intr)
        self.uc.hook_add(UC_HOOK_INSN_INVALID, self._hook_invalid)
        self.uc.hook_add(UC_HOOK_CODE, self._hook_code)

    def _log(self, acc: Access) -> None:
        cur = self._cur
        if cur is None:
            return
        if not acc.write:
            cur.read_counts[(acc.pc, acc.addr)] += 1
        if self.log_periph and len(cur.accesses) < self.max_log:
            cur.accesses.append(acc)

    def _hook_read(self, uc, access, address, size, value, ud):
        pc = uc.reg_read(UC_PPC_REG_PC)
        stub = self._read_stubs.get(address)
        if stub is not None:
            v = stub(pc, address, size) if callable(stub) else stub
            uc.mem_write(address, (v & ((1 << (8 * size)) - 1)).to_bytes(size, "big"))
        got = int.from_bytes(bytes(uc.mem_read(address, size)), "big")
        r = mm.region_of(address)
        self._log(Access(pc, address, size, got, False, r.name if r else "?"))

    def _hook_write(self, uc, access, address, size, value, ud):
        pc = uc.reg_read(UC_PPC_REG_PC)
        r = mm.region_of(address)
        self._log(Access(pc, address, size, value & 0xFFFFFFFF, True, r.name if r else "?"))

    def _hook_unmapped(self, uc, access, address, size, value, ud):
        pc = uc.reg_read(UC_PPC_REG_PC)
        fetch = access in (16, 20)   # UC_MEM_FETCH_UNMAPPED / UC_MEM_FETCH_PROT
        cur = self._cur
        acc = Access(pc, address, size, value & 0xFFFFFFFF, access in (18, 21), False and "" or "unmapped")
        if cur is not None:
            cur.unmapped.append(acc)
            cur.issues.append(
                f"{'FETCH' if fetch else 'data'} access to unmapped {address:#010x} from pc {pc:#010x}")
        if fetch:
            self._stop_pc = pc
            uc.emu_stop()
            return False
        # Data access: give it a zero page and carry on, so one stray pointer
        # does not end the run.  The access stays in result.unmapped.
        page = address & ~(mm.PAGE - 1)
        try:
            uc.mem_map(page, mm.PAGE)
        except UcError:
            return False
        return True

    def _hook_intr(self, uc, intno, ud):
        pc = uc.reg_read(UC_PPC_REG_PC)
        if self._cur is not None:
            self._cur.issues.append(f"exception/interrupt {intno} at pc {pc:#010x}")
        self._stop_pc = pc
        uc.emu_stop()

    def _hook_invalid(self, uc, ud):
        pc = uc.reg_read(UC_PPC_REG_PC)
        if self._cur is not None:
            self._cur.issues.append(f"invalid instruction at pc {pc:#010x}")
        self._stop_pc = pc
        uc.emu_stop()
        return False

    def _hook_code(self, uc, address, size, ud):
        cur = self._cur
        if cur is None:
            return
        cur.insns += 1
        if self.trace and len(cur.pc_trace) < self.max_log:
            cur.pc_trace.append(address)
        if self.watch_spr and size == 4:
            w = int.from_bytes(bytes(uc.mem_read(address, 4)), "big")
            if (w >> 26) & 0x3F == 31:
                xo = (w >> 1) & 0x3FF
                if xo in (_XO_MFSPR, _XO_MTSPR):
                    f = (w >> 11) & 0x3FF
                    spr = ((f & 0x1F) << 5) | ((f >> 5) & 0x1F)
                    modelled = spr in MODELLED_SPRS
                    cur.sprs.append(SprAccess(address, spr, xo == _XO_MTSPR, modelled))
                    if not modelled:
                        msg = f"unmodelled SPR {spr} {'written' if xo == _XO_MTSPR else 'read'} at {address:#010x}"
                        if msg not in cur.issues:
                            cur.issues.append(msg)

    # -- running ----------------------------------------------------------
    def _regs(self) -> dict[str, int]:
        d = {f"r{i}": self.uc.reg_read(GPR[i]) for i in range(32)}
        d["lr"] = self.uc.reg_read(UC_PPC_REG_LR)
        d["ctr"] = self.uc.reg_read(UC_PPC_REG_CTR)
        d["cr"] = self.uc.reg_read(UC_PPC_REG_CR)
        d["xer"] = self.uc.reg_read(UC_PPC_REG_XER)
        d["msr"] = self.uc.reg_read(UC_PPC_REG_MSR)
        d["pc"] = self.uc.reg_read(UC_PPC_REG_PC)
        return d

    def _prepare(self, regs, mem, r2, msr) -> None:
        self.uc.reg_write(UC_PPC_REG_MSR, self.default_msr if msr is None else msr)
        self.uc.reg_write(UC_PPC_REG_1, STACK_TOP)
        self.uc.reg_write(UC_PPC_REG_13, R13_SDA)
        self.uc.reg_write(UC_PPC_REG_2, self.default_r2 if r2 is None else self._resolve_r2(r2))
        for addr, data in (mem or {}).items():
            self.write(addr, data)
        for name, val in (regs or {}).items():
            self.uc.reg_write(self._reg_id(name), val)

    @staticmethod
    def _reg_id(name: str) -> int:
        n = name.lower()
        if n.startswith("r") and n[1:].isdigit():
            return GPR[int(n[1:])]
        return {"lr": UC_PPC_REG_LR, "ctr": UC_PPC_REG_CTR, "cr": UC_PPC_REG_CR,
                "xer": UC_PPC_REG_XER, "msr": UC_PPC_REG_MSR, "pc": UC_PPC_REG_PC}[n]

    def call(self, addr: int, args=(), regs=None, mem=None, *,
             r2=None, msr=None, max_insns: int = 2_000_000,
             reset: bool = True) -> Result:
        """Call the function at CPU address `addr` with EABI arguments in r3..r10.

        `regs` overrides single registers after the arguments, `mem` writes
        bytes before the call (`{0x7FE9E8: b"\\x01"}`).  Returns a `Result`
        whose `.r3` is the return value and whose `.snapshot()` reads memory.
        """
        if reset:
            self.reset()
        self._prepare(regs, mem, r2, msr)
        for i, a in enumerate(args):
            self.uc.reg_write(GPR[3 + i], a & 0xFFFFFFFF)
        if regs:  # explicit overrides win over positional args
            for name, val in regs.items():
                self.uc.reg_write(self._reg_id(name), val)
        self.uc.reg_write(UC_PPC_REG_LR, mm.RETURN_MAGIC)
        return self._go(addr, mm.RETURN_MAGIC, max_insns)

    def run(self, start: int, until: int | None = None, *, regs=None, mem=None,
            r2=None, msr=None, max_insns: int = 2_000_000,
            reset: bool = True) -> Result:
        """Free-run from `start` (used for the reset-vector trace)."""
        if reset:
            self.reset()
        self._prepare(regs, mem, r2, msr)
        return self._go(start, mm.RETURN_MAGIC if until is None else until, max_insns)

    def _go(self, start: int, until: int, max_insns: int) -> Result:
        res = Result(stop_reason="", pc=start, insns=0)
        res._emu = self
        self._cur = res
        self._stop_pc = None
        try:
            self.uc.emu_start(start, until, 0, max_insns)
        except UcError as e:
            res.issues.append(f"UcError {e} at pc {self.uc.reg_read(UC_PPC_REG_PC):#010x}")
        finally:
            self._cur = None
        res.regs = self._regs()
        res.pc = self._stop_pc if self._stop_pc is not None else res.regs["pc"]
        if res.issues and any(i.startswith(("FETCH", "exception", "invalid", "UcError")) for i in res.issues):
            res.stop_reason = "fault"
        elif res.regs["pc"] == until or res.pc == until:
            res.stop_reason = "returned"
        elif res.insns >= max_insns:
            res.stop_reason = "insn_limit"
        else:
            res.stop_reason = "stopped"
        return res


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dump", nargs="?", default="data/passat_azx_ori.bin")
    ap.add_argument("--call", required=True, help="CPU address of the function")
    ap.add_argument("--arg", action="append", default=[], help="argument (r3, r4, ...)")
    ap.add_argument("--set", action="append", default=[], metavar="ADDR=HEX",
                    help="write bytes before the call, e.g. 0x7FE9E8=01")
    ap.add_argument("--r2", default="app", choices=("app", "boot"))
    ap.add_argument("--max-insns", type=int, default=2_000_000)
    a = ap.parse_args(argv)
    emu = Med9Emu(a.dump, r2=a.r2, watch_spr=True)
    mem = {}
    for s in a.set:
        k, v = s.split("=", 1)
        mem[int(k, 0)] = bytes.fromhex(v)
    res = emu.call(int(a.call, 0), [int(x, 0) for x in a.arg], mem=mem, max_insns=a.max_insns)
    print(res.summary())
    for acc in res.accesses[:50]:
        print("   ", acc)
    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
