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
 *   +29   1    reserved_core0   tick      0
 *   +2A   2    reserved_core1   tick      0
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
 *   --- 0x40 -----------------------------------------------------------------
 *
 * D2 (issue #38/#39, 2026-09-16) took the three reserved words at +3A..+3F for
 * the rate-limit counter and two saturating counters.  No offset D1 defined
 * moved, the length is still 0x40 and the magic is still "FF01", so D1's
 * tests, logging/sessions/ff_fuel.json and emu/models/flexfuel.py are
 * unaffected: the whole annex is outside the checksum and carries no control
 * value.
 *
 * The EEP_CONF request record is NOT part of this block.  The block manager
 * keeps a pointer to it for milliseconds after the call returns
 * (re/findings/eeprom.md section 8.2), so it has to be stable storage, but it
 * is the manager's layout, not ours; it is a separate .bss object and the
 * linker puts it right after the state block at PATCH_RAM + 0x40.
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

#include "../../common/types.h"

/* ------------------------------------------------------------ RAM state --- */
#define FF_MAGIC   0x46463031u        /* "FF01" */
#define FF_LENGTH  0x0040u

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
    volatile u8  reserved_core0;      /* +29 */
    volatile u16 reserved_core1;      /* +2A */
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
};

#define FF_CORE_OFF  0x08u            /* first checksummed byte */
#define FF_CORE_LEN  0x24u            /* +08 .. +2B inclusive   */

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
#define FF_CAL_VERSION     1u
#define FF_CAL_LENGTH      0x00E8u    /* what ffcal001.py emits today       */

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
#define FF_CAL_O_FZW_CURVE 0x42u      /* 17 x u8,  1/256, reserved (0)       */
#define FF_CAL_O_DZW_MAP   0x54u      /* 8 x 8 s8, 0.75 degCA, reserved (0)  */
#define FF_CAL_O_FST_MAP   0x94u      /* 6 x 6 u16, Q10, reserved (1024)     */
#define FF_CAL_O_PRAIL_ADD 0xDCu      /* 8 x u8, 0.1 MPa, reserved (0)       */
#define FF_CAL_O_CRC       0xE6u      /* u16 at length-2 */

#define FF_CURVE_N     17u
#define FF_CURVE_STEP  100u           /* 6.25 % in 1/16 % units */

#endif /* FF_STATE_H */
