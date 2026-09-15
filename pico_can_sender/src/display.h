/*
 * display.h - SSD1306 status page for the flex-fuel node.
 */

#ifndef DISPLAY_H
#define DISPLAY_H

#include <stdbool.h>
#include <stdint.h>

#include "ff_sensor.h"

#ifdef __cplusplus
extern "C" {
#endif

void display_init(void);

/* One line banner used while the node is booting. */
void display_splash(const char *line1, const char *line2);

/* Ethanol %, fuel temperature, status and the CAN health, plus the identifier
 * the node is transmitting on. */
void display_update(const ff_output_t *out, bool can_ok, uint16_t can_id);

#ifdef __cplusplus
}
#endif

#endif /* DISPLAY_H */
