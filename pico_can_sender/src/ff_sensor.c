/*
 * ff_sensor.c - implementation of the flex-fuel sensor measurement/state logic.
 * See ff_sensor.h for the contract and docs/05_flexfuel_design.md section 2 for
 * the sensor characteristic this encodes.
 *
 * Integer arithmetic only, so the host tests and the RP2350 firmware agree bit
 * for bit.
 */

#include "ff_sensor.h"

#include <string.h>

/* ------------------------------------------------------------------ helpers */

/* Round-half-away-from-zero division; den must be > 0. */
static int32_t div_round_i(int32_t num, int32_t den)
{
    if (num >= 0) {
        return (num + den / 2) / den;
    }
    return -(((-num) + den / 2) / den);
}

static int64_t div_round_i64(int64_t num, int64_t den)
{
    if (num >= 0) {
        return (num + den / 2) / den;
    }
    return -(((-num) + den / 2) / den);
}

static uint32_t div_round_u(uint64_t num, uint64_t den)
{
    if (den == 0u) {
        return 0u;
    }
    return (uint32_t)((num + den / 2u) / den);
}

static int32_t clamp_i(int32_t v, int32_t lo, int32_t hi)
{
    if (v < lo) {
        return lo;
    }
    if (v > hi) {
        return hi;
    }
    return v;
}

static void ring_push(uint32_t *ring, uint8_t *idx, uint8_t *fill, uint32_t v)
{
    ring[*idx] = v;
    *idx = (uint8_t)((*idx + 1u) % FF_RING_LEN);
    if (*fill < FF_RING_LEN) {
        (*fill)++;
    }
}

static uint32_t ring_avg(const uint32_t *ring, uint8_t fill)
{
    uint64_t sum = 0;
    uint8_t i;
    if (fill == 0u) {
        return 0u;
    }
    for (i = 0; i < fill; i++) {
        sum += ring[i];
    }
    return div_round_u(sum, fill);
}

/* -------------------------------------------------------- pure conversions */

uint32_t ff_mhz_from_period_us(uint32_t period_us)
{
    if (period_us == 0u) {
        return 0u;
    }
    /* f[mHz] = 1e9 / period[us] */
    return div_round_u(1000000000ull, (uint64_t)period_us);
}

uint8_t ff_ethanol_pct_from_mhz(uint32_t freq_mhz)
{
    /* E% = f[Hz] - 50 */
    int32_t e = div_round_i((int32_t)freq_mhz - 50000, 1000);
    return (uint8_t)clamp_i(e, 0, 100);
}

int16_t ff_temp_c_from_low_us(uint32_t low_us)
{
    /* T[C] = 41.5 * low[ms] - 81.25, evaluated in milli-degrees:
     *   T[mC] = 41.5 * low[us] - 81250 = (83 * low[us]) / 2 - 81250 */
    int64_t t_mc = div_round_i64((int64_t)83 * (int64_t)low_us, 2) - 81250;
    int64_t t_c = div_round_i64(t_mc, 1000);
    if (t_c < FF_TEMP_MIN_C) {
        t_c = FF_TEMP_MIN_C;
    }
    if (t_c > FF_TEMP_MAX_C) {
        t_c = FF_TEMP_MAX_C;
    }
    return (int16_t)t_c;
}

ff_status_t ff_status_from_mhz(const ff_config_t *cfg, uint32_t freq_mhz)
{
    if (freq_mhz < cfg->f_min_mhz) {
        return FF_STATUS_FAULT;
    }
    if (freq_mhz <= cfg->f_max_mhz) {
        return FF_STATUS_OK;
    }
    if (freq_mhz <= cfg->f_contam_mhz) {
        return FF_STATUS_CONTAMINATED;
    }
    return FF_STATUS_FAULT;
}

const char *ff_status_name(ff_status_t s)
{
    switch (s) {
    case FF_STATUS_OK:
        return "OK";
    case FF_STATUS_FAULT:
        return "FAULT";
    case FF_STATUS_CONTAMINATED:
        return "CONTAM";
    case FF_STATUS_NOT_READY:
        return "INIT";
    default:
        return "?";
    }
}

/* ------------------------------------------------------------ construction */

void ff_config_default(ff_config_t *cfg)
{
    memset(cfg, 0, sizeof(*cfg));
    cfg->can_id = (uint16_t)FF_CAN_ID_DEFAULT;
    cfg->debounce_us = 250u;       /* 0.25 ms */
    cfg->update_ms = 250u;
    cfg->signal_timeout_ms = 500u;
    cfg->f_min_mhz = 45000u;       /* 45 Hz */
    cfg->f_max_mhz = 155000u;      /* 155 Hz */
    cfg->f_contam_mhz = 200000u;   /* 200 Hz */
    cfg->invert_input = false;
}

void ff_init(ff_state_t *st, const ff_config_t *cfg, uint64_t now_us)
{
    ff_config_t def;
    memset(st, 0, sizeof(*st));
    if (cfg == NULL) {
        ff_config_default(&def);
        st->cfg = def;
    } else {
        st->cfg = *cfg;
    }
    st->t_init_us = now_us;
    st->last_update_us = now_us;
    st->held_temp_c = FF_TEMP_MIN_C;
    st->out.status = FF_STATUS_NOT_READY;
    st->out.ethanol_pct = 0u;
    st->out.fuel_temp_c = FF_TEMP_MIN_C;
}

/* --------------------------------------------------------------- capture */

void ff_on_edge(ff_state_t *st, uint64_t t_us, bool level_high)
{
    bool high = st->cfg.invert_input ? !level_high : level_high;

    if (st->have_edge && t_us < st->last_edge_us) {
        return; /* non-monotonic timestamp, ignore */
    }
    if (st->have_edge && (t_us - st->last_edge_us) < (uint64_t)st->cfg.debounce_us) {
        st->edges_rejected++;
        return; /* glitch */
    }

    st->last_edge_us = t_us;
    st->have_edge = true;
    st->edges_accepted++;

    if (!high) {
        /* falling edge: line pulled low by the sensor -> start of the pulse */
        if (st->have_fall) {
            uint64_t p = t_us - st->last_fall_us;
            if (p > 0u && p <= 0xFFFFFFFFull) {
                st->last_period_us = (uint32_t)p;
                ring_push(st->period_ring, &st->period_idx, &st->period_fill,
                          (uint32_t)p);
            }
        }
        st->last_fall_us = t_us;
        st->have_fall = true;
    } else {
        /* rising edge: end of the low pulse */
        if (st->have_fall && t_us > st->last_fall_us) {
            uint64_t l = t_us - st->last_fall_us;
            if (l <= 0xFFFFFFFFull) {
                ring_push(st->low_ring, &st->low_idx, &st->low_fill, (uint32_t)l);
            }
        }
    }
}

/* ---------------------------------------------------------------- update */

static void clear_rings(ff_state_t *st)
{
    memset(st->period_ring, 0, sizeof(st->period_ring));
    memset(st->low_ring, 0, sizeof(st->low_ring));
    st->period_idx = 0;
    st->period_fill = 0;
    st->low_idx = 0;
    st->low_fill = 0;
    st->last_period_us = 0;
    st->have_fall = false;
}

static void latch_no_signal(ff_state_t *st, uint64_t now_us)
{
    uint64_t timeout_us = (uint64_t)st->cfg.signal_timeout_ms * 1000ull;
    uint64_t since = st->have_edge ? (now_us - st->last_edge_us)
                                   : (now_us - st->t_init_us);
    bool timed_out = since >= timeout_us;

    memset(&st->out, 0, sizeof(st->out));
    st->out.measured = false;
    if (timed_out) {
        st->out.status = FF_STATUS_FAULT;
        /* hold the last plausible reading, see ff_sensor.h */
        st->out.ethanol_pct = st->have_valid ? st->held_pct : 0u;
        st->out.fuel_temp_c = st->have_valid ? st->held_temp_c : FF_TEMP_MIN_C;
    } else {
        st->out.status = FF_STATUS_NOT_READY;
        st->out.ethanol_pct = 0u;
        st->out.fuel_temp_c = FF_TEMP_MIN_C;
    }
}

bool ff_update(ff_state_t *st, uint64_t now_us)
{
    uint64_t timeout_us = (uint64_t)st->cfg.signal_timeout_ms * 1000ull;
    uint64_t update_us = (uint64_t)st->cfg.update_ms * 1000ull;
    uint64_t since_edge;
    bool due;

    since_edge = st->have_edge ? (now_us - st->last_edge_us)
                               : (now_us - st->t_init_us);

    /* The signal timeout is evaluated on every call so a dropout is reported
     * within one main-loop tick rather than after the next 250 ms window. */
    if (since_edge >= timeout_us) {
        if (st->period_fill != 0u || st->low_fill != 0u || st->have_fall) {
            st->timeouts++;
            clear_rings(st);
        }
        if (st->out.status != FF_STATUS_FAULT || st->out.measured) {
            latch_no_signal(st, now_us);
        }
        st->last_update_us = now_us;
        return false;
    }

    due = (now_us - st->last_update_us) >= update_us;
    if (!due) {
        return false;
    }
    st->last_update_us = now_us;

    if (st->period_fill < FF_RING_LEN || st->low_fill < FF_RING_LEN) {
        /* Signal present but not yet enough samples for a full average. */
        latch_no_signal(st, now_us);
        return false;
    }

    {
        uint32_t period_avg = ring_avg(st->period_ring, st->period_fill);
        uint32_t low_avg = ring_avg(st->low_ring, st->low_fill);
        uint32_t f_avg = ff_mhz_from_period_us(period_avg);
        uint32_t f_raw = ff_mhz_from_period_us(st->last_period_us);
        ff_status_t status = ff_status_from_mhz(&st->cfg, f_avg);

        if (status == FF_STATUS_FAULT) {
            /* Implausible frequency: same reporting rule as a dead signal,
             * but without waiting for the timeout. */
            memset(&st->out, 0, sizeof(st->out));
            st->out.status = FF_STATUS_FAULT;
            st->out.ethanol_pct = st->have_valid ? st->held_pct : 0u;
            st->out.fuel_temp_c = st->have_valid ? st->held_temp_c : FF_TEMP_MIN_C;
            st->out.measured = false;
            return true;
        }

        st->out.status = status;
        st->out.ethanol_pct = ff_ethanol_pct_from_mhz(f_avg);
        st->out.fuel_temp_c = ff_temp_c_from_low_us(low_avg);
        st->out.freq_avg_mhz = f_avg;
        st->out.freq_raw_mhz = f_raw;
        st->out.period_avg_us = period_avg;
        st->out.low_avg_us = low_avg;
        st->out.measured = true;

        if (status == FF_STATUS_OK) {
            st->have_valid = true;
            st->held_pct = st->out.ethanol_pct;
            st->held_temp_c = st->out.fuel_temp_c;
        }
    }
    return true;
}

const ff_output_t *ff_output(const ff_state_t *st)
{
    return &st->out;
}

/* ----------------------------------------------------------------- frame */

void ff_build_frame(const ff_output_t *out, uint8_t counter, uint8_t data[8])
{
    int32_t t_enc;
    uint32_t f_half;

    memset(data, 0, FF_FRAME_DLC);

    data[0] = (uint8_t)clamp_i((int32_t)out->ethanol_pct, 0, 100);

    t_enc = clamp_i((int32_t)out->fuel_temp_c + 40, 0, 255);
    data[1] = (uint8_t)t_enc;

    /* raw frequency / 2 in Hz: f[mHz] / 2000, rounded */
    f_half = div_round_u((uint64_t)out->freq_raw_mhz, 2000ull);
    if (f_half > 255u) {
        f_half = 255u;
    }
    data[2] = (uint8_t)f_half;

    data[3] = counter;
    data[4] = 0u;
    data[5] = 0u;
    data[6] = (uint8_t)FF_FW_VERSION;
    data[7] = (uint8_t)out->status;
}
