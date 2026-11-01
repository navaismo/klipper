# Offline compatibility tests for upstream homing and probe events.
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import importlib
import os
import sys
import types
import unittest
from unittest import mock

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'klippy'))
PACKAGE = '_homing_event_test_extras'
package = types.ModuleType(PACKAGE)
package.__path__ = [os.path.join(ROOT, 'klippy', 'extras')]
with mock.patch.dict(sys.modules, {PACKAGE: package}):
    homing = importlib.import_module(PACKAGE + '.homing')
    probe = importlib.import_module(PACKAGE + '.probe')
    gcode_move = importlib.import_module(PACKAGE + '.gcode_move')


class Printer:
    command_error = RuntimeError

    def __init__(self):
        self.events = []
        self.handlers = {}
        self.toolhead = mock.Mock()
        self.toolhead.get_status.return_value = {'homed_axes': 'xyz'}
        self.gcode = mock.Mock()
        self.primary = mock.Mock()
        self.reactor = mock.Mock()

    def lookup_object(self, name):
        return {'toolhead': self.toolhead, 'gcode': self.gcode,
                'probe': self.primary}[name]

    def get_reactor(self):
        return self.reactor

    def register_event_handler(self, event, callback):
        self.handlers.setdefault(event, []).append(callback)

    def send_event(self, event, *args):
        self.events.append(event)
        return [cb(*args) for cb in self.handlers.get(event, [])]


class HomingEventTests(unittest.TestCase):
    def setUp(self):
        self.printer = Printer()
        self.home = homing.Homing(self.printer)
        self.home.set_axes([2])
        self.home._set_start_position = mock.Mock()
        self.home._probing_home = mock.Mock()
        self.rail = mock.Mock()
        self.rail.get_endstops.return_value = [(
            types.SimpleNamespace(get_position_endstop=lambda: 1.065), 'z')]
        self.rail.get_homing_info.return_value = types.SimpleNamespace(
            speed=5., retract_dist=0.)

    def test_z_probe_home_emits_completed_axes_after_session_closes(self):
        session = self.printer.primary.start_probe_session.return_value
        completed = mock.Mock()
        self.printer.register_event_handler('homing:home_axes', completed)

        def check_completed(state):
            session.end_probe_session.assert_called_once()
            self.assertIs(state, self.home)
        completed.side_effect = check_completed
        self.home.home_rails([self.rail], [None, None, 5.],
                             [None, None, 0.])
        self.assertEqual(self.printer.events, ['homing:home_axes'])
        self.home._probing_home.assert_called_once()
        completed.assert_called_once_with(self.home)

    def test_z_probe_failure_does_not_emit_completed_axes(self):
        self.home._probing_home.side_effect = RuntimeError('probe failed')
        with self.assertRaisesRegex(RuntimeError, 'probe failed'):
            self.home.home_rails([self.rail], [None, None, 5.],
                                 [None, None, 0.])
        self.assertNotIn('homing:home_axes', self.printer.events)

    def test_normal_home_also_emits_completed_axes(self):
        self.rail.get_endstops.return_value = [(object(), 'z')]
        self.home._do_home_rails = mock.Mock()
        self.home.home_rails([self.rail], [None, None, 5.],
                             [None, None, 0.])
        self.home._do_home_rails.assert_called_once()
        self.assertEqual(self.printer.events, ['homing:home_axes'])

    def test_mixed_probe_rails_remain_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'mix of probe'):
            self.home.home_rails([self.rail, self.rail], [None, None, 5.],
                                 [None, None, 0.])
        self.home._probing_home.assert_not_called()
        self.assertEqual(self.printer.events, [])

    def test_z_home_resets_only_z_gcode_origin(self):
        move = gcode_move.GCodeMove.__new__(gcode_move.GCodeMove)
        move.reset_last_position = mock.Mock()
        move.base_position = [3., 4., 99., 6.]
        move.homing_position = [0., 0., 1.25, 0.]
        self.printer.register_event_handler('homing:home_axes',
                                            move._handle_homing_home_axes)
        self.home.home_rails([self.rail], [None, None, 5.],
                             [None, None, 0.])
        self.assertEqual(move.base_position, [3., 4., 1.25, 6.])
        move.reset_last_position.assert_called_once()


class ProbeEventTests(unittest.TestCase):
    def run_probe(self, nozzle_probe=None):
        printer = Printer()
        config = mock.Mock()
        config.get_printer.return_value = printer
        kwargs = {} if nozzle_probe is None else {'nozzle_probe': nozzle_probe}
        helper = probe.SampleAveragingHelper(config, mock.Mock(), mock.Mock(),
                                             **kwargs)
        helper.hw_probe_session = mock.Mock()
        result = probe.manual_probe.create_probe_result(
            [28., 28., 1.215], [-24., -14.5, 1.065])
        helper.hw_probe_session.pull_probed_results.return_value = [result]
        command = mock.Mock()
        command.get_command.return_value = 'G28'
        return printer, helper, command, result

    def test_cr_touch_default_keeps_probe_result_event(self):
        printer, helper, command, result = self.run_probe()
        self.assertIs(helper._probe(command), result)
        self.assertEqual(printer.events, ['probe:update_results'])

    def test_nozzle_probe_skips_axis_twist_result_event(self):
        printer, helper, command, result = self.run_probe(True)
        self.assertIs(helper._probe(command), result)
        self.assertEqual(printer.events, [])

    def test_unhomed_probe_still_rejected(self):
        printer, helper, command, result = self.run_probe()
        printer.toolhead.get_status.return_value = {'homed_axes': 'xy'}
        with self.assertRaisesRegex(RuntimeError, 'Must home'):
            helper._probe(command)
        helper.hw_probe_session.run_probe.assert_not_called()
        self.assertEqual(printer.events, [])


if __name__ == '__main__':
    unittest.main()
