#ifndef __TJC_UART_H
#define __TJC_UART_H

#include <stdint.h>

// Board transport for the mainboard-connected TJC display.
void tjc_uart_setup(void);
void tjc_uart_start(void);
void tjc_uart_reset_rx(void);
int tjc_uart_read(void);
uint_fast8_t tjc_uart_error(void);
uint_fast8_t tjc_uart_send(const uint8_t *data, uint_fast8_t len);
uint_fast8_t tjc_uart_busy(void);
void tjc_uart_stop(void);

#endif // tjc_uart.h
