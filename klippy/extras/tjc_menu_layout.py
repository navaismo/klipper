# Host-owned Ender-3 V3 SE menu designs, in logical 240x320 coordinates
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import math
try:
    from . import lcd_draw as draw
except ImportError:
    import lcd_draw as draw

BACKGROUND, WHITE, CYAN, ORANGE = 0x0863, 0xffff, 0x05ff, 0xfc00
RED = 0xf920
MAIN = ('Home All Axes', 'Load Filament', 'Unload Filament',
        'Preheat - B50, N170', 'More Moves...')
MOVES = ('Move Z Up (10mm)', 'Move Z Down (10mm)',
         'Move X Left (10mm)', 'Move X Right (10mm)',
         'Move Y Front (10mm)', 'Move Y Back (10mm)', 'Back')
ROWS = {'main': (76, 122, 150, 198, 246),
        'move': (76, 105, 143, 172, 210, 239, 284)}

# Sixteen-pixel icons are host assets, never compiled into MCU firmware.
ICONS = {
    'home': (0x0180, 0x03c0, 0x07e0, 0x0ff0, 0x1ff8, 0x3ffc, 0x7ffe,
             0xffff, 0x3ffc, 0x3ffc, 0x3e7c, 0x3e7c, 0x3e7c, 0x3e7c),
    'spool': (0x1ff8, 0x6006, 0x63c6, 0x6006, 0x1ff8, 0x1008, 0x1668,
              0x1668, 0x1668, 0x1668, 0x1668, 0x1008, 0x6006, 0x1ff8),
    'temperature': (0x0180, 0x0240, 0x0240, 0x03c0, 0x03c0, 0x03c0,
                    0x03c0, 0x03c0, 0x07e0, 0x0ff0, 0x0ff0, 0x07e0),
    'up': (0x0180, 0x03c0, 0x07e0, 0x0ff0, 0x1ff8, 0x3ffc, 0x7ffe,
           0x0180, 0x0180, 0x0180, 0x0180, 0x0180, 0x0180, 0x0180),
    'down': (0x0180, 0x0180, 0x0180, 0x0180, 0x0180, 0x0180, 0x0180,
             0x7ffe, 0x3ffc, 0x1ff8, 0x0ff0, 0x07e0, 0x03c0, 0x0180),
    'left': (0x0080, 0x0180, 0x0380, 0x0780, 0x0fff, 0x1fff, 0x3fff,
             0x1fff, 0x0fff, 0x0780, 0x0380, 0x0180, 0x0080),
    'right': (0x0100, 0x0180, 0x01c0, 0x01e0, 0xfff0, 0xfff8, 0xfffc,
              0xfff8, 0xfff0, 0x01e0, 0x01c0, 0x0180, 0x0100),
}


def box(x0, y0, x1, y1, color):
    return [draw.rectangle(color, x0, y0, x1, y0),
            draw.rectangle(color, x0, y1, x1, y1),
            draw.rectangle(color, x0, y0, x0, y1),
            draw.rectangle(color, x1, y0, x1, y1)]


def icon(name, x, y, color=WHITE):
    ops = []
    for row, bits in enumerate(ICONS[name]):
        col = 0
        while col < 16:
            if not bits & (0x8000 >> col):
                col += 1
                continue
            start = col
            while col < 16 and bits & (0x8000 >> col):
                col += 1
            ops.append(draw.rectangle(color, x+start, y+row, x+col-1, y+row))
    return ops


def centered(value, y, color=WHITE, font=1):
    width = draw.FONTS[font][0]
    return draw.text(value, (240-len(value.encode('gbk'))*width)//2,
                     y, color, BACKGROUND, font)


def heading(value, y):
    span = (240-len(value)*8)//2-6
    return [draw.rectangle(CYAN, 14, y+8, span, y+8),
            centered(value, y),
            draw.rectangle(CYAN, 239-span, y+8, 225, y+8)]


def selection(page, selected, old=None):
    ops = []
    if old is not None:
        y = ROWS[page][old]
        ops += box(6, y-5, 233, y+20, BACKGROUND)
    y = ROWS[page][selected]
    return ops + box(6, y-5, 233, y+20, CYAN)


def branding(y, size, text_y):
    # Orange nozzle faces over blue wings, a red K and white remaining text.
    ops = []
    for color, thickness in ((CYAN, 4), (ORANGE, 1)):
        for i in range(0, size, 3):
            ops += [draw.rectangle(color, 120-size*2+i, y+i,
                                   124-size*2+i, y+thickness+i),
                    draw.rectangle(color, 115+size*2-i, y+i,
                                   119+size*2-i, y+thickness+i)]
    return ops + [draw.text('K', 78, text_y, RED, font=3),
                  draw.text('lipper', 90, text_y, WHITE, font=3)]


def screen(page, selected, header=True):
    if header:
        ops = [draw.clear(BACKGROUND)] + branding(10, 12, 26)
    else:
        # Menu and Move share their header; only replace the body.
        ops = [draw.rectangle(BACKGROUND, 0, 50, 239, 319)]
    ops += heading('Menu' if page == 'main' else 'Move (10mm)', 52)
    labels = MAIN if page == 'main' else MOVES
    names = (('home', 'spool', 'spool', 'temperature', 'right')
             if page == 'main' else
             ('up', 'down', 'left', 'right', 'up', 'down', 'left'))
    for index, label in enumerate(labels):
        y = ROWS[page][index]
        ops += icon(names[index], 15, y)
        if page == 'main' and index == 3:
            ops += [draw.text('Preheat -', 39, y),
                    draw.text('B50, N170', 119, y, ORANGE)]
        else:
            ops.append(draw.text(label, 39, y))
        if index != len(labels)-1 or page == 'main':
            ops += icon('right', 215, y, WHITE)
    if page == 'main':
        for label, y in (('Filament', 100), ('Temperature', 176),
                         ('Movement', 224)):
            ops += heading(label, y)
        ops += [draw.rectangle(CYAN, 14, 278, 225, 278),
                centered('Press&Hold Knob: Idle Screen', 292)]
    else:
        for y in (133, 200, 271):
            ops.append(draw.rectangle(CYAN, 14, y, 225, y))
    return ops + selection(page, selected)


def message(title, detail):
    # The host supplies bounded lines; the driver has no message templates.
    ops = [draw.clear(BACKGROUND), centered(title[:26], 72)]
    value = detail.encode('gbk', 'replace').decode('gbk')
    lines, line = [], ''
    for char in value:
        if len((line+char).encode('gbk')) > 26:
            lines.append(line)
            line = ''
        line += char
        if len(lines) == 5:
            break
    if line and len(lines) < 5:
        lines.append(line)
    for index, line in enumerate(lines):
        ops.append(centered(line, 115+index*20))
    return ops


def temperature_value(value):
    try:
        value = float(value)
        if (not math.isnan(value) and not math.isinf(value)
            and 0. <= value <= 500.):
            return int(value+.5)
    except (ValueError, TypeError, OverflowError):
        pass
    return None


def filament_temperature(value):
    text = 'Nozzle: --' if value is None else 'Nozzle: %d C' % value
    ops = [draw.rectangle(BACKGROUND, 16, 229, 223, 254),
           centered(text, 230, font=3)]
    if value is not None:
        # The panel's degree glyph is blank; compose its ring on the host.
        x = (240-len(text)*12)//2 + (len(text)-2)*12 + 2
        ops += _ellipse(x+3, 235, 3, 3, WHITE, width=1)
    return ops


def filament_phase(value):
    return [draw.rectangle(BACKGROUND, 16, 260, 223, 278),
            centered(value, 262)]


def _line(x0, y0, x1, y1, color, width=1):
    ops = []
    steps = max(abs(x1-x0), abs(y1-y0), 1)
    for index in range(steps+1):
        x = x0+(x1-x0)*index//steps
        y = y0+(y1-y0)*index//steps
        ops.append(draw.rectangle(color, x, y, x+width-1, y+width-1))
    return ops


def _ellipse(cx, cy, rx, ry, color, width=2):
    spans = []
    for dy in range(-ry, ry+1):
        outer = int(rx*math.sqrt(max(0., 1.-float(dy*dy)/(ry*ry))))
        inner = -1
        if abs(dy) < ry-width:
            inner = int((rx-width)*math.sqrt(
                max(0., 1.-float(dy*dy)/((ry-width)**2))))
        runs = [(cx-outer, cx+outer)] if inner < 0 else [
            (cx-outer, cx-inner-1), (cx+inner+1, cx+outer)]
        for left, right in runs:
            if left > right:
                continue
            # Merge equal runs on consecutive rows before packing operations.
            for span in reversed(spans[-4:]):
                if span[0] == left and span[2] == right and span[3] == cy+dy-1:
                    span[3] += 1
                    break
            else:
                spans.append([left, cy+dy, right, cy+dy])
    return [draw.rectangle(color, *span) for span in spans]


def filament_screen(action, temperature, phase):
    ops = [draw.clear(BACKGROUND)] + box(3, 3, 236, 316, CYAN)
    # Rectangular corner strokes replace slow pixel-by-pixel diagonals.
    for x, y, dx, dy in ((10, 8, 26, 26), (229, 8, -26, 26),
                         (10, 309, 20, -20), (229, 309, -20, -20)):
        ops += [draw.rectangle(CYAN, min(x, x+dx), y, max(x, x+dx), y),
                draw.rectangle(CYAN, x, min(y, y+dy), x, max(y, y+dy))]
    ops += branding(22, 22, 60)
    ops += [centered('Load Filament' if action == 'load' else 'Unload Filament',
                     108, font=3)]
    for y in (96, 218, 286):
        ops += [draw.rectangle(CYAN, 20, y, 219, y),
                draw.rectangle(0x8fff, 110, y-1, 129, y+1)]
    # Spool, filament path, direction arrow, and hotend are host artwork.
    ops += _ellipse(52, 176, 18, 31, WHITE)
    ops += [draw.rectangle(CYAN, 52, 146, 78, 206)]
    for x in (57, 63, 69):
        ops += [draw.rectangle(0x3290, x, 150, x+1, 202)]
    ops += _ellipse(78, 176, 17, 31, BACKGROUND, width=31)
    ops += _ellipse(78, 176, 17, 31, WHITE)
    ops += _ellipse(78, 176, 4, 10, WHITE)
    for x0, y0, x1, y1 in ((76, 153, 78, 159), (76, 193, 78, 199),
                             (65, 171, 68, 174), (87, 171, 90, 174)):
        ops.append(draw.rectangle(WHITE, x0, y0, x1, y1))
    ops += [draw.rectangle(CYAN, 90, 195, 157, 197)]
    ops += _line(157, 195, 168, 189, CYAN, 2)
    arrow = 'right' if action == 'load' else 'left'
    ops += icon(arrow, 114, 170)
    ops += box(168, 155, 207, 182, WHITE)
    ops += box(176, 146, 199, 154, WHITE)
    ops += box(178, 183, 197, 192, WHITE)
    ops += _line(178, 193, 187, 202, WHITE, 2)
    ops += _line(187, 202, 197, 193, WHITE, 2)
    ops += _ellipse(187, 168, 3, 3, WHITE)
    for dx, dy in ((-8, -6), (5, -6), (-8, 5), (5, 5)):
        ops.append(draw.rectangle(WHITE, 187+dx, 168+dy,
                                   190+dx, 171+dy))
    for x in (179, 187, 195):
        ops += _line(x, 205, x-2, 209, ORANGE, 2)
        ops += _line(x-2, 209, x, 213, ORANGE, 2)
    ops += _ellipse(49, 303, 9, 9, WHITE, width=1)
    ops += [draw.rectangle(WHITE, 46, 300, 52, 306),
            draw.text('Press to cancel', 66, 295)]
    return ops + filament_temperature(temperature) + filament_phase(phase)
