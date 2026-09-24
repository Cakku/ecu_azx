#!/usr/bin/env python3
"""An emulated MED9.1.1 that speaks TP2.0/KWP2000 on a python-can bus.

Not a mock.  Every KWP service below is answered by the **real handler out of
`data/passat_azx_ori.bin`**, executed by the `emu/` Unicorn harness on one
`Med9Emu` whose RAM survives between requests -- so the simulated ECU has a
memory, the DDLI definitions really live in `ddli_def_table` (0x804038), and
the protected-window refusal comes from `kwp_upload_range_check` (0x0A3160),
not from any check written here.  That is what makes it worth testing the
logger against: if `logging/med9log.py` can drive this, the only thing left
between it and the car is the CAN adapter.

What is **not** real, and why (details in `re/findings/kwp.md` section 12):

* **the dispatcher.**  `kwp_service_dispatch` (0x13E98C) is reached through a
  function pointer and expects the OS around it.  Instead the 28-entry
  dispatch table is read out of the image (0x2B820, count from the config
  struct at 0x2BA50+0x1C) and its **own session bitmask** decides what is
  allowed -- the data is the firmware's, the loop is four lines of Python.
* **StartDiagnosticSession.**  `kwp_sid_10_h1` (0x3716C) is the K-line path:
  with request length 1 it accepts only 0x81/0x89 and otherwise walks into a
  baud-rate table and reprograms the serial hardware, which under Unicorn
  ends in the OS halt spin at 0x110F0.  The simulator applies the session map
  of kwp.md section 2 and calls the real one-line setter `kwp_session_set`
  (0x13CEE4), plus the real `kwp_sid_2C_h2` (0x35034) that wipes the dynamic
  ids on every session change.
* **the security seed's clock.**  It comes from the PowerPC time base, which
  the Unicorn 603e never advances.  `emu/time_base.py` gives
  `read_time_base` (0x478460) a running virtual time base off the simulator's
  own clock -- without it the level-1 seed loop at 0x36328 never exits and
  `27 01` cannot be answered at all.  The seed *handler* is the firmware's:
  `--seed` only writes a value into `kwp_sec_seed` (0x7FB774) *after* the real
  level-2 seed handler ran, so the number on the wire is predictable; the key
  is then checked by the real `kwp_sid_27_h1` against that value, exactly as
  `tools/kwp_seckey_verify.py` does.  `27 01` is not overridden: its seed is
  the virtual time base and the key follows from it.

**Power-on state (2026-09-22, brief F3, `re/findings/boot.md` section 6.5;
two more entries 2026-09-24, brief G5).**
`power_on` no longer hand-seeds the KWP security cells.  It calls twelve of the
1,028 entries of the firmware's own one-shot init table (`INIT_ENTRIES`), in
the table's order, the way `os_start` does -- so `kwp_sec_level_flags`,
`kwp_sec_lfsr_rounds`, `nvm_mode`, the TP buffer pointers and the DDLI entry
arrays are written by the ECU's code, not by this file.  What is still seeded
by hand, and why:

===================================  ====================================
cell                                 reason
===================================  ====================================
`kwp_session_current` 0x803D3E = 0   no init entry writes it (it is BSS,
`kwp_security_state`  0x803D3C = 0   and zero on a cold emulator); written
`kwp_sec_seed`        0x7FB774 = 0   so a *repeated* `power_on()` is a real
                                     power cycle rather than a no-op
`AnimatedRam.statics` + the ramps    engine values.  On the ECU they are
                                     produced by tasks, not by an init
                                     entry; the simulator runs no tasks
0x7FADAC / 0x7FADAD / 0x7FADAB       the NVM device sub-states and the
                                     request queue, seeded by
                                     `emu.qspi_eeprom.cold_start()`; no
                                     init entry writes them either
                                     (`eeprom.md` section 10.4)
0x7FAB70 / 0x7FAB74                  the NVM device function pointers.
                                     **None of the 1,028 entries writes
                                     them** (`boot.md` section 6.3), so
                                     `NvmDeviceBinding` still installs the
                                     two trampolines (`eeprom.md` 7 Q1)
0x803DDC = 0x2BA50 (G5)              the KWP config-struct pointer the h2
                                     walk reads.  Written by the real
                                     `kwp_register_table` 0x13E974, whose
                                     only caller is inside init entry 281
                                     (the whole diagnostic-stack start-up,
                                     not run); power_on calls just 0x13E974
===================================  ====================================

**The fault services (2026-09-24, brief G5, `re/findings/kwp.md` 12.7).**
`18 00 FF 00`, `17 hi lo` and `14 FF 00` are the firmware's own handlers
through the ordinary dispatch path, reading the firmware's RAM fault memory
(0x7F8890, 20 x 0x5C bytes).  Three pieces around them are the simulator's
and labelled as such: `DtcStore.seed` (`--seed-dtc`) stands in for the
fault-path manager that would have stored a fault; a pending (`7F xx 78`)
service is re-dispatched with the NVM queue pumped in between until it
answers (`14` commits EEP_CONF block 24 and then holds off ~0.8 s, so it needs
`--eeprom`); and after a positive `14` `DtcStore.after_clear` empties the
store, which on the car is done later by DFPM processes the simulator does not
run.  On every new TP2.0 channel the firmware's `kwp_service_h2_walk`
(0x13ECB0) runs once, as `kwp_conn_cyclic` does per new connection
(`obd.md` 10.1): it rebuilds the OBD support bitmaps and puts the session back
to 0.

One ordering difference from the part, deliberate: the simulator attaches the
EEPROM and runs `cold_start()` *before* the init entries, so `kwp_sec_init`
reads a filled mirror at 0x7FA02C.  On the ECU the mirror is still zero then
-- the start-up block read is driven from a task (0x061BF4, called by
0x120FB8 and 0x45CD48), i.e. after `os_start` -- and `app_init` has just
cleared 0x7F8490-0x7FA62F.  Both give `kwp_sec_delay_timer` = 0, because a
factory-shaped image holds 0x0000 at block 11 payload +0x0C.

A few RAM cells are animated so a log shows movement: an rpm ramp, coolant
warm-up, the Flash-1 counter of `patches/ff_counter/` and the five raster
activation counters of the two OS task sets.  `--task-set A` makes the *other*
set live, which freezes the set-B raster counters.  Since brief F1 (2026-09-22)
the Flash-1 block does **not** freeze with it: `patches/ff_counter` now hooks
the 10 ms raster of *both* sets, so the counter runs whichever set is live and
`ff_src_seen` at PATCH_RAM+0x06 names it (1 = set A, 2 = set B, 3 = both).
See :class:`AnimatedRam`.

Usage::

    python3 logging/ecu_sim.py --self-test          # handlers only, no bus
    python3 logging/ecu_sim.py --bus slcan:/dev/tty.usbmodem1411 -v
    python3 logging/ecu_sim.py --bus virtual:med9 --drop-ack 1 --delay-ms 5

**python-can's `virtual` bus does not cross process boundaries**, so a
simulator started from a second terminal on `virtual:` is invisible to the
logger.  Either put it on a real adapter (`slcan:`/`gs_usb:`, which is how you
would test a third-party tool against it) or, normally, let the logger start
it in-process::

    python3 logging/med9log.py probe --sim

which is this, from Python -- also what `tests/test_med9kwp.py` does::

    sim = EcuSimulator.on_virtual_bus("med9")
    with sim.background():
        ...                      # drive the logger against it
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import json
import math
import os
import struct
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO, os.path.join(REPO, "logging")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from med9kwp import CanLink, Tp20Params, Tp20Server, parse_bus_spec  # noqa: E402
from med9kwp.can_transport import open_link  # noqa: E402

DUMP = os.path.join(REPO, "data", "passat_azx_ori.bin")

# --- addresses (re/findings/kwp.md) ----------------------------------------
KWP_CONFIG_STRUCT = 0x2BA50          # config[0] = table base, config[0x1C] = count
KWP_TABLE_ENTRY = 20
SESSION_CURRENT = 0x803D3E           # kwp_session_current
SECURITY_STATE = 0x803D3C            # kwp_security_state
SEC_SEED = 0x7FB774                  # kwp_sec_seed
SEC_LEVEL_FLAGS = 0x7FB781
SEC_LFSR_ROUNDS = 0x7FB770
SEC_RETRY_FLAG = 0x7FB780
H_SESSION_SET = 0x13CEE4             # kwp_session_set: stb r3,0x803D3E
H_DDLI_WIPE = 0x35034                # kwp_sid_2C_h2: wipes all 10 dynamic ids

#: **The firmware's own start-up, as far as a KWP session needs it.**
#:
#: `tbl_module_init` (0x0B1A68) is a flat NULL-terminated array of 1,028
#: function pointers that `os_start` (0x477990) walks **once, in order, with
#: no arguments, before the first task ever runs** -- `re/findings/boot.md`
#: section 6.1 and 6.2.  It is the only caller those functions have, which is
#: why `ddli_init`, `nvm_set_normal_mode` and the rest looked caller-less.
#: The emulator has no OS to walk it, so `Med9Handlers.power_on` calls the
#: handful of entries a session depends on -- the list `boot.md` section 6.5
#: drew up for exactly this -- rather than hand-seeding the cells they write.
#: Calling all 1,028 is the wrong trade: most of them touch peripherals the
#: emulator does not model.
#:
#: `idx` is the array index from 0x0B1A68, so a row can be checked straight
#: against the table in `boot.md` section 6.4.  The names are that section's
#: (HYPOTHESIS as names, VERIFIED-STATIC as addresses and effects).
INIT_ENTRIES = (
    # idx   address     name                    what it leaves behind
    (18, 0x0BA0F4, "nvm_set_sync_mode"),     # nvm_mode (0x7FCD68) = 2 ...
    (24, 0x0BA104, "nvm_set_normal_mode"),   # ... then 1: normal, async mode
    (34, 0x12F10C, "dtc_code_table_select"), # G5: 0x7FBA58 = 0x5D9F06 if cal
                                             #   0x5CF642 == 1, else 0x5DA6DE
                                             #   (= 2 here); what 0x14 matches
    (38, 0x12F138, "kwp_tp_buf_init"),       # RAM buffer pointers 0x8037E4,
                                             #   0x8037E8, 0x8037EC, 0x8038D4
                                             #   (G5: they point INTO fault-
                                             #   memory entry 0 at 0x7F8890,
                                             #   +2/+3/+0xA/+0x1C, so the name
                                             #   is doubtful; kwp.md 12.7)
    (39, 0x12EC00, "dfp_init"),              # G5: the fault-memory manager's
                                             #   start-up; among others the
                                             #   lock pair 0x7FBA5C = 0 /
                                             #   0x7FBA60 = 0xFFFFFFFF that
                                             #   `14` needs, else NRC 0x10
    (71, 0x036AB8, "kwp_sec_init"),          # 0x7FB781 = 0x7FB780 = 0x7FB770
                                             #   = 0; lockout 0x7FB748 from
                                             #   the EEPROM mirror 0x7FA02C
    (72, 0x12E39C, "ddli_init"),             # the ten DDLI entry-array
                                             #   pointers at 0x80403C+8n:
                                             #   0xF0 -> 0x80366C (20 entries),
                                             #   0xF1..0xF9 -> 0x80370C+0x18n.
                                             #   Unrun, all ten are 0 and the
                                             #   second id defined overwrites
                                             #   the first (kwp.md 4.1)
    (75, 0x12E2D8, "flash_crc_init"),        # 0x7FB6F4 = 0, 0x801200 = 0, the
                                             #   state flash_crc_task starts
                                             #   from (flash_programming 5.3a)
    (83, 0x12F638, "kwp_ct_buf_init_a"),     # eight pointers 0x7FB074-0x7FB0A8
    (84, 0x12F664, "kwp_ct_buf_init_b"),     #   and 0x802CF8/0x802CFA = 0x444
    (86, 0x12FDA4, "kwp_list_head_init"),    # list heads through 0x4104C4
    (89, 0x134E5C, "dtc_list_head_init"),    #   (0x7FBC09/0x7FBC0B, 0x7FCBA8)
)

# scratch inside the external SRAM, above everything the application uses
IO_STRUCT = 0x807800
IO_BUFFER = 0x807900
IO_BUFFER_MAX = 0x100

#: patches/ff_fuel's calibration block; its magic is how a patched image
#: is recognised (patches/ff_fuel/src/ff_state.h).
FFCAL001_BASE = 0x5E2510
#: where patches/ff_counter's blob lands (patch.json build.flash).  It is the
#: erased 0xFF of the free area 0x150000-0x1AFFFF in a stock image, so the word
#: there is how a Flash-1 image is recognised.
FF_COUNTER_FLASH = 0x150000

#: what both 10 ms background tasks call to drain the EEP_CONF request queue
#: (re/findings/eeprom.md section 8.4)
NVM_PUMP_WRAPPER = 0x061944
#: the relative fuel mass the segment stub scales (injection.md section 6.1)
RK_FUEL_MASS = 0x803038
#: what the stock word at 0x42247C branches to, and what ff_fuel_rk_hook
#: tail-branches to once it has scaled `rk` (injection.md section 6.2).  A
#: STOCK baseline run calls this directly, so the two runs of the E0
#: equivalence comparison execute the same stock code.
RKSPLIT = 0x41C3A0

#: --- the flash CRC-32 task (flash_programming.md 5.3a, re/symbols.csv) -----
#: A four-state machine driven at 0x64 bytes per activation.  It is the classic
#: VW "Flash-Prüfsumme": a **reported** value with nothing in the image to
#: compare it against, so it cannot stop the engine -- but a tester can read it
#: and a patched image changes it, which is what makes it worth simulating.
FLASH_CRC_TASK = 0x11CB10
FLASH_CRC_STATE = 0x7FB6F4      # 0 build table, 1 hash, 2 publish, 7 done
FLASH_CRC_RANGE = 0x7FB6F5      # u8 index into tbl_crc32_ranges
FLASH_CRC_BUDGET = 0x7FB6F6     # u16 bytes left in this activation
FLASH_CRC_ACC = 0x7FB6F8        # u32 running CRC register (init 0xFFFFFFFF)
FLASH_CRC_END = 0x7FB6FC        # u32 last address of the current range
FLASH_CRC_CURSOR = 0x7FB700     # u32 next byte to hash
#: 0x7F9178 gets the **high** halfword and 0x7F917A the low one -- the other
#: way round from `flash_programming.md` 5.3, corrected here from the run.
FLASH_CRC_PUB_HI = 0x7F9178
FLASH_CRC_PUB_LO = 0x7F917A
FLASH_CRC_DONE = 0x801200       # bit 0 once the value has been published
FLASH_CRC_RANGES = 0x0A3A10     # {start, end} pairs, terminated by {0, 0}
#: 0x64 bytes per activation over the three ranges
FLASH_CRC_ACTIVATIONS = 24627
FLASH_CRC_FLAGS = 0x7F9176      # bit 0 set together with the published value

#: --- the period (brief G3, `re/findings/boot.md` 6.8, scheduler.md 13) -----
#: `flash_crc_task` is not a raster.  Its five thunks 0x11CD24-0x11CD34
#: (init-array slots 775-779) are processes of **task 0**, the set-A
#: background task, which runs its 13 processes back to back and re-activates
#: itself at its tail (`bg_task_tail` 0x11DA64).  So one background loop
#: hashes 5 x 0x64 = **500 bytes** and the value appears in loop
#: ceil(24,627 / 5) = **4,926**.  VERIFIED-STATIC.
FLASH_CRC_PER_LOOP = 5
FLASH_CRC_LOOPS = 4926
#: the loop counter `bg_task_tail` increments once per background loop
#: (`-0x28E4(r13)`), independent of the CRC state and still counting after
#: state 7 -- boot.md 6.8(c)/(e).  The simulator does not run the other eight
#: processes of task 0, so it writes this cell itself: a MODEL of 0x11DA64's
#: one increment, not the firmware.
BG_LOOP_COUNTER = 0x7FD70C
#: T_bg, one background loop.  VERIFIED-STATIC bounds (boot.md 6.8(c)):
#: the floor is 28,560 emulated instructions per loop at one instruction per
#: 56 MHz clock, the ceiling is deadline timer 1 (0x100FD7 ticks = 300.75 ms;
#: expiry is fatal code 0x74).  Where inside the bounds a real ECU sits is
#: set by its idle time and is a BENCH value: the default is a HYPOTHESIS,
#: chosen because it is what `--flash-crc` always did (one activation per
#: 10 ms = 246 s to publish).
BG_LOOP_MS_MIN = 0.51
BG_LOOP_MS_MAX = 300.75
BG_LOOP_MS_DEFAULT = 50.0

#: Stack pointer for the hook calls.  `Med9Emu.call()` parks r1 at the boot
#: stack top 0x7FEFFC, and a C function's frame there runs straight over
#: application variables -- `zwist_display_b1` (0x7FEF87) is 0x75 bytes below
#: it, and a stock-vs-patched comparison then differs by the frame rather than
#: by the patch.  On the real part every task has its own stack; the OS task
#: stacks are 0x7FF3C0-0x7FF76F (`re/findings/scheduler.md`, and the
#: patches/ff_fuel README puts its RAM block deliberately above them), so the
#: runner uses the top of that region.
TASK_STACK_TOP = 0x7FF768

#: wire sub-function -> (internal session number, required security state)
SESSION_MAP = {
    0x81: (0, None),
    0x83: (3, None),
    0x85: (4, 2),        # programming; kwp.md 2 (flash), needs level 1
    0x86: (4, 3),        # upload;      needs level 2
    0x89: (5, None),
}

STATUS_POSITIVE = 1
STATUS_NEGATIVE = 2
STATUS_PENDING = (8, 9)


@dataclass
class KwpEntry:
    """One 20-byte row of the dispatch table (kwp.md 1.2)."""

    sid: int
    sub: int
    mask_a: int
    session_mask: int
    h1: int
    h2: int
    arg: int

    def allows(self, session: int) -> bool:
        return bool((1 << session) & self.session_mask)


# ---------------------------------------------------------------------------
# animated RAM
# ---------------------------------------------------------------------------
@dataclass
class AnimatedRam:
    """The cells the simulator moves, so a log is not a flat line.

    Values are deterministic functions of the simulated time, so a test can
    predict them exactly.  Everything else in RAM is whatever the emulator's
    reset left there (zeros outside flash).
    """

    #: seconds of ramp from idle to peak and back
    ramp_period: float = 20.0
    idle_rpm: float = 800.0
    peak_rpm: float = 3000.0
    #: True when the loaded image carries patches/ff_fuel (FFCAL001 at
    #: 0x5E2510).  Then 0x7FFB00 holds a live `struct ff_state` driven by
    #: `emu/models/flexfuel.py` instead of ff_counter's Flash-1 counter, so
    #: `21 6F` (measuring block 111) answers with moving numbers -- the
    #: rehearsal for issue #39.  The two patches are never co-flashed.
    flexfuel: bool = False
    #: True when the loaded image carries `patches/ff_counter` (its blob is at
    #: flash 0x150000, which is 0xFF in a stock image).  Only then does the
    #: stand-in put a Flash-1 counter block at 0x7FFB00: on a **stock** image
    #: those bytes are whatever the reset left, and `ff_alive` = 0 is exactly
    #: what `logging/sessions/flash1_counter.json` check 1 wants to see.  To
    #: rehearse a successful Flash 1, give the simulator the patched image
    #: (`--sim-dump work/ff_counter.bin`) or run the patch's own hooks
    #: (`--sim-patch patches/ff_counter`).
    flash1: bool = False
    #: ethanol the simulated Pico reports, in %
    flexfuel_e_pct: int = 85
    #: seconds of head start, so a one-shot `groups` request already shows a
    #: settled estimate instead of the first activation after power-up
    flexfuel_warm_s: float = 60.0
    #: which OS task set is live, "A" or "B" (re/findings/scheduler.md 11-12,
    #: brief C4): os_init installs set A and 0x11DA64 switches to set B when
    #: 0x7FEB5E != 0.  The stock counters of the other set stay frozen.  C1's
    #: Flash-1 counter used to freeze with set B, because its single hook sat
    #: in a set-B task; brief F1 gave `patches/ff_counter` a hook in **both**
    #: 10 ms rasters (0x432940 set A, on-chip; 0x12067C set B), so the block
    #: now counts under either set and records which hook ran.
    live_task_set: str = "B"
    #: static values written once at power-on: {address: (bytes)}
    statics: dict = field(default_factory=dict)
    #: number of cylinders, for the segment rate the `rk` hook is called at
    cylinders: int = 6
    #: False once a :class:`PatchRunner` owns 0x7FFB00, so the stand-in stops
    #: writing a block the patch's own hooks are maintaining
    owns_patch_ram: bool = True

    #: the reference model, created lazily so a stock image never builds one
    _ff: object = None
    _ff_ticks: int = 0
    #: {address: the bytes this stand-in last wrote there}, for the cells that
    #: step aside once something else writes them (see `apply`)
    _owned: dict = field(default_factory=dict)

    def _own(self, emu, addr: int, size: int) -> bool:
        """True while `addr` still holds what this stand-in last put there."""
        last = self._owned.get(addr)
        return last is None or emu.read(addr, size) == last

    def flexfuel_block(self, t: float) -> bytes:
        """`struct ff_state` as the patch would have written it by time `t`."""
        from emu.models.flexfuel import Cal, FlexFuelModel, frame
        if self._ff is None:
            self._ff = FlexFuelModel(Cal())
            self._ff_ticks = 0
        want = int((t + self.flexfuel_warm_s) * 100.0)   # the 10 ms raster
        want = min(want, self._ff_ticks + 8000)          # bound the work
        while self._ff_ticks < want:
            self._ff_ticks += 1
            rx = (frame(e_pct=self.flexfuel_e_pct, t_fuel_c=25,
                        counter=(self._ff_ticks // 10) & 0xFF)
                  if self._ff_ticks % 10 == 0 else None)
            self._ff.tick(rx)
        return self._ff.full_bytes()

    def rpm(self, t: float) -> float:
        phase = (t % self.ramp_period) / self.ramp_period
        tri = 2 * phase if phase < 0.5 else 2 * (1 - phase)
        return self.idle_rpm + (self.peak_rpm - self.idle_rpm) * tri

    def load_pct(self, t: float) -> float:
        """rl, 18 % at idle to 90 % at peak -- the ramp everything follows."""
        rpm = self.rpm(t)
        return 18.0 + 72.0 * (rpm - self.idle_rpm) / (self.peak_rpm - self.idle_rpm)

    def rk_base(self, t: float) -> int:
        """`rk` (0x803038) as the stock segment task would have produced it.

        The stub at 0x42247C scales this cell **in place**, so a simulator
        that calls the hook more than once has to re-produce the upstream
        value first or `rk` would compound.  It is a plain function of load,
        which is all the E0-equivalence comparison of `procedure.md` section 4
        needs: at E0 the hook returns without writing, so the stock and the
        patched run must show the identical trace.
        """
        return min(int(512 + 24.0 * self.load_pct(t)), 0xFFFF)

    def dwkrz(self, t: float) -> bytes:
        """Six per-cylinder knock retards, 0x7FCE57..0x7FCE5C.

        `re/findings/ignition.md` section 5: the array is **s8 and <= 0** --
        the knock controller only ever takes advance away.  A constant
        positive value (which is what this file shipped until 2026-09-17)
        makes a `21 6C` rehearsal read something the real controller cannot
        produce, and `logging/sessions/ff_fuel.json` check 8a is exactly about
        these bytes.  Cylinders 2 and 5 are the two that knock first here, and
        the retard grows with load and decays between events, which is the
        shape `wkrm` (0x7FCE76, the mean) is debounced from.
        """
        load = self.load_pct(t)
        depth = max((load - 45.0) / 45.0, 0.0)           # nothing below 45 %
        wobble = 0.5 + 0.5 * math.sin(2.0 * math.pi * t / 3.0)
        counts = []
        for cyl, weight in enumerate((0.2, 1.0, 0.4, 0.0, 0.8, 0.3)):
            v = -int(round(8.0 * depth * weight * wobble))
            counts.append(max(v, -32) & 0xFF)            # -24 degCA at worst
        return bytes(counts)

    def apply(self, emu, t: float) -> None:
        rpm = self.rpm(t)
        # nmot_w, u16, 0.25 rpm/LSB (re/findings/scheduler.md, #44)
        emu.write(0x7FEE74, struct.pack(">H", min(int(rpm / 0.25), 0xFFFF)))
        # the measuring-block display byte for id 1: 40 rpm per count
        emu.write(0x7FCE95, bytes([min(int(rpm / 40), 0xFF)]))
        # rl, u16, 100/4096 % per LSB: follows the ramp between 18 % and 90 %
        load = self.load_pct(t)
        emu.write(0x7FED38, struct.pack(">H", int(load * 4096 / 100)))
        # rl_w (0x7FEFB2), the KFZW column axis and ff_dzw_map's x input
        emu.write(0x7FEFB2, struct.pack(">H", int(load * 4096 / 100)))
        # rk (0x803038): re-produced every activation, see rk_base()
        emu.write(0x803038, struct.pack(">H", self.rk_base(t)))
        # dwkrz (0x7FCE57..5C) and its mean wkrm (0x7FCE76), both s8 and <= 0.
        # These two yield to anyone who writes them: a test or a bench operator
        # poking a knock pattern into the simulator must not have it wiped out
        # by the next activation, which is how they behaved when they were
        # static values rather than an animation.
        knock = self.dwkrz(t)
        signed = [b - 256 if b & 0x80 else b for b in knock]
        mean = bytes([int(sum(signed) / len(signed)) & 0xFF])
        if self._own(emu, 0x7FCE57, 6):
            emu.write(0x7FCE57, knock)
            self._owned[0x7FCE57] = knock
        if self._own(emu, 0x7FCE76, 1):
            emu.write(0x7FCE76, mean)
            self._owned[0x7FCE76] = mean
        # tmot, u8, T = 0.75*x - 48 degC: 20 degC warming to 90 degC over 120 s
        degc = 20.0 + 70.0 * min(t / 120.0, 1.0)
        emu.write(0x8021EF, bytes([int((degc + 48.0) / 0.75) & 0xFF]))
        emu.write(0x802228, struct.pack(">H", int((degc + 48.0) / 0.75 * 16)))
        # The raster activation counters.  Periods per brief C4
        # (re/findings/scheduler.md 11-12): 1 ms, 2 ms and 10 ms, i.e. 1000,
        # 500 and 100 counts/s -- NOT the 10/20/100 ms of scheduler.md 5.4.
        # Only the live set counts; the other stays at zero.
        set_b = self.live_task_set.upper() == "B"
        emu.write(0x7FD754, struct.pack(">I", 0 if set_b else int(t * 100)))
        emu.write(0x7FD75C, struct.pack(">I", 0 if set_b else int(t * 1000)))
        emu.write(0x7FD758, struct.pack(">I", int(t * 100) if set_b else 0))
        emu.write(0x7FD760, struct.pack(">I", int(t * 1000) if set_b else 0))
        emu.write(0x7FD778, struct.pack(">I", int(t * 500) if set_b else 0))
        # Flash 1: patches/ff_counter/ at build.ram = 0x7FFB00.  Brief F1
        # (2026-09-22) gave it a hook in the 10 ms raster of BOTH task sets --
        # 0x432940 (set A, on-chip) and 0x12067C (set B) -- so unlike C1's
        # single set-B hook it counts whichever set is live, at 100/s either
        # way, and `ff_src_seen` at +0x06 says which hook ran (1 = A, 2 = B,
        # 3 = both; patches/ff_counter/README.md "The RAM block").  A frozen
        # block is now a statement about the FLASH, not about the task sets --
        # flash1_counter.json check 1.  The simulator never runs both hooks at
        # once, so it emits 1 or 2 and never 3.
        if not self.owns_patch_ram:
            return                      # a PatchRunner keeps 0x7FFB00 itself
        if self.flexfuel:
            # patches/ff_fuel hooks the same two words (its README's hook
            # table), so its 10 ms producer runs under either set too.
            emu.write(0x7FFB00, self.flexfuel_block(t))
        elif self.flash1:
            emu.write(0x7FFB00, struct.pack(">I", int(t * 100)))
            emu.write(0x7FFB04, struct.pack(">H", 0xFC01))
            emu.write(0x7FFB06, bytes([2 if set_b else 1]))       # ff_src_seen
            emu.write(0x7FFB07, bytes([0]))                       # ff_reserved

    def power_on(self, emu) -> None:
        for addr, value in self.statics.items():
            emu.write(addr, value)
        self.apply(emu, 0.0)


#: plausible constants for the addresses of logging/sessions/wave_b_confirm.json
#: that are not animated, so every row of #44 reads as something.
DEFAULT_STATICS = {
    0x8030C4: struct.pack(">H", 2400),     # ti_sum, 1 us/LSB  -> 2.4 ms
    0x8031DA: struct.pack(">H", 12000),    # prist, 0.005 bar  -> 60 bar
    0x8031F4: struct.pack(">H", 12200),    # prsoll            -> 61 bar
    0x7FEF87: bytes([0x14]),               # zw, s8, 0.75 deg  -> 15 deg
    # dwkrz (0x7FCE57) is ANIMATED now, not static: it used to sit at
    # {0,1,0,2,0,1}, and +2 is a value the knock controller cannot produce
    # (ignition.md section 5: the array is s8 and <= 0).  AnimatedRam.dwkrz().
    0x803088: struct.pack(">H", 0x0140),   # dwi
    0x80307E: struct.pack(">H", 0x00C8),   # wbho1s
    0x80316E: struct.pack(">H", 0x0200),
    0x8031F6: struct.pack(">H", 0x0010),
    0x80235A: struct.pack(">H", 0x0080),
    0x80302C: struct.pack(">H", 1024),     # ksta_kstaa, 1024 = 1.0
    0x802B08: struct.pack(">H", 0x0003),   # CAN 0x1A0 state word, module B
    0x802B72: struct.pack(">H", 0x0003),   # CAN 0x0C2 state word, module C
    0x802AFE: struct.pack(">H", 0x0003),   # CAN 0x050 state word, module C
    0x803EF4: bytes.fromhex("1122334455667788"),   # 0x1A0 data
    0x803F54: bytes.fromhex("99aabbccddeeff00"),   # 0x0C2 data
    0x803F60: bytes.fromhex("0f1e2d3c4b5a6978"),   # 0x050 data
}


# ---------------------------------------------------------------------------
# running the patch: a simulated clock that drives the real hooks
# ---------------------------------------------------------------------------
def apply_patch_to_temp(patch_dir: str, dump_path: str = DUMP) -> str:
    """Apply a patch directory to a temporary image and return its path.

    `tools/patch_apply.py`'s checked-in `changes` are complete, so this needs
    no cross compiler (`docs/03_tooling.md` section 5).
    """
    sys.path.insert(0, os.path.join(REPO, "tools"))
    import patch_apply                                   # noqa: E402

    import pathlib
    data, report, _warnings = patch_apply.apply_patch(
        pathlib.Path(dump_path), pathlib.Path(patch_dir))
    if not report["ok"]:
        raise RuntimeError(f"{patch_dir} did not apply: {report['issues']}")
    out = os.path.join(tempfile.mkdtemp(prefix="ecu_sim_"),
                       os.path.basename(os.path.normpath(patch_dir)) + ".bin")
    with open(out, "wb") as fh:
        fh.write(bytes(data))
    return out


class PatchRunner:
    """Calls `patches/ff_fuel`'s own hooks on the simulator's `Med9Emu`.

    Time here is **simulated**.  `advance(t)` runs whatever activations the
    patch would have had between the last call and simulated second `t`:

    * the 10 ms raster hook of the live task set, `1000 / ff_tick_ms` times
      per simulated second (100, `re/findings/scheduler.md` section 11);
    * the segment hook at 0x42247C once per simulated ignition segment, whose
      rate follows the animated rpm (`rpm * cylinders / 120` for a four-stroke
      engine), with `rk` re-produced upstream first (:meth:`AnimatedRam.rk_base`);
    * `nvm_pump_wrapper` (0x061944) once per 10 ms activation, because that is
      where the two stock background tasks call it (`eeprom.md` section 8.4)
      and without it a queued EEPROM commit never completes.

    The ignition stub at 0x41D40C is **not** called: it is a mid-function
    trampoline that returns into `zwgru_build`, so calling it in isolation
    would execute that function's tail with register state nobody produced.
    `ff_dzw_e` and `ff_fzw_q8` are computed by the 10 ms producer anyway, so
    measuring block 108 is live without it.
    """

    #: how much simulated time one `advance()` may make up in one go, so a
    #: slow host cannot stall the CAN bus while it catches up
    MAX_CATCHUP_S = 0.5

    #: and how much WALL time it may spend doing so.  This is the one that
    #: matters: the simulator answers TP2.0 from the same thread, and the
    #: tester gives up on an ACK after T1 = 100 ms x 4 tries.  A 0.5 s
    #: catch-up at `--time-scale 5` is fifty activations, which is 40 ms of
    #: host CPU on an idle M2 and more than twice that when the test suite is
    #: running beside it -- enough to lose a channel.  With a wall budget the
    #: bus is serviced every few milliseconds whatever the scale, and
    #: simulated time simply falls behind, which is already how a slow host
    #: behaves (logging/README.md section 9).
    MAX_CATCHUP_WALL_S = 0.005

    def __init__(self, emu, patch_dir: str | None, *, task_set: str = "A",
                 ram: "AnimatedRam | None" = None, segments: bool = True,
                 nvm_pump: bool = True, tick_ms: float = 10.0):
        from emu.toucan import SLOT15, RxMailbox

        self.emu = emu
        self.patch_dir = patch_dir
        if patch_dir is None:
            #: STOCK mode: no patch, so the only thing to run per segment is
            #: what the unmodified word at 0x42247C calls.  This is what makes
            #: a stock baseline comparable with a --sim-patch run instead of
            #: differing by every cell rksplit touches.
            self.syms = {}
            self.hook = None
            self.rk_hook = RKSPLIT
        else:
            with open(os.path.join(patch_dir, "patch.json"), encoding="utf-8") as fh:
                build = json.load(fh)["build"]
            self.syms = {k: int(v, 0) for k, v in build["symbols"].items()}
            #: The 10 ms raster hook of the live set, found by suffix rather
            #: than by name, so `patches/ff_counter` (`ff_counter_hook_a/_b`)
            #: runs here too and a Flash-1 rehearsal exercises the patch's own
            #: stubs instead of `AnimatedRam`'s stand-in.
            want = "_hook_a" if task_set.upper() == "A" else "_hook_b"
            named = sorted(k for k in self.syms if k.endswith(want))
            if not named:
                raise KeyError(
                    f"{patch_dir}/patch.json has no *{want} symbol; "
                    f"it has {sorted(self.syms)}")
            self.hook = self.syms[named[0]]
            self.rk_hook = self.syms.get("ff_fuel_rk_hook")
        self.task_set = task_set.upper()
        self.ram = ram
        self.segments_enabled = segments and self.rk_hook is not None
        self.nvm_pump = nvm_pump
        self.tick_s = tick_ms / 1000.0
        self.mailbox = RxMailbox(emu, SLOT15)
        #: the ethanol frame's id, out of FFCAL001 when the image carries it;
        #: a patch without that block (ff_counter) listens to nothing, so the
        #: default is only a place for `on_frame` to compare against.
        self.can_id = (int.from_bytes(emu.read(FFCAL001_BASE + 0x0C, 2), "big")
                       if emu.read(FFCAL001_BASE, 8) == b"FFCAL001" else 0x0EC)

        self.sim_t = 0.0
        self.ticks = 0
        self.segments = 0
        self.frames_in = 0
        #: how often a catch-up ran out of its wall budget, i.e. how often the
        #: simulated clock fell behind the wall clock
        self.lagged = 0
        self.errors: list[str] = []
        self._seg_accum = 0.0
        #: Frames wait in a short queue rather than overwriting one slot.  A
        #: real mailbox does overwrite, but simulated time moves in bursts of
        #: up to MAX_CATCHUP_S while the sender thread runs on the wall clock,
        #: so one slot would drop most of a burst and the ECU would see 2 Hz
        #: instead of 10.  The queue is bounded, so a runner that falls badly
        #: behind still drops the oldest, exactly as the hardware would.
        self._pending: "collections.deque[bytes]" = collections.deque(maxlen=8)
        self._lock = threading.Lock()
        self.mailbox.idle()

    # -- the bus side ------------------------------------------------------
    def on_frame(self, can_id: int, data: bytes) -> None:
        """Every frame the simulator sees; ours is copied into the mailbox."""
        if can_id == self.can_id and len(data) == 8:
            with self._lock:
                self._pending.append(bytes(data))
                self.frames_in += 1

    def _take_frame(self) -> bytes | None:
        with self._lock:
            return self._pending.popleft() if self._pending else None

    # -- the clock ---------------------------------------------------------
    def advance(self, target_s: float) -> None:
        """Run the hooks forward to simulated second `target_s`."""
        if target_s <= self.sim_t:
            return
        target_s = min(target_s, self.sim_t + self.MAX_CATCHUP_S)
        wall_deadline = time.monotonic() + self.MAX_CATCHUP_WALL_S
        while self.sim_t + self.tick_s <= target_s:
            self.sim_t += self.tick_s
            self._one_tick()
            if time.monotonic() >= wall_deadline:
                self.lagged += 1
                return

    def _one_tick(self) -> None:
        if self.ram is not None:
            self.ram.apply(self.emu, self.sim_t)
        frame = self._take_frame()
        if frame is not None:
            self.mailbox.arm(frame)
        else:
            self.mailbox.idle()
        if self.hook is not None:
            self._call(self.hook, "10 ms hook")
        self.ticks += 1
        if self.nvm_pump:
            self._call(NVM_PUMP_WRAPPER, "nvm pump", max_insns=4_000_000)
        if self.segments_enabled:
            self._run_segments()

    def _run_segments(self) -> None:
        rpm = self.ram.rpm(self.sim_t) if self.ram is not None else 0.0
        cylinders = self.ram.cylinders if self.ram is not None else 6
        self._seg_accum += rpm * cylinders / 120.0 * self.tick_s
        n = int(self._seg_accum)
        self._seg_accum -= n
        base = (self.ram.rk_base(self.sim_t) if self.ram is not None else 1024)
        for _ in range(min(n, 64)):
            self.emu.write(RK_FUEL_MASS, struct.pack(">H", base))
            self._call(self.rk_hook, "rk hook")
            self.segments += 1

    def _call(self, addr: int, what: str, max_insns: int = 2_000_000) -> None:
        res = self.emu.call(addr, regs={"r1": TASK_STACK_TOP}, reset=False,
                            max_insns=max_insns)
        if not res.ok and len(self.errors) < 20:
            self.errors.append(
                f"{what} at {addr:#08x}: {res.stop_reason} at {res.pc:#08x} "
                f"{res.issues[:1]}")

    def status(self) -> str:
        return (f"sim {self.sim_t:.2f} s, {self.ticks} activations, "
                f"{self.segments} segments, {self.frames_in} frames in"
                + (f", {self.lagged} catch-ups cut short" if self.lagged else "")
                + (f", {len(self.errors)} hook errors" if self.errors else ""))


class FlashCrcTask:
    """Run the firmware's own `flash_crc_task` (0x11CB10) in the background.

    The state machine is the firmware's, unchanged: state 0 builds the
    reflected CRC-32 table (polynomial 0xEDB88320) into RAM 0x800288 and loads
    range 0 from `tbl_crc32_ranges` (0x0A3A10); state 1 hashes **0x64 bytes per
    activation** with no reset between ranges; state 2 publishes the two
    halfwords to 0x7F9178 / 0x7F917A; state 7 sets bit 0 of 0x801200 and of
    0x7F9176 and every later activation returns at once.

    **The period (brief G5, from G3's `re/findings/boot.md` 6.8).**  The task
    is not a raster: it is five consecutive processes of the set-A
    background task 0, so the simulator runs it in **background loops** of
    :data:`FLASH_CRC_PER_LOOP` activations, one loop per `bg_loop_ms` (T_bg)
    of simulated time, and bumps the loop counter 0x7FD70C once per loop.
    The cursor 0x7FB700 therefore moves by **500 bytes per loop**, and the
    value appears in loop :data:`FLASH_CRC_LOOPS` = 4,926, i.e.
    ``4,926 x T_bg`` after power-on.  T_bg is bounded **0.51 ms <= T_bg <=
    300.75 ms** (VERIFIED-STATIC), so the publish time on a real ECU lies
    between 2.5 s and 1,481 s; the default 50 ms (246 s) is a HYPOTHESIS
    inside the bound until the bench reads the slope of 0x7FB700 or 0x7FD70C
    (`logging/sessions/flash_crc.json`).

    **A warm ECU** (``warm=True``) is one on which the task already ran this
    power cycle: state 7, the value published, and every activation a no-op.
    That state is written directly -- the expected value from
    :meth:`expected` over the image the emulator was built from, the other
    cells as a cold run leaves them (a test pins the two against each other)
    -- because producing it the honest way costs 24,627 activations.

    **What it costs.** 2,462,208 bytes are 24,627 activations and about 37 s
    of host CPU whatever T_bg is; that is why this is opt-in (`--flash-crc`)
    and why `advance()` carries the same wall-clock budget `PatchRunner`
    does: a simulator that services TP2.0 from the same thread must not
    disappear into the CRC.  With a short T_bg the simulated clock simply
    falls behind (``lagged``).  `0x7F9178` moves exactly **once**, at the
    end; what a logging session can watch move every loop is the cursor
    0x7FB700, the running register 0x7FB6F8 and the loop counter 0x7FD70C.

    **The harness must not show up in the answer.**  The second range is the
    on-chip flash, and `emu/time_base.py` rewrites three words at 0x47846C
    inside it -- which changes the CRC (0x7FB0DF4E instead of 0x5562139F).
    Anything the harness writes into a hashed range does.  So the task
    compares the live flash against the image the emulator was built from and
    puts the image's own bytes back for the one activation whose window covers
    them.  A *patch* is not hidden: it is part of `emu.dump`, and a patched
    image really does report a different checksum (:meth:`expected`).
    """

    #: how much wall time one `advance()` may spend, as `PatchRunner`
    MAX_CATCHUP_WALL_S = 0.003

    def __init__(self, emu, *, bg_loop_ms: float = BG_LOOP_MS_DEFAULT,
                 hide_harness_writes: bool = True, warm: bool = False):
        if not BG_LOOP_MS_MIN <= bg_loop_ms <= BG_LOOP_MS_MAX:
            raise ValueError(
                f"T_bg = {bg_loop_ms} ms is outside the VERIFIED-STATIC bound "
                f"{BG_LOOP_MS_MIN}-{BG_LOOP_MS_MAX} ms (boot.md 6.8(c)); a "
                "real ECU with a longer background loop trips fatal code 0x74")
        self.emu = emu
        self.bg_loop_s = bg_loop_ms / 1000.0
        #: loops run since construction; the simulated clock is this times
        #: T_bg, so it never drifts by float accumulation
        self._ticks = 0
        self.activations = 0
        self.loops = 0
        #: the background loop in which the value appeared (None until then)
        self.published_loop: int | None = None
        self.lagged = 0
        self.errors: list[str] = []
        self.warm = warm
        self.ranges = self._read_ranges()
        self.shadow = (self._find_harness_writes() if hide_harness_writes
                       else {})
        self._write_loop_counter(0)
        if warm:
            self._make_warm()

    # -- setup -------------------------------------------------------------
    def _read_ranges(self) -> list[tuple[int, int]]:
        return self.read_ranges(lambda a, n: self.emu.read(a, n))

    @staticmethod
    def read_ranges(read) -> list[tuple[int, int]]:
        """`tbl_crc32_ranges` through ``read(addr, n)``, up to its {0, 0}."""
        out, addr = [], FLASH_CRC_RANGES
        while True:
            start, end = struct.unpack(">II", bytes(read(addr, 8)))
            if end == 0:
                return out
            out.append((start, end))
            addr += 8

    @staticmethod
    def expected(image) -> int:
        """The value `flash_crc_task` publishes for a firmware image.

        `image` is a path or the dump's bytes.  One reflected CRC-32 (zlib's)
        over the concatenation of the ranges the image's own
        `tbl_crc32_ranges` names -- the recomputation `flash_crc.json` item 6
        asks for before a bench run of a patched image.  VERIFIED against the
        emulated task for the stock image (0x5562139F, brief F3) and, over the
        patched tail, for `patches/ff_fuel` (`tests/test_ecu_sim_flashcrc.py`).
        """
        import zlib
        tools = os.path.join(REPO, "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import med9lib as ml                                  # noqa: E402

        data = (ml.load_dump(image) if isinstance(image, (str, os.PathLike))
                else image)

        def read(addr, n):
            off = ml.cpu_to_file(addr)
            return data[off:off + n]

        crc = 0
        for start, end in FlashCrcTask.read_ranges(read):
            crc = zlib.crc32(bytes(read(start, end - start + 1)), crc)
        return crc

    def _find_harness_writes(self) -> dict[int, bytes]:
        """{address: the image's own bytes} for every run the harness changed.

        `Med9Emu.dump` is the file the emulator was built from -- the patched
        temporary image when `--sim-patch` is on -- so this finds exactly the
        edits made *after* load: the virtual time base, and anything else a
        future harness writes into a hashed range.
        """
        tools = os.path.join(REPO, "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import med9lib as ml                                  # noqa: E402

        out: dict[int, bytes] = {}
        for start, end in self.ranges:
            live = self.emu.read(start, end - start + 1)
            base = ml.cpu_to_file(start)
            stock = bytes(self.emu.dump[base:base + len(live)])
            if live == stock:
                continue
            run_start = None
            for i in range(len(live) + 1):
                differs = i < len(live) and live[i] != stock[i]
                if differs and run_start is None:
                    run_start = i
                elif not differs and run_start is not None:
                    out[start + run_start] = stock[run_start:i]
                    run_start = None
        return out

    #: What a finished cold run leaves in the byte budget 0x7FB6F6: 0x20 of
    #: the last activation's 0x64 unused.  Read off the emulated stock run
    #: (brief G5); it depends only on the range table, which no patch moves.
    WARM_BUDGET_LEFT = 0x20

    def _make_warm(self) -> None:
        """The post-run state of a task that already published this cycle.

        Every cell as a finished cold run leaves it -- `tests/
        test_ecu_sim_flashcrc.py` compares a warm start with a cold run cell
        by cell, so this cannot drift.  The register 0x7FB6F8 holds the final
        (already inverted) value, the same number as the two halfwords.
        """
        crc = self.expected(bytes(self.emu.dump))
        _last_start, last_end = self.ranges[-1]
        e = self.emu
        e.write(FLASH_CRC_STATE, b"\x07")
        e.write(FLASH_CRC_RANGE, bytes([len(self.ranges)]))
        e.write(FLASH_CRC_BUDGET, struct.pack(">H", self.WARM_BUDGET_LEFT))
        e.write(FLASH_CRC_ACC, struct.pack(">I", crc))
        e.write(FLASH_CRC_END, struct.pack(">I", last_end))
        e.write(FLASH_CRC_CURSOR, struct.pack(">I", last_end + 1))
        e.write(FLASH_CRC_PUB_HI, struct.pack(">HH", crc >> 16, crc & 0xFFFF))
        e.write(FLASH_CRC_FLAGS, bytes([e.read(FLASH_CRC_FLAGS, 1)[0] | 1]))
        e.write(FLASH_CRC_DONE, bytes([e.read(FLASH_CRC_DONE, 1)[0] | 1]))
        self.published_loop = FLASH_CRC_LOOPS
        self.loops = FLASH_CRC_LOOPS
        self._write_loop_counter(self.loops)

    def _write_loop_counter(self, n: int) -> None:
        self.emu.write(BG_LOOP_COUNTER, struct.pack(">I", n & 0xFFFFFFFF))

    # -- state -------------------------------------------------------------
    @property
    def state(self) -> int:
        return self.emu.read(FLASH_CRC_STATE, 1)[0]

    @property
    def done(self) -> bool:
        return bool(self.emu.read(FLASH_CRC_DONE, 1)[0] & 1)

    @property
    def cursor(self) -> int:
        return struct.unpack(">I", self.emu.read(FLASH_CRC_CURSOR, 4))[0]

    @property
    def crc(self) -> int:
        """The published 32-bit value; 0 until state 2 has run."""
        hi, lo = struct.unpack(">HH", self.emu.read(FLASH_CRC_PUB_HI, 4))
        return (hi << 16) | lo

    @property
    def loop_counter(self) -> int:
        return struct.unpack(">I", self.emu.read(BG_LOOP_COUNTER, 4))[0]

    @property
    def sim_t(self) -> float:
        """Simulated seconds this task has been running for."""
        return self._ticks * self.bg_loop_s

    def _due(self, target_s: float) -> int:
        """Whole loops between the task's clock and `target_s`."""
        return max(int(target_s / self.bg_loop_s + 1e-9) - self._ticks, 0)

    @property
    def publish_s(self) -> float:
        """Simulated seconds after power-on at which the value appeared."""
        loops = self.published_loop or FLASH_CRC_LOOPS
        return loops * self.bg_loop_s

    # -- running -----------------------------------------------------------
    def _swap(self, lo: int, hi: int) -> list[tuple[int, bytes]]:
        saved = []
        for addr, stock in self.shadow.items():
            if addr < hi and lo < addr + len(stock):
                saved.append((addr, bytes(self.emu.read(addr, len(stock)))))
                self.emu.write(addr, stock)
        return saved

    def _unswap(self, saved) -> None:
        for addr, live in saved:
            self.emu.write(addr, live)
        drop = getattr(self.emu.uc, "ctl_remove_cache", None)
        for addr, live in saved:
            if drop is not None:
                try:
                    drop(addr, addr + len(live))
                except Exception:                             # pragma: no cover
                    pass

    def activate(self) -> bool:
        """One activation.  False once the task has published and parked."""
        if self.done:
            return False
        cursor = self.cursor
        saved = self._swap(cursor, cursor + 0x68) if self.shadow else []
        try:
            res = self.emu.call(FLASH_CRC_TASK, regs={"r1": TASK_STACK_TOP},
                                reset=False, max_insns=4_000_000)
        finally:
            if saved:
                self._unswap(saved)
        self.activations += 1
        if not res.ok and len(self.errors) < 20:
            self.errors.append(
                f"flash_crc_task at {FLASH_CRC_TASK:#08x}: {res.stop_reason} "
                f"at {res.pc:#08x}")
            return False
        return True

    def loop(self) -> bool:
        """One background loop: five activations, then the loop counter.

        Returns False if an activation failed.  On a task that has already
        published the five activations are the firmware's no-op and only the
        counter moves, as on the ECU (boot.md 6.8(e) item 2).
        """
        ok = True
        if not self.done:
            for _ in range(FLASH_CRC_PER_LOOP):
                before = len(self.errors)
                if not self.activate():
                    ok = len(self.errors) == before
                    break
        self.loops += 1
        self._write_loop_counter(self.loops)
        if self.published_loop is None and self.done:
            self.published_loop = self.loops
        return ok

    def advance(self, target_s: float) -> None:
        """Run one background loop per T_bg of simulated time, within a budget."""
        n = self._due(target_s)
        if n == 0:
            return
        if self.done:
            # only the counter moves; no need to call the parked task
            self._ticks += n
            self.loops += n
            self._write_loop_counter(self.loops)
            return
        deadline = time.monotonic() + self.MAX_CATCHUP_WALL_S
        while self._due(target_s):
            self._ticks += 1
            if not self.loop():
                return
            if time.monotonic() >= deadline:
                self.lagged += 1
                return

    def run_to_completion(self, max_loops: int = 6000) -> int | None:
        """Loop until the value is published; the CRC, or None.

        The simulated clock moves with the loops, so afterwards
        ``sim_t == publish_s``.
        """
        while self.loops < max_loops and not self.done:
            self._ticks += 1
            if not self.loop():
                return None
        return self.crc if self.done else None

    def status(self) -> str:
        return (f"flash CRC: {self.loops} background loops "
                f"(T_bg {self.bg_loop_s * 1000:g} ms), {self.activations} "
                f"activations, state {self.state}, cursor {self.cursor:#08x}"
                + (f", published {self.crc:#010x} at loop "
                   f"{self.published_loop} = {self.publish_s:.1f} simulated s"
                   if self.done else
                   f" ({100.0 * self.activations / FLASH_CRC_ACTIVATIONS:.1f} %)"))


# ---------------------------------------------------------------------------
# the fault memory: what 18 / 17 / 14 read and clear (brief G5, kwp.md 12.7)
# ---------------------------------------------------------------------------
#: The firmware's RAM fault memory ("Fehlerspeicher"), VERIFIED-STATIC from
#: `dfp_list_active` 0x43E080 and `dfp_entry_read` 0x43DB20 (both on-chip):
#: up to 20 entries of 0x5C bytes at 0x7F8890; 0x7F91CA = entries in use,
#: 0x7F91CB = length of the 1-based order list at 0x7F91AA.  An entry is
#: reported by `18` when its halfword +0 has bit 0x2000 clear, its fault-path
#: id +2 is non-zero and bit 0x0800 of +0x1C is set.
FAULT_MEMORY = 0x7F8890
FAULT_ENTRY_LEN = 0x5C
FAULT_SLOTS = 20
FAULT_USED = 0x7F91CA
FAULT_ORDER_LEN = 0x7F91CB
FAULT_ORDER = 0x7F91AA
#: four SAE-encoded DTCs per fault path, u16 at +(path*4 + k)*2; `18` always
#: reads this one (0x3519C-0x351B8), `14` the one `dtc_code_table_select`
#: (init entry 34) put into 0x7FBA58 -- 0x5DA6DE in this dump.
DTC_TABLE_18 = 0x5D9F06
DTC_TABLE_PTR = 0x7FBA58
#: the fault-memory lock `14` takes through 0x43D7AC: {0x7FBA5C, ~0x7FBA5C}
#: must read {0, 0xFFFFFFFF} (what `dfp_init` leaves) and becomes {0xFA, ~0xFA}
DFP_LOCK = 0x7FBA5C
#: `kwp_sid_14_h1`'s state byte (0 idle, 1 committing, 2 done)
DTC_CLEAR_STATE = 0x7FB718
#: the per-path "readiness" bytes `18` reads for status bit 0x10
DFP_PATH_FLAGS = 0x7F9C46
#: `kwp_register_table` 0x13E974 stores the config-struct pointer 0x803DDC,
#: which `kwp_service_h2_walk` 0x13ECB0 walks (obd.md 10.1).  Its only caller
#: is 0x13C9FC inside init entry 281 (0x1344C0, the diagnostic-stack start-up,
#: far too wide to run here), so power_on calls just this one function.
KWP_REGISTER_TABLE = 0x13E974
KWP_SERVICE_H2_WALK = 0x13ECB0
#: how long the simulator keeps re-dispatching a pending (0x78) service, in
#: simulated seconds.  `14` waits 0x2AD4E9 time-base ticks (~0.8 s) after its
#: NVM commit before it answers, so a clear takes about a second.
PENDING_BUDGET_S = 5.0
PENDING_STEP_S = 0.1


def dtc_text(code: int) -> str:
    """SAE J2012 text of a two-byte DTC: 0x0601 -> 'P0601'."""
    return "PCBU"[code >> 14] + f"{(code >> 12) & 3}{code & 0xFFF:03X}"


def dtc_code(text: str) -> int:
    """The inverse of :func:`dtc_text`."""
    text = text.strip().upper()
    if len(text) != 5 or text[0] not in "PCBU":
        raise ValueError(f"not an SAE DTC: {text!r}")
    return ("PCBU".index(text[0]) << 14) | (int(text[1]) << 12) | int(text[2:], 16)


def parse_read_dtc(answer: bytes) -> list[tuple[int, int]]:
    """`58 n (hi lo status)*n` -> [(code, status)].  Raises on a bad shape."""
    if len(answer) < 2 or answer[0] != 0x58:
        raise ValueError(f"not a readDTCByStatus answer: {answer.hex()}")
    n = answer[1]
    if len(answer) != 2 + 3 * n:
        raise ValueError(f"{n} DTCs need {2 + 3 * n} bytes, got {len(answer)}")
    return [(int.from_bytes(answer[2 + 3 * i:4 + 3 * i], "big"),
             answer[4 + 3 * i]) for i in range(n)]


class DtcStore:
    """Seed and inspect the firmware's own RAM fault memory.

    `18 00 FF 00` (readDiagnosticTroubleCodesByStatus), `17 hi lo`
    (readStatusOfDiagnosticTroubleCodes) and `14 FF 00`
    (clearDiagnosticInformation) are all answered by the **real handlers**
    through the ordinary dispatch path (`kwp_sid_18_h1` 0x35064,
    `kwp_sid_17_h1` 0x36024, `kwp_sid_14_h1` 0x35410); this class only puts
    entries where those handlers look.  Two things here are a MODEL, not the
    firmware, and say so:

    * :meth:`seed` writes an entry in the layout `dfp_entry_read` reads,
      standing in for the fault-path manager (DFPM) that debounces a fault
      into memory on the car.  The simulator runs no DFPM task.
    * :meth:`after_clear` empties the store and releases the lock once the
      real `14` has answered positively.  On the ECU `14` only takes the
      lock (0x43D7AC) and commits EEP_CONF block 24; the erase itself is done
      later by DFPM processes behind the lock state machine 0x7FBA5C
      (0xFA..0xFD, e.g. 0x0D4D74, 0x125C18 in task 8) that the simulator does
      not run.  The observable result -- an empty `18` after a positive `14`
      -- is what the bench sequence needs, and it is labelled.
    """

    def __init__(self, emu):
        self.emu = emu
        self.clears = 0

    # -- the tables ----------------------------------------------------------
    def code_of(self, path: int, kind: int, table: int = DTC_TABLE_18) -> int:
        return struct.unpack(">H", self.emu.read(table + (path * 4 + kind) * 2,
                                                 2))[0]

    def find(self, code: int, table: int = DTC_TABLE_18) -> tuple[int, int]:
        """The first (fault path, kind 0..3) whose code is `code`."""
        for path in range(1, 0xFA):
            for kind in range(4):
                if self.code_of(path, kind, table) == code:
                    return path, kind
        raise KeyError(f"{dtc_text(code)} is in no fault path of the table "
                       f"at {table:#08x}")

    # -- the store -----------------------------------------------------------
    @property
    def used(self) -> int:
        return self.emu.read(FAULT_USED, 1)[0]

    def entries(self) -> list[dict]:
        out = []
        order = self.emu.read(FAULT_ORDER, self.emu.read(FAULT_ORDER_LEN, 1)[0])
        for idx in order:
            raw = self.emu.read(FAULT_MEMORY + (idx - 1) * FAULT_ENTRY_LEN,
                                FAULT_ENTRY_LEN)
            out.append({"slot": idx - 1,
                        "path": struct.unpack_from(">H", raw, 2)[0],
                        "kinds": raw[0x0A],
                        "reported": bool(struct.unpack_from(">H", raw, 0x1C)[0]
                                         & 0x0800)})
        return out

    def seed(self, dtc: "str | int | None" = None, *, path: int | None = None,
             kind: int = 0) -> tuple[int, int]:
        """MODEL: store one active fault, as the DFPM would have.

        Give a DTC ("P0601" or 0x0601) or a fault path and a kind 0..3.
        Returns (path, kind).  Kind k sets bit k of entry +0x0A, which is
        how `18` picks the path's k-th code and sets status bit 1 << k.
        """
        if dtc is not None:
            code = dtc_code(dtc) if isinstance(dtc, str) else int(dtc)
            path, kind = self.find(code)
        if path is None or not 1 <= path < 0xFA or not 0 <= kind <= 3:
            raise ValueError(f"fault path {path} / kind {kind}")
        slot = self.used
        if slot >= FAULT_SLOTS:
            raise ValueError("the fault memory holds 20 entries")
        entry = bytearray(FAULT_ENTRY_LEN)
        struct.pack_into(">H", entry, 2, path)
        entry[0x0A] = 1 << kind
        struct.pack_into(">H", entry, 0x1C, 0x0800)
        self.emu.write(FAULT_MEMORY + slot * FAULT_ENTRY_LEN, bytes(entry))
        n = self.emu.read(FAULT_ORDER_LEN, 1)[0]
        self.emu.write(FAULT_ORDER + n, bytes([slot + 1]))
        self.emu.write(FAULT_USED, bytes([slot + 1]))
        self.emu.write(FAULT_ORDER_LEN, bytes([n + 1]))
        return path, kind

    def after_clear(self) -> None:
        """MODEL: what the DFPM does after a positive `14` (class docstring)."""
        self.emu.write(FAULT_MEMORY, bytes(FAULT_SLOTS * FAULT_ENTRY_LEN))
        self.emu.write(FAULT_ORDER, bytes(FAULT_SLOTS))
        self.emu.write(FAULT_USED, b"\x00\x00")
        self.emu.write(DFP_LOCK, struct.pack(">II", 0, 0xFFFFFFFF))
        self.clears += 1


class FrameTap:
    """A `CanLink` proxy that shows every received frame to a callback.

    `Tp20Server` drops anything that is not its own channel, so the ethanol
    frame would otherwise be invisible.  Wrapping the link keeps
    `logging/med9kwp/` free of any knowledge of the patch.
    """

    def __init__(self, link, on_frame):
        self._link = link
        self._on_frame = on_frame

    def recv(self, timeout: float):
        frame = self._link.recv(timeout)
        if frame is not None:
            self._on_frame(frame[0], frame[1])
        return frame

    def __getattr__(self, name):
        return getattr(self._link, name)


# ---------------------------------------------------------------------------
# the handler layer
# ---------------------------------------------------------------------------
class Med9Handlers:
    """Answers a KWP request by running the firmware's own handler."""

    def __init__(self, dump_path: str = DUMP, *, seed: int | None = None,
                 animate: bool = True, ram: AnimatedRam | None = None,
                 clock=None, session_timeout_s: float | None = None,
                 patch_dir: str | None = None, eeprom: str | None = None,
                 time_scale: float = 1.0, run_patch: bool = True,
                 stock_tasks: bool = False, wip_polls: int = 0,
                 time_base: bool = True, flash_crc: "bool | float" = False,
                 flash_crc_warm: bool = False, dtcs=()):
        from emu import Med9Emu
        from emu.time_base import VirtualTimeBase
        if patch_dir:
            dump_path = apply_patch_to_temp(patch_dir, dump_path)
        self.dump_path = dump_path
        self.emu = Med9Emu(dump_path, r2="app")
        #: Installed **first**, before anything runs `read_time_base`: the
        #: level-1 seed loop spins forever on a frozen time base, so `27 01`
        #: could not be answered at all and `kwp_sec_lfsr_rounds` had to be
        #: hand-seeded.  See `emu/time_base.py`.
        self.time_base = VirtualTimeBase(self.emu) if time_base else None
        self.seed_override = seed
        self.animate = animate
        self.ram = ram or AnimatedRam(statics=dict(DEFAULT_STATICS))
        self.clock = clock or time.monotonic
        #: simulated seconds per wall-clock second (see logging/README.md)
        self.time_scale = time_scale
        self.t0 = self.clock()
        self.eeprom_path = eeprom
        self.eeprom = None
        self.qspi = None
        self.runner: PatchRunner | None = None
        self.flash_crc: "FlashCrcTask | None" = None
        #: P3: drop back to session 0 after this long without a KWP request.
        #: `None` = never, which is what an emulator does on its own; the real
        #: ECU times out in ~5 s (kwp.md 2.2, exact value not extracted).
        self.session_timeout_s = session_timeout_s
        self._last_request = self.clock()
        self.session_timeouts = 0
        self.table = self._read_table()
        self.log: list[str] = []
        #: A patched image is detected, not declared: FFCAL001 at 0x5E2510 is
        #: only there if patches/ff_fuel was applied, and then 0x7FFB00 carries
        #: `struct ff_state` rather than ff_counter's block (brief D2, #39).
        if self.emu.read(FFCAL001_BASE, 8) == b"FFCAL001":
            self.ram.flexfuel = True
        #: and a Flash-1 image the same way: `patches/ff_counter`'s blob sits
        #: at 0x150000, which is erased (0xFF) in a stock image.  A stock image
        #: must leave 0x7FFB00 alone -- `bench_rehearsal.py`'s stock step reads
        #: those bytes and a live counter there is a false "the patch is in".
        elif self.emu.read(FF_COUNTER_FLASH, 4) != b"\xFF\xFF\xFF\xFF":
            self.ram.flash1 = True
        if eeprom:
            self._install_eeprom(eeprom, wip_polls)
        if (patch_dir and run_patch) or stock_tasks:
            self.runner = PatchRunner(self.emu, patch_dir if run_patch else None,
                                      task_set=self.ram.live_task_set,
                                      ram=self.ram if self.animate else None,
                                      nvm_pump=self.eeprom is not None)
            #: with the hooks running, `ff_state` is the patch's own, so the
            #: AnimatedRam stand-in must keep its hands off 0x7FFB00
            if patch_dir:
                self.ram.flexfuel = False
                self.ram.owns_patch_ram = False
        self.power_on()
        #: the firmware's RAM fault memory, and what seeds it (a MODEL of the
        #: DFPM; the read and clear services are the firmware's own)
        self.dtc = DtcStore(self.emu)
        for d in dtcs:
            self.dtc.seed(d)
        #: new TP2.0 connections seen (:meth:`on_connect`)
        self.connections = 0
        #: built after `power_on`, so `flash_crc_init` (init entry 75) has put
        #: the state byte back to 0 and the shadow scan sees the final flash.
        #: `flash_crc` is True (T_bg = BG_LOOP_MS_DEFAULT) or T_bg in ms.
        self.flash_crc = None
        if flash_crc or flash_crc_warm:
            t_bg = (BG_LOOP_MS_DEFAULT if flash_crc is True or not flash_crc
                    else float(flash_crc))
            self.flash_crc = FlashCrcTask(self.emu, bg_loop_ms=t_bg,
                                          warm=flash_crc_warm)

    # -- the EEPROM device -------------------------------------------------
    def _install_eeprom(self, path: str, wip_polls: int) -> None:
        """Attach `emu/qspi_eeprom.py` and run the start-up block read."""
        from emu import qspi_eeprom as qe

        if not os.path.exists(path):
            image = qe.factory_image(DUMP)
            with open(path, "wb") as fh:
                fh.write(image)
            self.log.append(f"created a factory EEPROM image at {path}")
        self.eeprom = qe.M95160.load(path, wip_polls=wip_polls)
        self.qspi, _binding = qe.install_eeprom(self.emu, device=self.eeprom)
        if not qe.cold_start(self.emu):
            self.log.append("WARNING: nvm_read_all_blocks never reached idle")

    def save_eeprom(self, path: str | None = None) -> str | None:
        """Write the device image back, so a restart sees what was stored."""
        target = path or self.eeprom_path
        if self.eeprom is None or target is None:
            return None
        self.eeprom.save(target)
        return target

    # -- setup -------------------------------------------------------------
    def _read_table(self) -> list[KwpEntry]:
        cfg = self.emu.read(KWP_CONFIG_STRUCT, 0x20)
        base = struct.unpack(">I", cfg[0:4])[0]
        count = cfg[0x1C]
        raw = self.emu.read(base, count * KWP_TABLE_ENTRY)
        out = []
        for i in range(count):
            e = raw[i * KWP_TABLE_ENTRY:(i + 1) * KWP_TABLE_ENTRY]
            mask_a = struct.unpack(">H", e[2:4])[0]
            smask, h1, h2, arg = struct.unpack(">IIII", e[4:20])
            out.append(KwpEntry(e[0], e[1], mask_a, smask, h1, h2, arg))
        return out

    def power_on(self) -> None:
        """Bring RAM to the state the firmware's own start-up leaves.

        The three KWP state bytes below are the residue documented in the
        module docstring; everything else is produced by running the real
        init-table entries of :data:`INIT_ENTRIES`, in the table's own order.
        `self.init_log` records how each one ended, so a test can assert that
        the firmware -- not this file -- wrote the cells it checks.
        """
        self.emu.write(SESSION_CURRENT, b"\x00")
        self.emu.write(SECURITY_STATE, b"\x00")
        self.emu.write(SEC_SEED, b"\x00\x00\x00\x00")
        self.init_log = []
        for idx, addr, name in INIT_ENTRIES:
            res = self.emu.call(addr, regs={"r1": TASK_STACK_TOP},
                                reset=False, max_insns=4_000_000)
            self.init_log.append((idx, name, addr, res.ok))
            if not res.ok:                                   # pragma: no cover
                self.log.append(
                    f"init entry {idx} ({name} {addr:#08x}) did not return: "
                    f"{res.stop_reason} at {res.pc:#08x}")
        # the config-struct pointer the h2 walk reads (see KWP_REGISTER_TABLE)
        res = self.emu.call(KWP_REGISTER_TABLE, args=[KWP_CONFIG_STRUCT],
                            regs={"r1": TASK_STACK_TOP}, reset=False)
        if not res.ok:                                       # pragma: no cover
            self.log.append(f"kwp_register_table did not return: "
                            f"{res.stop_reason} at {res.pc:#08x}")
        self.ram.power_on(self.emu)
        if self.time_base is not None:
            self.time_base.advance(0.0)
        self.t0 = self.clock()

    # -- state -------------------------------------------------------------
    @property
    def session(self) -> int:
        return self.emu.read(SESSION_CURRENT, 1)[0]

    @property
    def security_state(self) -> int:
        return self.emu.read(SECURITY_STATE, 1)[0]

    def sim_time(self) -> float:
        """Simulated seconds since power-on.

        Without a :class:`PatchRunner` this is wall-clock time times
        ``time_scale``.  With one it is the runner's own clock, which only
        moves in 10 ms activations and can fall behind the wall clock on a
        slow host -- so every animated cell and every hook always see the same
        instant (`logging/README.md`, "bench rehearsal").
        """
        if self.runner is not None:
            return self.runner.sim_t
        return (self.clock() - self.t0) * self.time_scale

    def wall_target(self) -> float:
        return (self.clock() - self.t0) * self.time_scale

    def step(self) -> None:
        """Let the patch's hooks and the CRC task catch up with the clock."""
        if self.runner is not None:
            self.runner.advance(self.wall_target())
        if self.flash_crc is not None:
            self.flash_crc.advance(self.sim_time())

    def read_ram(self, addr: int, size: int) -> bytes:
        return self.emu.read(addr, size)

    def on_connect(self) -> None:
        """A new diagnostic connection: run the firmware's `h2` walk once.

        `kwp_conn_cyclic` (0x13E650) calls `kwp_service_h2_walk` (0x13ECB0)
        once per new connection from the set-A 10 ms task (obd.md 10.1, G3).
        The walk calls every dispatch-table h2 -- among them the OBD support
        bitmap builder 0x5CBE8, the DDLI wipe and `14`'s state reset -- and
        puts the session back to 0.  The simulator has no 10 ms task, so
        `EcuSimulator` calls this when `Tp20Server` opens a channel: the
        trigger is modelled, the walk is the firmware's.
        """
        res = self.emu.call(KWP_SERVICE_H2_WALK, regs={"r1": TASK_STACK_TOP},
                            reset=False, max_insns=2_000_000)
        self.connections += 1
        if not res.ok:                                       # pragma: no cover
            self.log.append(f"kwp_service_h2_walk did not return: "
                            f"{res.stop_reason} at {res.pc:#08x}")

    def _pump_nvm(self, n: int = 8) -> None:
        """What the two 10 ms tasks do for the EEP_CONF queue (eeprom.md 8.4)."""
        if self.eeprom is None:
            return
        for _ in range(n):
            self.emu.call(NVM_PUMP_WRAPPER, regs={"r1": TASK_STACK_TOP},
                          reset=False, max_insns=4_000_000)

    def _finish_pending(self, entry: "KwpEntry", sid: int, data: bytes):
        """Re-dispatch a service that answered 'response pending' (status 8).

        The ECU sends `7F sid 78` and dispatches the same request again until
        the handler leaves the pending state (kwp.md 1.3, the 0x803DB8/B9
        bytes).  The simulator does that here, in one go: between two calls
        it pumps the NVM queue (so `14`'s EEP_CONF commit can finish; that
        needs `--eeprom`) and moves the virtual time base on by
        PENDING_STEP_S, which the `14` handler compares against its own
        0x2AD4E9-tick hold-off.  The loop is the simulator's; every call is
        the firmware's handler.  -> (status, body) of the last call.
        """
        t = self.sim_time()
        status, body = STATUS_PENDING[0], b""
        steps = int(PENDING_BUDGET_S / PENDING_STEP_S)
        for _ in range(steps):
            self._pump_nvm()
            t += PENDING_STEP_S
            if self.time_base is not None:
                self.time_base.advance(t)
            status, body = self._call(entry.h1, sid, data, entry.arg)
            if status not in STATUS_PENDING:
                return status, body
        self.log.append(
            f"SID {sid:#04x} still pending after {PENDING_BUDGET_S:g} simulated "
            "s" + ("" if self.eeprom is not None else
                   " -- no --eeprom, so its NVM commit cannot complete"))
        return status, body

    # -- calling a real handler -------------------------------------------
    def _call(self, handler: int, sid: int, data: bytes, arg: int = 0):
        """Run `handler` with a seeded `kwp_io_struct`; return (status, bytes)."""
        if len(data) > IO_BUFFER_MAX - 8:
            raise ValueError("request longer than the simulated I/O buffer")
        s = bytearray(16)
        s[0:4] = struct.pack(">I", IO_BUFFER)
        struct.pack_into(">H", s, 6, len(data))
        s[0xA] = 0
        s[0xB] = sid & 0xFF
        s[0xC] = data[0] if data else 0        # the cached sub-function, kwp.md 12.1
        self.emu.write(IO_STRUCT, bytes(s))
        self.emu.write(IO_BUFFER, bytes(data) + b"\x00" * (IO_BUFFER_MAX - len(data)))
        res = self.emu.call(handler, args=[arg, IO_STRUCT], reset=False)
        if not res.ok:
            raise RuntimeError(
                f"handler {handler:#08x} for SID {sid:#04x} did not return "
                f"({res.stop_reason} at {res.pc:#08x}); {res.issues[:2]}")
        st = self.emu.read(IO_STRUCT, 16)
        rlen = struct.unpack(">H", st[8:10])[0]
        body = self.emu.read(IO_BUFFER, max(rlen, 1))[:rlen]
        return st[0xA], body

    # -- session -----------------------------------------------------------
    def _start_session(self, sub: int) -> list[bytes]:
        if sub not in SESSION_MAP:
            return [bytes([0x7F, 0x10, 0x11])]
        internal, needs = SESSION_MAP[sub]
        if needs is not None and self.security_state != needs:
            return [bytes([0x7F, 0x10, 0x33])]
        self.emu.call(H_SESSION_SET, args=[internal], reset=False)
        if sub in (0x85, 0x86):
            # kwp.md 2: entering the upload session resets the security state
            self.emu.write(SECURITY_STATE, b"\x00")
        # kwp.md 4.1: the dynamic ids are wiped on every session change, by
        # the real kwp_sid_2C_h2.
        self._call(H_DDLI_WIPE, 0x2C, b"\x04")
        return [bytes([0x50, sub])]

    # -- dispatch ----------------------------------------------------------
    def handle(self, request: bytes) -> list[bytes]:
        """One KWP request in, the list of KWP responses out (usually one)."""
        if not request:
            return []
        self.step()
        if self.animate:
            self.ram.apply(self.emu, self.sim_time())
        if self.time_base is not None:
            # every handler sees a time base that has moved since the last
            # request, which is what the level-1 seed loop insists on
            self.time_base.advance(self.sim_time())
        now = self.clock()
        if (self.session_timeout_s is not None and self.session != 0
                and now - self._last_request > self.session_timeout_s):
            # P3 expired: back to session 0, dynamic ids wiped (kwp.md 2.2)
            self.emu.call(H_SESSION_SET, args=[0], reset=False)
            self.emu.write(SECURITY_STATE, b"\x00")
            self._call(H_DDLI_WIPE, 0x2C, b"\x04")
            self.session_timeouts += 1
            self.log.append(f"session timed out after "
                            f"{now - self._last_request:.2f} s")
        self._last_request = now
        sid, data = request[0], request[1:]
        matching = [e for e in self.table if e.sid == sid]
        if not matching:
            return [bytes([0x7F, sid, 0x11])]
        allowed = [e for e in matching
                   if e.sub == 0xFF or (data and e.sub == data[0])]
        if not allowed:
            return [bytes([0x7F, sid, 0x12])]
        session = self.session
        entry = next((e for e in allowed if e.allows(session)), None)
        if entry is None:
            self.log.append(
                f"SID {sid:#04x} refused: session {session} not in mask "
                f"{allowed[0].session_mask:#06x}")
            return [bytes([0x7F, sid, 0x33])]

        if sid == 0x10:
            return self._start_session(data[0] if data else 0)

        status, body = self._call(entry.h1, sid, data, entry.arg)
        if status in STATUS_PENDING and sid != 0x27:
            status, body = self._finish_pending(entry, sid, data)
            if status == STATUS_POSITIVE and sid == 0x14:
                self.dtc.after_clear()                       # MODEL, see DtcStore
            final = ([bytes([(sid + 0x40) & 0xFF]) + body]
                     if status == STATUS_POSITIVE else
                     [bytes([0x7F, sid, body[0] if body and status ==
                             STATUS_NEGATIVE else 0x10])])
            return [bytes([0x7F, sid, 0x78])] + final
        if sid == 0x27 and data[:1] == b"\x03" and self.seed_override is not None \
                and status == STATUS_POSITIVE:
            # substitute the time-base seed the emulator cannot produce
            self.emu.write(SEC_SEED, struct.pack(">I", self.seed_override))
            body = body[:1] + struct.pack(">I", self.seed_override) + body[5:]
        if status == STATUS_POSITIVE:
            return [bytes([(sid + 0x40) & 0xFF]) + body]
        if status == STATUS_NEGATIVE:
            return [bytes([0x7F, sid, body[0] if body else 0x10])]
        if status in STATUS_PENDING:
            # kwp.md 3.2: a wrong key defers, arms the lockout and answers
            # invalidKey.  Send the pending answer first, as the ECU does.
            return [bytes([0x7F, sid, 0x78]), bytes([0x7F, sid, 0x35])]
        return [bytes([0x7F, sid, 0x10])]


# ---------------------------------------------------------------------------
# the bus-facing simulator
# ---------------------------------------------------------------------------
class EcuSimulator:
    """`Tp20Server` + :class:`Med9Handlers`, with a poll loop you can thread."""

    def __init__(self, link: CanLink, handlers: Med9Handlers | None = None, *,
                 address: int = 0x01, delay_ms: float = 0.0,
                 drop_ack: int = 0, seed: int | None = None,
                 session_timeout_s: float | None = None, animate: bool = True,
                 dump: str = DUMP, patch_dir: str | None = None,
                 eeprom: str | None = None, ram: AnimatedRam | None = None,
                 trace: list[str] | None = None, verbose: bool = False,
                 dtcs=()):
        #: `dump` is how a PATCHED image is driven end to end: the handlers are
        #: the firmware's own, so `21 <group>` on patches/ff_fuel's image runs
        #: the patch's measuring handlers (brief D2, issue #39).  `patch_dir`
        #: goes one further and runs the patch's own hooks (brief E4).
        self.handlers = handlers or Med9Handlers(
            dump, seed=seed, animate=animate, patch_dir=patch_dir,
            eeprom=eeprom, ram=ram, session_timeout_s=session_timeout_s,
            dtcs=dtcs)
        runner = self.handlers.runner
        self.link = FrameTap(link, runner.on_frame) if runner else link
        link = self.link
        self.server = Tp20Server(link, address=address,
                                 params=Tp20Params(),
                                 delay_s=delay_ms / 1000.0, trace=trace)
        if drop_ack:
            self.server.drop_next_acks(drop_ack)
        self.verbose = verbose
        self.requests = 0
        self._channels_seen = 0
        self._stop = threading.Event()
        self.error: BaseException | None = None

    # -- construction ------------------------------------------------------
    @classmethod
    def on_virtual_bus(cls, channel: str = "med9", **kw) -> "EcuSimulator":
        link = open_link(parse_bus_spec(f"virtual:{channel}"))
        link.set_accept(None)
        return cls(link, **kw)

    @property
    def runner(self) -> "PatchRunner | None":
        return self.handlers.runner

    # -- loop --------------------------------------------------------------
    def poll(self, timeout: float = 0.05) -> bool:
        """Service the bus once.  True if a request was answered."""
        request = self.server.poll(timeout)
        if self.server.channels_opened != self._channels_seen:
            # a new diagnostic connection: the firmware re-runs its h2 walk
            self._channels_seen = self.server.channels_opened
            self.handlers.on_connect()
        # the patch keeps ticking whether or not a tester is talking to us
        self.handlers.step()
        if request is None:
            return False
        self.requests += 1
        try:
            responses = self.handlers.handle(request)
        except Exception as exc:                            # pragma: no cover
            responses = [bytes([0x7F, request[0], 0x10])]
            self.handlers.log.append(f"handler crash: {exc!r}")
        if self.verbose:
            print(f"  -> {request.hex()}  <- "
                  + " ".join(r.hex() for r in responses), flush=True)
        for r in responses:
            self.server.send_message(r)
        return True

    def serve_forever(self, timeout: float = 0.05) -> None:
        try:
            while not self._stop.is_set():
                self.poll(timeout)
        except BaseException as exc:                         # pragma: no cover
            self.error = exc
            raise

    def stop(self) -> None:
        self._stop.set()

    @contextlib.contextmanager
    def background(self, timeout: float = 0.02):
        """Run `serve_forever` in a daemon thread for the duration of a block.

        The protocol core stays thread-free; this is only how a *test* (or
        `med9log.py --sim`) gets an ECU and a tester into the same process.
        """
        thread = threading.Thread(target=self.serve_forever, args=(timeout,),
                                  name="ecu_sim", daemon=True)
        thread.start()
        try:
            yield self
        finally:
            self.stop()
            thread.join(timeout=5.0)

    def close(self) -> None:
        self.stop()
        self.handlers.save_eeprom()
        self.link.close()


# ---------------------------------------------------------------------------
# self-test: the handlers alone, no bus
# ---------------------------------------------------------------------------
def self_test(dump_path: str = DUMP) -> bool:
    """Run every service the logger needs through the real handlers."""
    from med9kwp.kwp import key_level2

    h = Med9Handlers(dump_path, seed=0x12345678, animate=False)
    ok = True
    results: list[tuple[str, str, bool]] = []

    def step(label: str, request: bytes, expect_prefix: bytes | None = None):
        nonlocal ok
        answers = h.handle(request)
        got = answers[-1] if answers else b""
        good = expect_prefix is None or got.startswith(expect_prefix)
        ok = ok and good
        results.append((label, got.hex(), good))
        return got

    step("21 F0 before a session", b"\x21\xf0", b"\x7f\x21\x33")
    step("10 89 session", b"\x10\x89", b"\x50\x89")
    step("3E (no sub-function)", b"\x3e", b"\x7e")
    step("3E 01 -> NRC 0x12", b"\x3e\x01", b"\x7f\x3e\x12")
    h.emu.write(0x7FFB00, bytes.fromhex("000005CBFC010000"))
    step("2C F0 04 clear", b"\x2c\xf0\x04", b"\x6c\xf0")
    step("2C F0 03 def", b"\x2c\xf0\x03\x01\x06\x7f\xfb\x00", b"\x6c\xf0")
    step("21 F0 read", b"\x21\xf0", b"\x61\xf0\x00\x00\x05\xcb\xfc\x01")
    step("21 01 group", b"\x21\x01", b"\x61\x01")
    step("21 8C group 140", b"\x21\x8c", b"\x7f\x21\x31")
    step("35 without session 0x86", b"\x35\x7f\x80\x00\x00\x00\x01\x00",
         b"\x7f\x35\x33")
    seed_answer = step("27 03 seed", b"\x27\x03", b"\x67\x03\x12\x34\x56\x78")
    seed = int.from_bytes(seed_answer[2:6], "big") if len(seed_answer) >= 6 else 0
    step("27 04 key", b"\x27\x04" + key_level2(seed).to_bytes(4, "big"),
         b"\x67\x04\x34")
    step("10 86 upload session", b"\x10\x86", b"\x50\x86")
    h.emu.write(0x7F8000, bytes(range(256)))
    step("35 RAM ok", b"\x35\x7f\x80\x00\x00\x00\x01\x00", b"\x75\x3f")
    step("36 block", b"\x36", bytes([0x76]) + bytes(range(62)))
    step("37 exit", b"\x37", b"\x77")
    step("35 protected window", b"\x35\x7f\x9e\x00\x00\x00\x02\x00",
         b"\x7f\x35\x31")
    step("21 F0 after session change", b"\x21\xf0", b"\x7f\x21\x12")
    # the fault services (brief G5): real handlers, an empty then a seeded
    # store (the seeding is the MODEL, DtcStore.seed)
    step("10 89 again", b"\x10\x89", b"\x50\x89")
    step("18 00 FF 00 no DTCs", b"\x18\x00\xff\x00", b"\x58\x00")
    h.dtc.seed("P0601")
    step("18 00 FF 00 seeded P0601", b"\x18\x00\xff\x00",
         b"\x58\x01\x06\x01")
    step("17 06 01 status of P0601", b"\x17\x06\x01", b"\x57\x01\x06\x01")
    step("18 02 FF 00 -> NRC 0x12", b"\x18\x02\xff\x00", b"\x7f\x18\x12")

    width = max(len(r[0]) for r in results)
    for label, hexed, good in results:
        print(f"  {label:<{width}}  {hexed:<28} {'ok' if good else 'MISMATCH'}")
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Emulated MED9.1.1 speaking TP2.0/KWP2000 on a CAN bus.")
    ap.add_argument("--bus", default="virtual:med9",
                    help="interface:channel (default virtual:med9)")
    ap.add_argument("--dump", default=DUMP, help="firmware image to run")
    ap.add_argument("--address", type=lambda s: int(s, 0), default=0x01,
                    help="logical address to answer on (default 0x01, engine)")
    ap.add_argument("--seed", type=lambda s: int(s, 0), default=0x12345678,
                    help="SecurityAccess seed to report (the time base is 0 "
                         "under Unicorn); 0 keeps the handler's own value")
    ap.add_argument("--drop-ack", type=int, default=0, metavar="N",
                    help="silently drop the next N TP2.0 ACKs")
    ap.add_argument("--delay-ms", type=float, default=0.0,
                    help="delay every frame this long")
    ap.add_argument("--no-animate", action="store_true",
                    help="freeze the animated RAM cells")
    ap.add_argument("--sim-patch", metavar="DIR", default=None,
                    help="apply a patch directory (patches/ff_fuel) to a "
                         "temporary image AND run its hooks on a simulated "
                         "10 ms raster -- the bench rehearsal of brief E4")
    ap.add_argument("--sim-stock-tasks", action="store_true",
                    help="without --sim-patch: still run the stock code the "
                         "patch's hooks replace (rksplit per segment, the NVM "
                         "pump per 10 ms), so a STOCK baseline log is "
                         "comparable with a --sim-patch run")
    ap.add_argument("--eeprom", metavar="FILE", default=None,
                    help="back the QSPI EEPROM with this 2 KB image "
                         "(emu/qspi_eeprom.py); created from the firmware's "
                         "own block defaults if it does not exist, and "
                         "written back when the simulator stops")
    ap.add_argument("--wip-polls", type=int, default=0, metavar="N",
                    help="make an EEPROM page write report WIP for N status "
                         "polls (0 = instant, which is what a time base that "
                         "never advances gives us anyway)")
    ap.add_argument("--flash-crc", nargs="?", type=float, default=None,
                    const=BG_LOOP_MS_DEFAULT, metavar="T_BG_MS",
                    help="run the firmware's own flash_crc_task (0x11CB10) in "
                         "the background as five processes of background "
                         "task 0: one loop of 5 activations (500 bytes) and "
                         "one tick of the loop counter 0x7FD70C per T_BG_MS "
                         "of simulated time (default %(const)g ms, a "
                         "HYPOTHESIS inside the VERIFIED-STATIC bound "
                         f"{BG_LOOP_MS_MIN}-{BG_LOOP_MS_MAX} ms, boot.md "
                         "6.8). The value appears at loop 4,926 = 4,926 x "
                         "T_BG_MS (246 simulated s at the default; ~37 s of "
                         "host CPU whatever T_BG is); "
                         "logging/sessions/flash_crc.json logs it")
    ap.add_argument("--flash-crc-warm", action="store_true",
                    help="model a WARM ECU: the CRC task already published "
                         "this power cycle (state 7), so nothing but the loop "
                         "counter moves (flash_crc.json item 1)")
    ap.add_argument("--print-flash-crc", action="store_true",
                    help="print the value flash_crc_task would publish for "
                         "--dump (or for --sim-patch applied to it) and exit: "
                         "the recomputation flash_crc.json item 6 asks for "
                         "before a bench run of a patched image")
    ap.add_argument("--seed-dtc", action="append", default=[], metavar="DTC",
                    help="store this DTC (e.g. P0601) in the firmware's RAM "
                         "fault memory at power-on, so 18/17 have something "
                         "to report. A MODEL of the fault-path manager; the "
                         "18/17/14 handlers are the firmware's. Repeatable. "
                         "14 (clear) needs --eeprom: it commits EEP_CONF "
                         "block 24 before it answers")
    ap.add_argument("--time-scale", type=float, default=1.0, metavar="X",
                    help="simulated seconds per wall-clock second (default 1)")
    ap.add_argument("--task-set", choices=("A", "B"), default="A",
                    help="which OS task set is live (scheduler.md 11-12, "
                         "11.8: set A is live by necessity, so it is the "
                         "default here). With A, the set-B counters and C1's "
                         "Flash-1 block stay at zero")
    ap.add_argument("--session-timeout", type=float, default=0.0, metavar="S",
                    help="drop back to session 0 after S seconds without a KWP "
                         "request (0 = never; the real ECU is about 5 s)")
    ap.add_argument("--self-test", action="store_true",
                    help="run the handlers directly and exit")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return 0 if self_test(args.dump) else 1
    if args.print_flash_crc:
        image = (apply_patch_to_temp(args.sim_patch, args.dump)
                 if args.sim_patch else args.dump)
        crc = FlashCrcTask.expected(image)
        print(f"{crc:#010x}  pub_hi={crc >> 16:#06x} pub_lo={crc & 0xFFFF:#06x}"
              f"  {args.sim_patch or args.dump}")
        return 0

    handlers = Med9Handlers(
        args.dump, seed=args.seed or None, animate=not args.no_animate,
        session_timeout_s=args.session_timeout or None,
        patch_dir=args.sim_patch, eeprom=args.eeprom,
        stock_tasks=args.sim_stock_tasks,
        flash_crc=args.flash_crc if args.flash_crc is not None else False,
        flash_crc_warm=args.flash_crc_warm, dtcs=args.seed_dtc,
        time_scale=args.time_scale, wip_polls=args.wip_polls,
        ram=AnimatedRam(live_task_set=args.task_set,
                        statics=dict(DEFAULT_STATICS)))
    link = open_link(parse_bus_spec(args.bus))
    link.set_accept(None)
    sim = EcuSimulator(link, handlers, address=args.address,
                       delay_ms=args.delay_ms, drop_ack=args.drop_ack,
                       verbose=args.verbose)
    print(f"MED9 simulator on {link.description}, logical address "
          f"{args.address:#04x}, seed {args.seed:#010x}")
    if handlers.runner is not None:
        r = handlers.runner
        print(f"running {args.sim_patch} on task set {r.task_set}: 10 ms hook "
              f"{r.hook:#08x}, segment hook {r.rk_hook:#08x}, "
              f"CAN id {r.can_id:#05x}, {args.time_scale:g} simulated s / "
              f"wall s")
    if handlers.eeprom is not None:
        print(f"EEPROM: {args.eeprom} (2 KB M95160, written back on exit)")
    if handlers.flash_crc is not None:
        c = handlers.flash_crc
        print(f"flash CRC task: {len(c.ranges)} ranges, "
              f"{FLASH_CRC_ACTIVATIONS} activations in {FLASH_CRC_LOOPS} "
              f"background loops of {c.bg_loop_s * 1000:g} ms = "
              f"{c.publish_s:.1f} simulated s to a published value"
              + (" (WARM: already published)" if c.warm else "")
              + (f", hiding {len(c.shadow)} harness edit(s) from it"
                 if c.shadow else ""))
    print("ctrl-C to stop")
    try:
        sim.serve_forever()
    except KeyboardInterrupt:
        print(f"\n{sim.requests} requests served")
        if handlers.runner is not None:
            print(handlers.runner.status())
            for line in handlers.runner.errors[:5]:
                print("  !", line)
        if handlers.flash_crc is not None:
            print(handlers.flash_crc.status())
    finally:
        sim.close()
    for line in handlers.log[-5:]:
        print(" ", line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
