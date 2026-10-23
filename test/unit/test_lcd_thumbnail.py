# Image conversion and job payload tests; no MCU, LCD, or network access
#
# Copyright (C) 2026 navaismo
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import base64, hashlib, importlib.util, io, json, os, struct, sys
import tempfile, unittest
import zlib
from unittest import mock
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)),
                              '..', '..', 'klippy', 'extras'))
import lcd_status as lcd
import lcd_thumbnail as thumbnail
import lcd_transfer as transfer


def encoded_image(color=(255, 0, 0), size=(96, 96), mode='RGB', fmt='PNG'):
    out = io.BytesIO()
    Image.new(mode, size, color).save(out, format=fmt)
    return out.getvalue()


def gcode_block(data, size='96x96', name='thumbnail'):
    encoded = base64.b64encode(data).decode('ascii')
    lines = ['; %s begin %s %d' % (name, size, len(encoded))]
    lines.extend('; ' + encoded[i:i + 78]
                 for i in range(0, len(encoded), 78))
    lines.append('; %s end' % name)
    return ('\n'.join(lines) + '\n').encode('ascii')


class ImageTests(unittest.TestCase):
    def test_reference_thumbnail_raw16_golden_digest(self):
        path = os.path.join(os.path.dirname(__file__), '..', 'fixtures',
                            'lcd_thumbnail.gcode')
        with open(path, 'rb') as f:
            source = thumbnail.extract_gcode_thumbnail(f.read())
        raw = thumbnail.convert_image(source['data'], colors=32)['data']
        # Generated with the owner's unmodified orca_parser.py (Pillow 13).
        expected = ('a45a6d92a036dac2685ab5163177f7766'
                    'eeb420d458e740dd195be1fd96e5fa5')
        self.assertEqual(hashlib.sha256(raw).hexdigest(), expected)

    def test_rgb565_known_values_and_dark_foreground(self):
        for rgb, word in (((255, 0, 0), 0xf800), ((0, 255, 0), 0x07e0),
                          ((0, 0, 255), 0x001f), ((255, 255, 255), 0xffff),
                          ((0, 0, 0), 0), ((1, 1, 1), 1)):
            image = thumbnail.convert_image(encoded_image(rgb))
            self.assertEqual(image['data'], struct.pack('>H', word) * 9216)
        self.assertEqual(thumbnail.rgb888_to_565(128, 128, 128), 0x7bef)

    def test_resize_jpeg_and_reference_alpha_behavior(self):
        for data in (encoded_image(size=(120, 80)),
                     encoded_image((255, 255, 255), fmt='JPEG'),
                     encoded_image((255, 0, 0, 0), mode='RGBA')):
            result = thumbnail.convert_image(data)
            self.assertEqual((result['width'], result['height']), (96, 96))
            self.assertEqual(result['size'], 18432)
            self.assertEqual(result['colors'], 1)
        result = thumbnail.convert_image(encoded_image(size=(120, 80)))
        self.assertEqual((result['source_width'], result['source_height']),
                         (120, 80))
        self.assertEqual(result['data'], b'\xf8\x00' * 9216)

    def test_quantization_preserves_background_mask(self):
        image = Image.new('RGB', (96, 96))
        for y in range(96):
            for x in range(48, 96):
                image.putpixel((x, y), ((x * 7) % 255 + 1,
                                        (y * 5) % 255 + 1, (x + y) % 256))
        source = io.BytesIO()
        image.save(source, format='PNG')
        raw = thumbnail.convert_image(source.getvalue())['data']
        words = struct.unpack('>9216H', raw)
        self.assertLessEqual(len(set(words)), 65)  # palette plus black sentinel
        for y in range(96):
            self.assertEqual(words[y * 96:y * 96 + 48], (0,) * 48)
            self.assertNotIn(0, words[y * 96 + 48:y * 96 + 96])

    def test_reject_corruption_wrong_format_and_large_dimensions(self):
        for data in (b'\x89PNG\r\n\x1a\n', encoded_image()[:-20],
                     encoded_image(size=(1025, 1025)),
                     b'x' * (thumbnail.MAX_SOURCE_BYTES + 1)):
            with self.assertRaises(thumbnail.ThumbnailError):
                thumbnail.convert_image(data)
        with self.assertRaises(thumbnail.ThumbnailError):
            thumbnail.convert_image(encoded_image(), expected_format='jpeg')

    def test_gcode_png_jpg_and_selection(self):
        png = encoded_image()
        jpg = encoded_image(fmt='JPEG')
        for data, name, fmt in ((png, 'thumbnail', 'png'),
                                (png, 'thumbnail_PNG', 'png'),
                                (jpg, 'thumbnail_JPG', 'jpeg')):
            result = thumbnail.extract_gcode_thumbnail(gcode_block(
                data, name=name).replace(b'\n', b'\r\n'))
            self.assertEqual(result['data'], data)
            self.assertEqual(result['format'], fmt)
        small = gcode_block(encoded_image(size=(32, 32)), size='32x32')
        result = thumbnail.extract_gcode_thumbnail(small + gcode_block(png))
        self.assertEqual(result['width'], 96)
        self.assertEqual(result['data'], png)

    def test_gcode_reject_incomplete_bad_base64_and_declared_length(self):
        block = gcode_block(encoded_image())
        broken = [b'G28\n', block.replace(b'; thumbnail end', b'; unfinished'),
                  block.replace(b'iVBOR', b'!VBOR'),
                  block.replace(b'96x96 ', b'96x96 9'),
                  b'x' * (thumbnail.MAX_GCODE_BYTES + 1)]
        for data in broken:
            with self.assertRaises(thumbnail.ThumbnailError):
                thumbnail.extract_gcode_thumbnail(data)

    def test_raw16_export_matches_marlin_comment_format(self):
        raw = b'\xf8\x00' * 9216
        block = thumbnail.raw16_block(raw)
        lines = block.splitlines()
        self.assertEqual(lines[0], '; E3V3SE_THUMB_RAW16_BEGIN 96x96')
        self.assertEqual(lines[1], '; ' + 'F800' * 96)
        self.assertEqual(lines[-1], '; E3V3SE_THUMB_RAW16_END')
        self.assertEqual(len(lines), 98)
        self.assertEqual(bytes.fromhex(''.join(s[2:] for s in lines[1:-1])),
                         raw)

    def test_attach_gcode_and_dimension_check(self):
        job = lcd.prepare_metadata('cube.gcode', {})
        lcd.attach_gcode_thumbnail(job, gcode_block(encoded_image()))
        lcd.finalize_job(job)
        self.assertEqual(job['thumbnail']['source'], 'gcode')
        self.assertEqual(job['thumbnail']['rgb565']['size'], 18432)
        self.assertIsNotNone(job['transfer'])
        job['thumbnail']['width'] = 32
        with self.assertRaises(lcd.DataError):
            lcd.attach_thumbnail(job, encoded_image())


class TransferTests(unittest.TestCase):
    def prepared_job(self):
        job = lcd.prepare_metadata('cube.gcode', {
            'layer_count': 100, 'layer_height': .2, 'estimated_time': 3600,
            'filament_type': 'PETG'})
        lcd.attach_gcode_thumbnail(job, gcode_block(encoded_image()))
        return lcd.finalize_job(job)

    def test_header_material_and_pixels_are_unambiguous(self):
        job = self.prepared_job()
        payload = job['transfer']['data']
        # Fixed header decoded independently of the producer's Struct object.
        texts = (b'Layers: 100Height: 0.2mmETA: 01:00:00Mat: PETG'
                 b'cube----NozzleBed')
        self.assertEqual(len(payload), 34 + len(texts) + 7)
        fields = struct.unpack('>4sBBHHHHHHHHHHHHI', payload[:34])
        self.assertEqual(fields, (b'KLCD', 5, 31, 34, 96, 96,
                                  11, 13, 13, 9, 4, 2, 2, 6, 3, 7))
        self.assertEqual(payload[34:34 + len(texts)], texts)
        self.assertEqual(payload[34 + len(texts):],
                         b'\x01\xf8\x00\x00\x00\x5f\x5f')
        self.assertEqual(job['estimated_time_formatted'], '01:00:00')
        self.assertEqual(job['display']['estimated_time'], 'ETA: 01:00:00')

    def test_chunks_round_trip_offsets_and_crc(self):
        job = self.prepared_job()
        payload = job['transfer']
        chunks = list(transfer.iter_chunks(payload))
        self.assertEqual([off for off, data in chunks],
                         list(range(0, payload['size'], 40)))
        self.assertTrue(all(0 < len(data) <= 40 for off, data in chunks))
        combined = b''.join(data for off, data in chunks)
        self.assertEqual(combined, payload['data'])
        self.assertEqual(zlib.crc32(combined) & 0xffffffff, payload['crc32'])
        self.assertEqual(len(chunks), payload['chunk_count'])
        payload['data'] = b'x' + payload['data'][1:]
        with self.assertRaises(transfer.TransferError):
            list(transfer.iter_chunks(payload))

    def test_missing_data_flags_and_number_range(self):
        job = lcd.finalize_job(lcd.prepare_metadata('cube.gcode', {}))
        payload = job['transfer']['data']
        self.assertEqual(payload[5], 0)
        self.assertEqual(payload[8:12], b'\x00' * 4)
        self.assertEqual(payload[34:], b'Layers: --Height: --ETA: --'
                         b'Mat: --cube----NozzleBed')
        self.assertIsNone(job['estimated_time_formatted'])
        for value in (float('nan'), float('inf'), 2 ** 32, -1, True):
            job['estimated_time'] = value
            with self.assertRaises(lcd.DataError):
                lcd.finalize_job(job)
            self.assertIsNone(job['transfer'])

    def test_gbk_material_and_multibyte_boundary(self):
        job = lcd.prepare_metadata('cube.gcode', {})
        job['material'] = 'A' + '\u6750' * 40 + '\U0001f600'
        payload = lcd.finalize_job(job)['transfer']['data']
        fields = struct.unpack('>4sBBHHHHHHHHHHHHI', payload[:34])
        start = 34 + sum(fields[6:9])
        material = payload[start:start + fields[9]].decode('gbk')
        self.assertEqual(fields[9], 26)
        self.assertEqual(material, 'Mat: A' + '\u6750' * 10)
        self.assertEqual(job['display']['material'], material)

    def test_eta_duration_format_and_real_host_result(self):
        cases = ((.1, '00:00:00'), (.5, '00:00:01'), (29, '00:00:29'),
                 (59, '00:00:59'), (59.5, '00:01:00'), (60, '00:01:00'),
                 (90, '00:01:30'), (3599, '00:59:59'),
                 (3599.5, '01:00:00'), (12552, '03:29:12'),
                 (86400, '24:00:00'), (90061, '25:01:01'),
                 (360000, '100:00:00'))
        for seconds, expected in cases:
            job = lcd.prepare_metadata('cube.gcode', {
                'layer_count': 221, 'layer_height': .18,
                'estimated_time': seconds, 'filament_type': 'PETG'})
            lcd.finalize_job(job)
            self.assertEqual(job['estimated_time'], seconds)
            self.assertEqual(job['estimated_time_formatted'], expected)
            self.assertEqual(job['display'], {
                'total_layers': 'Layers: 221',
                'layer_height': 'Height: 0.18mm',
                'estimated_time': 'ETA: %s' % expected,
                'material': 'Mat: PETG', 'filename': 'cube',
                'nozzle_target': '--', 'bed_target': '--',
                'nozzle_label': 'Nozzle', 'bed_label': 'Bed'})

    def test_summary_does_not_export_any_binary_data(self):
        summary = lcd.job_summary(self.prepared_job())
        self.assertNotIn('data', summary['transfer'])
        self.assertNotIn('data', summary['thumbnail'])
        self.assertNotIn('data', summary['thumbnail']['rgb565'])
        json.dumps(summary, allow_nan=False)

    def test_filename_and_temperatures_are_bounded_host_text(self):
        job = lcd.prepare_metadata('parts/' + '\u6750'*60 + '.gcode', {
            'first_layer_extr_temp': 237, 'first_layer_bed_temp': 62})
        lcd.finalize_job(job)
        self.assertEqual(job['display']['filename'], '\u6750'*26)
        self.assertEqual(job['display']['nozzle_target'], '237\u00b0C')
        self.assertEqual(job['display']['bed_target'], '62\u00b0C')
        self.assertEqual(job['transfer']['data'][5], 32 | 64)
        self.assertLessEqual(job['transfer']['size'], 18655)
        for value in (-1, True, 501, float('nan'), 'bad'):
            job['nozzle_target'] = value
            with self.assertRaises(lcd.DataError):
                lcd.finalize_job(job)


class ExportTests(unittest.TestCase):
    def test_offline_gcode_export_and_input_preservation(self):
        root = os.path.realpath(os.path.join(os.path.dirname(__file__),
                                            '..', '..'))
        script = os.path.join(root, 'scripts', 'lcd_prepare.py')
        spec = importlib.util.spec_from_file_location('lcd_prepare', script)
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        with tempfile.TemporaryDirectory() as tmp:
            gcode = os.path.join(tmp, 'source.gcode')
            raw16 = os.path.join(tmp, 'thumb.txt')
            payload = os.path.join(tmp, 'job.bin')
            original = gcode_block(encoded_image()) + b'G1 X10\n'
            with open(gcode, 'wb') as f:
                f.write(original)
            args = [script, 'parts/cube.gcode', '--metadata-json',
                    os.path.join(root, 'test/fixtures/lcd_metadata.json'),
                    '--gcode-file', gcode, '--output-raw16', raw16,
                    '--output-transfer', payload]
            with mock.patch.object(sys, 'argv', args), \
                    mock.patch.object(sys, 'stdout', new_callable=io.StringIO) \
                    as output:
                tool.main()
                summary = json.loads(output.getvalue())
            with open(gcode, 'rb') as f:
                self.assertEqual(f.read(), original)
            with open(payload, 'rb') as f:
                data = f.read()
            self.assertEqual(len(data), summary['transfer']['size'])
            self.assertEqual(data[:4], b'KLCD')
            with open(raw16) as f:
                self.assertTrue(f.read().startswith(thumbnail.RAW_BEGIN))
            args[-1] = gcode
            with mock.patch.object(sys, 'argv', args), \
                    mock.patch.object(sys, 'stderr', new_callable=io.StringIO):
                with self.assertRaises(SystemExit):
                    tool.main()
            with open(gcode, 'rb') as f:
                self.assertEqual(f.read(), original)


if __name__ == '__main__':
    unittest.main()
