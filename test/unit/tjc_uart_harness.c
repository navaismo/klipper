// Native tests of the STM32 USART2 queues with simulated registers
// Copyright (C) 2026
// This file may be distributed under the terms of the GNU GPLv3 license.

#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include "tjc_uart.c"

struct usart_regs fake_usart;

static unsigned int clock_enabled;
void enable_pclock(uint32_t addr) { clock_enabled = 1; }
int is_enabled_pclock(uint32_t addr) { return clock_enabled; }
uint32_t get_pclock_frequency(uint32_t addr) { return 36000000; }
void gpio_peripheral(uint32_t gpio, uint32_t mode, int pullup) { }
void armcm_enable_irq(void (*handler)(void), int number, int priority) { }
irqstatus_t irq_save(void) { return 0; }
void irq_restore(irqstatus_t status) { }

static void receive(uint8_t data, uint32_t errors)
{
    fake_usart.SR = USART_SR_RXNE | errors;
    fake_usart.DR = data;
    USART2_IRQHandler();
}

int main(void)
{
    tjc_uart_setup();
    assert(fake_usart.BRR == 313 && fake_usart.CR1 == 0);
    tjc_uart_start();
    // An empty queue may send even if TC was cleared before re-enabling UE.
    fake_usart.SR = 0;
    const uint8_t msg[] = { 0xaa, 0x00, 0xcc, 0x33, 0xc3, 0x3c };
    assert(!tjc_uart_busy() && tjc_uart_send(msg, sizeof(msg)));
    assert(tjc_uart_busy() && !tjc_uart_send(msg, sizeof(msg)));
    for (unsigned int i = 0; i < sizeof(msg); i++) {
        fake_usart.SR = USART_SR_TXE;
        USART2_IRQHandler();
        assert(fake_usart.DR == msg[i]);
    }
    // TXE/empty software queue does not mean the last stop bit has left.
    assert(tjc_uart_busy());
    USART2_IRQHandler();
    assert(!(fake_usart.CR1 & USART_CR1_TXEIE));
    fake_usart.SR = USART_SR_TC;
    assert(!tjc_uart_busy());
    assert(!tjc_uart_send(msg, 0) && !tjc_uart_send(msg, 65));
    uint8_t long_msg[64];
    for (unsigned int i=0; i<sizeof(long_msg); i++) long_msg[i]=i;
    assert(tjc_uart_send(long_msg, sizeof(long_msg)));
    for (unsigned int i=0; i<sizeof(long_msg); i++) {
        fake_usart.SR = USART_SR_TXE;
        USART2_IRQHandler();
        assert(fake_usart.DR == long_msg[i]);
    }
    USART2_IRQHandler();
    fake_usart.SR = USART_SR_TC;
    assert(!tjc_uart_busy());

    for (unsigned int round = 0; round < 3; round++) {
        for (unsigned int i = 0; i < 63; i++) receive(i, 0);
        assert(!tjc_uart_error());
        for (unsigned int i = 0; i < 63; i++)
            assert(tjc_uart_read() == (int)i);
        assert(tjc_uart_read() == -1);
    }
    for (unsigned int i = 0; i < 64; i++) receive(i, 0);
    assert(tjc_uart_error());
    tjc_uart_reset_rx();
    assert(!tjc_uart_error() && tjc_uart_read() == -1);
    receive(0xaa, USART_SR_ORE);
    assert(tjc_uart_error() && tjc_uart_read() == -1);
    tjc_uart_reset_rx();
    receive(0xaa, USART_SR_FE | USART_SR_NE);
    assert(tjc_uart_error());
    tjc_uart_stop();
    assert(!fake_usart.CR1);
    receive(0xaa, 0);
    assert(tjc_uart_read() == -1); // no RX interrupt work after completion
    tjc_uart_setup(); // a later config_reset may reclaim its own peripheral
    tjc_uart_start();
    assert(tjc_uart_send(msg, sizeof(msg)));
    return 0;
}
