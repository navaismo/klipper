// Bounded coordinate-based drawing for the mainboard-connected TJC LCD
// Copyright (C) 2026
// This file may be distributed under the terms of the GNU GPLv3 license.

#include <string.h>
#include "board/misc.h"
#include "command.h"
#include "sched.h"
#include "tjc_lcd.h"
#include "tjc_uart.h"

DECL_CONSTANT("TJC_CANVAS_VERSION", 1);

enum { READY=1, DONE, ERROR };
// Match the existing job error codes used by the host lifecycle.
enum { E_BUSY=1, E_FORMAT, E_INDEX, E_UART=5, E_TIMEOUT, E_ABORT };
enum { BEGIN, RECT, TEXT, END };
enum { PALETTE, DRAW, DRAIN, FENCE, REPLY, SETTLE };

static struct {
    struct timer timer;
    uint32_t token, deadline, next_frame;
    uint16_t index, color, background, palette, x0, y0, x1, y1;
    uint8_t data[36], len, font, oid, active, pending, operation, phase;
    uint8_t clear, color_valid, batch;
} canvas;
static struct task_wake canvas_wake;

static void
report(uint8_t oid, uint32_t token, uint16_t index,
       uint8_t state, uint8_t error)
{
    sendf("tjc_lcd_canvas_result oid=%c token=%u index=%hu state=%c error=%c",
          oid, token, index, state, error);
}

static void
finish(uint8_t error)
{
    sched_del_timer(&canvas.timer);
    canvas.active = canvas.pending = 0;
    tjc_lcd_job_release(error);
    report(canvas.oid, canvas.token, canvas.index,
           error ? ERROR : DONE, error);
}

static uint_fast8_t
canvas_event(struct timer *timer)
{
    sched_wake_task(&canvas_wake);
    timer->waketime += timer_from_us(1000);
    return SF_RESCHEDULE;
}

static uint_fast8_t
frame(uint8_t opcode, const uint8_t *data, uint8_t len)
{
    if (!tjc_lcd_send_frame(opcode, data, len)) {
        finish(E_UART);
        return 0;
    }
    if (++canvas.batch == 10) {
        canvas.batch = 0;
        canvas.next_frame = timer_read_time() + timer_from_us(16000);
    }
    return 1;
}

static void
put16(uint8_t *p, uint16_t v)
{
    p[0] = v >> 8;
    p[1] = v;
}

static uint_fast8_t
request(uint32_t *args)
{
    if (!canvas.active || args[0] != canvas.oid || args[1] != canvas.token) {
        report(args[0], args[1], args[2], ERROR, E_BUSY);
        return 0;
    }
    if (canvas.pending || args[2] != canvas.index+1u || args[2] > 512) {
        finish(E_INDEX);
        return 0;
    }
    canvas.index = args[2];
    canvas.pending = 1;
    canvas.deadline = timer_read_time() + timer_from_us(5000000);
    return 1;
}

void
command_tjc_lcd_canvas_begin(uint32_t *args)
{
    if (args[2] > 1) {
        report(args[0], args[1], 0, ERROR, E_FORMAT);
        return;
    }
    if (canvas.active || !tjc_lcd_job_claim(args[0])) {
        report(args[0], args[1], 0, ERROR, E_BUSY);
        return;
    }
    memset(&canvas, 0, sizeof(canvas));
    canvas.oid = args[0];
    canvas.token = args[1];
    canvas.clear = args[2];
    canvas.color = args[3];
    canvas.background = args[3];
    canvas.active = canvas.pending = 1;
    canvas.phase = canvas.clear ? PALETTE : DRAIN;
    // Zero is not a valid immediate deadline at every phase of a 32-bit clock.
    canvas.next_frame = timer_read_time();
    canvas.deadline = canvas.next_frame + timer_from_us(5000000);
    canvas.timer.func = canvas_event;
    canvas.timer.waketime = timer_read_time() + timer_from_us(1000);
    sched_add_timer(&canvas.timer);
}
DECL_COMMAND(command_tjc_lcd_canvas_begin,
             "tjc_lcd_canvas_begin oid=%c token=%u clear=%c color=%hu");

void
command_tjc_lcd_canvas_rect(uint32_t *args)
{
    if (!request(args))
        return;
    if (canvas.index > 511) {
        finish(E_INDEX);
        return;
    }
    if (args[3] > args[5] || args[4] > args[6]
        || args[5] >= 240 || args[6] >= 320) {
        finish(E_FORMAT);
        return;
    }
    canvas.x0 = args[3]; canvas.y0 = args[4];
    canvas.x1 = args[5]; canvas.y1 = args[6];
    canvas.color = args[7];
    canvas.operation = RECT;
    canvas.phase = PALETTE;
}
DECL_COMMAND(command_tjc_lcd_canvas_rect,
             "tjc_lcd_canvas_rect oid=%c token=%u index=%hu"
             " x0=%hu y0=%hu x1=%hu y1=%hu color=%hu");

void
command_tjc_lcd_canvas_text(uint32_t *args)
{
    if (!request(args))
        return;
    if (canvas.index > 511) {
        finish(E_INDEX);
        return;
    }
    uint32_t font = args[5], len = args[8];
    const uint8_t *data = command_decode_ptr(args[9]);
    uint32_t width = font == 1 ? 8 : 12, height = font == 1 ? 16 : 24;
    if ((font != 1 && font != 3) || !len || len > sizeof(canvas.data)
        || args[3]+len*width > 240 || args[4]+height > 320) {
        finish(E_FORMAT);
        return;
    }
    for (uint_fast8_t i=0; i<len; i++) {
        if (data[i] >= 32 && data[i] <= 126)
            continue;
        if (data[i] < 0x81 || data[i] > 0xfe || ++i == len
            || data[i] < 0x40 || data[i] > 0xfe || data[i] == 0x7f) {
            finish(E_FORMAT);
            return;
        }
    }
    canvas.x0 = args[3]; canvas.y0 = args[4];
    canvas.font = font;
    canvas.len = len;
    canvas.color = args[6]; canvas.background = args[7];
    memcpy(canvas.data, data, len);
    canvas.operation = TEXT;
    canvas.phase = DRAW;
}
DECL_COMMAND(command_tjc_lcd_canvas_text,
             "tjc_lcd_canvas_text oid=%c token=%u index=%hu"
             " x=%hu y=%hu font=%c color=%hu background=%hu text=%*s");

void
command_tjc_lcd_canvas_end(uint32_t *args)
{
    if (request(args)) {
        canvas.operation = END;
        canvas.phase = FENCE;
    }
}
DECL_COMMAND(command_tjc_lcd_canvas_end,
             "tjc_lcd_canvas_end oid=%c token=%u index=%hu");

void
command_tjc_lcd_canvas_abort(uint32_t *args)
{
    if (canvas.active && args[0] == canvas.oid && args[1] == canvas.token)
        finish(E_ABORT);
}
DECL_COMMAND(command_tjc_lcd_canvas_abort,
             "tjc_lcd_canvas_abort oid=%c token=%u");

void
tjc_canvas_task(void)
{
    if (!sched_check_wake(&canvas_wake) || !canvas.active)
        return;
    uint32_t now = timer_read_time();
    if (tjc_uart_error()) {
        finish(E_UART);
        return;
    }
    if (!timer_is_before(now, canvas.deadline)) {
        finish(E_TIMEOUT);
        return;
    }
    if (!canvas.pending || tjc_uart_busy()
        || timer_is_before(now, canvas.next_frame))
        return;
    switch (canvas.phase) {
    case PALETTE:
        if (canvas.color_valid && canvas.palette == canvas.color) {
            canvas.phase = DRAW;
            return;
        }
        uint8_t palette[] = {canvas.color >> 8, canvas.color,
                             canvas.background >> 8, canvas.background};
        if (frame(0x40, palette, sizeof(palette))) {
            canvas.palette = canvas.color;
            canvas.color_valid = 1;
            canvas.phase = DRAW;
        }
        break;
    case DRAW: {
        uint8_t data[47];
        if (canvas.operation == BEGIN) {
            if (frame(0x52, NULL, 0)) {
                canvas.phase = DRAIN;
                canvas.next_frame = now + timer_from_us(50000);
            }
        } else if (canvas.operation == RECT) {
            put16(data, canvas.x0); put16(data+2, canvas.y0);
            put16(data+4, canvas.x1); put16(data+6, canvas.y1);
            if (frame(0x5b, data, 8))
                canvas.phase = DRAIN;
        } else {
            put16(data, canvas.x0); put16(data+2, canvas.y0);
            data[4] = 0; data[5] = 0x42; data[6] = canvas.font;
            put16(data+7, canvas.color); put16(data+9, canvas.background);
            memcpy(data+11, canvas.data, canvas.len);
            if (frame(0x98, data, 11+canvas.len)) {
                canvas.color_valid = 0;
                canvas.phase = DRAIN;
            }
        }
        break;
    }
    case DRAIN:
        canvas.pending = 0;
        report(canvas.oid, canvas.token, canvas.index, READY, 0);
        break;
    case FENCE:
        if (tjc_lcd_fence_start())
            canvas.phase = REPLY;
        else
            finish(E_UART);
        break;
    case REPLY:
        if (tjc_lcd_fence_ready()) {
            canvas.next_frame = now + timer_from_us(50000);
            canvas.phase = SETTLE;
        }
        break;
    case SETTLE:
        finish(0);
        break;
    }
}
DECL_TASK(tjc_canvas_task);

void
tjc_canvas_shutdown(void)
{
    if (canvas.active) {
        sched_del_timer(&canvas.timer);
        canvas.active = canvas.pending = 0;
        tjc_lcd_job_release(1);
    }
}
DECL_SHUTDOWN(tjc_canvas_shutdown);
