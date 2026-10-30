// Native semantic canvas tests; no printer hardware
// Copyright (C) 2026
// This file may be distributed under the terms of the GNU GPLv3 license.
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "tjc_canvas.c"

static uint32_t now;
static struct timer *active_timer;
static unsigned int state, error, last_index, busy, clears, frames, stopped;
static unsigned int claim=1, uart_error, no_reply, reserved;
static uint16_t panel[320][240], saved[320][240], color, background;
static uint8_t input[100];

uint32_t timer_read_time(void) { return now; }
void sched_add_timer(struct timer *t) { assert(!active_timer); active_timer=t; }
void sched_del_timer(struct timer *t) { active_timer=NULL; }
void sched_wake_task(struct task_wake *w) { w->wake=1; }
uint8_t sched_check_wake(struct task_wake *w)
{ uint8_t v=w->wake; w->wake=0; return v; }
void *command_decode_ptr(uint32_t value) { return input; }
uint_fast8_t tjc_lcd_job_claim(uint8_t oid)
{ if (!claim || reserved) return 0; reserved=1; stopped=0; return 1; }
void tjc_lcd_job_release(uint8_t e) { reserved=0; stopped=1; }
uint_fast8_t tjc_uart_busy(void) { return busy != 0; }
uint_fast8_t tjc_uart_error(void) { return uart_error; }
uint_fast8_t tjc_lcd_fence_start(void) { return 1; }
uint_fast8_t tjc_lcd_fence_ready(void) { return !no_reply; }
void test_sendf(const char *format, ...)
{
    va_list args;
    va_start(args, format);
    assert(va_arg(args, unsigned int) == 7);
    unsigned int token=va_arg(args, unsigned int);
    assert(token == 42 || token == 41);
    last_index=va_arg(args, unsigned int);
    state=va_arg(args, unsigned int);
    error=va_arg(args, unsigned int);
    va_end(args);
}
static unsigned int be16(const uint8_t *p) { return (p[0]<<8)|p[1]; }
uint_fast8_t tjc_lcd_send_frame(uint8_t op, const uint8_t *p, uint8_t len)
{
    assert(!busy && reserved && !stopped && len <= TJC_FRAME_DATA_MAX);
    frames++;
    busy=2;
    if (op == 0x40) {
        assert(len == 4);
        color=be16(p); background=be16(p+2);
    } else if (op == 0x52) {
        assert(!len);
        clears++;
        for (unsigned int y=0; y<320; y++)
            for (unsigned int x=0; x<240; x++) panel[y][x]=background;
    } else if (op == 0x5b) {
        assert(len == 8);
        unsigned int x0=be16(p), y0=be16(p+2), x1=be16(p+4), y1=be16(p+6);
        assert(x0 <= x1 && y0 <= y1 && x1 < 240 && y1 < 320);
        for (unsigned int y=y0; y<=y1; y++)
            for (unsigned int x=x0; x<=x1; x++) panel[y][x]=color;
    } else if (op == 0x98) {
        unsigned int width=p[6] == 1 ? 8 : 12, height=p[6] == 1 ? 16 : 24;
        assert(len > 11 && p[4] == 0 && p[5] == 0x42);
        assert(be16(p)+(len-11)*width <= 240 && be16(p+2)+height <= 320);
        // Some real panels also change the active palette during text draw.
        color=be16(p+7);
    } else assert(0);
    return 1;
}

static void tick(void)
{
    now+=1000;
    if (busy) busy--;
    if (active_timer) {
        active_timer->func(active_timer);
        tjc_canvas_task();
    }
}
static void ready(void)
{
    for (unsigned int i=0; i<6000 && canvas.active && canvas.pending; i++)
        tick();
    assert(!canvas.pending);
}
static void begin(unsigned int clear)
{
    uint32_t args[]={7,42,clear,0x0022};
    uint32_t started=now;
    command_tjc_lcd_canvas_begin(args);
    ready();
    assert(state == READY && !error && last_index == 0);
    assert((uint32_t)(now-started) < 100000);
}
static void end(void)
{
    uint32_t args[]={7,42,canvas.index+1};
    command_tjc_lcd_canvas_end(args);
    ready();
    assert(!canvas.active && !active_timer && stopped && !reserved);
}
static void draw_file(const char *path)
{
    FILE *file=fopen(path,"r");
    assert(file);
    char line[512], hex[201], kind[16];
    unsigned int a,b,c,d,e,f;
    while (fgets(line,sizeof(line),file)) {
        assert(sscanf(line,"%15s",kind) == 1);
        if (!strcmp(kind,"rect")) {
            assert(sscanf(line,"%*s %u %u %u %u %u",&a,&b,&c,&d,&e) == 5);
            uint32_t args[]={7,42,canvas.index+1,a,b,c,d,e};
            command_tjc_lcd_canvas_rect(args);
        } else {
            assert(sscanf(line,"%*s %u %u %u %u %u %200s",
                          &a,&b,&c,&d,&e,hex) == 6);
            unsigned int len=strlen(hex)/2;
            assert(len < sizeof(input));
            for (unsigned int i=0; i<len; i++) {
                assert(sscanf(hex+2*i,"%2x",&f) == 1);
                input[i]=f;
            }
            uint32_t args[]={7,42,canvas.index+1,a,b,c,d,e,len,0};
            command_tjc_lcd_canvas_text(args);
        }
        ready();
        assert(state == READY && !error);
        if (!strcmp(kind,"rect"))
            assert(panel[b][a] == e && panel[d][c] == e);
    }
    fclose(file);
}

int main(int argc, char **argv)
{
    assert(argc >= 2);
    const char *scenario=argv[1];
    if (!strcmp(scenario,"busy")) {
        claim=0;
        uint32_t args[]={7,42,1,0};
        command_tjc_lcd_canvas_begin(args);
        assert(error == E_BUSY && !canvas.active && !frames);
        return 0;
    }
    if (!strcmp(scenario,"rollover")) now=0xffff0000;
    if (!strcmp(scenario,"late_start")) now=0x90000000;
    begin(1);
    if (!strcmp(scenario,"draw") || !strcmp(scenario,"rollover")
        || !strcmp(scenario,"late_start") || !strcmp(scenario,"late_patch")) {
        assert(argc == 4);
        draw_file(argv[2]);
        end();
        assert(!error && clears == 1);
        memcpy(saved,panel,sizeof(panel));
        // Simulate warm-up/homing between the initial screen and a highlight.
        if (!strcmp(scenario,"late_patch")) now=0x90000000;
        begin(0);
        draw_file(argv[3]);
        end();
        assert(!error && clears == 1);
        // Partial updates must leave the illustration and title untouched.
        for (unsigned int y=25; y<200; y++)
            for (unsigned int x=0; x<240; x++)
                assert(saved[y][x] == panel[y][x]);
    } else if (!strcmp(scenario,"fence_timeout")) {
        no_reply=1; end(); assert(error == E_TIMEOUT);
    } else if (!strcmp(scenario,"abort")) {
        uint32_t args[]={7,42};
        command_tjc_lcd_canvas_abort(args);
        assert(error == E_ABORT && !canvas.active && stopped);
    } else if (!strcmp(scenario,"shutdown")) {
        tjc_canvas_shutdown();
        assert(!canvas.active && stopped && !active_timer);
    } else if (!strcmp(scenario,"timeout")) {
        for (unsigned int i=0; i<6000; i++) tick();
        assert(error == E_TIMEOUT && !canvas.active && stopped);
    } else if (!strcmp(scenario,"uart_error")) {
        uart_error=1; tick(); assert(error == E_UART && stopped);
    } else if (!strcmp(scenario,"stale")) {
        uint32_t args[]={7,41,1,0,0,4,4,0xffff};
        unsigned int before=frames;
        command_tjc_lcd_canvas_rect(args);
        assert(error == E_BUSY && frames == before && canvas.active);
        end(); assert(!error);
    } else if (!strcmp(scenario,"sequence")) {
        uint32_t args[]={7,42,2,0,0,4,4,0xffff};
        command_tjc_lcd_canvas_rect(args); assert(error == E_INDEX && stopped);
    } else if (!strcmp(scenario,"bounds")) {
        unsigned int before=frames;
        uint32_t args[]={7,42,1,0,0,240,319,0xffff};
        command_tjc_lcd_canvas_rect(args);
        assert(error == E_FORMAT && frames == before && stopped);
    } else if (!strcmp(scenario,"text_bounds")) {
        memset(input,'X',sizeof(input));
        uint32_t args[]={7,42,1,232,305,1,0xffff,0,2,0};
        command_tjc_lcd_canvas_text(args); assert(error == E_FORMAT && stopped);
    } else if (!strcmp(scenario,"gbk")) {
        input[0]=0x81;
        uint32_t args[]={7,42,1,0,0,1,0xffff,0,1,0};
        command_tjc_lcd_canvas_text(args); assert(error == E_FORMAT && stopped);
    } else assert(0);
    unsigned int before=frames;
    for (unsigned int i=0; i<10000; i++) tick();
    assert(frames == before && !active_timer);
    return 0;
}
