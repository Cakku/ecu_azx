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
| `ethanol_frame_decode.py` | Decode the Pico flex-fuel node's CAN frame (0x0EC) from candump / candump -L / SavvyCAN CSV lines or a whole log, with plausibility and counter/gap checks. `--live` uses python-can if installed; everything else is dependency-free. Layout also in `data/ethanol_node.dbc`. |
| `measuring_vars.py` | Measuring-variable (TKMWL) table: find the dispatcher, walk all 2200 handlers, report each variable's RAM address/width and VAG display formula; `--groups` dumps the measuring-block group table. |
| `bindiff.py` | Diff two dumps and classify every changed byte as *patch* (listed in a `patch.json`), *descriptor* (a checksum sum/~sum word) or **unexpected**. Exit 1 on anything unexpected. |
| `logcmp.py` | Compare a baseline and a candidate log over their common variables with per-variable tolerances. Format and tolerance file: `logging/README.md`. |
| `blobdis.py` | Disassemble a raw big-endian PowerPC blob at a chosen CPU address; `--check-sda` fails if patch code touches r2/r13. |
| `eeprom_map.py` | Decode the SPI EEPROM block layout (EEP_CONF, file 0xB2FF0): block table, copies, RAM mirror, free space; `--clients` maps which block bytes the firmware actually uses; `--check` verifies the block checksums of a real 2 KB EEPROM read. `re/findings/eeprom.md`. |

Quick checks:

```bash
python3 tools/checksum.py verify -q data/passat_azx_ori.bin      # expect: ALL OK (65 blocks)
python3 tools/layout_report.py data/passat_azx_ori.bin
python3 tools/find_abs_refs.py data/passat_azx_ori.bin --target 0x6FC100   # BR0 writers
python3 tools/ethanol_frame_decode.py "0EC#322A320500000100"   # -> E 50 %, 2 C, OK
python3 tools/measuring_vars.py data/passat_azx_ori.bin --csv re/measuring_vars.csv
python3 tools/measuring_vars.py data/passat_azx_ori.bin --groups
```

Regression checks before a file goes anywhere near the car
(`docs/06_patch_pipeline.md` section 5):

```bash
python3 tools/bindiff.py data/passat_azx_ori.bin work/patched.bin \
        -p patches/ff_counter/patch.json          # exit 0 == only intended bytes moved
python3 tools/bindiff.py stock.bin patched.bin -p patch.json --json work/diff.json
python3 tools/logcmp.py base.csv cand.csv -t patches/ff_counter/test/tolerance.json
```

`checksum.py fix` rewrites descriptors in place semantics-preserving; running
it on the original dump changes nothing (this is part of the test).

## Tests

Everything in `tools/` and `emu/` is covered by one suite:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 -m unittest discover -s tests -v      # 31 tests, needs data/passat_azx_ori.bin
```

`tests/` contains `test_bindiff.py` (builds a patched copy in a temp directory
and checks that only the edits and their descriptors moved), `test_logcmp.py`
(the synthetic logs in `logging/samples/`) and `test_emu.py` (the Unicorn
harness, `emu/README.md`). Every test that loads the dump asserts its SHA-256
is unchanged afterwards; none of them writes to `data/`.
`blobdis.py` disassembles a raw big-endian PowerPC blob at a chosen CPU
address (capstone). `llvm-objdump` cannot do this — it has no `-b binary` —
and looking at the ELF instead of the bytes the CPU will fetch is exactly the
mistake the pre-flash checklist exists to prevent.

```bash
python3 tools/blobdis.py patches/examples/hello_patch/build/hello.bin \
    --addr 0x145000 --check-sda      # non-zero exit if r2 or r13 are touched
python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x20004 --len 0x20
```
