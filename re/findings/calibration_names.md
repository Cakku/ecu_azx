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
* **0x7FD3E5 is *probably* a battery voltage at 1/16 V per LSB, and this is
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
* **0x80223B — SETTLED (VERIFIED-STATIC).** It has its own axis,
  `axis_opmode_5C887B` 0x5C887B = 0, 1, 2, 3, 4, 5, 6, 7, searched by
  `cal_axis_key_process` into 0x7FD7A0, and exactly one writer, 0x45C064. Four
  of the six charge thresholds of §10.5 are maps over it. It is the
  **operating-mode index**, the same variable `%MDFUE` compares against 7 to
  select `cand_KFMIRLS` (§9.1). Its eight values are the combustion modes of a
  BDE engine; which value is which mode is **not** established, and VCDS
  measuring id 130 (groups 051.3 / 068.3, format 0x36) displays it directly,
  so one drive log would settle that too.
* **`cand_KFMIRLINV` 0x5C9938's value unit — still open.** What was tried:
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
