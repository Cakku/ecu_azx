#ifndef POT_H
#define POT_H

#include <stdint.h>

// Potentiometer on GPIO 26 (ADC0)
#define POT_PIN 26
#define POT_ADC_CH 0

void pot_init(void);
uint8_t pot_read_percentage(void);

#endif // POT_H
