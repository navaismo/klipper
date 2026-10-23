# Thumbnail preparation based on SlicerScripts/orca_parser.py
#
# Copyright (C) 2026 navaismo
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import base64, binascii, io, re, struct

SIZE = 96
QUANTIZE_COLORS = 64
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_SOURCE_PIXELS = 1024 * 1024
MAX_GCODE_BYTES = 2 * 1024 * 1024
RAW_BEGIN = '; E3V3SE_THUMB_RAW16_BEGIN 96x96'
RAW_END = '; E3V3SE_THUMB_RAW16_END'


class ThumbnailError(ValueError):
    pass


def source_rank(width, height):
    # Downsample a larger source when supplied, instead of preferring the
    # slicer's small preview. Bound downloads/decoded pixels separately.
    if min(width, height) >= SIZE*2:
        return (0, width*height)
    return (1, -min(width, height), -width*height)


def rgb888_to_565(r, g, b):
    # Match Marlin's slicer script: scaled integer division, not bit shifts.
    return ((r * 31 // 255) << 11) | ((g * 63 // 255) << 5) | (b * 31 // 255)


def convert_image(data, expected_format=None, colors=QUANTIZE_COLORS):
    if (isinstance(colors, bool) or not isinstance(colors, int)
        or not 2 <= colors <= 256):
        raise ThumbnailError('Thumbnail palette must have 2 to 256 colors')
    if len(data) > MAX_SOURCE_BYTES:
        raise ThumbnailError('Thumbnail exceeds the source size limit')
    try:
        from PIL import Image
    except ImportError:
        raise ThumbnailError('Thumbnail conversion requires Pillow; see '
                             'scripts/lcd-requirements.txt')
    try:
        with Image.open(io.BytesIO(data)) as source:
            image_format = source.format.lower()
            if image_format not in ('png', 'jpeg'):
                raise ThumbnailError('Only PNG/JPEG thumbnails are supported')
            if expected_format and image_format != expected_format:
                raise ThumbnailError('Thumbnail format differs from metadata')
            source_width, source_height = source.size
            if source_width * source_height > MAX_SOURCE_PIXELS:
                raise ThumbnailError('Thumbnail exceeds the pixel limit')
            source.verify()
        # Reopen after verify; convert forces complete pixel decoding.
        with Image.open(io.BytesIO(data)) as source:
            image = source.convert('RGB')
        if image.size != (SIZE, SIZE):
            resampling = getattr(Image, 'Resampling', Image)
            image = image.resize((SIZE, SIZE), resampling.LANCZOS)
        # Match the reference's RGB conversion, including its alpha behavior.
        source_pixels = image.load()
        original = [source_pixels[x, y] for y in range(SIZE)
                    for x in range(SIZE)]
        if any(pixel != (0, 0, 0) for pixel in original):
            quantize = getattr(Image, 'Quantize', Image)
            image = image.quantize(colors=colors,
                                   method=quantize.MEDIANCUT).convert('RGB')
        pixels = []
        quantized_pixels = image.load()
        for index, before in enumerate(original):
            after = quantized_pixels[index % SIZE, index // SIZE]
            # Match the reference literally: black after quantization is skip;
            # a non-black output that rounds down to RGB565 zero becomes 1.
            if before == (0, 0, 0) or after == (0, 0, 0):
                pixels.append(0)
            else:
                pixels.append(rgb888_to_565(*after) or 1)
        raw = struct.pack('>%dH' % (SIZE * SIZE,), *pixels)
        return {'width': SIZE, 'height': SIZE, 'format': 'rgb565be',
                'source_width': source_width, 'source_height': source_height,
                'colors': len(set(pixels)), 'size': len(raw), 'data': raw}
    except ThumbnailError:
        raise
    except (IOError, OSError, ValueError, SyntaxError,
            Image.DecompressionBombError):
        raise ThumbnailError('Invalid or truncated thumbnail image')


def extract_gcode_thumbnail(data):
    # Read only a bounded prefix; never rewrite the original G-code.
    if len(data) > MAX_GCODE_BYTES:
        raise ThumbnailError('G-code thumbnail scan exceeds the size limit')
    text = data.decode('ascii', 'replace')
    begin = re.compile(r'^;\s*(thumbnail(?:_(?:PNG|JPG|JPEG))?)\s+begin\s+'
                       r'(\d+)x(\d+)\s+(\d+)\s*$', re.I)
    candidates = []
    active = None
    chunks = []
    for line in text.splitlines():
        line = line.strip()
        match = begin.match(line)
        if match:
            active = match.groups()
            chunks = []
            continue
        if active is None:
            continue
        if re.match(r'^;\s*%s\s+end\s*$' % re.escape(active[0]), line, re.I):
            width, height, declared = [int(v) for v in active[1:]]
            encoded = ''.join(chunks)
            if (width <= 0 or height <= 0
                or width * height > MAX_SOURCE_PIXELS
                or len(encoded) != declared or len(encoded) % 4
                or not re.match(r'^[A-Za-z0-9+/]*={0,2}$', encoded)):
                raise ThumbnailError('Invalid G-code thumbnail block')
            try:
                image_data = base64.b64decode(encoded)
            except (ValueError, binascii.Error):
                raise ThumbnailError('Invalid thumbnail base64')
            if len(image_data) > MAX_SOURCE_BYTES:
                raise ThumbnailError('Embedded thumbnail exceeds size limit')
            rank = source_rank(width, height)
            image_format = ('jpeg' if 'jpg' in active[0].lower()
                            or 'jpeg' in active[0].lower() else 'png')
            candidates.append((rank, {
                'path': None, 'source': 'gcode', 'width': width,
                'height': height, 'format': image_format, 'data': image_data,
            }))
            active = None
        elif line.startswith(';'):
            chunks.append(line[1:].strip())
        else:
            raise ThumbnailError('Invalid line inside G-code thumbnail')
    if not candidates:
        raise ThumbnailError('No complete PNG/JPEG thumbnail in G-code prefix')
    return min(candidates, key=lambda item: item[0])[1]


def raw16_block(raw):
    if len(raw) != SIZE * SIZE * 2:
        raise ThumbnailError('Expected a 96x96 RGB565 image')
    rows = [RAW_BEGIN]
    for offset in range(0, len(raw), SIZE * 2):
        row = binascii.hexlify(raw[offset:offset + SIZE * 2])
        rows.append('; ' + row.decode('ascii').upper())
    return '\n'.join(rows + [RAW_END, ''])
