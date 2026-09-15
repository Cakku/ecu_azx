# Brief A1 — Development environment and Ghidra project

Issues: **#5** (Mac dev environment), **#7** (Ghidra project with verified
map), **#9** (symbol export/import). Runs now; nothing depends on hardware.
Wave B briefs need your result, so finish #7 with a working headless import
before polishing.

Read the common rules first (`00_common_rules.md`). `sudo` is **allowed only**
for the devkitPro pkg installer step; if it prompts for a password you cannot
supply, skip devkitPPC, use the LLVM path, and list the commands in the report.

## Context
- Machine: Apple Silicon Mac, Homebrew in `/usr/local` (x86_64 under
  Rosetta), Python 3.14 (`/usr/local/bin/python3`), also `python@3.13`
  formula present, OpenJDK 24 and 17 installed. No Ghidra, no PowerPC compiler.
- Ghidra: `brew install ghidra` (formula, 12.1.x, pulls openjdk@21). Language
  `PowerPC:BE:32:default`. PyGhidra wheel is under
  `<install>/Ghidra/Features/PyGhidra/pypkg/dist`; if it fails on 3.14,
  create the project venv with `python3.13`.
- The verified memory map and the exact block list are in
  `docs/03_tooling.md` §2.1 and `docs/02_memory_map.md` §3-§4.

## Tasks
1. **#5 Environment**
   - Install Ghidra via Homebrew; confirm `analyzeHeadless` runs.
   - Create `.venv` (3.13 or 3.14), `pip install -r requirements.txt`, install
     PyGhidra from the shipped wheel; record versions.
   - Compiler: `brew install llvm lld`; verify
     `clang --target=powerpc-none-eabi -mcpu=603e -c` on a tiny C file and link
     with `ld.lld -m elf32ppc` at a fixed address using a linker script; dump
     with `llvm-objcopy -O binary` and `llvm-objdump -d`. Then try devkitPPC
     (`powerpc-eabi-gcc -mcpu=505 ... ` flags from `docs/03_tooling.md` §3).
     Check that the emitted code never uses r2/r13 and has no `.sdata`.
   - Put a `hello_patch/` under `patches/examples/` with both toolchain
     variants and a `Makefile`; record what worked in `docs/03_tooling.md`
     (versions, exact commands), tagged with the date.
2. **#7 Ghidra project**
   - Write `ghidra_scripts/med9_setup.py` (PyGhidra API) implementing the seven
     steps of `docs/03_tooling.md` §2.1: EXT_FLASH 0x0 (file 0-0x1FFFFF),
     INT_FLASH 0x404000 (file 0x200000, len 0x7C000), CAL_ALIAS byte-mapped
     0x5C0000 -> 0x1C0000 len 0x40000, uninitialised peripheral/RAM blocks,
     r13=0x7FFFF0 and r2=0x5C9FF0 program-wide, disassembly of the vector
     `ba` entries and 0x404000, data types on the tables of
     `docs/02_memory_map.md` §7, then auto-analysis.
   - Write `ghidra_scripts/README.md` with the headless command line
     (`analyzeHeadless <projdir> med9 -import data/passat_azx_ori.bin -loader
     BinaryLoader ... -postScript med9_setup.py`) and run it. Project directory
     `ghidra_projects/` is gitignored.
   - Acceptance evidence to include in the report: number of functions in
     EXT_FLASH and INT_FLASH; count of references landing in CAL_ALIAS
     (expect thousands); decompilation of `romcheck_result_set` (0x20004) shows
     stores to 0x7F824A.. via r13; the KWP handler 0x4386D8 is a function.
   - Known trap: the loader will create one block for the whole file; split or
     re-create blocks so the tail is at 0x404000, not 0x200000.
3. **#9 Export/import**
   - `ghidra_scripts/export_symbols.py`: functions, labels, plate comments to
     `re/ghidra_export/functions.csv` and merge new entries into
     `re/symbols.csv` (columns as in `docs/04_re_guidelines.md` §4, confidence
     `hypothesis` for auto-named `FUN_` entries — or skip those; only export
     names that were changed from defaults plus all functions to
     `functions.csv`).
   - `ghidra_scripts/import_symbols.py`: applies names/comments from
     `re/symbols.csv` to a fresh project. Prove the round trip.
4. Commit on `agent/A1`. Comment on #5, #7, #9.

## Not in scope
Naming functions beyond what the setup script creates; r2 boot-context
assignment (#8, Wave B); any patch beyond the hello example.
