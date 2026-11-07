# Unsaved Auto Z-offset print trials using native G-code coordinate mapping
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import copy
import importlib
import json
import os
import types
import unittest
from unittest import mock

import test_auto_z_offset_diagnostic as workflows
from test_auto_z_offset_cr import auto, Config, Gcmd, lcprobe, Printer, ROOT

native_move = importlib.import_module('extras.gcode_move')


class ProvisionalTests(unittest.TestCase):
    def setUp(self):
        self.printer = Printer()
        self.tap = mock.Mock()
        with mock.patch.object(lcprobe, 'LoadCellTapHelper',
                               return_value=self.tap):
            self.module = auto.ContactRegressionAutoZOffset(
                Config(self.printer))
        self.module._connect()
        # Exercise the real SET_GCODE_OFFSET and homing callbacks, never a
        # substitute implementation of their sign or persistence semantics.
        register = self.printer.gcode.register_command
        self.printer.gcode.register_command = (
            lambda name, handler, *args, **kwargs: register(name, handler))
        self.move = native_move.GCodeMove(Config(self.printer))
        self.printer.objects['gcode_move'] = self.move
        self.move.position_with_transform = self.printer.toolhead.get_position
        self.move.move_with_transform = mock.Mock()
        self.move.is_printer_ready = True
        self.move.reset_last_position()
        self.make_candidate(1.25)

    def make_candidate(self, value):
        self.module.result = {'valid': True, 'z_offset': value}
        self.module.candidate = self.module._fingerprint()
        self.module.state = 'candidate'
        self.module.last_contact = {'diagnostic_only': False}

    def origin(self):
        return self.move.get_status()['homing_origin'][2]

    def apply(self):
        self.module.cmd_APPLY(Gcmd('AUTO_Z_OFFSET_APPLY'))

    def rollback(self):
        self.module.cmd_ROLLBACK(Gcmd('AUTO_Z_OFFSET_ROLLBACK'))

    def assert_unchanged(self):
        self.assertEqual(self.printer.primary.offsets[2], 1.065)
        self.assertEqual(self.printer.cell.counts_per_gram, 100.)
        self.printer.objects['configfile'].set.assert_not_called()
        self.move.move_with_transform.assert_not_called()
        self.assertFalse(any(r[0] in ('move', 'heat', 'gcode', 'tap')
                             for r in self.printer.trace))

    def test_explicit_apply_consumes_candidate_and_never_moves_or_saves(self):
        self.assertEqual(self.origin(), 0.)
        self.apply()
        self.assertAlmostEqual(self.origin(), 1.065 - 1.25)
        self.assertEqual(self.module.state, 'provisional')
        self.assertIsNone(self.module.candidate)
        self.assertEqual(self.module.get_status(0.)['provisional']['z_offset'],
                         1.25)
        self.assert_unchanged()

    def test_replaces_prior_babystepping_and_restores_it_exactly(self):
        self.move.cmd_SET_GCODE_OFFSET(Gcmd(params={'Z': .037}))
        self.apply()
        self.assertAlmostEqual(self.origin(), -.185)
        self.move.cmd_SET_GCODE_OFFSET(Gcmd(params={'Z_ADJUST': -.02}))
        self.rollback()
        self.assertAlmostEqual(self.origin(), .037)
        self.assertIsNone(self.module.provisional)
        self.assertEqual(self.module.state, 'idle')
        self.assert_unchanged()

    def test_lower_measured_offset_has_positive_correction(self):
        self.make_candidate(.93)
        self.apply()
        self.assertAlmostEqual(self.origin(), .135)
        self.assert_unchanged()

    def test_native_homing_retains_correction_and_rollback_record(self):
        self.apply()
        self.printer.script('G28')
        self.assertIsNone(self.module.result)
        self.assertIsNotNone(self.module.provisional)
        self.assertAlmostEqual(self.origin(), -.185)
        self.assertAlmostEqual(self.move.base_position[2], -.185)
        self.move.cmd_G1(Gcmd(params={'Z': .2}))
        # A new saved offset of 1.25 and the old homing seed of 1.065 are
        # equivalent for subsequent print moves at this temporary correction.
        self.assertAlmostEqual(self.move.last_position[2] - 1.065, .2 - 1.25)
        self.rollback()
        self.assertAlmostEqual(self.origin(), 0.)

    def test_unknown_numerical_homing_seed_is_not_a_calibration_target(self):
        for seed in (0., .4, 1.065, 2.1):
            with self.subTest(seed=seed):
                self.printer.primary.offsets = (-24., -14.5, seed)
                self.make_candidate(1.25)
                self.apply()
                self.assertAlmostEqual(self.origin(), seed - 1.25)
                self.move._handle_homing_home_axes(
                    types.SimpleNamespace(get_axes=lambda: [2]))
                self.move.cmd_G1(Gcmd(params={'Z': .2}))
                self.assertAlmostEqual(self.move.last_position[2] - seed,
                                       .2 - 1.25)
                self.rollback()

    def test_repeated_apply_and_new_calibration_refused_without_motion(self):
        self.apply()
        with self.assertRaisesRegex(RuntimeError, 'already applied'):
            self.apply()
        with self.assertRaisesRegex(RuntimeError, 'ROLLBACK'):
            self.module.cmd_AUTO_Z_OFFSET(Gcmd())
        with self.assertRaisesRegex(RuntimeError, 'No validated'):
            self.module.cmd_ACCEPT(Gcmd())
        self.assert_unchanged()

    def test_invalid_diagnostic_or_missing_candidates_never_apply(self):
        for result in (None, {'valid': False, 'z_offset': 1.25},
                       {'valid': True, 'z_offset': None},
                       {'valid': True, 'z_offset': float('nan')},
                       {'valid': True, 'z_offset': -1.},
                       {'valid': True, 'z_offset': 6.}):
            with self.subTest(result=result):
                self.make_candidate(1.25)
                self.module.result = result
                with self.assertRaisesRegex(RuntimeError, 'No validated'):
                    self.apply()
        self.make_candidate(1.25)
        self.module.last_contact = {'diagnostic_only': True}
        with self.assertRaisesRegex(RuntimeError, 'No validated'):
            self.apply()
        self.assertEqual(self.origin(), 0.)
        self.assert_unchanged()

    def test_stale_reference_or_pending_offset_never_applies(self):
        self.printer.cell.counts_per_gram = 99.
        with self.assertRaisesRegex(RuntimeError, 'stale'):
            self.apply()
        self.assertEqual(self.origin(), 0.)
        self.make_candidate(1.25)
        self.printer.pending['bltouch'] = {'z_offset': '1.3'}
        with self.assertRaisesRegex(RuntimeError, 'pending'):
            self.apply()
        self.assertEqual(self.origin(), 0.)

    def test_busy_printing_paused_and_shutdown_cannot_apply_or_rollback(self):
        for mode in ('busy', 'printing', 'paused', 'shutdown'):
            with self.subTest(mode=mode):
                if mode == 'busy':
                    self.module.busy = True
                elif mode == 'printing':
                    self.printer.stats.get_status.return_value = {
                        'state': 'printing'}
                elif mode == 'paused':
                    self.printer.pause_resume.is_paused = True
                else:
                    self.printer.shutdown = True
                with self.assertRaises(RuntimeError):
                    self.apply()
                self.module.provisional = {'previous_gcode_z_offset': 0.}
                with self.assertRaises(RuntimeError):
                    self.rollback()
                self.module.provisional = None
                self.module.busy = self.printer.shutdown = False
                self.printer.stats.get_status.return_value = {
                    'state': 'standby'}
                self.printer.pause_resume.is_paused = False
        self.assert_unchanged()

    def test_no_user_value_override_and_no_empty_rollback(self):
        for handler in (self.module.cmd_APPLY, self.module.cmd_ROLLBACK):
            with self.assertRaises(RuntimeError):
                handler(Gcmd(params={'Z': 1.065}))
        with self.assertRaisesRegex(RuntimeError, 'No provisional'):
            self.rollback()
        self.assert_unchanged()

    def test_apply_and_rollback_failures_preserve_recovery_state(self):
        with mock.patch.object(self.move, 'cmd_SET_GCODE_OFFSET',
                               side_effect=RuntimeError('native command')):
            with self.assertRaisesRegex(RuntimeError, 'native command'):
                self.apply()
        self.assertIsNone(self.module.provisional)
        self.assertIsNotNone(self.module.candidate)
        self.apply()
        with mock.patch.object(self.move, 'cmd_SET_GCODE_OFFSET',
                               side_effect=RuntimeError('native command')):
            with self.assertRaisesRegex(RuntimeError, 'native command'):
                self.rollback()
        self.assertIsNotNone(self.module.provisional)
        self.rollback()
        self.assert_unchanged()


class RealWorkflowApplicationTests(unittest.TestCase):
    def test_real_two_point_workflow_then_explicit_trial_and_rollback(self):
        h = workflows.NativeWorkflowTests()
        h.setUpClass()
        h.setUp()
        self.addCleanup(h.doCleanups)
        move = mock.Mock()
        move.get_status.return_value = {'homing_origin': (0., 0., 0., 0.)}
        h.printer.objects['gcode_move'] = move
        h.automatic(BED_TEMP=60., NOZZLE_TEMP=170.)
        self.assertEqual(h.tap.tapping_move.run_tap.call_count, 8)
        value = h.module.result['z_offset']
        self.assertAlmostEqual(value, h.record['cr_test_z']
            - auto.load_cell_contact_onset.estimate(h.diagnostic)['z'])
        move.cmd_SET_GCODE_OFFSET.assert_not_called()
        h.module.cmd_APPLY(Gcmd())
        cmd = move.cmd_SET_GCODE_OFFSET.call_args.args[0]
        self.assertAlmostEqual(cmd.params['Z'], 1.065 - value)
        self.assertEqual(cmd.params['MOVE'], 0)
        h.module.cmd_ROLLBACK(Gcmd())
        self.assertEqual(move.cmd_SET_GCODE_OFFSET.call_args.args[0].params,
                         {'Z': 0., 'MOVE': 0})
        h.assert_preserved()

    def test_unresolved_real_hot_traces_still_cannot_be_applied(self):
        with open(os.path.join(ROOT, 'test/fixtures',
                               'load_cell_contact_six_20261102.json')) as f:
            records = json.load(f)['records']
        for record in records[3:]:
            with self.subTest(record=record['id']):
                h = workflows.NativeWorkflowTests()
                h.setUpClass()
                h.setUp()
                self.addCleanup(h.doCleanups)
                h.primary.height = record['cr_test_z']
                h.tap.tapping_move.run_tap.side_effect = None
                z = record['diagnostic']['regression_contact_z']
                h.tap.tapping_move.run_tap.return_value = ([28., 28., z], True)
                h.tap.tapping_move.get_contact_diagnostics.return_value = (
                    copy.deepcopy(record['diagnostic']))
                with self.assertRaisesRegex(RuntimeError,
                                            'window_models_disagree'):
                    h.automatic(BED_TEMP=60., NOZZLE_TEMP=170.)
                h.tap.tapping_move.run_tap.assert_called_once()
                with self.assertRaisesRegex(RuntimeError, 'No validated'):
                    h.module.cmd_APPLY(Gcmd())
                h.assert_preserved()


if __name__ == '__main__':
    unittest.main()
