# Independent estimator selection and non-application; offline mocks only
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import copy
import configparser
import unittest
from unittest import mock

import test_auto_z_offset_diagnostic as commands
from test_auto_z_offset_cr import auto, Config, Gcmd, Printer
import configfile


class EstimatorSelectionTests(unittest.TestCase):
    setUpClass = classmethod(
        commands.DiagnosticCommandTests.setUpClass.__func__)
    setUp = commands.DiagnosticCommandTests.setUp
    use_record = commands.DiagnosticCommandTests.use_record
    command = commands.DiagnosticCommandTests.command
    assert_not_applicable = (
        commands.DiagnosticCommandTests.assert_not_applicable)
    def prtouch_result(self, z=.15):
        return {'status': 'identified', 'z': z, 'reasons': [],
                'diagnostic_only': True, 'eligible_for_application': False}

    def test_default_and_invalid_option(self):
        self.assertEqual(self.module.contact_estimator, 'current')
        with mock.patch.object(auto.load_cell_probe, 'LoadCellTapHelper',
                               return_value=self.tap):
            with self.assertRaisesRegex(RuntimeError,
                                        'Invalid contact_estimator'):
                auto.ContactRegressionAutoZOffset(Config(self.printer,
                    dict(self.config, contact_estimator='hybrid')))
            module = auto.ContactRegressionAutoZOffset(Config(Printer(),
                dict(self.config, contact_estimator='prtouch')))
            self.assertEqual(module.contact_estimator, 'prtouch')

    def test_option_uses_native_klipper_config_choice_api(self):
        def native_choice(config, option, choices, default=None):
            parser = configparser.ConfigParser()
            parser.add_section('auto_z_offset_cr')
            if option in config.data:
                parser.set('auto_z_offset_cr', option, config.data[option])
            wrapper = configfile.ConfigWrapper(
                None, parser, {}, 'auto_z_offset_cr')
            return wrapper.getchoice(option, choices, default)
        with mock.patch.object(Config, 'getchoice', native_choice):
            with mock.patch.object(auto.load_cell_probe, 'LoadCellTapHelper',
                                   return_value=self.tap):
                for selection in (None, 'current', 'prtouch'):
                    data = dict(self.config)
                    if selection is not None:
                        data['contact_estimator'] = selection
                    module = auto.ContactRegressionAutoZOffset(
                        Config(Printer(), data))
                    self.assertEqual(module.contact_estimator,
                                     selection or 'current')

    def test_current_selection_and_bounds_ignore_comparison_result(self):
        with mock.patch.object(auto.load_cell_prtouch_estimator, 'estimate',
                               return_value=self.prtouch_result(-2.)):
            self.command()
        self.assertEqual(self.module.last_contact['contact_estimator'],
                         'descent_onset')
        self.assertAlmostEqual(self.module.last_contact['contact_z'],
                               self.module.last_contact['contact_onset']['z'])
        self.assertEqual(set(self.module.last_contact['estimator_comparison']),
                         {'lcbestfit', 'current', 'prtouch'})
        self.assert_not_applicable()

    def test_prtouch_diagnostic_selects_its_own_numerical_result(self):
        self.module.contact_estimator = 'prtouch'
        with mock.patch.object(auto.load_cell_prtouch_estimator, 'estimate',
                               return_value=self.prtouch_result()):
            self.command()
        c = self.module.last_contact
        self.assertEqual(c['contact_estimator'], 'prtouch')
        self.assertEqual(c['contact_z'], .15)
        self.assertAlmostEqual(c['prtouch']['observational_offset'],
                               self.record['cr_test_z'] - .15)
        self.assertFalse(c['onset_qualification']['eligible_for_candidate'])
        self.assertEqual(self.module.state, 'diagnostic_complete')
        self.assert_not_applicable()

    def test_missing_prtouch_data_never_falls_back_to_current(self):
        self.module.contact_estimator = 'prtouch'
        self.command()
        self.assertEqual(self.module.state, 'diagnostic_rejected')
        c = self.module.last_contact
        self.assertEqual(c['contact_onset']['status'], 'identified')
        self.assertEqual(c['prtouch']['status'], 'not_evaluable')
        self.assertIsNone(c['contact_z'])
        self.assert_not_applicable()

    def test_prtouch_result_remains_blocked_with_populated_candidate(self):
        fingerprint = self.module._fingerprint()
        point = {'point_index': 1, 'point': [28., 28.],
                 'offsets': [1.2] * 3, 'median': 1.2}
        self.module.contact_estimator = 'prtouch'
        self.module._report_result(Gcmd(), [point, copy.deepcopy(point)],
                                   fingerprint)
        self.assertEqual(self.module.state, 'comparison_complete')
        self.assertIsNone(self.module.candidate)
        self.assertAlmostEqual(self.module.result['z_offset'], 1.2)
        self.assertFalse(self.module.result['eligible_for_application'])
        # Defense in depth even if another caller corrupts candidate state,
        # or the configured selection changes after a comparison.
        for selected in ('prtouch', 'current'):
            self.module.contact_estimator = selected
            self.module.candidate = fingerprint
            for command in (self.module.cmd_APPLY, self.module.cmd_ACCEPT):
                with self.assertRaisesRegex(RuntimeError, 'No validated'):
                    command(Gcmd())
        self.printer.objects['configfile'].set.assert_not_called()
        self.assertIsNone(self.module.provisional)

    def test_comparison_exception_is_warning_for_current_selection(self):
        with self.assertLogs(level='WARNING'):
            with mock.patch.object(auto.load_cell_prtouch_estimator,
                                   'estimate',
                                   side_effect=ValueError('failure')):
                self.command()
        self.assertEqual(self.module.last_contact['prtouch']['reasons'],
                         ['estimator_error'])
        self.assertEqual(self.module.state, 'diagnostic_complete')
        self.assert_not_applicable()

    def test_native_stop_uses_acquired_rows_and_original_sensor_period(self):
        decision = copy.deepcopy(self.diagnostic)
        del decision['motion']['sample_period']
        self.diagnostic['contact_stop'] = {
            'reason': 'onset', 'decision_report': decision,
            'stop_qualification': {'qualified': True,
                                   'load_evidence': {'qualified': True}}}
        status = self.tap.tapping_move.get_status.return_value
        status['last_stop_z'] = 0.
        status['last_regression_z'] = self.diagnostic['regression_contact_z']
        with mock.patch.object(auto.load_cell_prtouch_estimator, 'estimate',
                               return_value=self.prtouch_result()) as estimate:
            self.command()
        self.assertIs(estimate.call_args.args[0], self.diagnostic)
        self.assertEqual(estimate.call_args.kwargs['sample_period'], .0125)
        self.assert_not_applicable()

    def test_both_estimators_receive_one_unsegmented_native_acquisition(self):
        with mock.patch.object(auto.load_cell_contact_onset, 'estimate',
                wraps=auto.load_cell_contact_onset.estimate) as current:
            with mock.patch.object(auto.load_cell_prtouch_estimator,
                    'estimate', wraps=auto.load_cell_prtouch_estimator.estimate
                    ) as prtouch:
                self.command()
        self.tap.tapping_move.run_tap.assert_called_once()
        self.assertNotIn('onset_stop',
                         self.tap.tapping_move.run_tap.call_args.kwargs)
        current.assert_called_once()
        prtouch.assert_called_once()
        self.assertIs(current.call_args.args[0], self.diagnostic)
        self.assertIs(prtouch.call_args.args[0], self.diagnostic)
        self.assertEqual(prtouch.call_args.kwargs['sample_period'], .0125)
        comparison = self.module.last_contact['estimator_comparison']
        self.assertEqual(comparison['lcbestfit']['status'], 'identified')
        self.assertEqual(comparison['lcbestfit']['reasons'], [])
        self.assertEqual(comparison['prtouch']['parameters'][
            'acquisition_hz'], 80.)
        self.assertEqual(comparison['prtouch']['parameters'][
            'min_hold_counts'], 3000)
        self.assertFalse(comparison['prtouch']['eligible_for_application'])
        self.assert_not_applicable()

    def test_console_reports_each_independent_status_and_exact_rejection(self):
        self.module.contact_estimator = 'prtouch'
        unresolved = {'status': 'unresolved', 'z': None,
                      'reasons': ['window_models_disagree']}
        rejected = dict(self.prtouch_result(), status='not_evaluable',
            z=None, reasons=[
                'original_signal_rules_not_met_before_acquisition_end'])
        with mock.patch.object(auto.load_cell_contact_onset, 'estimate',
                               return_value=unresolved):
            with mock.patch.object(auto.load_cell_prtouch_estimator,
                                   'estimate', return_value=rejected):
                self.command()
        messages = '\n'.join(c.args[0] for c in
                             self.gcmd.respond_info.call_args_list)
        for expected in ('LCBestFit status=identified',
                'Current onset status=unresolved', 'window_models_disagree',
                'PRTouch status=not_evaluable',
                'original_signal_rules_not_met_before_acquisition_end'):
            self.assertIn(expected, messages)
        self.assertEqual(self.module.state, 'diagnostic_rejected')
        self.assert_not_applicable()


class FullWorkflowSelectionTests(unittest.TestCase):
    def setUp(self):
        self.h = commands.NativeWorkflowTests()
        self.h.setUpClass()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)

    def test_current_full_workflow_and_apply_are_unchanged(self):
        move = mock.Mock()
        move.get_status.return_value = {'homing_origin': (0., 0., .01, 0.)}
        self.h.printer.objects['gcode_move'] = move
        self.h.automatic(BED_TEMP=60., NOZZLE_TEMP=170.)
        self.assertEqual(self.h.module.state, 'candidate')
        self.assertEqual(self.h.tap.tapping_move.run_tap.call_count, 8)
        self.h.module.cmd_APPLY(Gcmd())
        self.assertEqual(self.h.module.state, 'provisional')
        self.h.module.cmd_ROLLBACK(Gcmd())
        self.h.assert_preserved()

    def test_full_prtouch_comparison_has_eight_pairs_and_no_application(self):
        self.h.automatic(BED_TEMP=60., NOZZLE_TEMP=170.)
        original_calls = self.h.tap.tapping_move.run_tap.call_args_list
        original_bounds = [(c.kwargs['minimum_z'], c.kwargs['maximum_descent'])
                           for c in original_calls]
        self.setUp()
        self.h.module.contact_estimator = 'prtouch'
        with mock.patch.object(auto.load_cell_prtouch_estimator, 'estimate',
                side_effect=lambda *a, **kw: {'status': 'identified',
                    'z': .15, 'reasons': [], 'diagnostic_only': True,
                    'eligible_for_application': False}):
            self.h.automatic(BED_TEMP=60., NOZZLE_TEMP=170.)
        module = self.h.module
        self.assertEqual(module.state, 'comparison_complete')
        self.assertIsNone(module.candidate)
        self.assertEqual(self.h.tap.tapping_move.run_tap.call_count, 8)
        compared_bounds = [(c.kwargs['minimum_z'],
                            c.kwargs['maximum_descent'])
            for c in self.h.tap.tapping_move.run_tap.call_args_list]
        self.assertEqual(compared_bounds, original_bounds)
        self.assertAlmostEqual(module.result['z_offset'],
                               self.h.record['cr_test_z'] - .15)
        for command in (module.cmd_APPLY, module.cmd_ACCEPT):
            with self.assertRaisesRegex(RuntimeError, 'No validated'):
                command(Gcmd())
        self.h.assert_preserved()

    def test_sensor_failure_precedes_prtouch_and_never_retries(self):
        self.h.module.contact_estimator = 'prtouch'
        self.h.tap.tapping_move.run_tap.side_effect = RuntimeError('RAW_RANGE')
        with mock.patch.object(auto.load_cell_prtouch_estimator,
                               'estimate') as estimate:
            with self.assertRaisesRegex(RuntimeError, 'RAW_RANGE'):
                self.h.automatic(BED_TEMP=60., NOZZLE_TEMP=170.)
            estimate.assert_not_called()
        self.h.tap.tapping_move.run_tap.assert_called_once()
        self.assertIsNone(self.h.module.candidate)
        self.h.assert_preserved()

    def test_prtouch_not_evaluable_aborts_normal_run_without_fallback(self):
        self.h.module.contact_estimator = 'prtouch'
        with self.assertRaisesRegex(RuntimeError, 'Unreliable contact PRTouch'):
            self.h.automatic(BED_TEMP=60., NOZZLE_TEMP=170.)
        self.assertEqual(self.h.tap.tapping_move.run_tap.call_count, 1)
        self.assertIsNone(self.h.module.candidate)
        self.assertIsNone(self.h.module.result)
        self.assertIsNotNone(self.h.module.last_contact)
        self.h.assert_preserved()


if __name__ == '__main__':
    unittest.main()
