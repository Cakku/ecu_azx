#include "pot.h"
#include "hardware/adc.h"
#include "pico/stdlib.h"

void pot_init(void) {
  adc_init();
  adc_gpio_init(POT_PIN);
  adc_select_input(POT_ADC_CH);
}

uint8_t pot_read_percentage(void) {
  // 12-bit ADC (0-4095)
  uint16_t raw = adc_read();

  // Simple smoothing could go here, but for now raw read

  // Convert to 0-100%
  uint32_t percent = (raw * 100) / 4095;

  if (percent > 100)
    percent = 100;

  return (uint8_t)percent;
}
