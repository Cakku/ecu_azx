/*
 * hooks.h - the C side of patches/common/hooks.S.
 *
 * The trampolines themselves are assembly (hooks.S); this header is what C
 * code includes to declare them and to document, in one checkable place, what
 * each trampoline promises the hooked site.
 *
 * Usage:
 *     #include "../common/hooks.h"
 *     HOOK_DECL(ff_counter_hook);      // defined by HOOK_TAIL in hook.S
 *     void ff_counter_tick(void);      // the C function it calls
 *
 * A hooked C function takes no arguments and returns nothing.  Both
 * trampolines call it as a plain EABI function with a valid, 8-byte aligned
 * r1 and a frame of its own; it may clobber the whole volatile set.
 */
#ifndef MED9_HOOKS_H
#define MED9_HOOKS_H

#ifndef __ASSEMBLER__

/* Declare a trampoline defined by HOOK_TAIL / HOOK_FULL in a patch's .S. */
#define HOOK_DECL(name) extern void name(void)

/*
 * What each trampoline saves and what it costs.  Kept here so a patch can
 * reason about stack headroom: the ECU switches r1 per task
 * (re/findings/scheduler.md section 7) and a raster task's stack is not large.
 * The C function's own frame comes on top of these.
 */
#define HOOK_TAIL_FRAME_BYTES 16
#define HOOK_FULL_FRAME_BYTES 80

/* Registers a hooked site may rely on across the trampoline. */
#define HOOK_TAIL_PRESERVES "LR"
#define HOOK_FULL_PRESERVES "r0, r3-r12, CR, LR, CTR, XER"

/*
 * Neither trampoline saves FP state: patch code is built -msoft-float
 * (docs/03_tooling.md section 3.1) and must stay integer-only.  Neither
 * touches r2, r13 or r14-r31.
 */

#endif /* __ASSEMBLER__ */
#endif /* MED9_HOOKS_H */
