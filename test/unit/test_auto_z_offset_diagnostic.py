# Single-pair Auto Z-offset diagnostic command; offline hardware mocks
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import copy
import json
import os
import unittest
from unittest import mock

import test_auto_z_offset as engine_harness
from test_auto_z_offset import auto, Config, Gcmd, lcprobe, Printer, ROOT
from test_load_cell_window_diagnostics import records, report


class DiagnosticCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recorded = records()
        cls.rebuilt = [report(record)[0] for record in cls.recorded]

    def setUp(self):
        self.printer = Printer()
        self.printer.toolhead.position = [28., 28., 5., 0.]
        self.printer.cell.counts_per_gram = 114.88636
        self.printer.cell.reference = -436490
        self.printer.cell.invert = -1.
        self.tap = mock.Mock()
        self.config = {
            'probe_point': None, 'probe_points': [[28., 28.], [110., 110.]],
            'sample_retract_dist': .5}
        self.tap.param_helper.get_probe_params.return_value = {
            'load_cell_retract_dist': .5}
        with mock.patch.object(lcprobe, 'LoadCellTapHelper',
                               return_value=self.tap):
            self.module = auto.AutoZOffset(Config(self.printer, self.config))
        self.module._connect()
        self.primary = self.printer.primary
        # Mock physical CR Touch delivery, keeping the actual session caller.
        original = self.primary.start_probe_session
        def session(gcmd, descent_bounds=None):
            self.assertIsNone(descent_bounds)
            value = original(gcmd)
            run = value.run_probe.side_effect
            def trigger(cmd):
                run(cmd)
                self.printer.toolhead.position[2] = self.primary.height
            value.run_probe.side_effect = trigger
            return value
        self.primary.start_probe_session = mock.Mock(side_effect=session)
        self.primary.check_probe_session_bounds = mock.Mock()
        self.module._frame_event = mock.Mock()
        self.use_record(-1)

    def use_record(self, index):
        self.record = self.recorded[index]
        self.diagnostic = copy.deepcopy(self.rebuilt[index])
        self.primary.height = self.record['cr_test_z']
        self.tap.tapping_move.get_contact_diagnostics.return_value = (
            self.diagnostic)
        self.tap.tapping_move.get_status.return_value = {
            'last_trigger_z': self.diagnostic['mcu_trigger_z']}
        def tap(gcmd, minimum_z=None, maximum_descent=None, onset_stop=None):
            floor = self.primary.height - self.primary.offsets[2] - .5
            ref = self.module._approach_reference
            if ref is not None:
                floor = (ref['nozzle_contact_z'] + self.primary.height
                         - ref['cr_test_z'] - .5)
            self.assertAlmostEqual(minimum_z, floor)
            self.assertAlmostEqual(maximum_descent, 5. - floor)
            self.assertEqual(gcmd.get_command_parameters(), {})
            self.printer.trace.append(('tap',
                self.printer.toolhead.get_position()))
            # Real LCBestFit on the original recorded ascent, not a fitted
            # constant chosen by this command or the printing-offset target.
            m = self.diagnostic['motion']
            start = m['ascent_start_time']
            end = min(m['ascent_end_time'], start + m['ascent_sample_time'])
            rows = [(r[1], r[2]) for r in self.diagnostic['samples']
                    if start <= r[0] <= end]
            z = lcprobe.LCBestFit(self.printer).find_best_fit(rows)[0]
            self.printer.toolhead.position[2] = .5
            return self.printer.toolhead.get_position()[:2] + [z], True
        self.tap.tapping_move.run_tap.side_effect = tap

    def command(self, **params):
        values = {'DIAGNOSTIC': 1, 'COLD': 1}
        values.update(params)
        self.gcmd = Gcmd('AUTO_Z_OFFSET', values)
        self.printer.gcode.commands['AUTO_Z_OFFSET'](self.gcmd)

    def messages(self):
        return '\n'.join(c.args[0]
                         for c in self.gcmd.respond_info.call_args_list)

    def assert_not_applicable(self):
        status = self.module.get_status(0.)
        self.assertFalse(status['candidate'])
        self.assertIsNone(status['result'])
        self.assertFalse(status['contact_verified'])
        self.assertEqual(status['verified_points'], [])
        self.assertFalse(self.module.busy)
        self.assertEqual(self.primary.offsets[2], 1.065)
        self.assertEqual(self.printer.cell.counts_per_gram, 114.88636)
        self.assertEqual(self.printer.cell.reference, -436490)
        self.printer.objects['configfile'].set.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, 'No validated'):
            self.module.cmd_ACCEPT(Gcmd())
        self.assertFalse(any(row[0] == 'gcode' for row in self.printer.trace))

    def test_legacy_branch_rejection_is_diagnostic_and_never_applies(self):
        self.command()
        self.assertEqual(self.module.state, 'diagnostic_complete')
        c = self.module.last_contact
        self.assertEqual(c['contact_qualification']['status'], 'rejected')
        self.assertEqual(c['contact_onset']['status'], 'identified')
        self.assertAlmostEqual(c['descent_fit_z'], .07224360243889061)
        self.assertAlmostEqual(c['ascent_fit_z'], .10087207574946752)
        self.assertAlmostEqual(c['observational_offset'], 1.2174612575841506)
        self.assertAlmostEqual(c['observational_offset'],
                               self.record['cr_test_z'] - c['ascent_fit_z'])
        self.assertIsNone(c['offset'])
        self.assertTrue(c['diagnostic_only'])
        self.assertFalse(c['eligible_for_application'])
        self.assertIn('contact_branches_disagree', self.messages())
        for name in ('cr_test_z', 'descent_fit_z', 'ascent_fit_z',
                     'observational_offset', 'branch_difference'):
            self.assertIn(name, self.messages())
        self.assertIn('no candidate, no apply or save', self.messages())
        self.assert_not_applicable()

    def test_only_one_pair_uses_native_probe_and_local_nozzle_bound(self):
        self.command()
        self.primary.check_probe_session_bounds.assert_not_called()
        self.assertEqual(self.primary.start_probe_session.call_args.kwargs, {})
        bound = self.module.last_contact['contact_bounds']
        self.assertAlmostEqual(bound['minimum_z'],
                               self.record['cr_test_z'] - 1.065 - .5)
        self.assertAlmostEqual(bound['maximum_descent'],
                               5. - bound['minimum_z'])
        self.primary.start_probe_session.assert_called_once()
        self.primary.sessions[0].run_probe.assert_called_once()
        self.primary.sessions[0].end_probe_session.assert_called_once()
        self.tap.tapping_move.run_tap.assert_called_once()
        cr = [row[1] for row in self.printer.trace if row[0] == 'crtouch']
        taps = [row[1] for row in self.printer.trace if row[0] == 'tap']
        self.assertEqual(cr[0][:3], [52., 42.5, 5.])
        self.assertEqual(taps[0][:3], [28., 28., 5.])
        self.assertEqual(self.printer.toolhead.position[2], 5.)
        self.assert_not_applicable()

    def test_internally_consistent_real_fit_still_cannot_be_accepted(self):
        self.use_record(2)
        self.command()
        self.assertEqual(self.module.state, 'diagnostic_complete')
        self.assertTrue(self.module.last_contact['contact_qualification'][
            'eligible_for_candidate'])
        self.assertIsNone(self.module.last_contact['offset'])
        self.assertFalse(self.module.last_contact['eligible_for_application'])
        self.assert_not_applicable()

    def test_all_four_original_recorded_models_preserve_calculation(self):
        for index, record in enumerate(self.recorded):
            with self.subTest(record=record['id']):
                self.use_record(index)
                self.command()
                self.assertAlmostEqual(
                    self.module.last_contact['observational_offset'],
                    record['recorded_offset_mm'])
                self.assert_not_applicable()

    def test_accept_defense_even_with_accidentally_populated_candidate(self):
        self.use_record(2)
        self.command()
        self.module.candidate = self.module._fingerprint()
        self.module.result = {'z_offset': 1.515}
        with self.assertRaisesRegex(RuntimeError, 'No validated'):
            self.module.cmd_ACCEPT(Gcmd())
        self.printer.objects['configfile'].set.assert_not_called()

    def test_previous_candidate_and_point_qualifications_are_cleared(self):
        self.module.candidate = self.module._fingerprint()
        self.module.result = {'z_offset': 1.5}
        self.module.verified_points = {0: self.module.candidate,
                                       1: self.module.candidate}
        self.module.contact_verified = True
        self.command()
        self.assert_not_applicable()

    def test_invalid_diagnostic_switch_is_refused(self):
        with self.assertRaises(RuntimeError):
            self.module.cmd_AUTO_Z_OFFSET(Gcmd(params={'DIAGNOSTIC': 2}))
        self.assertEqual(self.printer.trace, [])

    def test_no_p2_or_force_floor_override(self):
        for params in ({'POINT': 2}, {'MINIMUM_Z': -3},
                       {'TRIGGER_FORCE': 1}, {'SAMPLES': 3}):
            with self.assertRaises(RuntimeError):
                self.command(**params)
            self.assertEqual(self.printer.trace, [])
        self.tap.tapping_move.run_tap.assert_not_called()

    def test_retired_options_are_ignored_and_not_required(self):
        retired = ('test_minimum_z', 'test_max_descent',
                   'test_probe_minimum_z', 'test_probe_max_descent',
                   'test_probe_deploy_minimum_z', 'test_position_minimum_z',
                   'test_position_max_descent')
        config = dict(self.config, **dict.fromkeys(retired, 'ignored'))
        with mock.patch.object(lcprobe, 'LoadCellTapHelper',
                               return_value=self.tap):
            self.module = auto.AutoZOffset(Config(Printer(), config))
        # Constructor consumes obsolete keys without validating or using them.
        for name in retired:
            self.assertFalse(hasattr(self.module, name))

    def test_invalid_surface_or_retract_prevents_nozzle_descent(self):
        for retract in (0., -1., float('nan'), 10.):
            with self.subTest(retract=retract):
                self.tap.param_helper.get_probe_params.return_value = {
                    'load_cell_retract_dist': retract}
                with self.assertRaisesRegex(RuntimeError, 'no descent'):
                    self.command()
                self.tap.tapping_move.run_tap.assert_not_called()
        self.assert_not_applicable()

    def test_mcu_safety_errors_propagate_without_retry_or_result(self):
        for error in ('RAW_RANGE', 'SENSOR_TIMEOUT', 'No trigger'):
            with self.subTest(error=error):
                self.tap.tapping_move.run_tap.reset_mock()
                self.tap.tapping_move.run_tap.side_effect = RuntimeError(error)
                with self.assertRaisesRegex(RuntimeError, error):
                    self.command()
                self.assertEqual(self.module.state, 'error')
                self.tap.tapping_move.run_tap.assert_called_once()
                self.assert_not_applicable()

    def test_cr_error_stops_before_tap_and_closes_session(self):
        self.primary.fail = True
        with self.assertRaisesRegex(RuntimeError, 'CR Touch error'):
            self.command()
        self.tap.tapping_move.run_tap.assert_not_called()
        self.primary.sessions[0].end_probe_session.assert_called_once()
        self.assert_not_applicable()

    def test_cold_sensor_fault_is_not_converted_to_model_rejection(self):
        self.printer.bed.temperature = float('nan')
        with self.assertRaisesRegex(RuntimeError, 'Cold contact requires'):
            self.command()
        self.tap.tapping_move.run_tap.assert_not_called()
        self.assertEqual(self.printer.trace, [])
        self.assert_not_applicable()

    def test_homing_invalidation_clears_completed_diagnostic(self):
        self.command()
        self.module._invalidate()
        self.assertEqual(self.module.state, 'idle')
        self.assertIsNone(self.module.last_contact)
        self.assert_not_applicable()

    def test_nonfinite_contact_is_not_swallowed_as_model_rejection(self):
        self.tap.tapping_move.run_tap.side_effect = None
        self.tap.tapping_move.run_tap.return_value = ([28., 28., float('nan')],
                                                     True)
        with self.assertRaisesRegex(RuntimeError, 'Invalid nozzle contact'):
            self.command()
        self.assert_not_applicable()

    def test_cold_mode_keeps_heaters_off_and_rejects_temperature_params(self):
        with self.assertRaisesRegex(RuntimeError, 'without temperature'):
            self.command(BED_TEMP=50.)
        self.assertEqual(self.printer.trace, [])
        self.command()
        self.assertFalse(any(row[0] == 'heat' for row in self.printer.trace))
        self.assertEqual(self.printer.bed.target, 0.)
        self.assertEqual(self.printer.nozzle.target, 0.)

    def test_hot_mode_uses_existing_heat_and_never_qualifies_points(self):
        self.command(COLD=0, BED_TEMP=50., NOZZLE_TEMP=170.)
        self.assertTrue(any(row[0] == 'heat' for row in self.printer.trace))
        self.assertEqual(self.printer.bed.target, 0.)
        self.assertEqual(self.printer.nozzle.target, 0.)
        self.assert_not_applicable()

    def test_unhomed_or_active_mesh_has_no_automatic_preparation(self):
        self.printer.toolhead.homed = ''
        with self.assertRaisesRegex(RuntimeError, 'Home XYZ'):
            self.command()
        self.assertEqual(self.printer.trace, [])
        self.printer.toolhead.homed = 'xyz'
        mesh = mock.Mock()
        mesh.get_mesh.return_value = object()
        self.printer.objects['bed_mesh'] = mesh
        with self.assertRaisesRegex(RuntimeError, 'BED_MESH_CLEAR'):
            self.command()
        self.assertEqual(self.printer.trace, [])

    def test_post_tap_cleanup_failure_remains_an_error(self):
        original = self.module._raise_z
        calls = []
        def retract():
            calls.append(True)
            if len(calls) == 3:
                raise RuntimeError('retraction failed')
            original()
        self.module._raise_z = retract
        with self.assertLogs(level='ERROR'):
            with self.assertRaisesRegex(RuntimeError, 'retraction failed'):
                self.command()
        self.assertEqual(self.module.state, 'error')
        self.assert_not_applicable()


class NativeWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, 'test/fixtures',
                               'load_cell_contact_six_20261102.json')) as f:
            cls.recorded = json.load(f)['records']
        cls.rebuilt = [copy.deepcopy(r['diagnostic']) for r in cls.recorded]

    use_record = DiagnosticCommandTests.use_record

    def setUp(self):
        DiagnosticCommandTests.setUp(self)
        self.use_record(0)

    def automatic(self, **params):
        self.gcmd = Gcmd('AUTO_Z_OFFSET', params)
        self.module.cmd_AUTO_Z_OFFSET(self.gcmd)

    def assert_preserved(self):
        self.assertEqual(self.primary.offsets[2], 1.065)
        self.assertEqual(self.printer.cell.counts_per_gram, 114.88636)
        self.assertEqual(self.printer.cell.reference, -436490)
        self.printer.objects['configfile'].set.assert_not_called()
        self.assertFalse(self.module.busy)

    def test_normal_auto_runs_native_preparation_then_eight_pairs(self):
        self.printer.toolhead.homed = ''
        mesh = mock.Mock()
        mesh.get_mesh.return_value = object()
        def clear(value):
            mesh.get_mesh.return_value = value
        mesh.set_mesh.side_effect = clear
        self.printer.objects['bed_mesh'] = mesh
        self.automatic(BED_TEMP=60., NOZZLE_TEMP=170.)
        scripts = [r[1] for r in self.printer.trace if r[0] == 'gcode']
        self.assertEqual(scripts, ['BED_MESH_CLEAR', 'G28'])
        contacts = [r[1][:2] for r in self.printer.trace if r[0] == 'tap']
        self.assertEqual(contacts, [[28., 28.], [110., 110.]]
                         + [[28., 28.]] * 3 + [[110., 110.]] * 3)
        self.assertEqual(self.module.state, 'candidate')
        self.assertAlmostEqual(self.module.result['z_offset'],
            self.record['cr_test_z'] - auto.load_cell_contact_onset.estimate(
                self.diagnostic)['z'])
        self.assertEqual(len(self.module.result['offsets']), 6)
        self.assertEqual(self.primary.start_probe_session.call_count, 8)
        self.primary.check_probe_session_bounds.assert_not_called()
        self.assertEqual(self.printer.bed.target, 0.)
        self.assertEqual(self.printer.nozzle.target, 0.)
        self.assert_preserved()

    def test_calibrate_without_previous_manual_qualification(self):
        self.assertEqual(self.module.verified_points, {})
        self.module.cmd_CALIBRATE(Gcmd('AUTO_Z_OFFSET_CALIBRATE'))
        self.assertEqual(self.tap.tapping_move.run_tap.call_count, 6)
        self.assertEqual(self.module.state, 'candidate')
        self.assertAlmostEqual(self.module.result['z_offset'],
            self.record['cr_test_z'] - auto.load_cell_contact_onset.estimate(
                self.diagnostic)['z'])
        self.assertFalse(any(r[0] == 'gcode' for r in self.printer.trace))
        self.assert_preserved()

    def test_zero_diagnostic_flag_selects_normal_workflow(self):
        self.automatic(DIAGNOSTIC=0)
        self.assertEqual(self.tap.tapping_move.run_tap.call_count, 8)
        self.assertEqual(self.module.state, 'candidate')
        self.assert_preserved()

    def test_normal_auto_model_rejection_is_not_bypassed(self):
        self.use_record(-1)
        with self.assertRaisesRegex(RuntimeError, 'window_models_disagree'):
            self.automatic()
        self.tap.tapping_move.run_tap.assert_called_once()
        self.assertIsNone(self.module.candidate)
        self.assertIsNone(self.module.result)
        self.assertEqual(self.module.state, 'error')
        self.assert_preserved()

    def test_calibrate_model_rejection_stops_without_extra_pairs(self):
        self.use_record(-1)
        with self.assertRaisesRegex(RuntimeError, 'window_models_disagree'):
            self.module.cmd_CALIBRATE(Gcmd('AUTO_Z_OFFSET_CALIBRATE'))
        self.tap.tapping_move.run_tap.assert_called_once()
        self.assertIsNone(self.module.candidate)
        self.assert_preserved()

    def test_native_homing_error_prevents_all_nozzle_taps(self):
        def script(text):
            self.printer.script(text)
            if text == 'G28':
                raise RuntimeError('Homing failed')
        self.printer.gcode.run_script_from_command.side_effect = script
        with self.assertRaisesRegex(RuntimeError, 'Homing failed'):
            self.automatic()
        self.tap.tapping_move.run_tap.assert_not_called()
        self.assertIsNone(self.module.candidate)
        self.assert_preserved()

    def test_native_heater_failure_prevents_all_nozzle_taps(self):
        self.printer.bed.autoheat = False
        with self.assertRaisesRegex(RuntimeError, 'heating timed out'):
            self.automatic()
        self.tap.tapping_move.run_tap.assert_not_called()
        self.assert_preserved()

    def test_bound_tracks_each_fresh_surface_without_cold_floor(self):
        for surface in (-.2, .1, .25):
            cr = mock.Mock(bed_z=surface)
            floor, distance = self.module._nozzle_bounds(cr, Gcmd())
            self.assertAlmostEqual(floor, surface - .5)
            self.assertAlmostEqual(distance, 5. - floor)
            self.assertGreater(floor, -3.)
            self.assertNotEqual(floor, 0.)
        self.assert_preserved()

    def test_invalid_reference_cannot_fall_back_to_global_minimum(self):
        for surface in (-2.5, -4., float('nan'), float('inf'), 6.):
            with self.subTest(surface=surface):
                with self.assertRaisesRegex(RuntimeError, 'no descent'):
                    self.module._nozzle_bounds(mock.Mock(bed_z=surface), Gcmd())
        self.tap.tapping_move.run_tap.assert_not_called()
        self.assert_preserved()

    def test_frozen_or_missing_hx711_samples_reach_only_explicit_endpoint(self):
        cr = mock.Mock(bed_z=.2)
        floor, distance = self.module._nozzle_bounds(cr, Gcmd())
        harness = engine_harness.EngineTests()
        harness.printer = self.printer
        for failure in ('No trigger on probe after full movement',
                        'Trigger analog error: SENSOR_TIMEOUT'):
            with self.subTest(failure=failure):
                self.printer.toolhead.position[2] = 5.
                move, collector = harness.failed_diagnostic_move()
                # Frozen-but-arriving samples never cross threshold; native
                # homing exhausts the commanded path and raises no-trigger.
                collector.stop_collecting.return_value = (
                    [(i / 80., 0., -427524) for i in range(10)], 0)
                def stop_at_endpoint(endstop, target, speed):
                    self.assertAlmostEqual(target[2], floor)
                    self.assertGreater(target[2], -3.)
                    self.printer.toolhead.position[2] = target[2]
                    raise RuntimeError(failure)
                phoming = self.printer.objects['homing']
                phoming.probing_move.side_effect = stop_at_endpoint
                with self.assertLogs(level='INFO'):
                    with self.assertRaisesRegex(RuntimeError, failure):
                        move.probing_move(Gcmd(), minimum_z=floor,
                                          maximum_descent=distance)
                phoming.probing_move.assert_called_once()
                collector.stop_collecting.assert_called_once()
                self.assertAlmostEqual(self.printer.toolhead.position[2], -.3)
        self.assert_preserved()

    def test_no_trigger_in_normal_workflow_retracts_without_retry(self):
        def no_trigger(gcmd, minimum_z=None, maximum_descent=None,
                       onset_stop=None):
            self.assertGreater(minimum_z, -3.)
            self.printer.toolhead.position[2] = minimum_z
            raise RuntimeError('No trigger on probe after full movement')
        self.tap.tapping_move.run_tap.side_effect = no_trigger
        with self.assertRaisesRegex(RuntimeError, 'No trigger'):
            self.automatic()
        self.tap.tapping_move.run_tap.assert_called_once()
        self.assertEqual(self.printer.toolhead.position[2], 5.)
        self.assertIsNone(self.module.candidate)
        self.assert_preserved()

    def test_force_error_aborts_normal_workflow_with_original_error(self):
        self.tap.tapping_move.run_tap.side_effect = RuntimeError('RAW_RANGE')
        with self.assertRaisesRegex(RuntimeError, 'RAW_RANGE'):
            self.automatic()
        self.tap.tapping_move.run_tap.assert_called_once()
        self.assertIsNone(self.module.candidate)
        self.assert_preserved()

    def test_retired_cr_only_command_is_not_registered(self):
        self.assertNotIn('AUTO_Z_OFFSET_TEST_PROBE',
                         self.printer.gcode.commands)
        self.assert_preserved()


if __name__ == '__main__':
    unittest.main()
