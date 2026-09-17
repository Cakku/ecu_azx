# Naming and scaling the calibration (brief D3, issue #41)

Agent D3, brief `docs/agent_briefs/D3_calibration_definition.md`, issue #41.
Date: 2026-09-16. Dump `data/passat_azx_ori.bin` (03H906032 / 1037382557,
SHA-256 `b15590d3…09b3`), unchanged — `tools/checksum.py verify -q` prints
`ALL OK (65 blocks)` before and after. No byte of the image was written.

Tags as in `docs/agent_briefs/00_common_rules.md`. In this document
**VERIFIED-STATIC** always means: read out of the disassembly of the named
address in a private copy of the Ghidra project
(`/tmp/ghidra_D3`, built per `re/findings/injection.md` §0), or read out of the
bytes of the dump with `tools/med9lib.py`.

---

## 0. What this brief added

| | before | after |
|---|---|---|
| objects with a name | 78 | **157** |
| … tagged `static` | 54 | 24 |
| … tagged `hypothesis` | 24 | 133 |
| objects with a unit | 0 | **146** |
| objects with a non-identity scale on the value or an axis | 0 | **98** |
| … scaling tagged `static` | — | 118 |
| … scaling tagged `hypothesis` | — | 39 |

By kind: 82 scalars, 25 `map_2d_data`, 17 `map_2d_shared`, 10
`curve_1d_shared`, 10 axes, 8 `map_2d`, 5 `curve_1d`.

**The `static` count went down on purpose.** The draft had one `confidence`
column doing two jobs, and waves B6-B9 used it for both: `KFZW` was `static`
because the map is understood, even though the label `KFZW` comes from a
Funktionsrahmen for a different engine. D3 splits the question (§1): the
confidence in the *object* stays in the draft, and two new columns judge the
*label* and the *scaling* separately. A Bosch label taken from the TFSI FR is
`hypothesis` here even when the map behind it is fully understood; it becomes
`static` only when the role is read out of the disassembly **and** the FR
declares exactly one label with that role and matching axes. Nothing was
un-learned — 118 of the 157 rows carry a `static` scaling, which is the number
that says how much of this is real.

## 1. Where the knowledge lives now

`re/calibration_draft.csv` is machine-generated: `enumerate_maps.py` rewrites
it from the image and writes `name_or_blank` empty for every row, so **names
kept there do not survive a regeneration**. Brief D3 therefore put the hand
knowledge in a sidecar, `re/calibration_names.csv`, keyed by the draft's
`addr`, and taught `tools/draft_to_xdf.py` to merge the two. The sidecar is
the file a human edits; `re/README.md` has the column list, the confidence
legend and the contribution recipe.

The sidecar may also **correct** a draft column, but only where the detector
could not read a shape out of the image. That happened for the six
`KFPRSOL*` maps (§4) and for two `s8` scalars the detector recorded as `u8`;
each correction names its evidence in `name_evidence`.

## 2. The units this dataset uses (all carried over, all cited)

| Quantity | 1 LSB | Where it was established |
|---|---|---|
| `ti`, dead time, `TIMINP` | 1 us | `rail.md` §8 and §14.1 (the 3/128 degCA angle LSB and `k_nmot`) |
| rail pressure, `dp` | 0.005 bar | `rail.md` §2, cross-checked against display format 0x53 in `measuring_vars.md` §7.3 |
| `tmot`, `tmst` (u8) | 0.75 degC, offset −48 | `start.md` §1, cross-checked against display format 0x05 in `measuring_vars.md` §7.1 |
| ignition angles (s8) | 0.75 degCA | `ignition.md` §6 and §8 (`zw * 15 / 2` into the 0.1 degCA TPU unit) |
| internal injection angles | 3/128 degCA | `rail.md` §14.1 (0x7800 = 720 degCA) |
| `nmot_w` (u16) | 0.25 rpm | `ignition.md` §2 |
| `nmot` (u8, 0x7FCE95) | 40 rpm | `measuring_vars.md` §7.2 |
| `rl_w` (u16) | 100/4096 % | `ignition.md` §2 |
| **`rl` (u8, 0x7FEF74)** | **100/128 %** | **new, §3** |
| `ksta`, `kstaa` | 1024 = 1.0 | `start.md` §3.2 |

### 2.1 Closed: the percent scaling of the u8 relative charge

`rail.md` §13 left "percent scaling of the u8 `rl` (0x7FEF74)" open.
**It is 100/128 % per LSB** — VERIFIED-STATIC:

* `SRL11OPUW` (0x5CA3E6), the load axis of `KFZWOP`, is 11 u8 breakpoints
  `13, 20, 27, 40, 53, 67, 80, 93, 107, 120, 133`;
* `SRL12ZUUW` (0x5C775A), the load axis of `KFZW`, is 12 u16 breakpoints
  `416, 640, 864, 1056, 1280, 1696, 2144, 2560, 2976, 3424, 3840, 4256` at the
  proven 100/4096 %/LSB;
* the eleven u8 points are exactly the twelve u16 points divided by 32 with the
  25.8 % point dropped (416/32 = 13, 640/32 = 20, 864/32 = 27, 1280/32 = 40 …
  4256/32 = 133), so the u8 is the u16 shifted right by 5 and 1 LSB is
  32 × 100/4096 = **100/128 % = 0.78125 %**.

The same grid appears in `axis_awea_rl` (0x5C88DC, `13, 27, 40, 53, 67, 80,
93, 107`), which therefore runs 10.2 … 83.6 %, and in the `KFZWOP` delta maps.

This *disagrees* with the reading of VCDS display format 0x21 in
`measuring_vars.md` §7.4, which assumed `100 × B / A` with `A = 133` and would
make 133 counts read 100 % rather than 103.9 %. Only the firmware-internal
identity above is evidence about the ECU's own scaling; the display formula is
still COMMUNITY and may well carry a different normalisation for the tester.
Do not use the one to correct the other without a log.

> **SETTLED 2026-09-17 (E3, #41), and D3 was right.** The u8 is not merely
> *consistent with* `rl_w >> 5`, it **is** `rl_w >> 5`: at 0x419280
> `rlwinm r6,r5,0x1b,0x15,0x1f` shifts `rl_w` right by five and 0x419284
> stores it to 0x7FEF74, with the clamp `cmplwi r12,0x1fe0` (= 255 × 32) two
> instructions earlier; the two initialisation sites 0x11BC38 / 0x12D8B4 write
> the pair `rl_w = 4267` / `u8 rl = 133` together. So 1 LSB = 32 × 100/4096 =
> **100/128 %**, VERIFIED-STATIC from the instruction, not from a grid.
> The display side is settled too, in the emulator: the real handler for id 2
> emits **B = the raw byte with no arithmetic** and a constant `A = 0x85`, so
> `A` is a tester-side normalisation and never was a claim about the ECU's
> LSB. There is no contradiction to resolve — both notes describe different
> things, and `measuring_vars.md` §7.5 has the sweep and the reproduction.

## 3. The shared nmot × rl breakpoint blocks (VERIFIED-STATIC)

`FUN_000BDB58` (0x0BDB58-0x0BDC4B) is the central axis-key process. It
performs eleven breakpoint searches and stores the keys in the 0x7FD7xx-0x7FD8xx
block; two of them label most of the big ignition-domain maps:

```
0BDB98  DAT_007fd820 = axis_search_u16_hint(&DAT_005c8982, nmot_w, DAT_007fd820)
0BDBAC  DAT_007fd84c = axis_search_u16_hint(&DAT_005c89e0, rl_w,   DAT_007fd84c)
```

`tools/sda_xref.py data/passat_azx_ori.bin --var 0x7FD820` (and `--var
0x7FD84C`) finds **exactly one writer each**, the two instructions above, and
nine resp. fourteen readers. So every map read with that key pair is a map over
those two axis blocks, and a map that is `map_2d_data` in the draft (no axes
recovered) gets its axes from the dataflow instead:

| Axis block | Data | Points | Physical | Named |
|---|---|---|---|---|
| 0x5C8982 | **0x5C8984** | 16 u16 | 520 … 6520 rpm | `axis_nmot16_w` |
| 0x5C89E0 | **0x5C89E2** | 12 u16 | 10.2 … 103.9 % | `axis_rl12_w` |

The breakpoints of `axis_nmot16_w` are identical to `SNM16ZUUW`'s; the
breakpoints of `axis_rl12_w` differ from `SRL12ZUUW`'s in the second point
(512 = 12.5 % instead of 640 = 15.6 %), so they are separate objects and
neither FR label was reused. The FR names this family `SNM16<module>UW` /
`SRL12<module>UW`; which member these two are is not established, so the names
stay descriptive.

Maps that gained both axes from this (all s8 at 0.75 degCA/LSB unless noted):

| Map | Consumer | Role |
|---|---|---|
| `cand_KFSWKFZK` 0x5D5A3E, `cand_KFSWKFZKR` 0x5D5AFE, `cand_KFDZK` 0x5D597E | `zwgru_low_octane_detect` 0x0F436C | the low-octane-fuel detector (`ignition.md` §13.2) |
| `cand_DZWZYLB2` 0x5C7772 | `FUN_00431140` 0x4311BC → 0x7FD317 | the only bank-to-bank ignition difference, −3.75 … +2.25 degCA |
| `cand_KFDZWZWB` 0x5C78B2 | `FUN_004311D4` 0x431208 → 0x7FD31A | additive delta, all zero |
| `cand_KFDZWGRU` 0x5C7A58 | `FUN_00431228` 0x431250 → 0x7FD337 | additive delta weighted by a Q16 factor, all zero |
| `cand_KFETAZWOP` 0x5CA092 (u16) | `FUN_00436498` 0x4364DC → 0x800020 | the reference the `KFDZWKG` weighting is measured against |

and `cand_KFDZWKG` (0x5C753E) gained `SNM16ZUUW` × `SRL12ZUUW`, because the
keys it is handed (0x7FD5EC / 0x7FD5F0) are written only by
`zwgru_kfzw_lookup` 0x41D334 — i.e. it rides on `KFZW`'s own axes.

## 4. `%ZWMIN` — the latest permitted ignition angle (VERIFIED-STATIC)

`FUN_00458E74` (0x458E74-0x459333) is the ZWMIN module. `ignition.md` §8 had
its output side (`zwmin_select` 0x41D440 picks 0x7FD32B or 0x7FD32C into
`zwmin` 0x7FD32A); this is the producer. It searches its own speed axis

```
0458E90  DAT_007fd5f4 = axis_search_u16_hint(&DAT_005c7a36, nmot_w, DAT_007fd5f4)
```

(`axis_zwmn_nmot`, 16 u16, 520 … 6520 rpm) and reads five 16 × 12 s8 maps with
that key and the shared `rl` key 0x7FD84C:

| Address | Branch | Values | Name given |
|---|---|---|---|
| 0x5D5BCB | the default | +21 … −15 degCA | `cand_KFZWMN` |
| 0x5D5C8B | `0x80156F` bit 0 and `0x5C7972` bit 0 set | −15 … +19.5 degCA | `cand_KFZWMNUM` |
| 0x5D5D4B | `0x5C7972` bit 3 set; the other branch takes `zwstt` (0x802096) instead | −24 … +4.5 degCA | `cand_KFZWMNST` |
| 0x5D5E0B | read unconditionally, selected by `0x7FD321` bit 0 | −15 … +27 degCA | `cand_KFZWMS` |
| 0x5C7973 | `0x7FD306` / `0x802AB2` bit 6 — the same bit that selects 0x7FD32C in `zwmin_select` | all −24.75 degCA | `cand_KFZWMNLB` |

plus the 4 × 6 additive map 0x5D5EF4 (`cand_KFDZWMNST`, all zero) over
`nmot` × `tmst` and the module code word 0x5C7972.

The FR declares the ZWMIN family over `(SNM18ZWMUW, SRL12ZUUW)` — 18 speed
points, where this dataset has 16 — so every label stays HYPOTHESIS. The
positional argument for `cand_KFZWMNST` is the strongest of the five: the FR
text for ZWMIN reads "KFZWMNST bzw. zwstt", and this branch is literally the
choice between a map and `zwstt`.

**Why this matters for flex fuel:** `zwmin` is the late limit that the torque
structure's ignition intervention may not cross. An ethanol advance added at
`zwgru` (`ignition.md` §11) is never limited by it — it limits retard, not
advance — but a calibration that moves `KFZWMN` changes the cat-heating and
torque-reserve behaviour, so leave it alone.

## 5. `%NMAXMD` — the engine speed limiter (VERIFIED-STATIC, new)

`FUN_000FC74C` (0x0FC74C-0x0FCB6F) is the speed limiter. It was not in any
findings file. Structure, read out of the decompilation:

```
nmax_raw = B_nmxred ? NMAXOGGA[gangist] : NMAXGA[gangist]     (0x8034A2)
nmax     = min(nmax_raw,
               E_vfz      ? (B_autget ? NMAXDVG[gangist] : NMAXDV)  : inf,
               NMAXTO(oil temperature 0x8021B8),
               B_nmaxext  ? nmaxext_w (0x8022D4)                    : inf,
               ... six more limp-home constants ...)              -> 0x8034A4
B_nmaxd (0x7FEC8E) = any armed source whose CWNMXMD bit is set
```

| Object | Value | Physical | Name |
|---|---|---|---|
| **0x5D7F46[gangist]** | 26000 (all) | **6500 rpm** | `NMAXGA` — the stationary limit |
| **0x5D7F5E[gangist]** | 26000 … 26800 | **6500 … 6700 rpm** | `NMAXOGGA` — the short-term raise |
| 0x5D7F34[gangist] | 26000 | 6500 rpm | `NMAXDVG` — automatic gearbox, speed-signal fault |
| 0x5D7F32 | 26000 | 6500 rpm | `NMAXDV` — manual, speed-signal fault |
| 0x5D7F70 | `{n=4}` 26800 × 4 | 6700 rpm | `NMAXTO` — over oil temperature |
| 0x5D7F82 | `{n=4}` 26000 × 4 | 6500 rpm | gearbox limp-home limit |
| 0x5D7F5A / 0x5D7F44 / 0x5D7F58 / 0x5D7F5C / 0x5D7F6E / 0x5D7F56 | 24000 / 26000 / 17000 / 16000 / 13000 / 5888 | 6000 / 6500 / 4250 / 4000 / 3250 / **1472** rpm | the limp-home ladder |
| 0x5D7F2C | 157 | **69.75 degC** | `TMOTNMX` — arms the raise |
| 0x5D7F2D | 4 | — | `VNMX` — vehicle-speed threshold |
| 0x5D7F30 / 0x5D7F94 | 25 / 40 | — | `ITNMXH` / `TNMXH` — the two timers |
| 0x5D7F2E | 0x00FF | — | `CWNMXMD`, bits 0..8, one per limit source |

**So the rev limiter of this car is 6500 rpm, with 6700 rpm allowed briefly
once the limiter has already engaged and the coolant is above 69.75 degC.**
Only the objects the detector found as draft rows are in the sidecar; the
per-gear arrays are indexed at run time, so `enumerate_maps.py` did not see
them and they have no XDF entry yet (§7).

The FR labels come from the ABK table on FR **p487** (`NMAXMD` starts at
p484), matched by role and by unit; they are HYPOTHESIS. The *facts* — the
values, the units and which condition selects which cell — are VERIFIED-STATIC.

## 6. Six rail-pressure maps that were 1 × 1 in the XDF

`enumerate_maps.py` left `x_n` and `y_n` empty for `KFPRSOLHOM`, `KFPRSOLSCH`,
`KFPRSOLKH`, `KFPRSOLHMM`, `KFPRSOLHKS` and `KFPRSOLOFF`, because their row
length is read out of the count word at 0x5D5578 rather than passed as an
immediate. `draft_to_xdf.py` therefore emitted them as 1 × 1 tables — the six
most important rail maps in the file, unusable in TunerPro. `rail.md` §3.1 has
the shape (8 rows `nmot` × 8 columns load) and both axis addresses, so the
sidecar restores them. Same treatment, same evidence trail, for the axes of the
maps in §3.

## 6.1 The FFCAL001 block

`re/ffcal001_draft_rows.csv` holds the twelve objects of
`docs/05_flexfuel_design.md` §4 laid out from 0x5E2510 (after the eight-byte
`FFCAL001` marker) in the draft's column format, so that
`tools/draft_to_xdf.py --extra-rows re/ffcal001_draft_rows.csv` puts them in
the definition. **Everything about it is HYPOTHESIS**: the block does not
exist in this image (0x5E2510 upwards is erased, `calibration_maps.md` §6), and
brief **D1** owns the real descriptor and delivers
`patches/ff_fuel/ffcal001_rows.csv`. Replace the placeholder with D1's file at
merge time. The scaling in it is the one `docs/05` §4 specifies — `ff_F_curve`
and `ff_fst_map` 1024 = 1.0, `ff_fzw_curve` 256 = 1.0, `ff_dzw_map` in the
0.75 degCA of the ignition chain, `ff_prail_add` in 0.1 MPa — which is also the
scaling the rest of this pass proved for the stock maps those hooks act on.

## 7. What is still unnamed (leads, not conclusions)

Time-boxed and left for a follow-up brief; each line is a real lead with the
address of its consumer.

| Objects | Consumer | Why it looks important |
|---|---|---|
| 0x5C91F2, 0x5C9372, 0x5C94F2 — three 16 × 12 u16 maps over (load 0…65535, `nmot` 400…7000 rpm) | `FUN_00434C14` | it blends two of them with `0x7FD448` and rate-limits the result into 0x803522 — the shape of the FR's torque ↔ load pair (`MDBAS` `KFMIRL` / `KFMIOP`) |
| 0x5CA252 — 16 × 11 u16 on the `KFZWOP` grid, 4313…58841 | `FUN_000C8204`, `FUN_000E0A0C`, `FUN_00436838`, `FUN_0044C044` | four consumers in the torque structure; a second candidate for the torque ↔ load pair |
| 0x5C9938 — 16 × 12 u16, f(`nmot_w`, 0x8034FA) | `FUN_000E069C` | the result 0x8015D0 is computed twice, once with a 200/`0x8015AF` scaled input — an inverse-map shape |
| 0x5C8FAE — 8 × 8 u16 over `rl_w` × `nmot_w`, 29…983 | `FUN_004336E0` | the function that produces 0x803508, the charge request the rail setpoint maps are indexed by |
| nine 14 × 14 u8 maps 0x5C430D…0x5C4A1D, values around 128 = 1.0 | `FUN_00424CD0` | one is selected by a three-bit state built from three temperature thresholds, then multiplied by a base map over (`nmot`, `rl`) into 0x802856 |
| 0x5D863C — 16 × 12 u16, 0…65277 | `FUN_0045FAA8` | — |
| the lambda path (`LAMSOLL` `lamsbg_w`, `LAMBTS` `KFLBTS`, WOT enrichment) | not located | **not found in this brief.** The fuel path of `gk_rk` (`injection.md` §9, `start.md` §4.2) has no lambda-target map in it; the request comes from the torque/efficiency cascade, so `LAMSOLL` has to be entered from the measuring variables or from the exhaust-temperature model, not from `rk` |
| the charge limiters 0x80234C / 0x803358 / 0x803360 feeding the min-chain at 0x0C7CF8 | `FUN_000FBE74` and friends | `rail.md` §10 and §12.3 name the chain but not the maps behind each limit |

## 8. Reproducing

```bash
# the Ghidra project copy (about a minute)
mkdir -p /tmp/ghidra_D3
cp -R ghidra_projects/med9.gpr ghidra_projects/med9.rep /tmp/ghidra_D3/
export GHIDRA_INSTALL_DIR=/usr/local/Cellar/ghidra/12.1.3/libexec
./.venv/bin/python -m pyghidra.ghidra_launch --install-dir "$GHIDRA_INSTALL_DIR" \
    ghidra.app.util.headless.AnalyzeHeadless /tmp/ghidra_D3 med9 \
    -process passat_azx_ori.bin -noanalysis \
    -scriptPath ghidra_scripts -postScript import_symbols.py "$PWD"

# the five functions this brief decompiled
./.venv/bin/python ghidra_scripts/decompile.py --project-dir /tmp/ghidra_D3 \
    --project-name med9 0x0BDB58 0x00458E74 0x000FC74C 0x00431140 0x00436498

# the axis-key dataflow
./.venv/bin/python tools/sda_xref.py data/passat_azx_ori.bin --var 0x7FD820
./.venv/bin/python tools/sda_xref.py data/passat_azx_ori.bin --var 0x7FD84C

# the definition file (never committed from a brief branch; the integrator builds it)
./.venv/bin/python tools/draft_to_xdf.py re/calibration_draft.csv \
    -o work/med9_draft.xdf --min-confidence hypothesis
./.venv/bin/python -m unittest tests.test_draft_to_xdf
```

The FR page index of `re/findings/fr_index.md` was used with
`pdftotext -layout -f N -l N documents/MED9.1_TFSI_Funktionsrahmen.pdf -`;
FR page = PDF page. Pages read for this brief: **484-487** (`NMAXMD`),
**3085-3094** (`ZWGRU`), **3095-3109** (`ZWMIN`), **736** (`KFZWOP`).
