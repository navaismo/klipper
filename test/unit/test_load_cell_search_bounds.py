# Native HX711 search bounds and endpoint rejection; no printer connection
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import unittest
from unittest import mock
import test_auto_z_offset_cr as fixtures


class SearchBoundsTests(unittest.TestCase):
    def setUp(self):
        suite = fixtures.EngineTests()
        suite.setUp()
        self.p = suite.printer
        self.move, self.collector = suite.diagnostic_move()
        self.move._z_min_position = -3.
        self.move._log_trace = False
        self.move._param_helper = mock.Mock()
        self.move._param_helper.get_probe_params.return_value = {
            'probe_speed': .5, 'load_cell_retract_dist': .5}
        self.collector.collect_min.return_value = ([(0., 0., 1000.)]*6, 0)
        self.p.toolhead.position = [28., 28., 5., 0.]
        self.stepper = mock.Mock()
        self.stepper.get_step_dist.return_value = .0025
        self.stepper.calc_position_from_coord.side_effect = lambda pos: pos[2]
        self.move._mcu_trigger_analog.get_steppers.return_value = [self.stepper]
        self.move._mcu_trigger_analog.get_last_trigger_time.return_value = 3.
        self.homing = mock.Mock()
        self.p.objects['homing'] = self.homing

    def run_move(self, result_z=None, error=None, floor=None):
        if floor is None:
            floor, distance = self.move.get_probe_bounds(5.)
        else:
            distance = 5.-floor
        def descend(trigger, target, speed):
            self.p.toolhead.position[2] = (
                target[2] if result_z is None else result_z)
            if error:
                raise RuntimeError(error)
            return self.p.toolhead.get_position()
        self.homing.probing_move.side_effect = descend
        return self.move.probing_move(fixtures.Gcmd(), minimum_z=floor,
                                      maximum_descent=distance)

    def test_search_uses_native_minimum_not_saved_offset_or_retraction(self):
        for offset in (0., .5, 1.065, 2.):
            self.p.primary.offsets = (-24., -14.5, offset)
            for retract in (.1, .5, 2.):
                self.move._param_helper.get_probe_params.return_value[
                    'load_cell_retract_dist'] = retract
                self.assertEqual(self.move.get_probe_bounds(5.), (-3., 8.))
        self.move._mcu_trigger_analog.set_raw_range.assert_not_called()

    def test_native_minimum_is_clipped_to_kinematic_limits(self):
        original = self.p.toolhead.get_status
        def status(now):
            result = original(now)
            result['axis_minimum'][2] = -2.
            return result
        self.p.toolhead.get_status = status
        self.assertEqual(self.move.get_probe_bounds(5.), (-2., 7.))

    def test_configured_minimum_can_be_stricter_than_kinematic_limit(self):
        self.move._z_min_position = -1.
        self.assertEqual(self.move.get_probe_bounds(5.), (-1., 6.))

    def test_unhomed_z_cannot_select_search_bounds(self):
        self.p.toolhead.homed = 'xy'
        with self.assertRaisesRegex(RuntimeError, 'Invalid native Z'):
            self.move.get_probe_bounds(5.)
        self.homing.probing_move.assert_not_called()

    def test_nonfinite_and_outside_start_or_minimum_reject(self):
        for value in (float('nan'), float('inf'), -3., -4., 251.):
            with self.assertRaises(RuntimeError):
                self.move.get_probe_bounds(value)
        self.move._z_min_position = float('nan')
        with self.assertRaises(RuntimeError):
            self.move.get_probe_bounds(5.)
        self.homing.probing_move.assert_not_called()

    def test_force_stop_before_endpoint_preserves_native_thresholds(self):
        position, collector = self.run_move(-.4)
        self.assertAlmostEqual(position[2], -.4)
        self.assertIs(collector, self.collector)
        self.assertAlmostEqual(self.homing.probing_move.call_args[0][1][2], -3.)
        self.assertEqual(self.homing.probing_move.call_args[0][2], .5)
        self.move._mcu_trigger_analog.set_raw_range.assert_called_once_with(
            -19000, 21000)
        self.move._mcu_trigger_analog.set_trigger.assert_called_once_with(
            'abs_ge', 75*32768)
        collector.stop_collecting()

    def test_no_trigger_or_frozen_stream_stops_at_native_endpoint(self):
        # A constant synthetic tare stream and a native no-trigger response;
        # this tests the host boundary path, not physical force-stop timing.
        with self.assertRaisesRegex(RuntimeError, 'No trigger'):
            self.run_move(error='No trigger on probe after full movement')
        self.assertEqual(self.p.toolhead.get_position()[2], -3.)
        self.assertEqual(self.homing.probing_move.call_count, 1)
        self.assertEqual(self.collector.stop_collecting.call_count, 2)

    def test_late_force_notification_at_endpoint_is_not_contact(self):
        with self.assertRaisesRegex(RuntimeError, 'trigger at travel endpoint'):
            self.run_move(-3.)
        self.assertEqual(self.homing.probing_move.call_count, 1)
        self.assertEqual(self.collector.stop_collecting.call_count, 2)

    def test_recorded_quantized_floor_positions_are_not_valid_contacts(self):
        # Real target/return pairs; the MCU response is mocked, not replayed.
        for target, returned in ((-.3733333333330713, -.3724999999994729),
                                 (-.394999999999742, -.39499999999948177)):
            self.p.toolhead.position[2] = 5.
            with self.assertRaisesRegex(RuntimeError, 'trigger at travel'):
                self.run_move(returned, floor=target)

    def test_endpoint_uncertainty_is_one_native_step_not_new_tolerance(self):
        with self.assertRaisesRegex(RuntimeError, 'trigger at travel'):
            self.run_move(-2.9975)
        self.p.toolhead.position[2] = 5.
        position, collector = self.run_move(-2.995)
        self.assertEqual(position[2], -2.995)
        collector.stop_collecting()

    def test_raw_safety_error_is_preserved_without_retry(self):
        with self.assertRaisesRegex(RuntimeError, 'RAW_RANGE'):
            self.run_move(error='Trigger analog error: RAW_RANGE')
        self.assertEqual(self.homing.probing_move.call_count, 1)
        self.assertEqual(self.collector.stop_collecting.call_count, 2)

    def test_unknown_step_resolution_cannot_authorize_contact(self):
        self.stepper.get_step_dist.return_value = 0.
        with self.assertRaisesRegex(RuntimeError, 'Invalid Z step resolution'):
            self.run_move(-.4)
        self.assertEqual(self.collector.stop_collecting.call_count, 2)


if __name__ == '__main__':
    unittest.main()
