"""patches/common/ + tools/patch_gen.py + tools/patch_apply.py (issues #25, #27).

Four layers, so that a missing cross compiler only costs the first one:

  1. build      `make check` for hello and ff_counter; skipped with a clear
                message when LLVM_DIR is not installed
  2. descriptor branch encoding, and `patch.json` still matching a fresh build
  3. apply      both patches onto temporary copies: checksums ALL OK, bindiff
                clean, and every way of applying them wrongly refused
  4. emulate    the ff_counter trampoline and the hooked site, on the patched
                image, in the Unicorn harness (emu/README.md)

Nothing here writes to `data/` (DumpUnchanged asserts it) and nothing is ever
flashed.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.common import DUMP, DUMP_SHA256, REPO, DumpUnchanged, requires_dump

import bindiff  # noqa: E402  (tests.common put tools/ on sys.path)
import checksum as cs  # noqa: E402
import gen_stock_header  # noqa: E402
import med9lib as m  # noqa: E402
import patch_apply  # noqa: E402
import patch_gen  # noqa: E402

PATCHES = REPO / "patches"
HELLO = PATCHES / "examples" / "hello_patch"
FF_COUNTER = PATCHES / "ff_counter"
FF_FUEL = PATCHES / "ff_fuel"          # brief D1, #32; its own tests are in
                                       # tests/test_ff_fuel_patch.py
LLVM_DIR = Path(os.environ.get("LLVM_DIR",
                               "/Users/carlo/toolchains/LLVM-23.1.1-macOS-ARM64"))

# --- ff_counter facts under test -------------------------------------------
# re/findings/scheduler.md section 8 and patches/ff_counter/README.md.
HOOK_SITE = 0x12067C
HOOK_OLD = bytes.fromhex("4bffe9b1")        # bl 0x11F02C
NEXT_INSN = 0x120680
STOCK_LEAF = 0x11F02C
CLEARED_BYTE = 0x7FE889                     # cleared by the stock leaf
CLEARED_HALF = 0x800E18
PATCH_FLASH = 0x150000
PATCH_RAM = 0x7FFB00                        # re/findings/ram.md 8.1 (C2, #23); runtime confirmation pending
ALIVE = 0xFC01
SRAM_START, SRAM_LEN = 0x7F8000, 0x10000    # the whole ECU RAM (emu/memmap.py)
STACK_TOP = 0x7FEFFC                        # emu resets r1 here
HOOK_TAIL_FRAME = 16                        # patches/common/hooks.h
HOOK_FULL_FRAME = 80                        # patches/common/hooks.h

toolchain_available = (LLVM_DIR / "bin" / "clang").is_file()
requires_toolchain = unittest.skipUnless(
    toolchain_available,
    f"no PowerPC cross compiler at {LLVM_DIR}; install it with the curl/tar "
    f"lines of docs/03_tooling.md section 3.1, or set LLVM_DIR")

try:
    from emu import Med9Emu
    emu_available = True
except Exception:                                            # pragma: no cover
    emu_available = False
requires_emu = unittest.skipUnless(emu_available, "unicorn / emu package missing")


@contextlib.contextmanager
def quiet():
    """checksum.verify and gen_stock_header print a summary line either way."""
    with contextlib.redirect_stdout(io.StringIO()):
        yield


def make(patch_dir: Path, *targets: str) -> subprocess.CompletedProcess:
    return subprocess.run(["make", "-C", str(patch_dir), *targets],
                          capture_output=True, text=True)


def load_patch(patch_dir: Path) -> dict:
    return json.loads((patch_dir / "patch.json").read_text())


def blob_of(patch_dir: Path) -> bytes:
    """The blob bytes the checked-in patch.json promises to write."""
    for ch in load_patch(patch_dir)["changes"]:
        if ch.get("kind") == "blob":
            return bytes.fromhex(ch["new"])
    raise AssertionError(f"{patch_dir}/patch.json has no blob change")


# ---------------------------------------------------------------- 1. build --
@requires_toolchain
class TestBuild(unittest.TestCase):
    """`make check` is the gate: no r2/r13, no small data, sizes agree."""

    def _check(self, patch_dir: Path):
        r = make(patch_dir, "check")
        self.assertEqual(r.returncode, 0, f"make check failed:\n{r.stdout}\n{r.stderr}")
        self.assertIn("OK: r2 and r13 are never referenced", r.stdout)
        self.assertIn("OK: no .sdata/.sbss/.srodata/.sdata2", r.stdout)
        self.assertIn("== linked flash size", r.stdout)

    def test_hello_builds(self):
        self._check(HELLO)

    def test_ff_counter_builds(self):
        self._check(FF_COUNTER)

    def test_ff_fuel_builds(self):
        self._check(FF_FUEL)

    def test_blob_has_no_sda_reference(self):
        """blobdis reads the raw bytes, not the ELF: the last check before flash."""
        make(FF_COUNTER, "all")
        r = make(FF_COUNTER, "dump")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("OK: no reference to r2 or r13", r.stdout)
        self.assertIn("ba       0x11f02c", r.stdout)     # the tail branch

    def test_patch_json_still_matches_a_fresh_build(self):
        """`changes` is generated; a stale patch.json must not survive a build."""
        for patch_dir in (HELLO, FF_COUNTER, FF_FUEL):
            with self.subTest(patch=patch_dir.name):
                r = make(patch_dir, "all")
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                fresh, _notes = patch_gen.generate(patch_dir, DUMP)
                self.assertEqual(fresh, load_patch(patch_dir),
                                 f"{patch_dir}/patch.json is stale: run `make gen`")

    def test_a_missing_placement_symbol_fails_the_link(self):
        """patch.ld has no defaults on purpose (see its header comment)."""
        self.assertEqual(make(FF_COUNTER, "all").returncode, 0)
        obj = FF_COUNTER / "build" / "hook.o"
        r = subprocess.run(
            [str(LLVM_DIR / "bin" / "ld.lld"), "-m", "elf32ppc",
             "-T", str(PATCHES / "common" / "patch.ld"),
             "--defsym=PATCH_FLASH=0x150000", str(obj), "-o", os.devnull],
            capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0, r.stderr)
        self.assertIn("PATCH_RAM", r.stderr)


# ----------------------------------------------------------- 2. descriptor --
class TestBranchEncoding(unittest.TestCase):
    """I-form: word = 0x48000000 | (LI & 0x03FFFFFC) | (AA << 1) | LK."""

    def test_the_stock_word_decodes_to_the_documented_leaf(self):
        kind, target = patch_gen.decode_branch(int.from_bytes(HOOK_OLD, "big"), HOOK_SITE)
        self.assertEqual((kind, target), ("bl", STOCK_LEAF))

    def test_the_hook_word(self):
        self.assertEqual(patch_gen.encode_branch(HOOK_SITE, PATCH_FLASH, "bl"),
                         0x4802F985)

    def test_absolute_and_relative_forms(self):
        self.assertEqual(patch_gen.encode_branch(0x150000, STOCK_LEAF, "ba"), 0x4811F02E)
        self.assertEqual(patch_gen.encode_branch(0x150000, STOCK_LEAF, "b"), 0x4BFCF02C)

    def test_roundtrip(self):
        for kind in ("b", "bl", "ba", "bla"):
            for target in (0x000100, STOCK_LEAF, 0x1AFFFC, 0x47FFFC):
                with self.subTest(kind=kind, target=target):
                    word = patch_gen.encode_branch(PATCH_FLASH, target, kind)
                    self.assertEqual(patch_gen.decode_branch(word, PATCH_FLASH),
                                     (kind, target))

    def test_misaligned_and_out_of_reach_are_refused(self):
        with self.assertRaises(patch_gen.PatchError):
            patch_gen.encode_branch(PATCH_FLASH, STOCK_LEAF + 2, "bl")
        with self.assertRaises(patch_gen.PatchError):
            patch_gen.encode_branch(PATCH_FLASH, PATCH_FLASH + 0x4000000, "b")


class TestStockHeader(unittest.TestCase):
    def test_the_generated_header_is_current(self):
        with quiet():
            rc = gen_stock_header.main(["--check"])
        self.assertEqual(rc, 0, "run python3 tools/gen_stock_header.py")

    def test_every_address_comes_from_symbols_csv(self):
        text = (PATCHES / "common" / "med9_stock.h").read_text()
        syms = gen_stock_header.load_symbols()
        for name, suffix in gen_stock_header.WANTED:
            macro = gen_stock_header.macro_name(name, suffix)
            addr = int(syms[name]["cpu_addr"], 16)
            self.assertIn(f"#define {macro}", text)
            self.assertIn(f"0x{addr:08X}", text)


# ------------------------------------------------------------------ facts --
@requires_dump
class TestStockImageFacts(DumpUnchanged):
    """The assumptions the patch is built on, checked against the dump itself."""

    def test_the_hook_site_holds_the_documented_word(self):
        data = m.load_dump(str(DUMP))
        off = m.cpu_to_file(HOOK_SITE)
        self.assertEqual(bytes(data[off:off + 4]), HOOK_OLD)

    def test_the_blob_region_of_the_stock_image_is_blank(self):
        data = m.load_dump(str(DUMP))
        for patch_dir in (HELLO, FF_COUNTER):
            with self.subTest(patch=patch_dir.name):
                blob = blob_of(patch_dir)
                flash = int(load_patch(patch_dir)["build"]["flash"], 0)
                off = m.cpu_to_file(flash)
                self.assertEqual(set(data[off:off + len(blob)]), {0xFF})

    def test_the_whole_free_area_is_blank(self):
        data = m.load_dump(str(DUMP))
        self.assertEqual(set(data[patch_gen.FREE_FLASH_START:patch_gen.FREE_FLASH_END]),
                         {0xFF})


# ------------------------------------------------------------------ 3. apply --
@requires_dump
class TestApply(DumpUnchanged):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _apply(self, patch_dir: Path) -> tuple[Path, dict]:
        out = self.tmp / f"{patch_dir.name}.bin"
        data, report, _warnings = patch_apply.apply_patch(DUMP, patch_dir)
        out.write_bytes(bytes(data))
        return out, report

    def test_hello_applies(self):
        out, report = self._apply(HELLO)
        self.assertTrue(report["ok"])
        self.assertEqual(report["bytes"]["unexpected"], 0)
        with quiet():
            self.assertTrue(cs.verify(m.load_dump(str(out)), quiet=True))

    def test_ff_counter_applies(self):
        out, report = self._apply(FF_COUNTER)
        self.assertTrue(report["ok"])
        self.assertEqual(report["bytes"]["unexpected"], 0)
        with quiet():
            self.assertTrue(cs.verify(m.load_dump(str(out)), quiet=True))
        patched = m.load_dump(str(out))
        off = m.cpu_to_file(HOOK_SITE)
        self.assertEqual(bytes(patched[off:off + 4]), bytes.fromhex("4802f985"))
        blob_off = m.cpu_to_file(PATCH_FLASH)
        self.assertEqual(bytes(patched[blob_off:blob_off + len(blob_of(FF_COUNTER))]),
                         blob_of(FF_COUNTER))

    def test_bindiff_sees_only_the_patch_and_its_descriptors(self):
        out, _ = self._apply(FF_COUNTER)
        ranges, report = bindiff.diff(m.load_dump(str(DUMP)), m.load_dump(str(out)),
                                      str(FF_COUNTER / "patch.json"))
        self.assertTrue(report["ok"], report["issues"])
        self.assertEqual(report["counts"]["unexpected"], 0)
        self.assertTrue(any(r.cls == bindiff.CLS_DESCRIPTOR for r in ranges))

    def test_the_identification_block_is_untouched(self):
        out, _ = self._apply(FF_COUNTER)
        stock, patched = m.load_dump(str(DUMP)), m.load_dump(str(out))
        off = m.cpu_to_file(patch_apply.IDENT_START)
        n = patch_apply.IDENT_END - patch_apply.IDENT_START
        self.assertEqual(bytes(patched[off:off + n]), bytes(stock[off:off + n]))

    def test_applying_twice_fails_on_the_old_bytes(self):
        out, _ = self._apply(FF_COUNTER)
        with self.assertRaises(patch_apply.ApplyError) as cm:
            patch_apply.apply_patch(out, FF_COUNTER)
        # It trips on base_sha256 first, which is the point: a patched image is
        # not a base image.  Re-check with the sha requirement relaxed.
        self.assertIn("base_sha256", str(cm.exception))

        patch = load_patch(FF_COUNTER)
        patch["base_sha256"] = patch_apply.hashlib.sha256(
            Path(out).read_bytes()).hexdigest()
        p2 = self.tmp / "twice.json"
        p2.write_text(json.dumps(patch))
        with self.assertRaises(patch_apply.ApplyError) as cm:
            patch_apply.apply_patch(out, p2)
        self.assertIn("already applied", str(cm.exception))

    def test_a_wrong_base_sha256_is_refused(self):
        patch = load_patch(FF_COUNTER)
        patch["base_sha256"] = "0" * 64
        p = self.tmp / "wrongbase.json"
        p.write_text(json.dumps(patch))
        with self.assertRaises(patch_apply.ApplyError) as cm:
            patch_apply.apply_patch(DUMP, p)
        self.assertIn("base_sha256", str(cm.exception))

    def _refuse(self, changes: list[dict]) -> str:
        patch = load_patch(FF_COUNTER)
        patch["changes"] = changes
        p = self.tmp / "region.json"
        p.write_text(json.dumps(patch))
        with self.assertRaises(patch_apply.ApplyError) as cm:
            patch_apply.apply_patch(DUMP, p)
        return str(cm.exception)

    def test_the_boot_block_is_refused(self):
        self.assertIn("boot block", self._refuse(
            [{"addr": "0x006C00", "new": "60000000", "why": "immobiliser"}]))

    def test_the_on_chip_flash_is_refused(self):
        self.assertIn("on-chip flash", self._refuse(
            [{"addr": "0x41C3A0", "new": "60000000", "why": "rksplit"}]))

    def test_the_on_chip_flash_needs_the_explicit_flag(self):
        """D1 (#32): 0x404000-0x47FFFF *is* in our read and is checksummed, so
        B6's fuel hook can be applied - but only with "onchip_edit", and the
        apply always warns (docs/06 section 1, 2026-09-16)."""
        stock = m.load_dump(str(DUMP))
        site = 0x42247C                               # bl rksplit, injection.md 6.2
        old = bytes(stock[m.cpu_to_file(site):m.cpu_to_file(site) + 4])
        self.assertEqual(old.hex(), "4bff9f25")
        ch = {"addr": f"{site:#08x}", "old": old.hex(), "new": "60000000",
              "why": "brief B6's fuel hook site"}
        self.assertIn("on-chip flash", self._refuse([dict(ch)]))
        # calibration_edit must NOT open this range
        self.assertIn("on-chip flash", self._refuse([dict(ch, calibration_edit=True)]))

        patch = load_patch(FF_COUNTER)
        patch["changes"] = [dict(ch, onchip_edit=True)]
        p = self.tmp / "onchip_ok.json"
        p.write_text(json.dumps(patch))
        _data, report, warnings = patch_apply.apply_patch(DUMP, p)
        self.assertTrue(report["ok"], report["issues"])
        self.assertTrue(any("KESSv2" in w for w in warnings), warnings)

    def test_the_missing_16k_of_on_chip_flash_can_never_be_written(self):
        msg = self._refuse([{"addr": "0x400010", "new": "60000000",
                             "onchip_edit": True, "calibration_edit": True,
                             "why": "should be impossible"}])
        self.assertIn("not in our read", msg)
        self.assertIn("not unlockable", msg)

    def test_onchip_edit_does_not_open_the_stock_calibration(self):
        self.assertIn("stock calibration", self._refuse(
            [{"addr": "0x5D2600", "new": "0102", "onchip_edit": True,
              "why": "wrong flag"}]))

    def test_stock_calibration_needs_the_explicit_flag(self):
        # 0x5D2600 == file 0x1D2600, inside the guarded stock calibration
        # 0x1C0000-0x1DFFFF (docs/06_patch_pipeline.md section 3).
        cal = {"addr": "0x5D2600", "new": "0102", "why": "a map edit"}
        self.assertIn("stock calibration", self._refuse([dict(cal)]))
        # The low alias of the same bytes must be refused too.
        self.assertIn("stock calibration", self._refuse(
            [{"addr": "0x1D2600", "new": "0102", "why": "same bytes, low alias"}]))
        # With the flag it goes through (and the calibration block re-checksums).
        patch = load_patch(FF_COUNTER)
        patch["changes"] = [dict(cal, calibration_edit=True,
                                 old=bytes(m.load_dump(str(DUMP))[0x1D2600:0x1D2602]).hex())]
        p = self.tmp / "cal_ok.json"
        p.write_text(json.dumps(patch))
        _data, report, _w = patch_apply.apply_patch(DUMP, p)
        self.assertTrue(report["ok"], report["issues"])

    def test_the_new_calibration_area_is_not_guarded(self):
        """0x5E2510-0x5EFFFF is where OUR calibration goes (docs/06 section 3)."""
        patch = load_patch(FF_COUNTER)
        patch["changes"] = [{"addr": "0x5E2600", "new": "0102",
                             "old": bytes(m.load_dump(str(DUMP))[0x1E2600:0x1E2602]).hex(),
                             "why": "new flex-fuel calibration"}]
        p = self.tmp / "newcal.json"
        p.write_text(json.dumps(patch))
        _data, report, _w = patch_apply.apply_patch(DUMP, p)
        self.assertTrue(report["ok"], report["issues"])

    def test_the_identification_block_can_never_be_edited(self):
        msg = self._refuse([{"addr": "0x1CEE20", "new": "00", "calibration_edit": True,
                             "why": "should be impossible"}])
        self.assertIn("identification block", msg)

    def test_a_ram_block_that_is_not_verified_produces_a_warning(self):
        """ff_counter is "static" (no static reference, snapshots pending),
        hello is "example"; both must warn, and only "verified" is silent."""
        _data, _report, warnings = patch_apply.apply_patch(DUMP, FF_COUNTER)
        self.assertTrue(any('"static"' in w for w in warnings), warnings)
        self.assertTrue(any("issue #23" in w and "Do not flash" in w for w in warnings), warnings)
        _data, _report, warnings = patch_apply.apply_patch(DUMP, HELLO)
        self.assertTrue(any('"example"' in w and "Do not flash" in w for w in warnings), warnings)

    def test_the_original_dump_is_untouched(self):
        self._apply(FF_COUNTER)
        self.assertEqual(patch_apply.hashlib.sha256(DUMP.read_bytes()).hexdigest(),
                         DUMP_SHA256)


# --------------------------------------------------------------- 4. emulate --
@requires_dump
@requires_emu
class TestEmulatedHook(DumpUnchanged):
    """The trampoline and the hooked site, run on the patched image."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp = Path(tempfile.mkdtemp())
        cls.image = cls.tmp / "ff_counter.bin"
        data, _report, _w = patch_apply.apply_patch(DUMP, FF_COUNTER)
        cls.image.write_bytes(bytes(data))
        cls.hook_addr = int(load_patch(FF_COUNTER)["build"]["symbols"]["ff_counter_hook"], 0)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        super().tearDownClass()

    @staticmethod
    def _seeded(ticks: int, alive: int) -> dict[int, bytes]:
        """A warm RAM image: a counter mid-count and both stock cells dirty."""
        return {PATCH_RAM: struct.pack(">IHH", ticks, alive, 0),
                CLEARED_BYTE: b"\xAA",
                CLEARED_HALF: b"\xBE\xEF"}

    @staticmethod
    def _expected_before(seeds: dict[int, bytes]) -> bytearray:
        """RAM as Med9Emu.reset() leaves it, plus the seeds."""
        img = bytearray(SRAM_LEN)
        for addr, data in seeds.items():
            img[addr - SRAM_START:addr - SRAM_START + len(data)] = data
        return img

    @staticmethod
    def _changed(before: bytes, after: bytes) -> list[int]:
        return [SRAM_START + i for i in range(SRAM_LEN) if before[i] != after[i]]

    def test_the_trampoline_increments_the_counter_by_exactly_one(self):
        emu = Med9Emu(self.image)
        seeds = self._seeded(0x1234, ALIVE)
        res = emu.call(self.hook_addr, mem=seeds)

        self.assertTrue(res.ok, f"{res.stop_reason}: {res.issues}")
        after = res.snapshot(SRAM_START, SRAM_LEN)
        ticks, alive, reserved = struct.unpack(
            ">IHH", after[PATCH_RAM - SRAM_START:PATCH_RAM - SRAM_START + 8])
        self.assertEqual(ticks, 0x1235)
        self.assertEqual(alive, ALIVE)
        self.assertEqual(reserved, 0)

    def test_the_original_leaf_still_runs(self):
        emu = Med9Emu(self.image)
        res = emu.call(self.hook_addr, mem=self._seeded(1, ALIVE))
        after = res.snapshot(SRAM_START, SRAM_LEN)
        self.assertEqual(after[CLEARED_BYTE - SRAM_START], 0)
        self.assertEqual(after[CLEARED_HALF - SRAM_START:CLEARED_HALF - SRAM_START + 2],
                         b"\x00\x00")

    def test_r1_is_restored_and_nothing_else_in_ram_moves(self):
        emu = Med9Emu(self.image)
        seeds = self._seeded(7, ALIVE)
        res = emu.call(self.hook_addr, mem=seeds)
        self.assertEqual(res.regs["r1"], STACK_TOP)

        changed = set(self._changed(self._expected_before(seeds),
                                    res.snapshot(SRAM_START, SRAM_LEN)))
        allowed = set(range(PATCH_RAM, PATCH_RAM + 8))
        allowed |= {CLEARED_BYTE, CLEARED_HALF, CLEARED_HALF + 1}
        # HOOK_TAIL's own 16-byte frame below the entry r1: back chain at
        # r1-16 and the saved LR at r1-4 (patches/common/hooks.h).
        allowed |= set(range(STACK_TOP - HOOK_TAIL_FRAME, STACK_TOP))
        self.assertEqual(changed - allowed, set(),
                         f"unexpected RAM writes: {[hex(a) for a in sorted(changed - allowed)]}")

    def test_a_cold_start_zeroes_the_counter_and_writes_the_alive_pattern(self):
        """Nothing initialises our .bss, so the first tick has to do it."""
        emu = Med9Emu(self.image)
        res = emu.call(self.hook_addr)             # RAM is all zero after reset
        after = res.snapshot(SRAM_START, SRAM_LEN)
        ticks, alive, _ = struct.unpack(
            ">IHH", after[PATCH_RAM - SRAM_START:PATCH_RAM - SRAM_START + 8])
        self.assertEqual((ticks, alive), (1, ALIVE))

    def test_the_hooked_site_behaves_like_the_stock_site(self):
        """Run 0x12067C..0x120680 on both images and diff the whole of RAM."""
        snaps, insns = {}, {}
        for tag, image in (("stock", DUMP), ("patched", self.image)):
            emu = Med9Emu(image)
            res = emu.run(HOOK_SITE, until=NEXT_INSN)
            self.assertTrue(res.ok, f"{tag}: {res.stop_reason} {res.issues}")
            self.assertEqual(res.regs["r1"], STACK_TOP, tag)
            snaps[tag], insns[tag] = res.snapshot(SRAM_START, SRAM_LEN), res.insns

        self.assertEqual(insns["stock"], 5)         # bl + the 4-instruction leaf
        self.assertEqual(insns["patched"], 29)      # + trampoline + ff_counter_tick

        changed = set(self._changed(snaps["stock"], snaps["patched"]))
        allowed = set(range(PATCH_RAM, PATCH_RAM + 8))
        allowed |= set(range(STACK_TOP - HOOK_TAIL_FRAME, STACK_TOP))
        self.assertEqual(changed - allowed, set(),
                         f"the patch changed RAM it must not touch: "
                         f"{[hex(a) for a in sorted(changed - allowed)]}")
        # and it really did write the counter block
        self.assertTrue(changed & set(range(PATCH_RAM, PATCH_RAM + 8)))

    def test_the_stock_leaf_is_reached_through_the_tail_branch(self):
        """`ba 0x11F02C` is the last instruction executed before the leaf."""
        emu = Med9Emu(self.image, trace=True)
        res = emu.call(self.hook_addr, mem=self._seeded(1, ALIVE))
        trace = res.pc_trace
        self.assertEqual(trace[0], self.hook_addr)
        self.assertIn(STOCK_LEAF, trace)
        tail = self.hook_addr + HOOK_TAIL_FRAME + 0x0C    # the `ba`, 8th word
        self.assertEqual(trace[trace.index(STOCK_LEAF) - 1], tail)
        self.assertEqual(emu.read(tail, 4),
                         patch_gen.encode_branch(tail, STOCK_LEAF, "ba")
                         .to_bytes(4, "big"))
        # and the leaf's own four instructions are the last thing that runs
        self.assertEqual(trace[-4:], [STOCK_LEAF + 4 * i for i in range(4)])


# ------------------------------------------------------- 5. HOOK_FULL ------
@requires_dump
@requires_emu
@requires_toolchain
class TestHookFull(DumpUnchanged):
    """HOOK_FULL is unused by Flash 1 but is what brief D1's fuel patch needs,
    so it is proven here rather than the first time it matters.

    `tests/fixtures/hook_full_probe.S` wraps a register-destroying stub in a
    HOOK_FULL trampoline that tail-branches to the same stock leaf. The test
    then asks one question: is the register state after the trampoline the same
    as after calling that leaf directly?
    """

    PROBE_BASE = 0x160000          # free flash, nowhere near ff_counter
    PROBE_RAM = 0x807E00
    SCRATCH = 0x807EC0             # inside the probe's RAM block, unused by it
    SEED = {**{f"r{i}": 0x1000 + i for i in range(3, 13)},
            "r0": 0xDEAD0000, "cr": 0x12345678, "ctr": 0xCAFEBABE,
            "xer": 0xE000007F}
    MEM = {CLEARED_BYTE: b"\xAA", CLEARED_HALF: b"\xBE\xEF"}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp = Path(tempfile.mkdtemp())
        src = REPO / "tests" / "fixtures" / "hook_full_probe.S"
        obj, elf = cls.tmp / "probe.o", cls.tmp / "probe.elf"
        cls.bin, cls.sym = cls.tmp / "probe.bin", cls.tmp / "probe.sym"
        bindir = LLVM_DIR / "bin"
        common = PATCHES / "common"
        cls._run([bindir / "clang", "--target=powerpc-unknown-eabi", "-mcpu=603e",
                  "-I", common, "-c", src, "-o", obj])
        cls._run([bindir / "ld.lld", "-m", "elf32ppc", "--no-dynamic-linker",
                  "--discard-locals", "-T", common / "patch.ld",
                  f"--defsym=PATCH_FLASH={cls.PROBE_BASE:#x}",
                  f"--defsym=PATCH_RAM={cls.PROBE_RAM:#x}",
                  "--defsym=PATCH_RAM_SIZE=0x100", "-o", elf, obj])
        cls._run([bindir / "llvm-objcopy", "-O", "binary", elf, cls.bin])
        with open(cls.sym, "w") as fh:
            subprocess.run([str(bindir / "llvm-nm"), "-n", str(elf)],
                           stdout=fh, check=True)
        cls.syms = patch_gen.parse_sym(cls.sym)
        cls.blob = cls.bin.read_bytes()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        super().tearDownClass()

    @staticmethod
    def _run(argv):
        r = subprocess.run([str(a) for a in argv], capture_output=True, text=True)
        assert r.returncode == 0, f"{argv[0]} failed:\n{r.stdout}\n{r.stderr}"

    def _emu_with_probe(self):
        emu = Med9Emu(DUMP)
        emu.reset()
        emu.write(self.PROBE_BASE, self.blob)
        return emu

    def test_the_trampoline_leaves_the_registers_the_stock_call_would(self):
        direct = Med9Emu(DUMP).call(STOCK_LEAF, regs=dict(self.SEED), mem=dict(self.MEM))
        self.assertTrue(direct.ok, direct.issues)

        emu = self._emu_with_probe()
        hooked = emu.call(self.syms["test_full_hook"], regs=dict(self.SEED),
                          mem=dict(self.MEM), reset=False)
        self.assertTrue(hooked.ok, f"{hooked.stop_reason}: {hooked.issues}")

        # r12 is clobbered by the stock leaf itself (`li r12,0`), so comparing
        # against the direct call - rather than against the seed - is the point.
        watched = [f"r{i}" for i in range(13)] + ["cr", "ctr", "lr", "r1"]
        differ = {k: (hex(direct.regs[k]), hex(hooked.regs[k]))
                  for k in watched if direct.regs[k] != hooked.regs[k]}
        self.assertEqual(differ, {},
                         "HOOK_FULL did not restore the EABI volatile set")
        self.assertEqual(hooked.regs["r1"], STACK_TOP)

    def test_the_stub_really_destroys_those_registers(self):
        """Otherwise the test above would pass with an empty trampoline."""
        emu = self._emu_with_probe()
        res = emu.call(self.syms["clobber_all"], regs=dict(self.SEED), reset=False)
        self.assertTrue(res.ok, res.issues)
        for name in ["r0"] + [f"r{i}" for i in range(3, 13)]:
            self.assertEqual(res.regs[name], 0xFFFFFFFF, name)
        self.assertEqual(res.regs["ctr"], 0xFFFFFFFF)
        self.assertEqual(res.regs["cr"], 0xFFFFFFFF)

    def test_xer_is_restored_as_the_guest_sees_it(self):
        """uc_reg_read(XER) drops SO/OV/CA, so ask the guest with mfxer.

        VERIFIED-DYNAMIC 2026-09-16 (brief C1): `mfxer` -> `stw` -> `lwz` ->
        `mtxer` round-trips exactly inside Unicorn for every seed tried, while
        the Python accessor shows 0x20000000 as 0. Recorded in emu/README.md.
        """
        emu = self._emu_with_probe()
        res = emu.call(self.syms["xer_probe"], args=[0, self.SCRATCH],
                       mem=dict(self.MEM), regs={"xer": self.SEED["xer"]},
                       reset=False)
        self.assertTrue(res.ok, f"{res.stop_reason}: {res.issues}")
        before, after = struct.unpack(">II", emu.read(self.SCRATCH, 8))
        self.assertNotEqual(before, 0, "seed XER so the test can fail")
        self.assertEqual(before, after,
                         f"HOOK_FULL lost XER: {before:#010x} -> {after:#010x}")

    def test_the_stock_leaf_still_runs(self):
        emu = self._emu_with_probe()
        res = emu.call(self.syms["test_full_hook"], regs=dict(self.SEED),
                       mem=dict(self.MEM), reset=False)
        self.assertTrue(res.ok, res.issues)
        self.assertEqual(emu.read(CLEARED_BYTE, 1), b"\x00")
        self.assertEqual(emu.read(CLEARED_HALF, 2), b"\x00\x00")

    def test_the_frame_stays_inside_the_documented_size(self):
        emu = self._emu_with_probe()
        res = emu.call(self.syms["test_full_hook"], regs=dict(self.SEED),
                       mem=dict(self.MEM), reset=False)
        self.assertTrue(res.ok, res.issues)
        below = emu.read(STACK_TOP - 0x100, 0x100 - HOOK_FULL_FRAME)
        self.assertEqual(set(below), {0},
                         "HOOK_FULL wrote below its own 80-byte frame")


if __name__ == "__main__":
    unittest.main()
