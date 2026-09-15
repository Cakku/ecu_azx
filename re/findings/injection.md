# Injection path: relative fuel mass `rk` -> injection time `ti`

Agent B6, brief `docs/agent_briefs/B6_injection_path.md`, issue #14.
Date: 2026-09-15. Dump `data/passat_azx_ori.bin`, SHA-256
`b15590d3f1874ace3125c5d047c09a686db9b8bb498187663539ebab205609b3`
(`python3 tools/checksum.py verify -q` -> ALL OK (65 blocks)).

Tags as in `docs/agent_briefs/00_common_rules.md`:
**VERIFIED-STATIC** = read out of the bytes of the dump,
**VERIFIED-DYNAMIC** = reproduced by the emulator,
**COMMUNITY** = outside knowledge (FR, forums),
**HYPOTHESIS** = our inference.

## 0. Working environment (reproduce)

```bash
mkdir -p /tmp/ghidra_B6
cp -R ghidra_projects/med9.gpr ghidra_projects/med9.rep /tmp/ghidra_B6/
export GHIDRA_INSTALL_DIR=/usr/local/Cellar/ghidra/12.1.3/libexec
./.venv/bin/python -m pyghidra.ghidra_launch --install-dir "$GHIDRA_INSTALL_DIR" \
    ghidra.app.util.headless.AnalyzeHeadless /tmp/ghidra_B6 med9 \
    -process passat_azx_ori.bin -noanalysis \
    -scriptPath ghidra_scripts -postScript import_symbols.py "$PWD"
# -> 198 functions named, 168 labels created, 3 skipped (0x400000, 0x702000,
#    0x707000 have no memory block; they are the un-dumped / peripheral aliases)
```

Read-only decompilation from the command line (safe in parallel with other
agents, it opens the project read-only):

```bash
./.venv/bin/python ghidra_scripts/decompile.py \
    --project-dir /tmp/ghidra_B6 --project-name med9 0x<addr>
```
