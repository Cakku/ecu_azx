# Hardware desk preparation — spare ECU, BDM backup, bench harness, Windows box

Agent brief: `docs/agent_briefs/A4_hardware_desk_prep.md` (issues #1, #2, #3, #4).
Research only — nothing was bought, nothing was connected to an ECU, the dump
was not touched (`tools/checksum.py verify -q data/passat_azx_ori.bin` →
`ALL OK (65 blocks)` before and after; SHA-256 unchanged).
Date: 2026-09-15.

## How to read this document

Every claim carries a project status tag (`docs/README.md`) **and** a
confidence word:

| Confidence | Meaning |
|---|---|
| **high** | Manufacturer/OEM document, or several independent sources agree |
| **medium** | One good source, or several vendor pages that copy each other |
| **low** | Single forum post, or inference; treat as a lead, not a fact |

Anything from a forum is marked *(forum)*. Where sources disagree, the
disagreement is written out instead of being resolved silently.

**Our unit, for reference** (VERIFIED-STATIC, from `docs/02_memory_map.md` §1):
VW `03H906032` (no suffix), engine label `P3.2 FSI-EU4`, Bosch `0261S02226`,
Bosch software `1037382557`, project string
`56/1/MED91/3/6W6432.D//D9133_43K6P0/D9133_43K6P0/080506/`, extra version
field `9387`. Car: 2007 Passat B6 3.2 FSI 4Motion, engine AXZ.

---

# 1. Spare ECU compatibility (issue #1)

## 1.1 The part-number family

`03H906032` is the VW hardware number of the whole Bosch MED9.1 family for the
VR6 FSI engines (3.2 AXZ and 3.6 BLV/BWS/BHK), used across Passat B6/CC,
Touareg, Phaeton, Eos, Audi Q7 and Porsche Cayenne 3.6. There are well over
100 suffixes. The letter suffix changes with market, emissions level, gearbox
and software revision; the **Bosch number `0261S02xxx` is the more reliable
identifier**, and the **Bosch software number `1037xxxxxx` is the only thing
that pins down the actual binary.**

Suffix → Bosch number table collected from vendor catalogues. Status
**COMMUNITY**, confidence **medium** for the number pairs, **low** for the
"engine" column (see the conflict in §1.6).

| Suffix | Bosch no. | Engine/vehicle as listed by the vendor | Source |
|---|---|---|---|
| *(none)* | 0261S02226 | Passat 3.2 FSI | [cartechelectronics.com](https://www.cartechelectronics.com/products/plug-play-bosch-engine-ecu-vw-passat-3-2-fsi-0261s02226-0-261-s02-226-03h906032-03h-906-032-med9-1) |
| A | 0261S02227 | — | [cartech search p2](https://www.cartechelectronics.com/search?q=03H906032&type=product&options%5Bprefix%5D=last&page=2) |
| B | 0261S02168 | Passat 3.6 FSI | [cartech](https://www.cartechelectronics.com/products/plug-play-bosch-engine-ecu-vw-passat-3-6-fsi-0261s02168-0-261-s02-168-03h906032b-03h-906-032-b-med9-1-1039s14236) |
| C | 0261S02226 *(see §1.6)* | "Passat 3.2 FSI" per Cartech; **"3.6 LITER" per VW's own catalogue** | [cartech](https://www.cartechelectronics.com/plug-play-bosch-engine-ecu-vw-passat-3-2-fsi-0261s02226-0-261-s02-226-03h906032c-03h-906-032-c-med9-1/), [parts.vw.com](https://parts.vw.com/p/Volkswagen__/Engine-Control-Module-ECM/47990925/03H906032C.html) |
| H, L | 0261S02193 | — | cartech search p2 |
| S | 0261S02355 | Touareg 3.6 FSI | [cartech](https://www.cartechelectronics.com/products/plug-play-bosch-engine-ecu-vw-touareg-3-6-fsi-0261s02355-0-261-s02-355-03h906032s-03h-906-032-s-med9-1) |
| T | 0261S02354 | Touareg 3.6 FSI | cartech search p2 |
| AB | 0261S02349 | **Passat B6 3.2 FSI V6** | [cartech](https://www.cartechelectronics.com/plug-play-bosch-engine-ecu-vw-passat-3-2-fsi-0261s02349-0-261-s02-349-03h906032ab-03h-906-032-ab-med9-1/), [obdtotal](https://obdtotal.com/product/vw-passat-b6-3-2-fsi-v6-bosch-med9-1-03h906032ab-0261s02349-1037384761-0764-ecu-stock-firmware/) |
| AC | 0261S02350 | Passat 3.2 FSI | [cartech](https://www.cartechelectronics.com/plug-play-bosch-engine-ecu-vw-passat-3-2-fsi-0261s02350-0-261-s02-350-03h906032ac-03h-906-032-ac-med9-1/) |
| AD | 0261S02352 | Passat 3.6 FSI | [cartech](https://www.cartechelectronics.com/products/plug-play-bosch-engine-ecu-vw-passat-3-6-fsi-0261s02352-0-261-s02-352-03h906032ad-03h-906-032-ad-med9-1) |
| AM | 0261S02356 | Passat 3.6 FSI | [cartech](https://www.cartechelectronics.es/products/plug-play-bosch-engine-ecu-vw-passat-3-6-fsi-0261s02356-0-261-s02-356-03h906032am-03h-906-032-am-med9-1/) |
| AN | 0261S02454 / 0261S02460 *(two vendor entries disagree)* | — | cartech search |
| AP, AQ, AR | 0261S02423 / 0261S02424 / 0261S02459 | AR = Touareg 3.6 FSI | cartech search |
| BF | 0261S02436 | Audi Q7 3.6 FSI | cartech search |
| BR | 0261S02563 | Passat 3.6 FSI | [cartech](https://www.cartechelectronics.com/products/plug-play-bosch-engine-ecu-vw-passat-3-6-fsi-0261s02563-0-261-s02-563-03h906032br-03h-906-032-br-med9-1) |
| BS | 0261S02564 | Passat 3.6 FSI (VW: "3.6 LITER") | cartech, [parts.vw.com](https://parts.vw.com/p/Volkswagen__/Engine-Control-Module-ECM/48001108/03H906032BS.html) |
| CA, CL | 0261S02529 / 0261S02528 | Audi Q7 3.6 FSI | cartech search |
| CB, CC, CR | 0261S02585 / 0261S02584 / 0261S02586 | Touareg 3.6 FSI | cartech search |
| CD | 0261S02525 | — | [gowtuning](https://gowtuning.com/ecu/bosch/med9-1/p57846-03h906032cd-0261s02525/) |
| CF | 0261S02532 | — | cartech search p2 |
| DC, DE, DH | 0261S02592 / 0261S02599 / 0261S02593 | — | cartech search |
| DN | 0261S02616 | Passat 3.6 FSI | [cartech](https://www.cartechelectronics.com/plug-play-bosch-engine-ecu-vw-passat-3-6-fsi-0261s02616-0-261-s02-616-03h906032dn-03h-906-032-dn-med9-1/) |
| DQ | 0261S02625 | Passat/CC 3.6 FSI (BWS, automatic) | [cartech](https://www.cartechelectronics.com/plug-play-bosch-engine-ecu-vw-passat-3-6-fsi-0261s02625-0-261-s02-625-03h906032dq-03h-906-032-dq-med9-1/) |
| EA, EB, EE | 0261S02660 / 0261S02661 / 0261S02664 | Phaeton 3.6 | cartech search |
| EK, EL, EM | 0261S02716 / 0261S02756 / 0261S02696 | Eos 3.6 | cartech search |
| EP | 0261S02679 | Passat 3.2 FSI | [cartech](https://www.cartechelectronics.com/products/plug-play-bosch-engine-ecu-vw-passat-3-2-fsi-0261s02679-0-261-s02-679-03h906032ep-03h-906-032-ep-med9-1) |
| FR, FS | 0261S02712 / 0261S02713 | Passat 3.6 FSI (FS: SW 1039S34835) | [cartech](https://www.cartechelectronics.com/plug-play-bosch-engine-ecu-vw-passat-3-6-fsi-0261s02712-0-261-s02-712-03h906032fr-03h-906-032-fr-med9-1/) |
| HB | 0261S02751 | Passat 3.6 FSI per Cartech; **"3.2 FSI AXZ" per a forum post** | [cartech](https://www.cartechelectronics.com/products/plug-play-bosch-engine-ecu-vw-passat-3-6-fsi-0261s02751-0-261-s02-751-03h906032hb-03h-906-032-hb-med9-1), [nefariousmotorsports 11645](http://nefariousmotorsports.com/forum/index.php?topic=11645.0) *(forum)* |
| HD | 0261S02784 | Phaeton 3.6 | cartech search |

Cartech's search reports "106 results" for this hardware number; the table
above is what could be read off the first pages
([search URL](https://www.cartechelectronics.com/search?q=03H906032&type=product&options%5Bprefix%5D=last)).

### Software numbers observed on this hardware (COMMUNITY, medium)

| VW part | Bosch no. | Bosch SW | 4-digit version | Size quoted | Source |
|---|---|---|---|---|---|
| 03H906032 | 0261S02226 | **1037382557** | 9387 | 2,605,056 (our KESS read) | our dump — VERIFIED-STATIC |
| 03H906032 | 0261S02226 | 1037394466 | 9951 | 2,621,440 | [dyno-chiptuningfiles](https://www.dyno-chiptuningfiles.com/original-ecu-files-database/bosch-med9-480090/), [ecubin](https://www.ecubin.com/original-ecu-files-hw-03h906032-9951-0261s02226) |
| 03H906032AB | 0261S02349 | 1037384761 | 0764 | 2,097,152 (external flash only) | [obdtotal](https://obdtotal.com/product/vw-passat-b6-3-2-fsi-v6-bosch-med9-1-03h906032ab-0261s02349-1037384761-0764-ecu-stock-firmware/) |
| 03H906032AB | 0261S02349 | 1037387767 / 1037396864 | — | — | [ziptuning](https://www.ziptuning.com/ecu-tuning-file/bosch-med9-1-03h906032ab-0261s02349-396864-ecu-tuning-files/) |
| 03H906032S | 0261S02355 | 1037383785 (+1037390480) | — | — | [automoto-firmware](https://automoto-firmware.com/index.php?a=downloads&b=tags&tag=MED9.1) |
| 03H906032AM | 0261S02356 | 1037393968 | — | — | [ziptuning](https://www.ziptuning.com/ecu-tuning-file/bosch-med9-1-03h906032am-0261s02356-393968-ecu-tuning-files/) |

Two useful consequences:

1. **A later factory software exists for our exact hardware** (1037394466,
   version 9951, on 0261S02226). A spare carrying it is still the same board
   but is **not** our binary. COMMUNITY, medium.
2. The dyno-chiptuningfiles entry for our hardware quotes a full file of
   **2,621,440 bytes**. Our KESS read is 2,605,056 bytes, and
   2,621,440 − 2,605,056 = **16,384** — exactly the 0x400000-0x403FFF block
   that `docs/02_memory_map.md` §2 says is missing. Independent arithmetic
   support for the "KESSv2 skips the first 16 KB of on-chip flash"
   hypothesis. COMMUNITY + inference, medium.

## 1.2 Which suffixes are plausible bench substitutes

Ranked, best first:

1. **`03H906032` no suffix, Bosch `0261S02226`, software `1037382557`.**
   Identical unit. Everything transfers byte-for-byte. The Bosch number is
   printed on the ECU label, so it can be checked from a seller's photo.
2. **`03H906032` / `0261S02226` with software `1037394466`.** Same board and
   same Bosch hardware, different code/calibration revision. Perfect for
   harness bring-up, KWP/TP2.0 work, connector and current measurements and
   for rehearsing a flash. Patch *addresses* will not transfer unchanged.
3. **Any 3.2 FSI suffix (AB `0261S02349`, AC `0261S02350`, EP `0261S02679`).**
   Same 6-cylinder board family, same engine, different software. Fine for
   harness and tooling work.
4. **A 3.6 FSI unit (B, AD, AM, DQ, BR, …).** Same MED9.1 6-cylinder hardware,
   different application software and calibration. Usable as a "does my
   harness power up and answer KWP" mule; useless for anything that depends
   on our addresses. Far easier and cheaper to find.

**Do not buy a 4-cylinder MED9.1 (2.0 TFSI, e.g. `06F906056`, `3C0907115F`).**
Same ECU *family*, different board: "another 2 HDEV injectors to fire (so
another 2x drivers and 2x boosters) along with another 2 coils" (gman86) and
"6cyl BDM won't work on 4cyl ECU" (Apsik),
[nefariousmotorsports thread 11645](http://nefariousmotorsports.com/forum/index.php?topic=11645.0)
*(forum)*, COMMUNITY, medium. The 2 MB 4-cylinder units also have no on-chip
flash (`docs/02_memory_map.md` §1).

## 1.3 What must match for our patches to transfer

| Goal | What must be identical | Why |
|---|---|---|
| Flash our patched binary unchanged; all addresses valid | **Bosch software number `1037382557`** (hence dataset `D9133_43K6P0`) | Code layout, calibration offsets, the 65 checksum descriptors and the RAM map are properties of the software build, not of the VW part number |
| Re-derive the patch with modest work (same functions, shifted addresses) | Same Bosch hardware `0261S02226`, or at least another 3.2 FSI VR6 software of the same generation | Same peripheral wiring, same driver set, same Funktionsrahmen modules; only the build differs |
| Harness / KWP / power-up / flash-procedure rehearsal only | Any `03H906032` VR6 unit (3.2 or 3.6) | Connector, supply pins, CAN and bootloader behaviour are common to the board |

Practical rule: **the VW suffix is not a compatibility statement; the Bosch
software number is.** Ask the seller for a photo of the label — it carries
`03H906032x`, `0261S02xxx` and the 10-digit `1037xxxxxx` — before paying.

## 1.4 Where to buy (EU, with Finland in mind)

Status **COMMUNITY**, confidence **medium** (prices move).

| Channel | What it is | Rough price | Notes |
|---|---|---|---|
| **Nettivaraosa.fi** ([Passat parts](https://www.nettivaraosa.com/volkswagen-passat-varaosat)) | Finnish used-parts aggregator, dozens of breakers | €50-150 for an engine ECU | The 3.2 FSI Passat was a rare, high-tax car in Finland; expect few hits. Search "moottorinohjainlaite" + Passat 3.2 |
| **Autopurkaamot.com** ([site](https://www.autopurkaamot.com/)) | Second Finnish breaker aggregator | same | Had a Passat 3.2 FSI engine listed at the time of writing, so 3.2 cars do get broken here |
| **Ovoko / rrr.lt** ([listing example](https://rrr.lt/en/used-part/dra25594-03h906032-volkswagen-passat-b6-engine-control-unit-module)) | Baltic/EU marketplace of breakers, ships to Finland | one bare `03H906032` listed "from €499" (looks like an outlier; most B6 ECUs there are €60-200) | Best EU-wide coverage for PQ35 parts; the site blocks scripted access, browse by hand |
| **eBay.de / eBay.co.uk / Kleinanzeigen** | Private and breaker listings | €40-150 delivered to FI | Search `03H906032` and `0261S02226`; sellers usually post the label photo, which is exactly what we need |
| **Cartech Electronics (UK)** ([search](https://www.cartechelectronics.com/search?q=03H906032&type=product&options%5Bprefix%5D=last)) | Refurbished "plug & play", programmed to VIN, 12-month warranty | £395.99-£499.99 inc. VAT (≈ €460-580) | Overkill for a bench mule, and UK→FI import duty/VAT applies |
| **German breakers** (autoteile-markt, Autoscout24 parts) | The 3.2 FSI Passat was mainly a German/Austrian car | €50-150 | Highest chance of a genuine 3.2 unit |

Recommendation: watch eBay.de and Ovoko for `0261S02226`; accept a 3.6 unit at
€40-80 as an immediate harness mule if a 3.2 does not appear within a few
weeks. Two units (a cheap 3.6 for harness bring-up plus a matching 3.2 for the
flash rehearsal) still costs under €200 and keeps the car's ECU out of every
experiment.

## 1.5 Immobiliser implications for a bench unit

- The Passat B6 uses **Immobilizer 4D, with the immobiliser function in the
  comfort/convenience module**, not in the ECU; matching an engine ECU needs
  the car's PIN written to **engine adaptation channel 050**
  ([Ross-Tech: VW Passat (3C) Immobilizer](https://wiki.ross-tech.com/wiki/index.php/VW_Passat_(3C)_Immobilizer),
  [Immobilizer IV ECU Swapping](https://wiki.ross-tech.com/wiki/index.php/Immobilizer_IV_ECU_Swapping)).
  COMMUNITY (vendor wiki), high.
- **For everything planned on the bench, the immobiliser is irrelevant.**
  Powering the ECU, KWP/TP2.0 sessions, measuring blocks, RequestUpload RAM
  snapshots and reading/writing flash all work regardless of immo state —
  that is exactly what every bench tool does; Alientech advertises working
  "on the bench also on the ECUs Bosch EDC16 and MED9, without opening them"
  ([alientech-tools.com](https://www.alientech-tools.com/news/k-suite-3-91/)).
  COMMUNITY, high.
- Expect the bench unit to log immobiliser DTCs and to refuse to run an
  engine. Normal and harmless.
- **Never move the EEPROM or a full clone between the bench unit and the car's
  unit**, in either direction. The EEPROM carries the immo pairing, VIN and the
  adaptation/flash counters. Keep the two units' backups in separate
  directories with the serial number in the name.

## 1.6 Conflicts in the sourcing data (read before buying)

1. **Suffix C.** Cartech lists `03H906032C` as *Passat 3.2 FSI, 0261S02226* —
   the same Bosch number as our unit. VW's own catalogue lists `03H906032C` as
   *"Engine Control Module (ECM). 3.6 LITER"* for Passat / Passat Wagon
   ([parts.vw.com](https://parts.vw.com/p/Volkswagen__/Engine-Control-Module-ECM/47990925/03H906032C.html)).
   The most likely explanation is that **parts.vw.com is the North-American
   catalogue and the 3.2 FSI was never sold there**, so only the 3.6
   application shows; but Cartech reusing the 0261S02226 page for two
   different VW numbers also looks like a copy/paste. **Treat suffix C as
   unresolved** and go by the Bosch number and software number on the label.
2. **Suffix HB.** Cartech says Passat 3.6; a forum post (Apsik) names
   `03H906032AB` and `03H906032HB` as the 3.2 FSI AXZ variants
   ([thread 11645](http://nefariousmotorsports.com/forum/index.php?topic=11645.0))
   *(forum)*. AB is confirmed 3.2 by a second source (obdtotal); HB is not.
3. **ECU-family label.** Alientech classifies our unit as MED9.1.1 (K-Suite
   vehicle list, `docs/02_memory_map.md` §1); dyno-chiptuningfiles labels the
   same hardware "Bosch MED9.1.3"
   ([link](https://www.dyno-chiptuningfiles.com/original-ecu-files-database/bosch-med9-480090/)).
   Most vendors just say "MED9.1". The sub-version suffix is a tool-vendor
   convention, not a Bosch one; do not filter listings on it.

---

# 2. Full BDM backup route (issue #2)

## 2.1 What "complete" means, and expected sizes

| Memory | Expected size | Evidence |
|---|---|---|
| External flash (CS0) | **2,097,152 B** (2 MB) | VERIFIED-STATIC: first 2 MB of our dump. Community names the chip **M58BW016-B, 2048 KB** on MED9.1.x with MPC563/564 ([VF2 Flasher release notes](https://chiptuningshop.com/news/vf2-flasher-news/vf2-flasher-v2-4-0-0/)), COMMUNITY medium. Matches obdtotal's 2,097,152-byte MED9.1 3.2 FSI file |
| On-chip flash (UC3F) | **524,288 B** (512 KB) | MPC563 and MPC564 both carry 512 KB UC3F ([NXP MPC564](https://www.nxp.com/products/MPC564), [MPC561/563 reference manual](https://www.antoniosantoro.com/sheet/MPC561_3RM.pdf)). Datasheet, high |
| Serial EEPROM | **2,048 B or 4,096 B** | MED9.1 EEPROM is a 95160 (2 KB) or 95320 (4 KB) SPI device; immo-off tooling states "the EEPROM file for Bosch MED9.1 must be a 2 KB or 4 KB file" ([car-auto-repair.com](https://www.car-auto-repair.com/how-to-disable-bosch-med9-1-immo-for-auditouareg-and-golf-6/)). COMMUNITY, medium |
| **Total flash** | **2,621,440 B** | 2,097,152 + 524,288; independently quoted as the file size for our hardware by [dyno-chiptuningfiles](https://www.dyno-chiptuningfiles.com/original-ecu-files-database/bosch-med9-480090/). COMMUNITY, medium |

Our KESS read is 2,605,056 B = 2,621,440 − 16,384, i.e. exactly one 16 KB
sector short — consistent with a protected boot sector at 0x400000-0x403FFF.

**MCU expectation.** MED9.1.1 is reported as **MPC563-based**
([pcmhacking thread 8487](https://pcmhacking.net/forums/viewtopic.php?t=8487)
*(forum)*, COMMUNITY low — the page is Cloudflare-protected and could only be
read through a search snippet). ecu.design publishes separate pinout pages for
"Bosch MED9.1 xrom **MPC562**" and "Bosch MED9.1.X xrom **MPC564**"
([MPC564 page](https://ecu.design/ecu-pinout/pinout-bosch-med9-1-x-xrom-mpc564-egpt-vag/)),
while EVC's BDM100 compatibility table says "MED9.1 VAG = MPC562"
([evc.de](https://www.evc.de/en/product/bdm/Default.asp)). These are not
contradictory once the family is split: the **flashless 2 MB 4-cylinder units
are MPC562; the 2.5 MB units with 512 KB on-chip flash are MPC563/MPC564** —
exactly the reasoning already in `docs/02_memory_map.md` §1. Read the marking
when the case is open (§7).

## 2.2 Route A — Alientech K-TAG, BDM (Motorola MPC5xx), protocol 64

The route named in the issue. Needs:

- A **K-TAG** (master or slave) with protocol 64 enabled.
- The **"BDM Motorola MPC5xx" positioning-frame adapter kit**, Alientech code
  **144300KBDM**, containing adapters `14AM00T00M`, `14AM00T01M`,
  `14AM00TBAS`, `14AM00TB01`, `14AM00TB02`, `14AM00TB03`, `14AM00T02M`
  ([tuningtools.com, NL](https://www.tuningtools.com/k-tag-positioning-frame-adapter-kit-bdm-motorola-mpc5xx?___store=en);
  both that page and the UK reseller blocked scripted access, the part list
  comes from the reseller's title text). COMMUNITY, medium.
- Used **with** Alientech's metal positioning frame, sold separately
  ([Alientech "Positioning Frame"](https://alientech-usa.com/products/positioning-frame)).

Subscription state matters. Alientech has moved the line to KESS3 and offers a
trade-in; several resellers report that **subscription renewals are no longer
offered**, existing subscriptions keep working until they expire, and the
vehicle list is effectively frozen
([mychiptuningfiles](https://mychiptuningfiles.com/en/chiptuning-news/alientech-kess3),
[ms-group.pl](https://ms-group.pl/en/product/kess3-nowe-urzadzenie-alientech-kessv2-k-tag/)),
COMMUNITY medium. **Alientech UK still sells KESS V2 and its subscriptions and
says nothing about end of life**
([alientechuk.co.uk](https://www.alientechuk.co.uk/pages/kess-v2)) — **sources
conflict; verify against the actual tool's K-Suite subscription screen.**
Subscription prices where quoted: €1190/yr master, €590/yr slave (ms-group.pl,
COMMUNITY, low).

What a protocol-64 read produces, per community reports: **three files — FLS
(flash), EEPROM and a combined/compressed container**, and "on MED9.1 ECU you
need to do eeprom and extflash" (vendor blogs summarising K-Suite; COMMUNITY,
low). **Assume nothing about which files you get — verify sizes against §2.1.**

## 2.3 Route B — K-TAG **Service Mode**, no opening of the ECU (evaluate this first)

Alientech added a "Service Mode" for exactly this ECU class:

> "you can now work on the bench also on the ECUS Bosch EDC16 and MED9,
> without opening them. Reading and writing can be made through the direct
> connection to the ECU connectors, in addition you will also be able to clone
> the entire content of the ECU."
> — [Alientech, K-Suite 3.91 release notes](https://www.alientech-tools.com/news/k-suite-3-91/)
> (listed ECUs include **MED9.1**, MED9.1.5, MED9.5.10; brands VAG, PSA,
> Volvo, Mercedes, KTM)

A later release explicitly lists MED9.1 for VW Passat / Phaeton / Touareg /
Eos, Porsche Cayenne 3.6 VR6, Audi and Lamborghini, with "read, write, and
clone in Service Mode. You no longer have to open the ECU" and access to "the
microprocessor, the EEPROM, the flash memory"
([K-Suite 4.18](https://www.alientech-tools.com/en/k-suite-4-18/)).
COMMUNITY (manufacturer), **high** for the capability, **medium** for whether
*our* tool's subscription/protocol set includes it.

If Service Mode covers our unit, **this removes the entire pad-damage risk**
and needs only the K-TAG bench cable, not the BDM frame. **Check this in
K-Suite before buying any frame** — it is the highest-value single action on
the Windows machine (§4).

## 2.4 Route C — EVC BDM100 (the classic MPC5xx BDM tool)

- Works with **Motorola MPC555-565**; the compatibility table lists **MED9.1
  VAG**. Reads internal flash, external flash and EEPROM
  ([evc.de BDM100](https://www.evc.de/en/product/bdm/Default.asp)).
  COMMUNITY (manufacturer), high.
- Needs the **BDM140.P positioning frame** plus a **BDM141-BDM147 probe**
  ("To connect the ECU the positioning frame BDM140.P with the corresponding
  probe BDM141 - BDM147 will be needed", same page).
- Genuine prices from EVC: **BDM100.K €1,204.28**, **BDM140.P €418.88**,
  probes **€124.36-€183.26**. High (manufacturer price list).
- Clone BDM100 interfaces and universal "BDM frame with adapters" sets sell for
  roughly **€30-90** all-in (e.g. [vxdas BDM frame set](https://www.vxdas.com/products/bdm-frame-with-adapters-set)).
  COMMUNITY, medium. Cheapest way into MPC5xx BDM, at the usual clone risks.
- The **BDM100 manual is a genuinely useful free document**. It shows the Bosch
  14-pad array and says "only 10 pads of the total 14 pads are used for the
  EDC16 or ME9 programming port" (pads 3, 4, 13, 14 unused on that family);
  it gives the Motorola standard 10-pin BDM pinout —
  1 VFLS0, 2 SRESET, 3 GND, 4 TCK/DSCK, 5 GND, 6 VFLS1, 7 HRESET, 8 TDI/DSDI,
  9 +3.3 V, 10 TDO/DSDO — and the trick for finding pad 1: **the two grounded
  pads are 3 and 5, so pin 1 is "left of the two grounded pins"**, found with
  an ohmmeter. It warns that the Bosch pad array additionally carries 12 V
  battery and 12 V ignition, and that "in early EDC16 ECUs the 3.3 V pad is
  driven by a 5 V circuit".
  [BDM100-en.pdf](https://www.evc.de/ftp/winols/BDM100-en.pdf), pp. 4-9, 33-34. High.

## 2.5 Route D — PCMflash module 77 (cheapest credible non-BDM route)

- **Module 77: "ME9 / MED9 / EDC16" with MPC556/MPC562/MPC564**; reads and
  writes **built-in flash, external flash and EEPROM**; works in **service
  mode via direct ECU-connector connection** (no opening)
  ([chiptuningshop, PCMflash 1.2.2](https://chiptuningshop.com/news/pcmflash-1-2-2-released/)).
  COMMUNITY, medium.
- Hardware: **Scanmatik 2 Pro + PCMflash BENCH/BOOT cable**, or a **PowerBox
  for PCMflash + any standard J2534 interface**.
- The release note says "Connection diagrams are NOT provided", and that
  checksum/RSA correction is available "except MED9 VAG ECU" — irrelevant for
  us, `tools/checksum.py` already handles our 65 blocks.
- ECUTools Vietnam publishes a "Pinout PCMflash Module 77 MED9.1, MED9.1.1 VAG
  ECU" page; the pinout itself is an image that could not be extracted here,
  but it is a concrete thing to open in a browser:
  <https://ecutools.vn/en/post-ecu/pinout-pcmflash-module-77-med9-1-med9-1-1-vag-ecu/>

## 2.6 Route E — other tools that claim MPC5xx BDM or MED9 bench

All COMMUNITY, confidence medium; none verified by us.

| Tool | What it claims | Source |
|---|---|---|
| **bFlash** (bench) | "full iFlash, eFlash and Eeprom access from the ECU connector" for MED9.1/9.1.2/9.1.3 and EDC16 with MPC556/562/564; no BDM pads | [chiptuningshop](https://chiptuningshop.com/news/bflash-update-v1906a-med9-edc16-bench-mode/) (master tool ≈ £4,200 at launch) |
| **Autotuner** | MED9.1 (MPC562) in **Bench** — "doesn't require to open the engine ECU" | [autotuner.com](https://www.autotuner.com/pages/ecu/bosch-med91-mpc562) |
| **DFOX (DFB Technology)** | bench mode for EDC16/MED9.1 with **MPC563 and MPC564**, "comprehensive reading and writing", recovery and checksums | [dfbtechnology.com](https://www.dfbtechnology.com/en/new-bench-mode-for-ecu-edc16-with-mpc563-and-mpc564-added/) |
| **VF2 Flasher** | Bosch BENCH ME(D)9 / EDC7 / EDC16; MED9.1.x MPC563/564, internal 512K + external M58BW016-B 2048KB | [chiptuningshop](https://chiptuningshop.com/news/vf2-flasher-news/vf2-flasher-v2-4-0-0/) |
| **KT200 / KTM200** | MED9.1 EEPROM read on the bench | [eobdtool blog](http://blog.eobdtool.co.uk/audi-med9-1-eeprom-read-by-kt200-ktm200-on-bench/) (TLS certificate expired; read via search snippet) |
| **ECUHELP "ECU Bench Tool"** | MD1/MG1/EDC16/**MED9** on the bench, "no need open ECU" | [ecuhelpshop](https://www.ecuhelpshop.com/products/ecu-bench-tool-ecu-programmer.html) |
| **Dimsport New Trasdata, Magic Flex, CMD, FGTech** | generic BDM MPC5xx, used with the same universal frames | vendor listings; not separately verified |

## 2.7 Route F — have a shop do it (Finland / EU)

A shop with a master tool can produce the backup in an hour. Finnish
candidates (their own claims; COMMUNITY, low):

- **Chip Tuning Finland** — <https://chiptuningfinland.com/>,
  branch list <https://chip-tuning.fi/jalleenmyyjat/> (Helsinki, Tampere and
  others); states it programs even "tuning-locked" MD1/MG1 ECUs with genuine
  professional tools.
- **TuningChip Suomi** — <https://www.tuningchip.fi/kontakt_fi> (Helsinki,
  Vantaa, Espoo, Lahti, Tampere, …).
- **Special Tuning Harinen / Hestec** — <http://www.hestec.fi/>.

What to ask for, in these words: *"a bench read of a Bosch MED9.1 VR6 ECU:
external flash, internal/microprocessor flash and the serial EEPROM, as three
separate raw files, no checksum correction, no modification."* Bring the ECU
loose, supply your own USB stick, expect €50-150. Beware: many shops will only
quote for "reading the file for tuning", which usually means the 2 MB external
flash alone — that is what we already have.

## 2.8 Risks, and how the frame avoids them

- The BDM port is a bare **pad array on the PCB**, not a connector. There is no
  protection at that level — which is why BDM works on read-protected units,
  and why a slip ruins the board.
- The frame plus spring-loaded pogo probe exists precisely so that **nothing is
  soldered and nothing is hand-held**: the probe is aligned once and pressed
  down mechanically. EVC specifies "a spring travel of 1.5-2 mm minimum for
  best contact conditions" and says to switch power on only *after* the probes
  are centred on the correct pads
  ([BDM100 manual](https://www.evc.de/ftp/winols/BDM100-en.pdf), pp. 8, 33). High.
- Practical rules, ordered by how much damage they prevent:
  1. **Try Service Mode / bench first (§2.3, §2.5).** If it reads all three
     memories, never open the ECU at all.
  2. Never solder to the pads. One lifted pad is an unrecoverable board.
  3. Clean the pads of flux/conformal coating with IPA before contacting.
  4. Identify pad 1 with an ohmmeter (two grounded pads = 3 and 5) *before*
     applying power, and photograph the board first.
  5. Power the ECU only from the BDM tool's own supply path as the manual
     shows; do not also feed the main connector.
  6. Read twice, compare SHA-256, and only then believe the file.
- **Known-good cross-check we already have:** the first 2 MB of any correct BDM
  read must be byte-identical to `data/passat_azx_ori.bin[0:0x200000]`, and the
  on-chip image at offset 0x4000..0x80000 must equal
  `data/passat_azx_ori.bin[0x200000:0x27C000]`. If both hold, the read is
  genuine and the only new bytes are 0x400000-0x403FFF plus the EEPROM.

## 2.9 Where the BDM pads are on a MED9.1 board

No freely viewable photograph of an `03H906032` board could be sourced. What is
documented:

- On Bosch EDC16/ME9-class boards the pads are a **14-pad array near the rear
  edge of the PCB, next to the connector header**, of which 10 are used
  ([BDM100 manual](https://www.evc.de/ftp/winols/BDM100-en.pdf), pp. 4-5:
  "Pict. A: These are the typical Bosch pads for BDM"). High.
- Forum description for MED9/EDC16: the BDM pads sit "in the bottom right
  corner" of the opened ECU, and the contact is made on "the first top 5 and
  bottom pads" from the left *(forum)*, low.
- Orientation is found from the board, not from a picture: ohm out the two
  grounded pads (3 and 5); pad 1 is to their left (§2.4).

**Action for the human:** when the spare ECU is open, photograph the pad array
with a ruler in frame and commit the photo. That closes this gap permanently
and beats anything findable online.

## 2.10 Recommendation for issue #2

1. Put the **spare** ECU on the bench first; never practise on the car's unit.
2. On the Windows box, open K-Suite and check whether the owned tool offers
   **K-TAG Service Mode** (or a KESS bench mode) for MED9.1 (§2.3). If yes,
   read all three memories from the connector. Cost: €0.
3. If not, choose between a genuine Alientech BDM MPC5xx frame kit (several
   hundred €), a clone BDM100 + universal frame (€30-90, some risk), or a shop
   read (€50-150, no risk to our hardware).
4. Whatever the route: store the images under `data/backup_bdm/` with a
   SHA-256 `MANIFEST`, run the two byte-identity cross-checks of §2.8 and
   `tools/checksum.py verify` on the flash image.

---

# 3. Bench harness (issue #3)

## 3.1 Connectors

The VAG MED9.1 engine ECU uses **two connectors, T60 (60-pin) and T94
(94-pin)**, 154 pins in total; PCB header assemblies are sold as "154 pin /
60 pin and 94 pin PCB pin-header ECU connector for VW"
([kinkong-connector](https://www.kinkong-connector.com/products/154pin60pinand94pinpcbpinheaderecuconnectorforvwckk154-ID551.html)).
COMMUNITY, medium.

Mating housings / pigtails that can be bought instead of butchering a harness:

| Part | What | Source / price |
|---|---|---|
| `3C0906385` | **94-pin ECU connector housing, Passat 3C family** | [wolfautoparts](https://wolfautoparts.com/94pin-engine-module-ecu-wiring-harness-connector-3c0906385-ps39045.html) (was sold out) |
| `3C0906379` | **60-pin ECU wiring connector pigtail** | same vendor, US$34.99 |
| `7L0906385A` | 94-pin ECU connector housing (Touareg number, same family) | [ECS Tuning](https://www.ecstuning.com/b-genuine-volkswagen-audi-parts/ecu-connector-housing-94-pin/7l0906385a/), [wolfautoparts pigtail](https://wolfautoparts.com/94pin-ecu-wiring-connector-pigtail-7l0906385a-ps10784.html) |
| generic | "BOSCH EDC17/EDC16 94-pin + 60-pin ECU connector harness cable for Audi VW" — ready-made flying leads | [autoecupart.net](https://www.autoecupart.net/products/one-pair-bosch-edc17-edc16-94pin-60-pin-ecu-connector-harness-cable-for-audi-vw), [eBay 94-pin AMP EDC16/MED ECU terminals](https://www.ebay.com/itm/387086532074) |
| ready-made | "Volkswagen MED9.1 Bench Cable" for KESS3/K-TAG/Bitbox etc. | [rigotech express](https://express.rigotech.hu/volkswagen-med9-1-bench-cable/) (HU; page blocks scripted access, price not read) |

Cheapest and most certain route in Finland: **buy the ECU with a ~30 cm harness
stub still attached** — ask the breaker to cut long, not at the plug. You then
have the correct plug, the correct terminals and the OEM wire colours to trace.

## 3.2 Minimum bench pinout (T94 connector)

Several independent community sources give the same small pin set for powering
and talking to a MED9.1/MED9.1.1 on the bench. Status **COMMUNITY**,
confidence **medium**; *verify with a meter before applying power* (§3.3).

| Function | T94 pin(s) | Notes |
|---|---|---|
| **Ground (KL31)** | **1, 2, 4** (and 61) | All to PSU negative. Thick wire; these are the power grounds |
| **+12 V, set A** | **3, 5, 6** | rusefi: "+12 V from ECU relay, term. 15" (switched). Other vendor pinouts label these term. 30 — **sources conflict on which set is which** |
| **+12 V, set B** | **87, 92** | rusefi: "Constant +12 V from Fuse #25, 10 A, term. 30" |
| **CAN-L (powertrain)** | **67** | ORG/BRN, "yellow tape" |
| **CAN-H (powertrain)** | **68** | ORG/BLK, "black tape" |
| **K-line** | **86** | Present on the ECU; whether the AXZ car's harness uses it is unverified |
| ECU relay control / CAN wake-up | 69 (low side) | Not needed when 12 V is fed directly to both supply sets |
| Main relay control | 32 (low side) | Likewise not needed on the bench |

Sources: [transpondery VAG MED9xx pinout](https://www.transpondery.com/pinouts/vag/med9x_pinout.html)
("Ground 1; VCC 5, 87, 92; K-Line 86; CAN-L 67; CAN-H 68"),
[rusefi wiki VolkswagenPassatB6](https://github.com/rusefi/rusefi/wiki/VolkswagenPassatB6)
(full T94/T60 tables; raw markdown at
[raw.githubusercontent.com](https://raw.githubusercontent.com/wiki/rusefi/rusefi/VolkswagenPassatB6.md)),
plus several tool-vendor pinout summaries that all repeat "GND 1, 2, 4 /
12 V 3, 5, 6 and 87, 92 / CAN-L 67 / CAN-H 68"
(e.g. [MED9.1.2 pinout thread](https://mhhauto.com/Thread-MED9-1-2-Pinout) *(forum)*).

**Bench practice:** feed +12 V to **all** of 3, 5, 6, 87, 92 and ground
1, 2, 4 (+61). Both groups are just battery and ignition rails; with both fed
directly the ECU does not need its main relay, and it cannot matter which of
the two conflicting labellings is right.

## 3.3 Important caveats about the pinout

1. **The rusefi table is for a 2.0 TFSI BPY ECU (`3C0907115F`)**, not for our
   VR6 `03H906032`. The T94 pins above (power, ground, CAN, K-line) are
   platform-generic and corroborated by the tool-vendor pinouts, so they should
   carry over. **The T60 connector definitely does not**: our unit drives 6
   injectors and 6 coils, so every injector/coil pin differs.
2. The rusefi T94 table also contradicts itself — it lists `27/87` as fuel-pump
   control and `32/92` as main-relay control while also listing 87 and 92 as
   constant +12 V. The `x/y` notation is used elsewhere on that page to mean
   *pin / connector-size* ("22/94", "29/60"), so the table probably mixes two
   conventions. Do not trust 87/92 blindly.
3. **Ring the connector out before applying power.** With the ECU unpowered,
   measure resistance from each candidate pin to the ECU case / known ground:
   the power grounds read near 0 Ω. Check that the intended +12 V pins do *not*
   read near 0 Ω to ground. Then power up through a **current-limited** supply
   set to ~1 A and watch the current before raising the limit.
4. A ready-made "MED9.1 bench cable" (§3.1) removes all of this guesswork and
   is worth its price if one can be bought in the EU.

## 3.4 The CAN side

All from VW's own self-study programme **SSP 269 "Datenaustausch auf dem
CAN-Bus II"**
([PDF](https://phaetonclub.com/images/companies/1/SSP269%20Datenaustausch%20auf%20dem%20CAN-Bus%20II.pdf)),
status **COMMUNITY (manufacturer document)**, confidence **high**:

- **The powertrain bus (CAN-Datenbus Antrieb) runs at 500 kBit/s**, two-wire,
  the "High-Speed CAN". Nodes: engine, ABS, ESP, gearbox, airbag, instrument
  cluster.
- **Termination: VW does not use two 120 Ω end resistors.** It uses "verteilte
  Lastwiderstände mit einem *zentralen Abschlusswiderstand* im
  Motorsteuergerät": **the engine ECU carries ~66 Ω between CAN-H and CAN-L**,
  every other module about 2.6 kΩ, giving a bus load of 53-66 Ω depending on
  how many modules are connected. Measurable with an ohmmeter when terminal 15
  is off.
  → **On the bench the ECU alone already terminates the bus.** Use a CAN
  adapter with *switchable* termination and leave it **OFF**; 66 Ω on its own
  is close enough to the nominal 60 Ω. With a fixed 120 Ω adapter the
  resulting ≈43 Ω is slightly below the ISO 11898 minimum load — it works at
  desk distances, but prefer a switchable adapter.
- "Auch zu Messzwecken sollte der CAN-Datenbus Antrieb nicht um mehr als 5 m
  verlängert werden" — keep bench CAN wiring short and twisted.
- Levels: recessive ≈ 2.5 V on both lines; dominant CAN-H ≈ 3.5 V, at least 1 V
  swing per line. Useful for a scope sanity check.
- **The powertrain bus is switched on by terminal 15 and off after a short
  run-on.** On the bench, KL15 must be present for CAN activity. Cyclic
  messages repeat typically every 10-25 ms.

## 3.5 Does the ECU need crank/cam or CAN partners to answer KWP?

- **Power + ground + CAN is enough** for diagnostics, reading and writing —
  the premise of every bench tool on the market. Alientech states that MED9
  ECUs can be read, written and cloned "through the direct connection to the
  ECU connectors" on the bench
  ([K-Suite 3.91](https://www.alientech-tools.com/news/k-suite-3-91/)), and
  Autotuner lists MED9.1 in "Bench" mode
  ([autotuner.com](https://www.autotuner.com/pages/ecu/bosch-med91-mpc562)).
  COMMUNITY (manufacturer), high.
- **No crank or cam signal is needed** to power up and talk. Without them the
  ECU stays in "engine not running" state and stores DTCs. HYPOTHESIS (strongly
  implied by the above, not separately sourced), medium.
- **No other control unit is needed.** The ECU is the bus's central termination
  (§3.4), so a bus of just the ECU plus our adapter is electrically valid.
  Frames expecting a gearbox/ABS answer will time out and set DTCs — expected
  and harmless.
- Two behaviours to settle on the bench, both **HYPOTHESIS**:
  1. **Run-on / sleep.** If KL15 is dropped the ECU may power the bus down
     after its run-on time. Keep KL15 on for logging sessions.
  2. **KWP session keep-alive.** TP2.0 channels need TesterPresent; the exit
     criterion of issue #3 (ECU answers TesterPresent) is the right first test.

## 3.6 Power supply and current

- No manufacturer figure for MED9.1 quiescent/active current could be sourced.
  Bench-harness vendors specify a **"quality 12 V 5 amp power supply"** for
  engine ECUs generally
  ([customecm.com bench harnesses](https://www.customecm.com/bench-harnesses)).
  COMMUNITY, low.
- Expected, as a **HYPOTHESIS to be measured and then written down**: with all
  actuators unplugged (no injectors, coils, throttle motor, fuel pump), an ECU
  of this class draws on the order of **0.3-1 A** with ignition on, and a few
  mA on KL30 alone. **Measure it and replace this paragraph with the number.**
- Choose a **current-limited lab PSU**: 13.8 V, ≥3 A, adjustable current limit
  with a current display. Start at ~1 A. A fixed 12 V brick with no limit turns
  a wiring mistake into a dead ECU.
- **For flashing the supply must be rock-solid**: set the limit generously
  (3-5 A) so a transient cannot trip it mid-write, and never flash from a
  supply that is also running something else.

## 3.7 CAN adapter for the Mac

macOS has no SocketCAN (`docs/03_tooling.md` §6). Options, cheapest first:

| Option | Interface | Notes | Rough price |
|---|---|---|---|
| **CANable 2.0** | slcan or candleLight (gs_usb) | open source; Linux/macOS/Windows; `python-can` supports both firmwares; the design is resold under many names | ≈ $36 at [Openlight Labs](https://openlightlabs.com/products/canable-2-0); clones €12-25 |
| **Existing Pi Zero W + MCP2515 + socketcand** | SocketCAN over WiFi to SavvyCAN | already written up in `pi_can_setup/README.md` | €0-25 |
| **Innomaker / DSD TECH USB-CAN** (gs_usb) | candleLight-compatible | same software path as CANable | €25-45 |
| **PEAK PCAN-USB** | proprietary driver | rock solid, well supported by professional tools, still no macOS SocketCAN | €200+ |

Recommendation: **CANable 2.0 with candleLight firmware** as primary (same
gs_usb path `python-can[gs-usb]` already expects), keeping the Pi Zero +
socketcand route as the fallback that also feeds SavvyCAN. Make sure the
adapter has a **jumper or solder bridge to disable its 120 Ω termination**
(§3.4).

## 3.8 Bench harness parts list

| # | Item | Why | Rough price |
|---|---|---|---|
| 1 | Spare `03H906032` ECU **with a harness stub attached** | the unit under test *and* the correct connector | €40-150 |
| 2 | Bench PSU 0-30 V / 0-5 A with current limit (Korad KA3005 class) | current-limited bring-up, stable flashing | €60-110 |
| 3 | Inline fuse holder + 5 A blade fuses | second line of defence | €5 |
| 4 | Two toggle switches (KL30, KL15) | model the ignition, test run-on behaviour | €10 |
| 5 | Crimp terminals / spare pins for T94+T60, or a ready-made bench cable | connection | €10-60 |
| 6 | CANable 2.0 (or equivalent gs_usb adapter) with switchable termination | CAN to the Mac | €15-40 |
| 7 | OBD-II female socket on a short lead | reuse OBD-style cables and the Pico node's connector | €8 |
| 8 | Twisted-pair wire for CAN, 1.5 mm² for power | wiring | €10 |
| 9 | Multimeter (continuity + current) | ring out the connector, measure draw | owned |
| 10 | Pico + CAN transceiver (already in `pico_can_sender/`) | ethanol-frame injection point | €0 |

## 3.9 Suggested bring-up order (results into `re/findings/bench.md`)

1. Photograph the ECU label and both connectors; record hardware / Bosch /
   software numbers.
2. With no power: ohm out pins 1, 2, 4, 61 to case → expect ~0 Ω. Check that
   3, 5, 6, 87, 92 are **not** shorted to ground. Measure CAN-H (68) to
   CAN-L (67) → **expect ≈ 66 Ω** — this both confirms the pin numbering and
   confirms the SSP 269 termination claim on our actual unit.
3. Connect ground only, then the KL30 group, current limit 1 A. Note the
   current.
4. Add the KL15 group. Note the current. Listen for a relay click.
5. Sniff CAN-H/CAN-L: with KL15 on the ECU should transmit. Capture with
   SavvyCAN at 500 kbit/s and record which IDs it sends alone.
6. Open a TP2.0 channel to logical address 0x01 and send TesterPresent
   (exit criterion of issue #3).
7. Only then: inject Pico frames; and only much later, flash anything.

---

# 4. Windows machine for K-Suite (issue #4)

## 4.1 What K-Suite needs

- **Windows only.** K-Suite is "a unique software to manage your tools" and
  updates itself online
  ([Alientech UK](https://www.alientechuk.co.uk/pages/kess-v2)). High.
- Reseller documentation for the K-Suite 2.4x-2.8x line states **Windows
  7/8/10/11, 32- or 64-bit, administrator rights required**, with the USB
  drivers shipped in the installer
  ([tuning-database.co.uk, KSuite 2.70](https://www.tuning-database.co.uk/ksuite-installation-configuration-kess-v2/),
  [uobdii, K-Suite 2.47](https://blog.uobdii.com/kess-v2-v5-017-user-manual-with-k-suit-v2-47-installation-tips/)).
  COMMUNITY (reseller pages, written for *clone* KESS units), **medium** —
  treat the Windows-version claims as indicative.
- Recurring practical points from those pages, medium:
  - **Antivirus must be disabled during install and the K-Suite folder
    excluded afterwards** — the drivers and protection layer trigger false
    positives in Defender and others.
  - **Prefer a USB 2.0 port**; several reports of failures on USB-3-only
    machines, fixed with a USB 2.0 hub.
  - Older installers also pull in Microsoft Visual C++ runtimes.
- Windows 11 specifically: third-party driver/registry "fix" packages for
  KESSv2/K-TAG exist because of **driver-signature enforcement and USB
  detection problems** on Win 10/11
  ([github kessv2-ktag-driver-fix-alientech-w11](https://github.com/kessv2-ktag-driver-fix-alientech-w11/Menu)).
  Their existence is evidence of the problem; **do not run unknown driver
  patchers on the machine that holds the only working K-Suite install.**
  COMMUNITY, low.

## 4.2 VM vs. real hardware

- USB passthrough to a Windows guest **is** supported on Apple-silicon
  Parallels ([Parallels forum](https://forum.parallels.com/tags/usb-passthrough/),
  [Parallels 20.3 notes](https://alternativeto.net/news/2025/4/parallels-desktop-20-3-0-adds-x86_64-emulation-fixes-obs-camera-support-and-usb-passthrough)),
  but the guest is **Windows on ARM running K-Suite under x86 emulation**, with
  an emulated USB stack and a signed-driver requirement. Three fragile layers
  under a tool whose driver layer is already the weak point. Confidence
  **medium** that it will be unreliable; **no direct report of KESSv2 under
  Windows-on-ARM was found either way — this is a genuine gap, the sources do
  not settle it.**
- **Recommendation: a real x86 Windows machine.** A second-hand business laptop
  is the cheapest reliable answer:

| Option | Rough cost in Finland | Notes |
|---|---|---|
| Used Intel ThinkPad T460/T480, X260 or Dell Latitude 5x90, i5, 8 GB, SSD, Win 10/11 Pro | **€90-180** (Tori.fi, huuto.net, refurb shops) | Real USB 2.0/3.0 ports; can be dedicated to K-Suite and kept offline |
| Mini PC / small desktop + existing monitor | €120-200 | No battery for a garage flash; needs a UPS if used for flashing in the car |
| Existing Intel Mac + Boot Camp or a VM with real x86 | €0 | Boot Camp = native x86, the best VM-free option if an Intel Mac exists |
| Parallels/UTM on Apple Silicon | €0-120 | Last resort; document exactly what happens (see gap above) |

Practical requirements: **a native USB-A 2.0 port** (or a powered USB 2.0 hub),
no aggressive third-party antivirus, and portability to the car.

## 4.3 Preserving the current install (the part that actually matters)

KESSv2 is end-of-life as a product line: Alientech has moved to KESS3 and
offers a trade-in, and multiple resellers report that **subscription renewals
are no longer offered** while existing subscriptions keep working until they
expire ([mychiptuningfiles](https://mychiptuningfiles.com/en/chiptuning-news/alientech-kess3),
[ms-group.pl](https://ms-group.pl/en/product/kess3-nowe-urzadzenie-alientech-kessv2-k-tag/)) —
though Alientech UK still lists KESS V2 and its subscriptions for sale
([alientechuk.co.uk](https://www.alientechuk.co.uk/pages/kess-v2)).
**Sources conflict; assume the pessimistic reading.**

Therefore, before touching anything:

1. **Image the whole Windows disk** of the machine that currently runs K-Suite
   (Clonezilla, Macrium Reflect Free, or `dd` from a Linux USB stick). This is
   the single most valuable action in issue #4 — the install, its activation
   state and its protocol set may not be reproducible.
2. Copy out separately: the K-Suite installation directory, the driver package,
   the K-Suite workspace/log directory, and any licence/protocol files.
3. Write down: K-Suite version, tool serial, firmware version, subscription
   expiry date, and whether the tool is master or slave.
4. **Do not update K-Suite** until the image exists and until we know an update
   cannot remove protocol 179 or 64.
5. Keep the machine offline except when deliberately updating.

## 4.4 Checklist to verify the install against the spare ECU

1. Machine imaged (§4.3) — yes/no recorded in the repo.
2. K-Suite starts, tool detected; version, serial and subscription state
   recorded.
3. Spare ECU on the bench harness, powered, current noted.
4. **KESSv2, OBD, protocol 179 (MED9.1.1)**: run *Identification* only. Record
   what it reports (VW number, Bosch number, software number) and compare with
   the label.
5. **Read** the spare ECU. Expect 2,605,056 bytes if it behaves like ours.
   SHA-256 it, then run `python3 tools/checksum.py verify -q` — a stock file
   must give `ALL OK`.
6. If the spare has the same software as the car: diff it against
   `data/passat_azx_ori.bin`. Any difference is calibration/adaptation and is
   interesting in itself.
7. **K-TAG**: check whether *Service Mode* (§2.3) is offered for MED9.1 and, if
   so, read microprocessor + external flash + EEPROM and check the sizes
   against §2.1.
8. Record every protocol number, menu path and quirk in `re/findings/` —
   K-Suite's own menus are the only documentation of what this particular tool
   can still do.
9. Only after 1-8 pass: consider writing (issue #26, Flash 0).

---

# 5. VCDS and OBD-port CAN access on this car

## 5.1 VCDS on the engine controller

- The engine ECU is **address 01**; VCDS reads its measuring blocks
  ("Measuring Blocks", groups **000-255**, four values per group except group
  000 which has ten). Gaps in the group numbering are normal
  ([Ross-Tech VCDS-Lite manual: Measuring Blocks](https://www.ross-tech.com/vcds-lite/manual/measblocks.html)).
  COMMUNITY (vendor doc), high.
- **Which groups exist is a property of this software build**, not of the
  model. Ross-Tech explicitly declines to publish per-engine lists and tells
  you to open the controller and press **Advanced Measuring Values**, which
  enumerates what the ECU actually supports
  ([Ross-Tech forums](https://forums.ross-tech.com/index.php?threads%2F24344%2F=)).
  COMMUNITY, high. → The definitive list for `1037382557` will come from the
  ECU itself, or from the MED9inf-style static extraction that brief A3
  covers. Nothing useful is published for AXZ specifically.
- **Sample rate is low.** Ross-Tech's own figures: ~4 samples/s with one group,
  ~2/s with two, ~1.3/s with three. That is why `docs/03_tooling.md` §6 prefers
  DDLI (0x2C + 0x21, ~40 samples/s) for real logging; VCDS is for orientation
  and cross-checking, not for flex-fuel calibration logs.
- **Interface caveat, worth stating before money is spent:** VCDS-Lite and
  "dumb" K-line cables cannot talk to a CAN-diagnosed controller. The B6's
  engine controller is diagnosed over CAN (§5.2), so a genuine Ross-Tech
  **HEX-V2 / HEX-NET** (≈ €250-450) is needed for VCDS. VCDS-Lite's shareware
  mode is additionally limited to groups 001-025
  ([Ross-Tech](https://www.ross-tech.com/vcds-lite/manual/measblocks.html)).
  COMMUNITY, medium.
- Alternative that costs nothing extra: our own TP2.0/KWP client over the
  CANable (issues #20, #23), which is where the project is going anyway.

## 5.2 Does an OBD-port CAN adapter see the powertrain bus? **No.**

This is the important answer, and VW's own documents settle it:

- **The OBD socket T16 carries the *Diagnose-CAN*, not the powertrain CAN.**
  VW self-study programme **SSP 315** gives the T16 pinout explicitly:
  pin 6 = "**CAN_H, Diagnose-CAN**", pin 14 = "**CAN_L, Diagnose-CAN**",
  pin 7 = K-Leitung, pins 4/5 = Klemme 31, pin 16 = Klemme 30, pin 1 = Klemme 15
  ([SSP 315, p. 53](https://vwcampersite.wordpress.com/wp-content/uploads/2015/01/ssp_315-euro-on-board-diagnose.pdf)).
  COMMUNITY (manufacturer), **high**.
- **The buses are deliberately separated and bridged only by the gateway.**
  SSP 269: "Die verschiedenen Datenbus-Systeme Antrieb und Komfort/
  Infotainment werden im Fahrzeug über das Gateway verbunden", with an explicit
  warning that they **must not** be electrically connected
  ([SSP 269](https://phaetonclub.com/images/companies/1/SSP269%20Datenaustausch%20auf%20dem%20CAN-Bus%20II.pdf)).
  COMMUNITY (manufacturer), **high**.
- Consequence: plugging a CANable into the OBD socket gives a 500 kbit/s bus on
  which **diagnostic sessions** work (TP2.0 → KWP2000 to logical address 0x01),
  because the gateway routes them — but you will **not** see the engine's
  cyclic powertrain frames, and **you cannot inject a frame that the engine ECU
  will receive**. That is exactly why the ethanol frame (issues #30, #31) has
  to go on the powertrain bus, and why CCP (CRO/DTO 0x7C3/0x7C4) needs direct
  bus access, as `docs/03_tooling.md` §6 already says. Confidence **high** for
  "no raw powertrain frames"; **medium** for the exact set of things the
  gateway does forward — the routing table is configurable and some VAG
  gateways do bridge a few frames.
- **Where to tap the powertrain bus instead** (to be confirmed on the car): at
  the engine ECU connector itself (T94 pins **67 / 68**), at the gateway J533's
  powertrain-CAN pins, or at any other powertrain node (ABS/ESP, gearbox,
  cluster). The ECU connector is the natural place for us because the bench
  harness uses the same two pins.
- Reminder from SSP 269 for whoever does the tap: the powertrain bus is
  **switched on with terminal 15 and off after a short run-on**, so it is dead
  with the ignition off; and it should not be extended by more than 5 m even
  for measurement.

---

# 6. Shopping / preparation list

Rough, VAT-inclusive, Finland/EU, September 2026. Nothing here has been ordered.

## Tier 1 — needed for any bench work (≈ €150-350)

| Item | Price | Where |
|---|---|---|
| Spare `03H906032` VR6 ECU, preferably 3.2 / `0261S02226`, **with harness stub** | €40-150 | eBay.de, Ovoko/rrr.lt, Nettivaraosa.fi, Autopurkaamot.com |
| Bench PSU, 0-30 V / 0-5 A, current limit + current display | €60-110 | Partco, Elfa/Distrelec, Biltema, AliExpress |
| CANable 2.0 or equivalent gs_usb adapter, switchable termination | €15-40 | Openlight Labs, Mouser/Digi-Key EU, AliExpress |
| Fuse holder, switches, wire, OBD-II socket, crimp terminals | €25 | Partco / Biltema |

## Tier 2 — the complete backup (pick one)

| Item | Price | Comment |
|---|---|---|
| Nothing — K-TAG Service Mode / KESS bench already on the owned tool | €0 | **Check this first** (§2.3) |
| Shop bench/BDM read in Finland | €50-150 | No tool risk; ask for raw external + internal + EEPROM |
| Clone BDM100 + universal BDM frame with adapters | €30-90 | Cheapest DIY; clone quality varies |
| Genuine Alientech BDM MPC5xx frame kit `144300KBDM` (+ Alientech frame) | several hundred € | Only worth it if the K-TAG is genuine and will be reused |
| Genuine EVC BDM100.K + BDM140.P + probe | €1,750+ | Reference prices only; not sensible for one ECU |
| PCMflash module 77 + Scanmatik 2 Pro (or PowerBox + J2534) | €300-700 | Bench, no opening; also useful later |

## Tier 3 — Windows box (≈ €0-180)

| Item | Price |
|---|---|
| Used Intel business laptop, Win 10/11 Pro, USB-A | €90-180 |
| External SSD/USB for the full disk image of the current K-Suite machine | €25-50 |
| Powered USB 2.0 hub (insurance against USB-3-only ports) | €12 |

## Tier 4 — nice to have

| Item | Price | Why |
|---|---|---|
| Ross-Tech HEX-V2 (genuine) | €250-450 | VCDS on a CAN car; optional, our own TP2.0 client does the real logging |
| USB microscope or good macro lens | €30 | Reading MCU/flash/EEPROM markings, photographing the BDM pads |
| ESD mat + wrist strap | €20 | Opening the ECU |
| Second (3.6) ECU as a sacrificial harness mule | €40-80 | Lets the matching 3.2 unit stay pristine |

---

# 7. Checklist — what to verify when the ECU is opened

Do this on the **spare** unit first. Photograph everything before touching
anything; a phone photo with a ruler in frame is enough.

**Identification**
- [ ] Case label: VW number + suffix, Bosch `0261S02xxx`, Bosch software
      `1037xxxxxx`, date code, serial. Compare with the car's unit.
- [ ] Any second label or sticker on the PCB (Bosch internal board revision).

**Semiconductors — the facts issue #2 asks for**
- [ ] **MCU marking.** Expect `MPC563` or `MPC564` (both 512 KB UC3F + 32 KB
      CALRAM); `MPC562` would mean a flashless 2 MB board and would contradict
      our dump. Record the full part number *and* the mask/revision suffix
      (e.g. `MPC563MZP56B`).
- [ ] **External flash marking.** Expect a 16 Mbit device; community reports
      **M58BW016-B (2048 KB)** for MED9.1.x. Record manufacturer and full part
      number.
- [ ] **SRAM chip** (the 32 KB external SRAM at CS1, 0x800000 in our map):
      part number and size. Confirms `docs/02_memory_map.md` §3.
- [ ] **Serial EEPROM** — 8-pin SOIC near the connector. Expect **95160 (2 KB)**
      or **95320 (4 KB)**. Record the exact marking; it determines the expected
      EEPROM file size.
- [ ] Devices on **CS2 (0x900000-0x93FFFF)** and **CS3 (0xA00000-0xA07FFF)**,
      currently "unknown" in the memory map. Anything besides flash and SRAM
      (a CAN expander? a watchdog/SPI ASIC? a second processor?) directly
      answers an open question in `docs/02_memory_map.md` §3.
- [ ] CAN transceivers: how many and which type. The map has three TouCAN
      modules (0x707080 / 0x707480 / 0x707880) — how many are populated and
      wired to the connector?

**BDM port**
- [ ] Photograph the 14-pad array at enough resolution to count pads, with a
      ruler, and note where it sits relative to the connector header.
- [ ] With an ohmmeter, find the **two grounded pads** → those are 3 and 5, so
      **pad 1 is to their left**; mark the orientation on the photo.
- [ ] Note whether the pads are bare, tinned, or under conformal coating.
- [ ] Note whether a 10-way header footprint is present (some boards have one).

**Mechanical / bench**
- [ ] How the case opens (clips vs. screws vs. sealant), and whether the PCB can
      be lifted without stressing the connector header.
- [ ] Whether the main relay is inside the ECU or in the car.
- [ ] Measured resistance CAN-H (68) ↔ CAN-L (67) with the unit off:
      **expect ≈ 66 Ω** per SSP 269. Write the measured value down.
- [ ] Measured current at 13.8 V: KL30 only; KL30+KL15; during a flash. These
      numbers do not exist anywhere in the project yet.

**Afterwards**
- [ ] Add the photos and all part numbers to `re/findings/` and update
      `docs/02_memory_map.md` §1 (MCU class) with the tag and date.

---

# 8. Open questions this desk work could not settle

1. **Which `03H906032` suffixes are genuinely 3.2 FSI.** Vendor catalogues and
   VW's North-American catalogue disagree (§1.6), and the 3.2 was a
   Europe-only engine so the free catalogues are biased towards the 3.6. Only a
   European ETKA/ETOS lookup, or the label photo of a specific unit, settles it.
   Workaround: buy on Bosch number + software number.
2. **Whether the owned K-TAG/KESS subscription still exposes protocol 64 and
   Service Mode for MED9.1.** Only the tool itself can answer (§4.4).
3. **Whether Alientech still sells or renews KESSv2 subscriptions.** Alientech
   UK says yes; several resellers say no (§4.3).
4. **T60 pinout for the VR6 unit.** Only the 2.0 TFSI T60 map is public. Our
   unit's injector/coil pins must come from a VW wiring diagram for the AXZ
   (SSP 340 "Passat 2006 electrical system" has the right chapters but no free
   full-text copy could be fetched:
   <https://en.volkswagenclub.net/manual_download.php?id=1139>). Not needed for
   KWP/CAN work; needed only if anything is ever actuated.
5. **Whether the Passat B6 harness actually connects the ECU's K-line pin 86.**
   The pin exists on the ECU; the car is CAN-diagnosed. Easy to test once the
   harness is built.
6. **Exact bench current draw, and whether the ECU sleeps without KL15.**
   Measurement, not research.
7. **A photograph of an `03H906032` board's BDM pads.** None found that is
   freely viewable. Our own spare will produce the best one.

## Tangent noted but not pursued (per the common rules)

While answering the OBD/gateway question it became clear that the ethanol-frame
work (issues #30/#31) needs a physical tap on the powertrain bus, that the ECU
connector pins 67/68 are the most convenient point, and that the SSP 269 rules
(66 Ω central termination in the ECU, terminal-15 switching, 5 m length limit)
apply to any node we add. That belongs to those issues, not to this brief.

---

## Source list

**VW / manufacturer documents**
- VW SSP 315 "Euro-On-Board-Diagnose" (T16 diagnostic-socket pinout): <https://vwcampersite.wordpress.com/wp-content/uploads/2015/01/ssp_315-euro-on-board-diagnose.pdf>
- VW SSP 269 "Datenaustausch auf dem CAN-Bus II" (bus rates, termination, gateway): <https://phaetonclub.com/images/companies/1/SSP269%20Datenaustausch%20auf%20dem%20CAN-Bus%20II.pdf>
- VW SSP 340 "Passat 2006 electrical system" (not fully retrievable): <https://en.volkswagenclub.net/manual_download.php?id=1139>
- VW parts catalogue (NA) for 03H906032C / BS: <https://parts.vw.com/p/Volkswagen__/Engine-Control-Module-ECM/47990925/03H906032C.html>, <https://parts.vw.com/p/Volkswagen__/Engine-Control-Module-ECM/48001108/03H906032BS.html>
- NXP MPC564 and the MPC561/563 reference manual: <https://www.nxp.com/products/MPC564>, <https://www.antoniosantoro.com/sheet/MPC561_3RM.pdf>

**Tool manufacturers**
- Alientech, K-TAG Service Mode for EDC16/MED9: <https://www.alientech-tools.com/news/k-suite-3-91/>, <https://www.alientech-tools.com/en/k-suite-4-18/>
- Alientech UK, KESS V2: <https://www.alientechuk.co.uk/pages/kess-v2>
- Alientech positioning frame: <https://alientech-usa.com/products/positioning-frame>
- K-TAG BDM MPC5xx frame kit 144300KBDM: <https://www.tuningtools.com/k-tag-positioning-frame-adapter-kit-bdm-motorola-mpc5xx?___store=en>
- EVC BDM100 product page and manual: <https://www.evc.de/en/product/bdm/Default.asp>, <https://www.evc.de/ftp/winols/BDM100-en.pdf>
- PCMflash 1.2.2, module 77: <https://chiptuningshop.com/news/pcmflash-1-2-2-released/>
- bFlash MED9/EDC16 bench mode: <https://chiptuningshop.com/news/bflash-update-v1906a-med9-edc16-bench-mode/>
- VF2 Flasher (MED9.1.x MPC563/564, M58BW016-B): <https://chiptuningshop.com/news/vf2-flasher-news/vf2-flasher-v2-4-0-0/>
- Autotuner MED9.1 (MPC562): <https://www.autotuner.com/pages/ecu/bosch-med91-mpc562>
- DFOX bench MPC563/564: <https://www.dfbtechnology.com/en/new-bench-mode-for-ecu-edc16-with-mpc563-and-mpc564-added/>
- ECUHELP ECU Bench Tool: <https://www.ecuhelpshop.com/products/ecu-bench-tool-ecu-programmer.html>
- Ross-Tech: measuring blocks <https://www.ross-tech.com/vcds-lite/manual/measblocks.html>; Passat 3C immobilizer <https://wiki.ross-tech.com/wiki/index.php/VW_Passat_(3C)_Immobilizer>; Immobilizer IV ECU swapping <https://wiki.ross-tech.com/wiki/index.php/Immobilizer_IV_ECU_Swapping>

**Pinouts**
- transpondery VAG MED9xx: <https://www.transpondery.com/pinouts/vag/med9x_pinout.html>
- rusefi wiki, Passat B6 MED9.1 (2.0T): <https://github.com/rusefi/rusefi/wiki/VolkswagenPassatB6> (raw: <https://raw.githubusercontent.com/wiki/rusefi/rusefi/VolkswagenPassatB6.md>)
- ECUTools Vietnam, PCMflash module 77 MED9.1/9.1.1 pinout: <https://ecutools.vn/en/post-ecu/pinout-pcmflash-module-77-med9-1-med9-1-1-vag-ecu/>
- Connector housings: <https://wolfautoparts.com/94pin-engine-module-ecu-wiring-harness-connector-3c0906385-ps39045.html>, <https://www.ecstuning.com/b-genuine-volkswagen-audi-parts/ecu-connector-housing-94-pin/7l0906385a/>, <https://www.autoecupart.net/products/one-pair-bosch-edc17-edc16-94pin-60-pin-ecu-connector-harness-cable-for-audi-vw>

**Part-number / file databases (vendor catalogues; treat as COMMUNITY)**
- Cartech Electronics search: <https://www.cartechelectronics.com/search?q=03H906032&type=product&options%5Bprefix%5D=last>
- OBDTotal 03H906032AB: <https://obdtotal.com/product/vw-passat-b6-3-2-fsi-v6-bosch-med9-1-03h906032ab-0261s02349-1037384761-0764-ecu-stock-firmware/>
- Dyno-ChiptuningFiles 03H906032: <https://www.dyno-chiptuningfiles.com/original-ecu-files-database/bosch-med9-480090/>
- ecubin 03H906032 9951 0261S02226: <https://www.ecubin.com/original-ecu-files-hw-03h906032-9951-0261s02226>
- ZipTuning: <https://www.ziptuning.com/ecu-tuning-file/bosch-med9-1-03h906032ab-0261s02349-396864-ecu-tuning-files/>
- automoto-firmware MED9.1 list: <https://automoto-firmware.com/index.php?a=downloads&b=tags&tag=MED9.1>

**Forums (marked as such throughout)**
- nefariousmotorsports, "looking for 3.2 FSI AXZ MED9.1 03H906032": <http://nefariousmotorsports.com/forum/index.php?topic=11645.0> (TLS certificate expired; read through a text-extraction proxy)
- pcmhacking, "Bosch MED9.1.1": <https://pcmhacking.net/forums/viewtopic.php?t=8487> (Cloudflare-protected; read via search snippet only)
- mhhauto, MED9.1.2 pinout: <https://mhhauto.com/Thread-MED9-1-2-Pinout>

**Marketplaces / services**
- Nettivaraosa: <https://www.nettivaraosa.com/volkswagen-passat-varaosat>
- Autopurkaamot.com: <https://www.autopurkaamot.com/>
- Ovoko / rrr.lt: <https://rrr.lt/en/used-part/dra25594-03h906032-volkswagen-passat-b6-engine-control-unit-module>
- Chip Tuning Finland: <https://chiptuningfinland.com/>, <https://chip-tuning.fi/jalleenmyyjat/>
- TuningChip Suomi: <https://www.tuningchip.fi/kontakt_fi>
- Hestec / Special Tuning Harinen: <http://www.hestec.fi/>
- CANable: <https://canable.io/>, <https://openlightlabs.com/products/canable-2-0>
- customecm bench harnesses: <https://www.customecm.com/bench-harnesses>
