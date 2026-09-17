/*
 * ff_state.h - the ff_fuel RAM state block and the FFCAL001 calibration block.
 *
 * Shared with brief D2 (diagnostics and persistence, issue #33): D2 adds code,
 * not layout.  Every offset below is part of the contract with
 * `logging/sessions/ff_fuel.json`, `emu/models/flexfuel.py` and
 * `tests/test_ff_fuel_patch.py`; changing one means changing all four.
 *
 * ---------------------------------------------------------------------------
 * RAM: PATCH_RAM = 0x7FFB00, 0x100 bytes (re/findings/ram.md section 8.1).
 *
 * The block is NOT filled at cold start (brief C2), so it carries a header -
 * magic, length and a checksum over the core - and the first activation that
 * finds the header wrong re-initialises the whole block.  `struct ff_state` is
 * placed in `.bss.patch_state`, which patches/common/patch.ld puts first in
 * `.bss`, so `ff_state == PATCH_RAM` exactly.
 *
 *   off  size  field            owner     meaning
 *   ---- ----  ---------------  --------  ------------------------------------
 *   +00   4    magic            tick      0x46463031 "FF01" when valid
 *   +04   2    length           tick      0x0040, the size of this struct
 *   +06   2    csum             tick      ~sum16 of the CORE bytes (+08..+2B)
 *   --- core: written only by the periodic tick, covered by csum -------------
 *   +08   2    e_filt           tick      filtered ethanol, 1/16 %, 0..1600
 *   +0A   2    f_q10            tick      fuel factor, 1/1024, 1024..2048
 *   +0C   1    mode             tick      0 INIT 1 OK 2 HOLD 3 FAULT 4 OFF
 *                                         5 OVERRIDE
 *   +0D   1    status           tick      status byte of the last frame (b7)
 *   +0E   1    e_raw            tick      ethanol % of the last frame (b0)
 *   +0F   1    t_fuel           tick      fuel temperature byte, degC + 40 (b1)
 *   +10   1    frame_ctr        tick      rolling counter of the last frame (b3)
 *   +11   1    fw_ver           tick      Pico firmware version (b6)
 *   +12   1    cal_mode         tick      ff_mode as read from FFCAL001
 *   +13   1    cal_ok           tick      1 = FFCAL001 header and checksum OK
 *   +14   2    age_ticks        tick      activations since the last good frame
 *   +16   2    hold_ticks       tick      activations left of the FAULT hold
 *   +18   2    frames           tick      good frames since power-up (sat)
 *   +1A   2    faults           tick      FAULT entries since power-up (sat)
 *   +1C   1    stall            tick      frames with an unchanged counter
 *   +1D   1    src_owner        tick      1 = set A hook, 2 = set B hook
 *   +1E   1    src_seen         tick      bit0 set A fired, bit1 set B fired
 *   +1F   1    src_foreign      tick      consecutive calls from the non-owner
 *   +20   4    ticks            tick      periodic activations since power-up
 *   +24   2    e_key            tick      decay target, 1/16 % (D2: from EEPROM)
 *   +26   2    e_frac           tick      sub-count of e_filt, 1/1024 of a count
 *   +28   1    frame_bad        tick      1 = the last frame was rejected
 *   +29   1    dzw_e            tick      E1: s8 ignition offset, 0.75 degCA
 *   +2A   2    fzw_q8           tick      E1: f_zw(E), 1/256, 0..255
 *   --- annex: NOT covered by csum (other writers, or pure diagnostics) ------
 *   +2C   4    rk_calls         rk hook   segment-task invocations since power-up
 *   +30   2    e_persist        D2        E% staged for / read back from EEPROM
 *   +32   1    persist_state    D2        0 idle 1 staged 2 committing 3 done 4 err
 *   +33   1    persist_err      D2        last nvm_block_request return code
 *   +34   2    diag_e_pct       D2        e_filt in 1 %, for the measuring block
 *   +36   2    diag_f_pct       D2        F in %, (f_q10 * 100) >> 10
 *   +38   2    diag_t_degc      D2        fuel temperature, degC + 40
 *   +3A   2    persist_wait     D2        activations left of the commit rate limit
 *   +3C   2    persist_writes   D2        commits that finished OK (saturating)
 *   +3E   2    persist_fails    D2        commits that failed (saturating)
 *   --- core 2: appended by E2, grown by E5, checksummed like the first core -
 *   +40   2    fst_q10          tick      E2: f_st(E, tmst), 1/1024, 1024..2560
 *   +42   1    zwst_add         tick      E2: start-ignition advance, s8, 0.75 degCA
 *   +43   1    msv_sat_ticks    tick      E5: MSV-saturated activations this window
 *   +44   2    prail_add        tick      E5: rail setpoint adder, 0.005 bar
 *   +46   2    win_margin_min   tick      E5: s16 worst injection-window margin
 *   +48   2    prist_min        tick      E5: worst prist this window, 0.005 bar
 *   +4A   2    diag_ticks       tick      E5: activations left of the window
 *   --- 0x4C -----------------------------------------------------------------
 *
 * D2 (issue #38/#39, 2026-09-16) took the three reserved words at +3A..+3F for
 * the rate-limit counter and two saturating counters.  No offset D1 defined
 * moved, the length is still 0x40 and the magic is still "FF01", so D1's
 * tests, logging/sessions/ff_fuel.json and emu/models/flexfuel.py are
 * unaffected: the whole annex is outside the checksum and carries no control
 * value.
 *
 * E1 (issue #34, 2026-09-17) took D1's two reserved CORE fields at +29 and
 * +2A for `dzw_e` and `fzw_q8`, and that is the whole RAM cost of the
 * ignition blend.  Nothing moved, the length is still 0x40 and the magic is
 * still "FF01", so `ff_persist_buf` and `ff_nvm_req` keep their addresses.
 * The core was the right half for them: `dzw_e` is a CONTROL value that the
 * segment-synchronous trampoline in src/hooks.S consumes, it is written only
 * by the periodic tick, and being inside the checksum means a corrupted
 * `dzw_e` makes the very next activation re-initialise the block instead of
 * leaving a stale offset in the ignition path.  The trampoline itself checks
 * only the magic, exactly as `ff_rk_scale` does -- what bounds it is the
 * `ff_dzw_max` clamp on the producing side, the FF_DZW_HARD_MAX clamp in
 * code, and the stock s8 clamp at 0x41D410 plus `zwmin` and the
 * -54..+58.5 degCA output clamp downstream (re/findings/ignition.md 8, 11).
 *
 * E2 (issue #35, 2026-09-17) is the first brief that had to GROW the struct:
 * the core was full at +0x2B and its two new values -- `fst_q10` and
 * `zwst_add` -- are CONTROL values that three segment-asynchronous stubs
 * consume, so by E1's argument they belong inside the checksum and not in the
 * annex.  Growing past the annex instead of inserting keeps every D1, D2 and
 * E1 offset exactly where it was, at the price of making the checksummed core
 * TWO ranges: +08..+2B (FF_CORE_OFF/FF_CORE_LEN) and +40..+43
 * (FF_CORE2_OFF/FF_CORE2_LEN).  `ff_core_csum()` sums both, in that order;
 * the annex +2C..+3F stays outside, unchanged and still written by other
 * paths.  The length grew 0x40 -> 0x44 and the magic is still "FF01", so a
 * block written by the D2/E1 blob is rejected by the length check and
 * re-initialised on the first activation -- which is the safe direction and
 * the same rule FFCAL001's strict version check follows.
 *
 * E5 (issue #36, 2026-09-17) GREW core 2 rather than adding a third range.
 * Its five fields are all written by the periodic tick and by nothing else --
 * `prail_add` is a CONTROL value that the 0x45845C stub consumes, and the four
 * diagnostics come out of the same `ff_rail_update()` that produces it -- so
 * extending FF_CORE2_LEN from 0x04 to 0x0C covers every one of them with the
 * checksum and leaves `ff_core_csum()` summing exactly two ranges.  The byte
 * E2 reserved at +0x43 is spent here, on `msv_sat_ticks`, which is what it was
 * reserved for.  The length grew 0x44 -> 0x4C: still a multiple of four, which
 * `ff_state_init()`'s word-wise clear needs, and still "FF01", so a block
 * written by an E2 blob is rejected by the length check and re-initialised on
 * the first activation.
 *
 * Why `prail_add` belongs in the core is E1's argument one step stronger: the
 * stub that reads it runs in a DIFFERENT ERCOSEK task from the one that writes
 * it (`hdrpsol_main` 0x45822C is called from set A's 20 ms task 0x45CAC4 id
 * 23, the producer from set A's 10 ms task 0x4328E4 id 19 --
 * re/findings/scheduler.md section 11.8), so nothing about the ordering can be
 * relied on and a corrupted `prail_add` has to make the block re-initialise
 * rather than sit in the rail-pressure path.  What bounds the value is the
 * `ff_prail_max` clamp on the producing side, FF_PRAIL_HARD_MAX in code, and
 * the stock `KLPRMAX` ceiling (22000 = 110.0 bar) and `PRSOLMN` floor that run
 * four instructions after the hooked store and re-read the cell from memory
 * (re/findings/rail.md section 12.1) -- three mechanisms, none of which the
 * patch can switch off.
 *
 * The EEP_CONF request record is NOT part of this block.  The block manager
 * keeps a pointer to it for milliseconds after the call returns
 * (re/findings/eeprom.md section 8.2), so it has to be stable storage, but it
 * is the manager's layout, not ours; it is a separate .bss object and the
 * linker puts it right after the state block -- at PATCH_RAM + 0x44 since E2
 * grew the struct, which is why `build.ram_symbols` pins it by NAME and
 * nothing quotes the number.
 *
 * `frame_bad` is a LATCH, not an event.  docs/05 section 3.2 lists "status in
 * {fault, not ready}" and "counter unchanged for 3 received frames" as
 * conditions, and a condition has to hold between frames too: the Pico sends
 * at 10 Hz and the raster runs at 100 Hz, so nine activations out of ten see
 * no frame at all.  Deciding FAULT only on the activation that carries the bad
 * frame would drop back to OK on the very next one.  The latch is set and
 * cleared only when a fresh frame is processed.
 *
 * Why the split: the checksum is recomputed at the end of every periodic
 * activation, so it may only cover fields that activation owns.  `rk_calls` is
 * written by the engine-synchronous segment task, which runs asynchronously to
 * the raster and must not pay for a checksum; D2's persistence fields are
 * written from a slower path.  A corrupt annex therefore does not trigger a
 * re-init, which is the right trade: the annex carries no control value.
 *
 * `patches/ff_counter` uses +0x00..+0x07 of the same block.  The two patches
 * are never flashed together (patches/ff_counter/README.md), and ff_counter's
 * `ff_alive` = 0xFC01 at +0x04 cannot be mistaken for our length 0x0040.
 * ---------------------------------------------------------------------------
 * Calibration: FFCAL001 at 0x5E2510 (docs/06_patch_pipeline.md section 3;
 * 0xFF up to 0x5FFFFF, inside checksum block 0x5E0000-0x5EFFFF).  Built by
 * patches/ff_fuel/ffcal001.py, which is the authority on the layout; the
 * offsets here must match it and `tests/test_ff_fuel_patch.py` asserts they do.
 */
#ifndef FF_STATE_H
#define FF_STATE_H

#ifndef __ASSEMBLER__
#include "../../common/types.h"
#endif

/*
 * Everything below that is a plain #define is visible to the ASSEMBLER too:
 * src/hooks.S includes this file so the hand-written ignition trampoline can
 * spell its RAM offsets with the same names the C uses instead of repeating
 * numbers.  Constants the assembler may see therefore carry NO `u` suffix.
 */

/* ------------------------------------------------------------ RAM state --- */
#define FF_MAGIC   0x46463031u        /* "FF01" */
#define FF_LENGTH  0x004Cu            /* E2 grew it 0x40 -> 0x44, E5 -> 0x4C */

/*
 * Byte offsets inside `struct ff_state` that src/hooks.S addresses directly.
 * src/ff_ign.c and src/ff_start.c assert each of them against the struct, so a
 * field that moves fails the build rather than the engine.
 */
#define FF_OFF_MAGIC    0x00
#define FF_OFF_DZW_E    0x29
#define FF_OFF_FST_Q10  0x40          /* E2, read by the two S1 stubs */
#define FF_OFF_ZWST_ADD 0x42          /* E2, read by the Z1 stub      */
#define FF_OFF_PRAIL_ADD 0x44         /* E5, read by the R1 stub      */

#define FF_MODE_INIT      0u
#define FF_MODE_OK        1u
#define FF_MODE_HOLD      2u
#define FF_MODE_FAULT     3u
#define FF_MODE_OFF       4u
#define FF_MODE_OVERRIDE  5u

/* The two periodic hook sources; see re/findings/scheduler.md section 8.1. */
#define FF_SRC_A          1u          /* 0x432940, task set A (0x4328E4)  */
#define FF_SRC_B          2u          /* 0x12067C, task set B (0x1205A0)  */
#define FF_OWNER_SWITCH   3u          /* foreign calls before ownership moves */

#define FF_E_FILT_MAX  1600u          /* 100 % in 1/16 %                  */
#define FF_F_MIN       1024u          /* 1.000, bit-identical to stock    */
#define FF_F_MAX       2048u          /* 2.000, the hard ceiling in code  */
#define FF_FRAC        1024u          /* sub-count resolution of e_frac   */

/*
 * E2 (#35).  These three are spelled by src/hooks.S as well as by the C, so
 * they carry NO `u` suffix and they live here, above the __ASSEMBLER__ guard,
 * rather than with the rest of the start-enrichment constants.  Every C use is
 * in a u32 or s32 context, where a positive int constant converts silently.
 */
#define FF_FST_ONE       1024         /* f_st = ff_fst_map[..] / 1024      */
/*
 * The ceiling the CODE applies whatever `ff_fst_max` says, the start-fuel
 * counterpart of FF_F_MAX and FF_DZW_HARD_MAX.  2560 = 2.50x is above any
 * sane calibration (E85 needs about 1.5x the mass and the stock map already
 * supplies the cold-start enrichment this multiplies on top of,
 * re/findings/start.md section 6), and it is chosen to be exactly the largest
 * factor the DIAGNOSTIC byte can carry: (2560 * 100) >> 10 = 250, which still
 * fits the u8 `B` of formula 0x21 with A = 100.  So there is no value this
 * patch can produce that measuring block 69 field 1 cannot show, and no
 * saturation to explain in the procedure.
 */
#define FF_FST_HARD_MAX  2560
/*
 * And the same for the start ADVANCE.  8 counts = 6.00 degCA is twice the
 * shipped `ff_zwst_max` (4 counts = 3.00 degCA) and twice the +2..+4 degCA
 * that re/findings/start.md section 5 calls the useful range.  It matters more
 * here than anywhere else in the patch because the knock retard is BYPASSED
 * during the start (start.md section 5): nothing downstream will take this
 * advance back.  src/hooks.S uses it as an unsigned RANGE CHECK on the byte it
 * reads, which is why the stub needs no lower clamp.
 */
#define FF_ZWST_HARD_MAX 8

/*
 * E5 (#36).  The ceiling the CODE puts on `prail_add`, whatever `ff_prail_max`
 * says -- the rail counterpart of FF_F_MAX, FF_FST_HARD_MAX and
 * FF_ZWST_HARD_MAX.  Spelled here rather than with the rest of the rail
 * constants because src/hooks.S may not see it... it does not, in fact: the R1
 * stub does NOT re-clamp (see src/hooks.S), so this is a C-only constant and
 * carries no `u` suffix only for consistency with its three neighbours.
 *
 * 6000 = 30.0 bar is twice the shipped `ff_prail_max` (3000 = 15.0 bar) and
 * twice the whole headroom the stock calibration has: `KFPRSOLHOM` tops out at
 * 19000 = 95 bar and `KLPRMAX` (0x5D5546) is 22000 = 110 bar, so 3000 is every
 * count the map can actually use and anything past 6000 is a calibration that
 * lies -- the KLPRMAX clamp four instructions after the hooked store eats it
 * (re/findings/rail.md sections 3.3 and 12.1).
 */
#define FF_PRAIL_HARD_MAX 6000

#ifndef __ASSEMBLER__

struct ff_state {
    volatile u32 magic;               /* +00 */
    volatile u16 length;              /* +04 */
    volatile u16 csum;                /* +06 */
    /* --- core, checksummed --------------------------------------------- */
    volatile u16 e_filt;              /* +08 */
    volatile u16 f_q10;               /* +0A */
    volatile u8  mode;                /* +0C */
    volatile u8  status;              /* +0D */
    volatile u8  e_raw;               /* +0E */
    volatile u8  t_fuel;              /* +0F */
    volatile u8  frame_ctr;           /* +10 */
    volatile u8  fw_ver;              /* +11 */
    volatile u8  cal_mode;            /* +12 */
    volatile u8  cal_ok;              /* +13 */
    volatile u16 age_ticks;           /* +14 */
    volatile u16 hold_ticks;          /* +16 */
    volatile u16 frames;              /* +18 */
    volatile u16 faults;              /* +1A */
    volatile u8  stall;               /* +1C */
    volatile u8  src_owner;           /* +1D */
    volatile u8  src_seen;            /* +1E */
    volatile u8  src_foreign;         /* +1F */
    volatile u32 ticks;               /* +20 */
    volatile u16 e_key;               /* +24 */
    volatile u16 e_frac;              /* +26 */
    volatile u8  frame_bad;           /* +28 */
    volatile s8  dzw_e;               /* +29  E1 */
    volatile u16 fzw_q8;              /* +2A  E1 */
    /* --- annex, not checksummed ---------------------------------------- */
    volatile u32 rk_calls;            /* +2C */
    volatile u16 e_persist;           /* +30  D2 */
    volatile u8  persist_state;       /* +32  D2 */
    volatile u8  persist_err;         /* +33  D2 */
    volatile u16 diag_e_pct;          /* +34  D2 */
    volatile u16 diag_f_pct;          /* +36  D2 */
    volatile u16 diag_t_degc;         /* +38  D2 */
    volatile u16 persist_wait;        /* +3A  D2 */
    volatile u16 persist_writes;      /* +3C  D2 */
    volatile u16 persist_fails;       /* +3E  D2 */
    /* --- core 2, checksummed (E2, grown by E5) -------------------------- */
    volatile u16 fst_q10;             /* +40  E2 */
    volatile s8  zwst_add;            /* +42  E2 */
    volatile u8  msv_sat_ticks;       /* +43  E5, was E2's st_reserved */
    volatile u16 prail_add;           /* +44  E5 */
    volatile s16 win_margin_min;      /* +46  E5 */
    volatile u16 prist_min;           /* +48  E5 */
    volatile u16 diag_ticks;          /* +4A  E5 */
};

#define FF_CORE_OFF   0x08u           /* first checksummed byte */
#define FF_CORE_LEN   0x24u           /* +08 .. +2B inclusive   */
#define FF_CORE2_OFF  0x40u           /* E2: the second checksummed range */
#define FF_CORE2_LEN  0x0Cu           /* +40 .. +4B inclusive (E5 grew it) */

extern struct ff_state ff_state;

/* ------------------------------------------- D2: E% persistence (#38) --- */
/*
 * `persist_state`, and what each value means for the next activation.
 */
#define FF_P_IDLE    0u               /* nothing in flight; may start a commit */
#define FF_P_STAGED  1u               /* mirror written, commit not yet queued */
#define FF_P_BUSY    2u               /* queued; poll ff_nvm_req.status        */
#define FF_P_DONE    3u               /* the last commit finished OK           */
#define FF_P_ERR     4u               /* the last commit failed; persist_err   */

/*
 * The EEP_CONF block manager's request record (re/findings/eeprom.md 8.2).
 * `nvm_block_request(blk, off, len, mode, buf, &req)` fills it in and puts a
 * POINTER to it in the manager's 4-slot queue, so it must outlive the call -
 * a stack temporary would be dereferenced after the frame is gone.
 */
struct ff_nvm_req {
    volatile u32 buf;                 /* +0  the caller's buffer, 0 on commit */
    volatile u8  blk;                 /* +4 */
    volatile u8  off;                 /* +5 */
    volatile u8  len;                 /* +6 */
    volatile u8  shape;               /* +7  the call-shape case, 3 = commit  */
    volatile u8  status;              /* +8  1 queued, 2 done, else failed    */
    volatile u8  pad[3];              /* keep the next object aligned         */
};

extern struct ff_nvm_req ff_nvm_req;

/* The one-byte staging buffer the stage shape copies out of; global so the
 * linker records it and `build.ram_symbols` can pin its address. */
extern volatile u8 ff_persist_buf;

#define FF_NVM_WRITE     0u           /* `mode` for stage (mirror <- buf)     */
#define FF_NVM_READ      1u           /* `mode` for read back (buf <- mirror) */
#define FF_NVM_RC_SYNC   2u           /* a synchronous shape did its work     */
#define FF_NVM_RC_QUEUED 1u           /* the commit was accepted              */
#define FF_NVM_ST_BUSY   1u           /* record.status while in flight        */
#define FF_NVM_ST_OK     2u           /* record.status when it finished       */

#define FF_E_PCT_MAX     100u         /* a stored byte above this is garbage  */
#define FF_E_PCT_NONE    0xFFu        /* an erased EEPROM byte                */

/* src/ff_diag.c; all three read the calibration themselves except for the
 * raster period, which ff_cal_load() has already clamped. */
void ff_persist_init(void);
void ff_persist_tick(u16 tick_ms);
void ff_diag_publish(void);

/* --------------------------------------- D2: the measuring block (#39) --- */
/*
 * The four TKMWL ids and the group are FACTS ABOUT THE IMAGE, not choices the
 * code makes: the handler pointers live in tbl_measuring_vars (0xA5658) and
 * the group words in tbl_measuring_groups (0x5C5518), both written by
 * patch.json.  They are recorded here so the C, patch.json, the tests and
 * logging/sessions/ff_fuel.json quote one number each.
 * Evidence: re/findings/measuring_vars.md section 8.
 */
#define FF_MW_ID_E       2196u        /* field 1, formula 0x21, A = 100       */
#define FF_MW_ID_F       2197u        /* field 2, formula 0x21, A = 100       */
#define FF_MW_ID_T       2198u        /* field 3, formula 0x05, A = 10        */
#define FF_MW_ID_MODE    2199u        /* field 4, formula 0x36 (a count)      */
#define FF_MW_GROUP      111u         /* 0x6F; its 0x7F echo (238) is empty   */

#define FF_FMT_PCT       0x21u        /* 100 * B / A                          */
#define FF_FMT_DEGC      0x05u        /* 0.1 * A * (B - 100)                  */
#define FF_FMT_COUNT     0x36u        /* (A << 8) | B                         */
#define FF_FMT_NONE      0x25u        /* with A = B = 0: "not available"      */
#define FF_FMT_A_PCT     100u         /* A for 0x21, so the value IS B in %   */
#define FF_FMT_A_DEGC    10u          /* A for 0x05, so the value IS B - 100  */
#define FF_DEGC_BIAS     100u         /* B of formula 0x05 at 0 degC          */
#define FF_T_FUEL_BIAS   40u          /* the frame byte is degC + 40          */

/* ------------------------------------------------------ FFCAL001 layout --- */
#define FF_CAL_BASE        0x005E2510u
#define FF_CAL_VERSION     4u         /* E5 appended the rail adder         */
#define FF_CAL_LENGTH      0x014Cu    /* what ffcal001.py emits today       */

#define FF_CAL_MAGIC0      (FF_CAL_BASE + 0x00u)   /* "FFCA" */
#define FF_CAL_MAGIC1      (FF_CAL_BASE + 0x04u)   /* "L001" */
#define FF_CAL_O_VERSION   0x08u      /* u16 */
#define FF_CAL_O_LENGTH    0x0Au      /* u16 */
#define FF_CAL_O_CAN_ID    0x0Cu      /* u16, documentation + a sanity check */
#define FF_CAL_O_TIMEOUT   0x0Eu      /* u16, ms  */
#define FF_CAL_O_HOLD_S    0x10u      /* u16, s   */
#define FF_CAL_O_TAU_MS    0x12u      /* u16, ms  (first-order time constant) */
#define FF_CAL_O_SLEW      0x14u      /* u16, %/s */
#define FF_CAL_O_TICK_MS   0x16u      /* u16, ms  (period of the periodic hook) */
#define FF_CAL_O_MODE      0x18u      /* u8, 0 off / 1 normal / 2 bench override */
#define FF_CAL_O_E_OVR     0x19u      /* u8, ethanol % used in mode 2 */
#define FF_CAL_O_STALL_MAX 0x1Au      /* u8  */
#define FF_CAL_O_PERSIST   0x1Bu      /* u8, D2: 1 = persist E% in EEPROM */
#define FF_CAL_O_P_HYST    0x1Cu      /* u8, D2: minimum E% change to commit */
#define FF_CAL_O_P_BLOCK   0x1Du      /* u8, D2: EEP_CONF block (8) */
#define FF_CAL_O_P_OFFSET  0x1Eu      /* u8, D2: payload offset in that block */
#define FF_CAL_O_P_RATE_S  0x1Fu      /* u8, D2: minimum seconds between commits */
#define FF_CAL_O_F_CURVE   0x20u      /* 17 x u16, Q10, E 0..100 step 6.25 % */
#define FF_CAL_O_FZW_CURVE 0x42u      /* 17 x u8,  1/256, E1: the blend factor */
#define FF_CAL_O_DZW_MAP   0x54u      /* 8 x 8 s8, 0.75 degCA, E1 (all 0)    */
#define FF_CAL_O_FST_MAP   0x94u      /* 6 x 6 u16, Q10, reserved (1024)     */
#define FF_CAL_O_PRAIL_ADD 0xDCu      /* 8 x u8, 0.1 MPa, reserved (0)       */
/* --- appended by E1 (issue #34); v1 ended at 0xE6 with the checksum ------ */
#define FF_CAL_O_ZW_ENABLE 0xE6u      /* u8, 0 = the blend is never applied  */
#define FF_CAL_O_DZW_MAX   0xE7u      /* u8, |dzw_e| ceiling in s8 counts    */
#define FF_CAL_O_DZW_NMOT  0xE8u      /* 8 x u16, nmot_w breakpoints (rows)  */
#define FF_CAL_O_DZW_RL    0xF8u      /* 8 x u16, rl_w breakpoints (columns) */
/* --- appended by E2 (issue #35); v2 ended at 0x108 with the checksum ----- */
#define FF_CAL_O_ST_ENABLE   0x108u   /* u8, 0 = f_st is permanently 1.000   */
#define FF_CAL_O_ZWST_ENABLE 0x109u   /* u8, 0 = zwst_add is permanently 0   */
#define FF_CAL_O_FST_MAX     0x10Au   /* u16, Q10 ceiling of fst_q10         */
#define FF_CAL_O_ZWST_MAX    0x10Cu   /* u8, zwst_add ceiling in s8 counts   */
#define FF_CAL_O_ZWST_TMAX   0x10Du   /* u8, tmst COUNT at/above which the   */
                                      /*     start advance is 0              */
#define FF_CAL_O_FST_E_AXIS  0x10Eu   /* 6 x u8, ethanol % (map rows)        */
#define FF_CAL_O_FST_T_AXIS  0x114u   /* 6 x u8, tmst counts (map columns)   */
#define FF_CAL_O_FZWST_CURVE 0x11Au   /* 6 x s8, counts, on the E axis above */
/* --- appended by E5 (issue #36); v3 ended at 0x120 with the checksum ----- */
#define FF_CAL_O_PRAIL_ENABLE 0x122u  /* u8, 0 = prail_add is permanently 0  */
#define FF_CAL_O_PRAIL_RSV    0x123u  /* u8, 0; keeps the two u16 below even */
#define FF_CAL_O_PRAIL_MAX    0x124u  /* u16, prail_add ceiling, 0.005 bar   */
#define FF_CAL_O_DIAG_WIN_MS  0x126u  /* u16, ms of the diagnostic window    */
#define FF_CAL_O_PRAIL_CURVE  0x128u  /* 17 x u16, 0.005 bar, on the E grid  */
#define FF_CAL_O_CRC         0x14Au   /* u16 at length-2 */

#define FF_CURVE_N     17u
#define FF_CURVE_STEP  100u           /* 6.25 % in 1/16 % units */

/* ---------------------------------------- E1: the ignition blend (#34) --- */
#define FF_DZW_N         8u           /* ff_dzw_map is 8 rows x 8 columns   */
#define FF_FZW_MAX       255u         /* the u8 ceiling of ff_fzw_curve     */
#define FF_FZW_ONE       256u         /* f_zw = ff_fzw_curve[..] / FF_FZW_ONE */
/*
 * The ceiling the CODE applies whatever the calibration says, the ignition
 * counterpart of FF_F_MAX.  16 counts = 12.00 degCA is above the widest
 * KFZWOP - KFZW gap in this dataset (~9 degCA at 4500 rpm / 90 %,
 * re/findings/ignition.md 12), so it never binds a sane calibration, and it
 * bounds a corrupt one to well inside the s8 range the stock clamp allows.
 */
#define FF_DZW_HARD_MAX  16

/* src/ff_ign.c; called from ff_finish() at the end of every activation. */
void ff_zw_update(void);

/* --------------------------------- E1: the measuring block 108 (#34) ----- */
/*
 * Facts about the image, not choices the code makes: the handler pointers go
 * into tbl_measuring_vars (0xA5658) ids 2192-2195 and the group words into
 * tbl_measuring_groups (0x5C5518) group 108, both written by patch.json.
 * Evidence: re/findings/measuring_vars.md section 8.4.
 */
#define FF_MW_ID_FZW     2192u        /* field 1, formula 0x21, A = 100      */
#define FF_MW_ID_DZW     2193u        /* field 2, formula 0x22, A = 0x4B     */
#define FF_MW_ID_DWKRZ   2194u        /* field 3, formula 0x22, A = 0x4B     */
#define FF_MW_ID_ZWLATCH 2195u        /* field 4, formula 0x36 (a count)     */
#define FF_MW_GROUP_ZW   108u         /* 0x6C; its 0x7F echo 235 is empty    */

/*
 * Formula 0x22 is `0.01 * A * (B - 128)`.  With A = 0x4B (75) that is
 * 0.75 degCA per count -- and it is not a guess: the six stock knock-retard
 * handlers at 0x039CD0-0x039D48 emit exactly (0x22, 0x4B, byte + 0x80) for
 * the same array this block's field 3 reports (re/findings/ignition.md 6).
 */
#define FF_FMT_ZW        0x22u
#define FF_FMT_A_ZW      0x4Bu        /* 75 -> 0.75 degCA per count          */
#define FF_ZW_BIAS       128u         /* B of formula 0x22 at 0 degCA        */
#define FF_ZW_LATCH_MASK 3u           /* bits 0/1 of 0x7FD31B                */

/* -------------------------------- E2: the start enrichment (#35) --------- */
#define FF_FST_N         6u           /* ff_fst_map is 6 E rows x 6 tmst cols */

/* src/ff_start.c; called from ff_finish() at the end of every activation. */
void ff_start_update(void);

/* ---------------------------------- E2: the measuring block 69 (#35) ----- */
/*
 * Facts about the image, not choices the code makes: the handler pointers go
 * into tbl_measuring_vars (0xA5658) ids 2188-2191 and the group words into
 * tbl_measuring_groups (0x5C5518) group 69, both written by patch.json.
 * Evidence: re/findings/measuring_vars.md section 8.5.
 */
#define FF_MW_ID_FST     2188u        /* field 1, formula 0x21, A = 100      */
#define FF_MW_ID_ZWST    2189u        /* field 2, formula 0x22, A = 0x4B     */
#define FF_MW_ID_TMST    2190u        /* field 3, formula 0x05, A = 10       */
#define FF_MW_ID_KSTA    2191u        /* field 4, formula 0x36 (a raw count) */
#define FF_MW_GROUP_ST   69u          /* 0x45; its 0x7F echo 196 is empty    */

/*
 * `tmst` is a u8 count of 0.75 degC with a -48 degC offset (start.md 2).
 * Formula 0x05 is `0.1 * A * (B - 100)`, so with A = 10 the reported number
 * is `B - 100` whole degrees C and the handler does the conversion, rounding
 * to nearest: degC = (count * 3 + 2) / 4 - 48.
 */
#define FF_TMST_BIAS     48           /* degC at count 0 is -48              */

/* --------------------------------- E5: the rail adder (#36) -------------- */
/*
 * `ff_prail_curve` is 17 points on the SAME ethanol grid as `ff_F_curve` and
 * `ff_fzw_curve` (one breakpoint every 6.25 % = FF_CURVE_STEP counts of
 * 1/16 %), so the three read with one mental model and one interpolator shape.
 */
#define FF_PRAIL_N       FF_CURVE_N

/*
 * The injection-window margin of re/findings/rail.md section 11:
 *
 *     margin = wbho1s (0x80307E, s16) - dwi (0x803088, u16)
 *              - dwbho1smn_w (0x7FD290, u8) * FF_ANGLE_MAP_SCALE
 *
 * all in angle LSB of 3/128 degCA.  The subtrahend is NOT the literal 2144 the
 * brief quotes: 2144 is 67 * 32, and 67 is what `awea_angles` happens to write
 * into 0x7FD290 from the flat `KLWBHO1SMX` (0x5D3BE8) in THIS dataset.  Reading
 * the RAM cell instead follows both a re-calibrated curve and the runtime
 * value, exactly as emu/models/window.py does rather than hard-coding it.
 * FF_ANGLE_MAP_SCALE is the `* 0x20` every %AWEA u8 angle map carries -- 32
 * angle LSB = 0.75 degCA per map count (rail.md sections 8 and 14.1).
 */
#define FF_ANGLE_MAP_SCALE  32

/*
 * Formula 0x22 is `0.01 * A * (B - 128)`.  Groups 108 and 69 use A = 0x4B (75)
 * = 0.75 degCA per count, copied from the stock knock handlers -- but that
 * spans only -96.00 .. +95.25 degCA, and the window margin reaches about
 * +280 degCA at a light-load 2000 rpm point (rail.md section 15), so it would
 * sit pinned at its maximum nearly all the time.  Group 109 field 3 uses the
 * same formula with **A = 225**, and 225 is not a round number by accident:
 * 0.01 * 225 = 2.25 degCA is exactly FF_WIN_COUNT_LSB = 96 angle LSB of
 * 3/128 degCA, so the handler converts with one exact integer division and no
 * accumulated rounding.  The span is -288.00 .. +285.75 degCA, which covers
 * the whole range `KFWBHO1SW` (210..330 degCA) can produce.  The resolution is
 * coarse on purpose: the decision the field exists for is "does the margin
 * approach zero", and 2.25 degCA is 0.3 % of a cycle.  The division FLOORS
 * (towards minus infinity, not C's truncate-towards-zero), so the number a
 * tester reads never overstates the margin at either end.
 */
#define FF_FMT_A_WIN     225u         /* 0.01 * 225 = 2.25 degCA per count   */
#define FF_WIN_COUNT_LSB 96           /* 2.25 degCA in angle LSB: 96 * 3/128 */

/* src/ff_rail.c; called from ff_finish() at the end of every activation. */
void ff_rail_update(void);

/* ---------------------------------- E5: the measuring block 109 (#36) ---- */
/*
 * Facts about the image, not choices the code makes: the handler pointers go
 * into tbl_measuring_vars (0xA5658) ids 2184-2187 and the group words into
 * tbl_measuring_groups (0x5C5518) group 109, both written by patch.json.
 * Evidence: re/findings/measuring_vars.md section 8.6.
 */
#define FF_MW_ID_PRAIL   2184u        /* field 1, formula 0x53               */
#define FF_MW_ID_PRIST   2185u        /* field 2, formula 0x53               */
#define FF_MW_ID_WINMRG  2186u        /* field 3, formula 0x22, A = 225      */
#define FF_MW_ID_MSVSAT  2187u        /* field 4, formula 0x36 (a count)     */
#define FF_MW_GROUP_PR   109u         /* 0x6D; its 0x7F echo (236) is empty  */

/*
 * Formula 0x53 is `((A << 8) | B) * 0.01` bar, CROSS-CHECKED in
 * re/findings/measuring_vars.md section 7.3 against the stock `prist` and
 * `prsoll` handlers at 0x3DB94/0x3DBAC -- which emit the raw 0.005 bar word
 * SHIFTED RIGHT BY ONE.  Group 109 fields 1 and 2 do exactly the same, so a
 * tester reads bar with two decimals and the arithmetic is the stock one.
 */
#define FF_FMT_BAR       0x53u

#endif /* __ASSEMBLER__ */
#endif /* FF_STATE_H */
