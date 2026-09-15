# Brief B3 — KWP2000 services: security access, DDLI, ReadDataByLocalId, RequestUpload

Issue: **#13**. Prerequisite: brief A1 merged; A3's measuring-variable
results help. No `sudo`.

Read `00_common_rules.md`, `docs/02` §7 (service table) and
`docs/03_tooling.md` §6 first.

## Starting facts
Dispatch table at 0x2B870: 24 entries `SID FF FF FF, flags, handler,
handler2, 0`. Handlers of interest (CPU addresses, external flash low alias):
0x27 -> 0x36210; 0x2C -> 0x34D28 / 0x35034; 0x21 -> 0x35F6C; 0x35 -> 0x360C4;
0x36 -> 0x37434 / 0xA33A4; 0x37 -> 0x360B0; 0x10 -> 0x3716C / 0x370F8;
0x14 -> 0x35410 / 0x352F4; 0x18 -> 0x35064; 0x17 -> 0x36024; 0x3B -> 0x375E8 /
0xA3320; on-chip flash handlers: 0x81 -> 0x4390A4, 0x82 -> 0x4387CC,
0x20 -> 0x4386D8, 0x31 -> 0x439110, 0x32 -> 0x438F40. Community reports for
MED9.1: `key = seed + 0x11170` for the development session, DDLI limited to
~17 entries with only the last allowed to exceed 1 byte.

## Tasks
1. Decode the dispatcher: entry flags meaning (session/security requirements,
   the 0x38/0x3C/0x3F/0x50 values), how the handler is called (argument
   structure: request buffer pointer, length, response length/status — the
   MED9Toolchain `kwp_input_struct` is a starting hypothesis), and where the
   table ends / whether it can be extended.
2. **0x27 SecurityAccess**: seed generation and key check; confirm or refute
   `seed + 0x11170` (search the constant 0x00011170 in the dump first).
3. **0x2C DynamicallyDefineLocalIdentifier**: definition table in RAM,
   maximum entries, size rules, allowed memory ranges.
4. **0x21 ReadDataByLocalIdentifier**: which local ids exist (measuring
   blocks -> the tables from A3 / KFMWNTK-equivalent, dynamic ids), response
   formatting and scaling path.
5. **0x35/0x36/0x37 RequestUpload/TransferData/RequestTransferExit**: allowed
   address ranges (RAM dump possibility), block sizes, security requirement.
6. **0x10 StartDiagnosticSession** types and the effect on other services;
   TesterPresent handling; timeouts.
7. Locate the measuring-block tables (index table and pointer table) so
   spare slots can carry our variables later (#39).
8. Write `re/findings/kwp.md` with everything a logger implementer needs
   (brief for #20), add symbols, commit on `agent/B3`, comment on #13.

## Acceptance
A reader of `kwp.md` can write the TP2.0/KWP client for #20 without opening
Ghidra: session type, security algorithm, DDLI/0x21 request formats and
limits, upload ranges.
