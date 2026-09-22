"""patches/ff_counter with both task sets hooked (brief F1, issues #27 / #44).

Brief E1 showed statically that **task set A** is the live one
(`re/findings/scheduler.md` §11.8), and Flash 1 hooked only the set-B raster,
so the counter it shipped with would never have moved.  The patch now hooks
one word in each 10 ms raster and records which one ran.  What is proven here:

  1. stock facts     both hook words, and the empty leaf the set-A hook
                     tail-branches to
  2. the variants    `patch.json` (HOOKS=both, two words, one on-chip) and
                     `patch.external.json` (HOOKS=external, the set-B word
                     alone) — the second is the pre-F1 patch byte for byte
  3. apply           both descriptors: ALL OK (65 blocks), clean bindiff, the
                     on-chip warning for the default build and none for the
                     other, and the external image's exact sha256
  4. fresh build     `make HOOKS=external` really does reproduce the old blob
                     (skipped without the cross compiler)
  5. emulate         each stub ticks once and sets exactly its own bit, the
                     set-A site moves nothing but the counter block and the
                     trampoline frame, and both stubs return into their task
                     with r1 and LR as at entry

The HOOK_TAIL trampoline itself and the set-B site are proven in
`tests/test_patch_framework.py`; nothing here writes to `data/` and nothing is
ever flashed.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.common import DUMP, REPO, DumpUnchanged, requires_dump

import bindiff  # noqa: E402  (tests.common put tools/ on sys.path)
import checksum as cs  # noqa: E402
import med9lib as m  # noqa: E402
import patch_apply  # noqa: E402
import patch_gen  # noqa: E402

FF_COUNTER = REPO / "patches" / "ff_counter"
BOTH_JSON = FF_COUNTER / "patch.json"
EXTERNAL_JSON = FF_COUNTER / "patch.external.json"

# --- the two hook sites (re/findings/scheduler.md §8 and §8.1) --------------
HOOK_A_SITE, HOOK_A_OLD = 0x432940, bytes.fromhex("4bc8b0a5")   # bl 0x0BD9E4
HOOK_B_SITE, HOOK_B_OLD = 0x12067C, bytes.fromhex("4bffe9b1")   # bl 0x11F02C
NOP_LEAF = 0x0BD9E4            # one `blr`, only caller 0x432940
CLR_LEAF = 0x11F02C            # clears 0x7FE889 and 0x800E18
ONCHIP_START, ONCHIP_END = 0x404000, 0x480000

PATCH_FLASH = 0x150000
PATCH_RAM = 0x7FFB00           # re/findings/ram.md §8.1 (C2, #23)
ALIVE = 0xFC01
SRC_A, SRC_B = 0x01, 0x02      # ff_src_seen bits, as in patches/ff_fuel
CLEARED_BYTE = 0x7FE889        # the set-B leaf's two cells
CLEARED_HALF = 0x800E18

SRAM_START, SRAM_LEN = 0x7F8000, 0x10000
STACK_TOP = 0x7FEFFC           # emu resets r1 here
HOOK_TAIL_FRAME = 16           # patches/common/hooks.h

# The patch exactly as it stood before brief F1 (commit e9386b9 on main):
# `make HOOKS=external` must reproduce all three of these numbers.
PRE_F1_BLOB_LEN = 96
PRE_F1_BLOB_SHA256 = "4645f45b1eeef4795aceab574938abcb51dbef6c70e36b074ca9d0ebfb4d95f5"
PRE_F1_IMAGE_SHA256 = "9ecde359ef91eabfa332ad80c3ad5667f5d5535ea72d0a0306da78a59ab375e2"

LLVM_DIR = Path(os.environ.get("LLVM_DIR",
                               "/Users/carlo/toolchains/LLVM-23.1.1-macOS-ARM64"))
requires_toolchain = unittest.skipUnless(
    (LLVM_DIR / "bin" / "clang").is_file(),
    f"no PowerPC cross compiler at {LLVM_DIR}; docs/03_tooling.md §3.1")

try:
    from emu import Med9Emu
    emu_available = True
except Exception:                                            # pragma: no cover
    emu_available = False
requires_emu = unittest.skipUnless(emu_available, "unicorn / emu package missing")


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def blob_of(patch: dict) -> bytes:
    for ch in patch["changes"]:
        if ch.get("kind") == "blob":
            return bytes.fromhex(ch["new"])
    raise AssertionError("no blob change in this patch.json")


def hooks_of(patch: dict) -> dict[int, dict]:
    return {int(ch["addr"], 16): ch
            for ch in patch["changes"] if ch.get("kind") == "hook"}


# ------------------------------------------------------------ 1. the dump ---
@requires_dump
class TestStockFacts(DumpUnchanged):
    """What the two hooks assume about the unmodified image."""

    def setUp(self):
        self.data = m.load_dump(str(DUMP))

    def _word(self, cpu: int) -> bytes:
        off = m.cpu_to_file(cpu)
        return bytes(self.data[off:off + 4])

    def test_both_hook_sites_hold_the_documented_bl(self):
        for site, old, target in ((HOOK_A_SITE, HOOK_A_OLD, NOP_LEAF),
                                  (HOOK_B_SITE, HOOK_B_OLD, CLR_LEAF)):
            with self.subTest(site=hex(site)):
                self.assertEqual(self._word(site), old)
                self.assertEqual(
                    patch_gen.decode_branch(int.from_bytes(old, "big"), site),
                    ("bl", target))

    def test_the_set_a_tail_target_is_an_empty_function(self):
        """scheduler.md §8.1: 0x0BD9E4 is one `blr`, so the tail branch of the
        set-A trampoline preserves literally nothing."""
        self.assertEqual(self._word(NOP_LEAF), bytes.fromhex("4e800020"))
        self.assertEqual(self._word(NOP_LEAF - 4), bytes.fromhex("4e800020"),
                         "the previous function must end right before it")

    def test_the_set_a_word_is_in_the_on_chip_flash_and_the_set_b_word_is_not(self):
        self.assertTrue(ONCHIP_START <= HOOK_A_SITE < ONCHIP_END)
        self.assertFalse(ONCHIP_START <= HOOK_B_SITE < ONCHIP_END)

    def test_the_blob_area_is_blank_for_both_variants(self):
        off = m.cpu_to_file(PATCH_FLASH)
        for path in (BOTH_JSON, EXTERNAL_JSON):
            with self.subTest(variant=path.name):
                size = load(path)["build"]["blob_size"]
                self.assertEqual(set(self.data[off:off + size]), {0xFF})


# -------------------------------------------------------- 2. the variants ---
class TestVariantDescriptors(unittest.TestCase):
    """Two patch.json files, one build; they must not drift apart."""

    def setUp(self):
        self.both, self.external = load(BOTH_JSON), load(EXTERNAL_JSON)

    def test_the_default_hooks_both_rasters_and_flags_the_on_chip_word(self):
        hooks = hooks_of(self.both)
        self.assertEqual(sorted(hooks), [HOOK_B_SITE, HOOK_A_SITE])
        self.assertTrue(hooks[HOOK_A_SITE].get("onchip_edit"),
                        '0x432940 is on-chip flash and needs "onchip_edit"')
        self.assertNotIn("onchip_edit", hooks[HOOK_B_SITE])
        self.assertEqual(hooks[HOOK_A_SITE]["old"], HOOK_A_OLD.hex())
        self.assertEqual(hooks[HOOK_B_SITE]["old"], HOOK_B_OLD.hex())

    def test_the_external_variant_hooks_the_set_b_word_alone(self):
        hooks = hooks_of(self.external)
        self.assertEqual(list(hooks), [HOOK_B_SITE])
        self.assertNotIn("onchip_edit", hooks[HOOK_B_SITE])
        for ch in self.external["changes"]:
            self.assertNotIn("onchip_edit", ch)

    def test_the_external_blob_is_the_pre_f1_one(self):
        blob = blob_of(self.external)
        self.assertEqual(len(blob), PRE_F1_BLOB_LEN)
        self.assertEqual(hashlib.sha256(blob).hexdigest(), PRE_F1_BLOB_SHA256)
        self.assertEqual(self.external["build"]["blob_sha256"], PRE_F1_BLOB_SHA256)

    def test_the_default_blob_is_bigger_and_different(self):
        self.assertGreater(len(blob_of(self.both)), PRE_F1_BLOB_LEN)
        self.assertNotEqual(self.both["build"]["blob_sha256"], PRE_F1_BLOB_SHA256)

    def test_the_two_descriptors_agree_on_everything_but_the_hooks(self):
        for key in ("name", "issue", "base_sha256", "ram_status"):
            self.assertEqual(self.both[key], self.external[key], key)
        for key in ("flash", "ram", "ram_size"):
            self.assertEqual(self.both["build"][key], self.external["build"][key], key)
        self.assertEqual(self.both["build"]["bss_size"], 8)
        self.assertEqual(self.external["build"]["bss_size"], 8,
                         "the RAM block must stay 8 bytes in both variants")

    def test_each_variant_builds_into_its_own_directory(self):
        """Otherwise a HOOKS=external build could link the other variant's
        objects, which is exactly the stale-object bug patch.mk fixed in wave E."""
        paths = {p: load(p)["build"]["blob"] for p in (BOTH_JSON, EXTERNAL_JSON)}
        self.assertEqual(len(set(paths.values())), 2, paths)
        self.assertIn("both", paths[BOTH_JSON])
        self.assertIn("external", paths[EXTERNAL_JSON])


# ------------------------------------------------------------- 3. apply -----
@requires_dump
class TestApplyBothVariants(DumpUnchanged):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.out = {}
        for tag, path in (("both", BOTH_JSON), ("external", EXTERNAL_JSON)):
            cls.out[tag] = patch_apply.apply_patch(DUMP, path)

    def test_both_variants_apply_cleanly(self):
        for tag, (data, report, _w) in self.out.items():
            with self.subTest(variant=tag):
                self.assertTrue(report["ok"], report["issues"])
                self.assertEqual(report["bytes"]["unexpected"], 0)
                self.assertTrue(cs.verify(data, quiet=True))

    def test_bindiff_sees_only_the_patch_and_its_descriptors(self):
        stock = m.load_dump(str(DUMP))
        for tag, path in (("both", BOTH_JSON), ("external", EXTERNAL_JSON)):
            with self.subTest(variant=tag):
                data = self.out[tag][0]
                _ranges, report = bindiff.diff(stock, data, str(path))
                self.assertTrue(report["ok"], report["issues"])
                self.assertEqual(report["counts"]["unexpected"], 0)

    def test_the_hook_words_point_at_the_trampolines(self):
        for tag, path, pairs in (
                ("both", BOTH_JSON, ((HOOK_A_SITE, "ff_counter_hook_a"),
                                     (HOOK_B_SITE, "ff_counter_hook_b"))),
                ("external", EXTERNAL_JSON, ((HOOK_B_SITE, "ff_counter_hook"),))):
            syms = load(path)["build"]["symbols"]
            data = self.out[tag][0]
            for site, name in pairs:
                with self.subTest(variant=tag, site=hex(site)):
                    off = m.cpu_to_file(site)
                    word = int.from_bytes(bytes(data[off:off + 4]), "big")
                    self.assertEqual(patch_gen.decode_branch(word, site),
                                     ("bl", int(syms[name], 0)))

    def test_the_set_b_word_of_the_default_build_is_untouched_in_the_stock_image(self):
        """Sanity: the two variants write different words at the same site."""
        a = hooks_of(load(BOTH_JSON))[HOOK_B_SITE]["new"]
        b = hooks_of(load(EXTERNAL_JSON))[HOOK_B_SITE]["new"]
        self.assertNotEqual(a, b, "the set-B trampoline moved by 0x20 in the "
                                  "default build; the words cannot be equal")

    def test_only_the_default_build_warns_about_the_on_chip_flash(self):
        warn_both = self.out["both"][2]
        self.assertEqual(sum("on-chip flash" in w for w in warn_both), 1,
                         f"exactly one on-chip word (0x432940): {warn_both}")
        self.assertFalse(any("on-chip flash" in w for w in self.out["external"][2]))
        for tag in ("both", "external"):
            self.assertTrue(any('"static"' in w and "Do not flash" in w
                                for w in self.out[tag][2]), tag)

    def test_the_external_variant_reproduces_the_pre_f1_image(self):
        """The acceptance criterion of brief F1: byte for byte, not just equivalent."""
        data = bytes(self.out["external"][0])
        self.assertEqual(hashlib.sha256(data).hexdigest(), PRE_F1_IMAGE_SHA256)

    def test_the_identification_block_is_untouched_in_both(self):
        stock = m.load_dump(str(DUMP))
        off = m.cpu_to_file(patch_apply.IDENT_START)
        n = patch_apply.IDENT_END - patch_apply.IDENT_START
        for tag, (data, _r, _w) in self.out.items():
            with self.subTest(variant=tag):
                self.assertEqual(bytes(data[off:off + n]), bytes(stock[off:off + n]))


# --------------------------------------------------------- 4. fresh build ---
@requires_toolchain
class TestExternalVariantBuild(unittest.TestCase):
    """`make HOOKS=external` must still produce the old bytes from the source."""

    @staticmethod
    def make(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["make", "-C", str(FF_COUNTER), *args],
                              capture_output=True, text=True)

    def test_the_external_build_checks_out(self):
        r = self.make("HOOKS=external", "check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("OK: r2 and r13 are never referenced", r.stdout)
        self.assertIn("00150000 T ff_counter_hook", r.stdout)

    def test_the_external_blob_is_byte_identical_to_the_pre_f1_one(self):
        r = self.make("HOOKS=external", "all")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        blob = (FF_COUNTER / load(EXTERNAL_JSON)["build"]["blob"]).read_bytes()
        self.assertEqual(len(blob), PRE_F1_BLOB_LEN)
        self.assertEqual(hashlib.sha256(blob).hexdigest(), PRE_F1_BLOB_SHA256,
                         "HOOKS=external no longer reproduces the pre-F1 blob")

    @requires_dump
    def test_the_external_descriptor_still_matches_a_fresh_build(self):
        r = self.make("HOOKS=external", "all")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        fresh, _notes = patch_gen.generate(EXTERNAL_JSON, DUMP)
        self.assertEqual(fresh, load(EXTERNAL_JSON),
                         "patch.external.json is stale: run `make HOOKS=external gen`")

    def test_an_unknown_variant_is_refused(self):
        r = self.make("HOOKS=setA", "all")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("HOOKS must be", r.stderr + r.stdout)


# ------------------------------------------------------------ 5. emulate ----
@requires_dump
@requires_emu
class TestBothStubs(DumpUnchanged):
    """The two trampolines on the applied default image."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp = Path(tempfile.mkdtemp())
        cls.image = cls.tmp / "ff_counter.bin"
        data, _report, _w = patch_apply.apply_patch(DUMP, BOTH_JSON)
        cls.image.write_bytes(bytes(data))
        cls.syms = {k: int(v, 0)
                    for k, v in load(BOTH_JSON)["build"]["symbols"].items()}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        super().tearDownClass()

    @staticmethod
    def _seed(ticks: int, alive: int, src_seen: int = 0) -> dict[int, bytes]:
        return {PATCH_RAM: struct.pack(">IHBB", ticks, alive, src_seen, 0)}

    @staticmethod
    def _block(emu) -> tuple[int, int, int, int]:
        """(ticks, alive, src_seen, reserved) out of the state block."""
        return struct.unpack(">IHBB", emu.read(PATCH_RAM, 8))

    def _after(self, res) -> tuple[int, int, int, int]:
        raw = res.snapshot(SRAM_START, SRAM_LEN)
        off = PATCH_RAM - SRAM_START
        return struct.unpack(">IHBB", raw[off:off + 8])

    # -- one activation of each stub -------------------------------------
    def test_each_stub_ticks_once_and_sets_exactly_its_own_bit(self):
        for hook, bit in (("ff_counter_hook_a", SRC_A), ("ff_counter_hook_b", SRC_B)):
            with self.subTest(hook=hook):
                emu = Med9Emu(self.image)
                res = emu.call(self.syms[hook], mem=self._seed(0x1234, ALIVE))
                self.assertTrue(res.ok, f"{res.stop_reason}: {res.issues}")
                ticks, alive, seen, reserved = self._after(res)
                self.assertEqual(ticks, 0x1235)
                self.assertEqual(alive, ALIVE)
                self.assertEqual(seen, bit, "a stub set a bit that is not its own")
                self.assertEqual(reserved, 0)

    def test_a_cold_start_initialises_the_block_whichever_stub_runs_first(self):
        """Nothing zeroes our .bss; the first tick has to, including src_seen."""
        for hook, bit in (("ff_counter_hook_a", SRC_A), ("ff_counter_hook_b", SRC_B)):
            with self.subTest(hook=hook):
                emu = Med9Emu(self.image)
                # RAM is all zero after reset, so `alive` is not 0xFC01.
                res = emu.call(self.syms[hook])
                self.assertEqual(self._after(res), (1, ALIVE, bit, 0))

    def test_a_cold_start_from_garbage_clears_a_stale_src_seen(self):
        emu = Med9Emu(self.image)
        res = emu.call(self.syms["ff_counter_hook_a"],
                       mem={PATCH_RAM: bytes.fromhex("DEADBEEF0000FFFF")})
        self.assertEqual(self._after(res), (1, ALIVE, SRC_A, 0))

    def test_the_bits_accumulate_and_the_slope_doubles_if_both_ever_fire(self):
        """src_seen = 3 is its own outcome in test/procedure.md §4."""
        emu = Med9Emu(self.image)
        emu.reset()
        for _ in range(10):
            emu.call(self.syms["ff_counter_hook_a"], reset=False)
            emu.call(self.syms["ff_counter_hook_b"], reset=False)
        ticks, alive, seen, reserved = self._block(emu)
        self.assertEqual(ticks, 20)
        self.assertEqual((alive, seen, reserved), (ALIVE, SRC_A | SRC_B, 0))

    def test_neither_stub_touches_the_other_stock_leaf(self):
        """The set-A leaf is empty; the set-B leaf clears two cells."""
        emu = Med9Emu(self.image)
        emu.reset()
        emu.write(CLEARED_BYTE, 0xAA, 1)
        emu.write(CLEARED_HALF, 0xBEEF, 2)
        emu.call(self.syms["ff_counter_hook_a"], reset=False)
        self.assertEqual(emu.read(CLEARED_BYTE, 1), b"\xAA",
                         "the set-A stub ran the set-B leaf")
        self.assertEqual(emu.read(CLEARED_HALF, 2), b"\xBE\xEF")
        emu.call(self.syms["ff_counter_hook_b"], reset=False)
        self.assertEqual(emu.read(CLEARED_BYTE, 1), b"\x00")
        self.assertEqual(emu.read(CLEARED_HALF, 2), b"\x00\x00")

    def test_the_set_a_tail_branch_is_a_ba_to_the_empty_leaf(self):
        emu = Med9Emu(self.image, trace=True)
        hook = self.syms["ff_counter_hook_a"]
        res = emu.call(hook, mem=self._seed(1, ALIVE))
        self.assertTrue(res.ok, res.issues)
        tail = hook + 0x1C                       # the 8th word of HOOK_TAIL
        self.assertEqual(emu.read(tail, 4),
                         patch_gen.encode_branch(tail, NOP_LEAF, "ba").to_bytes(4, "big"))
        self.assertIn(NOP_LEAF, res.pc_trace)
        self.assertEqual(res.pc_trace[res.pc_trace.index(NOP_LEAF) - 1], tail)
        self.assertEqual(res.pc_trace[-1], NOP_LEAF,
                         "the empty leaf's single `blr` must be the last insn")

    # -- the site, stock against patched ---------------------------------
    def _run_site(self, image, site: int):
        emu = Med9Emu(image)
        res = emu.run(site, until=site + 4)
        self.assertTrue(res.ok, f"{res.stop_reason}: {res.issues}")
        return res

    def test_the_set_a_site_moves_nothing_but_the_counter_and_the_frame(self):
        """Run 0x432940..0x432944 on both images and diff all 64 KB of SRAM."""
        snaps, insns = {}, {}
        for tag, image in (("stock", DUMP), ("patched", self.image)):
            res = self._run_site(image, HOOK_A_SITE)
            self.assertEqual(res.regs["r1"], STACK_TOP, tag)
            snaps[tag], insns[tag] = res.snapshot(SRAM_START, SRAM_LEN), res.insns

        self.assertEqual(insns["stock"], 2, "bl + the empty leaf's blr")
        changed = {SRAM_START + i for i in range(SRAM_LEN)
                   if snaps["stock"][i] != snaps["patched"][i]}
        allowed = set(range(PATCH_RAM, PATCH_RAM + 8))
        allowed |= set(range(STACK_TOP - HOOK_TAIL_FRAME, STACK_TOP))
        self.assertEqual(changed - allowed, set(),
                         f"the set-A hook moved RAM it must not: "
                         f"{[hex(a) for a in sorted(changed - allowed)]}")
        self.assertTrue(changed & set(range(PATCH_RAM, PATCH_RAM + 8)),
                        "the counter block did not move at all")

    def test_the_set_b_site_still_moves_nothing_else_either(self):
        """The pre-F1 test, kept: the set-B leaf's two cells are the only
        stock RAM that may differ, and they are cleared by both images."""
        snaps = {}
        for tag, image in (("stock", DUMP), ("patched", self.image)):
            res = self._run_site(image, HOOK_B_SITE)
            self.assertEqual(res.regs["r1"], STACK_TOP, tag)
            snaps[tag] = res.snapshot(SRAM_START, SRAM_LEN)
        changed = {SRAM_START + i for i in range(SRAM_LEN)
                   if snaps["stock"][i] != snaps["patched"][i]}
        allowed = set(range(PATCH_RAM, PATCH_RAM + 8))
        allowed |= set(range(STACK_TOP - HOOK_TAIL_FRAME, STACK_TOP))
        self.assertEqual(changed - allowed, set(),
                         f"{[hex(a) for a in sorted(changed - allowed)]}")

    def test_each_stub_returns_into_its_task_with_r1_and_lr_as_at_entry(self):
        """The `bl` at the site leaves LR = site+4; after the stub and the
        original target's `blr` the CPU must be back there, with r1 restored."""
        for site in (HOOK_A_SITE, HOOK_B_SITE):
            with self.subTest(site=hex(site)):
                res = self._run_site(self.image, site)
                self.assertEqual(res.pc, site + 4)
                self.assertEqual(res.regs["r1"], STACK_TOP)
                self.assertEqual(res.regs["lr"], site + 4,
                                 "LR was not restored before the tail branch")

    def test_the_cost_of_one_activation(self):
        """Recorded in README.md; the stubs are straight-line, so warm == worst."""
        cold, warm = {}, {}
        for tag, hook in (("a", "ff_counter_hook_a"), ("b", "ff_counter_hook_b")):
            emu = Med9Emu(self.image)
            cold[tag] = emu.call(self.syms[hook]).insns
            emu = Med9Emu(self.image)
            warm[tag] = emu.call(self.syms[hook], mem=self._seed(5, ALIVE)).insns
        self.assertEqual((warm["a"], warm["b"]), (21, 24))
        self.assertEqual((cold["a"], cold["b"]), (29, 32))


if __name__ == "__main__":
    unittest.main()
