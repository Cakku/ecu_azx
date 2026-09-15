# Reverse-engineering process and guidelines

## 1. Evidence levels

Every recorded fact carries one of the tags from `docs/README.md`
(VERIFIED-STATIC, VERIFIED-DYNAMIC, COMMUNITY, HYPOTHESIS) and a pointer to
its evidence: a script in `tools/`, a Ghidra address with the decisive
instruction, a log file, or a URL. "It looks like" is a HYPOTHESIS.

Upgrade rules:

| Claim type | Static evidence needed | Dynamic evidence needed |
|---|---|---|
| Address of a RAM variable | xrefs consistent with the FR description, unit/scale plausible | logged value tracks a VCDS measuring block or a physical expectation (rpm, temperature) |
| Purpose of a function | decompilation reads as the FR module; its callers/callees fit | emulator run reproduces a logged input/output pair |
| Location of a calibration map | interpolation routine xref, axis tables found, values physically plausible | changing it on the bench ECU changes the logged output |
| Hook point for a patch | call graph shows it runs in the intended task and context | bench counter/log proves it runs at the expected rate |

Never "verify" one fact with another that is itself unverified.

## 2. Address conventions

- Addresses in notes, symbols and patches are **CPU addresses**. File offsets
  are written with a `file` prefix. Conversion only through
  `tools/med9lib.py` (`cpu_to_file`, `file_to_cpu`).
- External flash code is addressed at 0x000000-0x1FFFFF, calibration at
  0x5C0000-0x5FFFFF (as the firmware does), on-chip flash at 0x404000-0x47FFFF.
  If a community source quotes 0x4xxxxx for a 2 MB MED9.1, subtract 0x400000
  before comparing with ours, and remember that in our ECU 0x400000-0x47FFFF
  is a different memory.
- Small data: r13-relative offsets are noted as `r13-0x1122` **and** the
  resolved address (0x7FEECE). r2-relative offsets state which r2 (boot
  0x17FF0 or application 0x5C9FF0).

## 3. Naming

- Use Bosch Funktionsrahmen names when the match is confident (`KRKATE`,
  `LAMFA`, `nmot_w`, `rl_w`, `ti_b1`). Unconfirmed identifications get a
  `cand_` prefix (`cand_KRKATE`). Ghidra `FUN_`/`DAT_` defaults stay until
  there is a reason to rename.
- Modules/functions we cannot map to the FR get descriptive snake_case names
  with a domain prefix: `can_rx_slot_copy`, `kwp_ddli_handler`,
  `boot_memctrl_init`. Our own patch code uses the `ff_` prefix
  (`ff_rx_task`, `ff_fuel_factor`).
- Tables: `tbl_` prefix and the element format, e.g. `tbl_can_rx (21 x 16 B)`.

## 4. Knowledge base

`re/symbols.csv` is the machine-readable truth; docs explain it.

Columns: `cpu_addr, file_off, space, kind, name, size, evidence, confidence, source, notes`

- `space`: `ext_flash`, `int_flash`, `cal` (high alias), `sram_int`, `sram_ext`, `periph`
- `kind`: `func`, `table`, `var`, `const`, `marker`, `region`
- `confidence`: `static`, `dynamic`, `community`, `hypothesis`
- `evidence`: script name + command, or Ghidra address, or log path

Workflow: Ghidra is the working surface; a script exports functions, labels
and plate comments to `re/` after each session and imports `re/symbols.csv`
into a fresh project. The Ghidra project directory itself is not committed
(`ghidra_projects/` is ignored). Longer findings go to `re/findings/<topic>.md`
with the same tags. Update `docs/02_memory_map.md` when a table or region
changes level.

`med9_re/old_work/` is frozen. Do not extend it; extract anything still
useful into `re/` with the correct tag.

## 5. Working method in Ghidra

1. Start from the setup script; never analyse a raw import with the default
   map (that is how the old work went wrong).
2. Fix r2 context per function before reading constant loads.
3. Anchor on hard facts first: the tables in `02_memory_map.md`, the KWP
   dispatcher, the CAN tables, string references, the measuring-variable
   table. Then walk xrefs outward. Function size is not evidence of purpose.
4. Bosch code is generated from the FR: a function usually corresponds to one
   FR module and calls shared helpers (interpolation, filters, min/max). Name
   the helpers early; everything else becomes readable.
5. Record negative results too ("0x23 not present in KWP table") to stop
   others from re-checking.

## 6. Safety rules for binaries and flashing

Before any write to any ECU:

1. `python3 tools/checksum.py verify -q FILE` prints `ALL OK (65 blocks)`.
2. The diff against the source file is listed (`cmp -l`) and every changed
   byte range is explained in the patch's JSON/notes. Nothing outside the
   intended ranges and the checksum descriptors may change.
3. The file was built from **this ECU's own read**, never from a downloaded
   file, and the identification block (0x1CEE20) is unchanged.
4. The target ECU has a verified full backup (external flash, on-chip flash,
   EEPROM) whose SHA-256 is recorded in `data/backup_bdm/MANIFEST`.
5. First run on the bench spare; only files that ran there go to the car.
6. Power: stable supply / charger at 13.5 V or more, no accessories, no
   interruptions; laptop on mains.
7. After writing: read back, compare with the intended file, clear DTCs, log
   a short drive/idle and compare with the baseline log.
8. Roll back with the original file if anything is unexplained. Do not
   "fix forward" on the car.

Never disable the ROM check, immobiliser pairing or component protection to
make something work; find the actual cause.

## 7. Rules for patch code

- C compiled with the flags in `03_tooling.md`; no libc, no floating point,
  no small-data sections, r2/r13 never touched, stack use small and bounded.
- No dynamic memory; all state in a documented RAM block that was verified
  unused (static refs + runtime RAM dumps across ignition cycles).
- Hooks preserve every register the EABI treats as volatile that the hooked
  code still needs; trampolines save LR/CR explicitly.
- Every calibratable value of ours lives in a table in a checksummed
  calibration block with a descriptor comment, so tools can edit it.
- Default behaviour equals stock: factor 1.0, zero offsets, on power-up and
  on any fault.
- Every patch has a unit test (emulator), a bench test procedure and an
  expected-log description before it is flashed to the car.

## 8. Documentation hygiene

- One fact per table row; dates and dump hashes on anything that could change.
- Commands are shown exactly as run; outputs are quoted when they are the
  evidence.
- Corrections go into the document with the old statement struck or listed
  under "Corrections", not silently replaced, when the old statement was
  ever used for a decision.
