# Coordinate canvas lifecycle and unchanged-region tests with fake hardware
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import os, shutil, subprocess, tempfile, unittest
from unittest import mock
import test_tjc_lcd as startup
import lcd_canvas


class HostTests(unittest.TestCase):
    def setUp(self):
        startup.HostTests.setUp(self)
        self.commands = {}
        self.sent = []
        for name in ('begin', 'rect', 'text', 'end', 'abort'):
            command = self.commands[name] = mock.Mock()
            setattr(self.module, 'canvas_' + name + '_cmd', command)
            def send(args, name=name):
                self.sent.append((name, args))
                if name != 'abort':
                    self.ack(0 if name == 'begin' else args[2],
                             2 if name == 'end' else 1)
            command.send.side_effect = send
        self.module.connected = True
        self.module.state = 'ready'

    def ack(self, index, state, error=0, token=None):
        self.module._handle_canvas_result({
            'token': self.module.job_token if token is None else token,
            'index': index, 'state': state, 'error': error})
        self.reactor.register_async_callback.call_args[0][0](11.)

    def test_initial_draw_then_only_affected_rows_without_clear(self):
        self.module.begin_calibration('Warming up', 170, 50)
        self.assertEqual(self.sent[0][0], 'begin')
        self.assertEqual(self.sent[0][1][2], 1)
        self.sent = []
        self.module.update_calibration('cr_touch', 'Test P1 pair 1')
        self.module.update_calibration('nozzle', 'Test P1 pair 1')
        self.module.update_calibration('computing', 'Computing offset')
        self.assertTrue(all(args[2] == 0 for name, args in self.sent
                            if name == 'begin'))
        for name, args in self.sent:
            if name == 'rect':
                # Status strip or panel rows, never the logo/title region.
                self.assertTrue(args[6] <= 21 or args[4] >= 211)
        self.assertEqual(self.module.state, 'ready')
        self.assertIsNone(self.module.job_completion)
        self.module.end_calibration('Offset 1.462mm', True)
        self.assertEqual(self.module.screen, 'calibration_result')
        self.assertFalse(self.module.calibration_active)
        self.assertEqual(self.sent[-1][0], 'end')

    def test_host_bounds_validation_precedes_any_uart_claim(self):
        for operations in ([('rect', 0, 0, 240, 319, 0)],
                           [('rect', 10, 0, 9, 2, 0)],
                           [('text', 230, 310, 1, 1, 0, 'No')],
                           [('text', 0, 0, 2, 1, 0, 'X')]):
            with self.assertRaises(ValueError):
                self.module.render_canvas(operations)
        self.assertEqual(self.sent, [])

    def test_missing_or_wrong_ack_aborts_without_claiming_success(self):
        self.commands['rect'].send.side_effect = lambda args: self.ack(999, 1)
        with self.assertLogs(level='WARNING'):
            with self.assertRaisesRegex(RuntimeError, 'acknowledgement'):
                self.module.render_canvas([lcd_canvas.rect(8, 9, 10, 11, 0)])
        self.commands['abort'].send.assert_called_once()
        self.assertEqual(self.module.state, 'error')
        self.assertIsNone(self.module.screen)

    def test_stale_token_cannot_complete_new_canvas(self):
        self.commands['begin'].send.side_effect = lambda args: self.ack(
            0, 1, token=args[1]-1)
        with self.assertLogs(level='WARNING'):
            with self.assertRaisesRegex(RuntimeError, 'acknowledgement'):
                self.module.render_canvas([])
        self.commands['abort'].send.assert_called_once()

    def test_print_or_pause_prevents_any_canvas_traffic(self):
        for state in ('printing', 'paused'):
            self.stats.get_status.return_value = {'state': state}
            with self.assertRaisesRegex(RuntimeError, 'idle printer'):
                self.module.render_canvas([])
        self.assertEqual(self.sent, [])

    def test_calibration_reserves_lcd_and_invalidates_queued_idle(self):
        self.module._schedule_idle()
        callback = self.reactor.register_callback.call_args[0][0]
        self.module.begin_calibration('Warming up')
        count = len(self.sent)
        callback(12.)
        self.module._handle_idle_timeout(12.)
        self.assertEqual(len(self.sent), count)
        with self.assertRaisesRegex(RuntimeError, 'reserved'):
            self.module._handle_before_print('job.gcode')

    def test_disconnect_prevents_final_screen(self):
        self.module.begin_calibration('Warming up')
        self.module._handle_disconnect()
        count = len(self.sent)
        self.module.end_calibration('Failed', False)
        self.assertEqual(len(self.sent), count)
        self.assertFalse(self.module.calibration_active)

    def test_old_firmware_has_no_blinking_fallback(self):
        self.module.canvas_begin_cmd = None
        self.assertFalse(self.module.begin_calibration('Warming up'))
        self.assertEqual(self.sent, [])

    def test_capability_registers_coordinate_commands(self):
        self.mcu.get_constants.return_value = {'TJC_LCD_VERSION': 1,
                                               'TJC_CANVAS_VERSION': 1}
        names = ('begin', 'rect', 'text', 'end', 'abort')
        self.mcu.lookup_command.side_effect = [self.mcu.start, self.mcu.clear] \
            + [self.commands[n] for n in names]
        self.module._build_config()
        self.assertIs(self.module.canvas_rect_cmd, self.commands['rect'])
        self.mcu.register_serial_response.assert_any_call(
            self.module._handle_canvas_result,
            'tjc_lcd_canvas_result oid=%c token=%u index=%hu state=%c error=%c',
            self.module.oid)

    def test_manual_rectangle_uses_coordinates_without_clear(self):
        gcmd = mock.Mock()
        values = {'X0': 20, 'Y0': 100, 'X1': 90, 'Y1': 120, 'COLOR': 34}
        gcmd.get_int.side_effect = lambda name, **kw: values[name]
        self.module.cmd_TJC_LCD_RECT(gcmd)
        self.assertEqual(self.sent[0][1][2], 0)
        rectangle = [a for name, a in self.sent if name == 'rect']
        self.assertEqual(rectangle[0][3:], [20, 100, 90, 120, 34])
        self.assertEqual(self.module.screen, 'custom')
        self.objects['toolhead'].wait_moves.assert_called_once()

    def test_manual_rectangle_cannot_interrupt_calibration(self):
        self.module.begin_calibration('Warming up')
        count = len(self.sent)
        gcmd = mock.Mock(error=RuntimeError)
        with self.assertRaisesRegex(RuntimeError, 'reserved'):
            self.module.cmd_TJC_LCD_RECT(gcmd)
        self.assertEqual(len(self.sent), count)


class NativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which('gcc') is None:
            raise unittest.SkipTest('Native GCC is required')
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        for name, data in startup.STUBS.items():
            path = os.path.join(cls.tmp.name, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w') as out:
                out.write(data)
                if name == 'command.h':
                    out.write('\nvoid *command_decode_ptr(uint32_t);\n')
        for name in ('tjc_canvas.c', 'tjc_lcd.h', 'tjc_uart.h'):
            shutil.copyfile(os.path.join(startup.ROOT, 'src', name),
                            os.path.join(cls.tmp.name, name))
        harness = os.path.join(startup.ROOT, 'test/unit/tjc_canvas_harness.c')
        cls.binary = os.path.join(cls.tmp.name, 'canvas-test')
        result = subprocess.run(['gcc', '-std=gnu11', '-Wall', '-Wextra',
                        '-Werror',
                        '-Wno-unused-parameter', '-I', cls.tmp.name, harness,
                        '-o', cls.binary], capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stderr)

    def write_ops(self, name, operations):
        path = os.path.join(self.tmp.name, name)
        with open(path, 'w') as out:
            for op in lcd_canvas.validate(operations):
                values = list(op)
                if op[0] == 'text':
                    values[-1] = values[-1].hex()
                out.write(' '.join(map(str, values)) + '\n')
        return path

    def test_actual_receiver_draws_once_then_preserves_static_artwork(self):
        initial = self.write_ops('initial', lcd_canvas.initial_view('Warm-up'))
        for scenario in ('draw', 'rollover'):
            for phase in lcd_canvas.PHASES:
                patch = self.write_ops('patch', lcd_canvas.highlight(
                    'cr_touch', phase, 'Calibrate P2 pair 3'))
                result = subprocess.run([self.binary, scenario, initial, patch],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_canvas_sessions_start_at_current_clock_after_waiting(self):
        initial = self.write_ops('initial', lcd_canvas.initial_view('Warm-up'))
        patch = self.write_ops('patch', lcd_canvas.highlight(
            None, 'cr_touch', 'Test P1 pair 1'))
        for scenario in ('late_start', 'late_patch'):
            with self.subTest(scenario=scenario):
                result = subprocess.run([self.binary, scenario, initial, patch],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_protocol_errors_cleanup_and_shutdown_are_bounded(self):
        for scenario in ('busy', 'bounds', 'text_bounds', 'sequence', 'gbk',
                         'stale', 'abort', 'timeout', 'fence_timeout',
                         'uart_error', 'shutdown'):
            with self.subTest(scenario=scenario):
                result = subprocess.run([self.binary, scenario],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
