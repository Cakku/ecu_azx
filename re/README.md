# re/ — reverse-engineering knowledge base

`symbols.csv` is the machine-readable list of everything located in the
firmware, with its evidence and confidence (format in
`docs/04_re_guidelines.md` section 4). Ghidra exports are merged here; the
Ghidra project itself is not committed.

`measuring_vars.csv` is the measuring-variable (TKMWL) table: one row per
implemented KWP SID 0x21 variable id with its RAM address, access width, VAG
display formula and evidence. Regenerate with
`python3 tools/measuring_vars.py data/passat_azx_ori.bin --csv re/measuring_vars.csv`.

`findings/` holds longer notes per topic (CAN, KWP, injection, ...).
