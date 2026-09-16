/*
 * types.h - the only integer types MED9.1.1 patch code may use.
 *
 * There is no libc in a flash patch (docs/04_re_guidelines.md section 7), so
 * <stdint.h> is deliberately not used: it drags in the toolchain's headers and
 * with them assumptions about a hosted environment.  The sizes are asserted
 * here instead, so a toolchain change that moves `long` cannot slip through.
 *
 * PowerPC 32-bit big endian, ILP32:  char 1, short 2, int 4, long 4.
 *
 * Usage:
 *     #include "../common/types.h"
 */
#ifndef MED9_TYPES_H
#define MED9_TYPES_H

#ifndef __ASSEMBLER__

typedef unsigned char  u8;
typedef signed char    s8;
typedef unsigned short u16;
typedef signed short   s16;
typedef unsigned int   u32;
typedef signed int     s32;

_Static_assert(sizeof(u8)  == 1, "u8 is not 1 byte");
_Static_assert(sizeof(s8)  == 1, "s8 is not 1 byte");
_Static_assert(sizeof(u16) == 2, "u16 is not 2 bytes");
_Static_assert(sizeof(s16) == 2, "s16 is not 2 bytes");
_Static_assert(sizeof(u32) == 4, "u32 is not 4 bytes");
_Static_assert(sizeof(s32) == 4, "s32 is not 4 bytes");

/* The ECU core is big endian; a patch that assumes otherwise is broken. */
_Static_assert((u32)((u8)-1) == 255u, "char is not 8 bit / not unsigned-castable");

/*
 * Absolute-address accessors.  Patch code addresses ECU RAM and registers
 * absolutely (lis/ori), never through r2 or r13 - those are the ECU's own
 * small-data bases (docs/02_memory_map.md section 4) and
 * `tools/blobdis.py --check-sda` fails the build if they are touched.
 */
#define MED9_U8(addr)  (*(volatile u8  *)(u32)(addr))
#define MED9_S8(addr)  (*(volatile s8  *)(u32)(addr))
#define MED9_U16(addr) (*(volatile u16 *)(u32)(addr))
#define MED9_S16(addr) (*(volatile s16 *)(u32)(addr))
#define MED9_U32(addr) (*(volatile u32 *)(u32)(addr))
#define MED9_S32(addr) (*(volatile s32 *)(u32)(addr))

/*
 * Call a stock function whose address is known but whose prototype has to be
 * spelled out at the call site, e.g.
 *     MED9_FN(void (*)(void), MED9_CLR_RAM_7FE889_800E18)();
 * Stock prototypes are never invented in med9_stock.h: only what
 * re/symbols.csv proves is recorded there, and that is the address.
 */
#define MED9_FN(sig, addr) ((sig)(u32)(addr))

#endif /* __ASSEMBLER__ */
#endif /* MED9_TYPES_H */
