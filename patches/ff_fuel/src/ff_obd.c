/*
 * ff_obd - OBD mode 01 PID 0x52, "ethanol fuel %" (brief G1, issue #39).
 *
 * The stock generic-OBD code does all the work.  `obd_pid_support_build`
 * (0x5CBE8) rebuilds the "supported PIDs" bitmaps in RAM from each list
 * entry's record, and `obd_pid_read` (0x5CED4) answers a PID by copying the
 * value bytes of the first record whose PID matches and whose `valid` byte is
 * non-zero (re/findings/obd.md sections 3-4).  A one-byte PID of the B group
 * reads the record `{value, valid}`: value at +0, valid at +1.
 *
 * patch.json grows the stock three-entry B2 list to four (seven instruction
 * words in those two functions, the list itself in the blank block 0x160000,
 * and class byte 0x02 for PID 0x52 at 0x0A3A06 - re/findings/obd.md section
 * 9), and points the fourth entry at the record this file writes:
 *
 *   ff_state.obd52_a      (+0x4C)  A = round(e_filt * 255 / 1600), 0..255
 *   ff_state.obd52_valid  (+0x4D)  1 = ff_pid52_enable && cal_ok, else 0
 *
 * J1979 decodes PID 0x52 as A * 100 / 255 %, so A = 255 is E100.  `e_filt` is
 * in 1/16 %, which is why the divisor is 1600 and why the rounding is half up
 * in integer arithmetic: (e_filt * 255 + 800) / 1600.  The widest intermediate
 * is 1600 * 255 + 800 = 408,800, far inside u32.
 *
 * THE RUN-TIME GATE.  The seven instruction edits and the list are always in
 * the image; what `ff_pid52_enable` = 0 (the shipped value) controls is the
 * `valid` byte.  With `valid` = 0 the builder skips the fourth entry, the
 * bitmap is exactly the stock one, and `obd_pid_read` returns 0 for PID 0x52
 * exactly as it does on the stock image, where 0x52 has no entry at all.  The
 * disabled image is therefore OBSERVABLY identical to stock - bitmaps and
 * every PID answer - although it is not byte-identical, and
 * tests/test_ff_obd_patch.py proves the first property in the emulator.
 *
 * Safety properties this file is written to keep
 *   - it writes four bytes of our own state block and nothing else;
 *   - it reads `e_filt`, `cal_ok` and one FFCAL001 byte, nothing stock;
 *   - no loop, no division by anything but a constant, no stock call;
 *   - it can change nothing the engine does: the only reader of the record
 *     is the OBD mode-01 code in the KWP task.
 */
#include "../../common/types.h"
#include "../../common/med9_stock.h"
#include "ff_state.h"

_Static_assert(__builtin_offsetof(struct ff_state, obd52_a) == FF_OFF_OBD52,
               "patches/ff_fuel/Makefile exports ff_state + FF_OFF_OBD52 as"
               " ff_obd_pid52_rec; the list in flash points there");
_Static_assert(__builtin_offsetof(struct ff_state, obd52_valid)
               == FF_OFF_OBD52 + 1u,
               "a one-byte B-group record is {value, valid}: obd_pid_read"
               " (0x5CED4) reads valid at +1 (0x5CFF0 lbz r10,1(r7))");
_Static_assert(FF_OFF_OBD52 >= FF_CORE2_OFF
               && FF_OFF_OBD52 + 4u == FF_CORE2_OFF + FF_CORE2_LEN,
               "the record and its reserved halfword end core 2");

void ff_obd_update(void)
{
    u32 a;

    if (MED9_U8(FF_CAL_BASE + FF_CAL_O_PID52_ENABLE) == 0u
        || ff_state.cal_ok == 0u) {
        /* valid first, value second: a reader that preempts between the two
         * stores sees either the old pair or {old A, 0} - "not supported" -
         * never a supported PID carrying a value from nowhere. */
        ff_state.obd52_valid = 0u;
        ff_state.obd52_a = 0u;
        ff_state.obd_rsv = 0u;
        return;
    }

    a = ((u32)ff_state.e_filt * FF_OBD_A_FULL + FF_E_FILT_MAX / 2u)
        / FF_E_FILT_MAX;
    if (a > FF_OBD_A_FULL)
        a = FF_OBD_A_FULL;
    ff_state.obd52_a = (u8)a;          /* value first, then the flag */
    ff_state.obd52_valid = 1u;
    ff_state.obd_rsv = 0u;
}
