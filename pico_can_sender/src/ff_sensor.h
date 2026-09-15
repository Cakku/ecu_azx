/*
 * ff_sensor.h - GM/Continental flex-fuel sensor measurement and state logic.
 *
 * Pure C11. No Pico SDK headers, no floating point, no dynamic allocation, so
 * the exact same object code semantics are exercised by the host test suite in
 * ../test/ and by the firmware on the RP2350.
 *
 * Reference: docs/05_flexfuel_design.md section 2.
 *
 *   Sensor: GM/Continental 13577379 / 13577394, open collector square wave.
 *     frequency  50 Hz =  0 % ethanol .. 150 Hz = 100 % ethanol (linear)
 *                E% = f_Hz - 50
 *     low pulse   1 ms = -40 C .. 5 ms = 125 C fuel temperature
 *                T_C = 41.5 * low_ms - 81.25
 *
 *   Plausibility (this module):
 *     f <  45.000 Hz                 -> FAULT
 *     45.000 Hz <= f <= 155.000 Hz   -> OK
 *     155.000 Hz <  f <= 200.000 Hz  -> CONTAMINATED
 *     f > 200.000 Hz                 -> FAULT
 *     no accepted edge for 500 ms    -> FAULT
 *     no complete measurement yet    -> NOT_READY (until the signal timeout
 *                                      expires, then FAULT)
 *
 * Conventions
 *   - All timestamps are free-running microseconds (uint64_t), monotonic.
 *   - Frequencies are carried in milli-hertz (mHz) so that 0.001 Hz of
 *     resolution survives the integer arithmetic; 100 Hz == 100000 mHz.
 *   - The low pulse is measured between an accepted falling edge and the next
 *     accepted rising edge; the period between two consecutive accepted
 *     falling edges. With the sensor's open collector and an external pull-up
 *     the line idles HIGH, so "low" is the sensor pulling the line down. Use
 *     ff_config_t.invert_input if the level shifter in front of the Pico
 *     inverts.
 *
 * Reported values when there is no valid measurement (see ff_update()):
 *   NOT_READY   ethanol 0 %, temperature -40 C, raw frequency 0
 *   FAULT       the last plausible ethanol % and temperature are HELD
 *               (0 % / -40 C if there never was one), raw frequency 0.
 *               Holding is deliberate: a consumer that ignores the status byte
 *               keeps fuelling for the last known blend instead of leaning out
 *               on a dead sensor. docs/05_flexfuel_design.md section 3.2 makes
 *               the ECU do the same thing.
 *   CONTAMINATED the measured values are reported; the ethanol percentage is
 *               clamped to 100 % so byte 0 of the frame stays in range. The
 *               ECU is expected to HOLD its filtered value in this state.
 */

#ifndef FF_SENSOR_H
#define FF_SENSOR_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Firmware version reported in byte 6 of the CAN frame. */
#ifndef FF_FW_VERSION
#define FF_FW_VERSION 1u
#endif

/* Default CAN identifier, compile-time overridable; also settable at run time
 * through ff_config_t.can_id. docs/05_flexfuel_design.md section 2. */
#ifndef FF_CAN_ID_DEFAULT
#define FF_CAN_ID_DEFAULT 0x0ECu
#endif

/* Number of periods / low pulses averaged. */
#define FF_RING_LEN 4u

/* Frame length; fixed by the Zeitronix ECA-2 compatible layout. */
#define FF_FRAME_DLC 8u

/* Reported temperature is clamped to the sensor's specified range. */
#define FF_TEMP_MIN_C (-40)
#define FF_TEMP_MAX_C (125)

typedef enum {
    FF_STATUS_OK = 0,
    FF_STATUS_FAULT = 1,
    FF_STATUS_CONTAMINATED = 2,
    FF_STATUS_NOT_READY = 3
} ff_status_t;

typedef struct {
    uint16_t can_id;            /* 11 bit identifier, default FF_CAN_ID_DEFAULT */
    uint32_t debounce_us;       /* edges closer than this are glitches (250) */
    uint32_t update_ms;         /* averaged output refresh interval (250) */
    uint32_t signal_timeout_ms; /* no edge for this long -> FAULT (500) */
    uint32_t f_min_mhz;         /* lower plausibility limit (45000) */
    uint32_t f_max_mhz;         /* upper limit of the valid window (155000) */
    uint32_t f_contam_mhz;      /* upper limit of the contaminated window (200000) */
    bool invert_input;          /* true if the input stage inverts the sensor */
} ff_config_t;

typedef struct {
    ff_status_t status;
    uint8_t ethanol_pct;    /* 0..100, from the averaged frequency */
    int16_t fuel_temp_c;    /* FF_TEMP_MIN_C..FF_TEMP_MAX_C */
    uint32_t freq_avg_mhz;  /* averaged frequency, milli-hertz, 0 if none */
    uint32_t freq_raw_mhz;  /* last single period, milli-hertz, 0 if none */
    uint32_t period_avg_us; /* averaged period, 0 if none */
    uint32_t low_avg_us;    /* averaged low pulse, 0 if none */
    bool measured;          /* a complete ring average was available */
} ff_output_t;

typedef struct {
    ff_config_t cfg;

    /* edge capture */
    uint64_t t_init_us;
    uint64_t last_edge_us;  /* last accepted edge of either direction */
    uint64_t last_fall_us;  /* last accepted falling edge */
    bool have_edge;         /* an edge was accepted since init/timeout */
    bool have_fall;

    /* ring buffers */
    uint32_t period_ring[FF_RING_LEN];
    uint32_t low_ring[FF_RING_LEN];
    uint8_t period_idx, period_fill;
    uint8_t low_idx, low_fill;
    uint32_t last_period_us; /* raw, un-averaged */

    /* output latching */
    uint64_t last_update_us;
    ff_output_t out;

    /* hold-last-plausible */
    bool have_valid;
    uint8_t held_pct;
    int16_t held_temp_c;

    /* diagnostics */
    uint32_t edges_accepted;
    uint32_t edges_rejected; /* debounce rejects */
    uint32_t timeouts;
} ff_state_t;

/* Fill cfg with the defaults from docs/05_flexfuel_design.md section 2. */
void ff_config_default(ff_config_t *cfg);

/* Reset st. cfg may be NULL, in which case the defaults are used. */
void ff_init(ff_state_t *st, const ff_config_t *cfg, uint64_t now_us);

/* Feed one captured edge. level_high is the pin level AFTER the edge, before
 * ff_config_t.invert_input is applied. Safe to call with bursts of events that
 * were queued by an interrupt handler, as long as t_us is non-decreasing. */
void ff_on_edge(ff_state_t *st, uint64_t t_us, bool level_high);

/* Advance the state machine. Call as often as convenient (the firmware calls
 * it every ~1 ms); the averaged values are re-latched at most every
 * cfg.update_ms, the timeout/status is evaluated on every call.
 * Returns true when the averaged output was re-latched. */
bool ff_update(ff_state_t *st, uint64_t now_us);

/* Latest output. Never NULL. */
const ff_output_t *ff_output(const ff_state_t *st);

/* Encode the 8 byte CAN payload. docs/05_flexfuel_design.md section 2:
 *   0 ethanol %, 1 fuel temperature + 40, 2 raw frequency / 2 [Hz],
 *   3 rolling counter, 4-5 reserved 0, 6 firmware version, 7 status. */
void ff_build_frame(const ff_output_t *out, uint8_t counter, uint8_t data[8]);

/* Pure conversions, exposed so the host tests can check them directly. */
uint8_t ff_ethanol_pct_from_mhz(uint32_t freq_mhz);
int16_t ff_temp_c_from_low_us(uint32_t low_us);
ff_status_t ff_status_from_mhz(const ff_config_t *cfg, uint32_t freq_mhz);
uint32_t ff_mhz_from_period_us(uint32_t period_us);

/* Short status name for the display / logs ("OK", "FAULT", "CONTAM", "INIT"). */
const char *ff_status_name(ff_status_t s);

#ifdef __cplusplus
}
#endif

#endif /* FF_SENSOR_H */
