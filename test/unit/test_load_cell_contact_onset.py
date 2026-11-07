# Descent-onset mathematics and six original acquisitions; no hardware
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import copy
import json
import os
import random
import unittest
from unittest import mock

import test_auto_z_offset_diagnostic as commands
from test_auto_z_offset_cr import auto, ROOT

onset = auto.load_cell_contact_onset


def records():
    with open(os.path.join(ROOT, 'test', 'fixtures',
                           'load_cell_contact_six_20261102.json')) as source:
        return json.load(source)['records']


def signal(drift=1.7, correlated=False, contact=.08, compression=600.):
    rng = random.Random(701)
    rows, error = [], 0.
    for i in range(850):
        t = i*.012 + .0002*(i % 3)
        z = 5. - .5*t
        error = (.8*error if correlated else 0.) + rng.gauss(0., .6)
        force = .6 + drift*t + compression*max(contact-z, 0.) + error
        rows.append([1000. + t, force, z, 'descent', None, None])
    return {'quality': {'references_complete': True,
        'frame_state': 'consistent',
        'valid_time_bounds': True, 'bad_timestamps': 0, 'invalid_samples': 0,
        'expired_history_samples': 0, 'lookup_errors': 0},
        'motion': {'sensor_errors': 0}, 'samples': rows,
        'sample_count': len(rows), 'stride': 1,
        'trigger_time': rows[-1][0] + .005}


class OnsetMathTests(unittest.TestCase):
    def test_drifting_baseline_recovers_synthetic_contact(self):
        for drift in (-8., 0., 12.):
            with self.subTest(drift=drift):
                result = onset.estimate(signal(drift=drift))
                self.assertEqual(result['status'], 'identified', result)
                self.assertAlmostEqual(result['z'], .08, delta=.01)
                lo, hi = result['interval_z_mm']
                self.assertLessEqual(lo, .08)
                self.assertGreaterEqual(hi, .08)
                self.assertFalse(result['eligible_for_application'])
                self.assertFalse(result['physical_accuracy_validated'])
                self.assertIsNone(result['physical_uncertainty_mm'])

    def test_autocorrelation_reduces_observed_information(self):
        result = onset._fit_window(signal(correlated=True)['samples'])
        self.assertLess(result['effective_samples'], result['sample_count'])
        self.assertLess(result['effective_free_samples'],
                        result['free_samples'])
        self.assertGreater(result['baseline_rms_g'], 0.)

    def test_constant_and_drifting_free_signals_do_not_create_contact(self):
        self.assertEqual(onset.estimate(signal(compression=0.))['status'],
                         'unresolved')
        frozen = signal()
        for row in frozen['samples']:
            row[1] = 0.
        self.assertEqual(onset.estimate(frozen)['status'], 'unresolved')

    def test_coordinate_time_and_force_origin_invariance(self):
        original = signal()
        before = onset.estimate(original)
        shifted = copy.deepcopy(original)
        for row in shifted['samples']:
            row[0] += 100000.
            row[1] += 345.
            row[2] -= .43
        shifted['trigger_time'] += 100000.
        after = onset.estimate(shifted)
        self.assertEqual(before['status'], 'identified')
        self.assertEqual(after['status'], 'identified')
        self.assertAlmostEqual(after['z'], before['z'] - .43, places=5)

    def test_force_unit_rescaling_does_not_change_contact(self):
        original = signal()
        scaled = copy.deepcopy(original)
        for row in scaled['samples']:
            row[1] *= 1.4
        a, b = onset.estimate(original), onset.estimate(scaled)
        self.assertEqual(a['status'], b['status'])
        self.assertAlmostEqual(a['z'], b['z'], places=5)

    def test_invalid_coordinates_samples_and_missing_data_are_unresolved(self):
        for field, value in (('stride', 2), ('sample_count', 999),
                             ('trigger_time', None)):
            data = signal()
            data[field] = value
            self.assertEqual(onset.estimate(data)['status'], 'unresolved')
        for index, column, value in ((20, 0, 0.), (20, 2, 10.),
                                     (20, 1, float('nan'))):
            data = signal()
            data['samples'][index][column] = value
            self.assertEqual(onset.estimate(data)['status'], 'unresolved')
        self.assertEqual(onset.estimate(None)['status'], 'unresolved')

    def test_saturated_or_errored_acquisition_cannot_yield_estimate(self):
        data = signal()
        data['motion']['sensor_errors'] = 1
        self.assertEqual(onset.estimate(data)['status'], 'unresolved')
        data['motion']['sensor_errors'] = 0
        data['quality']['frame_state'] = 'changed'
        self.assertEqual(onset.estimate(data)['status'], 'unresolved')

    def test_estimator_does_not_mutate_acquisition(self):
        data = signal()
        before = copy.deepcopy(data)
        onset.estimate(data)
        self.assertEqual(before, data)

    def test_no_loaded_or_free_position_span_is_unresolved(self):
        for z in (0., 1.):
            data = signal()
            for row in data['samples']:
                row[2] = z
            self.assertEqual(onset.estimate(data)['status'], 'unresolved')


class SixRealTraceTests(unittest.TestCase):
    def test_six_real_curves_report_evidence_without_forced_offset(self):
        for record in records():
            with self.subTest(record=record['id']):
                before = copy.deepcopy(record['diagnostic'])
                result = onset.estimate(record['diagnostic'])
                self.assertEqual(before, record['diagnostic'])
                self.assertFalse(result['eligible_for_application'])
                self.assertEqual(len(result['windows']), 5)
                if record['id'].startswith('cold'):
                    self.assertEqual(result['status'], 'identified')
                    self.assertGreater(result['compression_slope_g_per_mm'], 0.)
                    self.assertGreater(result['baseline_rms_g'], 0.)
                    self.assertGreater(result['delta_bic'], 0.)
                else:
                    self.assertEqual(result['status'], 'unresolved')
                    self.assertIsNone(result['z'])
                    self.assertIn('window_models_disagree', result['reasons'])

    def test_withdrawal_and_working_offset_are_not_estimator_inputs(self):
        record = records()[0]
        before = onset.estimate(record['diagnostic'])
        data = copy.deepcopy(record['diagnostic'])
        data['ascent'] = {'status': 'unsupported_fit', 'z': None}
        data['ascent_fit_z'] = None
        data['regression_contact_z'] = -9.
        data['saved_offset'] = 999.
        # Existing coordinate-quality checks still apply; the withdrawal
        # model and any supposed printing target do not select an onset.
        self.assertEqual(before, onset.estimate(data))


class DiagnosticOnsetTests(unittest.TestCase):
    use_record = commands.DiagnosticCommandTests.use_record
    command = commands.DiagnosticCommandTests.command
    assert_not_applicable = (
        commands.DiagnosticCommandTests.assert_not_applicable)

    @classmethod
    def setUpClass(cls):
        cls.recorded = records()
        cls.rebuilt = [copy.deepcopy(r['diagnostic']) for r in cls.recorded]

    def setUp(self):
        commands.DiagnosticCommandTests.setUp(self)
        self.use_record(0)

    def test_withdrawal_disagreement_does_not_reject_supported_descent(self):
        self.command()
        self.assertEqual(self.module.state, 'diagnostic_complete')
        contact = self.module.last_contact
        self.assertEqual(contact['contact_qualification']['status'], 'rejected')
        self.assertEqual(contact['contact_onset']['status'], 'identified')
        self.assertAlmostEqual(contact['contact_onset']['observational_offset'],
            self.record['cr_test_z'] - contact['contact_onset']['z'])
        self.assertNotEqual(contact['contact_onset']['observational_offset'],
                            1.065)
        self.assert_not_applicable()

    def test_unresolved_hot_never_creates_an_offset(self):
        self.use_record(-1)
        self.command(COLD=0, BED_TEMP=60., NOZZLE_TEMP=170.)
        self.assertEqual(self.module.state, 'diagnostic_rejected')
        contact = self.module.last_contact['contact_onset']
        self.assertIsNone(contact['z'])
        self.assertIsNone(contact['observational_offset'])
        self.assert_not_applicable()

    def test_estimator_failure_is_reported_without_retry_or_application(self):
        with mock.patch.object(onset, 'estimate', side_effect=RuntimeError()):
            with self.assertLogs(level='WARNING'):
                self.command()
        self.assertEqual(self.module.state, 'diagnostic_rejected')
        self.assertEqual(self.module.last_contact['contact_onset']['reasons'],
                         ['estimator_error'])
        self.tap.tapping_move.run_tap.assert_called_once()
        self.assert_not_applicable()

    def test_original_mcu_safety_error_precedes_new_estimator(self):
        self.tap.tapping_move.run_tap.side_effect = RuntimeError('RAW_RANGE')
        with mock.patch.object(onset, 'estimate') as estimator:
            with self.assertRaisesRegex(RuntimeError, 'RAW_RANGE'):
                self.command()
        estimator.assert_not_called()
        self.assert_not_applicable()

    def test_normal_workflow_uses_supported_onset_despite_withdrawal_gap(self):
        with mock.patch.object(onset, 'estimate', wraps=onset.estimate) as fit:
            self.module.cmd_AUTO_Z_OFFSET(commands.Gcmd('AUTO_Z_OFFSET'))
        self.assertEqual(fit.call_count, 8)
        self.assertEqual(self.module.state, 'candidate')
        self.assertAlmostEqual(self.module.result['z_offset'],
            self.record['cr_test_z'] - onset.estimate(self.diagnostic)['z'])
        self.printer.objects['configfile'].set.assert_not_called()


if __name__ == '__main__':
    unittest.main()
