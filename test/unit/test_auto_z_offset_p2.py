# Same-run P1 reference for P2 travel; recorded failure and offline delivery
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import copy
import json
import os
import unittest
from unittest import mock

import test_auto_z_offset_diagnostic as commands
from test_auto_z_offset import auto, Gcmd, ROOT


class P2ReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, 'test/fixtures',
                     'auto_z_offset_p2_no_trigger_20261103.json')) as source:
            cls.evidence = json.load(source)

    def setUp(self):
        self.h = h = commands.NativeWorkflowTests()
        h.setUpClass()
        h.setUp()
        self.addCleanup(h.doCleanups)
        p1 = self.evidence['p1']
        record = {'id': 'p1_before_p2_failure',
            'cr_test_z': p1['contact']['cr_trigger_z'],
            'recorded_offset_mm': p1['contact']['model_offset'],
            'diagnostic': p1['diagnostic']}
        h.recorded = [record]
        h.rebuilt = [copy.deepcopy(record['diagnostic'])]
        h.use_record(0)
        self.p1 = p1['contact']
        self.p2 = self.evidence['p2']

    def reference(self):
        return {'cr_test_z': self.p1['cr_trigger_z'],
            'nozzle_contact_z': self.p1['contact_z'],
            'fingerprint': self.h.module._fingerprint()}

    def test_recorded_hx711_floor_exhaustion_with_increasing_load(self):
        d = self.p2['diagnostic']
        self.assertEqual(d['sample_count'], 928)
        self.assertEqual(d['stride'], 1)
        m = d['motion']
        self.assertEqual(m['frames']['before_probe']['position'],
                         [110., 110., 5.])
        self.assertAlmostEqual(m['target'][2], -.49833333333258256)
        self.assertAlmostEqual(m['frames']['after_probe']['position'][2],
                               -.4974999999990093)
        self.assertEqual(d['error'], 'No trigger on probe after full movement')
        self.assertEqual(m['sensor_errors'] if 'sensor_errors' in m else
                         self.p2['failure']['sensor_errors'], 0)
        peak = max(row[1] for row in d['samples'])
        self.assertAlmostEqual(peak, 47.31632197242562)
        self.assertLess(peak, m['safety']['trigger_force'])
        free = [r[1] for r in d['samples'] if -.4 < r[2] < -.3]
        loaded = [r[1] for r in d['samples'] if r[2] < -.49]
        self.assertLess(sum(free)/len(free), 5.)
        self.assertGreater(sum(loaded)/len(loaded), 35.)

    def test_exact_p2_floor_uses_measured_p1_and_same_allowance(self):
        cr = mock.Mock(**self.p2['bounds']['cr'])
        module = self.h.module
        previous, previous_travel = module._nozzle_bounds(cr, Gcmd())
        self.assertAlmostEqual(previous, -.49833333333258256)
        ref = self.reference()
        floor, travel = module._nozzle_bounds(cr, Gcmd(), ref)
        surface = module._nozzle_surface(cr, ref)
        self.assertAlmostEqual(surface, -.4122267557590335)
        self.assertAlmostEqual(floor, -.9122267557590335)
        self.assertAlmostEqual(travel, 5.912226755759034)
        self.assertAlmostEqual(surface - floor, .5)
        self.assertAlmostEqual(surface - previous, .086106577573549)
        self.assertAlmostEqual(previous - floor,
                               self.p1['offset'] - self.p1['saved_offset'])
        self.assertGreater(floor, -3.)
        self.assertGreater(travel, previous_travel)
        self.assertEqual(self.h.tap.tapping_move.run_tap.call_count, 0)

    def test_p1_estimate_and_full_sequence_preserved_without_apply(self):
        h = self.h
        # Mocked P2 delivery reuses P1's supported curve for control flow;
        # the recorded failed P2 is never used to manufacture a candidate.
        h.automatic(BED_TEMP=60., NOZZLE_TEMP=170.)
        self.assertEqual(h.module.state, 'candidate')
        self.assertAlmostEqual(h.module.result['z_offset'],
                               1.478893422426451)
        self.assertEqual(h.tap.tapping_move.run_tap.call_count, 8)
        for point in h.module.result['points']:
            for c in point['contacts']:
                bounds = c['contact_bounds']
                self.assertAlmostEqual(bounds['surface_reference_z']
                                       - bounds['minimum_z'], .5)
                self.assertIsNotNone(bounds['approach_reference'])
        self.assertIsNone(h.module._approach_reference)
        h.assert_preserved()

    def test_corrected_p2_no_trigger_aborts_without_retry_or_candidate(self):
        h = self.h
        native_session = h.primary.start_probe_session.side_effect
        def session(gcmd, descent_bounds=None):
            h.primary.height = (self.p2['bounds']['cr']['test_z']
                if h.printer.toolhead.position[0] == 134.
                else self.p1['cr_trigger_z'])
            return native_session(gcmd, descent_bounds)
        h.primary.start_probe_session.side_effect = session
        successful_tap = h.tap.tapping_move.run_tap.side_effect
        def tap(gcmd, minimum_z=None, maximum_descent=None, onset_stop=None):
            if h.printer.toolhead.position[:2] == [110., 110.]:
                self.assertAlmostEqual(minimum_z, -.9122267557590335)
                self.assertAlmostEqual(maximum_descent, 5.912226755759034)
                h.printer.toolhead.position[2] = minimum_z
                raise RuntimeError('No trigger on probe after full movement')
            return successful_tap(gcmd, minimum_z, maximum_descent)
        h.tap.tapping_move.run_tap.side_effect = tap
        with self.assertRaisesRegex(RuntimeError, 'No trigger'):
            h.automatic(BED_TEMP=60., NOZZLE_TEMP=170.)
        self.assertEqual(h.tap.tapping_move.run_tap.call_count, 2)
        self.assertIsNone(h.module.candidate)
        self.assertIsNone(h.module._approach_reference)
        self.assertEqual(h.printer.toolhead.position[2], 5.)
        h.assert_preserved()

    def test_native_z_and_finite_checks_remain_fail_closed(self):
        cr = mock.Mock(**self.p2['bounds']['cr'])
        for z in (-4., 7., float('nan'), float('inf')):
            ref = self.reference()
            ref['nozzle_contact_z'] = z
            with self.assertRaisesRegex(RuntimeError, 'no descent'):
                self.h.module._nozzle_bounds(cr, Gcmd(), ref)
        self.h.tap.tapping_move.run_tap.assert_not_called()

    def test_reference_not_reused_from_previous_run(self):
        self.h.module._approach_reference = {'fingerprint': ('stale',)}
        self.h.automatic()
        self.assertEqual(self.h.module.state, 'candidate')
        self.assertIsNone(self.h.module._approach_reference)
        self.h.assert_preserved()


class P1EndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, 'test/fixtures',
                         'auto_z_offset_p1_endpoints_20261103.json')) as f:
            cls.records = json.load(f)['records']

    def setUp(self):
        self.h = commands.NativeWorkflowTests()
        self.h.setUpClass()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.h.module._check_geometry()

    def test_real_first_and_repeat_failures_exhaust_floor_below_threshold(self):
        self.assertEqual([r['frame']['begin']['stage'] for r in self.records],
            ['Test P1 pair 1', 'Calibrate P1 pair 2',
             'Test P1 pair 1', 'Test P1 pair 1'])
        for record in self.records:
            d = record['diagnostic']
            summary = record['summary']
            self.assertEqual(summary['start_z'], 5.)
            self.assertLess(abs(summary['end_z'] - summary['target_z']), .001)
            if d['error'] is not None:
                self.assertIsNone(d['mcu_trigger_z'])
                self.assertLess(summary['maximum_recorded_force_g'], 75.)
                self.assertLess(
                    summary['maximum_before_first_endpoint_sample_g'], 75.)
            else:
                self.assertAlmostEqual(d['mcu_trigger_z'],
                                       summary['target_z'])

    def test_repeated_p1_uses_same_run_reference_before_tap(self):
        h = self.h
        ref = dict(self.records[0]['contact_reference'],
                   fingerprint=h.module._fingerprint())
        h.module._approach_reference = ref
        h.primary.height = self.records[1]['frame']['bounds']['cr']['test_z']
        h.tap.tapping_move.run_tap.side_effect = RuntimeError('tap sentinel')
        with self.assertRaisesRegex(RuntimeError, 'tap sentinel'):
            h.module._measure_pair(Gcmd(), Gcmd(), [], 0, 1,
                                   h.module._fingerprint())
        bounds = h.tap.tapping_move.run_tap.call_args.kwargs
        self.assertAlmostEqual(bounds['minimum_z'], -.7904110535334705)
        self.assertAlmostEqual(bounds['maximum_descent'], 5.79041105353347)
        h.tap.tapping_move.run_tap.assert_called_once()
        h.assert_preserved()

    def test_first_p1_does_not_reuse_old_runs_or_invent_deeper_endpoint(self):
        h = self.h
        h.primary.height = self.records[-1]['frame']['bounds']['cr']['test_z']
        self.assertIsNone(h.module._approach_reference)
        h.tap.tapping_move.run_tap.side_effect = RuntimeError('tap sentinel')
        with self.assertRaisesRegex(RuntimeError, 'tap sentinel'):
            h.module._measure_pair(Gcmd(), Gcmd(), [], 0, 0,
                                   h.module._fingerprint())
        bounds = h.tap.tapping_move.run_tap.call_args.kwargs
        self.assertAlmostEqual(bounds['minimum_z'], -.3616666666664023)
        self.assertGreater(bounds['minimum_z'], -3.)
        h.assert_preserved()

    def test_bootstrap_seed_dependency_remains_explicit(self):
        module = self.h.module
        native = self.records[-1]['frame']['bounds']['cr']
        for seed in (0., .5, 1.065, 2.):
            shifted = native['test_z'] + seed - 1.065
            cr = auto.manual_probe.create_probe_result(
                [28., 28., shifted], (-24., -14.5, seed))
            floor, distance = module._nozzle_bounds(cr, Gcmd())
            self.assertAlmostEqual(cr.test_z - floor, seed + .5)
            self.assertAlmostEqual(floor, -.3616666666664023)


if __name__ == '__main__':
    unittest.main()
