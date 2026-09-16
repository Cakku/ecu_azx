# Tooling and environment

This Mac is an Apple M2 Pro running macOS 26 with an **x86_64 (Rosetta)
Homebrew in /usr/local** and Python 3.14 from it. Everything below works
under Rosetta; a native `/opt/homebrew` install would be faster but is not
required. Versions were checked in September 2026.

## 1. Repository Python tools

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 tools/checksum.py verify -q data/passat_azx_ori.bin   # ALL OK (65 blocks)
```

`tools/med9lib.py` is the single source of truth for address mapping. Import
it in every new script instead of hard-coding offsets.

Python 3.14 works for capstone 5.0.x, unicorn 2.1.4 and cantools (tested
here). If you add `pypcode`/`angr` use a Python 3.13 venv; their wheels lag.

**Correction, 2026-09-15 (VERIFIED-STATIC, issue #5).** Use **3.13 for the
one project venv**, not 3.14: the jpype wheels shipped with Ghidra 12.1.3 stop
at `cp313`, so PyGhidra cannot be installed on 3.14 at all. Installed and
working in `.venv` (Python 3.13.8): capstone 5.0.9, unicorn 2.1.4,
cantools 44.0.0, python-can 4.6.1, pyelftools 0.33, pyghidra 3.1.0,
jpype1 1.5.2.

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
- Headless import example (adjust paths):

```bash
analyzeHeadless ~/ghidra_projects med9 -import data/passat_azx_ori.bin \
  -loader BinaryLoader -loader-baseAddr 0x0 -loader-blockName EXT_FLASH \
  -processor PowerPC:BE:32:default -cspec default -noanalysis \
  -scriptPath ghidra_scripts -postScript med9_setup.py
```

**Correction, 2026-09-15 (VERIFIED-STATIC, issue #7).** That command does not
work as written: `support/analyzeHeadless` refuses `.py` post-scripts with
`Ghidra was not started with PyGhidra. Python is not available`. Start the
same analyzer *through* PyGhidra instead (this is what `support/pyghidraRun -H`
does internally):

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

- `-loader-blockName` is pointless here: the script deletes the imported block
  and rebuilds `EXT_FLASH`/`INT_FLASH` from the same `FileBytes`.
- The **project path must not contain a directory whose name starts with a
  dot**; Ghidra aborts with `Path element starting with '.' is not permitted`.
  That rules out `.claude/worktrees/...`, so agents must put the project
  somewhere else.

Versions in use (2026-09-15): Ghidra 12.1.3 (`brew install ghidra`, bottle
poured, 799 MB), openjdk@21 21.0.12.1, pyghidra 3.1.0, jpype1 1.5.2. A full
import with analysis takes about seven minutes on the M2 Pro.

### 2.1 What `med9_setup.py` must do (to be written in Phase 1)

1. Truncate/adjust the imported block so `EXT_FLASH` covers file 0x0-0x1FFFFF
   at 0x000000.
2. Create `INT_FLASH` at 0x404000 length 0x7C000 from file offset 0x200000
   (`Memory.createInitializedBlock` with the file bytes).
3. Create `CAL_ALIAS` at 0x5C0000 length 0x40000 as a **byte-mapped block**
   onto 0x1C0000 (`createByteMappedBlock`), so calibration references resolve
   to the same bytes without duplicating code. Add a mapped block for
   0x480000-0x5BFFFF only if a real reference into it shows up (three found).
4. Uninitialised RAM blocks: `SRAM_INT` 0x7F8000/0x8000, `SRAM_EXT`
   0x800000/0x8000, `DECRAM` 0x6F8000/0x800, `USIU` 0x6FC000/0x400,
   `UC3F_CTL` 0x6FC800/0x20, IMB modules 0x704000-0x707FFF (one block per
   module, names from `02_memory_map.md`), `CS2_DEV` 0x900000/0x40000,
   `CS3_DEV` 0xA00000/0x8000, `CALRAM_CTL` 0x780000/0x40.
5. Register values over all code: r13 = 0x7FFFF0; r2 = 0x5C9FF0; then
   r2 = 0x17FF0 over the boot module (functions reachable from 0x1004 until
   the application SDA setup at 0x986AC/0x9E3E0/0x405588). Use
   `ProgramContext.setValue(register, start, end, BigInteger)`.
6. Disassemble the vector table entries (`ba`) at 0x0-0x1000 and the tail
   start 0x404000, create functions at the KWP handler addresses and the
   checksum/CAN tables as data, then run auto-analysis.
7. Export functions/symbols/comments to `re/` (see the guidelines doc).

EliasTuning/Med9GhidraScripts has a working `med9-install.py` for 2 MB ECUs
(ROM at 0x400000, RAM at 0x600000, same r13/r2) and a `no_globals` cspec that
stops the decompiler folding SDA globals; borrow from it, but keep our map.

**Status 2026-09-15 (issue #7): written and run.** `ghidra_scripts/med9_setup.py`
implements all seven steps; `ghidra_scripts/README.md` has the command lines.
Results of a full run on `data/passat_azx_ori.bin` (VERIFIED-STATIC,
reproduced twice with identical counts):

| Check | Result |
|---|---|
| functions in `EXT_FLASH` 0x0-0x1FFFFF | 2,928 |
| functions in `INT_FLASH` 0x404000-0x47FFFF | 1,123 |
| functions total | 4,060 |
| references into `CAL_ALIAS` 0x5C0000-0x5FFFFF | 13,619 references to 7,917 distinct addresses |
| `0x4386D8` | a function, 204 bytes, named `kwp_sid_20_h1` from the dispatch table |
| `0x20004` decompiled | `romcheck_result_flags = 0; DAT_007f824b = 0xff; DAT_007f8248 = 0; DAT_007f8249 = 0xff;` i.e. the r13-relative stores resolve to 0x7F8248-0x7F824B |

Two deviations from the list above, both deliberate:

- Step 3's optional 0x480000-0x5BFFFF alias is **off by default**
  (`--code-alias` enables it). Only three static references land in it and it
  shadows code that is already mapped at 0x80000-0x1BFFFF, so switching it on
  makes the listing ambiguous for almost no gain.
- Step 5's boot `r2 = 0x17FF0` is **off by default** (`--boot-r2` sets it over
  0x1000-0x1FFFF). Deciding the real boundary is issue #8; until then the
  program-wide 0x5C9FF0 is the honest default.

Two additions that were not in the list and earned their place:

- Functions are seeded from the EABI prologue pattern (`stwu r1,-N(r1)` next
  to `mflr r0`, either order) over 0x20000-0x14494F and 0x404000-0x47FFFF:
  1,741 + 581 candidates, 2,308 new functions. Without it auto-analysis only
  reaches what the call graph from the seeds covers. `--no-prologue-scan`
  turns it off.
- The 24 entries of `tbl_kwp_services` are parsed and each non-zero handler
  becomes a named function (`kwp_sid_XX_h1`/`_h2`), 33 in total.

### 2.2 Other Ghidra uses

- Version Tracking / `ghidriff` to diff our binary against related dumps
  (e.g. 03H906032 with another software number) and to port symbols.
- `EmulatorHelper` for unit-testing single functions (section 5).

## 3. PowerPC cross compiler for patches

Recommended: **devkitPPC** (GCC 15, target `powerpc-eabi`, macOS binaries).

```bash
# install devkitPro pacman (pkg from github.com/devkitPro/pacman/releases), then
sudo dkp-pacman -S devkitPPC
export DEVKITPPC=/opt/devkitpro/devkitPPC
$DEVKITPPC/bin/powerpc-eabi-gcc -mcpu=505 -mbig-endian -meabi -msdata=none -G0 \
  -ffixed-r2 -ffixed-r13 -mno-relocatable -mstrict-align -msoft-float \
  -ffreestanding -fno-builtin -nostdlib -nostartfiles -fno-pic \
  -fno-stack-protector -fno-asynchronous-unwind-tables -Os -c patch.c
```

Flags that matter: `-mcpu=505` is the MPC5xx entry; `-msdata=none -G0
-ffixed-r2 -ffixed-r13` stop GCC from creating or using small-data sections
and from touching the ECU's SDA registers; `-msoft-float` keeps the FPU out
of hooked contexts (we use integer math anyway; if a float routine is ever
needed, `-mhard-float` is available on this core but the hook must then save
FP state). Link with a script that places `.text/.rodata/.data` at the chosen
free-flash address and `.bss` at the chosen RAM address (`06_patch_pipeline.md`).
Extract with `powerpc-eabi-objcopy -O binary`, symbols with `nm -n`, and
always disassemble the final blob with
`objdump -D -b binary -m powerpc:common -EB --adjust-vma=ADDR`.

Alternative: Homebrew LLVM (`brew install llvm lld`) with
`clang --target=powerpc-none-eabi -mcpu=603e ...` and `ld.lld -m elf32ppc`.
LLVM reserves r2/r13 on 32-bit SVR4 by itself, but has no libgcc for PPC
(avoid 64-bit division and float conversions). Docker with Debian
`gcc-powerpc-linux-gnu` is a third option with the same flags.
The Windows-only gnutoolchains build referenced by MED9Toolchain is not usable here.

### 3.1 What actually works on this machine (2026-09-15, issue #5)

**devkitPPC is not installed.** Its pkg installer needs an administrator
password, which an agent cannot supply. To install it, the human runs:

```bash
# 1. download the current pkg from https://github.com/devkitPro/pacman/releases
#    (devkitpro-pacman-installer.pkg)
sudo installer -pkg ~/Downloads/devkitpro-pacman-installer.pkg -target /
# 2. install the PowerPC toolchain
sudo dkp-pacman -Syu
sudo dkp-pacman -S devkitPPC
# 3. verify
export DEVKITPPC=/opt/devkitpro/devkitPPC
$DEVKITPPC/bin/powerpc-eabi-gcc --version
cd patches/examples/hello_patch && make TOOLCHAIN=gcc check
```

**`brew install llvm lld` also fails**, and will keep failing: Homebrew no
longer builds x86_64 bottles, so llvm's dependencies (openssl@3, xz) are built
from source in the Rosetta prefix and the build breaks with
`clang: error: unsupported argument 'westmere' to option '-march='`. Do not
retry it; either install a native `/opt/homebrew` or use the tarball below.

**The route that works** is the official LLVM binary release, native arm64, no
sudo, no Homebrew:

```bash
curl -L -o /tmp/llvm.tar.xz \
  https://github.com/llvm/llvm-project/releases/download/llvmorg-23.1.1/LLVM-23.1.1-macOS-ARM64.tar.xz
mkdir -p ~/toolchains && tar -xJf /tmp/llvm.tar.xz -C ~/toolchains
export LLVM_DIR=~/toolchains/LLVM-23.1.1-macOS-ARM64
```

Verified flags (`patches/examples/hello_patch/Makefile`):

```bash
$LLVM_DIR/bin/clang --target=powerpc-unknown-eabi -mcpu=603e \
  -msoft-float -ffreestanding -fno-builtin -nostdlib -fno-pic \
  -fno-stack-protector -fno-asynchronous-unwind-tables -fno-common \
  -fomit-frame-pointer -Os -std=c99 -Wall -Wextra -Werror -c hello.c -o hello.o
$LLVM_DIR/bin/ld.lld -m elf32ppc --no-dynamic-linker --discard-locals \
  -T hello.ld --defsym=PATCH_FLASH=0x00145000 --defsym=PATCH_RAM=0x00806000 \
  -o hello.elf hello.o
$LLVM_DIR/bin/llvm-objcopy -O binary hello.elf hello.bin
```

Notes that cost time, so they are recorded:

- `-msdata=none -G0 -ffixed-r2 -ffixed-r13 -meabi -mno-relocatable` are **GCC
  spellings and clang rejects them**. LLVM emits no small data for PPC32 EABI
  by itself; the linker script `ASSERT`s `.sdata`/`.srodata`/`.data` are empty
  and `make check` greps the disassembly for r2/r13, so this is checked rather
  than assumed. Both came out clean.
- **`llvm-objdump` has no `-b binary`**, so the "disassemble the final blob"
  step of section 3 cannot use it. `tools/blobdis.py` (capstone) does that job
  and `--check-sda` fails the build if r2 or r13 appear.
- **`ld.lld` 23.1.1 segfaults** in `Writer::finalizeSections()` if a linker
  script discards a synthetic section (`/DISCARD/ : { *(.got .got2 .plt) }`).
  Leave those out of `/DISCARD/`; `-fno-pic` keeps them empty anyway.
- A linker script needs a space before the colon (`.srodata : {`), otherwise
  lld reports `malformed number`.

Result: `patches/examples/hello_patch` builds an 88-byte blob with `.text` at
0x145000, `.rodata` at 0x145048 and `.bss` at 0x806000, addressing its table
with `lis 0x14 / addi 0x5048` and never touching r2 or r13.

#### Added 2026-09-16 (brief C1, issue #25)

The flags above are unchanged, but the build moved into the shared framework:
`patches/common/patch.mk` + `patches/common/patch.ld`, and hello_patch now
links at **0x150000** (`src/hello.c`, `patch.json`, `Makefile`). Three more
things this toolchain does that cost time to find out:

- **`--defsym` does not override a linker-script assignment.** The
  `X = DEFINED(X) ? X : <default>;` idiom is evaluated while lld computes
  addresses, and `--defsym` is applied only afterwards — so the output is
  linked at the *default* while the ELF symbol table reports the override.
  `patch.ld` therefore has no defaults and requires all three `PATCH_*`
  symbols. (This means the `make PATCH_FLASH=… PATCH_RAM=…` line documented
  for the old `hello.ld` never actually worked.)
- **A numeric branch operand is a displacement, not a target.**
  `b 0x0011F02C` assembles to 0x4811F02C = branch to pc + 0x11F02C, and
  `.set sym, 0x11F02C` + `b sym` behaves the same. Only an *undefined* symbol
  resolved by the linker gives the intended relative word. `patches/common/`
  trampolines use `ba` (AA=1) instead, whose operand is the address.
- **Clang preprocesses `.S` files**, so `#include "../common/hooks.S"` and the
  generated `med9_stock.h` work in assembly; the header is guarded with
  `#ifndef __ASSEMBLER__` around everything that is not a number.

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
| Fast tracing of boot / longer paths | Unicorn 2.1.4 (`UC_ARCH_PPC`, `UC_MODE_32|UC_MODE_BIG_ENDIAN`, cpu model 603E) | set MSR[FP] before float code; no MPC5xx SPR model; stub USIU/PLL/watchdog polling; map DECRAM 0x6F8000 (the boot copies code there and calls it) and a CS2 stub at 0x900000 |
| Symbolic questions | angr / pypcode (Python 3.13) | heavy; optional |

QEMU has no MPC5xx machine and is not useful here. The old
`med9_re/old_work/emulator.py` failed because it mapped peripherals at the
ISB=0 addresses and lacked DECRAM and CS2; its register values (r1, r13, boot
r2) were right.

### 5.1 The Unicorn harness (`emu/`, 2026-09-15, issue #21)

Built and documented in `emu/README.md`; tests in `tests/test_emu.py`
(`python3 -m unittest discover -s tests`). `python3 -m emu.boot_trace` prints
the boot-from-reset report. `capstone 5.0.1280` and `unicorn 2.1.4` install and
work on the Homebrew Python 3.14 here (x86_64 under Rosetta); no 3.13 venv was
needed. The Ghidra `EmulatorHelper` half of #21 is still open and waits for the
Ghidra project from brief A1.

Limitations found in practice, all **VERIFIED-DYNAMIC** by that harness:

| Limitation | Consequence |
|---|---|
| Unicorn runs a QEMU **603e**; MPC5xx SPRs do not exist | `mfspr` returns 0 and `mtspr` is swallowed, **without trapping**. SPR 638 (IMMR) is among them, so the ISB=1 relocation at file 0x1004 does nothing and the harness has to hard-code the relocated map. Also seen unmodelled: 560 (IC_CST), 158, 568, 792, 824. SPR 528-543 *do* exist on a 603e as IBAT/DBAT but are MPC5xx region/control registers here, so those writes land in the wrong model. `Med9Emu(watch_spr=True)` reports every one. |
| No peripheral model at all | USIU, TPU3, QADC, QSMCM, MIOS, TouCAN, CS2/CS3 are zero-filled pages. Anything polled has to be stubbed. Two blockers are known: **PLPRCR (0x6FC284) bit 0x8000**, the PLL lock-status bit the boot sets at 0x116FC and polls at 0x11704; and the **TPU3_A parameter-RAM scan at 0x14604-0x146C4**, which is where the boot stops once the PLL is stubbed. The SIPEND poll at 0x1048-0x1054 needs nothing: 0 already means "nothing pending". |
| No memory controller | BR/OR writes are recorded but change nothing; chip-select sizes, write protection and CS aliasing are not modelled. The RAM probe at 0x118E0 reads 0x808000, one word past the external SRAM, which aliases on the real part. |
| FP only because `MSR[FP]` is preset | The harness sets MSR = 0x3942 for every call; FPSCR exception behaviour is the 603e's. |
| MSR[IP]=1 vectors not modelled | 0xFFF00000 is mirrored to the first 64 KB of flash as a HYPOTHESIS (`02_memory_map.md` section 3); any exception stops the run. |
| No timing | `max_insns` is the only budget; `bdnz` delay loops cost real instructions. |
| Instruction counting / tracing / SPR watching run through `UC_HOOK_CODE` | roughly an order of magnitude slower; `trace` and `watch_spr` are opt-in. |

For exact PowerPC semantics (FP rounding, agreement with the decompiler) use
the Ghidra `EmulatorHelper` path instead, once A1 has produced the project.

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
  python-can), notyal/vwcanread (Passat B6 MED9.1 target). Security access:
  community reports `key = seed + 0x11170` for the development session on
  MED9.1; verify on our KWP 0x27 handler.
- Live RAM: **ReadMemoryByAddress (0x23) is not implemented** (our table
  confirms). Use DynamicallyDefineLocalIdentifier (0x2C) + ReadDataByLocalId
  (0x21), ~40 samples/s, or RequestUpload (0x35) for whole-RAM snapshots.
  Alternative without a PC tool: patch spare measuring-block slots so VCDS
  shows RAM values. CCP over CAN (MED9.1 CRO/DTO 0x7C3/0x7C4) needs direct
  powertrain-bus access (the gateway filters it).
- **The logger exists (brief C3, 2026-09-16, issue #20):**
  `logging/med9log.py` (`log` / `dump` / `groups` / `probe`) on
  `logging/med9kwp/` (TP2.0 + KWP2000, a clean re-implementation, nothing
  vendored), tested against `logging/ecu_sim.py`, an emulated ECU that answers
  with the firmware's own handlers on a python-can `virtual` bus. Try
  everything with `--sim` first. **Adapter recommendation, OBD wiring, the
  first-contact procedure and what to do if the gateway does not route 0x200
  are in `logging/README.md` sections 4-9.** Two corrections found while
  building it are in `re/findings/kwp.md` section 12: **TesterPresent takes no
  sub-function on this ECU** (`3E`, not `3E 01`), and `21 <group>` answers
  with group *G* **and** group *G*+0x7F, only 1..127 being requestable.
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
  Windows laptop if possible.
- **K-TAG with the "BDM Motorola MPC5xx" positioning frame, protocol 64**:
  bench read/write of external flash, on-chip flash and EEPROM via the 14-pin
  BDM pads (no protection at that level). This is both the complete backup
  and the recovery path after a failed OBD write. Use a frame, not soldering;
  the pads are easy to destroy.
- OBD writes do not touch the EEPROM (immobiliser data, adaptation, flash
  counter). Read-protected ECUs answer `7F 27 35`; ours read fine.
- Procedure and checklist: `04_re_guidelines.md` section 6.
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
