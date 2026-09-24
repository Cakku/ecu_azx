# Brief H6 — `make bench-kit`: build every image the bench day writes, with a manifest of hashes, expected flash CRCs and byte-diff ranges, from one command (#26/#27 desk half)

`docs/08_bench_playbook.md` tells the human which image to write at each step and
quotes the commands that build them, spread over `tools/checksum.py`,
`patches/ff_counter` (two variants), `patches/ff_fuel` (the shipped image — by Carlo's ruling of 2026-09-24
`ff_persist_enable` stays 1, so there is no separate bench build), `tools/bindiff.py`
and `logging/ecu_sim.py --print-flash-crc`. Assembling that by hand on the bench,
with a laptop and a running ECU, is where a wrong file gets written. This brief
makes it one command that produces a self-describing kit, and a test that pins the
kit to the known hashes so any drift shows up at the desk. Wave H pair 3, parallel
with H4. Tooling only; no patch source changes; nothing is flashed.

Read `00_common_rules.md`, `docs/08_bench_playbook.md` (steps 5-7 and the stop
list), `docs/07_workflow.md` §3 (flashing), `docs/06_patch_pipeline.md` §2-§4 and
§6 (read-back checklist), `patches/ff_counter/Makefile` and `README.md`
(`HOOKS=both|external`), `patches/ff_fuel/Makefile`, `README.md` (the image
SHA-256 `c08a78a6…c317` after G7) and `test/procedure_d2.md` §B4 (the
`ff_persist_enable = 0` build), `tools/checksum.py`, `tools/bindiff.py`,
`tools/patch_apply.py`, `logging/ecu_sim.py --print-flash-crc` (G5),
`logging/sessions/flash_crc.json`, `tests/common.py` (dump-unchanged base class),
`tools/README.md`.

## Facts
- Flash 0's file is the dump re-saved through `checksum.py fix` and must equal it
  byte for byte (G6: `0 descriptor(s) updated`, SHA-256 `b15590d3…09b3`).
- Flash 1 = `patches/ff_counter` `HOOKS=both` (image SHA `3cd20443…6498`, expected
  flash CRC 0x06C08AD4, G6/G5) and the `HOOKS=external` fallback (`9ecde359…`).
- `ff_fuel` shipped image `c08a78a6…c317` (G7). By ruling (Carlo, 2026-09-24)
  `ff_persist_enable` stays 1, so the shipped image *is* the bench image; the kit
  carries no persist-off variant.
- The firmware's flash CRC over the three ranges is what `flash_crc.json` reads
  back; `ecu_sim.py --print-flash-crc <image>` computes it the firmware's way.
- Every hash and CRC in the kit is **computed at build time**, never typed in;
  the test compares them with the values documented in the patch READMEs and
  fails loudly if either side drifted.

## Tasks
1. **`tools/bench_kit.py`** (`--out work/bench_kit`, default): builds
   `00_stock_resaved.bin`, `10_ff_counter_both.bin`, `11_ff_counter_external.bin`
   and `20_ff_fuel_shipped.bin` by invoking the existing
   tools/Makefiles (never re-implementing them); runs `checksum.py verify` on each;
   `bindiff` against the dump for each (range list); `ecu_sim.py --print-flash-crc`
   for each; writes `MANIFEST.json` (file, size, SHA-256, patch id/version, FFCAL001
   version, changed ranges, expected flash CRC, the docs/08 step and the S-rows
   that apply) and a short `README.md` in the kit (which file at which step, the
   read-back commands, the session files to load). Neither a top-level `Makefile` nor
   `patches/Makefile` exists today: add a minimal top-level `Makefile` whose
   `bench-kit` target runs `./.venv/bin/python3 tools/bench_kit.py` (and a `help`
   target listing it), so `make bench-kit` is the documented entry point.
2. **Guard rails.** Refuse to run if `data/passat_azx_ori.bin`'s SHA-256 is not the
   canonical one; never write under `data/`; the kit directory is under `work/`
   (gitignored); print the manifest summary; exit non-zero if any `checksum.py
   verify` or `bindiff` result is unexpected.
3. **`tests/test_bench_kit.py`**: builds into a temp dir (skip cleanly if the LLVM
   toolchain is missing, with the reason); asserts the stock re-save equals the
   dump; asserts the `ff_counter` and `ff_fuel` hashes and flash CRCs match the
   values quoted in their READMEs (parse them from the READMEs so the test fails
   when a README drifts, not when the build does); asserts no file under `data/`
   changed (`tests/common.py`).
4. **Docs.** `docs/08_bench_playbook.md`: one dated line at the top of step 5
   ("build the kit first: `make bench-kit`") — nothing else in that file (H4 edits
   its stop row concurrently; stay out of the stop list); `tools/README.md` row;
   `docs/07` is H4's this wave — do not edit it. Comment on #26 and #27; commit
   after every task.

## Ownership
`tools/bench_kit.py` (new), `tests/test_bench_kit.py` (new), the Makefile target,
`tools/README.md` row, one line in `docs/08` step 5. Do **not** edit patch
sources or their Makefiles/READMEs, `docs/07` (H4), `logging/**`, `re/**`.

## Acceptance
`make bench-kit` (or the documented equivalent) produces the four images and a
manifest whose hashes and CRCs the test pins to the READMEs; the stock re-save is
byte-identical to the dump; nothing under `data/` changes; suite green; the
playbook points at the kit.
