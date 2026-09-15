# ecu_azx — MED9.1.1 reverse engineering and flex-fuel for a Passat 3.2 FSI

Bosch MED9.1.1 engine ECU (VW 03H906032, Bosch 0261S02226, software
1037382557) from a 2007 VW Passat B6 3.2 V6 FSI 4Motion (engine AXZ).
Goal: firmware-level flex-fuel (E0-E85) driven by a CAN-broadcast ethanol
sensor, and a tuning platform for later hardware changes.

**Start with [docs/README.md](docs/README.md)** (index) and
[docs/01_project_plan.md](docs/01_project_plan.md) (plan).

## Layout

| Path | Content |
|---|---|
| `data/passat_azx_ori.bin` | KESSv2 read of the car's ECU, 0x27C000 bytes. **Never modify; never flash anything not derived from it.** |
| `data/*.dbc` | PQ35/46 infotainment-CAN database (not the powertrain bus). |
| `docs/` | Plan, verified memory map, tooling, guidelines, flex-fuel design, patch pipeline. |
| `tools/` | Verified Python helpers: address model, checksum verify/fix, layout report, reference finder. |
| `re/` | Symbol knowledge base (`symbols.csv`) and findings. |
| `pico_can_sender/` | Pico 2 + MCP2515 CAN sender (currently a potentiometer demo, id 0x123). Becomes the ethanol-sensor node. |
| `pi_can_setup/` | Raspberry Pi Zero W CAN sniffer with socketcand for SavvyCAN; bus survey script. |
| `MED9Toolchain/`, `MED9-Patches/` | Third-party (EliasTuning) C-patch toolchain and JSON patches for **other** MED9.1 ECUs; reference material for our pipeline. |
| `ppc_med9/` | Earlier Ghidra processor extension. Uses the wrong peripheral base for this ECU; superseded by the setup-script approach in `docs/03_tooling.md`. |
| `med9_re/` | Earlier Ghidra export and notes. Unverified and partly wrong; corrections in `docs/02_memory_map.md`. |

## Quick check

```bash
python3 tools/checksum.py verify -q data/passat_azx_ori.bin   # ALL OK (65 blocks)
python3 tools/layout_report.py data/passat_azx_ori.bin
```
