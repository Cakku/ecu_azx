# KWP2000 diagnostic services — everything a live-RAM logger needs

Brief **B3**, issue **#13** (prerequisite for the TP2.0/KWP logger in **#20**).
Dump `data/passat_azx_ori.bin`, sha256
`b15590d3f1874ace3125c5d047c09a686db9b8bb498187663539ebab205609b3`.
Date 2026-09-15. All addresses are **CPU addresses**; `file` prefixes file
offsets. Evidence tags per `docs/README.md`.

Reproduce the disassembly/decompiles with `ghidra_scripts/b3_decompile.py`
(usage in its header) against the analysed project; the two dynamic checks are
`tools/kwp_seckey_verify.py` and `tools/kwp_upload_verify.py`, run with the
`emu/` Unicorn harness (both print `RESULT: PASS`).

---

## 0. TL;DR for the logger implementer

Transport: **VW TP2.0 over CAN** (channel setup on 0x200, engine logical
address 0x01) carrying **KWP2000** (see `docs/03_tooling.md` §6 and
`re/findings/fr_index.md`). A KWP *request* on the wire is `[SID] [data...]`;
a positive *response* is `[SID+0x40] [data...]`, a negative one is
`7F [SID] [NRC]`.

Two independent ways to read live RAM, both confirmed by emulation:

1. **DynamicallyDefineLocalId (0x2C) + ReadDataByLocalId (0x21)** — the cheap,
   fast route. **Needs only a diagnostic session, no SecurityAccess.**
   * `10 89` → enter session (internal type 5).
   * `2C F0 03 <pos> <size> <a2> <a1> <a0> [03 ...]` → define dynamic id 0xF0
     as a list of memory chunks (up to 20 chunks, 24-bit addresses).
   * `21 F0` → returns `61 F0 <bytes...>` = those chunks concatenated. Repeat
     as fast as the bus allows (~40 samples/s per `docs/03_tooling.md`).

2. **RequestUpload (0x35) + TransferData (0x36) + RequestTransferExit (0x37)**
   — whole-region snapshots (any RAM/flash address). **Needs session 0x86,
   which requires SecurityAccess level 2 first.**
   * `10 89`; `27 03`→seed; `27 04 <seed+0x11170>`; `10 86`; then
     `35 <a2 a1 a0> 00 <s2 s1 s0>`, loop `36`, finish `37`.
   * TransferData returns **62 data bytes per block**.

Do **not** read the protected RAM window **0x7F9E3C–0x7FA47F** with 0x35 (it is
rejected with NRC 0x31); everything else in RAM (0x7F8000–0x807FFF external/on-
chip SRAM) and flash is readable.

---

## 1. The service dispatch table (task 1)

### 1.1 Table location — correction to `docs/02_memory_map.md` §7

The KWP/OBD dispatch table is **28 entries × 20 bytes at 0x2B820–0x2BA50**
(VERIFIED-STATIC). `docs/02` §7 listed base 0x2B870 / 24 entries; that missed
the first **4** entries. The dispatcher never references 0x2B870 or 0x2B820
directly — it reads them through the config struct at **0x2BA50**
(= `DAT_00803ddc`), whose `+0x1c` byte is the entry count `0x1C = 28`.

Full first-4 (the ones `docs/02` omitted):

| off | SID | meaning | session mask (+4) | h1 |
|---|---|---|---|---|
| 0x2B820 | 0x12 | readEcuIdentification (VW variant) | 0x38 | 0x035C1C |
| 0x2B834 | **0x3E** | **TesterPresent** | 0x3F | 0x43A4A8 (on-chip) |
| 0x2B848 | 0x1A | readEcuIdentification | 0x38 | 0x438BB4 (on-chip) |
| 0x2B85C | 0x83 | accessTimingParameters | 0x3C | 0x438448 (on-chip) |

The remaining 24 (0x2B870 onward) are as `docs/02` and `re/symbols.csv`
already list them. There is still **no 0x23 ReadMemoryByAddress and no 0x3D**.

### 1.2 Entry layout (each 20 bytes)

```
+0   u8   SID
+1   u8   sub-function to match, or 0xFF = "match any sub-function"
+2   u16  mask_A  (all entries = 0xFFFF = wildcard -> see below)
+4   u32  session bitmask  (the 0x38/0x3C/0x3F/0x10/0x50 values)
+8   u32  handler h1
+0xC u32  handler h2 (0 if none; called on a second dispatch phase)
+0x10 u32 argument passed to the handler in r3 (0 for all KWP entries)
```

### 1.3 How a service is dispatched (`kwp_service_dispatch`, 0x13E98C)

Reached through a function pointer, so Ghidra makes no auto-xref; disassembled
with `b3_decompile.py 0x13e98c:x`. Per incoming request it builds a *capability
mask* and then linearly scans the 28 entries:

```
for each entry:
  if entry.SID != request.SID: continue
  if entry.sub != 0xFF and (request.len==0 or entry.sub != request.sub): continue
  found_sid = 1
  if (cap_mask_A & entry.mask_A)[low16] != cap_mask_A[low16]: continue   # (1)
  found_perm = 1
  if ((1<<current_session) & entry.session_mask) == 0: continue          # (2)
  found_ok = 1
  call h1(r3 = entry.arg, r4 = &kwp_io_struct)                           # (3)
```

* **(1) mask_A is 0xFFFF on every entry**, and `cap_mask_A & 0xFFFF ==
  cap_mask_A` is always true — so **the dispatcher does NOT enforce
  SecurityAccess**. Any security gating is done *inside* individual handlers
  (and none of the logging handlers do it — see §4/§5). This is the single most
  important structural fact for the logger.
* **(2) the only gate the dispatcher enforces is the diagnostic session**:
  `(1 << current_session) & entry.session_mask`. `current_session` =
  `kwp_session_current` (0x803D3E). If no entry passes the session gate the
  request is answered with a negative response (0x11 serviceNotSupported /
  0x12 subFunctionNotSupported / 0x33 depending on how far it got).
* The `h2` field and the two response-pending bytes (0x803DB8/0x803DB9) drive a
  deferred-response path (status 8 = "response pending", 9 = second pending);
  handlers set `kwp_io_struct+0xa = 8` to defer, e.g. the SecurityAccess delay.

### 1.4 The handler I/O struct (`kwp_io_struct`, 0x803DA4)

Every handler receives `r4 = &kwp_io_struct`:

```
+0   u32  pointer to the request bytes AFTER the SID  (the "buffer")
+6   u16  request length  = number of bytes after the SID
+8   u16  response length  = number of buffer bytes the handler produced
+0xA u8   status: 1 = positive, 2 = negative, 8/9 = response pending
+0xB u8   SID (cached)
+0xC u8   sub-function / first data byte (cached)
```

So `buffer[0]` is the sub-function for services that have one, and the raw
first data byte for services that don't. The transport layer prepends the
response SID (`SID+0x40` positive, `7F SID` negative) — the handler only fills
`buffer[...]` and `response length`.

### 1.5 Can the table be extended?

The count lives in one byte (`config[0x1c]`), the table is contiguous flash,
and it is bounded above by the config struct at 0x2BA50 — so **adding an entry
means relocating the table** (or the config struct) to free flash and bumping
the count. It cannot grow in place. Spare *measuring-block* slots (§6) are the
cleaner extension point for our own variables (#39).

---

## 2. Diagnostic sessions — StartDiagnosticSession 0x10 (task 6)

Handler `kwp_sid_10_h1` (0x3716C); the real work is in
`kwp_start_session_core` (0x36BC0). The requested session byte is the
sub-function; it maps to an *internal* session number stored in
`kwp_session_current` (0x803D3E) via `kwp_session_set` (0x13CEE4):

| Request | on wire | internal session | prerequisite | notes |
|---|---|---|---|---|
| default | `10 81` | **0** | none | reset default; only management SIDs allowed |
| — | `10 83` | **3** | none | enables 0x21 / 0x2C / 0x27 |
| programming | `10 85` | (flash) | **security level 1** (state 2) | flash-reprogramming session; sets up UC3F |
| upload | `10 86` | **4** | **security level 2** (state 3) | the ONLY session that allows 0x35/0x36/0x37; on entry it also calls `kwp_security_state_set(0)` |
| — | `10 89` | **5** | none | enables 0x21 / 0x2C / 0x27 with no security |
| other | — | — | — | negative 0x11 |

`current_session` is **0 after reset** (BSS). In session 0 only the SIDs whose
mask has bit 0 set are reachable (0x3F and 0x50 entries: TesterPresent,
StartDiagnosticSession, ReadEcuId 0x1A/0x12, 0x81/0x82/0x20, OBD 0x01-0x09).
Measuring blocks and DDLI are **not** reachable in session 0 — you must start
0x83 or 0x89 first.

### 2.1 What each session bitmask (+4) means, decoded

`service allowed ⇔ (1 << current_session) & mask ≠ 0`:

| mask | bits (sessions) | services carrying it |
|---|---|---|
| 0x3F | 0,1,2,3,4,5 | 0x3E TesterPresent, 0x10, 0x1A, 0x12, 0x81, 0x82, 0x20 |
| 0x3C | 2,3,4,5 | **0x2C DDLI**, **0x27 SecurityAccess**, 0x83 |
| 0x38 | 3,4,5 | **0x21 ReadDataByLocalId**, 0x14, 0x3B, 0x18, 0x17, 0x31, 0x32 |
| 0x10 | 4 only | **0x35 RequestUpload, 0x36 TransferData, 0x37 RequestTransferExit** |
| 0x50 | 4,6 | OBD-II 0x01–0x09 |

Consequences that decide the logger design:
* **0x21 / 0x2C work in sessions 0x83(3) and 0x89(5) with no security.**
* **0x27 works in sessions 0x83(3), 0x86(4), 0x89(5)** (mask 0x3C).
* **0x35/0x36/0x37 work only in session 0x86(4)** — and 0x86 can only be
  entered after security level 2, so the upload route transitively needs
  security even though the upload handlers themselves never check it.

### 2.2 TesterPresent and timeouts

TesterPresent is SID **0x3E**, entry 0x2B834, handler 0x43A4A8 (on-chip),
mask 0x3F (valid in every session). Send it on the standard KWP **P3** interval
to keep the session alive; if the ECU times out it drops back to session 0 and
you must re-establish the session (and re-do security for the upload route).
Exact P3 value not extracted here — use the conventional ~2 s keep-alive (well
under the ~5 s default) and TP2.0 channel keep-alive; the logger should send
`3E 01` (sub 0x01 = with positive response) or `3E 02` (no response).

---

## 3. SecurityAccess — 0x27 (task 2)

Handler `kwp_sid_27_h1` (0x36210). Three access levels, each a
seed-request / send-key pair. The current level is `kwp_security_state`
(0x803D3C): **0 locked, 2 = level 1 granted, 3 = level 2 granted, 5 = level 3
granted**. The seed source is the **PowerPC time-base counter**
(`read_time_base`, 0x478460 — reads SPR TBU/TBL). All four multi-byte values
are big-endian.

| level | seed req | send key | seed = f(timebase) | **key = g(seed)** | verified |
|---|---|---|---|---|---|
| 1 | `27 01` | `27 02 <key:4>` | raw timebase (MSB≠0) | **5-round Galois LFSR**, mask **0x5FBD5DBD** | DYNAMIC |
| 2 | `27 03` | `27 04 <key:4>` | timebase × 0x3AA01BC0 | **seed + 0x11170** | DYNAMIC |
| 3 | `27 05` | `27 06 <key:4>` | timebase × 0x25D8B91F | seed + value from co-routine `FUN_004149cc` | static only |

The tester never needs the seed-generation formula — it just receives the seed
and computes the key. The two that matter for logging/flashing:

**Level 1 key** (5 rounds; `x` starts at the received seed):
```python
def key_level1(seed):
    x = seed & 0xffffffff
    for _ in range(5):                      # DAT_007fb770 = 5
        if x < 0x80000000: x = (x << 1) & 0xffffffff
        else:              x = ((x << 1 | 1) ^ 0x5FBD5DBD) & 0xffffffff
    return x
```

**Level 2 key** (this is the "development session" pair the community reported):
```python
def key_level2(seed):
    return (seed + 0x11170) & 0xffffffff    # + DAT_000a331c
```

### 3.1 Community claim resolved (task 2)

`docs/03_tooling.md` §6 / the brief report `key = seed + 0x11170` for MED9.1.
**Confirmed — but it is the LEVEL-2 pair (sub-functions 0x03/0x04), not
level 1.** The constant `0x00011170` is the flash word at `DAT_000a331c`
(file 0xA331C); the level-2 send-key path computes `seed + DAT_000a331c` and
compares. Level 1 is a completely different algorithm (the LFSR above).

### 3.2 On-wire exchange, request/response formats

```
->  27 01                 (request seed, level 1)
<-  67 01 s3 s2 s1 s0      (seed; = 00 00 00 00 if already unlocked)
->  27 02 k3 k2 k1 k0      (key = key_level1(seed))
<-  67 02 34               (0x34 = "access granted"); state -> 2

->  27 03                 (request seed, level 2)
<-  67 03 s3 s2 s1 s0
->  27 04 k3 k2 k1 k0      (key = seed + 0x11170)
<-  67 04 34               state -> 3
```

* Positive send-key response is `67 <sub> 34` (0x34 is VW's "granted" marker),
  `kwp_io_struct+0xa = 1`.
* Wrong key: the handler sets `kwp_io_struct+0xa = 8` (response pending), sets
  the **lockout delay** `kwp_sec_delay_timer` (0x7FB748) = 600 ticks and arms a
  countdown; while it is non-zero a new seed request returns NRC **0x37**
  (requiredTimeDelayNotExpired). Give one key attempt per seed.
* Requesting a seed while already unlocked at that level returns an all-zero
  seed and a positive response.

Dynamic proof (emulated `kwp_sid_27_h1`, seed 0x12345678):
```
L2 correct key 0x123567E8 -> resp 04 34, status 1, state -> 3
L2 wrong  key            -> status 8 (delay), state stays 0
L1 correct key 0xF9F07478 -> resp 02 34, status 1, state -> 2
L1 wrong  key            -> status 8 (delay), state stays 0
```

---

## 4. DynamicallyDefineLocalId 0x2C + ReadDataByLocalId 0x21 (tasks 3, 4)

### 4.1 DDLI define — `kwp_sid_2C_h1` (0x34D28)

Dynamic local identifiers are **0xF0–0xF9** (10 of them). Request:

```
2C <LID> <mode/entries...>
   LID   = 0xF0 .. 0xF9
   mode:
     04                 -> clear the definition of <LID>
     03 <pos> <size> <a2> <a1> <a0>   (6-byte entry, repeatable)  -> defineByMemoryAddress
```

Each `03` entry means "at position `<pos>` in the record, take `<size>` bytes
starting at the 24-bit address `<a2><a1><a0>`". Rules enforced by the firmware:

* `<pos>` of an entry must equal *(sum of the sizes of the preceding entries) +
  1* — i.e. the record is described contiguously from position 1. A bad `<pos>`
  aborts the whole define with NRC 0x12.
* You must **clear (`2C LID 04`) before redefining** an id that already has a
  definition, else NRC 0x22 (conditionsNotCorrect).
* **Max entries per id** (`tbl_ddli_max_entries`, 0xA2268): **0xF0 → 20**,
  **0xF1–0xF9 → 3 each**. Exceeding it aborts with NRC 0x12.
* Address is **24-bit**, which covers all RAM (0x7F8000–0x807FFF) — top byte is
  forced to 0, so you cannot address ≥0x1000000.
* **Correction to the community "only the last entry may exceed 1 byte" note:**
  our firmware imposes no such rule. Every `<size>` is honoured independently
  (the read path, §4.3, reads `<size>` bytes for *each* entry). Use whatever
  chunk sizes you like as long as the `<pos>` accounting is contiguous.

Positive response: `6C <LID>` (`kwp_io_struct+0xa = 1`). The definition is
stored in `ddli_def_table` (0x804038, 10 slots × 8 B: flag, entry count, ptr to
an entry array; each stored entry is 8 B = size at +1, 24-bit source addr at
+4). `kwp_sid_2C_h2` (0x35034) wipes all 10 slots (session change / reset), so
**redefine your dynamic ids after every session (re)start.**

> **2026-09-17 (E4, #20/#39) — where the entry arrays actually are, and the
> emulator trap that hid it. VERIFIED-STATIC.**
>
> The "ptr to an entry array" at slot+4 is **not** filled by `kwp_sid_2C_h1`,
> and `kwp_sid_2C_h2` clears only the count byte at slot+1 (0x035044-0x03505C
> writes `stb r3,1(r12)` for ten slots and nothing else). It is filled once by
> **`ddli_init` at 0x12E39C**:
>
> ```
> 0012E3B0  stw  r11,4(r4)        ; r4 = 0x804038, r11 = 0x80366C   -> id 0xF0
> 0012E3BC  mulli r11,r5,0x18     ; r3 = 0x80370C, r5 = 1..9
> 0012E3D0  stw  r11,4(r12)       ; slot n -> 0x80370C + (n-1)*0x18 -> 0xF1..0xF9
> ```
>
> so **id 0xF0's entry array is 0x80366C-0x80370B (0xA0 B = 20 entries)** and
> **ids 0xF1-0xF9 get 0x18 B = 3 entries each**, starting at 0x80370C — which
> is exactly `tbl_ddli_max_entries` (0xA2268) and confirms those limits from a
> second, independent direction. `ddli_init` is a leaf ending at 0x12E3E4 and
> is reached only through the function-pointer table at **0x0B1B88**, the same
> init-table family as the NVM mode setters of `eeprom.md` §9.
>
> **Consequence for `emu/` and `logging/ecu_sim.py`.** The emulator has no OS
> to walk that table, so before this note every pointer was 0 and every
> dynamic id wrote its entries to **address 0 + i·8** — into
> `tbl_etr_branch_table` at 0x000000 (one define turned `48 01 10 F2` into
> `48 02 10 F2 00 7F FB 00`). Two visible symptoms, one cause:
> * defining a **second** dynamic id silently overwrote the **first** one's
>   entries, so the first id's record read the wrong cells and, when the sizes
>   differed, came back short and shifted;
> * the exception branch table was corrupted, which is a plausible source of
>   the runs that died mid-session under load.
>
> `logging/ecu_sim.py::Med9Handlers.power_on` now calls the real routine, and
> all five ids of `logging/sessions/ff_fuel.json` then read back byte for byte
> against a direct RAM read of the same emulator
> (`tests/test_ecu_sim_patch.py::TestDdliAcrossSeveralIds`).
>
> **Nothing here says the bench will misbehave.** On the car the application's
> start-up runs the init table, so the pointers are right and five dynamic ids
> are fine — the 20/3/3/… entry budget and the response length are the real
> limits, and `logging/med9log.py::plan_chunks` already respects both. What was
> broken was only the simulator's fidelity, in exactly the way
> `eeprom.md` §10.3 describes for the NVM device pointers: an uninitialised
> pointer that no statically resolvable instruction in the *application* path
> writes, because the writer lives in an init table.

### 4.2 ReadDataByLocalId routing — `kwp_sid_21_h1` (0x35F6C)

```
21 <LID>
  0x01–0x7F , 0xA0–0xEF  -> measuring blocks via the group table (§6)
  0x80–0x9F             -> unsupported, NRC 0x31 (kwp21_localid_80_9F_stub, 0x35728)
  0xF0–0xF9             -> dynamic read (kwp21_dynamic_read, 0x34F48)
  0x00 / anything else  -> NRC 0x11
```

### 4.3 Dynamic read — `kwp21_dynamic_read` (0x34F48)

For `21 F0..F9`: walk the id's entries in order, and for each entry copy
`<size>` bytes read live from its stored 24-bit address into the response.
Response = `61 <LID> <chunk0><chunk1>...`, `response length = total+1`; if the
id has no definition the answer is NRC 0x12. This is the fast RAM-logging path
and it needs **no security** — only session 0x83/0x89.

---

## 5. RequestUpload 0x35 / TransferData 0x36 / RequestTransferExit 0x37 (task 5)

### 5.1 RequestUpload — `kwp_sid_35_h1` (0x360C4)

Request is exactly **7 bytes after the SID** (else NRC 0x10):

```
35 <a2> <a1> <a0>  00  <s2> <s1> <s0>
   addr = 24-bit start address (a2 a1 a0)
   [3]  = 0x00  (format byte; must be 0, else NRC 0x51)
   size = 24-bit length (s2 s1 s0)
```

Positive response is a single byte: `75 3F`. The `0x3F` is the
maxNumberOfBlockLength indicator; actual data per TransferData is 0x3E = **62
bytes** (§5.2).

Address handling (two modes, `kwp_upload_mode` at 0x7FB80C):

* **Mode 1 — normal, any address.** Chosen for every address *except* the
  0x480000–0x480400 window. The only restriction is `kwp_upload_range_check`
  (0xA3160): the requested `[addr, addr+size)` must **not overlap the protected
  window 0x7F9E3C–0x7FA47F**; overlap → NRC 0x31 (requestOutOfRange). RAM
  (0x7F8000–0x807FFF), calibration and code flash are all otherwise allowed.
  **No SecurityAccess check in the handler** — the gate is the session (0x86,
  see §2) plus this range check.
* **Mode 4 — the 0x480000–0x480400 window** (1 KB), gated by flag
  `DAT_007feb65`; end must stay < 0x480400. Handled specially by
  `kwp_transfer_mode4` (0xA33B4) with a 32-entry segment table (DAT_000B2FF2);
  fills 0xFF for unmapped sub-ranges. Not needed for RAM logging.

### 5.2 TransferData — `kwp_sid_36_h1` (0x37434)

Requires an active upload (`kwp_upload_mode` 1..0x20, else NRC 0x10). Each call
returns the next block: `min(remaining, 0x3E)` = up to **62 bytes**, streamed
from `kwp_upload_addr` (0x7FB810), which then advances by the block length while
`kwp_upload_remaining` (0x7FB814) decrements. Response = `76 <bytes...>`,
`response length = block length`. When remaining hits 0 a further TransferData
returns NRC 0x12. `kwp_sid_36_h2` (0xA33A4) clears the upload state on session
change.

### 5.3 RequestTransferExit — `kwp_sid_37_h1` (0x360B0)

Just acknowledges: response length 0, status positive (`77`). No data.

### 5.4 Dynamic proof (emulated)

```
RequestUpload 0x7F8000 / 0x100  -> resp 3F, status 1, mode 1
RequestUpload 0x7F9E00 / 0x200  -> resp 31, status 2  (hits protected window)
RequestUpload 0x000000 / 0x40   -> resp 3F, status 1, mode 1 (flash ok)
RequestUpload 0x480000 / 0x10   -> resp 3F, status 1, mode 4
TransferData (mode 1, from RAM) -> 62 bytes, exactly matches source,
                                   addr += 62, remaining -= 62
```

---

## 6. Measuring-block tables for spare-slot use (task 7)

Already fully mapped by A3 (`re/findings/measuring_vars.md`,
`re/measuring_vars.csv`) — this brief only re-confirms the entry points the
0x21 path uses:

* **Index/handler table (TKMWL)** `tbl_measuring_vars` at **0xA5658**, 2200 ×
  4-byte handler pointers; dispatched by `measuring_var_dispatch` (0x45768) on
  the variable id. 665 ids implemented, 1535 point at the "not available" stub
  0x38EC4 — **those 1535 stub slots are the spare capacity** for carrying our
  flex-fuel variables later (#39): point a stub slot's pointer at a new handler
  that emits `(formula, A, B)` via `measuring_result_emit` (0x38EB4).
* **Group table** `tbl_measuring_groups` at **0x5C5518**, 4 fields × 255 groups
  × u16 variable id; `entry(field,group) = 0x5C5518 + field*0x1FE + group*2`.
  Reached from `21 <group>` via 0x35F6C → 0xA2CC4 → 0x3583C → 0x3574C →
  0x45768.

For a logger, the static route is: `21 <group 0x01..0xFF>` returns that group's
up-to-4 variables, each as a `(formula, A, B)` VAG triple (scaling in
`re/findings/measuring_vars.md`). DDLI (§4) is preferred for arbitrary RAM.

---

## 7. Negative response codes seen in these handlers

| NRC | where | meaning |
|---|---|---|
| 0x10 | 0x35/0x36 wrong precondition, 0x27 flag not set | generalReject |
| 0x11 | 0x21 bad LID, 0x10 bad session | serviceNotSupported |
| 0x12 | 0x21/0x2C bad length or record, 0x36 upload done | subFunctionNotSupported |
| 0x22 | 0x2C redefine w/o clear, 0x35 mode-4 flag, 0x86 preconditions | conditionsNotCorrect |
| 0x31 | 0x21 LID 0x80-0x9F, 0x35 protected/blocked address | requestOutOfRange |
| 0x33 | session/precondition gate (0x85/0x86 wrong security) | securityAccessDenied |
| 0x35 | 0x27 send-key mismatch (deferred) | invalidKey |
| 0x37 | 0x27 seed request during lockout | requiredTimeDelayNotExpired |
| 0x51 | 0x35 non-zero format byte | (VW) upload format error |
| 0x53 | 0x35 mode-4 end past window | (VW) upload out of range |

---

## 8. Recommended logger recipes (for #20)

**A. Fast selective RAM logging (no security):**
```
10 89                                  ; session 5
2C F0 04                               ; clear F0
2C F0 03 01 01 A2 A1 A0                ; pos1, 1 byte @0x00A2A1A0 ... (repeat, <=20 chunks)
loop:  21 F0   -> 61 F0 <bytes>        ; sample
       3E 02  every ~2 s               ; keep alive (no-response variant)
```

**B. Whole-region snapshot (RAM or flash), needs security:**
```
10 89                                  ; session where 0x27 is allowed
27 03            -> 67 03 <seed>       ; level-2 seed
27 04 <seed+0x11170>  -> 67 04 34      ; unlock level 2 (state 3)
10 86                                  ; session 4 (upload); note: resets security to 0, fine
35 <addr:3> 00 <size:3>  -> 75 3F      ; request upload
loop:  36  -> 76 <=62 bytes>           ; until NRC 0x12 / size exhausted
37               -> 77                 ; exit
```
Keep every requested range clear of **0x7F9E3C–0x7FA47F**. Split large regions
so no single range crosses that window (read up to 0x7F9E3B, skip to 0x7FA480).

---

## 9. Open questions / not settled

* Level-3 (0x27 sub 0x05/0x06) key: seed = timebase×0x25D8B91F, key =
  seed + a 16-bit value returned by the co-routine `FUN_004149cc` (a
  command/response state machine at 0x4149CC, likely the on-chip
  crypto/immobiliser helper). Not needed for logging; not reversed further.
* Exact P3/keep-alive timeout value (used the conventional figure).
* `kwp_transfer_mode4` (0x480000 window) internal segment semantics — mapped
  enough to know it is not the RAM route; not fully decoded.
* The transport-layer framing (who adds `SID+0x40` / `7F`) lives below these
  handlers and was taken as standard KWP2000; not disassembled here.

## 10. Reproduction

```bash
# static: dispatcher, handlers, helpers (needs the analysed Ghidra project)
ghidra_scripts/b3_decompile.py 0x36210 0x34d28 0x35f6c 0x360c4 0x37434 \
    0x36bc0 0x13cf38 0x478460
ghidra_scripts/b3_decompile.py 0x13e98c:x160      # the dispatcher (fn-ptr target)
# tables / constants straight from the bytes
python3 tools/find_abs_refs.py data/passat_azx_ori.bin --target 0x803ddc
# dynamic (emu/ Unicorn harness)
python3 tools/kwp_seckey_verify.py   # seed/key algorithms   -> RESULT: PASS
python3 tools/kwp_upload_verify.py   # upload gate + 62-byte streaming -> RESULT: PASS
```

## 11. Dynamic test method

`tools/kwp_seckey_verify.py` and `tools/kwp_upload_verify.py` call the real
handlers out of the dump with `emu.Med9Emu`, seeding only the RAM globals each
success path needs (seed, level flags, security state, the I/O struct) — no ECU
and no time base (the success paths never read it). See those files for the
exact buffers; results are quoted in §3.2 and §5.4.

---

## 12. Corrections and additions from brief C3 (2026-09-16, issue #20)

Found while building `logging/ecu_sim.py`, which drives the **real handlers**
out of the dump with one persistent `emu.Med9Emu` (RAM survives between
requests) instead of one emulator per call. Reproduce every item below with

```bash
python3 -m unittest tests.test_med9kwp -v          # the whole set, end to end
python3 logging/ecu_sim.py --self-test             # the handler probes alone
```

### 12.1 The handler reads the sub-function from the I/O struct, not the buffer

`kwp_sid_21_h1` (0x35F6C) takes the local id from **`kwp_io_struct+0xC`**
(`lbz r3,0xc(r4)` at 0x35F84), not from `buffer[0]`, and requires
`kwp_io_struct+6` (request length) > 0. §1.4 documents the field; this note
records that at least one handler *only* reads the cached copy, so any harness
that calls a handler directly must fill `+0xB` (SID) and `+0xC` (first data
byte) as well as the buffer. VERIFIED-STATIC (disassembly above) and
VERIFIED-DYNAMIC (with `+0xC` unset, `21 F0` answers NRC 0x11; with it set it
answers `61 F0 <bytes>`).

### 12.2 TesterPresent takes NO sub-function on this ECU — correction to §2.2

§2.2 recommended `3E 01` / `3E 02`. The real handler (0x43A4A8, on-chip,
file 0x2364A8) is nine instructions and does the opposite:

```
0043A4A8  lhz   r12,6(r4)       ; request length
0043A4AC  cmpwi r12,0
0043A4B0  bne   0x43a4c4        ; anything after the SID -> negative
0043A4B4  li    r12,0 ; sth r12,8(r4) ; li r3,1      ; positive, 0 data bytes
...
0043A4C4  lwz   r12,0(r4) ; li r11,0x12 ; stb r11,0(r12)  ; NRC 0x12
```

* `3E` (one byte, nothing after it) -> **`7E`**, status 1, response length 0.
* `3E 01` or `3E 02` -> **`7F 3E 12`** (subFunctionNotSupported).

VERIFIED-STATIC (the listing above) and VERIFIED-DYNAMIC (emulated real
handler; `tests/test_med9kwp.py::TestHandlers::test_tester_present_takes_no_subfunction`).
**The logger and the bench recipes in §8 must send a bare `3E`.** §8 recipe A
is corrected accordingly below.

### 12.3 `21 <group>` returns TWO groups: G and G+0x7F — addition to §6

`kwp21_group_read` (0x3583C) is the group path. It stores the requested group
in `mw_group_requested` (0x7FD05E), calls the four-field reader `0x35748`, then
does

```
00035888  lbz   r12,-0x2f92(r13)   ; 0x7FD05E, the requested group
00035890  addi  r12,r12,0x7f       ; += 127
00035894  stb   r12,-0x2f92(r13)
00035898  lwz   r11,0(r30)         ; buffer
000358A0  add   r3,r11,r10         ; just past the first 12 bytes
000358A4  bl    0x35748            ; read four more fields
```

so the on-wire response is **25 bytes**:

```
61 <G> <4 x (formula,A,B) of group G> <4 x (formula,A,B) of group G+0x7F>
```

* Only groups **1..0x7F (127)** may be requested: 0x3585C rejects `> 0x7F`
  before anything else and the caller turns that into **NRC 0x31**.
  Groups 128..254 are readable **only** as the tail of the response for
  group *G* − 127. (Issue #44 wants group 140 -> request `21 0D` and read
  triples 5..8.)
* Groups 0x50..0x54 take a different reader (`0x46338` instead of `0x35748`)
  for their first four fields; the `+0x7F` second half is the same.
* Formula 0x25 with A=B=0 is the "not implemented" stub 0x038EC4
  (`re/findings/measuring_vars.md` §4), i.e. an empty field.

Verified dynamically (emulated real handlers, RAM seeded):

```
21 01 -> 61 01 (01,C8,20)(05,0A,7C)(14,32,00)(14,32,00) (25,00,00)x4
           group 001 = ids 1,80,28,29            group 128 = 813,826,0,827
21 03 -> 61 03 (01,C8,20)(19,FF,FF)(21,FF,00)(1B,4B,80) (05,0A,7C)(05,0A,34)(25,00,00)(25,00,00)
           group 003 = ids 1,10,7,9              group 130 = 80,480,0,0
21 8C (140) -> 7F 21 31
```

The id-80 triple `(05,0A,7C)` appears in group 001 field 2, group 004 field 3
and group 130 field 1 — the same variable, the same triple, three ways in.
VERIFIED-STATIC (0x3583C) + VERIFIED-DYNAMIC (emulated).

### 12.4 `kwp_sid_10_h1` (0x3716C) is the **K-line** session handler

It cannot be emulated and, for the CAN route, does not have to be. With
request length 1 it accepts **only sub-functions 0x81 and 0x89** — the
predicate at 0x36B48 is literally

```
00036B48  cmpwi r3,0x81 ; beq .. ; cmpwi r3,0x89 ; bne .. ; li r3,1 ; blr
```

— every other sub-function falls into the length-2/length-5 branch at
0x371D4, which is a **baud-rate table**: sub 1..6, 0x14, 0x87, 0x89, 0xA7,
0xA9 select 9600 / 19200 / 38400 / 57600 / 115200 / 10400 (0x2580, 0x4B00,
0x9600, 0xE100, 0x1C200, 0x28A0) and then reprogram the serial hardware.
Under the Unicorn harness that path reaches the OS halt spin at **0x110F0**
(`b 0x11114`) and never returns, so `logging/ecu_sim.py` does **not** run
0x3716C. It calls the real one-line setter `kwp_session_set` (0x13CEE4,
`stb r3,0x803D3E`) with the internal session number from the §2 table and
enforces the §2 prerequisites (0x86 needs `kwp_security_state` == 3) in
Python. HYPOTHESIS: on the CAN/TP2.0 route the session is started by a
different entry point than the dispatch-table handler for 0x10, because the
baud-rate branch is meaningless there; not chased further (time-boxed).

### 12.5 Corrected recipe A (replaces §8 A)

```
10 89                                  ; session 5
2C F0 04                               ; clear F0
2C F0 03 01 01 A2 A1 A0                ; pos1, 1 byte @0x00A2A1A0 ... (<=20 chunks)
loop:  21 F0   -> 61 F0 <bytes>        ; sample
       3E      every ~2 s              ; keep alive -- NO sub-function (12.2)
```

### 12.6 Emulator limits that show up in the simulator

* **The security seed is always 0.** `read_time_base` (0x478460) reads SPR
  TBU/TBL, which the Unicorn 603e does not advance, so the real seed path
  stores 0 in `kwp_sec_seed` (0x7FB774) and the key is 0x11170. The
  *verification* of the key is still the real code, so `logging/ecu_sim.py
  --seed 0x12345678` overwrites 0x7FB774 after the real seed handler has run
  and reports that value on the wire; the subsequent `27 04` is checked by
  `kwp_sid_27_h1` against it, exactly as `tools/kwp_seckey_verify.py` does.
* `kwp_sec_level_flags` (0x7FB781) and the LFSR round count (0x7FB770) are
  BSS in the dump and are seeded at simulated power-on (0x03 and 5), as the
  verify tools do; on a real ECU the application sets them.
