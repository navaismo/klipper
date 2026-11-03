// Native generic drawing tests with a simulated UART and scheduler
// Copyright (C) 2026
// This file may be distributed under the terms of the GNU GPLv3 license.
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include "tjc_draw.c"

static uint32_t now, previous;
static struct timer *active_timer;
static unsigned int state, error, offset, stopped, frames, busy;
static unsigned int claim = 1, uart_error, no_reply, stall;
static uint8_t *input;
static FILE *trace;

uint32_t timer_read_time(void) { return now; }
void sched_add_timer(struct timer *t) { assert(!active_timer); active_timer=t; }
void sched_del_timer(struct timer *t) { active_timer=NULL; }
void sched_wake_task(struct task_wake *w) { w->wake=1; }
uint8_t sched_check_wake(struct task_wake *w)
{ uint8_t value=w->wake; w->wake=0; return value; }
void *command_decode_ptr(uint32_t value) { return input; }
uint_fast8_t tjc_uart_busy(void) { return busy || stall; }
uint_fast8_t tjc_uart_error(void) { return uart_error; }
uint_fast8_t tjc_lcd_job_claim(uint8_t oid) { return claim; }
void tjc_lcd_job_release(uint8_t err) { assert(!busy || err); stopped=1; }
uint_fast8_t tjc_lcd_fence_start(void) { return 1; }
uint_fast8_t tjc_lcd_fence_ready(void) { return !no_reply; }

void test_sendf(const char *fmt, ...)
{
    va_list args;
    va_start(args, fmt);
    assert(va_arg(args, unsigned int) == 7);
    assert(va_arg(args, unsigned int) == 42);
    offset=va_arg(args, unsigned int);
    state=va_arg(args, unsigned int);
    error=va_arg(args, unsigned int);
    va_end(args);
}

uint_fast8_t tjc_lcd_send_frame(uint8_t code, const uint8_t *p, uint8_t len)
{
    assert(!busy && !stopped && len <= TJC_FRAME_DATA_MAX);
    if (frames && frames % 10 == 0)
        assert((uint32_t)(now-previous) >= 16000);
    previous=now;
    frames++;
    busy=2;
    if (trace) {
        fprintf(trace, "%02x", code);
        for (unsigned int i=0; i<len; i++) fprintf(trace, "%02x", p[i]);
        fputc('\n', trace);
    }
    return 1;
}

static void tick(void)
{
    now += 1000;
    if (busy) busy--;
    if (active_timer) {
        active_timer->func(active_timer);
        tjc_draw_task();
    }
}

int main(int argc, char **argv)
{
    assert(argc == 5);
    FILE *f=fopen(argv[1], "rb");
    assert(f);
    uint8_t data[17000];
    unsigned int size=fread(data, 1, sizeof(data), f);
    fclose(f);
    trace=fopen(argv[4], "w");
    assert(trace);
    const char *scenario=argv[2];
    uint32_t crc=strtoul(argv[3], NULL, 10);
    if (!strcmp(scenario, "crc")) crc ^= 1;
    if (!strcmp(scenario, "busy")) claim=0;
    if (!strcmp(scenario, "rollover")) now=UINT32_MAX-10000;
    unsigned int total=0;
    for (unsigned int pos=0; pos<size; pos+=data[pos]+1) total+=data[pos];
    uint32_t begin[]={7, 42, total, crc};
    command_tjc_lcd_draw_begin(begin);
    if (!strcmp(scenario, "busy")) {
        assert(error == E_BUSY && !frames && !active_timer);
        return 0;
    }
    if (!strcmp(scenario, "abort")) {
        command_tjc_lcd_draw_abort(begin);
        assert(error == E_ABORT && stopped && !active_timer);
        return 0;
    }
    if (!strcmp(scenario, "timeout")) {
        for (unsigned int i=0; i<5001; i++) tick();
        assert(error == E_TIMEOUT && stopped && !active_timer);
        return 0;
    }
    if (!strcmp(scenario, "early_end")) {
        command_tjc_lcd_draw_end(begin);
        assert(error == E_OFFSET && stopped && !active_timer);
        return 0;
    }
    for (unsigned int pos=0; pos<size;) {
        unsigned int len=data[pos++];
        assert(pos+len <= size);
        input=data+pos;
        uint32_t op[]={7, 42, offset, len, 0};
        if (!strcmp(scenario, "offset")) op[2]++;
        if (!strcmp(scenario, "stale")) {
            unsigned int before=frames;
            op[1]--;
            command_tjc_lcd_draw_op(op);
            assert(draw.phase == D_WAIT && frames == before && !error);
            op[1]++;
        }
        command_tjc_lcd_draw_op(op);
        if (!strcmp(scenario, "uart")) uart_error=1;
        if (!strcmp(scenario, "stall")) stall=1;
        unsigned int limit=0;
        while (draw.active && draw.phase != D_WAIT && ++limit < 6000) tick();
        assert(limit < 6000);
        if (error) break;
        assert(state == 1 && !busy);
        pos+=len;
    }
    if (!strcmp(scenario, "invalid") || !strcmp(scenario, "offset")
        || !strcmp(scenario, "uart") || !strcmp(scenario, "stall")) {
        assert(error == (!strcmp(scenario, "invalid") ? E_FORMAT
                         : !strcmp(scenario, "offset") ? E_OFFSET
                         : !strcmp(scenario, "uart") ? E_UART : E_TIMEOUT));
        assert(stopped && !active_timer);
        return 0;
    }
    assert(!error && offset == total);
    no_reply=!strcmp(scenario, "fence_timeout");
    command_tjc_lcd_draw_end(begin);
    unsigned int limit=0;
    while (draw.active && ++limit < 6000) tick();
    assert(limit < 6000 && stopped && !active_timer);
    assert(error == (!strcmp(scenario, "crc") ? E_CRC
                     : no_reply ? E_TIMEOUT : E_NONE));
    assert(error || state == 2);
    fclose(trace);
    return 0;
}
