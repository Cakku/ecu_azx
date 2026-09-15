# Bosch MED9.1 Funktionsrahmen — topic index for this project

Agent A2, brief `docs/agent_briefs/A2_reference_documents.md`, issue #6.
Date: 2026-09-15.

Source: `documents/MED9.1_TFSI_Funktionsrahmen.pdf` (not committed; URL and
SHA-256 in `documents/SOURCES.md`). 4,860 pages, 599 documented function
sections, **project "Ea827 TSI", 5-4420.01/41W038_PQ35;0, 20 August 2004**.
FR page numbers equal PDF page numbers, so `pdftotext -f N -l N -layout` gets
the page cited. Every `pNNNN` below is an FR page.

---

## 0. How much of this applies to us — read this first

| | |
|---|---|
| **The FR** | SG-MED9-1, Ea827 **TSI** — 2.0 l turbo 4-cylinder, PQ35, 2004 |
| **Our ECU** | 03H906032 / Bosch 0261S02226 / SW `1037382557`, dataset `D9133_43K6P0` (2006-05-08), engine label `P3.2 FSI-EU4` — 3.2 l **naturally aspirated V6 FSI**, MED9.1.1, 2007 |

Both are the same Bosch software platform (same FR block library, same ASCET
code generator, same tester/CAN/EEPROM framework), but ours is two years newer
and a different engine. Consequences:

* **Module and label naming conventions transfer.** `%RKTI`, `%ZWGRU`, `KFZW`,
  `lamsbg_w`, `prsoll_w` and so on will be the same identifiers in our A2L.
* **Turbo-only modules do not exist in ours** (`LDRUE`, `LDRPID`, `LDRPLS`,
  `LDOB`, `LDUVST`, `DLDR`, `DLDE`, `DLDUV`, `BBLDR`, `LDRLMX`, `LDTVMA`,
  everything gated by `SY_TURBO`). Our engine has no wastegate/DV.
* **Stratified/BDE-mode modules may or may not be active.** The 3.2 FSI is a
  homogeneous-only FSI in EU4 trim, so the `SY_SCH` / `SY_SKH` / `SY_HSP`
  branches are probably compiled out. Confirm before chasing them.
* **Our engine is a V6 → two banks.** `SY_ZZBANK`, `SY_STERVK`/`SY_STERHK`
  (two pre-cat and two post-cat sensors), `..._w` / `...2_w` duplicated lambda
  paths and `SY_SGANZ` matter much more for us than for the 4-cylinder FR.
* **No flex-fuel anything.** "Ethanol", "E85" and "Flex" (other than
  "Motor Flexia", an unrelated CAN message) do not occur in this FR at all.
  The flex-fuel feature has no stock hook; it has to be built
  (`docs/05_flexfuel_design.md`).

### What our dump actually confirms

Our binary contains **no FR label strings** (`strings` finds only the
identification block and noise inside calibration). Nothing below can be
"confirmed by a string". What *is* confirmed, from `docs/02_memory_map.md` §7:

| Confirmed by the dump | Evidence | FR modules it pins down |
|---|---|---|
| KWP2000 / OBD service dispatch table, 24 entries | file 0x2B870 | `T2SERV` and the `T2*` / `TC*MOD` set — see §7 |
| Three TouCAN modules in use | CANMCR table, file 0x2BC38 | `CAN_CONF` |
| 21-entry CAN **receive** table with IDs and DLCs | file 0x2BC90 | `CANECUR` |
| 16-entry CAN **transmit** table with IDs and DLCs | file 0x2BDF0 | `CANECU` (16 entries = the 16 bits of `CW_CAN_S`) |
| ECU identification strings | file 0x1CEE20 | `T2ID` / `T2REI` / `TKSWL` |
| 65 block checksums, runtime descriptor lists | `tools/checksum.py`, file 0x1E73C / 0x829A0 / 0xA3A18 | `URROM` / `URMEM` |
| SPI (QSMCM) driver, 49 references | `docs/02` §3 | `EEP_CONF` / `EEPINIKW` / `DSGEEP` |

Everything else in this document is **an assumption carried over from the TFSI
FR**, and is marked as such where it matters.

---

## 1. Injection quantity and injection time (FSI high-pressure path)

The MED9 BDE chain is: **relative fuel mass `rk` → injection time `ti` →
output angles `wesb/wese` → driver**.

| FR module | Pages | What it is | Key labels |
|---|---|---|---|
| `GK` Gemischkontrolle | 1556–1561 | produces the relative fuel mass `rk_w`, `rk2_w` (per bank), `rkzi_w`, `rkme_w`; minimum-mass clamps | `KLRKMINE`, `KLTIMIN`, `SY_RLRK`, `SY_ZYLZA`, `CWBDE1` |
| `ESVST` Einspritzung Vorsteuerung | 1552 | pre-control: `fgru`, `fst_w`, `fnswl_w`, `rkukg_w`, lambda feed-in `lamsbg_w` | `SYNAES`, `STADAP` |
| `ESGRU` Grundeinspritzungen | 1555 | base injections | `FWET`, `FWEMRFA`, `SY_TFRK` |
| `ESUK` Übergangskompensation | 1562–1568 | wall-film / transient compensation `rkuk_w`, `fbade_w`, `fvade_w` | **`KFBADE`**, **`KFVADE`**, `SY_TFBA`, `SY_TFVA` |
| `ESWE` Schub/Wiedereinsetzen | 1553–1554 | over-run cut and re-entry | `FWET`, `FWEHT`, `FWEMRFA` |
| `RKSPLIT` | 1838–1841 | splits `rk` across multiple injections (`rkho1s1_w`, `rkhk2k1_w`, `rkhp2k1_w`, `rksc1k1_w`) | `KLFRK`, `KFFRKHK2K1`, `KLFRKHK2K1`, `KLFRKHP2K1`, `FWSFRKS` |
| **`RKTI`** Einspritzdauerberechnung | **1826–1837** | **the `rk → ti` conversion — the single most important function for flex fuel** | see below |
| `AES` Ausgabe Einspritzung | 1789–1790 | assembles `tix_w`/`tiy_w`, `wesbx_w`/`wesby_w`, injection counts | `KLHDEV`, `KLTVTS`, `SYNTIZW` |
| `AWEA` Ausgabe Winkel Einspritz-Ansteuerung | 1800–1811 | **injection window / start-of-injection angles** | `KFWBHO1SW`, `KFWBHO1SWE`, `KFWBHO1SLE`, `KFWBHO1SS`, `KFWBHO1SWL`, `KFDWBHO1SK`, `KLWESABR`, `KLWFWHXXS`, `KLWBHO1SMX`, `KFWBHK2S1`, `KFWEHK2K1`, `KFWBHP2S1`, `KFWEHP2K1`, `KFDWESC1K1`, `KLSWBHTKR`, `KFGRPWBHDY` |
| `ESAUSG` | 1812–1819 | hands `tix_l`, `w1esb_w`, `w2esb_w` to the driver | `FWTIHO1PH`, `FWWBHO1PH` |
| `KT_ES` Komponententreiber Einspritzung | 1820–1825 | HDEV output stage driver | |
| `EAKO` Einspritzarten-Koordinator | 1544–1551 | chooses the injection type (`bdeeaz_w`, `bdeeaw_w`) | `SY_HDST`, `SY_SCH`, `SY_SKH`, `SY_HSP`, `SY_HKS`, `SY_HOS` |
| `SYNTIZW` | 1793–1796 | injection/ignition synchronisation | |
| `ZGST` Zylindergleichstellung | 1843–1871 | per-cylinder balancing on `ti` | |

### 1.1 `%RKTI` in detail (p1826–1837) — the flex-fuel lever

`ti..._w = ( rk..._w × frt..._w )` linearised through the HDEV master curve,
plus dead time. The FR names (p1835–1836 text, APP section p1836):

| Label | Role | Note for us |
|---|---|---|
| **`KRKATE`** | injector constant **[ms/%]**, "Gemischfaktor"; derived from the static flow `Qstat` at the calibration pressure with a **1.05 petrol correction factor** | this is the scalar that has to change (or be compensated) for E85 |
| **`KLTIKRPR`** | flow correction vs. **rail pressure** — must be consistent with `KRKATE`'s nominal HDEV pressure (e.g. 10 MPa) | |
| **`FKKVS`** | correction map over **effective ti × engine speed** for fuel-rail pulsations; suppressed during start via `B_stendes` | |
| **`KLHDEV`** | HDEV master curve, linearises small injection times | |
| **`KLTVTSV`** | dead time `tv..._w` (voltage/pressure dependent opening delay) | the FR's `TVUB` equivalent |
| **`TIMINP` / `TIMNP`** | minimum injection time clamp | too large a value breaks `%ZGST` |
| **`KLPBR`**, **`KLPBRFST`** | cylinder back-pressure over crank angle (and the start-specific ratio) used to get the **differential pressure `dp..._w` across the injector** | |
| **`KFPBRA`** | back-pressure map | |
| `rk..._w`, `ti..._w`, `tv..._w`, `frt..._w`, `dp..._w`, `prist_w` | the working variables | `prist_w` is also handed to the monitoring level |

Two reusable ASCET classes are documented once and instantiated per injection
type: **`FKKVSFUNC`** (time-synchronous part: `KRKATE` × `KLTIKRPR` × `FKKVS`,
plus `tv` from `KLTVTSV`) and **`RK2TI`** (angle-synchronous part:
`rk × frt`, `KLHDEV`, `+ tv`, `TIMINP` clamp). Expect the compiled code to show
one shared helper called from six sites.

### 1.2 Rail pressure, pump volume limit, high-pressure start

| FR module | Pages | Content | Key labels |
|---|---|---|---|
| `HD` | 1625 | overview: `prsoll_w`, `prist_w`, `prroh_w`, `prdiff_w`, `dwmsvs_w` | |
| **`HDRPSOL`** Kraftstoffdruck Sollwert | **1722–1726** | **rail-pressure setpoint** `prsoll_w` | **`KFPRSOL`**, `KFPRSOLHOM`, `KFPRSOLSCH`, `KFPRSOLHMM`, `KFPRSOLKH`, `KFPRSOLHKS`, `KFPRSOLOFF`, `KLPRSOLFAK`, `KLTFPRSO`, `KLPRMAX`, `KLLFPRSG`, `CWPRSOLAP` |
| `HDRPIST` Raildruck-Istwert | 1717–1721 | sensor path `prroh_w → prist_w`, offset adaptation `profadp_w` | `CWDSKVADP`, `SY_DSKVND`, `SY_DSKVADP`, `E_DSKV` |
| `HDR` Hochdruckregelung | 1713–1716 | PI controller, `prdiff_w`, `hdrpp_w`, upper/lower limits `phdria_w`/`phdraa_w` | `CWHDR`, `KLGPHDR`, `FWPHDR`, `KLBIHDR`, `FWIHDR` |
| **`AMSV`** Ansteuerung Mengensteuerventil | **1683–1689** | **MSV (volume control valve) drive — the pump delivery limit lives here** | `CWAMSV`, **`KLNEHDP`** (speed-dependent HDP limit), `KLTAMSVO`, `KFCOMSV`, `KFTAMSV`, `KLVZMSV`, `SY_2HDP2`, `SY_CAMNMSV` |
| `VSTMSV` Vorsteuerung MSV | 1690–1696 | MSV feed-forward `prvst_w`, rail temperature/`kaparail_w` model | `CWVSTMSV`, `KFVSTVG`, `KFVSTVO`, `KLTMVST`, `KLTVDHDP`, `KFKAPAKR`, `KLLFVGAA`, `KFGTSMU`, `KFGTMSU` |
| `BKS` Bedarfsgeregeltes Kraftstoffsystem | 1626–1646 | **low-pressure / demand-controlled lift pump** `pbksoll_w`, `pbkist_w` | `CWBKS`, `CWBKS2`, `KFKNKS`, `KFNTBKS`, `KLKFPSS`, `KLKPBKSR`, `KFPSNS`, `KLFDBKS`, `KLUSKBKS` |
| `AEKP` | 1673–1678 | EKP (in-tank pump) drive | |
| `BBSTHDR` / `DSTHDR` | 1597–1602 / 1603–1605 | high-pressure start conditions and its diagnosis | `KLRSTHDR`, `KLTVRSTHDR`, `KLPROSTHD` |
| `GGDSKV` | 1727 | rail-pressure sensor raw value | |
| `DKVS`, `DKVBDE`, `DDSKV`, `DBKS` | 1740–1790, 1647–1672 | fuel-system diagnostics (these are what will complain first if we change fuelling) | |

**Assumption flag:** all of §1.2 is TFSI-FR naming. The 3.2 FSI uses the same
Hitachi/Bosch single-piston HDP + MSV architecture, so the module set is
expected to be identical, but the maps' dimensions and the `SY_*` switches will
differ.

---

## 2. Lambda setpoint and component protection

| FR module | Pages | Content | Key labels |
|---|---|---|---|
| **`LAMSOLL`** Lambdasoll-Vorgabe | **1535–1536** | the final `lamsbg_w` / `lamsbg2_w` (per bank) and all the requesters feeding it: `lambas_w`, `lamnswl_w`, `lambts_w`, `lamkh_w`, `lamka_w`, `lamdeno_w`, `lamdiag_w`, `lamdkt_w`, `lamelsh_w`, `lamlash_w` | `KFFHOTMKO`, `KLRKAKORR`, `KLNSWLKORR`, `KLLAMKORR` |
| `LAMKO` Lambdakoordination | 2582–2587 | arbitration between those requests | `CWLAMKO`, `SY_STERVK`, `SY_STERHK` |
| `LAMKOD` | 2588–2590 | lambda coordination for diagnostic interventions | |
| **`LAMBTS`** Lambda **Bauteileschutz** | **2572–2581** | **component protection enrichment** — the enrichment that fights any lean flex-fuel strategy | **`KFLBTS`**, `KFFDLBTS`, `KFTVLAMBTS`, `KFTVLBTS`, `KFLBTSLBKO`, `KFTVL`, `CWLAMBTS`; variables `lambts_w`, `dlambts_w`, `lambs_w`, `frlmxbts_w` |
| `ATM` / `ATMHEX` / `KTMHK` / `ATR` / `BTKAT` | 2259–2293, 2377, 2562, 2561 | exhaust-gas and catalyst temperature models that drive `LAMBTS` | |
| `LAKH` / `BAKH` / `BBKH` / `KOMRH` | 2591–2643 | lambda while cat-heating | |
| `LAMSDNE` | 1542–1543 | lambda after DeNOx | |
| `LASO2SV`, `LSU2SV`, `LOCOS2SV` | 1537, 1539, 2557 | lambda setpoint / actual value to the OBD tester | |

---

## 3. Lambda control and adaptation (`fr`, `fra`, `frm`, `rka`)

| FR module | Pages | Content | Key labels |
|---|---|---|---|
| `GKRA` | 2177–2178 | overview of control + adaptation; names the variables: `fr_w` (closed-loop factor), **`fra_w`** (additive adaptation), **`frm_w`** (multiplicative adaptation), **`rka_w`**, `ora_w`, `rkte_w`, `dlahk_w`, `dlaka_w` | |
| `GKEB` | 2176 | overview of the enable conditions | |
| **`LRS`** stetige Lambdaregelung | **2901–2927** | the continuous (PI) controller: `fr_w`, `frini_w`, `ladiff_w`, `lrsp1..4_w`, `lrsg1..4_w` | `KFLRSP1…4`, `KFLRSG1…4`, `KFLRSP12…42`, `KFLRSG12…42` (bank 2), `KFLRST`, `KFLRSZ`, `KFDLASO`, `KFDLASO2`, `KFFRMIN`, `KFLRSPHI`, `CWLRMS`, `SY_LR2PAR` |
| `LRSEB` | 2892–2900 | enable conditions for `LRS` | `CWLRSMOD`, `SY_SLS`, `SY_LREBPS` |
| **`LRA`** adaptive Vorsteuerung | **2195–2212** | **mixture adaptation** — `fra_w` (additive, idle/low load), `frm_w` (multiplicative), `frat_w`, `ora_w`, `oratlp_w`, `fratlp_w`, `ftklra_w`, `rkg_w`, `rka_w` | **`KFRA`**, **`KFRAT`**, `KFFRAT`, `CWLRA`, `CWGALSV`, `E_LSV` |
| `LRAEB` | 2179–2194 | adaptation enable windows (load/speed/temperature) | `CWDKVSTAB`, `SY_DEGFE` |
| `LRAPHU` | 2215–2221 | physical urgency of the adaptation | |
| `LRSHKC` / `LRSHKOUT` / `LRHKEB` | 2717–2739, 2689 | post-cat trim controller | |
| `LRSKA` | 2644–2658 | cat clean-out | |
| `BGLASO` | 2944–2958 | `lamsons_w` — lambda setpoint seen by the controller, and reciprocal lambda | |
| `BGLAMBDA` / `GGO2LSU` / `SALSU` / `DSALSU` | 2940, 2959, 2974, 2982 | LSU wide-band signal path and over-run calibration | |
| `HRLSU` / `ALSU` / `DHRLSU` / `RPSLSU` | 2879, 2852, 2985, 2957 | LSU heater and pump-current control | |
| `ESPLANT` | 2937–2939 | plant parameters of the lambda loop | |

**Naming note:** the brief's `frau` / `frao` (the ME7 adaptation limits) appear
only once in this FR, in `DEGFE` p852 — the MED9 equivalents are the limit
variables inside `LRA`/`LRAEB`. `rkat` likewise is an ME7-ism; the MED9 name is
`rka_w` (`GKRA` p2177, `LRA` p2205).

---

## 4. Ignition

| FR module | Pages | Content | Key labels |
|---|---|---|---|
| `ZUE` Grundfunktion Zündung | 3073–3076 | overview; per-cylinder arrays `zwbasar`, `zwselar`, `zwlimar`, `zwsolar` | `SY_ZZBANK`, `SY_ZYLOFFH`, `SYNTIZW` |
| **`ZWGRU`** Grundzündwinkel | **3085–3094** | **the base ignition map(s)** | **`KFZW`**, **`KFZW2`**, `KFZWOUT`, `KFZW2OUT`, `KFZWLB1`, `KFZWLB2`, `KFZWLB1OUT`, `KFZWLB2OUT`, `KFDZWKG`, `KFDZWKGAGL`, `KFDZWKGAGR`, `KFDZWGVS`, `KFDZWGVS2`, `KFDZWHSP`, `KFDZWHKS`, `KFDZWHMM`, `KFDZWHMML`, `DZWRAMPAGR` |
| `ZWBAS` | 3081–3084 | builds `zwbasar` from `zwgru`, `dzwwl`, `zwstt`, knock retard `dwkrz`/`wkrdyv` | `KFZWWLRL`, `KFZWWLNM` |
| `ZWMIN` | 3095–3109 | latest permitted ignition angle | `KFZWMN`, `KFZWMNUM`, `KFZWMS`, `KFZWMNLB`, `KFZWMNKH`, `KFZWMNST`, `KFZWMNHSP`, `KFZWMNGS`, `KFZWMSLB`, `KFDZWSPM`, `KLFZWMNKH` |
| `ZWSEL` / `ZWOUT` | 3111–3121 | early/late limiting, output angle `zwist` | `DZWOL`, `DZWOLA` |
| `ZWSTT` | 3077–3078 | ignition during cranking | `KFZWSTTM`, `KFZWSTTMHD`, `KFZWSTZT`, `DZWSTTA` |
| `ZWWL` | 3079–3080 | warm-up ignition `dzwwl` | `CWZWWLE`, `KFZWWLNM`, `KFZWWLRL` |
| `ZWHMM` | 3110 | delta ignition vs. lambda in BDE | |
| `ZUESZ` | 3124–3134 | dwell time | `KFTSRL`, `KFTSRKM`, `KFSZDUB`, `TSMX`, `TSMNSA`, `TSMXNL`, `KFZWSCH`, `KFDZWSCH`, `KFWDZWSCH`, `KFDZWBS`, `CW_SZTRL` |
| `HT2KTIGNI`, `HT2KTCK110`, `DZUEET` | 3135–3160, 3151, 3153 | ignition driver + diagnosis | |
| `MDZW`, `KFZWOP` | 768–774 (`MDZW`), and `MDBAS` 729–740 / `MDIST` 741–745 | **`KFZWOP`, the optimum-ignition map, lives in the torque structure**, not in `ZWGRU` — it is the reference for efficiency `etazwist`/`etazwg` | |

### 4.1 Knock control

| FR module | Pages | Content | Key labels |
|---|---|---|---|
| `KRKE` Klopferkennung | 3191–3202 | detection: `ikr_w`, `rkr_w`, reference level, integrator windows | `KFKE0…KFKE7`, `KFKRINT1G…3G`, `KFKRINTG1…G3`, `KFFTPKR`, `CWKRINT`, `CWKRVKR`, `CWKRREF`, `CWREFI` |
| **`KRREG`** Stationärregelung | **3203–3211** | **`dwkrz` — the cylinder-individual knock retard** (also `wkrv`, `wkrmv`, `wkrav`, `zkrvf`) | `KFTKRVF`, `KFTKRVFN`, `KFTKRVFSN`, `KFKRFKN`, `STKRA`, `SY_KRLZ` |
| `KRADAP` | 3212–3214 | stationary adaptation `wkra`, `stkrax_w` | |
| `KRDY` | 3215–3219 | dynamic (load-step) retard `wkrdya`, `wkrdyv`, `zldy_w` | `KFDYMNTVS`, `KFDYMNTS`, `DZWTIN` |
| `BBKR` | 3161–3175 | knock-control operating conditions | `CWKR`, `CWKRNLR`, `CWKRLDY`, `CWTIPIN`, `KFDYESPF` |
| `GGKR` | 3176–3190 | knock sensor signal (CC196 evaluation IC) | |
| `DKRA`, `DKRIC`, `DKRS`, `DKRSPI` | 3220–3245 | knock diagnostics | |

**Directly relevant to flex fuel:** `dwkrz` / `wkrav` / `stkrax_w` are the
measurable proof that E85 has removed knock; `KRZFKT` (p3220) holds the special
add-ons.

---

## 5. Start, after-start and warm-up

| FR module | Pages | Content | Key labels |
|---|---|---|---|
| `BBSTT` Betriebsbereich Start | 1583–1587 | `tnst_w`, `tnse_w`, start→run transition | `KFKATI`, `KFKHOATI`, `KLRLSTEND`, `SY_STASTO` |
| **`ESSTT`** Einspritzzeit Start | **1588–1596** | cranking fuel mass `ksta`, `anztist` | **`KFKSTT`**, `KFKSTTHDR`, `KFWKSTT`, `KFWKSTTHDR`, `KFWKSTAB`, `KFWKSTN`, `KFSTHO`, `KLTMOTDIFF` |
| `STADAP` Startmengen-Adaption | 1610–1621 | learns the start quantity — **this is where an ethanol-blind ECU compensates first** | `CWSTADAP`, `KFNSTAMX`, `KLDNFHO` |
| **`ESNSWL`** Nachstart und Warmlauf | **1569–1579** | after-start `fnsk`/warm-up `fwlk` enrichment factors | `KFNSA`, `KFNSWRL`, `KFWNSNW`, `KFWWNS`, `KFFNSHO`, `KFWWLNW`, `KFFWLRL`, `KLZANSFHO`, `KLNSWSTAMX`, `KFWSTAARL`, `CWNSWLMOD` |
| `ESNSWLA` | 1580–1582 | optional adapter variant | `KFNSRLHO`, `KFFWL`, `KFFWLW`, `KFWLFHO`, `KLFNSHO`, `KLWWLFHO`, `KFWWLML`, `KLFWLN` |
| `LANSWL` | 1606–1609 | after-start / warm-up **lambda** `lamns_w`, `lamwl_w` → `lamnswl_w` | `KFLANS`, `KFLASWLR`, `CWWL` |
| `ZWSTT` / `ZWWL` | 3077–3080 | start and warm-up ignition (§4) | |
| `BBSTHDR` | 1597–1602 | high-pressure start enable | |
| `NSPTS` | 447–448 | after-start idle speed | |
| `BGKSTDTA` | 4526–4528 | cold-start detection | |
| `ALE` / `RDE` | 3246–3255, 3256 | run-down / reverse-rotation detection | |

---

## 6. CAN

| FR module | Pages | Content |
|---|---|---|
| **`CAN_CONF`** | 4025–4029 | **channel allocation in the MED9** — which physical TouCAN carries drivetrain / comfort / sensor CAN, gated by `SY_CANGE*`, `SY_CANBR*`, `SY_CANGAT`, `SY_CANDIA1`, `SY_CANACC`, `SY_CANNIV`, `SY_CANZAS`, `SY_CANBEM`, `SY_CANASY`, `SY_CANPB1`, `CWCAUVW` |
| **`CANECU`** | 4030–4086 | **transmit messages and signal definitions** |
| **`CANECUR`** | 4087–4207 | **receive messages**, per-message timeout/plausibility and `can_mirror` |
| `CANSEN` | 4010–4024 | sensor CAN (NOx sensor: `pstnox`, `dstnox`, `teilnrnox`, `hwstdnox`) |
| `CANLIB` | 4208 | shared CAN helper library |
| `GGCANECU` | 4208–4221 | the values the ECU puts into its own messages |
| `GGCASR` | 4222–4244 | ASR/MSR signals |
| `GGCEGS` / `GGCEGSPL` | 4247–4289 | **gearbox message evaluation and plausibility** (`migs_w`, `miges_w`, `statgesc`) |
| `GGCINS` | 4290 | **Kombi** messages (`vfzgkb_w` — vehicle speed from the cluster) |
| `GGCKLA` / `GGCLWS` / `GGCGRA` / `GGCS` | 4008, 4009, 4005, 4291 | climate, steering-angle sensor, GRA lever, crash sensor |
| `GGCTOL` / `GGCTUM` | 3614, 3630 | oil / ambient temperature over CAN |
| `WFSIF` / `WFSCOM` | 4300 | immobiliser interface |

### 6.1 Message enable code words — confirmed structure

`CW_CAN_S` (p4031, p4088) is a 16-bit transmit-enable word, one bit per
message:

| bit | message | bit | message |
|---|---|---|---|
| 0 | Motor 1 | 8 | Motor_Nox (`%CANSEN`) |
| 1 | Motor 2 | 9 | Motorslave_Istverbau 10 ms (2-ECU) |
| 2 | Motor 3 | 10 | Motorslave_Istverbau 100 ms (2-ECU) |
| 3 | GRA_neu | 11 | Motor_Bremse |
| 4 | Motor 5 | 12 | Motor_8 |
| 5 | Motor 6 | 13 | ACC_GRA_Anzeige |
| 6 | Motor 7 | 14 | ACC_1 |
| 7 | Motor Flexia | 15 | unused |

`CW_CAN_R` / `CW_CAN_RA` / `CW_CAN_RB` enable receive messages, `CW_CAN_C`
enables coding-error monitoring per received message (bit 0 Getriebe 1,
bit 2 Bremse 1, bit 3 Airbag, bit 6 Allrad, bit 7 Niveau).

Message names used in the FR: `Motor_1…Motor_8`, `Motor_Bremse`, `Motor_Nox`,
`Bremse_1…Bremse_8`, `Getriebe_1…Getriebe_6`, `Kombi_1…Kombi_3`, `Klima_1`,
`ACC_1`, `ACC_GRA_Anzeige`, `LWS_1`, `Niveau_1`, `Gateway_Komfort_1`,
`Diagnose_1`.

### 6.2 The FR does **not** contain the numeric CAN identifiers

Searched: none of our dump's 30 transmit/receive identifiers appears anywhere
in the 4,860 pages. The FR describes messages symbolically; the numeric
assignment comes from the VW CAN matrix and from the calibration. So:

* **From the dump (VERIFIED-STATIC):** the identifiers and DLCs themselves
  (`docs/02` §7).
* **Correspondence to names (HYPOTHESIS, for brief B2 to settle):** our
  transmit table has exactly 16 entries, matching `CW_CAN_S`'s 16 bits, and if
  the first entry (0x7C7) is the diagnostic/TP frame then entries 2–7 are
  0x280, 0x288, 0x380, 0x480, 0x488, 0x580 — which is the classic VW PQ35
  Motor_1/2/3/5/6/7 assignment, with `CW_CAN_S` bit 3 (GRA_neu) not sent.
  Verify by reading which table index each `CW_CAN_S` bit gates, not by
  assuming the order.

---

## 7. Tester communication (KWP2000 and OBD) — best-confirmed area

`T2SERV` (p4331) is literally the **service distributor / list of supported
services**, i.e. the FR counterpart of our dispatch table at file 0x2B870.
Mapping our 24 table entries to FR modules:

| SID in our table | Service | FR module | Pages |
|---|---|---|---|
| 0x10 | StartDiagnosticSession | `T2SDM` (+ `T2STC`) | 4330, 4334 |
| 0x14 | ClearDiagnosticInformation | `T2FCMD` | 4314 |
| 0x17 | ReadStatusOfDTC | `T2RSDTC` | 4325–4326 |
| 0x18 | ReadDTCByStatus | `T2DTCS` | 4311–4312 |
| 0x20 | StopDiagnosticSession | `T2EDS` / `T2EDSA` | 4313, 4314 |
| 0x21 | ReadDataByLocalIdentifier | `T2KRLI` / `T2RLID` / `T2LID` | 4319, 4324, 4320–4323 |
| **0x27** | **SecurityAccess** | **`T2SAC`** | **4328–4329** (`KLOGIN`, `requestSeed`/`sendKey`, `accessMode`) |
| **0x2C** | **DynamicallyDefineLocalIdentifier** | **`T2DDLI`** | **4310** (`definitionMode`, `memoryAddress`, `memorySize`, `SY_TKDLIMA`) |
| 0x31 | StartRoutineByLocalIdentifier | `T2STRL` | 4334–4344 |
| 0x32 | StopRoutineByLocalIdentifier | `T2SPRL` | 4332–4333 |
| **0x35** | **RequestUpload** | **`T2RU`** | **4327** |
| 0x36 | TransferData | `T2TD` | 4345 |
| 0x37 | RequestTransferExit | `T2RTE` | 4327 |
| 0x3B | WriteDataByLocalIdentifier | `T2WLID` | 4346 |
| 0x81 | StartCommunication | `T2STC` | 4334 |
| 0x82 | StopCommunication | `T2END` | 4314–4315 |
| OBD 0x01 | Mode 1 current data | `TC1MOD` | 4346–4366 |
| OBD 0x02 | Mode 2 freeze frame | `TC2MOD` | 4367–4371 |
| OBD 0x03 | Mode 3 stored DTCs | `TC3MOD` | 4372 |
| OBD 0x04 | Mode 4 clear | `TC4MOD` | 4373 |
| OBD 0x06 | Mode 6 test results | `TC6MOD` / `TC6CMOD` / `TC6MODC` | 4389–4413, 4385, 4414–4417 |
| OBD 0x07 | Mode 7 pending DTCs | `TC7MOD` | 4418 |
| OBD 0x08 | Mode 8 on-board tests | `TC8MOD` | 4419–4422 |
| OBD 0x09 | Mode 9 vehicle info | `TC9MOD` / `TC9CON` | 4425–4438, 4423 |

Also present in the FR but **not** in our dispatch table: `T2TP` Tester Present
(p4345), `T2ATP` access timing (p4308), `T2ID` Read ECU Information (p4315–4318),
`T2REI` Read ECU Identification (p4324), `T2RFFD` Read Freeze Frame Data
(p4546–4860 — a huge table of every freeze-frame variable, useful as a label
dictionary), `TC5MOD` Mode 5 (p4374–4384; superseded by Mode 6 on CAN),
`TCKOMUE` communication overview (p4305), `TCSORT` (p4439).

### 7.1 Measuring blocks and actuator tests (for brief A3)

| FR module | Pages | Content |
|---|---|---|
| **`TKMWL`** Meßwerte lesen | **4451–4515** | **the measuring-block (Meßwerteblock) definitions** — 65 pages listing block number, position, variable, scaling and unit. This is the document for `docs/02` §7's candidate table at 0xA5654. |
| `TKSTA` Stellgliedansteuerung | 4516–4521 | actuator tests, `CWSTAKDA` and the `SY_*` list of which actuators exist |
| `TKAP` Anpassungskanäle | 4440–4442 | adaptation channels, `CWTAF`/`CWTAK`/`CWTAS`, `KLOGIN`, and the `SY_T*` switches for tester-writable calibration (`SY_TFNS`, `SY_TFWL`, `SY_TFST`, `SY_TDZW`, `SY_TFRK`, `SY_TLR`, `SY_TRLX`, `SY_TMDR` …) |
| `TKDFA` Diagnosefunktion aktivieren | 4443–4450 | `CWFA28`…`CWFA130` — the numbered "diagnostic function" activations |
| `TKSWL` System-Werte Lesen | 4522–4523 | system values (immobiliser / SG identification) |
| `DSCHED` Diagnose-Scheduler | 3717–3725 | which diagnosis runs when |
| `DSM`, `DVAL`, `DFPM*` | 3726–3793 | diagnostic system manager, validator, fault-path manager |

`SY_TFRK` ("Tester-Freigabe relative Kraftstoffmasse") and `SY_TLR` are the
switches that make `rk` and the lambda controller writable over the tester —
directly interesting for bench testing a flex-fuel patch.

---

## 8. EEPROM

| FR module | Pages | Content |
|---|---|---|
| **`EEP_CONF`** EEPROM-Layout | **4529–4545** | the full block map |
| `EEPINIKW` | 106 | initialisation with key values (`eepinikw_tester`, `eepinikw_calib`, `eepinikw_sgid`, `csvklcw_w`) |
| `DSGEEP` | 107–108 | EEPROM plausibility diagnosis (`dsgeepctr`) |
| `DFPMEEP` | 3783 | fault-path storage in EEPROM |
| `IUMPREE` | 3796 | IUMPR counters in EEPROM |

Layout facts (p4529–4545) worth having before B4 opens the EEPROM:

* Organised in **32-byte blocks** (page size 32 = 0x20), addressed by block
  number; the FR tabulates `Addr [dez] / Addr [hex] / Page-Nr / Block-Nr /
  Blockname / Blockinhalt / Res. [Byte] / BVN / CallBack / InitType` plus the
  flags `InitV`, `ReplV`, `InMIR`, `CSMIR`, `exRead`, `exWrite`, `InEqRep`,
  `After`.
* Most blocks are duplicated ("Doppel von Block *n*") and carry a **per-block
  checksum**; `InitType` is typically `COPY_BL`.
* Block 0 `BlockFD` "Werksdaten" occupies 0x0000–0x003F and contains
  `dProductProgress_u8` (5 B), `dTswContVer_u16`, `dProdDateOne_u8` (8 B),
  `dCsPdOne_u8`, `dTswContVerCp_u16`, `dProdDateTwo_u8` (8 B),
  `numSerial_u8` (12 B), `numDswCont_u8` (10 B), `dHwLevel_u8`, `dLpLevel_u8`,
  `stFswMarker_u8` (2 B), `dReserved_u8` (10 B) and **`eepCheckSum` (2 B) at
  0x003E**.
* Named blocks further up include `BlockIUMPR` (0x0400), `BlockWVD`
  (Winkelversatzdiagnose, 0x0600), `BlockDFPMEEP5` (0x0620) and a run of free
  blocks `Block32…Block36` (0x0720 onward) — **candidate storage for a
  flex-fuel ethanol-content memory**, if we ever need non-volatile state.

**Assumption flag:** the block *numbers and addresses* are project-specific and
almost certainly differ in `D9133_43K6P0`. Only the *structure* (32-byte
blocks, duplicate + checksum, `COPY_BL`) should be carried over.

---

## 9. Scheduler / task structure

| FR module | Pages | Content |
|---|---|---|
| **`SYSYNC`** System-Synchronisation | **3274–3276** | the task model |
| `SYSCON` | 109–112 | ECU system states (KL15/KL30, `nachlauf`, `pwf`, `wub`) |
| `BBSYSCON` / `BBSYSREQ` | 132–147 | state transition conditions |
| `BBRCVRY` | 127–131 | system recovery after reset |
| `BBHWONOF` | 113–122 | hardware start-up / shutdown |
| `SYSKON` | 82–91 | the `SY_*` system constants (compile-time switches) |
| `KONCW` | 92–103 | the `CW*` code words (calibration-time configuration) |
| `BISYNC` / `SSTNW` / `HT2KTWNE` / `HT2KTPH` | 3315, 1432, 3394, 3317 | crank/cam angle acquisition |

From `SYSYNC` p3274, the rasters are:

1. **PreDrive 10 ms** (`sysync_10msPreDrive`, only if `SY_PREDRV > 0`)
2. **Drive 10 ms** (`sysync_10ms`)
3. **Synchro raster** (`synchroIrqHandler`, `synchro2IrqHandler`), crank-angle
   synchronous, with `syn` and `syns` sub-rasters

and the OSEK tasks `Task_inisyn`, `Task_firstsyn`, `Task_resetsyn` are activated
from a state machine with the states `WAIT_SYNC → NMOTNORMAL → STALLED`
(`drrev_sta`, `synstate`, `sync_level`). Expect the same three-level structure
(time raster, crank-angle raster, background) in our binary; B1 should look for
a 10 ms timer interrupt and a crank/cam interrupt reaching the handler at
CPU 0x80028 (`docs/02` and `re/findings/mpc5xx_registers.md` §5).

---

## 10. Variant coding

| FR module | Pages | Content |
|---|---|---|
| **`VARLC`** Variantencodierung Langes Codierwort (VW-Welt) | **38–66** | the long coding word |
| `DVARLC` | 68–81 | its diagnosis, `csvklcw_w`, `vkVariables`, `tLaCodWo`, `bloknr` |
| `VARLCUW` | 67 | monitoring-relevant parameters switched by the coding word (`*_0_A`, `*_1_A` … map variants) |
| `UFVARC` | 3946–3948 | coding check in the monitoring level |
| `KONCW` | 92–103 | code words (`CWKONFZ1`, `CWOBD`, `CWKLIMA`, `CWBDE1`, `CWAGR`, `CWTF`, …) |
| `SYSKON` | 82–91 | system constants |
| `ECUDEV` | 151 | detection of application (development) ECUs |

**`vkKraQu` does not exist in this FR edition.** The variant criteria are named
`vk*` and the full set in `VARLC` p38–66 is:

`vkADR, vkAbgVar, vkAbsMkt, vkAnhSt, vkAsrEsp, vkCAN, vkDaDrKr, vkELuef,
vkElZWP, vkFrQtro, vkFzgKl, vkFzgTyp, vkGangSt, vkGeArt1…4, vkIndex, vkKlima,
vkKraSt, vkLueftAk, vkMarke, vkNiveau, vkParFil, vkPedCh, vkVoNaGe, vkXyz`

The fuel-related one is **`vkKraSt`** (Kraftstoffart/-sorte). `VARLCUW` p67
shows the mechanism we would need for a fuel-dependent calibration set: a
criterion selects between parallel labels `KFxxx_0_A`, `KFxxx_1_A`, … at run
time. If our dataset implements that, a flex-fuel switch could reuse it rather
than patching code — worth checking in our A2L/calibration (open question).

---

## 11. ROM / RAM checks and the monitoring level

| FR module | Pages | Content |
|---|---|---|
| `URROM` | 3973–3974 | **ROM test** — `SY_MOROM`, `SY_ADRLAY`, `CW_NOROMDATACHKRESET`, `romrstc_um`; blockwise, faults escalate to shutdown |
| `URRAM` | 3971–3972 | **RAM test** — ascending pattern, own + inverted word, internal and external RAM, `e_ram_um` |
| `URMEM` | 3965–3968 | cyclic memory test that ties `URROM`/`URRAM` to the monitoring module, `E_MEM_UM` |
| `URPAK` | 3969–3970 | program-flow control (running checksum over executed blocks) |
| `URCPU` | 3962–3964 | instruction test against level 2' |
| `URADCC` | 3957–3961 | A/D converter test |
| `URTPU` | 3975–3983 | TPU monitoring |
| `UMKOM` | 3988–3992 | question/answer communication between the monitoring module and the main CPU |
| `UMAUSC` | 3984–3987 | shutdown-path test |
| `UFUE` | 3827 | overview of the EGAS level-2 function monitoring; `UFRKTI` p3922–3928 and `UFRKC` p3913–3921 check fuel mass and lambda, `UFZWC` p3949–3952 checks ignition |
| `DUR` / `DUF` | 3953–3956, 3828–3842 | faults out of the computer / function monitoring |

Our dump's 65 block checksums and the runtime descriptor lists at file 0x1E73C,
0x829A0 and 0xA3A18 (`docs/02` §6) are the `URROM` implementation. **Any patch
must keep them consistent — `tools/checksum.py fix`.**

The level-2 monitoring (`UFRKTI`, `UFRKC`, `UFMVER`, `UFZWC`) is the reason a
flex-fuel patch cannot simply scale `ti`: `%UFRKTI` re-computes an allowed fuel
mass independently and `%UFRKC` compares target and actual lambda. Brief B6 has
to decide whether to feed the patch in *before* the monitoring taps (i.e. change
`rk`, not `ti`) — the FR page to read first is `UFRKTI` p3922.

---

## 12. Quick lookup: FR section → page

The full 599-entry table of contents is on FR pages 2–29 (`pdftotext -f 2 -l 29
-raw`). Sections referenced above, sorted:

`AES` 1789, `AEKP` 1673, `AMSV` 1683, `AWEA` 1800, `BBKR` 3161, `BBSTHDR` 1597,
`BBSTT` 1583, `BKS` 1626, `CANECU` 4030, `CANECUR` 4087, `CANLIB` 4208,
`CANSEN` 4010, `CAN_CONF` 4025, `DSGEEP` 107, `DSCHED` 3717, `EAKO` 1544,
`EEPINIKW` 106, `EEP_CONF` 4529, `ESAUSG` 1812, `ESGRU` 1555, `ESNSWL` 1569,
`ESNSWLA` 1580, `ESSTT` 1588, `ESUK` 1562, `ESVST` 1552, `ESWE` 1553,
`GGCANECU` 4208, `GGCEGS` 4247, `GGCINS` 4290, `GK` 1556, `GKEB` 2176,
`GKRA` 2177, `HD` 1625, `HDR` 1713, `HDRPIST` 1717, `HDRPSOL` 1722,
`KONCW` 92, `KRADAP` 3212, `KRDY` 3215, `KRKE` 3191, `KRREG` 3203,
`KT_ES` 1820, `LAMBTS` 2572, `LAMKO` 2582, `LAMSOLL` 1535, `LANSWL` 1606,
`LRA` 2195, `LRAEB` 2179, `LRS` 2901, `LRSEB` 2892, `MDBAS` 729, `MDZW` 768,
`RKSPLIT` 1838, `RKTI` 1826, `STADAP` 1610, `SYSCON` 109, `SYSKON` 82,
`SYSYNC` 3274, `T2DDLI` 4310, `T2LID` 4320, `T2RU` 4327, `T2SAC` 4328,
`T2SERV` 4331, `T2TD` 4345, `TKAP` 4440, `TKDFA` 4443, `TKMWL` 4451,
`TKSTA` 4516, `TKSWL` 4522, `URMEM` 3965, `URPAK` 3969, `URRAM` 3971,
`URROM` 3973, `VARLC` 38, `VARLCUW` 67, `VSTMSV` 1690, `ZGST` 1843,
`ZUE` 3073, `ZUESZ` 3124, `ZWBAS` 3081, `ZWGRU` 3085, `ZWMIN` 3095,
`ZWOUT` 3117, `ZWSEL` 3111, `ZWSTT` 3077, `ZWWL` 3079.
