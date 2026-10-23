// TJC startup handshake and clear, using the owner's Marlin reference
// Packet/drawing reference: MarlinFirmware (C) 2020, LEO / Creality3D.
// Copyright (C) 2020 MarlinFirmware
//
// This file may be distributed under the terms of the GNU GPLv3 license.

#include <string.h> // memcpy
#include "basecmd.h" // oid_alloc
#include "board/misc.h" // timer_read_time
#include "command.h" // DECL_COMMAND
#include "sched.h" // sched_wake_task
#include "tjc_uart.h" // tjc_uart_send
#include "tjc_lcd.h" // TJC_FRAME_DATA_MAX

DECL_CONSTANT("TJC_LCD_VERSION", 1);

enum { TJC_IDLE, TJC_HANDSHAKE, TJC_CLEARING, TJC_READY, TJC_ERROR, TJC_JOB };
enum { ERR_NONE, ERR_TIMEOUT, ERR_UART };
enum { PH_BOOT, PH_REPLY, PH_PALETTE, PH_CLEAR, PH_DRAIN, PH_SETTLE };

struct tjc_lcd {
    struct timer timer;
    uint32_t startup_ticks, timeout_ticks, deadline;
    uint16_t color;
    uint8_t oid, state, phase, attempts, prefix_pos, tail_pos, reply_len;
};

static struct tjc_lcd *lcd;
static struct task_wake tjc_wake;
static const uint8_t tail[] = { 0xcc, 0x33, 0xc3, 0x3c };
static const uint8_t reply_prefix[] = { 0xaa, 0x00, 'O', 'K' };

static void
report_state(uint8_t error)
{
    sendf("tjc_lcd_state oid=%c state=%c error=%c",
          lcd->oid, lcd->state, error);
}

static void
finish(uint8_t error)
{
    sched_del_timer(&lcd->timer);
    tjc_uart_stop();
    lcd->state = error ? TJC_ERROR : TJC_READY;
    report_state(error);
}

// Frame construction and LCD opcodes belong to the MCU, never the host.
uint_fast8_t
tjc_lcd_send_frame(uint8_t opcode, const uint8_t *data, uint8_t len)
{
    uint8_t frame[TJC_FRAME_DATA_MAX + 6] = { 0xaa, opcode };
    if (len > sizeof(frame) - 6)
        return 0;
    if (len)
        memcpy(frame + 2, data, len);
    memcpy(frame + 2 + len, tail, sizeof(tail));
    return tjc_uart_send(frame, len + 6);
}

static void
start_handshake(uint32_t now)
{
    lcd->attempts++;
    lcd->prefix_pos = lcd->tail_pos = lcd->reply_len = 0;
    tjc_uart_reset_rx();
    if (!tjc_lcd_send_frame(0x00, NULL, 0)) {
        finish(ERR_UART);
        return;
    }
    lcd->phase = PH_REPLY;
    lcd->deadline = now + lcd->timeout_ticks;
}

// Accept an entire bounded reply, including the tail (optional version text).
static uint_fast8_t
read_reply(void)
{
    uint_fast8_t budget = 32;
    int data;
    while (budget-- && (data = tjc_uart_read()) >= 0) {
        if (lcd->prefix_pos < sizeof(reply_prefix)) {
            if (data == reply_prefix[lcd->prefix_pos])
                lcd->prefix_pos++;
            else
                lcd->prefix_pos = data == reply_prefix[0];
            lcd->tail_pos = lcd->reply_len = 0;
            continue;
        }
        if (++lcd->reply_len > 48) {
            lcd->prefix_pos = lcd->tail_pos = 0;
            continue;
        }
        if (data == tail[lcd->tail_pos]) {
            if (++lcd->tail_pos == sizeof(tail))
                return 1;
        } else
            lcd->tail_pos = data == tail[0];
    }
    return 0;
}

static uint_fast8_t
tjc_event(struct timer *timer)
{
    sched_wake_task(&tjc_wake);
    timer->waketime += timer_from_us(5000);
    return SF_RESCHEDULE;
}

static void
start_timer(void)
{
    lcd->timer.waketime = timer_read_time() + timer_from_us(5000);
    sched_add_timer(&lcd->timer);
}

void
command_config_tjc_lcd(uint32_t *args)
{
    if (lcd)
        shutdown("Only one TJC LCD is supported");
    uint32_t startup = args[1], timeout = args[2];
    if (startup > timer_from_us(5000000)
        || timeout < timer_from_us(100000)
        || timeout > timer_from_us(2000000))
        shutdown("Invalid TJC LCD timing");
    lcd = oid_alloc(args[0], command_config_tjc_lcd, sizeof(*lcd));
    lcd->oid = args[0];
    lcd->startup_ticks = startup;
    lcd->timeout_ticks = timeout;
    lcd->color = args[3];
    lcd->timer.func = tjc_event;
    tjc_uart_setup();
}
DECL_COMMAND(command_config_tjc_lcd,
             "config_tjc_lcd oid=%c startup_ticks=%u timeout_ticks=%u"
             " color=%hu");

void
command_tjc_lcd_start(uint32_t *args)
{
    oid_lookup(args[0], command_config_tjc_lcd);
    if (lcd->state == TJC_HANDSHAKE || lcd->state == TJC_CLEARING
        || lcd->state == TJC_JOB) {
        report_state(ERR_NONE);
        return;
    }
    lcd->state = TJC_HANDSHAKE;
    lcd->phase = PH_BOOT;
    lcd->attempts = 0;
    tjc_uart_start();
    lcd->deadline = timer_read_time() + lcd->startup_ticks;
    start_timer();
    report_state(ERR_NONE);
}
DECL_COMMAND(command_tjc_lcd_start, "tjc_lcd_start oid=%c");

void
command_tjc_lcd_clear(uint32_t *args)
{
    oid_lookup(args[0], command_config_tjc_lcd);
    if (lcd->state != TJC_READY) {
        report_state(ERR_NONE);
        return;
    }
    lcd->color = args[1];
    lcd->state = TJC_CLEARING;
    lcd->phase = PH_PALETTE;
    tjc_uart_start();
    lcd->deadline = timer_read_time() + lcd->timeout_ticks;
    start_timer();
    report_state(ERR_NONE);
}
DECL_COMMAND(command_tjc_lcd_clear, "tjc_lcd_clear oid=%c color=%hu");

void
tjc_lcd_task(void)
{
    if (!sched_check_wake(&tjc_wake) || !lcd
        || (lcd->state != TJC_HANDSHAKE && lcd->state != TJC_CLEARING))
        return;
    uint32_t now = timer_read_time();
    if (lcd->phase == PH_BOOT) {
        if (!timer_is_before(now, lcd->deadline))
            start_handshake(now);
        return;
    }
    if (tjc_uart_error()) {
        finish(ERR_UART);
        return;
    }
    if (lcd->phase == PH_REPLY) {
        if (read_reply()) {
            lcd->state = TJC_CLEARING;
            lcd->phase = PH_PALETTE;
            lcd->deadline = now + lcd->timeout_ticks;
            report_state(ERR_NONE);
        } else if (!timer_is_before(now, lcd->deadline)) {
            if (lcd->attempts < 3)
                start_handshake(now);
            else
                finish(ERR_TIMEOUT);
        }
        return;
    }
    if (lcd->phase == PH_SETTLE) {
        if (!timer_is_before(now, lcd->deadline))
            finish(ERR_NONE);
        return;
    }
    if (!timer_is_before(now, lcd->deadline)) {
        finish(ERR_TIMEOUT);
        return;
    }
    if (tjc_uart_busy())
        return;
    if (lcd->phase == PH_PALETTE) {
        uint8_t palette[] = { 0xff, 0xff, lcd->color >> 8, lcd->color };
        if (!tjc_lcd_send_frame(0x40, palette, sizeof(palette))) {
            finish(ERR_UART);
            return;
        }
        lcd->phase = PH_CLEAR;
    } else if (lcd->phase == PH_CLEAR) {
        if (!tjc_lcd_send_frame(0x52, NULL, 0)) {
            finish(ERR_UART);
            return;
        }
        lcd->phase = PH_DRAIN;
    } else if (lcd->phase == PH_DRAIN) {
        lcd->phase = PH_SETTLE;
        // Wire completion plus settling time is not a render acknowledgement.
        lcd->deadline = now + timer_from_us(50000);
    }
}
DECL_TASK(tjc_lcd_task);

uint_fast8_t
tjc_lcd_job_claim(uint8_t oid)
{
    oid_lookup(oid, command_config_tjc_lcd);
    if (lcd->state != TJC_READY)
        return 0;
    lcd->state = TJC_JOB;
    tjc_uart_start();
    return 1;
}

void
tjc_lcd_job_release(uint8_t error)
{
    if (lcd) {
        lcd->state = error ? TJC_ERROR : TJC_READY;
        tjc_uart_stop();
    }
}

uint_fast8_t
tjc_lcd_fence_start(void)
{
    lcd->prefix_pos = lcd->tail_pos = lcd->reply_len = 0;
    tjc_uart_reset_rx();
    return tjc_lcd_send_frame(0x00, NULL, 0);
}

uint_fast8_t
tjc_lcd_fence_ready(void)
{
    return read_reply();
}

void
tjc_lcd_shutdown(void)
{
    if (!lcd)
        return;
    sched_del_timer(&lcd->timer);
    lcd->state = TJC_IDLE;
    tjc_uart_stop();
    lcd = NULL;
}
DECL_SHUTDOWN(tjc_lcd_shutdown);
