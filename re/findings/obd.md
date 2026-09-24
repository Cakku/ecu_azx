# Generic OBD (SAE J1979 mode 01) — dispatcher, PID tables and support bitmaps

Brief **F6**, issue **#39** (optional half: "PID 0x52 in the OBD handler").
Dump `data/passat_azx_ori.bin`, sha256
`b15590d3f1874ace3125c5d047c09a686db9b8bb498187663539ebab205609b3`.
Date 2026-09-22. All addresses are **CPU addresses**; `file` prefixes file
offsets. In external flash file offset == CPU address; the calibration alias
is `file = cpu - 0x400000` (`docs/02_memory_map.md` §3).

Reproduce everything below with:

```bash
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x5D0F4 --len 0x200
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x5CED4 --len 0x220
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x5CBE8 --len 0x2EC
./.venv/bin/python3 tools/sda_xref.py  data/passat_azx_ori.bin --var 0x801215 0x801220
./.venv/bin/python3 -m unittest tests.test_ff_obd_patch -v      # the dynamic proof, §7
```

---

## 0. Answer in one paragraph

**The generic-OBD stack is in this image.** It is not a separate protocol
stack: J1979 modes 0x01-0x09 are nine more entries in the *same* 28-entry
KWP/OBD dispatch table at 0x2B820 that `re/findings/kwp.md` §1 describes, gated
to diagnostic sessions 4 and 6 instead of 3/5. Mode 01 is
`obd_mode01_h1` (0x5D0F4); the PID value lookup is `obd_pid_read` (0x5CED4);
the four "supported PIDs" bitmaps are **RAM** (0x801215-0x801220), rebuilt from
RAM validity flags by `obd_pid_support_build` (0x5CBE8), *not* flash constants.
A PID is described by two things: one byte in a **dense, index = PID** class
table at 0x0A39B4, and one entry in one of **five (record-pointer, PID, support-
mask) lists** in calibration at 0x5C5D24-0x5C5E1B. **All five lists are exactly
full** (20 + 8 + 3 + 6 + 4 = 41 entries, every slot used) and each loop bound is
an immediate in the code, so **PID 0x52 cannot be added by writing data words
only** — §6 has the decision and the design that a follow-on brief would need.
Per brief F6 task 2 this branch therefore stops after the decision and patches
nothing. *(2026-09-23: brief G1 has since implemented it — §9.)*

---

## 1. Where generic OBD enters (VERIFIED-STATIC)

### 1.1 CAN

No code in the image compares a received identifier against 0x7DF or 0x7E0
(whole-image scan of `addi/cmpi/cmpli/ori/andi.` immediates over
0x000000-0x1BFFFF and the on-chip block; only three hits for 0x7C0/0x7DF, all
in measuring-variable handlers at 0x41EAC/0x41EF4/0x1023A0). The filtering is
done in hardware:

| Direction | Object | Module | MB | Filter | Evidence |
|---|---|---|---|---|---|
| request | `can_cfg_struct[4]`, id 0x7C0, file 0x2BF94 | C (0x707800) | **15** | RX15MSK = 0x7C0 over base 0x7C0 → accepts **0x7C0-0x7FF**, which contains 0x7DF and 0x7E0 | `re/findings/can.md` §2, §6 |
| response | `can_cfg_struct[5]`, id **0x7E8**, file 0x2BFA0, flags = 0x01 | C (0x707800) | **13** | `can_cfg_struct` record dump, this brief | |

Both are *interrupt* objects (`can.md` §1 path 2), not polled `tbl_can_rx`
slots. The ISR body at 0x404000 classifies message buffers through
`can_mb_owner_map` (0x804088) and its **kind 4 = masked range object** arm is
the first one tested (0x4040C4 `cmpwi r28,4`), which is what a 0x7C0-0x7FF
acceptance window has to be.

> **Correction to `re/findings/can.md` §3 (2026-09-22, F6).** That section says
> "Module C (0x707800) **never transmits**. It only receives." That is true of
> `tbl_can_tx` (0x2BDF0), which is the only source §3 checked, but **not** of
> `can_cfg_struct` (0x2BF50): its record [5] registers id **0x7E8 on module C
> buffer 13 with flags = 0x01**, i.e. the OBD-II response object. Module C
> transmits exactly one identifier, 0x7E8. The §3 sentence should be read as
> "module C carries no `tbl_can_tx` object".

### 1.2 Transport and the session gate

The nine OBD entries of the dispatch table at 0x2B820 (`kwp.md` §1) are:

| off | SID (mode) | session mask | h1 | h2 |
|---|---|---|---|---|
| 0x2B9B0 | **0x01** current data | 0x50 | **0x05D0F4** | **0x05CBE8** |
| 0x2B9C4 | 0x02 freeze frame | 0x50 | 0x037804 | 0x037670 |
| 0x2B9D8 | 0x03 stored DTCs | 0x50 | 0x037D2C | — |
| 0x2B9EC | 0x04 clear DTCs | 0x50 | 0x037E1C | — |
| 0x2BA00 | 0x06 test results | 0x50 | 0x427B50 (on-chip) | 0x4278AC |
| 0x2BA14 | 0x07 pending DTCs | 0x50 | 0x037EA0 | — |
| 0x2BA28 | 0x08 control op | 0x50 | 0x037FE0 | — |
| 0x2BA3C | 0x09 vehicle info | 0x50 | 0x0381B0 | 0x03819C |

(Mode 0x05 is absent, as it is on every CAN-only ECU.) Session mask 0x50 =
bits 4 and 6, and the dispatcher's gate is `(1 << kwp_session_current) & mask`
(0x13EA00-0x13EA90, re-read this brief; it matches `kwp.md` §1.3).

`kwp_session_current` (0x803D3E) is written **only** by `kwp_session_set`
(0x13CEE4 — the only `stb` to it in the image,
`tools/find_abs_refs.py --target 0x803D3E`). Of its eleven call sites exactly
one passes **6**:

```
000370F8  obd/kwp SID 0x10 h2 (tbl entry 0x2B8FC +0xC)
000370FC..00037128   r3 = [0x803D6A] (channel type); [0x7F804A] in {0x10,0x11} ...
00037130  lbz   r4, 0x7F804B        ; tester target address
00037134  cmpwi r4, 0x33            ; 0x33 = the ISO 14230-4 / ISO 15765-4 OBD-II address
0003713C  beq   0x3714c
00037140  li    r3, 5               ; else: internal session 5 (the VAG diagnostic one)
0003714C  cmpwi r3, 4               ; channel type 4
00037154  li    r3, 6               ; -> internal session 6 = "generic OBD"
00037158  bl    0x13cee4
```

So **internal session 6 is the generic-OBD session**, reached when the channel
type byte 0x803D6A is 4 and the tester address byte 0x7F804B is 0x33. Session 4
(the upload session `10 86`, `kwp.md` §2) also passes the mask, which is why a
VAG tester can read OBD data without being address 0x33.

0x803D6A is loaded from a per-channel record (`0x1443CC lbz r8,1(r30)`;
`r30 = [0x7FDA7C] + [0x7FDA85]*0x14`, store at 0x1443D4). The ISO-TP
(ISO 15765-2) single-frame reassembly itself sits in the diagnostic module
0x13C000-0x145000 and was **not** pinned to a single function in this brief —
see §8.

---

## 2. Mode 01 — `obd_mode01_h1` at 0x05D0F4 (VERIFIED-STATIC)

Signature is the ordinary KWP one: `r4 = &kwp_io_struct` (`kwp.md` §1.4),
`+0` request buffer, `+6` request length, `+8` response length, `+0xA` status.
The handler writes the answer **into the request buffer**; the transport
prepends `0x41`.

```
0x5D104  r31 = io.len ; 1 <= len <= 6 else -> 0x5D3CC (status 3)
0x5D118  if (buffer[0] & 0x1F) != 0 : loop A  (0x5D138) — every requested PID must have low5 != 0
         else                        : loop B  (0x5D170) — every requested PID must have low5 == 0
         both copy the requested PIDs to a 6-byte stack array, count in r29
0x5D1AC  for each collected PID p:
           p == 0x00 -> emit 0x00 + bitmap bytes [0x1225..0x1228](r13)
           p == 0x02 -> skip (never answered; see the builder's last instruction)
           p == 0x20 -> if [0x1228](r13) & 1: emit 0x20 + [0x1229..0x122C](r13)
           p == 0x40 -> if [0x122C](r13) & 1: emit 0x40 + [0x122D..0x1230](r13)
           p >= 0x59 -> skip                                   (0x5D338 cmpwi 0x59)
           else      -> n = obd_pid_read(p, stack8); if n: emit p + n bytes
0x5D3B4  if nothing was emitted -> status 3, else response length = total, status 1
```

Two consequences worth writing down:

* **A mode-01 request may not mix bitmap PIDs with data PIDs.** All requested
  PIDs must agree on `pid & 0x1F == 0`. A scan tool that asks `01 00 0C` in one
  frame gets nothing.
* **PID 0x02 is hard-wired unsupported** on the mode-01 path (0x5D1B8) and its
  bit is cleared from the bitmap (0x5CEC4-0x5CECC), which is correct — 0x02 is
  a mode-02 PID.

## 3. `obd_pid_read` at 0x05CED4 — the PID → value lookup (VERIFIED-STATIC)

`int obd_pid_read(u8 pid /*r3*/, u8 *out /*r4*/)` returns the number of bytes
written (0 = unsupported).

```
r8 = class_tbl[pid]               ; lis r11,0xa ; addi r11,r11,0x39b4 ; lbzx
group  = (r8 & 0x80) ? A : B
class  =  r8 & 0x0F
scan the (ids, ptrs) list for (group, class); on id == pid and record.valid != 0,
copy `class` value bytes from the record and return that count.
```

### 3.1 The dense class table `tbl_obd_pid_class` — 0x0A39B4, 0x59 bytes

Index = PID, one byte per PID, PIDs 0x00-0x58 (the caller rejects >= 0x59, and
0x0A3A0D onward is a different structure). Byte value = `(group<<7) | class`:

```
       +0 +1 +2 +3 +4 +5 +6 +7 +8 +9 +A +B +C +D +E +F
0x00:  00 05 83 83 82 82 82 82 82 82 82 82 83 82 82 82
0x10:  83 82 82 02 03 03 03 03 03 03 03 03 02 02 82 83
0x20:  00 03 83 83 05 05 05 05 05 05 05 05 82 82 82 82
0x30:  02 03 03 82 05 05 05 05 05 05 05 05 03 03 03 03
0x40:  00 05 83 83 83 82 82 82 82 82 82 82 82 00 00 00
0x50:  00 00 00 00 00 00 82 00 82
```

`class` is the J1979 response length: **2 → 1 byte, 3 → 2 bytes, 5 → 4 bytes**
(checked against J1979 for all 76 non-zero entries: 0x01 = 4 B, 0x0C = 2 B,
0x05 = 1 B, 0x24-0x2B = 4 B, 0x3C-0x3F = 2 B, …). `group` selects which pair of
lists is scanned; group A holds classes 2 and 3, group B holds 2, 3 and 5.

**The class byte is necessary but not sufficient**: 76 PIDs have a non-zero
class byte, only **41** have a list entry, and the other 35 answer "not
supported". **PID 0x52's class byte is 0x00.**

### 3.2 The five lists — calibration 0x5C5D24-0x5C5E1B (r2 = 0x5C9FF0)

Each list is three parallel arrays: `u32 record_ptr[n]`, `u8 pid[n]`,
`u8 support_mask[n]`, laid out back to back with no padding.

| List | group/class | value bytes | valid flag | ptrs | ids | masks | n | loop bound |
|---|---|---|---|---|---|---|---|---|
| A2 | A, 2 | 1 | rec+1 | 0x5C5D24 (r2-0x42CC) | 0x5C5D74 (r2-0x427C) | 0x5C5D88 (r2-0x4268) | **20** | 0x5CF44 `cmpwi r8,0x14`, 0x5CC74 `cmpwi r3,0x14` |
| A3 | A, 3 | 2 | rec+2 | 0x5C5D9C (r2-0x4254) | 0x5C5DBC (r2-0x4234) | 0x5C5DC4 (r2-0x422C) | **8** | 0x5CFA4, 0x5CCE8 |
| B2 | B, 2 | 1 | rec+1 | 0x5C5DCC (r2-0x4224) | 0x5C5DD8 (r2-0x4218) | 0x5C5DDB (r2-0x4215) | **3** | 0x5D010, 0x5CD5C |
| B3 | B, 3 | 2 | rec+2 | 0x5C5DE0 (r2-0x4210) | 0x5C5DF8 (r2-0x41F8) | 0x5C5DFE (r2-0x41F2) | **6** | 0x5D070, 0x5CDD0 |
| B5 | B, 5 | 4 | rec+4 | 0x5C5E04 (r2-0x41EC) | 0x5C5E14 (r2-0x41DC) | 0x5C5E18 (r2-0x41D8) | **4** | 0x5D0E4, 0x5CE44 |

The whole block is contiguous: it begins immediately after the measuring-block
group table (`tbl_measuring_groups` 0x5C5518 + 4·0x1FE = 0x5C5D10, then 0x14
bytes of a different table), and it ends at 0x5C5E1B with another pointer table
at 0x5C5E1C. The only slack anywhere inside it is **two bytes** of 0x00 at
0x5C5DDE-0x5C5DDF, between the B2 masks and the B3 pointers.

Contents (PID → RAM record; `valid` is the record byte listed above):

* **A2 (20/20):** 04→0x800EF5, 05→0x802235, 06→0x801209, 07→0x80120B,
  08→0x80120D, 09→0x80120F, 0D→0x802266, 0E→0x802097, 0F→0x802237,
  11→0x8010A7, 2E→0x801EC3, 33→0x800F5B, 45→0x8010A9, 46→0x802231,
  47→0x8010AB, 49→0x8025F9, 4A→0x8025FB, 4C→0x8010AD, 56→0x801211,
  58→0x801213
* **A3 (8/8):** 03→0x80197E, 0C→0x802132, 10→0x800F54, 1F→0x801365,
  23→0x802032, 42→0x8020F6, 43→0x800EF7, 44→0x80151A
* **B2 (3/3):** 13→0x801A58, 1C→0x801356, 30→0x801358
* **B3 (6/6):** 15→0x801B9C, 19→0x801B9F, 21→0x80135A, 31→0x80135D,
  3C→0x8019A7, 3D→0x8019AA
* **B5 (4/4):** 01→0x801360, 34→0x801550, 38→0x801555, 41→0x8011FB

**Every slot of every list is occupied and every id is distinct. There is no
free entry.**

## 4. The four "supported PIDs" bitmaps are RAM, not flash (VERIFIED-STATIC)

`obd_pid_support_build` (0x5CBE8), the mode-01 table entry's **h2**, rebuilds
them from scratch:

```
0x5CBF0  memset(r13+0x1225, 0, 12)                         ; 0x801215..0x801220
for each of the five lists, for each entry i:
    if record.valid == 0            -> skip        (0x5CC18 / 0x5CC8C / ...)
    if class_tbl[pid] == 0          -> skip        (0x5CC34, lis 0xA / addi 0x39B4)
    byte = (pid - 1) >> 3                          ; 0x5CC40 addi -1 ; srawi 3 ; addze
    bitmap[byte] += support_mask[i]                ; 0x5CC5C..0x5CC68 (an ADD, not an OR)
0x5CE4C  if any of [0x122D..0x1230] != 0 : [0x122C] += 1   ; "PID 0x40 supported"
0x5CE88  if any of [0x1229..0x122C] != 0 : [0x1228] += 1   ; "PID 0x20 supported"
0x5CEC4  [0x1225] &= ~0x40                                 ; PID 0x02 always cleared
```

| Bitmap | RAM | r13 disp |
|---|---|---|
| PID 0x00 (PIDs 0x01-0x20) | **0x801215-0x801218** | +0x1225..+0x1228 |
| PID 0x20 (PIDs 0x21-0x40) | **0x801219-0x80121C** | +0x1229..+0x122C |
| PID 0x40 (PIDs 0x41-0x60) | **0x80121D-0x801220** | +0x122D..+0x1230 |

`tools/sda_xref.py --var 0x801215 0x801220` finds 26 references, all inside
0x5CE4C-0x5D2FC, i.e. only these two functions touch them.

**Consequence for a flex-fuel feature:** the brief's fallback ("if the bitmap is
a flash constant the bit cannot be gated at run time; then ship the bit clear")
does **not** apply. The bitmap is derived from the record's `valid` byte in RAM,
so a patch that owns the record can advertise or hide PID 0x52 at run time by
writing 0 or 1 to one RAM byte — **no calibration edit is needed to turn the bit
on or off**, the next run of `obd_pid_support_build` follows it.

`support_mask[i]` is simply the J1979 bit for that PID
(`mask = 0x80 >> ((pid - 1) & 7)`); verified for all 41 entries. **PID 0x52
would be byte index `(0x52-1)>>3 = 10` → RAM 0x80121F, mask 0x40.**

## 5. Where the mode-01 answer would come from

PID 0x52 is `A × 100 / 255` ethanol percent, one byte, class 2. The patch's
`e_filt` already exists as whole percent in `ff_state` (see
`docs/05_flexfuel_design.md` §3.7 and `patches/ff_fuel/README.md`), so the
handler would be `A = round(e_pct * 255 / 100)` clamped to 0..255, written into
a two-byte record `{A, valid}` owned by the patch, with
`valid = ff_pid52_enable && ff_cal_ok()`.

## 6. Decision (brief F6 task 2): **NO — not a data-only addition**

Adding PID 0x52 needs **three** things, and only the first two are data:

1. `tbl_obd_pid_class[0x52] = 0x82` (one byte in external flash at
   file 0x0A3A06, Bosch checksum block `desc 0x0A0100`, 0x0A0000-0x0A7FFF) —
   trivial.
2. a list entry `{record_ptr, pid = 0x52, mask = 0x40}` — **no free slot
   exists** (§3.2).
3. therefore a **longer list**, which means relocating that list's three
   arrays (they are packed back to back with 2 bytes of slack in the whole
   block) *and* rewriting the code immediates that address them and bound
   their loops.

For the cheapest list (B2, 3 entries, 3·4+3+3 = 18 bytes) that is **7
instruction words** in stock code, all inside the one Bosch checksum block
`desc 0x0A0070` (0x058000-0x05FFFF):

| Site | Now | Would become |
|---|---|---|
| 0x5CCF4 | `addi r4,r2,-0x4224` (ptrs) | new displacement |
| 0x5CD0C | `addi r12,r2,-0x4218` (ids) | new displacement |
| 0x5CD40 | `addi r9,r2,-0x4215` (masks) | new displacement |
| 0x5CD5C | `cmpwi r3,3` | `cmpwi r3,4` |
| 0x5CFD4 | `addi r6,r2,-0x4218` (ids) | new displacement |
| 0x5CFE4 | `addi r12,r2,-0x4224` (ptrs) | new displacement |
| 0x5D010 | `cmpwi r8,3` | `cmpwi r8,4` |

plus 24 bytes of free calibration inside r2 ± 32 KB (0x5C2010-0x5D1FEF) to hold
the grown list. The only *erased* (0xFF) runs in that window are 0x5C2188 (56 B),
0x5C6AF6 (72 B) and 0x5C8262 (65 B) — HYPOTHESIS that they are unused; the many
0x00 runs are almost certainly real map cells and must not be assumed free.

That is seven edits to stock instructions in a function nobody has hooked,
i.e. exactly the "instruction rewrite in the dispatcher" the brief rules out
for an optional feature. **Branch `agent/F6` therefore stops here: nothing in
`patches/ff_fuel/**` was touched, FFCAL001 stays at v4 / 332 B, and the patched
image is byte-for-byte the wave-F one.**

Two routes that were considered and rejected:

* **Steal a stock PID's slot** (e.g. A2 entry 19, PID 0x58 long-term secondary
  fuel trim bank 2). The id and mask bytes live in flash, so the substitution
  cannot be gated by `ff_pid52_enable`: with the byte at 0 the image would
  still have lost PID 0x58, which fails the bit-identity rule. §7 uses this as
  a *control experiment only*.
* **Redirect the `bl 0x5CED4` at 0x5D348** to a patch function that falls back
  to the stock lookup. One word, and it is a normal trampoline — but it is
  still a code edit inside the stock mode-01 handler, outside D2's data-only
  pattern, and it would not be covered by the eight hook words the patch README
  accounts for. Left for the follow-on brief to weigh against option 3 above.

**Recommended for the follow-on brief:** — **SETTLED (2026-09-23, G1, §9):
implemented in `patches/ff_fuel` on the B2 list, run-time gated by
`ff_pid52_enable`, with two corrections: the list lives in free flash
0x160000 rather than calibration (§9.1-9.2), and the class byte is 0x02, not
0x82 (§9.3).** Option 3 on the **B2** list (7 words,
the smallest), because after it the feature is *fully* run-time gated — the
enable byte drives the record's `valid` flag, which drives both the answer and
the support bitmap, and with `valid = 0` the extra loop iteration is
side-effect-free, so the disabled image is behaviourally identical to stock.
The mode-01 exit criterion of #39 is in any case already met by measuring block
111 (`measuring_vars.md` §8), so PID 0x52 stays optional.

## 7. Dynamic proof (VERIFIED-DYNAMIC, emulated)

`tests/test_ff_obd_patch.py` drives the **real** handlers out of the dump with
the `emu/` Unicorn harness (one persistent `Med9Emu`, `reset=False` between
calls), seeding one record per list and then calling `obd_pid_support_build`
followed by `obd_mode01_h1`:

```
stock image, records seeded for PIDs 01, 05, 0C, 13, 21
  bitmaps 0x801215..0x801220 = 88 10 20 01 | 80 00 00 00 | 00 00 00 00
  01 00     -> status 1, 00 88 10 20 01      (bits: 01=0x80 05=0x08 0C=0x10 13=0x20, +0x20 cont.)
  01 20     -> status 1, 20 80 00 00 00      (bit 21 = 0x80; no 0x40 continuation)
  01 40     -> status 3 (unsupported: [0x122C] bit0 clear)
  01 05     -> status 1, 05 5a
  01 0C     -> status 1, 0c 1a f8
  01 01     -> status 1, 01 82 07 65 00
  01 52     -> status 3   <-- PID 0x52 unsupported today
  01 05 0C  -> status 1, 05 5a 0c 1a f8

class byte 0x0A39B4+0x52 := 0x82 and nothing else
  bitmaps unchanged, 01 52 -> status 3        <-- the dense table alone does nothing

control: class byte + steal A2 slot 19 (id 0x58->0x52, mask 0x01->0x40, ptr->0x807F00={0x85,1})
  bitmaps = 88 10 20 01 | 80 00 00 01 | 00 00 40 00
  01 52 -> status 1, 52 85                    (0x85 = 133 -> 133*100/255 = 52.2 %)
  01 40 -> status 1, 40 00 00 40 00
```

The control is what proves the whole mechanism end to end **and** that the only
thing missing is a free slot: with a slot, one class byte plus three data bytes
plus a RAM record are enough, and both the answer and the support bitmap follow.

## 8. Open

| # | Item | State |
|---|---|---|
| 1 | The ISO 15765-2 single-frame parser: which function reads module C MB15 and hands the payload to `kwp_service_dispatch`. Traced as far as the ISR at 0x404000 kind-4 arm (0x4040C4) and the channel table `[0x7FDA7C] + idx*0x14` (0x1443C4). | **SETTLED (2026-09-24, H3, §11.1-11.2)**: MB15 → `can_poll_iflag_c` 0x1366EC → ISR body 0x404000 kind-4 range object 0xD8 (0x7DF) → `obd_func_rx_ind` 0x0B5534 → **`isotp_rx_indication` 0x1420A0** (PCI check, SF only on 0x7DF, DLC 8 required) → connection layer 0x13F18C / `kwp_conn_cyclic` → `kwp_service_dispatch` with descriptor 0x803D60; answer by `isotp_transmit` 0x1429BC → `can_tx_frame` 0x13C4CC on MB13 id 0x7E8, SF `0N` + 0x00 padding, DLC 8. The `[0x7FDA7C]` table is not on this path |
| 2 | Where the dispatch-table **h2** field (+0xC, 0x5CBE8 for mode 01) is called from. 0x5CBE8 is referenced only by the table word at 0x2B9BC, and the dispatcher itself (0x13E98C) only ever calls +0x8; the three `blrl`s at 0x13EB74/0x13EB90/0x13EBAC take handlers from the *config struct*, not the entry. So the bitmap builder runs from a second walker not yet found. | **SETTLED (2026-09-23, G3, §10)**: `kwp_service_h2_walk` **0x13ECB0** calls every non-NULL entry +0xC of the 28-entry table (`blrl` at 0x13ED10). It is called only from `kwp_conn_cyclic` 0x13E650 (0x13E71C, 0x13E840), which runs every **10 ms** from the set-A 10 ms task (0x432BC0 → 0x4328B4 → 0x4328C4), and fires the walk **once per new diagnostic connection** (and on a transport channel re-selection), not on every request. The bitmaps are therefore current as of the connection's first 10 ms tick |
| 3 | Whether the 0xFF runs at 0x5C2188 / 0x5C6AF6 / 0x5C8262 are genuinely free calibration. | **SETTLED (2026-09-23, G1, §9.1): EXCLUDED** — none is free: 0x5C6AF6 and 0x5C8262/0x5C8282 are live tables with r2-relative readers, 0x5C2188 is in the calibration segment header, and so are the other 0xFF runs of 24 B or more in the window |
| 4 | Whether internal session 6 is actually reachable on the car: it needs `[0x803D6A] == 4` and `[0x7F804B] == 0x33`, and 0x7F804B has exactly one reader and no statically resolvable writer (it is inside the word written by `stw` at 0x15BB8/0x15C0C and possibly by the `stswi` at 0x1443A0). Session 4 (`10 86`) reaches the OBD services regardless. | **SETTLED (2026-09-24, H3, §11.1 steps 11-14, §11.4)**: reachable, and it is what a generic scan tool gets. The 0x7DF rx map entry carries address **0x33**, the connection's address list maps 0x33 to channel type **4**, `kwp_conn_open` 0x13F18C writes 0x7F804B at **0x13F1E4** (base+displacement, missed by the lis-pair scan), and the connection's h2 walk sets session **6**. Physical 0x7E0 is refused by its gate 0x2C29C (`li r3,0`), and session 4 cannot be entered from the ISO 15765-4 route. VERIFIED-DYNAMIC (emulated). Bench check: `01 00` on 0x7DF → an answer on 0x7E8 (and `3E` on 0x7DF → silence) |
| 5 | Modes 0x02-0x09 handlers (0x37804, 0x37D2C, 0x37E1C, 0x427B50, 0x37EA0, 0x37FE0, 0x381B0) are named only from the dispatch table; none was read. | NOT DONE (out of scope) |

---

## 9. Brief G1 (2026-09-23): implementing PID 0x52 on the B2 list

Brief **G1** implements §6's recommended option 3 in `patches/ff_fuel`. This
section records what the implementation had to settle first; the patch side
is `patches/ff_fuel/README.md` "Stock-instruction edits (PID 0x52)".

### 9.1 No 0xFF run in the r2 window is free calibration (VERIFIED-STATIC)

§6 and §8 item 3 named three erased runs as HYPOTHESIS-free. A whole-window
scan (0x5C2010-0x5D1FEF, runs of 0xFF of at least 16 bytes) finds twelve, and
**every run of 24 bytes or more is a live cell or sits in the calibration
header**:

| Run | Bytes | Evidence it is live | Verdict |
|---|---|---|---|
| 0x5C2020-0x5C207F, 0x5C20C0-0x5C217F, 0x5C2188-0x5C21BF | 96, 192, 56 | inside the calibration segment header 0x5C2000-0x5C223F (its own Bosch block, desc 0x1C3310): `5A5A5A5A CCCCCCCC` then a pointer table at 0x5C2008-0x5C2014 whose words 0x1C2040 / 0x1C20C0 / 0x1C2100 point **into** the first two runs, and the word at 0x5C2184 (0x1C2240) sits in front of the third | header, never a patch target |
| 0x5C421A-0x5C4231 | 24 | three 8-byte tables: `addi r30,r2,-0x5dd6` at 0xFE4BC (= 0x5C421A), and r2-relative `addi` to 0x5C4222 (0xFE22C) and 0x5C422A (0xFE26C) | live |
| 0x5C61B8-0x5C61E9 | 50 | the **value body of a 5 x 5 u16 map** whose header is at 0x5C61A0 (`00 05 00 05`, x axis 2400..24000, y axis 0..0x6400, then 25 x 0xFFFF = exactly these 50 bytes); `addi r3,r2,-0x3e50` at on-chip 0x42C248 passes 0x5C61A0 to the interpolator | live |
| **0x5C6AF6**-0x5C6B25 | 48 | `addi r3,r2,-0x34fa` at on-chip 0x430498 (= 0x5C6AF6) loads it as a map argument; the next cell 0x5C6B26 is referenced by 0x430500 | live |
| **0x5C8262**-0x5C82A1 | 64 | **two** 16-entry u16 tables: `addi r3,r2,-0x1d8e` at 0xF5BB8 (= 0x5C8262) and `addi r3,r2,-0x1d6e` at 0xF5BCC (= 0x5C8282), each indexed by `(word >> 16) * 2` and `lhzx` | live |

So §8 item 3 is **excluded, not confirmed**: the three runs F6 listed (its
lengths 56/72/65 were measured differently; the table above gives the exact
bounds) are two live tables and a map body, and 0x5C2188 is the segment
header. The "0xFF = erased" reading was wrong for a calibration area: these
are real maps whose cells happen to hold 0xFF / 0xFFFF. The 0x00 runs were
not considered, per §6.

Reproduce: the r2-relative (`D-form` and `addi` with rA = 2, application base
0x5C9FF0), `lis`-pair (`tools/find_abs_refs.py` `resolve()`) and pointer-word
scan in `tests/test_ff_obd_patch.py::TestFreeCalibrationSurvey`, which
re-derives the table on every run.

### 9.2 The fallback: the grown list in the patch's own free flash

Because no calibration run is free, the brief's fallback applies: the 24-byte
list goes into free external flash. A `addi rD,r2,d` cannot reach it (r2 ±
32 KB), and each of the five array-base sites must stay **one instruction**.
Three one-instruction forms reach the free area, one per base register the
ABI keeps constant:

| Form | Value | Holds |
|---|---|---|
| `lis rD, 0x16` | **0x160000** | `u32 record_ptr[4]` (16 B) |
| `addis rD, r2, -0x46` | 0x5C9FF0 - 0x460000 = **0x169FF0** | `u8 pid[4]` |
| `addis rD, r13, -0x69` | 0x7FFFF0 - 0x690000 = **0x16FFF0** | `u8 support_mask[4]` |

All three are inside the one Bosch block 0x160000-0x16FFFF (code descriptor
0x0A0260), which is entirely 0xFF in the stock image and referenced by nothing
(`tools/find_abs_refs.py --range 0x160000 0x16FFFF` prints nothing; the only
aligned words with a value in the block are the descriptor itself at 0x0A0260
and two map cells at 0x5DA4D8/0x5DACB0 whose values merely fall in range).
The blob's own block 0x150000-0x15FFFF was not used because its one
`lis`-reachable word, 0x150000, is where `patches/ff_counter` links.
VERIFIED-STATIC.

### 9.3 Correction to §6 step 1: the class byte for a B2 entry is 0x02, not 0x82

`obd_pid_read` (0x5CED4) selects the list group from **bit 7** of the class
byte: `rlwinm. r10,r8,0,0x18,0x18` at 0x5CEE4, `beq 0x5cfb0` — set means the
**A** lists (0x5CEEC), clear means the **B** lists (0x5CFB0). The three stock
B2 PIDs 0x13, 0x1C and 0x30 all carry class byte **0x02**
(`test_every_list_slot_is_taken` asserts it). §6 step 1's `0x82` is what F6's
*control experiment* needed, because it stole an **A2** slot; for the B2 route
it would send PID 0x52 to the A2 lists, which do not contain it, while
`obd_pid_support_build` — which only tests the class byte for non-zero — would
still advertise it: `01 40` would say "supported" and `01 52` would answer
nothing. **The B2 route writes `tbl_obd_pid_class[0x52]` = 0x02.**
VERIFIED-STATIC (the disassembly above), VERIFIED-DYNAMIC (emulated) by
`tests/test_ff_obd_patch.py`.

The mode-02 users of the class table (0x37698, 0x3770C, 0x37B78, found with
`tools/find_abs_refs.py --range 0x0A39B4 0x0A3A0C`) scan **only the A2/A3
lists** for the freeze-frame bitmap and take the unsupported path at 0x37B88
whenever bit 7 is clear. A class byte of 0x02 is therefore invisible to mode
02, exactly as the stock 0x00 is. VERIFIED-STATIC.

### 9.4 Nothing else reads the B2 arrays (VERIFIED-STATIC)

The r2-relative scan of §9.1, restricted to 0x5C5D10-0x5C5E20, finds exactly
five references to the three B2 arrays — 0x5CCF4 and 0x5CFE4 (pointers),
0x5CD0C and 0x5CFD4 (ids), 0x5CD40 (masks) — and no `lis` pair and no pointer
word. The seven edits of §6 therefore redirect every reader; the stock arrays
at 0x5C5DCC-0x5C5DDD are left byte-for-byte as they are.

Naming, for the record: §6 calls 0x5CCF4-0x5CD5C "the reader" and
0x5CFD4-0x5D010 "the builder". It is the other way round —
0x5CCF4/0x5CD0C/0x5CD40/0x5CD5C are in `obd_pid_support_build` (0x5CBE8) and
0x5CFD4/0x5CFE4/0x5D010 in `obd_pid_read` (0x5CED4). The addresses in the
table are right.

### 9.5 The dynamic proof (VERIFIED-DYNAMIC, emulated)

`tests/test_ff_obd_patch.py` layers 5-7 run the real `obd_pid_support_build`
and `obd_mode01_h1` of the **patched** image (`tools/patch_apply.py` output)
with all 41 stock records valid and the PID 0x52 record produced by the real
10 ms tick:

* `ff_pid52_enable` = 0 — the bitmaps and every answer to `01 00`..`01 58`
  (plus three multi-PID requests) are byte-for-byte the stock image's, and a
  whole-SRAM diff after the builder moves nothing outside `ff_state`;
* `ff_pid52_enable` = 1 — the only SRAM byte outside `ff_state` that moves is
  0x80121F (bit 0x40 added), `01 40` shows it, and `01 52` answers `52 A` with
  A = 0 / 128 / 217 / 255 for E0 / E50 / E85 / E100;
* with F6's five sparse records, PID 0x52 alone also sets the `01 40`
  continuation bit (bitmaps `88102001 80000001 00004000`);
* `01 52` answers `41 52 D9` through `logging/ecu_sim.py`'s KWP dispatch in
  internal session 6, and is refused exactly as on the stock image with the
  switch off.

Cost: `obd_pid_support_build` 1,196 → 1,205 (off) / 1,223 (on) instructions
with all records valid; `obd_pid_read` for a B2 PID 44 → 51. Nothing here
settles §8 items 1, 2 or 4; they stay bench items.

---

## 10. Brief G3 (2026-09-23): who calls `obd_pid_support_build` — §8 item 2

VERIFIED-STATIC unless tagged. The second walker is not on the ISO-TP path and
not a mode-01 pre-pass: it is a **connection-start hook walk** in the
diagnostic layer, run from the 10 ms task.

### 10.1 `kwp_service_h2_walk` 0x13ECB0 calls every entry's +0xC

The config-struct pointer 0x803DDC (`= 0x2BA50`, stored by `kwp_register_table`
0x13E974 from 0x13C9FC) has four readers; the one outside the dispatcher is

```
0013ECC0  bl   0x13CF90 ; bl 0x13CF38(lbz [0x7F804E]) ; bl 0x13EC98
0013ECD8  r28 = &0x803DDC ; r31 = i = 0 ; r30 = i*0x14
0013ECF4  r12 = [[0x803DDC]] + r30          ; entry i of the 28-entry table 0x2B820
0013ED00  lwz  r29,0xC(r12)                 ; h2
0013ED04  cmpwi r29,0 ; beq skip
0013ED0C  mtlr r29 ; blrl                   ; h2()
0013ED14  r30 += 0x14 ; i++ ; while i < [config+0x1C] (= 28)
```

Nine entries have an h2; this walk calls all nine, in table order:

| entry | SID | h2 | what it is |
|---|---|---|---|
| 0x2B870 | 0x14 | 0x0352F4 | |
| 0x2B898 | 0x3B | 0x0A3320 | |
| 0x2B8AC | 0x2C | 0x035034 | |
| 0x2B8FC | 0x10 | **0x0370F8** | picks internal session 5 or **6** from the channel type and tester address 0x33 (§1.2) |
| 0x2B94C | 0x36 | 0x0A33A4 | |
| 0x2B9B0 | **0x01** | **0x05CBE8** | `obd_pid_support_build` (§4) |
| 0x2B9C4 | 0x02 | 0x037670 | |
| 0x2BA00 | 0x06 | 0x4278AC | |
| 0x2BA3C | 0x09 | 0x03819C | |

So h2 is the per-service **"a tester has connected" hook**, which is why SID
0x10's h2 is the one that sets session 6: both belong to the same moment.

### 10.2 When the walk runs

`find_branch_refs 0x13ECB0` gives exactly two `bl`s, both inside
**`kwp_conn_cyclic` 0x13E650** (0x13E650-0x13E948). It asks the transport for
the active connection (`bl 0x13CC5C` → r30, 0 = none) and compares it with the
one it saw last time (0x803D70):

| site | condition | then |
|---|---|---|
| 0x13E71C | r30 ≠ 0, `[0x803D70]` = 0, byte 0x7F8064 ≠ 0 (a request-pending flag, name HYPOTHESIS; written at 0x036B74, 0x08E04C, 0x13E838) | `bl 0x13D77C`; **h2 walk**; `[0x803D70] = r30` |
| 0x13E840 | r30 ≠ 0, `[0x803D70]` ≠ 0, channel-reselect flag 0x803D10 ≠ 0 (written only at 0x13CB84, inside the channel search of 0x13CC5C, which clears it on entry) | `0x7F8064 = 0`; `bl 0x13D77C`; **h2 walk** |
| 0x13E680 | r30 = 0, `[0x803D70]` ≠ 0 | connection gone: end hooks, `[0x803D70] = 0` |

`kwp_conn_cyclic` itself has three callers:

| caller | in | rate |
|---|---|---|
| 0x4328C4 (`bl`) in 0x4328B4 | the set-A 10 ms task 0x4328E4 (id 19), its second-last `bl` at 0x432BC0 | **10 ms**, live (scheduler.md §11.8) |
| 0x13CA48 (`bl`) in 0x13CA1C | the set-B 10 ms epilogue 0x120570 (`bl` at 0x120580) | 10 ms, set B only |
| 0x40BEEC (`b`) | the only process of task 20 (list 0x0B1ED4) | event task; nothing in the image loads its handle 0x4787C0 except the uncalled thunk 0x0B09A0 — no static activator |

### 10.3 What that means for `01 00`

* The support bitmaps are rebuilt **once per diagnostic connection**, at the
  first 10 ms tick of `kwp_conn_cyclic` that sees it (plus on a channel
  re-selection). They are **not** rebuilt per request and not on a raster.
  Whether that tick always precedes the dispatch of the connection's first
  request (the dispatcher 0x13E98C is reached from 0x13D890 / 0x13E260, not
  traced here) is open; a tester that sends `01 00` as its very first frame
  could in principle see the previous connection's bitmap.
* So a scan tool's `01 00` sees the record `valid` bytes **as they were when
  it connected**. A PID whose `valid` byte goes 0 → 1 during a session (for G1:
  `ff_obd_pid52_rec.valid` after `ff_cal_ok()` and the enable byte) appears in
  `01 40` only after the tester disconnects and reconnects; `01 52` itself is
  answered as soon as `valid` is 1, because `obd_pid_read` (§3) checks the byte
  on every request. The converse holds too: a PID that becomes invalid stays
  advertised until the next connection, and its `01 52` then returns nothing.
  G1's run-time gate is therefore correct but **per connection**, not
  instantaneous — worth one line in a bench test (connect, enable, reconnect).
* VERIFIED-DYNAMIC (emulated): with the config pointer 0x803DDC = 0x2BA50,
  `emu.call(0x13ECB0)` over a bitmap pre-filled with 0xAA returns in 2,123
  instructions and leaves exactly the bytes a direct `emu.call(0x5CBE8)` leaves
  (the stock valid flags are 0 on a cold emulator, so both are 12 × 0x00).

### 10.4 Reproduction

```bash
./.venv/bin/python3 tools/find_abs_refs.py data/passat_azx_ori.bin --range 0x803DDC 0x803DE0
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x13ECB0 --addr 0x13ECB0 --len 0x90
./.venv/bin/python3 tools/find_branch_refs.py data/passat_azx_ori.bin 0x13ECB0 0x13E650 0x4328B4 0x13CA1C
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x13E650 --addr 0x13E650 --len 0x200
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x22E8B4 --addr 0x4328B4 --len 0x30
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x22EBB0 --addr 0x432BB0 --len 0x2C
./.venv/bin/python3 -c "import struct;d=open('data/passat_azx_ori.bin','rb').read();[print(hex(0x2B820+20*i),hex(d[0x2B820+20*i]),hex(struct.unpack('>I',d[0x2B82C+20*i:0x2B830+20*i])[0])) for i in range(28) if d[0x2B82C+20*i:0x2B830+20*i]!=bytes(4)]"
```

The emulated check: `Med9Handlers(animate=False)` from `logging/ecu_sim.py`;
`emu.call(0x5CBE8, reset=False)`, keep the 12 bytes at 0x801215; fill them with
0xAA, write 0x0002BA50 to 0x803DDC, `emu.call(0x13ECB0, reset=False)`, compare.

---

## 11. Brief H3 (2026-09-24): the ISO 15765-2 route, 0x7DF → 0x7E8, and session 6 — §8 items 1 and 4

Brief **H3**, issue **#48** (#39 bench prerequisite). Everything below is
**VERIFIED-STATIC** (disassembly, `tools/blobdis.py`) unless tagged; the
emulated runs are **VERIFIED-DYNAMIC (emulated)** — the firmware's own code
under the `emu/` Unicorn harness with the start-up and harness pieces listed in
§11.6 — and say nothing about a car. The short answer:

* **A 0x7DF single frame reaches `kwp_service_dispatch` and is answered on
  0x7E8 in internal session 6.** The ISO 15765-2 layer is a complete,
  table-driven implementation (single frame, first/consecutive frame, flow
  control) at 0x1420A0 / 0x1429BC; the functional channel accepts **single
  frames only**, the ISO 15765-4 functional address byte **0x33** comes from a
  flash table, becomes 0x7F804B, and SID 0x10's h2 selects session 6 at the
  connection's first 10 ms tick.
* **A physical 0x7E0 request is received and then refused**: its connection
  gate is `li r3,0` (0x2C29C), so the ECU never answers 0x7E0. Session 4 is
  therefore **not** reachable from the ISO 15765-4 route at all (SID 0x10 is
  not in session 6's mask 0x50); it stays the TP2.0 route of §1.2.

### 11.1 The request path, function by function

| # | Step | Where | Evidence / note |
|---|---|---|---|
| 1 | TouCAN **C** MB15 accepts 0x7C0-0x7FF (RX15MSK 0x7C0, `can.md` §6); IFLAG bit 15 | hardware | — |
| 2 | Module C interrupt: ISR wrapper **0x41797C** (the sixth of `scheduler.md` §3) → thunk 0x40C1B0 (`bl` at 0x4179B0) → **`can_poll_iflag_c` 0x1366EC** (IFLAG ≠ 0) → ISR body **0x404000** with r3 = CANMCR 0x707880, r4 = 2 | 0x136700 `lhz r11,0x24(r4)`, 0x136714 `bl 0x404000`; `sda_xref.py --code 0x1366EC 0x404000` | hardware-interrupt level, not a task |
| 3 | `can_mb_owner_map` 0x804088 entry of C/MB15 has kind 4; the kind-4 arm (0x4040C4) indexes the group map **0x2C1BC** (`00 01 02 05 09 0E`, one byte per (module, MB14/15) pair) with `mb + 2·module − 14`, converts the buffer's id word (`bl 0x13C5FC` on `lwz 0x82`), walks the **range objects** of that group until `lo <= id <= hi`, then `blrl` +0x10 with r3 = the object's handle byte | 0x4040C4-0x4041F8 | |
| 4 | Range-object table **0x2C054**, 18 × 0x14 `{u8 handle; u32 lo; u32 hi; u8 flags, mb, module; u32 callback}`, registered by `can_range_obj_register` **0x13C288** (r3 = table, r4 = group map, r5 = 18; the tail `b` at 0x12E704 inside init entry 44). Module C MB15 carries four: **0xD7** 0x7D0 → 0x0A953C, **0xD8 0x7DF → 0x0B5534**, **0xD9 0x7E0 → 0x1420A0**, 0xDA unused (range 0xFFFFFFFF) | table dump; `[0x7FB994]` = 0x2C054, `[0x7FB998]` = 0x2C1BC after init entry 44 (emulated) | |
| 5 | **`obd_func_rx_ind` 0x0B5534** (0x7DF): if `[0x803DE0]` = 9 (the active connection is a TP2.0 one, §11.3) it calls 0x13AE18 and `kwp_session_set(0)`; then tail-branches to 0x1420A0 with the same handle | 0x0B5548-0x0B5574 | a 0x7DF frame ends a running TP2.0 session (HYPOTHESIS that 0x13AE18 is the TP2.0 disconnect) |
| 6 | **`isotp_rx_indication` 0x1420A0**: `can_rx_obj_read` 0x136AF4(handle, buf, …, &dlc) copies the 8 data bytes out of the buffer; the rx map **0x2C55C** (6-byte entries `{handle, channel, ext_addr, addr, need_dlc8, ff_allowed}`, entries `cfg[0]`..`cfg[2]-1` = 3..6 of the transport config **0x2C5F8**) gives the channel: 0xD9 → ch 3 addr 0x10, **0xD8 → ch 4 addr 0x33, ff_allowed 0** | 0x1420D0, 0x1420E0-0x142158 | |
| 7 | the **PCI byte** = data[0] (normal addressing, `ext_addr` = 0): type = `pci & 0xF0`; > 0x30 → dropped; **DLC ≠ 8 → dropped** (`need_dlc8` = 1 on both OBD entries, 0x142170-0x142180 — ISO 15765-4's fixed DLC); **FF (0x10) → dropped when `ff_allowed` = 0** (0x142184-0x142194), i.e. on 0x7DF | | |
| 8 | channel state (**0x804734** + ch·0x1C) must be armed (`flags & 0x82` = 0x82, set by `isotp_request` 0x141E58 from the connection layer) or the frame is dropped; for normal addressing both address bytes +0x12/+0x13 := the rx map's `addr` byte, and the SF limit is **7** | 0x14231C-0x1423B8 | |
| 9 | **SF (`0N`)**: needs 1 ≤ N ≤ 7 and DLC ≥ N+1, else dropped silently; copies N bytes to the channel buffer (0x142000), sets +0xA/+0xC = N and signals `isotp_event(ch, 1)` 0x141F74 (flags bit 0 = "message received") | 0x1423D8-0x14245C | |
| 10 | **FF / CF / FC** are implemented (0x142460 FF: 12-bit length, overflow → FC `32`; `isotp_send_fc` 0x142CA8 builds `30`/`31`/`32` BS STmin + **0x00 padding** to 8; CF sequence check at 0x1425F4); a FC for the tx channel is matched through the rx channel's descriptor +1 (0x1421CC) | | not exercised: the functional channel refuses FF |
| 11 | **Connection layer** (10 ms, `kwp_conn_cyclic` 0x13E650 → the channel search 0x13CC5C → 0x13F688 …): `isotp_flags_take(ch, mask)` 0x141F0C sees bit 0; the connection table **0x2C324** (4 × 0x24, `[0x7FD954]`) pairs ISO-TP channel 4 (entry 3) and channel 3 (entry 0) with the address list **0x2C310** = {0x10 → type 0, **0x33 → type 4**} and with a gate at +0x20 | 0x13F3B4-0x13F42C | |
| 12 | `kwp_addr_accept` **0x13CDCC**(target = +0x13, source = +0x12): the target must be in the connection's address list → **0x803D6A = type (4), 0x803D6B = 0x33, 0x803D6C = 0x33**. Then the gate: channel 4 → **0x386E4** (`lbz 0x5CEE6E`, > 0 → accept; the byte is **1** in this dataset, one reader), channel 3 (0x7E0) → **0x2C29C `li r3,0`** | emulated: 0x803D6B := 0x33 at 0x13CE60, 0x803D6A := 4 at 0x13CE70 | |
| 13 | **`kwp_conn_open` 0x13F18C**: copies 0x803D6B → **0x7F804B** (`stb r9,0x1F(r31)`, r31 = 0x7F802C, at **0x13F1E4**) and 0x803D6A → 0x7F804C (0x13F1EC); `[0x803DE0]` := entry +8 (5), `[0x803DE1]` := entry +4 (4) | emulated: `write 0x7f804b = 0x33 pc 0x13f1e4` | the writer §8 item 4 was missing (§11.4) |
| 14 | the next `kwp_conn_cyclic` tick sees a new connection (0x13E71C) → `kwp_service_h2_walk` 0x13ECB0 → SID 0x10's h2 **0x370F8** → **`kwp_session_set(6)`** (0x37154) | emulated: 0x803D3E := 0, then 6, pc 0x13CEE8 | |
| 15 | the request descriptor **0x803D60** `{ptr buf, u16 len, +6 new, …, +0xA type, +0xB target, +0xC source}` goes to `kwp_service_dispatch` 0x13E98C from 0x13D890 (r4 = 0x803D60) | emulated: `pc 0x13e98c r4=0x803d60` | |

The request buffer is **0x803C0C** (`[state+0]` of channels 3/4); the handler
writes its answer into the same buffer (§1.4, §2).

### 11.2 The answer path: dispatcher → MB13 / 0x7E8

| # | Step | Where |
|---|---|---|
| 1 | status (io +0xA) selects the action through the jump table at **0x13D8C4** (0x13D8A8 `slwi`, `bctr`): 1 → **`kwp_send_positive` 0x13D25C** (writes SID+0x40 in front of the body at 0x13D2B8 and calls the connection's send vector `[[0x803D70]]`); 2 → 0x13D454 (negative `7F sid nrc`); **3 → no answer, only re-arm reception** (0x13D95C) | 0x13D894-0x13D9E4 |
| 2 | the send vector ends in **`isotp_request` 0x141E58**(ch 0, buf, len) and **`isotp_transmit` 0x1429BC**: len ≤ 7 → **single frame `0N` + data + 0x00 padding to 8 bytes** (0x142A74-0x142AB8, `stbu r27` with r27 = 0), DLC always 8; len > 7 → FF `1L LL` + 6 bytes and wait for FC (0x142B28-0x142C1C) | |
| 3 | `isotp_can_send` 0x142940 → **`can_tx_frame` 0x13C4CC**(handle 0x6A = rx map entry 0, data, id, 8): the buffer at 0x7079D0 (module C **MB13**) gets CODE 1000, id word 0x7E8 << 5 = **0xFD00**, the 8 bytes, DLC 8, then CODE **1100** (transmit, `ori 0xC0` at 0x13C8E8) | emulated: writes at 0x13C890-0x13C8EC |
| 4 | the transmit-complete flag (C IFLAG bit 13, owner-map kind 2, handle 0x6A) runs the tx confirmation; the functional rx channel 4 is re-armed (`isotp_request(4, 0x803C0C, …)`) | emulated |

**What is suppressed on the functional channel** (dispatcher tail
0x13EABC-0x13EC80; config struct 0x2BA50 bytes +0x1F = 5, +0x20 = 2;
`[0x803DE1]` = 4 for this connection): status is forced to **3 (silence)**
when no table entry passed the session gate or the SID is unknown
(0x13EACC-0x13EB14), and for a negative answer with NRC **0x10, 0x11 or
0x12** on a type-4 or type-1 connection (0x13EBC4-0x13EC18). A handler that
returns status 3 itself (mode 01 with an unsupported PID, §2) is silent too.
So on 0x7DF, `3E`, `10 xx`, `21 xx`, `1A xx` and `01 20` on this image draw
**no frame at all** — VERIFIED-DYNAMIC (emulated). That is ISO 15765-4
behaviour and it matters on the bench: silence means "not supported", not
"not connected".

### 11.3 Timing and lifetime (VERIFIED-DYNAMIC, emulated)

* The frame is copied at interrupt level; the connection opens and the session
  is set on the first 10 ms tick, the answer is loaded into MB13 on the
  **second** 10 ms tick (10-20 ms after the frame).
* The connection **closes 5.00 s after the last answer** (`[0x803D70]` := 0 at
  0x13E6E8; `kwp_session_current` back to 0). A request inside the 5 s keeps
  it; the first request after it opens a **new connection** and with it a new
  h2 walk — which is what rebuilds the support bitmaps (§10). The constant
  0x010BB2AA ticks × 285 ns = 5.000 s is the `lis/ori` pair at
  0x2C278-0x2C27C (stored to 0x803D58); HYPOTHESIS that this is the value the
  timeout reads (the 5.00 s is measured, the link is not traced).
* `[0x803DE0]` = 5 for this connection, 9 for the TP2.0 ones (entries 1/2 of
  0x2C324 +8); step 5 of §11.1 uses it.

### 11.4 §8 item 4: who writes 0x7F804B

0x7F804B is byte +0x1F of a connection-parameter block at **0x7F802C**
(+0x1E = 0x7F804A, the ECU's own address 0x10, written at init by
`kwp_addr_config_set` 0x13D0F4 from the flash record **0x14493C**
`10 33 7D 00 …`; +0x20 = 0x7F804C the channel type; +0x4A the connection
index). Its writer is **0x13F1E4** (`stb r9,0x1F(r31)` after `lis r31,0x80 ;
addi r31,r31,-0x7FD4`) — a base-plus-displacement store, which is why
`tools/find_abs_refs.py` (lis + D-form on the same register) did not list it.
The two candidates §8 named are not it: the `stswi r7,r11,8` at 0x1443A0
writes **0x7FDA04-0x7FDA0B** (r11 = `lis 0x80 ; addi -0x25FC`), and the `stw`
at 0x15BB8/0x15C0C (`-0x7FA8(r13)` = 0x7F8048) sit in the boot block's own
time-base seed loop (0x15BEC-0x15C08) and were not executed on this path
(HYPOTHESIS that they run only under the boot software).

### 11.5 Reproduction

```bash
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x2000C4 --addr 0x4040C4 --len 0x140
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0xB5534 --addr 0xB5534 --len 0x44
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x1420A0 --addr 0x1420A0 --len 0x710
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x1429BC --addr 0x1429BC --len 0x2EC
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x13F18C --addr 0x13F18C --len 0x80
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x13CDCC --addr 0x13CDCC --len 0x100
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x13EABC --addr 0x13EABC --len 0x1DC
./.venv/bin/python3 tools/sda_xref.py data/passat_azx_ori.bin --code 0x13C288 0x1420A0 0x1429BC 0x13CDCC
./.venv/bin/python3 -c "d=open('data/passat_azx_ori.bin','rb').read();[print(hex(0x2C054+20*i),d[0x2C054+20*i:0x2C068+20*i].hex()) for i in range(18)]"
./.venv/bin/python3 -m unittest tests.test_ecu_sim_obd -v
```
