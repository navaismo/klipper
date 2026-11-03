# Host-owned static print card, based on Screenshots/newPrint_card.png
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
try:
    from . import lcd_draw as draw, lcd_idle_layout, lcd_transfer
except ImportError:
    import lcd_draw as draw, lcd_idle_layout, lcd_transfer

BACKGROUND, PANEL, WHITE = 0x0863, 0x10a6, 0xdeff
CYAN, BORDER, MUTED, YELLOW, ORANGE = 0x05ff, 0x3290, 0x8d7b, 0xffe0, 0xfc00
PAUSE_HINT = 'Press Knob to Pause'
THUMB_SIZE, THUMB_X, THUMB_Y = 120, 10, 54


def _text(value, x, y, color=WHITE, font=1, background=BACKGROUND):
    return draw.text(value, x, y, color, background, font)


def _small(value, x, y, color=CYAN):
    return lcd_idle_layout.small_text(value, x, y, color)


def _center(value, y, color=WHITE):
    return _text(value, (240-len(value.encode('gbk'))*8)//2, y, color)


def _box(x0, y0, x1, y1, color, fill=None):
    ops = []
    if fill is not None:
        ops.append(draw.rectangle(fill, x0+1, y0+1, x1-1, y1-1))
    return ops + [draw.rectangle(color, x0+2, y0, x1-2, y0),
                  draw.rectangle(color, x0+2, y1, x1-2, y1),
                  draw.rectangle(color, x0, y0+2, x0, y1-2),
                  draw.rectangle(color, x1, y0+2, x1, y1-2),
                  draw.rectangle(color, x0+1, y0+1, x0+1, y0+1),
                  draw.rectangle(color, x1-1, y0+1, x1-1, y0+1),
                  draw.rectangle(color, x0+1, y1-1, x0+1, y1-1),
                  draw.rectangle(color, x1-1, y1-1, x1-1, y1-1)]


def filename_lines(value):
    if len(value.encode('gbk')) <= 27:
        return [value]
    candidates = []
    for index in range(1, len(value)):
        left, right = value[:index], value[index:]
        separator = value[index] in '_ '
        if separator:
            right = right[1:]
        a, b = len(left.encode('gbk')), len(right.encode('gbk'))
        if a <= 27 and b <= 27 and right:
            candidates.append((not separator, abs(a-b), left, right))
    if candidates:
        choice = min(candidates)
        return list(choice[2:])
    raise ValueError('LCD filename exceeds two bounded lines')


def _thumbnail(job):
    thumb = job.get('thumbnail')
    if thumb is None:
        return []
    image = thumb['rgb565']
    if (image['format'] != 'rgb565be' or image['width'] != 96
        or image['height'] != 96 or len(image['data']) != 18432):
        raise ValueError('Expected the prepared 96x96 RGB565 thumbnail')
    # Scale rectangle edges on the host, retaining colors and operation count.
    rectangles = lcd_transfer.image_rectangles(image['data'])
    return [draw.rectangle(color, THUMB_X+x0*THUMB_SIZE//96,
                            THUMB_Y+y0*THUMB_SIZE//96,
                            THUMB_X+(x1+1)*THUMB_SIZE//96-1,
                            THUMB_Y+(y1+1)*THUMB_SIZE//96-1)
            for color, x0, y0, x1, y1 in rectangles]


def _temperature(value, center):
    known = value != '--'
    text = value.replace(u'\u00b0', ' ') if known else '--'
    x = center-len(text)*4
    ops = [_text(text, x, 261, background=PANEL)]
    if known:
        # Draw the degree ring in the blank before C; the panel glyph is blank.
        dx = x+(len(text)-2)*8+2
        ops += [draw.rectangle(WHITE, dx+1, 263, dx+2, 263),
                draw.rectangle(WHITE, dx+1, 266, dx+2, 266),
                draw.rectangle(WHITE, dx, 264, dx, 265),
                draw.rectangle(WHITE, dx+3, 264, dx+3, 265)]
    return ops


def screen(job):
    display = job['display']
    ops = [draw.clear(BACKGROUND)]
    for color, depth in ((CYAN, 5), (ORANGE, 2)):
        for step in range(0, 18, 3):
            ops += [draw.rectangle(color, 13+step, 12+step,
                                   18+step, 12+step+depth),
                    draw.rectangle(color, 45-step, 12+step,
                                   50-step, 12+step+depth)]
    ops += [_text('KLIPPER', 64, 15)] + _small('PRINTING', 181, 18, ORANGE)
    ops += [draw.rectangle(CYAN, 12, 39, 228, 39)]
    ops += _box(8, 44, 132, 177, CYAN) + _small('MODEL', 18, 46, MUTED)
    ops += _thumbnail(job)
    if job.get('thumbnail') is None:
        ops += _small('--', 64, 111, MUTED)
    for index, (label, name) in enumerate((('LAYERS', 'total_layers'),
            ('HEIGHT', 'layer_height'), ('ETA', 'estimated_time'),
            ('MATERIAL', 'material'))):
        y = 49+30*index
        ops += _small(label, 140, y)
        value = display[name].split(': ', 1)[1]
        color = YELLOW if index == 3 else WHITE
        if name == 'estimated_time' and len(value) > 11:
            # Preserve complete large hour counts in the narrower column.
            ops += _small(value, 140, y+10, color)
        else:
            value = lcd_transfer._text_bytes(value, 11).decode('gbk')
            ops += [_text(value, 140, y+8, color)]
        if index != 3:
            ops += [draw.rectangle(BORDER, 140, y+26, 228, y+26)]
    ops += [draw.rectangle(CYAN, 12, 180, 228, 180)]
    lines = filename_lines(display['filename'])
    for index, line in enumerate(lines):
        ops += [_center(line, (188 if len(lines) == 2 else 197)+index*18,
                         WHITE if index == 0 else MUTED)]
    ops += [draw.rectangle(CYAN, 12, 228, 228, 228)]
    ops += _box(12, 238, 228, 292, BORDER, PANEL)
    ops += [draw.rectangle(BORDER, 120, 245, 120, 285)]
    # Heat-break stem, cooling fins, heater block, tapered nozzle and orifice.
    ops += [draw.rectangle(ORANGE, 29, 247, 33, 257)]
    for y in (247, 250, 253):
        ops += [draw.rectangle(ORANGE, 24, y, 38, y+1)]
    ops += _box(23, 258, 39, 264, ORANGE)
    ops += [draw.rectangle(ORANGE, 27, 265, 35, 266),
            draw.rectangle(ORANGE, 29, 267, 33, 268),
            draw.rectangle(ORANGE, 31, 269, 31, 270),
            draw.rectangle(CYAN, 132, 266, 154, 267)]
    for x in (136, 143, 150):
        ops += [draw.rectangle(CYAN, x, 249, x, 251),
                draw.rectangle(CYAN, x-1, 252, x-1, 254),
                draw.rectangle(CYAN, x, 255, x, 257)]
    ops += _temperature(display['nozzle_target'], 75)
    ops += _temperature(display['bed_target'], 183)
    ops += _small('NOZZLE', 57, 247, YELLOW) + _small('BED', 174, 247, YELLOW)
    ops += [draw.rectangle(BORDER, 12, 303, 228, 303)]
    ops += _small(PAUSE_HINT, (240-len(PAUSE_HINT)*6+1)//2, 310, MUTED)
    return ops
