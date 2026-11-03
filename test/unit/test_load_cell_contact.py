# Real-curve replay and post-acquisition Auto Z-offset qualification tests
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import copy
import importlib.util
import json
import os
import unittest
from unittest import mock

from test_auto_z_offset import auto, Config, Gcmd, lcprobe, Printer, ROOT
from test_load_cell_diagnostics import curve

spec = importlib.util.spec_from_file_location('contact_replay', os.path.join(
    ROOT, 'scripts', 'replay_load_cell_contact.py'))
replay_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay_module)
qualify = auto.load_cell_contact.qualify_contact


def records():
    with open(os.path.join(ROOT, 'test', 'fixtures',
                           'load_cell_contact_real.json')) as source:
        return json.load(source)['records']


def consistent_report():
    samples, motion, position = curve(noise=.1)
    fit = lcprobe.LCBestFit(Printer()).find_best_fit
    return lcprobe.load_cell_diagnostics.build_report(
        samples, motion, position, fit, 0.)


class RealReplayTests(unittest.TestCase):
    def test_new_cold_contact_is_rejected_without_relaxing_tolerance(self):
        with open(os.path.join(ROOT, 'test', 'fixtures',
                    'load_cell_contact_cold_20261102.json')) as source:
            record = json.load(source)['records'][0]
        result = replay_module.replay(record)
        self.assertEqual(result['sample_count'], 1270)
        self.assertEqual(result['replay_error_mm'], 0.)
        self.assertAlmostEqual(result['descent_fit_z'], .07224360243889061)
        self.assertAlmostEqual(result['ascent_fit_z'], .10087207574946752)
        self.assertIsNone(result['qualified_offset_mm'])
        self.assertEqual(result['qualification']['reasons'],
                         ['contact_branches_disagree'])
        self.assertAlmostEqual(result['qualification']
            ['branch_disagreement_mm'], .028628473310576913)
        self.assertEqual(record['contact']['offset'], None)

    def test_cold_trigger_at_endpoint_preserves_original_clock_and_frames(self):
        with open(os.path.join(ROOT, 'test', 'fixtures',
                    'load_cell_contact_cold_20261102.json')) as source:
            record = json.load(source)['records'][0]
        report = record['diagnostic']
        first_crossing = next(r for r in report['samples']
                              if r[0] <= report['trigger_time'] and r[1] >= 75.)
        self.assertAlmostEqual(first_crossing[2], .01)
        self.assertAlmostEqual(report['trigger_time'] - first_crossing[0],
                               .019233930555856205)
        self.assertAlmostEqual(report['mcu_trigger_z'],
                               report['motion']['target'][2])
        zeros = [f['frame']['stepper_zero']['stepper_z']
                 for f in record['pair_frames']]
        self.assertLess(max(zeros) - min(zeros), 1.e-9)
        self.assertEqual(report['sample_count'], len(report['samples']))

    def test_all_original_offsets_and_branch_fits_reproduced(self):
        for record in records():
            with self.subTest(record=record['id']):
                result = replay_module.replay(record)
                self.assertEqual(result['replay_error_mm'], 0.)
                original = record['diagnostic']
                for field in ('descent_fit_z', 'ascent_fit_z', 'mcu_trigger_z'):
                    self.assertAlmostEqual(result[field], original[field], 10)
                self.assertEqual(result['sample_count'],
                                 original['sample_count'])

    def test_first_result_is_rejected_despite_nearness_to_printing_offset(self):
        result = replay_module.replay(records()[0])
        self.assertIsNone(result['qualified_offset_mm'])
        self.assertEqual(result['qualification']['reasons'],
                         ['contact_branches_disagree'])

    def test_second_result_is_rejected_without_replacing_estimator(self):
        result = replay_module.replay(records()[1])
        self.assertIsNone(result['qualified_offset_mm'])
        self.assertAlmostEqual(result['original_algorithm_offset_mm'],
                               1.5022287669669314)
        self.assertAlmostEqual(result['qualification']
            ['branch_disagreement_mm'], .05650359217930012)

    def test_third_result_is_only_internally_consistent(self):
        result = replay_module.replay(records()[2])
        self.assertEqual(result['qualification']['status'],
                         'internally_consistent')
        self.assertFalse(result['qualification']['physical_contact_verified'])
        self.assertIsNone(result['qualification']['physical_uncertainty_mm'])
        self.assertAlmostEqual(result['qualified_offset_mm'], 1.515)

    def test_complete_unchanged_adc_and_timestamp_records(self):
        self.assertEqual([r['diagnostic']['sample_count'] for r in records()],
                         [1279, 1337, 1342])
        for record in records():
            rows = record['diagnostic']['samples']
            self.assertEqual(record['diagnostic']['columns'],
                ['time', 'force_g', 'z', 'phase', 'counts', 'tare_counts'])
            self.assertTrue(all(b[0] > a[0] for a, b in zip(rows, rows[1:])))
            self.assertTrue(all(r[4] == int(r[4]) for r in rows))

    def test_saved_offset_is_not_a_qualification_target(self):
        for record in records():
            original = replay_module.replay(record)
            for frame in record['diagnostic']['motion']['frames'].values():
                frame['probe_offsets'][2] = 2.345
            result = replay_module.replay(record)
            self.assertEqual(original, result)

    def test_common_z_translation_cannot_be_detected_as_physical_bias(self):
        report = records()[2]['diagnostic']
        original = qualify(report, report['regression_contact_z'], .025)
        for branch in ('descent', 'ascent'):
            report[branch]['z'] += .4
        changed = qualify(report, report['regression_contact_z'] + .4, .025)
        self.assertEqual(original['status'], changed['status'])
        self.assertFalse(changed['physical_contact_verified'])
        self.assertAlmostEqual(original['model_span_mm'],
                               changed['model_span_mm'])

    def test_replay_rejects_decimation_and_duplicate_timestamps(self):
        for kind in ('stride', 'duplicate', 'missing'):
            record = records()[0]
            if kind == 'stride':
                record['diagnostic']['stride'] = 2
            elif kind == 'duplicate':
                record['diagnostic']['samples'][1][0] = (
                    record['diagnostic']['samples'][0][0])
            else:
                record['diagnostic']['samples'].pop()
            with self.assertRaises(ValueError):
                replay_module.replay(record)


class QualificationTests(unittest.TestCase):
    def test_new_tap_cannot_reuse_previous_diagnostic_report(self):
        tap = lcprobe.TappingMove.__new__(lcprobe.TappingMove)
        tap._last_diagnostic_report = consistent_report()
        tap._collector = None
        tap._run_tap = mock.Mock(return_value=([28., 28., 0.], True))
        tap.run_tap(Gcmd())
        self.assertIsNone(tap.get_contact_diagnostics())

    def test_good_synthetic_contact_keeps_original_primary_z(self):
        report = consistent_report()
        result = qualify(report, report['ascent_fit_z'], .025)
        self.assertTrue(result['eligible_for_candidate'])
        self.assertEqual(result['primary_fit_z'], report['ascent_fit_z'])

    def test_missing_or_failed_observer_cannot_qualify(self):
        for report in (None, {}, [], 'failed'):
            self.assertFalse(qualify(report, 0., .025)
                             ['eligible_for_candidate'])

    def test_invalid_tolerance_rejected(self):
        for tolerance in (0., -1., None, float('nan'), float('inf'), True):
            self.assertEqual(qualify(None, 0., tolerance)['reasons'],
                             ['invalid_tolerance'])

    def test_incomplete_references_and_timing_are_rejected(self):
        for key, value in (('references_complete', False),
                           ('frame_state', 'changed'),
                           ('bad_timestamps', 1), ('lookup_errors', 1),
                           ('valid_time_bounds', False)):
            report = consistent_report()
            report['quality'][key] = value
            self.assertFalse(qualify(report, report['ascent_fit_z'], .025)
                             ['eligible_for_candidate'])

    def test_sensor_error_and_missing_force_rise_are_rejected(self):
        report = consistent_report()
        report['motion']['sensor_errors'] = 1
        self.assertIn('acquisition_error', qualify(report, 0., .025)['reasons'])
        report = consistent_report()
        report['descent_threshold']['status'] = 'no_sustained_crossing'
        self.assertIn('no_sustained_descent_force_rise',
                      qualify(report, 0., .025)['reasons'])

    def test_nonfinite_or_inconsistent_primary_rejected(self):
        report = consistent_report()
        for primary in (float('nan'), float('inf'), None, True, 1.):
            self.assertFalse(qualify(report, primary, .025)
                             ['eligible_for_candidate'])

    def test_unsupported_branch_never_qualifies(self):
        for key in ('descent', 'ascent'):
            report = consistent_report()
            report[key]['status'] = 'unsupported_fit'
            self.assertIn('unsupported_contact_branches',
                          qualify(report, 0., .025)['reasons'])

    def test_budget_is_not_adjusted_to_historical_outcome(self):
        report = consistent_report()
        report['descent']['z'], report['ascent']['z'] = 0., .03125
        self.assertTrue(qualify(report, .03125, .03125)
                        ['eligible_for_candidate'])
        self.assertFalse(qualify(report, .03125, .03124)
                         ['eligible_for_candidate'])

    def test_qualification_does_not_mutate_acquisition(self):
        report = consistent_report()
        before = copy.deepcopy(report)
        qualify(report, 0., .025)
        self.assertEqual(before, report)


class PairIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.printer = Printer()
        self.tap = mock.Mock()
        self.tap.param_helper.get_probe_params.return_value = {
            'load_cell_retract_dist': .5}
        with mock.patch.object(lcprobe, 'LoadCellTapHelper',
                               return_value=self.tap):
            self.module = auto.AutoZOffset(Config(
                self.printer, {'probe_point': [28., 28.]}))
        self.module._connect()
        self.module._check_geometry()
        self.module._frame_event = mock.Mock()
        self.module._probe = mock.Mock()

    def configure_record(self, record):
        report = record['diagnostic']
        primary = report['regression_contact_z']
        self.module._probe.return_value = mock.Mock(
            test_z=record['cr_test_z'], bed_z=record['cr_test_z'] - 1.065)
        self.tap.tapping_move.run_tap.return_value = ([28., 28., primary], True)
        self.tap.tapping_move.get_status.return_value = {
            'last_trigger_z': report['mcu_trigger_z']}
        self.tap.tapping_move.get_contact_diagnostics.return_value = report

    def measure(self, record):
        self.configure_record(record)
        return self.module._measure_pair(Gcmd(), Gcmd(), [], 0, 0,
                                         self.module._fingerprint())

    def test_real_disagreement_refuses_pair_without_retry_or_save(self):
        for record in records()[:2]:
            self.tap.tapping_move.run_tap.reset_mock()
            with self.assertRaisesRegex(RuntimeError,
                                        'contact_branches_disagree'):
                self.measure(record)
            self.assertIsNone(self.module.last_contact['offset'])
            self.assertAlmostEqual(self.module.last_contact['model_offset'],
                                   record['recorded_offset_mm'])
            self.tap.tapping_move.run_tap.assert_called_once()
            self.assertIsNone(self.module.candidate)
            self.printer.objects['configfile'].set.assert_not_called()
            self.assertEqual(self.printer.primary.offsets[2], 1.065)

    def test_consistent_pair_preserves_original_offset_formula(self):
        record = records()[2]
        self.assertAlmostEqual(self.measure(record),
                               record['recorded_offset_mm'])
        self.assertFalse(self.module.last_contact['contact_qualification']
                         ['physical_contact_verified'])

    def test_missing_report_does_not_fall_back_to_ascent_only(self):
        self.tap.tapping_move.get_contact_diagnostics.return_value = None
        record = records()[2]
        with mock.patch.object(auto.load_cell_contact, 'qualify_contact',
                wraps=qualify) as check:
            # Configure the normal paired path, then suppress its observer.
            self.measure(record)
            self.tap.tapping_move.get_contact_diagnostics.return_value = None
            with self.assertRaisesRegex(RuntimeError, 'missing_contact_report'):
                self.module._measure_pair(Gcmd(), Gcmd(), [], 0, 0,
                                          self.module._fingerprint())
            self.assertIsNone(check.call_args.args[0])

    def test_primary_safety_error_wins_over_qualification(self):
        self.module._probe.return_value = mock.Mock(test_z=1.2, bed_z=.135)
        self.tap.tapping_move.run_tap.side_effect = RuntimeError(
            'original RAW_RANGE safety error')
        with self.assertRaisesRegex(RuntimeError, 'original RAW_RANGE'):
            self.module._measure_pair(Gcmd(), Gcmd(), [], 0, 0,
                                      self.module._fingerprint())
        self.tap.tapping_move.get_contact_diagnostics.assert_not_called()

    def test_workflow_retains_rejection_and_invalidates_qualification(self):
        self.configure_record(records()[1])
        with self.assertRaisesRegex(RuntimeError, 'contact_branches_disagree'):
            self.module.cmd_TEST_CONTACT(Gcmd(params={'COLD': 1}))
        status = self.module.get_status(0.)
        self.assertEqual(status['state'], 'error')
        self.assertIsNone(status['last_contact']['offset'])
        self.assertFalse(status['last_contact']['contact_qualification']
                         ['eligible_for_candidate'])
        self.assertFalse(status['contact_verified'])
        self.assertFalse(status['candidate'])
        self.assertFalse(self.module.busy)
        self.tap.tapping_move.run_tap.assert_called_once()
        self.printer.objects['configfile'].set.assert_not_called()


if __name__ == '__main__':
    unittest.main()
