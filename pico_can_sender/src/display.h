#ifndef DISPLAY_H
#define DISPLAY_H

#include <stdbool.h>
#include <stdint.h>


void display_init(void);
void display_update(uint8_t pot_val, bool can_status);

#endif // DISPLAY_H
