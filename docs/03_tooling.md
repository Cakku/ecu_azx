# Tooling and environment

This Mac is an Apple M2 Pro running macOS 26 with an **x86_64 (Rosetta)
Homebrew in /usr/local**. Everything below works under Rosetta; a native
`/opt/homebrew` install would be faster but is not required. Versions were
checked in September 2026.

## 1. Repository Python tools

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 tools/checksum.py verify -q data/passat_azx_ori.bin   # ALL OK (65 blocks)
```

`tools/med9lib.py` is the single source of truth for address mapping. Import
it in every new script instead of hard-coding offsets.

**The one project venv is Python 3.13** (`.venv`, 3.13.8), not the Homebrew
3.14 that is also installed: the jpype wheels shipped with Ghidra 12.1.3 stop
at `cp313`, so PyGhidra cannot be installed on 3.14 at all (issue #5). The
bare `python3` on this Mac is a third interpreter (miniconda 3.9) without the
project's packages, so run everything as `./.venv/bin/python3`. Installed and
working in `.venv`: capstone 5.0.9, unicorn 2.1.4, cantools 44.0.0,
python-can 4.6.1, pyelftools 0.33, pyghidra 3.1.0, jpype1 1.5.2. `pypcode`
and `angr`, if ever added, also want 3.13 (their wheels lag).

## 2. Ghidra

- Install: `brew install ghidra` (formula, currently 12.1.3; the cask no
  longer exists). It pulls `openjdk@21`. Manual zip install also works;
  remove the quarantine attribute before unzipping. Ghidra 12.1.x accepts any
  JDK >= 21 (the installed JDK 24 launches it; 21 is the supported one).
- Language: **`PowerPC:BE:32:default`**, compiler `default`. Do not use VLE,
  e500, 4xx or MPC8270 variants. The classic MPC5xx core is plain 32-bit PPC
  with FPU; most SPRs show as `sprNNN` (638 = IMMR, 560 = IC_CST).
- PyGhidra (Python 3 API) ships with 12.x: install its wheel from
  `<GhidraInstallDir>/Ghidra/Features/PyGhidra/pypkg/dist` into the venv;
  `pyghidra` runs headless by default, `-g` for the GUI.
- Headless import, the form that works (issue #7). `support/analyzeHeadless`
  refuses `.py` post-scripts with `Ghidra was not started with PyGhidra.
  Python is not available`, so start the same analyzer *through* PyGhidra
  (this is what `support/pyghidraRun -H` does internally):

```bash
export GHIDRA_INSTALL_DIR=/usr/local/Cellar/ghidra/12.1.3/libexec
mkdir -p ghidra_projects
./.venv/bin/python -m pyghidra.ghidra_launch \
    --install-dir "$GHIDRA_INSTALL_DIR" \
    ghidra.app.util.headless.AnalyzeHeadless ghidra_projects med9 \
    -import data/passat_azx_ori.bin \
    -loader BinaryLoader -loader-baseAddr 0x0 \
    -processor PowerPC:BE:32:default -cspec default -noanalysis \
    -scriptPath ghidra_scripts -postScript med9_setup.py
```

Two further facts from the first run:

- A `-loader-blockName` option is pointless here: the script deletes the
  imported block and rebuilds `EXT_FLASH`/`INT_FLASH` from the same
  `FileBytes`.
- The **project path must not contain a directory whose name starts with a
  dot**; Ghidra aborts with `Path element starting with '.' is not permitted`.
  That rules out `.claude/worktrees/...`, so agents must put the project
  somewhere else.

Versions in use (2026-09-15): Ghidra 12.1.3 (`brew install ghidra`, bottle
poured, 799 MB), openjdk@21 21.0.12.1, pyghidra 3.1.0, jpype1 1.5.2. A full
import with analysis takes about seven minutes on the M2 Pro.

### 2.1 What `med9_setup.py` does (issue #7, VERIFIED-STATIC)

`ghidra_scripts/med9_setup.py` builds the project; `ghidra_scripts/README.md`
has the command lines.

1. Adjusts the imported block so `EXT_FLASH` covers file 0x0-0x1FFFFF at
   0x000000.
2. Creates `INT_FLASH` at 0x404000 length 0x7C000 from file offset 0x200000
   (`Memory.createInitializedBlock` with the file bytes).
3. Creates `CAL_ALIAS` at 0x5C0000 length 0x40000 as a **byte-mapped block**
   onto 0x1C0000 (`createByteMappedBlock`), so calibration references resolve
   to the same bytes without duplicating code. A mapped block for
   0x480000-0x5BFFFF is **off by default** (`--code-alias`): only three static
   references land there, the hardware does not serve that range
   (`02_memory_map.md` §3), and it would shadow code already mapped at
   0x80000-0x1BFFFF.
4. Uninitialised RAM blocks: `SRAM_INT` 0x7F8000/0x8000, `SRAM_EXT`
   0x800000/0x8000, `DECRAM` 0x6F8000/0x800, `USIU` 0x6FC000/0x400,
   `UC3F_CTL` 0x6FC800/0x20, IMB modules 0x704000-0x707FFF (one block per
   module, names from `02_memory_map.md`), `CS2_DEV` 0x900000/0x40000,
   `CS3_DEV` 0xA00000/0x8000, `CALRAM_CTL` 0x780000/0x40.
5. Register values over all code: r13 = 0x7FFFF0; r2 = 0x5C9FF0
   (`ProgramContext.setValue(register, start, end, BigInteger)`). The boot
   module's r2 = 0x17FF0 is **not** set by this script: its `--boot-r2`
   option covers a blanket 0x1000-0x1FFFF, which also contains 100
   application functions (0x019948-0x01E848) that are not part of the boot
   module. Use `ghidra_scripts/b1_context_and_symbols.py` instead, which sets
   r2 = 0x17FF0 over exactly the twelve boot ranges of `02_memory_map.md` §4
   (issue #8, brief B1).
6. Disassembles the vector table entries (`ba`) at 0x0-0x1000 and the tail
   start 0x404000, creates functions at the KWP handler addresses and the
   checksum/CAN tables as data, then runs auto-analysis. Functions are also
   seeded from the EABI prologue pattern (`stwu r1,-N(r1)` next to `mflr r0`,
   either order) over 0x20000-0x14494F and 0x404000-0x47FFFF: 1,741 + 581
   candidates, 2,308 new functions; without it auto-analysis only reaches what
   the call graph from the seeds covers (`--no-prologue-scan` turns it off).
   The KWP dispatch table is parsed and each non-zero handler becomes a named
   function (`kwp_sid_XX_h1`/`_h2`, 33 in total). **Known gap:** the script
   still reads 24 entries from 0x2B870; the table really starts at 0x2B820
   with 28 entries (`02_memory_map.md` §7, brief B3), so the handlers of SIDs
   0x12, 0x3E, 0x1A and 0x83 are named by `re/symbols.csv` import, not by the
   setup script.
7. Symbols and comments round-trip through `re/symbols.csv` with
   `ghidra_scripts/export_symbols.py` / `import_symbols.py`
   (`04_re_guidelines.md` §4).

EliasTuning/Med9GhidraScripts has a working `med9-install.py` for 2 MB ECUs
(ROM at 0x400000, RAM at 0x600000, same r13/r2) and a `no_globals` cspec that
stops the decompiler folding SDA globals; it was borrowed from, with our map.

Results of a full run on `data/passat_azx_ori.bin` (reproduced twice with
identical counts):

| Check | Result |
|---|---|
| functions in `EXT_FLASH` 0x0-0x1FFFFF | 2,928 |
| functions in `INT_FLASH` 0x404000-0x47FFFF | 1,123 |
| functions total | 4,060 |
| references into `CAL_ALIAS` 0x5C0000-0x5FFFFF | 13,619 references to 7,917 distinct addresses |
| `0x4386D8` | a function, 204 bytes, named `kwp_sid_20_h1` from the dispatch table |
| `0x20004` decompiled | `romcheck_result_flags = 0; DAT_007f824b = 0xff; DAT_007f8248 = 0; DAT_007f8249 = 0xff;` i.e. the r13-relative stores resolve to 0x7F8248-0x7F824B |

### 2.2 Other Ghidra uses

- Version Tracking / `ghidriff` to diff our binary against related dumps
  (e.g. 03H906032 with another software number) and to port symbols.
- `EmulatorHelper` for unit-testing single functions (section 5).

## 3. PowerPC cross compiler for patches

**In use: the official LLVM 23.1.1 binary release**, native arm64, no sudo, no
Homebrew, unpacked at `~/toolchains/LLVM-23.1.1-macOS-ARM64` — which is the
default `LLVM_DIR` of `patches/common/patch.mk` (issue #5):

```bash
curl -L -o /tmp/llvm.tar.xz \
  https://github.com/llvm/llvm-project/releases/download/llvmorg-23.1.1/LLVM-23.1.1-macOS-ARM64.tar.xz
mkdir -p ~/toolchains && tar -xJf /tmp/llvm.tar.xz -C ~/toolchains
export LLVM_DIR=~/toolchains/LLVM-23.1.1-macOS-ARM64
```

The flags, as `patches/common/patch.mk` passes them for every patch:

```bash
$LLVM_DIR/bin/clang --target=powerpc-unknown-eabi -mcpu=603e \
  -msoft-float -ffreestanding -fno-builtin -nostdlib -fno-pic \
  -fno-stack-protector -fno-asynchronous-unwind-tables -fno-common \
  -fomit-frame-pointer -Os -std=c99 -Wall -Wextra -Werror -c hello.c -o hello.o
$LLVM_DIR/bin/ld.lld -m elf32ppc --no-dynamic-linker --discard-locals \
  -T ../common/patch.ld --defsym=PATCH_FLASH=0x00150000 \
  --defsym=PATCH_RAM=0x007FFB00 --defsym=PATCH_RAM_SIZE=256 -o hello.elf hello.o
$LLVM_DIR/bin/llvm-objcopy -O binary hello.elf hello.bin
```

`-msoft-float` keeps the FPU out of hooked contexts (patch code is integer
only; if a float routine is ever needed, the core has an FPU but the hook must
then save FP state). LLVM emits no small data for PPC32 EABI by itself and
has no libgcc for PPC (avoid 64-bit division and float conversions). The
linker script `ASSERT`s that `.sdata`/`.srodata`/`.data` are empty and
`make check` greps the disassembly for r2/r13, so the SDA rule is checked
rather than assumed (`06_patch_pipeline.md` §2). `patches/examples/hello_patch`
is the template: it links at **0x150000** with `.bss` at 0x7FFB00 through the
shared `patches/common/patch.mk` + `patch.ld`.

Notes about this toolchain that cost time, so they are recorded:

- `-msdata=none -G0 -ffixed-r2 -ffixed-r13 -meabi -mno-relocatable` are **GCC
  spellings and clang rejects them**.
- **`llvm-objdump` has no `-b binary`**, so the final blob cannot be
  disassembled with it. `tools/blobdis.py` (capstone) does that job, and
  `--check-sda` fails the build if r2 or r13 appear (`make dump`).
- **`ld.lld` 23.1.1 segfaults** in `Writer::finalizeSections()` if a linker
  script discards a synthetic section (`/DISCARD/ : { *(.got .got2 .plt) }`).
  Leave those out of `/DISCARD/`; `-fno-pic` keeps them empty anyway.
- A linker script needs a space before the colon (`.srodata : {`), otherwise
  lld reports `malformed number`.
- **`--defsym` does not override a linker-script assignment.** The
  `X = DEFINED(X) ? X : <default>;` idiom is evaluated while lld computes
  addresses, and `--defsym` is applied only afterwards — so the output is
  linked at the *default* while the ELF symbol table reports the override.
  `patch.ld` therefore has no defaults and requires all three `PATCH_*`
  symbols.
- **A numeric branch operand is a displacement, not a target.**
  `b 0x0011F02C` assembles to 0x4811F02C = branch to pc + 0x11F02C, and
  `.set sym, 0x11F02C` + `b sym` behaves the same. Only an *undefined* symbol
  resolved by the linker gives the intended relative word. `patches/common/`
  trampolines use `ba` (AA=1) instead, whose operand is the address.
- **Clang preprocesses `.S` files**, so `#include "../common/hooks.S"` and the
  generated `med9_stock.h` work in assembly; the header is guarded with
  `#ifndef __ASSEMBLER__` around everything that is not a number.

### 3.1 Alternatives, and why they are not in use

- **devkitPPC** (GCC 15, target `powerpc-eabi`, macOS binaries) would be the
  GCC route, with `-mcpu=505 -mbig-endian -meabi -msdata=none -G0 -ffixed-r2
  -ffixed-r13 -mno-relocatable -mstrict-align -msoft-float -ffreestanding
  -fno-builtin -nostdlib -nostartfiles -fno-pic -fno-stack-protector
  -fno-asynchronous-unwind-tables -Os`. **It is not installed**: its pkg
  installer needs an administrator password, which an agent cannot supply. If
  the human ever wants it:

  ```bash
  # download devkitpro-pacman-installer.pkg from github.com/devkitPro/pacman/releases
  sudo installer -pkg ~/Downloads/devkitpro-pacman-installer.pkg -target /
  sudo dkp-pacman -Syu && sudo dkp-pacman -S devkitPPC
  export DEVKITPPC=/opt/devkitpro/devkitPPC
  cd patches/examples/hello_patch && make TOOLCHAIN=gcc check
  ```

- **`brew install llvm lld` fails, and will keep failing** under Rosetta:
  Homebrew no longer builds x86_64 bottles, so llvm's dependencies (openssl@3,
  xz) are built from source in the Rosetta prefix and the build breaks with
  `clang: error: unsupported argument 'westmere' to option '-march='`. Do not
  retry it; a native `/opt/homebrew` would work, but the tarball above needs
  neither.
- Docker with Debian `gcc-powerpc-linux-gnu` would work with the GCC flags.
  The Windows-only gnutoolchains build referenced by MED9Toolchain is not
  usable here.

## 4. Disassembly and analysis in Python

- `capstone` 5.0.x: `Cs(CS_ARCH_PPC, CS_MODE_32 | CS_MODE_BIG_ENDIAN)`.
  Pin 5.x; 6.0 changes the PPC API. Never name a script `dis.py` (shadows the
  stdlib and breaks capstone's import).
- `tools/find_abs_refs.py` for `lis`+offset cross references outside Ghidra.
- Binary diff: `cmp -l a.bin b.bin | wc -l` and `radiff2` (`brew install radare2`).
- Map/table finders: Ghidra xrefs from the Bosch interpolation routines are
  the reliable way; TunerPro (under CrossOver/Wine) and WinOLS (Windows VM)
  for editing once a definition exists; `openremap`, `romHEX14`, MxT as
  heuristic helpers.
- Variable table / ECU id parser: **360trev/MED9inf** (C, tested by its author on
  a 3.6 FSI 03H906032DQ), and nubcake's MED9info.

## 5. Emulation

| Use | Tool | Notes |
|---|---|---|
| Unit test of a pure function (map lookup, fuel math) | Ghidra `EmulatorHelper` from PyGhidra | same semantics as the decompiler, FP modelled, SPRs are registers |
| Fast tracing of boot / longer paths | Unicorn 2.1.4 (`UC_ARCH_PPC`, `UC_MODE_32 \| UC_MODE_BIG_ENDIAN`, cpu model 603E) | set MSR[FP] before float code; no MPC5xx SPR model; stub USIU/PLL/watchdog polling; map DECRAM 0x6F8000 (the boot copies code there and calls it) and a CS2 stub at 0x900000 |
| Symbolic questions | angr / pypcode (Python 3.13) | heavy; optional |

QEMU has no MPC5xx machine and is not useful here. The old
`med9_re/old_work/emulator.py` failed because it mapped peripherals at the
ISB=0 addresses and lacked DECRAM and CS2; its register values (r1, r13, boot
r2) were right.

### 5.1 The Unicorn harness (`emu/`, issue #21)

Built and documented in `emu/README.md`; tests in `tests/test_emu.py`
(`./.venv/bin/python3 -m unittest discover -s tests`).
`./.venv/bin/python3 -m emu.boot_trace` prints the boot-from-reset report.
The Ghidra `EmulatorHelper` half of #21 was **not built**: nothing has needed
it (#21 closed 2026-09-15; confirming a lookup against a logged value is #44).
Every emulator proof in this project — the DECRAM routine, the boot trace,
the patch bit-identity tests, the simulated ECU — runs on this harness.

Limitations found in practice, all **VERIFIED-DYNAMIC** by that harness:

| Limitation | Consequence |
|---|---|
| Unicorn runs a QEMU **603e**; MPC5xx SPRs do not exist | `mfspr` returns 0 and `mtspr` is swallowed, **without trapping**. SPR 638 (IMMR) is among them, so the ISB=1 relocation at file 0x1004 does nothing and the harness has to hard-code the relocated map. Also seen unmodelled: 560 (IC_CST), 158, 568, 792, 824. SPR 528-543 *do* exist on a 603e as IBAT/DBAT but are MPC5xx region/control registers here, so those writes land in the wrong model. `Med9Emu(watch_spr=True)` reports every one. |
| No peripheral model at all | USIU, TPU3, QADC, QSMCM, MIOS, TouCAN, CS2/CS3 are zero-filled pages. Anything polled has to be stubbed. Two blockers are known: **PLPRCR (0x6FC284) bit 0x8000**, the PLL lock-status bit the boot sets at 0x116FC and polls at 0x11704; and the **TPU3_A parameter-RAM scan at 0x14604-0x146C4**, which is where the boot stops once the PLL is stubbed. The SIPEND poll at 0x1048-0x1054 needs nothing: 0 already means "nothing pending". |
| No memory controller | BR/OR writes are recorded but change nothing; chip-select sizes, write protection and CS aliasing are not modelled. The RAM probe at 0x118E0 reads 0x808000, one word past the external SRAM, which aliases on the real part. |
| FP only because `MSR[FP]` is preset | The harness sets MSR = 0x3942 for every call; FPSCR exception behaviour is the 603e's. |
| Exception vectors not modelled | On the hardware the BBC rewrites vector fetches to 0x400000 + offset, inside the 16 KB the dump lacks (`02_memory_map.md` §3); the harness mirrors the first 64 KB of flash at 0xFFF00000 only so a stray exception fails legibly (`emu/memmap.py`). Any exception stops the run. |
| No timing | `max_insns` is the only budget; `bdnz` delay loops cost real instructions. |
| Instruction counting / tracing / SPR watching run through `UC_HOOK_CODE` | roughly an order of magnitude slower; `trace` and `watch_spr` are opt-in. |

For exact PowerPC semantics (FP rounding, agreement with the decompiler) the
Ghidra `EmulatorHelper` path would be the tool; it has not been needed.

### 5.1.1 Device models (E4, 2026-09-17)

The row "No peripheral model at all" above has two exceptions. `emu/core.py`
has `stub_write(addr, fn)` and `add_device(start, end, model)`, both
additive, and two models use them:

* **`emu/qspi_eeprom.py`** — the QSMCM QSPI queue engine plus an M95160 on
  PCS0, backed by a 2 KB file. It is proved by the firmware's own code: the
  boot loopback self-test (0x017CF0) and the EEPROM WEL self-test (0x017A84)
  both pass, and `eeprom_read_bytes`/`eeprom_write_bytes` round-trip
  (`python3 -m emu.qspi_eeprom --self-test`). With it plus the two device
  function pointers bound, the EEP_CONF block manager runs its start-up read
  and a queued commit reaches status 2 — which closes the limit brief D2
  recorded. Derivation: `re/findings/eeprom.md` section 10.
* **`emu/toucan.py`** — the receive message buffer of a TouCAN slot, which is
  all `can_rx_poll` looks at.

Two harness caveats that cost time before they were understood, both
**VERIFIED-DYNAMIC**:

| Caveat | Consequence |
|---|---|
| Unicorn calls a `UC_HOOK_MEM_WRITE` callback **before** it performs the store | a device model cannot change the register the instruction is writing; it arms itself and acts on the next access |
| `Med9Emu.call()` parks r1 at the boot stack top **0x7FEFFC** | a C frame there runs over application variables — `zwist_display_b1` (0x7FEF87) is 0x75 bytes below it. Call task code with `regs={"r1": 0x7FF768}`, the top of the OS task-stack region |

### 5.2 Regression tooling (`tools/bindiff.py`, `tools/logcmp.py`, issue #24)

`bindiff.py` classifies every changed byte between two dumps as *patch*
(listed in the `patch.json` of `06_patch_pipeline.md` section 1), *descriptor*
(a checksum sum/~sum word) or **unexpected**, and exits 1 on anything
unexpected. `logcmp.py` compares two logs in the format defined in
`logging/README.md`. Both are covered by `python3 -m unittest discover -s tests`.

## 6. CAN and diagnostics

- Mac has no SocketCAN. Options: **slcan** adapter (CANable) via
  `python-can` `slcan`, **gs_usb/candleLight** via `python-can[gs-usb]`
  (libusb is installed), or the existing **Pi Zero + socketcand** route
  (`pi_can_setup/`) which also feeds SavvyCAN. SavvyCAN has arm64 macOS builds.
- Bus survey: `pi_can_setup/find_active_ids.py` on the powertrain CAN before
  choosing the ethanol frame id.
- Diagnostics transport: VW **TP2.0 over CAN (channel setup on 0x200, engine
  logical address 0x01) carrying KWP2000**. Libraries: EliasTuning/MED9RamReader
  (0x2C/0x21 + RequestUpload, needs the seed/key), EliasTuning/KWP2000-CAN,
  I-CAN-hack/pq-flasher (TP2.0 + KWP + CCP, panda hardware; ports exist for
  python-can), notyal/vwcanread (Passat B6 MED9.1 target). Security access on
  this ECU (`re/findings/kwp.md`, VERIFIED-DYNAMIC by emulation): level 2
  `key = seed + 0x11170` (constant at 0xA331C), level 1 a 5-round LFSR with
  mask 0x5FBD5DBD; only RequestUpload/TransferData need it (session 0x86).
- Live RAM: **ReadMemoryByAddress (0x23) is not implemented** (our table
  confirms). Use DynamicallyDefineLocalIdentifier (0x2C) + ReadDataByLocalId
  (0x21), or RequestUpload (0x35) for whole-RAM snapshots. The patch also
  publishes its values in VCDS measuring groups 111 / 108 / 69 / 109
  (`05_flexfuel_design.md` §3.7), so a stock tester shows them without a PC
  tool. CCP over CAN (MED9.1 CRO/DTO 0x7C3/0x7C4) needs direct powertrain-bus
  access (the gateway filters it).
- **The logger** (issue #20): `logging/med9log.py` (`log` / `dump` /
  `groups` / `probe`) on `logging/med9kwp/` (TP2.0 + KWP2000, a clean
  re-implementation, nothing vendored), tested against `logging/ecu_sim.py`,
  an emulated ECU that answers with the firmware's own handlers on a
  python-can `virtual` bus. Try everything with `--sim` first. **Adapter
  recommendation, OBD wiring, the first-contact procedure and what to do if
  the gateway does not route 0x200 are in `logging/README.md` sections 4-9.**
  Two facts about this ECU found while building it (`re/findings/kwp.md`
  §12): **TesterPresent takes no sub-function** (`3E`, not `3E 01`), and
  `21 <group>` answers with group *G* **and** group *G*+0x7F, only 1..127
  being requestable.
- **The generic OBD route** (issue #48, brief H3, `re/findings/obd.md` §11):
  J1979 requests must go to the **functional** id 0x7DF; a physical 0x7E0
  request is received and never answered (gate `li r3,0` at 0x2C29C). The
  answer comes on 0x7E8, silence means "no", and the OBD connection times out
  5 s after the last answer. `logging/obd_client.py` speaks it;
  `ecu_sim.py --obd-can` runs the firmware's own ISO-TP and connection code.
- DBC in `data/` is the infotainment CAN (PQ35 ICAN), useful only for
  gateway-bridged engine frames (`mGW_Motor` 0x35B etc.), not for the
  powertrain bus the ECU sits on. `data/ethanol_node.dbc` is ours: the one
  flex-fuel frame the Pico node sends (0x0EC), for SavvyCAN/cantools.
  Decode captured frames with `tools/ethanol_frame_decode.py`.

### 6.1 Pico flex-fuel node toolchain on this Mac (VERIFIED-DYNAMIC, 2026-09-15)

Verified by building `pico_can_sender` for `PICO_BOARD=pico2` (RP2350) on
macOS 15 / Apple silicon; `build/pico_can_sender.uf2` is produced with no
warnings, and the MCP2515 and SSD1306 libraries build unmodified.

```bash
brew install cmake ninja                 # no sudo
# Arm GNU toolchain 14.2.Rel1, no sudo (the gcc-arm-embedded cask needs a
# password for its .pkg installer); unpacked where the Pico VS Code extension
# also looks, which is the toolchainVersion named in CMakeLists.txt:
#   ~/.pico-sdk/toolchain/14_2_Rel1
# Pico SDK 2.2.0 (the sdkVersion in CMakeLists.txt) into the gitignored work/:
git clone --depth 1 --branch 2.2.0 https://github.com/raspberrypi/pico-sdk.git work/pico-sdk
git -C work/pico-sdk submodule update --init --depth 1 lib/tinyusb
```

Exact commands, the sensor pull-up/level-shift network and the host test
(`pico_can_sender/test/run_tests.sh`, needs only `cc`) are in
`pico_can_sender/README.md`.

## 7. Flashing, backup and recovery

- **KESSv2**: reads/writes this ECU with protocol 179 (MED9.1.1) over OBD and
  applies its own checksum correction. KESSv2 is end-of-life (no new
  subscriptions, updates only while the current one lasts). Software is
  Windows-only; USB drivers are the weak point in VMs, so use a real Intel
  Windows laptop if possible. Over OBD it drives *this firmware's* own
  programming route (brief E6, `re/findings/flash_programming.md`,
  VERIFIED-STATIC): session `10 85`, SID 0x34, which can erase and program
  **0x404000-0x47FFFF**, the on-chip flash — so the patch's seven on-chip
  hook words are reachable over OBD in principle — and there is **no
  boot-time integrity gate on flash content** (the 65 block sums are never
  recomputed at boot, the published CRC-32 is compared with nothing). Whether
  KESSv2 *offers* the on-chip range is a property of the tool, still open;
  **Flash 1's read-back of 0x432940 answers it** (`08_bench_playbook.md`
  step 6). Flash 0 writes the stock image, so its read-back cannot tell
  written from skipped.
- **K-TAG with the "BDM Motorola MPC5xx" positioning frame, protocol 64**:
  bench read/write of external flash, on-chip flash and EEPROM via the 14-pin
  BDM pads (no protection at that level). This is both the complete backup
  and the recovery path after a failed OBD write. Use a frame, not soldering;
  the pads are easy to destroy.
- OBD writes do not touch the EEPROM (immobiliser data, adaptation, flash
  counter). Read-protected ECUs answer `7F 27 35`; ours read fine.
- Recovery without BDM: the firmware's RAM bootstrap loader (brief F5,
  `re/findings/ram_loader.md`) is entered automatically when the calibration
  marker at 0x1E2500 is wrong and reprograms almost everything — over a
  **serial (SCI) line, not CAN**. Budget for a tool that drives it, and verify
  on the bench which pin SCI1 is bonded to.
- Checklist: `04_re_guidelines.md` section 6. The operating procedure — what
  to run before a write, what the read-back must show, what a failed flash
  looks like and what never to flash — is **[`07_workflow.md`](07_workflow.md)
  chapters 3 and 6**, and the bench-day order is
  **[`08_bench_playbook.md`](08_bench_playbook.md)**. Every step whose
  behaviour is still a prediction is marked `[unverified]` there, because no
  ECU has been written by this project yet. `make bench-kit` builds the four
  bench-day images with a manifest (`tools/bench_kit.py`).
- EEPROM tools for backup analysis only: E2PA (Windows), EliasTuning/MED9-EEPROM-Tool (Python).

## 8. Reference documents to obtain

- Bosch **MED9.1 Funktionsrahmen** (TFSI edition, ~55 MB PDF on s4wiki:
  `files.s4wiki.com/docs/MED9.1 TFSI.pdf`); the VR6 FSI software differs in
  places but module and variable names carry over.
- MPC561/MPC563 Reference Manual (NXP `MPC561RM.pdf`) for USIU, TouCAN,
  QADC, UC3F, dual mapping (DMBR/DMOR) and IMMR bit definitions.
- An A2L for a 03H906032 software close to 1037382557 would be the single
  most valuable artefact (all map and RAM symbols); none is public.
- Community code to read: EliasTuning `Med9GhidraScripts`, `MED9Toolchain`,
  `MED9.1-Compiler`, `MED9-Patches`, `MED9.1-Multimap-Tool`, `MED9RamReader`,
  `MED9-CCP`; 360trev `MED9inf`; the nefariousmotorsports MED9.1 threads by
  Basano (IDA setup, CAN_CONF, checksums, DDLI logging).
