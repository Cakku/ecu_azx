# Project documentation

Reverse engineering and flex-fuel conversion of the Bosch MED9.1.1 engine ECU
of a 2007 VW Passat B6 3.2 FSI 4Motion (engine AXZ, ECU 03H906032,
Bosch 0261S02226, software 1037382557).

| Document | What it is for |
|---|---|
| [01_project_plan.md](01_project_plan.md) | Goals, phases, milestones, risks, decisions. Start here. |
| [02_memory_map.md](02_memory_map.md) | Everything verified about the dump layout, address space, checksums, tables. The reference for all address work. |
| [03_tooling.md](03_tooling.md) | Software to install and how to set it up on this Mac: Ghidra, Python, PowerPC compiler, emulators, CAN, flashing. |
| [04_re_guidelines.md](04_re_guidelines.md) | How we work: evidence levels, naming, address conventions, knowledge base, safety rules, checklists. |
| [05_flexfuel_design.md](05_flexfuel_design.md) | Architecture of the flex-fuel feature, CAN frame spec, ECU-side design, fail-safe rules, RE targets, test plan. |
| [06_patch_pipeline.md](06_patch_pipeline.md) | How a C patch becomes bytes in flash: build, hook, checksum, verify, flash, roll back. |

Supporting material lives next to the docs:

- `../tools/` verified Python helpers (address model, checksum verify/fix, layout report, reference finder).
- `../re/` the symbol knowledge base (`symbols.csv`) that Ghidra exports are merged into.
- `../med9_re/old_work/` the pre-project notes and scripts. **Unverified and partly wrong**; kept for history only. Corrections are listed at the end of `02_memory_map.md`.

## Status legend used throughout

| Tag | Meaning |
|---|---|
| **VERIFIED-STATIC** | Derived from the dump itself by a reproducible script or disassembly in this repo; the evidence is cited. |
| **VERIFIED-DYNAMIC** | Confirmed on the real ECU (bench or vehicle) or by emulation with recorded results. |
| **COMMUNITY** | Reported for MED9.1 by other people (forum/GitHub), not yet checked on this binary. |
| **HYPOTHESIS** | Our current best guess. Do not build on it without upgrading it first. |

When a fact changes level, update the document and, if it is encoded in
`tools/med9lib.py` or `re/symbols.csv`, update those in the same commit.
