// Streaming static job-card renderer for the mainboard TJC display
// Based on Marlin dwin_lcd.cpp / e3v3se/ThumbRender.cpp references.
// Copyright (C) 2020 MarlinFirmware
// This file may be distributed under the terms of the GNU GPLv3 license.

#include <string.h>
#include "board/misc.h"
#include "command.h"
#include "sched.h"
#include "tjc_lcd.h"
#include "tjc_uart.h"

DECL_CONSTANT("TJC_JOB_VERSION", 5);

enum { HEADER_SIZE=34, FIELD_COUNT=9, TEXT_SIZE=188, MAX_JOB_SIZE=18655,
       BACKGROUND=0x0863, BORDER=0x3290, FOREGROUND=0xdeff,
       ORANGE=0xfc00, CYAN=0x05ff, YELLOW=0xffe0, RED=0xf920,
       THUMB_X=2, THUMB_Y=28, IDLE_SCREEN=128 };
static const uint8_t text_limits[FIELD_COUNT] = {
    26, 26, 26, 26, 52, 8, 8, 8, 8
};

enum { J_HEADER, J_PALETTE, J_CLEAR, J_DRAIN, J_SETTLE,
       J_DECORATE, J_TEXT_RX, J_TEXT_DRAW, J_UNITS, J_PIXELS, J_RECT, J_FLUSH,
       J_WAIT_END,
       J_FENCE, J_REPLY, J_DONE };
enum { E_NONE, E_BUSY, E_FORMAT, E_OFFSET, E_CRC, E_UART, E_TIMEOUT, E_ABORT };
enum { RESULT_RECEIVING=1, RESULT_RENDERED=2, RESULT_ERROR=3 };

struct run {
    uint16_t color;
    uint8_t x0, x1, y0, y1;
};

// Two rows of spans, never an 18 KiB framebuffer. Max 96 runs per row,
// including adjacent nonblack pixels of different colors.
static struct {
    struct timer timer;
    uint32_t token, crc, expected_crc, deadline, next_frame;
    uint16_t size, offset, text_size, text_pos, palette, image_size;
    uint8_t header[HEADER_SIZE], text[TEXT_SIZE], row[192], chunk[40];
    struct run runs[2][96];
    uint8_t skipped[96], lengths[FIELD_COUNT];
    uint8_t oid, active, phase, header_pos, row_pos, y;
    uint8_t previous, counts[2], emit, color_valid, final_flush, unit_step;
    uint8_t pending, chunk_size, chunk_pos, field, field_pos, line, batch;
    uint8_t decoration, icon_row, icon_col;
    uint8_t line_pos, line_size;
    uint8_t codec, codec_read;
} job;
static struct task_wake job_wake;

static uint16_t
be16(const uint8_t *p)
{
    return (p[0] << 8) | p[1];
}

static uint32_t
be32(const uint8_t *p)
{
    return ((uint32_t)be16(p) << 16) | be16(p + 2);
}

static void
result(uint8_t state, uint8_t error)
{
    sendf("tjc_lcd_job_result oid=%c token=%u offset=%hu state=%c error=%c",
          job.oid, job.token, job.offset, state, error);
}

static void
end_job(uint8_t error)
{
    sched_del_timer(&job.timer);
    job.active = job.pending = 0;
    tjc_lcd_job_release(error);
    result(error ? RESULT_ERROR : RESULT_RENDERED, error);
}

static uint_fast8_t
job_event(struct timer *timer)
{
    sched_wake_task(&job_wake);
    timer->waketime += timer_from_us(1000);
    return SF_RESCHEDULE;
}

static uint_fast8_t
frame(uint8_t opcode, const uint8_t *data, uint8_t len)
{
    if (!tjc_lcd_send_frame(opcode, data, len)) {
        end_job(E_UART);
        return 0;
    }
    // Preserve Marlin's measured throttle: 16 ms after each ten frames.
    if (++job.batch == 10) {
        job.batch = 0;
        job.next_frame = timer_read_time() + timer_from_us(16000);
    }
    return 1;
}

static void
put16(uint8_t *p, uint16_t value)
{
    p[0] = value >> 8;
    p[1] = value;
}

// Return true only after the rectangle, possibly on a later task than palette.
static uint_fast8_t
rectangle(uint16_t color, uint16_t x0, uint16_t y0,
          uint16_t x1, uint16_t y1)
{
    if (!job.color_valid || job.palette != color) {
        uint8_t palette[] = {color >> 8, color, BACKGROUND >> 8,
                             BACKGROUND & 0xff};
        if (frame(0x40, palette, sizeof(palette))) {
            job.palette = color;
            job.color_valid = 1;
        }
        return 0;
    }
    uint8_t data[8];
    put16(data, x0);
    put16(data+2, y0);
    put16(data+4, x1);
    put16(data+6, y1);
    return frame(0x5b, data, sizeof(data));
}

// Small code-native icons: layers, layer height, clock, spool, nozzle, bed.
// Rows are horizontal runs of up to 16 pixels, sent one frame per task.
static const uint16_t icons[7][16] = {
    {0, 0x0180, 0x07e0, 0x1ff8, 0x7ffe, 0x1ff8, 0x07e0, 0x0180,
     0x6006, 0x1ff8, 0x07e0, 0x6006, 0x1ff8, 0x07e0, 0x0180, 0},
    {0, 0x0180, 0x0660, 0x1818, 0x6006, 0x1818, 0x0660, 0x0180,
     0x6006, 0x1818, 0x0660, 0x6006, 0x1818, 0x0660, 0x0180, 0},
    {0x07e0, 0x1818, 0x2004, 0x4182, 0x4182, 0x8181, 0x8181, 0x8181,
     0x80c1, 0x8061, 0x8031, 0x4002, 0x4002, 0x2004, 0x1818, 0x07e0},
    {0x1ff8, 0x6006, 0x63c6, 0x6006, 0x1ff8, 0x1008, 0x1668, 0x1668,
     0x1668, 0x1668, 0x1668, 0x1668, 0x1008, 0x6006, 0x6006, 0x1ff8},
    {0x03c0, 0x03c0, 0x03c0, 0x03c0, 0x0ff0, 0x0ff0, 0x0ff0, 0x0ff0,
     0x0ff0, 0x07e0, 0x03c0, 0x0180, 0x0180, 0, 0, 0},
    {0x1248, 0x2490, 0x2490, 0x1248, 0x0924, 0x0924, 0x1248, 0x2490,
     0x2490, 0, 0, 0x7ffe, 0xffff, 0xffff, 0, 0},
    {0x07e0, 0x1818, 0x2004, 0x4002, 0x400a, 0x8019, 0x8031, 0x9061,
     0x98c1, 0x8d81, 0x8701, 0x4202, 0x4002, 0x2004, 0x1818, 0x07e0}
};

static const struct decoration {
    uint16_t color, y0, y1;
    uint8_t x0, x1;
} decorations[] = {
    {BORDER, 8, 8, 0, 99}, {BORDER, 143, 143, 0, 99},
    {BORDER, 8, 143, 0, 0}, {BORDER, 8, 143, 99, 99},
    {0, THUMB_Y, THUMB_Y+95, THUMB_X, THUMB_X+95},
    {BORDER, 162, 162, 0, 233},
    {BORDER, 226, 226, 10, 229}, {BORDER, 312, 312, 10, 229},
    {BORDER, 230, 308, 6, 6}, {BORDER, 230, 308, 233, 233},
    {BORDER, 228, 229, 8, 9}, {BORDER, 228, 229, 230, 231},
    {BORDER, 309, 310, 8, 9}, {BORDER, 309, 310, 230, 231},
    {BORDER, 238, 302, 119, 119}
};

static void
draw_idle_decoration(void)
{
    static const struct decoration borders[] = {
        {BORDER, 148, 148, 14, 225}, {CYAN, 147, 149, 108, 131},
        {BORDER, 231, 231, 14, 225}, {CYAN, 230, 232, 108, 131}
    };
    uint8_t count = sizeof(borders) / sizeof(borders[0]);
    if (job.decoration < count) {
        const struct decoration *d = &borders[job.decoration];
        if (rectangle(d->color, d->x0, d->y0, d->x1, d->y1))
            job.decoration++;
        return;
    }
    uint8_t stage = job.decoration-count, row = job.icon_row;
    if (stage < 3) {
        uint8_t x0, x1;
        uint16_t y, color;
        if (stage < 2) {
            // Mirrored nozzle wings: blue shaded body, then red front faces.
            x0 = stage ? (row < 38 ? 72+row : 110)
                       : 72 + (row > 16 ? row-16 : 0);
            x1 = row < 38 ? 79+row : 117;
            y = 38+row;
            color = stage ? RED : 0x2a4c;
        } else {
            x0 = 12+row;
            x1 = x0+1;
            y = (job.icon_col & 2) ? 319-row : row;
            color = 0x11aa;
        }
        uint8_t start = (job.icon_col & 1) ? 239-x1 : x0;
        uint8_t end = (job.icon_col & 1) ? 239-x0 : x1;
        if (!rectangle(color, start, y, end, y))
            return;
        if (++job.icon_col == (stage < 2 ? 2 : 4)) {
            job.icon_col = 0;
            if (++job.icon_row == (stage < 2 ? 53 : 48)) {
                job.icon_row = 0;
                job.decoration++;
            }
        }
        return;
    }
    if (row == 16) {
        job.phase = J_TEXT_RX;
        return;
    }
    uint16_t bits = icons[6][row];
    uint8_t col = job.icon_col;
    while (col < 16 && !(bits & (0x8000 >> col)))
        col++;
    if (col == 16) {
        job.icon_col = 0;
        job.icon_row++;
        return;
    }
    uint8_t end = col;
    while (end < 15 && (bits & (0x8000 >> (end+1))))
        end++;
    if (rectangle(CYAN, 112+col, 254+row, 112+end, 254+row))
        job.icon_col = end+1;
}

static void
draw_decoration(void)
{
    if (job.header[5] & IDLE_SCREEN) {
        draw_idle_decoration();
        return;
    }
    uint8_t count = sizeof(decorations) / sizeof(decorations[0]);
    if (job.decoration < count) {
        const struct decoration *d = &decorations[job.decoration];
        if (rectangle(d->color, d->x0, d->y0, d->x1, d->y1))
            job.decoration++;
        return;
    }
    uint8_t icon = job.decoration - count;
    if (icon == 6) {
        job.phase = J_TEXT_RX;
        return;
    }
    uint16_t bits = icons[icon][job.icon_row];
    uint8_t col = job.icon_col;
    while (col < 16 && !(bits & (0x8000 >> col)))
        col++;
    if (col == 16) {
        job.icon_col = 0;
        if (++job.icon_row == 16) {
            job.icon_row = 0;
            job.decoration++;
        }
        return;
    }
    uint8_t end = col;
    while (end < 15 && (bits & (0x8000 >> (end+1))))
        end++;
    uint8_t x = icon < 4 ? 102 : (icon == 4 ? 52 : 172);
    uint16_t y = icon < 4 ? 16 + 36*icon : 238;
    uint16_t color = icon == 4 ? ORANGE : CYAN;
    if (rectangle(color, x+col, y+job.icon_row, x+end, y+job.icon_row))
        job.icon_col = end+1;
}

static uint_fast8_t
validate_header(void)
{
    uint8_t *h = job.header;
    if (memcmp(h, "KLCD", 4) || h[4] != 5
        || ((h[5] & IDLE_SCREEN) && h[5] != IDLE_SCREEN)
        || be16(h + 6) != HEADER_SIZE)
        return 0;
    uint16_t text_size = 0;
    for (uint_fast8_t i=0; i<FIELD_COUNT; i++) {
        uint16_t len = be16(h + 12 + i*2);
        uint8_t limit = (h[5] & IDLE_SCREEN) && i == 2 ? 27 : text_limits[i];
        if (!len || len > limit)
            return 0;
        if ((h[5] & IDLE_SCREEN)
            && ((i == 0 && len > 12) || ((i == 1 || i == 4) && len > 20)
                || (i == 2 && len > 27)))
            return 0;
        job.lengths[i] = len;
        text_size += len;
    }
    uint8_t image = !!(h[5] & 16);
    uint32_t image_size = be32(h + 30);
    if (be16(h + 8) != (image ? 96 : 0)
        || be16(h + 10) != (image ? 96 : 0)
        || (image ? (!image_size || image_size > 18433) : image_size != 0)
        || job.size != HEADER_SIZE + text_size + image_size)
        return 0;
    job.image_size = image_size;
    job.text_size = text_size;
    return 1;
}

static uint_fast8_t
validate_text(void)
{
    uint16_t pos = 0;
    for (uint_fast8_t f=0; f<FIELD_COUNT; f++) {
        uint16_t end = pos + job.lengths[f];
        while (pos < end) {
            uint8_t c = job.text[pos++];
            if (c >= 0x20 && c <= 0x7e)
                continue;
            if (c < 0x81 || c > 0xfe || pos == end)
                return 0;
            c = job.text[pos++];
            if (c < 0x40 || c > 0xfe || c == 0x7f)
                return 0;
        }
    }
    return 1;
}

static void
prepare_row(void)
{
    uint8_t current = job.previous ^ 1, count = 0;
    struct run *cur = job.runs[current], *prev = job.runs[job.previous];
    for (uint_fast8_t x=0; x<96; x++) {
        uint16_t color = be16(job.row + x*2);
        if (!color)
            continue;
        uint8_t x0 = x;
        while (x + 1 < 96 && be16(job.row + (x+1)*2) == color)
            x++;
        cur[count++] = (struct run){color, x0, x, job.y, job.y};
    }
    memset(job.skipped, 0, sizeof(job.skipped));
    uint_fast8_t p=0, c=0;
    while (p < job.counts[job.previous] && c < count) {
        if (prev[p].x0 < cur[c].x0)
            p++;
        else if (prev[p].x0 > cur[c].x0)
            c++;
        else {
            if (prev[p].x1 == cur[c].x1 && prev[p].color == cur[c].color) {
                cur[c].y0 = prev[p].y0;
                job.skipped[p] = 1;
            }
            p++;
            c++;
        }
    }
    job.counts[current] = count;
    job.emit = 0;
    job.phase = J_FLUSH;
}

// One palette or rectangle frame per task. Group unmatched spans by color.
static void
flush_row(void)
{
    struct run *runs = job.runs[job.previous];
    uint_fast8_t count = job.counts[job.previous], i = job.emit;
    while (i < count && job.skipped[i])
        i++;
    if (i == count) {
        // Prefer another span of the current palette before changing color.
        for (i=0; i<count && job.skipped[i]; i++)
            ;
        if (i == count) {
            if (job.final_flush) {
                job.phase = J_FENCE;
                return;
            }
            job.previous ^= 1;
            job.row_pos = 0;
            job.y++;
            job.phase = job.y == 96 ? J_WAIT_END : J_PIXELS;
            return;
        }
    }
    struct run *r = &runs[i];
    if (!rectangle(r->color, THUMB_X+r->x0, THUMB_Y+r->y0,
                    THUMB_X+r->x1, THUMB_Y+r->y1))
        return;
    job.skipped[i] = 1;
    for (i=0; i<count; i++)
        if (!job.skipped[i] && runs[i].color == job.palette)
            break;
    job.emit = i;
}

static void
draw_idle_text(void)
{
    uint16_t base = 0;
    for (uint_fast8_t i=0; i<job.field; i++)
        base += job.lengths[i];
    uint8_t len = job.lengths[job.field] - job.field_pos;
    uint8_t font = 1;
    uint16_t x, y, color = 0xffff;
    if (job.field == 0) {
        font = 6;
        x = (240-job.lengths[0]*20)/2 + job.field_pos*20;
        y = 102;
        if (!job.field_pos) {
            len = job.text[base] >= 0x81 ? 2 : 1;
            color = RED;
        }
    } else if (job.field == 1) {
        font = 3;
        x = (240-len*12)/2;
        y = 174;
    } else if (job.field == 2) {
        x = 12;
        y = 207;
        color = 0x8d7b;
    } else {
        font = 3;
        x = (240-len*12)/2;
        y = 282;
        color = 0x8d7b;
    }
    uint8_t data[47] = {0, 0, 0, 0, 0, 0x42, font, 0, 0,
                        BACKGROUND >> 8, BACKGROUND & 0xff};
    put16(data, x);
    put16(data+2, y);
    put16(data+7, color);
    memcpy(data+11, job.text+base+job.field_pos, len);
    if (!frame(0x98, data, 11+len))
        return;
    job.field_pos += len;
    // Only slots 0,1,2,4 belong to the idle layout.
    if (job.field_pos == job.lengths[job.field]) {
        job.field_pos = 0;
        if (++job.field == 3)
            job.field = 4;
        if (job.field == 5)
            job.phase = J_WAIT_END;
    }
}

// Choose two balanced filename lines; prefer whole underscore/space tokens.
// Byte boundaries follow GBK cells, so a two-byte glyph is never split.
static uint8_t
filename_split(const uint8_t *text, uint8_t size)
{
    uint8_t best = 0, score = 255, fallback = 0, fallback_score = 255;
    for (uint8_t pos=0; pos<size;) {
        if (pos && pos <= 27 && size-pos <= 27) {
            uint8_t distance = pos*2 > size ? pos*2-size : size-pos*2;
            if (distance < fallback_score) {
                fallback = pos;
                fallback_score = distance;
            }
        }
        if (pos && pos <= 27 && size-pos-1 <= 27 && pos+1 < size
            && (text[pos] == '_' || text[pos] == ' ')) {
            uint8_t distance = pos*2 > size-1 ? pos*2-size+1 : size-1-pos*2;
            if (distance < score) {
                best = pos;
                score = distance;
            }
        }
        pos += text[pos] >= 0x81 ? 2 : 1;
    }
    return best ? best : fallback;
}

// Some panel fonts leave GBK A1 E3 (degree) blank. Reserve one ASCII cell
// in the temperature text and draw a bounded circle in that cell instead.
static uint8_t
degree_offset(const uint8_t *text, uint8_t size)
{
    for (uint8_t pos=0; pos<size;) {
        if (pos+1 < size && text[pos] == 0xa1 && text[pos+1] == 0xe3)
            return pos;
        pos += text[pos] >= 0x81 ? 2 : 1;
    }
    return 255;
}

static void
draw_units(void)
{
    static const uint8_t circle[8][4] = {
        {2,0,4,0}, {1,1,1,1}, {5,1,5,1}, {0,2,0,4},
        {6,2,6,4}, {1,5,1,5}, {5,5,5,5}, {2,6,4,6}
    };
    uint8_t field = 5 + job.unit_step/8;
    if (field == 7) {
        job.color_valid = 0;
        job.phase = (job.header[5] & 16) ? J_PIXELS : J_WAIT_END;
        return;
    }
    uint16_t base = 0;
    for (uint_fast8_t i=0; i<field; i++)
        base += job.lengths[i];
    uint8_t size = job.lengths[field];
    uint8_t degree = degree_offset(job.text+base, size);
    if (degree == 255) {
        job.unit_step = (field-4)*8;
        return;
    }
    uint16_t x = (field == 5 ? 60 : 180) - (size-1)*6 + degree*12 + 3;
    const uint8_t *p = circle[job.unit_step % 8];
    if (rectangle(0xffff, x+p[0], 266+p[1], x+p[2], 266+p[3]))
        job.unit_step++;
}

static void
draw_text(void)
{
    if (job.header[5] & IDLE_SCREEN) {
        draw_idle_text();
        return;
    }
    uint16_t base = 0;
    for (uint_fast8_t i=0; i<job.field; i++)
        base += job.lengths[i];
    uint8_t remain = job.lengths[job.field] - job.field_pos;
    uint8_t len = 0, *text = job.text + base + job.field_pos;
    uint8_t limit = job.field < 4 ? 14 : (job.field == 4 ? 27 : 8);
    while (len < remain) {
        uint8_t bytes = text[len] >= 0x81 ? 2 : 1;
        if (len + bytes > limit)
            break;
        len += bytes;
    }
    if (job.field == 4 && !job.line && remain > 27)
        len = filename_split(text, remain);
    // Prefer a word boundary when the remaining text still fits line two.
    // Keep a metadata value and its units together when wrapping is needed.
    if (!job.line && len < remain && job.field < 4) {
        uint8_t pos = 0, split = 0;
        while (pos < len) {
            if (text[pos] == ' ')
                split = pos+1;
            pos += text[pos] >= 0x81 ? 2 : 1;
        }
        if (split && remain-split < limit)
            len = split;
    }
    if (!job.line_size)
        job.line_size = len;
    len = job.line_size-job.line_pos;
    uint16_t color = 0xffff;
    if (job.field < 4) {
        uint8_t label = 0;
        while (label < job.lengths[job.field]
               && job.text[base+label] != ':')
            label++;
        if (label < job.lengths[job.field] && job.field_pos <= label) {
            color = YELLOW;
            uint8_t end = label+1-job.field_pos;
            if (end < len)
                len = end;
        }
    } else if (job.field > 6) {
        color = YELLOW;
    }
    uint8_t degree = (job.field == 5 || job.field == 6)
                     ? degree_offset(text, len) : 255;
    uint8_t drawn = len - (degree != 255);
    uint16_t x = 126+job.line_pos*8, y = 16+job.field*36+job.line*18;
    uint8_t font = 1;
    if (job.field == 4) {
        font = 1;
        x = (240 - len*8)/2;
        y = 174 + job.line*18;
    } else if (job.field == 5 || job.field == 6) {
        font = 3;
        x = (job.field == 5 ? 60 : 180) - drawn*6;
        y = 264;
    } else if (job.field > 6) {
        x = (job.field == 7 ? 60 : 180) - len*4;
        y = 296;
    }
    uint8_t data[47] = {0, 0, 0, 0, 0, 0x42, font, 0xff, 0xff,
                        BACKGROUND >> 8, BACKGROUND & 0xff};
    put16(data, x);
    put16(data+2, y);
    put16(data+7, color);
    if (degree == 255) {
        memcpy(data+11, text, len);
    } else {
        memcpy(data+11, text, degree);
        data[11+degree] = ' ';
        memcpy(data+12+degree, text+degree+2, len-degree-2);
    }
    if (!frame(0x98, data, 11 + drawn))
        return;
    job.field_pos += len;
    job.line_pos += len;
    if (job.line_pos == job.line_size) {
        job.line++;
        job.line_pos = job.line_size = 0;
        if (job.field == 4 && job.field_pos < job.lengths[4]
            && (job.text[base+job.field_pos] == '_'
                || job.text[base+job.field_pos] == ' '))
            job.field_pos++;
    }
    if (job.field_pos == job.lengths[job.field]) {
        job.field++;
        job.field_pos = job.line = 0;
        job.line_pos = job.line_size = 0;
    }
    if (job.field == FIELD_COUNT) {
        // Degree marks are primitives, independent of panel font coverage.
        job.color_valid = 0;
        job.phase = J_UNITS;
    }
}

static void
consume(void)
{
    while (job.chunk_pos < job.chunk_size) {
        uint8_t b = job.chunk[job.chunk_pos++];
        job.offset++;
        job.crc ^= b;
        for (uint_fast8_t bit=0; bit<8; bit++)
            job.crc = (job.crc >> 1) ^ (0xedb88320u & -(job.crc & 1));
        if (job.phase == J_HEADER) {
            job.header[job.header_pos++] = b;
            if (job.header_pos == HEADER_SIZE) {
                if (!validate_header()) {
                    end_job(E_FORMAT);
                    return;
                }
                job.phase = J_PALETTE;
                return;
            }
        } else if (job.phase == J_TEXT_RX) {
            job.text[job.text_pos++] = b;
            if (job.text_pos == job.text_size) {
                if (!validate_text()) {
                    end_job(E_FORMAT);
                    return;
                }
                job.phase = J_TEXT_DRAW;
                return;
            }
        } else if (job.phase == J_PIXELS) {
            if (!job.codec_read) {
                job.codec = b;
                job.codec_read = 1;
                if (b > 1 || (!b && job.image_size != 18433)
                    || (b == 1 && (job.image_size-1) % 6)) {
                    end_job(E_FORMAT);
                    return;
                }
                if (job.offset == job.size)
                    job.phase = J_WAIT_END;
                continue;
            }
            job.row[job.row_pos++] = b;
            if (job.codec == 1 && job.row_pos == 6) {
                if (job.row[2] > job.row[4] || job.row[3] > job.row[5]
                    || job.row[4] >= 96 || job.row[5] >= 96) {
                    end_job(E_FORMAT);
                    return;
                }
                job.phase = J_RECT;
                return;
            } else if (!job.codec && job.row_pos == sizeof(job.row)) {
                prepare_row();
                return;
            }
        } else {
            end_job(E_FORMAT);
            return;
        }
    }
}

void
command_tjc_lcd_job_begin(uint32_t *args)
{
    if (job.active || args[2] < HEADER_SIZE+FIELD_COUNT
        || args[2] > MAX_JOB_SIZE
        || !tjc_lcd_job_claim(args[0])) {
        sendf("tjc_lcd_job_result oid=%c token=%u offset=%hu state=%c error=%c",
              args[0], args[1], 0, RESULT_ERROR, E_BUSY);
        return;
    }
    memset(&job, 0, sizeof(job));
    job.oid = args[0];
    job.token = args[1];
    job.size = args[2];
    job.expected_crc = args[3];
    job.crc = 0xffffffffu;
    job.active = 1;
    job.deadline = timer_read_time() + timer_from_us(5000000);
    job.next_frame = timer_read_time();
    job.timer.func = job_event;
    job.timer.waketime = timer_read_time() + timer_from_us(1000);
    sched_add_timer(&job.timer);
    result(RESULT_RECEIVING, E_NONE);
}
DECL_COMMAND(command_tjc_lcd_job_begin,
             "tjc_lcd_job_begin oid=%c token=%u size=%hu crc=%u");

void
command_tjc_lcd_job_chunk(uint32_t *args)
{
    if (!job.active || args[0] != job.oid || args[1] != job.token)
        return;
    uint32_t len = args[3];
    if (job.pending || args[2] != job.offset || !len || len > 40
        || job.offset + len > job.size) {
        end_job(E_OFFSET);
        return;
    }
    memcpy(job.chunk, command_decode_ptr(args[4]), len);
    job.chunk_size = len;
    job.chunk_pos = 0;
    job.pending = 1;
    job.deadline = timer_read_time() + timer_from_us(5000000);
}
DECL_COMMAND(command_tjc_lcd_job_chunk,
             "tjc_lcd_job_chunk oid=%c token=%u offset=%hu data=%*s");

void
command_tjc_lcd_job_end(uint32_t *args)
{
    if (!job.active || args[0] != job.oid || args[1] != job.token)
        return;
    if (job.pending || job.offset != job.size || job.phase != J_WAIT_END) {
        end_job(E_OFFSET);
        return;
    }
    if ((job.crc ^ 0xffffffffu) != job.expected_crc) {
        end_job(E_CRC);
        return;
    }
    memset(job.skipped, 0, sizeof(job.skipped));
    job.final_flush = 1;
    job.emit = 0;
    job.phase = J_FLUSH;
    job.deadline = timer_read_time() + timer_from_us(5000000);
}
DECL_COMMAND(command_tjc_lcd_job_end, "tjc_lcd_job_end oid=%c token=%u");

void
command_tjc_lcd_job_abort(uint32_t *args)
{
    if (job.active && args[0] == job.oid && args[1] == job.token)
        end_job(E_ABORT);
}
DECL_COMMAND(command_tjc_lcd_job_abort, "tjc_lcd_job_abort oid=%c token=%u");

void
tjc_job_task(void)
{
    if (!sched_check_wake(&job_wake) || !job.active)
        return;
    uint32_t now = timer_read_time();
    if (tjc_uart_error()) {
        end_job(E_UART);
        return;
    }
    if (!timer_is_before(now, job.deadline)) {
        end_job(E_TIMEOUT);
        return;
    }
    if (tjc_uart_busy() || timer_is_before(now, job.next_frame))
        return;
    switch (job.phase) {
    case J_HEADER: case J_TEXT_RX: case J_PIXELS:
        if (job.pending)
            consume();
        break;
    case J_PALETTE: {
        uint8_t palette[] = {0xff, 0xff, BACKGROUND >> 8, BACKGROUND & 0xff};
        if (frame(0x40, palette, sizeof(palette)))
            job.phase = J_CLEAR;
        break;
    }
    case J_CLEAR:
        if (frame(0x52, NULL, 0))
            job.phase = J_DRAIN;
        break;
    case J_DRAIN:
        job.next_frame = now + timer_from_us(50000);
        job.phase = J_SETTLE;
        break;
    case J_SETTLE:
        job.phase = J_DECORATE;
        break;
    case J_DECORATE:
        draw_decoration();
        break;
    case J_TEXT_DRAW:
        draw_text();
        break;
    case J_UNITS:
        draw_units();
        break;
    case J_FLUSH:
        flush_row();
        break;
    case J_RECT:
        if (rectangle(be16(job.row), THUMB_X+job.row[2], THUMB_Y+job.row[3],
                       THUMB_X+job.row[4], THUMB_Y+job.row[5])) {
            job.row_pos = 0;
            job.phase = job.offset == job.size ? J_WAIT_END : J_PIXELS;
        }
        break;
    case J_FENCE:
        if (!tjc_lcd_fence_start())
            end_job(E_UART);
        else
            job.phase = J_REPLY;
        break;
    case J_REPLY:
        if (tjc_lcd_fence_ready()) {
            job.next_frame = now + timer_from_us(50000);
            job.phase = J_DONE;
        }
        break;
    case J_DONE:
        end_job(E_NONE);
        break;
    }
    if (job.active && job.pending && job.chunk_pos == job.chunk_size
        && (job.phase == J_HEADER || job.phase == J_TEXT_RX
            || job.phase == J_PIXELS || job.phase == J_WAIT_END)) {
        job.pending = 0;
        job.deadline = now + timer_from_us(5000000);
        result(RESULT_RECEIVING, E_NONE);
    }
}
DECL_TASK(tjc_job_task);

void
tjc_job_shutdown(void)
{
    if (job.active) {
        sched_del_timer(&job.timer);
        job.active = job.pending = 0;
        tjc_lcd_job_release(1);
    }
}
DECL_SHUTDOWN(tjc_job_shutdown);
