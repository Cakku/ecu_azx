# Measuring variables (TKMWL), measuring-block groups and the ECU id

Brief A3 / issue #10. Dump `data/passat_azx_ori.bin`, sha256
`b15590d3f1874ace3125c5d047c09a686db9b8bb498187663539ebab205609b3`.
Date 2026-09-15. All addresses are CPU addresses; `file` prefixes file offsets.

## 1. Result in one table

| Fact | Address | Tag |
|---|---|---|
| Measuring-variable dispatcher (`r3` = variable id) | 0x045768 (file 0x045768) | VERIFIED-STATIC |
| **Handler pointer table (TKMWL), 2200 x 4 B** | **0x0A5658-0x0A78B7** (file 0x0A5658) | VERIFIED-STATIC |
| "Not implemented" handler stub, used by 1535 of 2200 ids | 0x038EC4 | VERIFIED-STATIC |
| Result helper: writes the (formula, A, B) triple | 0x038EB4 | VERIFIED-STATIC |
| Result buffer: formula / B / A | 0x7FD06F / 0x7FD070 / 0x7FD071 | VERIFIED-STATIC |
| Handler flag/bit scratch byte, cleared by the dispatcher | 0x7FD072 | VERIFIED-STATIC |
| Requested measuring-block group number | 0x7FD05E | VERIFIED-STATIC |
| **Measuring-block group table, 4 x 255 x u16** | **0x5C5518** (file 0x1C5518) | VERIFIED-STATIC |
| Group reader (4 fields, KWP path) | 0x035748 / 0x03574C | VERIFIED-STATIC |
| Single-field reader (on-chip-flash path) | 0x0357E0 | VERIFIED-STATIC |
| Implemented variables | 665 of 2200 ids | VERIFIED-STATIC |
| Variables whose RAM location was resolved | **660**, all inside 0x7F8000-0x8073E9 | VERIFIED-STATIC |
| Distinct RAM addresses behind them | 543 (297 on-chip SRAM, 363 external SRAM) | VERIFIED-STATIC |
| Variables given a name | 6, all `cand_` | COMMUNITY / HYPOTHESIS |

Machine-readable results: `re/measuring_vars.csv` (one row per implemented id),
`re/findings/measuring_groups.txt` (group -> four variable ids),
`re/findings/med9inf_output.txt` (raw tool output).

Reproduce everything with

```bash
python3 tools/measuring_vars.py data/passat_azx_ori.bin --csv re/measuring_vars.csv
python3 tools/measuring_vars.py data/passat_azx_ori.bin --groups
python3 tools/measuring_vars.py data/passat_azx_ori.bin --explain 1
```

## 2. MED9inf: build and input layout

`https://github.com/360trev/MED9inf` (commit 9f2bb54, two files) does not build
with clang as published: `find_signature()` declares its buffer parameter as
`int rom_load_addr`, which truncates a 64-bit pointer. One line was changed in
the clone under `work/MED9inf/` (gitignored):

```diff
-signed int find_signature(int rom_load_addr, unsigned char *signature, ...
+signed int find_signature(unsigned char *rom_load_addr, unsigned char *signature, ...
```

```bash
git clone https://github.com/360trev/MED9inf work/MED9inf
# apply the one-line fix above
clang -O1 -Wno-int-conversion -Wno-pointer-sign -Wno-format -o med9inf main.c
```

The remaining warnings are the author's `%s`-with-`unsigned char*` calls and are
harmless. No other change was needed.

**Input layout.** MED9inf assumes the ROM's data segment is addressed at
0x400000 (`MED9_ROM_DATA_OFFSET`) and subtracts that from every pointer it
follows. That happens to be exactly right for our ECU as well, but for a
different reason: our external flash is mapped at 0x000000 and its upper half
is *also* visible through the high alias 0x480000-0x5FFFFF, and the firmware
stores the identification-string pointers as high-alias addresses
(0x5CEE20 -> file 0x1CEE20). So `pointer - 0x400000` is the correct file
offset here, and **no adaptation of the input was needed**: both
`med9_re/passat_azx_flash.bin` (first 2 MB) and the full 2,605,056-byte dump
give byte-identical output, because everything the tool looks at lives in the
first 2 MB. The on-chip-flash tail at file 0x200000 is never touched by it.

**ECU id cross-check** (task 3): MED9inf prints `0261S02226`, `1037382557`,
`4.7.6 A`, `03H906032`, `9387`, `P3.2 FSI-EU4`. Every field matches
`docs/02_memory_map.md` section 1 exactly. MED9inf reaches them through a
28-entry x 8-byte descriptor table found at file 0x0A24BC (each entry is a
4-byte tag plus a pointer); `docs/02` found the same strings by scanning the
identification block at file 0x1CEE20. Two independent routes, same values.

MED9inf's measuring-variable support is a stub — `// FIXME: Walk through and
dump variables memory table AND dump variables..` It prints only the element
count (2200) and the function offset (0x45768). Everything below is our own
work built on those two numbers.

## 3. How the measuring path works

The dispatcher found by MED9inf (0x045768) is eleven instructions:

```
00045768  9421FFF0  stwu   r1,-0x10(r1)
0004576C  7C0802A6  mflr   r0
00045770  93E1000C  stw    r31,0xC(r1)
00045774  90010014  stw    r0,0x14(r1)
00045778  28030898  cmplwi r3,0x898          ; 2200 variable ids
0004577C  4080002C  bge    0x457A8           ; out of range -> "not available"
00045780  3D80000A  lis    r12,0xA           ; \
00045784  398C5658  addi   r12,r12,0x5658    ; / r12 = 0x000A5658  <= the table
00045788  546B103A  rlwinm r11,r3,2,0,29     ; r11 = id * 4
0004578C  7FEC582E  lwzx   r31,r12,r11       ; handler = table[id]
00045790  7FE803A6  mtlr   r31
00045794  39400000  li     r10,0
00045798  93EDB8F8  stw    r31,-0x4708(r13)  ; 0x7FB8E8 = last handler called
0004579C  994DD082  stb    r10,-0x2F7E(r13)  ; 0x7FD072 = clear the flag byte
000457A0  4E800021  blrl                     ; call the handler
```

Each handler is a few instructions that read one RAM location and hand a
three-byte result to 0x038EB4:

```
00038EB4  986DD07F  stb r3,-0x2F81(r13)   ; 0x7FD06F  VAG display formula id
00038EB8  988DD081  stb r4,-0x2F7F(r13)   ; 0x7FD071  byte "A" (usually a constant)
00038EBC  98ADD080  stb r5,-0x2F80(r13)   ; 0x7FD070  byte "B" (the measured value)
00038EC0  4E800020  blr
```

Example, variable id 1 (handler 0x03915C):

```
0003915C  38600001  li  r3,1              ; formula 0x01
00039160  388000C8  li  r4,0xC8           ; A = 200
00039164  88ADCEA5  lbz r5,-0x315B(r13)   ; B = byte at RAM 0x7FCE95
00039168  4BFFFD4C  b   0x38EB4
```

1535 of the 2200 ids point at the stub 0x038EC4, which reports formula 0x25
with A = B = 0, i.e. "value not available in this software".

### The KWP2000 path (task 5: what makes the location VERIFIED-STATIC)

```
tbl_kwp_services entry file 0x2B884, SID 0x21 -> handler 0x035F6C
  0x035F6C  local id 0x01-0x7F and 0xA0-0xEF -> bl 0x0A2CC4
  0x0A2CC4  bl 0x03583C
  0x03583C  lbz/stb the requested group number to 0x7FD05E, range-check, then
            bl 0x035748 -> 0x03574C
  0x03574C  addi r29,r2,-0x4AD8          ; r2 = 0x5C9FF0 -> group table 0x5C5518
            loop r30 = 0..3 (the four fields of the group)
              lhzx r3,(r29 + r30*0x1FE),(group*2)     ; r3 = variable id
              bl   0x045768                            ; <== the dispatcher
              copy 0x7FD06F/0x7FD071/0x7FD070 into the response buffer
```

`tools/find_abs_refs.py data/passat_azx_ori.bin --range 0xA5650 0xA78C0`
reports exactly one absolute reference into the whole table,
`site file 0x045780 ... addi -> 0x000a5658`, which is the dispatcher above.
The table is reached only through it, and the dispatcher is reached only from
0x03574C and 0x0357E0, both of which sit on the SID 0x21 route. That closes
the chain from the KWP service table to the table bytes.

A second route exists from the on-chip flash: 0x0357E0 (same group table, one
field at a time) is called four times from 0x00439F8C-0x00439FE0, and
0x0357BC (which only sets the group number) from 0x004393C0 — i.e. from the
region that also holds the SID 0x31/0x32/0x81/0x82 handlers.

**Negative result:** SID 0x2C (DynamicallyDefineLocalIdentifier, handlers
0x034D28/0x035034) does *not* use this table; the only `bl` sites reaching
0x045768 in the whole dump are 0x03577C and 0x035808.

### Correction to the 0xA5654 candidate

`docs/02_memory_map.md` section 7 listed `0xA5654 TKMWL measuring-variable
table (candidate, MED9Toolchain signature "blr 00 03")`. The signature matched
the four bytes `4E800020` (`blr`, the last instruction of the function that
precedes the table) immediately followed by the first table entry
`00038EC4`. The table therefore starts **4 bytes later, at 0x0A5658**, which
is what the dispatcher's own `lis`/`addi` pair says. The candidate is
superseded, not confirmed; the byte signature was off by one instruction.

Likewise `0x38EA8 measuring-block return helper (candidate)` is 12 bytes
short: 0x038EA8 is the tail of an unrelated flag routine that ends at
0x038EB0; the real result helper starts at **0x038EB4**.

## 4. Extraction method and its limits

`tools/measuring_vars.py` locates the dispatcher with MED9inf's own signature
mask (so nothing about the table is hard-coded), reads the 2200 pointers, and
interprets each handler with a small PowerPC walker that

- starts with r13 = 0x7FFFF0 and r2 = 0x5C9FF0 (docs/02 section 4),
- tracks, per register, a constant and the set of RAM locations the value came
  from, so the address reported is the one that actually reaches byte B of the
  result, not merely the first load in the function,
- models stores, so a handler that writes a byte and reads it back (the
  bit-field handlers use 0x7FD072 as an accumulator) is not credited with a
  bogus variable — the dispatcher's own clearing store seeds that byte with 0,
- explores both sides of conditional branches with a step budget and inlines
  the shared converter tails (0x03906C, 0x0390C0, 0x039108, ...).

Every one of the 660 resolved addresses falls inside the verified r13 window
0x7F8000-0x8073E9, i.e. inside on-chip SRAM 0x7F8000-0x7FFFFF or external
SRAM 0x800000-0x807FFF. **No address landed outside**, which is a useful
self-check on the whole decode.

Limits, stated plainly:

- The address is where the *display* copy lives. Many handlers read a byte
  that the application already scaled for the measuring block (id 1 reads a
  byte of rpm/40, not the 16-bit `nmot_w`). Do not assume the recovered
  address is the FR variable of the same name.
- For bit-field (formula 0x10) and text (formula 0x25) handlers the reported
  byte B is a constant per branch, so the CSV reports the first RAM byte the
  handler examines and lists the others in the evidence column.
- 5 ids have no RAM source: id 999 (handler 0x041FFC) reports three
  *calibration* bytes at 0x5C6094-0x5C6096, and ids 1922-1925 (handlers
  0x04568C-0x04571C) call stubs at 0x0B2AA4-0x0B2ABC that return a fixed
  0x250000, i.e. they are unimplemented in this software version.
- 28 variable ids referenced by the group table point at the "not implemented"
  stub: 235-238, 338, 343, 477, 512, 513, 523, 552, 827, 844, 845, 891, 1164,
  1461-1464, 1496, 1499, 1517-1520, 1537, 1746. Those measuring-block fields
  read as "not available" on this ECU.

## 5. Naming: what was and was not done

The firmware contains **no names**. There is no ASCII anywhere in the handler
code block 0x038E00-0x045800, in the handler table, or in the group table, and
MED9inf supplies none either. The FR index from brief A2 is not merged into
`main` at the time of writing, so it was not used.

Names were therefore left blank for 654 of the 660 located variables, per the
brief ("blank is better than a guess"). Six ids carry a `cand_` name where two
signals agree — the VAG display formula the handler itself emits (a hard fact
from the dump) and the position the variable holds in a measuring block group
whose meaning is documented across the whole VAG range (COMMUNITY):

| id | RAM | width | formula / A | name | reasoning | tag |
|---|---|---|---|---|---|---|
| 1 | 0x7FCE95 | 1 | 0x01, A=0xC8 | `cand_mw_nmot` | group 001 field 1; VAG formula 0x01 is the rpm formula, A=200 gives 40 rpm per count | COMMUNITY |
| 2 | 0x7FEF74 | 1 | 0x21, A=0x85 | `cand_mw_rl` | group 002 field 2 (load); formula 0x21 is the percentage formula | COMMUNITY |
| 10 | 0x7FEFA2 | 2 | 0x19 | `cand_mw_ml` | group 003 field 2 (mass air flow); formula 0x19 is the g/s formula | COMMUNITY |
| 14 | 0x7FCE96 | 1 | 0x01, A=0x32 | `cand_mw_nmot_fine` | rpm formula at 10 rpm per count, RAM byte immediately after id 1; group 050 field 1 | HYPOTHESIS |
| 15 | 0x802529 | 1 | 0x01, A=0x32 | `cand_mw_nsoll` | group 050 field 2, same rpm scaling as id 14 (idle speed control group) | HYPOTHESIS |
| 80 | 0x8021EF | 1 | 0x05, A=0x0A | `cand_mw_tmot` | group 001 field 2 (coolant temperature); formula 0x05 is a temperature formula | COMMUNITY |

The exact VAG formula -> display-value arithmetic (`0.2*A*B` and friends) is
community knowledge and is deliberately **not** recorded as fact here; only
the formula id and the constant A the firmware emits are, because those come
from the dump. `scaling_or_blank` in the CSV holds exactly that.

Worth a follow-up, not named here: ids 88-93 read six consecutive bytes
0x7FCE57-0x7FCE5C with identical handler shape (formula 0x22, A=0x4B) and are
grouped as (88,92), (90,93), (89,91) by groups 020-024 — a six-cylinder
per-cylinder quantity, which for these VCDS groups is normally ignition retard
from knock control. Cylinder numbering cannot be assigned statically from the
table alone.

## 6. What a follow-up brief can use this for

- `re/findings/measuring_groups.txt` is a ready-made VCDS group map: pick the
  group a log will use, look up its four ids, and get the four RAM addresses
  from `re/measuring_vars.csv`.
- The 543 distinct RAM addresses are a large, evidence-backed seed for the
  Ghidra symbol import, and every one of them is a variable the ECU itself
  considers worth publishing.
- For flex-fuel work, the ids whose handlers read lambda/fuel-trim-shaped
  values are the quickest static route to `fra`/`frau`/`frao`; they need the
  FR index (brief A2) to be named, which is why they are blank here.
- The dispatcher is also a clean hook candidate for a bench logger: it is
  called once per field with the id in r3 and leaves the result in three
  fixed RAM bytes.

---

## 7. Formula cross-check against the ECU's own arithmetic (C3, 2026-09-16, #20)

Section 5 deliberately recorded only the formula id and the constant `A`,
because the formula-to-display arithmetic is community knowledge. Running the
real group handlers under `logging/ecu_sim.py` closes that gap for two of them
**without trusting the community table**: the ECU's own handler is fed a known
RAM byte and the emitted `B` is compared with what an independently derived
scaling predicts.

### 7.1 Formula 0x05 is `T = 0.1 x A x (B - 100)` -- CROSS-CHECKED

Measuring id 80 (group 001 field 2) reads **0x8021EF** and emits
`(0x05, A=0x0A, B)`. `re/findings/start.md` (brief B8) says 0x8021EF is `tmot`
with **T = 0.75x - 48 °C**, derived from the firmware, not from the display
path. The two agree exactly:

| 0x8021EF | 0.75x - 48 (start.md) | B emitted | 0.1 x 10 x (B-100) |
|---|---|---|---|
| 0x00 | -48.00 | 52 | **-48.00** |
| 0x30 | -12.00 | 88 | **-12.00** |
| 0x40 | 0.00 | 100 | **0.00** |
| 0x60 | 24.00 | 124 | **24.00** |
| 0x80 | 48.00 | 148 | **48.00** |
| 0xA0 | 72.00 | 172 | **72.00** |
| 0xC0 | 96.00 | 196 | **96.00** |
| 0xFF | 143.25 | **243 (clamped)** | 143.00 |

Two independent chains — a static read of the coolant path and the ECU's own
display handler — give the same number for every value up to the clamp. So:

* **formula 0x05 = 0.1 x A x (B - 100), °C** — VERIFIED-DYNAMIC (emulated real
  handler) *for this ECU*, no longer just COMMUNITY;
* **0x8021EF is `tmot` and its scaling is 0.75x - 48 °C** — independently
  confirmed, which settles the first row of issue **#44** without a car;
* the display byte **saturates at B = 243 = 143 °C**; anything hotter reads
  143 °C in VCDS. Do not use the measuring block near the top of the range —
  log 0x8021EF over DDLI instead.

Reproduce: `python3 logging/ecu_sim.py --self-test` for the handler, and
`tests/test_med9kwp.py::TestGroups::test_formula_05_matches_tmot_scaling`
for the table above.

### 7.2 Formula 0x01 is `n = 0.2 x A x B`, rpm -- CROSS-CHECKED

Id 1 (group 001 field 1) reads 0x7FCE95 and emits `(0x01, A=0xC8, B=x)`;
`0.2 x 200 x B = 40 x B`, which is exactly the "40 rpm per count" section 5
inferred from A alone. Checked for x = 0, 20, 75, 200 (0, 800, 3000, 8000 rpm).

### 7.3 Formula 0x53 is `p = ((A<<8)|B) x 0.01` bar -- CROSS-CHECKED

Measuring id **500** reads **0x8031DA** (`prist_w`) and id **501** reads
**0x8031F4** (`prsoll_w`), both with formula **0x53**.
`re/findings/rail.md` section 2 gives both as u16 at **0.005 bar/LSB**, derived
from the controller code. Varying 0x8031DA and reading group 106 field 1:

| 0x8031DA | x 0.005 (rail.md) | A | B | (A<<8)\|B | x 0.01 |
|---|---|---|---|---|---|
| 0 | 0.00 | 0x00 | 0x00 | 0 | **0.00** |
| 2000 | 10.00 | 0x03 | 0xE8 | 1000 | **10.00** |
| 12000 | 60.00 | 0x17 | 0x70 | 6000 | **60.00** |
| 20000 | 100.00 | 0x27 | 0x10 | 10000 | **100.00** |
| 40000 | 200.00 | 0x4E | 0x20 | 20000 | **200.00** |
| 65535 | 327.68 | 0x7F | 0xFF | **32767 (clamped)** | 327.67 |

So the handler simply halves the raw word (0.005 -> 0.01 bar/LSB) and the
tester multiplies by 0.01. That confirms, without a car:

* **formula 0x53 = ((A<<8)\|B) x 0.01, bar** -- and it is **not in any of the
  public formula tables consulted**, which stop well before 0x53;
* **0x8031DA (`prist`) and 0x8031F4 (`prsoll`) really are 0.005 bar/LSB**, the
  third row of issue **#44**;
* the display word saturates at 32767 = **327.67 bar** (a signed-16 clamp).

**Correction to the plan in #44:** group **140** does *not* contain `prsoll`.
Group 140 is `(1005, 1006, 500, 1689)` -- field 3 is `prist` and fields 1-2 are
0x803168/0x803164 with formula 0x5B. The group that holds **both** rail
pressures next to each other is **231** = `(1006, 501, 500, 1689)`, i.e.
field 2 `prsoll`, field 3 `prist`. Neither 140 nor 231 can be requested
directly (section 12.3 of `kwp.md`); ask for **104** and read the second half
of the answer, or for **13** to get group 140:

```bash
python3 logging/med9log.py groups --sim 231     # prints "reading group 104"
```

### 7.4 What was NOT verified

Id 2 emits `(0x21, A=0x85, B = the raw byte of 0x7FEF74)`, so `100 x B / A`
reads 100 % when the byte is 0x85 = 133 — consistent with "0x21 is the
percentage formula", but there is no second chain for 0x7FEF74's own scaling,
so 0x21 stays **COMMUNITY**. The other 41 formula ids this dataset emits stay
COMMUNITY or unknown; `logging/med9kwp/vag_formulas.py` carries the table with
a per-entry tag and prints the raw `(formula, A, B)` triple for everything it
does not claim to know. The ids actually used by this dataset, by frequency:

```
0x10 148   0x25  90   0x36  70   0x1F  50   0x21  39   0x22  34   0x14  31
0x05  30   0x3D  20   0x15  12   0x53  11   0x19  10   0x1A   9   0x08   8
0x17   7   0x42   7   0x5B   7   0x01   6   0x12   6   0x07   5   0x34   5
0x3E   5   ... (43 distinct ids, 660 implemented variables)
```

Reproduce with
`python3 -c "import csv,re,collections; ..."` over `re/measuring_vars.csv`, or
`python3 logging/med9log.py groups --formula-table`.
