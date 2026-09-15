# re/ — reverse-engineering knowledge base

`symbols.csv` is the machine-readable list of everything located in the
firmware, with its evidence and confidence (format in
`docs/04_re_guidelines.md` section 4). Ghidra exports are merged here; the
Ghidra project itself is not committed.

`findings/` holds longer notes per topic (CAN, KWP, injection, ...).

Current notes:

| File | Topic |
|---|---|
| `findings/mpc5xx_registers.md` | MPC561/MPC563 register facts: IMMR/ISB, chip selects BR/OR, DMBR/DMOR calibration window, exception-table relocation, TouCAN, QSMCM, UC3F. Every fact cites the reference manual. |
| `findings/fr_index.md` | Bosch MED9.1 Funktionsrahmen index: which FR module and which labels cover each area we care about, and what our dump actually confirms. |
