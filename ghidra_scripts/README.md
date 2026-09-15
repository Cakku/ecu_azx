# ghidra_scripts/

Scripts that build and maintain the Ghidra project for
`data/passat_azx_ori.bin`. The project directory itself is **not** committed
(`ghidra_projects/` is gitignored); these scripts plus `re/symbols.csv` are
what reproduce it.

| Script | Purpose |
|---|---|
| `med9_setup.py` | Builds the verified memory map (docs/03_tooling.md §2.1), seeds disassembly, types the known tables, runs auto-analysis and prints an acceptance report. Run this first, on a fresh import. |
| `export_symbols.py` | Ghidra → `re/ghidra_export/functions.csv` (all functions) and merge of the named symbols into `re/symbols.csv`. |
| `import_symbols.py` | `re/symbols.csv` → names, functions and plate comments in a fresh project. |
| `decompile.py` | Read-only: dump decompiled C, disassembly, callers/callees or references for given addresses from the command line. |
| `annotate.py` | Bulk-apply a `address,name,kind,comment` CSV of names and plate comments, so a headless session's findings can be exported by `export_symbols.py`. |
| `med9_symbols.py` | Shared CSV/address helpers. Not a Ghidra script; imported by the two above. |

## Prerequisites

```bash
brew install ghidra                       # 12.1.3, pulls openjdk@21
python3.13 -m venv .venv                  # 3.13: the shipped jpype wheels stop at cp313
./.venv/bin/pip install -r requirements.txt
./.venv/bin/pip install \
  /usr/local/Cellar/ghidra/12.1.3/libexec/Ghidra/Features/PyGhidra/pypkg/dist/pyghidra-3.1.0-py3-none-any.whl \
  /usr/local/Cellar/ghidra/12.1.3/libexec/Ghidra/Features/PyGhidra/pypkg/dist/jpype1-1.5.2-cp313-cp313-macosx_10_13_universal2.whl
export GHIDRA_INSTALL_DIR=/usr/local/Cellar/ghidra/12.1.3/libexec
```

## Build the project (issue #7)

```bash
mkdir -p ghidra_projects
./.venv/bin/python -m pyghidra.ghidra_launch \
    --install-dir "$GHIDRA_INSTALL_DIR" \
    ghidra.app.util.headless.AnalyzeHeadless ghidra_projects med9 \
    -import data/passat_azx_ori.bin \
    -loader BinaryLoader -loader-baseAddr 0x0 \
    -processor PowerPC:BE:32:default -cspec default \
    -noanalysis \
    -scriptPath ghidra_scripts -postScript med9_setup.py
```

Takes roughly seven minutes on an M2 Pro (most of it auto-analysis) and
prints the acceptance report at the end.

Equivalent standalone form, which creates the project itself and does not
need the launcher module:

```bash
./.venv/bin/python ghidra_scripts/med9_setup.py data/passat_azx_ori.bin \
    --project-dir ghidra_projects --project-name med9
```

Script options (script args in the headless form, flags in the standalone
form): `--no-analysis`, `--no-prologue-scan`, `--code-alias`, `--boot-r2`.

Open the result in the GUI with `ghidraRun` and pick
`ghidra_projects/med9.gpr`.

### Two traps

1. **`support/analyzeHeadless` cannot run `.py` scripts.** Ghidra 12.1.3
   answers `Ghidra was not started with PyGhidra. Python is not available`.
   The `pyghidra.ghidra_launch` wrapper above is the same analyzer started
   through PyGhidra, and it is what `support/pyghidraRun -H` runs internally.
2. **The project path must not contain a directory starting with `.`.**
   Ghidra rejects it with `Path element starting with '.' is not permitted`,
   so a project cannot live under `.claude/worktrees/...`. Put
   `ghidra_projects/` in a normal checkout, or pass an absolute path
   elsewhere.

The third trap is in the loader and is handled by the script: `BinaryLoader`
creates a single block for the whole 2,605,056-byte file, which would put the
on-chip flash at 0x200000. `med9_setup.py` deletes that block and recreates
`EXT_FLASH` and `INT_FLASH` from the same `FileBytes`, so the tail lands at
0x404000.

## Symbol round trip (issue #9)

Export after a working session:

```bash
./.venv/bin/python -m pyghidra.ghidra_launch \
    --install-dir "$GHIDRA_INSTALL_DIR" \
    ghidra.app.util.headless.AnalyzeHeadless ghidra_projects med9 \
    -process passat_azx_ori.bin -noanalysis \
    -scriptPath ghidra_scripts -postScript export_symbols.py "$PWD"
```

Import into a fresh project (after `med9_setup.py` has built its map):

```bash
./.venv/bin/python -m pyghidra.ghidra_launch \
    --install-dir "$GHIDRA_INSTALL_DIR" \
    ghidra.app.util.headless.AnalyzeHeadless ghidra_projects med9 \
    -process passat_azx_ori.bin -noanalysis \
    -scriptPath ghidra_scripts -postScript import_symbols.py "$PWD"
```

The trailing `"$PWD"` is the repository root; the scripts write
`re/symbols.csv` and `re/ghidra_export/functions.csv` relative to it.

What crosses the boundary, and what does not:

- `functions.csv` gets **every** function, `FUN_` ones included, so coverage
  is diffable between sessions.
- `symbols.csv` gets only symbols whose name differs from the Ghidra default.
  Existing rows are never overwritten: the evidence recorded by hand is
  richer than anything an export can reconstruct.
- Confidence travels in the **plate comment**. `export_symbols.py` looks for
  `VERIFIED-STATIC` / `VERIFIED-DYNAMIC` / `COMMUNITY` / `HYPOTHESIS` in it
  and falls back to `hypothesis`; `import_symbols.py` writes the tag back.
  So write the tag into the plate comment while you work.
- Ghidra function *signatures*, data types and decompiler settings do not
  round trip. Only names, kinds, sizes and comments do. Anything else has to
  be re-derived by `med9_setup.py`.
