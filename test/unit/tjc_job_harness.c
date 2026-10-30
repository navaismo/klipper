// Native streaming-render tests: actual C receiver, simulated panel/UART
// Copyright (C) 2026
// This file may be distributed under the terms of the GNU GPLv3 license.
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "tjc_job.c"

static uint32_t now;
static struct timer *active_timer;
static unsigned int last_state, last_error, last_offset;
static unsigned int stopped, busy_ticks, fills, texts, fence, frames;
static unsigned int no_reply, claim = 1;
static uint32_t previous_frame;
static uint16_t panel[320][240], color, background;
static uint8_t *input;
static uint8_t expected_text[9][64], expected_sizes[9], text_pos[9];
static FILE *trace;

uint32_t timer_read_time(void) { return now; }
void sched_add_timer(struct timer *t) { assert(!active_timer); active_timer=t; }
void sched_del_timer(struct timer *t) { active_timer = NULL; }
void sched_wake_task(struct task_wake *w) { w->wake = 1; }
uint8_t sched_check_wake(struct task_wake *w)
{ uint8_t value=w->wake; w->wake=0; return value; }
void test_sendf(const char *fmt, ...)
{
    va_list args;
    va_start(args, fmt);
    assert(va_arg(args, unsigned int) == 7);
    assert(va_arg(args, unsigned int) == 42);
    last_offset = va_arg(args, unsigned int);
    last_state = va_arg(args, unsigned int);
    last_error = va_arg(args, unsigned int);
    va_end(args);
}
void *command_decode_ptr(uint32_t value) { return input; }
uint_fast8_t tjc_uart_busy(void) { return busy_ticks != 0; }
uint_fast8_t tjc_uart_error(void) { return 0; }
uint_fast8_t tjc_lcd_job_claim(uint8_t oid) { return claim; }
void tjc_lcd_job_release(uint8_t error) { stopped = 1; }
uint_fast8_t tjc_lcd_fence_start(void) { fence++; return 1; }
uint_fast8_t tjc_lcd_fence_ready(void) { return !no_reply; }
uint_fast8_t tjc_lcd_send_frame(uint8_t op, const uint8_t *p, uint8_t len)
{
    assert(!busy_ticks && !stopped && len <= TJC_FRAME_DATA_MAX);
    if (frames && frames % 10 == 0)
        assert((uint32_t)(now - previous_frame) >= 16000);
    frames++;
    previous_frame = now;
    busy_ticks = 2;
    if (trace) {
        fprintf(trace, "%02x", op);
        for (unsigned int i=0; i<len; i++) fprintf(trace, "%02x", p[i]);
        fputc('\n', trace);
    }
    if (op == 0x40) {
        assert(len == 4);
        color = be16(p);
        background = be16(p+2);
    } else if (op == 0x52) {
        assert(!len);
        for (unsigned int y=0; y<320; y++)
            for (unsigned int x=0; x<240; x++) panel[y][x]=background;
    } else if (op == 0x98) {
        unsigned int x=be16(p), y=be16(p+2), field;
        assert(p[4] == 0 && p[5] == 0x42 && len > 11);
        assert(be16(p+9) == BACKGROUND);
        if (job.header[5] & IDLE_SCREEN) {
            unsigned int footer=expected_sizes[3] > 2;
            field = y == 102 ? 0 : y == 174 ? 1
                : y == 207 ? 2 : y == 304 ? 3 : 4;
            assert(y == 102 || y == 174 || y == 207
                   || y == (footer ? 276 : 282) || (footer && y == 304));
            unsigned int cell = field == 0 ? 20 : field == 3 ? 8
                : field == 2 ? 8 : 12;
            assert(p[6] == (field == 0 ? 6 : field == 3 ? 1
                            : field == 2 ? 1 : 3));
            assert(x == (field == 2 ? 12
                         : (240-expected_sizes[field]*cell)/2)
                   + text_pos[field]*cell);
            unsigned int c = field == 0 && !text_pos[field] ? RED
                : field == 2 || field == 3 || field == 4 ? 0x8d7b : 0xffff;
            assert(be16(p+7) == c);
        } else if (y == 296) {
            field = x < 120 ? 7 : 8;
            assert(be16(p+7) == YELLOW && p[6] == 1);
        } else if (y < 162) {
            field = (y-16)/36;
            assert(x >= 126 && p[6] == 1 && len-11 <= 14);
            unsigned int label = 0;
            while (label < expected_sizes[field]
                   && expected_text[field][label] != ':') label++;
            assert(be16(p+7) == (text_pos[field] <= label ? YELLOW : 0xffff));
        } else if (y < 226) {
            field = 4;
            assert((y == 174 || y == 192) && p[6] == 1);
            assert(len-11 <= 27 && x == (240u-(len-11)*8)/2);
            if (y == 192 && (expected_text[4][text_pos[4]] == '_'
                             || expected_text[4][text_pos[4]] == ' '))
                text_pos[4]++;
        } else {
            field = x < 120 ? 5 : 6;
            assert(y == 264 && p[6] == 3 && len-11 <= 8);
        }
        assert(field < 9 && x+(len-11)*(p[6] == 6 ? 20 : p[6] == 3 ? 12
                                     : p[6] ? 8 : 6)
               <= 240);
        assert(text_pos[field] + len-11 <= expected_sizes[field]);
        assert(!memcmp(p+11, expected_text[field]+text_pos[field], len-11));
        text_pos[field] += len-11;
        // Exercise panels where text also changes the active drawing palette.
        color = be16(p+7);
        texts++;
    } else if (op == 0x5b) {
        unsigned int x0=be16(p), y0=be16(p+2), x1=be16(p+4), y1=be16(p+6);
        assert(x1 < 240 && y1 < 320 && x0 <= x1 && y0 <= y1);
        for (unsigned int y=y0; y<=y1; y++)
            for (unsigned int x=x0; x<=x1; x++) panel[y][x]=color;
        if ((job.phase == J_FLUSH || job.phase == J_RECT)) {
            assert(x0 >= THUMB_X && x1 < THUMB_X+96);
            assert(y0 >= THUMB_Y && y1 < THUMB_Y+96);
            // Raw-image runs skip black; rectangle payloads may erase with it.
            assert(job.phase == J_RECT || color);
            fills++;
        }
    } else
        abort();
    return 1;
}

static void tick(void)
{
    now += 1000;
    if (busy_ticks) busy_ticks--;
    if (active_timer) {
        active_timer->func(active_timer);
        tjc_job_task();
    }
}

int main(int argc, char **argv)
{
    assert(argc == 4 || argc == 5);
    if (argc == 5) {
        trace = fopen(argv[4], "w");
        assert(trace);
    }
    FILE *f=fopen(argv[1], "rb");
    assert(f);
    uint8_t data[18655];
    unsigned int size=fread(data, 1, sizeof(data), f);
    fclose(f);
    // Expected CRC comes from Python zlib, independent of the MCU algorithm.
    uint32_t crc = strtoul(argv[3], NULL, 10);
    unsigned int text_start=34;
    for (unsigned int i=0; i<9; i++) {
        expected_sizes[i] = be16(data+12+i*2);
        memcpy(expected_text[i], data+text_start, expected_sizes[i]);
        text_start += expected_sizes[i];
        if (i == 5 || i == 6) {
            for (unsigned int j=0; j+1<expected_sizes[i]; j++) {
                if (expected_text[i][j] != 0xa1
                    || expected_text[i][j+1] != 0xe3) continue;
                expected_text[i][j] = ' ';
                memmove(expected_text[i]+j+1, expected_text[i]+j+2,
                        expected_sizes[i]-j-2);
                expected_sizes[i]--;
                break;
            }
        }
    }
    if (!strcmp(argv[2], "rollover")) now = UINT32_MAX - 10000;
    if (!strcmp(argv[2], "format")) data[4] = 99;
    if (!strcmp(argv[2], "crc")) crc ^= 1;
    if (!strcmp(argv[2], "busy")) claim = 0;
    no_reply = !strcmp(argv[2], "fence_timeout");
    uint32_t begin[]={7, 42, size, crc};
    command_tjc_lcd_job_begin(begin);
    if (!strcmp(argv[2], "busy")) {
        assert(last_error == E_BUSY && !frames);
        return 0;
    }
    if (!strcmp(argv[2], "abort")) {
        command_tjc_lcd_job_abort(begin);
        assert(last_error == E_ABORT && stopped && !active_timer);
        return 0;
    }
    if (!strcmp(argv[2], "timeout")) {
        for (unsigned int i=0; i<5001; i++) tick();
        assert(last_error == E_TIMEOUT && stopped && !active_timer);
        return 0;
    }
    for (unsigned int offset=0; offset<size;) {
        unsigned int len=size-offset < 40 ? size-offset : 40;
        input = data+offset;
        uint32_t chunk[]={7, 42, offset, len, 0};
        if (!strcmp(argv[2], "offset")) chunk[2]++;
        command_tjc_lcd_job_chunk(chunk);
        unsigned int limit=0;
        while (job.pending && ++limit < 4000) tick();
        assert(limit < 4000);
        if (last_error) break;
        assert(last_offset == offset+len && last_state == RESULT_RECEIVING);
        offset += len;
    }
    if (!strcmp(argv[2], "offset") || !strcmp(argv[2], "format")
        || !strcmp(argv[2], "invalid")) {
        assert(last_error == (!strcmp(argv[2], "offset") ? E_OFFSET:E_FORMAT));
        assert(stopped && !active_timer);
        return 0;
    }
    assert(!last_error);
    command_tjc_lcd_job_end(begin);
    unsigned int limit=0;
    while (job.active && ++limit < 7000) tick();
    assert(limit < 7000 && stopped && !active_timer);
    if (!strcmp(argv[2], "crc")) {
        assert(last_error == E_CRC && !fence);
        return 0;
    }
    if (no_reply) {
        assert(last_error == E_TIMEOUT && fence == 1);
        return 0;
    }
    assert(last_state == RESULT_RENDERED && !last_error && fence == 1);
    if (data[5] == IDLE_SCREEN) {
        assert(texts == (expected_sizes[3] > 2 ? 6 : 5));
        for (unsigned int i=0; i<9; i++)
            assert(text_pos[i] == (i < 3 || i == 4
                || (i == 3 && expected_sizes[3] > 2) ? expected_sizes[i] : 0));
        assert(panel[38][72] == RED && panel[90][110] == RED);
        assert(panel[148][14] == BORDER && panel[148][120] == CYAN);
        assert(panel[159][0] == BACKGROUND);
    } else {
        assert(texts >= 13 && texts <= 18);
        for (unsigned int i=0; i<9; i++)
            assert(text_pos[i] == expected_sizes[i]);
        assert(panel[0][0] == BACKGROUND && panel[162][0] == BORDER);
        assert(panel[250][119] == BORDER && panel[312][50] == BORDER);
        for (unsigned int f=5; f<=6; f++) {
            for (unsigned int j=0; j<expected_sizes[f]; j++) {
                if (expected_text[f][j] != ' ') continue;
                unsigned int x=(f == 5 ? 60 : 180)-expected_sizes[f]*6+j*12+3;
                assert(panel[266][x+2] == 0xffff);
                assert(panel[269][x] == 0xffff);
                assert(panel[269][x+3] == BACKGROUND);
            }
        }
    }
    unsigned int source=34;
    for (unsigned int i=0; i<9; i++) source+=be16(data+12+i*2);
    if (data[5] & 16) {
        uint16_t expected[96][96] = {{0}};
        if (data[source] == 0) {
            for (unsigned int y=0; y<96; y++)
                for (unsigned int x=0; x<96; x++)
                    expected[y][x] = be16(data+source+1+2*(y*96+x));
        } else {
            assert(data[source] == 1);
            for (unsigned int i=source+1; i<size; i+=6) {
                uint16_t c=be16(data+i);
                for (unsigned int y=data[i+3]; y<=data[i+5]; y++)
                    for (unsigned int x=data[i+2]; x<=data[i+4]; x++)
                        expected[y][x]=c;
            }
        }
        for (unsigned int y=0; y<96; y++)
            for (unsigned int x=0; x<96; x++)
                assert(panel[28+y][2+x] == expected[y][x]);
        if (!strcmp(argv[2], "solid")) assert(fills == 1);
    } else assert(!fills);
    unsigned int old_frames=frames;
    for (unsigned int i=0; i<10000; i++) tick();
    assert(frames == old_frames);
    return 0;
}
