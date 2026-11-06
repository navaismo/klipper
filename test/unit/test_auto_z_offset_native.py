# Native-only pressure-reference calibration tests; no printer connection
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import ast
import configparser
import importlib
import pathlib
import sys
import unittest
from unittest import mock
import test_auto_z_offset as fixtures

with mock.patch.dict(sys.modules, {
        fixtures.PACKAGE: fixtures.package,
        fixtures.PACKAGE + '.load_cell_probe': fixtures.lcprobe,
        fixtures.PACKAGE + '.manual_probe': fixtures.auto.manual_probe,
        fixtures.PACKAGE + '.probe': fixtures.lcprobe.probe,
        fixtures.PACKAGE + '.trigger_analog': fixtures.lcprobe.trigger_analog}):
    native = importlib.import_module(fixtures.PACKAGE + '.auto_z_offset_native')


class Config(fixtures.Config):
    def __init__(self, printer, data=None, sections=()):
        values = {'probe_point': [28., 28.], 'sample_retract_dist': .5,
                  'lift_speed': 5., 'samples_tolerance': .025}
        values.update(data or {})
        super().__init__(printer, values)
        if 'probe_points' in values and not (data or {}).get('probe_point'):
            self.data.pop('probe_point', None)
        self.sections = sections
    def get_name(self):
        return 'auto_z_offset_native'
    def has_section(self, name):
        return super().has_section(name) or name in self.sections


class NativeWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.p = fixtures.Printer()
        self.p.toolhead.get_last_move_time = mock.Mock(return_value=10.)
        self.trigger = mock.Mock()
        self.trigger.get_last_trigger_time.return_value = 3.
        self.move = mock.Mock()
        self.zs = iter([-.3, -.305, -.31])
        self.collectors = []
        self.move.probing_move.side_effect = self.press
        self.factories = {}
        for owner, name, result in [
                (native.trigger_analog, 'MCU_trigger_analog', self.trigger),
                (native.trigger_analog, 'MCU_SosFilter', mock.Mock()),
                (native.load_cell_probe, 'LoadCellProbingMove', self.move),
                (native.load_cell_probe, 'TappingMove', mock.Mock()),
                (native.load_cell_probe, 'LCBestFit', mock.Mock())]:
            patch = mock.patch.object(owner, name, return_value=result)
            self.factories[name] = patch.start()
            self.addCleanup(patch.stop)
        self.module = native.NativeAutoZOffset(Config(self.p))
        self.module._connect()
    def press(self, gcmd, minimum_z=None, maximum_descent=None):
        z = next(self.zs)
        self.p.trace.append(('pressure', minimum_z, maximum_descent,
                             self.p.toolhead.get_position()))
        self.p.toolhead.position[2] = z
        collector = mock.Mock()
        collector.collect_until.return_value = (
            [(2., 0., 1000., 1000.), (3., 75., 8500., 1000.)], 0)
        self.collectors.append(collector)
        return self.p.toolhead.get_position(), collector
    def run_command(self, params=None):
        cmd = fixtures.Gcmd('AUTO_Z_OFFSET', params)
        self.module.cmd_AUTO_Z_OFFSET(cmd)
        return self.module.result

    def test_constructs_only_native_raw_probe_with_existing_sensor(self):
        self.factories['MCU_trigger_analog'].assert_called_once_with(
            self.p.cell.sensor)
        self.assertIs(self.factories['LoadCellProbingMove'].call_args[0][1],
                      self.p.cell)
        self.assertTrue(self.factories['LoadCellProbingMove'].call_args[1][
            'log_trace'])
        self.assertIs(self.p.objects['probe'], self.p.primary)
        self.factories['TappingMove'].assert_not_called()
        self.factories['LCBestFit'].assert_not_called()
        self.p.objects['pins'].register_chip.assert_not_called()
        self.assertEqual(set(self.p.gcode.commands), {'AUTO_Z_OFFSET'})

    def test_other_backend_rejected_before_mcu_or_sensor_initialization(self):
        for section in ('auto_z_offset', 'load_cell_probe', 'prtouch'):
            before = self.factories['MCU_trigger_analog'].call_count
            with self.assertRaisesRegex(RuntimeError, 'not both'):
                native.NativeAutoZOffset(Config(self.p, sections=(section,)))
            self.assertEqual(self.factories['MCU_trigger_analog'].call_count,
                             before)

    def test_marlin_physical_sequence_uses_absolute_native_coordinates(self):
        result = self.run_command()
        touch = [t for t in self.p.trace if t[0] == 'crtouch']
        self.assertEqual(touch[0][1][:3], [52., 42.5, 5.])
        presses = [t for t in self.p.trace if t[0] == 'pressure']
        self.assertEqual(len(presses), 3)
        for event in presses:
            self.assertAlmostEqual(event[1], -.4)
            self.assertAlmostEqual(event[2], 5.4)
            self.assertEqual(event[3][:3], [28., 28., 5.])
        self.assertAlmostEqual(result['pressure_z'], -.305)
        self.assertAlmostEqual(result['provisional_z_offset'], 1.470)
        self.assertEqual(result['applied_adjustment_mm'], 0.)
        self.assertFalse(result['eligible_for_application'])
        self.assertFalse(result['saved'])
        self.assertEqual(self.p.primary.offsets[2], 1.065)
        self.p.objects['configfile'].set.assert_not_called()
        for c in self.collectors:
            c.stop_collecting.assert_called_once()
        self.p.primary.sessions[0].end_probe_session.assert_called_once()

    def test_common_z_frame_translation_cancels_in_offset(self):
        self.p.primary.height += 3.
        self.zs = iter([2.7, 2.695, 2.69])
        result = self.run_command()
        self.assertAlmostEqual(result['provisional_z_offset'], 1.470)

    def test_saved_offset_is_not_added_to_measured_difference(self):
        self.p.primary.offsets = (-24., -14.5, .9)
        self.p.primary.height = 1.2
        self.zs = iter([.2, .2, .2])
        result = self.run_command()
        self.assertAlmostEqual(result['provisional_z_offset'], 1.)
        self.assertNotAlmostEqual(result['provisional_z_offset'], 1.9)

    def test_mesh_is_cleared_and_not_part_of_offset(self):
        mesh = mock.Mock()
        mesh.get_mesh.return_value = object()
        mesh.set_mesh.side_effect = lambda value: setattr(
            mesh.get_mesh, 'return_value', value)
        self.module.mesh = mesh
        result = self.run_command()
        self.assertIsNone(mesh.get_mesh())
        self.assertEqual(mesh.set_mesh.call_count, 2)
        self.assertAlmostEqual(result['provisional_z_offset'], 1.470)

    def test_new_mesh_during_measurement_aborts_without_result(self):
        mesh = mock.Mock()
        mesh.get_mesh.return_value = None
        self.module.mesh = mesh
        original_press = self.move.probing_move.side_effect
        def changed(*args, **kwargs):
            value = original_press(*args, **kwargs)
            mesh.get_mesh.return_value = object()
            return value
        self.move.probing_move.side_effect = changed
        with self.assertRaisesRegex(RuntimeError, 'mesh became active'):
            self.run_command()
        self.assertIsNone(self.module.result)
        self.assertEqual(self.move.probing_move.call_count, 1)

    def test_missing_native_trigger_does_not_start_next_press_or_retract(self):
        def failed(*args, **kwargs):
            self.p.toolhead.position[2] = -.4
            self.p.trace.append(('at_floor',))
            raise RuntimeError('No trigger on probe after full movement')
        self.move.probing_move.side_effect = failed
        with self.assertRaisesRegex(RuntimeError, 'No trigger'):
            self.run_command()
        self.assertEqual(self.move.probing_move.call_count, 1)
        self.assertEqual(self.p.toolhead.get_position()[2], -.4)
        self.assertIsNone(self.module.result)
        self.assertFalse(self.module.busy)
        self.assertEqual(self.p.bed.target, 0.)
        self.assertEqual(self.p.nozzle.target, 0.)

    def test_reported_cr_values_keep_endpoint_and_reject_absent_trigger(self):
        # Recorded CR Touch readings; no fabricated contact/force measurement.
        bed_z = sum((.145, .1475, .145))/3.
        self.p.primary.height = 1.065 + bed_z
        def no_trigger(gcmd, minimum_z=None, maximum_descent=None):
            self.assertAlmostEqual(minimum_z, -.3541666666666667)
            self.assertAlmostEqual(maximum_descent, 5.354166666666667)
            # Simulate native termination at the endpoint, not a hardware run.
            self.p.toolhead.position[2] = minimum_z
            raise RuntimeError('No trigger on probe after full movement')
        self.move.probing_move.side_effect = no_trigger
        with self.assertRaisesRegex(RuntimeError, 'No trigger'):
            self.run_command()
        self.assertIsNone(self.module.result)
        self.assertEqual(self.move.probing_move.call_count, 1)
        self.assertAlmostEqual(self.p.toolhead.get_position()[2],
                               -.3541666666666667)
        self.assertEqual(self.p.primary.offsets[2], 1.065)
        self.p.objects['configfile'].set.assert_not_called()

    def test_sensor_raw_safety_error_is_not_treated_as_contact(self):
        self.move.probing_move.side_effect = RuntimeError('RAW_RANGE')
        with self.assertRaisesRegex(RuntimeError, 'RAW_RANGE'):
            self.run_command()
        self.assertIsNone(self.module.result)
        self.assertEqual(self.move.probing_move.call_count, 1)

    def test_collector_errors_clear_provisional_result(self):
        original_press = self.move.probing_move.side_effect
        def failed(*args, **kwargs):
            position, collector = original_press(*args, **kwargs)
            collector.collect_until.return_value = ([], (1, 0))
            return position, collector
        self.move.probing_move.side_effect = failed
        with self.assertRaisesRegex(RuntimeError, 'sensor reported errors'):
            self.run_command()
        self.collectors[0].stop_collecting.assert_called_once()
        self.assertIsNone(self.module.result)

    def test_no_positive_mcu_timestamp_is_not_contact(self):
        self.trigger.get_last_trigger_time.return_value = 0.
        with self.assertRaisesRegex(RuntimeError, 'MCU pressure trigger'):
            self.run_command()
        self.assertIsNone(self.module.result)
        self.assertEqual(self.p.toolhead.get_position()[2], -.3)

    def test_native_repeatability_failure_is_not_averaged_into_result(self):
        self.zs = iter([-.3, -.15, -.31])
        with self.assertRaisesRegex(RuntimeError, 'samples_tolerance'):
            self.run_command()
        self.assertIsNone(self.module.result)
        self.assertEqual(self.move.probing_move.call_count, 3)

    def test_primary_probe_session_closed_on_error(self):
        self.p.primary.fail = True
        with self.assertRaisesRegex(RuntimeError, 'CR Touch error'):
            self.run_command()
        self.p.primary.sessions[0].end_probe_session.assert_called_once()
        self.move.probing_move.assert_not_called()

    def test_floor_cannot_fall_back_to_global_minimum(self):
        self.p.primary.height = -2.
        with self.assertRaisesRegex(RuntimeError, 'outside native Z range'):
            self.run_command()
        self.move.probing_move.assert_not_called()

    def test_offset_change_during_measurement_invalidates_session(self):
        original_press = self.move.probing_move.side_effect
        def changed(*args, **kwargs):
            value = original_press(*args, **kwargs)
            self.p.primary.offsets = (-24., -14.5, 2.)
            return value
        self.move.probing_move.side_effect = changed
        with self.assertRaisesRegex(RuntimeError, 'reference changed'):
            self.run_command()
        self.assertIsNone(self.module.result)

    def test_no_apply_accept_or_save_parameters(self):
        for key in ('APPLY', 'ACCEPT', 'SAVE_CONFIG', 'TRIGGER_FORCE'):
            with self.assertRaisesRegex(RuntimeError, 'Unsupported'):
                self.run_command({key: 1})
        self.assertEqual(self.p.trace, [])

    def test_native_homing_only_when_needed(self):
        self.p.toolhead.homed = ''
        self.run_command()
        self.p.gcode.run_script_from_command.assert_called_once_with('G28')

    def test_calibrated_printer_does_not_rehome_or_change_origin(self):
        self.run_command()
        self.p.gcode.run_script_from_command.assert_not_called()
        self.assertFalse(any(t[0] == 'set_position' for t in self.p.trace))

    def test_hot_targets_skip_native_pid_settling_wait(self):
        self.run_command({'BED_TEMP': 60., 'NOZZLE_TEMP': 170.})
        calls = self.p.objects['heaters'].set_temperature.call_args_list
        self.assertEqual([c[0][1:] for c in calls],
                         [(60., False), (170., False)])

    def test_warmup_starts_at_lower_band_edge_without_settling(self):
        for heater, measured in ((self.p.bed, 57.), (self.p.nozzle, 167.)):
            heater.autoheat = False
            heater.temperature = measured
        self.run_command()
        self.p.reactor.pause.assert_not_called()
        self.assertEqual(self.move.probing_move.call_count, 3)

    def test_warmup_starts_at_upper_band_edge_without_settling(self):
        for heater, measured in ((self.p.bed, 63.), (self.p.nozzle, 173.)):
            heater.autoheat = False
            heater.temperature = measured
        self.run_command()
        self.p.reactor.pause.assert_not_called()
        self.assertEqual(self.move.probing_move.call_count, 3)

    def test_warmup_requires_both_heaters_in_band_at_same_check(self):
        self.p.bed.autoheat = self.p.nozzle.autoheat = False
        self.p.bed.temperature, self.p.nozzle.temperature = 57., 166.
        readings = iter([(64., 167.), (63., 173.)])
        def advance(until):
            self.assertFalse(any(e[0] in ('move', 'crtouch', 'pressure')
                                 for e in self.p.trace))
            self.p.bed.temperature, self.p.nozzle.temperature = next(readings)
            return self.p.pause(until)
        self.p.reactor.pause.side_effect = advance
        self.run_command()
        self.assertEqual(self.p.reactor.pause.call_count, 2)
        self.assertEqual(self.p.now, .5)

    def test_changed_target_during_warmup_aborts_before_motion(self):
        self.p.nozzle.autoheat = False
        def change(until):
            self.p.nozzle.target = 0.
            return self.p.pause(until)
        self.p.reactor.pause.side_effect = change
        with self.assertRaisesRegex(RuntimeError, 'heater state changed'):
            self.run_command()
        self.move.probing_move.assert_not_called()
        self.assertFalse(self.p.primary.sessions)
        self.assertIsNone(self.module.result)

    def test_invalid_sensor_during_warmup_aborts_before_motion(self):
        self.p.nozzle.autoheat = False
        def change(until):
            self.p.nozzle.temperature = float('nan')
            return self.p.pause(until)
        self.p.reactor.pause.side_effect = change
        with self.assertRaisesRegex(RuntimeError, 'heater state changed'):
            self.run_command()
        self.move.probing_move.assert_not_called()
        self.assertFalse(self.p.primary.sessions)
        self.assertIsNone(self.module.result)

    def test_shutdown_during_warmup_cannot_continue_into_homing(self):
        self.p.nozzle.autoheat = False
        def change(until):
            self.p.shutdown = True
            return self.p.pause(until)
        self.p.reactor.pause.side_effect = change
        with self.assertRaisesRegex(RuntimeError, 'not connected'):
            self.run_command()
        self.p.gcode.run_script_from_command.assert_not_called()
        self.move.probing_move.assert_not_called()
        self.assertFalse(self.p.primary.sessions)
        self.assertEqual(
            self.p.objects['heaters'].set_temperature.call_count, 2)

    def thermal_context(self):
        return ([(self.p.bed, 60.), (self.p.nozzle, 170.)],
                (self.p.primary.get_offsets(),
                 self.p.cell.get_counts_per_gram(),
                 self.p.cell.get_reference_tare_counts()))

    def test_default_temperature_margin_includes_both_boundaries(self):
        self.assertEqual(self.module.temp_tolerance, 3.)
        targets, calibration = self.thermal_context()
        for heater, target in targets:
            heater.target = target
        for delta in (-3., 3.):
            for heater, target in targets:
                heater.temperature = target + delta
            self.module._check_context(targets, calibration)

    def test_each_heater_outside_temperature_margin_is_rejected(self):
        targets, calibration = self.thermal_context()
        for selected, unused in targets:
            for delta in (-3.01, 3.01):
                for heater, target in targets:
                    heater.target = target
                    heater.temperature = target
                selected.temperature += delta
                with self.assertRaisesRegex(RuntimeError, 'out of range'):
                    self.module._check_context(targets, calibration)

    def test_configured_temperature_margin_is_used(self):
        module = native.NativeAutoZOffset(Config(
            fixtures.Printer(), {'temperature_tolerance': 5.}))
        module._connect()
        targets = [(module.bed, 60.), (module.nozzle, 170.)]
        calibration = (module.primary.get_offsets(),
                       module.cell.get_counts_per_gram(),
                       module.cell.get_reference_tare_counts())
        for heater, target in targets:
            heater.target = target
            heater.temperature = target + 4.
        module._check_context(targets, calibration)
        self.assertEqual(module.temp_tolerance, 5.)

    def test_temperature_margin_requires_positive_finite_value(self):
        for value in (0., -1., float('nan'), float('inf')):
            with self.assertRaises(RuntimeError):
                native.NativeAutoZOffset(Config(
                    fixtures.Printer(), {'temperature_tolerance': value}))

    def test_margin_never_accepts_invalid_sensor_or_changed_target(self):
        targets, calibration = self.thermal_context()
        for value in (float('nan'), float('inf'), -1., 300.):
            for heater, target in targets:
                heater.target = target
                heater.temperature = target
            self.p.bed.temperature = value
            with self.assertRaisesRegex(RuntimeError, 'heater state changed'):
                self.module._check_context(targets, calibration)
        self.p.bed.temperature = 60.
        self.p.bed.target = 59.
        with self.assertRaisesRegex(RuntimeError, 'heater state changed'):
            self.module._check_context(targets, calibration)

    def test_out_of_band_after_press_stops_next_measurement(self):
        original = self.move.probing_move.side_effect
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            self.p.nozzle.temperature = 174.
            return result
        self.move.probing_move.side_effect = changed
        with self.assertRaisesRegex(RuntimeError, 'out of range'):
            self.run_command()
        self.assertEqual(self.move.probing_move.call_count, 1)
        self.assertIsNone(self.module.result)
        self.collectors[0].stop_collecting.assert_called_once()
        self.assertEqual(self.p.bed.target, 0.)
        self.assertEqual(self.p.nozzle.target, 0.)

    def test_active_job_rejected_without_heat_or_motion(self):
        self.p.stats.get_status.return_value = {'state': 'printing'}
        with self.assertRaisesRegex(RuntimeError, 'idle job'):
            self.run_command()
        self.assertEqual(self.p.trace, [])

    def test_printer_shutdown_does_not_restore_heaters_or_retry(self):
        def stopped(*args, **kwargs):
            self.p.shutdown = True
            raise RuntimeError('MCU shutdown')
        self.move.probing_move.side_effect = stopped
        with self.assertRaisesRegex(RuntimeError, 'MCU shutdown'):
            self.run_command()
        self.assertEqual(
            self.p.objects['heaters'].set_temperature.call_count, 2)
        self.assertIsNone(self.module.result)

    def test_heater_cleanup_cannot_replace_native_safety_error(self):
        self.move.probing_move.side_effect = RuntimeError('RAW_RANGE')
        heaters = self.p.objects['heaters']
        original = heaters.set_temperature.side_effect
        def set_temperature(heater, target, wait=False):
            if target == 0.:
                raise RuntimeError('cleanup unavailable')
            return original(heater, target, wait)
        heaters.set_temperature.side_effect = set_temperature
        with self.assertLogs(level='ERROR'):
            with self.assertRaisesRegex(RuntimeError, 'RAW_RANGE'):
                self.run_command()
        self.assertIsNone(self.module.result)

    def test_configuration_cannot_raise_existing_force_ceiling(self):
        for data in ({'trigger_force': 76.}, {'force_safety_limit': 201.}):
            before = self.factories['MCU_trigger_analog'].call_count
            with self.assertRaises(RuntimeError):
                native.NativeAutoZOffset(Config(self.p, data))
            self.assertEqual(self.factories['MCU_trigger_analog'].call_count,
                             before)

    def test_failed_followup_discards_previous_provisional_report(self):
        self.run_command()
        self.assertIsNotNone(self.module.result)
        self.move.probing_move.side_effect = RuntimeError('No trigger')
        with self.assertRaisesRegex(RuntimeError, 'No trigger'):
            self.run_command()
        self.assertIsNone(self.module.result)
        self.assertFalse(self.module.get_status(0.)[
            'eligible_for_application'])

    def test_raw_reference_and_scale_are_not_recalibrated(self):
        initial = (self.p.cell.get_counts_per_gram(),
                   self.p.cell.get_reference_tare_counts())
        self.run_command()
        self.assertEqual(initial, (self.p.cell.get_counts_per_gram(),
                         self.p.cell.get_reference_tare_counts()))

    def test_point_reference_is_captured_once_at_heated_nozzle_clearance(self):
        def reference():
            self.assertEqual(self.p.toolhead.get_position()[:3],
                             [28., 28., 5.])
            self.assertEqual((self.p.bed.temperature,
                              self.p.nozzle.temperature), (60., 170.))
            self.assertTrue(any(e[0] == 'crtouch' for e in self.p.trace))
            self.move.probing_move.assert_not_called()
            return 39000.
        with mock.patch.object(self.p.cell, 'avg_counts',
                               side_effect=reference) as capture:
            result = self.run_command()
        capture.assert_called_once_with()
        self.move.set_safety_reference.assert_called_once_with(39000.)
        self.assertEqual(self.move.probing_move.call_count, 3)
        self.assertEqual(self.move.clear_safety_reference.call_count, 2)
        self.assertEqual(result['safety_reference_counts'], 39000)
        self.assertEqual(self.p.cell.reference, 1000)
        self.assertEqual(self.p.cell.counts_per_gram, 100.)
        self.assertEqual(self.p.primary.offsets[2], 1.065)
        self.p.objects['configfile'].set.assert_not_called()

    def test_reference_acquisition_error_prevents_every_pressure_descent(self):
        with mock.patch.object(self.p.cell, 'avg_counts',
                               side_effect=RuntimeError('ADC reference error')):
            with self.assertRaisesRegex(RuntimeError, 'ADC reference error'):
                self.run_command()
        self.move.set_safety_reference.assert_not_called()
        self.move.probing_move.assert_not_called()
        self.assertEqual(self.move.clear_safety_reference.call_count, 2)
        self.assertIsNone(self.module.result)

    def test_invalid_reference_rejects_without_pressure_or_candidate(self):
        self.move.set_safety_reference.side_effect = RuntimeError(
            'Invalid load cell point tare')
        with self.assertRaisesRegex(RuntimeError, 'Invalid load cell point'):
            self.run_command()
        self.move.probing_move.assert_not_called()
        self.assertEqual(self.move.clear_safety_reference.call_count, 2)
        self.assertIsNone(self.module.result)

    def test_failed_contact_clears_reference_without_recentering_or_retry(self):
        self.move.probing_move.side_effect = RuntimeError(
            'Load cell tare outside force limit')
        with mock.patch.object(self.p.cell, 'avg_counts',
                               return_value=39000.) as capture:
            with self.assertRaisesRegex(RuntimeError, 'tare outside'):
                self.run_command()
        capture.assert_called_once_with()
        self.move.set_safety_reference.assert_called_once_with(39000.)
        self.assertEqual(self.move.clear_safety_reference.call_count, 2)
        self.assertEqual(self.move.probing_move.call_count, 1)
        self.assertIsNone(self.module.result)

    def test_disconnect_discards_point_reference_and_provisional_result(self):
        self.run_command()
        self.move.clear_safety_reference.reset_mock()
        self.module._disconnect()
        self.move.clear_safety_reference.assert_called_once_with()
        self.assertIsNone(self.module.result)
        self.assertFalse(self.module.connected)

    def configure_three_points(self, points=None):
        points = points or [(28., 28.), (110., 110.), (180., 28.)]
        self.p.gcode.commands.pop('AUTO_Z_OFFSET')
        self.module = native.NativeAutoZOffset(Config(
            self.p, {'probe_points': points}))
        self.module._connect()
        heights = iter([1.165, 1.015, 1.19])
        original = self.p.primary.start_probe_session
        def start(cmd):
            self.p.primary.height = next(heights)
            return original(cmd)
        self.p.primary.start_probe_session = start
        self.zs = iter([-.3, -.305, -.31, -.45, -.455, -.46,
                        -.275, -.28, -.285])

    def test_three_points_pair_sensor_and_nozzle_at_same_physical_xy(self):
        self.configure_three_points()
        result = self.run_command()
        touches = [e[1][:2] for e in self.p.trace if e[0] == 'crtouch']
        self.assertEqual(touches, [[52., 42.5], [134., 124.5], [204., 42.5]])
        pressures = [e for e in self.p.trace if e[0] == 'pressure']
        self.assertEqual(len(pressures), 9)
        for index, point in enumerate(self.module.points):
            for e in pressures[3*index:3*index+3]:
                self.assertEqual(e[3][:3], list(point)+[5.])
                floor = [1.165, 1.015, 1.19][index] - 1.065 - .5
                self.assertAlmostEqual(e[1], floor)
                self.assertAlmostEqual(e[2], 5.-floor)
            self.assertAlmostEqual(result['points'][index][
                'provisional_z_offset'], 1.47)
            self.assertEqual(result['points'][index]['point_index'], index+1)
            session = self.p.primary.sessions[index]
            session.end_probe_session.assert_called_once()
        self.assertEqual(result['reference_point'], 1)
        self.assertAlmostEqual(result['spatial_range'], 0.)
        self.assertFalse(result['eligible_for_application'])
        self.p.objects['configfile'].set.assert_not_called()

    def test_each_point_has_a_fresh_fixed_local_reference(self):
        self.configure_three_points()
        values = iter([39000., 35000., 41000.])
        def reference():
            index = self.move.probing_move.call_count // 3
            self.assertEqual(self.p.toolhead.get_position()[:3],
                             list(self.module.points[index])+[5.])
            return next(values)
        with mock.patch.object(self.p.cell, 'avg_counts',
                               side_effect=reference) as capture:
            result = self.run_command()
        self.assertEqual(capture.call_count, 3)
        self.assertEqual(self.move.set_safety_reference.call_args_list,
                         [mock.call(39000.), mock.call(35000.),
                          mock.call(41000.)])
        references = [r['safety_reference_counts'] for r in result['points']]
        self.assertEqual(references, [39000, 35000, 41000])
        self.assertEqual(self.p.cell.reference, 1000)
        self.assertEqual(self.p.cell.counts_per_gram, 100.)
        self.assertEqual(self.move.clear_safety_reference.call_count, 2)

    def test_spatial_differences_are_reported_without_averaging(self):
        self.configure_three_points()
        self.zs = iter([-.3, -.305, -.31, -.45, -.455, -.46,
                        -.175, -.18, -.185])
        result = self.run_command()
        self.assertAlmostEqual(result['provisional_z_offset'], 1.47)
        self.assertAlmostEqual(
            result['points'][2]['provisional_z_offset'], 1.37)
        self.assertAlmostEqual(result['spatial_range'], .1)
        self.assertFalse(result['eligible_for_application'])
        self.assertTrue(all(not p['eligible_for_application']
                            for p in result['points']))

    def test_no_trigger_at_second_point_stops_before_third_point(self):
        self.configure_three_points()
        original = self.move.probing_move.side_effect
        def press(*args, **kwargs):
            if self.move.probing_move.call_count == 4:
                raise RuntimeError('No trigger on probe after full movement')
            return original(*args, **kwargs)
        self.move.probing_move.side_effect = press
        with self.assertRaisesRegex(RuntimeError, 'No trigger'):
            self.run_command()
        self.assertEqual(len(self.p.primary.sessions), 2)
        self.assertEqual(self.move.probing_move.call_count, 4)
        self.assertIsNone(self.module.result)
        self.assertFalse(self.module.busy)
        self.assertEqual(self.move.clear_safety_reference.call_count, 2)
        self.p.objects['configfile'].set.assert_not_called()

    def test_second_point_repeatability_failure_invalidates_run(self):
        self.configure_three_points()
        self.zs = iter([-.3, -.305, -.31, -.45, -.4, -.46])
        with self.assertRaisesRegex(RuntimeError, 'samples_tolerance'):
            self.run_command()
        self.assertEqual(len(self.p.primary.sessions), 2)
        self.assertEqual(self.move.probing_move.call_count, 6)
        self.assertIsNone(self.module.result)

    def test_all_points_checked_for_sensor_reach_before_heat_or_motion(self):
        self.configure_three_points([(28., 28.), (110., 110.), (220., 28.)])
        with self.assertRaisesRegex(RuntimeError, 'point 3 out of range'):
            self.run_command()
        self.assertEqual(self.p.trace, [])
        self.move.probing_move.assert_not_called()

    def test_all_points_checked_for_nozzle_reach_before_heat_or_motion(self):
        self.configure_three_points([(28., 28.), (110., 110.), (-8., 28.)])
        with self.assertRaisesRegex(RuntimeError, 'point 3 out of range'):
            self.run_command()
        self.assertEqual(self.p.trace, [])

    def test_xy_moves_between_points_remain_at_clearance(self):
        self.configure_three_points()
        self.run_command()
        xy_moves = [e[1] for e in self.p.trace
                    if e[0] == 'move' and e[1][0] is not None]
        self.assertEqual(len(xy_moves), 6)
        self.assertTrue(all(pos[2] >= self.module.clearance
                            for pos in xy_moves))

    def test_bad_point_lists_rejected_before_trigger_creation(self):
        data = [[], [(28., 28.)], [(28., 28.), (110., 110.)],
                [(28., 28.), (110., 110.), (28., 28.)],
                [(28., 28.), (110., 110.), (float('nan'), 28.)],
                [(28., 28.), (110., 110.), (180., float('inf'))],
                [(28., 28.), (110., 110.), (180.,)]]
        for points in data:
            count = self.factories['MCU_trigger_analog'].call_count
            with self.assertRaises(RuntimeError):
                native.NativeAutoZOffset(Config(
                    self.p, {'probe_points': points}))
            self.assertEqual(self.factories['MCU_trigger_analog'].call_count,
                             count)

    def test_single_and_multi_point_options_are_mutually_exclusive(self):
        data = {'probe_point': [28., 28.], 'probe_points':
                [(28., 28.), (110., 110.), (180., 28.)]}
        with self.assertRaisesRegex(RuntimeError, 'not both'):
            native.NativeAutoZOffset(Config(self.p, data))

    def test_multiline_points_accept_more_than_three_locations(self):
        self.p.gcode.commands.pop('AUTO_Z_OFFSET')
        module = native.NativeAutoZOffset(Config(self.p, {'probe_points':
            '28, 28\n110, 110\n180, 28\n28, 180'}))
        self.assertEqual(module.points, ((28., 28.), (110., 110.),
                                         (180., 28.), (28., 180.)))

    def test_legacy_single_point_result_fields_are_preserved(self):
        result = self.run_command()
        self.assertEqual(result['point'], [28., 28.])
        self.assertEqual(len(result['points']), 1)
        self.assertEqual(result['points'][0]['point'], [28., 28.])
        self.assertEqual(result['spatial_range'], 0.)
        self.assertEqual(result['reference_point'], 1)

    def attach_lcd(self):
        lcd = mock.Mock()
        lcd.calibration_active = False
        acquisition = {'active': False}
        def begin(text, **targets):
            lcd.calibration_active = True
            self.p.trace.append(('lcd_begin', text, targets))
            return True
        def update(phase, text):
            self.assertTrue(lcd.calibration_active)
            self.assertFalse(acquisition['active'])
            self.p.trace.append(('lcd_phase', phase, text))
        def end(text, success):
            lcd.calibration_active = False
            self.p.trace.append(('lcd_end', text, success))
        lcd.begin_calibration.side_effect = begin
        lcd.update_calibration.side_effect = update
        lcd.end_calibration.side_effect = end
        self.p.objects['tjc_lcd'] = lcd
        original = self.move.probing_move.side_effect
        def press(*args, **kwargs):
            acquisition['active'] = True
            position, collector = original(*args, **kwargs)
            collector.stop_collecting.side_effect = lambda: acquisition.update(
                active=False)
            return position, collector
        self.move.probing_move.side_effect = press
        return lcd

    def test_native_lcd_uses_existing_views_outside_acquisition(self):
        lcd = self.attach_lcd()
        result = self.run_command()
        lcd.begin_calibration.assert_called_once_with(
            'Warming up', nozzle_target=170., bed_target=60.)
        phases = [e[1] for e in self.p.trace if e[0] == 'lcd_phase']
        self.assertEqual(phases, ['cr_touch', 'nozzle', 'nozzle', 'nozzle',
                                  'nozzle', 'computing'])
        names = [e[0] for e in self.p.trace]
        self.assertLess(names.index('lcd_begin'), names.index('heat'))
        for index, event in enumerate(self.p.trace):
            if event[0] == 'crtouch':
                self.assertEqual(self.p.trace[index-1],
                                 ('lcd_phase', 'cr_touch', 'P1 CR Touch'))
            if event[0] == 'pressure':
                self.assertEqual(self.p.trace[index-1][0:2],
                                 ('lcd_phase', 'nozzle'))
        lcd.end_calibration.assert_called_once_with(
            'P1 1.470mm - review', True)
        self.assertFalse(lcd.calibration_active)
        self.assertFalse(result['eligible_for_application'])
        self.assertIsNone(self.module._lcd)

    def test_native_lcd_shows_point_and_sample_for_three_points(self):
        self.configure_three_points()
        lcd = self.attach_lcd()
        self.run_command()
        calls = lcd.update_calibration.call_args_list
        self.assertEqual(len(calls), 18)
        for point in (1, 2, 3):
            lcd.update_calibration.assert_any_call(
                'cr_touch', 'P%d CR Touch' % point)
            for sample in (1, 2, 3):
                lcd.update_calibration.assert_any_call(
                    'nozzle', 'P%d PRESS %d/3' % (point, sample))
            lcd.update_calibration.assert_any_call(
                'computing', 'P%d computing offset' % point)
        lcd.begin_calibration.assert_called_once()
        lcd.end_calibration.assert_called_once()
        self.assertFalse(lcd.calibration_active)

    def test_native_lcd_failure_view_preserves_missing_trigger_error(self):
        lcd = self.attach_lcd()
        self.move.probing_move.side_effect = RuntimeError(
            'No trigger on probe after full movement')
        with self.assertRaisesRegex(RuntimeError, 'No trigger'):
            self.run_command()
        lcd.end_calibration.assert_called_once_with(
            'Failed: P1 PRESS 1/3', False)
        self.assertFalse(lcd.calibration_active)
        self.assertIsNone(self.module.result)
        self.assertEqual(self.move.probing_move.call_count, 1)

    def test_native_lcd_unavailable_firmware_does_not_change_result(self):
        lcd = self.attach_lcd()
        lcd.begin_calibration.side_effect = None
        lcd.begin_calibration.return_value = False
        result = self.run_command()
        lcd.update_calibration.assert_not_called()
        lcd.end_calibration.assert_not_called()
        self.assertAlmostEqual(result['provisional_z_offset'], 1.47)

    def test_native_lcd_start_error_does_not_change_pressure_result(self):
        lcd = self.attach_lcd()
        lcd.begin_calibration.side_effect = RuntimeError('LCD start timeout')
        with self.assertLogs(level='ERROR'):
            result = self.run_command()
        lcd.update_calibration.assert_not_called()
        lcd.end_calibration.assert_not_called()
        self.assertEqual(self.move.probing_move.call_count, 3)
        self.assertAlmostEqual(result['provisional_z_offset'], 1.47)

    def test_native_lcd_update_error_suppresses_retries_and_releases(self):
        lcd = self.attach_lcd()
        lcd.update_calibration.side_effect = RuntimeError('LCD update timeout')
        with self.assertLogs(level='ERROR'):
            result = self.run_command()
        self.assertEqual(lcd.update_calibration.call_count, 1)
        lcd.end_calibration.assert_called_once_with(
            'P1 1.470mm - review', True)
        self.assertFalse(lcd.calibration_active)
        self.assertAlmostEqual(result['provisional_z_offset'], 1.47)
        self.assertFalse(result['eligible_for_application'])

    def test_native_lcd_cleanup_error_cannot_mask_raw_safety_error(self):
        lcd = self.attach_lcd()
        self.move.probing_move.side_effect = RuntimeError('RAW_RANGE')
        def finish(text, success):
            lcd.calibration_active = False
            raise RuntimeError('LCD finish timeout')
        lcd.end_calibration.side_effect = finish
        with self.assertLogs(level='ERROR'):
            with self.assertRaisesRegex(RuntimeError, 'RAW_RANGE'):
                self.run_command()
        self.assertEqual(self.module.error, 'RAW_RANGE')
        self.assertFalse(lcd.calibration_active)
        self.assertIsNone(self.module.result)
        self.assertFalse(self.module.busy)

    def test_native_lcd_finish_error_keeps_pressure_report_unchanged(self):
        lcd = self.attach_lcd()
        def finish(text, success):
            lcd.calibration_active = False
            raise RuntimeError('LCD finish timeout')
        lcd.end_calibration.side_effect = finish
        with self.assertLogs(level='ERROR'):
            result = self.run_command()
        self.assertAlmostEqual(result['provisional_z_offset'], 1.47)
        self.assertIsNone(self.module.error)
        self.assertIsNone(self.module._lcd)
        self.assertFalse(self.module.busy)
        self.assertFalse(lcd.calibration_active)

    def test_native_lcd_never_draws_for_active_print_or_failed_preflight(self):
        lcd = self.attach_lcd()
        self.p.stats.get_status.return_value = {'state': 'printing'}
        with self.assertRaisesRegex(RuntimeError, 'idle job'):
            self.run_command()
        lcd.begin_calibration.assert_not_called()
        lcd.update_calibration.assert_not_called()
        self.assertEqual(self.p.trace, [])

    def test_native_lcd_render_cannot_skip_post_render_temperature_check(self):
        lcd = self.attach_lcd()
        def update(phase, text):
            self.p.nozzle.temperature = 174.
        lcd.update_calibration.side_effect = update
        with self.assertRaisesRegex(RuntimeError, 'temperature out of range'):
            self.run_command()
        self.assertFalse(self.p.primary.sessions)
        self.move.probing_move.assert_not_called()
        lcd.end_calibration.assert_called_once_with(
            'Failed: P1 CR Touch', False)
        self.assertFalse(lcd.calibration_active)

    def test_native_probe_helpers_do_not_initialize_old_estimators(self):
        source = pathlib.Path(native.__file__).read_text()
        tree = ast.parse(source)
        names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        self.assertNotIn('TappingMove', names)
        self.assertNotIn('LCBestFit', names)
        self.assertNotIn('set_calibration', names)
        self.assertNotIn('set_position', names)


class NativeMotionPrimitiveTests(unittest.TestCase):
    def test_native_homing_receives_finite_endpoint_for_frozen_samples(self):
        p = fixtures.Printer()
        move = native.load_cell_probe.LoadCellProbingMove.__new__(
            native.load_cell_probe.LoadCellProbingMove)
        move._printer, move._load_cell = p, p.cell
        move._z_min_position, move._log_trace = -3., False
        move._pause_and_tare = mock.Mock()
        move._param_helper = mock.Mock()
        move._param_helper.get_probe_params.return_value = {'probe_speed': .5}
        move._mcu_trigger_analog = object()
        collector = mock.Mock()
        move._start_collector = mock.Mock(return_value=collector)
        homing = mock.Mock()
        homing.probing_move.side_effect = RuntimeError('No trigger')
        p.objects['homing'] = homing
        p.toolhead.position = [28., 28., 5., 0.]
        with self.assertRaisesRegex(RuntimeError, 'No trigger'):
            move.probing_move(fixtures.Gcmd(), minimum_z=-.4,
                              maximum_descent=5.4)
        self.assertAlmostEqual(homing.probing_move.call_args[0][1][2], -.4)
        self.assertIs(homing.probing_move.call_args[0][0],
                      move._mcu_trigger_analog)
        collector.stop_collecting.assert_called_once()
        self.assertEqual(homing.probing_move.call_count, 1)

    def test_native_tare_sets_existing_mcu_force_and_raw_limits(self):
        suite = fixtures.EngineTests()
        suite.setUp()
        move, collector = suite.diagnostic_move()
        move._log_trace = False
        collector.collect_min.return_value = ([(0., 0., 1000.)]*6, 0)
        move._pause_and_tare(fixtures.Gcmd())
        move._mcu_trigger_analog.set_raw_range.assert_called_once_with(
            -19000, 21000)
        move._mcu_trigger_analog.set_trigger.assert_called_once_with(
            'abs_ge', 75*32768)


    def test_point_reference_resolves_stale_saved_tare_rejection(self):
        suite = fixtures.EngineTests()
        suite.setUp()
        move, collector = suite.diagnostic_move()
        move._log_trace = False
        collector.collect_min.return_value = ([(0., 0., 39000.)]*6, 0)
        with self.assertRaisesRegex(RuntimeError, 'tare outside force limit'):
            move._pause_and_tare(fixtures.Gcmd())
        move._mcu_trigger_analog.set_raw_range.assert_not_called()
        move.set_safety_reference(39000.)
        move._pause_and_tare(fixtures.Gcmd())
        move._mcu_trigger_analog.set_raw_range.assert_called_once_with(
            19000, 59000)
        move._mcu_trigger_analog.set_trigger.assert_called_once_with(
            'abs_ge', 75*32768)
        self.assertEqual(suite.printer.cell.reference, 1000)
        self.assertEqual(suite.printer.cell.counts_per_gram, 100.)
        # Later retare cannot walk the band along with growing preload.
        collector.collect_min.return_value = ([(0., 0., 55000.)]*6, 0)
        with self.assertRaisesRegex(RuntimeError, 'compression margin'):
            move._pause_and_tare(fixtures.Gcmd())
        collector.collect_min.return_value = ([(0., 0., 59001.)]*6, 0)
        with self.assertRaisesRegex(RuntimeError, 'tare outside force limit'):
            move._pause_and_tare(fixtures.Gcmd())
        self.assertEqual(move._mcu_trigger_analog.set_raw_range.call_count, 1)
        self.assertEqual(move._mcu_trigger_analog.set_trigger.call_count, 1)
        self.assertEqual(move._safety_reference_counts, 39000)
        move.clear_safety_reference()
        self.assertIsNone(move._safety_reference_counts)


class NativeProfileTests(unittest.TestCase):
    def test_profiles_share_load_cell_and_select_separate_backends(self):
        for name, section in [('current', 'auto_z_offset'),
                              ('native', 'auto_z_offset_native')]:
            cfg = configparser.ConfigParser()
            cfg.read(pathlib.Path(fixtures.ROOT)/'config'/(
                'sample-auto-z-offset-%s.cfg' % name))
            self.assertEqual(cfg.sections(), [section])
            self.assertEqual(cfg[section]['load_cell'], 'bed_force')
            self.assertEqual(cfg[section]['trigger_force'], '75')
            self.assertEqual(cfg[section]['force_safety_limit'], '200')


if __name__ == '__main__':
    unittest.main()
