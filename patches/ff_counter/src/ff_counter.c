/*
 * ff_counter - Flash 1 (issue #27): the no-op patch.
 *
 * The smallest change that proves the whole chain at once: our code is in
 * flash, it is reached from a stock periodic task, it owns a RAM block, and
 * the ECU behaves exactly as before.  Nothing here reads or writes a single
 * stock variable.
 *
 * What runs on the ECU
 *   task_100ms (0x1205A0, TCB 23) calls a four-instruction leaf at 0x11F02C
 *   once per activation.  The patch replaces that one `bl` with a `bl` to the
 *   HOOK_TAIL trampoline (src/hook.S), which calls ff_counter_tick() and then
 *   tail-branches to 0x11F02C, so the stock call still happens.
 *   re/findings/scheduler.md section 8 has the evidence for the site.
 *
 * What it measures
 *   `ticks` counts activations of that task.  Brief C4 (2026-09-16,
 *   re/findings/scheduler.md section 11) settled the raster at 10 ms, so the
 *   expected slope is +100 per second; the task belongs to task set B, and if
 *   set A turns out to be the live one this hook never runs at all.  Either
 *   way this flash doubles as the measurement (test/procedure.md section 4,
 *   issue #44).
 *
 * Cold start
 *   Nothing zeroes our .bss: there is no startup code for a patch, and the
 *   ECU's own RAM init does not know this block exists.  So the first tick
 *   after power-up recognises itself by the absence of the ALIVE pattern and
 *   zeroes the counter.  The residual risk is the 1-in-65536 chance that the
 *   uninitialised half-word already reads 0xFC01, which costs nothing but a
 *   counter that starts at an arbitrary value; it cannot affect the engine.
 *
 * Safety
 *   No stock RAM is touched, no calibration is read, there is no branch that
 *   depends on engine state, and the code is straight-line: the worst case is
 *   the best case, ~15 instructions per activation.
 */
#include "../../common/types.h"

/*
 * The state block, placed first in .bss by patches/common/patch.ld, so its
 * layout is exactly what README.md documents and what the logger reads:
 *
 *     PATCH_RAM + 0x00  u32 ticks     activations of task_100ms since power-up
 *     PATCH_RAM + 0x04  u16 alive     0xFC01 once our code has run
 *     PATCH_RAM + 0x06  u16 reserved  always 0, room for a second counter
 */
#define FF_COUNTER_ALIVE 0xFC01u

struct ff_counter_state {
    volatile u32 ticks;
    volatile u16 alive;
    volatile u16 reserved;
};

struct ff_counter_state ff_state __attribute__((section(".bss.patch_state")));

_Static_assert(sizeof(struct ff_counter_state) == 8,
               "the state block layout in README.md assumes 8 bytes");

void ff_counter_tick(void);

/* Called by the ff_counter_hook trampoline, once per task_100ms activation. */
void ff_counter_tick(void)
{
    if (ff_state.alive != (u16)FF_COUNTER_ALIVE) {
        ff_state.ticks = 0u;
        ff_state.reserved = 0u;
        ff_state.alive = (u16)FF_COUNTER_ALIVE;
    }
    ff_state.ticks = ff_state.ticks + 1u;
}
