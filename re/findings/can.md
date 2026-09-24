# CAN receive path and the TouCAN modules (brief B2, issue #12)

2026-09-15, agent B2, branch `agent/B2`. All addresses are CPU addresses; in
external flash file offset == CPU address. Register-map facts about the
MPC561 TouCAN come from `re/findings/mpc5xx_registers.md` §6 (A2).

Reproduce anything below with:

```bash
./.venv/bin/python ghidra_scripts/decompile.py --project-dir <proj> 0x135750
./.venv/bin/python ghidra_scripts/decompile.py --project-dir <proj> --asm 0x12E570 --count 30
python3 tools/find_branch_refs.py data/passat_azx_ori.bin 0x4379C8 0x135750
python3 tools/find_abs_refs.py  data/passat_azx_ori.bin --range 0x803EE0 0x804100
```

---

## 1. Summary of the mechanism (VERIFIED-STATIC)

The ECU has **two independent receive paths**:

1. **Polled** — the 21 application frames in `tbl_can_rx` (0x2BC90). Each slot
   owns one dedicated TouCAN message buffer, filtered on an exact 11-bit id.
   `can_init_mb` (0x135750) arms the buffer once at start-up; a per-frame
   supervision task calls `can_rx_poll` (0x4379C8), which checks the buffer's
   IFLAG bit, copies the 8 data bytes into a 12-byte RAM shadow and clears the
   flag. **No interrupt is involved.**
2. **Interrupt-driven** — the diagnostic / TP2.0 / CCP buffers described by
   `can_cfg_struct` (0x2BF50). Only these set bits in IMASK, and only these
   appear in `can_mb_owner_map` (0x804088), which the ISR at 0x404000 uses to
   route a flag to a TX object (kind 1), an RX object (kind 2) or a masked
   range object (kind 4).

The flex-fuel frame belongs in path 1.

## 2. Table formats (VERIFIED-STATIC, corrects docs/02 §7)

`tbl_can_rx` at **0x2BC90**, 22 x 16 bytes (slot 0 + 21 slots):

| Offset | Field | Meaning |
|---|---|---|
| +0x0 | u32 | slot index, equal to the row number |
| +0x4 | u8 | always 0x01 |
| +0x5 | u8 | **TouCAN module index** into `tbl_toucan_bases` (0=A, 1=B, 2=C) |
| +0x6 | u8 | **message-buffer number** 0..15 inside that module |
| +0x7 | u8 | DLC. **Read by nothing in the driver** — documentation only |
| +0x8 | u32 | **byte-order selector**: 2 = swap both data words, anything else (always 4 here) = copy as received. `med9_setup.py` currently calls this field `dlc`; that name is wrong |
| +0xC | u32 | 11-bit CAN id, 0x7FF = unused slot |

`tbl_can_tx` at **0x2BDF0** is 21 x 16 bytes (not 17): `u32 index; u32 can_id;
u8 0x01; u8 module; u8 buffer; u8 flags; u8 dlc; u8 pad[3]`. Rows 17..19
repeat id 0x7C6 and row 20 carries id 0x7FC on module 0.

The pointer to the configuration structure is at **0x2BC54** (value
0x0002BF50), not 0x2BC50 — 0x2BC50 holds 0x00000100. `can_tx_config_apply`
(0x1377D8) reads it as `ptr_can_config[idx*2 + 1]`.

`can_cfg_struct` at **0x2BF50** holds, from 0x2BF64, 12-byte records
`{u32 can_id; u8 0x01; u8 module; u8 buffer; u8 flags; u8 dlc; u8 a,b,c}`:

| id | module | buffer |
|---|---|---|
| 0x500 | 0 (A) | 14 |
| 0x200 | 1 (B) | 14 |
| 0x740 | 1 (B) | 15 |
| 0x420 | 2 (C) | 14 |
| 0x7C0 | 2 (C) | 15 |
| 0x7E8 | 2 (C) | 13 |
| 0x700 | 1 (B) | 9 (twice) |
| 0x010 | 1 (B) | 8 |
| 0x011 | 1 (B) | 10 |

So every message buffer of every module is accounted for:

| Module | RX (polled) | RX (interrupt) | TX | free |
|---|---|---|---|---|
| A 0x707000 | MB0 (id 0x7FE) | MB14 (0x500) | MB1 (id 0x7FD) | MB2..13, 15 |
| B 0x707400 | MB0..MB7 | MB8, 9, 10, 14, 15 | **MB11, 12, 13** | none |
| C 0x707800 | MB0..MB12 (4 of them spare) | MB13, 14, 15 | none | none |

## 3. Which module is which bus

- **Module B (0x707400) is the powertrain CAN.** Every one of the 20 transmit
  objects is registered on module B, buffers 0x0B-0x0D
  (`can_tx_register_all` 0x1345A8: `can_obj_register(.., 20 objects, module 1,
  3 buffers, first buffer 0x0B, .., tbl_can_tx)`). Those are the Motor_x
  frames plus 0x7C4-0x7C7 (CCP) — VERIFIED-STATIC.
- **Module C (0x707800) never transmits.** It only receives.
  - *Correction 2026-09-22 (brief F6, `re/findings/obd.md` §1.1):* true of `tbl_can_tx` (0x2BDF0), the only source this section checked, but **not** of `can_cfg_struct` (0x2BF50): its record [5] registers id **0x7E8 on module C buffer 13 with flags = 0x01**, the OBD-II response object. Module C transmits exactly one identifier, 0x7E8; read the sentence as "module C carries no `tbl_can_tx` object".
  - *Note 2026-09-24 (brief H3, `re/findings/obd.md` §11.2) — the frame format on 0x7E8.* MB13 is loaded by `can_tx_frame` 0x13C4CC (handle 0x6A): CODE 1000, id word 0xFD00 (0x7E8 << 5), eight data bytes, **DLC always 8**, then CODE 1100. The data are ISO 15765-2: a single frame `0N` + N bytes + **0x00 padding** (`isotp_transmit` 0x1429BC, 0x142A74-0x142AB8), or a first frame `1L LL` + 6 bytes followed by consecutive frames `2n` + 7 bytes after the tester's flow control on 0x7E0, the last one padded with 0x00. The request side is module C **MB15**: range objects 0xD8 (0x7DF → 0x0B5534) and 0xD9 (0x7E0 → 0x1420A0) of the table 0x2C054, DLC 8 required, single frames only on 0x7DF. VERIFIED-STATIC, VERIFIED-DYNAMIC (emulated, `tests/test_ecu_sim_obd.py`).
- **All three modules use identical bit timing**: `can_module_cfg_a/b/c`
  (0x2C1D8 / 0x2C1F8 / 0x2C218) all end in `07 08 04 03 02`, which
  `can_module_configure` turns into PRESDIV 6, PROPSEG 7, PSEG1 3, PSEG2 2,
  RJW 1 = 16 Tq per bit at f_sys/7. With `sys_clock_hz` = 56 000 000
  (0x144878) that is **500 kbit/s, sample point 81.25 %** — VERIFIED-STATIC.
- **Module A** carries only ids 0x7FC / 0x7FD / 0x7FE / 0x500 and all-ones
  masks. HYPOTHESIS: a loopback or self-test channel, or an unpopulated third
  interface. Nothing in the flex-fuel path needs it.

**HYPOTHESIS (important, needs one bench check):** modules B and C are wired
to the *same* physical Antrieb-CAN. Supporting evidence: identical 500 kbit/s
timing; module C is receive-only, which is exactly how you add a second
controller to one wire without arbitration trouble; id 0x38A is transmitted on
B and received on C; and both groups contain ordinary powertrain ids
(0x1A0/0x4A0/0x440 on B, 0x050 Airbag_1 and 0x0C2 on C) that an engine ECU
cannot do without. Against it: nothing found so far, but no register or pin
evidence either way was located in the dump. **This matters** because all four
spare receive slots are on module C — see §6.

## 4. RX slot -> module / buffer / RAM map (VERIFIED-STATIC)

RAM layout: `can_rx_shadow` at **0x803EE4**, 22 x 12 bytes,
`{u32 can_id; u8 data[8]}`. `can_init_mb` writes the id, `can_rx_poll` writes
the 8 data bytes. **Data of slot i is at 0x803EE8 + 12*i.**

Message buffer of slot i is at `tbl_toucan_bases[module] + 0x80 + buffer*0x10`
(the table holds CANMCR = module base + 0x80, so this equals
`module_base + 0x100 + buffer*0x10`, MPC561RM Table 16-10).

"state" is the u16 supervision word, r13-relative (r13 = 0x7FFFF0); bit0 =
object enabled, bit1 = new data accepted this cycle. The u16 two bytes below
it is the status word produced by `can_rx_status` (0x42A0A0): bit2 = timeout
(consumer substitutes defaults), bit0/5/6 = fresh data.

| slot | id | mod | MB | MB address | RAM id | **RAM data** | state u16 | `can_rx_poll` call site | first consumer | `can_init_mb` call |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 0x7FE | A | 0 | 0x707100 | 0x803EE4 | 0x803EE8 | - | - | - | - |
| 1 | **0x1A0** | **B** | **0** | **0x707500** | 0x803EF0 | **0x803EF4** | 0x802B08 | 0x428140 | `can_rx_eval_bremse` 0x4280D4 | 0x12E720 |
| 2 | 0x5A0 | B | 1 | 0x707510 | 0x803EFC | 0x803F00 | 0x802B0C | 0x4282BC | 0x4280D4 | 0x12E728 |
| 3 | 0x4A0 | B | 2 | 0x707520 | 0x803F08 | 0x803F0C | 0x802B10 | 0x428428 | 0x4280D4 | 0x12E730 |
| 4 | 0x440 | B | 3 | 0x707530 | 0x803F14 | 0x803F18 | 0x802B2E | 0x429038 | 0x428FCC | 0x12E7EC |
| 5 | 0x540 | B | 4 | 0x707540 | 0x803F20 | 0x803F24 | 0x802B32 | 0x4291C8 | 0x428FCC | 0x12E7F4 |
| 6 | 0x320 | B | 5 | 0x707550 | 0x803F2C | 0x803F30 | 0x802B62 | 0x44E50C | 0x44E4BC | 0x12E8B8 |
| 7 | 0x442 | B | 6 | 0x707560 | 0x803F38 | 0x803F3C | 0x802B36 | 0x429400 | 0x428FCC | 0x12E7FC |
| 8 | 0x1AC | B | 7 | 0x707570 | 0x803F44 | 0x803F48 | 0x802B18 | 0x44D39C | 0x44D334 | 0x12E74C |
| 9 | 0x0C2 | C | 0 | 0x707900 | 0x803F50 | 0x803F54 | 0x802B72 | 0x42A2AC | 0x42A258 | 0x12E8C0 |
| 10 | 0x050 | C | 1 | 0x707910 | 0x803F5C | 0x803F60 | 0x802AFE | 0x44D0DC | 0x44D088 | 0x12E70C |
| 11 | 0x51A | C | 2 | 0x707920 | 0x803F68 | 0x803F6C | 0x802B78 | 0x0D2840 | 0x0D27C4 | 0x12E8C8 |
| 12 | 0x5E0 | C | 3 | 0x707930 | 0x803F74 | 0x803F78 | 0x802B2A | 0x44D548 | 0x44D4F4 | 0x12E754 |
| 13 | 0x390 | C | 4 | 0x707940 | 0x803F80 | 0x803F84 | 0x802B5E | 0x121870 | 0x121850 | 0x12E894 |
| 14 | 0x38A | C | 5 | 0x707950 | 0x803F8C | 0x803F90 | 0x802B56 | 0x44DF68 | 0x44DEB8 | 0x12E84C |
| **15** | **0x7FF** | **C** | **6** | **0x707960** | 0x803F98 | **0x803F9C** | - | **none** | **none** | **none** |
| **16** | **0x7FF** | **C** | **7** | **0x707970** | 0x803FA4 | **0x803FA8** | - | **none** | **none** | **none** |
| 17 | 0x2A0 | C | 8 | 0x707980 | 0x803FB0 | 0x803FB4 | 0x802B14 | 0x428590 | 0x4280D4 | 0x12E738 |
| 18 | 0x368 | C | 9 | 0x707990 | 0x803FBC | 0x803FC0 | 0x802B50 | 0x44E2BC | 0x44DEB8 | 0x12E83C |
| **19** | **0x7FF** | **C** | **10** | **0x7079A0** | 0x803FC8 | **0x803FCC** | - | **none** | **none** | **none** |
| **20** | **0x7FF** | **C** | **11** | **0x7079B0** | 0x803FD4 | **0x803FD8** | - | **none** | **none** | **none** |
| 21 | 0x5C0 | C | 12 | 0x7079C0 | 0x803FE0 | 0x803FE4 | 0x802B4C | 0x44DD28 | 0x44DCB8 | 0x12E828 |

Every used slot has **exactly one** `can_rx_poll` call and **exactly one**
`can_init_mb` call; the four 0x7FF slots have neither (evidence:
`python3 tools/find_branch_refs.py data/passat_azx_ori.bin 0x4379C8 0x135750`
plus the `li r3,<slot>` immediately before each site).

### Bremse_1 (0x1A0), the acceptance case

- Received into TouCAN **B**, message buffer **0**, control word 0x707500,
  id word 0x707502, data 0x707506..0x70750D.
- Copied to **RAM 0x803EF4..0x803EFB** (`can_rx_buf_1A0`) by
  `can_rx_poll(1)` at 0x428140.
- First consumer: `can_rx_eval_bremse` at **0x4280D4**. It requires
  `can_rx_poll` to return DLC 8 and bit 0x10 of `can_rx_inhibit_flags`
  (0x7FD0CC) to be clear, then sets bit1 of the state word at 0x802B08 and
  takes `data[7] & 0x0F` into 0x7FD091. The bit decoding of `data[0]`
  continues in `FUN_000A7D60` (called from 0x4280D4 via the status word),
  which splits data[0] bits 0..3 into four flags at r13-0x16C0, -0x16B8,
  -0x16C5 and so on.
- Naming (Bremse_1, ABS/ESP status) is COMMUNITY from the VAG id list; the
  slot -> buffer -> RAM -> consumer chain above is VERIFIED-STATIC.

## 5. Driver entry points (VERIFIED-STATIC)

| Address | Name | What it does |
|---|---|---|
| 0x135750 | `can_init_mb(slot)` | `ctrl &= 0xFF0F` (CODE = NOT ACTIVE), `id = can_id << 5`, `ctrl \|= 0x40` (CODE = 0100 EMPTY). Also copies the id into `can_rx_shadow[slot]` |
| 0x12E570 | `can_rx_arm_all()` | calls `can_init_mb` for the 17 used slots; reached through the init pointer table at 0x0B1B18 |
| 0x4379C8 | `can_rx_poll(slot)` | returns the received **DLC** (0..8) when the buffer's IFLAG bit was set, else **0x40**. Waits out BUSY (`ctrl & 0x10`), copies `MB+6` and `MB+10` to `can_rx_shadow[slot].data`, then clears the IFLAG bit |
| 0x437B6C | `can_rx_ack(slot)` | clears the IFLAG bit only. **No caller** — unused API |
| 0x136624/0x136688/0x1366EC | `can_poll_iflag_a/b/c` | if IFLAG != 0, call the ISR body at 0x404000 with (CANMCR, module) |
| 0x404000 | ISR body (`int_flash_entry` in symbols.csv) | masks IFLAG with `can_sw_imask`, walks the set bits, looks each up in `can_mb_owner_map` and dispatches |
| 0x13B7E0 | `can_module_start(module)` | freeze, write CTRL0/1, PRESDIV/CTRL2, clear all 16 buffers, write RXGMSK/RX14MSK/RX15MSK, CANICR, IMASK, leave freeze |
| 0x13B4D0 | `can_module_configure(module, cfg)` | builds those register values from `can_module_cfg_*`; also zeroes both software IMASK words |
| 0x13B648 | `can_id_to_mask(id, ide)` | standard id -> `id << 21` |

`can_rx_poll` returning the DLC (not a status code) is proven by the two
4-byte frames: at 0x44D0DC (slot 10, id 0x050) and 0x44DF68 (slot 14, id
0x38A) the test is `addi r11,r11,-4; cmplwi r11,4; bgt skip` (i.e. 4 <= dlc <=
8), while every 8-byte slot tests `== 8`.

## 6. Acceptance masks (VERIFIED-STATIC)

`can_module_start` writes RXGMSK to CANMCR+0x10, RX14MSK to +0x14 and RX15MSK
to +0x18 from the shadows that `can_module_configure` computed with
`can_id_to_mask(id, 1) = id << 21`:

| module | RXGMSK (MB0..MB13) | RX14MSK (MB14) | RX15MSK (MB15) |
|---|---|---|---|
| A | 0x7FF -> 0xFFE00000 | 0x7FF | 0x7FF |
| B | 0x7FF -> 0xFFE00000 | 0x7EB | 0x77C |
| C | 0x7FF -> 0xFFE00000 | 0x6AD | 0x7C0 |

**RXGMSK is all-ones on all three modules: message buffers 0..13 do exact
11-bit id matching.** That is the whole reason the slot mechanism works, and
it means **adding a receive id needs no mask change at all**, as long as the
new id goes on a buffer 0..13.

The only wide filter is module C MB15: mask 0x7C0 with base id 0x7C0 accepts
**0x7C0..0x7FF**. Module C MB14 (base 0x420, mask 0x6AD) and module B MB14/15
(0x200/0x7EB and 0x740/0x77C) are narrow. Checked against the flex-fuel id:
`0x0EC & 0x6AD = 0x0AC != 0x420`, `0x0EC & 0x7C0 = 0x0C0 != 0x7C0`,
`0x0EC & 0x7EB = 0x0E8 != 0x200`, `0x0EC & 0x77C = 0x06C != 0x740`
— **0x0EC collides with no existing buffer on either module.**

## 7. Repurposing a 0x7FF slot — the procedure

Target: slot **15** (id 0x7FF, TouCAN **C**, message buffer **6**), the first
spare. Slots 16, 19 and 20 are identical in every respect except their buffer
number and RAM address.

1. **Flash edit (2 bytes).** Slot 15 starts at 0x2BD80; its id word is at
   **0x2BD8C** = `00 00 07 FF`. Write `00 00 00 EC` — only 0x2BD8E and
   0x2BD8F change. Nothing else in the row needs touching: byte 0x2BD87 (the
   DLC 0x08) is never read by the driver, and word 0x2BD88 (0x00000004, the
   byte-order selector) must stay != 2 so the payload is copied as received.
   If a different buffer is wanted: slot 16 id word 0x2BD9C, slot 19
   0x2BDCC, slot 20 0x2BDDC.
2. **Checksum.** 0x2BD8C falls in exactly one Bosch block, descriptor
   **0x0A0010** covering 0x020000-0x02FFFF. `python3 tools/checksum.py fix`
   rewrites it; `verify` must then report ALL OK (65 blocks).
3. **No mask register changes.** RXGMSK on module C is 0xFFE00000, so MB6
   matches only the exact id written in step 1 (§6). RX14MSK/RX15MSK are not
   involved because MB6 is not MB14/MB15.
4. **Arm the buffer.** Nothing arms slot 15 today, so its CODE stays 0000
   (NOT ACTIVE) and it will never receive. Call `can_init_mb(15)`
   (`li r3,15; bl 0x135750`) exactly once during start-up. The cheapest hooks:
   append a call inside `can_rx_arm_all` (0x12E570, its last operation is a
   tail `b 0x12E824`), or call it from the flex-fuel module's own init. It is
   idempotent, so calling it again later (e.g. after a bus-off recovery) is
   harmless.
5. **Poll it.** Call `can_rx_poll(15)` (`li r3,15; bl 0x4379C8`) from a
   periodic task (10-100 ms is plenty for a 10 Hz frame). Return value 8 means
   a new frame arrived and the payload is fresh; 0x40 means nothing new since
   the last call; anything else means the buffer was still BUSY.
6. **Read the data at RAM 0x803F9C..0x803FA3** (`can_rx_buf_spare0`), byte 0
   first, laid out exactly as on the wire. The configured id is echoed at
   0x803F98. For slot 16 the data is at 0x803FA8, slot 19 at 0x803FCC, slot 20
   at 0x803FD8.
7. **Timeouts are ours to build.** The stock supervision (`can_rx_supervise`
   0x429D44 / `can_rx_status` 0x42A0A0) is called per slot from the stock
   tasks with calibration constants; a new slot has no such calibration. The
   flex-fuel module should keep its own "frames since last valid" counter and
   fall back per `docs/05_flexfuel_design.md` §3.2.
8. **No interrupt risk.** IMASK is only ever set from `can_sw_imask`
   (0x7FB9A4), and only object registration writes that (all writers are in
   0x136C..0x13C1, driven by `tbl_can_tx` and `can_cfg_struct`, never by
   `tbl_can_rx`). Module C's IMASK therefore has only bits 13, 14, 15 set, so
   arming MB6 raises no interrupt and the ISR at 0x404000 will not see it and
   will not steal the flag.
9. **No TX collision.** All transmit objects live on module B buffers
   0x0B-0x0D (plus module A buffer 1). Module C transmits nothing, so a
   receive buffer on module C can never collide with a transmit buffer
   (`can_tx_register_all` 0x1345A8, §2 table).

**Open risk, one bench check:** the spare slots are on module **C**. If module
C is not on the same wire as module B (§3), the flex-fuel node has to be wired
to whatever bus C is on, or a module-B buffer has to be freed first (module B
is full; the cheapest victim would be one of the three TX buffers, which is a
much bigger change). Suggested check without any flashing: with the engine
running, read a measuring value fed by a module-C frame (e.g. id 0x050
Airbag_1, slot 10, or 0x0C2, slot 9) and one fed by a module-B frame (0x1A0,
slot 1) over KWP; if both are live while only the Antrieb-CAN pair is
connected, B and C share that bus.

## 8. Corrections to earlier documents

- `docs/02_memory_map.md` §7: the CAN configuration pointer is at **0x2BC54**,
  not 0x2BC50 (0x2BC50 holds 0x00000100).
- `docs/02_memory_map.md` §7: the RX entry's third word (0x00000004) is a
  **byte-order selector** (2 = swap), not a payload length; the DLC is byte 3
  of the `0x01mmnndd` word, and the driver never reads it.
- `docs/02_memory_map.md` §7: the transmit table has **21** rows, not 17
  (0x2BDF0..0x2BF4F).
- `ghidra_scripts/med9_setup.py`: `med9_can_rx_entry.dlc` should be renamed
  (it is the byte-order selector) and `tbl_can_tx` should be 21 entries.
- The `mm` byte of `0x01mmnndd` is the module index into `tbl_toucan_bases`
  and `nn` is the message-buffer number — settled by `can_init_mb`.
