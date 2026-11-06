# Native bounded HX711 contact acquisition; no printer connection
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import configparser
import inspect
import types
import unittest
from unittest import mock

import test_auto_z_offset as engine
import configfile

lcprobe = engine.lcprobe


class NativeContactTests(unittest.TestCase):
    def setUp(self):
        self.printer = engine.Printer()
        self.printer.toolhead.position = [28., 28., 5., 0.]

    def make_move(self):
        move = lcprobe.LoadCellProbingMove.__new__(lcprobe.LoadCellProbingMove)
        move._printer = self.printer
        move._load_cell = self.printer.cell
        move._z_min_position = -3.
        move._pause_and_tare = mock.Mock()
        move._param_helper = mock.Mock()
        move._param_helper.get_probe_params.return_value = {'probe_speed': .5}
        move._mcu_trigger_analog = mock.Mock()
        move._log_trace = False
        collector = mock.Mock()
        move._start_collector = mock.Mock(return_value=collector)
        homing = mock.Mock()
        homing.probing_move.return_value = [28., 28., -.2, 0.]
        self.printer.objects['homing'] = homing
        return move, collector, homing

    def test_one_native_descent_to_existing_endpoint_at_configured_speed(self):
        move, collector, homing = self.make_move()
        pos, returned = move.probing_move(engine.Gcmd(), -.3, 5.3)
        homing.probing_move.assert_called_once()
        args, kwargs = homing.probing_move.call_args
        self.assertIs(args[0], move._mcu_trigger_analog)
        self.assertAlmostEqual(args[1][2], -.3)
        self.assertEqual(args[2], .5)
        self.assertEqual(kwargs, {})
        self.assertIs(returned, collector)
        self.assertEqual(pos[2], -.2)
        collector.snapshot_until.assert_not_called()
        collector.collect_until.assert_not_called()
        move._pause_and_tare.assert_called_once()

    def test_travel_distance_can_only_shorten_the_endpoint(self):
        move, collector, homing = self.make_move()
        move.probing_move(engine.Gcmd(), -.3, .25)
        self.assertEqual(homing.probing_move.call_args.args[1][2], 4.75)

    def test_native_global_minimum_is_still_a_hard_limit(self):
        move, collector, homing = self.make_move()
        move.probing_move(engine.Gcmd(), -10., 20.)
        self.assertEqual(homing.probing_move.call_args.args[1][2], -3.)

    def test_incomplete_or_invalid_bounds_fail_before_tare_or_motion(self):
        for floor, distance in ((None, 1.), (-.3, None), (-.3, 0.),
                                (float('nan'), 1.), (5., 1.)):
            move, collector, homing = self.make_move()
            with self.assertRaisesRegex(RuntimeError, 'bound'):
                move.probing_move(engine.Gcmd(), floor, distance)
            move._pause_and_tare.assert_not_called()
            homing.probing_move.assert_not_called()

    def test_uncalibrated_cell_cannot_start_acquisition(self):
        move, collector, homing = self.make_move()
        self.printer.cell.calibrated = False
        with self.assertRaisesRegex(RuntimeError, 'not calibrated'):
            move.probing_move(engine.Gcmd(), -.3, 5.3)
        move._pause_and_tare.assert_not_called()
        homing.probing_move.assert_not_called()

    def test_native_error_is_preserved_and_collector_closed(self):
        for message in ('Trigger analog error: RAW_RANGE',
                        'No trigger on probe after full movement',
                        'Communication timeout', 'Emergency stop'):
            move, collector, homing = self.make_move()
            homing.probing_move.side_effect = RuntimeError(message)
            with self.assertRaisesRegex(RuntimeError, message):
                move.probing_move(engine.Gcmd(), -.3, 5.3)
            homing.probing_move.assert_called_once()
            collector.stop_collecting.assert_called_once()

    def test_tare_failure_cannot_start_motion(self):
        move, collector, homing = self.make_move()
        move._pause_and_tare.side_effect = RuntimeError('tare failed')
        with self.assertRaisesRegex(RuntimeError, 'tare failed'):
            move.probing_move(engine.Gcmd(), -.3, 5.3)
        homing.probing_move.assert_not_called()
        move._start_collector.assert_not_called()

    def test_native_parameter_helper_preserves_independent_probe_speeds(self):
        parser = configparser.RawConfigParser()
        parser.read_string('[auto_z_offset]\nspeed: 0.5\nlift_speed: 0.1\n'
                           'sample_retract_dist: 0.5\n'
                           '[bltouch]\nspeed: 5\nlift_speed: 10\n')
        cfg = configfile.ConfigWrapper(self.printer, parser, {},
                                       'auto_z_offset')
        helper = lcprobe.LoadCellParameterHelper(cfg)
        self.assertEqual(helper.get_probe_params(engine.Gcmd())['probe_speed'],
                         .5)
        cfg = configfile.ConfigWrapper(self.printer, parser, {}, 'bltouch')
        params = lcprobe.probe.ProbeParameterHelper(cfg).get_probe_params(
            engine.Gcmd())
        self.assertEqual(params['probe_speed'], 5.)

    def test_segmented_hook_and_streaming_poll_are_no_longer_available(self):
        self.assertNotIn('move_handler', inspect.signature(
            engine.homing.HomingMove.homing_move).parameters)
        self.assertNotIn('onset_stop', inspect.signature(
            lcprobe.LoadCellProbingMove.probing_move).parameters)
        self.assertFalse(hasattr(lcprobe, 'load_cell_contact_move'))
        self.assertFalse(hasattr(lcprobe.load_cell.LoadCellSampleCollector,
                                 'snapshot_until'))


class NativeGuardTests(unittest.TestCase):
    def native_move(self, trigger=100.1, error=None, motion_error=None):
        printer = engine.Printer()
        th = printer.toolhead
        th.flush_step_generation = mock.Mock()
        stepper = mock.Mock()
        stepper.get_name.return_value = 'stepper_z'
        stepper.get_commanded_position.return_value = 5.
        th.get_kinematics = mock.Mock()
        th.get_kinematics.return_value.get_steppers.return_value = [stepper]
        th.get_last_move_time = mock.Mock(return_value=100.)
        th.dwell = th.set_position = mock.Mock()
        state = {'armed': False, 'events': []}
        endstop = mock.Mock()
        endstop.get_steppers.return_value = [stepper]
        def arm(*args, **kwargs):
            state['armed'] = True
            state['events'].append('arm')
            return mock.Mock()
        def clear(*args):
            state['armed'] = False
            state['events'].append('disarm')
            if error:
                raise RuntimeError(error)
            return trigger
        def drip(*args):
            self.assertTrue(state['armed'])
            state['events'].append('descent')
            if motion_error:
                raise RuntimeError(motion_error)
        endstop.home_start.side_effect = arm
        endstop.home_wait.side_effect = clear
        th.drip_move = mock.Mock(side_effect=drip)
        position = types.SimpleNamespace(stepper_name='stepper_z',
            endstop_name='probe', start_pos=0, halt_pos=1, trig_pos=1,
            note_home_end=mock.Mock(), verify_no_probe_skew=mock.Mock())
        hmove = engine.homing.HomingMove(printer, [(endstop, 'probe')])
        hmove._calc_endstop_rate = mock.Mock(return_value=.001)
        hmove.calc_toolhead_pos = mock.Mock(return_value=[28., 28., .2, 0.])
        with mock.patch.object(engine.homing, 'StepperPosition',
                               return_value=position):
            try:
                hmove.homing_move([28., 28., -.3], .5, probe_pos=True)
            finally:
                self.assertEqual(state['events'], ['arm', 'descent', 'disarm'])
                self.assertFalse(state['armed'])
                th.drip_move.assert_called_once()
                endstop.home_wait.assert_called_once()

    def test_mcu_guard_is_armed_for_one_direct_descent(self):
        self.native_move()

    def test_missing_trigger_fails_instead_of_reaching_global_minimum(self):
        with self.assertRaisesRegex(RuntimeError, 'No trigger'):
            self.native_move(trigger=0.)

    def test_raw_safety_violation_is_not_replaced_by_an_estimator(self):
        with self.assertRaisesRegex(RuntimeError, 'RAW_RANGE'):
            self.native_move(error='Trigger analog error: RAW_RANGE')

    def test_motion_abort_still_waits_for_mcu_cleanup(self):
        with self.assertRaisesRegex(RuntimeError, 'Emergency stop'):
            self.native_move(motion_error='Emergency stop')


if __name__ == '__main__':
    unittest.main()
