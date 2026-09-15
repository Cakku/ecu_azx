/*
 * can_hw.h - MCP2515 transport for the flex-fuel frame.
 *
 * 500 kbit/s, standard (11 bit) identifiers, DLC 8. The identifier defaults to
 * FF_CAN_ID_DEFAULT (0x0EC, docs/05_flexfuel_design.md section 2) and can be
 * changed at run time through can_hw_set_id() / the ff_config_t the node is
 * started with.
 */

#ifndef CAN_HW_H
#define CAN_HW_H

#include <stdbool.h>
#include <stdint.h>

/* MCP2515 module crystal. The common blue "MCP2515 + TJA1050" boards ship with
 * an 8 MHz crystal; some carry 16 MHz. Override with -DCAN_XTAL_MHZ=16. */
#ifndef CAN_XTAL_MHZ
#define CAN_XTAL_MHZ 8
#endif

#ifdef __cplusplus
extern "C" {
#endif

/* Reset the controller, set 500 kbit/s and go to normal mode.
 * Returns true when the controller answered. */
bool can_hw_init(uint16_t can_id);

/* Change the transmit identifier (11 bit). */
void can_hw_set_id(uint16_t can_id);
uint16_t can_hw_get_id(void);

/* Send one 8 byte frame. Returns true when the MCP2515 accepted it. */
bool can_hw_send_frame(const uint8_t data[8]);

/* True while the controller is initialised and the last transmissions were
 * accepted. */
bool can_hw_is_connected(void);

/* Consecutive transmit failures and the total failure count, for the display. */
uint32_t can_hw_tx_errors(void);

#ifdef __cplusplus
}
#endif

#endif /* CAN_HW_H */
