/*
 * main.cpp - flex-fuel sensor node for the MED9.1.1 conversion.
 *
 * A GM/Continental flex-fuel sensor (13577379 / 13577394) drives GP15 through a
 * pull-up and a level shifter. This node measures its frequency and low pulse
 * width, converts them to ethanol content and fuel temperature, and transmits
 * them on the powertrain CAN at 10 Hz on identifier 0x0EC.
 *
 * See docs/05_flexfuel_design.md section 2 and README.md for the wiring.
 *
 * Structure
 *   sensor_input.cpp  GPIO interrupt -> timestamped edge ring   (target only)
 *   ff_sensor.c       measurement, plausibility, frame encoding (target + host)
 *   can_hw.cpp        MCP2515 transport                         (target only)
 *   display.cpp       SSD1306 status page                       (target only)
 */

#include <stdio.h>

#include "pico/stdlib.h"

#include "can_hw.h"
#include "display.h"
#include "ff_sensor.h"
#include "sensor_input.h"

/* Transmit period; docs/05_flexfuel_design.md section 2 asks for 10 Hz. */
#define FF_TX_INTERVAL_US 100000ull
/* Display refresh; slower than the frame so the I2C transfer never delays a
 * transmission by more than one slot. */
#define FF_DISPLAY_INTERVAL_US 250000ull
/* Serial diagnostics over USB CDC. */
#define FF_LOG_INTERVAL_US 1000000ull

static ff_state_t g_ff;
static ff_config_t g_cfg;

int main()
{
    stdio_init_all();

    const uint LED_PIN = PICO_DEFAULT_LED_PIN;
    gpio_init(LED_PIN);
    gpio_set_dir(LED_PIN, GPIO_OUT);
    gpio_put(LED_PIN, 0);

    /* Run-time configuration. Everything here is a compile-time default that
     * can be changed without touching the measurement logic; ff_config_t is
     * the single place the ECU-visible identifier lives. */
    ff_config_default(&g_cfg);
    /* g_cfg.can_id = 0x123;        // example: move the frame somewhere else
     * g_cfg.invert_input = true;   // example: inverting level shifter        */

    display_init();
    display_splash("FlexFuel node", "starting...");

    sensor_input_init(FF_SENSOR_GPIO);
    ff_init(&g_ff, &g_cfg, time_us_64());

    const bool can_ok_at_boot = can_hw_init(g_cfg.can_id);
    printf("flex-fuel node v%u: CAN id 0x%03X, sensor on GP%u, MCP2515 %s\n",
           (unsigned)FF_FW_VERSION, (unsigned)g_cfg.can_id,
           (unsigned)FF_SENSOR_GPIO, can_ok_at_boot ? "ready" : "NOT RESPONDING");

    uint64_t now = time_us_64();
    uint64_t next_tx = now + FF_TX_INTERVAL_US;
    uint64_t next_disp = now + FF_DISPLAY_INTERVAL_US;
    uint64_t next_log = now + FF_LOG_INTERVAL_US;
    uint64_t led_off = 0;
    uint8_t counter = 0;
    bool can_ok = can_ok_at_boot;

    while (true) {
        /* 1. Hand every captured edge to the measurement logic. */
        sensor_input_drain(&g_ff);

        /* 2. Advance the state machine (timeouts are evaluated every call,
         *    the averaged output is re-latched every cfg.update_ms). */
        now = time_us_64();
        ff_update(&g_ff, now);

        /* 3. Transmit at 10 Hz. */
        if (now >= next_tx) {
            uint8_t data[8];
            ff_build_frame(ff_output(&g_ff), counter, data);
            can_ok = can_hw_send_frame(data) && can_hw_is_connected();
            counter++;

            gpio_put(LED_PIN, 1); /* heartbeat: one blink per frame */
            led_off = now + 20000ull;
            next_tx += FF_TX_INTERVAL_US;
            if (next_tx <= now) { /* fell behind, resynchronise */
                next_tx = now + FF_TX_INTERVAL_US;
            }
        }
        if (led_off != 0ull && now >= led_off) {
            gpio_put(LED_PIN, 0);
            led_off = 0ull;
        }

        /* 4. Display. */
        if (now >= next_disp) {
            display_update(ff_output(&g_ff), can_ok, can_hw_get_id());
            next_disp += FF_DISPLAY_INTERVAL_US;
            if (next_disp <= now) {
                next_disp = now + FF_DISPLAY_INTERVAL_US;
            }
        }

        /* 5. USB serial diagnostics. */
        if (now >= next_log) {
            const ff_output_t *o = ff_output(&g_ff);
            printf("E=%u%% T=%dC f=%u.%03u Hz low=%u us status=%s "
                   "can=%s txerr=%u ovf=%u\n",
                   (unsigned)o->ethanol_pct, (int)o->fuel_temp_c,
                   (unsigned)(o->freq_avg_mhz / 1000u),
                   (unsigned)(o->freq_avg_mhz % 1000u), (unsigned)o->low_avg_us,
                   ff_status_name(o->status), can_ok ? "ok" : "err",
                   (unsigned)can_hw_tx_errors(),
                   (unsigned)sensor_input_overflows());
            next_log += FF_LOG_INTERVAL_US;
            if (next_log <= now) {
                next_log = now + FF_LOG_INTERVAL_US;
            }
        }

        /* 6. The capture is interrupt driven, so the loop only has to be quick
         *    enough to drain the ring. 1 ms leaves plenty of margin. */
        sleep_us(500);
    }

    return 0;
}
