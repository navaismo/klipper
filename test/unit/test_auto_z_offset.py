# Offline contact-engine and standalone offset tests; no printer connection
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import importlib
import math
import os
import random
import sys
import types
import unittest
from unittest import mock

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'klippy'))
PACKAGE = '_auto_z_test_extras'
MISSING = object()
package = types.ModuleType(PACKAGE)
package.__path__ = [os.path.join(ROOT, 'klippy', 'extras')]
fake_mcu = types.ModuleType('mcu')
fake_mcu.MCU_trsync = types.SimpleNamespace(REASON_COMMS_TIMEOUT=10)
with mock.patch.dict(sys.modules, {PACKAGE: package, 'mcu': fake_mcu}):
    auto = importlib.import_module(PACKAGE + '.auto_z_offset')
    bedmesh = importlib.import_module(PACKAGE + '.bed_mesh')
lcprobe = auto.load_cell_probe


class Config:
    error = RuntimeError
    def __init__(self, printer, data=None):
        self.printer = printer
        self.data = {'load_cell': 'bed_force', 'probe_point': [25., 28.],
            'speed': 0.5, 'lift_speed': 0.1, 'trigger_force': 75.,
            'force_safety_limit': 200., 'temperature_stable_time': 1.,
            'heating_timeout': 3., 'z_offset': 1.065}
        self.data.update(data or {})
    def get_printer(self):
        return self.printer
    def get_name(self):
        return 'auto_z_offset'
    def has_section(self, name):
        return name in ('bltouch', 'stepper_z')
    def getsection(self, name):
        return Config(self.printer, {'position_min': -3.})
    def get(self, key, default=MISSING):
        if key not in self.data and default is MISSING:
            raise RuntimeError('Missing ' + key)
        return self.data.get(key, default)
    def _number(self, key, default, minval, maxval, above, below, cast):
        raw = self.get(key, default)
        if raw is None:
            return None
        value = cast(raw)
        if ((minval is not None and value < minval)
                or (maxval is not None and value > maxval)
                or (above is not None and value <= above)
                or (below is not None and value >= below)):
            raise RuntimeError('Invalid ' + key)
        return value
    def getfloat(self, key, default=MISSING, minval=None, maxval=None,
                 above=None, below=None, **kwargs):
        return self._number(key, default, minval, maxval, above, below, float)
    def getint(self, key, default=MISSING, minval=None, maxval=None):
        return self._number(key, default, minval, maxval, None, None, int)
    def getfloatlist(self, key, default=None, count=None):
        result = self.get(key, default)
        if isinstance(result, str):
            result = [float(v) for v in result.split(',')]
        if count is not None and len(result) != count:
            raise RuntimeError('Invalid ' + key)
        return result
    def getlists(self, key, seps=None, parser=str, count=None):
        raw = self.get(key)
        if isinstance(raw, str):
            raw = [line.split(',') for line in raw.splitlines() if line.strip()]
        if any(len(point) != count for point in raw):
            raise RuntimeError('Invalid ' + key)
        return tuple(tuple(parser(v) for v in point) for point in raw)
    def getchoice(self, key, choices, default=None):
        value = self.get(key, default)
        if value not in choices:
            raise RuntimeError('Invalid ' + key)
        return choices[value] if isinstance(choices, dict) else value


class Gcmd:
    error = RuntimeError
    def __init__(self, name='TEST', params=None):
        self.name = name
        self.params = params or {}
        self.respond_info = mock.Mock()
    def get_float(self, key, default=None, minval=None, maxval=None,
                  above=None, below=None):
        cfg = Config(None, {key: self.params.get(key, default)})
        return cfg.getfloat(key, minval=minval, maxval=maxval,
                            above=above, below=below)
    def get(self, key, default=None):
        return self.params.get(key, default)
    def get_int(self, key, default=None, minval=None, maxval=None):
        cfg = Config(None, {key: self.params.get(key, default)})
        return cfg.getint(key, minval=minval, maxval=maxval)
    def get_command(self):
        return self.name
    def get_command_parameters(self):
        return self.params


class Gcode:
    error = RuntimeError
    Coord = tuple
    def __init__(self):
        self.commands = {}
        self.respond_info = mock.Mock()
    def register_command(self, name, callback, **kwargs):
        if callback is None:
            return self.commands.pop(name, None)
        if name in self.commands:
            raise RuntimeError('Already registered ' + name)
        self.commands[name] = callback
    def create_gcode_command(self, name, line, params):
        return Gcmd(name, params)


class Heater:
    min_temp = 0.
    max_temp = 260.
    def __init__(self, temperature, name='extruder'):
        self.name = name
        self.temperature = temperature
        self.target = 0.
        self.autoheat = True
    def get_temp(self, now):
        return self.temperature, self.target
    def get_name(self):
        return self.name


class Toolhead:
    def __init__(self, trace):
        self.trace = trace
        self.position = [100., 100., 10., 0.]
        self.homed = 'xyz'
    def get_position(self):
        return list(self.position)
    def get_status(self, now):
        return {'homed_axes': self.homed, 'axis_minimum': [-6., -14., -3.],
                'axis_maximum': [230., 225., 250.]}
    def manual_move(self, pos, speed):
        self.trace.append(('move', list(pos)))
        for i, value in enumerate(pos):
            if value is not None:
                self.position[i] = value
    def wait_moves(self):
        self.trace.append(('drain',))


class LoadCell:
    invert = 1.
    def __init__(self):
        self.calibrated = True
        self.counts_per_gram = 100.
        self.reference = 1000
        self.sensor = mock.Mock()
        self.sensor.get_samples_per_second.return_value = 80
        self.sensor.get_range.return_value = (-8388608, 8388607)
        self.sensor.get_mcu.return_value.is_fileoutput.return_value = False
    def is_calibrated(self):
        return self.calibrated
    def get_counts_per_gram(self):
        return self.counts_per_gram
    def get_reference_tare_counts(self):
        return self.reference
    def get_sensor(self):
        return self.sensor
    def avg_counts(self):
        return self.reference
    def tare(self, value):
        self.tare_counts = int(value)


class Primary:
    def __init__(self, printer):
        self.printer = printer
        self.offsets = (-24., -14.5, 1.065)
        self.sessions = []
        self.fail = False
        self.height = 1.165
    def get_offsets(self):
        return self.offsets
    def start_probe_session(self, gcmd):
        session = mock.Mock()
        def run(cmd):
            self.printer.trace.append(('crtouch',
                self.printer.toolhead.get_position()))
            if self.fail:
                raise RuntimeError('CR Touch error')
        session.run_probe.side_effect = run
        session.pull_probed_results.side_effect = lambda: [
            auto.manual_probe.create_probe_result(
                self.printer.toolhead.get_position()[:2] + [self.height],
                self.offsets)]
        self.sessions.append(session)
        return session


class Printer:
    config_error = command_error = RuntimeError
    def __init__(self):
        self.trace = []
        self.now = 0.
        self.shutdown = False
        self.events = {}
        self.toolhead = Toolhead(self.trace)
        self.gcode = Gcode()
        self.gcode.run_script_from_command = mock.Mock(side_effect=self.script)
        self.reactor = mock.Mock()
        self.reactor.monotonic.side_effect = lambda: self.now
        self.reactor.pause.side_effect = self.pause
        self.cell = LoadCell()
        self.primary = Primary(self)
        self.bed, self.nozzle = Heater(20., 'heater_bed'), Heater(20.)
        heaters = mock.Mock()
        heaters.lookup_heater.side_effect = {
            'heater_bed': self.bed, 'extruder': self.nozzle}.__getitem__
        heaters.set_temperature.side_effect = self.set_temperature
        self.stats = mock.Mock()
        self.stats.get_status.return_value = {'state': 'standby'}
        self.sd = mock.Mock(preflight_pending=False)
        self.sd.is_active.return_value = False
        self.pause_resume = types.SimpleNamespace(is_paused=False)
        self.pending = {'heater_bed': {'pid_kp': '42'}}
        cfg = mock.Mock()
        cfg.get_status.side_effect = lambda now: {
            'save_config_pending_items': self.pending}
        cfg.set.side_effect = lambda section, key, value: (
            self.pending.setdefault(section, {}).update({key: value}))
        self.objects = {'gcode': self.gcode, 'bltouch': self.primary,
            'probe': self.primary, 'load_cell bed_force': self.cell,
            'toolhead': self.toolhead, 'heaters': heaters,
            'configfile': cfg, 'print_stats': self.stats,
            'pause_resume': self.pause_resume, 'virtual_sdcard': self.sd,
            'webhooks': mock.Mock(), 'pins': mock.Mock()}
    def pause(self, until):
        self.now = until
        return until
    def script(self, script):
        self.trace.append(('gcode', script))
        if script == 'G28':
            for callback in self.events.get('homing:home_rails_begin', []):
                callback(None, None)
            self.toolhead.homed = 'xyz'
        elif script == 'BED_MESH_CLEAR':
            mesh = self.objects.get('bed_mesh')
            if mesh is not None:
                mesh.set_mesh(None)
    def set_temperature(self, heater, target, wait=False):
        self.trace.append(('heat', heater, target))
        heater.target = target
        if heater.autoheat and target:
            heater.temperature = target
    def get_reactor(self):
        return self.reactor
    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)
    def load_object(self, config, name):
        return self.objects[name]
    def register_event_handler(self, name, callback):
        self.events.setdefault(name, []).append(callback)
    def is_shutdown(self):
        return self.shutdown
    def add_object(self, name, value):
        if name in self.objects:
            raise RuntimeError('Duplicate object ' + name)
        self.objects[name] = value


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.printer = Printer()
        self.tap = mock.Mock()
        self.tap.tapping_move.run_tap.side_effect = self.contact
        self.tap.tapping_move.get_status.return_value = {'last_trigger_z': .08}
        self.contact_action = None
        self.nozzle_heights = iter([0.1] * 16)
        self.patch = mock.patch.object(lcprobe, 'LoadCellTapHelper',
                                       return_value=self.tap)
        self.factory = self.patch.start()
        self.addCleanup(self.patch.stop)
        self.module = auto.AutoZOffset(Config(
            self.printer, getattr(self, 'config_data', None)))
        self.module._connect()
    def contact(self, gcmd):
        self.printer.trace.append(('tap',
            self.printer.toolhead.get_position()))
        self.printer.toolhead.position[2] = 0.1
        if self.contact_action is not None:
            self.contact_action()
        return (self.printer.toolhead.get_position()[:2]
                + [next(self.nozzle_heights)], True)
    def calibrate(self):
        self.module.verified_points = dict.fromkeys(
            range(len(self.module.points)), self.module._fingerprint())
        self.module.contact_verified = True
        self.module.cmd_CALIBRATE(Gcmd())
    def assert_no_motion_or_heat(self):
        self.assertEqual(self.printer.trace, [])
    def test_constructor_reuses_primary_and_single_cell(self):
        self.factory.assert_called_once()
        self.assertIs(self.factory.call_args.args[1], self.printer.cell)
        self.assertEqual(self.factory.call_args.kwargs,
                         {'ascent_sample_time': 5., 'log_trace': True})
        self.assertIs(self.printer.objects['probe'], self.printer.primary)
        self.assertEqual(set(self.printer.gcode.commands), {
            'AUTO_Z_OFFSET_CALIBRATE', 'AUTO_Z_OFFSET_TEST_CONTACT',
            'AUTO_Z_OFFSET_ACCEPT', 'AUTO_Z_OFFSET'})
        self.assert_no_motion_or_heat()
    def test_five_pairs_same_physical_point_and_sign(self):
        self.calibrate()
        self.assertEqual(self.module.state, 'candidate')
        self.assertAlmostEqual(self.module.result['z_offset'], 1.065)
        self.assertEqual(len(self.module.result['offsets']), 5)
        cr = [item[1] for item in self.printer.trace if item[0] == 'crtouch']
        taps = [item[1] for item in self.printer.trace if item[0] == 'tap']
        self.assertEqual(len(cr), 5)
        self.assertEqual(len(taps), 5)
        self.assertTrue(all(p[:2] == [49., 42.5] for p in cr))
        self.assertTrue(all(p[:2] == [25., 28.] for p in taps))
        for session in self.printer.primary.sessions:
            session.end_probe_session.assert_called_once()
        self.printer.objects['configfile'].set.assert_not_called()
        self.assertEqual(self.printer.bed.target, 0.)
        self.assertEqual(self.printer.nozzle.target, 0.)
        self.assertFalse(self.module.busy)
    def test_confirmation_only_stages_offset_preserving_other_pending(self):
        self.calibrate()
        self.module.cmd_ACCEPT(Gcmd())
        self.assertEqual(self.printer.pending, {'bltouch': {'z_offset':
            '1.065000'}, 'heater_bed': {'pid_kp': '42'}})
        self.assertEqual(self.printer.primary.offsets[2], 1.065)
        self.assertEqual(self.module.state, 'accepted')
        with self.assertRaisesRegex(RuntimeError, 'No validated'):
            self.module.cmd_ACCEPT(Gcmd())
    def test_requires_supervised_contact(self):
        with self.assertRaisesRegex(RuntimeError, 'TEST_CONTACT'):
            self.module.cmd_CALIBRATE(Gcmd())
        self.assert_no_motion_or_heat()
    def test_supervised_test_uses_same_engine_without_candidate(self):
        self.module.cmd_TEST_CONTACT(Gcmd())
        self.assertTrue(self.module.contact_verified)
        self.assertIsNone(self.module.candidate)
        self.assertEqual(self.tap.tapping_move.run_tap.call_count, 1)
        self.printer.objects['configfile'].set.assert_not_called()
    def test_single_point_reference_is_captured_once_from_hot_clearance(self):
        def reference():
            self.assertEqual(self.printer.toolhead.get_position()[:3],
                             [25., 28., self.module.clearance])
            self.assertEqual(self.printer.bed.target, 50.)
            self.assertEqual(self.printer.nozzle.target, 170.)
            return -451195
        with mock.patch.object(self.printer.cell, 'avg_counts',
                               side_effect=reference) as capture:
            self.calibrate()
        capture.assert_called_once()
        self.tap.probing_move.set_safety_reference.assert_called_once_with(
            -451195)
        self.assertEqual(
            self.tap.probing_move.clear_safety_reference.call_count, 2)
        self.assertEqual(self.printer.cell.reference, 1000)
        self.assertEqual(self.printer.primary.offsets[2], 1.065)

    def test_point_reference_acquisition_failure_prevents_nozzle_contact(self):
        with mock.patch.object(self.printer.cell, 'avg_counts',
                               side_effect=RuntimeError('ADC error')):
            with self.assertRaisesRegex(RuntimeError, 'ADC error'):
                self.calibrate()
        self.tap.tapping_move.run_tap.assert_not_called()
        self.tap.probing_move.set_safety_reference.assert_not_called()
        self.assertEqual(
            self.tap.probing_move.clear_safety_reference.call_count, 2)
        self.assertIsNone(self.module.candidate)
        self.printer.objects['configfile'].set.assert_not_called()
    def test_contact_report_has_trigger_fit_difference_and_conditions(self):
        self.module.cmd_TEST_CONTACT(Gcmd())
        report = self.module.get_status(0.)['last_contact']
        self.assertEqual(report['point'], [25., 28.])
        self.assertAlmostEqual(report['force_trigger_z'], .08)
        self.assertAlmostEqual(report['regression_z'], .1)
        self.assertAlmostEqual(report['regression_delta'], .02)
        self.assertEqual(report['saved_offset'], 1.065)
        self.assertFalse(report['mesh_active'])
        self.assertEqual(report['temperatures_before']['bed'],
                         {'measured': 50., 'target': 50.})
        self.assertEqual(report['temperatures_after']['nozzle'],
                         {'measured': 170., 'target': 170.})
        self.printer.objects['configfile'].set.assert_not_called()
        self.assertIsNone(self.module.candidate)
        self.printer.events['homing:home_rails_begin'][0](None, None)
        self.assertIsNone(self.module.last_contact)
    def test_missing_trigger_z_cannot_leave_a_candidate(self):
        self.tap.tapping_move.get_status.return_value = {'last_trigger_z': None}
        with self.assertRaisesRegex(RuntimeError, 'Missing force trigger'):
            self.module.cmd_TEST_CONTACT(Gcmd())
        self.assertIsNone(self.module.candidate)
        self.assertIsNone(self.module.last_contact)
        self.printer.objects['configfile'].set.assert_not_called()
    def test_preset_targets_stay_constant_for_manual_comparison(self):
        self.printer.set_temperature(self.printer.bed, 60.)
        self.printer.set_temperature(self.printer.nozzle, 170.)
        self.module.cmd_TEST_CONTACT(Gcmd())
        self.assertEqual(self.printer.bed.target, 60.)
        self.assertEqual(self.printer.nozzle.target, 170.)
        self.assertEqual(self.printer.primary.offsets[2], 1.065)
        self.printer.objects['configfile'].set.assert_not_called()
    def test_repeated_contacts_do_not_accumulate_or_depend_on_old_offset(self):
        for old_offset in (1.065, .5, 2.):
            self.printer.primary.offsets = (-24., -14.5, old_offset)
            gcmd = Gcmd()
            self.module.cmd_TEST_CONTACT(gcmd)
            results = [c.args[0] for c in gcmd.respond_info.call_args_list
                       if c.args[0].startswith('Z-offset pair')]
            self.assertEqual(results, ['Z-offset pair 1: 1.065000 mm'])
            self.assertEqual(self.printer.primary.offsets[2], old_offset)
        self.printer.objects['configfile'].set.assert_not_called()
    def test_paired_result_is_invariant_under_coordinate_translation(self):
        self.printer.primary.height += .75
        self.nozzle_heights = iter([.85] * 5)
        self.calibrate()
        self.assertAlmostEqual(self.module.result['z_offset'], 1.065)
    def test_print_pause_sd_and_preflight_guards(self):
        checks = [lambda: self.printer.stats.get_status.configure_mock(
            return_value={'state': 'printing'}),
            lambda: setattr(self.printer.pause_resume, 'is_paused', True),
            lambda: self.printer.sd.is_active.configure_mock(return_value=True),
            lambda: setattr(self.printer.sd, 'preflight_pending', True)]
        for change in checks:
            self.printer.stats.get_status.return_value = {'state': 'standby'}
            self.printer.pause_resume.is_paused = False
            self.printer.sd.is_active.return_value = False
            self.printer.sd.preflight_pending = False
            change()
            with self.assertRaisesRegex(RuntimeError, 'idle printer'):
                self.module.cmd_TEST_CONTACT(Gcmd())
        self.assert_no_motion_or_heat()
    def test_unhomed_and_uncalibrated_guards(self):
        self.printer.toolhead.homed = 'xy'
        with self.assertRaisesRegex(RuntimeError, 'Home XYZ'):
            self.module.cmd_TEST_CONTACT(Gcmd())
        self.printer.toolhead.homed = 'xyz'
        self.printer.cell.calibrated = False
        with self.assertRaisesRegex(RuntimeError, 'Calibrate the load cell'):
            self.module.cmd_TEST_CONTACT(Gcmd())
        self.assert_no_motion_or_heat()
    def test_geometry_includes_probe_offset(self):
        self.module.points = [[220., 28.]]
        with self.assertRaisesRegex(RuntimeError, 'out of range'):
            self.module.cmd_TEST_CONTACT(Gcmd())
        self.assert_no_motion_or_heat()
    def test_mesh_and_compensation_require_explicit_clearance(self):
        mesh = mock.Mock()
        mesh.get_mesh.return_value = object()
        mesh.get_status.return_value = {'mesh_matrix': [[0., 0.]]}
        self.printer.objects['bed_mesh'] = mesh
        with self.assertRaisesRegex(RuntimeError, 'BED_MESH_CLEAR'):
            self.module.cmd_TEST_CONTACT(Gcmd())
        mesh.get_mesh.return_value = None
        mesh.get_status.return_value = {'mesh_matrix': [[]]}
        self.printer.objects['axis_twist_compensation'] = object()
        with self.assertRaisesRegex(RuntimeError, 'axis_twist_compensation'):
            self.module.cmd_TEST_CONTACT(Gcmd())
        mesh.set_mesh.assert_not_called()
        self.assert_no_motion_or_heat()
    def test_real_bed_mesh_clear_allows_contact_preflight(self):
        mesh = bedmesh.BedMesh.__new__(bedmesh.BedMesh)
        mesh.printer = self.printer
        mesh.splitter = mock.Mock()
        mesh.pmgr = mock.Mock()
        mesh.pmgr.get_profiles.return_value = {'default': {}}
        mesh.z_mesh = object()
        mesh.status = {'mesh_matrix': [[0., 0.]]}
        self.printer.objects['bed_mesh'] = mesh
        self.printer.objects['gcode_move'] = mock.Mock()
        with self.assertRaisesRegex(RuntimeError, 'BED_MESH_CLEAR'):
            self.module._check_geometry()
        mesh.cmd_BED_MESH_CLEAR(Gcmd())
        self.assertIsNone(mesh.get_mesh())
        self.assertEqual(mesh.get_status()['mesh_matrix'], [[]])
        self.module._check_geometry()
        self.assert_no_motion_or_heat()
    def test_minimum_and_maximum_temperatures_before_heater_changes(self):
        for params in ({'BED_TEMP': 49.}, {'NOZZLE_TEMP': 169.},
                       {'NOZZLE_TEMP': 270.}, {'BED_TEMP': float('nan')}):
            with self.assertRaises(RuntimeError):
                self.module.cmd_TEST_CONTACT(Gcmd(params=params))
        self.assert_no_motion_or_heat()
    def test_configured_bed_at_50_reaches_contact_without_waiting_for_60(self):
        self.printer = Printer()
        self.module = auto.AutoZOffset(Config(self.printer, {'bed_temp': 50.}))
        self.module._connect()
        self.module.cmd_TEST_CONTACT(Gcmd())
        self.assertEqual(self.module.state, 'contact_ready')
        self.assertEqual(self.module.last_contact['temperatures_before']['bed'],
                         {'measured': 50., 'target': 50.})
        self.assertEqual(self.module.nozzle_temp, 170.)
        self.printer.objects['configfile'].set.assert_not_called()

    def test_bed_command_accepts_50_and_configuration_rejects_below(self):
        self.module.cmd_TEST_CONTACT(Gcmd(params={'BED_TEMP': 50.}))
        self.assertEqual(self.module.last_contact['temperatures_before']['bed'],
                         {'measured': 50., 'target': 50.})
        self.assertEqual(self.module.bed_temp, 50.)
        with self.assertRaisesRegex(RuntimeError, 'bed_temp'):
            auto.AutoZOffset(Config(self.printer, {'bed_temp': 49.}))

    def test_heating_timeout_prevents_motion_and_restores_targets(self):
        self.printer.bed.autoheat = False
        with self.assertRaisesRegex(RuntimeError, 'heating timed out'):
            self.module.cmd_TEST_CONTACT(Gcmd())
        self.assertFalse(any(item[0] == 'move' for item in self.printer.trace))
        self.assertEqual(self.printer.bed.target, 0.)
        self.assertEqual(self.printer.nozzle.target, 0.)
        self.assertIsNone(self.module.candidate)
    def test_nominal_minimum_crossing_inside_band_does_not_abort(self):
        self.contact_action = lambda: setattr(self.printer.bed,
                                              'temperature', 49.99)
        self.calibrate()
        self.assertEqual(self.tap.tapping_move.run_tap.call_count, 5)
        self.assertIsNotNone(self.module.candidate)
    def test_startup_oscillation_does_not_reset_minimum_latch(self):
        def oscillate(until):
            self.printer.pause(until)
            self.printer.nozzle.temperature = (
                169.1 if int(until * 4) % 2 else 171.7)
            return until
        self.printer.reactor.pause.side_effect = oscillate
        self.module.cmd_TEST_CONTACT(Gcmd())
        self.assertEqual(self.module.state, 'contact_ready')
        self.assertLess(self.printer.now, self.module.heating_timeout)
    def test_warmup_still_requires_reaching_nominal_minimum(self):
        self.printer.nozzle.autoheat = False
        self.printer.nozzle.temperature = 169.9
        with self.assertRaisesRegex(RuntimeError, 'heating timed out'):
            self.module.cmd_TEST_CONTACT(Gcmd())
        self.tap.tapping_move.run_tap.assert_not_called()
    def test_warmup_accumulates_in_band_time_across_pid_cycles(self):
        def oscillate(until):
            self.printer.pause(until)
            self.printer.nozzle.temperature = (
                173. if int(until * 4) % 2 else 171.)
            return until
        self.printer.reactor.pause.side_effect = oscillate
        self.module.cmd_TEST_CONTACT(Gcmd())
        self.assertEqual(self.module.state, 'contact_ready')
        self.assertGreaterEqual(self.printer.now, 2.)
        self.assertLess(self.printer.now, self.module.heating_timeout)
    def test_transient_temperature_excursion_recovers_without_retry(self):
        def dip():
            self.printer.nozzle.temperature = 167.
            self.contact_action = None
        self.contact_action = dip
        def recover(until):
            self.printer.pause(until)
            if self.module.state == 'measuring':
                self.printer.nozzle.temperature = 170.
            return until
        self.printer.reactor.pause.side_effect = recover
        self.calibrate()
        self.assertEqual(self.module.state, 'candidate')
        self.assertEqual(self.tap.tapping_move.run_tap.call_count, 5)
        messages = self.printer.gcode.respond_info.call_args_list
        self.assertTrue(any('temperature recovery' in c.args[0]
                            for c in messages))
    def test_invalid_sensor_temperature_aborts_without_recovery_wait(self):
        self.contact_action = lambda: setattr(self.printer.bed,
                                              'temperature', -18.1)
        with self.assertRaisesRegex(RuntimeError,
                                    'heater_bed: measured -18.10 C'):
            self.calibrate()
        self.assertEqual(self.printer.now, self.module.stable_time)
        self.assertIsNone(self.module.candidate)
    def test_temperature_error_precedes_restoration_of_zero_targets(self):
        targets_at_failure = []
        def bed_reading_drop(pos, speed):
            targets_at_failure.append((self.printer.bed.target,
                                       self.printer.nozzle.target))
            self.printer.bed.temperature = 50.9
        self.printer.toolhead.manual_move = bed_reading_drop
        gcmd = Gcmd(params={'BED_TEMP': 65., 'NOZZLE_TEMP': 175.})
        with self.assertRaisesRegex(RuntimeError,
                'heater_bed: measured 50.90 C, target 65.00 C, expected 65.00'):
            self.module.cmd_TEST_CONTACT(gcmd)
        self.assertTrue(targets_at_failure)
        self.assertEqual(targets_at_failure[0], (65., 175.))
        self.assertEqual(self.printer.bed.target, 0.)
        self.assertEqual(self.printer.nozzle.target, 0.)
        self.assertIn('restoring previous heater targets',
                      gcmd.respond_info.call_args.args[0])
        self.tap.tapping_move.run_tap.assert_not_called()
        self.assertIsNone(self.module.candidate)
    def test_changed_heater_target_is_not_silently_reapplied(self):
        self.contact_action = lambda: setattr(self.printer.nozzle, 'target', 0.)
        with self.assertRaisesRegex(RuntimeError,
                'extruder: measured 170.00 C, target 0.00 C, expected 170.00'):
            self.module.cmd_TEST_CONTACT(Gcmd())
        self.assertFalse(self.module.contact_verified)
        self.assertIsNone(self.module.candidate)
    def test_invalid_tap_fails_without_saving_or_retrying(self):
        self.tap.tapping_move.run_tap.return_value = ([25., 28., 0.1], False)
        self.tap.tapping_move.run_tap.side_effect = None
        with self.assertRaisesRegex(RuntimeError, 'Invalid nozzle contact'):
            self.calibrate()
        self.assertEqual(self.tap.tapping_move.run_tap.call_count, 1)
        self.printer.objects['configfile'].set.assert_not_called()
    def test_nonfinite_and_inconsistent_offsets_rejected(self):
        self.nozzle_heights = iter([0.1, float('nan')])
        with self.assertRaisesRegex(RuntimeError, 'Invalid nozzle contact'):
            self.calibrate()
        self.nozzle_heights = iter([0.1, 0.2])
        with self.assertRaisesRegex(RuntimeError, 'offset_tolerance'):
            self.calibrate()
        self.assertIsNone(self.module.result)
    def test_probe_session_closed_on_error(self):
        self.printer.primary.fail = True
        with self.assertRaisesRegex(RuntimeError, 'CR Touch error'):
            self.calibrate()
        self.printer.primary.sessions[0].end_probe_session.assert_called_once()
        self.tap.tapping_move.run_tap.assert_not_called()
    def test_shutdown_skips_recovery_motion_and_heater_restore(self):
        def stop():
            self.printer.shutdown = True
            self.shutdown_trace = list(self.printer.trace)
        self.contact_action = stop
        with self.assertRaisesRegex(RuntimeError, 'ready printer'):
            self.calibrate()
        self.assertEqual(self.printer.trace, self.shutdown_trace)
    def test_pending_offset_not_overwritten(self):
        self.printer.pending['bltouch'] = {'z_offset': '1.2'}
        with self.assertRaisesRegex(RuntimeError, 'already pending'):
            self.module.cmd_TEST_CONTACT(Gcmd())
        self.assert_no_motion_or_heat()
    def test_invalid_force_scale_and_pending_section_removal_rejected(self):
        self.printer.cell.counts_per_gram = float('nan')
        with self.assertRaisesRegex(RuntimeError, 'force calibration'):
            self.module.cmd_TEST_CONTACT(Gcmd())
        self.printer.cell.counts_per_gram = 100.
        self.printer.pending['bltouch'] = None
        with self.assertRaisesRegex(RuntimeError, 'already pending'):
            self.module.cmd_TEST_CONTACT(Gcmd())
        self.assert_no_motion_or_heat()
    def test_candidate_stale_after_sensor_change(self):
        self.calibrate()
        self.printer.cell.counts_per_gram *= 2.
        with self.assertRaisesRegex(RuntimeError, 'stale'):
            self.module.cmd_ACCEPT(Gcmd())
        self.printer.objects['configfile'].set.assert_not_called()
    def test_homing_and_disconnect_invalidate_candidate(self):
        self.calibrate()
        self.printer.events['homing:home_rails_begin'][0](None, None)
        self.assertIsNone(self.module.candidate)
        self.assertFalse(self.module.contact_verified)
        self.module._disconnect()
        self.assertFalse(self.module.connected)
    def test_failed_cleanup_cannot_leave_acceptable_candidate(self):
        self.printer.objects['heaters'].set_temperature.side_effect = (
            lambda h, t, wait=False: self.printer.set_temperature(h, t)
            if t else (_ for _ in ()).throw(RuntimeError('restore failure')))
        with self.assertLogs(level='ERROR'):
            with self.assertRaisesRegex(RuntimeError, 'cleanup failed'):
                self.calibrate()
        self.assertIsNone(self.module.candidate)
        self.assertFalse(self.module.busy)


class MultiPointWorkflowTests(unittest.TestCase):
    config_data = {'probe_point': None, 'probe_points': [
        [28., 28.], [110., 110.], [180., 28.], [28., 180.]]}
    setUp = WorkflowTests.setUp
    contact = WorkflowTests.contact
    calibrate = WorkflowTests.calibrate
    assert_no_motion_or_heat = WorkflowTests.assert_no_motion_or_heat

    def test_reference_is_fixed_for_three_pairs_then_recaptured(self):
        references = iter((1000, 2000, 3000, 4000))
        captured = []
        def reference():
            captured.append(self.printer.toolhead.get_position()[:3])
            return next(references)
        def contact():
            count = self.tap.tapping_move.run_tap.call_count
            self.assertEqual(
                self.tap.probing_move.set_safety_reference.call_count,
                (count + 2) // 3)
        self.contact_action = contact
        with mock.patch.object(self.printer.cell, 'avg_counts',
                               side_effect=reference) as capture:
            self.calibrate()
        self.assertEqual(capture.call_count, 4)
        self.assertEqual(captured, [list(p) + [self.module.clearance]
                                    for p in self.module.points])
        self.assertEqual(
            self.tap.probing_move.set_safety_reference.call_args_list,
            [mock.call(v) for v in (1000, 2000, 3000, 4000)])
        self.assertEqual(self.printer.cell.reference, 1000)

    def test_four_points_three_fresh_pairs_with_clearance_before_xy(self):
        self.calibrate()
        report = self.module.result
        self.assertTrue(report['valid'])
        self.assertEqual(len(report['offsets']), 12)
        self.assertEqual(len(report['points']), 4)
        self.assertAlmostEqual(report['z_offset'], 1.065)
        cr = [item[1] for item in self.printer.trace if item[0] == 'crtouch']
        taps = [item[1] for item in self.printer.trace if item[0] == 'tap']
        for i, point in enumerate(self.module.points):
            self.assertEqual([p[:2] for p in taps[3*i:3*i+3]],
                             [list(point)] * 3)
            expected = [point[0] + 24., point[1] + 14.5]
            self.assertEqual([p[:2] for p in cr[3*i:3*i+3]], [expected] * 3)
            local = report['points'][i]
            self.assertEqual(local['point_index'], i+1)
            self.assertEqual(len(local['offsets']), 3)
            self.assertEqual(len(local['contacts']), 3)
            self.assertEqual([p['sample_index'] for p in local['contacts']],
                             [1, 2, 3])
        # The trace starts at Z10 and every XY move must remain at clearance.
        previous_z = 10.
        for item in self.printer.trace:
            if item[0] == 'tap':
                previous_z = .1
            elif item[0] == 'move':
                pos = item[1]
                if pos[0] is not None or pos[1] is not None:
                    self.assertGreaterEqual(previous_z, self.module.clearance)
                if pos[2] is not None:
                    previous_z = pos[2]
        self.assertIs(self.printer.objects['probe'], self.printer.primary)
        self.printer.objects['configfile'].set.assert_not_called()

    def test_local_medians_then_global_median_not_all_pair_average(self):
        offsets = [1.440, 1.450, 1.451, 1.448, 1.452, 1.453,
                   1.449, 1.455, 1.456, 1.460, 1.462, 1.464]
        self.nozzle_heights = iter(self.printer.primary.height - v
                                   for v in offsets)
        self.calibrate()
        report = self.module.result
        self.assertEqual(len(report['offsets']), 12)
        for point, expected in zip(report['points'],
                                    (1.450, 1.452, 1.455, 1.462)):
            self.assertAlmostEqual(point['median'], expected)
        self.assertAlmostEqual(report['spatial_range'], .012)
        self.assertAlmostEqual(report['z_offset'], (1.452 + 1.455) / 2.)
        self.assertNotAlmostEqual(report['z_offset'], sum(offsets) / 12.)

    def test_bed_height_variation_cancels_within_each_pair(self):
        def bed_height(point):
            return 1.2 + .003 * point[0] + .002 * point[1]
        start = self.printer.primary.start_probe_session
        def varying_probe(gcmd):
            toolhead = self.printer.toolhead.get_position()
            offsets = self.printer.primary.offsets
            point = [toolhead[i] + offsets[i] for i in (0, 1)]
            self.printer.primary.height = bed_height(point)
            return start(gcmd)
        self.printer.primary.start_probe_session = varying_probe
        self.nozzle_heights = iter(bed_height(point) - 1.46
                                   for point in self.module.points
                                   for sample in range(3))
        self.calibrate()
        self.assertAlmostEqual(self.module.result['z_offset'], 1.46)
        self.assertAlmostEqual(self.module.result['spatial_range'], 0.)

    def test_spatial_disagreement_retains_report_without_global_candidate(self):
        offsets = [1.462] * 3 + [1.438] * 3 + [1.471] * 3 + [1.445] * 3
        self.nozzle_heights = iter(self.printer.primary.height - v
                                   for v in offsets)
        gcmd = Gcmd()
        self.module.verified_points = dict.fromkeys(
            range(4), self.module._fingerprint())
        self.module.cmd_CALIBRATE(gcmd)
        self.assertEqual(self.module.state, 'rejected')
        self.assertFalse(self.module.result['valid'])
        self.assertAlmostEqual(self.module.result['spatial_range'], .033)
        self.assertEqual(len(self.module.result['points']), 4)
        self.assertIsNone(self.module.result['z_offset'])
        self.assertIsNone(self.module.candidate)
        with self.assertRaisesRegex(RuntimeError, 'No validated'):
            self.module.cmd_ACCEPT(Gcmd())
        self.printer.objects['configfile'].set.assert_not_called()
        self.assertEqual(self.printer.primary.offsets[2], 1.065)
        self.assertTrue(any('no global candidate' in call.args[0]
                            for call in gcmd.respond_info.call_args_list))
        self.module._invalidate()
        self.assertEqual(self.module.state, 'idle')
        self.assertIsNone(self.module.result)

    def test_supervised_contact_is_required_at_each_location(self):
        for point in range(1, 5):
            self.module.cmd_TEST_CONTACT(Gcmd(params={'POINT': point}))
            self.assertEqual(self.module.get_status(0.)['verified_points'],
                             list(range(1, point+1)))
            self.assertEqual(self.module.contact_verified, point == 4)
            if point < 4:
                with self.assertRaisesRegex(RuntimeError, 'every configured'):
                    self.module.cmd_CALIBRATE(Gcmd())
        self.assertEqual(self.tap.tapping_move.run_tap.call_count, 4)
        self.module.cmd_CALIBRATE(Gcmd())
        self.assertEqual(self.tap.tapping_move.run_tap.call_count, 16)
        self.assertEqual(self.module.state, 'candidate')

    def test_invalid_point_is_rejected_before_heat_or_motion(self):
        for point in (0, 5):
            with self.assertRaisesRegex(RuntimeError, 'POINT'):
                self.module.cmd_TEST_CONTACT(Gcmd(params={'POINT': point}))
        self.module.points = [list(p) for p in self.module.points]
        self.module.points[3] = [220., 180.]
        with self.assertRaisesRegex(RuntimeError, 'out of range'):
            self.module.cmd_TEST_CONTACT(Gcmd(params={'POINT': 1}))
        self.assert_no_motion_or_heat()

    def test_failed_remote_contact_aborts_without_retry_or_offset_edit(self):
        def fail():
            if len([p for p in self.printer.trace if p[0] == 'tap']) == 7:
                raise RuntimeError('Force safety limit reached')
        self.contact_action = fail
        with self.assertRaisesRegex(RuntimeError, 'Force safety'):
            self.calibrate()
        self.assertEqual(self.tap.tapping_move.run_tap.call_count, 7)
        self.assertIsNone(self.module.candidate)
        self.assertEqual(self.module.verified_points, {})
        self.printer.objects['configfile'].set.assert_not_called()

    def test_local_spread_rejects_without_hiding_pairs(self):
        self.nozzle_heights = iter([.1, .1, .1, .1, .15])
        with self.assertRaisesRegex(RuntimeError, 'Point 2 pairs'):
            self.calibrate()
        self.assertEqual(self.tap.tapping_move.run_tap.call_count, 5)
        self.assertIsNone(self.module.candidate)
        self.printer.objects['configfile'].set.assert_not_called()

    def test_sensor_changes_require_requalifying_previously_tested_points(self):
        self.module.cmd_TEST_CONTACT(Gcmd(params={'POINT': 1}))
        self.printer.cell.counts_per_gram += 1.
        self.module.cmd_TEST_CONTACT(Gcmd(params={'POINT': 2}))
        self.assertEqual(self.module.get_status(0.)['verified_points'], [2])
        with self.assertRaisesRegex(RuntimeError, 'every configured'):
            self.module.cmd_CALIBRATE(Gcmd())

    def test_homing_invalidates_all_location_qualifications(self):
        self.module.cmd_TEST_CONTACT(Gcmd(params={'POINT': 1}))
        self.printer.events['homing:home_rails_begin'][0](None, None)
        self.assertEqual(self.module.get_status(0.)['verified_points'], [])
        self.assertFalse(self.module.contact_verified)

    def test_configuration_rejects_bad_points_and_relaxed_spatial_limit(self):
        invalid = [{'probe_points': [[28, 28]] * 4},
                   {'probe_point': None, 'probe_points': [[28, 28]] * 4},
                   {'probe_point': None, 'probe_points': [[28, 28]] * 3},
                   {'probe_point': None, 'probe_points': [
                       [28, 28], [110, 110], [180, 28], [float('nan'), 180]]},
                   {'probe_point': None, 'probe_points': [
                       [28, 28], [110, 110], [180, 28], [28, 180]],
                    'spatial_tolerance': .03}]
        for data in invalid:
            with self.subTest(data=data):
                with self.assertRaises(RuntimeError):
                    auto.AutoZOffset(Config(self.printer, data))
        self.assert_no_motion_or_heat()

    def test_real_config_parser_reads_multiline_physical_points(self):
        configfile = importlib.import_module('configfile')
        parser = configfile.configparser.RawConfigParser()
        parser.read_string('[auto_z_offset]\nprobe_points:\n'
                           '    28, 28\n    110, 110\n'
                           '    180, 28\n    28, 180\n')
        cfg = configfile.ConfigWrapper(self.printer, parser, {},
                                        'auto_z_offset')
        self.assertEqual(cfg.getlists('probe_points', seps=(',', '\n'),
                                       parser=float, count=2),
                         tuple(tuple(p) for p in self.module.points))

    def test_four_point_confirmation_still_only_stages_after_validation(self):
        self.calibrate()
        self.printer.objects['configfile'].set.assert_not_called()
        self.module.cmd_ACCEPT(Gcmd())
        self.assertEqual(self.printer.primary.offsets[2], 1.065)
        self.assertEqual(self.printer.pending['bltouch']['z_offset'],
                         '1.065000')


class TwoPointWorkflowTests(unittest.TestCase):
    config_data = {'probe_point': None, 'probe_points': [
        [28., 28.], [110., 110.]]}
    setUp = WorkflowTests.setUp
    contact = WorkflowTests.contact
    calibrate = WorkflowTests.calibrate
    assert_no_motion_or_heat = WorkflowTests.assert_no_motion_or_heat
    test_local_spread_rejects_without_hiding_pairs = (
        MultiPointWorkflowTests.test_local_spread_rejects_without_hiding_pairs)
    test_sensor_changes_require_requalifying_previously_tested_points = (
        MultiPointWorkflowTests
        .test_sensor_changes_require_requalifying_previously_tested_points)
    test_homing_invalidates_all_location_qualifications = (
        MultiPointWorkflowTests
        .test_homing_invalidates_all_location_qualifications)

    def test_six_fresh_pairs_at_matching_physical_points(self):
        self.calibrate()
        report = self.module.result
        self.assertEqual(len(report['offsets']), 6)
        self.assertEqual([len(p['contacts']) for p in report['points']], [3, 3])
        cr = [p[1][:2] for p in self.printer.trace if p[0] == 'crtouch']
        taps = [p[1][:2] for p in self.printer.trace if p[0] == 'tap']
        self.assertEqual(cr, [[52., 42.5]] * 3 + [[134., 124.5]] * 3)
        self.assertEqual(taps, [[28., 28.]] * 3 + [[110., 110.]] * 3)
        self.assertIs(self.printer.objects['probe'], self.printer.primary)
        self.printer.objects['configfile'].set.assert_not_called()

    def test_first_point_median_is_candidate_without_averaging(self):
        # Replay the local medians from the owner's hot qualification.
        offsets = [1.454575, 1.462007, 1.471985,
                   1.462843, 1.464676, 1.471097]
        self.nozzle_heights = iter(self.printer.primary.height - v
                                   for v in offsets)
        self.calibrate()
        report = self.module.result
        self.assertAlmostEqual(report['z_offset'], 1.462007)
        self.assertAlmostEqual(report['spatial_range'], .002669)
        self.assertNotAlmostEqual(report['z_offset'],
                                 (1.462007 + 1.464676) * .5)
        self.printer.objects['configfile'].set.assert_not_called()
        self.module.cmd_ACCEPT(Gcmd())
        self.assertEqual(self.printer.pending['bltouch']['z_offset'],
                         '1.462007')
        self.assertEqual(self.printer.primary.offsets[2], 1.065)

    def test_reference_remains_first_point_when_validator_is_lower(self):
        self.nozzle_heights = iter(self.printer.primary.height - v
                                   for v in [1.47] * 3 + [1.45] * 3)
        self.calibrate()
        self.assertAlmostEqual(self.module.result['z_offset'], 1.47)

    def test_disagreement_retains_both_reports_and_refuses_acceptance(self):
        self.nozzle_heights = iter(self.printer.primary.height - v
                                   for v in [1.46] * 3 + [1.49] * 3)
        self.calibrate()
        self.assertEqual(self.module.state, 'rejected')
        self.assertFalse(self.module.result['valid'])
        self.assertAlmostEqual(self.module.result['spatial_range'], .03)
        self.assertEqual(len(self.module.result['points']), 2)
        self.assertIsNone(self.module.result['z_offset'])
        self.assertIsNone(self.module.candidate)
        with self.assertRaisesRegex(RuntimeError, 'No validated'):
            self.module.cmd_ACCEPT(Gcmd())
        self.printer.objects['configfile'].set.assert_not_called()
        self.assertEqual(self.printer.primary.offsets[2], 1.065)

    def test_both_locations_require_supervised_contact(self):
        self.module.cmd_TEST_CONTACT(Gcmd(params={'POINT': 1}))
        with self.assertRaisesRegex(RuntimeError, 'every configured'):
            self.module.cmd_CALIBRATE(Gcmd())
        self.module.cmd_TEST_CONTACT(Gcmd(params={'POINT': 2}))
        self.assertEqual(self.module.get_status(0.)['verified_points'], [1, 2])
        self.assertTrue(self.module.contact_verified)
        self.module.cmd_CALIBRATE(Gcmd())
        self.assertEqual(self.tap.tapping_move.run_tap.call_count, 8)

    def test_invalid_third_point_is_rejected_before_heat_or_motion(self):
        with self.assertRaisesRegex(RuntimeError, 'POINT'):
            self.module.cmd_TEST_CONTACT(Gcmd(params={'POINT': 3}))
        self.assert_no_motion_or_heat()

    def test_fixed_force_reference_is_recaptured_only_between_points(self):
        def contact():
            count = self.tap.tapping_move.run_tap.call_count
            self.assertEqual(
                self.tap.probing_move.set_safety_reference.call_count,
                (count + 2) // 3)
        self.contact_action = contact
        with mock.patch.object(self.printer.cell, 'avg_counts',
                               side_effect=(1000, 2000)) as capture:
            self.calibrate()
        self.assertEqual(capture.call_count, 2)
        self.assertEqual(
            self.tap.probing_move.set_safety_reference.call_args_list,
            [mock.call(1000), mock.call(2000)])
        self.tap.probing_move.clear_safety_reference.assert_called()
        self.assertEqual(self.printer.cell.reference, 1000)

    def test_force_error_at_validator_aborts_without_retry_or_candidate(self):
        def fail():
            if self.tap.tapping_move.run_tap.call_count == 4:
                raise RuntimeError('Force safety limit reached')
        self.contact_action = fail
        with self.assertRaisesRegex(RuntimeError, 'Force safety'):
            self.calibrate()
        self.assertEqual(self.tap.tapping_move.run_tap.call_count, 4)
        self.assertIsNone(self.module.candidate)
        self.assertEqual(self.module.verified_points, {})
        self.tap.probing_move.clear_safety_reference.assert_called()
        self.printer.objects['configfile'].set.assert_not_called()

    def test_two_point_configuration_requires_distinct_finite_locations(self):
        for points in ([[28., 28.]] * 2,
                       [[28., 28.], [float('nan'), 110.]]):
            with self.subTest(points=points):
                with self.assertRaises(RuntimeError):
                    auto.AutoZOffset(Config(self.printer, {
                        'probe_point': None, 'probe_points': points}))
        self.assert_no_motion_or_heat()


class AutomaticWorkflowTests(unittest.TestCase):
    config_data = TwoPointWorkflowTests.config_data
    setUp = WorkflowTests.setUp
    contact = WorkflowTests.contact
    assert_no_motion_or_heat = WorkflowTests.assert_no_motion_or_heat

    def run_automatic(self):
        self.module.cmd_AUTO_Z_OFFSET(Gcmd('AUTO_Z_OFFSET'))

    def test_tests_both_points_then_six_fresh_pairs_with_one_warmup(self):
        self.run_automatic()
        taps = [item[1][:2] for item in self.printer.trace if item[0] == 'tap']
        self.assertEqual(taps, [[28., 28.], [110., 110.]]
                         + [[28., 28.]] * 3 + [[110., 110.]] * 3)
        self.assertEqual(len(self.module.result['offsets']), 6)
        self.assertEqual(self.module.state, 'candidate')
        self.assertEqual(self.module.stage, 'complete')
        self.assertEqual(self.module.get_status(0.)['verified_points'], [1, 2])
        heats = [item for item in self.printer.trace if item[0] == 'heat']
        self.assertEqual(len(heats), 4)
        self.assertEqual([item[2] for item in heats], [50., 170., 0., 0.])
        self.assertEqual(self.printer.primary.offsets[2], 1.065)
        self.printer.objects['configfile'].set.assert_not_called()

    def test_failed_second_qualification_prevents_all_calibration_pairs(self):
        def fail():
            if self.tap.tapping_move.run_tap.call_count == 2:
                raise RuntimeError('Trigger analog error: RAW_RANGE')
        self.contact_action = fail
        with self.assertRaisesRegex(RuntimeError, 'Test P2 pair 1.*RAW_RANGE'):
            self.run_automatic()
        self.assertEqual(self.tap.tapping_move.run_tap.call_count, 2)
        self.assertIsNone(self.module.candidate)
        self.assertEqual(self.module.verified_points, {})
        self.assertIn('RAW_RANGE', self.module.error)
        self.assertEqual(self.printer.bed.target, 0.)
        self.assertEqual(self.printer.primary.offsets[2], 1.065)

    def test_calibration_error_identifies_point_pair_and_original_failure(self):
        def fail():
            if self.tap.tapping_move.run_tap.call_count == 7:
                raise RuntimeError('Insufficient ascent samples')
        self.contact_action = fail
        with self.assertRaisesRegex(RuntimeError,
                'Calibrate P2 pair 2.*Insufficient ascent samples'):
            self.run_automatic()
        self.assertEqual(self.tap.tapping_move.run_tap.call_count, 7)
        self.assertIsNone(self.module.candidate)
        self.printer.objects['configfile'].set.assert_not_called()

    def test_spatial_failure_remains_available_for_diagnosis(self):
        offsets = [1.46, 1.49] + [1.46] * 3 + [1.49] * 3
        self.nozzle_heights = iter(self.printer.primary.height-v
                                   for v in offsets)
        with self.assertRaisesRegex(RuntimeError, 'Spatial range'):
            self.run_automatic()
        self.assertEqual(self.module.state, 'rejected')
        self.assertEqual(len(self.module.result['points']), 2)
        self.assertIsNone(self.module.result['z_offset'])
        self.printer.objects['configfile'].set.assert_not_called()

    def test_lcd_highlights_finish_before_measurements(self):
        lcd = mock.Mock()
        lcd.begin_calibration.return_value = True
        self.printer.objects['tjc_lcd'] = lcd
        lcd.update_calibration.side_effect = lambda phase, text: (
            self.printer.trace.append(('lcd_phase', phase)))
        self.run_automatic()
        phases = [item[1] for item in self.printer.trace
                  if item[0] == 'lcd_phase']
        self.assertEqual(phases, ['cr_touch', 'nozzle'] * 8 + ['computing'])
        lcd.end_calibration.assert_called_once_with(
            'Offset 1.065mm - review', True)
        # Every measurement follows the synchronous phase render, not a timer.
        for i, item in enumerate(self.printer.trace):
            if item[0] in ('crtouch', 'tap'):
                expected = 'cr_touch' if item[0] == 'crtouch' else 'nozzle'
                self.assertEqual(self.printer.trace[i-1],
                                 ('lcd_phase', expected))

    def test_lcd_phase_failure_prevents_contact_and_reports_step(self):
        lcd = mock.Mock()
        lcd.begin_calibration.return_value = True
        lcd.update_calibration.side_effect = RuntimeError('LCD timeout')
        self.printer.objects['tjc_lcd'] = lcd
        with self.assertRaisesRegex(RuntimeError,
                                     'Test P1 pair 1.*LCD timeout'):
            self.run_automatic()
        self.tap.tapping_move.run_tap.assert_not_called()
        self.assertIsNone(self.module.candidate)
        lcd.end_calibration.assert_called_once_with('Failed: Test P1 pair 1',
                                                     False)

    def test_preflight_failure_does_not_render_or_heat(self):
        lcd = mock.Mock()
        self.printer.objects['tjc_lcd'] = lcd
        self.printer.stats.get_status.return_value = {'state': 'printing'}
        with self.assertRaisesRegex(RuntimeError, 'preflight.*idle'):
            self.run_automatic()
        lcd.begin_calibration.assert_not_called()
        self.assert_no_motion_or_heat()

    def test_single_point_configuration_cannot_run_two_point_workflow(self):
        self.module.points = [[28., 28.]]
        with self.assertRaisesRegex(RuntimeError, 'requires two probe_points'):
            self.run_automatic()
        self.assert_no_motion_or_heat()

    def test_automatic_homes_unhomed_printer_and_clears_active_mesh(self):
        self.printer.toolhead.homed = ''
        mesh = mock.Mock()
        active = [object()]
        mesh.get_mesh.side_effect = lambda: active[0]
        mesh.set_mesh.side_effect = lambda value: active.__setitem__(0, value)
        self.printer.objects['bed_mesh'] = mesh
        self.run_automatic()
        scripts = [v[1] for v in self.printer.trace if v[0] == 'gcode']
        self.assertEqual(scripts, ['BED_MESH_CLEAR', 'G28'])
        self.assertIsNone(mesh.get_mesh())
        self.assertEqual(self.printer.toolhead.homed, 'xyz')
        self.assertEqual(self.module.state, 'candidate')
        self.assertEqual(self.printer.primary.offsets[2], 1.065)
        self.printer.objects['configfile'].set.assert_not_called()

    def test_homing_failure_prevents_contacts_and_restores_heater_targets(self):
        self.printer.gcode.run_script_from_command.side_effect = RuntimeError(
            'Homing failed')
        with self.assertRaisesRegex(RuntimeError, 'homing.*Homing failed'):
            self.run_automatic()
        self.tap.tapping_move.run_tap.assert_not_called()
        self.assertEqual(self.printer.bed.target, 0.)
        self.assertEqual(self.printer.nozzle.target, 0.)
        self.assertIsNone(self.module.candidate)
        self.assertFalse(any(v[0] == 'move' for v in self.printer.trace))

    def test_mesh_reloaded_by_homing_macro_is_cleared_again(self):
        mesh = mock.Mock()
        active = [object()]
        mesh.get_mesh.side_effect = lambda: active[0]
        mesh.set_mesh.side_effect = lambda value: active.__setitem__(0, value)
        self.printer.objects['bed_mesh'] = mesh
        def script(value):
            self.printer.script(value)
            if value == 'G28':
                active[0] = object()
        self.printer.gcode.run_script_from_command.side_effect = script
        self.run_automatic()
        scripts = [v[1] for v in self.printer.trace if v[0] == 'gcode']
        self.assertEqual(scripts, ['BED_MESH_CLEAR', 'G28', 'BED_MESH_CLEAR'])
        self.assertIsNone(mesh.get_mesh())

    def test_invalid_temperature_rejected_before_homing_or_heating(self):
        with self.assertRaisesRegex(RuntimeError, 'BED_TEMP'):
            self.module.cmd_AUTO_Z_OFFSET(Gcmd(params={'BED_TEMP': 49.}))
        self.printer.gcode.run_script_from_command.assert_not_called()
        self.assert_no_motion_or_heat()

    def test_explicit_temperature_overrides_default_50_170(self):
        self.module.cmd_AUTO_Z_OFFSET(Gcmd(
            params={'BED_TEMP': 65., 'NOZZLE_TEMP': 180.}))
        heats = [v[2] for v in self.printer.trace if v[0] == 'heat']
        self.assertEqual(heats, [65., 180., 0., 0.])

    def test_manual_contact_still_requires_homing_and_mesh_clear(self):
        self.printer.toolhead.homed = ''
        with self.assertRaisesRegex(RuntimeError, 'Home XYZ'):
            self.module.cmd_TEST_CONTACT(Gcmd())
        self.printer.gcode.run_script_from_command.assert_not_called()
        self.assert_no_motion_or_heat()


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.printer = Printer()
    def passive_diagnostic(self, calibrated=True):
        cell = lcprobe.load_cell.LoadCell.__new__(lcprobe.load_cell.LoadCell)
        cell.sensor = self.printer.cell.sensor
        cell.counts_per_gram = 100. if calibrated else None
        cell.reference_tare_counts = 1000 if calibrated else None
        cell.tare_counts = 500
        cell.invert = -1.
        collector = mock.Mock()
        collector.stop_collecting.return_value = (
            [(i / 80., 0., raw) for i, raw in enumerate((800, 900, 1000))], 0)
        cell.get_collector = mock.Mock(return_value=collector)
        helper = lcprobe.load_cell.LoadCellCommandHelper.__new__(
            lcprobe.load_cell.LoadCellCommandHelper)
        helper.printer, helper.load_cell = self.printer, cell
        gcmd = Gcmd()
        helper.cmd_LOAD_CELL_DIAGNOSTIC(gcmd)
        return cell, collector, gcmd

    def test_passive_diagnostic_uses_saved_reference_and_orientation(self):
        cell, collector, gcmd = self.passive_diagnostic()
        messages = [call.args[0] for call in gcmd.respond_info.call_args_list]
        self.assertIn('Raw counts: min 800, mean 900.00, max 1000', messages)
        self.assertIn('Reference mean force: 1.00 g '
                      '(reference tare: 1000 counts); '
                      'noise: 2.00 g peak-to-peak', messages)
        self.assertEqual(cell.tare_counts, 500)
        self.assertEqual(cell.reference_tare_counts, 1000)
        self.assertEqual(self.printer.trace, [])
        collector.stop_collecting.assert_called_once()

    def test_uncalibrated_passive_diagnostic_still_reports_raw_counts(self):
        cell, collector, gcmd = self.passive_diagnostic(calibrated=False)
        messages = [call.args[0] for call in gcmd.respond_info.call_args_list]
        self.assertIn('Raw counts: min 800, mean 900.00, max 1000', messages)
        self.assertFalse(any('Reference mean force' in m for m in messages))
        self.assertIsNone(cell.reference_tare_counts)
        self.assertEqual(self.printer.trace, [])

    def diagnostic_move(self):
        move = lcprobe.LoadCellProbingMove.__new__(lcprobe.LoadCellProbingMove)
        move._printer = self.printer
        move._load_cell = self.printer.cell
        move._name = 'auto_z_offset'
        move._log_trace = True
        move._last_safety = None
        move._safety_reference_counts = None
        move._mcu = self.printer.cell.sensor.get_mcu()
        move._config_helper = lcprobe.LoadCellProbeConfigHelper(
            Config(self.printer), self.printer.cell)
        move._continuous_tare_filter_helper = mock.Mock()
        move._mcu_trigger_analog = mock.Mock()
        collector = mock.Mock()
        move._start_collector = mock.Mock(return_value=collector)
        return move, collector

    def test_compression_margin_rejects_normal_orientation_before_arming(self):
        move, collector = self.diagnostic_move()
        collector.collect_min.return_value = ([(0., 0., 16000.)] * 6, 0)
        with self.assertLogs(level='INFO'):
            with self.assertRaisesRegex(RuntimeError,
                                        '50.00 g available, 75.00 g trigger'):
                move._pause_and_tare(Gcmd())
        move._mcu_trigger_analog.set_raw_range.assert_not_called()
        move._mcu_trigger_analog.set_trigger.assert_not_called()
        self.assertFalse(hasattr(self.printer.cell, 'tare_counts'))
        self.assertEqual(self.printer.cell.reference, 1000)
        collector.stop_collecting.assert_called_once()

    def test_owner_inverted_point_four_tare_has_less_margin_than_trigger(self):
        move, collector = self.diagnostic_move()
        self.printer.cell.invert = -1.
        self.printer.cell.counts_per_gram = 114.88636
        self.printer.cell.reference = -436490
        collector.collect_min.return_value = ([(0., 0., -451195)] * 6, 0)
        with self.assertLogs(level='INFO') as logs:
            with self.assertRaisesRegex(RuntimeError, 'compression margin'):
                move._pause_and_tare(Gcmd())
        report = lcprobe.json.loads(
            logs.output[0].split('Load cell safety: ')[1])
        self.assertEqual((report['raw_min'], report['raw_max']),
                         (-459467, -413513))
        self.assertAlmostEqual(report['baseline_force'], 128., delta=.01)
        self.assertAlmostEqual(report['compression_margin_grams'], 72.,
                               delta=.01)
        self.assertEqual(report['force_safety_limit'], 200.)
        self.assertEqual(report['trigger_force'], 75.)
        move._mcu_trigger_analog.set_raw_range.assert_not_called()

    def test_adequate_margin_keeps_reference_centered_safety_range(self):
        move, collector = self.diagnostic_move()
        collector.collect_min.return_value = ([(0., 0., 11000.)] * 6, 0)
        with self.assertLogs(level='INFO'):
            move._pause_and_tare(Gcmd())
        move._mcu_trigger_analog.set_raw_range.assert_called_once_with(
            -19000, 21000)
        self.assertEqual(self.printer.cell.reference, 1000)
        self.assertEqual(self.printer.cell.tare_counts, 11000)

    def test_owner_hot_tare_uses_point_reference_with_full_force_limit(self):
        move, collector = self.diagnostic_move()
        self.printer.cell.invert = -1.
        self.printer.cell.counts_per_gram = 114.88636
        self.printer.cell.reference = -436490
        collector.collect_min.return_value = ([(0., 0., -451195)] * 6, 0)
        move.set_safety_reference(-451195)
        with self.assertLogs(level='INFO'):
            move._pause_and_tare(Gcmd())
        move._mcu_trigger_analog.set_raw_range.assert_called_once_with(
            -474172, -428218)
        move._mcu_trigger_analog.set_trigger.assert_called_once_with(
            'abs_ge', int(75 * lcprobe.FRAC_GRAMS_CONV))
        self.assertAlmostEqual(move._last_safety['compression_margin_grams'],
                               200., delta=.01)
        self.assertEqual(move._last_safety['safety_reference_counts'], -451195)
        self.assertEqual(self.printer.cell.reference, -436490)

    def test_local_retare_cannot_move_point_safety_limit_between_contacts(self):
        move, collector = self.diagnostic_move()
        move.set_safety_reference(1000)
        collector.collect_min.return_value = ([(0., 0., 1000)] * 6, 0)
        with self.assertLogs(level='INFO'):
            move._pause_and_tare(Gcmd())
        collector.collect_min.return_value = ([(0., 0., 16000)] * 6, 0)
        with self.assertLogs(level='INFO'):
            with self.assertRaisesRegex(RuntimeError, 'compression margin'):
                move._pause_and_tare(Gcmd())
        move._mcu_trigger_analog.set_raw_range.assert_called_once_with(
            -19000, 21000)
        self.assertEqual(move._safety_reference_counts, 1000)
        self.assertEqual(self.printer.cell.tare_counts, 1000)

    def test_point_reference_must_leave_real_adc_headroom(self):
        move, collector = self.diagnostic_move()
        for value in (float('nan'), float('inf'), 8380000, -8380000):
            with self.subTest(value=value):
                with self.assertRaises(RuntimeError):
                    move.set_safety_reference(value)
        move._mcu_trigger_analog.set_raw_range.assert_not_called()
        self.assertIsNone(move._safety_reference_counts)

    def test_clearing_point_reference_restores_generic_probe_behavior(self):
        move, collector = self.diagnostic_move()
        move.set_safety_reference(11000)
        move.clear_safety_reference()
        collector.collect_min.return_value = ([(0., 0., 1000)] * 6, 0)
        with self.assertLogs(level='INFO'):
            move._pause_and_tare(Gcmd())
        move._mcu_trigger_analog.set_raw_range.assert_called_once_with(
            -19000, 21000)
        self.assertIsNone(move._safety_reference_counts)

    def failed_diagnostic_move(self):
        move, collector = self.diagnostic_move()
        move._pause_and_tare = mock.Mock()
        move._z_min_position = -3.
        move._param_helper = mock.Mock()
        move._param_helper.get_probe_params.return_value = {'probe_speed': .5}
        homing = mock.Mock()
        homing.probing_move.side_effect = RuntimeError('RAW_RANGE')
        self.printer.objects['homing'] = homing
        return move, collector

    def test_failed_approach_logs_bounded_raw_samples_and_full_extrema(self):
        move, collector = self.failed_diagnostic_move()
        samples = [(i / 80., 0., 1000 + i) for i in range(6000)]
        samples[17] = (17 / 80., 0., -23000)
        collector.stop_collecting.return_value = (samples, (1, 2))
        with self.assertLogs(level='INFO') as logs:
            with self.assertRaisesRegex(RuntimeError, 'RAW_RANGE'):
                move.probing_move(Gcmd())
        report = lcprobe.json.loads(
            logs.output[0].split('Load cell probe failure: ')[1])
        self.assertEqual(report['observed_raw_min'], -23000)
        self.assertEqual(report['observed_raw_max'], 6999)
        self.assertEqual(report['sample_count'], 6000)
        self.assertEqual(report['sensor_errors'], [1, 2])
        self.assertLessEqual(len(report['time_counts']), 257)
        self.assertEqual(report['time_counts'][-1], [5999 / 80., 6999])
        collector.stop_collecting.assert_called_once()

    def test_failure_logging_cannot_replace_original_mcu_error(self):
        move, collector = self.failed_diagnostic_move()
        collector.stop_collecting.return_value = ([], 0)
        move._log_probe_failure = mock.Mock(side_effect=RuntimeError('log'))
        with self.assertLogs(level='ERROR'):
            with self.assertRaisesRegex(RuntimeError, 'RAW_RANGE'):
                move.probing_move(Gcmd())
        collector.stop_collecting.assert_called_once()

    def test_global_fit_beats_nonconvex_search_failure(self):
        rng = random.Random(42)
        for case in range(4):
            contact = .05 + .35 * rng.random()
            slope = 100. + 900. * rng.random()
            noise = 1. + 10. * rng.random()
            drift = -30. + 60. * rng.random()
            data = [(slope * max(contact - i * .5 / 79, 0.)
                     + drift * i / 79 + rng.gauss(0., noise), i * .5 / 79)
                    for i in range(80)]
        fit = lcprobe.LCBestFit(self.printer)
        contact, below, above, slope = fit.find_best_fit(data)
        samples = [(z, force) for force, z in data]
        score = fit._calc_least_squares(samples, contact)[0]
        grid_score = min(fit._calc_least_squares(samples, i * .5 / 800)[0]
                         for i in range(1, 800))
        self.assertLessEqual(score, grid_score + 1.e-6)
        self.assertAlmostEqual(contact, .0551418, places=6)
        self.assertLess(slope, 0.)
        self.assertGreaterEqual(min(below, above), 3)
        # The former search converged near the opposite end of this trace.
        old_score = fit._calc_least_squares(samples, .4936676025390625)[0]
        self.assertGreater(old_score - score, 1000.)
    def test_global_fit_handles_translations_and_step_plateaus(self):
        fit = lcprobe.LCBestFit(self.printer)
        for shift in (-20., 0., 200.):
            data = [(1.e6 + max(.037 - (i // 2) * .0025, 0.) * 2000.,
                     shift + (i // 2) * .0025) for i in range(100)]
            contact, below, above, slope = fit.find_best_fit(data)
            self.assertAlmostEqual(contact, shift + .037, places=6)
            self.assertAlmostEqual(slope, -2000., places=4)
            self.assertGreaterEqual(min(below, above), 3)
    def test_global_fit_matches_matrix_grid_for_noisy_traces(self):
        fit = lcprobe.LCBestFit(self.printer)
        for contact in (.04, .12, .22, .34, .44):
            data = [(max(contact - i * .5 / 47, 0.) * 60. / contact
                     + 2. * math.sin(i * 1.17) + math.cos(i * .3),
                     i * .5 / 47) for i in range(48)]
            estimate, below, above, slope = fit.find_best_fit(data)
            samples = [(z, force) for force, z in data]
            score = fit._calc_least_squares(samples, estimate)[0]
            grid_score = min(fit._calc_least_squares(samples, i / 1000.)[0]
                             for i in range(1, 500))
            self.assertLessEqual(score, grid_score + 1.e-6)
            self.assertLess(slope, 0.)
    def test_global_fit_rejects_reversed_position_history(self):
        fit = lcprobe.LCBestFit(self.printer)
        with self.assertRaisesRegex(RuntimeError, 'Non-monotonic'):
            fit.find_best_fit([(30., 0.), (20., .1), (0., .05), (0., .2)])
    def test_reversed_force_orientation_is_not_a_valid_release(self):
        tap = lcprobe.TappingMove(Config(self.printer), mock.Mock(),
                                  mock.Mock())
        tap._load_cell_probing_move._mcu.is_fileoutput.return_value = False
        samples = [(i / 80., -max(0., .015 - i / 800.) * 10000., 0.)
                   for i in range(24)]
        with mock.patch.object(lcprobe, '_lookup_z_pos',
                               side_effect=lambda th, t: t * .1):
            with self.assertRaisesRegex(RuntimeError, 'Invalid load cell'):
                tap._analyze_ascent(Gcmd(), samples, 0., None, 0.)
    def test_automatic_trace_logging_preserves_replay_coordinates(self):
        tap = lcprobe.TappingMove(Config(self.printer), mock.Mock(),
                                  mock.Mock(), log_trace=True)
        tap._load_cell_probing_move._mcu.is_fileoutput.return_value = False
        samples = [(i / 80., max(0., .015 - i / 800.) * 10000., 0.)
                   for i in range(24)]
        with mock.patch.object(lcprobe, '_lookup_z_pos',
                               side_effect=lambda th, t: t * .1):
            with self.assertLogs(level='INFO') as logs:
                tap._analyze_ascent(Gcmd(), samples, 0., None, 0.)
        trace = lcprobe.json.loads(
            logs.output[0].split('Load cell ascent: ')[1])
        result = lcprobe.json.loads(logs.output[1].split('Load cell fit: ')[1])
        self.assertEqual(trace['sample_count'], 24)
        self.assertEqual(trace['sample_times'], [s[0] for s in samples])
        self.assertEqual(len(trace['force_z']), 24)
        self.assertAlmostEqual(result['contact_z'], .015, places=5)
    def test_automatic_trace_logging_is_bounded_and_keeps_endpoints(self):
        tap = lcprobe.TappingMove(Config(self.printer), mock.Mock(),
                                  mock.Mock(), ascent_sample_time=5.,
                                  log_trace=True)
        tap._load_cell_probing_move._mcu.is_fileoutput.return_value = False
        samples = [(i / 2000., max(0., .08 - i / 20000.) * 1000., 0.)
                   for i in range(6000)]
        with mock.patch.object(lcprobe, '_lookup_z_pos',
                               side_effect=lambda th, t: t * .1):
            with self.assertLogs(level='INFO') as logs:
                tap._analyze_ascent(Gcmd(), samples, 0., None, 0.)
        trace = lcprobe.json.loads(
            logs.output[0].split('Load cell ascent: ')[1])
        self.assertEqual(trace['sample_count'], 6000)
        self.assertLessEqual(len(trace['force_z']), 4096)
        self.assertEqual(trace['sample_times'][0], samples[0][0])
        self.assertEqual(trace['sample_times'][-1], samples[-1][0])
    def test_actual_piecewise_estimator_and_release_analysis(self):
        tap = lcprobe.TappingMove.__new__(lcprobe.TappingMove)
        tap._printer = self.printer
        tap._load_cell_probing_move = mock.Mock()
        tap._load_cell_probing_move._mcu.is_fileoutput.return_value = False
        tap._best_fit = lcprobe.LCBestFit(self.printer)
        tap._ascent_sample_time = .3
        tap._log_trace = False
        samples = [(i / 80., max(0., .015 - i / 800.) * 10000., 0.)
                   for i in range(24)]
        with mock.patch.object(lcprobe, '_lookup_z_pos',
                               side_effect=lambda th, t: t * .1):
            result = tap._analyze_ascent(Gcmd(), samples, 0., None, 0.)
            self.assertAlmostEqual(result, .015, places=4)
            flat = [(s[0], 0., 0.) for s in samples]
            with self.assertRaisesRegex(RuntimeError, 'Invalid load cell'):
                tap._analyze_ascent(Gcmd(), flat, 0., None, 0.)
            samples[5] = (samples[5][0], float('nan'), 0.)
            with self.assertRaisesRegex(RuntimeError, 'Non-finite'):
                tap._analyze_ascent(Gcmd(), samples, 0., None, 0.)
    def test_slow_release_uses_bounded_longer_window(self):
        tap = lcprobe.TappingMove.__new__(lcprobe.TappingMove)
        tap._printer = self.printer
        tap._load_cell_probing_move = mock.Mock()
        tap._load_cell_probing_move._mcu.is_fileoutput.return_value = False
        tap._best_fit = lcprobe.LCBestFit(self.printer)
        tap._log_trace = False
        samples = [(i / 80., max(0., .08 - i / 800.) * 1000., 0.)
                   for i in range(400)]
        # A later unrelated sample must not enter the bounded fit window.
        samples.append((5.1, float('nan'), 0.))
        with mock.patch.object(lcprobe, '_lookup_z_pos',
                               side_effect=lambda th, t: t * .1):
            tap._ascent_sample_time = .3
            with self.assertRaises(RuntimeError):
                tap._analyze_ascent(Gcmd(), samples, 0., None, 0.)
            tap._ascent_sample_time = 5.
            result = tap._analyze_ascent(Gcmd(), samples, 0., None, 0.)
            self.assertAlmostEqual(result, .08, places=4)
    def test_collector_stopped_when_mcu_probing_fails(self):
        move = lcprobe.LoadCellProbingMove.__new__(
            lcprobe.LoadCellProbingMove)
        move._printer = self.printer
        move._log_trace = False
        move._load_cell = self.printer.cell
        move._pause_and_tare = mock.Mock()
        move._z_min_position = -3.
        move._param_helper = mock.Mock()
        move._param_helper.get_probe_params.return_value = {'probe_speed': .5}
        move._mcu_trigger_analog = object()
        collector = mock.Mock()
        move._start_collector = mock.Mock(return_value=collector)
        homing = mock.Mock()
        homing.probing_move.side_effect = RuntimeError('MCU force limit')
        self.printer.objects['homing'] = homing
        with self.assertRaisesRegex(RuntimeError, 'MCU force limit'):
            move.probing_move(Gcmd())
        collector.stop_collecting.assert_called_once()
        self.assertIs(homing.probing_move.call_args.args[0],
                      move._mcu_trigger_analog)
    def test_force_trigger_z_survives_regression_replacement(self):
        self.printer.toolhead.position = [25., 28., -.3375, 0.]
        move = mock.Mock()
        move.probing_move.return_value = (
            [25., 28., -.3375], mock.Mock())
        move._param_helper.get_probe_params.return_value = {
            'load_cell_retract_dist': .5, 'lift_speed': .1}
        move.probing_move.return_value[1].collect_until.return_value = (
            [(0., 75., 0.), (5., 0., 0.)], 0)
        self.printer.objects['homing'] = mock.Mock()
        self.printer.objects['homing'].check_probe_first_home.return_value = (
            False)
        self.printer.toolhead.get_last_move_time = mock.Mock(
            side_effect=[0., 5.])
        tap = lcprobe.TappingMove(Config(self.printer), move, mock.Mock())
        tap._analyze_ascent = mock.Mock(return_value=-.233463)
        result, valid = tap.run_tap(Gcmd())
        self.assertTrue(valid)
        self.assertAlmostEqual(result[2], -.233463)
        status = tap.get_status(0.)
        self.assertAlmostEqual(status['last_trigger_z'], -.3375)
        self.assertAlmostEqual(status['last_z_result'], -.233463)
    def test_tap_failure_clears_last_valid_state_and_collector(self):
        tap = lcprobe.TappingMove.__new__(lcprobe.TappingMove)
        tap._last_result, tap._is_last_result_valid = 1., True
        tap._last_trigger_z = .08
        collector = mock.Mock()
        tap._collector = collector
        tap._run_tap = mock.Mock(side_effect=RuntimeError('sensor error'))
        with self.assertRaisesRegex(RuntimeError, 'sensor error'):
            tap.run_tap(Gcmd())
        self.assertFalse(tap._is_last_result_valid)
        self.assertIsNone(tap._last_result)
        self.assertIsNone(tap._last_trigger_z)
        collector.stop_collecting.assert_called_once()
    def test_shared_factory_registers_no_primary_probe_or_second_adc(self):
        config = Config(self.printer)
        with mock.patch.object(lcprobe.trigger_analog, 'MCU_trigger_analog') \
                as trigger, mock.patch.object(lcprobe.trigger_analog,
                'MCU_SosFilter'), mock.patch.object(lcprobe,
                'LoadCellProbingMove') as moving:
            helper = lcprobe.LoadCellTapHelper(config, self.printer.cell)
        trigger.assert_called_once_with(self.printer.cell.sensor)
        self.assertIs(helper.load_cell, self.printer.cell)
        self.assertIs(moving.call_args.args[1], self.printer.cell)
        self.assertEqual(helper.tapping_move._ascent_sample_time, .3)
        self.assertIs(self.printer.objects['probe'], self.printer.primary)
        self.assertEqual(self.printer.gcode.commands, {})
        self.printer.objects['pins'].register_chip.assert_not_called()
    def test_primary_load_cell_probe_keeps_single_sensor_and_registration(self):
        del self.printer.objects['probe']
        sensor_factory = mock.Mock(return_value=self.printer.cell.sensor)
        with mock.patch.dict(lcprobe.hx71x.HX71X_SENSOR_TYPES,
                {'hx711': sensor_factory}), mock.patch.object(
                lcprobe.load_cell, 'LoadCell', return_value=self.printer.cell) \
                as cell_factory, mock.patch.object(lcprobe.trigger_analog,
                'MCU_trigger_analog'), mock.patch.object(
                lcprobe.trigger_analog, 'MCU_SosFilter'):
            primary = lcprobe.LoadCellPrinterProbe(Config(self.printer,
                {'sensor_type': 'hx711'}))
        sensor_factory.assert_called_once()
        cell_factory.assert_called_once()
        self.assertIs(self.printer.objects['probe'], primary)
        self.printer.objects['pins'].register_chip.assert_called_once()
        self.assertIn('PROBE', self.printer.gcode.commands)
        self.assertIn('LOAD_CELL_TEST_TAP', self.printer.gcode.commands)
    def test_tare_uses_existing_collector_range_and_mcu_trigger(self):
        move = lcprobe.LoadCellProbingMove.__new__(
            lcprobe.LoadCellProbingMove)
        move._printer = self.printer
        move._log_trace = False
        move._safety_reference_counts = None
        move._load_cell = self.printer.cell
        move._mcu = self.printer.cell.sensor.get_mcu()
        move._config_helper = lcprobe.LoadCellProbeConfigHelper(
            Config(self.printer), self.printer.cell)
        move._continuous_tare_filter_helper = mock.Mock()
        move._mcu_trigger_analog = mock.Mock()
        collector = mock.Mock()
        collector.collect_min.return_value = (
            [(i / 80., 0., 1000. + i) for i in range(6)], 0)
        move._start_collector = mock.Mock(return_value=collector)
        move._pause_and_tare(Gcmd())
        self.assertEqual(self.printer.cell.tare_counts, 1002)
        move._mcu_trigger_analog.set_trigger.assert_called_once_with(
            'abs_ge', int(75. * lcprobe.FRAC_GRAMS_CONV))
        move._mcu_trigger_analog.set_raw_range.assert_called_once_with(
            -19000, 21000)
        collector.stop_collecting.assert_called_once()
        collector.collect_min.return_value = (
            [(i / 80., 0., 30000.) for i in range(6)], 0)
        with self.assertRaisesRegex(RuntimeError, 'outside force limit'):
            move._pause_and_tare(Gcmd())
        collector.collect_min.return_value = ([], (1, 0))
        with self.assertRaisesRegex(RuntimeError, 'sensor reported errors'):
            move._pause_and_tare(Gcmd())
    def test_tare_collector_closed_on_collection_exception(self):
        move = lcprobe.LoadCellProbingMove.__new__(
            lcprobe.LoadCellProbingMove)
        move._printer = self.printer
        move._log_trace = False
        move._load_cell = self.printer.cell
        move._config_helper = mock.Mock()
        collector = mock.Mock()
        collector.collect_min.side_effect = RuntimeError('timeout')
        move._start_collector = mock.Mock(return_value=collector)
        with self.assertRaisesRegex(RuntimeError, 'timeout'):
            move._pause_and_tare(Gcmd())
        collector.stop_collecting.assert_called_once()
    def test_diagnostic_payload_preserves_json_values_without_numpy(self):
        payload = lcprobe.TapAnalysis([(1., 2., 3.), (4., 5., 6.)]).to_dict()
        self.assertEqual(payload, {'time': [1., 4.], 'force': [2., 5.],
                                   'is_valid': True})


if __name__ == '__main__':
    unittest.main()
