/*
 * sensor_input.h - RP2350 edge capture for the flex-fuel sensor input.
 *
 * Both edges raise a GPIO interrupt; the handler only timestamps the edge with
 * time_us_64() and pushes it into a lock-free ring. The main loop drains the
 * ring into ff_on_edge(), so all measurement logic stays in ff_sensor.c where
 * the host tests can reach it.
 */

#ifndef SENSOR_INPUT_H
#define SENSOR_INPUT_H

#include <stdbool.h>
#include <stdint.h>

#include "ff_sensor.h"

/* GPIO carrying the (level shifted) sensor square wave.
 * GP15 = physical pin 20 on the Pico 2. Kept clear of SPI0 (GP16..GP20) and
 * I2C0 (GP4/GP5). Override at build time with -DFF_SENSOR_GPIO=n. */
#ifndef FF_SENSOR_GPIO
#define FF_SENSOR_GPIO 15u
#endif

#ifdef __cplusplus
extern "C" {
#endif

/* Configure the pin as a plain input with no internal pull (the sensor needs an
 * external 2.2-3.5 kOhm pull-up to 5 V, see README) and enable both-edge IRQs. */
void sensor_input_init(unsigned gpio);

/* Move every captured edge into the measurement state. Returns how many edges
 * were handed over. Call from the main loop, not from an interrupt. */
uint32_t sensor_input_drain(ff_state_t *st);

/* Number of edges dropped because the capture ring was full (should stay 0). */
uint32_t sensor_input_overflows(void);

/* Current pin level, for the display / diagnostics. */
bool sensor_input_level(void);

#ifdef __cplusplus
}
#endif

#endif /* SENSOR_INPUT_H */
