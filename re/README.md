# re/ — reverse-engineering knowledge base

`symbols.csv` is the machine-readable list of everything located in the
firmware, with its evidence and confidence (format in
`docs/04_re_guidelines.md` section 4). Ghidra exports are merged here; the
Ghidra project itself is not committed.

`measuring_vars.csv` is the measuring-variable (TKMWL) table: one row per
implemented KWP SID 0x21 variable id with its RAM address, access width, VAG
display formula and evidence. Regenerate with
`python3 tools/measuring_vars.py data/passat_azx_ori.bin --csv re/measuring_vars.csv`.

`calibration_draft.csv` is the machine-generated calibration inventory: one row
per detected map, curve, axis or scalar with its address, dimensions, element
type, the function that reads it, and the evidence. Regenerate with
`ghidra_scripts/enumerate_maps.py` (issue #19).

`calibration_names.csv` is the **hand-maintained** companion of that draft
(brief D3, issue #41): one row per named object, keyed by the draft's `addr`,
carrying the Bosch label, the unit/scale/offset of the value and of both axes,
the FR module and page, a description, and the confidence and evidence of the
name and of the scaling. It is a separate file because `enumerate_maps.py`
rewrites the draft from the image and would drop anything added to it by hand.

`med9_draft.xdf` is the TunerPro definition built from the two with
`tools/draft_to_xdf.py`; its addresses are **file offsets**.

`findings/` holds longer notes per topic (CAN, KWP, injection, ...).

Current notes:

| File | Topic |
|---|---|
| `findings/mpc5xx_registers.md` | MPC561/MPC563 register facts: IMMR/ISB, chip selects BR/OR, DMBR/DMOR calibration window, exception-table relocation, TouCAN, QSMCM, UC3F. Every fact cites the reference manual. |
| `findings/calibration_maps.md` | The 44 Bosch interpolation helpers at 0x40C000-0x411FFF: argument conventions, the self-describing and shared-axis table layouts, which axis is X, and what the detected draft is and is not. |
| `findings/calibration_coverage.md` | What of 0x5C2000-0x5E2FFF the draft accounts for, every uncovered range, and the free-space check for the FFCAL001 block. |
| `findings/calibration_call_sites.csv` | One row per interpolation-helper call site, resolved or not, with the reason. |
| `findings/fr_index.md` | Bosch MED9.1 Funktionsrahmen index: which FR module and which labels cover each area we care about, and what our dump actually confirms. |
| `findings/ram.md` | RAM survey (issue #23, static half): what references every byte of 0x7F8000-0x807FFF, what the cold start fills, where the stack and the kernel RAM are, where the KWP programming copy lands, and the block a patch may use (`PATCH_RAM = 0x7FFB00`). Data in `ram_map.csv`. |
| `ram_map.csv` | One row per 32-byte line of the two SRAMs with the reference counts and the `free_candidate` flag. Regenerate with `python3 tools/ram_survey.py data/passat_azx_ori.bin --csv re/ram_map.csv`. |
| `findings/calibration_names.md` | The naming and scaling pass (briefs D3 and E3, issue #41): the method, what the shared nmot/rl axis blocks are, the ZWMIN family, the engine speed limiter, and — §9, pass 2 — the torque ↔ charge pair (`KFMIRL` / `KFMIOP`), the charge-limit chain, the `%GGHFM` air-mass correction and the list of what is still unnamed. |
| `findings/tuning_checklist_draft.md` | Which of the named maps matter per hardware change (intake, exhaust, cams, injectors). HYPOTHESIS level, a starting point for issue #43. |

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

## The TunerPro definition (issue #41, brief D3, 2026-09-16)

### Building it

```bash
# the standard build: draft + sidecar, every detected object, named scalars on
python3 tools/draft_to_xdf.py re/calibration_draft.csv \
        -o re/med9_draft.xdf --min-confidence hypothesis
# ... plus brief D1's FFCAL001 block once that branch is merged
python3 tools/draft_to_xdf.py re/calibration_draft.csv \
        -o re/med9_draft.xdf --min-confidence hypothesis \
        --extra-rows patches/ff_fuel/ffcal001_rows.csv
python3 tools/draft_to_xdf.py --validate re/med9_draft.xdf
python3 -m unittest tests.test_draft_to_xdf
```

`re/calibration_names.csv` is picked up automatically because it sits next to
the draft; `--names FILE` points somewhere else and `--no-names` builds the raw
counts-only definition. `--extra-rows` is repeatable and takes the draft's own
column format (plus any sidecar column), so a brief that produces new objects
delivers its own rows instead of editing the draft.

`patches/ff_fuel/ffcal001_rows.csv` is such a file: the flex-fuel calibration
block FFCAL001 at 0x5E2510 exactly as `patches/ff_fuel/ffcal001.py` lays it out
(22 objects, brief D1, 2026-09-16). It is not part of the default build; the
checked-in `re/med9_draft.xdf` was generated with

```bash
python3 tools/draft_to_xdf.py re/calibration_draft.csv -o re/med9_draft.xdf \
        --min-confidence hypothesis --extra-rows patches/ff_fuel/ffcal001_rows.csv
python3 tools/draft_to_xdf.py --validate re/med9_draft.xdf
```

Until a patched image is loaded every cell of the block reads 255, because
0x5E2510 upwards is erased flash. (Brief D3's docs/05 §4 placeholder,
`re/ffcal001_draft_rows.csv`, was removed when the real rows landed.)

### Using it in TunerPro

1. **Addresses in the XDF are file offsets**, `<baseoffset>` is 0. Open
   `data/passat_azx_ori.bin` (or a copy of it) directly; do not apply an
   offset. The calibration window the ECU sees as 0x5C0000-0x5FFFFF is file
   0x1C0000-0x1FFFFF.
2. Work on a **copy**. `data/passat_azx_ori.bin` must keep its SHA-256
   `b15590d3…09b3`; the test suite fails if it changes.
3. After saving from TunerPro the block checksums are wrong. Run
   `python3 tools/checksum.py fix <copy>` and then
   `python3 tools/checksum.py verify -q <copy>`, which must print
   `ALL OK (65 blocks)`.
4. Before anything is flashed: `python3 tools/bindiff.py data/passat_azx_ori.bin
   <copy>` and check that **every** changed byte is inside the map you meant to
   change plus the checksum descriptors. `docs/04_re_guidelines.md` section 6 is
   the full pre-flash checklist, and nothing goes to the car that has not run on
   the bench spare.
5. Values are **raw counts wherever no scaling was derived**
   (`equation="X"`). Every table's description says where its name and its
   scaling come from and how confident they are — read it before turning a
   number into a physical quantity.
6. Names beginning with `cand_` are candidates, not Bosch labels. Names
   beginning with `cand_KF_`/`cand_KL_`/`cand_SST_` plus an address are pure
   placeholders: nobody has looked at that object yet. They live in the
   "Unnamed candidates" category.
7. Categories: every named object appears under its **FR module** (`ZWGRU -
   base ignition angle`, `HDRPSOL - rail pressure setpoint`, …) and under its
   shape (`Maps (2D, own axes)`, `Curves (1D, shared axis)`, `Axes`,
   `Constants`).

### Confidence legend

Two independent tags travel with every named object, both from the vocabulary
of `docs/README.md` (VERIFIED-STATIC / VERIFIED-DYNAMIC / COMMUNITY /
HYPOTHESIS, written lower case in the CSV):

| Column | What it judges |
|---|---|
| `name_confidence` | that **this label belongs to this object**. `static` means the object's role is read out of the disassembly and the FR declares exactly one label with that role and matching axes; `hypothesis` means several FR labels could fit, or the axes only partly agree. |
| `scale_confidence` | that the **unit, scale and offset** are right. `static` means the factor follows from the firmware's own arithmetic or from a breakpoint grid that only works with that factor. |

The draft's own `confidence` column is a third, separate thing: it is the
detector's confidence in the **object** (its address, shape and element type),
and it is what `--min-confidence` filters on. The sidecar never overwrites it.

### Contributing a name

1. Find the object in `re/calibration_draft.csv` by address, and read its
   `consumer_func` and `evidence`.
2. Decompile the consumer
   (`ghidra_scripts/decompile.py --project-dir /tmp/ghidra_<you> 0x<addr>`) and
   establish **what the map does**, not what it looks like. The arithmetic
   around the lookup usually fixes the fixed point: `>> 7` means 128 = 1.0,
   `>> 10` means 1024 = 1.0, `* 15 >> 1` converts 0.75 degCA to 0.1 degCA.
3. Check the axes against the units this project has already proved: `ti`
   1 us/LSB, rail pressures 0.005 bar/LSB, `tmot`/`tmst` 0.75 degC/LSB - 48,
   ignition 0.75 degCA/LSB, `nmot_w` 0.25 rpm/LSB, u8 `nmot` 40 rpm/LSB,
   `rl_w` 100/4096 %/LSB, u8 `rl` 100/128 %/LSB, internal angles 3/128 degCA.
   An axis that reads as round physical numbers under one of those and as
   nonsense under the others is evidence; an axis that reads as nonsense under
   all of them means the input is something else.
4. Only then look for the FR label
   (`re/findings/fr_index.md` has the method and the page index). **The FR is
   a different project (2.0 TFSI, 2004)**: a label whose axes or dimensions do
   not match ours stays a candidate. Never confirm a guess with another guess.
5. Add one row to `re/calibration_names.csv` with the name, both confidence
   tags, the evidence (script and command, or the decisive instruction
   address), the units and a one-sentence description. Append the symbol to
   `re/symbols.csv` too if it deserves a Ghidra label.
6. `python3 -m unittest tests.test_draft_to_xdf` checks the sidecar against the
   draft: every address must exist, every row must have a name, a confidence
   and evidence, every scale must parse, and a name must never contradict the
   draft's own.
