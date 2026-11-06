# Real Auto Z-offset onset integration; recorded samples and offline mocks
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import copy
import json
import os
import unittest
from unittest import mock

import test_auto_z_offset_diagnostic as commands
from test_auto_z_offset import auto, Gcmd, ROOT


class RealOnsetIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = []
        for name in ('load_cell_contact_six_20261102.json',
                     'load_cell_contact_hot_20261103_1052.json'):
            with open(os.path.join(ROOT, 'test/fixtures', name)) as source:
                cls.records.extend(json.load(source)['records'])

    def harness(self, index):
        h = commands.NativeWorkflowTests()
        h.setUpClass()
        h.setUp()
        self.addCleanup(h.doCleanups)
        h.recorded = self.records
        h.rebuilt = [copy.deepcopy(r['diagnostic']) for r in self.records]
        h.use_record(index)
        return h

    def test_seven_acquisitions_use_onset_confidence_not_branch_gate(self):
        self.assertEqual(sum(r['diagnostic']['sample_count']
                             for r in self.records), 9025)
        for i, record in enumerate(self.records):
            with self.subTest(record=record['id']):
                h = self.harness(i)
                expected = auto.load_cell_contact_onset.estimate(h.diagnostic)
                if expected['status'] == 'identified':
                    # Replay one recorded curve at each mocked physical
                    # delivery to test full command sequencing, not spatial
                    # agreement on hardware for which no P2 data was taken.
                    h.automatic(BED_TEMP=60., NOZZLE_TEMP=170.)
                    self.assertEqual(h.module.state, 'candidate')
                    self.assertEqual(h.tap.tapping_move.run_tap.call_count, 8)
                    self.assertAlmostEqual(h.module.result['z_offset'],
                                           record['cr_test_z'] - expected['z'])
                    c = h.module.last_contact
                    self.assertFalse(c['contact_qualification'][
                        'eligible_for_candidate'])
                    self.assertTrue(c['onset_qualification'][
                        'eligible_for_candidate'])
                    self.assertEqual(c['contact_estimator'], 'descent_onset')
                    self.assertFalse(c['eligible_for_application'])
                    self.assertFalse(c['contact_onset'][
                        'physical_accuracy_validated'])
                else:
                    with self.assertRaisesRegex(RuntimeError,
                                                'window_models_disagree'):
                        h.automatic(BED_TEMP=60., NOZZLE_TEMP=170.)
                    self.assertEqual(h.tap.tapping_move.run_tap.call_count, 1)
                    self.assertIsNone(h.module.candidate)
                    self.assertIsNone(h.module.result)
                    self.assertIsNotNone(h.module.last_contact)
                    self.assertIsNone(h.module.last_contact['offset'])
                    with self.assertRaisesRegex(RuntimeError, 'No validated'):
                        h.module.cmd_APPLY(Gcmd())
                h.assert_preserved()

    def test_today_actual_onset_is_selected_without_tolerance_change(self):
        h = self.harness(-1)
        h.automatic(BED_TEMP=60., NOZZLE_TEMP=170.)
        c = h.module.last_contact
        self.assertAlmostEqual(c['contact_qualification'][
            'branch_disagreement_mm'], .050474498916054655)
        self.assertEqual(h.module.tolerance, .025)
        self.assertAlmostEqual(c['regression_z'], -.2560494615153615)
        self.assertAlmostEqual(c['descent_fit_z'], -.20557496259930685)
        self.assertAlmostEqual(c['contact_z'], -.2057133759585113)
        self.assertAlmostEqual(c['model_offset'], 1.449382794848957)
        self.assertAlmostEqual(h.module.result['z_offset'], 1.3990467092921068)
        self.assertAlmostEqual(c['contact_onset']['window_span_mm'],
                               .002327215042817632)
        self.assertNotAlmostEqual(h.module.result['z_offset'], 1.065)
        h.assert_preserved()

    def test_today_candidate_needs_explicit_apply_and_supports_rollback(self):
        h = self.harness(-1)
        move = mock.Mock()
        move.get_status.return_value = {'homing_origin': (0., 0., .01, 0.)}
        h.printer.objects['gcode_move'] = move
        h.automatic(BED_TEMP=60., NOZZLE_TEMP=170.)
        move.cmd_SET_GCODE_OFFSET.assert_not_called()
        h.module.cmd_APPLY(Gcmd())
        cmd = move.cmd_SET_GCODE_OFFSET.call_args.args[0]
        self.assertAlmostEqual(cmd.params['Z'], 1.065 - 1.3990467092921068)
        self.assertEqual(cmd.params['MOVE'], 0)
        h.module.cmd_ROLLBACK(Gcmd())
        self.assertEqual(move.cmd_SET_GCODE_OFFSET.call_args.args[0].params,
                         {'Z': .01, 'MOVE': 0})
        h.assert_preserved()

    def test_common_coordinate_translation_cancels_in_normal_calculation(self):
        reference = self.harness(-1)
        reference.automatic()
        offset = reference.module.result['z_offset']
        for shift in (-.03, .3):
            h = self.harness(-1)
            h.primary.offsets = (-24., -14.5, 1.065 + shift)
            h.primary.height += shift
            for row in h.diagnostic['samples']:
                row[2] += shift
            h.diagnostic['mcu_trigger_z'] += shift
            for key in ('descent_fit_z', 'ascent_fit_z'):
                h.diagnostic[key] += shift
            for branch in ('descent', 'ascent'):
                h.diagnostic[branch]['z'] += shift
            h.tap.tapping_move.get_status.return_value = {
                'last_trigger_z': h.diagnostic['mcu_trigger_z']}
            h.automatic()
            self.assertAlmostEqual(h.module.result['z_offset'], offset,
                                   places=5)
            self.assertEqual(h.printer.primary.offsets[2], 1.065 + shift)

    def test_estimator_error_rejects_even_if_legacy_agrees(self):
        h = self.harness(-1)
        # A supported withdrawal model cannot substitute for a failed onset.
        h.diagnostic['descent']['z'] = h.diagnostic['ascent']['z']
        with mock.patch.object(auto.load_cell_contact_onset, 'estimate',
                               side_effect=RuntimeError('onset error')):
            with self.assertLogs(level='WARNING'):
                with self.assertRaisesRegex(RuntimeError, 'estimator_error'):
                    h.automatic()
        h.tap.tapping_move.run_tap.assert_called_once()
        self.assertIsNone(h.module.candidate)
        self.assertIsNone(h.module.last_contact['offset'])
        h.assert_preserved()

    def test_out_of_range_onset_rejects_without_ascent_fallback(self):
        h = self.harness(-1)
        h.module.max_offset = 1.3
        with self.assertRaisesRegex(RuntimeError, 'offset_outside_valid_range'):
            h.automatic()
        h.tap.tapping_move.run_tap.assert_called_once()
        self.assertIsNone(h.module.candidate)
        h.assert_preserved()

    def test_mcu_safety_failure_precedes_estimator_and_never_retries(self):
        h = self.harness(-1)
        h.tap.tapping_move.run_tap.side_effect = RuntimeError('RAW_RANGE')
        with mock.patch.object(auto.load_cell_contact_onset, 'estimate') as fit:
            with self.assertRaisesRegex(RuntimeError, 'RAW_RANGE'):
                h.automatic()
        fit.assert_not_called()
        h.tap.tapping_move.run_tap.assert_called_once()
        self.assertIsNone(h.module.candidate)
        h.assert_preserved()

    def test_supported_diagnostic_still_cannot_apply(self):
        h = self.harness(-1)
        h.module.cmd_AUTO_Z_OFFSET(Gcmd(params={
            'DIAGNOSTIC': 1, 'BED_TEMP': 60., 'NOZZLE_TEMP': 170.}))
        self.assertEqual(h.module.state, 'diagnostic_complete')
        self.assertFalse(h.module.last_contact['onset_qualification'][
            'eligible_for_candidate'])
        self.assertIsNone(h.module.candidate)
        with self.assertRaisesRegex(RuntimeError, 'No validated'):
            h.module.cmd_APPLY(Gcmd())
        h.assert_preserved()


if __name__ == '__main__':
    unittest.main()
