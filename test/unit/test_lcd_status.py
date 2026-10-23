# Offline tests for static LCD job data preparation
#
# Copyright (C) 2026 navaismo
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import importlib.util, io, json, os, sys, unittest
from unittest import mock
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)),
                              '..', '..', 'klippy', 'extras'))
import lcd_status as lcd


def image_bytes():
    out = io.BytesIO()
    Image.new('RGB', (96, 96), (255, 0, 0)).save(out, format='PNG')
    return out.getvalue()


def thumbnail(path='.thumbs/cube-96x96.png', width=96, height=96):
    return {'relative_path': path, 'width': width, 'height': height}


def metadata():
    return {'filename': 'parts/cube.gcode', 'layer_count': 100,
            'layer_height': 0.2, 'estimated_time': 3600,
            'filament_type': 'PETG', 'filament_name': 'Generic PETG',
            'first_layer_extr_temp': 237, 'first_layer_bed_temp': 62,
            'thumbnails': [thumbnail()]}


class MetadataTests(unittest.TestCase):
    def test_static_fields_and_units(self):
        job = lcd.prepare_metadata('parts/cube.gcode', metadata())
        self.assertEqual(job['total_layers'], 100)
        self.assertEqual(job['layer_height'], 0.2)
        self.assertEqual(job['estimated_time'], 3600.)
        self.assertEqual(job['material'], 'PETG')
        self.assertEqual(job['nozzle_target'], 237)
        self.assertEqual(job['bed_target'], 62)
        self.assertEqual(job['thumbnail']['path'],
                         'parts/.thumbs/cube-96x96.png')
        self.assertNotIn('current_layer', job)
        self.assertNotIn('remaining_time', job)

    def test_unknown_values_are_not_estimated(self):
        job = lcd.prepare_metadata('cube.gcode', {'object_height': 20})
        for field in ('total_layers', 'layer_height', 'estimated_time',
                      'material', 'filament_name', 'thumbnail',
                      'nozzle_target', 'bed_target'):
            self.assertIsNone(job[field])

    def test_invalid_numbers_and_material(self):
        for value in (None, True, False, 0, -1, float('nan'), float('inf'),
                      {}, [], 'unknown'):
            with self.subTest(value=value):
                job = lcd.prepare_metadata('cube.gcode', {
                    'layer_count': value, 'layer_height': value,
                    'estimated_time': value, 'filament_type': value})
                self.assertIsNone(job['total_layers'])
                self.assertIsNone(job['layer_height'])
                self.assertIsNone(job['estimated_time'])
                self.assertEqual(job['material'],
                                 value if isinstance(value, str) else None)
        self.assertIsNone(lcd.positive_number(1.5, integer=True))
        self.assertIsNone(lcd.positive_number(2 ** 40, integer=True))
        self.assertEqual(lcd.metadata_text(' PETG\n\x00 '), 'PETG')
        job = lcd.prepare_metadata('cube.gcode', {'filament_name': 'My PETG'})
        self.assertEqual(job['material'], 'My PETG')

    def test_targets_use_only_file_metadata_and_allow_heater_off(self):
        job = lcd.prepare_metadata('cube.gcode', {
            'first_layer_extr_temp': '237', 'first_layer_bed_temp': 0,
            'extruder': {'temperature': 150, 'target': 180}})
        lcd.finalize_job(job)
        self.assertEqual(job['display']['nozzle_target'], '237\u00b0C')
        self.assertEqual(job['display']['bed_target'], '0\u00b0C')
        for value in (True, -1, 501, float('inf'), float('nan'), {}, None):
            self.assertIsNone(lcd.target_temperature(value))

    def test_reject_wrong_file_and_malformed_metadata(self):
        for value in (None, [], 'text', {'filename': 'other.gcode'}):
            with self.assertRaises(lcd.DataError):
                lcd.prepare_metadata('cube.gcode', value)

    def test_reject_paths_outside_gcodes(self):
        for path in ('', '.', '/tmp/job.gcode', '../job.gcode',
                     'parts/../../job.gcode', 'a\\b', 'a\n', None):
            with self.assertRaises(lcd.DataError):
                lcd.relative_path(path)
        self.assertEqual(lcd.relative_path('parts/./cube.gcode'),
                         'parts/cube.gcode')

    def test_thumbnail_selection_and_path_resolution(self):
        thumbs = [thumbnail('large.png', 300, 300),
                  thumbnail('small.png', 32, 32), thumbnail()]
        chosen = lcd.select_thumbnail('parts/cube.gcode', thumbs)
        self.assertEqual(chosen['width'], 300)
        chosen = lcd.select_thumbnail('parts/cube.gcode', thumbs[:2])
        self.assertEqual(chosen['path'], 'parts/large.png')
        chosen = lcd.select_thumbnail('parts/cube.gcode', [thumbs[1]])
        self.assertEqual(chosen['width'], 32)
        bad = [thumbnail('../escape.png'), thumbnail('/escape.png'),
               thumbnail('unsupported.qoi'), thumbnail('huge.png', 9999),
               {'relative_path': 'missing-size.png'}, None]
        self.assertIsNone(lcd.select_thumbnail('cube.gcode', bad))

    def test_thumbnail_data_is_not_exported_in_status(self):
        job = lcd.prepare_metadata('parts/cube.gcode', metadata())
        data = image_bytes()
        lcd.attach_thumbnail(job, data)
        summary = lcd.job_summary(job)
        self.assertNotIn('data', summary['thumbnail'])
        self.assertEqual(summary['thumbnail']['size'], len(data))
        self.assertEqual(job['thumbnail']['data'], data)
        self.assertNotIn('data', summary['thumbnail']['rgb565'])
        self.assertEqual(summary['thumbnail']['rgb565']['size'], 18432)
        summary['warnings'].append('changed')
        self.assertEqual(job['warnings'], [])
        json.dumps(summary, allow_nan=False)
        for invalid in (b'<!DOCTYPE html>', b'x' *
                        (lcd.MAX_THUMBNAIL_BYTES + 1)):
            with self.assertRaises(lcd.DataError):
                lcd.attach_thumbnail(job, invalid)


class MoonrakerTests(unittest.TestCase):
    def test_collect_metadata_and_thumbnail_with_only_get(self):
        source = lcd.MoonrakerData('http://localhost:7125/prefix',
                                   api_key='secret')
        raw = json.dumps({'result': metadata()}).encode()
        image = image_bytes()
        responses = []
        for data in (raw, image):
            response = mock.Mock(status=200)
            response.read.side_effect = io.BytesIO(data).read
            responses.append(response)
        connection = mock.Mock()
        connection.getresponse.side_effect = responses
        source.connection_type = mock.Mock(return_value=connection)
        job = source.prepare('parts/cube.gcode')
        self.assertEqual(job['thumbnail']['data'], image)
        calls = connection.request.call_args_list
        self.assertEqual([call.args[0] for call in calls], ['GET', 'GET'])
        self.assertEqual(calls[0].args[1],
                         '/prefix/server/files/metadata?'
                         'filename=parts%2Fcube.gcode')
        self.assertEqual(calls[1].args[1],
                         '/prefix/server/files/gcodes/'
                         'parts/.thumbs/cube-96x96.png')
        self.assertEqual(calls[0].kwargs['headers']['X-Api-Key'], 'secret')
        self.assertEqual(connection.close.call_count, 2)

    def test_url_encoding_unicode_and_query_characters(self):
        source = lcd.MoonrakerData()
        source._get = mock.Mock(return_value=b'{"result": {}}')
        source.prepare('piezas/caf\u00e9 & 1.gcode')
        self.assertIn('caf%C3%A9+%26+1.gcode', source._get.call_args.args[0])

    def test_missing_thumbnail_does_not_discard_text(self):
        source = lcd.MoonrakerData()
        source._get = mock.Mock(side_effect=[
            json.dumps({'result': metadata()}).encode(),
            lcd.DataError('Moonraker GET failed (HTTP 404)')])
        job = source.prepare('parts/cube.gcode')
        self.assertEqual(job['material'], 'PETG')
        self.assertIsNone(job['thumbnail'])
        self.assertIn('404', job['warnings'][0])

    def test_errors_and_size_limit_close_connection(self):
        source = lcd.MoonrakerData()
        connection = mock.Mock()
        source.connection_type = mock.Mock(return_value=connection)
        for status, content in ((401, b'secret'), (302, b'redirect'),
                                (200, b'x' * 11)):
            connection.getresponse.return_value = mock.Mock(status=status)
            connection.getresponse.return_value.read.return_value = content
            with self.assertRaises(lcd.DataError) as ctx:
                source._get('/test', 10)
            self.assertNotIn('secret', str(ctx.exception))
        self.assertEqual(connection.close.call_count, 3)
        connection.request.side_effect = OSError('network failure')
        with self.assertRaises(lcd.DataError):
            source._get('/test', 10)
        self.assertEqual(connection.close.call_count, 4)
        connection.request.side_effect = ValueError('Invalid header: secret')
        with self.assertRaises(lcd.DataError) as ctx:
            source._get('/test', 10)
        self.assertNotIn('secret', str(ctx.exception))
        self.assertEqual(connection.close.call_count, 5)

    def test_invalid_json_response(self):
        source = lcd.MoonrakerData()
        for content in (b'html', b'[]', b'{}', b'{"result": null}',
                        b'{"error": {"message": "not found"}}', b'\xff'):
            source._get = mock.Mock(return_value=content)
            with self.assertRaises(lcd.DataError):
                source.prepare('cube.gcode')

    def test_reject_invalid_urls(self):
        for url in ('file:///tmp/x', 'http://user:secret@host',
                    'http://localhost:abc', 'http://host/?token=secret',
                    'http://host/#fragment', 'not a url'):
            with self.assertRaises(lcd.DataError):
                lcd.MoonrakerData(url)


class ModuleTests(unittest.TestCase):
    def setUp(self):
        self.objects = {'gcode': mock.Mock(), 'aio_executor': mock.Mock()}
        self.objects['gcode'].error = RuntimeError
        self.printer = mock.Mock()
        self.printer.lookup_object.side_effect = (
            lambda name, default=None: self.objects.get(name, default))
        self.executor = mock.Mock()
        self.executor.submit.side_effect = lambda fn, *args: fn(*args)
        aio = self.objects['aio_executor']
        aio.allocate_executor.return_value = self.executor
        self.printer.load_object.return_value = aio
        config = mock.Mock()
        config.get_printer.return_value = self.printer
        config.get.side_effect = lambda name, default: default
        config.getfloat.side_effect = lambda name, default, **kw: default
        config.getboolean.side_effect = lambda name, default: default
        self.module = lcd.LCDStatus(config)
        self.module._handle_ready()
        self.module.source = mock.Mock()
        self.module.source.prepare.return_value = lcd.finalize_job(
            lcd.prepare_metadata('parts/cube.gcode', {}))
        # Use text-only payloads here; image conversion has separate tests.
        self.module.source.prepare.return_value.update(
            total_layers=100, material='PETG')
        lcd.finalize_job(self.module.source.prepare.return_value)

    def test_prepare_is_explicit_and_does_not_send_to_mcu(self):
        self.module.source.prepare.assert_not_called()
        result = self.module.prepare_job('parts/cube.gcode')
        self.assertEqual(self.module.state, 'prepared')
        self.assertEqual(result['total_layers'], 100)
        result['total_layers'] = 1
        self.assertEqual(self.module.get_job_data()['total_layers'], 100)
        for unused in range(3):
            self.module.get_status(0.)
        self.module.source.prepare.assert_called_once()
        self.printer.get_reactor().register_timer.assert_not_called()
        self.assertNotIn(mock.call.lookup_object('mcu'),
                         self.printer.mock_calls)

    def test_transfer_chunks_are_invalidated_by_print_or_new_job(self):
        self.module.prepare_job('parts/cube.gcode')
        chunks = self.module.get_transfer_chunks()
        self.assertEqual(next(chunks)[0], 0)
        self.module.job = None
        with self.assertRaises(lcd.DataError):
            next(chunks)
        self.module.prepare_job('parts/cube.gcode')
        chunks = self.module.get_transfer_chunks()
        self.objects['virtual_sdcard'] = mock.Mock()
        self.objects['virtual_sdcard'].is_active.return_value = True
        with self.assertRaises(lcd.DataError):
            next(chunks)

    def test_refuse_during_print_or_pause_without_fetch(self):
        self.objects['print_stats'] = mock.Mock()
        for state in ('printing', 'paused'):
            self.objects['print_stats'].get_status.return_value = {
                'state': state}
            with self.assertRaises(lcd.DataError):
                self.module.prepare_job('parts/cube.gcode')
        del self.objects['print_stats']
        self.objects['virtual_sdcard'] = mock.Mock()
        self.objects['virtual_sdcard'].is_active.return_value = True
        with self.assertRaises(lcd.DataError):
            self.module.prepare_job('parts/cube.gcode')
        self.module.source.prepare.assert_not_called()

    def test_failed_new_job_cannot_reuse_previous_data(self):
        self.module.prepare_job('parts/cube.gcode')
        self.module.source.prepare.side_effect = lcd.DataError('missing file')
        with self.assertRaises(lcd.DataError):
            self.module.prepare_job('missing.gcode')
        self.assertIsNone(self.module.get_job_data())
        self.assertEqual(self.module.get_status(0.)['state'], 'error')

    def test_disconnect_during_fetch_does_not_publish_result(self):
        def disconnect(filename):
            self.module._handle_disconnect()
            return lcd.prepare_metadata(filename, {})
        self.module.source.prepare.side_effect = disconnect
        with self.assertRaises(lcd.DataError):
            self.module.prepare_job('parts/cube.gcode')
        self.assertIsNone(self.module.get_job_data())
        self.assertEqual(self.module.state, 'empty')

    def test_print_started_during_fetch_does_not_publish_result(self):
        def start_print(filename):
            self.objects['virtual_sdcard'] = mock.Mock()
            self.objects['virtual_sdcard'].is_active.return_value = True
            return lcd.prepare_metadata(filename, {})
        self.module.source.prepare.side_effect = start_print
        with self.assertRaises(lcd.DataError):
            self.module.prepare_job('parts/cube.gcode')
        self.assertIsNone(self.module.get_job_data())
        self.assertEqual(self.module.state, 'error')

    def test_command_returns_summary_and_converts_data_errors(self):
        gcmd = mock.Mock()
        gcmd.get.return_value = 'parts/cube.gcode'
        gcmd.error.side_effect = lambda text: RuntimeError(text)
        self.module.cmd_LCD_PREPARE(gcmd)
        summary = json.loads(gcmd.respond_info.call_args.args[0])
        self.assertEqual(summary['material'], 'PETG')
        self.module.source.prepare.side_effect = lcd.DataError('not found')
        with self.assertRaisesRegex(RuntimeError, 'not found'):
            self.module.cmd_LCD_PREPARE(gcmd)


class OfflineToolTests(unittest.TestCase):
    def test_saved_response_never_constructs_http_client(self):
        root = os.path.realpath(os.path.join(os.path.dirname(__file__),
                                            '..', '..'))
        script = os.path.join(root, 'scripts', 'lcd_prepare.py')
        spec = importlib.util.spec_from_file_location('lcd_prepare', script)
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        args = [script, 'parts/cube.gcode', '--metadata-json',
                os.path.join(root, 'test', 'fixtures', 'lcd_metadata.json')]
        with mock.patch.object(sys, 'argv', args), \
                mock.patch.object(sys, 'stdout', new_callable=io.StringIO) \
                as output, mock.patch.object(lcd, 'MoonrakerData') as source:
            tool.main()
            source.assert_not_called()
            result = json.loads(output.getvalue())
        self.assertEqual(result['total_layers'], 100)
        self.assertEqual(result['material'], 'PETG')
        self.assertIsNone(result['thumbnail'])
        self.assertNotIn('data', result['transfer'])
        self.assertTrue(result['warnings'])


if __name__ == '__main__':
    unittest.main()
