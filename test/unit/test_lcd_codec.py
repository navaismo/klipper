# Lossless image transport, physical text metrics and idle lifecycle checks
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import io, os, struct, sys, unittest
from PIL import Image
import test_tjc_job as jobs
import lcd_status, lcd_thumbnail, lcd_transfer


def decode_image(data):
    if data[0] == 0:
        return data[1:]
    pixels = [0] * 9216
    for offset in range(1, len(data), 6):
        color = int.from_bytes(data[offset:offset+2], 'big')
        x0, y0, x1, y1 = data[offset+2:offset+6]
        for y in range(y0, y1+1):
            for x in range(x0, x1+1):
                pixels[y*96+x] = color
    return struct.pack('>9216H', *pixels)


class CodecTests(unittest.TestCase):
    def test_solid_and_black_have_small_exact_encodings(self):
        for raw, expected in ((b'\xf8\x00'*9216,
                               b'\x01\xf8\x00\x00\x00\x5f\x5f'),
                              (b'\x00\x00'*9216, b'\x01')):
            data, info = lcd_transfer.encode_image(raw)
            self.assertEqual(data, expected)
            self.assertEqual(decode_image(data), raw)
            self.assertEqual(info['encoding'], 'rects565')

    def test_multicolor_rectangles_are_lossless_and_globally_grouped(self):
        raw = b''.join(struct.pack('>H', (0, 31, 2016, 63488)[
            (x//12+y//12) % 4]) for y in range(96) for x in range(96))
        data, info = lcd_transfer.encode_image(raw)
        self.assertEqual(info['encoding'], 'rects565')
        self.assertEqual(decode_image(data), raw)
        colors = [int.from_bytes(data[i:i+2], 'big')
                  for i in range(1, len(data), 6)]
        self.assertEqual(colors, sorted(colors))
        self.assertLess(len(data), len(raw)//10)

    def test_high_entropy_falls_back_without_expanding_the_wire_image(self):
        raw = b''.join(struct.pack('>H', (x+y) % 2 + 1)
                       for y in range(96) for x in range(96))
        data, info = lcd_transfer.encode_image(raw)
        self.assertEqual(info['encoding'], 'rgb565be')
        self.assertEqual(len(data), len(raw)+1)
        self.assertEqual(decode_image(data), raw)
        with self.assertRaises(lcd_transfer.TransferError):
            lcd_transfer.encode_image(b'bad')

    def test_source_preference_and_decoded_pixel_limit(self):
        thumbs = [{'width': n, 'height': n,
                   'relative_path': '%d.png' % n} for n in (96, 300, 192, 4096)]
        chosen = lcd_status.select_thumbnail('test.gcode', thumbs)
        self.assertEqual(chosen['width'], 192)
        chosen = lcd_status.select_thumbnail(
            'test.gcode', [thumbs[0], thumbs[3]])
        self.assertEqual(chosen['width'], 96)

    def test_64_colors_reduce_gradient_error(self):
        source = Image.new('RGB', (96, 96))
        original = [(x*255//95, y*255//95, (x+y)*255//190)
                    for y in range(96) for x in range(96)]
        source.putdata(original)
        out = io.BytesIO()
        source.save(out, format='PNG')
        errors = []
        for colors in (32, 64):
            raw = lcd_thumbnail.convert_image(out.getvalue(), colors=colors)
            error = 0
            words = struct.unpack('>9216H', raw['data'])
            for word, rgb in zip(words, original):
                restored = ((word >> 11)*255//31,
                            ((word >> 5) & 63)*255//63, (word & 31)*255//31)
                error += sum((a-b)**2 for a, b in zip(rgb, restored))
            errors.append(error)
        self.assertLess(errors[1], errors[0])

    def test_idle_url_is_complete_and_fits_at_left_margin(self):
        data = lcd_transfer.prepare_idle()['data']
        fields = lcd_transfer.HEADER.unpack(data[:34])
        lengths = fields[6:15]
        start = 34+sum(lengths[:2])
        url = data[start:start+lengths[2]]
        self.assertEqual(url, b'github.com/navaismo/klipper')
        self.assertLessEqual(12+len(url)*8, 240)

    def test_filename_suffix_is_hidden_only_in_display(self):
        for name in ('part.gcode', 'part.GCODE', 'part.gco', 'part.g'):
            job = lcd_status.finalize_job(lcd_status.prepare_metadata(
                'folder/'+name, {}))
            self.assertEqual(job['filename'], 'folder/'+name)
            self.assertEqual(job['display']['filename'], 'part')
            self.assertEqual(job['display']['material'], 'Mat: --')

    def test_40_byte_chunks_fit_klipper_even_with_worst_case_integers(self):
        sys.path.insert(0, os.path.join(jobs.ROOT, 'klippy'))
        import msgproto
        msgid = []
        msgproto.PT_uint32().encode(msgid, 16383)
        message = msgproto.MessageFormat(msgid,
                                        'tjc_lcd_job_chunk oid=%c'
                                        ' token=%u offset=%hu data=%*s')
        encoded = message.encode([255, 0xffffffff, 65535, b'\xff'*40])
        self.assertLessEqual(len(encoded), msgproto.MESSAGE_PAYLOAD_MAX)


class NativeCodecTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        jobs.NativeJobTests.setUpClass()
        cls.tmp = jobs.NativeJobTests.tmp
        cls.binary = jobs.NativeJobTests.binary
        cls.addClassCleanup(jobs.NativeJobTests.doClassCleanups)

    def test_packed_records_cross_chunks_and_gbk_fields(self):
        raw = b''.join(struct.pack('>H', (31, 2016, 63488)[
            (x//12+y//12) % 3]) for y in range(96) for x in range(96))
        for material in ('PETG', 'A\u6750', '\u6750'*9):
            jobs.NativeJobTests.run_job(self, raw, 'rects', material=material)

    def test_bad_codec_and_rectangle_bounds_are_rejected(self):
        raw = b'\xf8\x00'*9216
        data = jobs.prepared(raw)['transfer']['data']
        fields = struct.unpack('>4sBBHHHHHHHHHHHHI', data[:34])
        start = 34+sum(fields[6:15])
        for damage in ((start, 2), (start, 0), (start+3, 96), (start+6, 96)):
            jobs.NativeJobTests.run_job(self, raw, 'invalid', damage=damage)


class IdleEventTests(unittest.TestCase):
    setUp = jobs.JobHostTests.setUp
    ack = jobs.JobHostTests.ack
    idle_callback = jobs.JobHostTests.idle_callback

    def test_idle_timeout_draws_once_without_a_periodic_refresh(self):
        self.module._handle_idle_timeout(30.)
        self.idle_callback()(12.)
        self.assertEqual(self.module.screen, 'idle')
        self.module._handle_idle_timeout(60.)
        self.reactor.register_callback.assert_called_once()

    def test_idle_timeout_during_pause_or_print_does_not_replace_card(self):
        for state in ('paused', 'printing'):
            self.stats.get_status.return_value = {'state': state}
            self.module._handle_idle_timeout(60.)
        self.reactor.register_callback.assert_not_called()
