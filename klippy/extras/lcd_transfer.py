# Host-side static job payload; the C MCU driver owns LCD operations
#
# Copyright (C) 2026 navaismo
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import math, posixpath, re, struct, zlib

VERSION = 5
CHUNK_SIZE = 40
TEXT_FIELDS = ('total_layers', 'layer_height', 'estimated_time', 'material',
               'filename', 'nozzle_target', 'bed_target',
               'nozzle_label', 'bed_label')
# Two bounded lines on the 240x320 panel; GBK characters need two cells.
TEXT_MAX_BYTES = (26, 26, 26, 26, 52, 8, 8, 8, 8)
HEADER = struct.Struct('>4sBBHHHHHHHHHHHHI')
# Header flags identify known values; unknown labels already contain "--".
HAS_LAYERS = 1
HAS_HEIGHT = 2
HAS_TIME = 4
HAS_MATERIAL = 8
HAS_THUMBNAIL = 16
HAS_NOZZLE = 32
HAS_BED = 64
IDLE_SCREEN = 128


class TransferError(ValueError):
    pass


def _number(value, integer=False):
    if value is None:
        return None
    try:
        if isinstance(value, bool):
            raise ValueError()
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        raise TransferError('Expected a finite numeric job field')
    if (math.isnan(number) or math.isinf(number) or number <= 0.
        or number > 4294967295):
        raise TransferError('Job number is outside the supported range')
    if integer and number != int(number):
        raise TransferError('Total layers must be an integer')
    return int(number) if integer else number


def _text_bytes(value, limit):
    # DWIN_Draw_String in the reference uses GBK. Never split a character.
    chunks = []
    size = 0
    for char in value:
        encoded = char.encode('gbk', 'replace')
        if size + len(encoded) > limit:
            break
        chunks.append(encoded)
        size += len(encoded)
    return b''.join(chunks)


def _format_duration(seconds):
    if seconds is None:
        return None
    total = int(math.floor(seconds + .5))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return '%02d:%02d:%02d' % (hours, minutes, seconds)


def _temperature(value):
    if value is None:
        return None
    if isinstance(value, bool):
        raise TransferError('Invalid target temperature')
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise TransferError('Invalid target temperature')
    if math.isnan(number) or math.isinf(number) or not 0. <= number <= 500.:
        raise TransferError('Invalid target temperature')
    # Limit the display width; targets are formatted here, never on the MCU.
    return u'%.0f\u00b0C' % number


def prepare_transfer(job):
    flags = 0
    layers = _number(job.get('total_layers'), integer=True)
    height = _number(job.get('layer_height'))
    seconds = _number(job.get('estimated_time'))
    duration = _format_duration(seconds)
    material = job.get('material')
    nozzle = _temperature(job.get('nozzle_target'))
    bed = _temperature(job.get('bed_target'))
    texts = [
        'Layers: %s' % (layers if layers is not None else '--'),
        'Height: %s' % ('%.6gmm' % height
                              if height is not None else '--'),
        'ETA: %s' % (duration if duration is not None else '--'),
        'Mat: %s' % (material or '--'),
        _display_filename(job['filename']),
        nozzle or '--',
        bed or '--',
        'Nozzle',
        'Bed',
    ]
    encoded = [_text_bytes(text, limit)
               for text, limit in zip(texts, TEXT_MAX_BYTES)]
    display = dict((name, data.decode('gbk'))
                   for name, data in zip(TEXT_FIELDS, encoded))
    for value, flag in ((layers, HAS_LAYERS), (height, HAS_HEIGHT),
                        (seconds, HAS_TIME), (material or None, HAS_MATERIAL),
                        (nozzle, HAS_NOZZLE), (bed, HAS_BED)):
        if value is not None:
            flags |= flag
    raw = b''
    width = height = 0
    thumb = job.get('thumbnail')
    if thumb is not None:
        image = thumb.get('rgb565')
        if image is None:
            raise TransferError('Thumbnail RGB565 data has not been prepared')
        raw = image['data']
        width, height = image['width'], image['height']
        if (image['format'] != 'rgb565be' or width != 96 or height != 96
            or len(raw) != width * height * 2):
            raise TransferError('Expected a 96x96 RGB565 thumbnail')
        flags |= HAS_THUMBNAIL
    job['estimated_time_formatted'] = duration
    job['display'] = display
    image_info = None
    if raw:
        raw, image_info = encode_image(raw)
    result = _pack(flags, encoded, width, height, raw)
    result['image'] = image_info
    return result


def _display_filename(filename):
    name = posixpath.basename(filename)
    base, ext = posixpath.splitext(name)
    return base if ext.lower() in ('.gcode', '.gco', '.g') and base else name


def image_rectangles(raw):
    # Lossless relative to RGB565. Extend equal horizontal runs vertically,
    # then group across the whole image by color (not just within each row).
    if len(raw) != 18432:
        raise TransferError('Expected a 96x96 RGB565 thumbnail')
    pixels = struct.unpack('>9216H', raw)
    active, rectangles = {}, []
    for y in range(96):
        current, x = {}, 0
        while x < 96:
            color, start = pixels[y*96+x], x
            x += 1
            while x < 96 and pixels[y*96+x] == color:
                x += 1
            if not color:
                continue
            key = color, start, x-1
            previous = active.pop(key, None)
            current[key] = (color, start, previous[2] if previous else y,
                            x-1, y)
        rectangles.extend(active.values())
        active = current
    rectangles.extend(active.values())
    return sorted(rectangles)


def encode_image(raw):
    rectangles = image_rectangles(raw)
    if 1 + len(rectangles)*6 < 1 + len(raw):
        data = b'\x01' + b''.join(struct.pack('>HBBBB', *rect)
                                 for rect in rectangles)
        encoding = 'rects565'
    else:
        data, encoding = b'\x00' + raw, 'rgb565be'
    return data, {'encoding': encoding, 'size': len(data),
                  'rectangles': len(rectangles), 'raw_size': len(raw),
                  'colors': len(set(r[0] for r in rectangles))}


def _pack(flags, encoded, width=0, height=0, raw=b''):
    header = HEADER.pack(b'KLCD', VERSION, flags, HEADER.size,
                         width, height, *(list(map(len, encoded)) + [len(raw)]))
    data = header + b''.join(encoded) + raw
    return {'version': VERSION, 'format': 'klcd-job-v5',
            'size': len(data), 'crc32': zlib.crc32(data) & 0xffffffff,
            'chunk_size': CHUNK_SIZE,
            'chunk_count': (len(data) + CHUNK_SIZE - 1) // CHUNK_SIZE,
            'data': data}


def _firmware_line(version, compact=False):
    # Preserve revision/build identity within the selected idle text slot.
    # Read no files here: version comes from the connected MCU identification.
    revision = re.search(r'(?:^|-)g?([0-9a-fA-F]{7,40})(?:-|$)', version)
    stamp = re.search(r'(?:^|-)([0-9]{8}_[0-9]{6})(?:-|$)', version)
    if revision and stamp:
        if compact:
            # Older v5 firmware has only a 20-cell printer-name slot free.
            return '%s %s' % (revision.group(1)[:8], stamp.group(1)[4:])
        return 'FW %s %s' % (revision.group(1)[:8], stamp.group(1))
    return 'FW: ' + version


def prepare_idle(mcu_version=None, version_footer=False):
    # Same bounded transport; the MCU draws the logo and chooses idle layout.
    # Unused slots stay nonempty to retain the receiver's text validation.
    model, firmware = 'Ender 3 V3 SE', '--'
    if mcu_version is not None:
        if version_footer:
            firmware = _firmware_line(mcu_version)
        else:
            model = _firmware_line(mcu_version, compact=True)
    texts = ('Klipper', model, 'github.com/navaismo/klipper',
             firmware, 'Idle', '--', '--', '--', '--')
    limits = (12, 20, 27, 27, 20, 2, 2, 2, 2)
    encoded = [_text_bytes(text, limit)
               for text, limit in zip(texts, limits)]
    return _pack(IDLE_SCREEN, encoded)


def iter_chunks(transfer):
    # These are application bytes, not Klipper packets or LCD serial frames.
    data = transfer['data']
    if (len(data) != transfer['size']
        or zlib.crc32(data) & 0xffffffff != transfer['crc32']):
        raise TransferError('Transfer size or CRC32 does not match the data')
    for offset in range(0, len(data), CHUNK_SIZE):
        yield offset, data[offset:offset + CHUNK_SIZE]
