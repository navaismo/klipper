// Dedicated USART2 transport for the Ender-3 V3 SE TJC LCD
// Based on stm32/serial.c, Copyright (C) 2019 Kevin O'Connor
//
// This file may be distributed under the terms of the GNU GPLv3 license.

#include <string.h> // memcpy
#include "autoconf.h" // CONFIG_STM32_SERIAL_USART2
#include "board/armcm_boot.h" // armcm_enable_irq
#include "board/irq.h" // irq_save
#include "command.h" // DECL_CONSTANT_STR
#include "internal.h" // USART2
#include "sched.h" // shutdown
#include "tjc_uart.h" // tjc_uart_send

#if CONFIG_STM32_SERIAL_USART2 || CONFIG_STM32_SERIAL_USART2_ALT_PD6_PD5
#error "TJC LCD and host communication cannot share USART2"
#endif

DECL_CONSTANT_STR("RESERVE_PINS_tjc_lcd", "PA2,PA3");
DECL_CONSTANT("TJC_LCD_BAUD", 115200);

#define RX_SIZE 64
#define TX_SIZE 64
#define CR1_FLAGS (USART_CR1_UE | USART_CR1_RE | USART_CR1_TE \
                   | USART_CR1_RXNEIE)
#define RX_ERRORS (USART_SR_ORE | USART_SR_FE | USART_SR_NE)

static uint8_t rx_buf[RX_SIZE], tx_buf[TX_SIZE];
static volatile uint8_t rx_head, rx_tail, rx_error, tx_pos, tx_len;
static uint8_t claimed;

void
USART2_IRQHandler(void)
{
    uint32_t sr = USART2->SR;
    if ((sr & (USART_SR_RXNE | RX_ERRORS))
        && (USART2->CR1 & USART_CR1_RXNEIE)) {
        // SR followed by DR clears overrun, noise, and framing errors.
        uint8_t data = USART2->DR;
        uint8_t next = (rx_head + 1) % RX_SIZE;
        if ((sr & RX_ERRORS) || next == rx_tail)
            rx_error = 1;
        else {
            rx_buf[rx_head] = data;
            rx_head = next;
        }
    }
    if ((sr & USART_SR_TXE) && (USART2->CR1 & USART_CR1_TXEIE)) {
        if (tx_pos < tx_len)
            USART2->DR = tx_buf[tx_pos++];
        else
            USART2->CR1 = CR1_FLAGS;
    }
}

void
tjc_uart_setup(void)
{
    if (is_enabled_pclock((uint32_t)USART2) && !claimed)
        shutdown("TJC USART2 is already in use");
    enable_pclock((uint32_t)USART2);
    claimed = 1;
    gpio_peripheral(GPIO('A', 3), GPIO_FUNCTION(7), 1);
    gpio_peripheral(GPIO('A', 2), GPIO_FUNCTION(7), 0);
    USART2->BRR = DIV_ROUND_CLOSEST(
        get_pclock_frequency((uint32_t)USART2), 115200);
    USART2->CR2 = USART2->CR3 = 0; // 8N1, no flow control
    USART2->CR1 = 0;
    armcm_enable_irq(USART2_IRQHandler, USART2_IRQn, 0);
}

void
tjc_uart_start(void)
{
    irqstatus_t flag = irq_save();
    USART2->CR1 = 0;
    tx_pos = tx_len = 0;
    tjc_uart_reset_rx();
    USART2->CR1 = CR1_FLAGS;
    irq_restore(flag);
}

void
tjc_uart_reset_rx(void)
{
    irqstatus_t flag = irq_save();
    if (USART2->SR & (USART_SR_RXNE | RX_ERRORS))
        (void)USART2->DR;
    rx_head = rx_tail = rx_error = 0;
    irq_restore(flag);
}

int
tjc_uart_read(void)
{
    irqstatus_t flag = irq_save();
    int data = -1;
    if (rx_head != rx_tail) {
        data = rx_buf[rx_tail];
        rx_tail = (rx_tail + 1) % RX_SIZE;
    }
    irq_restore(flag);
    return data;
}

uint_fast8_t
tjc_uart_error(void)
{
    return rx_error;
}

uint_fast8_t
tjc_uart_busy(void)
{
    return tx_pos < tx_len || (tx_len && !(USART2->SR & USART_SR_TC));
}

uint_fast8_t
tjc_uart_send(const uint8_t *data, uint_fast8_t len)
{
    irqstatus_t flag = irq_save();
    if (!len || len > TX_SIZE || tjc_uart_busy()) {
        irq_restore(flag);
        return 0;
    }
    memcpy(tx_buf, data, len);
    tx_pos = 0;
    tx_len = len;
    USART2->CR1 = CR1_FLAGS | USART_CR1_TXEIE;
    irq_restore(flag);
    return 1;
}

void
tjc_uart_stop(void)
{
    USART2->CR1 = 0;
    tx_pos = tx_len = rx_head = rx_tail = rx_error = 0;
}
