# Static card transfer and native MCU rendering regression tests
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import os, random, shutil, subprocess, tempfile, unittest
from unittest import mock
import test_tjc_lcd as startup
import lcd_status
import lcd_transfer
import pause_resume
import test_lcd_autostart as autostart


def prepared(raw=None, material=None):
    job = lcd_status.prepare_metadata('parts/Job with spaces.gcode', {
        'layer_count': 221, 'layer_height': .18,
        'estimated_time': 12552, 'filament_type': 'PETG',
        'first_layer_extr_temp': 237, 'first_layer_bed_temp': 62})
    if material is not None:
        job['material'] = material
    if raw is not None:
        job['thumbnail'] = {'rgb565': {'data': raw, 'width': 96,
                                      'height': 96, 'format': 'rgb565be'}}
    return lcd_status.finalize_job(job)


ROOT, STUBS = startup.ROOT, startup.STUBS


class JobHostTests(unittest.TestCase):
    def setUp(self):
        startup.HostTests.setUp(self)
        self.begin, self.chunk, self.end, self.abort = [mock.Mock()
                                                       for _ in range(4)]
        self.module.job_begin_cmd = self.begin
        self.module.job_chunk_cmd = self.chunk
        self.module.job_end_cmd = self.end
        self.module.job_abort_cmd = self.abort
        self.module.connected = True
        self.module.state = 'ready'
        self.sent = []
        def begin(args):
            self.sent = []
            self.ack(0, 1)
        self.begin.send.side_effect = begin
        def chunk(args):
            self.sent.append((args[2], args[3]))
            self.ack(args[2]+len(args[3]), 1)
        self.chunk.send.side_effect = chunk
        self.end.send.side_effect = lambda a: self.ack(
            sum(len(data) for offset, data in self.sent), 2)

    def ack(self, offset, state, error=0):
        self.module._handle_job_result({'token': self.module.job_token,
                                       'offset': offset, 'state': state,
                                       'error': error})
        self.reactor.register_async_callback.call_args[0][0](11.)

    def test_streams_payload_and_requires_final_render_result(self):
        job = prepared(b'\xf8\x00'*9216)
        self.module.render_job(job)
        self.assertEqual(b''.join(data for off, data in self.sent),
                         job['transfer']['data'])
        self.assertTrue(self.module.rendered)
        self.assertEqual(self.module.job_filename, job['filename'])
        self.assertEqual(self.module.state, 'ready')
        self.abort.send.assert_not_called()

    def test_missing_ack_aborts_and_prevents_print(self):
        self.chunk.send.side_effect = None
        with self.assertLogs(level='WARNING'):
            with self.assertRaisesRegex(RuntimeError, 'acknowledgement'):
                self.module.render_job(prepared())
        self.abort.send.assert_called_once()
        self.assertEqual(self.module.state, 'error')
        self.assertFalse(self.module.rendered)

    def test_corrupt_local_payload_never_claims_mcu(self):
        job = prepared()
        job['transfer']['data'] = b'broken'
        with self.assertRaises(lcd_transfer.TransferError):
            self.module.render_job(job)
        self.begin.send.assert_not_called()

    def test_wrong_token_or_offset_does_not_complete_request(self):
        def wrong(args):
            self.ack(999, 1)
        self.begin.send.side_effect = wrong
        with self.assertLogs(level='WARNING'):
            with self.assertRaisesRegex(RuntimeError, 'acknowledgement'):
                self.module.render_job(prepared())

    def test_stale_job_token_cannot_acknowledge_new_transfer(self):
        def wrong(args):
            self.module._handle_job_result({'token': args[1]-1,
                                           'offset': 0, 'state': 1, 'error': 0})
            self.reactor.register_async_callback.call_args[0][0](11.)
        self.begin.send.side_effect = wrong
        with self.assertLogs(level='WARNING'):
            with self.assertRaisesRegex(RuntimeError, 'acknowledgement'):
                self.module.render_job(prepared())

    def test_metadata_hook_invokes_render_after_preparation(self):
        printer = mock.Mock()
        display = mock.Mock()
        printer.lookup_object.return_value = display
        module = object.__new__(lcd_status.LCDStatus)
        module.printer = printer
        module.prepare_job = mock.Mock(return_value=prepared())
        module.gcode = mock.Mock(error=RuntimeError)
        module._handle_before_print('cube.gcode')
        display.render_job.assert_called_once_with(
            module.prepare_job.return_value)

    def test_firmware_capability_enables_semantic_job_commands(self):
        self.mcu.get_constants.return_value = {'TJC_LCD_VERSION': 1,
                                               'TJC_JOB_VERSION': 5}
        self.mcu.lookup_command.side_effect = [self.mcu.start, self.mcu.clear,
                                               self.begin, self.chunk,
                                               self.end, self.abort]
        self.module._build_config()
        self.mcu.register_serial_response.assert_any_call(
            self.module._handle_job_result,
            'tjc_lcd_job_result oid=%c token=%u offset=%hu'
            ' state=%c error=%c', 7)
        self.assertIs(self.module.job_begin_cmd, self.begin)

    def test_file_reset_aborts_inflight_transfer(self):
        self.chunk.send.side_effect = (
            lambda args: self.module._handle_reset_file())
        with self.assertLogs(level='WARNING'):
            with self.assertRaisesRegex(RuntimeError, 'acknowledgement'):
                self.module.render_job(prepared())
        self.assertFalse(self.module.rendered)
        self.abort.send.assert_called()

    def test_previous_firmware_cannot_accept_new_card_format(self):
        self.mcu.get_constants.return_value = {'TJC_LCD_VERSION': 1,
                                               'TJC_JOB_VERSION': 4}
        self.module.job_begin_cmd = None
        self.mcu.lookup_command.side_effect = [self.mcu.start, self.mcu.clear]
        self.module._build_config()
        with self.assertRaisesRegex(RuntimeError, 'TJC_JOB_VERSION=5'):
            self.module.render_job(prepared())
        self.begin.send.assert_not_called()

    def test_no_display_traffic_when_print_begins_unexpectedly(self):
        self.stats.get_status.return_value = {'state': 'printing'}
        with self.assertRaisesRegex(RuntimeError, 'idle printer'):
            self.module.render_job(prepared())
        self.begin.send.assert_not_called()

    def idle_callback(self):
        return self.reactor.register_callback.call_args[0][0]

    def test_startup_renders_idle_once_after_handshake_and_clear(self):
        self.module._start()
        # Real firmware reports clearing between initializing and ready.
        self.module._handle_state({'state': 2, 'error': 0})
        self.reactor.register_async_callback.call_args[0][0](10.5)
        self.assertEqual(self.module.state, 'clearing')
        self.reactor.register_callback.assert_not_called()
        self.module._handle_state({'state': 3, 'error': 0})
        self.reactor.register_async_callback.call_args[0][0](11.)
        callback = self.idle_callback()
        callback(12.)
        self.assertEqual(self.module.screen, 'idle')
        self.assertFalse(self.module.rendered)
        self.assertIsNone(self.module.job_filename)
        self.assertEqual(b''.join(data for off, data in self.sent),
                         lcd_transfer.prepare_idle('unknown')['data'])
        self.reactor.register_callback.assert_called_once()

    def test_idle_reports_connected_mcu_version_without_canvas_support(self):
        version = 'ee2c6fb6-dirty-20261023_054730-IrohKlipper'
        self.mcu.get_status.return_value = {'mcu_version': version}
        self.assertIsNone(self.module.canvas_begin_cmd)
        self.module.render_idle()
        data = b''.join(data for offset, data in self.sent)
        self.assertEqual(data, lcd_transfer.prepare_idle(version)['data'])
        self.assertIn(b'ee2c6fb6 1023_054730', data)
        self.assertNotIn(b'CANVAS', data)
        self.assertIn(b'github.com/navaismo/klipper', data)
        self.assertIn(b'Idle', data)

    def test_same_revision_different_mcu_builds_have_distinct_idle_labels(self):
        self.mcu.get_constants.return_value['TJC_IDLE_VERSION'] = 1
        labels = []
        for stamp in ('20261029_211120', '20261029_223000'):
            self.mcu.get_status.return_value = {
                'mcu_version': '43fc76cb-dirty-' + stamp + '-IrohKlipper'}
            self.module.render_idle()
            data = b''.join(data for offset, data in self.sent)
            fields = lcd_transfer.HEADER.unpack(data[:34])
            start = 34+sum(fields[6:9])
            label = data[start:start+fields[9]]
            self.assertEqual(label, ('FW 43fc76cb ' + stamp).encode('ascii'))
            self.assertLessEqual(len(label), 27)
            labels.append(label)
        self.assertNotEqual(*labels)

    def test_idle_footer_preserves_url_model_and_full_firmware_identity(self):
        self.mcu.get_constants.return_value['TJC_IDLE_VERSION'] = 1
        self.mcu.get_status.return_value = {
            'mcu_version': '43fc76cb-dirty-20261029_211120-IrohKlipper'}
        self.module.render_idle()
        data = b''.join(data for offset, data in self.sent)
        for text in (b'FW 43fc76cb 20261029_211120',
                     b'github.com/navaismo/klipper', b'Ender 3 V3 SE', b'Idle'):
            self.assertIn(text, data)

    def test_missing_mcu_version_shows_unknown_without_local_fallback(self):
        self.mcu.get_status.return_value = {}
        self.module.render_idle()
        data = b''.join(data for offset, data in self.sent)
        self.assertIn(b'FW: unknown', data)

    def test_finished_print_waits_for_motion_before_idle_uart(self):
        events = []
        self.gcode.get_mutex.return_value.__enter__.side_effect = (
            lambda: events.append('macro_done'))
        toolhead = self.objects['toolhead'] = mock.Mock()
        toolhead.wait_moves.side_effect = lambda: events.append('motion_done')
        self.begin.send.side_effect = lambda args: (
            events.append('uart_begin'), self.ack(0, 1))
        self.stats.get_status.return_value = {'state': 'complete'}
        self.module._handle_finished('complete')
        self.begin.send.assert_not_called()
        self.idle_callback()(12.)
        self.assertEqual(events, ['macro_done', 'motion_done', 'uart_begin'])
        self.assertEqual(self.module.screen, 'idle')

    def test_cancel_command_then_sd_reset_restores_idle_after_motion(self):
        runtime = autostart.AutoStartTests()
        runtime.setUp()
        self.addCleanup(runtime.doCleanups)
        printer = runtime.printer
        printer.get_start_args = lambda: {}
        printer.objects['webhooks'] = mock.Mock()
        printer.gcode.get_mutex.return_value = mock.MagicMock()
        printer.gcode.get_mutex.return_value.test.return_value = False
        self.module.printer = printer
        self.module.gcode = printer.gcode
        self.module.screen = 'job'
        self.module.rendered = True
        events = []
        printer.gcode.get_mutex.return_value.__enter__.side_effect = (
            lambda: events.append('macro_done'))
        toolhead = printer.objects['toolhead'] = mock.Mock()
        toolhead.wait_moves.side_effect = lambda: events.append('motion_done')
        self.begin.send.side_effect = lambda args: (
            events.append('uart_begin'), self.ack(0, 1))
        printer.register_event_handler('virtual_sdcard:finished',
                                      self.module._handle_finished)
        printer.register_event_handler('virtual_sdcard:reset_file',
                                      self.module._handle_reset_file)
        runtime.sd.cmd_SDCARD_PRINT_FILE(runtime.command('other.gcode'))
        printer.stats.note_start()
        printer.reactor.pause.side_effect = runtime.sd.work_handler
        pause = pause_resume.PauseResume(autostart.Config(printer))
        pause.handle_connect()
        pause.cmd_CANCEL_PRINT(runtime.command(''))
        self.assertFalse(runtime.sd.is_active())
        self.assertEqual(printer.stats.state, 'cancelled')
        cancelled = self.idle_callback()
        # Common cancel macros reset the file after calling the base command.
        runtime.sd.cmd_SDCARD_RESET_FILE(runtime.command(''))
        reset = self.idle_callback()
        self.begin.send.assert_not_called()
        cancelled(12.)
        self.begin.send.assert_not_called()
        reset(13.)
        self.assertEqual(events, ['macro_done', 'motion_done', 'uart_begin'])
        self.assertEqual(self.module.screen, 'idle')
        self.begin.send.assert_called_once()

    def test_reset_only_cancel_macro_restores_job_screen_to_idle(self):
        self.module.screen = 'job'
        self.module.rendered = True
        self.module._handle_reset_file()
        self.begin.send.assert_not_called()
        self.idle_callback()(12.)
        self.objects['toolhead'].wait_moves.assert_called_once()
        self.assertEqual(self.module.screen, 'idle')

    def test_new_job_supersedes_idle_queued_by_cancel_reset(self):
        self.module.screen = 'job'
        self.module._handle_reset_file()
        callback = self.idle_callback()
        self.module._handle_before_print('next.gcode')
        callback(12.)
        self.begin.send.assert_not_called()
        self.assertEqual(self.module.screen, 'job')

    def test_new_job_supersedes_pending_idle_after_motion_wait(self):
        toolhead = self.objects['toolhead'] = mock.Mock()
        toolhead.wait_moves.side_effect = lambda: self.module.render_job(
            prepared())
        self.module._handle_finished('complete')
        self.idle_callback()(12.)
        self.assertEqual(self.module.screen, 'job')
        self.assertEqual(b''.join(data for off, data in self.sent),
                         prepared()['transfer']['data'])
        self.begin.send.assert_called_once()

    def test_preprint_invalidates_queued_idle_callback(self):
        self.module._schedule_idle()
        callback = self.idle_callback()
        self.module._handle_before_print('cube.gcode')
        callback(12.)
        self.begin.send.assert_not_called()

    def test_pause_preserves_card_and_never_draws_idle(self):
        self.module.screen = 'job'
        self.module.rendered = True
        self.stats.get_status.return_value = {'state': 'paused'}
        self.module._schedule_idle(wait_moves=True)
        with self.assertLogs(level='WARNING'):
            self.idle_callback()(12.)
        self.begin.send.assert_not_called()
        self.assertEqual(self.module.screen, 'job')
        self.assertTrue(self.module.rendered)

    def test_preflight_pending_skips_idle_during_metadata_collection(self):
        self.sd.preflight_pending = True
        self.module._schedule_idle()
        self.idle_callback()(12.)
        self.begin.send.assert_not_called()

    def test_disconnect_while_draining_motion_cannot_draw_idle(self):
        toolhead = self.objects['toolhead'] = mock.Mock()
        toolhead.wait_moves.side_effect = self.module._handle_disconnect
        self.module._handle_finished('complete')
        self.idle_callback()(12.)
        self.begin.send.assert_not_called()
        self.assertIsNone(self.module.screen)

    def test_print_waits_for_an_already_running_idle_render(self):
        self.module.state = 'rendering'
        self.module.render_kind = 'idle'
        completion = self.module.render_completion = mock.Mock()
        completion.wait.side_effect = lambda: setattr(
            self.module, 'state', 'ready')
        self.module._handle_before_print('cube.gcode')
        completion.wait.assert_called_once()

    def test_selecting_a_file_during_idle_render_does_not_abort_lcd(self):
        self.module.state = 'rendering'
        self.module.render_kind = 'idle'
        self.module._handle_reset_file()
        self.assertEqual(self.module.state, 'rendering')
        self.abort.send.assert_not_called()

    def test_error_or_pause_does_not_schedule_an_idle_transition(self):
        for state in ('paused', 'error', 'printing'):
            self.module._handle_finished(state)
        self.reactor.register_callback.assert_not_called()


class NativeJobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which('gcc') is None:
            raise unittest.SkipTest('Native GCC is required')
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        stubs = dict(STUBS)
        stubs['command.h'] += '\nvoid *command_decode_ptr(uint32_t);\n'
        for name, content in stubs.items():
            path = os.path.join(cls.tmp.name, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w') as f:
                f.write(content)
        for name in ('tjc_job.c', 'tjc_uart.h', 'tjc_lcd.h'):
            shutil.copyfile(os.path.join(ROOT, 'src', name),
                            os.path.join(cls.tmp.name, name))
        shutil.copyfile(os.path.join(ROOT, 'test', 'unit', 'tjc_job_harness.c'),
                        os.path.join(cls.tmp.name, 'harness.c'))
        cls.binary = os.path.join(cls.tmp.name, 'job-test')
        result = subprocess.run([
            'gcc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
            '-Wno-unused-parameter', '-I', cls.tmp.name,
            os.path.join(cls.tmp.name, 'harness.c'), '-o', cls.binary],
            capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stderr)

    def run_job(self, raw, scenario, material=None, filename=None, damage=None,
                idle=False, trace=False, targets=None, idle_version=None,
                idle_footer=False):
        path = os.path.join(self.tmp.name, 'job.bin')
        job = prepared(raw, material)
        if filename is not None:
            job['filename'] = filename
            lcd_status.finalize_job(job)
        if targets is not None:
            job.update(targets)
            lcd_status.finalize_job(job)
        transfer = lcd_transfer.prepare_idle(idle_version, idle_footer) \
            if idle else job['transfer']
        data = bytearray(transfer['data'])
        if damage is not None:
            offset, value = damage
            data[offset] = value
        with open(path, 'wb') as f:
            f.write(data)
        args = [self.binary, path, scenario, str(transfer['crc32'])]
        trace_path = os.path.join(self.tmp.name, 'frames.txt')
        if trace:
            args.append(trace_path)
        result = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        if trace:
            with open(trace_path) as f:
                return [bytes.fromhex(line) for line in f]
        return []

    def test_text_only_and_protocol_failures(self):
        for scenario in ('text', 'rollover', 'format', 'offset', 'crc',
                         'abort', 'timeout', 'fence_timeout', 'busy'):
            with self.subTest(scenario=scenario):
                self.run_job(None, scenario)

    def test_images_roundtrip_without_losing_adjacent_runs(self):
        self.run_job(b'\xf8\x00'*9216, 'solid')
        self.run_job(b'\x00\x00'*9216, 'black')
        checker = b''.join((b'\xf8\x00' if (x+y) % 2 else b'\x07\xe0')
                           for y in range(96) for x in range(96))
        self.run_job(checker, 'checker')
        rng = random.Random(123)
        colors = [b'\x00\x00', b'\xf8\x00', b'\x00\x1f', b'\x07\xe0']
        self.run_job(b''.join(rng.choice(colors) for _ in range(9216)), 'noise')

    def test_thumbnail_palette_is_restored_after_text(self):
        self.run_job(b'\x05\xff'*9216, 'solid')

    def test_gbk_material_wrap_preserves_every_transferred_character(self):
        for material in ('A' + '\u6750'*40, '\u6750'*40):
            self.run_job(b'\x00\x1f'*9216, 'gbk', material)

    def test_long_filename_wrap_stays_above_temperature_panel(self):
        for filename in ('parts/' + 'A'*100 + '.gcode',
                         'parts/' + 'A\u6750'*40 + '.gcode'):
            self.run_job(None, 'filename', filename=filename)

    def test_filename_lines_balance_at_separators(self):
        cases = (
            ('Angel_0.12mm_PLA_Klipper_1h23m.gcode',
             (b'Angel_0.12mm_PLA', b'Klipper_1h23m')),
            ('Long model name PLA Klipper 1h23m.gcode',
             (b'Long model name', b'PLA Klipper 1h23m')),
            ('A'*40+'.gcode', (b'A'*20, b'A'*20)),
            ('short.gcode', (b'short',)),
        )
        for filename, expected in cases:
            with self.subTest(filename=filename):
                frames = self.run_job(None, 'layout', filename=filename,
                                      trace=True)
                rows = [f for f in frames if f[0] == 0x98
                        and int.from_bytes(f[3:5], 'big') in (174, 192)]
                self.assertEqual(tuple(f[12:] for f in rows), expected)
                for f in rows:
                    self.assertEqual(int.from_bytes(f[1:3], 'big'),
                                     (240-len(f[12:])*8)//2)

    def test_height_value_stays_on_same_lowered_row(self):
        frames = self.run_job(None, 'layout', trace=True)
        labels = [f for f in frames if f[0] == 0x98 and f[12:] == b'Height:']
        self.assertEqual(len(labels), 1)
        self.assertEqual(int.from_bytes(labels[0][1:3], 'big'), 126)
        self.assertEqual(126-(102+16), 8)
        self.assertEqual(int.from_bytes(labels[0][3:5], 'big'), 52)
        values = [f for f in frames if f[0] == 0x98 and f[12:] == b' 0.18mm']
        self.assertEqual(len(values), 1)
        self.assertEqual(int.from_bytes(values[0][3:5], 'big'), 52)

    def test_degree_primitives_and_ascii_temperature_cells(self):
        frames = self.run_job(None, 'layout', trace=True)
        values = [f[12:] for f in frames if f[0] == 0x98
                  and int.from_bytes(f[3:5], 'big') == 264]
        self.assertEqual(values, [b'237 C', b'62 C'])
        # The harness checks both white ring outlines and their empty centers.
        rings = [f for f in frames if f[0] == 0x5b
                 and int.from_bytes(f[3:5], 'big') == 266]
        self.assertEqual(len(rings), 2)

    def test_missing_and_zero_temperature_units(self):
        for value, expected in ((None, b'--'), (0, b'0 C')):
            frames = self.run_job(None, 'layout', trace=True, targets={
                'nozzle_target': value, 'bed_target': value})
            values = [f[12:] for f in frames if f[0] == 0x98
                      and int.from_bytes(f[3:5], 'big') == 264]
            self.assertEqual(values, [expected, expected])
            rings = [f for f in frames if f[0] == 0x5b
                     and int.from_bytes(f[3:5], 'big') == 266]
            self.assertEqual(len(rings), 0 if value is None else 2)

    def test_invalid_header_lengths_flags_and_gbk_are_rejected(self):
        for damage in ((5, 129), (7, 24), (13, 27), (34, 0)):
            with self.subTest(damage=damage):
                self.run_job(None, 'invalid', damage=damage)

    def test_idle_render_and_failures_use_the_bounded_transport(self):
        for scenario in ('idle', 'crc', 'offset', 'abort', 'fence_timeout'):
            with self.subTest(scenario=scenario):
                self.run_job(None, scenario, idle=True)

    def test_idle_firmware_identity_fits_original_v5_text_slot(self):
        for version in ('43fc76cb-dirty-20261029_211120-IrohKlipper',
                        'v0.13.0-440-g43fc76cb-20261029_211120-host',
                        'unknown'):
            with self.subTest(version=version):
                self.run_job(None, 'idle', idle=True, idle_version=version)

    def test_idle_text_too_wide_is_rejected_before_drawing(self):
        self.run_job(None, 'invalid', idle=True, damage=(13, 13))

    def test_idle_keeps_url_and_centers_firmware_using_verified_8x16_font(self):
        frames = self.run_job(None, 'idle', idle=True, trace=True,
            idle_version='43fc76cb-dirty-20261029_211120-IrohKlipper',
            idle_footer=True)
        text_frames = [f for f in frames if f[0] == 0x98]
        url = next(f for f in text_frames
                   if b'github.com/navaismo/klipper' in f)
        self.assertEqual(int.from_bytes(url[1:3], 'big'), 12)
        self.assertEqual(int.from_bytes(url[3:5], 'big'), 207)
        self.assertEqual(url[7], 1)
        firmware = next(f for f in text_frames if b'FW 43fc76cb' in f)
        self.assertEqual(int.from_bytes(firmware[1:3], 'big'), 12)
        self.assertEqual(int.from_bytes(firmware[3:5], 'big'), 304)
        self.assertEqual(firmware[7], 1)
        width = len(firmware[12:]) * 8
        x = int.from_bytes(firmware[1:3], 'big')
        self.assertEqual(x + width // 2, 120)
        self.assertLessEqual(x + width, 240)
        idle = next(f for f in text_frames if f[12:] == b'Idle')
        self.assertEqual(int.from_bytes(idle[3:5], 'big'), 276)
        self.assertLessEqual(int.from_bytes(idle[3:5], 'big') + 24, 304)


if __name__ == '__main__':
    unittest.main()
