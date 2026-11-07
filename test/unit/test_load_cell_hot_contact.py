# Five real cold/hot traces: branch provenance and report regression tests
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import json
import math
import os
import unittest

import test_auto_z_offset_diagnostic as commands
import test_load_cell_window_diagnostics as windows
from test_auto_z_offset_cr import ROOT, lcprobe, Printer
from test_load_cell_contact import replay_module


def records():
    with open(os.path.join(ROOT, 'test', 'fixtures',
                           'load_cell_contact_cold_hot_20261102.json')) as f:
        return json.load(f)['records']


def branch_rows(record, descending):
    d = record['diagnostic']
    m = d['motion']
    if descending:
        return list(reversed([r for r in d['samples']
                              if r[0] <= d['trigger_time']]))
    start = m['ascent_start_time']
    end = min(m['ascent_end_time'], start + m['ascent_sample_time'])
    return [r for r in d['samples'] if start <= r[0] <= end]


def independent_sse(rows, contact):
    # Direct least squares, independent of the LCBestFit prefix-sum search.
    h = [min(r[2] - contact, 0.) for r in rows]
    f = [r[1] for r in rows]
    mh, mf = sum(h) / len(h), sum(f) / len(f)
    variance = sum((x - mh)**2 for x in h)
    if not variance:
        return float('inf')
    slope = sum((x - mh)*(y - mf) for x, y in zip(h, f)) / variance
    baseline = mf - slope * mh
    return sum((y - baseline - slope*x)**2 for x, y in zip(h, f))


class FiveTraceTests(unittest.TestCase):
    def test_replay_all_6423_original_samples_and_original_results(self):
        original = records()
        self.assertEqual([r['diagnostic']['sample_count'] for r in original],
                         [1270, 1266, 1272, 1305, 1310])
        for record in original:
            with self.subTest(record=record['id']):
                d = record['diagnostic']
                rebuilt, lookup = windows.report(record)
                for field in ('samples', 'descent', 'ascent', 'quality',
                              'regression_contact_z', 'sample_timing'):
                    self.assertEqual(rebuilt[field], d[field])
                self.assertEqual(lookup.call_count, d['sample_count'] + 1)
                result = replay_module.replay(record)
                self.assertEqual(result['replay_error_mm'], 0.)
                self.assertIsNone(result['qualified_offset_mm'])
                self.assertEqual(result['descent_fit_z'], d['descent_fit_z'])
                self.assertEqual(result['ascent_fit_z'], d['ascent_fit_z'])
                self.assertEqual(result['primary_fit_z'],
                                 d['regression_contact_z'])

    def test_raw_counts_conversion_and_real_time_branch_selection(self):
        for record in records():
            with self.subTest(record=record['id']):
                d = record['diagnostic']
                s = d['motion']['safety']
                rows = d['samples']
                self.assertEqual(d['stride'], 1)
                self.assertEqual(d['sample_count'], len(rows))
                self.assertEqual(s['counts_per_gram'], 114.88636)
                self.assertEqual(record['contact']['saved_offset'], 1.065)
                self.assertEqual(d['quality']['frame_state'], 'consistent')
                self.assertEqual(d['motion']['sensor_errors'], 0)
                for a, b in zip(rows, rows[1:]):
                    self.assertLess(a[0], b[0])
                for row in rows:
                    force = s['orientation'] * (row[4] - row[5]) / (
                        s['counts_per_gram'])
                    self.assertAlmostEqual(force, row[1], 10)
                descent = branch_rows(record, True)
                ascent = branch_rows(record, False)
                self.assertEqual(len(descent), d['descent']['sample_count'])
                self.assertEqual(len(ascent), d['ascent']['sample_count'])
                self.assertTrue(all(r[0] <= d['trigger_time']
                                    for r in descent))
                self.assertTrue(all(r[0] >= d['motion']['ascent_start_time']
                                    for r in ascent))
                self.assertTrue(all(b[2] >= a[2]
                    for selected in (descent, ascent)
                    for a, b in zip(selected, selected[1:])))

    def test_hot_failure_is_ascent_signal_and_nonflat_tail_not_descent(self):
        for record in records()[3:]:
            with self.subTest(record=record['id']):
                d = record['diagnostic']
                self.assertEqual(d['descent']['status'], 'identified')
                self.assertIsNotNone(d['descent_fit_z'])
                self.assertEqual(d['ascent']['status'], 'insufficient_signal')
                self.assertIsNone(d['ascent_fit_z'])
                up = branch_rows(record, False)
                a = d['ascent']
                amplitude = max(r[1] for r in up) - a['baseline_g']
                self.assertLessEqual(amplitude, 5.*a['free_noise_rms_g'])
                self.assertGreater(abs(a['free_slope_g_per_mm']),
                                   .2*abs(a['force_slope_g_per_mm']))
                self.assertGreaterEqual(a['free_count'], 3)
                self.assertGreaterEqual(a['loaded_count'], 3)
                self.assertLess(min(r[1] for r in up), -20.)
                self.assertGreater(up[-1][1] - min(r[1] for r in up), 30.)

    def test_cold_supported_branches_and_existing_rejection_do_not_change(self):
        for record in records()[:3]:
            r = replay_module.replay(record)
            self.assertEqual(r['descent_status'], 'identified')
            self.assertEqual(r['ascent_status'], 'identified')
            self.assertEqual(r['qualification']['reasons'],
                             ['contact_branches_disagree'])

    def test_lcbestfit_optimum_beats_actual_z_grid_on_all_branches(self):
        fit = lcprobe.LCBestFit(Printer()).find_best_fit
        for record in records():
            for down in (True, False):
                with self.subTest(record=record['id'], descent=down):
                    rows = branch_rows(record, down)
                    z = fit([(r[1], r[2]) for r in rows])[0]
                    actual = independent_sse(rows, z)
                    grid = min(independent_sse(rows, contact)
                               for contact in set(r[2] for r in rows[:-1]))
                    # Floating-point comparison only; no model criterion.
                    self.assertLessEqual(actual,
                                         grid + max(1., grid)*1.e-8)
                    self.assertTrue(math.isfinite(actual))


class FiveTraceConsoleTests(unittest.TestCase):
    use_record = commands.DiagnosticCommandTests.use_record
    command = commands.DiagnosticCommandTests.command
    messages = commands.DiagnosticCommandTests.messages
    assert_not_applicable = (
        commands.DiagnosticCommandTests.assert_not_applicable)

    @classmethod
    def setUpClass(cls):
        cls.recorded = records()
        cls.rebuilt = [windows.report(r)[0] for r in cls.recorded]

    def setUp(self):
        commands.DiagnosticCommandTests.setUp(self)

    def test_report_separates_each_validated_branch_from_primary(self):
        for index, record in enumerate(self.recorded):
            with self.subTest(record=record['id']):
                self.use_record(index)
                if index < 3:
                    self.command()
                else:
                    self.command(COLD=0, BED_TEMP=60., NOZZLE_TEMP=170.)
                d = self.diagnostic
                c = self.module.last_contact
                self.assertEqual(c['descent_fit_z'], d['descent_fit_z'])
                self.assertEqual(c['ascent_fit_z'], d['ascent_fit_z'])
                self.assertEqual(c['descent_status'], d['descent']['status'])
                self.assertEqual(c['ascent_status'], d['ascent']['status'])
                self.assertAlmostEqual(c['primary_fit_z'],
                                       d['regression_contact_z'])
                self.assertAlmostEqual(c['observational_offset'],
                                       record['recorded_offset_mm'])
                self.assertIn('descent_status=identified', self.messages())
                if index >= 3:
                    self.assertIn('ascent_fit_z=unavailable', self.messages())
                    self.assertIn('ascent_status=insufficient_signal',
                                  self.messages())
                    self.assertNotIn('descent_fit_z=unavailable',
                                     self.messages())
                    self.assertIn('unsupported_contact_branches',
                                  self.messages())
                self.assert_not_applicable()


if __name__ == '__main__':
    unittest.main()
