# Host-side generic drawing operations for the mainboard TJC driver
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import struct
import zlib
from array import array

VERSION = 1
WIDTH, HEIGHT = 240, 320
FONTS = {1: (8, 16), 3: (12, 24)}
MAX_SIZE = 16000


def clear(color):
    return struct.pack('>BH', 1, color)


def rectangle(color, x0, y0, x1, y1):
    return struct.pack('>BHHHHH', 2, color, x0, y0, x1, y1)


def text(value, x, y, color=0xffff, background=0x0863, font=1):
    data = value.encode('gbk', 'replace')
    return struct.pack('>BBHHHH', 3, font, color, background, x, y) + data


def validate(op):
    if not isinstance(op, bytes) or not op or len(op) > 40:
        raise ValueError('Invalid LCD drawing operation size')
    code = bytearray(op)[0]
    if code == 1 and len(op) == 3:
        return
    if code == 2 and len(op) == 11:
        color, x0, y0, x1, y1 = struct.unpack('>HHHHH', op[1:])
        if x0 <= x1 < WIDTH and y0 <= y1 < HEIGHT:
            return
    if code == 3 and len(op) > 10:
        font, color, bg, x, y = struct.unpack('>BHHHH', op[1:10])
        data = op[10:]
        try:
            value = data.decode('gbk')
        except UnicodeError:
            raise ValueError('Invalid LCD drawing GBK text')
        if (font in FONTS and all(ord(c) >= 32 and ord(c) != 127
                                 for c in value)):
            width, height = FONTS[font]
            if x + len(data)*width <= WIDTH and y + height <= HEIGHT:
                return
    raise ValueError('Invalid LCD drawing operation or bounds')


def _rectangle_pass(operations):
    if len(operations) < 12:
        return operations
    rectangles = [struct.unpack('>HHHHH', op[1:]) for op in operations]
    left = min(r[1] for r in rectangles)
    top = min(r[2] for r in rectangles)
    width = max(r[3] for r in rectangles) - left + 1
    height = max(r[4] for r in rectangles) - top + 1
    # This bounded scratch image exists only on the host. -1 means untouched;
    # even background-colored painted pixels must survive partial updates.
    pixels = array('i', [-1]) * (width*height)
    for color, x0, y0, x1, y1 in rectangles:
        fill = array('i', [color]) * (x1-x0+1)
        for y in range(y0-top, y1-top+1):
            start = y*width + x0-left
            pixels[start:start+len(fill)] = fill

    def spans(transpose):
        cols, rows = (height, width) if transpose else (width, height)
        active, result = {}, []
        for row in range(rows+1):
            current, col = {}, 0
            while row < rows and col < cols:
                color = pixels[col*width+row if transpose else row*width+col]
                start = col
                col += 1
                while col < cols and pixels[
                        col*width+row if transpose else row*width+col] == color:
                    col += 1
                if color < 0:
                    continue
                key = color, start, col-1
                current[key] = active.pop(key, row)
            for (color, x0, x1), y0 in active.items():
                if transpose:
                    result.append((color, left+y0, top+x0,
                                   left+row-1, top+x1))
                else:
                    result.append((color, left+x0, top+y0,
                                   left+x1, top+row-1))
            active = current
        return result

    # Thin diagonal strokes can split differently in each orientation.
    merged = min((spans(False), spans(True)), key=len)
    if len(merged) >= len(operations):
        return operations
    return [rectangle(*r) for r in sorted(merged)]


def _optimize(operations):
    def compact(rectangles):
        merged = _rectangle_pass(rectangles)
        groups = []
        for op in rectangles:
            if not groups or groups[-1][0][1:3] != op[1:3]:
                groups.append([])
            groups[-1].append(op)
        if len(groups) < 2:
            return merged
        # Sometimes keeping overpaint is cheaper than disjoint color spans.
        layered = [op for group in groups for op in _rectangle_pass(group)]
        return min((merged, layered), key=len)

    result, pending = [], []
    for op in operations:
        code = bytearray(op)[0]
        if code == 2:
            pending.append(op)
            continue
        result.extend(compact(pending))
        pending = []
        # Text and clears retain painter order, including opaque text cells.
        result.append(op)
    return result + compact(pending)


def prepare(operations):
    source, size = [], 0
    for op in operations:
        validate(op)
        source.append(op)
        size += len(op)
        if size > MAX_SIZE:
            raise ValueError('LCD drawing exceeds bounded transfer size')
    if not source:
        raise ValueError('Empty LCD drawing')
    chunks, offset = [], 0
    for op in _optimize(source):
        chunks.append((offset, op))
        offset += len(op)
    data = b''.join(op for offset, op in chunks)
    return {'size': len(data), 'crc32': zlib.crc32(data) & 0xffffffff,
            'chunks': chunks}


def prepare_batches(operations):
    # Prepare and validate every batch before any LCD traffic begins.
    batches, pending, size = [], [], 0
    for op in operations:
        validate(op)
        if size + len(op) > MAX_SIZE:
            batches.append(prepare(pending))
            pending, size = [], 0
        pending.append(op)
        size += len(op)
    if pending:
        batches.append(prepare(pending))
    if not batches:
        raise ValueError('Empty LCD drawing')
    return batches
