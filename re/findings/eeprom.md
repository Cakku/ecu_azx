# SPI EEPROM, the EEP_CONF block layer, and the external SRAM

Agent B4, brief `docs/agent_briefs/B4_variant_byte_and_eeprom.md`, issue #18.
Date: 2026-09-15. Dump: `data/passat_azx_ori.bin`
(SHA-256 `b155…09b3`, unmodified; `tools/checksum.py verify -q` = `ALL OK (65 blocks)`
before and after this work).

All addresses are **CPU** addresses unless prefixed `file`. In the external
flash region CPU == file offset, so most addresses below are both.

Reproduce the block table and the client map with

```bash
python3 tools/eeprom_map.py data/passat_azx_ori.bin            # EEP_CONF table + occupancy
python3 tools/eeprom_map.py data/passat_azx_ori.bin --clients  # who uses which block byte
python3 tools/eeprom_map.py data/passat_azx_ori.bin --check dumped_eeprom.bin  # verify a real 2 KB read
```

---

## 0. Summary

| Question from the brief | Answer |
|---|---|
| SPI driver | QSMCM/QSPI at 0x705000; **three** driver instances (boot 0x017838/0x0178DC, application 0x085888/0x085920, second application copy 0x09D800/0x09D894). VERIFIED-STATIC |
| EEPROM device | ST M95xxx-class, **16-bit address**, **32-byte page**, **2048 bytes**, on **PCS0**, SPI mode 0, 8 bits/transfer, SCK ≈ 1.25 MHz. VERIFIED-STATIC |
| Block table (EEP_CONF) | **file 0xB2FF0, 32 records × 12 bytes**, fully decoded below. VERIFIED-STATIC |
| Per-block checksum | **16-bit sum of the first (len-2) bytes, stored bit-complemented as a big-endian u16 at offset len-2**. Generator `FUN_00061A48`/`FUN_00061AC4`, verifier `FUN_000619AC`. VERIFIED-STATIC |
| RAM mirror | one contiguous mirror at **0x7F9E80-0x7FA47F (0x600 bytes)**, base pointer in flash at file 0xB3184. VERIFIED-STATIC |
| Read at start-up / write at key-off | `FUN_00062280` (read-all + verify) and `FUN_00062740` (write-all + regenerate checksums); queue pump `FUN_00060A68` runs from the background task (`FUN_001205A0`, `FUN_004328E4`). VERIFIED-STATIC for the code, HYPOTHESIS for the exact trigger points |
| Spare block / spare bytes | **No spare block.** The 32 records cover 0x000-0x7FF with no gap. There *are* unused payload bytes inside several blocks — see §5. VERIFIED-STATIC |
| External SRAM battery-backed? | The firmware **behaves as if it is**: the boot-time sizing probe saves and restores every word it disturbs, and neither start-up clears 0x800000-0x807FFF. Electrical confirmation still needed. VERIFIED-STATIC (code) + HYPOTHESIS (hardware) |

---

## 1. The QSPI hardware layer

### 1.1 The 49 QSMCM references, sorted

`tools/find_abs_refs.py data/passat_azx_ori.bin --range 0x705000 0x7051DF`
gives 49 sites. Following each `lis 0x70 / addi 0x5000` base register forward
through its D-form accesses (scratch script, method recorded here) splits them:

| Sites | Registers touched | Function |
|---|---|---|
| 0x015674-0x0159A8, 0x084648-0x085260, 0x0BA084-0x0BB690, 0x09545C-0x095688, 0x0BBDB0, 0x1435B0-0x1437DC | SCC1R0/1, SC1SR, SC1DR, QSCI1CR/SR, SCC2R1, SCTQ, PORTQS | **SCI / QSCI** (K-line and the second serial), not EEPROM |
| **0x017838, 0x0178DC-0x0179E8, 0x017AB0, 0x017B40, 0x017CFC** | PQSPAR, DDRQS, PORTQS, SPCR0-3, SPSR, TXRAM, RXRAM, CMDRAM | **QSPI driver, boot module** (r2 = 0x017FF0) |
| **0x085888/0x08589C, 0x085920/0x08598C** | same set | **QSPI driver, application** |
| **0x09D800/0x09D818, 0x09D894/0x09D8FC** | same set | **QSPI driver, second application copy** |
| 0x0133E8, 0x064CFC, 0x01B238 | SPSR only / none | helpers |

### 1.2 Correction to `re/findings/mpc5xx_registers.md` §7

§7 lists the QSPI RAMs as "16 × 16-bit". **They are 32 entries**, and the
firmware uses all 32:

* the address ranges themselves are 0x40 bytes of RX (0x705140-0x70517F) and
  0x40 bytes of TX (0x705180-0x7051BF) = 32 × u16, plus 0x20 bytes of command
  RAM (0x7051C0-0x7051DF) = 32 × u8;
* `FUN_000178DC` rejects a queue longer than 0x20 entries
  (`if ((param_2 & 0xff) < 0x21)`);
* it writes ENDQP as a **5-bit** field: `SPCR2 &= 0xE0FF; SPCR2 |= ((n-1)<<8) & 0x1F00`
  (file 0x017954/0x01796C). A 16-entry queue would use a 4-bit field.

VERIFIED-STATIC, evidence: disassembly at file 0x0178DC-0x0179E8 and the
decompilation of `FUN_000178DC`.

### 1.3 The generic QSPI transfer primitive

```
FUN_00017838(cfg)          boot  |  FUN_00085888()      application
FUN_000178DC(q,n,rx,wait)  boot  |  FUN_00085920(q,n,rx) application
```

`q` is an array of `n` 3-byte entries `{ u16 data; u8 command }`.
`FUN_000178DC` copies `data[i]` to TXRAM[i] (0x705180 + 2*i) and `command[i]`
to CMDRAM[i] (0x7051C0 + i), programs ENDQP = n-1 in SPCR2, sets SPE
(SPCR1 |= 0x8000), spins on SPSR.SPIF, clears it, and copies RXRAM[i]
(0x705140 + 2*i) into `rx`.

Command-RAM byte = `CONT(0x80) | BITSE(0x40) | DT(0x20) | DSCK(0x10) | PCS[3:0]`.
The PCS nibble holds the **levels** driven on PCS3..PCS0 during the transfer.

### 1.4 Port and mode setup

`FUN_00017838` (boot) and `FUN_00085888` (application) both write

```
QSMCMMCR = 0x0080
QSPI_IL  = 0x00            (polled, no interrupt)
PORTQS   = ...|0x78        PCS0..PCS3 idle HIGH  -> chip selects are active low
PQSPAR   = 0x7B            MISO, MOSI, PCS0..PCS3 assigned to the QSPI
DDRQS    = 0x7E            MOSI, SCK, PCS0..PCS3 outputs; MISO input
```

`FUN_00085888` additionally sets

```
SPCR0 = 0xA000 | (f_sys[MHz] * 1e6 / 2.5e6)
        MSTR=1, WOMQ=0, BITS=1000b (8 bits), CPOL=0, CPHA=0  -> SPI mode 0
        SPBR = f_sys / 2.5 MHz  =>  SCK = f_sys / (2*SPBR) = 1.25 MHz
SPCR1 = 0x0019   (SPE off, DTL = 0x19)
SPCR2 = SPCR3 = SPSR = 0
```

The boot driver takes the same five numbers from a per-device configuration
record instead (see §1.5).

### 1.5 The boot-level SPI device table

`FUN_00017E14` selects one of six device tables by hardware variant
(IMMR PARTNUM == 0x35, a nibble of RAM 0x7F800C, RAM 0x7F8014, two magic words
at 0x005FE0) and stores three pointers:

| RAM | Meaning |
|---|---|
| 0x7F83A0 | pointer to the SPI **device table**: `DAT_00[01]02EA` = 4 records × 15 bytes |
| 0x7F83A4 | pointer to the 7-entry **EEPROM WREN/WRDI test queue** |
| 0x7F83A8 | pointer to the command-byte template used by the bulk EEPROM read |

**These functions run under the boot small-data base r2 = 0x017FF0, not the
application r2 = 0x5C9FF0.** Ghidra resolves the operands with the application
base and therefore labels them `DAT_005C2xxx`; the real addresses are
0x0102EA + the same displacement. `docs/02_memory_map.md` §4 already warns
about this; it is the single most misleading thing in this module.

Device table, file 0x0102EB, 4 records of 15 bytes
(`record[0] = deviceId | 0x80` when the device is **enabled**):

| Rec | file | byte0 | 5-byte QSPI config (SPCR0hi, SPBRnum, DSCKL, DTL, SPCR2flags) |
|---|---|---|---|
| 0 | 0x0102EB | `0x80` (dev 0, enabled) | 03 21 14 00 19 |
| 1 | 0x0102FA | `0x01` (dev 1, **disabled**) | 06 21 14 00 19 |
| 2 | 0x010309 | `0x02` (dev 2, **disabled**) | 04 21 14 00 19 |
| 3 | 0x010318 | `0x83` (dev 3, enabled) | 02 20 14 00 19 |

`FUN_00017A10(id, &idxOut, start)` walks this table looking for
`record[0] == (id | 0x80)`; the device count is the byte at file 0x0102EA = **4**
and the EEPROM's device id is the byte at file 0x0102E9 = **3**.

### 1.6 Two EEPROM self-tests in the boot module

`FUN_00017CF0` — **QSPI loopback test**: configures from a 5-byte record,
sets `SPCR3 |= 0x04` (LOOPQ), transfers a 5-entry pattern queue and compares
RX with TX. Error codes 1 (QSPI stuck busy), 2 (short transfer), 3 (data
mismatch).

`FUN_00017A84` — **EEPROM write-enable-latch test**, the decisive proof that
the device is an M95xxx-class SPI EEPROM. It sends the 7-entry queue at
`*(0x7F83A4)`:

```
{0x04,0x20} WRDI   {0x05,0x80} RDSR(CONT)  {0x00,0x20} dummy
{0x06,0x20} WREN   {0x05,0x80} RDSR(CONT)  {0x00,0x20} dummy
{0x04,0x20} WRDI
```

and returns 1 only if `(rx[2] & 2) == 0 && (rx[5] & 2) != 0`, i.e. the status
register's **WEL** bit is clear after WRDI and set after WREN. Opcodes 0x04 /
0x05 / 0x06 and the WEL bit position are the ST M95xxx instruction set.

`FUN_00017B34` is a **bulk dump/probe**: it reads `param_1` bytes twice, once
with a 1-byte address and once with a 2-byte address, into two different
buffers — a device-width probe. Its address is stored in the pointer table at
file 0x0105F0 (`0x017838, 0x0179EC, 0x017A10, 0x017B34`).

---

## 2. The application byte/block primitives (M95160)

| Address | Name | Behaviour |
|---|---|---|
| 0x085888 | `eeprom_spi_config` | the SPCR setup of §1.4 |
| 0x085920 | `eeprom_qspi_xfer` | the generic transfer of §1.3 |
| **0x085A8C** | `eeprom_write_byte(addr, data)` | queue `{0x06,0x0E} WREN`, `{0x02,0x8E} WRITE`, `{addr>>8,0x8E}`, `{addr&0xFF,0x8E}`, `{data,0x8E}`; then poll `{0x05,0x8E} RDSR`, `{0x00,0x8E}` until **WIP (status bit 0) clears** |
| **0x085B54** | `eeprom_write_bytes(addr, n, src)` | byte-at-a-time loop over `eeprom_write_byte`, with `FUN_0008437C()` (watchdog/delay) between bytes |
| **0x085BC0** | `eeprom_read_bytes(addr, n, dst)` | chunks of ≤ 0x1D bytes: `{0x03,0x8E} READ`, `{addr>>8,0x8E}`, `{addr&0xFF,0x8E}` + up to 29 dummies = 32 queue entries; error count accumulated in 0x7FD150 |
| 0x09D800 / 0x09D894 / 0x09D9EC | second copy of config / xfer / read | same code, error count in 0x7FAAD0; only caller 0x09DADC |

**The EEPROM is on PCS0.** Every command byte is 0x0E or 0x8E; `0x0E & 0x0F = 0b1110`
drives PCS3..PCS1 high and **PCS0 low**, and 0x8E is the same with CONT set so
the chip select stays asserted across the opcode/address/data entries of one
command.

**16-bit addressing** (`addr>>8` then `addr & 0xFF`) rules out M95040 and
smaller; the EEP_CONF table in §3 ends at 0x7FF, which fixes the part at
**2 KB = M95160**.

Direct clients of these primitives (all in the immobiliser / adaptation
module, bypassing the block manager of §3 and keeping their own mirror at
0x7FD2CC/0x7FD2EC):

| Function | EEPROM bytes |
|---|---|
| 0x085CB8 | read 0x042, 6 B -> 0x7FD32B |
| 0x085DEC | read 0x142, 11 B -> 0x7FD346 -> 0x7FD30D |
| 0x085D44 / 0x085E54 | read 0x260, 32 B -> 0x7FD2EC |
| 0x085EF4 | read 0x288, 1 B |
| 0x085F44 | read 0x280, 32 B -> 0x7FD2CC |
| 0x087494 | writes 0x288/0x28A/0x28B/0x294/0x296/0x297 and the same at +0x20 (0x2A8/0x2AA/0x2AB/0x2B4/0x2B6/0x2B7), plus 0x274 (2 B); then reads back 0x280 and 0x2A0 and compares against the mirror |
| 0x087C44 / 0x08808C | write 0x28C/0x298/0x2AC/0x2B8 (2 B each) |
| 0x0894A0 | write then read-back-verify a whole 32-byte block at 0x280 **or** 0x2A0 |

Note the redundancy scheme this module uses: the same byte is stored **four
times** — twice inside the block (data at +0x08, its **bitwise complement** at
+0x14, e.g. `DAT_007FD2E2 = ~DAT_007FD488` at 0x087494) and again in the
duplicate block at 0x2A0. That is *on top of* the block manager's own
checksum, and it is why block 11's payload looks sparse in §5.

---

## 3. EEP_CONF: the block manager

### 3.1 API

```
FUN_0006131C(blockIdx, offset, len, mode, bufPtr, handlePtr)
```

It is **not called with `bl`** — every caller builds the address with
`lis/addi`, does `mtlr`, and calls `blrl`
(`tools/find_abs_refs.py … --target 0x6131c` finds 60+ sites).

The stock write sequence, from the KWP coding handler `FUN_000A2938`:

```c
FUN_0006131C(7, 0x08, 1, 0, &src1, 0);    /* returns 2: field staged in the mirror */
FUN_0006131C(7, 0x02, 6, 0, &src2, 0);    /* returns 2                              */
FUN_0006131C(7, 0x00, 0, 0, 0, &handle);  /* returns 1: block queued for the device */
```

i.e. **stage one or more fields into the RAM mirror, then commit the whole
block with `len = 0`, `bufPtr = 0` and a non-null handle.** The commit path
regenerates the checksum and writes every copy; the caller polls `handle`.

Supporting data, all in flash next to the table:

| file | Content |
|---|---|
| 0xB2FF0 | the 32 × 12-byte block table (§3.2) |
| 0xB3184 | pointer to the **RAM mirror base = 0x7F9E80** |
| 0xB318C | pointer to the **default/init value table = file 0xB3238** |
| 0xB3190 | pointer to the scratch/page buffer |
| 0xB3195 | request-queue length (4) |
| **0xB3196** | **EEPROM page size = 0x20** |
| 0xB3197 | retry count (2) |
| 0xB3228 | -> `FUN_000619AC`, checksum **verify** |
| 0xB322C | -> `FUN_00061A48`, checksum **generate in place** |
| 0xB3230 | -> `FUN_00061AC4`, checksum **generate while copying** |

The device itself is reached through function pointers in RAM,
`(*0x7FAB70)(eepAddr, len, buf, &status)` for read and the pointer at
0x7FAB74 for write (`FUN_0005FCC8` / `FUN_00060524`). Nothing writes
0x7FAB6C-0x7FAB7C with a statically resolvable instruction, so the binding of
these pointers to the §2 primitives is **HYPOTHESIS** (it is the only SPI
EEPROM driver in the image, and the signature matches, but the assignment
was not observed).

### 3.2 Record layout

Derived from `FUN_0006131C`, `FUN_0005FB64` and `FUN_00061AC4`:

| Off | Type | Meaning |
|---|---|---|
| +0 | u16 | RAM mirror offset from 0x7F9E80; `0xFFFF` = no mirror |
| +2 | u16 | **EEPROM byte address of copy 0** |
| +4 | u16 | 0xFFFF in every record except block 7 (0x0000) — unused by the code paths read |
| +6 | u16 | offset into the flash default-value table at 0xB3238 |
| +8 | u16 | flags (bit2 = has mirror, bit5 = verify on read, bit6 = re-init from defaults on failure, bit7 = preload defaults, bits0-1 = the "ReplV" byte at payload offset len-3 is managed) |
| +10 | u8 | **block length in bytes, including the trailing 2-byte checksum** |
| +11 | u8 | 0 in every record |

`FUN_0005FB64(blk, copy)` returns
`eepAddr + copy * ceil(len / 0x20) * 0x20`, so **copy *n* starts on a page
boundary**.

### 3.3 The table

| blk | EEPROM | len | copies | span | mirror | flags |
|---:|---|---:|---:|---|---|---|
| 0 | 0x000 | 0x40 | 1 | 0x000-0x03F | — | 0xC000 |
| 1 | 0x040 | 0x20 | 2 | 0x040-0x07F | 0x7F9E80 | 0xC1A5 |
| 2 | 0x080 | 0x40 | 2 | 0x080-0x0FF | 0x7F9EA0 | 0xC1A5 |
| 3 | 0x100 | 0x20 | 1 | 0x100-0x11F | 0x7F9EE0 | 0xC1E4 |
| 4 | 0x120 | 0x20 | 1 | 0x120-0x13F | 0x7F9F00 | 0xC0F4 |
| 5 | 0x140 | 0x20 | 1 | 0x140-0x15F | 0x7F9F20 | 0x02F4 |
| 6 | 0x160 | 0x20 | 1 | 0x160-0x17F | 0x7F9F40 | 0x03F4 |
| 7 | 0x180 | 0x20 | 2 | 0x180-0x1BF | 0x7F9F60 | 0x01F5 |
| 8 | 0x1C0 | 0x20 | 2 | 0x1C0-0x1FF | 0x7F9F80 | 0x03F5 |
| 9 | 0x200 | 0x60 | 1 | 0x200-0x25F | 0x7F9FA0 | 0x03F4 |
| 10 | 0x260 | 0x20 | 1 | 0x260-0x27F | 0x7FA000 | 0x03F4 |
| 11 | 0x280 | 0x20 | 2 | 0x280-0x2BF | 0x7FA020 | 0x03F5 |
| 12 | 0x2C0 | 0x20 | 1 | 0x2C0-0x2DF | 0x7FA040 | 0x03F4 |
| 13 | 0x2E0 | 0x20 | 1 | 0x2E0-0x2FF | 0x7FA060 | 0x03F4 |
| 14 | 0x300 | 0x20 | 1 | 0x300-0x31F | 0x7FA080 | 0x03F4 |
| 15 | 0x320 | 0x20 | 1 | 0x320-0x33F | 0x7FA0A0 | 0x03F4 |
| 16 | 0x340 | 0x20 | 1 | 0x340-0x35F | 0x7FA0C0 | 0x03F4 |
| 17 | 0x360 | 0x20 | 1 | 0x360-0x37F | 0x7FA0E0 | 0x03F4 |
| 18 | 0x380 | 0x20 | 1 | 0x380-0x39F | 0x7FA100 | 0x03F4 |
| 19 | 0x3A0 | 0x20 | 1 | 0x3A0-0x3BF | 0x7FA120 | 0x03F4 |
| 20 | 0x3C0 | 0x20 | 1 | 0x3C0-0x3DF | 0x7FA140 | 0x03F4 |
| 21 | 0x3E0 | 0x20 | 1 | 0x3E0-0x3FF | 0x7FA160 | 0x03F4 |
| 22 | 0x400 | 0xFE | 2 | 0x400-0x5FF | 0x7FA180 | 0x03F5 |
| 23 | 0x600 | 0x20 | 1 | 0x600-0x61F | 0x7FA280 | 0x03F4 |
| 24 | 0x620 | 0xFF | 1 | 0x620-0x71F | 0x7FA2A0 | 0x03F4 |
| 25 | 0x720 | 0x20 | 1 | 0x720-0x73F | 0x7FA3A0 | 0x03F4 |
| 26 | 0x740 | 0x20 | 1 | 0x740-0x75F | 0x7FA3C0 | 0x03F4 |
| 27 | 0x760 | 0x20 | 1 | 0x760-0x77F | 0x7FA3E0 | 0x03F4 |
| 28 | 0x780 | 0x20 | 1 | 0x780-0x79F | 0x7FA400 | 0x03F4 |
| 29 | 0x7A0 | 0x20 | 1 | 0x7A0-0x7BF | 0x7FA420 | 0x03F4 |
| 30 | 0x7C0 | 0x20 | 1 | 0x7C0-0x7DF | 0x7FA440 | 0x03F4 |
| 31 | 0x7E0 | 0x20 | 1 | 0x7E0-0x7FF | 0x7FA460 | 0x03F4 |

* **The 2 KB device is fully allocated. 0x7E0 + 0x20 = 0x800 exactly.**
  There is no free block and no gap between blocks.
* Block 0 has no mirror and flag bit 2 clear, so the manager refuses every
  operation on it (`FUN_000619AC` also returns `true` unconditionally for
  block 0). This is the factory-data block the FR calls `BlockFD`
  (`re/findings/fr_index.md` §8).
* Block 11 at 0x280 with its duplicate at 0x2A0 is exactly the block the
  immobiliser code of §2 reads and writes directly. Two independent code
  paths therefore touch the same 32 bytes.
* Page padding that belongs to no block's payload: **4 bytes** in block 22
  (0x4FE-0x4FF and 0x5FE-0x5FF) and **1 byte** in block 24 (0x71F). They are
  outside every `len`, so the manager neither writes nor checksums them.

### 3.4 The checksum, exactly

`FUN_00061A48(blk, buf)` (generate in place) and `FUN_00061AC4(blk, src, dst)`
(generate while copying):

```c
u16 sum = 0;
for (i = 0; i < len - 2; i++) sum += (u8)buf[i];   /* 16-bit wrap */
*(u16 *)&buf[len - 2] = (u16)(-(sum + 1));          /* == ~sum */
```

`FUN_000619AC(blk, buf)` (verify) recomputes the same sum and returns

```c
return (u16)(sum + *(u16 *)&buf[len - 2]) == 0xFFFF;
```

So: **plain 16-bit sum of the payload, stored as its bitwise complement, big
endian, in the last two bytes of the block.** Block 0 is exempt. This is a
*different* algorithm from the flash block checksums in
`docs/02_memory_map.md` §6 (which sum 16-bit *words* and store both the sum
and its complement) — do not reuse `tools/checksum.py` for EEPROM blocks.

### 3.5 When blocks move

| Function | Role |
|---|---|
| `FUN_00062280` | walk all blocks, read each from the device, **verify**, fall back to the second copy / the flash defaults on failure. The start-up read (FR `EEPINIKW`). |
| `FUN_00062740` | walk all blocks, **generate** the checksum, write each copy to the device. The write-back. |
| `FUN_00060A68` | the request-queue state machine (`DAT_007FADAB`), states 0x20-0x27 |
| `FUN_0005FCC8` / `FUN_00060524` | per-block device read / write state machines (`DAT_007FADAC`), 2 retries, second copy on failure |
| `FUN_00061944` | thin wrapper that pumps `FUN_00060A68`; called from the **background task** at `FUN_001205A0` (external flash) and `FUN_004328E4` (on-chip flash) |

`FUN_0006131C` itself also pumps the queue inline (`while (DAT_007FADAB != 0x21) FUN_00060A68();`)
when `DAT_007FCD68 == 2`, i.e. it can run synchronously during shutdown.

`FUN_00062280` and `FUN_00062740` have no statically resolvable callers; like
the device primitives they are reached through pointers. The *exact* key-off
trigger is therefore **HYPOTHESIS**; that a write-all-blocks routine exists and
what it does is VERIFIED-STATIC.

---

## 4. Who uses which block

Recovered by resolving the constant `r3/r4/r5` at all 60+ `blrl` call sites of
`FUN_0006131C` (`tools/eeprom_map.py --clients`). Sites where the block index
is computed at run time (0x038C04, 0x038C44, 0x038C84, 0x038D20, 0x038DF0,
0x038E2C, 0x0A35F8, 0x12E4A4) are not attributed.

| blk | EEPROM | Payload bytes actually used | Notable clients |
|---|---|---|---|
| 1 | 0x040 | +8..+19, +22..+25 | 0x05C664, 0x05C8D8, 0x05CA88, 0x0A1B58-0x0A1D50 (KWP read), 0x11B934/0x11B98C/0x11B9E4. The 7-byte item at +13 is the best candidate for the VW coding word. |
| 2 | 0x080 | +5..+53 | 0x0A1DF8 (17 B @ +5), 0x0A1EA0 (14 B @ +0x16), 0x0A1F48 (17 B @ +0x24), 0x0A1FF0 — identification strings read over KWP |
| 3 | 0x100 | +2..+8 | 0x0A18B0 (4 B @ +2), 0x0A1958, 0x0A1A00, 0x0A1AA8 |
| 5 | 0x140 | +2..+17 | 0x1343F0 (11 B @ +2), 0x134430 (3 B @ +13), 0x134470 (2 B @ +16) |
| 6 | 0x160 | +2..+21 | 0x0FF95C, 0x0FF984, 0x11F134-0x11F284 and 0x245F10-0x246028 (eight u16 at +2,+4,…,+16) |
| 7 | 0x180 | +2..+8, +12..+13 | 0x0A274C, 0x0A284C, 0x0A287C (6 B @ +2, from KWP **SID 0x3B local id 0xBC**), 0x115994/0x115A58/0x115A8C, 0x134284/0x134314, 0x236150/0x236178 |
| 8 | 0x1C0 | ~~**only +14**~~ **+2..+18** (the 17 tester adaptation channels, channel *k* at +(k+1)) — *2026-09-24 (G7, #38): the constant-offset scan sees only +14 = channel 13; the channel paths load the block number from 0x0A3AD8 (§5 note of 2026-09-24)*; +19 = the ff_fuel E% store since G7 | 0x134380 (1 byte, +14); 0x12E4A4 `adaptation_restore_all` (read-back); 0x038C04 / 0x038C44 / 0x038C84 (KWP adaptation service 0x038708, sub-function 0x83 commit); 0x038D20 (read-back); 0x038DF0 / 0x038E2C (reset-all 0x038D64) |
| 10 | 0x260 | +2..+21 | 0x036DC4-0x036EDC (12 B @ +2, 4 B @ +14, 1 B @ +18, 1 B @ +19, 2 B @ +20) |
| 11 | 0x280 | +11..+13 | 0x0364B8, 0x036650, 0x036B34 (2 B @ +12), 0x0D1110 (1 B @ +11) — plus the direct SPI path of §2 |
| 12 | 0x2C0 | +4..+9, +19 | 0x120878, 0x1208A8, 0x121760, 0x121790, 0x1217C0, 0x125C7C, 0x12EA5C, 0x24B030 |
| 24 | 0x620 | **only +2**, plus a 61-byte access at a computed offset (0x11FC0C, 0x12F238) | 0x11FB74, 0x035378, 0x0353BC |
| 4, 9, 13-23, 25-31 | | **no constant block index resolved** — either unused in this dataset or only reached through the indirect sites above |

---

## 5. Spare space, and where to put one byte

**There is no spare block.** Every one of the 2048 bytes is inside a record's
span (§3.3). The only bytes outside every payload are the 5 page-padding bytes
at 0x4FE, 0x4FF, 0x5FE, 0x5FF and 0x71F; the manager cannot address them and
they are not covered by any checksum, so using them would mean a raw SPI write
and no integrity protection. **Do not use them.**

What *is* available is unused payload inside existing blocks. Two caveats
before reading the table below:

1. The last payload byte, offset `len-3`, is managed by the block manager
   itself when `flags & 3 != 0` (`FUN_0006131C` copies
   `buf[len-3]` around in cases 1 and 4). For a 0x20 block that is offset 29.
   Blocks with `flags & 3 == 0` (0x03F4, 0x02F4, 0xC0F4) do not use it.
2. "Unused" here means *no code path with a constant offset touches it in this
   firmware image*. It does **not** prove the factory leaves it at 0xFF; a
   bench read of a real ECU's EEPROM must confirm it before anything is
   written there.

| blk | EEPROM | flags&3 | free payload offsets | contiguous free |
|---|---|---|---|---|
| **8** | 0x1C0 (+ copy 0x1E0) | 1 | ~~+0..+13~~ ~~**+2..+13**, +15..+28~~ **+19..+28** (G7, 2026-09-24: +2..+18 are the adaptation channels) | ~~14~~ ~~**12** + 14 bytes~~ **10** bytes |
| 11 | 0x280 (+ copy 0x2A0) | 1 | +0..+10, +14..+28 | 11 + 15 bytes, but the immobiliser writes this block behind the manager's back — avoid |
| 12 | 0x2C0 | 0 | +0..+3, +10..+18, +20..+29 | 4 + 9 + 10 bytes |
| 3 | 0x100 | 0 | +0..+1, +9..+29 | 21 bytes |
| 7 | 0x180 (+ copy 0x1A0) | 1 | +0..+1, +9..+11, +14..+28 | 15 bytes, but this is the coding block a tester rewrites |
| **24** | 0x620 | 0 | +0..+1, +3..+252 | **252 bytes**, single copy, 255-byte block (8 pages per write) — **excluded (G7, 2026-09-24)**: the fault-clear service zeroes and commits it (G5, §7 item 4 note of 2026-09-24) |

> **2026-09-17 (E4, #38) — correction to this whole table.** Payload **+0 and
> +1 of every block are a {block id, version} stamp** that
> `nvm_read_all_blocks` validates against the flash default record; a block
> whose stamp is wrong is discarded and the mirror is reloaded from the
> defaults. They are **not free** in block 8 or in any other block whose
> default record starts with its own number (1, 2, 3, 5, 7, 8, 10, 11 at
> least). Derivation and the emulated proof: §10.5. The "Recommendation"
> below must therefore read **offset +2**, and `ff_persist_offset` in
> `patches/ff_fuel/ffcal001.json` — which ships as 0 — has to move.

> **2026-09-24 (G7, #38) — second correction: block 8 +2..+18 are the
> adaptation channels, and +19 is the byte that is really free.**
> F4's `adaptation_restore_all` 0x12E3F8 (`re/symbols.csv`) and the KWP
> adaptation service 0x038708 (`calibration_names.md` §10.1) address block 8
> with the block number and base index **loaded from 0x0A3AD8** (`08 02`), so
> `eeprom_map.py --clients`, which resolves immediates only, never saw them:
> channel *k* = 1..17 lives at payload **+(k+1)**, i.e. **+2..+18**, and the
> "one client at +14" of §4 is channel 13's slot. E4's "+2..+13" therefore put
> the ethanol store on **channel 1**. The table row for block 8 now reads
> **free: +19..+28** (10 bytes; +0/+1 stamp, +2..+18 channels, +29 ReplV,
> +30/+31 checksum). `ff_persist_offset` moves 2 → **19** (brief G7).
>
> **The exclusion set for +19..+28** (VERIFIED-STATIC unless stated; every
> command runs against `data/passat_azx_ori.bin`):
>
> | # | Who could write block 8 +19..+28 | Result | Evidence |
> |---|---|---|---|
> | a | the flash default record (what a stamp or checksum failure reloads) | `+19..+28 = 00` — the defaults write **0x00** there, not 0xFF, exactly as they write 0x00 at +2 (channel 1's default); the patch reads that as E0, the same behaviour as before the move | record at file **0xB32DC** (table entry +6 = 0x00A4 from 0xB3238): `08 01 00 80 80 80 80 00 00 80 00 80 80 FF` then 00 × 18; `eeprom_map.py` block row + `xxd -s 0xB32DC -l 0x20` |
> | b | every constant-offset call site of `nvm_block_request` | 87 sites; the only block-8 one is **0x134380, stage 1 B at +14** (channel 13) | `tools/eeprom_map.py data/passat_azx_ori.bin --clients` |
> | c1 | 0x038C04 — service 0x83 "commit all" (after a channel-0 reset) | stages each implemented channel's default at `n + 2` for **n = 0..16** (`cmpwi r11,0x11; blt` at 0x038C10) → +2..+18 | `blobdis.py --file-off 0x38B10 --addr 0x38B10 --len 0x120` |
> | c2 | 0x038C44 — the queued block-8 request that follows c1 | `(8, 2, 17, 0, buf 0, &0x8001D8)`: offset **2**, length **17** → +2..+18 | same listing |
> | c3 | 0x038C84 — service 0x83, one channel | `(8, ch + 1, 1, 0, &0x8001E6, &0x8001D8)` with ch = the byte at **0x8001E5**. Its only writer is 0x038740 (sub-function 0x81, `sda_xref.py --var 0x8001E5 0x8001E5`), which stores it *before* the `ch ≤ 0x11` test at 0x03875C — but the KWP caller at 0x439780 (id 0x103, frame 0xB9) enters state **0xB** (0x4398E0), the only state from which 0x82 (0x439A94) and then 0x83 (0x43A2EC, state 0xC) are reachable, **only when that 0x81 returned 2**; a refused channel leaves the state at 0xA. So ch ≤ 17 at every 0x83 → at most **+18** | `blobdis.py --file-off 0x235780 --addr 0x439780 --len 0x380`, `--file-off 0x236044 --addr 0x43A044 --len 0x2B0`; `sda_xref.py --var 0x7FB7D0 0x7FB7D0` (all 28 state writes) |
> | c4 | 0x038D20 — the function at 0x038CBC (counts channels that differ from their default) | mode **1** (read-back) at n + 2, n < 17 | `blobdis.py --file-off 0x38CBC --addr 0x38CBC --len 0xA8` |
> | c5 | 0x038DF0 / 0x038E2C — the function at 0x038D64, "restore every channel to its default and commit" | stages n + 2 for n < 17 (`cmplwi r31,0x11` at 0x038DF8), then `(8, 2, 17, …)` → +2..+18. **Called from 0x0D10D0** — see the note below | `blobdis.py --file-off 0x38D64 --addr 0x38D64 --len 0xD0`; `find_abs_refs.py … --target 0x038D64` |
> | c6 | 0x12E4A4 — `adaptation_restore_all` | mode 1 read-back of n + 2, n < 17 → +2..+18, into RAM, never into the mirror | `blobdis.py --file-off 0x12E3F8 --addr 0x12E3F8 --len 0xCC` |
> | c7 | 0x0A35F8 — the generic block walker | block index from 0x7FB834, but `r6 = 1` (**mode 1**, read) is a constant at 0x0A35F4 — it never writes | `blobdis.py --file-off 0xA3560 --addr 0xA3560 --len 0x200` |
> | c8 | 0x11FC0C / 0x12F238 | block **24** is an immediate at both; only the offset is computed | `eeprom_map.py --clients` |
> | d | `nvm_read_all_blocks` payload validation | one halfword compare of payload **+0** against the default record's +0 (`lhz r12,0(r29)` / `lhzx r11,…` / `cmpw` at 0x060458-0x06046C); no other payload byte is compared, so a non-default +19 is kept exactly as +2 was (§10.5 table; emulated again at +19 by `tests/test_ff_diag_patch.py`) | `blobdis.py --file-off 0x60420 --addr 0x60420 --len 0x70` |
> | e | the raw SPI writers behind the manager's back (§2) | `eeprom_write_byte` has one caller, inside `eeprom_write_bytes`; the 22 `eeprom_write_bytes` sites write **0x274 and 0x288-0x2B8** (blocks 10 and 11, immediate `li r3,…`), and 0x089508 is gated to 0x280/0x2A0 (0x0894E4/0x0894EC). Nothing addresses 0x1C0-0x1FF | `sda_xref.py --code 0x085A8C 0x085B54`; `blobdis.py` at 0x87700, 0x87F40, 0x880F0, 0x89480 |
> | f | a direct store into the block-8 mirror 0x7F9F80-0x7F9F9F | **0 store sites**, and no indexed store with a base up to 0x200 below it (the manager reaches the mirror through the base pointer at file 0xB3184) | `tools/store_xref.py data/passat_azx_ori.bin --control` (PASS) then `--window 0x7F9F80 0x7F9FA0 --near 0x200` |
>
> So **no stock path writes block 8 +19..+28** except the manager's own
> whole-record copies (commit, write-all, defaults reload), which carry the
> mirror byte through unchanged or replace it with the default 0x00.
> The caveat of note 2 above still applies: this is the firmware's view, not
> a read of a real ECU's EEPROM (bench step, `docs/08` S12 / step 3f).
>
> *Tangent — SETTLED (2026-09-24, H2, #47, §11).* **0x0D1068** drives the
> fault-clear state machine
> 0x035300 (`kwp14_clear_state` 0x7FB718, which also commits block 24, G5's
> §7 item 4 note of 2026-09-24) when 0x7FEB59 is set and bit 0 of block 11
> payload +11 (mirror 0x7FA02B) is set, and when that finishes it calls
> 0x038D64 (c5) — i.e. **a stock, tester-free path also resets all 17
> channels**, then clears the bit and stages block 11 +11 (0x0D1110).
> Call and range VERIFIED-STATIC (`blobdis.py --file-off 0xD1068 --addr
> 0xD1068 --len 0xD8`); at +19 the E% survives it; at +2 it did not.
> **Who sets the bit (was HYPOTHESIS/open) is now VERIFIED-STATIC: the
> routine-0xC5 progress handler 0x087494 (SID 33 C5, programming stack),
> raw-writing EEPROM 0x28B/0x2AB with bit 0 set — §11.1.** So it is a
> reflash-with-component-protection consequence, not a bare download one; a
> plain tester `14 FF 00` cannot reach 0x038D64 (§11.3). The whole path is
> emulated end-to-end in `tests/test_adaptation_reset_path.py` (§11.4).
>
> **Emulated (VERIFIED-DYNAMIC, emulated; `tests/test_ff_diag_patch.py::
> TestPersistOffsetOffTheChannels` and `::TestPersistenceThroughTheDeviceAt19`,
> stock code on the §10 device model):**
>
> * `adaptation_restore_all` (entry 0x12E3F4) over a block carrying E% 85 at
>   +19 leaves every channel cell 0x7FD062-0x7FD06D at its default and
>   0x7FD06B at 0; the whole-SRAM difference against the default block is the
>   mirror byte 0x7F9F93 and the checksum. At +2 the loop copies the E% into
>   0x7FD06B.
> * the reset-all routine 0x038D64 + the pump: both device copies carry the
>   channel defaults at +2..+18, a valid checksum and **+19 unchanged**; with
>   the E% at +2 the stored 85 becomes **0**. The handle record reads
>   `{blk 8, off 2, len 0x11, case 8, status 2}` — case 8 writes the **whole**
>   record from the mirror (ReplV +29 moves, checksum regenerated). After it a
>   power cut and a fresh start-up read hand the E% back to the patch.
> * **the tester path does less than F4's §10.1 wording suggests on this
>   dataset.** The service tests channel bit (ch − 1) against three access
>   words, 0x5CF004 / 0x5CF008 / 0x5CF00C (0x5CF00C and 0x5CF008 only together
>   with the state byte 0x7FB745 = 2 / 1), and all three are **0x00000040** —
>   channel 7 only (`xxd -s 0x1CF004 -l 12`). So 0x81 on channel 1 (and on 2,
>   4, 8, 10, 13) is refused with **0x33**, the state machine at 0x439780
>   never reaches 0x82/0x83 for it, and a channel-0 reset (0x81 ch 0, 0x82,
>   0x83) **changes no channel at all** — the reset loops at 0x038848 and
>   0x038B24 skip every channel whose bit is clear while any access word is
>   non-zero, and skip n = 6 (channel 7) explicitly — though the 0x83 still
>   commits the block. With the three words opened to 0xFFFFFFFF in the
>   emulator (a dataset whose tester may reset every channel) the same
>   sequence zeroes +2 and spares +19. So on this dataset the brief's three
>   tester exposures of the E% at +2 (channel-0 reset, a channel-1 write, a
>   channel-1 read) do **not** occur; the one real exposure was the stock
>   reset-all path above. The move to +19 closes both.

### Recommendation for a one-byte ethanol store

**Block 8, payload offset ~~+0~~ ~~+2~~ +19, one byte.** Reasons:

> *2026-09-24 (G7, #38): **+19**, not +2. +2..+18 are the 17 tester
> adaptation channels (note of 2026-09-24 above); +2 is channel 1, which the
> stock reset-all path 0x038D64 (and, on a dataset whose access words admit
> channel 1, a workshop channel-0 reset + commit)
> overwrites with its default 0. +19 is the lowest byte of the exclusion set
> above; `ff_persist_offset` = 19 in `patches/ff_fuel/ffcal001.json`. The
> bullet "only one stock client" below is wrong in the same way: one
> constant-offset client, plus the adaptation-channel paths.*

* Block 8 is **duplicated** (copies at 0x1C0 and 0x1E0), so the manager's own
  fallback-to-second-copy logic protects the value for free.
* It is a 32-byte block = **one page**, so a commit is a single page write
  (~5 ms) — cheap enough to do at key-off, or even periodically.
* Only one stock client uses it (1 byte at +14), so a mistake there cannot
  corrupt anything the engine depends on.
* `flags = 0x03F5`: bit 2 (mirror) set, bit 5 (verify on read) set, bits 0-1
  = 1 so the manager keeps its own byte at +29 — leave +29 alone.

Write path, using only stock code:

```c
u8 e_pct = <0..100>;                       /* or 0..255 for 0.5 % resolution */
FUN_0006131C(8, 19, 1, 0, &e_pct, 0);      /* stage into mirror 0x7F9F93 (G7) */
FUN_0006131C(8, 0, 0, 0, 0, &handle);      /* commit: checksum + both copies */
/* poll handle until != 0 */
```

Read path at start-up: the block is already in the mirror after
`FUN_00062280`, so `FUN_0006131C(8, 19, 1, 1, &dst, 0)` returns it (mode 1,
§8.1), or read **0x7F9F93** directly (G7, 2026-09-24; the offsets 0 in this
block were D2's, see §10.5).

**Checksum implications: none that the patch has to handle.** The commit path
calls `FUN_00061A48`/`FUN_00061AC4`, which recomputes the 16-bit sum over
bytes +0..+29 and rewrites the complement at +30..+31 before the device write.
A patch must *not* write the EEPROM through the raw SPI primitives of §2, or it
would have to replicate that itself and would race the manager's mirror.

~~Fallback if block 8 turns out to be occupied on a real ECU: **block 24 offset
+3**, which has 250 unused payload bytes, at the cost of an 8-page (~40 ms)
write per commit — acceptable at key-off, not in a cyclic task.~~

> *2026-09-24 (G7, #38): **block 24 is not a fallback.** G5 found
> (§7 item 4, note of 2026-09-24) that `kwp_sid_14_h1`
> (clearDiagnosticInformation) commits block 24 — `nvm_block_request(0x18, …)`
> at 0x3536C/0x353B0 — after zeroing a 251-byte stack buffer, so block 24 is
> fault-memory family and a DTC clear would wipe anything stored there. If
> block 8 +19..+28 turns out to be occupied on a real ECU, move within
> +19..+28 first (`--set ff_persist_offset=20`), and otherwise use the
> external-SRAM route below.*

Second fallback, and the one to prefer during bench development: keep the byte
in the external SRAM (§6) and only mirror it to the EEPROM at key-off.

---

## 6. Is the external SRAM battery-backed?

`docs/02_memory_map.md` §3: external SRAM 32 KB at 0x800000 on CS1, BR1 =
0x800403 (8-bit port), OR1 mask 0xFFFC0000 (a 256 KB window, so the device is
aliased). The brief asks whether 0x800000-0x807FFF is cleared at every start.

**Evidence that it is not, and that the firmware expects retention:**

1. **The boot-time sizing probe preserves the memory it tests.**
   `FUN_00011898`, file 0x011898-0x011918, reached from the memory-controller
   init at file 0x012130:

   ```
   r9 = 0x800000
   r8 = lwz 0(r9)                 ; SAVE the word at 0x800000
        stw 0x5AA53CC3 -> 0(r9)
        if (lwz 0(r9) != 0x5AA53CC3) -> not present, restore r8, reprogram BR1
   r3 = lwzx r9,0x8000            ; SAVE the word at 0x808000
        stwx 0xA55AC33C -> 0x808000
        if (0x800000 still 0x5AA53CC3 && 0x808000 == 0xA55AC33C)
              RAM 0x7F8012 = 0x44          ; >= 64 KB, no aliasing
        else  RAM 0x7F8012 = 0x41          ; 32 KB (0x808000 aliases 0x800000)
        stwx r3 -> 0x808000                ; RESTORE
        stw  r8 -> 0(r9)                   ; RESTORE
   ```

   A routine that carefully restores two words it only needed for a presence
   test is a routine written for memory whose contents matter across a reset.
   (It also means the **SRAM size is detected at run time** and reported in
   RAM byte 0x7F8012 — 0x41 = 32 KB, 0x44 = 64 KB. Which one our hardware
   reports cannot be decided from the dump.)

2. **Neither application start-up clears it.** `FUN_0009E3B4` (file 0x09E3B4)
   is the C run-time start-up: it zeroes 0x7F802C-0x7F807B and
   0x7F8080-0x7F80E7 — both **internal** SRAM — and then runs the `.data`
   copy loop with `src = dst = 0x800000`, i.e. **zero bytes**, so the external
   SRAM has no initialised-data image at all. File 0x08A1AC repeats the same
   empty copy in the second start-up copy.

3. The only large write into external SRAM found is `FUN_0008A12C`, which
   copies flash 0x081A00-0x085887 to **0x804800** — and its callers are
   0x086A28, 0x087494 and 0x088828, all KWP/flash-programming entry points,
   not the cold start. So 0x804800-0x808687 is scratch **during a programming
   session only**.

> **2026-09-16 (C2, issue #23) — point 2 and the verdict below are REFUTED.
> VERIFIED-STATIC.** The `.data` copy really is empty, but the `.bss` fill for
> the external SRAM is done by a *different* routine: `ram_clear_block`
> (**0x06D8F8**, called only from 0x04CD84 inside `app_init` 0x04CCD4, which
> `app_entry_crt0` reaches) zeroes **0x800004-0x800D07, 0x800D08-0x80361F and
> 0x803620-0x80498F** in three `stwu`/`bdnz` loops, plus seven ranges of the
> internal SRAM. Of the external SRAM only **0x800000-0x800003** (the word
> this probe saves and restores) and **0x804990-0x807FFF** survive a reset,
> and all of the latter is the destination of the KWP programming copy
> (`FUN_0008A12C`, flash 0x081A00-0x085887 -> 0x804800-0x808688).
> **So the external SRAM is ordinary `.bss` and cannot carry flex-fuel state
> across a key cycle.** The "second fallback" of section 5 — "keep the byte in
> the external SRAM and only mirror it to the EEPROM at key-off" — is not
> available and must not be used; the block-8 proposal is unaffected.
> Full derivation, the list of all fourteen fill ranges and the patch-RAM
> consequences: `re/findings/ram.md` sections 3 and 3.1.
> Reproduce: `python3 tools/ram_survey.py data/passat_azx_ori.bin` (the
> "cold-start constant fills" block) and
> `python3 tools/callgraph.py data/passat_azx_ori.bin --callers 0x06D8F8`.

**Verdict:** VERIFIED-STATIC that the firmware never clears 0x800000-0x807FFF
on a normal start and takes care to preserve it while probing;
**HYPOTHESIS** that the SRAM is on a permanent (KL30) supply, because that is
an electrical fact the dump cannot settle. It must be measured on the bench
before any flex-fuel state is trusted to it across a key cycle. If it is
*not* backed, the probe's care is simply defensive coding and nothing is lost.

A note on the "RAM init table at 0x5C2E78" the brief points at: the words
there are

```
0x5C2E78: 00800000 00800000      0x5C2E80: 00800004 00807FF8
0x5C2E88: 007FE588 007FEFDC
```

preceded by three more start/end pair lists at 0x5C2E14, 0x5C2E24 and
0x5C2E50 that name **internal** RAM ranges (0x7F8104-0x7F8233,
0x7F8104-0x7F8368, 0x7FAAD0-0x7FE0BC, 0x7FF770-0x7FFFEC, 0x7F8490-0x7FAAC0)
and the patterns 0xAAAAAAAA / 0x55555555 — an `URRAM`-style memory-test
descriptor set. **No instruction in the image references 0x5C2E78**, by
absolute `lis/addi`, by r2-relative displacement (with either
r2 = 0x5C9FF0 or r2 = 0x017FF0) or by a stored 32-bit pointer
(`tools/find_abs_refs.py --range`, an SDA scan, and a byte search for
`005C2E78`/`001C2E78` all come back empty). So the pair
`{0x800004, 0x807FF8}` is **not** evidence that the external SRAM is cleared
or tested at start-up; treat the table as data whose consumer has not been
found. This is a **correction to the reading implied in
`docs/02_memory_map.md` §3**, which cites it as "RAM init table … gives
0x800000..0x807FF8".

---

## 7. Open questions

1. Which concrete driver is bound to the device function pointers at
   0x7FAB70/0x7FAB74. Needs either a dynamic trace or a careful look at the
   module that fills the structure they live in.
   *2026-09-17 (E4, #38): **NARROWED**, not settled — §10.3. The signature is
   now VERIFIED-STATIC (`rc = (*fp)(eepAddr, len, buf, statusPtr)`, non-zero
   rc = started, `*statusPtr = 1` = success), and the fact that the pointers
   are 0 in the emulator is exactly what brief D2 saw as the halt spin.*
   *2026-09-22 (F3, #38): **still open, and now bounded** — §10.7. It is not
   two cells but a seven-pointer descriptor at 0x7FAB58-0x7FAB7B that nothing
   in the image writes: no store of any width or addressing mode reaches it
   (30,239 resolvable SRAM stores, the highest in the page is 0x7FAB55), no
   copy loop starts within reach, no flash word hands its address to anyone,
   nothing branches into the missing 16 KB, and no four-argument function of
   the required shape exists. Read the question from now on as **which
   variant module fills the descriptor**, the way `FUN_00017E14` fills the
   boot's own three device pointers in §1.5.*
2. The PCS encoding used by the **boot** driver (command bytes 0x20/0x80,
   PCS field = 0b0000, i.e. all four chip selects driven low) contradicts the
   application driver's clean PCS0-only encoding (0x0E/0x8E). Either the boot
   hardware variant wires only PCS0, or a variant table other than the last
   `else` branch applies. Does not affect the proposal, which uses the
   application path.
3. Whether the factory really leaves block 8 offsets +0..+13 at 0xFF. Read a
   real EEPROM on the bench (see §5 caveat 2).
4. The exact identity of blocks 4, 9, 13-23, 25-31 — plausibly the fault-path
   (`DFPMEEP`) and IUMPR blocks the FR lists, reached only through the
   run-time-indexed call sites.
   *2026-09-24 (G5, #22 prep): **still open — named, not reversed.** The
   simulator's DTC read-back does not need them: `18`/`17`/`14` read the RAM
   fault memory at 0x7F8890 (20 × 0x5C, `kwp.md` §12.7), which the
   rehearsal seeds as a labelled model. One adjacent fact, VERIFIED-STATIC:
   `kwp_sid_14_h1` (clearDiagnosticInformation) commits **block 24** —
   `nvm_block_request(0x18, …)` at 0x3536C/0x353B0, the handle record reads
   block 0x18 done in the emulator — so block 24 belongs to the same
   fault-memory family although it is not in this list. It is the largest
   block — length 0xFF, EEPROM 0x620-0x71F (eight pages), mirror 0x7FA2A0,
   inside the upload-protected window (`tools/eeprom_map.py`) — and the
   handler zeroes a 251-byte stack buffer before its first call
   (0x35338-0x35350). What the payload holds is not established.*
5. Whether the external SRAM is 32 KB or 64 KB on our hardware (RAM 0x7F8012
   at run time answers it). *2026-09-16 (C2, #23): the probe is now emulated
   under both models — `python3 -m emu.ext_sram_probe` gives 0x44 with plain
   RAM and 0x41 when 0x808000 is folded onto 0x800000 — so the byte's meaning
   is VERIFIED-DYNAMIC; only the hardware answer is still open.
   `logging/sessions/ram_snapshot.json` asks C3's logger to read it first.*

---

## 8. The request record and the result codes (D2, 2026-09-16, #38)

Section 3.1 gives the call shapes but calls the sixth argument a "handle" and
says only "poll it". It is not a word — it is a **9-byte request record that
the manager keeps a pointer to**, so it must outlive the call. Everything
below is VERIFIED-STATIC from the disassembly of `nvm_block_request`
(0x06131C) and the queue pump `nvm_queue_pump` (0x060A68).

### 8.1 How the call shape is chosen

`nvm_block_request` does **not** switch on `mode` alone. At 0x06136C-0x0613AC
it builds a table index out of four booleans and reads one byte from a
16-entry table at **0x06199C**:

```
idx  = 8*(len != 0) + 4*(handle != 0) + 2*(buf != 0) + (mode & 1)
byte = *(u8 *)(0x06199C + idx)          ; 0 -> return 0x81 (illegal shape)
case = byte & 0x0F                      ; jump table at 0x06146C, 11 entries
```

| idx | len | handle | buf | mode | byte | case | target | meaning |
|---:|---|---|---|---|---|---|---|---|
| 2 | 0 | 0 | ptr | 0 | 0x51 | 1 | 0x06174C | checksum the caller's buffer into the mirror |
| 3 | 0 | 0 | ptr | 1 | 0x62 | 2 | 0x061840 | copy the **whole** block out of the mirror |
| 4 | 0 | ptr | 0 | 0 | 0x43 | 3 | 0x061498 | **commit**: queue "checksum + write all copies" |
| **10** | >0 | 0 | ptr | 0 | 0x46 | 6 | 0x0616C4 | **stage**: copy `len` bytes into the mirror at `off` |
| **11** | >0 | 0 | ptr | 1 | 0x67 | 7 | 0x0617E0 | **read back**: copy `len` bytes out of the mirror at `off` |
| 12 | >0 | ptr | 0 | 0 | 0x48 | 8 | 0x061498 | queued read of `len` bytes from the device |
| 14/15 | >0 | ptr | ptr | 0/1 | 0x09/0x0A | 9/10 | 0x061498 | queued read/write of `len` bytes |
| 0,1,5,8,9,13 | | | | | 0x00 | — | — | **rejected, returns 0x81** |

**Correction to the read-back call in brief D2's own text and in §5**: reading
one byte back is `nvm_block_request(8, off, 1, **1**, &dst, 0)` — *mode 1*.
With mode 0 the same arguments are the **stage** shape and would overwrite the
mirror with whatever `dst` happened to contain. The two differ only in that
one bit.

Cases 6, 7, 1, 2 are synchronous: they run inside the manager's own nesting
critical section (`mtspr 81` / `mtspr 80` around a depth counter at 0x7FCDB4
and the saved MSR[EE] at 0x7FCDB8) and return **2** immediately. Only the
`handle != 0` shapes are queued.

### 8.2 The request record, 9 bytes

The queued path at 0x0615C4 fills the caller's record and enqueues *the
pointer*:

```
+0  u32  buf        (the caller's buffer; 0 for a commit)
+4  u8   blk
+5  u8   off
+6  u8   len
+7  u8   case       (the low nibble above: 3 = commit)
+8  u8   status     <- 1 as soon as it is queued
```

The queue is 4 slots of u32 at **0x7FC434** with a write index at
**0x7FCC61** and a read index at **0x7FCC60**, both masked with
`queue_len - 1` (queue length 4, from 0xB3195). If the slot is already taken
the call returns **0x40** and nothing is queued. So:

* **the record must not be a stack temporary** — the pump dereferences it
  milliseconds later. A patch keeps it in its own RAM block.
* one request at a time per record: re-using a record whose `status` is still
  1 would corrupt the in-flight request.

### 8.3 Result codes

`nvm_state_complete` (0x0612B4, the pump's state 0x25) clears the queue slot,
advances the read index and copies the byte at **0x7FCC95** into
`record+8`. The four writers of 0x7FCC95 give the whole code set:

| value | written at | meaning |
|---|---|---|
| 1 | 0x061640 (the enqueue itself) | queued, still in flight |
| **2** | 0x06115C, 0x061284 | **finished successfully** |
| 0x80 | 0x0611D4, 0x06129C | device read/write failed |
| 0x82 | 0x060C60, 0x060F38 | checksum/verify failed |

and `nvm_block_request` itself returns 2 (synchronous shape done), 1 (queued),
0x40 (queue full), 0x81 (illegal shape / no mirror) or 0x83 (blocked by the
0x7F9E3C flag).

**So a patch polls `record+8`: 1 = busy, 2 = done, anything else = failed.**

### 8.4 Who pumps the queue

`FUN_00061944` pumps `nvm_queue_pump` and is called from the background task
of *both* task sets — `FUN_001205A0` (set B) and `FUN_004328E4` (set A), which
are exactly the two tasks `patches/ff_fuel` hooks. A commit issued from the
flex-fuel 10 ms tick therefore completes on its own, in the same task, a few
activations later; the patch never has to pump anything.

Reproduce:

```bash
python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x6131C --addr 0x6131C --len 0x280
python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x612B4 --addr 0x612B4 --len 0x70
python3 tools/find_abs_refs.py data/passat_azx_ori.bin --target 0x7FCC95
```

---

## 9. Where the stock code commits, and why there is nothing to piggyback on
### (D2, 2026-09-16, #38 — a time-boxed negative result)

Brief D2 was asked to find the key-off write-back and hook it "only if the
piggyback is trivial". It is not trivial, and for block 8 it does not exist.
What the search established, all VERIFIED-STATIC:

**1. Block 8 has exactly one stock client, and it never commits.**
*(2026-09-24, G7, #38 — corrected in place, after F4's note in
`calibration_names.md` §10.1: exactly one **constant-offset** client. The KWP
adaptation service 0x038708 **does commit block 8**, in sub-function 0x83 —
0x038C44 `(8, 2, 17, …, &0x8001D8)` after a channel-0 reset and 0x038C84
`(8, ch + 1, 1, 0, &0x8001E6, &0x8001D8)` for one channel — and so does the
reset-all routine 0x038D64 (0x038E2C), called from 0x0D10D0 after the
fault-clear state machine. Those commits write the **whole mirror**, so a
byte the patch has staged goes to the device with them; nothing in them
writes past +18. §5, note of 2026-09-24, has the exclusion set. What follows
holds for the constant-offset sites.)*
`tools/eeprom_map.py data/passat_azx_ori.bin --clients` resolves all 87 call
sites of `nvm_block_request`. Exactly one names block 8:

```
blk off len mode   site
  8 0xE   1    0   cpu 0x134380        <- a STAGE of one byte at payload +14
```

There is **no commit site (`len = 0`, `handle != 0`) for block 8 anywhere in
the image**. The twelve commit-shaped sites belong to blocks 24 (0x035378,
0x0353BC), 10 (0x036DC4, 0x036DEC, 0x036EDC), 7 (0x0A28AC, 0x115994,
0x115A58, 0x115A8C) and 6 (0x0FF9AC, 0x44A05C, 0x44A0B0). So the stock byte at
+14 reaches the device only through the *write-all-blocks* routine — and so
would ours, if we waited for the stock code. **A patch that wants its value in
the EEPROM has to commit block 8 itself**, which is what
`patches/ff_fuel/src/ff_diag.c` does.

**2. The write-all-blocks routine has no resolvable trigger.**
`tools/find_branch_refs.py data/passat_azx_ori.bin 0x062740 0x062280` finds
**no branch and no stored pointer** to either the write-back (0x62740) or the
start-up read (0x62280) — §3.5 already said so, and it still holds after a
second pass. They are reached through a structure the static scan cannot
resolve.

**3. The synchronous "shutdown" mode is real but equally unreachable.**
`nvm_block_request` pumps the queue inline (`while (0x7FADAB != 0x21)
nvm_queue_pump()`) when the halfword at **0x7FCD68** is 2, i.e. a commit
becomes synchronous — exactly what a shutdown path needs. That halfword is
written by two one-line setters, `nvm_set_sync_mode` **0x0BA0F4** (writes 2)
and `nvm_set_normal_mode` **0x0BA104** (writes 1), and **neither has a
caller**: they appear only as two entries of the function-pointer table at
0x0B1AB0 / 0x0B1AC8, and `find_abs_refs.py --range 0x0B1A00 0x0B1B00` finds
nothing that references that table either. The existence of the mode is
VERIFIED-STATIC; its trigger stays **HYPOTHESIS**.

**4. `engine_not_running` (0x7FEAD0) leads somewhere else.**
Twelve sites read or write it (`tools/sda_xref.py --var 0x7FEAD0 0x7FEAD0`).
The two nearest are the functions on either side of ff_fuel's set-A hook:
0x0BD9E8 and 0x0BDA64 both gate on `0x7FEAD0 != 0`, take a one-shot latch at
0x7FC1D8, and call 0x475E8C with pointers loaded from 0x487678 / 0x4876C0.
They look like afterrun event dispatchers and they touch no EEPROM function.
**Not followed further — time-boxed here.**

**5. The queue is pumped from the two tasks we already hook.**
`FUN_00061944` (the pump wrapper) is called from exactly two sites,
**0x120694** inside task set B's 0x1205A0 and **0x432BB4** inside task set A's
0x4328E4 (`find_branch_refs.py ... 0x061944`). Both are the 10 ms rasters
`patches/ff_fuel` hooks, so a commit the flex-fuel tick queues is drained by
the same task a few activations later without the patch pumping anything.

**Consequence for #38.** No key-off hook is added. The ethanol estimate is
written *while the engine runs*, rate-limited to one page write per minute, so
the value that survives a power cut is at most one minute and one hysteresis
step old — which is what the requirement asks for, and it does not depend on
an orderly shutdown at all. A future brief that wants a true key-off flush
should start at the function-pointer table 0x0B1A80+ and find its consumer.

---

## 10. The device model, and what actually blocked the EEPROM in the emulator
### (E4, 2026-09-17, #38 — closes the D2 limit)

Brief D2 could stage a block and queue a commit but never saw the request
record leave status 1: `nvm_queue_pump` reached state 0x23, the write state
machine set **0x53**, and the next instruction "walked into the OS halt spin
at 0x110F0". D2 read that as *no device answers*. It is not.

Reproduce everything below with

```bash
./.venv/bin/python3 -m emu.qspi_eeprom --self-test
./.venv/bin/python3 -m emu.qspi_eeprom --blank work/eeprom.bin --factory
./.venv/bin/python3 -m unittest tests.test_qspi_eeprom
```

### 10.1 Corrections to sections 1-3 and 9

| Claim | Correction | Tag |
|---|---|---|
| §3.5/§9: the start-up read is `FUN_00062280`, the write-back `FUN_00062740` | **They start at 0x06227C and 0x06273C.** Both functions open with `mr r11,r1` (the frame base the `_savegpr` helper at 0xB8224-0xB8244 stores through) and only then `stwu r1,-0x20(r1)`. Calling 0x62280 skips that and the helper writes through r11 = 0, which faults at once. The manager's own pointer table agrees: the word at file **0xB3224 is 0x0006273C**, not 0x62740 | VERIFIED-STATIC: `blobdis.py --file-off 0x62268 --addr 0x62268 --len 0x20` |
| §1.2: "the QSPI RAMs are 32 entries" | confirmed again from `eeprom_qspi_xfer`: TXRAM is formed as `0x705000+0x17E` with `sthu 2(r30)`, CMDRAM as `0x705000+0x1BF` with `stbu 1(r29)`, RXRAM as `0x705000+0x13E` with `lhzu 2(r30)` (file 0x085964-0x085988, 0x085A50-0x085A58) | VERIFIED-STATIC |
| §3.5: "Nothing writes 0x7FAB6C-0x7FAB7C with a statically resolvable instruction" | still true, and it is the whole of D2's blocker — see §10.3 | VERIFIED-STATIC |

Register facts the device model rests on, all from `eeprom_qspi_xfer`
(0x085920) and the two boot self-tests (all **VERIFIED-STATIC**):

| Address | Register | Evidence |
|---|---|---|
| 0x70501A | SPCR1, **SPE = 0x8000** | `lhz r10,0x1a(r31); ori r10,r10,0x8000; sth` at 0x0859C4-0x0859CC |
| 0x70501C | SPCR2, **ENDQP = bits 8-12**, NEWQP = bits 0-4 | `rlwinm r11,r11,0,0x18,0x12` then `((n-1)&0x1F)<<8`, 0x085998-0x0859B4 |
| 0x70501E | SPCR3, **LOOPQ = 0x04** | `lbz r11,0x1e(r7); ori r11,r11,4; stb` at 0x017D6C-0x017D78 |
| 0x70501F | SPSR, **SPIF = 0x80** | `rlwinm r12,r12,0x19,0x1f,0x1f` keeps bit 24 of the word, 0x0859E4-0x0859EC |
| — | **the hardware clears SPE when the queue ends** | 0x017AB8-0x017AC0 spins while SPE is set, i.e. it is the busy flag, and 0x017AC4 clears it defensively |
| — | the last queue entry's CONT does not keep PCS asserted | `eeprom_write_byte` ends its 5-entry queue with command **0x8E** (0x085ADC-0x085AE4) and the device must still see the WRITE complete |

`eeprom_write_byte`'s exact queue, from file 0x085A9C-0x085AE4, settles the
CONT question §2 left open: `{0x0006,0x0E}` WREN **without** CONT (a one-byte
instruction), then `{0x0002,0x8E}` `{addr>>8,0x8E}` `{addr&0xFF,0x8E}`
`{data,0x8E}` — four entries with CONT set, so the page write is one chip
select that is released by the *end of the queue*. The WIP poll is a separate
two-entry queue `{0x0005,0x8E} {0x0000,0x8E}` re-run until `rx[1] & 1 == 0`
(0x085AFC-0x085B38).

### 10.2 `emu/qspi_eeprom.py`, and the firmware's own proof

`M95160` is a byte-level device (WREN/WRDI/RDSR/WRSR/READ/WRITE, WEL and WIP,
16-bit address, 32-byte pages that wrap **inside** the page); `QspiEeprom` is
the queue engine. The proof that the pair behaves like the part is the
firmware, not a hand-written expectation:

| Firmware routine | Result |
|---|---|
| `FUN_00017CF0` QSPI loopback self-test | returns **1**, error byte 0 |
| `FUN_00017A84` EEPROM WEL self-test | returns **1** (WEL clear after WRDI, set after WREN) |
| `eeprom_write_byte` 0x085A8C | the byte appears in the device array |
| `eeprom_read_bytes` 0x085BC0, 0x40 B | three queues of ≤ 0x1D bytes, exact round trip |
| `eeprom_write_bytes` 0x085B54 | 4 B written byte at a time |

VERIFIED-DYNAMIC (emulated), `tests/test_qspi_eeprom.py`.

One emulator detail that is not a fact about the ECU: Unicorn calls a write
hook **before** it performs the store, so the model cannot clear SPE inside
the hook that saw SPE set. It arms itself there and runs the queue on the
next QSPI access, which in both drivers is the `lbz SPSR` poll that follows
immediately (0x017994, 0x0859E4).

### 10.3 What really blocked D2: two unbound function pointers

The block manager reaches the device only through
`(*0x7FAB70)(eepAddr, len, buf, statusPtr)` at **0x05FD88** and
`(*0x7FAB74)(...)` at **0x06068C**. Both words are BSS, no instruction in the
image stores to them (§7 Q1, re-checked with `find_abs_refs.py --range
0x7FAB40 0x7FAB90` and `sda_xref.py`), so in the emulator they are **0** and
the manager's `blrl` branches to address 0 — the reset vector, which runs the
boot code and ends in the OS halt spin at 0x110F0. The state byte 0x53 D2
saw is written at **0x060664**, four instructions before that `blrl`. So:

> **The EEPROM was never "not answering". The manager was calling a null
> pointer.** A QSPI device model alone would not have helped. VERIFIED-STATIC
> for the call sites, VERIFIED-DYNAMIC for the failure mode.

The *signature* the two call sites require is VERIFIED-STATIC and is what
`NvmDeviceBinding` implements with two sixteen-instruction trampolines onto
the real §2 primitives:

* the return value is checked at 0x05FDBC: **non-zero = transfer started**
  (go to state 0x44 and wait); zero is the failure path;
* the driver later writes **1** into `*statusPtr` for success — 0x05FE84
  compares the byte at 0x7FCC79 against 1, and *any* other non-zero value is
  a failure; 0 means "still in flight" and the state machine simply returns.

Which driver the factory software installs there is still **open**. What is
now settled is that it is a four-argument, asynchronous-completion wrapper,
not `eeprom_read_bytes` itself (three arguments, returns a count).

### 10.4 Two sub-states no start-up code seeds

`nvm_dev_block_read` (0x05FCC8) and `nvm_dev_block_write` (0x060524) dispatch
on **0x7FADAC** and **0x7FADAD**; the entry states are 0x40 and 0x50. Each
routine resets its own byte to that value only **after** it finishes a block
(`li r9,0x40; stb r9,0(r25)` at 0x060510, `li r9,0x50` at 0x0609D4). On a
cold emulator, where all RAM is zero, the first call therefore falls into the
default branch and reports "failed" — the first mirrored block of the
start-up read is silently skipped and the first commit fails once. No
statically resolvable instruction writes 0x40/0x50 there, so how the real ECU
arms them is **open**; `emu.qspi_eeprom.cold_start()` seeds both and says so.

`nvm_read_all_blocks` is also the manager's **initialiser**: its final state
(0x062534) sets bit 1 of the flag halfword at 0x7F9E3C and parks the request
queue at **0x7FADAB = 0x20**. Until it has run, `nvm_block_request` queues
work that nothing will ever pump — which is why `tests/test_ff_diag_patch.py`
has to write 0x20 into 0x7FADAB by hand. VERIFIED-STATIC + VERIFIED-DYNAMIC.

### 10.5 **A block's payload starts with a {block id, version} stamp**

This is the finding that matters for issue #38.

`nvm_read_all_blocks`, after both copies of a block have been read and their
checksums verified and found equal, compares the **first halfword of the
block payload** against the first halfword of that block's record in the
flash default table (0x060458-0x060470: `lhz r12,0(r29)` on the read buffer
against `lhzx r11, *(0xB318C), table[blk].dflt`). A mismatch sets the return
code to 0x80 and the caller re-initialises the mirror **from the defaults**.

The default records make the meaning plain — every one of them starts with
its own block number and a version byte:

```
blk 1  01 03 02 04 ...      blk 5  05 03 'HARDWAREXAB00000'
blk 2  02 04 03 04 ...      blk 8  08 01 00 80 80 80 80 00 00 80 00 80 80 ff ...
blk 3  03 04 b4 3a ...      blk 11 0b 02 00 00 ...
```

Emulated, with a synthetic 2 KB image and a device that answers
(`tests/test_qspi_eeprom.py::TestBlockManager`):

| Block 8 payload | Mirror after `nvm_read_all_blocks` |
|---|---|
| `FF FF FF …` (erased) | the **defaults** `08 01 00 80 80 …` |
| `55 01 …` (E% at +0) | the **defaults** |
| `08 55 …` (E% at +1) | the **defaults** |
| `08 01 55 …` (E% at +2) | `08 01 55 …` — **accepted** |

**Consequence for `patches/ff_fuel`.** `ff_persist_offset` ships as **0**, so
the stored ethanol percent lands exactly on the block-id byte. Every cold
start then throws the block away and reloads the defaults, and the mirror
byte the patch reads back is `0x08` — a perfectly plausible **8 %**, not the
`0xFF` that means "nothing known". The value never survives a key cycle and
the failure is silent. The fix is one calibration byte,
`ff_persist_offset = 2` (procedure_d2.md §B4 already documents the lever),
and §5's "free payload offsets +0..+13" for block 8 must read ~~**+2..+13**~~
**+19..+28** (*2026-09-24, G7, #38: +2..+18 are the 17 tester adaptation
channels — §5, note of 2026-09-24 — so +2 was channel 1; `ff_persist_offset`
is now 19. The stamp check below is unchanged: it compares payload +0 only,
so +19 is accepted exactly as +2 was, emulated in
`tests/test_ff_diag_patch.py::TestPersistOffsetOffTheChannels`*).
The same correction applies to blocks 1, 3, 7, 11 and 12 in that table.

### 10.6 The full #38 path, emulated end to end

With `ff_persist_offset = 2`, a factory-shaped device image and the 10 ms
hook plus the real pump wrapper `nvm_pump_wrapper` (0x061944) called once per
simulated activation:

| Step | Result |
|---|---|
| cold start, `nvm_read_all_blocks` | block 8 mirror = the device image |
| 600 activations at E85 then one with the rate limit open | request record `{buf 0, blk 8, off 0, len 0, shape 3, status 1}` and the mirror already carries the new E% at +2 with a correct block checksum |
| ~7 further activations, pump only | record status **2** |
| the next activation | `ff_persist_state` = 3, `ff_persist_err` = 2, `ff_persist_writes` = 1, `ff_persist_fails` = 0 |
| the device | **both** copies, 0x1C0 and 0x1E0, carry the byte and verify |
| payload +14 | unchanged |
| payload +29 | **changes** 0xFF → 0x00 — it is the manager's own ReplV byte and it moves on the first commit; `logging/sessions/ff_fuel.json` check 7 and `procedure_d2.md` §B2 say it never changes, and that is wrong |
| power cut: a fresh `Med9Emu` with that device image | `e_key` = `e_filt` = stored × 16, `e_persist` = stored, `ff_persist_err` = 2 |

VERIFIED-DYNAMIC (emulated),
`tests/test_qspi_eeprom.py::TestPersistenceThroughTheDevice`. It is *not* a
bench measurement: it proves the code path, the call shapes and the data
layout, not the device's timing. `M95160(wip_polls=n)` is there to make the
WIP poll take n rounds when someone wants to rehearse a slow part.

Section 7 open question 1 is therefore **narrowed, not settled**: see §10.3.

### 10.7 Who binds 0x7FAB70 / 0x7FAB74 — the exclusion set
### (F3, 2026-09-22, #38 — a time-boxed negative result, 4 h)

Brief F3 was asked for either a VERIFIED-STATIC answer to §7 Q1 or the set of
possibilities it can exclude, with the commands. This is the second.
**Nothing in this image binds the two pointers**, and the search is now wide
enough that the interesting question has moved.

The new tool is `tools/store_xref.py`: it carries a constant-propagation model
over both code regions, seeds r13 = 0x7FFFF0 and r2 = 0x5C9FF0, follows `lwz`
through pointer words in flash, and reports every store — D-form, indexed,
`stmw`, `stfd` — whose effective address lands in a window. `--control` proves
the scan works before it is believed:

```bash
./.venv/bin/python3 tools/store_xref.py data/passat_azx_ori.bin --control
#   0x7fb6f4-0x7fb703   24 store site(s)  ok   flash_crc_task's state cells
#   0x7fcd68-0x7fcd69    2 store site(s)  ok   nvm_mode
#   0x803d3c-0x803d3f    3 store site(s)  ok   kwp_security_state / session
#   RESULT: PASS
./.venv/bin/python3 tools/store_xref.py data/passat_azx_ori.bin \
    --window 0x7FAB58 0x7FAB80
#   0 store site(s) into 0x7fab58-0x7fab7f
```

**(a) It is not two cells, it is a whole descriptor that nobody writes.**
The scan resolves **30,239 stores into 0x7F8000-0x807FFF**, 3,228 of them
word-sized. Not one lands anywhere in **0x7FAB58-0x7FAB7F** — and that block is
*all* pointers the same module reads:

| cell | read at | what it is |
|---|---|---|
| 0x7FAB58 | 0x05E964, 0x05EB50, 0x05F260, and `addi` at 0x05F868 | table base, indexed `<<3` |
| 0x7FAB5C | 0x05EB14, and `addi` at 0x05F820 | table base, indexed `<<2` |
| 0x7FAB60 / 0x7FAB64 | 0x05ED18 / 0x05EE0C | table bases |
| 0x7FAB6C | `addi` at 0x07191C, 0x071C90, read as a **byte** count | how many devices |
| **0x7FAB70** | 0x05FD88, and `addi`+`lwz`+`blrl` at 0x061CF0 | the read entry point |
| **0x7FAB74** | 0x06068C | the write entry point |
| 0x7FAB78 | 0x061D40 | a third entry point, same shape |

Two more call sites through the pair turned up on the way — **0x061CF0** and
**0x061FAC**, both `lis`/`addi` 0x7FAB70, `lwz`, `mtlr`, `blrl`; the second
calls it with `(0x20, 0x20, r1+8)` and then tests the byte at `r1+8` against 1,
which is the §10.3 completion convention seen from the caller's side.
So the highest address in 0x7FAB00-0x7FABFF that any store reaches is
**0x7FAB55**. VERIFIED-STATIC.

**(b) No copy loop can reach it.** `--loops` lists all 18 `stwu` fill/copy
loops in the image; the only one that starts below the block and within reach
is 0x0327D0, and it is `ctr = 4`, two words per turn, from flash 0x03449C to
**0x7FA480-0x7FA4A4** — 0x6CC bytes short. VERIFIED-STATIC.

**(c) No table in flash hands the address to anyone.** Every aligned word of
both flash regions and of the calibration was checked for a value inside
0x7FAB40-0x7FAB90: exactly two, **0x0B45D4 = 0x7FAB88** and
**0x0B45D8 = 0x7FAB7C**, both *above* the pair and both trailing words of the
TouCAN module-base array at 0x0B45C0 (records of 0xC bytes, count byte at
0x0B4468, consumer 0x063D4C, which does `lhz 0x816(r31)` on its argument —
a CAN module base, not a RAM destination). VERIFIED-STATIC.

**(d) Indexed array walks are excluded for a *word*.** `--near 0x1000` keeps
every indexed store whose base lies up to 0x1000 below the block: 73 sites,
**all of them `stbx` or `sthx`** off eight array bases in 0x7FA638-0x7FA990.
A function pointer the manager reads with one `lwz` cannot plausibly be
assembled by byte or halfword array walks. There is **no `stwx` anywhere in
the image whose base lies in 0x7FA000-0x7FAB74**. VERIFIED-STATIC.

**(e) It is not in the 16 KB the dump is missing.** No `b`, `bl`, `ba` or `bc`
anywhere in either code region targets **0x400000-0x403FFF**, and the only
words pointing there are calibration constants and three peripheral addresses
(0x401FF0/F4/F8 at 0x010284). A routine in the missing block could therefore
only be entered through the live ETR exception table that occupies its first
0x100 bytes — which is not how a device driver gets bound. VERIFIED-STATIC.

**(f) The RAM bootstrap loader is *not* an untested hiding place.** The boot
copies flash 0x019798-0x02A827 to 0x7F8728 (`boot.md` §6 note (b)); those bytes
are inside 0x000000-0x1BFFFF and were scanned as code. Absolute `lis`/`addi`
stores resolve the same wherever the block executes, so the scan covers it.
Zero hits.

**(g) Approach (c) of the brief — a function with the right signature — finds
nothing either.** The callers of the three §2 primitives are

```bash
./.venv/bin/python3 tools/sda_xref.py data/passat_azx_ori.bin \
    --code 0x085A8C 0x085B54 0x085BC0
```

0x085B8C for `eeprom_write_byte`; 0x085CDC, 0x085D68, 0x085E10, 0x085E78,
0x085F18, 0x085F68, 0x087914, 0x089528 for `eeprom_read_bytes`; and
0x087740-0x0878D8, 0x087F80-0x087FE4, 0x088120-0x088154, 0x089508 for
`eeprom_write_bytes` — **all** inside the KWP programming module
0x085000-0x08A000, all three-argument `(addr, len, buf)` and synchronous.
0x089508/0x089528 is the closest thing to a pair, and it is a write-then-verify
of 0x20 bytes at 0x280/0x2A0 with a 0x15 error code, not a device binding.
**No four-argument `(eepAddr, len, buf, statusPtr)` function exists in the
image.** VERIFIED-STATIC.

#### What that leaves

The question is no longer "which of the functions in this image is bound" —
none of them is, and nothing in this image performs the binding. The live
candidates are now:

1. **A variant module the linker dropped.** The block manager, its three table
   pointers and its three entry-point pointers are one descriptor; a build that
   selects a different non-volatile device would fill all seven. The boot's own
   SPI layer already has exactly that shape — `FUN_00017E14` picks one of six
   device tables by hardware variant and stores three pointers at
   0x7F83A0/A4/A8 (§1.5). This is the reading §7 Q1 should carry from now on.
   **HYPOTHESIS.**
2. **A write through a pointer held in RAM**, which no static scan can follow.
   The scan does follow a base loaded out of *flash*; a base loaded out of RAM
   is where its model stops.
3. A store the model drops because the base was built before a branch. The
   model resets at every branch, so this is possible in principle — but it
   would have to build 0x7FAB70 somewhere, and the only sites in the whole
   image that form an address inside the block are the six readers in the table
   of (a).

For the simulator the practical answer is unchanged and now better founded:
`emu.qspi_eeprom.NvmDeviceBinding` installs the two trampolines because
**nothing else does**, and it implements the signature the call sites require,
which is VERIFIED-STATIC. `logging/ecu_sim.py` keeps it, and the residue table
in its module docstring lists 0x7FAB70/0x7FAB74 as hand-bound with this
section as the reason.

---

## 11. The adaptation-reset path: who sets the flag, and what a reflash does
### (H2, 2026-09-24, #47 / #26 / #38)

Brief G7 found the stock, tester-free path `fault_clear_then_adaptation_reset`
0x0D1068 that clears the fault memory (block 24) and then resets all 17 KWP
adaptation channels (block 8 +2..+18) — gated on **0x7FEB59 != 0 and bit 0 of
0x7FA02B** (EEP_CONF block 11 mirror +11) — and left "who sets the bit" open.
This section closes it. Everything is VERIFIED-STATIC (disassembly) unless
tagged; the end-to-end run is VERIFIED-DYNAMIC (emulated),
`tests/test_adaptation_reset_path.py`.

### 11.1 Who sets block 11 +11 bit 0 (the flag)

The flag has three faces: EEPROM **0x28B** (block 11 copy 0 payload +11) and
its copy **0x2AB**; the EEP_CONF manager mirror **0x7FA02B**; and the
immobiliser module's *private* 32-byte mirror **0x7FD2CC**
(`immo_eep_block_280_mirror`, `re/symbols.csv`), +0xB = **0x7FD2D7**. The
manager mirror is loaded from EEPROM 0x28B by the start-up read
`nvm_read_all_blocks` (section 3.5) — that is the only way a raw-SPI-written bit
reaches the gate 0x0D1068 reads.

**The sole setter is the routine-0xC5 progress handler
`kwp_prog_routine_c5_results` 0x087494** (`re/symbols.csv`;
`blobdis.py --file-off 0x87494 --len 0x684`). It is dispatched at **0x087AFC**
as the **SID 0x33 RequestRoutineResults** handler for **local id 0xC5**
(`cmpwi r3,0xC5; beq 0x87494`), a service of the **programming** KWP stack
(`flash_programming.md` section 1), so it needs `prog_security_level == 2` and is
reachable only after `10 85`. It is a step machine on the byte **0x7FD5FC**
(`routine_c5_step`):

| step | at | what it does |
|---|---|---|
| 0x11 | 0x875D0 | after the access/variant/download-state checks pass, stages the immobiliser mirror: `stb r28,0xB(r25)` at **0x876A8** with `r28 = [0x7FD486] \| 1` (**bit 0 set**) into 0x7FD2D7, and its complement at +0x17; sets step 0x12 |
| 0x12 | 0x876D8 | waits until 0x7FD5FE and 0x7FDA3D are both 1, then sets step 0x14 |
| 0x14 | 0x87710 | flushes the staged mirror bytes to EEPROM through `eeprom_write_bytes` 0x85B54: **0x087850 writes 0x28B, 0x087894 writes 0x2AB** (both from mirror +0xB, bit 0 set), then reads both copies back and verifies |

So the flag reaches the device **only when a tester runs routine 0xC5** in the
programming session — the immobiliser / component-protection adaptation step.

**Writers of the gate and the flag — the complete set (VERIFIED-STATIC):**

| target | writer | value / condition |
|---|---|---|
| EEPROM 0x28B / 0x2AB (raw SPI) | 0x087850 / 0x087894 in 0x087494 | bit 0 **SET**; routine 0xC5 step 0x14, programming mode only |
| EEPROM 0x280/0x2A0 whole block (raw SPI) | `eeprom_write_verify_immo_block` 0x0894A0 (one caller 0x0861F4, in the relocated programming driver) | flushes the immo mirror as-is — **preserves** whatever step 0x11 staged; does not decide the bit |
| other raw-SPI block-11 sites (0x087F80-0x087FE4, 0x088120-0x088154, 0x089508) | programming module | write +0xC/+0xD/+0x18 and the whole 0x280/0x2A0 blocks — **never +0xB** (`find_branch_refs.py ... 0x85b54`; each `li r3,...` checked) |
| manager mirror 0x7FA02B (direct store) | **none** | `store_xref.py --window 0x7FA020 0x7FA040` = 0 sites; the mirror is only touched by the start-up read (from EEPROM 0x28B) and by `nvm_block_request(11,11,...)` |
| manager block 11 +11 (`nvm_block_request(11,11,1,0,...)`) | **0x0D1110** inside 0x0D1068 | bit 0 **CLEARED** (`rlwinm r11,r11,0,0x18,0x1E` at 0x0D10D8), staged into the mirror after the reset — one-shot; the other manager block-11 clients (0x0364B8, 0x036650, 0x036B34) write +12/+13 only (section 4) |
| 0x7FEB59 | **0x134030** in `boot_mode_classifier` 0x133F40 (sole store site) | 1 if any abnormal/just-programmed condition holds — section 11.3 |

### 11.2 The reflash question — the verdict

**The OBD download itself does NOT set the flag.** `10 85` -> `27` -> `34`/`36`/`37`
-> `37` -> reboot writes flash and EEP_CONF block 10 (`flash_programming.md`
section 2, section 5.2); none of it touches block 11 +11. The flag is set
**only** if the tester additionally runs **routine 0xC5** (`31 C5` start /
`33 C5` poll) in the same programming session — the immobiliser /
component-protection adaptation step VW flash tools run when they (re)pair or
virginise the ECU. When that routine reaches its commit (step 0x14) the bit is
written to EEPROM 0x28B/0x2AB.

**On the first application boot after such a session**, `boot_mode_classifier`
0x133F40 sets 0x7FEB59 = 1 (the programming magic 0x7F8020 = 0xAABFFB11 is
still present, and/or the flashed identity differs — section 11.3), and
`nvm_read_all_blocks` loads bit 0 of 0x7FA02B = 1 from EEPROM 0x28B. Both gates
of 0x0D1068 are then satisfied. 0x0D1068 is a **process of the 100 ms task
list** (`tbl_os_process_lists`; its pointer at 0x0B22A4 is in the
0x0B1FC0-0x0B231C span = task 18, priority 3, 100 ms; `boot.md` section 6.8(c)),
so it self-fires within 100 ms of the application starting and, over a few
cycles, runs the fault-clear machine to completion, resets the channels and
clears the flag. **Consequence for the fuel trims:** channels 4/8/10
(`docs/05` section 3.3) and every other implemented channel except 7 are forced
back to their block-8 defaults. **Consequence for #38:** the ff_fuel E% store at
block 8 **+19** survives untouched (the reset only rewrites +2..+18); at the old
+2 it did not. **Consequence for #28's exit criterion "adaptation values
unchanged":** it holds **only if no routine 0xC5 was run** in the programming
session. A flash that includes the component-protection adaptation step *will*
reset the trims on the next boot, and #28 must read them back (session file
below) rather than assume they are unchanged. **Flash 0/1 (#26/#27) must record**
whether routine 0xC5 ran (it is visible on the bus as `31 C5` / `33 C5`, and
after the fact as a cleared bit 0 of EEPROM 0x28B plus default channels in
block 8).

**The RAM bootstrap loader (F5, `ram_loader.md`) does NOT set the flag.** It
speaks SCI1, writes flash by address against a blacklist, and has no KWP routine
dispatch and no path into the immobiliser module; it cannot write EEPROM 0x28B.
The serial recovery route therefore does not reset adaptations.

**The immobiliser pairing path IS the writer, but only through routine 0xC5.**
The setter lives in the immobiliser/programming module and manipulates the
immobiliser's private block-11 mirror; there is no application-stack path (all
raw block-11 writers are inside 0x085000-0x08A000, section 5 note of 2026-09-24
exclusion (e)). So "immobiliser pairing" and "routine 0xC5 in programming mode"
are the same event.

### 11.3 What 0x7FEB59 means, what else 0x0D1068 does, and the tester `14`

**0x7FEB59** is the **abnormal / just-programmed boot latch**, not G5's
0x7FEB65 "engine running" gate. Its sole writer 0x134030 sits in
`boot_mode_classifier` 0x133F40, which first builds a status byte **0x7FD411**
(`boot_mode_status`):

* bit 0 — the return of `bl 0x6D720(1)`;
* bit 1 — calibration byte 0x5D78EB != 0;
* bit 2 — **word 0x7F8020 == 0xAABFFB11** (the programming-request magic a
  `10 85` leaves behind, `flash_programming.md` section 2.1);
* bit 3 — a 10-byte flashed HW/SW identity string (flash 0x80280...) differs
  from the stored copy at RAM 0x7F84A0.

0x7FEB59 is then set to **1 if any of status bits 0-3 is set, or bytes 0x7F846A
/ 0x7F8468 are non-zero**; else 0. Bits 2 and 3 tie it directly to a reflash.
The same function sets `boot_mode_flags` bit 2 (`flash_programming.md`
section 2.2).

**What 0x0D1068 does before 0x038D64** (`blobdis.py --file-off 0xD1068 --len 0xD8`):
gate on 0x7FEB59 != 0 and bit 0 of 0x7FA02B; clamp the halfword counter
0x7FB758 to <= 0x258; drive the fault-clear state machine **0x035300** one step
per call through `kwp14_clear_state` 0x7FB718 (state 0 takes the fault-memory
lock and queues EEP_CONF **block 24**, state 1 commits block 24, state 2
completes after its time-base wait); when it reports 2, call **0x038D64** (reset
every implemented channel except 7 to its default and commit block 8 +2..+18),
then clear bit 0 of the flag byte and **stage** it back into the manager mirror
with `nvm_block_request(11,11,1,0,...)` at 0x0D1110. The stage updates the
mirror 0x7FA02B immediately (so the gate closes for this power cycle); the
**device** write of the cleared block 11 is deferred to the manager's key-off
write-all (section 3.5) — the one-shot across power cycles relies on that flush
happening (caveat, section 11.5).

**A plain tester `14 FF 00` does NOT reset the fuel trims on this dataset.**
`adaptation_reset_all_commit` 0x038D64 has **exactly one caller, 0x0D10D0**
(`find_branch_refs.py ... 0x38d64`), inside 0x0D1068 and behind both gates. The
tester `14` handler `kwp_sid_14_h1` 0x35410 runs the *same* fault-clear machine
0x035300 directly (commits block 24, `kwp.md` section 12.7) and answers
`54 FF 00`, but it **never calls 0x038D64**. So a workshop DTC clear wipes the
fault memory and commits block 24 — it does **not** touch the adaptation
channels. VERIFIED-STATIC.

### 11.4 The end-to-end run (VERIFIED-DYNAMIC, emulated)

`tests/test_adaptation_reset_path.py`, the G7 pattern on the section 10 device
model: seed the QSPI device with block 11 +11 bit 0 set and E% 85 at block 8
+19, run init entries 33/34/38/39 and `adaptation_restore_all` (a real boot's
set-up, which arms the implemented-channel mask 0x8001D4 = 0x77BE), set
0x7FEB59 = 1, perturb a channel away from its default, then call 0x0D1068 in a
loop with the NVM pump and the virtual time base between calls. Result:
`kwp14_clear_state` cycles 0 -> 1 -> 2 -> 0; block 24 is committed to the device
(stamp `18 02`, valid checksum); the perturbed channel and all of
0x7FD062-0x7FD06D return to their defaults; **both** copies of block 8 carry the
channel defaults at +2..+18 and **+19 = 0x55 unchanged**, with a valid checksum;
the manager mirror 0x7FA02B bit 0 is cleared. The whole-SRAM and whole-device
difference between an E% of 85 and the default 0 at +19, taken through the
*entire* path, is the +19 byte and the block checksum of each block-8 copy and
nothing else.

### 11.5 Open / caveats

* **Device write-back of the cleared block 11.** 0x0D1068 only *stages* the
  cleared flag into the mirror (0x0D1110); the EEPROM 0x28B copy still reads
  bit 0 = 1 until the manager's key-off write-all (section 3.5, HYPOTHESIS
  trigger) flushes block 11. If power is cut before that flush, the next boot
  reloads the set bit and the reset repeats. Bench read of EEPROM 0x28B before
  and after a key cycle would settle it.
* **The RAM fault-memory erase** (0x7F8890, 20 x 0x5C) is not done by 0x035300
  or 0x0D1068 — only the lock is taken and block 24 committed; DFPM processes
  behind the lock value erase the RAM entries later (G5, `kwp.md` section 12.7).
  The test asserts the block-24 commit and the lock cycle, and labels the RAM
  erase as downstream / not-modelled.
* **Which conditions actually hold after a *given* flash tool's run** (does the
  identity string mismatch, does the magic persist to app-init) is tool- and
  sequence-dependent; the firmware side is settled, the bus capture is the
  bench proof (`docs/08`, the new session file).
