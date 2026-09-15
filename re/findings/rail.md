# Rail pressure control (HDR) and the injection window (AWEA)

Agent B9, brief `docs/agent_briefs/B9_rail_pressure_and_window.md`, issue #17.
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
mkdir -p /tmp/ghidra_B9
cp -R /Users/carlo/ecu_azx/ghidra_projects/med9.gpr \
      /Users/carlo/ecu_azx/ghidra_projects/med9.rep /tmp/ghidra_B9/
export GHIDRA_INSTALL_DIR=/usr/local/Cellar/ghidra/12.1.3/libexec
/Users/carlo/ecu_azx/.venv/bin/python -m pyghidra.ghidra_launch \
    --install-dir "$GHIDRA_INSTALL_DIR" \
    ghidra.app.util.headless.AnalyzeHeadless /tmp/ghidra_B9 med9 \
    -process passat_azx_ori.bin -noanalysis \
    -scriptPath ghidra_scripts -postScript import_symbols.py "$PWD"
# -> 227 functions named, 222 labels created, 3 skipped (0x400000, 0x702000,
#    0x707000 have no memory block)
```

Read-only disassembly / decompilation from the command line:

```bash
/Users/carlo/ecu_azx/.venv/bin/python ghidra_scripts/decompile.py \
    --project-dir /tmp/ghidra_B9 --project-name med9 0x<addr>
```

(This file is written progressively while the work runs.)
