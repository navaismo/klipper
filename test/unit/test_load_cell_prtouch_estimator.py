# PRTouch reference fidelity and acquisition validation; no hardware
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import copy
import importlib.util
import json
import math
import os
import random
import types
import unittest

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), '..', '..'))
spec = importlib.util.spec_from_file_location('prtouch_replay', os.path.join(
    ROOT, 'klippy/extras/load_cell_prtouch_estimator.py'))
prtouch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prtouch)


def acquisition():
    # Synthetic large compression ramp exercises original ADC-unit rules.
    # These values are test inputs, never printer configuration advice.
    rows = []
    for i in range(160):
        counts = -400000 - 100 * max(0, i - 40)**2
        rows.append([10. + i / 80., 0., 5. - i * .001, 'descent',
                     counts, -400000])
    return {'samples': rows, 'stride': 1, 'sample_count': len(rows),
        'descent_end_time': rows[-1][0],
        'quality': {'references_complete': True, 'frame_state': 'consistent',
            'valid_time_bounds': True, 'bad_timestamps': 0,
            'invalid_samples': 0, 'expired_history_samples': 0,
            'lookup_errors': 0},
        'motion': {'sample_period': .0125, 'sensor_errors': 0,
                   'collector_start_time': 10.}}


class OriginalFidelityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, 'test/fixtures',
                               'prtouch_giorgiomaccari_reference.json')) as f:
            cls.reference = json.load(f)
        cls.namespace = {'math': math}
        for source in cls.reference['filters'].values():
            exec(source, cls.namespace)
        for source in cls.reference['prtouch'].values():
            exec(source, cls.namespace)

    def original_owner(self):
        owner = types.SimpleNamespace(
            cfg=types.SimpleNamespace(pi_count=32),
            val=types.SimpleNamespace(out_index=None),
            obj=types.SimpleNamespace(
                filter=types.SimpleNamespace(lft_k1_oft=.8),
                dirzctl=types.SimpleNamespace(get_params=lambda: ([], 0))),
            _pnt_tri_msg=lambda *args: None)
        return owner

    def test_three_filter_stages_match_original_exactly(self):
        rng = random.Random(20261103)
        for size in (1, 2, 16, 32, 64):
            values = [rng.uniform(-10000., 10000.) for i in range(size)]
            for name, args in (('RCTFilter', ()), ('RCHFilter', (5., 80.)),
                               ('RCLFilter', (.8,))):
                actual = getattr(prtouch, name)(*args).ftr_val(values)
                expected = self.namespace[name](*args).ftr_val(values)
                self.assertEqual(actual, expected)

    def test_per_channel_filter_pipeline_matches_original(self):
        config = types.SimpleNamespace(getfloat=lambda name, default,
                                        **kwargs: default)
        original = self.namespace['Filter'](config)
        rows = acquisition()['samples']
        for end in (31, 39, 55, 70, 100):
            vals = [r[4] - r[5] for r in rows[max(0, end-63):end+1]]
            expected = original.cal_filter_by_vals(1, [vals], 5., .8, 32)
            self.assertEqual(prtouch._filter(vals, .8), expected[1][0])

    def test_trigger_matches_original_signal_rules(self):
        rng = random.Random(23)
        cases = [[0.] * 32, [100.*i*i for i in range(32)],
                 [float(i) for i in range(32)], [60000.] * 32]
        cases.extend([rng.uniform(0., 60000.) for i in range(32)]
                     for unused in range(100))
        for vals in cases:
            owner = self.original_owner()
            original = self.namespace['_check_trigger'](
                owner, 0, vals, [], 3000, 50000)
            self.assertEqual(bool(prtouch._check_trigger(vals)), original)

    def test_rotation_index_and_z_match_original_cal_min_z(self):
        owner = self.original_owner()
        owner.cfg.pi_count = 32
        owner.val.re_probe_cnt = 0
        owner.pnt_msg = lambda *args: None
        owner.obj.printer = types.SimpleNamespace(command_error=RuntimeError)
        owner.obj.hx711s = types.SimpleNamespace(mcu_freq=80.,
            get_params=lambda: ([{'nt': i} for i in range(32)], 0))
        owner.obj.dirzctl = types.SimpleNamespace(mcu_freq=80., step_base=1,
            steppers=[types.SimpleNamespace(get_step_dist=lambda: .01)],
            get_params=lambda: ([{'tick': 0, 'step': 100},
                                 {'tick': 31, 'step': 70}], 0))
        owner._get_linear2 = lambda a, b, p, unused: [p[0], 0,
            a[2] + (b[2]-a[2]) * (p[0]-a[0]) / (b[0]-a[0])]
        vals = [float(max(0, i-12)**2) for i in range(32)]
        self.namespace['_cal_min_z'](owner, 5., vals)
        index = prtouch._contact_index(vals)
        self.assertEqual(index, owner.val.out_index)
        self.assertAlmostEqual(5. - index * .01, owner.val.out_val_mm)

    def test_original_end_of_travel_shortcut_is_not_imported(self):
        owner = self.original_owner()
        owner.obj.dirzctl.get_params = lambda: ([{}, {}], 0)
        self.assertTrue(self.namespace['_check_trigger'](
            owner, 0, [0.] * 32, [], 3000, 50000))
        self.assertIsNone(prtouch._check_trigger([0.] * 32))


class AcquisitionTests(unittest.TestCase):
    def test_identified_result_is_always_comparison_only(self):
        report = acquisition()
        before = copy.deepcopy(report)
        result = prtouch.estimate(report)
        self.assertEqual(result['status'], 'identified')
        self.assertFalse(result['eligible_for_application'])
        self.assertFalse(result['physical_accuracy_validated'])
        self.assertEqual(report, before)

    def test_selected_z_is_original_staircase_history_not_interpolation(self):
        report = acquisition()
        for i, row in enumerate(report['samples']):
            row[2] = 5. - .02 * (i // 15)
        result = prtouch.estimate(report)
        row = report['samples'][result['sample_index']]
        self.assertEqual((result['time'], result['z']), (row[0], row[2]))
        self.assertGreater(result['stationary_intervals_in_window'], 0)
        self.assertIn(result['z'], [r[2] for r in report['samples']])

    def test_force_scale_and_known_offset_cannot_select_contact(self):
        report = acquisition()
        expected = prtouch.estimate(report)
        for row in report['samples']:
            row[1] = 123456.
        report['saved_offset'] = 999.
        report['motion']['safety'] = {'counts_per_gram': 1.}
        self.assertEqual(expected, prtouch.estimate(report))

    def test_absolute_adc_translation_and_polarity_preserve_index(self):
        report = acquisition()
        expected = prtouch.estimate(report)
        for row in report['samples']:
            row[4], row[5] = -row[4] + 123, -row[5] + 123
        actual = prtouch.estimate(report)
        self.assertEqual(actual['sample_index'], expected['sample_index'])
        self.assertEqual(actual['z'], expected['z'])

    def test_no_trigger_at_endpoint_does_not_manufacture_contact(self):
        report = acquisition()
        for row in report['samples']:
            row[4] = row[5]
        result = prtouch.estimate(report)
        self.assertEqual(result['status'], 'not_evaluable')
        self.assertIsNone(result['z'])
        self.assertEqual(result['reasons'], [
            'original_signal_rules_not_met_before_acquisition_end'])

    def test_protective_max_hold_cannot_be_contact(self):
        report = acquisition()
        # Exponential changes make the filtered signal exceed max_hold for
        # three samples before a full trigger window is available.
        for i, row in enumerate(report['samples']):
            row[4] = -400000 - int(min(i, 31) * 200000)
        result = prtouch.estimate(report)
        self.assertEqual(result['status'], 'not_evaluable')
        self.assertIn('max_hold_is_protection_not_contact', result['reasons'])

    def test_invalid_sampling_and_references_are_not_evaluable(self):
        cases = []
        for period in (None, .1, .01, float('nan')):
            r = acquisition(); r['motion']['sample_period'] = period
            cases.append(r)
        r = acquisition(); r['stride'] = 2; cases.append(r)
        r = acquisition(); r['samples'][5][4] += .5; cases.append(r)
        r = acquisition(); r['samples'][5][4] = 8388608; cases.append(r)
        r = acquisition(); r['samples'][5][4] = None; cases.append(r)
        r = acquisition(); r['samples'][5][5] += 1; cases.append(r)
        r = acquisition(); r['samples'][5][0] = r['samples'][4][0]
        cases.append(r)
        r = acquisition(); r['samples'][5][2] = 9.; cases.append(r)
        r = acquisition(); r['quality']['references_complete'] = False
        cases.append(r)
        r = acquisition(); r['motion']['sensor_errors'] = 1; cases.append(r)
        r = acquisition(); r['sample_count'] += 1; cases.append(r)
        r = acquisition(); r['samples'] = r['samples'][:20]
        r['sample_count'] = 20; cases.append(r)
        r = acquisition()
        for row in r['samples'][10:]:
            row[0] += .1
        cases.append(r)
        for report in cases:
            with self.subTest(report=cases.index(report)):
                result = prtouch.estimate(report)
                self.assertEqual(result['status'], 'not_evaluable')
                self.assertIsNone(result['z'])
                self.assertTrue(result['reasons'])

    def test_only_original_descent_samples_are_used(self):
        report = acquisition()
        expected = prtouch.estimate(report)
        report['samples'].append([20., 0., 10., 'ascent', -200000, 0])
        report['sample_count'] += 1
        self.assertEqual(expected, prtouch.estimate(report))

    def test_truncated_capture_needs_original_complete_rolling_buffer(self):
        report = acquisition()
        report['samples'] = report['samples'][45:80]
        report['sample_count'] = len(report['samples'])
        result = prtouch.estimate(report)
        self.assertEqual(result['status'], 'not_evaluable')
        self.assertEqual(result['buffer_initialization'],
                         'complete_rolling_buffer_required')
        self.assertEqual(result['reasons'], ['insufficient_original_samples'])

    def test_all_seven_real_continuous_acquisitions_keep_original_rules(self):
        total = 0
        for name in ('load_cell_contact_six_20261102.json',
                     'load_cell_contact_hot_20261103_1052.json'):
            with open(os.path.join(ROOT, 'test/fixtures', name)) as f:
                records = json.load(f)['records']
            for record in records:
                total += 1
                result = prtouch.estimate(record['diagnostic'])
                self.assertEqual(result['status'], 'not_evaluable')
                self.assertEqual(result['reasons'], [
                    'original_signal_rules_not_met_before_acquisition_end'])
                self.assertLess(result['maximum_filtered_counts'],
                                prtouch.MIN_HOLD)
        self.assertEqual(total, 7)


if __name__ == '__main__':
    unittest.main()
