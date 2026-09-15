# re/ — reverse-engineering knowledge base

`symbols.csv` is the machine-readable list of everything located in the
firmware, with its evidence and confidence (format in
`docs/04_re_guidelines.md` section 4). Ghidra exports are merged here; the
Ghidra project itself is not committed.

`findings/` holds longer notes per topic (CAN, KWP, injection, ...).

`ghidra_export/functions.csv` is a regenerated dump of **every** function in
the current Ghidra project, auto-named `FUN_` ones included. It is not
knowledge, it is coverage: diff two of them to see what a session added.

## Round trip with Ghidra (issue #9)

Both directions run headless; the full command lines, including the PyGhidra
wrapper that `analyzeHeadless` needs for `.py` scripts, are in
`ghidra_scripts/README.md`.

```bash
# after a session: Ghidra -> re/
... -postScript export_symbols.py "$PWD"

# into a fresh project, after med9_setup.py has built the map: re/ -> Ghidra
... -postScript import_symbols.py "$PWD"
```

Rules the two scripts follow, so that nothing is lost or silently invented:

- Only symbols whose name differs from a Ghidra default reach `symbols.csv`.
  That filter also drops names that *look* hand-written but are not: thunks
  (Ghidra names them after their target) and the decompiler's `switchD`,
  `switchdataD`, `caseD` and `default` labels.
- A row that already exists (same address **and** name) is never overwritten.
  The evidence recorded by hand is richer than anything an export can
  reconstruct, so the export only ever appends.
- **Confidence travels in the Ghidra plate comment.** Write
  `VERIFIED-STATIC`, `VERIFIED-DYNAMIC`, `COMMUNITY` or `HYPOTHESIS` into it
  while you work; `export_symbols.py` reads the tag back out and falls back to
  `hypothesis` when there is none. `import_symbols.py` writes the tag,
  evidence and source back into the plate comment, so the cycle is stable.
- Function signatures, data types and decompiler settings do **not** round
  trip. They are re-derived by `ghidra_scripts/med9_setup.py`, which is why
  that script, not the project file, is the source of truth for the map.
