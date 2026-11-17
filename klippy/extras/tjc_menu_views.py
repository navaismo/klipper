# Host-owned information pages and isometric bed mesh rendering
# Copyright (C) 2026
# Projection and palette adapted from MarlinFirmware's BedLevelSurface.
# This file may be distributed under the terms of the GNU GPLv3 license.
import math
from array import array
try:
    from . import lcd_draw as draw, tjc_menu_layout as layout
except ImportError:
    import lcd_draw as draw, tjc_menu_layout as layout

GRID_COLOR, AXIS_COLOR = 0x3186, 0x7bef


def information(title, lines):
    ops = [draw.clear(layout.BACKGROUND)] + layout.branding(10, 12, 26)
    ops += layout.heading(title, 52)
    wrapped = []
    for line in lines:
        current = ''
        for char in str(line).encode('gbk', 'replace').decode('gbk'):
            if ord(char) < 32 or ord(char) == 127:
                char = ' '
            if len((current+char).encode('gbk')) > 27:
                wrapped.append(current)
                current = ''
            current += char
        wrapped.append(current)
    for index, line in enumerate(wrapped[:10]):
        if line:
            ops.append(draw.text(line, 12, 80+index*19))
    return ops + [layout.centered('Click: Back', 292)]


def mesh_color(z, scale):
    # Zero is green; negative/positive heights progress toward blue/red.
    index = max(0, min(32, int(math.floor((z/scale+1)*16+.5))))
    ramp = (index % 8)*255//8
    if index < 8:
        r, g, b = 0, ramp, 255
    elif index < 16:
        r, g, b = 0, 255, 255-ramp
    elif index < 24:
        r, g, b = ramp, 255, 0
    else:
        r, g, b = 255, 0 if index == 32 else 255-ramp, 0
    return ((r & 0xf8) << 8) | ((g & 0xfc) << 3) | (b >> 3)


def mesh_screen(matrix):
    if (not isinstance(matrix, (list, tuple)) or not 2 <= len(matrix) <= 64
        or not isinstance(matrix[0], (list, tuple))
        or not 2 <= len(matrix[0]) <= 64):
        raise ValueError('No displayable bed mesh (2..64 points per axis)')
    rows, cols = len(matrix), len(matrix[0])
    values = []
    for row in matrix:
        if not isinstance(row, (list, tuple)) or len(row) != cols:
            raise ValueError('Bed mesh has inconsistent rows')
        for value in row:
            if (not isinstance(value, (int, float))
                or math.isnan(value) or math.isinf(value)):
                raise ValueError('Bed mesh contains unmeasured points')
            values.append(float(value))
    low, high = min(values), max(values)
    center = (low+high)*.5
    height_scale = 24./max(high-low, .1)
    color_scale = max(abs(low), abs(high), .05)
    left, top, right, bottom = 24, 150, 215, 246
    width, height = right-left+1, bottom-top+1
    pixels = array('i', [-1])*(width*height)

    def project(x, y):
        u, v = float(x)/(cols-1), float(y)/(rows-1)
        z = float(matrix[y][x])
        return (119.5+(u-v)*95.5,
                198.+(1-u-v)*32.-(z-center)*height_scale, z)

    def grid_line(a, b):
        x0, y0 = [int(math.floor(v+.5)) for v in a[:2]]
        x1, y1 = [int(math.floor(v+.5)) for v in b[:2]]
        dx, dy = abs(x1-x0), -abs(y1-y0)
        sx, sy = 1 if x0 < x1 else -1, 1 if y0 < y1 else -1
        error = dx+dy
        while True:
            if left <= x0 <= right and top <= y0 <= bottom:
                pixels[(y0-top)*width+x0-left] = GRID_COLOR
            if x0 == x1 and y0 == y1:
                break
            twice = 2*error
            if twice >= dy:
                error += dy
                x0 += sx
            if twice <= dx:
                error += dx
                y0 += sy

    def triangle(a, b, c):
        area = ((b[1]-c[1])*(a[0]-c[0])
                + (c[0]-b[0])*(a[1]-c[1]))
        if abs(area) < .001:
            return
        x0 = max(left, int(math.floor(min(a[0], b[0], c[0]))))
        x1 = min(right, int(math.ceil(max(a[0], b[0], c[0]))))
        y0 = max(top, int(math.floor(min(a[1], b[1], c[1]))))
        y1 = min(bottom, int(math.ceil(max(a[1], b[1], c[1]))))
        for y in range(y0, y1+1):
            for x in range(x0, x1+1):
                wa = ((b[1]-c[1])*(x+.5-c[0])
                      + (c[0]-b[0])*(y+.5-c[1]))/area
                wb = ((c[1]-a[1])*(x+.5-c[0])
                      + (a[0]-c[0])*(y+.5-c[1]))/area
                wc = 1-wa-wb
                if min(wa, wb, wc) >= -.00001:
                    pixels[(y-top)*width+x-left] = mesh_color(
                        wa*a[2]+wb*b[2]+wc*c[2], color_scale)

    # Paint far (+X,+Y) cells first, as in Marlin's isometric surface.
    for diagonal in range(cols+rows-4, -1, -1):
        for x in range(max(0, diagonal-rows+2), min(diagonal, cols-2)+1):
            y = diagonal-x
            a, b = project(x, y), project(x+1, y)
            c, d = project(x+1, y+1), project(x, y+1)
            triangle(a, b, c)
            triangle(a, c, d)
            # Outline the actual cell perimeter, without the triangle's
            # internal diagonal. Nearer cells occlude far edges naturally.
            for start, end in ((a, b), (b, c), (c, d), (d, a)):
                grid_line(start, end)
    ops = [draw.clear(layout.BACKGROUND)] + layout.branding(10, 12, 26)
    ops += layout.heading('Bed Grid IsoMap', 52)
    for index, value in enumerate(('Min: %+.3f mm' % low,
                                   'Max: %+.3f mm' % high,
                                   'Range: %.3f mm' % (high-low))):
        # Bounds remain valid even for unusually large numeric values.
        ops.append(draw.text(value[:18], 12, 76+index*18))
    ops += [draw.text('%dx%d' % (cols, rows), 180, 76),
            draw.text('Z scaled', 164, 112)]
    # Marlin draws axes below the surface, with labels beyond its envelope.
    origin = (120, 245)
    for endpoint in ((226, 209), (13, 209), (120, 152)):
        ops += layout._line(origin[0], origin[1], endpoint[0], endpoint[1],
                            AXIS_COLOR)
    for y in range(height):
        x = 0
        while x < width:
            color = pixels[y*width+x]
            start = x
            while x < width and pixels[y*width+x] == color:
                x += 1
            if color >= 0:
                ops.append(draw.rectangle(color, left+start, top+y,
                                          left+x-1, top+y))
    for index in range(33):
        ops.append(draw.rectangle(mesh_color((index/16.-1)*color_scale,
                                              color_scale),
                                  20+index*200//33, 258,
                                  20+(index+1)*200//33-1, 265))
    legend_low, legend_high = ('%+.2f' % -color_scale,
                               '%+.2f' % color_scale)
    if len(legend_high) > 8:
        legend_low, legend_high = ('%+.0e' % -color_scale,
                                   '%+.0e' % color_scale)
    legend_high += ' mm'
    ops += [draw.text('Y', 2, 203), draw.text('X', 230, 203),
            draw.text('Z', 126, 136),
            draw.text(legend_low, 20, 270), draw.text('0', 116, 270),
            draw.text(legend_high, 220-len(legend_high)*8, 270),
            layout.centered('Click: Back', 298)]
    return ops
