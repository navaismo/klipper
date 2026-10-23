# Exercise real virtual-SD start paths with simulated printer dependencies
#
# Copyright (C) 2026 navaismo
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)),
                              '..', '..', 'klippy', 'extras'))
import lcd_status as lcd
import virtual_sdcard


class Config:
    def __init__(self, printer, values=None):
        self.printer = printer
        self.values = values or {}
    def get_printer(self):
        return self.printer
    def get(self, name, default=None):
        return self.values.get(name, default)
    def getfloat(self, name, default, **kwargs):
        return self.get(name, default)
    def getboolean(self, name, default):
        return self.get(name, default)
    def error(self, message):
        return RuntimeError(message)


class PrintStats:
    def __init__(self, events):
        self.events = events
        self.reset()
    def reset(self):
        self.state = 'standby'
    def set_current_file(self, filename):
        self.reset()
    def get_status(self, eventtime):
        return {'state': self.state}
    def note_start(self):
        self.state = 'printing'
        self.events.append('print_started')
    def note_complete(self):
        if self.state != 'cancelled':
            self.state = 'complete'
    def note_pause(self):
        self.state = 'paused'
    def note_cancel(self):
        self.state = 'cancelled'


class Printer:
    def __init__(self):
        self.handlers = {}
        self.events = []
        self.shutdown = False
        self.reactor = mock.Mock(NOW=0., NEVER=99999999.)
        self.reactor.monotonic.return_value = 0.
        self.reactor.register_timer.side_effect = self.register_timer
        self.gcode = mock.Mock()
        self.gcode.error = RuntimeError
        self.gcode.get_mutex.return_value.test.return_value = False
        self.gcode.run_script.side_effect = self.record_gcode
        self.executor = mock.Mock()
        self.executor.submit.side_effect = (
            lambda fn, *args, **kwargs: fn(*args, **kwargs))
        self.executor.wrap_obj.side_effect = lambda obj: obj
        aio = mock.Mock()
        aio.allocate_executor.return_value = self.executor
        self.stats = PrintStats(self.events)
        self.objects = {'gcode': self.gcode, 'aio_executor': aio,
                        'print_stats': self.stats, 'gcode_macro': mock.Mock()}
    def register_timer(self, callback, when):
        self.events.append('timer_scheduled')
        return object()
    def record_gcode(self, line):
        self.events.append('gcode:' + line)
    def register_event_handler(self, event, callback):
        self.handlers.setdefault(event, []).append(callback)
    def send_event(self, event, *args):
        return [cb(*args) for cb in self.handlers.get(event, [])]
    def get_reactor(self):
        return self.reactor
    def is_shutdown(self):
        return self.shutdown
    def load_object(self, config, name):
        return self.objects[name]
    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)


class AutoStartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.printer = Printer()
        self.sd = virtual_sdcard.VirtualSD(Config(self.printer,
                                                 {'path': self.tmp.name}))
        self.printer.objects['virtual_sdcard'] = self.sd
        self.addCleanup(self.close_file)
        os.mkdir(os.path.join(self.tmp.name, 'parts'))
        for filename in ('parts/Job with spaces.gcode', 'other.gcode'):
            with open(os.path.join(self.tmp.name, filename), 'w') as f:
                # Only the fake G-code recorder sees these marker strings.
                f.write('FIRST_TEST_MARKER\nSECOND_TEST_MARKER\n')
        self.module = None

    def close_file(self):
        if self.sd.current_file is not None:
            self.sd.current_file.close()

    def load_lcd(self, auto=True):
        self.module = lcd.LCDStatus(Config(self.printer,
                                          {'auto_prepare': auto}))
        self.printer.objects['lcd_status'] = self.module
        self.module.source = mock.Mock()
        self.module.source.prepare.side_effect = self.collect
        self.printer.send_event('klippy:ready')

    def collect(self, filename):
        self.assertIsNone(self.sd.work_timer)
        self.assertEqual(self.printer.stats.state, 'standby')
        self.printer.events.append('collected:' + filename)
        return lcd.finalize_job(lcd.prepare_metadata(filename, {
            'layer_count': 221, 'layer_height': .18,
            'estimated_time': 12552, 'filament_type': 'PETG'}))

    def command(self, filename):
        gcmd = mock.Mock()
        gcmd.error.side_effect = lambda message: RuntimeError(message)
        gcmd.get.return_value = filename
        gcmd.get_raw_command_parameters.return_value = filename
        return gcmd

    def test_moonraker_path_prepares_before_first_gcode(self):
        self.load_lcd()
        self.sd.cmd_SDCARD_PRINT_FILE(self.command(
            'parts/Job with spaces.gcode'))
        self.assertEqual(self.printer.events, [
            'collected:parts/Job with spaces.gcode', 'timer_scheduled'])
        self.assertEqual(self.module.job['display']['estimated_time'],
                         'ETA: 03:29:12')
        self.sd.work_handler(0.)
        self.assertEqual(self.printer.events[2:4],
                         ['print_started', 'gcode:FIRST_TEST_MARKER'])
        self.assertEqual(self.printer.stats.state, 'complete')
        self.module.source.prepare.assert_called_once()

    def test_m23_then_m24_and_actual_case(self):
        self.load_lcd()
        self.sd.cmd_M23(self.command('OTHER.GCODE'))
        self.module.source.prepare.assert_not_called()
        self.sd.cmd_M24(self.command(''))
        self.module.source.prepare.assert_called_once_with('other.gcode')
        self.assertEqual(self.printer.events[-1], 'timer_scheduled')

    def test_finished_event_runs_after_sd_stops_and_file_gcode_finishes(self):
        finished = []
        def done(state):
            self.assertFalse(self.sd.is_active())
            self.assertEqual(self.printer.stats.state, 'complete')
            self.assertIn('gcode:SECOND_TEST_MARKER', self.printer.events)
            finished.append(state)
        self.printer.register_event_handler('virtual_sdcard:finished', done)
        self.sd.cmd_SDCARD_PRINT_FILE(self.command('other.gcode'))
        self.sd.work_handler(0.)
        self.assertEqual(finished, ['complete'])

    def test_pause_emits_no_finished_event_and_cancel_emits_once(self):
        finished = []
        self.printer.register_event_handler('virtual_sdcard:finished',
                                            finished.append)
        self.sd.cmd_SDCARD_PRINT_FILE(self.command('other.gcode'))
        def pause(line):
            self.sd.must_pause_work = True
        self.printer.gcode.run_script.side_effect = pause
        self.sd.work_handler(0.)
        self.assertEqual(finished, [])
        self.assertEqual(self.printer.stats.state, 'paused')
        self.sd.do_cancel()
        self.assertEqual(finished, ['cancelled'])

    def test_cancel_inside_file_emits_once_after_worker_exits(self):
        finished = []
        def done(state):
            self.assertFalse(self.sd.is_active())
            finished.append(state)
        self.printer.register_event_handler('virtual_sdcard:finished', done)
        self.sd.cmd_SDCARD_PRINT_FILE(self.command('other.gcode'))
        self.printer.gcode.run_script.side_effect = lambda line: (
            self.sd.do_cancel())
        self.sd.work_handler(0.)
        self.assertEqual(finished, ['cancelled'])

    def test_card_render_finishes_before_scheduling_first_gcode(self):
        self.load_lcd()
        display = mock.Mock()
        def render(job):
            self.assertIsNone(self.sd.work_timer)
            self.assertEqual(self.printer.stats.state, 'standby')
            self.printer.events.append('render_complete')
        display.render_job.side_effect = render
        self.printer.objects['tjc_lcd'] = display
        self.sd.cmd_SDCARD_PRINT_FILE(self.command('other.gcode'))
        self.assertEqual(self.printer.events,
                         ['collected:other.gcode', 'render_complete',
                          'timer_scheduled'])
        self.sd.work_handler(0.)
        self.assertEqual(self.printer.events[3:5],
                         ['print_started', 'gcode:FIRST_TEST_MARKER'])

    def test_resume_skips_preparation_even_at_position_zero(self):
        self.load_lcd()
        self.sd.cmd_SDCARD_PRINT_FILE(self.command('other.gcode'))
        self.sd.work_timer = None
        self.printer.stats.state = 'paused'
        self.sd.cmd_M24(self.command(''))
        self.module.source.prepare.assert_called_once()
        self.assertEqual(self.printer.events.count('timer_scheduled'), 2)

    def test_same_filename_new_print_collects_fresh_data(self):
        self.load_lcd()
        self.sd.cmd_SDCARD_PRINT_FILE(self.command('other.gcode'))
        self.sd.work_handler(0.)
        self.sd.cmd_SDCARD_PRINT_FILE(self.command('other.gcode'))
        self.assertEqual(self.module.source.prepare.call_count, 2)

    def test_failure_keeps_file_unread_and_start_retryable(self):
        self.load_lcd()
        self.module.source.prepare.side_effect = lcd.DataError('HTTP 503')
        with self.assertLogs(level='ERROR'):
            with self.assertRaisesRegex(RuntimeError, 'LCD preparation failed'):
                self.sd.cmd_SDCARD_PRINT_FILE(self.command('other.gcode'))
        self.assertIsNone(self.sd.work_timer)
        self.assertFalse(self.sd.preflight_pending)
        self.assertFalse(self.sd.preflight_done)
        self.assertEqual(self.sd.current_file.tell(), 0)
        self.assertEqual(self.printer.stats.state, 'standby')
        self.printer.gcode.run_script.assert_not_called()
        self.assertEqual(self.module.state, 'error')
        self.module.source.prepare.side_effect = self.collect
        self.sd.cmd_M24(self.command(''))
        self.assertTrue(self.sd.preflight_done)

    def test_no_lcd_or_disabled_auto_preserves_start(self):
        self.sd.cmd_SDCARD_PRINT_FILE(self.command('other.gcode'))
        self.assertEqual(self.printer.events, ['timer_scheduled'])
        self.sd.work_handler(0.)
        self.load_lcd(auto=False)
        self.sd.cmd_SDCARD_PRINT_FILE(self.command('other.gcode'))
        self.module.source.prepare.assert_not_called()
        self.assertEqual(self.printer.events[-1], 'timer_scheduled')

    def test_reset_during_collection_discards_result(self):
        self.load_lcd()
        def reset(filename):
            self.sd._reset_file()
            return lcd.finalize_job(lcd.prepare_metadata(filename, {}))
        self.module.source.prepare.side_effect = reset
        with self.assertLogs(level='ERROR'):
            with self.assertRaisesRegex(RuntimeError, 'invalidated'):
                self.sd.cmd_SDCARD_PRINT_FILE(self.command('other.gcode'))
        self.assertIsNone(self.sd.work_timer)
        self.assertIsNone(self.module.job)
        self.assertEqual(self.module.state, 'empty')

    def test_shutdown_during_collection_cannot_start(self):
        self.load_lcd()
        def shutdown(filename):
            self.printer.shutdown = True
            self.printer.send_event('klippy:shutdown')
            return lcd.finalize_job(lcd.prepare_metadata(filename, {}))
        self.module.source.prepare.side_effect = shutdown
        with self.assertLogs(level='ERROR'):
            with self.assertRaisesRegex(RuntimeError, 'requires ready Klipper'):
                self.sd.cmd_SDCARD_PRINT_FILE(self.command('other.gcode'))
        self.assertIsNone(self.sd.work_timer)
        self.assertIsNone(self.module.job)

    def test_generic_hook_rejects_file_or_position_changes(self):
        def move_position(filename):
            self.sd.file_position = 10
        self.printer.register_event_handler('virtual_sdcard:before_print',
                                             move_position)
        with self.assertRaisesRegex(RuntimeError, 'changed during preparation'):
            self.sd.cmd_SDCARD_PRINT_FILE(self.command('other.gcode'))
        self.assertIsNone(self.sd.work_timer)
        self.assertFalse(self.sd.preflight_pending)

    def test_generic_hook_rejects_reentrant_resume(self):
        self.printer.register_event_handler('virtual_sdcard:before_print',
                                            lambda filename:
                                            self.sd.do_resume())
        with self.assertRaisesRegex(RuntimeError, 'SD busy'):
            self.sd.cmd_SDCARD_PRINT_FILE(self.command('other.gcode'))
        self.assertIsNone(self.sd.work_timer)
        self.assertFalse(self.sd.preflight_pending)


if __name__ == '__main__':
    unittest.main()
