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

Quick checks:

```bash
python3 tools/checksum.py verify -q data/passat_azx_ori.bin      # expect: ALL OK (65 blocks)
python3 tools/layout_report.py data/passat_azx_ori.bin
python3 tools/find_abs_refs.py data/passat_azx_ori.bin --target 0x6FC100   # BR0 writers
```

`checksum.py fix` rewrites descriptors in place semantics-preserving; running
it on the original dump changes nothing (this is part of the test).
