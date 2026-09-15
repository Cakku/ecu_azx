# emu/ — Unicorn emulation harness

Issue **#21** (Unicorn half). Runs code out of `data/passat_azx_ori.bin` in the
address space the firmware really sees, so that a function can be unit-tested
without an ECU. Nothing in the image is ever patched: peripherals are
zero-filled stub pages plus read hooks.

> The **Ghidra `EmulatorHelper`** half of #21 is not here. It needs the Ghidra
> project and setup script from brief **A1**; do it after A1 lands.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # unicorn 2.1.x is the only hard dependency
python3 -m unittest discover -s tests -v  # the whole repository test suite
python3 -m emu.boot_trace                 # boot-from-reset report
```

## Using it

```python
from emu import Med9Emu

emu = Med9Emu("data/passat_azx_ori.bin", r2="app")     # or r2="boot" / r2=0x17FF0
res = emu.call(0x11E44, args=[0xFF800140], mem={0x7FE9E8: b"\x01"})
res.r3                 # 0xFF800040   (EABI return value)
res.ok                 # True: the function returned instead of faulting
res.snapshot(0x7F8000, 0x40)           # memory after the call
res.accesses           # every peripheral read/write, with pc and value
res.issues             # unmapped accesses, exceptions, unmodelled SPRs
```

* `call(addr, args, regs=..., mem=..., r2=..., msr=..., max_insns=...)` puts the
  arguments in r3..r10, parks a magic return address in LR and runs until the
  function returns. `regs` overrides any register by name (`"r5"`, `"lr"`,
  `"ctr"`, `"cr"`, `"xer"`, `"msr"`); `mem` writes `{address: bytes}` first.
* `run(start, until=..., ...)` free-runs a stretch of code (used for the reset
  trace and the DECRAM path).
* `stub_read(addr, value_or_callable)` makes a peripheral read yield a value.
  The callable form gets `(pc, addr, size)`, so a register can change over time.
* `Med9Emu(..., trace=True)` records the pc of every instruction;
  `watch_spr=True` decodes `mfspr`/`mtspr` and flags the ones Unicorn does not
  implement. Both slow the run down; leave them off for plain unit tests.
* Unmapped **data** accesses are logged and backed with a zero page so one
  stray pointer does not end the run; unmapped **instruction fetches** stop it.
  Neither raises.

## The memory map (`emu/memmap.py`)

From `docs/02_memory_map.md` section 3. The boot code relocates the internal
map to 0x400000 (ISB=1) at file 0x1004, so every peripheral address here is the
MPC5xx datasheet address **+ 0x400000**.

| CPU range | Kind | Contents |
|---|---|---|
| 0x000000-0x1FFFFF | flash | external flash, file 0x000000-0x1FFFFF |
| 0x400000-0x403FFF | absent | protected sector KESSv2 does not read; zero, logged |
| 0x404000-0x47FFFF | flash | on-chip flash, file 0x200000-0x27BFFF |
| 0x5C0000-0x5FFFFF | flash | DMBR/DMOR dual-mapped window onto file 0x1C0000-0x1FFFFF (calibration); 0x480000-0x5BFFFF is unbacked |
| 0x6F8000-0x6F8FFF | ram | DECRAM (only the first 2 KB is real) |
| 0x6FC000-0x6FCFFF | periph | USIU (page also holds UC3F flash control at 0x6FC800) |
| 0x700000-0x70FFFF | periph | IMB3: TPU3 A/B, QADC A/B, QSMCM, MIOS14, TouCAN A/B/C, UIMB |
| 0x780000-0x780FFF | periph | CALRAM control |
| 0x7F8000-0x807FFF | ram | on-chip SRAM + external SRAM on CS1 |
| 0x900000-0x93FFFF | periph | CS2 device (identity unknown) |
| 0xA00000-0xA07FFF | periph | CS3 device (identity unknown) |
| 0xFFF00000-0xFFF0FFFF | flash | mirror of the first 64 KB, because MSR[IP]=1 fetches vectors there (**HYPOTHESIS**, docs/02 section 3) |

Register defaults, all taken from the firmware's own setup: `r1 = 0x7FEFFC`,
`r13 = 0x7FFFF0`, `r2 = 0x5C9FF0` (application) or `0x17FF0` (boot, pass
`r2="boot"`), `MSR = 0x3942` (FP=1, ME=1, FE0/FE1=1, IP=1, EE=0).
CPU model: `UC_CPU_PPC32_603E_V4_1`.

## What the harness has established

Run `python3 -m emu.boot_trace` to reproduce; the findings are written up in
`re/findings/emulation_boot_path.md`.

* **`boot_or_adjust` (0x11E44)** clears **bit 0x100** of its argument when the
  RAM byte at 0x7FE9E8 is exactly 1, otherwise returns it unchanged. Derived
  from the instruction word 0x5463062C (`rlwinm r3,r3,0,24,22`: MB=24 > ME=22,
  so the mask is every bit except bit 23 = 0xFFFFFEFF) and confirmed by
  running it. The results go into OR0/OR1/OR2/OR3 (USIU 0x6FC104/0x10C/0x114/
  0x11C), so the bit is the OR-register **BI / burst-inhibit** field
  (field name COMMUNITY, from the MPC5xx manual; the bit position is
  VERIFIED-STATIC). **0xFF800650 -> 0xFF800650, not 0xFF800550**: that value has
  bit 0x100 clear already.
* **Boot from 0x100** reaches 0x116FC after ~14 k instructions and spins there:
  it sets PLPRCR (0x6FC284) bit 0x8000 at 0x116FC and polls it at 0x11704 until
  it reads 0 — the PLL lock-status bit. That is the first peripheral value the
  map does not provide. The SIPEND poll at 0x1048-0x1054 is *not* a blocker:
  the zero stub already means "nothing pending".
* With one read hook reporting the PLL locked, the boot programs BR0-BR3 and
  OR0-OR3, runs the DECRAM routine, sets up UC3F, loads what looks like TPU3
  code into 0x702000-0x7027FF and then stalls in a **TPU3_A parameter-RAM scan**
  at 0x14604-0x146C4 that repeats until every channel reports a plausible
  value. Modelling the TPU3 is the next step if the boot has to run further.
* **The DECRAM path (0x120C8-0x120FC)** copies 0x238 bytes from file 0x11118 to
  0x6F8000 byte-for-byte and calls it; the routine runs 0x6F8000 -> 0x6F8234 and
  returns. It passes 0x6F80B8, where the old
  `med9_re/old_work/emulator.py` died, with no unmapped access, for every value
  of the flash-type nibble at 0x7F800C.

## Unicorn limitations hit here

Also summarised in `docs/03_tooling.md` section 5.

1. **No MPC5xx SPRs.** Unicorn runs a QEMU **603e**. `mfspr` of an SPR the 603e
   does not implement returns 0 and `mtspr` is swallowed — silently, with no
   trap. That includes **SPR 638 (IMMR)**, so the ISB=1 relocation the firmware
   performs at 0x1004 has no effect and the harness has to hard-code the
   relocated addresses; and **SPR 560 (IC_CST)**, 158, 568, 792, 824. Worse,
   SPR 528-543 *do* exist on a 603e (IBAT/DBAT) but mean something else on the
   MPC5xx, so those writes land in the wrong model. Use `watch_spr=True` to see
   them; `Med9Emu.MODELLED_SPRS` is the list the harness trusts.
2. **No peripherals at all.** USIU, TPU3, QADC, QSMCM, MIOS, TouCAN and the CS2
   and CS3 devices are zero-filled RAM. Anything the firmware polls has to be
   stubbed by hand (`stub_read`). Two are known: PLPRCR bit 0x8000 and the
   TPU3_A parameter RAM.
3. **No memory controller.** BR/OR writes are recorded but change nothing, so
   chip-select sizes, write protection and the aliasing the real part does are
   not modelled. The RAM probe at 0x118E0 reads 0x808000, one word past the
   external SRAM; on the real ECU that aliases back into CS1, here it is
   reported as unmapped and backed with a zero page.
4. **Floating point** works only because `MSR[FP]` is set for you; the reset
   code does it at 0x1040, but a `call()` into application code starts with
   MSR = 0x3942 regardless. FPSCR exception behaviour is the 603e's, not the
   MPC5xx's.
5. **No exception model worth trusting.** MSR[IP]=1 means the real part fetches
   vectors from 0xFFF00000; we mirror the first 64 KB of flash there, which is a
   HYPOTHESIS (docs/02 section 3), and any exception stops the run instead.
6. **No timing.** `max_insns` is the only budget; a `bdnz` delay loop costs real
   instructions (the DECRAM routine spends ~2500 of its 3059 on one).
7. Instruction counting, pc tracing and SPR watching all go through a
   `UC_HOOK_CODE` callback, which costs roughly an order of magnitude in speed.
   `Result.insns` is always available; `trace`/`watch_spr` are opt-in.

For exact PowerPC semantics (FP rounding, SPR values, decompiler agreement),
use the Ghidra `EmulatorHelper` path instead once brief A1 has produced the
project.

## Files

| File | Purpose |
|---|---|
| `memmap.py` | the address map, peripheral names, consistency check against `tools/med9lib.py` |
| `core.py` | `Med9Emu`, `Result`, `Access`, `SprAccess`; also a small CLI (`python3 -m emu.core --call 0x11E44 --arg 0xFF800140`) |
| `boot_trace.py` | `python3 -m emu.boot_trace`: the two-stage boot report above |
| `zw_model.py` | brief B7: bit-exact model of the base-ignition path (`KFZW`, `zwgru_build`); `python3 -m unittest tests.test_zw_model` |
| `start_model.py` | brief B8: bit-exact model of the start path -- the cranking fuel factor `ksta` (`%ESSTT`, 0x41A268) and the start ignition angle `zwstt` (0x431294), with the two flex-fuel insertion points S1 and Z1; `python3 -m emu.start_model` prints both tables, `python3 -m unittest tests.test_start_model` checks them against the real code |
| `../tests/test_emu.py` | the regression tests for all of it |
