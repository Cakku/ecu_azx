# tools/

Small, dependency-free Python 3 helpers that encode what has been *verified*
about this ECU's dump.  Every fact they rely on is documented in
`docs/02_memory_map.md`; if you learn that something there is wrong, fix the
document and `med9lib.py` together.

| Tool | Purpose |
|---|---|
| `med9lib.py` | Address-space model (file offset <-> CPU address, aliases), SDA bases, `sum16`. Import this from any new script. |
| `checksum.py` | `verify` / `fix` the 65 Bosch block checksums (sum of 16-bit words, stored as sum/~sum). Run `verify` on every file before it goes anywhere near the car. |
| `layout_report.py` | Structural overview of a dump: fill/entropy map, `5A5A5A5A` block markers, ID strings, boot register setup, checksum tables. |
| `find_abs_refs.py` | Resolve `lis`+offset absolute references; find who touches an address, or histogram address usage. |
| `find_branch_refs.py` | Control-flow counterpart of `find_abs_refs.py`: who `b`/`bl`s to an address, and where the address appears as a 32-bit pointer word. Finds the callers Ghidra's auto-analysis misses. |
| `ethanol_frame_decode.py` | Decode the Pico flex-fuel node's CAN frame (0x0EC) from candump / candump -L / SavvyCAN CSV lines or a whole log, with plausibility and counter/gap checks. `--live` uses python-can if installed; everything else is dependency-free. Layout also in `data/ethanol_node.dbc`. |
| `measuring_vars.py` | Measuring-variable (TKMWL) table: find the dispatcher, walk all 2200 handlers, report each variable's RAM address/width and VAG display formula; `--groups` dumps the measuring-block group table. |
| `bindiff.py` | Diff two dumps and classify every changed byte as *patch* (listed in a `patch.json`), *descriptor* (a checksum sum/~sum word) or **unexpected**. Exit 1 on anything unexpected. |
| `logcmp.py` | Compare a baseline and a candidate log over their common variables with per-variable tolerances. Format and tolerance file: `logging/README.md`. |
| `draft_to_xdf.py` | `re/calibration_draft.csv` -> a TunerPro `.xdf`. Maps CPU addresses to **file offsets** through `med9lib`, emits big-endian row-major tables, and validates the result structurally (`--validate`, `--self-test`). No scaling is applied: every value is raw counts. |
| `blobdis.py` | Disassemble a raw big-endian PowerPC blob at a chosen CPU address; `--check-sda` fails if patch code touches r2/r13. |
| `eeprom_map.py` | Decode the SPI EEPROM block layout (EEP_CONF, file 0xB2FF0): block table, copies, RAM mirror, free space; `--clients` maps which block bytes the firmware actually uses; `--check` verifies the block checksums of a real 2 KB EEPROM read. `re/findings/eeprom.md`. |
| `callgraph.py` | Static PowerPC call graph: every `bl` target is a function entry, each function is walked as a CFG (`--reach`, `--func`, `--callers`, `--entries`). Also extracts r2/r13-relative accesses and finds `lis`+D-form pairs that address a register range (`--xref-store`). |
| `r2_context.py` | Decides the SDA2 base (r2) of every function from the call graph and checks every r2-relative access against it: reports references that leave the SDA2 window, land outside a mapped region, or hit 0xFF filler. Evidence for issue #8. |
| `sda_xref.py` | Whole-image cross-references. `--var LO [HI]` decodes every r2/r13-relative D-form load/store and prints the ones resolving into the range — the small-data accesses `callgraph.py --xref-store` cannot see. `--code ADDR...` prints every `b`/`bl` **site** targeting an address (not the enclosing function), so a flat ERCOSEK task body reads off directly. Used throughout `re/findings/rail.md` (issue #17). |
| `gen_stock_header.py` | Generate `patches/common/med9_stock.h` (stock function / RAM addresses for patch code) from `re/symbols.csv`; `--check` fails the build when the checked-in header is stale. |
| `patch_gen.py` | Turn a patch's `build` section into its `changes` list: resolve hook targets from the `.sym` file, encode the I-form branch words (reach and alignment checked), assert the stock bytes under the blob are 0xFF, and emit one change per `build.data` entry (a new calibration block from a file, or an inline table edit) with its `old` read from the stock image. `changes` is generated, never hand-edited. |
| `patch_apply.py` | The only tool that modifies an image. Checks `base_sha256`, the forbidden regions and every `old`; writes the `new` bytes to a copy; fixes and verifies the checksums; proves the identification block is unchanged; requires a clean `bindiff`. Writes nothing if any of that fails. Guarded regions are unlocked per change by `calibration_edit` (0x1C0000-0x1DFFFF) or `onchip_edit` (0x404000-0x47FFFF, always warns); 0x000000-0x00FFFF, 0x400000-0x403FFF and the identification block are never unlockable. |
| `ram_survey.py` | Per-byte static usage survey of the two SRAMs (0x7F8000-0x807FFF): r13 D-form accesses, absolute `lis`+D-form pairs, pointer words in both flash regions, measuring-variable cells, the cold-start fills and a table of known structures. Emits `re/ram_map.csv`, a 256-byte page map and the longest reference-free runs. `--indexed` bounds the arrays those runs usually belong to; `--stack` walks the deepest `stwu` chain from each task entry. `re/findings/ram.md`. |
| `ercosek_tasks.py` | Brief C4 (#44). Decodes the whole ERCOSEK activation chain: the 37 task descriptors behind the ActivateTask thunk table (0x0B091C), both cyclic time tables (0x478EE4 / 0x478F80) and both raster divider chains (0x40BEF0 / 0x40C064), and prints every raster period in Time Base ticks and milliseconds. `--tasks`, `--timetable`, `--dividers`, `--periods`, `--json`. `re/findings/scheduler.md` section 11. |
| `ram_snapshot_diff.py` | Compares the RAM snapshots taken over KWP RequestUpload and classifies every byte `changed` / `constant` / `blank`. The dynamic half of issue #23; ranges in `logging/sessions/ram_snapshot.json`, format in the module docstring, `--self-test` runs it on synthetic snapshots. |
| `flash_segments.py` | Brief E6. Dumps the firmware's flash-programming tables: the three-entry flash device table (0x082980), the erase geometry and the UC3F block map (0x0825E4 / 0x082684), the programming-mode KWP dispatch table (0x088174) with the download/erase whitelist, and the 0x480000 mode-4 EEPROM window; `--all`, `--json`. `re/findings/flash_programming.md`. |

Quick checks:

```bash
python3 tools/checksum.py verify -q data/passat_azx_ori.bin      # expect: ALL OK (65 blocks)
python3 tools/layout_report.py data/passat_azx_ori.bin
python3 tools/find_abs_refs.py data/passat_azx_ori.bin --target 0x6FC100   # BR0 writers
python3 tools/ethanol_frame_decode.py "0EC#322A320500000100"   # -> E 50 %, 2 C, OK
python3 tools/measuring_vars.py data/passat_azx_ori.bin --csv re/measuring_vars.csv
python3 tools/measuring_vars.py data/passat_azx_ori.bin --groups
python3 tools/draft_to_xdf.py re/calibration_draft.csv -o re/med9_draft.xdf \
        --min-confidence hypothesis            # 1,066 tables
python3 tools/draft_to_xdf.py --validate re/med9_draft.xdf
python3 tools/callgraph.py data/passat_azx_ori.bin \
        --reach 0x1004 0x12328 --stop 0x986AC 0x9E3E0 0x405588   # the boot module
python3 tools/r2_context.py data/passat_azx_ori.bin --compare --violations
python3 tools/sda_xref.py data/passat_azx_ori.bin --var 0x8031DA   # prist readers/writers
python3 tools/sda_xref.py data/passat_azx_ori.bin --code 0x457BC8  # who calls the HDR controller
python3 tools/ram_survey.py data/passat_azx_ori.bin --csv re/ram_map.csv
python3 tools/ram_survey.py data/passat_azx_ori.bin --indexed --indexed-min 0x40
python3 tools/ram_survey.py data/passat_azx_ori.bin --stack
python3 tools/ram_snapshot_diff.py --self-test
python3 tools/ercosek_tasks.py data/passat_azx_ori.bin --periods   # every raster
python3 -m emu.ext_sram_probe                # 0x7F8012 = 0x44 / 0x41 per CS1 model
python3 -m emu.os_clock --set a --seconds 5  # the same periods, emulated
```

Building and applying a patch (`docs/06_patch_pipeline.md`, issue #25). The
Makefile in each patch directory wraps all of it; these are the raw commands:

```bash
python3 tools/gen_stock_header.py --check           # the stock addresses are current
cd patches/ff_counter && make check && make dump    # build, prove no r2/r13, read the blob
python3 tools/patch_gen.py patches/ff_counter       # rewrite `changes` from the blob
python3 tools/patch_apply.py data/passat_azx_ori.bin patches/ff_counter \
        -o work/ff_counter.bin                      # --dry-run, --json
```

`patch_apply.py` refuses (and writes nothing) on a wrong `base_sha256`, an
`old` that is not there, a change inside 0x000000-0x00FFFF or 0x400000-0x403FFF,
inside 0x1C0000-0x1DFFFF without `"calibration_edit": true` or inside
0x404000-0x47FFFF without `"onchip_edit": true`, a touched
identification block, a failed checksum or an unexpected byte in the bindiff.
A patch whose `"ram_status"` is not `"verified"` applies with a loud
do-not-flash warning.

Regression checks before a file goes anywhere near the car
(`docs/06_patch_pipeline.md` section 5):

```bash
python3 tools/bindiff.py data/passat_azx_ori.bin work/patched.bin \
        -p patches/ff_counter/patch.json          # exit 0 == only intended bytes moved
python3 tools/bindiff.py stock.bin patched.bin -p patch.json --json work/diff.json
python3 tools/logcmp.py base.csv cand.csv -t patches/ff_counter/test/tolerance.json
```

`checksum.py fix` is semantics-preserving, and running it on the original dump
changes nothing (this is part of the test). **Corrected 2026-09-17 (E7): it
does not rewrite in place.** `checksum.py fix FILE` writes `FILE.fixed.bin`
(gitignored) and leaves `FILE` alone; `-o OUT` names the output instead. So the
verify that follows a fix must name the *output* file, not the input.

## Tests

Everything in `tools/`, `emu/`, `logging/` and `patches/` is covered by one
suite:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 -m unittest discover -s tests -v   # 591 tests, needs data/passat_azx_ori.bin
```

**591 tests in 21 files, about two minutes** (2026-09-17, E7, on
`integration/wave-E`; it was 107 when this section was written and 365 after
wave D, so treat the number as a date-stamped observation rather than a
constant):

| File | What it covers |
|---|---|
| `test_bindiff.py` | builds a patched copy in a temp directory and checks that only the edits and their descriptors moved |
| `test_draft_to_xdf.py` | the XDF skeleton, the file-offset mapping, the `val[iy*nx+ix]` layout, and `re/calibration_names.csv` against the draft |
| `test_ecu_sim_patch.py` | `logging/ecu_sim.py --sim-patch`: the patch's hooks driven on a simulated raster |
| `test_emu.py` | the Unicorn harness (`emu/README.md`) |
| `test_ercosek_tasks.py` | `tools/ercosek_tasks.py` and `emu/os_clock.py`: the raster periods |
| `test_ethanol_frame_send.py` | the simulated Pico node |
| `test_ff_diag_patch.py`, `test_ff_fuel_patch.py`, `test_ff_ign_patch.py`, `test_ff_rail_patch.py`, `test_ff_start_patch.py` | the five `patches/ff_fuel` features under the emulator, including the two bit-identity proofs each (disabled, and enabled at neutral calibration) |
| `test_flexfuel_model.py` | `emu/models/flexfuel.py`, the reference model the patch and FFCAL001 are both checked against |
| `test_injection_model.py`, `test_start_model.py`, `test_window_model.py`, `test_zw_model.py` | the bit-exact models of the injection, start, injection-window and base-ignition paths |
| `test_logcmp.py` | the synthetic logs in `logging/samples/` |
| `test_med9kwp.py` | the TP2.0 + KWP2000 stack against `logging/ecu_sim.py` (49 tests, no hardware) |
| `test_patch_framework.py` | `patches/common/` + `patch_gen` + `patch_apply` + the `ff_counter` hook under the emulator; the build layer skips itself with a clear message when `LLVM_DIR` is not installed |
| `test_qspi_eeprom.py` | the QSMCM QSPI queue and the M95160 device model |
| `test_ram_survey.py` | `tools/ram_survey.py` and `emu/ext_sram_probe.py` |

`tests/common.py` has the dump-unchanged base class. Every test that loads the
dump asserts its SHA-256 is unchanged afterwards; none of them writes to
`data/`.

`blobdis.py` disassembles a raw big-endian PowerPC blob at a chosen CPU
address (capstone). `llvm-objdump` cannot do this — it has no `-b binary` —
and looking at the ELF instead of the bytes the CPU will fetch is exactly the
mistake the pre-flash checklist exists to prevent.

```bash
cd patches/examples/hello_patch && make          # build/ is gitignored: build it first
python3 tools/blobdis.py patches/examples/hello_patch/build/hello.bin \
    --addr 0x145000 --check-sda      # non-zero exit if r2 or r13 are touched
python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x20004 --len 0x20
```

The end-to-end walkthrough that ties all of this together — a calibration-only
change, a code change, flashing, logging, log review and roll-back — is
[`../docs/07_workflow.md`](../docs/07_workflow.md) (2026-09-17, E7).
