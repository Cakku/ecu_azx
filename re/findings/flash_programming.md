# The ECU's own flash-programming route, and what it can erase and program

Agent **E6**, brief `docs/agent_briefs/E6_kwp_programming_route.md`, blocker of
issues **#26 #27 #28 #32**. Date **2026-09-17**. Dump
`data/passat_azx_ori.bin`, SHA-256
`b15590d3f1874ace3125c5d047c09a686db9b8bb498187663539ebab205609b3`
(`./.venv/bin/python3 tools/checksum.py verify -q` -> `ALL OK (65 blocks)`
before and after this work).

All addresses are **CPU addresses**; `file` prefixes a file offset. In the
external flash 0x000000-0x1FFFFF the two are equal, so most addresses here
need no conversion. Application SDA bases: `r13 = 0x7FFFF0`, `r2 = 0x5C9FF0`;
inside the relocated flash driver `r2 = 0xD4CDF0` (section 3.1).
Tags per `docs/README.md`.

Reproduce every disassembly with

```bash
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin \
        --file-off 0x<addr> --addr 0x<addr> --len 0x<n>       # ext flash
./.venv/bin/python3 tools/flash_segments.py data/passat_azx_ori.bin --all
```

---

## 0. The answer, in one table

**VERIFIED-STATIC.** The firmware's own OBD programming service accepts
exactly these address ranges for erase (`31 C4`) and download (`34`), decided
by one function, `kwp_download_range_allowed` (0x0889C8), which requires an
**exact** `(start, end)` match:

| # | start | end | size | device | what it is |
|---|---|---|---|---|---|
| 1 | 0x020000 | 0x07FFFF | 384 KB | CS0 external | application code, part 1 |
| 2 | 0x0A0000 | 0x1BFFFF | 1152 KB | CS0 external | application code, part 2 |
| 3 | **0x404000** | **0x47FFFF** | **496 KB** | **on-chip UC3F** | **the whole on-chip flash except small block 0** |
| 4 | 0x080000 | 0x09FFFF | 128 KB | — | **alias**: rewritten to 0x1C0000-0x1DFFFF by the 0x34 handler |
| 5 | 0x1C0000 | 0x1DFFFF | 128 KB | CS0 external | calibration (variant-gated, section 2.4) |
| 6 | 0x1E0000 | 0x1FFFFF | 128 KB | CS0 external | calibration upper half (variant-gated) |
| 7 | 0x1C0000 | 0x1FFFFF | 256 KB | CS0 external | whole calibration (variant-gated) |

Anything else is refused with NRC **0x42** (`can'tDownloadToSpecifiedAddress`).

> **So yes: the ECU's own KWP programming service can erase and program the
> on-chip flash 0x404000-0x47FFFF over OBD.** It is not an afterthought — the
> on-chip array is device 1 of a three-entry flash-device table, with its own
> erase-block map and a textbook UC3F program/erase state machine in the
> RAM-resident driver (sections 3 and 4). Consequence for
> `patches/ff_fuel`: see section 7.

Two ranges are **never** downloadable and this is by construction:

* **0x000000-0x01FFFF** — the exception tables, the boot module and the source
  image of the RAM-resident bootstrap loader.
* **0x080000-0x09FFFF** — the resident flash-programming module itself: the
  code directory (0x080100), the flash driver (0x081A00-0x085887), the
  programming-mode KWP stack (0x086000-0x09BFFF) and `app_entry_crt0`
  (0x09E3B4). This is exactly the 128 KB hole between whitelist rows 1 and 2,
  and it is also why a tester request for 0x080000-0x09FFFF is silently
  rewritten to the calibration block.
* **0x400000-0x403FFF** — UC3F small block 0 (shadow row, reset configuration
  word, live ETR vector table). It is an erasable block of device 1 and the
  driver knows its block-select bit, but no whitelist row starts there, and
  the erase loop only ever starts at the range start (section 4.3), so the
  OBD route cannot reach it. This confirms from the firmware side what
  `docs/02` §2 and `mpc5xx_registers.md` §8 inferred from the silicon.

---

## 1. The two KWP stacks

The ECU has **two** complete KWP2000 service stacks in this image.

| | application stack | programming stack |
|---|---|---|
| dispatch table | 0x02B820, **28** entries | **0x088174, 13** entries |
| config struct | 0x02BA50 (`+0x1C` = 0x1C = 28) | **0x088280** (`+0x1C` = 0x0D = 13) |
| registered by | 0x13C9F4 | **`prog_kwp_init` 0x08C244** (`lis r3,9; addi r3,r3,-0x7D80` = 0x088280, `bl 0x08E190`) |
| gate per entry | diagnostic session bitmask | **0xFFFFFFFF — no session gate at all** |
| SID 0x34 RequestDownload | **absent** | **present** |

`re/findings/kwp.md` §1 documents the application table. The programming
table (**VERIFIED-STATIC**, `tools/flash_segments.py --kwp`) is:

| entry | SID | service | handler |
|---|---|---|---|
| 0x088174 | 0x3E | TesterPresent | 0x0899C8 |
| 0x088188 | 0x1A | ReadEcuIdentification | 0x086984 |
| 0x08819C | 0x81 | StartCommunication | 0x088470 |
| 0x0881B0 | 0x10 | StartDiagnosticSession | 0x0884E8 |
| 0x0881C4 | 0x27 | **SecurityAccess** | 0x087C44 |
| 0x0881D8 | 0x83 | AccessTimingParameters | 0x0863E0 (h2 0x0866B4) |
| 0x0881EC | **0x31** | **StartRoutineByLocalId** | **0x088D40** |
| 0x088200 | 0x33 | RequestRoutineResultsByLocalId | 0x087AFC |
| 0x088214 | **0x34** | **RequestDownload** | **0x086A28** |
| 0x088228 | **0x36** | **TransferData** | **0x086CDC** |
| 0x08823C | 0x37 | RequestTransferExit | 0x08731C |
| 0x088250 | 0x20 | StopDiagnosticSession | 0x08836C |
| 0x088264 | 0x82 | StopCommunication | 0x0882A4 |

This settles the open item in the brief ("0x34 RequestDownload is not in
docs/02 §7's SID list — find how data gets *into* the ECU"). **Data gets in
through SID 0x34 of the second stack, not through 0x36 of the first.** The
first stack's 0x35/0x36/0x37 trio is upload-only, exactly as `kwp.md` §5
describes it.

The word **0x5FBD5DBD** sits at 0x088170, immediately in front of the table:
the programming stack's SecurityAccess uses the *same* level-1 Galois-LFSR
algorithm as the application stack (`kwp.md` §3). Its granted-level byte is
**`prog_security_level` at RAM 0x805A10**, read by the one-line getter
`prog_security_check` (0x08C778, `lis r3,0x80; lbz r3,-0x25F0(r3)`), and
**every** programming service tests it for the value **2**: 0x086400,
0x086A50 (SID 0x34), 0x087CA4, 0x087DFC, 0x088698, 0x088B40, 0x088D78
(SID 0x31), 0x08C208, 0x08C8D4, 0x08E594, 0x092900, 0x093978. Failure gives
NRC **0x33** securityAccessDenied.

---

## 2. The flow, from `10 85` to a programmed block

### 2.1 `10 85` never switches a session — it reboots the ECU

`kwp_start_session_core` (0x036BC0) branches on the sub-function
(`kwp.md` §2). 0x85 goes to **0x036C60**, and that path never calls
`kwp_session_set`. In order (**VERIFIED-STATIC**, 0x036C60-0x0370B8):

1. `0x7FEB65 == 0` (the programming-mode flag; non-zero -> NRC 0x22).
2. `kwp_security_state` must be **2** = level 1 granted, else NRC 0x33.
   (`kwp.md` §2 already said "needs security level 1"; this is the code.)
3. Six further preconditions, each NRC 0x22, plus
   `kwp_sec_delay_timer != 0` -> NRC 0x37: the halfword at 0x7FEE74, the byte
   at 0x803D6A, the byte at 0x7FEB57, bit 0x40 of the halfword at 0x7FB054.
4. A re-entrancy latch at 0x7FB7C0; the time base is captured to
   0x7FB788/0x7FB78C (`read_time_base` 0x478460).
5. **A 0x16-byte "programming attempt" record is staged into EEP_CONF block
   10** through five `nvm_block_request` calls (`bl 0x06131C`, r3 = 10) —
   see section 5.2.
6. `kwp_io_struct+0xA = 8` -> **response pending**. The tester is kept waiting
   while the handler is re-entered from the KWP task until, at 0x036F98:
   * `stw 0xAABFFB11 -> RAM 0x7F8020`  — the programming-request magic;
   * `RAM 0x7F8000 = 0` — the *other* magic cell is cleared (section 5.1);
   * `bl 0x071814(0x400000, [0x7F8014], 1)` — shuts the IMB peripherals down
     (TPU3 A 0x704000, QADC A/B 0x704800/0x704C00 get 0x8080/0x80A0),
     `mtspr 81` = EID disables external interrupts;
   * `bl 0x0BA444` with index **0x14B** — `app_fatal_exception_handler`, which
     sets MSR = 0x2942 and spins. **The watchdog then resets the CPU.**

So `10 85` is a *reset request*, not a session change. That is why the
application dispatch table has no programming SIDs and why no session bitmask
value ever selects them.

### 2.2 After the reset: how the programming stack is selected

`0x133F40`, called from `app_init`'s neighbourhood (0x04CD34 and 0x04CF7C
inside 0x04CC94), reads RAM **0x7F8020**; if it equals **0xAABFFB11** it sets
**bit 2** of the mode byte `boot_mode_flags` (0x7FD401). Four one-line getters
at 0x04CC34 / 0x04CC4C / 0x04CC64 / 0x04CC7C return bits 0..3 of that byte.
None of the four has a static caller — they are reached through the
function-pointer-table family at 0x0B1A80+ (the same family as
`eeprom.md` §9), so **which consumer turns bit 2 into "run
`prog_kwp_init`"** is **HYPOTHESIS**; the chain 0x087494 -> 0x089E9C ->
0x085FEC -> `prog_kwp_init` 0x08C244 is VERIFIED-STATIC but 0x087494's own
entry has no static caller either.

Nothing else in the image writes or reads 0xAABFFB11: the two sites are the
producer 0x036FB0 and the consumer 0x133F8C (searched as a `lis`/`ori`
immediate pair over both code regions).

### 2.3 The services, in tester order

```
27 01                -> 67 01 <seed>          ; programming stack, level 1 LFSR 0x5FBD5DBD
27 02 <key>          -> 67 02 34              ; prog_security_level (0x805A10) = 2
31 C4 <s2 s1 s0> <e2 e1 e0>                   ; erase [start, end]  (0x088D40)
34 <a2 a1 a0> <fmt> <s2 s1 s0>                ; request download    (0x086A28)
36 <data...>         (repeat)                 ; transfer data       (0x086CDC)
37                                            ; transfer exit       (0x08731C)
31 C5 ...                                     ; routine 0xC5        (0x0891C8)
33 <id>                                       ; routine results     (0x087AFC)
```

`31` accepts **routine local ids 0xC4 and 0xC5 only** (0x088D64/0x088D6C);
anything else falls through to 0x0891D8. Routine **0xC4** takes six bytes,
`start:3` and `end:3`, stores them at 0x7FD67C/0x7FD680 and validates them
with **the same** `kwp_download_range_allowed` (`bl 0x0889C8` at 0x088E24).
The four call sites of that validator are 0x086AF8 (SID 0x34), 0x088BF4,
0x088E24 (routine 0xC4) and 0x0891A4 — i.e. **every** address the programming
stack ever acts on goes through the section-0 whitelist.

### 2.4 `kwp_download_range_allowed` (0x0889C8) in full

```c
u8 kwp_download_range_allowed(u32 start, u32 end)   /* r3, r4 */
{
    if (start == 0x020000 && end == 0x07FFFF) return 1;   /* 0x0889C8 */
    if (start == 0x0A0000 && end == 0x1BFFFF) return 1;   /* 0x0889E8 */
    if (start == 0x404000 && end == 0x47FFFF) return 1;   /* 0x088A08 */
    if (start == 0x080000 && end == 0x09FFFF) return 1;   /* 0x088A28 */
    switch (variant_byte /* 0x7FD328 */) {                /* 0x088A48 */
    case 0x11:                                            /* 0x088A58 */
        if (start == 0x1E0000 && end == 0x1FFFFF) return 1;
        if (start == 0x1C0000 && end == 0x1DFFFF) return 1;
        return 0;
    case 0x33:                                            /* 0x088A9C */
        if (start == 0x1C0000 && end == 0x1FFFFF) return 1;
        return 0;
    default:                                              /* 0x088ACC */
        if (start == 0x1E0000 && end == 0x1FFFFF) return 1;
        if (start == 0x1C0000 && end == 0x1DFFFF) return 1;
        if (start == 0x1C0000 && end == 0x1FFFFF) return 1;
        return 0;
    }
}
```

The comparisons are `cmplw` against `lis`/`addi`/`ori` immediates; there is no
table and no mask, so the match really is exact. **VERIFIED-STATIC.**

### 2.5 SID 0x34 `RequestDownload` (0x086A28)

```
34 <a2> <a1> <a0> <fmt> <s2> <s1> <s0>      exactly 7 bytes after the SID
```

* length != 7 -> NRC **0x10**; `prog_security_level != 2` -> NRC **0x33**.
* `start = a2a1a0`, `size = s2s1s0`, `end = start + size - 1`, stored at
  0x7FD498 / 0x7FD49C.
* `kwp_download_range_allowed(start, end) == 0` -> NRC **0x42**.
* **calibration alias**: if `start == 0x080000 && end == 0x09FFFF`, both are
  shifted by `0x1C0000 - 0x080000 = 0x140000` to **0x1C0000 / 0x1DFFFF** and
  the byte 0x7F8077 is set to 0x24 (0x086B10-0x086B5C).
* `fmt` (dataFormatIdentifier) must be **0x00 or 0x11**, else NRC **0x41**;
  `fmt == 0` together with the byte 0x7FD2D4 == 0xA5 -> NRC **0x40**.
  `fmt & 0xF0 != 0` selects one transfer mode, `== 0` another (the second
  writes 0xFFFFFFFF sentinels to 0x7FD4BC/0x7FD4C4).
* finally `bl 0x08A12C` — **the flash driver is copied to RAM here**
  (`ram.md` §6: flash 0x081A00-0x085887, 0x3E88 B, to 0x804800).

The three call sites of `0x08A12C` are 0x086C10 (this handler), 0x087A5C and
0x0888D8; `ram.md` §6 lists their enclosing functions 0x086A28, 0x087494 and
0x088828. **So the driver is relocated once per download request**, not once
per session.

---

## 3. The relocated driver, 0x081A00-0x085887

### 3.1 Correction to `boot.md` §3.2 and `ram.md` §6 — the block *does* use r2

> **2026-09-17 (E6). VERIFIED-STATIC.** `boot.md` §3.2 states that the
> relocated block "contains **zero** r2-relative references — it addresses
> everything through `addis rX,r13,0` / `addi rX,rX,disp`". That is wrong, and
> it is the reason the `r2 = 0xD4CDF0` value exists at all. The block reaches
> its own constant tables with the two-instruction form
> `addis rX,r2,-0x54` + a D-form displacement, which `tools/r2_context.py`
> does not classify as an r2 access because the base register is not r2
> itself. Three such sites, all in `flash_dev_open` (0x082CE0):
>
> ```
> 00082D1C  addis r6,r2,-0x54 ; lbz r6,-0x7670(r6)   -> 0x082980  (device count)
> 00082D64  addis r12,r2,-0x54; addi r12,r12,-0x766C -> 0x082984  (entry[].start)
> 00082D90  addis r12,r2,-0x54; addi r12,r12,-0x7668 -> 0x082988  (entry[].end)
> ```
>
> and a fourth in the erase routine:
>
> ```
> 00082130  addis r10,r2,-0x54; addi r10,r10,-0x796C -> 0x082694  (block-select masks)
> ```
>
> With the *application* base `r2 = 0x5C9FF0` these resolve to the flash
> originals (0x5C9FF0 - 0x540000 - 0x7670 = 0x082980); with the relocated base
> `r2 = 0xD4CDF0` they resolve to the **RAM copy** (0xD4CDF0 - 0x547670 =
> 0x805780 = 0x804800 + (0x082980 - 0x081A00)). That is precisely what
> `reloc_enter_ram_driver` (0x0862F4) is for. The rest of `boot.md` §3.2 —
> that leaving r2 = 0x5C9FF0 over 0x086284-0x086350 in Ghidra is correct —
> still holds, because the block is also executed **in place** from flash
> (section 3.3).

### 3.2 The flash device table (0x082980) — and a correction to `docs/02` §6

```
0x082980  u32 count = 3
0x082984  entry[0]  start 0x000000  end 0x3FFFFF  ops 0x805690 0x8056D4 0x80575C 0x082784 0x8055C4
0x0829A0  entry[1]  start 0x404000  end 0x47FFFF  ops (same five)
0x0829BC  entry[2]  start 0xC00000  end 0xC7FFFF  ops (same five)
```

Entry stride 0x1C. The five `ops` are the driver entry points, given as their
**RAM** addresses inside the 0x804800 image (0x805690 = flash 0x082890,
0x8056D4 = 0x0828D4, 0x80575C = 0x08295C, 0x8055C4 = 0x0827C4); slot +0x14
(0x082784) is a flash address because that routine runs in place and calls the
RAM copy of the probe (`bl 0x804A18` at 0x0827AC = RAM image of 0x081C18).

The **RAM-resident bootstrap loader has the identical table**, at flash
0x01E71C (RAM 0x7FD6AC), with the same three start/end pairs and its own five
pointers 0x7FCC8C / 0x7FCCC4 / 0x7FCD20 / 0x7FCDD0 / 0x7FCE04.

> **Correction to `docs/02_memory_map.md` §6, 2026-09-17 (E6).
> VERIFIED-STATIC.** §6 says "descriptor lists for the runtime checker are at
> file 0x1E73C and 0x829A0 (`00404000 0047FFFF` followed by RAM pointers
> 0x7FCC8C/0x7FCCC4 and 0x805690/0x8056D4)". Those two are **not** checksum
> descriptor lists: they are **entry 1 of the flash-device table** in the RAM
> loader and in the application's programming module respectively — a
> `{start, end, five driver function pointers}` record. The only real runtime
> descriptor list of the three is **0x0A3A10** (section 5.3).

Device 1 and device 2 are additionally gated on a device-type byte:
`flash_dev_open` (0x082CE0) requires **`RAM 0x7F8014 == 0x20`** for index 1
and `RAM 0x7F8019 == 0x20` for index 2 (0x082D34, 0x082D4C). Both bytes are
written by the boot module (section 6.1); on MPC563 silicon 0x7F8014 is
**always** 0x20, so device 1 is always enabled on this ECU.

### 3.3 Where each half runs

`ram.md` §6 and `boot.md` §3.2 describe the sequence at 0x0861A8:

```
000861A8  bl 0x083A18        ; copy helper, runs in place from flash
000861AC  bl 0x0862F4        ; reloc_enter_ram_driver: delta += 0x782E00, r2 = 0xD4CDF0
000861B0  bl 0x806EA0        ; RAM entry, flash original 0x0840A0
000861B4  bl 0x0862A4        ; reloc_leave_ram_driver: r2 back to 0x5C9FF0
```

The block therefore has **two** execution contexts. Anything that touches the
external flash's command interface must run from RAM (a flash device cannot be
read while it is in a command cycle); the bookkeeping around it runs in place.
Five functions in the block `bl` **out** of it to 0x086350 / 0x086398 /
0x0863C0 (sites 0x083EBC-0x083F04, 0x083EDC-0x083EFC, 0x084490-0x0844A0,
0x0844D0-0x084534) — those relative branches are only correct when the code
runs at its flash address, which confirms the split.

---

## 4. What the driver can program, and with what granularity

### 4.1 Five command sets, selected at run time

`flash_dev_probe` (0x081C18) picks the command set:

* **`addr >= 0x404000` -> type 3, the on-chip UC3F.** Register block
  0x6FC800 when `addr < 0xC00000`, else 0xEFC800; array base stored to
  0x7FD260 as **0x400000** (resp. 0xC00000).
* otherwise the low nibble of **`RAM 0x7F800C`** selects the external command
  set: 1 -> type 0, 2 -> type 1, 4 -> type 2, 8 -> type 4; anything else
  returns error 0x20.

| type | command set | identify sequence | manufacturer / device id |
|---|---|---|---|
| 0 | Intel, 32-bit port | `FFFFFFFF`, `00900090`, read 0 | 0x00890089, id word **0x88F388F3** |
| 1 | AMD, 32-bit port | `F0`, `AA`@0x1554, `55`@0xAA8, `90`@0x1554 | manufacturer 1, id **0x0000007E** |
| 2 | Intel/ST, 32-bit port | `90`, read 0 | manufacturer **0x20**, id **0x00008835** |
| 3 | **UC3F on-chip** | UC3FMCRE[SBEN] = 11; require UC3FMCR[PROTECT] == 0 and UC3FMCRE[SBPROTECT] == 0 | — |
| 4 | AMD, 16-bit port | `F0`, `AA`@0xAAA, `55`@0x554, `90`@0xAAA (`sth`) | manufacturer 1, id **0x00002203** |

The run-time probe in the boot module (0x010EF8-0x010FAC) only ever produces
nibble **4** (manufacturer 0x20 -> ST/Numonyx, CFI query 0x98, sub-type from
the byte at array+0x8C) or **2** (manufacturer 1 -> AMD) or 0 (nothing), so on
this hardware the CS0 part is a **type 1 or type 2** device. Which one cannot
be read out of a static dump — the value lives in RAM. Both candidates are
2 MB parts; their geometries are in the next table. (The type-4 id 0x2203 with
the sector list 16K/8K/8K/224K/7x256K is the AMD **Am29BL162C** layout, the
part other MED9 boards use, but the boot probe on *this* image cannot select
it. **HYPOTHESIS**, noted only so nobody chases it again.)

### 4.2 The erase-block geometry table (0x0825E4, five 0x20-byte entries)

`flash_erase_block_start` (0x081F78) walks
`geom[type] = 0x0825E4 + type*0x20`, whose layout is
`{u32 id; u8 count[5]; u8 pad[3]; u32 size[5]}`, starting at the array base
and accumulating block starts until it hits the requested address. **The
requested address must be exactly a block start**, otherwise the function
returns **0x40** (no match).

| type | region list | total |
|---|---|---|
| 0 | 31 x 0x20000, 8 x 0x4000 | 4 MB |
| 1 | 8 x 0x2000, 30 x 0x10000, 8 x 0x2000 | 2 MB |
| 2 | 8 x 0x2000, 31 x 0x10000 | 2 MB |
| **3 (UC3F)** | **1 x 0x4000, 2 x 0xC000, 1 x 0x4000, 6 x 0x10000** | **512 KB** |
| 4 | 1 x 0x4000, 2 x 0x2000, 1 x 0x38000, 7 x 0x40000 | 2 MB |

**The UC3F map, expanded from array base 0x400000** — and it is exactly
Figure 21-8 of the MPC561/563 reference manual as `mpc5xx_registers.md` §8
quotes it:

| idx | range | size | UC3FCTL select bit (table 0x082684) | note |
|---|---|---|---|---|
| 0 | 0x400000-0x403FFF | 16 KB | 0x0200 = SBBLOCK[0] | small block 0, shadow row — **not in any whitelist row** |
| 1 | 0x404000-0x40FFFF | 48 KB | 0x0080 = BLOCK[0] | |
| 2 | 0x410000-0x41BFFF | 48 KB | 0x0040 = BLOCK[1] | |
| 3 | 0x41C000-0x41FFFF | 16 KB | 0x0100 = SBBLOCK[1] | small block 1 |
| 4 | 0x420000-0x42FFFF | 64 KB | 0x0020 = BLOCK[2] | |
| 5 | 0x430000-0x43FFFF | 64 KB | 0x0010 = BLOCK[3] | holds the set-A raster hook 0x432940 and 0x431384 |
| 6 | 0x440000-0x44FFFF | 64 KB | 0x0008 = BLOCK[4] | |
| 7 | 0x450000-0x45FFFF | 64 KB | 0x0004 = BLOCK[5] | |
| 8 | 0x460000-0x46FFFF | 64 KB | 0x0002 = BLOCK[6] | |
| 9 | 0x470000-0x47FFFF | 64 KB | 0x0001 = BLOCK[7] | |

The mask table at 0x082684 is ten halfwords `0200 0080 0040 0100 0020 0010
0008 0004 0002 0001`, and the driver shifts them left by 8 into UC3FCTL, which
places them at bits 14:15 (SBBLOCK) and 16:23 (BLOCK) — the exact field
positions of Figure 21-4 in the manual. **The mapping is self-consistent three
ways** (block sizes, block-select bits, and the array-base walk), so it is
VERIFIED-STATIC without needing the silicon.

**Erase granularity for the OBD route: 48 KB / 16 KB / 64 KB as above for the
on-chip flash, 8 KB parameter blocks plus 64 KB main blocks for the 2 MB CS0
part.** Nothing smaller can be erased; programming is per 32-bit word.

### 4.3 The UC3F program / erase sequences, verbatim

Register base `r31 = 0x6FC800`: `+0 UC3FMCR`, `+4 UC3FMCRE`, `+8 UC3FCTL`.

**Erase** (`flash_erase_block_start` 0x081F78, type-3 arm at 0x082120):

```
UC3FCTL &= ~0x0003FC00          ; rlwinm r11,r11,0,24,13 -> clear SBBLOCK+BLOCK
UC3FCTL |= (blockmask << 8)     ; select this block
UC3FCTL |= 0x6                  ; bit 29 PE = 1 (erase), bit 30 SES = 1
*(u32 *)0x400000 = 0            ; the erase interlock write ("any array location")
UC3FCTL |= 0x1                  ; bit 31 EHV = 1  -> high voltage, erase starts
return 0x100                    ; busy
```

**Program** (`flash_program` 0x082208, type-3 arm at 0x082460), one 32-bit
word per iteration:

```
UC3FCTL |= 0x2                  ; SES = 1, PE stays 0 => program
*(u32 *)dst = *(u32 *)src       ; the programming write: latches address + data
UC3FCTL |= 0x1                  ; EHV = 1
r = flash_poll_uc3f(1)          ; blocking
if (r == 0x20) return 0x1000    ; program failed
dst += 4
```

**Poll** (`flash_poll_uc3f` 0x081BB4):

```
while (UC3FCTL & 0x80000000)    ; bit 0 HVS = high voltage in progress
    if (!blocking) return 0x100
UC3FCTL &= ~1                   ; EHV = 0
UC3FCTL &= ~2                   ; SES = 0
return (saved & 0x40000000) ? 0 : 0x20     ; bit 1 PEGOOD
```

This is step-for-step the manual's program sequence (§21, "Write SES = 1 ...
Write EHV = 1 ... read until HVS = 0 ... confirm PEGOOD = 1 ... EHV = 0 ...
SES = 0") and erase sequence (the same with PE = 1 and one interlock write).
**VERIFIED-STATIC.**

### 4.4 Who takes the protection off

`UC3FMCR[PROTECT[0:7]]` and `UC3FMCRE[SBPROTECT[0:1]]` reset to **all ones**
(every block protected). Two identical unlock routines exist:

| addr | what it does | callers |
|---|---|---|
| **0x011CE0** `uc3f_unprotect` | `BR0 &= ~0x100` (CS0 write-protect off); if `0x7F8014 == 0x20`: `UC3FMCR &= 0xFFFFFF00` (PROTECT = 0), `UC3FMCRE &= ~SBPROTECT`; the same for the second module at 0xEFC800 if `0x7F8019 == 0x20` | boot 0x012708 (tail `b`), boot 0x012F88 |
| **0x011D58** `uc3f_protect` | the mirror: `BR0 \|= 0x100`, `UC3FMCR \|= 0xFF`, `UC3FMCRE \|= SBPROTECT` | boot 0x012528 |
| **0x082CA8** `uc3f_unprotect_ram` | the same unlock, without the device-byte gates and without the 0xEFC800 arm — **inside the relocated driver** | (through the driver's own paths) |

Neither routine ever writes `UC3FMCR[LOCK]` (bit 1), so the write-lock is never
armed and PROTECT stays writable for the whole power cycle. **A normal boot
ends with the array protected** (0x012528); the programming boot path
**unprotects** it (0x012708 / 0x012F88), and the RAM driver can unprotect it
again on its own. `flash_dev_probe`'s type-3 arm (0x081F2C) *refuses* to
proceed while PROTECT or SBPROTECT is non-zero, which is how the two halves
are tied together.

---

## 5. Boot-time verification, and what a bad flash does

### 5.1 Two RAM magics drive the boot's mode decision

`boot_mode_select` (0x012ED4) runs three tests in order and, if any says yes,
calls 0x012658 — which copies the third DECRAM routine, runs it, and
**tail-calls `uc3f_unprotect`** — then stores 0xDEADBEEF to DECRAM 0x6F840C and
branches to **`bl 0x7F8728`** instead of the application entry.

| test | what it looks at | verdict |
|---|---|---|
| 0x01270C | byte `RAM 0x7F8010` not in {0, 0x10}, plus a check over the two CS2 pointers at DECRAM 0x6F8404/0x6F8408 | external-tool bootstrap |
| **0x012780** | word **`RAM 0x7F8000`** equals one of **0xBB44E169, 0xA5BCD193, 0xBD5593F3, 0xE45CD91A, 0x356BD372**; the cell is then zeroed | **software request to re-enter the loader** |
| 0x012894 | (third path) leads to the indirect call through the word at 0x1C0120, which is 0xFFFFFFFF in this dump -> dead (`boot.md` §2.1) | |

`bl 0x7F8728` is the **RAM-resident bootstrap loader**: the boot copies flash
**0x019798-0x02A827 (0x5090 B) to 0x7F8728** and `boot_swsr_service`
(0x0110D0, 0x48 B) to 0x7FD7B8 — the two tile exactly into
0x7F8728-0x7FD7FF — and then calls it. This is the loader that can rewrite the
regions the application-side module refuses to touch, and it carries its own
copy of the device table (section 3.2). Its entry has **no other caller**.

Of the five magics only **0xBB44E169** is used anywhere else:

| site | what it does |
|---|---|
| 0x020D90 | a fault counter at 0x7F90F9 exceeds the calibration byte at 0x5C2EA6 -> set the magic, `bl 0x0BA444` with index 0x13D (hang -> watchdog reset) |
| **0x06DFB8** | **the calibration marker check** (next section) |
| 0x088340 | in the programming module: if 0x7F8000 already holds the magic, spin at 0x08835C forever (deliberate watchdog reset); else clear it |
| 0x438868 | on-chip: if the magic is set, `bl 0x0BA444` index 0xC8; else clear it |
| 0x0130A4 | boot: set the magic and re-enter `boot_mode_select` |

### 5.2 EEP_CONF block 10 is the "programmed by" record

The `10 85` handler stages five fields into **EEPROM block 10** (offset 0x260,
0x20 B, `eeprom_map.py`) before it resets the ECU — this is the record the
brief asked about, and it is the only "flash counter"-like store in the image:

| block-10 offset | length | source | value |
|---|---|---|---|
| +0x02 | 0x0C | `r2+0x4E30` = **0x5CEE20** = flash 0x1CEE20 | the VW hardware number / engine label from the identification block |
| +0x0E | 0x04 | `r2+0x4E52` = **0x5CEE42** = flash 0x1CEE42 | the `9387` version field |
| +0x12 | 1 | 0x7FB7C1 | 0 |
| +0x13 | 1 | 0x7FB7C2 | the range classifier (0x00 / 0x11 / 0x22 / 0x33, from comparing 0x7FB7BC against 0x1DFFFF / 0x1EFFFF / 0x1FFFFF) |
| +0x14 | 2 | 0x7FB7C3 | 0x8F70 if `0x7FEB57 != 0`, else 0x4EB1 |

The fifth call is the **commit** (len 0, `eeprom.md` §9 calls this shape a
commit site: 0x036DC4, 0x036DEC, 0x036EDC are three of the twelve). The
identification block 0x1CEE20-0x1CEE6F itself is *read*, never written — the
flash tool does not stamp it. **VERIFIED-STATIC.**

### 5.3 What the firmware actually verifies

Three mechanisms, and only one of them can stop the engine:

**(a) A CRC-32 over three ranges, reported, never compared.**
`flash_crc_task` (0x011CB10-0x011CD20) is a four-state machine (state byte
0x7FB6F4) driven at 0x64 bytes per activation:

* state 0 builds the standard reflected CRC-32 table (polynomial
  **0xEDB88320**) into RAM **0x800288** (256 x u32) and loads range 0 from the
  descriptor list at **0x0A3A10**;
* the list is `{0x020000, 0x1BFFFF}, {0x404000, 0x47FFFF}, {0x5C2E00,
  0x5FFFFF}, {0, 0}` — i.e. all of the application code in both flashes plus
  the calibration through its high alias;
* state 1 runs the CRC; at the end it applies the final `^ 0xFFFFFFFF`;
* state 2 publishes the **low halfword** of the CRC to RAM **0x7F9178** and a
  second halfword to 0x7F917A, then goes to state 7;
* state 7 only sets bit 0 of 0x801200 and of 0x7F9176.

**There is no comparison against a stored value anywhere in this chain.** The
CRC is a *reported* value (the classic VW "Flash-Prüfsumme" that a tester
reads back), not a gate. The 65 sum/~sum block descriptors
(`tools/checksum.py`, `docs/02` §6) are likewise never recomputed at run time:
the only references to the 54-entry table at 0x0A0000 are 0x020764, 0x02081C,
0x089E68, 0x09DD94, 0x09DE28, 0x09DEBC — all inside the programming module and
its neighbours, i.e. **the tool-facing side**, not a start-up check.
(0x0889E8 also matches the search but is a false positive: it is the
`lis r12,0xA; addi r12,r12,0` of the 0x0A0000 whitelist bound.)

**(b) The calibration `5A5A5A5A` marker — this one bites.**
Inside `app_init`'s chain (`bl 0x06DD3C` at 0x04CF6C):

```
0006DF9C  lis   r12,0x5E
0006DFA0  lhz   r12,0x2500(r12)      ; halfword at 0x5E2500 = flash 0x1E2500
0006DFA4  cmpwi r12,0x5A5A
0006DFA8  beq   0x6DFC4              ; ok
0006DFAC  bl    0x6E0AC              ; raise the "no valid dataset" flag
0006DFB0  lis   r12,0xBB44 ; ori r12,r12,0xE169
0006DFB8  stw   r12,-0x7FF0(r13)     ; RAM 0x7F8000 = the loader magic
0006DFBC  li    r3,0xD2
0006DFC0  bl    0x0BA444             ; hang -> watchdog reset -> loader
```

In this dump `0x1E2500` really is `5A 5A 5A 5A`, so the check passes. **If the
calibration block is erased or mis-programmed the ECU will not start the
application at all: it reboots straight into the RAM bootstrap loader and
stays there** — which is exactly the "programming incomplete" behaviour the
brief asked about, and it is a *good* property: a half-finished Flash 0 leaves
a re-flashable ECU rather than a brick.

**(c) The same reaction from a fault counter** (0x020D90, section 5.1), gated
by the calibration byte at 0x5C2EA6.

**VERIFIED-STATIC** for all three.

---

## 6. Loose ends this brief closed or moved

### 6.1 The hardware descriptor block at 0x7F8000-0x7F802F

| cell | meaning | written at |
|---|---|---|
| 0x7F8000 | u32 loader-request magic (section 5.1); cleared by `kwp_start_session_core` at 0x036BE0 | 0x0130B0, 0x020D90, 0x06DFB8 |
| 0x7F8004 | u32 boot handshake word 0x5A78AA23 | 0x012F0C |
| 0x7F800C | u8 CS0 command-set nibble (section 4.1) | 0x010FAC |
| 0x7F800D | u8 CS0 size in MB (`(x & 0x3F) << 20` gives the end address) | boot probe |
| 0x7F8010 | u8 boot-mode byte tested by 0x01270C | 0x011F4C |
| 0x7F8012 | u8 external SRAM size 0x41/0x44 (`eeprom.md` §6) | 0x011898 |
| **0x7F8014** | **u8 on-chip flash device type: 0x20 = UC3F present, 0x10 = none** | **0x012388 / 0x0123F8** |
| 0x7F8018 | u8 IMMR bits 16:23 (mask revision) | 0x01239C / 0x01240C |
| 0x7F8019 | u8 second on-chip flash device type | 0x011C70 |
| 0x7F8020 | u32 programming-request magic 0xAABFFB11 (section 2.1) | 0x036FB8 |

`0x012364-0x0123F8` is the part-number switch `docs/02` §1 refers to:

```
00012364  mfspr r3,0x27E        ; IMMR
00012368  srwi  r3,r3,0x18      ; PARTNUM
0001236C  cmpwi r3,0x35         ; MPC561/562 = flashless
...  match:     mtspr 0x27E, 0x0002   ; FLEN=0, ISB=1   ; 0x7F8014 = 0x10
...  no match:  mtspr 0x27E, 0x0802   ; FLEN=1, ISB=1   ; 0x7F8014 = 0x20
```

so on our MPC563 the on-chip array is enabled and **flagged as programmable at
every boot**, unconditionally. This upgrades the `docs/02` §1 sentence "the
boot code checks the IMMR part number for 0x35 and takes a different path" to
a decoded pair of writes, and gives FLEN its site.

### 6.2 `kwp_transfer_mode4` is the EEPROM window — `kwp.md` §9 item 3 settled

`kwp.md` §5.1 and §9 left "the 0x480000 window's internal segment semantics"
open and the brief guessed it was "the programming-time view of the flash".
It is not. `kwp_transfer_mode4` (0x0A33B4) computes `off = addr - 0x480000`
and walks **32 entries of 0xC bytes at 0x0B2FF0** — which is `EEP_CONF`, the
SPI-EEPROM block table that `tools/eeprom_map.py` already decodes
(`eeprom.md` §3.3). The fields it uses are exactly EEP_CONF's: `+0x02` u16
eeprom offset, `+0x08` u16 flags whose bit 0 means "two copies" (the length is
then doubled: `rlwinm r12,r12,1,23,30` at 0x0A346C), `+0x0A` u8 length,
`+0x0E` the next entry's offset. Ranges no block covers are filled with 0xFF.

> **The 1 KB upload window 0x480000-0x480400 is the SPI EEPROM exposed through
> its block table** — i.e. the tool's EEPROM read-back path, not a flash view.
> VERIFIED-STATIC. `kwp.md` §9 is marked SETTLED accordingly.

The table maps all 32 EEP_CONF blocks (2,043 of the 2,048 device bytes; the
other 5 are page padding), but `kwp_sid_35_h1` refuses an upload whose end
reaches 0x480400 with NRC 0x53, so only **blocks 0-21, EEPROM offsets
0x000-0x3FF**, are readable this way. Block 10 — the programming record of
section 5.2, EEPROM 0x260 — is inside that first kilobyte, so a tester can read
it back over OBD without an EEPROM clip.

### 6.3 `boot.md` §5 open item — settled

"What is the routine relocated to 0x804800 (entry 0x806EA0, flash 0x0840A0)?
It is reached from 0x0861A8 and is almost certainly the external-flash
programming driver." **It is the flash programming driver for *both* devices**:
sections 3 and 4. Marked SETTLED in `boot.md` §5.

---

## 7. Consequences for `patches/ff_fuel` (#32) and for Flash 0 (#26)

### 7.1 The on-chip hook words are reachable over OBD

`patches/ff_fuel` currently has seven hook words, six of them in
0x404000-0x47FFFF: 0x42247C (rk), 0x432940 (set-A raster), 0x41D40C
(ignition), 0x41A680 / 0x41A808 (start fuel), 0x431384 (start ignition); only
0x12067C (set-B raster) is external. All six lie inside whitelist row 3
**0x404000-0x47FFFF**, in erase blocks 2, 3, 5 and 7 of section 4.2.

**From the dump: the firmware's own programming service can erase and
reprogram every one of them.** The remaining uncertainty is no longer "can the
ECU write that array" — it is "does KESSv2 protocol 179 *drive* this route for
that range". That is a property of the tool, not of the firmware, and it is
out of this brief's scope; the bench read-back after Flash 0 remains the proof
(`patches/ff_fuel/test/procedure.md` §1).

### 7.2 What the first bench flash must check

1. **Read back 0x404000-0x47FFFF after Flash 0** and `bindiff` it against the
   written file. This is the one measurement that decides #32. If KESS writes
   the on-chip array, the bytes match; if it silently skips it, they are the
   stock bytes.
2. **Read back 0x1E2500** and confirm `5A 5A 5A 5A` (section 5.3b). If the
   calibration marker is wrong the ECU will loop into the loader rather than
   start — expected, recoverable, but it must be recognised as such and not
   mistaken for a brick.
3. **Read EEP_CONF block 10** (eeprom offset 0x260, 0x20 B) before and after.
   The ECU stamps the identification string and a status word there on every
   `10 85`; a changed block 10 is independent evidence that the ECU's own
   programming route ran, and the classifier byte at +0x13 says which range
   family the tool asked for.
4. **Do not** let any tool address 0x000000-0x01FFFF, 0x080000-0x09FFFF or
   0x400000-0x403FFF. The firmware refuses all three; a tool that goes around
   the firmware (BDM) does not, and 0x400000-0x403FFF carries the reset
   configuration word and the censorship bits. Take the K-TAG/BDM read of the
   missing 16 KB **before** the first write, as `docs/02` §2 already says.

### 7.3 If the read-back shows the on-chip flash was skipped

Then the decision per hook is the one the brief asked for. Note first that an
"external-flash alternative" is only worth it if it is reachable from an
external-flash **caller**: whitelist rows 1, 2 and 5-7 cover 0x020000-0x07FFFF,
0x0A0000-0x1BFFFF and the calibration, and the erase granularity there is
8 KB / 64 KB, so an alternative hook costs no more than the on-chip one.

| hook | site | erase block | external-flash alternative |
|---|---|---|---|
| rk (D1) | 0x42247C in `FUN_004223B0` | 4 (0x420000-0x42FFFF) | **candidate C**: `KRKATE` at 0x5D3DBC, one reference in the whole image (`injection.md` §6.4), pure calibration, covers every injection mode — but it is a constant and follows E% only at the 1000 ms raster. Candidate B (rewriting the `mullw` at 0x0AC39C, external flash 0x0AC39C, whitelist row 2) is the only *code* alternative and needs an in-place instruction rewrite, not a `bl`. |
| set-A raster | 0x432940 | 5 | none in external flash: task set A lives entirely on-chip (`scheduler.md` §11.8). The existing **set-B raster hook 0x12067C is already external** and is the fallback — at the cost of losing set A. |
| ignition (E1) | 0x41D40C | 3 (0x41C000-0x41FFFF, small block 1) | open — E1 owns `ignition.md`; the caller chain into 0x41D40C has not been searched for an external-flash site by this brief. |
| start fuel (E2) | 0x41A680 / 0x41A808 | 2 (0x410000-0x41BFFF) | `FUN_00430974` is called only from `task_100ms_int` (`injection.md` §6.4), which is on-chip; the calibration-only lever is `KRKATE` again. |
| start ignition (E2) | 0x431384 | 5 | as above |
| rail (E5) | E5's site | — | E5 owns `rail.md`; not assessed here. |

For any of these, "BDM/K-TAG only" is the honest answer if no external-flash
caller exists — but **that situation is not the one the dump predicts**.

---

## 8. Open questions

| Question | Status |
|---|---|
| Which consumer turns `boot_mode_flags` bit 2 (0x7FD401) into "start the programming KWP stack"? The getters 0x04CC34-0x04CC7C and the module entry 0x087494 have no static callers; they sit behind the 0x0B1A80+ pointer-table family. | open (HYPOTHESIS: an init-table entry) |
| Which CS0 part is fitted — type 1 (AMD, id 0x7E) or type 2 (ST, id 0x8835)? The probe result is a RAM byte; the dump cannot say. | needs the bench (read RAM 0x7F800C/0x7F800D over DDLI) |
| What does the RAM bootstrap loader (0x7F8728, flash 0x019798-0x02A827) allow? It has its own device table but its own whitelist, if any, was not decoded. | open; it is the route that could rewrite 0x000000-0x01FFFF and 0x080000-0x09FFFF |
| Meaning of the byte at 0x7FD328 that selects the calibration whitelist variant (0x11 / 0x33 / other). | open |
| What the second UC3F module at 0xEFC800 / 0xC00000-0xC7FFFF is for (device 2). Not populated on this ECU (`0x7F8019 != 0x20`). | closed enough: multi-variant firmware |
| Does `31 C5` (0x0891C8) report erase progress or run a checksum? | partly decoded; not needed for the decision |

## 9. Reproduction

```bash
./.venv/bin/python3 tools/checksum.py verify -q data/passat_azx_ori.bin
./.venv/bin/python3 tools/flash_segments.py data/passat_azx_ori.bin --all
./.venv/bin/python3 tools/find_abs_refs.py data/passat_azx_ori.bin --range 0x6FC800 0x6FC80F
./.venv/bin/python3 tools/find_branch_refs.py data/passat_azx_ori.bin 0x11CE0 0x11D58 0x8A12C
./.venv/bin/python3 tools/eeprom_map.py data/passat_azx_ori.bin          # the mode-4 window
GHIDRA_INSTALL_DIR=/usr/local/Cellar/ghidra/12.1.3/libexec \
  ./.venv/bin/python ghidra_scripts/decompile.py --project-dir /tmp/ghidra_E6 \
  --project-name med9 0x889c8 0x86a28 0x81c18 0x81f78 0x82208
```
