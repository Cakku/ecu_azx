# Variant criteria and the `vkKraQu` fuel-quality byte

Agent B4, brief `docs/agent_briefs/B4_variant_byte_and_eeprom.md`, issue #18.
Date: 2026-09-15. Dump: `data/passat_azx_ori.bin` (SHA-256 `b155…09b3`,
unmodified).

---

## Verdict

**`vkKraQu` — or any equivalent fuel-quality variant byte that switches a
family of calibration maps — is ABSENT from `1037382557` / `D9133_43K6P0`.**

Confidence: **VERIFIED-STATIC** for each individual piece of evidence below;
the conclusion is a negative, so it is as strong as the search was wide. The
search covered the whole 2.5 MB image (both the external-flash and the on-chip
flash region) and three independent angles: the documentation, the literal
community patch site, and the code shape a map-set switch must have.

Practical consequence for `docs/05_flexfuel_design.md`: **there is no stock
hook to reuse.** A flex-fuel map blend has to be built, and the ethanol value
has to be stored somewhere we choose (see `re/findings/eeprom.md` §5).

---

## 1. Documentation: the criterion does not exist on this platform

`re/findings/fr_index.md` §10 (agent A2, from
`documents/MED9.1_TFSI_Funktionsrahmen.pdf`, module `VARLC`, p38-66) lists the
complete set of variant criteria for SG-MED9-1:

```
vkADR, vkAbgVar, vkAbsMkt, vkAnhSt, vkAsrEsp, vkCAN, vkDaDrKr, vkELuef,
vkElZWP, vkFrQtro, vkFzgKl, vkFzgTyp, vkGangSt, vkGeArt1…4, vkIndex, vkKlima,
vkKraSt, vkLueftAk, vkMarke, vkNiveau, vkParFil, vkPedCh, vkVoNaGe, vkXyz
```

`vkKraQu` is not among them; the fuel-related criterion is **`vkKraSt`**
(Kraftstoffart/-sorte). The community patches that NOP a `vkKraQu` write are
for **1K8907115F/L**, a 2.0 TFSI MED9.1 dataset that is newer than both this
FR edition and our 2006 dataset. Source tag: COMMUNITY for the patch, and the
FR itself for the criterion list.

Our dump contains **no FR label strings** at all (`re/findings/fr_index.md`
§0), so nothing here can be confirmed or refuted by a string search. Everything
below is structural.

## 2. The literal community patch site is not present

The 1K8907115F/L patch NOPs `stb r10,-0x1122(r13)`. With our
`r13 = 0x7FFFF0` (`docs/02_memory_map.md` §4) that displacement resolves to
RAM **0x7FEECE**.

Scanning every D-form instruction in the image with `rA = r13` and
displacement `-0x1122`:

```
$ sda_refs.py --base r13=0x7FFFF0 --range 0x7FEECE 0x7FEECE
  file 0x0ad110 (cpu 0x0ad110)  sth   r30, -4386(r13) -> 0x7feece
1 hits
```

**No `stb` at that displacement anywhere.** The single hit is a 16-bit store,
in an unrelated function. As expected for a different software number — the
offset was never going to transfer — but it is worth recording that the naive
"apply the community patch" route is closed.

## 3. Code shape: nothing switches a family of maps on one byte

A variant criterion that selects between parallel map sets (`VARLCUW` p67:
`KFxxx_0_A` / `KFxxx_1_A` / …) must compile to code that loads **one of two
calibration addresses into the same register under a conditional branch**, at
roughly as many sites as there are switched maps (the community claim for
`vkKraQu` is ~17).

Method (scratch scripts, reproducible from this description):

1. Find every `lis rX,hi` + `addi/ori rX,rX,lo` pair in the whole image whose
   result lands in the calibration window 0x5C0000-0x5E2FFF.
   **2105 constructions.**
2. Keep pairs of such constructions that (a) are within 0x30 bytes of each
   other, (b) target the **same** destination register, (c) resolve to
   **different** addresses, and (d) have a conditional branch between or just
   before them. **327 candidate selection sites.**
3. For each candidate, back-scan 0x40 bytes for the `lbz` that feeds the
   compare, and histogram the byte it reads. **168 candidates resolve.**

Result — the ten bytes that drive the most selection sites:

| RAM byte | sites | identity |
|---|---:|---|
| 0x7FEF74 | 11 | **engine load `rl`** — `re/measuring_vars.csv` id 2, formula 0x21 A=0x85 |
| 0x7FCE95 | 10 | **engine speed `nmot`** — `re/measuring_vars.csv` id 1, formula 0x01 A=0xC8 |
| 0x8021F6 | 8 | measuring var id 442, formula 0x05 A=0x0A |
| 0x7FD3F7 | 7 | — |
| 0x800ED1 | 6 | — |
| 0x801062 | 6 | — |
| 0x8019A6 | 4 | — |
| 0x7FEB74 | 4 | — |
| 0x7FEBDF | 4 | — |
| 0x5C69FC | 4 | a *calibration* byte, i.e. a compile/calibration-time switch |

The distribution has no dominant byte: the top two are the **map axis
variables** (rpm and load), which is exactly what a breakpoint/interpolation
branch looks like, and the tail falls off immediately. Nothing comes near a
single byte selecting 17 map pairs.

Cross-check from the other direction: scanning the whole image for runs of
consecutive 32-bit words that are themselves calibration pointers (the shape a
"map set selector table" would have) finds 12 runs, of which the only
substantial ones are the diagnostic pointer tables at file 0x1C5E1C-0x1C6200
and two 12-entry arrays at 0x1CB158 / 0x1CB194 whose targets are 0x32 bytes
apart (per-record pointers, not two alternative map sets).

## 4. What variant/coding machinery this ECU *does* have

Recorded so the negative above is read in context. Details and evidence in
`re/findings/eeprom.md`.

* **KWP SID 0x3B (WriteDataByLocalIdentifier)**, handler `kwp_sid_3B_h1`
  at 0x0375E8 -> `FUN_000A376C`. Its record table at **file 0xA2C9C**
  (8-byte entries `{localId, nFields, 0x01, 0x00, ptr}`, terminated by a zero
  count) accepts exactly **two** record local identifiers:

  | localId | fields | descriptor | len | handler |
  |---|---|---|---|---|
  | 0x9A | 1 | file 0xA2CB4, flags 0x12 | 14 B | 0x0A25A8 |
  | 0xBC | 1 | file 0xA2CBC, flags 0x13 | 7 B | 0x0A2938 |

* The 0xBC handler `FUN_000A2938` is gated by a security/session state and
  stores its payload as **6 bytes at EEP_CONF block 7 offset +2** plus a
  **u16 at block 7 offset +12**, then commits block 7. That is the coding
  write path.

* The 6-byte item at block 7 +2 is read back only at cpu 0x43A150/0x43A178
  (the KWP services in on-chip flash). **No engine-control function reads
  it.** The u16 at +12 is used by two small accessor functions
  (`FUN_00134250`, and 0x0A284C in the handler itself).

* Further identification/coding data sits in blocks 1 (a 7-byte item at +13,
  the best candidate for the VW long coding word), 2 (three string fields read
  over KWP) and 3; every constant-offset client of those blocks is a KWP
  read/write handler.

So the ECU stores and reports its coding, but in this dataset nothing in the
fuelling path branches on it. Under `VARLCUW` that is the expected state when
no monitoring-relevant parameter is coded-variant in the dataset.

## 5. Where a flex-fuel switch would have to go instead

Not pursued here (other briefs own it), noted so the search is not repeated:
the absence of a stock selector means the blend must be inserted in the
fuelling path itself rather than by flipping an existing variant byte, and the
ethanol value needs its own non-volatile home — `re/findings/eeprom.md` §5
proposes **EEP_CONF block 8, payload offset +0** (duplicated, one page, one
stock client).

## 6. Residual uncertainty

* A negative from a structural scan cannot be absolute. The scan window was
  0x30 bytes between the two pointer constructions and 0x40 bytes back to the
  compare; a selector written with a wider gap, through a pointer table
  indexed by the variant byte, or with the byte loaded into a register long
  before the branch, would be missed. The pointer-table cross-check in §3
  covers the first of those.
* Eight `FUN_0006131C` call sites use a run-time block index and were not
  attributed (`re/findings/eeprom.md` §4). If a fuel-quality byte were read
  through one of those, it would not appear in §4's client map — but it would
  still have had to reach a map selection, which §3 rules out.
* A dynamic check is cheap once a bench ECU exists: log the ~10 candidate
  bytes of §3 over a drive cycle and confirm they behave as rpm/load/etc.
