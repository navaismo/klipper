// Native tests of the actual MCU protocol code with a simulated UART
// Copyright (C) 2026
// This file may be distributed under the terms of the GNU GPLv3 license.

#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "tjc_lcd.c"

static uint32_t clock_now;
static struct timer *active_timer;
static unsigned int frames, stopped, busy, uart_error, last_state, last_error;
static unsigned int tx_size[16];
static uint8_t tx[16][32], rx[128];
static unsigned int rx_pos, rx_len;

void *oid_alloc(uint8_t oid, void *type, uint16_t size)
{ return calloc(1, size); }
void *oid_lookup(uint8_t oid, void *type)
{ assert(lcd && oid == 7); return lcd; }
uint32_t timer_read_time(void)
{ return clock_now; }
void sched_add_timer(struct timer *timer)
{ assert(!active_timer); active_timer = timer; }
void sched_del_timer(struct timer *timer)
{ active_timer = NULL; }
void sched_wake_task(struct task_wake *wake)
{ wake->wake = 1; }
uint8_t sched_check_wake(struct task_wake *wake)
{ uint8_t val = wake->wake; wake->wake = 0; return val; }
void test_sendf(const char *fmt, ...)
{
    va_list args;
    va_start(args, fmt);
    assert(va_arg(args, int) == 7);
    last_state = va_arg(args, int);
    last_error = va_arg(args, int);
    va_end(args);
}
void tjc_uart_setup(void) { }
void tjc_uart_start(void) { stopped = busy = uart_error = 0; }
void tjc_uart_stop(void) { stopped = 1; busy = 0; }
void tjc_uart_reset_rx(void) { rx_pos = rx_len = uart_error = 0; }
uint_fast8_t tjc_uart_busy(void) { return busy; }
uint_fast8_t tjc_uart_error(void) { return uart_error; }
int tjc_uart_read(void)
{ return rx_pos < rx_len ? rx[rx_pos++] : -1; }
uint_fast8_t tjc_uart_send(const uint8_t *data, uint_fast8_t len)
{
    assert(!stopped);
    if (busy) return 0;
    assert(frames < 16 && len <= 32);
    memcpy(tx[frames], data, len);
    tx_size[frames++] = len;
    busy = 1;
    return 1;
}

static void tick(unsigned int count)
{
    while (count--) {
        clock_now += 5000;
        if (active_timer) {
            active_timer->func(active_timer);
            tjc_lcd_task();
        }
    }
}

static void receive(const uint8_t *data, unsigned int len)
{
    assert(len <= sizeof(rx));
    memcpy(rx, data, len);
    rx_pos = 0;
    rx_len = len;
}

static void reply(void)
{
    const uint8_t response[] = {
        0x19, 0xaa, 0xaa, 0x00, 'O', 'K', '_', 'V', '1', '.', '5',
        0xcc, 0x33, 0xc3, 0x3c
    };
    receive(response, sizeof(response));
    busy = 0;
    tick(1);
}

static void finish_clear(void)
{
    tick(1); // palette
    assert(frames == 2);
    const uint8_t palette[] = {
        0xaa, 0x40, 0xff, 0xff, 0x12, 0x34, 0xcc, 0x33, 0xc3, 0x3c
    };
    assert(tx_size[1] == sizeof(palette));
    assert(!memcmp(tx[1], palette, sizeof(palette)));
    tick(3); // TX has not drained: never queue the clear yet
    assert(frames == 2);
    busy = 0;
    tick(1);
    const uint8_t clear[] = { 0xaa, 0x52, 0xcc, 0x33, 0xc3, 0x3c };
    assert(frames == 3 && tx_size[2] == sizeof(clear));
    assert(!memcmp(tx[2], clear, sizeof(clear)));
    busy = 0;
    tick(1); // start settling after the final stop bit
    tick(9);
    assert(last_state == TJC_CLEARING && active_timer);
    tick(1);
    assert(last_state == TJC_READY && !last_error);
    assert(!active_timer && stopped);
    tick(2000);
    assert(frames == 3); // no idle refresh / background UART work
}

int main(int argc, char **argv)
{
    assert(argc == 2);
    if (!strcmp(argv[1], "rollover"))
        clock_now = UINT32_MAX - 20000;
    uint32_t config[] = { 7, 30000, 100000, 0x1234 };
    command_config_tjc_lcd(config);
    uint32_t start[] = { 7 };
    command_tjc_lcd_start(start);
    assert(!frames && last_state == TJC_HANDSHAKE);
    tick(5);
    assert(!frames);
    tick(1);
    const uint8_t handshake[] = { 0xaa, 0x00, 0xcc, 0x33, 0xc3, 0x3c };
    assert(frames == 1 && tx_size[0] == sizeof(handshake));
    assert(!memcmp(tx[0], handshake, sizeof(handshake)));
    if (!strcmp(argv[1], "timeout")) {
        for (unsigned int i = 0; i < 65; i++) {
            busy = 0;
            tick(1);
        }
        assert(frames == 3 && last_state == TJC_ERROR);
        assert(last_error == ERR_TIMEOUT && !active_timer && stopped);
    } else if (!strcmp(argv[1], "uart_error")) {
        uart_error = 1;
        tick(1);
        assert(frames == 1 && last_error == ERR_UART);
        assert(last_state == TJC_ERROR && !active_timer && stopped);
    } else if (!strcmp(argv[1], "partial")) {
        const uint8_t partial[] = { 0xaa, 0x00, 'O', 'K', 0xcc, 0x33 };
        receive(partial, sizeof(partial));
        busy = 0;
        tick(2);
        assert(frames == 1 && last_state == TJC_HANDSHAKE);
        const uint8_t end[] = { 0xc3, 0x3c };
        receive(end, sizeof(end));
        tick(1);
        finish_clear();
    } else if (!strcmp(argv[1], "shutdown")) {
        tjc_lcd_shutdown();
        tick(1000);
        assert(frames == 1 && stopped && !active_timer && !lcd);
        // config_reset can subsequently allocate a new LCD safely.
        command_config_tjc_lcd(config);
        command_tjc_lcd_start(start);
        tick(6);
        assert(frames == 2);
    } else if (!strcmp(argv[1], "retry")) {
        busy = 0;
        tick(20);
        assert(frames == 2 && last_state == TJC_HANDSHAKE);
        reply();
        tick(1);
        assert(frames == 3 && tx[2][1] == 0x40);
    } else if (!strcmp(argv[1], "tx_stall")) {
        reply();
        busy = 1;
        tick(21);
        assert(last_state == TJC_ERROR && frames == 1 && stopped);
    } else if (!strcmp(argv[1], "oversized_reply")) {
        uint8_t malformed[64] = { 0xaa, 0x00, 'O', 'K' };
        memset(malformed + 4, 'X', 56);
        memcpy(malformed + 60, tail, sizeof(tail));
        receive(malformed, sizeof(malformed));
        busy = 0;
        tick(3);
        assert(last_state == TJC_HANDSHAKE && frames == 1);
    } else {
        command_tjc_lcd_start(start); // duplicate start does not reset it
        assert(frames == 1);
        reply();
        finish_clear();
        if (!strcmp(argv[1], "manual_clear")) {
            uint32_t clear[] = { 7, 0xabcd };
            command_tjc_lcd_clear(clear);
            tick(1);
            assert(tx[3][1] == 0x40 && tx[3][4] == 0xab);
            assert(tx[3][5] == 0xcd && last_state == TJC_CLEARING);
        }
    }
    return 0;
}
