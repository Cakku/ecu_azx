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
| `bindiff.py` | Diff two dumps and classify every changed byte as *patch* (listed in a `patch.json`), *descriptor* (a checksum sum/~sum word) or **unexpected**. Exit 1 on anything unexpected. |
| `logcmp.py` | Compare a baseline and a candidate log over their common variables with per-variable tolerances. Format and tolerance file: `logging/README.md`. |

Quick checks:

```bash
python3 tools/checksum.py verify -q data/passat_azx_ori.bin      # expect: ALL OK (65 blocks)
python3 tools/layout_report.py data/passat_azx_ori.bin
python3 tools/find_abs_refs.py data/passat_azx_ori.bin --target 0x6FC100   # BR0 writers
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
