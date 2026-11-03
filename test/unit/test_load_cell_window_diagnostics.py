# Diagnostic-only fitting-window replay and non-interference tests
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import copy
import json
import os
import unittest
from unittest import mock

import test_load_cell_contact as contacts
import test_load_cell_diagnostics as diagnostics
from test_auto_z_offset import auto, Gcmd, lcprobe, Printer, ROOT

diag = lcprobe.load_cell_diagnostics


def fixture(name):
    with open(os.path.join(ROOT, 'test', 'fixtures', name)) as source:
        return json.load(source)


def records():
    return (fixture('load_cell_contact_real.json')['records']
            + fixture('load_cell_contact_cold_20261102.json')['records'])


def report(record, fit=None):
    original = record['diagnostic']
    rows = original['samples']
    positions = {r[0]: r[2] for r in rows}
    positions[original['trigger_time']] = original['trigger_reconstructed_z']
    lookup = mock.Mock(side_effect=positions.__getitem__)
    if fit is None:
        fit = lcprobe.LCBestFit(Printer()).find_best_fit
    result = diag.build_report([[r[0], r[1], r[4], r[5]] for r in rows],
        original['motion'], lookup, fit, original['regression_contact_z'])
    return result, lookup


class RealWindowTests(unittest.TestCase):
    def test_all_36_fits_match_fixed_preintegration_reference(self):
        expected = fixture('load_cell_window_reference.json')['records']
        for record in records():
            before = copy.deepcopy(record)
            after, lookup = report(record)
            ws = after['window_sensitivity']
            with self.subTest(record=record['id']):
                self.assertEqual(ws['status'], 'available')
                self.assertTrue(ws['diagnostic_only'])
                self.assertFalse(ws['absolute_accuracy_validated'])
                self.assertEqual(ws['physical_contact_bias'], 'unknown')
                for branch in ('descent', 'ascent'):
                    actual = ws[branch]['windows']
                    reference = expected[record['id']][branch]
                    self.assertEqual(len(actual), len(reference))
                    for window, old in zip(actual, reference):
                        self.assertEqual(window['status'], 'identified')
                        self.assertEqual(window['window_s'], old['window_s'])
                        self.assertEqual(window['sample_count'],
                                         old['sample_count'])
                        self.assertAlmostEqual(window['z'], old['z'], 10)
                        self.assertAlmostEqual(window['force_slope_g_per_mm'],
                                               old['slope_g_per_mm'], 6)
                        self.assertAlmostEqual(window['residual_rms_g'],
                                               old['rms_g'], 8)
                    zs = [w['z'] for w in reference]
                    self.assertAlmostEqual(ws[branch]['max_z_variation_mm'],
                                           max(zs) - min(zs), 10)
                # Window diagnostics use already reconstructed rows only.
                self.assertEqual(lookup.call_count, len(after['samples']) + 1)
                self.assertEqual(before, record)

    def test_primary_positions_and_existing_verdicts_are_unchanged(self):
        for record in records():
            before = record['diagnostic']
            after, unused = report(record)
            for key in ('samples', 'descent', 'ascent', 'quality',
                        'ascent_minus_descent_fit_mm', 'regression_contact_z',
                        'force_trigger_z', 'trigger_time'):
                self.assertEqual(before[key], after[key])
            primary = before['regression_contact_z']
            qualify = auto.load_cell_contact.qualify_contact
            self.assertEqual(qualify(before, primary, .025),
                             qualify(after, primary, .025))
            self.assertEqual(
                after['window_sensitivity']['branch_difference_mm'],
                before['ascent_minus_descent_fit_mm'])

    def test_full_branch_results_are_reused(self):
        fit = mock.Mock(wraps=lcprobe.LCBestFit(Printer()).find_best_fit)
        after, unused = report(records()[-1], fit)
        self.assertEqual(after['window_sensitivity']['status'], 'available')
        # Two original branch fits plus seven extra windows, not eleven fits.
        self.assertEqual(fit.call_count, 9)

    def test_status_retains_windows_and_branch_verdict_even_when_rejected(self):
        harness = contacts.PairIntegrationTests(
            'test_consistent_pair_preserves_original_offset_formula')
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        record = records()[-1]
        record['diagnostic'], unused = report(record)
        with self.assertRaisesRegex(RuntimeError, 'contact_branches_disagree'):
            harness.measure(record)
        status = harness.module.last_contact
        self.assertEqual(status['window_sensitivity'],
                         record['diagnostic']['window_sensitivity'])
        self.assertEqual(status['contact_qualification']['status'], 'rejected')
        self.assertIsNone(status['offset'])

    def test_stable_biased_record_is_not_reported_as_physically_accurate(self):
        record = records()[2]
        after, unused = report(record)
        ws = after['window_sensitivity']
        self.assertLess(ws['descent']['max_z_variation_mm'], .002)
        self.assertFalse(ws['absolute_accuracy_validated'])
        self.assertEqual(ws['physical_contact_bias'], 'unknown')
        primary = after['regression_contact_z']
        self.assertTrue(auto.load_cell_contact.qualify_contact(
            after, primary, .025)['eligible_for_candidate'])
        self.assertAlmostEqual(record['cr_test_z'] - primary, 1.515)


class WindowFailureTests(unittest.TestCase):
    def test_optional_window_errors_do_not_change_existing_qualification(self):
        record = records()[-1]
        baseline, unused = report(record)
        with mock.patch.object(diag, '_window_sensitivity',
                               side_effect=RuntimeError('window failed')):
            with self.assertLogs(level='WARNING'):
                failed, unused = report(record)
        self.assertEqual(failed['window_sensitivity']['status'],
                         'diagnostic_error')
        primary = baseline['regression_contact_z']
        qualify = auto.load_cell_contact.qualify_contact
        self.assertEqual(qualify(baseline, primary, .025),
                         qualify(failed, primary, .025))
        for key in ('samples', 'descent', 'ascent', 'quality'):
            self.assertEqual(baseline[key], failed[key])

    def test_individual_fit_errors_are_local_to_their_windows(self):
        fit = lcprobe.LCBestFit(Printer()).find_best_fit
        calls = []
        def fail_extra_windows(data):
            calls.append(1)
            if len(calls) > 2:
                raise RuntimeError('optional fit failed')
            return fit(data)
        result, unused = report(records()[2], fail_extra_windows)
        self.assertTrue(auto.load_cell_contact.qualify_contact(
            result, result['regression_contact_z'], .025)
            ['eligible_for_candidate'])
        ws = result['window_sensitivity']
        for branch in ('descent', 'ascent'):
            self.assertEqual(ws[branch]['valid_window_count'], 1)
            self.assertIsNone(ws[branch]['max_z_variation_mm'])
            self.assertTrue(any(w['status'] == 'fit_error'
                                for w in ws[branch]['windows']))

    def test_missing_or_invalid_references_do_not_create_window_estimates(self):
        for broken in ('frame', 'time'):
            record = records()[-1]
            d = record['diagnostic']
            if broken == 'frame':
                del d['motion']['frames']['after_ascent']
            else:
                d['samples'][10][0] = d['samples'][9][0]
            result, unused = report(record)
            ws = result['window_sensitivity']
            self.assertNotEqual(ws['status'], 'available')
            self.assertIsNone(ws['descent']['max_z_variation_mm'])
            self.assertIsNone(ws['ascent']['max_z_variation_mm'])

    def test_short_capture_is_not_silently_reused_as_five_seconds(self):
        samples, motion, position = diagnostics.curve()
        motion['ascent_sample_time'] = .3
        result = diag.build_report(samples, motion, position,
            lcprobe.LCBestFit(Printer()).find_best_fit)
        branch = result['window_sensitivity']['ascent']
        self.assertEqual(branch['valid_window_count'], 0)
        self.assertIsNone(branch['max_z_variation_mm'])
        self.assertTrue(all(w['status'] == 'insufficient_window_coverage'
                            for w in branch['windows']))

    def test_duplicate_windows_do_not_imply_zero_sensitivity(self):
        window = {'status': 'identified', 'z': .1,
                  'data_time_bounds_s': [1., 2.]}
        result = diag._window_summary([window, window])
        self.assertEqual(result['distinct_window_count'], 1)
        self.assertIsNone(result['max_z_variation_mm'])

    def test_analysis_sample_count_is_bounded(self):
        rows = []
        for i in range(6000):
            z = .4 - i * .0002
            rows.append([i * .001, 1000. * max(-z, 0.), z])
        result = diag._window_fit(rows,
            lcprobe.LCBestFit(Printer()).find_best_fit, descending=True)
        self.assertEqual(result['status'], 'identified')
        self.assertLessEqual(result['analyzed_count'], diag.MAX_SAMPLES)
        self.assertAlmostEqual(result['z'], 0., 8)

    def test_window_failure_preserves_existing_tap_result_and_motion(self):
        harness = diagnostics.IntegrationTests(
            'test_logging_reuses_collector_and_preserves_motion_and_result')
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        normal, original_moves, unused = harness.tap(False)
        frame = diagnostics.curve()[1]['frames']['before_probe']
        with mock.patch.object(diag, '_window_sensitivity',
                               side_effect=RuntimeError('window failed')):
            with mock.patch.object(diag, 'snapshot',
                    side_effect=lambda *args: copy.deepcopy(frame)):
                with self.assertLogs(level='WARNING'):
                    actual, moves, tap = harness.tap(True)
        self.assertEqual(actual, normal)
        self.assertEqual(moves, original_moves)
        self.assertEqual(tap.get_contact_diagnostics()['window_sensitivity']
                         ['status'], 'diagnostic_error')

    def test_summary_failure_cannot_replace_branch_rejection(self):
        harness = contacts.PairIntegrationTests(
            'test_consistent_pair_preserves_original_offset_formula')
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        record = records()[-1]
        record['diagnostic'], unused = report(record)
        harness.configure_record(record)
        gcmd = Gcmd()
        def respond(text):
            if text.startswith('Fit window sensitivity:'):
                raise RuntimeError('console unavailable')
        gcmd.respond_info.side_effect = respond
        with self.assertLogs(level='WARNING'):
            with self.assertRaisesRegex(RuntimeError,
                                        'contact_branches_disagree'):
                harness.module._measure_pair(gcmd, Gcmd(), [], 0, 0,
                                              harness.module._fingerprint())


if __name__ == '__main__':
    unittest.main()
