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
| 0x5C91F2, 0x5C9372, 0x5C94F2 — three 16 × 12 u16 maps over (load 0…65535, `nmot` 400…7000 rpm) | `FUN_00434C14` | **SETTLED (2026-09-17, E3, §9.1):** `%MDFUE`, the charge setpoint from the torque setpoint — `cand_KFMIRLUM` / `cand_KFMIRL` / `cand_KFMIRLS`, 100/32768 %/LSB over `nmot` × `misol_w` |
| 0x5CA252 — 16 × 11 u16 on the `KFZWOP` grid, 4313…58841 | `FUN_000C8204`, `FUN_000E0A0C`, `FUN_00436838`, `FUN_0044C044` | **SETTLED (2026-09-17, E3, §9.1):** `cand_KFMIOP`, the inverse of `KFMIRL` — torque from `rl_w` × `nmot_w`, 100/65536 %/LSB |
| 0x5C9938 — 16 × 12 u16, f(`nmot_w`, 0x8034FA) | `FUN_000E069C` | **PARTLY SETTLED (2026-09-17, E3, §9.6):** `cand_KFMIRLINV`; both axes proven, the value still has no unit |
| 0x5C8FAE — 8 × 8 u16 over `rl_w` × `nmot_w`, 29…983 | `FUN_004336E0` | **SETTLED and CORRECTED (2026-09-17, E3, §9.6):** it is `cand_KFRLSOLDY`, the step size of the charge-setpoint approach, over (Δcharge, `nmot_w`); it does **not** produce 0x803508 |
| nine 14 × 14 u8 maps 0x5C430D…0x5C4A1D, values around 128 = 1.0 | `FUN_00424CD0` | **SETTLED and CORRECTED (2026-09-17, E3, §9.4):** `%GGHFM`'s air-mass correction — `KFKHFM` plus the eight `KFPU*` pulsation maps; the three states are adjusters, not temperatures |
| 0x5D863C — 16 × 12 u16, 0…65277 | `FUN_0045FAA8` | **SETTLED (2026-09-17, E3, §9.1):** `cand_KFMIOPRL`, torque from the alternative charge request 0x8034B8 |
| the lambda path (`LAMSOLL` `lamsbg_w`, `LAMBTS` `KFLBTS`, WOT enrichment) | not located | **SETTLED as an exclusion (2026-09-22, F4, §10.1-§10.2):** 0x7FD066 is tester adaptation channel 10, not a lambda request, so `fgru_trim` has no map upstream of it; `cand_KFMIXA`/`cand_KFMIXB` — the `%LAMSOLL`-shaped pair — are **all 128**, i.e. λ = 1 everywhere; and every other factor `gk_rk` applies is named. **There is no full-load or component-protection enrichment on the fuel path of this dataset.** `%LAMBTS` through `%ATM` was time-boxed; the candidate module is `FUN_00108950` |
| the charge limiters 0x80234C / 0x802358 / 0x802360 / 0x80235E feeding the min-chain at 0x0C7CF8 | `FUN_000FBE74`, `FUN_000FC250` | **SETTLED (2026-09-17, E3, §9.3):** all four sources named and read out of the image; only `cand_KLRLMXNRED` (0x5D7EAE) is calibrated to anything but "off". (The 0x803358 / 0x803360 of the original lead were typos for 0x802358 / 0x802360.) |

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

---

## 9. Pass 2 (brief E3, 2026-09-17, issue #41)

Method unchanged from §7's recipe and D3's: take a consumer that reads several
objects, decompile it once, read the axes out of the image and fix the fixed
point from the ECU's own arithmetic — with one addition that turned out to be
the most productive tool of this pass:

> **The measuring handlers are a scaling oracle.** Every charge- and
> torque-domain variable of the torque structure is a VCDS measuring variable,
> and the handler's own code says what full scale is. Measuring id 375
> (0x803510, handler 0x03C348) emits formula 0x21 with `A = 0x80` and
> `B = v >> 8`, so **32768 counts = 100 %** in that domain; id 8 (0x8035E6,
> handler 0x0391A8) emits `B = (v * 255) >> 16` with `A = 0xFF`, so
> **65536 counts = 100 %** in that one. Both were then confirmed by the
> breakpoint grids falling on round percentages, never the other way round.

### 9.0 Counts

| | before (D3) | after (E3) |
|---|---|---|
| objects with a name | 157 | **259** |
| … tagged `static` (the *label*) | 24 | **57** |
| … tagged `hypothesis` | 133 | 202 |
| objects with a unit | 146 | **244** |
| scaling tagged `static` | 118 | **197** |
| tables/curves/axes still `cand_*` | 991 of 1,066 | 958 of 1,066 |

The 102 new rows are 69 scalars, 16 `map_2d`, 4 `map_2d_shared`, 3
`map_2d_data`, 4 `curve_1d`, 4 `curve_1d_shared` and 2 axes; 98 of them carry
a unit and 79 a `static` scaling.

### 9.1 The torque ↔ charge pair (§7 lead 1, closed)

`FUN_00434C14` is **`%MDFUE`**, "Sollwertvorgabe für Luftmasse aus Sollmoment"
(FR p724). It is the charge setpoint from the torque setpoint:

```
key_y = axis_search_u16(0x5C968C, misol_w 0x8035E6)     ; 0x434C2C -> 0x7FD8C8
key_x = axis_search_u16(0x5C9672, nmot_w)               ; 0x434C3C -> 0x7FD8C4
if (0x80223B == 7)  r = interp_2d_u16(cand_KFMIRLS 0x5C94F2, 12, key_y, key_x)
else                r = (1-t) * cand_KFMIRL   0x5C9372
                       + t    * cand_KFMIRLUM 0x5C91F2,  t = 0x7FD448 / 128
0x803522 = clamp(r, -inf, prev + cand_DRLSOLMX 0x5C91F0 = 1.00 %/step)
```

and `mdkol_charge_request` 0x4336E0 continues
`0x803510 = max(0x8034B8, 0x803522)` → `rlsol_w 0x803508 = min(2 x that,
cand_RLSOLMX 0x5C8C0C << 8 = 99.2 %)`, which is **the x input of every
`KFPRSOL*` rail-pressure map** (rail.md §3.1).

Units, all VERIFIED-STATIC: the KFMIRL maps are **100/32768 %/LSB** (0 …
111.1 %) over `nmot` 400…7000 rpm (axis 0x5C9674, 0.25 rpm/LSB) and
`misol_w` 0…100 % (axis 0x5C968E, 100/65536 %/LSB, breakpoints 0, 0.5, 4, 8,
9.8, 12, 16, 20, 25, 32, 40, 50, 60, 72.5, 92, 100 %).

The inverse direction is **`cand_KFMIOP` 0x5CA252** (FR p724 §APP: "Das
Kennfeld KFMIRL ist invers zum Kennfeld KFMIOP in der Sektion %MDBAS"): 11
`rl_w` columns (10.4…104.2 %) × 16 `nmot_w` rows (560…6520 rpm) → torque in
100/65536 %/LSB, 6.6…89.8 %. Numerically the two agree to about 1 % of charge
(KFMIOP(2000 rpm, 104.2 %) = 86.5 %; KFMIRL(2000 rpm, 86.5 %) = 102.8 %); the
residue is the efficiency chain the FR puts between them. A second, coarser
charge→torque map, **`cand_KFMIOPRL` 0x5D863C** with its 1-D twin
`cand_KLMIOPRL` 0x5D87DE, lives in `mi_from_rl_alt` 0x45FAA8 and converts the
*alternative* charge request 0x8034B8 (100/32768 %/LSB, axis 0…120 %).

The RAM names are HYPOTHESIS as labels and VERIFIED-STATIC as facts:
`misol_w` 0x8035E6 (one writer, 0x4359D0), `rlsol_mdfue` 0x803522,
`rlsol_req` 0x803510, `rlsol_w` 0x803508, `cand_rlsol_alt` 0x8034B8,
`cand_rlmin_w` 0x803598.

### 9.2 What 0x7FD448 actually is

The blend weight is **a counter, not a position**: `mdfue_blend_counter`
0x465BD4 reloads it with `cand_ZRLMIRLUM` 0x5D8FB0 = 100 while 0x7FE95B is
set and decrements it by one per activation afterwards, so `t` runs from
100/128 = 0.78 down to 0 and the steady-state map is `cand_KFMIRL`. Which
event 0x7FE95B marks is **open**.

> **SETTLED 2026-09-22 (F4, #41, §10.6).** 0x7FE95B is bit 0 of the first data
> byte of the CAN frame received in RX slot 4, **id 0x440** (`can.md` §4,
> shadow at 0x803F18); it is set at 0x0A93A8 and cleared with six sibling
> flags by `FUN_00428FCC` when that message times out. 0x440 is the gearbox
> group in the public VAG matrices, so `cand_KFMIRLUM` is the charge map used
> while the gearbox asks for it.

### 9.3 The charge-limit chain — almost all of it is switched off

rail.md §10 and §12.3 named the min-chain `rl_limit_min_awea` 0x0C7CF8 but not
the maps behind its five inputs. Two functions hold them:
`rl_limit_charge_protect` 0x0FBE74 (0x80234C, 0x80234E) and the previously
unidentified **`rl_limit_rail_and_speed` 0x0FC250** (0x802358, 0x802360,
0x80235E). Reading their calibration out of the image:

| Input of the min-chain | Source | State in this dataset |
|---|---|---|
| 0x803070 | the injection-window limit (rail.md §10) | inactive unless the rail-pressure fault bit 0x80201E.5 is set |
| 0x80234C | `cand_KLRLMXMI` 0x5D7E3A over `misol_w` | **all 0xFFFF**, and `cand_CWRLMXBTS` 0x5D7E5E = 0 disables every alternative |
| 0x802358 | `cand_KLRLMXNRED` 0x5D7EAE over `nmot8` | **the only calibrated limiter**: 100 % to 3520 rpm, then 71, 60, 55, 52, 50 % at 4000…6520 rpm — armed only by the debounced flag 0x7FEA84 |
| 0x802360 | `cand_KLFRLMXT` 0x5D7EBF × `cand_KFFRLMXN` 0x5D7E76 | 255 and 128 everywhere, and the code forces 0xFFFF as soon as the factor reaches 1.0 |
| 0x80235E | rail pressure: `(prist_w − cand_PRRLMX) × cand_KVRLMXPR1/2 (84/512)` | **enabled only below `cand_TMRLMXPR` = −20.25 °C** |

**For flex fuel this is good news and one warning.** Good news: nothing in the
stock charge-limit chain will cut charge because the fuel system is working
harder. Warning: `cand_KLRLMXNRED` is real and severe (down to 50 % above
6000 rpm), so if a flex-fuel calibration ever provokes the fault that arms
0x7FEA84 the engine loses half its charge at the top end. Log 0x802358
(and the arbitrated 0x80235A, **VCDS measuring id 2051**) on any E85 run.

### 9.4 The nine 14 × 14 maps are the air-mass correction, not temperatures

§7's last-but-one lead guessed "a three-temperature-threshold state". It is
`%GGHFM`. `FUN_00424CD0` (now `gghfm_correction`) writes 0x802856, which is
read **exactly once**, at 0x418CD0, as
`cand_mw_ml = (raw HFM 0x7FEF9E × 0x802856) >> 15`. The three states are the
three *adjusters* (`Verstellelemente`) that the FR's `CWHFMPUKL1..3` select,
with the switch points `LSPPUKL1..3` / `RSPPUKL1..3`, and the ECU builds the
map index as `1 + 2·e1 + 2·e2 + 4·e3` — the same numbering as the FR's eight
labels, so the assignment is forced:

| index | map | FR label | index | map | FR label |
|---|---|---|---|---|---|
| 1 | 0x5C43EF | `KFPU` | 5 | 0x5C4A1D | `KFPUKL3` |
| 2 | 0x5C44D1 | `KFPUKL1` | 6 | 0x5C4777 | `KFPUKL13` |
| 3 | 0x5C4859 | `KFPUKL2` | 7 | 0x5C493B | `KFPUKL23` |
| 4 | 0x5C45B3 | `KFPUKL12` | 8 | 0x5C4695 | `KFPUKL123` |

and the base map 0x5C430D over (`nmot8`, u8 `rl`) is **`KFKHFM`**, all 128 =
1.0 in this dataset. The pulsation maps run 124…134, i.e. −3 %…+5 %.
`gghfm_air_mass` 0x418B68 adds `cand_NPULSHFMMN`, `cand_MLDKFHFM` and
`cand_MLMIN`.

### 9.5 The lambda path — NOT found, and what was excluded

§7 listed it as "not located"; it still is. What this pass rules out:

* **It is not in the fuel path.** `gk_rk` 0x41AA48 multiplies by `fgru_trim`
  0x801CF2 (Q7), and `fgru_trim`'s only producer is the four-line
  `FUN_000E8D9C`: `fgru = mul_shr15_sat(cand_KFGRUTRIM 0x5D350C = 128,
  0x7FD066 × 64 + 0x6000)`, clipped at 255. There is no map. The variable
  0x7FD066 has **no writer that `tools/sda_xref.py --var` or
  `tools/find_abs_refs.py --target` can see**, so whatever sets the lambda
  request writes it through a pointer; that is the thread to pull next.
* **It is not reachable from the knock retard either.** ignition.md §13.3 read
  the 0x4594E8 reference to `wkrm` as "the exhaust-gas temperature model:
  `if (0x5D396C & 8) tabgm += 0x7FCE76`". It is not: 0x4594E8 sits in
  `FUN_0045943C`, a *comparison* — `cand_WKRMKR` 0x5D6123 < `wkrm` — inside
  the knock-control load window that produces 0x7FEA80, whose only reader is
  0x103044. **ignition.md §13.3 needs that dated correction; brief E1 owns
  that file while E3 runs, so it is not made here.**

So `LAMSOLL` / `lamsbg_w`, `KFLBTS` and the WOT enrichment remain open. The
cheapest next entries are (a) the writer of 0x7FD066, (b) `%LAMBTS` through
the exhaust-temperature model proper (`ATM`, FR p2259), and (c) the lambda
controller outputs `fr_w` 0x802DF8 / 0x802E00 traced backwards.

> **ANSWERED 2026-09-22 (F4, #41, §10.1-§10.2).** (a) 0x7FD066 is **tester
> adaptation channel 10**, written by the KWP adaptation service
> `FUN_00038708` through the pointer table at 0x0A3ADC and restored from
> EEP_CONF block 8 by `FUN_0012E3F8`; there is no map upstream of
> `fgru_trim`. (c) `fr_w` 0x802DF8 / 0x802E00 are written by the PI controller
> `FUN_00440A3C`, whose setpoint 0x802CDE is computed by `FUN_0043E164` **from
> the commanded `rk` itself**, so the loop tracks the request rather than
> carrying one. (b) was time-boxed. The conclusion is that this dataset has no
> lambda-setpoint map at all: `cand_KFMIXA` and `cand_KFMIXB` are the
> `%LAMSOLL`-shaped pair and every cell of both is 128.

### 9.6 The other §7 leads

* **0x5C9938 (`cand_KFMIRLINV`)** — `FUN_000E069C` evaluates the same map
  twice, once at the real charge 0x8034FA and once at
  `0x8034FA × 200 / u8@0x8015AF`, the shape of a normalisation to standard
  conditions. Both **axes are proven** (`nmot_w` 560…6520 rpm; charge 0, 10,
  15, 20, 25, 30, 40, 50, 60, 70, 80, 100 % at 100/65536 %/LSB) and the
  values (0…5103, almost independent of speed) have **no unit yet**. Its
  output 0x8015D0 keys `cand_KFPSSRM` 0x5D1CBA in `FUN_000E06F8`, the
  intake-manifold/residual-gas chain; that block is the natural follow-up.
* **0x5D863C (`cand_KFMIOPRL`)** — closed, see §9.1.
* **0x5C8FAE** — closed, but the §7 lead was wrong about it: it does **not**
  produce 0x803508. It is the *step size* of the charge-setpoint approach,
  `0x8034F4 += (cand_KFRLSOLDY(Δcharge, nmot) × 0x7FD43E) >> 6`, clipped to
  0x803504; 0x803508 is written at 0x433884 from 0x803510. Its twin
  `cand_KFRLSOLDYS` 0x5C904A is 4 × 8, not 8 × 8, and the weight 0x7FD43E is
  a constant 0x40 here because the mode array 0x5C8DFD is all zero.
* **0x5CA252** — closed, it is `cand_KFMIOP` (§9.1).

### 9.7 Verification and reproduction

```bash
mkdir -p /tmp/ghidra_E3 && cp -R ghidra_projects/med9.gpr ghidra_projects/med9.rep /tmp/ghidra_E3/
export GHIDRA_INSTALL_DIR=/usr/local/Cellar/ghidra/12.1.3/libexec
./.venv/bin/python -m pyghidra.ghidra_launch --install-dir "$GHIDRA_INSTALL_DIR" \
    ghidra.app.util.headless.AnalyzeHeadless /tmp/ghidra_E3 med9 \
    -process passat_azx_ori.bin -noanalysis \
    -scriptPath ghidra_scripts -postScript import_symbols.py "$PWD"

# the functions this brief decompiled
./.venv/bin/python ghidra_scripts/decompile.py --project-dir /tmp/ghidra_E3 \
    --project-name med9 0x00434C14 0x004336E0 0x00465BD4 0x000E069C 0x000E06F8 \
    0x00424CD0 0x00418B68 0x0045FAA8 0x000FBE74 0x000FC250 0x000C7CF8 \
    0x0045943C 0x000E8DD4 0x0041AA48

# the measuring handlers that fixed the charge and torque scaling
./.venv/bin/python ghidra_scripts/decompile.py --project-dir /tmp/ghidra_E3 \
    --project-name med9 --asm 0x0003C348 --count 6 --asm 0x000391A8 --count 7

# the definition (the integrator builds the committed one)
./.venv/bin/python tools/draft_to_xdf.py re/calibration_draft.csv -o work/x.xdf \
    --min-confidence hypothesis --extra-rows patches/ff_fuel/ffcal001_rows.csv
./.venv/bin/python tools/draft_to_xdf.py --validate work/x.xdf
./.venv/bin/python -m unittest tests.test_draft_to_xdf
```

FR pages read for this brief: **724-728** (`MDFUE`), **729-745** (`MDBAS` /
`MDIST`), **813-823** (`GGHFM`), **1037-1047** (`BGRLMIN`, `BGRLMXS`) and the
table of contents **2-29**.

---

## 10. Pass 3 (brief F4, 2026-09-22, issues #41 and #43)

Brief `docs/agent_briefs/F4_calibration_naming_pass3_lambda.md`. Dump
unchanged (`tools/checksum.py verify -q` -> `ALL OK (65 blocks)` before and
after). The Ghidra work was done in a private copy at `/tmp/ghidra_F4`; the
read-outs use the new `tools/cal_show.py`.

### 10.0 Counts

| | before (E3) | after (F4) |
|---|---|---|
| objects with a name | 259 | **359** |
| … tagged `static` (the *label*) | 57 | **148** |
| … tagged `hypothesis` | 202 | 211 |
| objects with a unit | 244 | **344** |
| scaling tagged `static` | 197 | **268** |
| tables/curves/axes still `cand_*` | 958 of 1,066 | **876 of 1,066** |

The 100 new rows are 57 axes, 18 scalars, 12 `map_2d_data`, 5 `map_2d`,
4 `map_2d_shared`, 2 `curve_1d` and 2 `curve_1d_shared`.

### 10.1 The writer of 0x7FD066 — it is a tester adaptation channel (VERIFIED-STATIC)

E3 left "the writer of 0x7FD066" as the cheapest next entry into the lambda
path (§9.5), and `start.md` §4.1 had recorded that the cell has "**no code
writer at all** — it is only reachable through the tester pointer table at
0x0A3ADC". Both are right about the mechanism and wrong about the conclusion:
the cell **is** written, through that table, by the **KWP adaptation service**,
and it has nothing to do with the lambda request.

The structure at 0x0A3AD8 is a four-table adaptation-channel descriptor for
**17 channels**, indexed 1..0x11:

| table | base | what the entry is |
|---|---|---|
| RAM pointer | 0x0A3AD8 + 4n | the byte the channel lives in |
| upper limit | 0x0A3B2C + 4n | pointer to a calibration byte |
| lower limit | 0x0A3B70 + 4n | pointer to a calibration byte |
| default | 0x0A3BB8 + 4(n−1) | pointer into the byte array at 0x0A3B20 |

with **0x008001D0 as the "channel not implemented" sink**. Twelve channels are
implemented; `FUN_0012E3F8` (0x0012E3F8) sets the implemented-mask 0x8001D4 to
`(… & 0xFFFC77BF) | 0x77BE` — bits 1,2,3,4,5,7,8,9,10,12,13,14 — and restores
each of them from **EEP_CONF block 8** with
`nvm_block_request(8, n + 1, 1, 1, PTR[n], 0)`.

| ch | RAM | max | min | default | signed | read at | what it multiplies |
|---|---|---|---|---|---|---|---|
| 1 | 0x7FD06B | 0x5C608E = 0 | 0x5C608F = 0 | 0 | yes | 0x46B0BC | — |
| 2 | 0x7FD064 | 141 | 64 | 128 | no | 0x0E8F20 | 0x801D16 |
| 3 | 0x7FD068 | 192 | 64 | 128 | no | 0x0E8EE4 | 0x801D18 |
| 4 | 0x7FD065 | 141 | 64 | 128 | no | **0x41A0BC** | **the running mixture** |
| 5 | 0x7FD069 | 141 | 64 | 128 | no | **0x430394** | **the 0x7FD264 mixture factor** |
| 7 | 0x7FD06D | 0 | 205 | 0 | yes | 0x0FBCB8 | — |
| 8 | 0x7FD067 | 141 | 64 | 128 | no | **0x41A5EC, 0x41A780** | **`ksta`, the start quantity** |
| 9 | 0x7FD063 | 13 | 243 | 0 | yes | (through the pointer only) | — |
| 10 | **0x7FD066** | **179** | **26** | **128** | no | **0x0E8DA8** | **`fgru_trim`** |
| 12 | 0x7FD06C | 0 | 0 | 255 | yes | (through the pointer only) | — |
| 13 | 0x7FD062 | 255 | 0 | 0x5C885C | yes | 0x11816C, 0x46B0C4 | — |
| 14 | 0x7FD06A | 255 | 0 | 0 | no | 0x466908 | — |

`FUN_00038708` (0x00038708) is the service. Sub-function 0x81 reads a channel,
0x82 writes one — clamping the tester's byte between the two calibration
limits and storing it with `**(byte **)(&DAT_000a3ad8 + n*4) = 0x8001E6` —
and 0x83 commits it to the EEPROM. Channels whose bit is set in the mask
**0x382C2** (1, 6, 7, 9, 11, 12, 13) are displayed signed, i.e. offset by
−0x80. Sub-function 0x82 with channel 0 restores every channel to its default.

**Three of the twelve are fuel trims, and all three matter for flex fuel:**

* **channel 10 → `fgru_trim`.** `FUN_000E8D9C`:
  `fgru_trim = min(mul_shr15_sat(cand_KFGRUTRIM = 128, ch10 × 64 + 0x6000), 255)`,
  so the factor is `0.75 + n/512` in Q7 and the limits 26…179 allow
  **0.797 … 1.094**, i.e. −20.3 % … +9.4 % on `rk`, with 128 = exactly 1.0.
* **channel 8 → `ksta`.** `esstt_ksta` 0x41A268 multiplies the start quantity
  by `ch8/128` (the `mul_shr15_sat(v, ch8 << 8)` at 0x41A5EC), limits 64…141 =
  **0.50 … 1.10**.
* **channel 4 → the running mixture.** `mixture_running_build` 0x419DA4:
  `(ch4 × 0x7FD267 × v) >> 14`, limits 64…141, same 128 = 1.0.

Two consequences. First, **`fgru_trim` is not a lambda setpoint and there is no
map upstream of it** — the exclusion E3 asked for is complete, and §9.5's
"whatever sets the lambda request writes it through a pointer" is answered: no
lambda request writes it, a tester does. Second, these three channels are a
**tester-writable, EEPROM-persistent ±10 % fuel trim that needs no patch at
all**, which is worth knowing for bench work even though ±10 % is far short of
what E85 needs. They are also a *risk*: a workshop "basic setting" that resets
them changes the fuelling of a flex-fuel calibration.

> **Correction to `re/findings/start.md` §4.1 (2026-09-22, F4):** the line
> "`0x7FD066` has **no code writer at all** … so it is 1.0 in normal
> operation" is right about the default and wrong about the reason. It is
> adaptation channel 10, written by `FUN_00038708` through the table and
> restored from EEP_CONF block 8 by `FUN_0012E3F8` at every power-up. It is
> 1.0 only while the channel sits at its default 128.

> **Correction to `re/findings/eeprom.md` §9 (2026-09-22, F4):** "Exactly one
> [call site] names block 8" is an artefact of `eeprom_map.py --clients`
> resolving immediates only. `FUN_0012E3F8` and `FUN_00038708` call
> `nvm_block_request` with the block number **loaded from 0x0A3AD8**, so
> block 8 has three more clients, including a *commit* path in sub-function
> 0x83. D2's conclusion that a patch must commit block 8 itself still holds —
> the tester path only runs when a tester asks.

### 10.2 The lambda path, closed as far as this dataset allows

With §10.1 the fuel path is fully accounted for, and the answer to "where is
`LAMSOLL` / `KFLBTS` / the WOT enrichment" is **that this dataset does not have
them as maps**. The evidence, all VERIFIED-STATIC:

1. `gk_rk` 0x41AA48 multiplies exactly four things into the base quantity:
   `fgru_trim` (§10.1, a tester constant), `ksta_adapted` or `mixture_running`,
   the charge `rl_for_fuel`, and the lambda **controller** outputs
   `fr`/`fra`/`frm`. There is no fifth factor and no additive enrichment.
2. `mixture_running` 0x803020 is Q12 with 4096 = 1.0 and is built as
   `mul_q15(0x803026, (0x7FD264 × (0x80301C + 0x1000)) >> 7)`.
   **0x803026 = `cand_KFMIXA` × `cand_KFMIXB` × 2**, and this pass read both
   maps out of the image: **every cell of both is 128**, so 0x803026 is
   0x8000 = 1.0 at every operating point. The `%LAMSOLL`-shaped pair of this
   software is calibrated to λ = 1 everywhere.
3. The only non-neutral contribution to `mixture_running` is 0x80301C, and
   §10.3 names every map in it. It is a *warm-up* enrichment over `tmst`, zero
   once the engine is warm.
4. The lambda **controller** setpoint is not a map either. `FUN_0043E164`
   computes 0x802CDE from the *commanded* fuel mass (0x80303E / 0x80303A, both
   written by `gk_rk`) divided by the charge, and `FUN_00440A3C` (the PI
   controller, 0x440A3C-0x442037) subtracts the sensor value 0x802E0C from it.
   So the loop tracks whatever `rk` asks for; the request is implicit in `rk`.

> **CORRECTED 2026-09-23 (G4, §11.8):** point 1 above misses a divisor —
> `gk_rk` also computes `rk = (rk << 12) / 0x80304A`, and 0x80304A is very
> probably the lambda setpoint `lamsbg_w`. The sentence below is F4's, kept for
> history; it holds only until the inputs of 0x803046 are traced.

**So there is no full-load or component-protection enrichment on the fuel path
of this dataset.** `%LAMBTS` may exist as code — nothing here proves it does
not — but it cannot reach `rk`, because every term that can is named and none
of them is a function of an exhaust temperature. For flex fuel this is good
news: an ethanol factor at B6's `rk` hook is not fighting a hidden enrichment.
It is also a warning: **there is no stock enrichment to lean out**, so the
whole E85 fuel increase has to come from the patch, and the ±10 % of the
adaptation channels is the only stock lever.

What was *not* done, and is the honest remainder: lead (b), `%LAMBTS` through
the exhaust-temperature model `%ATM`, was time-boxed once the exclusion above
made it unable to change the fuel path. The candidate module is
`FUN_00108950` (0x108950-0x10A0DB), a soak/cool-down model over
(engine-off time 10…945 s, `tmst` −39.75…99.75 °C) with six 10 × 10 u16 maps;
it was identified and left unnamed.

Commands that produced the exclusion:

```bash
./.venv/bin/python3 tools/sda_xref.py data/passat_azx_ori.bin --var 0x7FD062 0x7FD06D
./.venv/bin/python3 tools/find_abs_refs.py data/passat_azx_ori.bin --range 0x7FD040 0x7FD080
./.venv/bin/python3 tools/cal_show.py data/passat_azx_ori.bin 0x5C6B64   # KFMIXA, all 128
./.venv/bin/python3 tools/cal_show.py data/passat_azx_ori.bin 0x5C6C12   # KFMIXB, all 128
./.venv/bin/python ghidra_scripts/decompile.py --project-dir /tmp/ghidra_F4 \
    --project-name med9 0x00038708 0x0012E3F8 0x000E8D9C 0x0041AA48 \
    0x00419DA4 0x0043E164 0x00440A3C 0x0010C874 0x004302DC 0x00430448
```

The 12 pointer words of the adaptation table were found with a halfword-aligned
scan for words whose value lies in 0x7FD060-0x7FD07F (`ram_map.csv` already
flagged the line with `ptr_words = 12`), which is the search
`find_abs_refs --target` cannot do because the address never appears as an
instruction immediate.

### 10.3 The running-mixture cascade (%GK), 13 maps

`start.md` §4.2 listed the cascade and called its calibration "partly
HYPOTHESIS". All thirteen objects are now named, each with its fixed point read
off the shift at the use site (per-object evidence in
`re/calibration_names.csv`):

| function | objects | produces |
|---|---|---|
| `FUN_004302DC` | `mix_7FD265_map` 0x5C6A56, `mix_7FD261_map` 0x5C6A86, `mix_7FD264_nmot_curve` 0x5C6AED | 0x7FD265, 0x7FD261, 0x7FD264 |
| `FUN_00430448` | `mix_7FD267_map` 0x5C6AF6, `mix_7FD268_map` 0x5C6B26 | 0x7FD267, 0x7FD268, and 0x803026 from `cand_KFMIXA` / `cand_KFMIXB` |
| `FUN_0010C874` | `mix_801CF6_map` 0x5C6AB6, `mix_801CF5_map` 0x5D3580, `mix_801D00_map` 0x5D3568, `mix_801D01_map` 0x5D361B, `mix_801CF4_curve` 0x5D3673 | 0x801CF4-0x801D01 |
| `FUN_000C5E54` | `mix_801D04_map` 0x5D365B, `mix_801D06_map` 0x5D36A8 | 0x801D04, 0x801D06 |
| `FUN_004544A8` | `mix_7FD263_map` 0x5D362D | 0x7FD263 |

**Eleven of the thirteen are neutral in this dataset.** The two that are not:

* **`mix_801CF5_map` 0x5D3580**, 12 × 12 u8 over (`tmst` −30…+90 °C, the
  0x7FD3F7 temperature). It is *added* to 0x7FD265 and runs 44/128 = +34 % at
  the cold corner down to 0 hot. **This is the warm-up enrichment of this
  software** — the thing `start.md` §4.1 correctly said is not a separate
  `fnsk` / `fwlk` factor. It is the map an E85 cold-start calibration has to
  move, alongside `KFKSTT` / `KFWKSTT` (which act during cranking only).
* **`mix_7FD264_nmot_curve` 0x5C6AED**: 1.60 at 600 rpm, 1.00 at 1000 rpm,
  0.797 from 1520 rpm up.

Argument-order note, used throughout this section and worth writing down:
`lookup_2d_*(struct, a, b)` takes **a = the y (row) value and b = the x
(column) value** — the convention `KFWKSTT` fixes (`start.md` §3, whose call is
`lookup_2d_u8(&DAT_005c6c60, tmst, anztist)` with `tmst` on the 12-point y
axis) — and `interp_2d_*(map, x_axis_struct, key_y, key_x)` likewise puts the
*second* key on x. Both were re-checked against four independent draft rows
before the rows above were written.

### 10.4 The three shared axis-key processes, and 57 axes

`cal_axis_key_process` 0x0BDB58 (§3) is one of **three** functions of that
shape. `FUN_000FB974` performs 15 breakpoint searches and `FUN_00115AE0` 18,
into the same 0x7FD79x-0x7FD8Ax key block. Between them every unnamed `axis`
row in 0x5C88xx and 0x5D79xx-0x5D7Axx is a breakpoint list whose input is named
in the call, so 40 axes were named in one pass, plus 17 more from the consumers
of §10.3 and §10.5. 24 of the 40 carry a unit this project had already proved;
the rest keep raw counts and say so.

Two fixed points fall out of the grids:

* **0x8022A2 is a Q15 signed fraction.** `axis_q15_8022A2_5D79CA` 0x5D79CA is
  −32768, −24576, −16384, −3277, 3277, 16384, 24576, 32767 = **−1.0, −0.75,
  −0.5, −0.1, +0.1, +0.5, +0.75, +1.0** at 1/32768 exactly.
* **SETTLED (2026-09-23, G4, §11.1): 0x7FD3E5 is the intake-air temperature
  at 0.75 °C − 48, not a voltage; the text below is F4's reasoning, kept for
  history.** **0x7FD3E5 is *probably* a battery voltage at 1/16 V per LSB, and this is
  NOT settled.** For it: the axis 0x5C7BA5 that `start.md` §5 recorded without
  a unit is 40, 80, 120, 160, 200, 240, i.e. **2.5, 5.0, 7.5, 10.0, 12.5,
  15.0 V** exactly, and `axis_ubatt_5D7947` 0x5D7947 reads 8.19…14.00 V.
  Against it: under the proved `tmot` unit (0.75 °C − 48) the same axes read
  −18…132 °C in exact 30 °C steps and 50.25…120 °C, both plausible temperature
  ladders, and 0x5D5FF1 over the same cell reads −24.75…80.25 °C, which looks
  more like a coolant grid than a voltage grid. Both sidecar rows are therefore
  `hypothesis` on the name **and** on the scale, and the axis of
  `zwdelta_7FD338_map` (§10.5) keeps raw counts. **Settling 0x7FD3E5 is a
  short job for the next brief**: decompile its writers 0x0F8FD4, 0x11A990 and
  0x11A998.

### 10.5 Two calibrated interventions nobody had located

* **`FUN_00459334` writes the s8 ignition term 0x7FD338**, and
  `zwbas_per_bank` 0x41D10C reads it at 0x41D120 — so it is an ignition angle
  at 0.75 °CA/LSB, on top of `zwgru`, and **no findings file mentioned it**.
  It is `zwdelta_7FD338_weight_map` 0x5D5F81 (a load/speed gate that is zero
  below 47 % charge) times `zwdelta_7FD338_map` 0x5D5FFB (−6.0…+2.25 °CA over
  speed and 0x7FD339) plus `zwdelta_7FD338_add_map` 0x5D6075 (−3.75…+7.5 °CA
  over the 0x7FD3F7 temperature and load, largest cold). Anything that adds
  advance — brief E1's ethanol blend included — shares the budget with it, and
  `ignition.md` should pick it up on the next pass through that file.
* **`FUN_000C7DD0` holds six charge thresholds over (engine speed, operating
  mode)**, all in the 100/32768 %/LSB charge domain, and
  `rl_rlsolreq_limit_curve` 0x5D9216 is the ceiling it compares `rlsol_req`
  against: **100.0 % up to 4000 rpm, 105.0 % above**, setting 0x7FD442 bit 0.

### 10.6 The three loose ends of §9

* **0x7FE95B — SETTLED (VERIFIED-STATIC for the mechanism, COMMUNITY for the
  message).** It is written at 0x0A93A8 as `bit 0 of the byte at 0x803F18`.
  `can.md` §4 says 0x803F18 is the data shadow of **RX slot 4, CAN id 0x440**,
  and `FUN_00428FCC` — the receive handler that owns slots 4 (0x440), 5
  (0x540) and 7 (0x442) — clears 0x7FE95B together with six sibling flags when
  that message times out. 0x440 / 0x442 / 0x540 are the **gearbox** group in
  the public VAG powertrain matrices, which makes `cand_KFMIRLUM` the charge
  map used *while the gearbox asks for it* and `cand_ZRLMIRLUM` = 100 the
  number of activations it is blended out over afterwards (§9.2). The CAN id
  and the slot are VERIFIED-STATIC; "gearbox" is COMMUNITY and one bench trace
  of 0x440 would confirm it.
* **CORRECTED 2026-09-23 (G4, §11.7): 0x80223B is the gear `gangi` (FR `%BBGANG`), not an
  operating mode; 7 = reverse.** **0x80223B — SETTLED (VERIFIED-STATIC).** It has its own axis,
  `axis_opmode_5C887B` 0x5C887B = 0, 1, 2, 3, 4, 5, 6, 7, searched by
  `cal_axis_key_process` into 0x7FD7A0, and exactly one writer, 0x45C064. Four
  of the six charge thresholds of §10.5 are maps over it. It is the
  **operating-mode index**, the same variable `%MDFUE` compares against 7 to
  select `cand_KFMIRLS` (§9.1). Its eight values are the combustion modes of a
  BDE engine; which value is which mode is **not** established, and VCDS
  measuring id 130 (groups 051.3 / 068.3, format 0x36) displays it directly,
  so one drive log would settle that too.
* **SETTLED (2026-09-23, G4, §11.2): relative charge at 100/4096 %/LSB; 0x8015AF is an
  ignition efficiency at 1/200.** **`cand_KFMIRLINV` 0x5C9938's value unit — still open.** What was tried:
  `FUN_000E069C` evaluates it at 0x8034FA and at `0x8034FA × 200 / 0x8015AF`
  and writes 0x8015D0, whose only readers are three sites inside
  `FUN_000E06F8` (0x0E0720, 0x0E07E0, 0x0E0838) where it is a *breakpoint
  argument*, not an arithmetic operand — so there is no shift to read the
  fixed point off, and the measuring-handler oracle does not help either
  (0x8015D0 is not a measuring variable). The remaining lead is **0x8015AF**,
  the divisor: it is written at 0x0E0D5C and 0x104224 and the literal 200 is
  its reference value, so naming 0x8015AF (an ambient or manifold pressure,
  most likely) would fix the unit of the whole block. Time-boxed here.

### 10.7 Reproducing

```bash
mkdir -p /tmp/ghidra_F4
cp -R ghidra_projects/med9.gpr ghidra_projects/med9.rep /tmp/ghidra_F4/
export GHIDRA_INSTALL_DIR=/usr/local/Cellar/ghidra/12.1.3/libexec
./.venv/bin/python -m pyghidra.ghidra_launch --install-dir "$GHIDRA_INSTALL_DIR" \
    ghidra.app.util.headless.AnalyzeHeadless /tmp/ghidra_F4 med9 \
    -process passat_azx_ori.bin -noanalysis \
    -scriptPath ghidra_scripts -postScript import_symbols.py "$PWD"

# the functions this brief decompiled
./.venv/bin/python ghidra_scripts/decompile.py --project-dir /tmp/ghidra_F4 \
    --project-name med9 0x00038708 0x0012E3F8 0x000E8D9C 0x000E8DE4 0x0041AA48 \
    0x00419DA4 0x0043E164 0x00440A3C 0x0010C874 0x004302DC 0x00430448 \
    0x004544A8 0x000C5E54 0x000BDB58 0x000FB974 0x00115AE0 0x000C7958 \
    0x000C7DD0 0x000F44D8 0x00459334 0x00108950 0x00428FCC

# the three axis-key processes and the adaptation limit block
./.venv/bin/python tools/sda_xref.py data/passat_azx_ori.bin --var 0x7FD780 0x7FD8FF
./.venv/bin/python tools/cal_show.py data/passat_azx_ori.bin --raw 0x5C607C 24 u8

# any object named by this pass, e.g.
./.venv/bin/python tools/cal_show.py data/passat_azx_ori.bin 0x5D3580 \
    --scale 1/128 --x-scale 0.75 --x-offset -48
./.venv/bin/python tools/cal_show.py data/passat_azx_ori.bin 0x5D7A42 --guess

# the definition (the integrator builds the committed one)
./.venv/bin/python tools/draft_to_xdf.py re/calibration_draft.csv -o work/x.xdf \
    --min-confidence hypothesis --extra-rows patches/ff_fuel/ffcal001_rows.csv
./.venv/bin/python tools/draft_to_xdf.py --validate work/x.xdf
./.venv/bin/python -m unittest discover -s tests
```

**No Funktionsrahmen page was read for this pass.** Every label it adds is
either descriptive (`axis_*`, `mix_*`, `rl_*`, `zwdelta_*`, `tol_*`, `krke_*`,
`esstt_*`, `esnswl_*`, `N_*`, `CW_*`, `adap_*`) or an existing `cand_` label,
so no new FR-module category was needed and no new Bosch label is claimed.

---

## 11. Pass 4 (brief G4, 2026-09-23, issues #41 and #43)

Brief `docs/agent_briefs/G4_calibration_naming_pass4.md`. Dump unchanged
(`tools/checksum.py verify -q` -> `ALL OK (65 blocks)` before and after). The
Ghidra work was done read-only in a private copy at `/tmp/ghidra_G4` (recipe
in §11.9); the read-outs use `tools/cal_show.py`.

### 11.1 0x7FD3E5 is the intake-air temperature, not a battery voltage (VERIFIED-STATIC; label COMMUNITY)

§10.4 left it at "probably a battery voltage at 1/16 V per LSB" and named the
three writers. All three sit in two functions, and both say the same thing:

```
FUN_000F8A44 (cyclic, 0x0F8A44-0x0F90C3)
  0x8021CC = lookup_1d_u8(struct 0x5D727A -> curve 0x5D728F, 0x7FD427)   ; 0x0F8A64
  ... plausibility / fault debouncing, substitute 0x5D7276 on a fault ...
  0x8021D0 = FUN_004116A4(0x5D72B8, v, 0x8021D0)          ; u16 low-pass, 0x0F8FC4
  0x0F8FCC  lbz   r8,0x21E0(r13)        ; high byte of 0x8021D0
  0x0F8FD0  rlwinm r9,r8,5,0,26         ; v << 5
  0x0F8FD4  stb   r8,-0x2C0B(r13)       ; 0x7FD3E5 = v            <- writer 1
  0x0F8FD8  addi  r4,r9,0x2586          ; v*32 + 9606
  0x0F8FDC  sth   r4,0x21E4(r13)        ; 0x8021D4
FUN_0011A898 (init, 0x11A898-0x11AA23)
  0x7FD3E5 = lookup_1d_u8(0x5D727A, 0x7FD427)             ; writer 2, 0x11A990
  if (v > 0x5D7278 || 0x7F9DFA & 1) 0x7FD3E5 = 0x5D7276   ; writer 3, 0x11A998
  0x8021D4 = 0x7FD3E5 * 32 + 0x2586
```

and `FUN_000BDC4C` fills the input: `0x7FD427 = ADC channel 6 >> 2`
(`FUN_0046CEE0(6)`, store at 0x0BDD08). The unit follows from four
independent facts, none of which is a guess about another:

1. **The curve 0x5D728F is an NTC linearisation.** Over the ADC byte
   8 … 230 (axis 0x5D727B) it falls monotonically from 251 to 0. Under the
   proved `tmot` unit that is **140.25 °C … −48 °C**; as a voltage at 1/16 V it
   would be a 15.7 V … 0 V *decreasing* function of the sensor voltage, which
   no battery input is.
2. **The measuring handler says so.** 0x8021CC (the unfiltered value) is VCDS
   measuring id **85**, handler 0x039C20, and that handler is instruction for
   instruction the `tmot` handler 0x039BA4 (`mulli 3; srwi 2; addi 0x34` →
   formula 0x05 `A = 0x0A`, measuring_vars.md §7.1), i.e. **0.75 °C/LSB − 48**.
   The battery voltage of the same groups is id 81 (0x80345E, formula 0x15).
3. **The absolute-temperature companion is exact.** 0x8021D4 = v × 32 +
   0x2586: at 3/128 K per LSB, 0x2586 = 9606 × 3/128 = **225.14 K = −48.0 °C**
   and 32 counts = 0.75 K, i.e. the same temperature in kelvin. It is consumed
   by the exhaust-temperature block of §11.2, where the cap 0x5D179E = 54321
   reads **1000.0 °C** under the same unit.
4. **The calibration around it is an intake-air calibration.** The substitute
   value 0x5D7276 = 91 = **20.25 °C** (a coolant substitute would be hot), and
   the axes over the cell read as air temperatures: 0x5D5FF1 = −24.75 … 80.25 °C,
   0x5C7BA5 = −18 … 132 °C in 30 °C steps, 0x5D3612 = 30 … 80.25 °C.

The label: id 85 sits in groups **004.4, 006.3 and 011.3** (with rpm, the
battery voltage id 81 and `tmot` id 80), which is where every public VAG
measuring-block list puts the **intake-air temperature**. That makes 0x7FD3E5
`tans` (FR name) — the *unit* VERIFIED-STATIC, the *meaning* COMMUNITY, and one
VCDS group-004 read on the bench confirms it.

Consequences, all applied to `re/calibration_names.csv` in place with a dated
correction in the evidence column:

| row | was | now |
|---|---|---|
| 0x5D7942 | `axis_ubatt_5D7942`, 1/16 V | `axis_tans_5D7942`, −9.75, 0, 19.5, 79.5 °C |
| 0x5D7947 | `axis_ubatt_5D7947`, 8.19 … 14.00 V | `axis_tans_5D7947`, 50.25 … 120 °C |
| 0x5C7BAB `cand_KLZWSTT` | axis unscaled | x = `tans` −18 … 132 °C |
| 0x5D361B `mix_801D01_map` | y = "battery voltage 6.5 … 10.7 V" | y = `tans` 30 … 80.25 °C, x = 0x7FD3F7 60/75/90 °C |
| 0x5D5FFB `zwdelta_7FD338_map` | x raw counts | x = `tans` −24.75 … 80.25 °C |

So `zwdelta_7FD338_map` is an **intake-air-temperature ignition correction**
(−6.0 … +2.25 °CA over `tans` and speed) — the classic hot-air knock
protection — and `cand_KLZWSTT` is the start angle's `tans` term.

**0x7FD3F7, the other half of the `start.md` §8 row, is settled by the same
read.** `FUN_000F90C8` (0x0F9F24, 0x0F9FC4) and `tmot_init` 0x11AA28
(0x11ADD0) write it as the high byte of the low-pass state 0x802214 =
`FUN_004116A4(0x0A0A, 0x8021F3, …)`, where 0x8021F3 is the modelled engine
temperature of `start.md` §2 (itself `tmot` or the substitute 0x8021EB); at
init it is `cand_mw_tmot` 0x8021EF directly. So **0x7FD3F7 is a filtered engine
(coolant) temperature in the `tmot` unit**, VERIFIED-STATIC, which is what
every row that already used it at 0.75 °C − 48 assumed.

> **§10.4 bullet 2 — SETTLED (2026-09-23, G4, §11.1).** 0x7FD3E5 is the
> intake-air temperature at 0.75 °C/LSB − 48, not a voltage.

### 11.2 `cand_KFMIRLINV` outputs a relative charge, and 0x8015AF is an ignition efficiency (VERIFIED-STATIC)

§10.6 left the value unit of 0x5C9938 open with one lead, the divisor
0x8015AF. Decompiling its two writers settled the divisor, and following the
map's output one step further than F4 did settled the map.

**0x8015AF.** `FUN_000E0A0C` (store 0x0E0D5C) and `FUN_00104110` (store
0x104224) both compute

```
0x8015B0 = min(0x802661 + etazw_offset_nmot_curve(nmot_w), 255)     ; 0x0E0C40
0x8015AF = (0x7FD305 & 0x40) ? min(0x8015B0 * 0x80265C / 200, 255) : 0x8015B0
```

and `FUN_00436B24` makes every u8 of that family from a u16 as
`u8 = u16 * 25 >> 12` (0x436B24-0x436B8C), which maps 32768 to exactly 200.
The u16s are Q15 factors: 0x802632 = `FUN_0043619C(zwopt, zwmin)` (0x436890),
the same efficiency function B7 found behind `etazwb` (`ignition.md` §9), used
as a `mul_shr15_sat` operand at the end of `FUN_00436838`; 0x802620 is
`(0x10000 − curve(nmot) × 0x801D8A) >> 1`, 1.0 when the curve is zero. So
**0x8015AF is an ignition efficiency at 1/200 per LSB, 200 = 1.0**, and the
"reference value 200" of §10.6 is simply 1.0 in that unit. The second
evaluation of the map, at `0x8034FA × 200 / 0x8015AF`, is at **the charge
divided by the ignition efficiency** — the charge the engine would need to make
the same work at the efficiency of the latest permitted angle — which is the
textbook input of an exhaust-temperature model (a retarded angle heats the
exhaust).

**The map's output.** Nothing in `FUN_000E06F8` fixes it (§10.6 was right
about that), but `FUN_000E0A0C` does, twice:

```
0x0E0D90  0x8015D2 = cand_KFMIRLINV(nmot_w, 0x8034FA * 200 / 0x8015AE)
0x0E0E64  0x8015CC = mul_div_sat(0x8015B4, 0x1BC, 0x8015C0)
0x0E0E98  compare 0x8015CC < 0x8015D2          ; a compare needs one unit
0x0E0EC8  0x8015C6 = lookup_2d_u16(cand_KFMIOP 0x5CA218, nmot_w, 0x8015CC)
```

and `cand_KFMIOP`'s x axis is `rl_w` at **100/4096 %/LSB** (E3, 10.4 … 104.2 %).
Independently, `FUN_000E06F8` forms `0x8015BA = 0x8015D0 × 0x8015C0 / 0x1BC`
and the caller undoes exactly that factor (`× 0x1BC / 0x8015C0`) before the
KFMIOP lookup, so the round trip cannot change the unit. **`cand_KFMIRLINV`'s
values are a relative charge at 100/4096 %/LSB: 0 … 5103 = 0 … 124.6 %**, about
1.2 × its input at every speed (e.g. 2000 rpm: 10 % → 12.4 %, 50 % → 57.9 %,
100 % → 123.4 %). The label stays `cand_` and `hypothesis`; the unit is
`static`.

**What the block is.** With the unit known, the rest of `FUN_000E06F8` reads
as temperatures:

| object | before | now (unit VERIFIED-STATIC) |
|---|---|---|
| 0x5D1CBA | `cand_KFPSSRM`, manifold pressure, unit unknown | `temp_exh_nmot_rl_map`: `0x8015D6 = map(nmot_w, 0x8015D0 >> 1) − tans_kelvin` — a **subtraction of 0x8021D4 (K at 3/128)**, so the map is 3/128 K: **315 … 886 °C** over 650 … 6000 rpm and 15.6 … 104.2 % charge (x at 100/2048 %) |
| 0x5D179E | unnamed | `temp_exh_max_5D179E` = 54321 = **1000.0 °C**, the cap of the block |
| 0x5D17A2 | `cand_PSREF`, unit unknown | `dtemp_ref_5D17A2`, subtracted *from* by `ΔT >> 1`, so 3/64 K: **1100.0 K** |
| 0x5D179A / 0x5D1790 | unnamed | `etazw_offset_nmot_curve` (+0.02 … 0 over 1500 … 2700 rpm) and its count |
| 0x5D178D | unnamed | `etazw_offset_8015AE` = +0.05 |
| 0x5D178C / 0x5D17A0 | unnamed | the block's code word (3) and the unused substitute of 0x8015D8 |

Three exact round numbers (225.15 K, 1000.0 °C, 1100.0 K) under one unit are
the confirmation, and the map itself is the fourth: under 3/128 K − 273.15, 35
of its 64 cells land within ±0.01 °C of a **whole degree Celsius** (a random table would give about one; 315.01,
575.01, 623.01, 656.01, 601.00, 683.01, 722.01, 802.00, 839.01, 886.00 …) —
the calibrator typed integer °C, which no other reading of the counts
reproduces. **This is the exhaust-gas temperature model that F4's §10.2
time-boxed as lead (b)** — not `FUN_00108950`, which is a different soak
model — and its output turns back into a charge 0x8015CC and a torque
0x8015C6 through `cand_KFMIOP`, which is the shape of a *component-protection
charge limit*. The meaning ("exhaust", "component protection") is HYPOTHESIS;
every unit in the table is VERIFIED-STATIC. §10.2's conclusion is untouched:
none of these cells reaches `rk`.

> **§10.6 bullet 3 — SETTLED (2026-09-23, G4, §11.2).** The value unit of
> `cand_KFMIRLINV` is a relative charge at 100/4096 %/LSB; 0x8015AF is an
> ignition efficiency at 1/200 (200 = 1.0).

> **Refined later in this pass (§11.6):** the exhaust-gas temperature model
> proper is `FUN_001043C8` (`%ATM`); the block above is a second user of its
> manifold map, which is FR **`KFATMKRH`** (the row `temp_exh_nmot_rl_map` was
> renamed). Its x input in `%ATM` is the fuel mass `rkg` 0x803034, so
> `cand_KFMIRLINV` >> 1 plays the role of an `rkg` estimate here.

Commands:

```bash
./.venv/bin/python3 tools/sda_xref.py data/passat_azx_ori.bin --var 0x8015AE 0x8015B0
./.venv/bin/python ghidra_scripts/decompile.py --project-dir /tmp/ghidra_G4 --project-name med9 \
    0x0E0D5C 0x104224 0x0E069C 0x0E06F8 0x0E0900 0x436B38 0x4362C8 0x436890 \
    --asm 0x0E0D84 --count 70
./.venv/bin/python3 tools/cal_show.py data/passat_azx_ori.bin 0x5C9938 --scale 100/4096
./.venv/bin/python3 tools/cal_show.py data/passat_azx_ori.bin 0x5D1CBA --scale 3/128 --offset -273.15 --x-scale 100/2048
./.venv/bin/python3 tools/cal_show.py data/passat_azx_ori.bin --raw 0x5D179A 6 u16
```

### 11.3 The intake-air-temperature process (11 objects, VERIFIED-STATIC)

With 0x7FD3E5 settled, `tans_process` 0x0F8A44 and `tans_init` 0x11A898
(§11.1) read as a textbook sensor chain, and every constant in them has a unit
from the variable it is compared with:

| object | value | what it does |
|---|---|---|
| `tans_ntc_curve` 0x5D728F | ADC 8 … 230 → 140.25 … −48 °C | the NTC linearisation (20 points) |
| `tans_subst` 0x5D7276 | 91 = 20.25 °C | substitute on a sensor fault |
| `tans_plaus_min` / `_max` 0x5D7277 / 0x5D7278 | −45.0 / 138.75 °C | range check, fault words 0x8201 / 0x8101 through `FUN_004067FC(0xDA)` |
| `tans_debounce` 0x5D72A9 | 5 activations | reload of all five debounce counters |
| `tans_filter_k` 0x5D72B8 | 2621/65536 = 0.040 | low-pass weight in `lowpass_u8q8` 0x4116A4 |
| `tans_warm_tmot` 0x5D72AC / `tans_warm_count` 0x5D72B6 | 75 °C / 1200 activations | delay before the "sensor stuck" check arms |
| `tans_min_spread` 0x5D7271 | 0 K | the "stuck" threshold — 0, so the check can never fail |
| `CW_tans` 0x5D7270, `tans_thr_7FD3D1_b1` 0x5D7279 | 4, 143.25 °C | code word (bit 1, the stuck report, is clear) and a status threshold |

`FUN_004116A4` is now `lowpass_u8q8`: `y += (x·256 − y)·k >> 16`, at least one
LSB per call. For flex fuel the only row that matters is the NTC curve: the
ethanol sensor's fuel temperature is *not* this input, and nothing here needs
to change.

### 11.4 The BDE mode word decides the `KFPRSOL*` labels — four of six were wrong

`rail.md` §13 listed "which FR name belongs to which `KFPRSOL*` variant" as
open, the addresses and the selection VERIFIED-STATIC and the names guessed.
The selection tests the **u16** 0x7FB69A (`lhz −0x4956(r13)` at 0x4582A4).
Its writers `FUN_00119640` (0x11965C) and `FUN_0044C970` (0x44C9FC, 0x44CBD0)
copy it from 0x802AB2 and maintain three more bits exactly as the FR's
`%BDEMUM` describes `bdemod_w` (bit 9 `B_berhom`, bit 10 → 12
`B_easch` → `B_bersch`, bit 14 `B_bdemz` while target ≠ actual mode). The FR
(`%BDEMKO` FB, the bit table; COMMUNITY for this software) codes the modes as

| bit | 0 | 1 | 2 | 3 | 4 | 6 | 7 |
|---|---|---|---|---|---|---|---|
| mode | HOM | HMM | HOS | SCH | SKH | HSP | HKS |

and `%HDRPSOL` p1722 draws the selection with **exactly six booleans** —
`B_hmm`, `B_skh`, `B_hos`, `B_kh`, `B_sch`, `B_hks` — and says the offset
(`CWPRSOLAP` bit 5) goes "auf `KFPRSOLHOM` und `KFPRSOLSCH`". The code tests
bits 7, 4, 2 (+ 0x80156F bit 0), 3, 1 and adds the offset on exactly the bit-3
and default branches. That fixes all six:

| map | tested | B9's label | **now** |
|---|---|---|---|
| 0x5D5224 | bit 7 | `KFPRSOLKH` | **`KFPRSOLHKS`** (homogeneous knock protection) |
| 0x5D53A4 | bit 4, bit 2, 0x80156F.0 | `KFPRSOLHMM` | **`KFPRSOLKH`** (catalyst heating: SKH, HOS or homogeneous) |
| 0x5D54A4 | bit 3, + offset | `KFPRSOLHKS` | **`KFPRSOLSCH`** (stratified) |
| 0x5D52A4 | bit 1 | `KFPRSOLSCH` | **`KFPRSOLHMM`** (homogeneous lean) |
| 0x5D5324 | default, + offset | `KFPRSOLHOM` | `KFPRSOLHOM` (unchanged, now `static`) |
| 0x5D5424 | — | `KFPRSOLOFF` | `KFPRSOLOFF` (unchanged, now `static`) |

0x80156F bit 0 is written by `FUN_001032FC` (0x103430) as `bdemod_w` bit 0
(HOM) AND 0x801571 bit 3, which needs the exhaust-temperature block of §11.2
running inside a `tmst` / `tnst_w` after-start window: **homogeneous catalyst
heating**, the FR's `B_kh` (HYPOTHESIS for the meaning). The same bit selects
the ZWMIN map D3 called `cand_KFZWMNUM` 0x5D5C8B, whose −15 °CA plateau is a
cat-heating angle; it is now **`cand_KFZWMNKH`** ("Min-Zündwinkel
Katheizen", FR p3095), still a candidate.

The same bit table names the two `KFZWOP` deltas of `ignition.md` §9:
`zwopt_delta_maps` reads 0x5C9E25 on bit 7 and 0x5C9EF2 on bit 6, and FR
`%MDZW` p768 defines the HKS and HSP optimum angles as deltas **`KFDZWOHKS` /
`KFDZWOHSP`** on the 16 × 11 `KFZWOP` grid with default 0 — both are all zero
here. The 1-D curve in the same function is **`KLFAKSP`**: `0x802620 =
(0x10000 − KLFAKSP(nmot)·0x801D8A) >> 1` is FR's "efficiency depending on the
split", and its axis 0x5C9FA3 is the FR default 520/1000/1520/2000/2520 rpm to
the rpm.

**For this engine**: the 3.2 FSI runs `bdemod` = HOM in normal driving
(`fr_index.md` §0), so the live rail setpoint is **`KFPRSOLHOM` (+ `KFPRSOLOFF`)
and `KFPRSOLKH` during catalyst heating after a cold start**. E5's rail hook (on `prsoll_raw`) and
`docs/05` (the rail section) name only `KFPRSOLHOM` and `KFPRSOLOFF`, which are unchanged; nothing that was built
depends on the four corrected labels. Two things outside this brief's files
still carry the old labels and are listed for their owners: the draft's
`name_or_blank` column (the sidecar wins, so the XDF is right) and the
comments of `tests/test_ff_rail_patch.py` lines 124-129.

### 11.5 `FUN_00455C60` is the purge-fuel block (`%TEB`), and 0x80315C is `rkte_w` (12 objects)

`injection.md` §7 took `FUN_00455C60` for the EGAS level-2 fuel monitor and §9
listed `− 0x80315C` as a "component/diagnostic subtraction". It is neither:
the function limits the purge-valve opening, delays and mixes the purge gas,
and writes **0x80315C, the canister fuel that `gk_rk` then subtracts from
`rk`** — the FR's `rkte_w` (VCDS id 171 shows it as a percentage). The FR's
`%TEB` names fit where the inputs and the mode split are unique:

| object | FR label | evidence in the code |
|---|---|---|
| 0x5D479E | **`KFFTEVFX`** | 4 × 4 over (`nmot`, the pressure ratio 0x7FEFAE / 0x800EED) — FR "nmot, pspu" |
| 0x5D46F0 | **`FTEVFXHM`** | curve over `nmot`, min()'d in when `bdemod_w` bit 1 (HMM) |
| 0x5D46F9 | **`FTEVFXS`** | the same, bit 3 (SCH) |
| 0x5D4888 / 0x5D488A | **`FRKTEMN` / `FRKTEMX`** | `rkte` clamped to [−0.08, +0.50] × `rk`, clamp flag = `B_rkteb` |
| 0x5D4982, 0x5D4828, 0x5D4705, 0x5C732B | `cand_NVERZMN`, `cand_DSTEMIN`, `cand_FVERMN`, the 5-point `qmsdyn` axis | transport delay and mixing, HYPOTHESIS |
| 0x5D4807, 0x5D46C3, 0x5D497E | descriptive | release debounce, code word, mass-flow floor |

For flex fuel: the subtraction assumes gasoline vapour. It is a tuning-checklist
item (log id 171 during purge on E85), not a patch item. `injection.md` has
the dated correction (§12) and its §11 row is marked SETTLED.

### 11.6 `FUN_001043C8` is the exhaust-gas temperature model `%ATM` (53 objects)

The second consumer of the §11.2 map is the two-bank `%ATM` (0x1043C8-0x1081FF;
every object has one load site per bank). Its temperatures are u16 K at
3/128 K — the unit §11.1 found for `tans_kelvin` — and the calibration lands on
whole °C under it everywhere: `KTMOTW` 95.0, `TAVHKEMN` 230.0, `TAVVKEMN`
244.0, `TAVVKGEMN` 250.0, `TATMKRSA` 275.0, the default start temperature 20.0,
the manifold maps on whole degrees, the main-catalyst exotherm on whole kelvin.
`bdemod_w` bits 3|2|4 (the stratified family) select the S variants, which is
what fixes the S/H labels.

| group | objects | label status |
|---|---|---|
| manifold | **`KFATMKRH`** 0x5D1CBA (was `temp_exh_nmot_rl_map`, over `nmot_w` × `rkg` 0x803034), **`KFATMKRS`** 0x5D1D5E (stratified: 800-3600 rpm, half load, 216-625 °C), **`KFATLAMS`** 0x5D1C1E (λ 0.75-1.40 axis, 1.0 at λ = 1), **`KFATZWMS`** 0x5D1E4E (ignition-efficiency axis 0.30-1.00, 1.0 at η = 1, up to +53 %) + its HSP twin, `cand_FATMDKS`, **`TATMKRSA`** and the overrun rate curve | static except `FATMDKS` |
| pre-catalyst (feeds the chain) | **`EAVKH`, `EAVKS`, `EBVKH`, `EBVKS`** + counts, **`MATMAVK`, `MATMBVK`**, **`TAVVKEMN`, `TOEXTVK`**, **`FEXOLAVK`** (λ axis 0.70-4.0) | static — and **all neutral**: zero exotherm, zero mass, zero λ factor, i.e. no pre-catalyst is modelled |
| parallel reference section | `cand_EAVKG(H)`, `cand_EBVKG(H)`, `cand_MATMA/BVKG`, `cand_TAVVKGEMN`, `cand_TOEXTVKG` + counts | hypothesis: its outputs feed nothing downstream, which is what the FR's *Grenzkat* (the catalyst-diagnosis reference) is |
| main catalyst | **`FEXOLAHK`** (0.70 at λ 0.70 … 1.00), **`TAVHKEMN`**, **`TOEXTHK`**, `cand_FATMEHK` (+76 … +135 K), `cand_FATMEBHK` (−35 … −14 K), two stratified twins, `cand_MATMA/BHK` | static / hypothesis as marked |
| general | **`KTMOTW`**, the default start temperature, the HSP enable temperature, `cand_SOPOV` | static / hypothesis |

For flex fuel the one object that matters is **`KFATZWMS`**: the model heats
the exhaust as the ignition efficiency falls, so an E85 calibration that runs
*more* advance (higher efficiency) lowers the modelled exhaust temperature by
itself, and one that is knock-limited later raises it. Whether anything
enriches on the modelled temperature is the open question of §11.8.

### 11.7 0x80223B is the gear, not an operating mode (`%BBGANG`)

§10.6 settled 0x80223B as "the operating-mode index, 0..7". Its one writer
(0x45C064) is `FUN_0045BD00`, and that function is textbook FR `%BBGANG`
(the FB text): `nvquot_w` 0x80223E = `nmot_w · 4096 / 0x802260` (engine speed
over vehicle speed); keep the last gear while `nvquot_w` stays inside its
window, else test gears 1 … 6 upwards against **`NVQUOT1O` … `NVQUOT6U`**
(0x5D77F0 … 0x5D7806, twelve new rows), 0 when none fits, **7 from the
reverse flag** 0x7FEBD7, and the CAN gear 0x7FD17C with an automatic. So:

* **0x80223B is `gangi`** (VERIFIED-STATIC for the dataflow, the FR labels
  `static`); `re/symbols.csv` renames `opmode_index` with a dated note, the
  sidecar renames `axis_opmode_5C887B` → **`axis_gangi_5C887B`**, and the
  four `rl_*_map` rows of §10.5 plus `cand_KFRLMXBTS` / `cand_KFFRLMXN` are
  maps over **gear**, not mode (descriptions corrected in place);
* `%MDFUE`'s "`== 7` → 0x5C94F2" is **reverse gear**, so the FR's `KFMIRLS`
  (stratified) is no longer a candidate: the row is now the descriptive
  **`rl_mdfue_gear7_map`**;
* one VCDS log of id 130 while shifting confirms it outright.

> **§10.6 bullet 2 — CORRECTED (2026-09-23, G4, §11.7).** The variable is the
> gear `gangi`, not an operating-mode index; everything else in that bullet
> (one writer, the 0..7 axis, id 130) stands.

### 11.8 A lambda divisor in `gk_rk` that §10.2 missed (a lead, not a closed item)

While naming `KFATLAMS` this pass read `gk_rk` again: after the base mass and
before `fr`, it computes **`rk = (rk << 12) / 0x80304A` whenever 0x7FEA33 is
set** (set at 0x41AE3C on the 0x7FE920 branch). `injection.md` §9 lists the
step as "per-injection normalisation (mode-dependent)"; §10.2's statement
"`gk_rk` multiplies exactly four things … there is no fifth factor" overlooked
it. What is VERIFIED-STATIC:

* `lamsbg_select` `FUN_0041AF2C` writes 0x80304A = 0x803046 in homogeneous
  mode (`bdemod_w` bit 0), else 0x80340C clamped to [0x802AC0, 0x802ABE];
* 0x803046 / 0x803044 are built per bank by B8's `eta_coordinator` 0x442C18
  from a list of candidates — a base value (0x803050: 1.0, or
  `lamsbg_mode_change` 0.970 during a BDE mode change), component-protection
  style inputs (0x803058, 0x801CC6 / 0x801CC4 under 0x801CD4 bits 1 / 3 and
  0x7FEA38), 0x80341E / 0x80341C, 0x7FED90 / 0x7FED8E, 0x801D2C / 0x801D2A,
  0x803412 / 0x803410 — clamped to [0x802AC0, 0x802ABE], with fixed values on
  `dwbho1smn_w` bits 0 / 1 (`lamsbg_subst_dwbho` 1.008) and 0x8033FA bit 13
  (`lamsbg_fixed_b1` / `_b2` 1.000);
* `%ATM` keys `KFATLAMS` (FR input `lamsbg_w`) with 0x80304A over a λ axis.

So 0x80304A is, with high probability, **`lamsbg_w`, the lambda setpoint, and
a value below 1.0 enriches**. B8's reading of 0x803046 / 0x803042 as
*efficiency* setpoints (`start.md` §5.1) is therefore suspect too — the
12-point x axis 0.65 … 1.20 of the 0x5C76D5 ignition map reads as a λ axis as
naturally as an efficiency axis. **This reopens the part of §10.2 that says
"there is no stock enrichment": the mechanism exists; whether any calibrated
input ever asks for λ < 1 was not traced** (time-boxed; only the six constants
above are named). The follow-up is to trace the candidate inputs listed above
to their maps, starting with 0x803058 and 0x801CC6, which look like the
component-protection (`%LAMBTS`) request. `symbols.csv` carries dated notes on
`eta_coordinator`, `eta_mean_w` and `eta_mean`, and a `start.md` §5.1 note
points here.

> **§10.2 — CORRECTION (2026-09-23, G4, §11.8).** Point 1 is incomplete:
> `gk_rk` also divides by the lambda setpoint 0x80304A. The rest of §10.2
> (`KFMIXA` / `KFMIXB` neutral, `fgru_trim` a tester channel, the PI
> controller tracking the request) stands; its conclusion "no full-load or
> component-protection enrichment on the fuel path" does not, until
> 0x803046's inputs are traced.

### 11.9 Counts, verification, reproduction

| | before (F4) | after (G4) |
|---|---|---|
| rows in `re/calibration_names.csv` (objects with a name) | 359 | **462** |
| … tagged `static` (the label) | 148 | **242** |
| … tagged `hypothesis` | 211 | 220 |
| objects with a unit | 344 | **450** |
| scaling tagged `static` | 268 | **348** |
| tables/curves/axes without a sidecar row | 878 of 1,068 | **850 of 1,068** |

(F4 quoted 876 of 1,066; the recount with the current draft gives 878 / 1,068
before this pass, so the comparison uses that.) **103 new rows**, and 29
earlier rows corrected in place (the six 0x7FD3E5 rows, `cand_KFMIRLINV`,
`KFPSSRM` → `KFATMKRH`, `PSREF`, `TMSRMMN`, six `KFPRSOL*`, two `KFZWOP`
deltas and their counts, `KFZWMNUM` → `cand_KFZWMNKH`, eight gear-keyed
rows). The generator on a work copy:

```bash
./.venv/bin/python3 tools/draft_to_xdf.py re/calibration_draft.csv -o work/med9_draft.xdf \
    --extra-rows patches/ff_fuel/ffcal001_rows.csv --min-confidence hypothesis
./.venv/bin/python3 tools/draft_to_xdf.py --validate work/med9_draft.xdf
# re/med9_draft.xdf is not touched on this branch
```

Reproduction (read-only Ghidra copy, as §10.7):

```bash
mkdir -p /tmp/ghidra_G4
cp -R /Users/carlo/ecu_azx/ghidra_projects/med9.gpr /Users/carlo/ecu_azx/ghidra_projects/med9.rep /tmp/ghidra_G4/
export GHIDRA_INSTALL_DIR=/usr/local/Cellar/ghidra/12.1.3/libexec
./.venv/bin/python -m pyghidra.ghidra_launch --install-dir "$GHIDRA_INSTALL_DIR" \
    ghidra.app.util.headless.AnalyzeHeadless /tmp/ghidra_G4 med9 \
    -process passat_azx_ori.bin -noanalysis -scriptPath ghidra_scripts -postScript import_symbols.py "$PWD"
./.venv/bin/python ghidra_scripts/decompile.py --project-dir /tmp/ghidra_G4 --project-name med9 \
    0x0F8FD4 0x11A990 0x0F9F24 0x11ADD0 0x0BDD08 0x4116A4 \
    0x0E0D5C 0x104224 0x0E069C 0x0E06F8 0x0E0900 0x436B38 0x4362C8 0x436890 \
    0x44C9FC 0x11965C 0x4582C4 0x103430 0x455C60 0x1043C8 0x45C064 \
    0x41AA48 0x41AF60 0x442C18 0x454698
./.venv/bin/python ghidra_scripts/decompile.py --project-dir /tmp/ghidra_G4 --project-name med9 \
    --asm 0x39BA4 --count 9 --asm 0x39C20 --count 9 --asm 0x0F8FA4 --count 20
./.venv/bin/python3 tools/sda_xref.py data/passat_azx_ori.bin --var 0x7FD3E5
./.venv/bin/python3 tools/store_xref.py data/passat_azx_ori.bin --window 0x7FD3E0 0x7FD3F8
./.venv/bin/python3 tools/measuring_vars.py data/passat_azx_ori.bin --groups | grep -w 85
./.venv/bin/python3 tools/cal_show.py data/passat_azx_ori.bin 0x5D728F --scale 0.75 --offset -48
./.venv/bin/python3 tools/cal_show.py data/passat_azx_ori.bin 0x5D1CBA --scale 3/128 --offset -273.15
./.venv/bin/python3 tools/cal_show.py data/passat_azx_ori.bin --raw 0x5D1E96 16 u16
```

FR pages read for this pass (`documents/MED9.1_TFSI_Funktionsrahmen.pdf`,
`pdftotext -layout`): `%BDEMKO` FB (the mode bit table), `%BDEMUM` ABK/FB,
`%HDRPSOL` p1722 (diagram, ABK, `CWPRSOLAP`), `%MDZW` p768 APP (`KFDZWOHKS`,
`KFDZWOHSP`, `KLFAKSP`), `%ZWMIN` ABK p3095, `%TEB` ABK, `%ATM` ABK,
`%BBGANG` ABK/FB. Every FR label this pass assigns is either `static` (the
FR's inputs, mode split and count match the code) or `cand_` (one of those is
missing).
