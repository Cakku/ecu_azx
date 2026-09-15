# MPC561/MPC563 register facts for the MED9.1.1 (03H906032 / 1037382557)

Agent A2, brief `docs/agent_briefs/A2_reference_documents.md`, issue #6.
Date: 2026-09-15.

Source for every register fact: **NXP/Freescale *MPC561/MPC563 Reference
Manual*, Rev. 1.2** (`documents/MPC561RM.pdf`, SHA-256 in
`documents/SOURCES.md`). Citations are `§section, p. <printed page>` with the
absolute PDF page in brackets, because the printed numbers restart per chapter.

Values quoted from our dump are the ones listed in the brief; the disassembly
that produced them is quoted inline so each can be re-checked with
`xxd -s <file offset> -l 16 data/passat_azx_ori.bin`.

Tags: **VERIFIED-STATIC** = derived from the dump plus the manual;
**MANUAL** = a property of the silicon, from the reference manual only;
**HYPOTHESIS** = our inference, not yet proven.

---

## 1. IMMR (Internal Memory Map Register), SPR 638

§6.2.2.1.2, Fig. 6-13 / Table 6-12, p. 6-28…6-29 (PDF 268–269).
Also listed as SPR 638 in the SPR map, p. 5-8 (PDF 239).

| Bits | Field | Meaning (MANUAL) |
|---|---|---|
| 0:7 | PARTNUM | Mask-programmed part id. **MPC561 = 0x35, MPC563 = 0x36.** Read-only. |
| 8:15 | MASKNUM | Mask revision. Read-only. |
| 16:19 | — | reserved |
| 20 | FLEN | Flash enable. 0 = on-chip flash disabled, its address space maps to external memory; 1 = on-chip flash enabled. Read/write; reset value comes from the reset configuration word. |
| 21:27 | — | reserved (bit 23 "programme to 0 at all times") |
| 28:30 | ISB | Internal space base. `000`→0x000000, **`001`→0x0040 0000**, `010`→0x0080 0000, `011`→0x00C0 0000, `100`→0x0100 0000, `101`→0x0140 0000, `110`→0x0180 0000, `111`→0x01C0 0000. Read/write. |
| 31 | — | reserved |

### 1.1 What `andi. 0xFFF1 ; ori 0x2` at file 0x1008-0x100C does — CONFIRMED

Bytes at file 0x1004 (`xxd -s 0x1004 -l 16`):

```
00001004: 7d5e 9aa6     mfspr r10, 638        # IMMR
00001008: 714a fff1     andi. r10, r10, 0xFFF1
0000100c: 614a 0002     ori   r10, r10, 0x2
00001010: 7d5e 9ba6     mtspr 638, r10
```

In a 32-bit big-endian-numbered word the ISB field (bits 28:30) has the mask
`0x0000000E` and bit 30 alone is `0x00000002`. `0xFFF1` = `~0x000E` in the low
half-word, so `andi.` clears **exactly** the ISB field (and, as PowerPC `andi.`
always does, the upper half-word, which holds only the read-only PARTNUM /
MASKNUM). `ori 0x2` then sets bit 30.

⇒ **ISB = 0b001 → the internal 4 MB block is based at 0x0040 0000.**
FLEN (bit 20) is **preserved**, i.e. on-chip flash enable is taken from the
reset configuration word and never changed by software. **VERIFIED-STATIC.**

`docs/02_memory_map.md` §3 is therefore correct, and the address `0x400000 +
<datasheet offset>` convention holds for every peripheral.

### 1.2 The PARTNUM comparison at file 0x1236C — CONFIRMED

```
00012364: 7c7e 9aa6     mfspr r3, 638          # IMMR
00012368: 5463 463e     rlwinm r3, r3, 8, 24, 31   # r3 = IMMR[0:7] = PARTNUM
0001236c: 2c03 0035     cmpwi  r3, 0x35        # MPC561 ?
00012370: 3fa0 0080
00012374: 3bbd 8014     # r29 = 0x7F8014 (CALRAM)
00012378: 4082 0074     bne +0x74              # != 0x35 -> different path
```

0x35 is the MPC561 id (Table 6-12). Which parts have on-chip flash
(§1.3.1.6, p. 1-5 (PDF 89); Fig. 1-4 note, p. 1-13 (PDF 97); part table p. 1-1
(PDF 85)):

| Part | On-chip UC3F flash | Code compression |
|---|---|---|
| MPC561 | **none** | no |
| MPC562 | **none** | yes |
| MPC563 | 512 KB | no |
| MPC564 | 512 KB | yes |

The manual gives ids only for MPC561 (0x35) and MPC563 (0x36); it does not
state the MPC562/MPC564 ids. Since our dump contains 496 KB of *on-chip* flash
content (`docs/02` §2), the ECU cannot be an MPC561/MPC562, so the branch taken
at 0x12378 is the "not 0x35" path and the part is **MPC563 (PARTNUM 0x36) or
MPC564**. The firmware keeps the 0x35 path so the same image also runs on the
flashless 2 MB MED9.1 hardware. **VERIFIED-STATIC (that the compare is against
the MPC561 id) + HYPOTHESIS (that our silicon is specifically the MPC563).**

---

## 2. Internal memory map with ISB = 1

Fig. 1-4 "MPC561/MPC563 Internal Memory Map", p. 1-13 (PDF 97). The internal
block is 4 MB; every offset below is `+ ISB*0x400000 = + 0x400000` for us.

| Offset (manual) | CPU address (ours) | Content | `docs/02` §3 |
|---|---|---|---|
| 0x00 0000–0x07 FFFF | **0x400000–0x47FFFF** | UC3F flash, 512 KB (MPC563/564 only) | ✔ matches |
| 0x08 0000–0x2F 7FFF | 0x480000–0x6F7FFF | "Reserved for Flash", 2,605 KB — **nothing responds** | ✘ see §4 |
| 0x2F 8000–0x2F 87FF | 0x6F8000–0x6F87FF | BBC DECRAM, 2 KB | ✔ |
| 0x2F A000–0x2F A03F | 0x6FA000–0x6FA03F | BBC DCCR (decompressor class config) | new |
| 0x2F C000–0x2F FFFF | 0x6FC000–0x6FFFFF | USIU & flash control, 16 KB | ✔ |
| 0x2F C800–0x2F C80B | 0x6FC800–0x6FC80B | UC3F control registers | ✔ |
| 0x30 0000–0x30 7FFF | 0x700000–0x707FFF | UIMB interface + IMB3 modules, 32 KB | ✔ (doc says 0x70FFFF; the manual ends the block at 0x707FFF and calls 0x308000–0x37FFFF "Reserved for IMB") |
| 0x30 0000 / 0x30 2000 | 0x700000 / 0x702000 | DPTRAM control (32 B) / DPTRAM 8 KB | new |
| 0x30 4000 / 0x30 4400 | 0x704000 / 0x704400 | TPU3_A / TPU3_B | ✔ |
| 0x30 4800 / 0x30 4C00 | 0x704800 / 0x704C00 | QADC64E_A / QADC64E_B | ✔ |
| 0x30 5000 | 0x705000 | QSMCM (1 KB) | ✔ |
| 0x30 5C00 | 0x705C00 | PPM (64 B) | new |
| 0x30 6000 | 0x706000 | MIOS14 (4 KB) | ✔ |
| 0x30 7000 / 0x30 7400 / 0x30 7800 | **0x707000 / 0x707400 / 0x707800** | TouCAN_A / _B / _C module base (1 KB each) | see §6 |
| 0x30 7F80 | 0x707F80 | UIMB registers (128 B) | ✔ |
| 0x38 0000–0x38 00FF | 0x780000–0x7800FF | CALRAM / READI control, 256 B | ✔ |
| 0x3F 8000–0x3F FFFF | 0x7F8000–0x7FFFFF | **CALRAM, 32 KB** (the "on-chip SRAM"), with 0x3F F000–0x3F FFFF a 4 KB overlay section | ✔ (the manual's name for it is CALRAM, §22, p. 22-1 (PDF 898)) |

All the internal offsets used in `docs/02` §3 are confirmed. **MANUAL.**
The 32 KB CALRAM, the 8 KB DPTRAM and the 2 KB DECRAM are the three arrays kept
alive by the `IRAMSTBY` pin (§1.7, p. 1-11, PDF 95) — relevant when we later
look for "keep-alive" adaptation storage.

---

## 3. USIU memory controller: BR0–BR3 / OR0–OR3

Register addresses, Table 10-6, p. 10-31 (PDF 429) — offsets from the ISB base,
so with ISB = 1:

| Register | Manual address | Our CPU address |
|---|---|---|
| BR0 / OR0 | 0x2F C100 / 0x2F C104 | 0x6FC100 / 0x6FC104 |
| BR1 / OR1 | 0x2F C108 / 0x2F C10C | 0x6FC108 / 0x6FC10C |
| BR2 / OR2 | 0x2F C110 / 0x2F C114 | 0x6FC110 / 0x6FC114 |
| BR3 / OR3 | 0x2F C118 / 0x2F C11C | 0x6FC118 / 0x6FC11C |
| DMBR / DMOR | 0x2F C140 / 0x2F C144 | 0x6FC140 / 0x6FC144 |
| MSTAT | 0x2F C178 | 0x6FC178 |

### 3.1 Field layout

**BR** — Fig. 10-23 / Table 10-8, p. 10-32…10-34 (PDF 430–432):

| Bits | Word mask | Field | Notes |
|---|---|---|---|
| 0:16 | 0xFFFF8000 | BA | base address, compared against ADDR[0:16] under OR[AM] |
| 17:19 | 0x00007000 | AT | address type |
| 20:21 | 0x00000C00 | PS | **00 = 32-bit, 01 = 8-bit, 10 = 16-bit port** |
| 22 | 0x00000200 | SST | short setup time |
| 23 | 0x00000100 | WP | write protect (writes give no CS/TA, set MSTAT[WPERn]) |
| 24 | 0x00000080 | — | reserved |
| 25 | 0x00000040 | BL | burst length 4/8 words |
| 26 | 0x00000020 | WEBS | 0 = WE pins, 1 = BE (byte-enable) pins |
| 27 | 0x00000010 | TBDIP | toggle burst-data-in-progress |
| 28 | 0x00000008 | LBDIP | late BDIP |
| 29 | 0x00000004 | SETA | external transfer acknowledge |
| 30 | 0x00000002 | BI | burst inhibit |
| 31 | 0x00000001 | V | valid |

**OR** — Fig. 10-24 / Table 10-10, p. 10-34…10-36 (PDF 432–434):

| Bits | Word mask | Field | Notes |
|---|---|---|---|
| 0:16 | 0xFFFF8000 | AM | address mask; a **clear** bit makes the address bit a don't-care |
| 17:19 | 0x00007000 | ATM | address-type mask |
| 20 | 0x00000800 | CSNT | CS/WE negated a quarter clock early |
| 21:22 | 0x00000600 | ACS | 00 = CS with address, 10 = ¼ clock later, 11 = ½ clock later |
| 23 | 0x00000100 | EHTR | extended hold time on reads |
| 24:27 | 0x000000F0 | SCY | wait states; total access = **(2 + SCY) clocks** |
| 28:30 | 0x0000000E | BSCY | burst-beat wait states (1xx reserved) |
| 31 | 0x00000001 | TRLX | relaxed timing (doubles SCY/BSCY) |

Region size = `2 ** (32 - popcount(AM[0:16]))`.

### 3.2 Decode of the values in our dump

| Register | Value | Region | Port | Wait states | Other |
|---|---|---|---|---|---|
| **BR0** | `0x00000103` | base **0x000000**, valid | **32-bit** (PS=00) | — | **WP=1** (read-only), BI=1 |
| **BR0** | `0x0000090B` | base **0x000000**, valid | **16-bit** (PS=10) | — | **WP=1**, LBDIP=1, BI=1 |
| OR0 | `0xFF800650` | AM=0xFF800000 → **8 MB**, 0x000000–0x7FFFFF | | SCY=5 → 7 clocks | ACS=11, EHTR=0, BSCY=0, TRLX=0 |
| OR0 | `0xFF8006FF` | **8 MB** | | SCY=15, BSCY=7 (reserved), TRLX=1 → slowest possible | ACS=11, EHTR=1 |
| OR0 | `0xFF800150` | **8 MB** | | SCY=5 → 7 clocks | ACS=00, EHTR=1 |
| OR0 | `0xFF800140` | **8 MB** | | SCY=4 → 6 clocks | ACS=00, EHTR=1 |
| OR0 | `0xFF800130` | **8 MB** | | SCY=3 → 5 clocks | ACS=00, EHTR=1 |
| OR0 | `0xFF800120` | **8 MB** | | SCY=2 → 4 clocks | ACS=00, EHTR=1 |
| **BR1** | `0x00800403` | base **0x800000**, valid | **8-bit** (PS=01) | — | WP=0, BI=1 |
| OR1 | `0xFFFC0140…0x180` | AM=0xFFFC0000 → **256 KB**, 0x800000–0x83FFFF | | SCY=4…8 | ACS=00, EHTR=1 |
| OR1 | `0xFFFC0110` / `0xFFFC0100` | **256 KB** | | SCY=1 / SCY=0 | ACS=00, EHTR=1 |
| **BR2** | `0x00900823` | base **0x900000**, valid | **16-bit** (PS=10) | — | **WEBS=1** (byte enables), BI=1 |
| OR2 | `0xFFFC0110` | **256 KB**, 0x900000–0x93FFFF | | SCY=1 → 3 clocks | ACS=00, EHTR=1 |
| OR2 | `0x0FFF1F00` | **not a plausible OR value — refuted, see below** | | | |
| **BR3** | `0x00A00003` | base **0xA00000**, valid | **32-bit** (PS=00) | — | WP=0, BI=1 |
| OR3 | `0xFFFF8C20` | AM=0xFFFF8000 → **32 KB**, 0xA00000–0xA07FFF | | SCY=2 → 4 clocks | CSNT=1, ACS=10 |
| OR3 | `0xFFFF8C30` | **32 KB** | | SCY=3 → 5 clocks | CSNT=1, ACS=10 |

**VERIFIED-STATIC** (values are in the dump; the decode is the manual's).

`0x0FFF1F00` decodes to AM = 0x0FFF0000, i.e. address bits 0–3 masked *out* and
4–15 compared, which would make the region repeat every 256 MB across the whole
address space — no memory controller is ever programmed this way, and it does
not match the 20 references into 0x900000–0x93FFFF. **We refute it as an OR2
value**; it is most likely a data constant that was mis-attributed. The OR2
actually paired with `BR2 = 0x900823` is `0xFFFC0110` (256 KB), which agrees
exactly with the 0x900000–0x93FFFF extent recorded in `docs/02` §3.

### 3.3 Clock-mode tables at file 0x10018-0x10097

The OR values are selected from six-entry tables indexed by a clock mode. The
table block starts with six selector bytes `24 28 30 4C 54 64` followed by the
OR tables (each entry read with `lwzx` in the code at file 0x12004-0x121D8):

```
010018: 2428304C 54640000 | FF800650 FF8006FF FF8006FF FF800140 FF800140 FF800150   <- OR0 set A
010038: FF800130 FF800130   FF800130 FF800120 FF800120 FF800120                     <- OR0 set B
010050: FFFC0140 FFFC0150   FFFC0160 FFFC0160 FFFC0170 FFFC0180                     <- OR1 set A
010068: FFFC0110 FFFC0120   FFFC0120 FFFC0100 FFFC0100 FFFC0100                     <- OR1/OR2 set B
010080: FFFF8C20 FFFF8C30   FFFF8C30 | FF800130 FF800130 FF800140                   <- OR3 set, then more OR0
```

Every OR0 entry has `AM = 0xFF800000`, i.e. **CS0 is an 8 MB region in every
clock mode** — this is the part of the `docs/02` §3 model that is confirmed.
The grouping above is **HYPOTHESIS** (the exact BR↔OR pairing per mode was not
traced; that is brief B1 territory).

### 3.4 "8 MB CS0 region, flash aliased at 0x400000-0x5FFFFF" — partly REFUTED

* **Confirmed**: CS0's programmed region is 8 MB, 0x000000–0x7FFFFF (OR0[AM]).
* **Refuted**: the memory controller does *not* see addresses that fall inside
  the internal 4 MB block. §10.8, p. 10-28 (PDF 426): *"If the address of any
  master is mapped within the internal MPC561/MPC563 address space, the access
  will be directed to the internal device, and will be ignored by the memory
  controller."* With ISB = 1 the whole of 0x400000–0x7FFFFF is internal space,
  so CS0 cannot "shine through" wherever no module answers — an unclaimed
  internal address simply gets no acknowledge (bus-monitor time-out).
* The one exception is the dual-mapping window, §4 below, which is exactly the
  0x5C0000–0x5FFFFF calibration range the firmware really uses.

Resulting external-flash visibility:

| CPU range | Backed by | Why |
|---|---|---|
| 0x000000–0x1FFFFF | flash 0x000000–0x1FFFFF | CS0, real |
| 0x200000–0x3FFFFF | flash 0x000000–0x1FFFFF | CS0 8 MB region, 2 MB device, upper address lines not decoded by the device (**HYPOTHESIS** — board wiring not inspected) |
| 0x400000–0x5BFFFF | *nothing* | internal space: UC3F 0x400000–0x47FFFF, then "Reserved for Flash" |
| **0x5C0000–0x5FFFFF** | **flash 0x1C0000–0x1FFFFF** | **DMBR/DMOR dual mapping, §4** |
| 0x600000–0x7FFFFF | internal modules only | DECRAM, USIU, IMB, CALRAM |

---

## 4. Dual mapping: DMBR = 0x70000001, DMOR = 0x70000000 — this is the calibration window

### 4.1 Field layout

**DMBR** — Fig. 10-25 / Table 10-11, p. 10-36…10-37 (PDF 434–435):

| Bits | Field | Notes |
|---|---|---|
| 0 | — | reserved |
| 1:6 | BA | **corresponds to address bits [11:16]** |
| 7:9 | — | reserved |
| 10:12 | AT | address type (reset 001 = data only) |
| 13:27 | — | reserved |
| 28:30 | DMCS | 000 = CS0, 001 = CS1, 010 = CS2, 011 = CS3 |
| 31 | DME | dual mapping enable |

**DMOR** — Fig. 10-26 / Table 10-12, p. 10-37…10-38 (PDF 435–436): bits 1:6 =
AM (mask for DMBR[BA]), bits 10:12 = ATM, everything else reserved. Clearing
ATM makes the comparison ignore the address type, i.e. **both instruction
fetches and data accesses are dual-mapped** (the reset default, ATM = 001, maps
data only).

The match condition is given explicitly, Eqn. 10-1, p. 10-25 (PDF 423):

```
bus_address[0:16] == { 0000000, ISB[0:2], 0, BA[1:6] }
```

### 4.2 The writes in our dump

```
00012508: 3d80 7000     lis  r12, 0x7000          # r12 = 0x70000000
0001250c: 919f 0144     stw  r12, 0x144(r31)      # DMOR <- 0x70000000     (r31 = 0x6FC000)
00012510: 618b 0001     ori  r11, r12, 1          # r11 = 0x70000001
00012514: 917f 0140     stw  r11, 0x140(r31)      # DMBR <- 0x70000001
```

DMOR is written before DMBR, so the mask is valid before DME is set — correct
ordering.

### 4.3 Decode

* DMBR: BA = `0b111000`, AT = `000`, DMCS = `000` (**CS0**), **DME = 1**.
* DMOR: AM = `0b111000` (bits 11,12,13 compared, 14,15,16 masked),
  ATM = `000` (all address types).

Substituting BA and ISB = `001` into Eqn. 10-1:

| address bit | 7 | 8 | 9 | 10 | 11 | 12 | 13 | weight |
|---|---|---|---|---|---|---|---|---|
| value | 0 | 0 | 1 | 0 | 1 | 1 | 1 | 0x400000 + 0x100000 + 0x80000 + 0x40000 |

⇒ base = **0x5C0000**, and with bits 14:16 masked the window is
**0x5C0000–0x5FFFFF (256 KB), redirected to chip select 0, for data *and*
instruction accesses.** **VERIFIED-STATIC.**

### 4.4 Why this matters

This single register pair explains the whole calibration addressing of the ECU:

* The firmware makes 7,055 absolute references into 0x5C0000–0x5E2FFF
  (`docs/02` §3) and sets `r2 = 0x5C9FF0` in the application (`docs/02` §4).
  All of these land inside the dual-mapped window.
* The checksum table at file 0x1C3300 covers 0x5C2000–0x5FFFFF
  (`docs/02` §6) and all six descriptors verify against file 0x1C2000–0x1FFFFF,
  i.e. `cpu - 0x400000 = file`. That is independent confirmation of the
  0x5C0000 ↔ file 0x1C0000 mapping that Eqn. 10-1 predicts.
* There is **no** general "high alias" at 0x480000–0x5BFFFF or 0x600000–
  0x6F7FFF. `docs/02` §3 and `tools/med9lib.py` are corrected accordingly.

Two further manual notes worth keeping:

* §10.6, p. 10-27 (PDF 425) says dual mapping "can only be enabled over memory
  addresses in the range 0x0000 0000 through 0x000F FFFF". That is inconsistent
  with Table 10-11 (BA = ADDR[11:16]) and with Eqn. 10-1, which allow offsets up
  to 0x1F8000 in 32 KB steps — and our ECU demonstrably uses offset 0x1C0000.
  **Trust Eqn. 10-1.**
* §10.5 note, p. 10-26 (PDF 424): with dual mapping active the addressed region
  is taken away from the internal flash. Here the window sits in the "Reserved
  for Flash" area above the 512 KB UC3F array, so nothing is lost.

---

## 5. Exception vectors with MSR[IP] = 1 — SOLVED

MSR bit 25 is IP, §3.11.5 / Table 3-11, p. 3-10 and 3-25 (PDF 165, 180):
IP = 0 → vector table at physical 0x0000 0000; IP = 1 → **0xFFF0 0000**.
Our MSR values decode as:

| MSR | FP(18) | ME(19) | FE0(20) | FE1(23) | **IP(25)** | RI(30) | EE(16) |
|---|---|---|---|---|---|---|---|
| `0x3942` | 1 | 1 | 1 | 1 | **1** | 1 | 0 |
| `0x2942` | 1 | 0 | 1 | 1 | **1** | 1 | 0 |

The external address bus is only **ADDR[8:31] — 24 pins** (§9.1, p. 9-1
(PDF 341): *"32-bit address bus with transfer size indication (only 24
available on pins)"*), and BR0[BA] = 0 with OR0[AM] = 0xFF800000 means an
address of 0xFFF0 0100 matches **no** chip select. So if the vectors really came
from 0xFFF0 0000 the ECU would take a bus error on its first interrupt.

They do not. The MPC561/MPC563 BBC has **Exception Table Relocation (ETR)**,
§4.3.1, p. 4-8…4-10 (PDF 214–216):

* enabled by `BBCMCR[ETRE]` (bit 19, SPR 560), whose reset value comes from
  **reset-configuration-word bit 19**; it only has an effect when MSR[IP] = 1;
* the BBC then rewrites each `0xFFF0 0x00` vector fetch to
  `Page_Offset + <small offset>`, two words (8 bytes) per entry
  (Table 4-1, p. 4-9);
* `Page_Offset` is selected by `BBCMCR[OERC]` (bits 24:25), Table 4-2,
  p. 4-10: `00` → **0x0 + ISB×0x400000**, `01` → 0x10000 + …, `10` → 0x80000 +
  …, `11` → 0x3FE000 + … ; "ISB offset is equal 4M × ISB".

**The first 256 bytes of our dump are exactly such a table.** Every offset that
Table 4-1 defines holds an absolute branch, the 8-byte pitch matches, and the
sparse offsets (0x98, 0xA0, 0xE0, 0xE8, 0xF0, 0xF8) are populated while the
gaps Table 4-1 leaves undefined are filled with a common "ignore" stub
(`ba 0x144`):

| Manual offset | Exception (Table 4-1) | file 0x0000xx | Target |
|---|---|---|---|
| +0x000 | Reserved | `480110F2` | `ba 0x110F0` (fatal spin) |
| +0x008 | **System Reset** | `480003C6` | `ba 0x3C4` |
| +0x010 | Machine Check | `48000166` | `ba 0x164` |
| +0x018 / +0x020 | Reserved | `48000186` / `480001A6` | `ba 0x184` / `ba 0x1A4` |
| +0x028 | **External Interrupt** | `4808002A` | **`ba 0x80028`** |
| +0x030 | Alignment | `480001C6` | `ba 0x1C4` |
| +0x038 | Program | `480001E6` | `ba 0x1E4` |
| +0x040 | FP unavailable | `48000206` | `ba 0x204` |
| +0x048 | Decrementer | `48000226` | `ba 0x224` |
| +0x060 | System Call | `48000246` | `ba 0x244` |
| +0x068 | Trace | `48000266` | `ba 0x264` |
| +0x070 | FP Assist | `48000286` | `ba 0x284` |
| +0x080 | SW Emulation | `480002C6` | `ba 0x2C4` |
| +0x098 | Instruction Storage Protection | `480002E6` | `ba 0x2E4` |
| +0x0A0 | Data Storage Protection | `48000326` | `ba 0x324` |
| +0x0E0 / +0x0E8 | Data / Instruction breakpoint | `48000346` / `48000366` | `ba 0x344` / `ba 0x364` |
| +0x0F0 / +0x0F8 | Maskable / non-maskable ext. breakpoint | `48000386` / `480003A6` | `ba 0x384` / `ba 0x3A4` |

Reproduce with `python3 tools/find_abs_refs.py` is not needed; the words are
`xxd -s 0 -l 0x100 data/passat_azx_ori.bin`.

Conclusions:

1. **The table at file 0x000000-0x0000FF is an ETR branch table, not a classic
   0x100-spaced PowerPC vector table.** `re/symbols.csv` and `docs/02` §3 are
   corrected. **VERIFIED-STATIC.**
2. ETR is active and `BBCMCR[OERC] = 00`, because `Page_Offset = 0x0 + ISB ×
   0x400000` is the only setting that puts the table at physical 0x0 (which is
   where it is, and where it is reachable while ISB is still 0 right after
   reset). **VERIFIED-STATIC for the table; HYPOTHESIS that OERC = 00 rather
   than the table being vestigial.**
3. The boot code reads-modifies-writes BBCMCR but only sets bit 30 (DCAE,
   "decompressor configuration access enable", Table 4-4, p. 4-21, PDF 227):

   ```
   00001118: 7d30 8aa6     mfspr r9, 560        # BBCMCR
   0000111c: 6129 0002     ori   r9, r9, 2      # set DCAE
   00001134: 7d30 8ba6     mtspr 560, r9
   00001138: 4c00 012c     isync                # required, note on p. 4-21
   ```

   ETRE and OERC are therefore **left at their reset-configuration-word
   values** and never touched by software. **VERIFIED-STATIC.**
4. **Once the boot code sets ISB = 1 (file 0x1008), `Page_Offset` becomes
   0x400000** — the first page of the on-chip flash, i.e. inside the
   **0x400000-0x403FFF that KESSv2 did not read**. So the vector table the ECU
   actually runs on is in the missing 16 KB, and we cannot see it. The
   handlers it branches to (0x110F0, 0x80028, 0x164…0x3C4) *are* in the dump.
   **HYPOTHESIS, but strongly supported**: it is the only OERC setting
   consistent with the table at 0x0, the alternatives resolve to 0x410000
   (file 0x20C000 — that is ordinary code, checked), 0x480000 (outside the
   512 KB array, nothing responds) and 0x7FE000 (CALRAM, would have to be
   written at run time).
   ⇒ **Another reason the K-TAG/BDM read of 0x400000-0x403FFF must happen
   before any write.**
5. The dump also contains a classic-layout entry at 0x100 (`ba 0x49C` →
   `b 0x1004`, the boot entry) so the image still boots when the reset
   configuration word has IP = 0. The classic table is *incomplete*, though —
   file 0x500 (external interrupt) is zero — which is further proof that the
   ETR table is the one in use.

---

## 6. TouCAN (CAN 2.0B controller), needed by brief B2

Three modules, register map Table 16-10, p. 16-17…16-20 (PDF 715–718), full
map also in Appendix B, p. B-11 (PDF 1136–1137). Module bases with ISB = 1:
**TouCAN_A 0x707000, TouCAN_B 0x707400, TouCAN_C 0x707800** (Fig. 1-4).

> Note for `docs/02` §7: the table at file 0x2BC38 holds `0x707080, 0x707480,
> 0x707880`. Those are the **CANMCR** addresses, i.e. module base **+ 0x80**,
> not the module bases. Registers live at +0x80…+0xA7, the 16 message buffers
> at +0x100…+0x1FF.

| Offset from module base | Register | Width |
|---|---|---|
| +0x80 | CANMCR — module configuration | 16 |
| +0x82 | CANTCR — test (factory only) | 16 |
| +0x84 | CANICR — interrupt configuration | 16 |
| +0x86 | CANCTRL0 / CANCTRL1 | 8 + 8 |
| +0x88 | PRESDIV / CANCTRL2 | 8 + 8 |
| +0x8A | TIMER — free-running timer | 16 |
| +0x90 / +0x92 | RXGMSKHI / RXGMSKLO — receive global mask | 16 + 16 |
| +0x94 / +0x96 | RX14MSKHI / RX14MSKLO — buffer 14 mask | 16 + 16 |
| +0x98 / +0x9A | RX15MSKHI / RX15MSKLO — buffer 15 mask | 16 + 16 |
| +0xA0 | ESTAT — error and status | 16 |
| +0xA2 | IMASK — interrupt masks (one bit per buffer) | 16 |
| +0xA4 | IFLAG — interrupt flags (one bit per buffer) | 16 |
| +0xA6 | RXECTR / TXECTR — error counters | 8 + 8 |
| +0x100 + 0x10·n | MBUFF*n*, n = 0…15 | 16 B each |

So for TouCAN_A: CANMCR 0x707080, IMASK 0x7070A2, IFLAG 0x7070A4,
message buffer *n* at `0x707100 + 0x10*n`.

**Message buffer layout** (Fig. 16-3 extended / Fig. 16-4 standard ID,
Table 16-1, p. 16-4, PDF 698) — 16 bytes, big-endian half-words:

| Offset | Standard ID (11 bit) | Extended ID (29 bit) |
|---|---|---|
| +0x0 | `TIMESTAMP[0:7] | CODE[8:11] | LENGTH[12:15]` (control/status) | same |
| +0x2 | `ID[28:18] | RTR | 0 0 0 0` | `ID[28:18] | SRR | IDE | ID[17:15]` |
| +0x4 | 16-bit time stamp | `ID[14:0] | RTR` |
| +0x6…+0xD | data bytes 0…7 | data bytes 0…7 |
| +0xE | reserved | reserved |

`CODE` is the buffer's state (Table 16-2 receive / Table 16-3 transmit,
p. 16-5); `LENGTH` is the DLC. Note that our receive table at file 0x2BC90
stores `0x01mmnn08` per entry with an explicit DLC — the `08`/`04` there is the
DLC that ends up in this LENGTH field.

---

## 7. QSMCM / QSPI (EEPROM driver, brief B4)

Module base 0x705000 (Fig. 1-4). Register map Table 15-2 / Appendix B,
p. 15-3 and B-1 (PDF 622–623, 1126–1127):

| CPU address | Register |
|---|---|
| 0x705000 | QSMCMMCR — module configuration |
| 0x705002 | QTEST |
| 0x705004 | QDSCI_IL — dual SCI interrupt level |
| 0x705006 | QSPI_IL — QSPI interrupt level |
| 0x705008 / 0x70500A | SCC1R0 / SCC1R1 — SCI1 control |
| 0x705014 | PORTQS — port QS data |
| 0x705016 / 0x705017 | PQSPAR / DDRQS — pin assignment / data direction |
| 0x705018 / 0x70501A / 0x70501C | SPCR0 / SPCR1 / SPCR2 — QSPI control |
| 0x70501E / 0x70501F | SPCR3 / SPSR — QSPI control 3 / status |
| 0x705020 / 0x705022 | SCC2R0 / SCC2R1 — SCI2 control |
| 0x705024 / 0x705026 | SC2SR / SC2DR — SCI2 status / data |
| 0x705028 / 0x70502A | QSCI1CR / QSCI1SR |
| 0x70502C–0x70504A | SCI transmit queue (SCTQ) |
| 0x70504C–0x70506A | SCI receive queue (SCRQ) |
| 0x705140–0x70517F | QSPI **receive RAM** (16 × 16-bit) |
| 0x705180–0x7051BF | QSPI **transmit RAM** (16 × 16-bit) |
| 0x7051C0–0x7051DF | QSPI **command RAM** (32 bytes, 1 per queue entry) |

§15.6.2, p. 15-22…15-24 (PDF 641–642). Each command-RAM byte carries the
peripheral chip-select field (PCS0…PCS3, with PCS0 = PCS0/SS) plus CONT/BITSE/
DT/DSCK. The M95160-class EEPROM will sit on one PCS line; which one is a
question for B4 (read PQSPAR/PORTQS writes and the command-RAM bytes).

`docs/02` §3 counts 49 references to the QSMCM block, consistent with an SPI
EEPROM driver plus at least one SPI peripheral driver (the CJ/CK smart drivers
of MED9 also hang off SPI).

---

## 8. UC3F flash — and why 0x400000-0x403FFF is missing

§21, p. 21-1ff (PDF 863ff). Control registers at 0x6FC800–0x6FC80B
(5 references in our dump: file 0x11D04, 0x11D7C, 0x1D4FC, 0x81CD4, 0x82CBC).

**Array structure**, Fig. 21-8 "512-Kbyte Array Configuration", p. 21-19
(PDF 881): eight 64 KB blocks; blocks 0 and 1 are each split into a 16 KB
"small block" and a 48 KB remainder, and the shadow row is hosted in
**small block 0, which is the first 16 KB of the array**:

| Array offset | CPU address (ISB=1) | Block |
|---|---|---|
| 0x00000–0x03FFF | **0x400000–0x403FFF** | **small block 0 — hosts the shadow row** |
| 0x04000–0x0FFFF | 0x404000–0x40FFFF | block 0 remainder (48 KB) |
| 0x10000–0x1BFFF | 0x410000–0x41BFFF | block 1 remainder (48 KB) |
| 0x1C000–0x1FFFF | 0x41C000–0x41FFFF | small block 1 (16 KB) |
| 0x20000–0x7FFFF | 0x420000–0x47FFFF | blocks 2–7 (64 KB each) |

The shadow row holds the **UC3FCFIG hard-reset configuration word**
(§21.2.3, Table 21-6, p. 21-17…21-18, PDF 879–880), the censorship bits and
the per-block protection. Its RCW fields include everything we had to infer
above: bit 1 `IP` (initial MSR[IP]), bit 19 `ETRE`, bits 24:25 `OERC`,
bits 28:30 `ISB`, bit 31 `DME`, bit 20 `FLEN`.

⇒ **The missing 16 KB is exactly UC3F small block 0**: a separately erasable
and separately protectable block that also carries the device's reset
configuration. Both a flash-programming routine and a tool that talks to it
have every reason to leave it alone, and the ECU's own checksum descriptors
cover `0x404000-0x47FFFF` only (`docs/02` §6) — i.e. the whole array *except*
small block 0. This upgrades the `docs/02` §2 HYPOTHESIS ("most likely a
protected boot/loader sector") to a **manual-backed explanation**, and §5
above adds that the live exception-branch table is in there too.

**Censorship** (§21.3.11, p. 21-29, PDF 891; USIU note p. 5-2, PDF 234):
`UC3FMCR[CENSOR]` (bits 6:7) plus `ACCESS` and `FIC` decide whether the array
may be read at all. The device is in *censored mode* whenever it is
"booting from external memory", "operating in peripheral mode or accessed from
an external master", or "operating in debug mode (BDM or Nexus)" — all three
apply to our ECU. Array reads are then allowed only if `CENSOR = 01/10`
("no censorship"). Since community BDM reads of this part number return the
full 2,621,440 bytes, `CENSOR` is evidently not set to a censoring state on
these ECUs — but it is worth knowing that the mechanism exists before the first
K-TAG session, and that `CENSOR = 11` is non-recoverable except by erasing the
whole array.

Other UC3F facts likely to matter when we write flash:

* Program/erase go through a hardware interlock state machine
  (Fig. 21-10 / Table 21-8, p. 21-27, PDF 889): set `SES`, do an *interlock
  write to any array location*, then set `EHV`; `HSUS` suspends.
* Reads are blocked while `EHV = 1`/`HVS = 1`, during reset in a censored
  state, and while the module is disabled or in STOP (§21.3.3, p. 21-20,
  PDF 882). A flash routine must therefore run from RAM or external flash —
  which is exactly what the DECRAM-resident routine copied from file 0x11118 to
  0x6F8000 does (`docs/02` §3).
* Two 32-byte read page buffers, one for instruction and one for data fetches
  (§21.3.3, p. 21-20).

---

## 9. USIU registers referenced by the boot code

Table 5-1, p. 5-3ff (PDF 234ff). With ISB = 1 add 0x400000:

| CPU address | Register |
|---|---|
| 0x6FC000 | SIUMCR — SIU module configuration |
| 0x6FC004 | SYPCR — system protection (watchdog, bus monitor) |
| 0x6FC00E | SWSR — software service (watchdog "tickle") |
| 0x6FC010 | SIPEND — interrupt pending |
| 0x6FC014 | SIMASK — interrupt mask |
| 0x6FC018 | SIEL — interrupt edge/level |
| 0x6FC01C | SIVEC — interrupt vector |
| 0x6FC020 | TESR — transfer error status |
| 0x6FC024 / 0x6FC028 / 0x6FC02C | SGPIODT1 / SGPIODT2 / SGPIOCR |
| 0x6FC030 | EMCR — external master control |
| 0x6FC038 / 0x6FC03C | PDMCR2 / PDMCR — pad configuration |
| 0x6FC040…0x6FC054 | SIPEND2/3, SIMASK2/3, SISR2/3 |
| 0x6FC100…0x6FC11C | BR0/OR0 … BR3/OR3 (§3) |
| 0x6FC140 / 0x6FC144 | DMBR / DMOR (§4) |
| 0x6FC178 | MSTAT — write-protect error flags |

The boot code addresses these with `lis rX,0x70` followed by a negative
displacement, because `0x700000 - 0x4000 = 0x6FC000`: for example
`lis r10,0x70 ; lwz r11,-0x3F00(r10)` at file 0x1014 is **BR0 (0x6FC100)**, and
`lis r10,0x70 ; lwz r10,-0x3FF0(r10)` at file 0x3F0 and 0x420 is
**SIPEND (0x6FC010)**. Watch for this pattern when reading disassembly: an
apparent `0x700000` base is almost always a USIU access.

SIUMCR bit 1 is `IP` — "Initial Interrupt Prefix … defines the initial value of
MSR[IP] immediately after reset", reset value from RCW bit 1 (Table 6-7, p.
6-22, PDF 299). That is the knob that decides whether the reset vector is
fetched as `0x00000100` (our `ba 0x49C → b 0x1004` path) or as `0xFFF00100`
(ETR → `+0x008` → `ba 0x3C4`). Both paths exist in the image.

---

## 10. Open items

| Question | Status |
|---|---|
| Exact BR↔OR pairing per clock mode, and what the six selector bytes `24 28 30 4C 54 64` mean (PLL multiplier? MHz?) | open — trace file 0x12004-0x121D8 (B1) |
| Which PCS line the EEPROM sits on | open — B4 |
| What the CS2 device at 0x900000 (16-bit port, byte enables, 3-clock access) and the CS3 device at 0xA00000 (32-bit port, 32 KB) actually are | open; CS2 is touched by the DECRAM-resident routine, so it is a strong candidate for the external flash's *programming* interface or a second flash bank |
| Whether `BBCMCR[OERC]` really is 00 (⇒ live vector table in the missing 16 KB) | needs the BDM read, or a run-time read of SPR 560 |
| Whether `UC3FMCR[CENSOR]` is set on our part | needs the BDM read |
