# The RAM-resident bootstrap loader: entry, transport, and what it can rewrite

Agent **F5**, brief `docs/agent_briefs/F5_ram_bootstrap_loader.md`, issues
**#26 #28**, risk table of **#2**, follow-up to
`re/findings/flash_programming.md` §8. Date **2026-09-22**. Dump
`data/passat_azx_ori.bin`, SHA-256
`b15590d3f1874ace3125c5d047c09a686db9b8bb498187663539ebab205609b3`
(`./.venv/bin/python3 tools/checksum.py verify -q` -> `ALL OK (65 blocks)`
before and after).

All addresses are **CPU addresses**; `file` prefixes a file offset. In the
external flash 0x000000-0x1FFFFF the two are equal. The loader runs in internal
SRAM: **code at flash offset `f` in 0x019798-0x02A827 runs at
`0x7F8728 + (f − 0x019798)`** (relocation delta **0x7DEF90**). Tags per
`docs/README.md`. E6 mapped the *application-side* OBD route
(`flash_programming.md`); this file is the *other* route.

Reproduce the tables with

```bash
./.venv/bin/python3 tools/flash_segments.py data/passat_azx_ori.bin --loader
```

and any disassembly with (RAM address on the left, flash offset for `--file-off`)

```bash
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin \
        --file-off 0x<flash> --addr 0x<ram> --len 0x<n>
```

---

## 0. The answer, in one table

**VERIFIED-STATIC.** There are **two** flash-programming routes in this image.

| | application route (E6) | **RAM bootstrap loader (this file)** |
|---|---|---|
| where it runs | flash driver relocated to RAM 0x804800, KWP stack in flash 0x086000 | **whole loader relocated to RAM 0x7F8728** |
| entered by | `10 85` reset + magic 0xAABFFB11 at 0x7F8020 | boot magic at 0x7F8000, **or** an external-tool marker |
| transport | KWP over TP2.0 (CAN) and K-line | **QSMCM SCI1 — asynchronous serial only, no CAN** |
| address filter | **whitelist**, exact (start,end) match (0x0889C8) | **blacklist** of three windows (RAM 0x7FAA9C / 0x7FB280) |
| can write the calibration | yes (variant-gated) | **yes** |
| can write on-chip 0x404000-0x47FFFF | yes | **yes** |
| can write the prog module 0x080000-0x09FFFF | **no** (refused) | **yes** |
| can write the boot body 0x010000-0x01FFFF | no | **no** (protected) |
| can write the reset stub 0x000000-0x001FFF | no | **no** (protected) |

> **The RAM loader is the only route in the image that can rewrite the resident
> programming module (0x080000-0x09FFFF).** It cannot rewrite the reset stub
> (0x0-0x1FFF) or the boot body (0x10000-0x1FFFF) — it protects those itself —
> so those two remain **BDM-only**. It speaks a **serial (SCI) line, not CAN**.

---

## 1. How the boot reaches the loader

`boot_mode_select` (0x012ED4) decides, near the end of `boot_main_init`, whether
to hand control to the application (the `blrl` at 0x01307C, `boot.md` §2.3) or
to the loader. Two of its arms reach the loader; both call
`boot_enter_prog_mode` (0x012658) — which runs the third DECRAM routine and
**tail-calls `uc3f_unprotect` (0x011CE0)**, unprotecting the on-chip array and
clearing the CS0 write-protect — then branches to **`bl 0x7F8728`**
(0x013088). **VERIFIED-STATIC** (blobdis 0x012ED4-0x013090, 0x012658-0x012708).

| arm | test | writer of the deciding cell | OBD-reachable? |
|---|---|---|---|
| 0x01270C | byte **0x7F8010 ∉ {0, 0x10}** AND the two DECRAM CS2 pointers 0x6F8404/0x6F8408 dereference to a magic (0x1228: 0x2EE2 / 0x3DD3 / 0x4CC4 / 0x55555555) | **only the boot writes 0x7F8010, and only to 0 or 0x10** (0x011F3C / 0x011F4C); a third value must come from outside | **no — BDM/emulator only** |
| **0x012780** | word **0x7F8000** equals one of 0xBB44E169, 0xA5BCD193, 0xBD5593F3, 0xE45CD91A, 0x356BD372; the cell is then zeroed | the **firmware itself** on a fault: bad calibration marker (0x06DFB8), fault counter (0x020D90), boot re-entry (0x0130A4) | **indirectly — see §5** |

### 1.1 The 0x7F8010 arm is a BDM / plug-in-tool door, not an OBD one

`0x7F8010` is read at 0x01270C but written **only** by the boot, at
0x011F3C (`= 0x10`) or 0x011F4C (`= 0`), decided by `0x011980`. `0x011980`
probes external memory at **0x900000** by writing 0x5AA53CC3 / 0xA55AC33C and
reading it back (blobdis 0x011980): `0x7F8010 = 0x10` means a device answers at
0x900000 (a plug-in emulator/BDM pod with RAM), else `0x10 = 0`. **Neither value
is ∉ {0, 0x10}.** So the 0x01270C arm can only fire when some agent other than
this boot has written a third value into the standby CALRAM cell 0x7F8010 (and
set up the two DECRAM CS2 pointers) — i.e. a hardware tool on the BDM/READI port
or on the CS2 memory bus. `0x900000` is referenced only by boot code
(0x011060, 0x011980, 0x011E74, 0x012158, 0x012E80). E6's label "external-tool
bootstrap" is correct: **it is not reachable from the OBD connector.**
**VERIFIED-STATIC.**

### 1.2 The 0x7F8000 arm is the software / recovery door

`boot_check_reprog_magic` (0x012780) is the path a *running firmware* uses to
ask for the loader on the next reset. The five magics and their writers are in
`flash_programming.md` §5.1; the one that matters for recovery is
**0xBB44E169**, written by `app_check_cal_marker` (0x06DD3C, at 0x06DFB8) when
the calibration block lacks its `5A5A5A5A` marker (§5 below). No KWP service
writes 0x7F8000 directly; a tester reaches this arm only by *leaving the ECU in
a state the firmware reacts to*. **VERIFIED-STATIC.**

### 1.3 The dead third arm

`boot_mode_select`'s 0x012894 arm copies the third DECRAM routine, calls
`uc3f_unprotect`, then does an indirect call through the word at **0x1C0120**,
which is 0xFFFFFFFF in this dump (`boot.md` §2.1, §5). It jumps to 0xFFFFFFFF
and is dead in a stock image. Not a route to anything.

---

## 2. The loader's own start-up

`0x7F8728` (blobdis --file-off 0x019798 --addr 0x7F8728) runs before it parses
any command:

1. seeds its state (session byte **0x7F842C = 2**, 0x7F86C0 = 0, a "started"
   flag 0x7F842C-family byte 0x7F8434 = 1), captures the time base;
2. calls its transport and driver init (`bl 0x7FA7AC`, `0x7FA1C8`, `0x7F9690`,
   `0x7F8D14`) — §3;
3. if the "external RAM" flag (0x7F8434) is set and DECRAM 0x6F840C ≠
   0xDEADBEEF, re-selects the code directory and may re-enter (0x7F87AC-0x7F87BC
   call the boot's 0x12D4C / 0x12B34);
4. runs `boot_swsr_service` at RAM 0x7FD7B8 to pet the watchdog (the boot copies
   it there; blobdis --file-off 0x110D0 --addr 0x7FD7B8);
5. enters an endless **receive → dispatch → respond** loop (0x7F8788-0x7F88BC),
   pumping the SCI driver and calling the command dispatcher `0x7FB9F0` on each
   framed request, petting the watchdog every pass.

So the loader is a small stand-alone monitor: it never returns to the boot and
relies on the watchdog service to stay alive. **VERIFIED-STATIC.**

---

## 3. Transport: SCI1, not CAN

The loader's I/O device is set by `0x7FA1C8`: it stores **0x705000** (the QSMCM
module base) to its device pointer 0x7F8558 and marks the port type
(0x7F8560 = 0x0A). Every character routine (0x7FA850-0x7FA974) guards on
`0x7F8560 == 0x0A` and then touches the **SCI1** registers of the QSMCM
(`mpc5xx_registers.md` §7):

| loader access | QSMCM offset | register | meaning |
|---|---|---|---|
| `0x7FA0F4` init: writes +0x08, sets bits in +0x0A | +0x08 / +0x0A | SCC1R0 / SCC1R1 | baud (SCBR) + control (TE, RE, M, PE) |
| `0x7FA874` rx: reads +0x0C, +0x0E | +0x0C / +0x0E | SC1SR / SC1DR | status + data |
| `0x7FA8A0` rx-ready: reads +0x0C bit 6 | +0x0C | SC1SR | RDRF (receive data register full) |
| `0x7FA95C` tx: writes +0x0E | +0x0E | SC1DR | transmit a byte |

The baud helper `0x7FA978` computes the divisor from the **system-clock byte at
0x7F8025** (`mulli x,0xF4240` = ×1 000 000, then `divwu`), i.e. the loader sets
its own baud from the measured f_sys. **No TouCAN register (0x707000/0x707400/
0x707800) is referenced anywhere in the loader image** (checked over the whole
0x019798-0x02A827 blob), and no QSPI (0x705018+). The loader also programs
QADC_A (0x704800, `0x7FA5C8`) — reading an analog input, plausibly a
programming-voltage pin — and touches only USIU/UC3F control otherwise.

> **The loader speaks an asynchronous serial line (SCI1 on the QSMCM), not
> CAN.** Which physical pin SCI1 is bonded to (the OBD K-line, or a bench-only
> header) is a wiring question the dump cannot answer; record it for the bench.
> **VERIFIED-STATIC** for "it is SCI1 and not CAN"; the pin mapping is
> **HYPOTHESIS (K-line)**.

---

## 4. The command interface

### 4.1 Dispatcher and framing

The dispatcher `0x7FB9F0` walks a **16-entry command table** (RAM **0x7FD510**,
flash 0x01E580; `tools/flash_segments.py --loader`). Each entry is 20 bytes:

```
+0  u8  SID          +1  u8  sub-function (0xFF = any)
+2  u16 session mask  (accepted iff  (1 << current_session) & mask)
+4  u32 security mask (accepted iff  (1 << current_level)   & mask)
+8  u32 handler (RAM)  +0xC u32 h2   +0x10 u32 arg
```

`current_session` starts at **2** (set at loader entry, 0x7F8742), so the
`session mask 0x0004` on the write commands is satisfied from the start.
`current_level` starts at 0 (locked); the write/erase commands carry **security
mask 0x06** = level 1 **or** 2, so a successful `27` is required first (§4.3).

| idx | SID sub | service | handler (RAM) | gate |
|---|---|---|---|---|
| 0,14 | 3E — | TesterPresent | 0x7FB8C4 | none |
| 12 | 81 — | StartCommunication | 0x7FAEAC | none |
| 1 | 82 — | StopCommunication | 0x7FB484 | none |
| 2 | 10 — | StartDiagnosticSession | 0x7FAF24 | none |
| 3 | 83 — | AccessTimingParameters | 0x7FB064 | none |
| 13 | 27 — | **SecurityAccess** | 0x7FB588 | none |
| 7 | 1A — | ReadEcuIdentification | 0x7FB07C | none |
| **4** | **34 —** | **RequestDownload** | **0x7FAA9C** | **sec 1/2** |
| **5** | **36 —** | **TransferData** | **0x7FACA4** | **sec 1/2** |
| **6** | **37 —** | **RequestTransferExit** | **0x7FADF8** | **sec 1/2** |
| **8** | **31 02** | **StartRoutine 02 = erase** | **0x7FB280** | **sec 1/2** |
| 9,15 | 33 02 | RequestRoutineResults 02 | 0x7FB928 | sec 1/2 |
| 10 | 31 01 | StartRoutine 01 (setup, arg 0x7FD658) | 0x7FBB14 | sec 1/2 |
| 11 | 33 01 | RequestRoutineResults 01 | 0x7FBBCC | sec 1/2 |

The set mirrors the application programming stack (`flash_programming.md` §1):
`27` unlock, `34`/`36`/`37` for download, `31 02` to erase, `33` for results.
**Data gets in through SID 0x34/0x36** here too. **VERIFIED-STATIC.**

### 4.2 The write commands' address filter — a blacklist of three windows

Both the download entry `0x7FAA9C` (SID 0x34) and the erase entry `0x7FB280`
(`31 02`) compute `start` (bytes 0..2 of the request) and `end`, and refuse with
**NRC 0x42** if `[start, end]` overlaps **any** of these, taken verbatim from
the `lis`/`addi` immediates in both handlers (blobdis 0x7FAA9C / 0x7FB280):

| protected window | size | what it holds |
|---|---|---|
| **0x000000-0x001FFF** | 8 KB | CS0 parameter block 0: reset stub (`boot_start` 0x1004), exception tables, the reset configuration word |
| **0x010000-0x01FFFF** | 64 KB | CS0 main block 0: the bulk of the **boot module** (0x011524-0x01978F) and the loader's own source (0x019798-0x01FFFF) |
| **0x400000-0x403FFF** | 16 KB | **UC3F small block 0**: shadow row / reset config word |

**Everything else the device table covers is accepted.** In particular the
loader **will** erase and program:

* **0x080000-0x09FFFF — the resident programming module** (the OBD route refuses
  this outright);
* **0x020000-0x07FFFF and 0x0A0000-0x1BFFFF** — all application code;
* **0x1C0000-0x1FFFFF** — the whole calibration, with **no variant gate** (the
  OBD route gates it on 0x7FD328);
* **0x404000-0x47FFFF** — the on-chip array (except small block 0);
* **0x002000-0x00FFFF** — CS0 parameter blocks 1-7 and 0x020000-0x02A827, the
  loader's own upper source (harmless: it already runs from RAM).

This is the opposite design from the OBD route: **permissive by default, with
three hard exclusions** that keep the machine bootable. Note the boot module and
reset stub are excluded *by the loader itself* — confirming that not even the
loader can rewrite them over the connector. **VERIFIED-STATIC.**

`SID 0x36 TransferData` (0x7FACA4) then streams the data to the program op
`0x7FD300` in ≤0xFE-byte chunks (NRC 0x40 if no active download, 0x43 if too
much); `31 02` (0x7FB280) walks the device table's erase op `0x7FCF0C` per
overlapping device. **VERIFIED-STATIC.**

### 4.3 SecurityAccess (0x7FB588)

Same shape as the application's: `27 01` returns a seed, `27 02 <key>` grants a
level. The seed is produced by an LCG (`0x7FA5C8`: `seed = seed*0xE89 + 1`,
called twice for 32 bits), and the key is verified by `0x7FB4F8`, a
polynomial-LFSR transform whose polynomials live at flash **0x005F80**
(`0x7FB4B0` reads `[0x5F80]` or `[0x5F84 + idx*4]`). On success it sets the
granted-level cell (used by the dispatcher's security mask) and remembers the
unlock across the session (0x7F8000-relative flags at 0x7FE9E0/0x7FE9F0). A
delay timer throttles retries (NRC 0x33 on a bad key, timing captured with
`mftb`). The polynomial/seed detail is **VERIFIED-STATIC**; whether the *key
values* equal the application's level-1 key (`kwp.md` §3) was not computed here
(the seed algorithm differs — LCG vs the app's Galois LFSR — so the keys are
**not** assumed equal). **HYPOTHESIS: a bench `27 01`/`27 02` capture on the SCI
line is the cheapest way to settle the key.**

---

## 5. Recovery: which failures the connector can still fix

This is the section the 2026-09-22 hardware plan (#2, `docs/01` §4 Phase 0, M1)
needs. For each failure the plan worries about, "the connector" means *a tool on
the car's diagnostic connector, no case opened*; "BDM" means the READI/BDM port
with the ECU on the bench. **VERIFIED-STATIC** unless noted.

| failure during a write of… | what the ECU does on the next power-up | recoverable over the connector? |
|---|---|---|
| **application code** 0x020000-0x07FFFF / 0x0A0000-0x1BFFFF | app image is broken, but the **calibration marker 0x1E2500 is still `5A5A`**, so `app_check_cal_marker` passes and the boot hands over to a **crashing application** — which does **not** auto-enter the loader. The OBD programming stack (reached by `10 85`, which lives in the intact prog module 0x080000-0x09FFFF) is still there. | **yes, over CAN** via the normal `10 85` OBD route (E6) — the loader is not even needed |
| **calibration** 0x1C0000-0x1FFFFF (marker erased/wrong) | `app_check_cal_marker` (0x06DFB8) fails, writes magic 0xBB44E169 to 0x7F8000, hangs; watchdog resets; `boot_mode_select` 0x012780 sees the magic and **auto-enters the RAM loader**. The loader can reprogram the calibration (no variant gate). | **yes — automatically**, but only over the loader's **SCI/serial** line, not CAN. A CAN-only tester cannot talk to the loader. |
| **on-chip array** 0x404000-0x47FFFF | if the calibration marker is intact the app still boots (into a crash if the corrupted block is code); the OBD route (`34` to 0x404000-0x47FFFF) can rewrite it, and so can the loader. If a corrupted on-chip vector faults early, the fatal handler hangs → watchdog → boot, but nothing sets the loader magic, so it loops through the boot without entering the loader. | **yes for a re-writeable app** via OBD `10 85` + `34`; **no automatic loader entry** — the on-chip case does not set the magic |
| **a wrong `5A5A` marker** (the specific case #26 asks about) | identical to the calibration row: the firmware **reboots straight into the RAM loader** and stays there (`flash_programming.md` §5.3b, confirmed: it is the 0x012780 arm, *not* the 0x01270C arm). | **yes — automatically**, over the loader's serial line |
| **the programming module** 0x080000-0x09FFFF | the app still boots (marker intact), but `10 85` now jumps into a **broken** prog module → likely a fault → watchdog → boot, with no loader magic set. The OBD route is dead. **Only the RAM loader can rewrite 0x080000-0x09FFFF**, and it is not auto-entered in this case. | **only if the loader can be entered** — and the sole non-BDM entry is the 0x7F8000 magic, which this failure does not set. So in practice **BDM**, unless the calibration marker is *also* invalidated on purpose to force loader entry (see note) |
| **the boot body** 0x010000-0x01FFFF **or reset stub** 0x000000-0x001FFF | cannot happen through either firmware route (the OBD route refuses <0x020000; the loader protects both windows). Only a BDM write can corrupt them, and only a BDM write can repair them. | **BDM only** |

**The one deliberate trick worth recording:** a programming-module corruption
(row 5) is recoverable over the connector *without BDM* if the operator first
erases the calibration marker (row 4 / row "wrong 5A5A"), which forces automatic
loader entry; the loader — reachable then over its serial line — can rewrite
both 0x080000-0x09FFFF and the calibration. This turns a "BDM-only" case into a
"serial-connector" case, at the cost of needing a tool that speaks the loader's
SCI protocol. It is **HYPOTHESIS** as a *procedure* (never executed here) but
each step is VERIFIED-STATIC.

**Bottom line for the plan.** Nothing short of overwriting the boot body or
reset stub bricks the ECU beyond the connector — and neither firmware route can
touch those, so only a BDM slip could. Every *data/calibration/app/on-chip/
prog-module* failure is recoverable without opening the case **provided the
recovery tool can drive the loader's serial line** (for the auto-loader cases)
or CAN (for the still-booting cases). The BDM/K-TAG purchase in M1 is justified
**as insurance against the boot/stub case and as the only tool that can read the
missing 16 KB at 0x400000-0x403FFF** (`docs/02` §2), not because ordinary flash
failures are unrecoverable over the wire.

---

## 6. §8 rows of `flash_programming.md` this brief settles or narrows

* **"What does the RAM bootstrap loader allow?"** — **SETTLED.** §4.2 above:
  a blacklist of 0x0-0x1FFF, 0x10000-0x1FFFF and 0x400000-0x403FFF; everything
  else, including 0x080000-0x09FFFF and the calibration, is writable. Transport
  is SCI1 (§3). Entry is the 0x7F8000 magic (software/auto) or the 0x7F8010 +
  DECRAM path (BDM only) (§1).
* **"Meaning of the selector byte 0x7FD328 (0x11 / 0x33 / other)"** —
  **NARROWED.** It is written by an on-chip function at 0x458F7C
  (`stb`, `sda_xref --var 0x7FD328`) and read only by the OBD-route whitelist
  (0x0889C8, E6 §2.4); its value is a market/variant coding, so it selects which
  calibration sub-ranges the **OBD** route will download. The **loader ignores
  it** (its filter is the §4.2 blacklist). Full decode of the writer's source of
  the value is left open (it reads a coding cell in on-chip flash).
* **"Which consumer turns `boot_mode_flags` bit 2 (0x7FD401) into start the
  programming KWP stack?"** — **NARROWED, with a correction to E6.** The bit-2
  getters 0x04CC34-0x04CC7C have **no** callers at all
  (`find_branch_refs`), i.e. they are dead in this image. E6 §2.2 offered the
  chain 0x087494 → 0x089E9C → 0x085FEC → `prog_kwp_init`; **0x087494 is not part
  of that chain** — it is the SID `31` routine-**0xC5** handler, dispatched by
  local id at 0x087B00 (sibling of 0x873C0 for routine 0xC4;
  blobdis 0x087B00). The programming stack is actually registered by
  `prog_kwp_init` (0x08C244), called from the programming-module init that
  starts at **0x085FEC** (which contains 0x086000; entry reached via 0x08A110).
  How 0x085FEC is entered on the post-`10 85` reboot (a code-directory
  selection, `boot.md` §2.3) is the remaining thread; it is an **application**
  route question, deferred to an E6/F3 follow-up. This file does not edit
  `boot.md`.
* **`31 C5` (0x0891C8)** — not pursued (lowest priority, brief); it is an
  application-route routine, unrelated to the loader.

---

## 7. Dynamic confirmation (emulator, task 4)

**VERIFIED-DYNAMIC.** The relocated loader was run in `Med9Emu` (Unicorn) with
the transport stubbed, per the brief's task 4. The loader blob is copied to its
RAM address, DECRAM 0x6F840C is primed with 0xDEADBEEF (what
`boot_enter_prog_mode` writes just before `bl 0x7F8728`, §1), the system-clock
byte 0x7F8025 is set, and SCI1 status (SC1SR 0x70500C) is stubbed to "no receive
data ready":

```python
# run from the repo root with ./.venv/bin/python3 (imports emu/ read-only)
import struct
from emu import Med9Emu
D = open("data/passat_azx_ori.bin", "rb").read()
emu = Med9Emu("data/passat_azx_ori.bin", r2="app", trace=True)
emu.stub_read(0x70500C, 0x0000)          # SCI1 SC1SR: no RDRF -> the loop waits
emu.stub_read(0x704810, 0xFFFF)          # QADC_A conversion-complete
res = emu.run(0x7F8728, max_insns=300000, mem={
    0x7F8728: D[0x019798:0x019798 + 0x5090],   # the loader
    0x7FD7B8: D[0x110D0:0x110D0 + 0x48],        # boot_swsr_service
    0x6F840C: struct.pack(">I", 0xDEADBEEF),
    0x7F8025: b"\x28"})
```

Result: **no faults, no unmapped accesses** (`res.issues == []`); the loader
relocates, runs its init chain, opens **SCI1** (`ldr_sci_open` 0x7FA1C8 is
executed), reaches the command **dispatcher** (`ldr_dispatch` 0x7FB9F0) and then
idles in its receive/dispatch loop (0x7F8788-0x7F88BC) spinning on the stubbed
SC1SR — exactly the "waiting for the first serial command" state. 529 distinct
loader instructions execute before it settles into the wait. This confirms the
relocation model (§0), the SCI transport (§3) and the loop structure (§2)
dynamically. A full command frame was **not** injected — the SCI framing was not
reverse-engineered far enough to trust a synthesised frame — so the address
filter (§4.2) stands on the static evidence only.

---

## 8. Reproduction

```bash
./.venv/bin/python3 tools/checksum.py verify -q data/passat_azx_ori.bin
./.venv/bin/python3 tools/flash_segments.py data/passat_azx_ori.bin --loader
# entry decision and the two doors
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x12ED4 --addr 0x12ED4 --len 0x1B0
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x1270C --addr 0x1270C --len 0x134
./.venv/bin/python3 tools/find_abs_refs.py data/passat_azx_ori.bin --target 0x7F8010
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x11980 --addr 0x11980 --len 0x60
# the loader: entry, SCI transport, dispatcher, the write/erase filters
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x019798 --addr 0x7F8728 --len 0x2A0
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x1B238 --addr 0x7FA1C8 --len 0x2C
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x1B9E8 --addr 0x7FA978 --len 0x48
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x1CA58 --addr 0x7FB9F0 --len 0x120
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x1BB0C --addr 0x7FAA9C --len 0x208
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin --file-off 0x1DF7C --addr 0x7FCF0C --len 0x164
```

> Note for the integrator: `boot.md` is owned by brief F3 this pair. F3 should
> add, at its §5 loader note, a pointer: *"the loader's command interface,
> transport (SCI1) and address filter are in `re/findings/ram_loader.md`
> (F5)."* This file deliberately does not edit `boot.md`.
