# The Bosch MED9 interpolation library and the calibration map draft

Agent B5, brief `docs/agent_briefs/B5_calibration_table_detector.md`, issue #19.
Date: 2026-09-15. Dump: `data/passat_azx_ori.bin`
(03H906032 / 1037382557, SHA-256 `b15590d3…09b3`), unchanged
(`tools/checksum.py verify -q` → `ALL OK (65 blocks)` before and after).

Everything in sections 1–4 is **VERIFIED-STATIC**: it comes from the Ghidra
decompilation of the named function, which anybody can reproduce with

```bash
./.venv/bin/python ghidra_scripts/enumerate_maps.py \
    --project-dir /tmp/ghidra_B5 --project-name med9 --repo .
```

after building the project per `ghidra_scripts/README.md`. Section 5 states
where the draft is a **HYPOTHESIS** and why.

---

## 0. The one thing to know first

The MED9 application does **not** inline its table lookups. Every curve and
every map is read by one of **44 helper functions that sit together in the
on-chip flash at 0x40C000-0x411FFF**, and they are called **1,343 times**.
That is the whole detector: find those 44 functions, resolve the constant
arguments at every call site, and the calibration inventory falls out.

This also corrects an assumption that was easy to make from
`docs/02_memory_map.md` section 2, which describes the on-chip flash as "the
KWP/flash-programming services and a second copy of the application start-up".
It holds those, **and the shared ASCET/Bosch runtime library** — interpolation,
saturating multiply/divide, debounce counters, ramps and hysteresis — which the
external-flash application calls across the region boundary with ordinary `bl`
(±32 MB reach, so no trampoline is needed).

Numbers, for orientation:

| | |
|---|---|
| `bl` instructions in the whole image | 10,489 |
| … of them to one of the 44 helpers | 1,343 (12.8 %) |
| call sites whose arguments resolve to constants | 1,306 (97.2 %) |
| call sites internal to the library (a 2-D wrapper tail-calling its own interpolator) | 17 |
| call sites that did **not** resolve | 20 (1.5 %) |
| absolute references into 0x5C0000-0x5FFFFF (`tools/find_abs_refs.py`) | 7,055 |
| … that form a pointer (`lis`+`addi`) rather than loading a value | 2,196 |
| distinct calibration objects detected | 1,066 tables/curves/axes + 5,488 scalars |

---

## 1. Data layout

Two shapes exist, and both are used heavily.

### 1.1 Self-describing tables

The table carries its own dimensions in the first one or two elements and the
caller passes a single pointer.

```c
struct KL_u16 { u16 n;  u16 axis[n];  u16 val[n]; };              /* 2 + 4n bytes */
struct KF_u16 { u16 ny; u16 nx; u16 yaxis[ny]; u16 xaxis[nx];
                u16 val[ny * nx]; };                              /* 4 + 2ny + 2nx + 2*ny*nx */
```

The 8-bit variants are the same with `u8`/`s8` throughout (`1 + 2n`,
`2 + ny + nx + ny*nx`).

### 1.2 Group tables (shared axes)

Several tables share one axis, so the axis and the value array are separate
pointers and the breakpoint count is a separate argument:

```c
u16 lookup_1d_g_u8_u16(u32 n, const u8 *axis, const u16 *val, u32 x);
u16 lookup_2d_g_u8_u16_u16(u32 ny, const u8 *yaxis,
                           u32 nx, const u16 *xaxis,
                           const u16 *val, u32 vy, u32 vx);
```

The generated code does **not** pass `n` as an immediate; it reads it out of
the shared axis, which is itself a self-describing `{ n; axis[n] }` block. The
canonical sequence, at 0x0BE9FC (`FUN_000be9ec`):

```
0bea00  subi r31,r31,0x33d2   ; r31 = 0x5CCC2E, the shared axis block
0bea04  lbz  r3,0x0(r31)      ; n  = 11
0bea08  lbz  r6,-0x315b(r13)  ; x  from RAM
0bea0c  addi r4,r31,0x1       ; axis = 0x5CCC2F
0bea10  addi r5,r31,0xc       ; val  = 0x5CCC3A = axis + n
0bea14  bl   0x0040f600       ; lookup_1d_g_u8_u16
```

That `lbz` is why a purely `lis`-based scanner finds nothing for these forms:
the detector has to fold constant loads out of flash. `enumerate_maps.py`
does exactly that, for the three read-only regions only, so r13-relative RAM
loads stay unknown.

### 1.3 Indexing — which axis is X

Both 2-D families end in the same value-array interpolator:

```c
/* FUN_0040c444, interp_2d_u16 */
p = val + ((key_x >> 16) + nx * (key_y >> 16)) * 2;
```

so the element at (iy, ix) is `val[iy * nx + ix]`. **The second axis argument
is the one with stride 1**, i.e. the X (column) axis in TunerPro terms, and the
first is the Y (row) axis. The calling convention is therefore
`lookup_2d(table, y, x)`, not `(x, y)`.

The layout check that settles it independently: the 14 × 10 `u16` map at
0x5CBF3E (struct 0x5CBF08, five call sites) has

* first axis, 10 points, 0, 1280, 2560 … 11520 — evenly spaced, a load axis;
* second axis, 14 points, 1200, 2880, 4000 … 26200 — an engine-speed axis
  (÷4 gives 300 … 6550 min⁻¹);

and the 14 values that are contiguous in memory are the ones that sweep the
speed axis. rpm across the columns is also how every community XDF for these
ECUs draws a `KF`.

### 1.4 Interpolation

The search is a binary-ish search with a linear walk-down, clamped at both
ends, and the fraction is 16-bit:

```
key   = (index << 16) | ((x - axis[index]) << 16) / (axis[index+1] - axis[index])
value = v0 + (((key & 0xFFFF) * (v1 - v0)) >> 16)
```

Two helpers return the breakpoint value without interpolating
(`lookup_1d_u8_noint`, `lookup_2d_u8_noint`, `interp_2d_u8_noint`) — those are
the "Kennfeld ohne Interpolation" variants and matter when a map holds an enum
or a bit mask rather than a physical quantity.

---

## 2. The 44 helpers

`kind` names are the ones `enumerate_maps.py` writes into the program and
`export_symbols.py` carries into `re/symbols.csv`. "calls" is the number of
call sites in the image.

### 2.1 One-dimensional, self-describing — `(struct, x)`

| CPU | name | axis | value | interp | calls |
|---|---|---|---|---|---|
| 0x40EDE4 | `lookup_1d_u8` | u8 | u8 | yes | 172 |
| 0x40EEB0 | `lookup_1d_s8` | s8 | s8 | yes | 1 |
| 0x40EFAC | `lookup_1d_u16` | u16 | u16 | yes | 194 |
| 0x40F098 | `lookup_1d_s16` | s16 | s16 | yes | 22 |
| 0x40F184 | `lookup_1d_u8_noint` | u8 | u8 | no | 7 |
| 0x40F22C | `lookup_1d_u16_noint` | u16 | u16 | no | 1 |

### 2.2 One-dimensional, shared axis — `(n, axis, val, x)`

| CPU | name | axis | value | calls |
|---|---|---|---|---|
| 0x40F454 | `lookup_1d_g_u8_s8` | u8 | s8 | 10 |
| 0x40F51C | `lookup_1d_g_s8_u8` | s8 | u8 | 6 |
| 0x40F600 | `lookup_1d_g_u8_u16` | u8 | u16 | 56 |
| 0x40F6C4 | `lookup_1d_g_s16_u16` | s16 | u16 | 37 |
| 0x40F7A0 | `lookup_1d_g_u16_s16` | u16 | s16 | 48 |
| 0x40F87C | `lookup_1d_g_u16_s8` | u16 | s8 | 2 |
| 0x40F95C | `lookup_1d_g_u16_u8` | u16 | u8 | 44 |

### 2.3 Two-dimensional, self-describing — `(struct, vy, vx)`

| CPU | name | axes | value | interp | calls |
|---|---|---|---|---|---|
| 0x40D18C | `lookup_2d_u8` | u8 | u8 | yes | 71 |
| 0x40D2F8 | `lookup_2d_u16` | u16 | u16 | yes | 142 |
| 0x40D4A4 | `lookup_2d_s16` | s16 | s16 | yes | 15 |
| 0x40D650 | `lookup_2d_u16_s16` | u16 | s16 | yes | 17 |
| 0x40D7FC | `lookup_2d_u8_noint` | u8 | u8 | no | 13 |

`FUN_0040d094` is a floating-point sibling with zero call sites; it is left
unnamed.

### 2.4 Two-dimensional, shared axes — `(ny, yaxis, nx, xaxis, val, vy, vx)`

| CPU | name | y axis | x axis | value | calls |
|---|---|---|---|---|---|
| 0x40D964 | `lookup_2d_g_u8_s8_s8` | u8 | s8 | s8 | 0 |
| 0x40DAF0 | `lookup_2d_g_u8_s8_s16` | u8 | s8 | s16 | 0 |
| 0x40DC7C | `lookup_2d_g_u8_u8_s8` | u8 | u8 | s8 | 28 |
| 0x40DDE4 | `lookup_2d_g_u8_u16_u8` | u8 | u16 | u8 | 13 |
| 0x40DF64 | `lookup_2d_g_s8_u16_u8` | s8 | u16 | u8 | 0 |
| 0x40E108 | `lookup_2d_g_u16_u8_u8` | u16 | u8 | u8 | 11 |
| 0x40E288 | `lookup_2d_g_s16_u8_s16` | s16 | u8 | s16 | 1 |
| 0x40E408 | `lookup_2d_g_s16_u8_u16` | s16 | u8 | u16 | 2 |
| 0x40E588 | `lookup_2d_g_u16_s8_s8` | u16 | s8 | s8 | 0 |
| 0x40E72C | `lookup_2d_g_u8_u16_u16` | u8 | u16 | u16 | 25 |
| 0x40E8AC | `lookup_2d_g_s16_u16_u16` | s16 | u16 | u16 | 10 |
| 0x40EA44 | `lookup_2d_g_s8_u8_u8` | s8 | u8 | u8 | 3 |
| 0x40EBD0 | `lookup_2d_g_s8_u8_s16` | s8 | u8 | s16 | 0 |

The thirteen exist because the generator instantiates one per (y type, x type,
value type) combination it needs; the five with no call sites are library
dead code and are kept in the table so that a future dataset that does use
them is recognised.

### 2.5 Axis search — `(axis_struct, x)` → `(index << 16) | frac`

| CPU | name | axis | calls |
|---|---|---|---|
| 0x40C578 | `axis_search_u8` | u8 | 42 |
| 0x40C614 | `axis_search_s8` | s8 | 2 |
| 0x40C6D8 | `axis_search_u16` | u16 | 37 |
| 0x40C78C | `axis_search_s16` | s16 | 5 |
| 0x40C840 | `axis_search_u8_hint` | u8 | 36 |
| 0x40C8F0 | `axis_search_s8_hint` | s8 | 5 |
| 0x40C9CC | `axis_search_u16_hint` | u16 | 37 |
| 0x40CA94 | `axis_search_s16_hint` | s16 | 1 |

The `_hint` variants take the previous key as a third argument and walk up or
down from it instead of bisecting — the classic "the operating point moves
slowly" optimisation. Argument layout is identical otherwise.

### 2.6 Value-array interpolators — `(val, nx, key_y, key_x)`

| CPU | name | value | interp | calls |
|---|---|---|---|---|
| 0x40C334 | `interp_2d_u8` | u8 | yes | 86 |
| 0x40C3B4 | `interp_2d_s8` | s8 | yes | 24 |
| 0x40C444 | `interp_2d_u16` | u16 | yes | 52 |
| 0x40C4D0 | `interp_2d_s16` | s16 | yes | 59 |
| 0x40C55C | `interp_2d_u8_noint` | u8 | no | 6 |

These are called both from inside the 2-D helpers above **and** directly from
application code that did its own axis searches (typically with the `_hint`
variants, to share one operating point between several maps). A direct call
reveals the value array and the row length, but not the row count — see
section 5.

### 2.7 Neighbours that are *not* table lookups

Recorded so nobody re-checks them: 0x40C2BC / 0x40C2EC saturating divide,
0x410054 / 0x410060 / 0x410084 / 0x4101A0 saturating multiply-shift,
0x40FFF0 divide-by-zero-safe divide, 0x4104C4 / 0x410508 / 0x410554 / 0x410598
down-counters, 0x4105E4 / 0x41061C / 0x410654 / 0x410690 debounce counters,
0x4115EC / 0x411648 / 0x4116A4 ramps, 0x40CBF8 / 0x40CC70 limiters,
0x40CD3C / 0x40CE80 filters. They appear in the "top callees near a
calibration reference" statistic only because their *thresholds* are
calibration constants loaded with `lbz`/`lhz` — which is also why the
5,488 scalars in the draft matter.

---

## 3. What the detector produces

`ghidra_scripts/enumerate_maps.py` writes three files:

| File | Content |
|---|---|
| `re/calibration_draft.csv` | one row per distinct object: `addr, kind, x_axis_addr, y_axis_addr, x_n, y_n, elem_size, signed, consumer_func, name_or_blank, confidence, evidence` plus `x_elem, y_elem, struct_addr, sites` |
| `re/findings/calibration_call_sites.csv` | one row per call site, including the 20 that did not resolve and why, and the 17 that are internal to
   the library |
| `re/findings/calibration_coverage.md` | coverage of 0x5C2000-0x5E2FFF, the free-space check for FFCAL001, and every uncovered range |

Counts:

| kind | objects | meaning |
|---|---|---|
| `map_2d` | 151 | 2-D, dimensions read from the table header |
| `map_2d_shared` | 83 | 2-D, shared axes, all five pointers/counts resolved |
| `map_2d_data` | 195 | value array from a direct `interp_2d_*` call |
| `curve_1d` | 312 | 1-D, dimensions from the header |
| `curve_1d_shared` | 172 | 1-D, shared axis |
| `axis` | 153 | standalone `{ n; axis[n] }` breakpoint block |
| `scalar` | 5,488 | single value loaded with `lbz`/`lhz`/`lha`/`lwz` |

Shapes, for a sanity check against what a MED9 dataset looks like: the maps
whose axes are read from the image have `nx` ∈ 2…16 and `ny` ∈ 3…16, and the
most common shapes are 5×5 (27), 6×6 (23), 8×8 (21), 4×4 (21) and
8×6 (12).  Curves have 2…35 breakpoints.

`tools/draft_to_xdf.py` turns the CSV into a TunerPro `.xdf`.
`re/med9_draft.xdf` is the committed result (1,066 tables, no scalars).
**XDF addresses are file offsets**, mapped with `med9lib.cpu_to_file`
(cpu − 0x400000 inside the window), and `<baseoffset>` is 0. Signedness
travels in `mmedtypeflags` bit 0x01; the LSB-first bit 0x02 is never set
because the ECU is big-endian; `mmedrowcount` = `y_n`, `mmedcolcount` = `x_n`
and both strides are 0, which is TunerPro's packed row-major — the same
`val[iy*nx+ix]` the helpers use. `tests/test_draft_to_xdf.py` asserts all of
that, and `python3 tools/draft_to_xdf.py --validate FILE` re-checks a file.

**No scaling is applied.** Every axis and value in the XDF is raw counts with
`equation="X"`. The physical factors are a separate job (briefs B6-B9 and the
FR labels in `fr_index.md`); a wrong factor is worse than no factor.

---

## 4. Names

Per the brief, nothing is named from a 2.0 TFSI XDF. The `name_or_blank`
column is empty for every row, and the XDF uses placeholders
`cand_KF_<addr>` / `cand_KL_<addr>` / `cand_GKF_…` / `cand_SST_…`. The
`consumer_func` column carries the Ghidra name of the calling function, which
is the hook a later brief needs: once A2/A3/B6-B9 name a consumer, every map
it reads inherits a candidate FR label.

> **Corrections, 2026-09-16 (D3, issue #41).** Two statements above are now
> out of date. (a) `name_or_blank` is no longer empty for every row: waves
> B6-B9 named 78 objects in place. (b) Names and scaling are not kept there any
> more, because **`enumerate_maps.py` writes `name_or_blank` empty on every
> regeneration**, so anything added to the draft by hand is lost the next time
> the detector runs. The hand knowledge lives in the sidecar
> `re/calibration_names.csv` (157 objects with unit, scale, offset, FR module
> and two confidence tags), which `tools/draft_to_xdf.py` merges on `addr`; the
> 78 draft names are duplicated there and a test asserts the two never
> disagree. The sidecar also corrects the shape of the six `KFPRSOL*` maps,
> whose `x_n`/`y_n` §5 leaves empty because the row length comes from a memory
> load. See `re/README.md` and `re/findings/calibration_names.md`.
>
> The statement that **no scaling is applied** is also superseded: 98 objects
> now carry a physical conversion, each tagged with how sure we are of it.
> Everything else is still `equation="X"`.

Only the 44 helper functions are pushed into `re/symbols.csv`
(`export_symbols.py`), with `VERIFIED-STATIC` in the plate comment. The 1,066
tables are deliberately **not** labelled in Ghidra by default — they would add
a thousand rows to a knowledge base several agents are appending to in
parallel. `enumerate_maps.py --label` puts `cand_*` labels on them for a local
session.

---

## 5. Where this is a hypothesis, and how wrong it can be

1. **`map_2d_data` row counts.** A direct `interp_2d_*` call passes only the
   row length. Where the same function searched two axes within 0x200 bytes of
   code before the call, `enumerate_maps.py` matches them positionally (the
   axis whose length equals the stride is X) — that worked for 14 maps and is
   tagged `hypothesis`, because the association is positional, not dataflow.
   For the other 125 the row count is inferred from the distance to the next
   detected object, preferring the row count of an identically shaped map that
   ends exactly at this address, and capped at 20 rows. The inferred
   distribution (4…17, mode 8 and 16) matches the verified one (3…16), which is
   the only cross-check available. **Expect individual maps to have one row too
   many.** Every such row says so in its `evidence` column.
2. **r2.** The detector uses the Ghidra register context, which
   `med9_setup.py` sets to the application value 0x5C9FF0. The boot module runs
   with r2 = 0x17FF0 (issue #8, agent B1). No detected object has a consumer in
   0x001000-0x01FFFF, so nothing in this draft depends on the boundary — but if
   B1 moves it, re-run the detector.
3. **The 20 unresolved call sites** pass their table pointer in a register that
   comes from somewhere the block simulator does not follow (a struct field, a
   pointer table, or a parameter of the calling function). They are listed in
   `calibration_call_sites.csv` with the reason. The tables they reach are part
   of the uncovered bytes in the coverage report.
4. **Scalars.** A `lbz` of a calibration byte is evidence that the byte is
   *read*, not that it is a tunable constant on its own: some of the 5,488 are
   the count byte at the head of a shared axis, and some are single cells of a
   map that the code reads directly. They are in the CSV because coverage needs
   them and because the majority really are Bosch *Festwerte*; they are off by
   default in the XDF (`--scalars` turns them on).
5. **Coverage is 49.0 %** of 0x5C2000-0x5E2FFF, in 649 uncovered ranges.
   That is the honest figure: an axis and the value array it serves are counted
   as two separate ranges, so nothing in between is claimed, and 414 of the 649
   gaps are under 8 bytes. The seven gaps over 1 KB are 0x5C2A15 (1,003 B, all
   zero), 0x5C4E72 (1,026 B), 0x5C7CBC (1,026 B), 0x5CF646 (4,502 B),
   0x5D9C14 (5,337 B), **0x5DB0EE-0x5E24F9 (29,708 B, 97 % zero — reserve, not
   maps)** and 0x5E2502 (2,814 B, 99 % 0xFF). Excluding the zero/erased tail
   from 0x5DB0EE upwards, coverage of the part that actually holds calibration
   (0x5C2000-0x5DB0ED) is about 63 %.

---

## 6. FFCAL001 free space (for `docs/06_patch_pipeline.md` section 3)

Checked by reading the bytes, not by reputation:

* **0x5E2510-0x5FFFFF (file 0x1E2510-0x1FFFFF), 121,584 bytes, is erased
  (every byte 0xFF).**
* The highest address any detected table, axis or scalar load reaches is
  **0x5E2502** — the two bytes of the `5A5A5A5A` block marker at 0x5E2500,
  read by `FUN_0006dd40` from 0x06DFA0.
* So the whole of 0x5E2510 upwards is free, and the block it lies in
  (0x5E0000-0x5EFFFF) is one of the six covered by the calibration checksum
  table at 0x5C3300 — anything written there has to go through
  `tools/checksum.py fix`.

---

## 7. Reproducing

```bash
# 1. build or copy the Ghidra project (ghidra_scripts/README.md)
./.venv/bin/python ghidra_scripts/med9_setup.py data/passat_azx_ori.bin \
    --project-dir /tmp/ghidra_B5 --project-name med9
./.venv/bin/python -m pyghidra.ghidra_launch --install-dir "$GHIDRA_INSTALL_DIR" \
    ghidra.app.util.headless.AnalyzeHeadless /tmp/ghidra_B5 med9 \
    -process passat_azx_ori.bin -noanalysis \
    -scriptPath ghidra_scripts -postScript import_symbols.py "$PWD"

# 2. enumerate (about 25 s)
./.venv/bin/python ghidra_scripts/enumerate_maps.py \
    --project-dir /tmp/ghidra_B5 --project-name med9 --repo .

# 3. definition file
python3 tools/draft_to_xdf.py re/calibration_draft.csv \
    -o re/med9_draft.xdf --min-confidence hypothesis
python3 tools/draft_to_xdf.py --validate re/med9_draft.xdf
python3 -m unittest tests.test_draft_to_xdf
```
