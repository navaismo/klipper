# Onset acquisition: finite motion, continuous MCU guard and causal traces
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import copy
import json
import os
import random
import types
import unittest
from unittest import mock

import test_auto_z_offset as engine
import test_auto_z_offset_diagnostic as commands
from test_load_cell_diagnostics import curve

auto, lcprobe = engine.auto, engine.lcprobe
movement = lcprobe.load_cell_contact_move
diag = auto.load_cell_diagnostics


def load_reference(report):
    report = copy.deepcopy(report)
    report['maximum_sample_age_s'] = 3. / 80.
    report['operational_tare_counts'] = report['samples'][0][5]
    return report


class Simulation:
    def __init__(self, frozen=False, delay=.1, tare=-436490):
        self.printer = engine.Printer()
        self.toolhead = self.printer.toolhead
        self.toolhead.position = [28., 28., .4, 0.]
        self.time = 1000.
        self.next_sample_time = self.time + 1./80.
        self.tare = tare
        self.printer.cell.tare_counts = tare
        self.printer.cell.get_tare_counts = lambda: (
            self.printer.cell.tare_counts)
        self.samples = []
        self.positions = {}
        self.moves = []
        self.frozen, self.delay = frozen, delay
        self.rng = random.Random(19)
        self.completion = mock.Mock()
        self.completion.test.return_value = False
        self.collector = mock.Mock()
        self.collector.min_time = self.time
        self.collector.snapshot_until.side_effect = self.snapshot
        self.toolhead.drip_move = self.drip
        self.toolhead.get_last_move_time = lambda: self.time
        self.owner = types.SimpleNamespace(
            _printer=self.printer, _load_cell=self.printer.cell,
            _mcu_trigger_analog=types.SimpleNamespace(MONITOR_MAX=3,
                get_steppers=lambda: [types.SimpleNamespace(
                    get_step_dist=lambda: .0025)]),
            _diagnostic_motion={'speed': .5, 'frames': {'before_probe':
                curve()[1]['frames']['before_probe']}})
        self.stop = movement.OnsetStop(self.owner, self.collector,
            lambda th, t: self.positions[t], lcprobe.check_sensor_errors,
            {'checkpoint': lambda: None, 'search_start_z': .3})

    def drip(self, target, speed, completion):
        self.moves.append((self.toolhead.position[2], target[2]))
        first = self.toolhead.position[2]
        start, duration = self.time, (first-target[2])/speed
        self.time += duration
        while self.next_sample_time <= self.time:
            t = self.next_sample_time
            z = max(target[2], first - speed*(t-start))
            self.add(t, z)
            self.next_sample_time += 1./80.
        self.toolhead.position[2] = target[2]

    def add(self, t, z):
        force = (0. if self.frozen else .2 + .03*(t-1000.)
                 + 600.*max(-z, 0.) + self.rng.gauss(0., .3))
        self.samples.append([t, force, self.tare-force*100., self.tare])
        self.positions[t] = z

    def snapshot(self, end, limit):
        # Advance time with position held: a delayed host never causes motion.
        self.time = max(self.time + self.delay, end + 1./80.)
        while self.next_sample_time <= self.time:
            self.add(self.next_sample_time, self.toolhead.position[2])
            self.next_sample_time += 1./80.
        return self.samples[-limit:], 0

    def run(self, floor=-.1):
        frame = self.owner._diagnostic_motion['frames']['before_probe']
        with mock.patch.object(diag, 'snapshot', return_value=frame):
            self.stop.move([28., 28., floor], .5, self.completion)


class FiniteStopTests(unittest.TestCase):
    def test_supported_onset_stops_before_floor_without_hard_force(self):
        sim = Simulation()
        sim.run()
        result = sim.stop.finish(0.)
        self.assertEqual(result['reason'], 'onset')
        self.assertGreater(result['stop_z'], -.1)
        self.assertAlmostEqual(result['onset']['z'], 0., delta=.02)
        self.assertLess(max(s[1] for s in sim.samples), 75.)
        self.assertAlmostEqual(result['max_search_segment_mm'], .01875)
        for start, end in sim.moves[1:]:
            self.assertLessEqual(start-end, .01875+1.e-12)
        self.assertTrue(all(end >= -.1 for start, end in sim.moves))

    def test_delayed_data_stops_at_last_finite_segment(self):
        sim = Simulation()
        def unavailable(end, limit):
            self.assertEqual(len(sim.moves), 1)
            self.assertAlmostEqual(sim.toolhead.position[2], .38375)
            raise RuntimeError('stream timeout')
        sim.collector.snapshot_until.side_effect = unavailable
        sim.run()
        with self.assertRaisesRegex(RuntimeError, 'stream timeout'):
            sim.stop.finish(0.)
        self.assertEqual(len(sim.moves), 1)

    def test_frozen_arriving_signal_exhausts_same_floor_without_candidate(self):
        sim = Simulation(frozen=True)
        sim.run()
        with self.assertRaisesRegex(RuntimeError, 'bounded endpoint'):
            sim.stop.finish(0.)
        self.assertAlmostEqual(sim.moves[-1][1], -.1)
        self.assertIsNone(sim.stop.stop_time)

    def test_hard_force_completion_and_error_never_become_normal_onset(self):
        sim = Simulation()
        def hit(*args):
            sim.completion.test.return_value = True
        sim.toolhead.drip_move = hit
        sim.run()
        sim.collector.snapshot_until.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, 'hard-force stop'):
            sim.stop.finish(1000.1)
        sim.stop.error = RuntimeError('host report error')
        with self.assertRaisesRegex(RuntimeError, 'hard-force stop'):
            sim.stop.finish(1000.1)

    def test_sensor_error_and_coordinate_change_prevent_next_segment(self):
        sim = Simulation()
        sim.collector.snapshot_until.return_value = ([], (1, 0))
        sim.collector.snapshot_until.side_effect = None
        sim.run()
        with self.assertRaisesRegex(RuntimeError, 'sensor reported errors'):
            sim.stop.finish(0.)
        self.assertEqual(len(sim.moves), 1)
        sim = Simulation()
        changed = copy.deepcopy(sim.owner._diagnostic_motion[
            'frames']['before_probe'])
        changed['stepper_zero']['stepper_z'] += .01
        with mock.patch.object(diag, 'snapshot', return_value=changed):
            sim.stop.move([28., 28., -.1], .5, sim.completion)
        with self.assertRaisesRegex(RuntimeError, 'coordinates'):
            sim.stop.finish(0.)
        self.assertEqual(len(sim.moves), 1)

    def test_guard_completion_during_checkpoint_queues_no_move(self):
        sim = Simulation()
        sim.stop.options['checkpoint'] = lambda: setattr(
            sim.completion.test, 'return_value', True)
        sim.run()
        self.assertEqual(sim.moves, [])


class RealPrefixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(engine.ROOT, 'test/fixtures',
                          'auto_z_offset_p1_endpoints_20261103.json')) as f:
            cls.records = json.load(f)['records']

    def test_all_four_causal_prefixes_identify_onset_without_75g(self):
        for record in self.records:
            d = record['diagnostic']
            quality, unused = diag._references(d['motion']['frames'],
                                              ('before_probe', 'after_probe'))
            quality.update(valid_time_bounds=True, bad_timestamps=0,
                invalid_samples=0, expired_history_samples=0, lookup_errors=0)
            end = d['trigger_time'] or d['samples'][-1][0]
            rows = [s for s in d['samples'] if s[0] <= end]
            found = None
            for i in range(max(6, len(rows)-160), len(rows)+1, 3):
                prefix = rows[:i]
                if max(r[1] for r in prefix) >= 75.:
                    break
                report = {'quality': quality, 'motion': {'sensor_errors': 0},
                    'samples': prefix, 'sample_count': len(prefix), 'stride': 1,
                    'descent_end_time': prefix[-1][0]}
                result = auto.load_cell_contact_onset.estimate(report)
                checker = movement.OnsetStop.__new__(movement.OnsetStop)
                checker.max_segment = .5 * 3. / 80.
                if (result['status'] == 'identified'
                        and checker.qualify_stop(result,
                            load_reference(report))['qualified']):
                    found = (prefix, result)
                    break
            self.assertIsNotNone(found, d['tap_id'])
            prefix, result = found
            self.assertLess(max(r[1] for r in prefix), 75.)
            self.assertGreater(prefix[-1][2], d['motion']['target'][2])
            self.assertLessEqual(max(r[0] for r in prefix), end)


class NativeGuardTests(unittest.TestCase):
    def native_move(self, handler=True, error=None):
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
        th.drip_move = mock.Mock()
        state = {'armed': False, 'during_callback': []}
        endstop = mock.Mock()
        endstop.get_steppers.return_value = [stepper]
        def arm(*args, **kwargs):
            state['armed'] = True
            return mock.Mock()
        def clear(*args):
            state['armed'] = False
            if error is not None:
                raise RuntimeError(error)
            return 0.
        endstop.home_start.side_effect = arm
        endstop.home_wait.side_effect = clear
        pos = types.SimpleNamespace(stepper_name='stepper_z',
            endstop_name='probe', start_pos=0,
            halt_pos=1, trig_pos=1, note_home_end=mock.Mock(),
            verify_no_probe_skew=mock.Mock())
        hm = engine.homing.HomingMove(printer, [(endstop, 'probe')])
        hm._calc_endstop_rate = mock.Mock(return_value=.001)
        hm.calc_toolhead_pos = mock.Mock(return_value=[28., 28., .2, 0.])
        def move(destination, speed, completion):
            state['during_callback'].append(state['armed'])
            th.drip_move([28., 28., .3], speed, completion)
            # Computation/data waiting must leave the same MCU guard armed.
            state['during_callback'].append(state['armed'])
        with mock.patch.object(engine.homing, 'StepperPosition',
                               return_value=pos):
            hm.homing_move([28., 28., -.1], .5, probe_pos=True,
                check_triggered=not handler,
                move_handler=move if handler else None)
        return state, endstop

    def test_native_guard_stays_armed_through_callback_and_waiting(self):
        state, endstop = self.native_move()
        self.assertEqual(state['during_callback'], [True, True])
        self.assertFalse(state['armed'])
        endstop.home_start.assert_called_once()
        endstop.home_wait.assert_called_once()

    def test_unrelated_native_probe_still_requires_trigger(self):
        with self.assertRaisesRegex(RuntimeError, 'No trigger'):
            self.native_move(handler=False)

    def test_native_sensor_error_is_not_replaced_by_soft_decision(self):
        with self.assertRaisesRegex(RuntimeError, 'RAW_RANGE'):
            self.native_move(error='Trigger analog error: RAW_RANGE')

    def test_stream_snapshot_does_not_restart_subscription(self):
        printer = engine.Printer()
        collector = lcprobe.load_cell.LoadCellSampleCollector(
            printer, printer.cell)
        collector.is_started = True
        collector._samples = [[i, 0., 0., 0.] for i in (1., 2., 3.)]
        rows, errors = collector.snapshot_until(2., 2)
        self.assertEqual([r[0] for r in rows], [2., 3.])
        self.assertEqual(errors, 0)
        self.assertTrue(collector.is_started)
        collector.snapshot_until(3., 2)
        self.assertTrue(collector.is_started)


class SoftStopCommandTests(unittest.TestCase):
    def test_soft_stop_without_trigger_or_ascent_can_form_candidate(self):
        h = commands.NativeWorkflowTests()
        h.setUpClass()
        h.setUp()
        self.addCleanup(h.doCleanups)
        d = copy.deepcopy(h.diagnostic)
        rows = d['samples'][:]
        report = dict(d, descent_end_time=d['trigger_time'])
        result = auto.load_cell_contact_onset.estimate(report)
        self.assertEqual(result['status'], 'identified')
        checker = movement.OnsetStop.__new__(movement.OnsetStop)
        checker.max_segment = .5 * 3. / 80.
        stop_quality = checker.qualify_stop(result, load_reference(report))
        self.assertTrue(stop_quality['qualified'])
        d['contact_stop'] = {'reason': 'onset', 'decision_report': report,
                             'stop_qualification': stop_quality}
        h.tap.tapping_move.get_contact_diagnostics.return_value = d
        h.tap.tapping_move.get_status.return_value = {
            'last_trigger_z': None, 'last_stop_z': rows[-1][2],
            'last_regression_z': None}
        h.tap.tapping_move.run_tap.side_effect = None
        h.tap.tapping_move.run_tap.return_value = (
            [28., 28., result['z']], True)
        h.automatic()
        self.assertEqual(h.module.state, 'candidate')
        self.assertIsNone(h.module.last_contact['force_trigger_z'])
        self.assertIsNone(h.module.last_contact['regression_z'])
        self.assertIsNone(h.module.last_contact['model_offset'])
        self.assertEqual(h.module.last_contact['stop_reason'], 'onset')
        self.assertAlmostEqual(h.module.result['z_offset'],
                               h.primary.height-result['z'])
        h.assert_preserved()

    def test_missing_load_confirmation_cannot_create_or_apply_candidate(self):
        h = commands.NativeWorkflowTests()
        h.setUpClass()
        h.setUp()
        self.addCleanup(h.doCleanups)
        d = copy.deepcopy(h.diagnostic)
        d['contact_stop'] = {'reason': 'onset', 'stop_qualification': {
            'qualified': True, 'load_evidence': {'qualified': False}}}
        h.tap.tapping_move.get_contact_diagnostics.return_value = d
        with self.assertRaisesRegex(RuntimeError, 'lacks confirmed load'):
            h.automatic()
        self.assertIsNone(h.module.candidate)
        self.assertIsNone(h.module.result)
        with self.assertRaisesRegex(RuntimeError, 'No validated'):
            h.module.cmd_APPLY(engine.Gcmd())
        h.printer.objects['configfile'].set.assert_not_called()
        self.assertEqual(h.primary.offsets[2], 1.065)
        self.assertEqual(h.printer.cell.counts_per_gram, 114.88636)


class SegmentedWindowTests(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(engine.ROOT, 'test/fixtures',
                            'load_cell_onset_segmented_20261103.json')) as f:
            self.record = json.load(f)

    def travel_report(self):
        return dict(self.record['report'], descent_window_basis='travel',
                    descent_window_speed=self.record['speed'])

    def test_exact_real_failure_replays_unchanged_with_time_windows(self):
        result = auto.load_cell_contact_onset.estimate(self.record['report'])
        self.assertEqual(result, self.record['recorded_result'])
        self.assertEqual(result['reasons'], ['window_models_disagree'])

    def test_real_segmented_windows_cover_travel_and_keep_actual_time(self):
        report = self.travel_report()
        original = copy.deepcopy(report)
        result = auto.load_cell_contact_onset.estimate(report)
        self.assertEqual(report, original)
        self.assertEqual(result['status'], 'identified', result)
        self.assertEqual(result['supported_window_count'], 4)
        self.assertAlmostEqual(result['z'], -.3025812438480189, places=8)
        self.assertLess(result['window_span_mm'], .005)
        first = result['windows'][0]
        self.assertEqual(first['travel_span_mm'], .125)
        self.assertGreater(first['time_bounds_s'][1]
                           - first['time_bounds_s'][0], .25)
        self.assertEqual(first['time_bounds_s'][1], report['samples'][-1][0])
        self.assertEqual(result['windows'][3]['status'], 'unresolved')
        self.assertEqual(result['windows'][3]['reasons'],
                         ['insufficient_window_coverage'])
        self.assertFalse(result['eligible_for_application'])

    def test_missing_invalid_speed_or_incomplete_data_still_reject(self):
        for speed in (None, 0., -.5, float('nan')):
            report = self.travel_report()
            report['descent_window_speed'] = speed
            result = auto.load_cell_contact_onset.estimate(report)
            self.assertEqual(result['reasons'],
                             ['invalid_descent_window_reference'])
        report = self.travel_report()
        report['stride'] = 2
        self.assertEqual(auto.load_cell_contact_onset.estimate(report)
                         ['reasons'], ['incomplete_acquisition'])


class StopPrecisionTests(unittest.TestCase):
    def setUp(self):
        self.checker = movement.OnsetStop.__new__(movement.OnsetStop)
        self.checker.max_segment = .5 * 3. / 80.

    def test_all_four_weak_real_onsets_are_rejected_without_changing_fit(self):
        with open(os.path.join(engine.ROOT, 'test/fixtures',
                              'load_cell_false_onsets_20261103.json')) as f:
            records = json.load(f)['records']
        self.assertEqual(len(records), 4)
        for record in records:
            result = auto.load_cell_contact_onset.estimate(record['report'])
            self.assertEqual(result, record['onset'])
            quality = self.checker.qualify_stop(result,
                                               load_reference(record['report']))
            self.assertFalse(quality['qualified'])
            self.assertIn('onset_interval_exceeds_search_advance',
                          quality['reasons'])
            self.assertFalse(quality['load_evidence']['qualified'])

    def test_precise_recorded_compression_remains_supported(self):
        with open(os.path.join(engine.ROOT, 'test/fixtures',
                            'load_cell_onset_segmented_20261103.json')) as f:
            record = json.load(f)
        report = dict(record['report'], descent_window_basis='travel',
                      descent_window_speed=record['speed'])
        result = auto.load_cell_contact_onset.estimate(report)
        quality = self.checker.qualify_stop(result, load_reference(report))
        self.assertTrue(quality['qualified'])
        self.assertFalse(quality['physical_contact_verified'])

    def test_missing_nonfinite_or_inverted_intervals_fail_closed(self):
        for bounds in (None, [0.], [0., float('nan')], [.2, .1], [0., 0.]):
            result = {'z': 0., 'selected_window_s': .25, 'windows': [
                {'status': 'identified', 'window_s': .25,
                 'profile_interval_z_mm': bounds}]}
            self.assertFalse(self.checker.qualify_stop(result)['qualified'])

    def test_imprecise_model_cannot_finish_even_with_recorded_stop_time(self):
        self.checker.stop_time = 1.
        self.checker.error = None
        self.checker.printer = engine.Printer()
        self.checker.qualification = {'qualified': False}
        with self.assertRaisesRegex(RuntimeError, 'bounded endpoint'):
            self.checker.finish(0.)

    def test_imprecise_signal_cannot_stop_motion_or_extend_original_floor(self):
        sim = Simulation()
        sim.stop.qualify_stop = mock.Mock(return_value={'qualified': False})
        sim.run()
        self.assertGreater(sim.stop.qualify_stop.call_count, 0)
        self.assertAlmostEqual(sim.moves[-1][1], -.1)
        self.assertTrue(all(end >= -.1 for start, end in sim.moves))
        with self.assertRaisesRegex(RuntimeError, 'bounded endpoint'):
            sim.stop.finish(0.)


class MeasuredLoadTests(unittest.TestCase):
    def setUp(self):
        self.checker = movement.OnsetStop.__new__(movement.OnsetStop)
        self.checker.max_segment = .5 * 3. / 80.

    def compression(self):
        with open(os.path.join(engine.ROOT, 'test/fixtures',
                            'load_cell_onset_segmented_20261103.json')) as f:
            record = json.load(f)
        report = load_reference(dict(record['report'],
            descent_window_basis='travel',
            descent_window_speed=record['speed']))
        return report, auto.load_cell_contact_onset.estimate(report)

    def test_four_weak_real_curves_fail_even_with_artificially_narrow_fit(self):
        with open(os.path.join(engine.ROOT, 'test/fixtures',
                              'load_cell_false_onsets_20261103.json')) as f:
            records = json.load(f)['records']
        for record in records:
            result = copy.deepcopy(record['onset'])
            selected = next(w for w in result['windows']
                            if w['window_s'] == result['selected_window_s'])
            dz = self.checker.max_segment / 10.
            selected['profile_interval_z_mm'] = [result['z'] - dz,
                                                 result['z'] + dz]
            q = self.checker.qualify_stop(result,
                                         load_reference(record['report']))
            self.assertNotIn('onset_interval_exceeds_search_advance',
                             q['reasons'])
            self.assertEqual(q['reasons'],
                             ['no_sustained_compression_above_noise'])

    def test_one_force_spike_is_not_sustained_load(self):
        report, result = self.compression()
        report['samples'][-3][1] = 0.
        report['samples'][-1][1] = 0.
        q = self.checker.qualify_stop(result, report)
        self.assertFalse(q['qualified'])
        self.assertEqual(q['load_evidence']['reason'],
                         'no_sustained_compression_above_noise')

    def test_stale_tail_and_reused_segment_samples_do_not_confirm_load(self):
        for stale in (True, False):
            report, result = self.compression()
            if stale:
                report['descent_end_time'] += 1.
            else:
                report['previous_descent_end_time'] = (
                    report['samples'][-1][0])
            q = self.checker.qualify_stop(result, report)
            self.assertEqual(q['load_evidence']['reason'],
                             'no_fresh_load_confirmation')

    def test_onset_outside_observed_transition_cannot_confirm_load(self):
        report, result = self.compression()
        z = report['samples'][-1][2] - .1
        selected = next(w for w in result['windows']
                        if w['window_s'] == result['selected_window_s'])
        result['z'] = z
        selected['profile_interval_z_mm'] = [z - .001, z + .001]
        q = self.checker.qualify_stop(result, report)
        self.assertEqual(q['load_evidence']['reason'],
                         'unobserved_contact_transition')

    def test_tare_changes_stop_before_next_segment(self):
        sim = Simulation()
        def change():
            if sim.stop.segment_count:
                sim.printer.cell.tare_counts += 1
        sim.stop.options['checkpoint'] = change
        sim.run()
        self.assertEqual(len(sim.moves), 1)
        with self.assertRaisesRegex(RuntimeError, 'Changed onset tare'):
            sim.stop.finish(0.)

    def test_new_contact_does_not_inherit_samples_tare_or_detector_state(self):
        first = Simulation()
        first.run()
        second = Simulation(tare=-1000)
        self.assertIsNone(second.stop.result)
        self.assertIsNone(second.stop._last_cutoff)
        self.assertIsNone(second.stop.qualification)
        self.assertEqual(second.samples, [])
        second.run()
        self.assertTrue(second.stop.finish(0.)['stop_qualification'][
            'load_evidence']['qualified'])
        self.assertEqual(second.stop.tare_counts, -1000)
        self.assertTrue(all(s[3] == -1000 for s in second.samples))

    def test_future_batch_cannot_make_old_pre_endpoint_samples_fresh(self):
        sim = Simulation()
        def gap(end, limit):
            sim.add(end - .05, .4)
            sim.add(end + 1., .3)
            return sim.samples[-2:], 0
        sim.collector.snapshot_until.side_effect = gap
        sim.run()
        self.assertEqual(len(sim.moves), 1)
        with self.assertRaisesRegex(RuntimeError, 'Stale onset samples'):
            sim.stop.finish(0.)

    def test_original_force_timestamp_and_step_history_z_stay_associated(self):
        sim = Simulation()
        sim.run()
        report = sim.stop.report
        source = {s[0]: s for s in sim.samples}
        for row in report['samples']:
            self.assertEqual([row[0], row[1], row[4], row[5]], source[row[0]])
            self.assertEqual(row[2], sim.positions[row[0]])
            self.assertLessEqual(row[0], report['descent_end_time'])
        self.assertGreater(report['samples'][0][2], report['samples'][-1][2])

    def test_stable_narrow_fake_model_cannot_stop_frozen_arriving_signal(self):
        sim = Simulation(frozen=True)
        def fitted(report):
            rows = report['samples']
            z = (rows[0][2] + rows[-1][2]) / 2.
            dz = sim.stop.max_segment / 10.
            return {'status': 'identified', 'z': z, 'reasons': [],
                'selected_window_s': None, 'windows': [{
                    'status': 'identified', 'window_s': None,
                    'baseline_force_g': 0., 'baseline_drift_g_per_s': 0.,
                    'baseline_time_s': rows[0][0],
                    'time_bounds_s': [rows[0][0], rows[-1][0]],
                    'profile_interval_z_mm': [z - dz, z + dz]}]}
        with mock.patch.object(auto.load_cell_contact_onset, 'estimate',
                               side_effect=fitted):
            sim.run()
        self.assertEqual(sim.stop.result['status'], 'identified')
        self.assertFalse(sim.stop.qualification['load_evidence']['qualified'])
        self.assertAlmostEqual(sim.moves[-1][1], -.1)
        with self.assertRaisesRegex(RuntimeError, 'bounded endpoint'):
            sim.stop.finish(0.)


class FailureEvidenceTests(unittest.TestCase):
    def rejected_move(self, native_error=None, broken_evidence=False):
        harness = engine.EngineTests()
        harness.setUp()
        move, collector = harness.diagnostic_move()
        move._z_min_position = -3.
        move._param_helper = mock.Mock()
        move._param_helper.get_probe_params.return_value = {'probe_speed': .5}
        move._pause_and_tare = mock.Mock()
        collector.min_time = 300.
        collector.stop_collecting.return_value = ([], 0)
        phoming = mock.Mock()
        phoming.probing_move.return_value = [110., 110., -.5675, 0.]
        phoming.probing_move.side_effect = native_error
        harness.printer.objects['homing'] = phoming
        move._mcu_trigger_analog.get_last_trigger_time.return_value = 373.
        stop = mock.Mock()
        stop.report = {'stride': 1, 'samples': [[372., 60., -.55]],
                       'sample_count': 1, 'descent_end_time': 372.}
        stop.result = {'status': 'unresolved',
                       'reasons': ['insufficient_supported_windows']}
        stop.segment_count = 88
        stop.max_segment = .01875
        stop.error = None
        stop.qualification = {'qualified': False}
        stop.finish.side_effect = RuntimeError('MCU hard-force stop')
        if broken_evidence:
            del stop.report
        move._log_probe_failure = mock.Mock()
        with mock.patch.object(movement, 'OnsetStop', return_value=stop):
            with self.assertRaisesRegex(RuntimeError,
                    'RAW_RANGE' if native_error else 'MCU hard-force stop'):
                move.probing_move(engine.Gcmd(), -.95, 5.95, onset_stop={})
        collector.stop_collecting.assert_called_once()
        move._log_probe_failure.assert_called_once()
        return move, stop

    def test_force_rejection_retains_native_trigger_and_exact_decision(self):
        move, stop = self.rejected_move()
        motion = move._diagnostic_motion
        self.assertEqual(motion['trigger_time'], 373.)
        self.assertEqual(motion['raw_z'], -.5675)
        self.assertIs(motion['onset_decision']['report'], stop.report)
        self.assertIs(motion['onset_decision']['result'], stop.result)
        self.assertEqual(motion['onset_decision']['segment_count'], 88)

    def test_native_safety_error_keeps_evidence_without_inventing_trigger(self):
        move, stop = self.rejected_move(RuntimeError('RAW_RANGE'))
        self.assertNotIn('trigger_time', move._diagnostic_motion)
        self.assertIs(move._diagnostic_motion['onset_decision']['report'],
                      stop.report)
        stop.finish.assert_not_called()

    def test_broken_evidence_does_not_replace_original_force_error(self):
        with self.assertLogs(level='WARNING'):
            self.rejected_move(broken_evidence=True)


class TapFinalizationTests(unittest.TestCase):
    def tap(self, sensor_error=False, bad_frame=False, invalid_sample=False):
        printer = engine.Printer()
        samples, motion, position = curve()
        if invalid_sample:
            samples[-1] = (samples[-1][0], float('nan'), 0., 0.)
        motion = copy.deepcopy(motion)
        motion['trigger_time'] = None
        motion['contact_stop'] = {'reason': 'onset', 'stop_time': 101.,
            'stop_z': -.1, 'onset': {'status': 'identified', 'z': 0.,
                                   'reasons': []}, 'decision_report': {}}
        move = mock.Mock()
        move._printer = printer
        move._mcu.is_fileoutput.return_value = False
        move._diagnostic_motion = motion
        move._param_helper.get_probe_params.return_value = {
            'load_cell_retract_dist': .5, 'lift_speed': .1}
        collector = mock.Mock()
        collector.collect_until.return_value = (samples,
                                                (1, 0) if sensor_error else 0)
        move.probing_move.return_value = ([28., 28., -.1, 0.], collector)
        printer.objects['homing'] = mock.Mock()
        printer.objects['homing'].check_probe_first_home.return_value = False
        printer.toolhead.get_last_move_time = mock.Mock(
            side_effect=[101.2, 106.2])
        tap = lcprobe.TappingMove(engine.Config(printer), move, mock.Mock(),
                                  log_trace=True)
        tap._analyze_ascent = mock.Mock(side_effect=RuntimeError('fit only'))
        frame = copy.deepcopy(motion['frames']['before_probe'])
        if bad_frame:
            frame['stepper_zero']['stepper_z'] += .01
        with mock.patch.object(diag, 'snapshot', return_value=frame):
            with mock.patch.object(lcprobe, '_lookup_z_pos',
                                   side_effect=lambda th, t: position(t)):
                if sensor_error or bad_frame or invalid_sample:
                    with self.assertRaisesRegex(RuntimeError,
                            'sensor reported errors' if sensor_error
                            else 'Invalid onset acquisition samples'
                            if invalid_sample
                            else 'reference changed'):
                        tap.run_tap(engine.Gcmd(), -.5, .9, onset_stop={})
                    result = None
                else:
                    result = tap.run_tap(engine.Gcmd(), -.5, .9,
                                         onset_stop={})
        collector.stop_collecting.assert_called_once()
        return tap, result

    def test_withdrawal_fit_is_optional_and_mcu_trigger_never_fabricated(self):
        tap, result = self.tap()
        self.assertEqual(result[0][2], 0.)
        self.assertTrue(result[1])
        status = tap.get_status(0.)
        self.assertIsNone(status['last_trigger_z'])
        self.assertIsNone(status['last_regression_z'])
        self.assertEqual(status['last_stop_reason'], 'onset')
        report = tap.get_contact_diagnostics()
        self.assertIsNone(report['mcu_trigger_z'])
        self.assertIsNone(report['trigger_time'])
        self.assertEqual(report['contact_stop_time'], 101.)
        self.assertIn('stop_region', report['phase_counts'])
        self.assertNotIn('trigger_region', report['phase_counts'])
        self.assertTrue(all(w['time_reference'] == 'descent_end_time' for w
            in report['window_sensitivity']['descent']['windows']))

    def test_sensor_errors_and_reference_changes_cannot_become_candidates(self):
        for options in ({'sensor_error': True}, {'bad_frame': True},
                        {'invalid_sample': True}):
            tap, result = self.tap(**options)
            self.assertIsNone(result)
            self.assertFalse(tap.get_status(0.)['is_last_tap_valid'])


if __name__ == '__main__':
    unittest.main()
