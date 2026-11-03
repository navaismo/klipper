# Host-owned firmware identification on the static idle screen
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import datetime
import re
try:
    from . import lcd_draw, lcd_transfer
except ImportError:
    import lcd_draw, lcd_transfer


def firmware_text(version, limit=28):
    # The footer formats the build date separately. The source is always
    # the connected MCU, never host Git or the host's current date.
    revision = re.split(r'-\d{8}_\d{6}(?:-|$)', version or '--',
                        maxsplit=1)[0]
    revision = ''.join(c if ord(c) >= 32 and ord(c) != 127 else ' '
                       for c in revision).strip() or '--'
    return lcd_transfer._text_bytes('FW: ' + revision, limit).decode('gbk')


# Host-owned 5x7 glyphs keep the existing Idle text and artwork untouched.
# Rows use five bits, with a one-pixel gap between characters.
SMALL_FONT = dict((char, tuple(int(row, 16) for row in rows.split()))
                 for char, rows in (
    (' ', '00 00 00 00 00 00 00'),
    ('?', '0e 11 01 02 04 00 04'),
    (':', '00 04 04 00 04 04 00'),
    ('-', '00 00 00 1f 00 00 00'),
    ('.', '00 00 00 00 00 0c 0c'),
    ('0', '0e 11 13 15 19 11 0e'),
    ('1', '04 0c 04 04 04 04 0e'),
    ('2', '0e 11 01 02 04 08 1f'),
    ('3', '1f 02 04 02 01 11 0e'),
    ('4', '02 06 0a 12 1f 02 02'),
    ('5', '1f 10 1e 01 01 11 0e'),
    ('6', '06 08 10 1e 11 11 0e'),
    ('7', '1f 01 02 04 08 08 08'),
    ('8', '0e 11 11 0e 11 11 0e'),
    ('9', '0e 11 11 0f 01 02 0c'),
    ('A', '0e 11 11 1f 11 11 11'),
    ('B', '1e 11 11 1e 11 11 1e'),
    ('C', '0e 11 10 10 10 11 0e'),
    ('D', '1e 11 11 11 11 11 1e'),
    ('E', '1f 10 10 1e 10 10 1f'),
    ('F', '1f 10 10 1e 10 10 10'),
    ('G', '0e 11 10 17 11 11 0f'),
    ('H', '11 11 11 1f 11 11 11'),
    ('I', '0e 04 04 04 04 04 0e'),
    ('J', '07 02 02 02 02 12 0c'),
    ('K', '11 12 14 18 14 12 11'),
    ('L', '10 10 10 10 10 10 1f'),
    ('M', '11 1b 15 15 11 11 11'),
    ('N', '11 19 15 13 11 11 11'),
    ('O', '0e 11 11 11 11 11 0e'),
    ('P', '1e 11 11 1e 10 10 10'),
    ('Q', '0e 11 11 11 15 12 0d'),
    ('R', '1e 11 11 1e 14 12 11'),
    ('S', '0f 10 10 0e 01 01 1e'),
    ('T', '1f 04 04 04 04 04 04'),
    ('U', '11 11 11 11 11 11 0e'),
    ('V', '11 11 11 11 11 0a 04'),
    ('W', '11 11 11 15 15 15 0a'),
    ('X', '11 11 0a 04 0a 11 11'),
    ('Y', '11 11 0a 04 04 04 04'),
    ('Z', '1f 01 02 04 08 10 1f'),
    ('a', '00 00 0e 01 0f 11 0f'),
    ('b', '10 10 16 19 11 11 1e'),
    ('c', '00 00 0e 10 10 11 0e'),
    ('d', '01 01 0d 13 11 11 0f'),
    ('e', '00 00 0e 11 1f 10 0e'),
    ('f', '06 09 08 1c 08 08 08'),
    ('g', '00 0f 11 11 0f 01 0e'),
    ('h', '10 10 16 19 11 11 11'),
    ('i', '04 00 0c 04 04 04 0e'),
    ('j', '02 00 06 02 02 12 0c'),
    ('k', '10 10 12 14 18 14 12'),
    ('l', '0c 04 04 04 04 04 0e'),
    ('m', '00 00 1a 15 15 11 11'),
    ('n', '00 00 16 19 11 11 11'),
    ('o', '00 00 0e 11 11 11 0e'),
    ('p', '00 1e 11 11 1e 10 10'),
    ('q', '00 0f 11 11 0f 01 01'),
    ('r', '00 00 16 19 10 10 10'),
    ('s', '00 00 0f 10 0e 01 1e'),
    ('t', '08 08 1c 08 08 09 06'),
    ('u', '00 00 11 11 11 13 0d'),
    ('v', '00 00 11 11 11 0a 04'),
    ('w', '00 00 11 11 15 15 0a'),
    ('x', '00 00 11 0a 04 0a 11'),
    ('y', '00 11 11 11 0f 01 0e'),
    ('z', '00 00 1f 02 04 08 1f')))


def firmware_footer_text(version):
    stamp = re.search(r'-(\d{4})(\d{2})(\d{2})_\d{6}(?:-|$)',
                      version or '')
    date = '--'
    if stamp is not None:
        try:
            date = datetime.date(*map(int, stamp.groups())).isoformat()
        except ValueError:
            pass
    return firmware_text(version, 18) + ' ' + date


def firmware_footer(version):
    label = firmware_footer_text(version)
    width = len(label)*6 - 1
    x, y = (240-width)//2, 309
    return small_text(label, x, y)


def small_text(label, x, y, color=0x8d7b):
    pixels = [set() for row in range(7)]
    for index, char in enumerate(label):
        for row, bits in enumerate(SMALL_FONT.get(char, SMALL_FONT['?'])):
            pixels[row].update(index*6+col for col in range(5)
                               if bits & (16 >> col))
    # Coalesce vertical runs to keep the small host font transfer bounded.
    ops, active = [], {}
    for row in range(8):
        cols = sorted(pixels[row]) if row < 7 else []
        runs, start, end = [], None, None
        for col in cols:
            if start is None:
                start = end = col
            elif col == end + 1:
                end = col
            else:
                runs.append((start, end))
                start = end = col
        if start is not None:
            runs.append((start, end))
        current = dict((run, active.pop(run, row)) for run in runs)
        for (left, right), top in sorted(active.items()):
            ops.append(lcd_draw.rectangle(color, x+left, y+top,
                                          x+right, y+row-1))
        active = current
    return ops
