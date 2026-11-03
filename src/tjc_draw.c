// Bounded generic drawing executor; all designs and assets belong to the host
// Copyright (C) 2026
// This file may be distributed under the terms of the GNU GPLv3 license.
#include <string.h>
#include "board/misc.h"
#include "command.h"
#include "sched.h"
#include "tjc_lcd.h"
#include "tjc_uart.h"

DECL_CONSTANT("TJC_DRAW_VERSION", 1);

enum { D_WAIT, D_PALETTE, D_DRAW, D_DRAIN, D_ACK, D_FENCE, D_REPLY, D_DONE };
enum { E_NONE, E_BUSY, E_FORMAT, E_OFFSET, E_CRC, E_UART, E_TIMEOUT, E_ABORT };

static struct {
    struct timer timer;
    uint32_t token, crc, expected_crc, deadline, next_frame;
    uint16_t size, offset;
    uint8_t oid, active, phase, length, batch, op[40];
} draw;
static struct task_wake draw_wake;

static uint16_t
word(const uint8_t *p)
{
    return (p[0] << 8) | p[1];
}

static void
result(uint8_t state, uint8_t error)
{
    sendf("tjc_lcd_draw_result oid=%c token=%u offset=%hu state=%c error=%c",
          draw.oid, draw.token, draw.offset, state, error);
}

static void
finish(uint8_t error)
{
    sched_del_timer(&draw.timer);
    draw.active = 0;
    tjc_lcd_job_release(error);
    result(error ? 3 : 2, error);
}

static uint_fast8_t
draw_event(struct timer *timer)
{
    sched_wake_task(&draw_wake);
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
    if (++draw.batch == 10) {
        draw.batch = 0;
        draw.next_frame = timer_read_time() + timer_from_us(16000);
    }
    return 1;
}

static uint_fast8_t
valid_op(const uint8_t *p, uint8_t len)
{
    if (p[0] == 1)
        return len == 3;
    if (p[0] == 2)
        return len == 11 && word(p+3) <= word(p+7)
            && word(p+5) <= word(p+9)
            && word(p+7) < 240 && word(p+9) < 320;
    if (p[0] != 3 || len <= 10 || (p[1] != 1 && p[1] != 3))
        return 0;
    uint8_t width = p[1] == 1 ? 8 : 12, height = p[1] == 1 ? 16 : 24;
    if (word(p+6) + (len-10)*width > 240 || word(p+8) + height > 320)
        return 0;
    for (uint8_t pos=10; pos<len; pos++) {
        uint8_t c = p[pos];
        if (c >= 0x81 && c <= 0xfe) {
            if (++pos == len || p[pos] < 0x40 || p[pos] > 0xfe
                || p[pos] == 0x7f)
                return 0;
        } else if (c < 0x20 || c >= 0x7f)
            return 0;
    }
    return 1;
}

void
command_tjc_lcd_draw_begin(uint32_t *args)
{
    if (draw.active || !args[2] || args[2] > 16000
        || !tjc_lcd_job_claim(args[0])) {
        sendf("tjc_lcd_draw_result oid=%c token=%u offset=%hu"
              " state=%c error=%c",
              args[0], args[1], 0, 3, E_BUSY);
        return;
    }
    memset(&draw, 0, sizeof(draw));
    draw.oid = args[0];
    draw.token = args[1];
    draw.size = args[2];
    draw.expected_crc = args[3];
    draw.crc = 0xffffffffu;
    draw.active = 1;
    draw.deadline = timer_read_time() + timer_from_us(5000000);
    draw.next_frame = timer_read_time();
    draw.timer.func = draw_event;
    draw.timer.waketime = timer_read_time() + timer_from_us(1000);
    sched_add_timer(&draw.timer);
    result(1, E_NONE);
}
DECL_COMMAND(command_tjc_lcd_draw_begin,
             "tjc_lcd_draw_begin oid=%c token=%u size=%hu crc=%u");

void
command_tjc_lcd_draw_op(uint32_t *args)
{
    if (!draw.active || args[0] != draw.oid || args[1] != draw.token)
        return;
    uint32_t len = args[3];
    if (draw.phase != D_WAIT || args[2] != draw.offset
        || !len || len > sizeof(draw.op) || draw.offset + len > draw.size) {
        finish(E_OFFSET);
        return;
    }
    const uint8_t *p = command_decode_ptr(args[4]);
    if (!valid_op(p, len)) {
        finish(E_FORMAT);
        return;
    }
    memcpy(draw.op, p, len);
    draw.length = len;
    for (uint8_t i=0; i<len; i++) {
        draw.crc ^= p[i];
        for (uint8_t bit=0; bit<8; bit++)
            draw.crc = (draw.crc >> 1) ^ (0xedb88320u & -(draw.crc & 1));
    }
    draw.phase = p[0] == 3 ? D_DRAW : D_PALETTE;
    draw.deadline = timer_read_time() + timer_from_us(5000000);
}
DECL_COMMAND(command_tjc_lcd_draw_op,
             "tjc_lcd_draw_op oid=%c token=%u offset=%hu data=%*s");

void
command_tjc_lcd_draw_end(uint32_t *args)
{
    if (!draw.active || args[0] != draw.oid || args[1] != draw.token)
        return;
    if (draw.phase != D_WAIT || draw.offset != draw.size) {
        finish(E_OFFSET);
        return;
    }
    if ((draw.crc ^ 0xffffffffu) != draw.expected_crc) {
        finish(E_CRC);
        return;
    }
    draw.phase = D_FENCE;
    draw.deadline = timer_read_time() + timer_from_us(5000000);
}
DECL_COMMAND(command_tjc_lcd_draw_end, "tjc_lcd_draw_end oid=%c token=%u");

void
command_tjc_lcd_draw_abort(uint32_t *args)
{
    if (draw.active && args[0] == draw.oid && args[1] == draw.token)
        finish(E_ABORT);
}
DECL_COMMAND(command_tjc_lcd_draw_abort, "tjc_lcd_draw_abort oid=%c token=%u");

void
tjc_draw_task(void)
{
    if (!sched_check_wake(&draw_wake) || !draw.active)
        return;
    uint32_t now = timer_read_time();
    if (tjc_uart_error()) {
        finish(E_UART);
        return;
    }
    if (!timer_is_before(now, draw.deadline)) {
        finish(E_TIMEOUT);
        return;
    }
    if (tjc_uart_busy() || timer_is_before(now, draw.next_frame))
        return;
    uint8_t *p = draw.op;
    switch (draw.phase) {
    case D_PALETTE: {
        uint8_t palette[] = {p[1], p[2], p[1], p[2]};
        if (frame(0x40, palette, sizeof(palette)))
            draw.phase = D_DRAW;
        break;
    }
    case D_DRAW:
        if (p[0] == 1) {
            if (frame(0x52, NULL, 0))
                draw.phase = D_DRAIN;
        } else if (p[0] == 2) {
            if (frame(0x5b, p+3, 8))
                draw.phase = D_ACK;
        } else {
            uint8_t data[41] = {p[6], p[7], p[8], p[9], 0, 0x42,
                               p[1], p[2], p[3], p[4], p[5]};
            memcpy(data+11, p+10, draw.length-10);
            if (frame(0x98, data, draw.length+1))
                draw.phase = D_ACK;
        }
        break;
    case D_DRAIN:
        draw.next_frame = now + timer_from_us(50000);
        draw.phase = D_ACK;
        break;
    case D_ACK:
        draw.offset += draw.length;
        draw.phase = D_WAIT;
        draw.deadline = now + timer_from_us(5000000);
        result(1, E_NONE);
        break;
    case D_FENCE:
        if (!tjc_lcd_fence_start())
            finish(E_UART);
        else
            draw.phase = D_REPLY;
        break;
    case D_REPLY:
        if (tjc_lcd_fence_ready()) {
            draw.next_frame = now + timer_from_us(50000);
            draw.phase = D_DONE;
        }
        break;
    case D_DONE:
        finish(E_NONE);
        break;
    }
}
DECL_TASK(tjc_draw_task);

void
tjc_draw_shutdown(void)
{
    if (draw.active) {
        sched_del_timer(&draw.timer);
        draw.active = 0;
        tjc_lcd_job_release(1);
    }
}
DECL_SHUTDOWN(tjc_draw_shutdown);
