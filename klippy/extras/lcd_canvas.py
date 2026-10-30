# Host-prepared semantic drawing operations for the TJC coordinate canvas
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.

BACKGROUND = 0x0022
PANEL = 0x0864
ACTIVE = 0x08c7
CYAN = 0x05ff
BLUE = 0x047f
ORANGE = 0xfd00
WHITE = 0xffff
DIM = 0x6bd3
GREEN = 0x07e0
RED = 0xf920
PHASES = ('cr_touch', 'nozzle', 'computing')
LABELS = ('CR Touch Reference', 'Nozzle Contact', 'Computing Offset')
ICONS = (
    (0x07e0, 0x1818, 0x2004, 0x4002, 0x47e2, 0x8811, 0x8991, 0x8991,
     0x8811, 0x47e2, 0x4182, 0x2184, 0x1988, 0x07e0, 0x0180, 0x0180),
    (0x0ff0, 0x0ff0, 0x0ff0, 0x0ff0, 0x07e0, 0x03c0, 0x0180, 0x0180,
     0, 0x4002, 0x2004, 0x1008, 0, 0x7ffe, 0x7ffe, 0),
    (0x0180, 0x399c, 0x3ffc, 0x1ff8, 0x700e, 0x77ee, 0x3c3c, 0xfc3f,
     0xfc3f, 0x3c3c, 0x77ee, 0x700e, 0x1ff8, 0x3ffc, 0x399c, 0x0180))


def rect(x0, y0, x1, y1, color):
    return ('rect', x0, y0, x1, y1, color)


def text(x, y, value, color=WHITE, background=BACKGROUND, font=1):
    return ('text', x, y, font, color, background, value)


def validate(operations):
    # Validate the complete session before claiming the MCU UART.
    result = []
    for op in operations:
        if op[0] == 'rect' and len(op) == 6:
            _, x0, y0, x1, y1, color = op
            if not (0 <= x0 <= x1 < 240 and 0 <= y0 <= y1 < 320
                    and 0 <= color <= 65535):
                raise ValueError('Canvas rectangle outside display bounds')
            values = op[1:]
        elif op[0] == 'text' and len(op) == 7:
            _, x, y, font, color, background, value = op
            data = value.encode('gbk', 'replace')
            width, height = (8, 16) if font == 1 else (12, 24)
            if not (font in (1, 3) and 0 < len(data) <= 36
                    and x >= 0 and x+len(data)*width <= 240
                    and y >= 0 and y+height <= 320
                    and 0 <= color <= 65535 and 0 <= background <= 65535
                    and all(ord(c) >= 32 and ord(c) != 127 for c in value)):
                raise ValueError('Invalid canvas text/font/bounds')
            values = (x, y, font, color, background)
            op = tuple(op[:-1]) + (data,)
        else:
            raise ValueError('Unknown canvas operation')
        if any(not isinstance(v, int) for v in values):
            raise ValueError('Canvas coordinates/colors must be integers')
        result.append(op)
        if len(result) > 511:
            raise ValueError('Canvas session exceeds 511 operations')
    return result


def _icon(index, color, y):
    ops = []
    for row, bits in enumerate(ICONS[index]):
        col = 0
        while col < 16:
            if not bits & (0x8000 >> col):
                col += 1
                continue
            start = col
            while col < 16 and bits & (0x8000 >> col):
                col += 1
            ops.append(rect(22+start, y+row, 21+col, y+row, color))
    return ops


def row(index, active=False, outcome=None):
    y = 211 + index*32
    background = ACTIVE if active else PANEL
    color = RED if outcome == 'error' else GREEN if outcome == 'done' else (
        CYAN if active else DIM)
    ops = [rect(14, y, 225, y+29, background)]
    ops.extend(_icon(index, color, y+6))
    ops.append(text(48, y+7, LABELS[index], WHITE if active else color,
                    background))
    if active:
        for i in range(6):
            ops.append(rect(213+i, y+8+i, 214+i, y+9+i, ORANGE))
            ops.append(rect(213+i, y+20-i, 214+i, y+21-i, ORANGE))
    else:
        ops += [rect(213, y+9, 219, y+9, color),
                rect(213, y+21, 219, y+21, color),
                rect(211, y+11, 211, y+19, color),
                rect(221, y+11, 221, y+19, color)]
    return ops


def status(value):
    value = value.replace('Calibrate', 'Cal').replace('pair ', '#')
    return [rect(8, 5, 231, 21, BACKGROUND),
            text(8, 6, value[:28])]


def initial_view(value):
    # Code-native approximation of the owner's portrait reference artwork.
    ops = []
    for i in range(20):
        ops += [rect(92+i, 53+i, 95+i, 53+i, CYAN),
                rect(144-i, 53+i, 147-i, 53+i, CYAN)]
    ops += [rect(92, 31, 95, 53, BLUE), rect(144, 31, 147, 53, BLUE),
            rect(110, 37, 129, 39, BLUE), rect(110, 43, 129, 45, BLUE),
            rect(110, 49, 129, 51, BLUE), rect(102, 73, 137, 75, CYAN),
            rect(110, 79, 129, 88, ORANGE)]
    for i in range(9):
        ops.append(rect(111+i, 89+i, 128-i, 89+i, ORANGE))
    for y in range(101, 113, 4):
        ops.append(rect(119, y, 120, y+1, ORANGE))
    ops += [rect(63, 115, 176, 117, CYAN),
            rect(55, 123, 184, 125, CYAN),
            rect(60, 127, 67, 131, BLUE), rect(172, 127, 179, 131, BLUE)]
    for i in range(8):
        ops += [rect(63-i, 115+i, 64-i, 115+i, CYAN),
                rect(175+i, 115+i, 176+i, 115+i, CYAN)]
    ops += [text(42, 140, 'Auto', ORANGE, font=3),
            text(102, 140, 'Z-Offset', font=3),
            text(54, 169, 'Calibration', font=3),
            rect(16, 202, 223, 202, BLUE), rect(16, 309, 223, 309, BLUE),
            rect(8, 210, 8, 301, BLUE), rect(231, 210, 231, 301, BLUE),
            rect(108, 201, 131, 203, CYAN),
            rect(108, 308, 131, 310, CYAN)]
    for i in range(8):
        ops += [rect(16-i, 202+i, 16-i, 202+i, BLUE),
                rect(223+i, 202+i, 223+i, 202+i, BLUE),
                rect(8+i, 302+i, 8+i, 302+i, BLUE),
                rect(231-i, 302+i, 231-i, 302+i, BLUE)]
    for index in range(3):
        ops.extend(row(index))
    return ops + status(value)


def highlight(previous, phase, value, outcome=None):
    if phase not in PHASES:
        raise ValueError('Unknown calibration phase')
    ops = []
    if previous in PHASES and previous != phase:
        ops.extend(row(PHASES.index(previous)))
    ops.extend(row(PHASES.index(phase), True, outcome))
    return ops + status(value)
