/*
 * ff_counter - Flash 1 (issue #27): the no-op patch.
 *
 * The smallest change that proves the whole chain at once: our code is in
 * flash, it is reached from a stock periodic task, it owns a RAM block, and
 * the ECU behaves exactly as before.  Nothing here reads or writes a single
 * stock variable.
 *
 * What runs on the ECU
 *   Both 10 ms rasters are hooked, one per task set (brief F1, 2026-09-22):
 *
 *     set A  0x432940 in task_100ms_int (0x4328E4, id 19) calls the empty leaf
 *            0x0BD9E4 once per activation -> ff_counter_tick_a()
 *     set B  0x12067C in task_100ms      (0x1205A0, id 32) calls the
 *            four-instruction leaf 0x11F02C            -> ff_counter_tick_b()
 *
 *   Each `bl` is replaced with a `bl` to a HOOK_TAIL trampoline (src/hook.S),
 *   which calls our C function and then tail-branches to the original target,
 *   so the stock call still happens.  re/findings/scheduler.md sections 8 and
 *   8.1 have the evidence for both sites.
 *
 *   The set-A word is in the ON-CHIP flash 0x404000-0x47FFFF and therefore
 *   carries "onchip_edit": true; `make HOOKS=external` builds the set-B word
 *   alone, which is byte for byte the pre-F1 patch (README.md, "Two build
 *   variants").
 *
 * What it measures
 *   `ticks` counts activations of whichever raster is live, and `src_seen`
 *   says which hook ran: 1 = set A, 2 = set B, 3 = both.  Brief C4
 *   (re/findings/scheduler.md section 11) settled the raster at 10 ms, so the
 *   expected slope is +100 per second either way; brief E1 (section 11.8)
 *   showed statically that set A is the live set, which is exactly what
 *   `src_seen` confirms or refutes from a single logged sample
 *   (test/procedure.md section 4).
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
 *   the best case, ~18 instructions per activation.
 */
#include "../../common/types.h"

/*
 * The state block, placed first in .bss by patches/common/patch.ld, so its
 * layout is exactly what README.md documents and what the logger reads:
 *
 *     PATCH_RAM + 0x00  u32 ticks     raster activations since power-up
 *     PATCH_RAM + 0x04  u16 alive     0xFC01 once our code has run
 *     PATCH_RAM + 0x06  u8  src_seen  1 = set A, 2 = set B, 3 = both
 *     PATCH_RAM + 0x07  u8  reserved  always 0
 *
 * In the HOOKS=external build there is only one source, so +0x06 keeps the
 * pre-F1 layout: a u16 `reserved` that is always 0.  That is what makes that
 * variant byte-identical to the patch as it stood before brief F1.
 */
#define FF_COUNTER_ALIVE 0xFC01u

#ifdef FF_HOOKS_BOTH
/* The two hook sources, ORed into `src_seen`; the same encoding
 * patches/ff_fuel uses (FF_SRC_A / FF_SRC_B in src/ff_state.h). */
#define FF_SRC_A 0x01u
#define FF_SRC_B 0x02u
#endif

struct ff_counter_state {
    volatile u32 ticks;
    volatile u16 alive;
#ifdef FF_HOOKS_BOTH
    volatile u8  src_seen;
    volatile u8  reserved;
#else
    volatile u16 reserved;
#endif
};

struct ff_counter_state ff_state __attribute__((section(".bss.patch_state")));

_Static_assert(sizeof(struct ff_counter_state) == 8,
               "the state block layout in README.md assumes 8 bytes");
_Static_assert(__builtin_offsetof(struct ff_counter_state, alive) == 4,
               "ff_alive must stay at PATCH_RAM + 0x04");
_Static_assert(__builtin_offsetof(struct ff_counter_state, reserved) == 6
               || __builtin_offsetof(struct ff_counter_state, reserved) == 7,
               "the tail of the block must stay at +0x06 / +0x07");

#ifdef FF_HOOKS_BOTH

void ff_counter_tick_a(void);
void ff_counter_tick_b(void);

/*
 * One activation.  `src` is the caller's bit, so a single logged sample of
 * `src_seen` answers re/findings/scheduler.md section 11.7 / 11.8.
 *
 * No arbitration is needed and none is wanted: unlike patches/ff_fuel, this
 * patch computes nothing, so a tick from the "wrong" set costs one count and
 * nothing else.  Exactly one set is live (section 11.8), and if both ever
 * fired the slope would simply be 200 /s with src_seen = 3 — which
 * test/procedure.md section 4 reads as its own outcome rather than hiding it
 * behind an owner byte.
 */
static void ff_counter_tick(u8 src)
{
    if (ff_state.alive != (u16)FF_COUNTER_ALIVE) {
        ff_state.ticks = 0u;
        ff_state.src_seen = 0u;
        ff_state.reserved = 0u;
        ff_state.alive = (u16)FF_COUNTER_ALIVE;
    }
    ff_state.src_seen = (u8)(ff_state.src_seen | src);
    ff_state.ticks = ff_state.ticks + 1u;
}

/* Called by the ff_counter_hook_a trampoline: task set A's 10 ms raster. */
void ff_counter_tick_a(void)
{
    ff_counter_tick((u8)FF_SRC_A);
}

/* Called by the ff_counter_hook_b trampoline: task set B's 10 ms raster. */
void ff_counter_tick_b(void)
{
    ff_counter_tick((u8)FF_SRC_B);
}

#else  /* HOOKS=external: the pre-F1 patch, byte for byte */

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

#endif /* FF_HOOKS_BOTH */
