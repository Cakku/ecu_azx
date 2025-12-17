#ifndef CAN_HW_H
#define CAN_HW_H

#include <stdbool.h>
#include <stdint.h>


void can_hw_init(void);
void can_hw_send_value(uint8_t value);
bool can_hw_is_connected(void);

#endif // CAN_HW_H
