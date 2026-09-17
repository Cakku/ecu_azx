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
so 0x21 stays **COMMUNITY**.
**SETTLED 2026-09-17 (E3, #41) — see §7.5: the handler passes the raw byte
through unchanged, so `A = 133` is a tester-side normalisation and says
nothing about the ECU's own LSB, which is `100/128 %`. The two readings never
were in conflict.** The other 41 formula ids this dataset emits stay
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

### 7.5 The u8 `rl` scaling, settled (E3, 2026-09-17, #41)

D3 read the u8 `rl` (0x7FEF74) as **100/128 %/LSB** (`calibration_names.md`
§2.1) from the breakpoint identity `SRL11OPUW = SRL12ZUUW / 32`; §7.4 above
read display formula 0x21 with `A = 133` as "133 counts = 100 %", i.e.
100/133 %/LSB. Brief E3 was asked to decide it with the ECU's own measuring
handler. Two chains, both run on this dump:

**1. The conversion instruction (VERIFIED-STATIC, decisive).** The u8 is
written at three sites, each one right after the `sth` of `rl_w` (0x7FEFB2):

```
00419268  rlwinm r12,r5,0x0,0x10,0x1f   ; r12 = rl_w & 0xFFFF
0041926c  cmplwi r12,0x1fe0             ; 0x1FE0 = 8160 = 255 * 32
00419270  sth    r5,-0x103e(r13)        ; rl_w   = r5
00419274  ble    0x00419280
00419278  li     r6,0xff                ; clamp
00419280  rlwinm r6,r5,0x1b,0x15,0x1f   ; r6 = (rl_w >> 5) & 0x7FF
00419284  stb    r6,-0x107c(r13)        ; u8 rl = rl_w >> 5
```

and the two initialisation sites 0x11BC38 / 0x12D8B4 write the pair as
`rl_w = 0x10AB = 4267` and `u8 rl = 0x85 = 133` in the same breath
(4267 >> 5 = 133). So **the u8 `rl` is `rl_w >> 5`** and 1 LSB is
`32 x 100/4096 = 100/128 % = 0.78125 %`. D3 is right, and this is now an
instruction, not an inference from a breakpoint grid.

**2. The measuring handler in the emulator (VERIFIED-DYNAMIC).** Running the
real handler for id 2 over a sweep of 0x7FEF74 (`logging/ecu_sim.py`'s
`Med9Handlers`, exactly as §7.1 did for formula 0x05) shows the handler
emits **B = the raw byte, unchanged**, with a constant `A = 0x85`:

| 0x7FEF74 | emitted `(fmt, A, B)` | tester reads `100 B / A` | internal `100/128 x raw` |
|---|---|---|---|
| 13 | (0x21, 0x85, 13) | 9.77 % | 10.16 % |
| 67 | (0x21, 0x85, 67) | 50.38 % | 52.34 % |
| 128 | (0x21, 0x85, 128) | 96.24 % | 100.00 % |
| 133 | (0x21, 0x85, 133) | 100.00 % | 103.91 % |
| 255 | (0x21, 0x85, 255) | 191.73 % | 199.22 % |

**So there is no contradiction.** The handler does no arithmetic at all; `A`
is a normalisation the ECU hands the tester, and VCDS therefore shows 100 %
where the ECU's own maps see 103.9 %. Formula 0x21 stays COMMUNITY as a
*formula*, but "133 counts = 100 %" was never a statement about the firmware.
**Use 100/128 %/LSB for every map axis; expect a VCDS log of group 002 field
2 to read about 3.8 % low against it.**

Reproduce (the script is four lines; `Med9Handlers` is the same class §7.1
used):

```python
import sys; sys.path.insert(0, "logging")
from ecu_sim import Med9Handlers
h = Med9Handlers("data/passat_azx_ori.bin", seed=1, animate=False)
h.handle(b"\x10\x89")
for raw in (13, 67, 128, 133, 255):
    h.emu.write(0x7FEF74, bytes([raw]))
    body = h.handle(b"\x21\x02")[0][2:]
    print(raw, tuple(body[3:6]))        # group 002 field 2 = id 2
```

and, for the static half,
`./.venv/bin/python tools/sda_xref.py data/passat_azx_ori.bin --var 0x7FEF74`
plus `ghidra_scripts/decompile.py --asm 0x00419258 --count 14`.

---

## 8. The four flex-fuel ids and group 111 (D2, 2026-09-16, #39)

`patches/ff_fuel` publishes its state in a measuring block. The slots it takes
and the proof that nothing else reads them, all VERIFIED-STATIC:

```bash
python3 tools/measuring_vars.py data/passat_azx_ori.bin --free
```

### 8.1 The ids: 2196-2199, the last four entries of TKMWL

1,507 of the 2,200 ids both point at the "not available" stub 0x038EC4 *and*
are named by no group, so nothing in the image can reach them. The four taken
are the **last four**, 2196-2199, which makes the edit one contiguous 16-byte
range at **0x0A78A8-0x0A78B7** (the table ends at 0x0A78B7) whose stock content
is `00 03 8E C4` four times.

| id | handler | field | emits | reads |
|---|---|---|---|---|
| 2196 | `ff_diag_e_pct` | 1 | `(0x21, A=0x64, B = E %)` | `ff_state.diag_e_pct` |
| 2197 | `ff_diag_f_pct` | 2 | `(0x21, A=0x64, B = F %)` | `ff_state.diag_f_pct` |
| 2198 | `ff_diag_t_degc` | 3 | `(0x05, A=0x0A, B = T + 100)` | `ff_state.diag_t_degc` |
| 2199 | `ff_diag_mode` | 4 | `(0x36, A = persist_state, B = mode)` | `ff_state.mode` |

Nothing else reads those four words:

* `tools/find_abs_refs.py data/passat_azx_ori.bin --range 0xA78A8 0xA78B7`
  → **no site at all**; over the whole table
  (`--range 0xA5650 0xA78C0`) there is **exactly one**, `0x045780`, which is
  the dispatcher's own `lis`/`addi` (§3);
* `tools/find_branch_refs.py data/passat_azx_ori.bin 0xA78A8 0xA78AC 0xA78B0
  0xA78B4` → no branch and no stored pointer to any of them;
* the only consumer of a variable id is the group table, and the highest id it
  names anywhere is **1746**.

### 8.2 The group: 111 (0x6F)

`21 <group>` accepts 1..0x7F only and answers with the requested group **and**
group + 0x7F (kwp.md §12.3), so a free group must be empty *in both halves*.
Sixteen qualify: 17, 19, 25, 29, 40, 45, 48, 49, 58, 59, 65, 67, 69, 108, 109
and **111**. Group 111 is the highest, and its echo 238 is empty too, so the
whole 25-byte response belongs to the patch: four flex-fuel fields followed by
four `(0x25, 0, 0)` "not implemented" triples.

108 and 109 are left free on purpose, for the ignition and rail blends of
docs/05 §3.4 / §3.6. **108 is TAKEN as of 2026-09-17 — see §8.4.**

The four words, all currently `00 00`:

| field | CPU | file |
|---|---|---|
| 1 | 0x5C55F6 | 0x1C55F6 |
| 2 | 0x5C57F4 | 0x1C57F4 |
| 3 | 0x5C59F2 | 0x1C59F2 |
| 4 | 0x5C5BF0 | 0x1C5BF0 |

They are inside the stock calibration (file 0x1C0000-0x1DFFFF), so
`tools/patch_apply.py` needs `"calibration_edit": true` on the change; the
TKMWL edit at file 0x0A78A8 is plain code-block flash and needs no flag. Both
blocks are re-checksummed by `checksum.py fix`.

Nothing else reads the group-table words either. Only two instructions in the
whole image form the table's base address —

```
0x035760  3B A2 B5 28  addi r29,r2,-0x4AD8     ; measuring_group_read4
0x0357F8  39 82 B5 28  addi r12,r2,-0x4AD8     ; the single-field reader 0x357E0
```

— and `tools/sda_xref.py data/passat_azx_ori.bin --var 0x5C5518 0x5C5D15`
finds no D-form access into the table body at all (its three hits, 0x5C5D10,
0x5C5D12 and 0x5C5D14, are past the last field row, which ends at 0x5C5D0F).
`find_abs_refs.py --range 0x5C5518 0x5C5D0F` is empty.

### 8.3 Formula choices

Only formulas the logger can decode were used
(`logging/med9kwp/vag_formulas.py`):

* **0x21** `100 * B / A` — with A = 0x64 the display value *is* B in percent,
  0..100 for ethanol and 100..200 for the fuel factor F (F = 1.40 reads
  140 %). COMMUNITY, consistent with the id-2 anchor (A = 0x85 reads 100 % at
  B = 0x85).
* **0x05** `0.1 * A * (B - 100)` °C — CROSS-CHECKED against `tmot` in §7.1.
  With A = 0x0A the value is `B - 100` °C, so the patch emits
  `B = T + 100` from its stored `degC + 40` byte, clamped to 0..255
  (i.e. -100 °C .. +155 °C).
* **0x36** `(A << 8) | B` as a plain count — the mode enum 0..5 in B, with the
  persistence state 0..4 in A, so the field reads `256 * persist + mode`.

Every one of them is checked on the applied image in
`tests/test_ff_diag_patch.py`.

## 8.4 The ignition blend: ids 2192-2195 and group 108 (E1, 2026-09-17, #34)

Brief **E1** took the second of the three slots §8.2 reserved. Re-checked
before taking it, as the 2026-09-17 rules require:

```bash
./.venv/bin/python3 tools/measuring_vars.py data/passat_azx_ori.bin --free
#   spare variable ids (stub handler AND named by no group): 1507 of 2200
#   free groups ...: [17, 19, 25, 29, 40, 45, 48, 49, 58, 59, 65, 67, 69,
#                     108, 109, 111]
#   group 108 (0x6C) words: 0x5c55f0, 0x5c57ee, 0x5c59ec, 0x5c5bea
```

### The ids: 2192-2195, the four entries below D2's

Table words **0x0A7898-0x0A78A7**, stock content `00 03 8E C4` four times, so
the edit is again one contiguous 16-byte range and it sits immediately below
D2's. Taking them downwards from the end of the table keeps every future
brief's edit contiguous with the last one.

| id | handler | field | emits | reads |
|---|---|---|---|---|
| 2192 | `ff_diag_fzw_pct` | 1 | `(0x21, A=0x64, B = f_zw %)` | `ff_state.fzw_q8` |
| 2193 | `ff_diag_dzw` | 2 | `(0x22, A=0x4B, B = dzw_e + 0x80)` | `ff_state.dzw_e` |
| 2194 | `ff_diag_dwkrz` | 3 | `(0x22, A=0x4B, B = max + 0x80)` | **stock** 0x7FCE57-0x7FCE5C |
| 2195 | `ff_diag_zwlatch` | 4 | `(0x36, A=0, B = latch & 3)` | **stock** 0x7FD31B |

The three negative searches, all reproduced in
`tests/test_ff_ign_patch.py::TestStockFacts`:

* `find_abs_refs.resolve()` over the whole image finds **no** `lis` + D-form
  pair resolving into 0x0A7898-0x0A78A7;
* `find_branch_refs.scan()` finds no branch and no stored pointer to any of
  the four words, nor to any of the four group words;
* the group table names no id above 1746, so nothing can reach 2192-2195
  except through our own edit.

### The group: 108 (0x6C)

Its `+0x7F` echo, **235**, is empty too (all eight words `00 00`), so the whole
25-byte answer to `21 6C` belongs to the patch: four fields followed by four
`(0x25, 0, 0)` triples. Words, all inside the guarded stock calibration
(file 0x1C0000-0x1DFFFF), hence `"calibration_edit": true`:

| field | CPU | file |
|---|---|---|
| 1 | 0x5C55F0 | 0x1C55F0 |
| 2 | 0x5C57EE | 0x1C57EE |
| 3 | 0x5C59EC | 0x1C59EC |
| 4 | 0x5C5BEA | 0x1C5BEA |

**109 is still free, for brief E5's rail-pressure adder, and 69 for E2.**

### Formula 0x22 with A = 0x4B is copied, not chosen

§8.3 used only formulas `logging/med9kwp/vag_formulas.py` can decode. Fields 2
and 3 carry an ignition angle, and the honest encoding for that is not a
count: the **six stock per-cylinder knock-retard handlers** at
0x039CD0-0x039D48 already emit exactly

```
00039CD0  38 60 00 22  li    r3,0x22          ; the formula
00039CD4  88 AD CE 67  lbz   r5,-0x3199(r13)  ; dwkrz[0] = 0x7FCE57
00039CD8  38 80 00 4B  li    r4,0x4b          ; A = 75
00039CDC  38 A5 00 80  addi  r5,r5,0x80       ; B = byte + 128
```

for the very array field 3 reports, so `0.01 × 75 × (B − 128)` = **0.75 °CA
per count** — §6's fixed point, and the same scaling VCDS groups 020-024 show.
Brief E1's text offered formula 0x36 (a raw signed count) as a fallback if the
angle formula of measuring id 9 (0x1B, A = 0x4B) could not be decoded, which
it cannot; 0x22 was used instead because it *is* decodable, it is what the
stock handlers for this exact quantity use, and it saves the tester converting
by hand. **Caveat:** `vag_formulas.py`'s community table labels 0x22 "kW". The
arithmetic is right and the unit string is the published table's, not the
patch's; nothing in `logging/` was changed for this (brief E4 owns it).

Field 4 uses **0x36** with A = 0, so the reading is the latch bits 0..3 as a
plain count: 0 is the only acceptable value on E85.

Fields 1 and 2 check the state-block header and answer `(0x25, 0, 0)` when it
does not hold, as D2's do. Fields 3 and 4 **do not**: they report stock cells
that are valid whether or not our block is, and a tester chasing knock has to
be able to see them.

## 8.5 The start enrichment: ids 2188-2191 and group 69 (E2, 2026-09-17, #35)

Brief **E2** took the third of the three slots §8.2 reserved, and the group the
wave-E budget in `docs/agent_briefs/README.md` assigned it. Re-checked before
taking it, as the 2026-09-17 rules require:

```bash
./.venv/bin/python3 tools/measuring_vars.py data/passat_azx_ori.bin --free
#   spare variable ids (stub handler AND named by no group): 1507 of 2200
#   free groups ...: [17, 19, 25, 29, 40, 45, 48, 49, 58, 59, 65, 67, 69,
#                     108, 109, 111]
#   group  69 (0x45) words: 0x5c55a2, 0x5c57a0, 0x5c599e, 0x5c5b9c
```

### The ids: 2188-2191, the four entries below E1's

Table words **0x0A7888-0x0A7897**, stock content `00 03 8E C4` four times, so
the edit is one contiguous 16-byte range immediately below E1's — the third
step of the downward walk from the end of the 2200-entry table (D2 took
2196-2199, E1 2192-2195). E5 continues with 2184-2187.

| id | handler | field | emits | reads |
|---|---|---|---|---|
| 2188 | `ff_diag_fst_pct` | 1 | `(0x21, A=0x64, B = f_st %)` | `ff_state.fst_q10` |
| 2189 | `ff_diag_zwst` | 2 | `(0x22, A=0x4B, B = zwst_add + 0x80)` | `ff_state.zwst_add` |
| 2190 | `ff_diag_tmst` | 3 | `(0x05, A=0x0A, B = °C + 100)` | **stock** 0x8021F6 |
| 2191 | `ff_diag_ksta` | 4 | `(0x36, A = v >> 8, B = v & 0xFF)` | **stock** 0x80302C |

The three negative searches, all reproduced in
`tests/test_ff_start_patch.py::TestStockFacts`:

* `find_abs_refs.resolve()` over the whole image finds **no** `lis` + D-form
  pair resolving into 0x0A7888-0x0A7897;
* `find_branch_refs.scan()` finds no branch and no stored pointer to any of
  the four words, nor to any of the four group words;
* `free_slots()` still lists all four ids as spare and group 69 as free.

### The group: 69 (0x45)

Words **0x5C55A2 / 0x5C57A0 / 0x5C599E / 0x5C5B9C**, i.e.
`0x5C5518 + field * 0x1FE + 69 * 2`, all four `00 00` in the stock image. Its
`+0x7F` echo, group **196**, is empty too, so the whole 25-byte answer to
`21 45` belongs to the patch. All four words are inside the guarded stock
calibration 0x1C0000-0x1DFFFF and carry `"calibration_edit": true`.

### Formula choices, and the one that is a fallback

Field 1 is **0x21 with A = 100**, so `B` is the percent directly — the same
shape as D2's fields 1 and 2 and E1's field 1. Field 2 is **0x22 with
A = 0x4B**, the angle formula §8.4 established: `0.01 × 75 × (B − 128)` =
0.75 °CA per count. Field 3 is **0x05 with A = 10**, §7.1's cross-checked
temperature formula, so the reading is `B − 100` whole degrees; the handler
does the count → °C conversion itself (`(count × 3 + 2) / 4 − 48`, rounded to
nearest, half **up**), because `tmst`'s 0.75 °C/LSB with a −48 °C offset has no
representation in the VAG formula table.

**Field 4 is the fallback the brief allowed, and it was needed.** The brief
asked for `ksta_adapted` as a percent, `(v × 100) >> 10`, and said to use a
count formula if the percent does not fit. It does not: the *stock* cranking
factor reaches 22.8× = 2280 % at −30 °C (`start.md` §6) and formula 0x21's `B`
is one byte. Field 4 therefore uses **0x36**, `(A << 8) | B`, and reports the
whole 16-bit cell exactly as the ECU holds it — 1024 = 1.00×, no saturation
anywhere in the range, and the same fixed point `emu/start_model.py` and the
tick-by-tick comparison work in.

**Field 1 cannot saturate either**, and that is what fixes the patch's code
ceiling `FF_FST_HARD_MAX` at 2560: `(2560 × 100) >> 10 = 250`, the largest
value formula 0x21's `B` can carry with A = 100. The clamp in the code, the
range the calibration may ask for and the range the measuring block can show
are deliberately the same number.

Fields 1 and 2 check the state-block header and answer `(0x25, 0, 0)` when it
does not hold, as D2's and E1's do. Fields 3 and 4 **do not**: they report
stock cells that are valid whether or not our block is, and somebody watching
a cold start has to see `tmst` and the cranking factor either way.

### The budget after E2

| Brief | ids | group | taken |
|---|---|---|---|
| D2 (#39) | 2196-2199 | 111 | yes |
| E1 (#34) | 2192-2195 | 108 | yes |
| **E2 (#35)** | **2188-2191** | **69** | **yes** |
| E5 (#36) | 2184-2187 | 109 | reserved |

Twelve of the 1507 spare ids and three of the sixteen free groups are now
spent. The remaining free groups are 17, 19, 25, 29, 40, 45, 48, 49, 58, 59,
65, 67 and 109.
