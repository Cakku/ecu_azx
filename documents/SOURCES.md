# Reference documents

Large PDFs (> 10 MB) live in this directory but are **not committed** (see
`.gitignore`). Re-download them with the URLs below and check the SHA-256 before
using them; every register or module fact quoted in `re/findings/` cites the
section/page of one of these files.

Text extraction (not committed either) is done with:

```bash
brew install poppler                      # provides pdftotext
pdftotext -layout documents/<file>.pdf work/<file>.txt
```

| File | Size | SHA-256 | Source | Retrieved |
|---|---|---|---|---|
| `MPC561RM.pdf` | 16,215,071 B | `355a85587737647ff9ebfca2526ea1fd31a29bfe9de3da1109eb146205881e01` | <https://www.nxp.com/docs/en/data-sheet/MPC561RM.pdf> | 2026-09-15 |
| `MED9.1_TFSI_Funktionsrahmen.pdf` | 55,637,491 B | `de8e05e7a85b9f0f0e996ebd837564f21978362e81cf780b8e6b531afa0c3b8a` | <https://files.s4wiki.com/docs/MED9.1%20TFSI.pdf> | 2026-09-15 |

## `MPC561RM.pdf`

*MPC561/MPC563 Reference Manual, Rev. 1.2* (Freescale/NXP), 1,152 PDF pages.
Also covers the code-compressed MPC562/MPC564. This is the CPU in the MED9.1.x
(see `docs/02_memory_map.md` §1 and `re/findings/mpc5xx_registers.md`).

Page references in this repository are given as **`§x.y.z, p. <printed page>`
(PDF page N)**. The printed page numbers restart per chapter (`6-28`), the PDF
page number is the absolute page in the file; both are quoted so a fact can be
found either way.

## `MED9.1_TFSI_Funktionsrahmen.pdf`

Bosch *Funktionsrahmen* (function framework) for **SG-MED9-1, project "Ea827
TSI", project number 5-4420.01/41W038_PQ35;0, dated 20 August 2004**, editor
Alexander Frick, 4,860 pages, 599 documented function sections (blocks ABK,
APP, FB, FDEF, FW).

**This is not our software.** Ours is `1037382557` / dataset `D9133_43K6P0`
(2006-05-08) for the 3.2 V6 FSI (engine AXZ, naturally aspirated, MED9.1.1).
The FR is for the 2.0 TFSI (turbo, 4-cylinder). Treat every module and label
name from it as a **naming convention that is very likely shared**, not as a
fact about our binary. `re/findings/fr_index.md` marks, per topic, what is
actually confirmed by our dump.

FR page numbers equal PDF page numbers (the footer "Seite N von 4860" matches
the PDF page index), so `pdftotext -f N -l N` extracts the page cited.
