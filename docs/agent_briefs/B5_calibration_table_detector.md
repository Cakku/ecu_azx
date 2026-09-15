# Brief B5 — Calibration map detector and draft definition

Issue: **#19**. Prerequisite: brief A1 merged. No `sudo`.

Read `00_common_rules.md`, `docs/02` §3 (calibration alias 0x5C0000-0x5FFFFF,
7,055 references, r2 = 0x5C9FF0 small data) and §5 first.

## Tasks
1. Identify the Bosch interpolation helpers: the few functions called
   thousands of times whose arguments are pointers into 0x5Cxxxx (1D curve
   lookup, 2D map lookup, possibly 8-bit and 16-bit variants and "group"
   lookups with shared axes). Name them (`lookup_1d_u8`, ...), document the
   argument convention (axis pointer, value pointer, sizes, input variables)
   and the data layout they imply (axis count byte, breakpoints, values).
2. Script `ghidra_scripts/enumerate_maps.py`: for every call site, recover
   the map/axis addresses (constant arguments, r2-relative or `lis 0x5D`
   forms), dimensions, element size, and the calling function. Output
   `re/calibration_draft.csv`: `addr, kind, x_axis_addr, y_axis_addr, x_n,
   y_n, elem_size, signed, consumer_func, name_or_blank, confidence,
   evidence`.
3. Cross-check coverage: which parts of 0x5C2000-0x5E2FFF are not covered
   by any detected structure? List them (constants, strings, tables of
   pointers, our future FFCAL001 block area 0x5E2510+).
4. Names: use FR names only where the consumer function is already named
   (A2/A3/B6-B9 results) or where the structure is unmistakable (e.g. the
   identification block). Everything else stays blank or `cand_`. Do not
   import names from 2.0 TFSI XDFs without evidence; you may list such a
   guess in the notes column as COMMUNITY.
5. Provide `tools/draft_to_xdf.py` producing a TunerPro XDF (base offset
   handling: XDF addresses are file offsets, so map through `med9lib`).
6. Commit on `agent/B5`; comment on #19.

## Acceptance
The CSV lists every call site of the interpolation helpers with resolved
addresses; a generated XDF opens in TunerPro (or is at least well-formed XML
validated against a known XDF structure).
