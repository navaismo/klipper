# Offline bidirectional tap diagnostics and non-interference tests
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import copy
import json
import os
import random
import unittest
from unittest import mock

from test_auto_z_offset import Config, Gcmd, Printer, ROOT, lcprobe

diag = lcprobe.load_cell_diagnostics


def curve(onset=0., release=0., stiffness=1000., drift=0., noise=0.,
          delay=0., count=497):
    def position(time):
        if time <= 101.:
            return .4 - .5 * (time - 100.)
        return -.1 + .1 * max(0., time - 101.2)
    rng = random.Random(527)
    samples = []
    up_stiffness = stiffness * (onset + .1) / (release + .1)
    period = 6.2 / (count - 1)
    for i in range(count):
        time = 100. + i * period
        z = position(time)
        contact, slope = ((onset, stiffness) if time < 101.2
                          else (release, up_stiffness))
        force = (max(contact - z, 0.) * slope + drift * (time - 100.)
                 + noise * rng.uniform(-1., 1.))
        samples.append([time + delay, force, 1000. - force * 100., 1000.])
    frame = {'stepper_names': ['stepper_z'], 'stepper_zero': {'stepper_z': 0.},
        'history_clear_time': 0., 'probe_offsets': [-24., -14.5, 1.065],
        'position': [28., 28., .4], 'homing_origin': [0., 0., 0., 0.],
        'gcode_position': [28., 28., .4, 0.], 'mesh_active': False,
        'read_errors': []}
    motion = {'name': 'auto_z_offset', 'tap_id': 1,
        'collector_start_time': 100., 'trigger_time': 101., 'raw_z': -.1,
        'ascent_start_time': 101.2, 'ascent_end_time': 106.2,
        'ascent_sample_time': 5., 'sample_period': period,
        'speed': .5, 'lift_speed': .1, 'retract_distance': .5,
        'safety': {'tare_counts': 1000., 'reference_tare_counts': 1000,
                   'baseline_force': 0., 'counts_per_gram': 100.,
                   'orientation': -1., 'trigger_force': 75.,
                   'force_safety_limit': 200.},
        'frames': {key: copy.deepcopy(frame) for key in (
            'before_probe', 'after_probe', 'after_workaround', 'after_ascent')}}
    return samples, motion, position


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.printer = Printer()
        self.toolhead = self.printer.toolhead
        self.stepper = mock.Mock()
        self.stepper.get_name.return_value = 'stepper_z'
        self.stepper.mcu_to_commanded_position.return_value = 0.
        self.toolhead.get_kinematics = mock.Mock()
        kin = self.toolhead.get_kinematics()
        kin.get_steppers.return_value = [self.stepper]
        self.move = mock.Mock()
        self.move.get_status.return_value = {
            'homing_origin': [0., 0., 0., 0.],
            'gcode_position': [28., 28., .4, 0.]}
        self.mesh = mock.Mock()
        self.mesh.get_mesh.return_value = None
        self.printer.objects.update({'gcode_move': self.move,
            'motion_queuing': mock.Mock(clear_history_time=0.),
            'bed_mesh': self.mesh})

    def test_failed_probe_read_does_not_hide_other_references(self):
        with mock.patch.object(self.printer.primary, 'get_offsets',
                               side_effect=RuntimeError('probe read failed')):
            with self.assertLogs(level='WARNING'):
                frame = diag.snapshot(self.printer, self.toolhead)
        self.assertIsNone(frame['probe_offsets'])
        self.assertEqual(frame['homing_origin'], [0., 0., 0., 0.])
        self.assertEqual(frame['gcode_position'], [28., 28., .4, 0.])
        self.assertIs(frame['mesh_active'], False)
        self.assertEqual(frame['history_clear_time'], 0.)
        self.assertEqual(frame['stepper_zero'], {'stepper_z': 0.})
        self.assertEqual(frame['read_errors'][0]['field'], 'probe_offsets')

    def test_failed_position_or_stepper_read_does_not_hide_other_fields(self):
        self.toolhead.get_position = mock.Mock(side_effect=RuntimeError('pos'))
        self.stepper.mcu_to_commanded_position.side_effect = RuntimeError(
            'zero')
        other = mock.Mock()
        other.get_name.return_value = 'stepper_x'
        other.mcu_to_commanded_position.return_value = 2.
        self.toolhead.get_kinematics().get_steppers.return_value.append(other)
        with self.assertLogs(level='WARNING'):
            frame = diag.snapshot(self.printer, self.toolhead)
        self.assertIsNone(frame['position'])
        self.assertIsNone(frame['stepper_zero']['stepper_z'])
        self.assertEqual(frame['stepper_zero']['stepper_x'], 2.)
        self.assertEqual(frame['history_clear_time'], 0.)
        self.assertEqual(frame['probe_offsets'][2], 1.065)

    def test_failed_heater_does_not_hide_other_heater_or_geometry(self):
        self.printer.bed.get_temp = mock.Mock(side_effect=RuntimeError('bed'))
        with self.assertLogs(level='WARNING'):
            frame = diag.snapshot(self.printer, self.toolhead)
        self.assertIsNone(frame['temperatures']['heater_bed'])
        self.assertEqual(frame['temperatures']['extruder']['measured'], 20.)
        self.assertEqual(frame['homing_origin'], [0., 0., 0., 0.])
        self.assertEqual(frame['history_clear_time'], 0.)

    def test_failed_gcode_field_mesh_or_clock_read_is_isolated(self):
        del self.move.get_status.return_value['homing_origin']
        self.mesh.get_mesh.side_effect = RuntimeError('mesh')
        with self.assertLogs(level='WARNING'):
            frame = diag.snapshot(self.printer, self.toolhead)
        self.assertIsNone(frame['homing_origin'])
        self.assertIsNone(frame['mesh_active'])
        self.assertEqual(frame['gcode_position'], [28., 28., .4, 0.])
        self.assertEqual(frame['history_clear_time'], 0.)
        self.printer.reactor.monotonic.side_effect = RuntimeError('clock')
        with self.assertLogs(level='WARNING'):
            frame = diag.snapshot(self.printer, self.toolhead)
        self.assertEqual(frame['probe_offsets'][2], 1.065)
        self.assertEqual(frame['history_clear_time'], 0.)


class TraceTests(unittest.TestCase):
    def setUp(self):
        self.printer = Printer()
        self.fit = lcprobe.LCBestFit(self.printer).find_best_fit

    def report(self, **kwargs):
        samples, motion, position = curve(**kwargs)
        original = copy.deepcopy(samples)
        report = diag.build_report(samples, motion, position, self.fit,
                                   regression_z=kwargs.get('release', 0.))
        self.assertEqual(samples, original)
        return report

    def test_ideal_no_hysteresis_has_coincident_contact_estimates(self):
        report = self.report()
        self.assertTrue(report['estimates_comparable'])
        self.assertAlmostEqual(report['descent']['z'], 0., places=8)
        self.assertAlmostEqual(report['ascent']['z'], 0., places=8)
        self.assertAlmostEqual(report['release_minus_onset_mm'], 0., places=8)
        self.assertEqual(set(report['phase_counts']),
                         {'descent', 'trigger_region', 'ascent'})
        self.assertAlmostEqual(report['observed_max_force_g'], 100.)
        self.assertAlmostEqual(report['trigger_reconstructed_z'], -.1)

    def test_hysteresis_is_reported_without_changing_primary_regression(self):
        report = self.report(release=.04)
        self.assertTrue(report['estimates_comparable'])
        self.assertAlmostEqual(report['release_minus_onset_mm'], .04, places=7)
        self.assertEqual(report['regression_contact_z'], .04)
        self.assertEqual(report['force_trigger_z'], -.1)

    def test_compliance_changes_force_slope_not_contact_separation(self):
        for stiffness in (200., 1000., 2500.):
            with self.subTest(stiffness=stiffness):
                report = self.report(stiffness=stiffness)
                self.assertTrue(report['estimates_comparable'])
                self.assertAlmostEqual(report['descent']['z'], 0., places=7)
                self.assertAlmostEqual(report['ascent']['z'], 0., places=7)
                self.assertAlmostEqual(
                    report['descent']['force_slope_g_per_mm'], -stiffness)

    def test_drift_is_visible_in_baselines_not_empirically_corrected(self):
        report = self.report(drift=.5, noise=.1)
        self.assertTrue(report['estimates_comparable'])
        self.assertGreater(report['baseline_change_g'], 1.)
        self.assertGreater(report['ascent']['baseline_trend_g_per_s'], .4)
        self.assertLess(abs(report['release_minus_onset_mm']), .01)
        self.assertEqual(report['regression_contact_z'], 0.)

    def test_delayed_timestamps_create_measurable_apparent_separation(self):
        report = self.report(delay=.1)
        self.assertTrue(report['estimates_comparable'])
        self.assertAlmostEqual(report['descent']['z'], -.05, places=6)
        # Delaying samples across the hold/ascent boundary adds a transient.
        self.assertAlmostEqual(report['ascent']['z'], .01, delta=.002)
        self.assertAlmostEqual(report['release_minus_onset_mm'], .06,
                               delta=.002)
        # This diagnostic does not silently estimate or remove ADC latency.
        self.assertEqual(report['regression_contact_z'], 0.)

    def test_origin_change_is_recorded_and_prevents_comparison(self):
        samples, motion, position = curve()
        motion['frames']['after_ascent']['stepper_zero']['stepper_z'] = .4
        report = diag.build_report(samples, motion, lambda t: position(t) + .4,
                                   self.fit, regression_z=.4)
        self.assertEqual(report['quality']['frame_state'], 'changed')
        self.assertAlmostEqual(report['trigger_reconstructed_z'], .3)
        self.assertFalse(report['estimates_comparable'])
        self.assertIsNone(report['release_minus_onset_mm'])

    def test_bad_or_missing_timestamps_cannot_produce_contact_comparison(self):
        for value in (None, float('nan'), float('inf'), 99.):
            with self.subTest(value=value):
                samples, motion, position = curve()
                samples[40][0] = value
                report = diag.build_report(samples, motion, position, self.fit)
                self.assertGreater(report['quality']['bad_timestamps'], 0)
                self.assertFalse(report['estimates_comparable'])
                self.assertIsNone(report['descent']['z'])

    def test_missing_trigger_time_and_expired_history_are_explicit(self):
        samples, motion, position = curve()
        motion['trigger_time'] = None
        report = diag.build_report(samples, motion, position, self.fit)
        self.assertFalse(report['quality']['valid_time_bounds'])
        self.assertFalse(report['estimates_comparable'])
        motion['trigger_time'] = 101.
        motion['frames']['after_ascent']['history_clear_time'] = 100.5
        report = diag.build_report(samples, motion, position, self.fit)
        self.assertGreater(report['quality']['expired_history_samples'], 0)
        self.assertTrue(any(r[2] is None for r in report['samples']))
        self.assertFalse(report['estimates_comparable'])

    def test_probe_offset_metadata_does_not_adjust_either_fit(self):
        samples, motion, position = curve()
        for offset in (.5, 1.065, 1.525, 3.):
            with self.subTest(offset=offset):
                for frame in motion['frames'].values():
                    frame['probe_offsets'][2] = offset
                report = diag.build_report(samples, motion, position, self.fit)
                self.assertAlmostEqual(report['descent']['z'], 0., places=7)
                self.assertAlmostEqual(report['ascent']['z'], 0., places=7)

    def test_incomplete_release_noise_and_failed_fit_remain_diagnostic(self):
        samples, motion, position = curve()
        samples = samples[:113]
        motion['ascent_end_time'] = samples[-1][0]
        report = diag.build_report(samples, motion, position, self.fit)
        self.assertIsNone(report['ascent']['z'])
        self.assertFalse(report['estimates_comparable'])
        noisy = self.report(noise=.5)
        self.assertLess(abs(noisy['release_minus_onset_mm']), .005)
        samples, motion, position = curve()
        with self.assertLogs(level='WARNING'):
            report = diag.build_report(samples, motion, position,
                mock.Mock(side_effect=RuntimeError('failed fit')))
        self.assertEqual(report['descent']['status'], 'fit_error')
        self.assertFalse(report['estimates_comparable'])

    def test_acquisition_error_does_not_publish_usable_contact_estimates(self):
        samples, motion, position = curve()
        report = diag.build_report(samples, motion, position, self.fit,
                                   error='Sensor reported errors')
        self.assertIsNone(report['descent']['z'])
        self.assertIsNone(report['ascent']['z'])
        self.assertFalse(report['estimates_comparable'])

    def test_log_bound_preserves_endpoints_trigger_and_full_peak_force(self):
        samples, motion, position = curve()
        samples = [samples[i % len(samples)][:] for i in range(12000)]
        samples[17][1] = 1234.
        report = diag.build_report(samples, motion, position, self.fit)
        self.assertLessEqual(report['logged_count'], diag.MAX_SAMPLES)
        self.assertEqual(report['samples'][0],
                         [*samples[0][:2], position(samples[0][0]),
                          'descent', *samples[0][2:]])
        self.assertEqual(report['samples'][-1][0], samples[-1][0])
        self.assertEqual(report['observed_max_force_g'], 1234.)
        self.assertTrue(any(r[1] == 1234. for r in report['samples']))

    def test_all_samples_within_log_budget_keep_their_original_timestamps(self):
        for count in (500, 2048):
            samples, motion, position = curve(count=count)
            report = diag.build_report(samples, motion, position, self.fit)
            self.assertEqual(report['logged_count'], count)
            self.assertEqual(report['stride'], 1)
            self.assertEqual([r[0] for r in report['samples']],
                             [s[0] for s in samples])

    def test_retained_real_ascent_traces_replay_but_cannot_supply_descent(self):
        filename = os.path.join(ROOT, 'test', 'fixtures',
                                'load_cell_ascent_reference.json')
        with open(filename) as source:
            fixture = json.load(source)
        for entry in fixture['traces']:
            trace, original = entry['trace'], entry['fit']
            result = self.fit(trace['force_z'])
            self.assertAlmostEqual(result[0], original['contact_z'], places=9)
            samples = [[t, row[0], 0., 0.] for t, row in zip(
                trace['sample_times'], trace['force_z'])]
            motion = {'name': 'auto_z_offset', 'raw_z': trace['raw_z'],
                'ascent_start_time': trace['start_time'],
                'ascent_end_time': samples[-1][0], 'ascent_sample_time': 5.}
            positions = dict((t, row[1]) for t, row in zip(
                trace['sample_times'], trace['force_z']))
            report = diag.build_report(samples, motion, positions.__getitem__,
                self.fit, regression_z=original['contact_z'])
            self.assertIsNone(report['descent']['z'])
            self.assertIsNone(report['release_minus_onset_mm'])
            self.assertFalse(report['estimates_comparable'])

    def test_every_required_reference_in_every_snapshot_must_be_complete(self):
        keys = ('position', 'stepper_names', 'stepper_zero', 'probe_offsets',
                'homing_origin', 'gcode_position', 'mesh_active',
                'history_clear_time')
        for frame_name in diag.FRAME_NAMES:
            for key in keys:
                with self.subTest(frame=frame_name, field=key):
                    samples, motion, position = curve()
                    del motion['frames'][frame_name][key]
                    report = diag.build_report(samples, motion, position,
                                               self.fit)
                    self.assertFalse(report['quality']['references_complete'])
                    self.assertFalse(report['estimates_comparable'])
                    self.assertIsNone(report['release_minus_onset_mm'])
                    self.assertIsNone(report['descent_fit_z'])

    def test_missing_snapshot_or_partial_mapping_prevents_comparison(self):
        for name in diag.FRAME_NAMES:
            samples, motion, position = curve()
            del motion['frames'][name]
            report = diag.build_report(samples, motion, position, self.fit)
            self.assertFalse(report['estimates_comparable'])
        samples, motion, position = curve()
        motion['frames']['after_probe']['stepper_names'].append('stepper_x')
        report = diag.build_report(samples, motion, position, self.fit)
        self.assertFalse(report['quality']['references_complete'])
        self.assertFalse(report['estimates_comparable'])

    def test_nonfinite_references_or_failed_reads_cannot_look_complete(self):
        for key in ('probe_offsets', 'homing_origin', 'position'):
            samples, motion, position = curve()
            motion['frames']['after_probe'][key][2] = float('nan')
            report = diag.build_report(samples, motion, position, self.fit)
            self.assertFalse(report['estimates_comparable'])
        samples, motion, position = curve()
        motion['frames']['after_probe']['read_errors'] = [
            {'field': 'probe_offsets', 'error': 'failed getter'}]
        report = diag.build_report(samples, motion, position, self.fit)
        self.assertFalse(report['quality']['references_complete'])

    def test_null_mappings_and_missing_read_status_are_incomplete(self):
        for frame_name in diag.FRAME_NAMES:
            for key in ('stepper_zero', 'stepper_names', 'read_errors'):
                samples, motion, position = curve()
                motion['frames'][frame_name][key] = None
                report = diag.build_report(samples, motion, position, self.fit)
                self.assertFalse(report['quality']['references_complete'])
                self.assertFalse(report['estimates_comparable'])

    def test_optional_heater_read_failure_does_not_invalidate_coordinates(self):
        samples, motion, position = curve()
        motion['frames']['after_probe']['read_errors'] = [
            {'field': 'temperatures.heater_bed', 'error': 'failed getter'}]
        report = diag.build_report(samples, motion, position, self.fit)
        self.assertTrue(report['quality']['references_complete'])
        self.assertTrue(report['estimates_comparable'])

    def test_transient_rebase_and_active_mesh_prevent_comparison(self):
        for key in ('stepper_zero', 'probe_offsets', 'homing_origin'):
            samples, motion, position = curve()
            if key == 'stepper_zero':
                motion['frames']['after_workaround'][key]['stepper_z'] = .4
            else:
                motion['frames']['after_workaround'][key][2] += .4
            report = diag.build_report(samples, motion, position, self.fit)
            self.assertEqual(report['quality']['frame_state'], 'changed')
            self.assertFalse(report['estimates_comparable'])
        samples, motion, position = curve()
        motion['frames']['after_probe']['mesh_active'] = True
        report = diag.build_report(samples, motion, position, self.fit)
        self.assertFalse(report['estimates_comparable'])

    def test_json_distinguishes_fits_threshold_and_original_mcu_trigger(self):
        report = self.report(release=.04, noise=.1)
        self.assertEqual(report['version'], 2)
        self.assertAlmostEqual(report['descent_fit_z'], 0., delta=.001)
        self.assertAlmostEqual(report['ascent_fit_z'], .04, delta=.001)
        self.assertEqual(report['mcu_trigger_z'], -.1)
        threshold = report['descent_threshold']
        self.assertEqual(threshold['status'], 'identified')
        self.assertGreater(report['descent_fit_z'],
                           report['descent_threshold_z'])
        self.assertGreater(report['descent_threshold_z'],
                           report['mcu_trigger_z'])
        self.assertEqual(report['fit_interpretation'],
                         'model_estimate_not_exact_physical_contact')
        row = next(r for r in report['samples'] if r[0] == threshold['time'])
        self.assertEqual(row[2], report['descent_threshold_z'])
        self.assertEqual(threshold['baseline_assumption'],
                         'initial_prefix_unloaded')

    def test_threshold_rejects_spikes_noise_and_invalid_baselines(self):
        samples, motion, position = curve()
        rows = [[s[0], s[1], position(s[0])] for s in samples if s[0] <= 101.]
        rows[20][1] = 10000.
        threshold = diag._descent_threshold(rows)
        self.assertGreater(threshold['time'], rows[20][0])
        self.assertEqual(diag._descent_threshold(rows[:10])['status'],
                         'insufficient_samples')
        noise = [[100. + i / 80., .2 * (-1)**i, .4 - i / 160.]
                 for i in range(80)]
        self.assertEqual(diag._descent_threshold(noise)['status'],
                         'no_sustained_crossing')
        rising = [[100. + i / 80., i * 2., .4 - i / 160.]
                  for i in range(80)]
        self.assertEqual(diag._descent_threshold(rising)['status'],
                         'baseline_not_flat')

    def test_threshold_can_be_identified_without_a_valid_regression(self):
        samples, motion, position = curve()
        with self.assertLogs(level='WARNING'):
            report = diag.build_report(samples, motion, position,
                mock.Mock(side_effect=RuntimeError('diagnostic fit failed')))
        self.assertIsNone(report['descent_fit_z'])
        self.assertIsNotNone(report['descent_threshold_z'])
        self.assertFalse(report['estimates_comparable'])


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.printer = Printer()
        stepper = mock.Mock()
        stepper.get_name.return_value = 'stepper_z'
        stepper.mcu_to_commanded_position.return_value = 0.
        self.printer.toolhead.get_kinematics = mock.Mock()
        kin = self.printer.toolhead.get_kinematics.return_value
        kin.get_steppers.return_value = [stepper]

    def tap(self, enabled, fit_error=False, first_home=False,
            probe_error=False, sensor_error=False):
        self.printer.trace.clear()
        samples, motion, position = curve()
        self.printer.toolhead.position = [28., 28., .4, 0.]
        move = lcprobe.LoadCellProbingMove.__new__(lcprobe.LoadCellProbingMove)
        move._printer, move._load_cell = self.printer, self.printer.cell
        move._log_trace, move._name = enabled, 'auto_z_offset'
        move._last_safety = motion['safety']
        move._pause_and_tare = mock.Mock()
        move._z_min_position = -3.
        move._param_helper = mock.Mock()
        move._param_helper.get_probe_params.return_value = {
            'probe_speed': .5, 'lift_speed': .1, 'load_cell_retract_dist': .5}
        move._mcu_trigger_analog = mock.Mock()
        move._mcu_trigger_analog.get_last_trigger_time.return_value = 101.
        collector = mock.Mock(min_time=100.)
        collector.collect_until.return_value = (samples, (1, 0)
                                                if sensor_error else 0)
        collector.stop_collecting.return_value = (samples, 0)
        move._start_collector = mock.Mock(return_value=collector)
        homing = mock.Mock()
        homing.check_probe_first_home.return_value = first_home
        def descend(endstop, target, speed):
            move._start_collector.assert_called_once()
            self.printer.trace.append(('probe', list(target), speed))
            if probe_error:
                raise RuntimeError('original MCU safety error')
            self.printer.toolhead.position[2] = -.1
            return [28., 28., -.1, 0.]
        homing.probing_move.side_effect = descend
        self.printer.objects['homing'] = homing
        self.printer.toolhead.set_position = mock.Mock()
        self.printer.toolhead.get_last_move_time = mock.Mock(
            side_effect=[101.2, 106.2])
        tap = lcprobe.TappingMove(Config(self.printer), move, mock.Mock(),
                                  ascent_sample_time=5., log_trace=enabled)
        tap._analyze_ascent = mock.Mock(return_value=0.)
        if fit_error:
            tap._analyze_ascent.side_effect = RuntimeError('original fit error')
        with mock.patch.object(lcprobe, '_lookup_z_pos',
                               side_effect=lambda th, t: position(t)):
            if fit_error or probe_error or sensor_error:
                message = ('original fit error' if fit_error else
                           'original MCU safety error' if probe_error else
                           'Load cell sensor reported errors')
                with self.assertRaisesRegex(RuntimeError, message):
                    tap.run_tap(Gcmd())
                result = None
            else:
                result = tap.run_tap(Gcmd())
        move._pause_and_tare.assert_called_once()
        move._start_collector.assert_called_once()
        if probe_error:
            collector.collect_until.assert_not_called()
        else:
            collector.collect_until.assert_called_once_with(106.2)
        collector.stop_collecting.assert_called_once()
        self.assertEqual(self.printer.toolhead.get_last_move_time.call_count,
                         0 if probe_error else 2)
        return result, list(self.printer.trace), tap

    def test_logging_reuses_collector_and_preserves_motion_and_result(self):
        silent, before, _ = self.tap(False)
        with self.assertLogs(level='INFO') as logs:
            verbose, after, tap = self.tap(True)
        self.assertEqual(before, after)
        self.assertEqual(silent, verbose)
        self.assertEqual(tap.get_status(0.)['last_trigger_z'], -.1)
        payload = next(item.split('Load cell tap diagnostic: ')[1]
                       for item in logs.output if 'Load cell tap diagnostic:'
                       in item)
        report = json.loads(payload)
        self.assertEqual(report['sample_count'], 497)
        self.assertEqual(report['motion']['safety']['force_safety_limit'], 200.)
        self.assertFalse(report['motion']['homing_workaround'])
        self.assertEqual(report['motion']['frames']['after_ascent']
                         ['temperatures']['heater_bed']['measured'], 20.)

    def test_fit_failure_is_not_replaced_by_diagnostic_success(self):
        with self.assertLogs(level='INFO') as logs:
            _, _, tap = self.tap(True, fit_error=True)
        self.assertFalse(tap.get_status(0.)['is_last_tap_valid'])
        self.assertTrue(any('original fit error' in item
                            for item in logs.output))

    def test_observer_failure_cannot_change_motion_or_result(self):
        normal, before, _ = self.tap(False)
        with mock.patch.object(diag, 'build_report',
                               side_effect=RuntimeError('observer failed')):
            with self.assertLogs(level='WARNING'):
                result, after, _ = self.tap(True)
        self.assertEqual(normal, result)
        self.assertEqual(before, after)

    def test_snapshot_exceptions_at_all_four_stages_preserve_result(self):
        original = diag.snapshot
        normal, before, _ = self.tap(False)
        for failed_stage in range(1, 5):
            calls = []
            def snapshot(*args):
                calls.append(1)
                if len(calls) == failed_stage:
                    raise RuntimeError('snapshot observer failure')
                return original(*args)
            with mock.patch.object(diag, 'snapshot', side_effect=snapshot):
                with self.assertLogs(level='WARNING'):
                    result, after, _ = self.tap(True)
            self.assertEqual(normal, result)
            self.assertEqual(before, after)

    def test_report_exception_cannot_mask_fit_sensor_or_mcu_error(self):
        for failure in ('fit_error', 'sensor_error', 'probe_error'):
            with self.subTest(failure=failure):
                with mock.patch.object(diag, 'log_report',
                        side_effect=RuntimeError('observer failure')):
                    with self.assertLogs(level='WARNING'):
                        _, _, tap = self.tap(True, **{failure: True})
                self.assertFalse(tap.get_status(0.)['is_last_tap_valid'])

    def test_broken_warning_handler_cannot_escape_diagnostic_failure(self):
        normal, before, _ = self.tap(False)
        with mock.patch.object(diag, 'build_report',
                               side_effect=RuntimeError('observer failed')):
            with mock.patch.object(diag.logging, 'warning',
                                   side_effect=RuntimeError('logging failed')):
                result, after, _ = self.tap(True)
        self.assertEqual(normal, result)
        self.assertEqual(before, after)

    def test_first_home_workaround_is_logged_without_changing_it(self):
        with self.assertLogs(level='INFO') as logs:
            self.tap(True, first_home=True)
        self.printer.toolhead.set_position.assert_called_once_with(
            [28., 28., 0., 0.])
        self.assertTrue(any('"homing_workaround":true' in item
                            for item in logs.output))


if __name__ == '__main__':
    unittest.main()
