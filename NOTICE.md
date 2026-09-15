# Licensing scope and third-party notices

[`LICENSE`](LICENSE) is the [PolyForm Noncommercial License 1.0.0][pf]
(`PolyForm-Noncommercial-1.0.0`). It applies **only to the original work in
this repository by Carlo H**. Several things shipped here are not mine to
license, and this file says which.

[pf]: https://polyformproject.org/licenses/noncommercial/1.0.0

## What the license covers

Original work, Copyright 2025-2026 Carlo H:

| Path | Content |
|---|---|
| `docs/` | Project plan, memory map, tooling and design documents |
| `tools/` | Address model, checksum, layout report, reference finder |
| `re/` | Symbol knowledge base and findings |
| `emu/` | Emulation models |
| `ghidra_scripts/` | Analysis and setup scripts |
| `tests/` | Test suite |
| `logging/` | Log comparison tooling and synthetic samples |
| `patches/` | Our own patch sources |
| `pico_can_sender/` | Firmware sources, **excluding** `lib/` |
| `pi_can_setup/` | CAN sniffer setup and survey scripts |
| `med9_re/` | Earlier Ghidra export and notes |
| `data/ethanol_node.dbc` | Our own CAN definition |
| `documents/SOURCES.md` | Our own source index |

In short: you may use, modify and redistribute the above for **any
noncommercial purpose**, including hobby projects, personal study, research,
education and non-profit organisations. Commercial use — anything done for
profit or in the course of a business — needs separate permission. Ask me.

The license permits derivative works; what it does not permit is derivative
works used commercially. Because of that restriction, this is
**source-available, not open source**: it does not meet the Open Source
Definition, and an OSI-licensed project cannot absorb this code without
inheriting the noncommercial restriction.

## What the license does NOT cover

### Manufacturer firmware and data — no rights granted

| Path | Status |
|---|---|
| `data/passat_azx_ori.bin` | Bosch/Volkswagen ECU firmware (VW 03H906032, Bosch 0261S02226, SW 1037382557). Copyright of its respective rights holders. Not licensed by me under any terms. |
| `data/PQ35_46_ICAN_V3_6_9_F_20081104_ASR_V1_2.dbc` | Volkswagen CAN database. Third-party material, redistributed here for interoperability research. No rights granted by me. |

The analysis in `docs/`, `re/` and `emu/` describes that firmware. The
descriptions, symbol names, measurements and models are my own work and are
covered by `LICENSE`; the underlying firmware is not, and any verbatim
firmware bytes reproduced in them remain the rights holders' property.

### Vendored third-party code

| Path | Origin | License |
|---|---|---|
| `MED9Toolchain/` | EliasTuning Med9Toolchain | MIT (per its `README.md`) |
| `MED9-Patches/` | EliasTuning MED9 patches | See upstream |
| `pico_can_sender/lib/pico-mcp2515/` | git submodule, [adamczykpiotr/pico-mcp2515](https://github.com/adamczykpiotr/pico-mcp2515) | MIT |
| `pico_can_sender/lib/harbys-ssd1306/` | git submodule, [Harbys/pico-ssd1306](https://github.com/Harbys/pico-ssd1306) | See upstream |
| `ppc_med9/data/languages/` | Derived from Ghidra's PowerPC processor specification | Apache-2.0 |
| `documents/pico2_pinout.png` | Raspberry Pi Pico 2 documentation | Raspberry Pi Ltd |

Untracked reference documents under `documents/` (for example
`MED9.1_TFSI_Funktionsrahmen.pdf`, `MPC561RM.pdf`) are third-party manuals and
are deliberately not redistributed here.

## No warranty, and a word about the hardware

Per the `No Liability` section of `LICENSE`, this comes as is. Beyond that:
this project modifies engine management firmware. Flashing a modified or
incorrectly checksummed image can immobilise or damage a vehicle, and
modifying emissions-related engine software may be illegal for road use in
your jurisdiction. You are responsible for what you flash and where you drive
it.
