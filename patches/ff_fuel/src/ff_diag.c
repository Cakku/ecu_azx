/*
 * ff_diag - the measuring block (#39) and the E% store (#38), brief D2.
 *
 * Two independent things that share one property: neither may ever affect what
 * the engine does.  Nothing in this file writes `rk`, `f_q10`, `mode` or any
 * other control value, and nothing in it can block - the one stock function it
 * calls that is not a memcpy (`nvm_block_request`) returns immediately and
 * finishes the work in the block manager's own queue.
 *
 *   1. FOUR MEASURING-VARIABLE HANDLERS.  `tbl_measuring_vars` (0xA5658) is
 *      2200 u32 handler pointers; 1535 of them point at the "not available"
 *      stub 0x38EC4.  patch.json points ids 2196-2199 at the four functions
 *      below and writes their ids into `tbl_measuring_groups` (0x5C5518) as
 *      the four fields of group 111, so `21 6F` over KWP - VCDS measuring
 *      block 111 - answers with our state.  Evidence for every id, group and
 *      formula: re/findings/measuring_vars.md section 8.
 *
 *      A handler is entered by `blrl` out of `measuring_var_dispatch`
 *      (0x45768) with the variable id in r3 and the return address in LR, and
 *      is expected to hand a `(formula, A, B)` triple to
 *      `measuring_result_emit` (0x38EB4) and return.  An ordinary non-leaf C
 *      function does exactly that: it saves LR, calls, restores, `blr`.  No
 *      trampoline is needed and none is used.
 *
 *   2. E% ACROSS POWER LOSS.  `ff_state.e_filt` is restored from EEP_CONF
 *      block 8 at cold start and written back when it has moved by more than
 *      `ff_persist_hyst_pct`, at most once every `ff_persist_rate_s`.  The
 *      only route to the device is `nvm_block_request` (0x6131C): stage into
 *      the RAM mirror, then queue a commit that the block manager checksums
 *      and writes to both copies.  The raw SPI primitives are never touched -
 *      they would race the mirror and would have to redo the checksum.
 *      Evidence: re/findings/eeprom.md sections 3, 5 and 8.
 *
 *      External-SRAM retention is NOT an alternative: brief C2 proved
 *      `ram_clear_block` (0x6D8F8) zeroes 0x800004-0x80498F at every cold
 *      start (eeprom.md section 6, ram.md section 3).  EEPROM is the only
 *      route.
 */
#include "../../common/types.h"
#include "../../common/med9_stock.h"
#include "ff_state.h"

/*
 * The block manager keeps a POINTER to this record in its 4-slot queue and
 * dereferences it milliseconds after the call returns (eeprom.md 8.2), so it
 * cannot be a stack temporary.  It is a separate .bss object, not part of
 * `struct ff_state`: the layout is the manager's, the state block's layout is
 * a contract with the logger and the tests.
 */
struct ff_nvm_req ff_nvm_req;

/*
 * What a stage copies from.  The stage shape is synchronous - the manager
 * memcpy()s out of this buffer before it returns - so a local would do, but a
 * static one is one more thing a RAM snapshot can show and costs nothing.
 */
volatile u8 ff_persist_buf;

void ff_diag_e_pct(void);
void ff_diag_f_pct(void);
void ff_diag_t_degc(void);
void ff_diag_mode(void);

/* ------------------------------------------------------ 1. the handlers -- */
static void ff_emit(u32 formula, u32 a, u32 b)
{
    MED9_FN(void (*)(u32, u32, u32), MED9_MEASURING_RESULT_EMIT)(formula, a, b);
}

/*
 * A handler may run at any time, in the KWP task, while the 10 ms tick is
 * halfway through an activation.  It therefore checks the header only: the
 * checksum covers the core and is recomputed at the END of an activation, so a
 * handler that insisted on it would report "not available" for the duration of
 * every tick.  Each field it reads is a single aligned u8/u16 load, which
 * cannot tear.
 */
static u8 ff_diag_ready(void)
{
    return (ff_state.magic == FF_MAGIC
            && ff_state.length == (u16)FF_LENGTH) ? 1u : 0u;
}

static u32 ff_byte(u32 v)
{
    return (v > 0xFFu) ? 0xFFu : v;
}

/* id 2196, group 111 field 1: filtered ethanol, 0..100 %. */
void ff_diag_e_pct(void)
{
    if (!ff_diag_ready()) {
        ff_emit(FF_FMT_NONE, 0u, 0u);
        return;
    }
    ff_emit(FF_FMT_PCT, FF_FMT_A_PCT, ff_byte((u32)ff_state.diag_e_pct));
}

/* id 2197, group 111 field 2: the fuel factor F, 100..200 % (1.00..2.00). */
void ff_diag_f_pct(void)
{
    if (!ff_diag_ready()) {
        ff_emit(FF_FMT_NONE, 0u, 0u);
        return;
    }
    ff_emit(FF_FMT_PCT, FF_FMT_A_PCT, ff_byte((u32)ff_state.diag_f_pct));
}

/*
 * id 2198, group 111 field 3: fuel temperature in degC.
 *
 * Formula 0x05 is `0.1 * A * (B - 100)` (CROSS-CHECKED against tmot,
 * measuring_vars.md 7.1), so with A = 10 the reading is `B - 100` degC and the
 * frame's `degC + 40` byte becomes `B = byte + 60`.  Before the first frame
 * `status` is 0xFF and there is no temperature to report, which is exactly
 * what the "not available" triple is for - better than displaying -40 degC.
 */
void ff_diag_t_degc(void)
{
    if (!ff_diag_ready() || ff_state.status == 0xFFu) {
        ff_emit(FF_FMT_NONE, 0u, 0u);
        return;
    }
    ff_emit(FF_FMT_DEGC, FF_FMT_A_DEGC,
            ff_byte((u32)ff_state.diag_t_degc + FF_DEGC_BIAS - FF_T_FUEL_BIAS));
}

/*
 * id 2199, group 111 field 4: `256 * persist_state + mode`.
 *
 * Formula 0x36 displays `(A << 8) | B` as a plain count, so one field carries
 * both state machines: B is `ff_mode` (0 INIT 1 OK 2 HOLD 3 FAULT 4 OFF
 * 5 OVERRIDE) and A is `persist_state` (0 idle 1 staged 2 committing 3 done
 * 4 error).  A reading of 769 is "the last EEPROM commit succeeded and the
 * ethanol estimate is good".
 */
void ff_diag_mode(void)
{
    if (!ff_diag_ready()) {
        ff_emit(FF_FMT_NONE, 0u, 0u);
        return;
    }
    ff_emit(FF_FMT_COUNT, ff_byte((u32)ff_state.persist_state),
            ff_byte((u32)ff_state.mode));
}

/* Refresh what the handlers read; called at the end of every activation. */
void ff_diag_publish(void)
{
    u32 e = ((u32)ff_state.e_filt + 8u) / 16u;      /* 1/16 % -> %, rounded */

    if (e > FF_E_PCT_MAX)
        e = FF_E_PCT_MAX;
    ff_state.diag_e_pct = (u16)e;
    ff_state.diag_f_pct = (u16)(((u32)ff_state.f_q10 * 100u) >> 10);
    ff_state.diag_t_degc = (u16)ff_state.t_fuel;
}

/* ---------------------------------------------------- 2. the E% store --- */
static u32 ff_nvm(u32 blk, u32 off, u32 len, u32 mode, u32 buf, u32 req)
{
    return MED9_FN(u32 (*)(u32, u32, u32, u32, u32, u32),
                   MED9_NVM_BLOCK_REQUEST)(blk, off, len, mode, buf, req);
}

static u16 ff_sat_inc(u16 v)
{
    return (v < 0xFFFFu) ? (u16)(v + 1u) : v;
}

/* Activations in `ff_persist_rate_s`, with every operand clamped. */
static u16 ff_rate_ticks(u16 tick_ms)
{
    u32 t = ((u32)MED9_U8(FF_CAL_BASE + FF_CAL_O_P_RATE_S) * 1000u)
            / (u32)(tick_ms ? tick_ms : 1u);

    return (t > 0xFFFFu) ? 0xFFFFu : (u16)t;
}

/*
 * Cold start: read the stored byte back out of the block manager's RAM mirror
 * and seed both `e_filt` and `e_key` with it.
 *
 * Seeding `e_filt`, not only the FAULT decay target, is the whole point of
 * issue #38.  After a key cycle the state block is gone and `mode` is FAULT
 * with `hold_ticks` 0, so the first activation decays `e_filt` towards `e_key`
 * - starting from 0 that would mean running the E0 fuel factor on an E85 tank
 * for the ~50 s the 2 %/s slew limit needs, i.e. lean, which is the dangerous
 * direction.  Starting from the stored value errs rich if the tank was
 * refilled while the car was off, and the sensor corrects it at the same
 * 2 %/s.
 *
 * `mode 1` in the call is FF_NVM_READ.  With mode 0 the identical argument
 * list is the STAGE shape and would overwrite the mirror with whatever was in
 * our buffer (eeprom.md 8.1) - the one bit that matters most in this file.
 */
void ff_persist_init(void)
{
    u32 rc;
    u16 tick;

    ff_state.persist_state = (u8)FF_P_IDLE;
    ff_state.persist_err = 0u;
    ff_state.e_persist = 0xFFFFu;                   /* nothing known yet */
    ff_state.persist_wait = 0u;

    if (!ff_state.cal_ok || ff_state.cal_mode != 1u
        || MED9_U8(FF_CAL_BASE + FF_CAL_O_PERSIST) == 0u)
        return;

    /* Arm the rate limit at power-up too: the first minute is when the filter
     * is still settling and when a key-on/key-off cycle would otherwise write
     * the EEPROM every time. */
    tick = MED9_U16(FF_CAL_BASE + FF_CAL_O_TICK_MS);
    if (tick == 0u)
        tick = 1u;
    else if (tick > 1000u)
        tick = 1000u;
    ff_state.persist_wait = ff_rate_ticks(tick);

    ff_persist_buf = (u8)FF_E_PCT_NONE;
    rc = ff_nvm((u32)MED9_U8(FF_CAL_BASE + FF_CAL_O_P_BLOCK),
                (u32)MED9_U8(FF_CAL_BASE + FF_CAL_O_P_OFFSET),
                1u, FF_NVM_READ, (u32)&ff_persist_buf, 0u);
    ff_state.persist_err = (u8)rc;
    if (rc != FF_NVM_RC_SYNC) {
        ff_state.persist_state = (u8)FF_P_ERR;
        return;
    }
    if (ff_persist_buf > (u8)FF_E_PCT_MAX)          /* 0xFF = never written */
        return;

    ff_state.e_persist = (u16)ff_persist_buf;
    ff_state.e_key = (u16)((u32)ff_persist_buf * 16u);
    ff_state.e_filt = ff_state.e_key;
}

/*
 * One activation of the store.  Four things keep the EEPROM safe:
 *
 *   - only OK and HOLD write.  A FAULT estimate is a decayed guess and must
 *     not overwrite the last value the sensor actually reported, and mode 2
 *     (bench override) never reaches here at all.
 *   - the hysteresis `ff_persist_hyst_pct` (5 %), clamped to at least 1 %.
 *   - the rate limit `ff_persist_rate_s` (60 s), armed at power-up as well as
 *     after every attempt, so a car that is started and stopped cannot write
 *     more than once a minute.
 *   - one request at a time: while `persist_state` is BUSY the record belongs
 *     to the manager and nothing here touches it.
 *
 * Worst case with the shipped calibration is one page write a minute; the
 * M95160's endurance is 1e6 cycles per page, so ~16,000 engine hours.
 */
void ff_persist_tick(u16 tick_ms)
{
    u32 blk, off, hyst, rc;
    u16 e_pct;
    s32 d;
    u8 st;

    if (!ff_state.cal_ok || MED9_U8(FF_CAL_BASE + FF_CAL_O_PERSIST) == 0u)
        return;

    if (ff_state.persist_state == (u8)FF_P_BUSY) {
        st = ff_nvm_req.status;
        if (st == (u8)FF_NVM_ST_BUSY)
            return;                                  /* still in the queue */
        ff_state.persist_err = st;
        if (st == (u8)FF_NVM_ST_OK) {
            ff_state.persist_state = (u8)FF_P_DONE;
            ff_state.persist_writes = ff_sat_inc(ff_state.persist_writes);
        } else {
            ff_state.persist_state = (u8)FF_P_ERR;
            ff_state.persist_fails = ff_sat_inc(ff_state.persist_fails);
        }
        return;
    }

    if (ff_state.persist_wait != 0u) {
        ff_state.persist_wait = (u16)(ff_state.persist_wait - 1u);
        return;
    }
    if (ff_state.mode != (u8)FF_MODE_OK && ff_state.mode != (u8)FF_MODE_HOLD)
        return;

    e_pct = ff_state.diag_e_pct;
    if (e_pct > (u16)FF_E_PCT_MAX)
        e_pct = (u16)FF_E_PCT_MAX;
    hyst = MED9_U8(FF_CAL_BASE + FF_CAL_O_P_HYST);
    if (hyst == 0u)
        hyst = 1u;
    d = (s32)(u32)e_pct - (s32)(u32)ff_state.e_persist;   /* 0xFFFF = unknown */
    if (d < 0)
        d = -d;
    if (d < (s32)hyst)
        return;

    /* Arm the rate limit before the attempt, so a failing store cannot retry
     * every 10 ms. */
    ff_state.persist_wait = ff_rate_ticks(tick_ms);

    blk = MED9_U8(FF_CAL_BASE + FF_CAL_O_P_BLOCK);
    off = MED9_U8(FF_CAL_BASE + FF_CAL_O_P_OFFSET);
    ff_persist_buf = (u8)e_pct;
    rc = ff_nvm(blk, off, 1u, FF_NVM_WRITE, (u32)&ff_persist_buf, 0u);
    ff_state.persist_err = (u8)rc;
    if (rc != FF_NVM_RC_SYNC) {
        ff_state.persist_state = (u8)FF_P_ERR;
        ff_state.persist_fails = ff_sat_inc(ff_state.persist_fails);
        return;
    }
    ff_state.persist_state = (u8)FF_P_STAGED;

    /* Commit: len 0, buf 0, a record - the manager regenerates the block
     * checksum and writes both copies of block 8 (eeprom.md 3.1). */
    rc = ff_nvm(blk, 0u, 0u, FF_NVM_WRITE, 0u, (u32)&ff_nvm_req);
    ff_state.persist_err = (u8)rc;
    if (rc != FF_NVM_RC_QUEUED) {
        ff_state.persist_state = (u8)FF_P_ERR;
        ff_state.persist_fails = ff_sat_inc(ff_state.persist_fails);
        return;
    }
    ff_state.persist_state = (u8)FF_P_BUSY;
    ff_state.e_persist = e_pct;
}
