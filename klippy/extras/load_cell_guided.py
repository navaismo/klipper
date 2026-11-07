# Guided, report-only load cell acquisition; no calibration application path
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import copy
import json
import logging
import math
import uuid
from . import load_cell_calibration as calibration

OBJECT_NAME = 'load_cell_diagnostic'
COMMANDS = ('TARE', 'CALIBRATE', 'CALIBRATE_ZERO', 'CALIBRATE_REPORT', 'ABORT')
PHASES = {'TARE': ('before', 'WAITING_UNLOADED_BEFORE',
                   'ACQUIRING_UNLOADED_BEFORE'),
          'CALIBRATE': ('loaded', 'WAITING_LOADED', 'ACQUIRING_LOADED'),
          'CALIBRATE_ZERO': ('after', 'WAITING_UNLOADED_AFTER',
                            'ACQUIRING_UNLOADED_AFTER')}


def finite(value):
    if isinstance(value, bool):
        raise ValueError('Boolean is not a measurement')
    value = float(value)
    if math.isnan(value) or math.isinf(value):
        raise ValueError('Non-finite numeric value')
    return value


def start(printer, cell, gcmd):
    try:
        manager = printer.lookup_object(OBJECT_NAME, None)
        if manager is None:
            manager = DiagnosticManager(printer)
            printer.add_object(OBJECT_NAME, manager)
        manager.start(cell, gcmd)
    except printer.command_error:
        raise
    except Exception as exc:
        raise gcmd.error('Diagnostic initialization failed: %s' % (exc,))


class DiagnosticManager:
    def __init__(self, printer):
        self.printer = printer
        self.gcode = printer.lookup_object('gcode')
        self.reactor = printer.get_reactor()
        self.session = None
        self.state = 'IDLE'
        self.report = None
        for name in ('klippy:disconnect', 'klippy:shutdown',
                     'homing:home_axes'):
            printer.register_event_handler(name, self.interrupt)
        for name in ('load_cell:tare', 'load_cell:calibrate'):
            printer.register_event_handler(name, self.sensor_changed)

    def check_idle(self):
        now = self.reactor.monotonic()
        for name, field, forbidden in (
                ('print_stats', 'state', ('printing', 'paused')),
                ('pause_resume', 'is_paused', (True,)),
                ('virtual_sdcard', 'is_active', (True,))):
            obj = self.printer.lookup_object(name, None)
            if obj is not None and obj.get_status(now).get(field) in forbidden:
                raise self.printer.command_error('Diagnostic requires idle')
        # Read scheduling state only; never flush, drain or queue motion.
        toolhead = self.printer.lookup_object('toolhead')
        planned, estimated, empty = toolhead.check_busy(now)
        if not empty or planned > estimated:
            raise self.printer.command_error('Diagnostic requires no motion')
        for name in ('auto_z_offset_cr', 'auto_z_offset_ft'):
            auto = self.printer.lookup_object(name, None)
            if auto is not None:
                status = auto.get_status(now)
                if (status.get('busy') or status.get('state')
                        in ('heating', 'measuring')):
                    raise self.printer.command_error('Auto Z-offset is busy')
        return planned, tuple(toolhead.get_position())

    def start(self, cell, gcmd):
        if self.session is not None:
            raise gcmd.error('A diagnostic session already owns the commands')
        # Includes ACCEPT so an existing manual/legacy helper is not hijacked.
        occupied = self.gcode.get_status(self.reactor.monotonic())['commands']
        if any(name in occupied for name in COMMANDS + ('ACCEPT',)):
            raise gcmd.error('Another calibration helper owns the commands')
        self.report = None
        self.state = 'REJECTED'
        motion = self.check_idle()
        session = DiagnosticSession(self, cell, gcmd, motion)
        self.report = None
        self.session = session
        self.state = session.state
        try:
            for name in COMMANDS:
                def handler(cmd, owner=session, command=name):
                    owner.command(command, cmd)
                self.gcode.register_command(
                    name, handler, desc='Report-only load cell calibration')
                session.commands.append(name)
            gcmd.respond_info(
                'DIAGNOSTIC session %s: remove load, then TARE; '
                'apply %.3f g, CALIBRATE; remove load, CALIBRATE_ZERO. '
                'Repeat three cycles. No calibration will be applied.'
                % (session.identifier, session.mass))
        except Exception:
            session.finish('REJECTED', 'Session initialization failed')
            raise

    def interrupt(self, *args):
        if self.session is not None:
            self.session.finish('ABORTED', 'Printer state interrupted session')
        else:
            self.report = None
            self.state = 'IDLE'

    def sensor_changed(self, cell):
        if self.session is not None and self.session.cell is cell:
            self.session.finish('REJECTED',
                                'Sensor calibration or tare changed')
        elif self.session is None:
            self.report = None
            self.state = 'IDLE'

    def get_status(self, eventtime):
        return {'state': self.state,
                'session_id': (self.session.identifier if self.session
                               else None),
                'report': copy.deepcopy(self.report)}


class DiagnosticSession:
    def __init__(self, manager, cell, gcmd, motion):
        self.manager = manager
        self.printer = manager.printer
        self.reactor = manager.reactor
        self.cell = cell
        self.sensor_name = cell.config_name.split()[-1]
        self.identifier = uuid.uuid4().hex
        self.commands = []
        self.collector = None
        self.cycles = []
        self.partial = {}
        self.state = 'WAITING_UNLOADED_BEFORE'
        self.motion = motion
        self.acquisition = []
        self.error = None
        self.cleanup_error = None
        allowed = ('MODE', 'LOAD_CELL', 'GRAMS', 'MASS_TOLERANCE',
                   'SETTLE_TIME', 'RETURN_TOLERANCE', 'REPEATABILITY_TOLERANCE',
                   'REFERENCE_MAX_AGE')
        if set(gcmd.get_command_parameters()) - set(allowed):
            raise gcmd.error('Unknown diagnostic calibration parameter')
        try:
            self.mass = finite(gcmd.get_float('GRAMS', above=0.))
            self.tolerance = gcmd.get_float('MASS_TOLERANCE', None, minval=0.)
            if self.tolerance is not None:
                self.tolerance = finite(self.tolerance)
                if self.tolerance >= self.mass:
                    raise ValueError('Mass tolerance must be less than mass')
            self.settle = finite(gcmd.get_float('SETTLE_TIME', 2.,
                                               minval=0., maxval=30.))
            self.settings = self.sensor_settings()
            for value in self.settings[3:7]:
                if value is not None:
                    finite(value)
            low, high = cell.saturation_range()
            self.policy = calibration.CalibrationPolicy(
                adc_min=low, adc_max=high,
                expected_sample_rate=cell.sensor.get_samples_per_second(),
                max_return_fraction=finite(gcmd.get_float(
                    'RETURN_TOLERANCE', .01, above=0.)),
                max_repeatability_fraction=finite(gcmd.get_float(
                    'REPEATABILITY_TOLERANCE', .02, above=0.)),
                max_reference_age=finite(gcmd.get_float(
                    'REFERENCE_MAX_AGE', 120., above=0.)))
            if cell.invert not in (-1., 1.):
                raise ValueError('Invalid load cell orientation')
        except (ValueError, TypeError, OverflowError) as exc:
            raise gcmd.error('Invalid diagnostic parameters: %s' % (exc,))

    def sensor_settings(self):
        sensor = self.cell.sensor
        return (sensor, sensor.get_samples_per_second(),
                getattr(sensor, 'gain_channel', None),
                self.cell.invert, self.cell.get_counts_per_gram(),
                self.cell.get_reference_tare_counts(),
                self.cell.get_tare_counts(),
                tuple(self.cell.saturation_range()))

    def check_owner(self):
        if self.manager.session is not self:
            raise self.printer.command_error('Diagnostic session is no longer '
                                             'active')

    def check_state(self):
        self.check_owner()
        if self.sensor_settings() != self.settings:
            raise self.printer.command_error('Sensor settings changed')
        if self.manager.check_idle() != self.motion:
            raise self.printer.command_error('Motion reference changed')

    def command(self, name, gcmd):
        self.check_owner()
        if gcmd.get('SESSION', self.identifier) != self.identifier:
            raise gcmd.error('Diagnostic session identifier mismatch')
        try:
            allowed = (('SESSION', 'GRAMS') if name == 'CALIBRATE'
                       else ('SESSION',))
            if set(gcmd.get_command_parameters()) - set(allowed):
                raise gcmd.error('Unsupported diagnostic command parameter')
            if name == 'ABORT':
                self.finish('ABORTED', 'Operator aborted diagnostic')
                gcmd.respond_info('Diagnostic aborted; calibration unchanged')
                return
            self.check_state()
            if name == 'CALIBRATE_REPORT':
                self.publish(gcmd)
                return
            phase, waiting, acquiring = PHASES[name]
            if self.state != waiting or phase in self.partial:
                raise gcmd.error('Wrong diagnostic sequence: %s, expected %s'
                                 % (name, self.state))
            if name == 'CALIBRATE' and finite(gcmd.get_float(
                    'GRAMS', self.mass)) != self.mass:
                raise gcmd.error('Reference mass is fixed for this session')
            self.state = self.manager.state = acquiring
            self.partial[phase] = self.acquire()
            self.check_state()
            report = self.analyze()
            last = report['cycles'][-1]
            if not last['windows'][phase]['valid']:
                raise gcmd.error('Acquisition rejected: %s' % ', '.join(
                    last['windows'][phase]['rejections']))
            if phase == 'after':
                self.state = self.manager.state = 'CYCLE_COMPLETE'
                if not last['quality_passed']:
                    raise gcmd.error('Cycle rejected: %s' % ', '.join(
                        last['rejections']))
                self.cycles.append(self.partial)
                self.partial = {}
                if len(self.cycles) == 3:
                    self.state = self.manager.state = 'ANALYZING'
                    report = self.analyze()
                    state = ('COMPLETED' if report['quality_passed']
                             else 'REJECTED')
                    self.finish(state)
                    self.publish(gcmd)
                    return
                self.state = 'WAITING_UNLOADED_BEFORE'
            else:
                self.state = ('WAITING_LOADED' if phase == 'before'
                              else 'WAITING_UNLOADED_AFTER')
            self.manager.state = self.state
            self.manager.report = self.analyze()
            gcmd.respond_info('Diagnostic cycle %d: %s' % (
                len(self.cycles) + 1, self.state))
        except Exception as exc:
            if self.manager.session is self:
                self.finish('REJECTED', str(exc))
                try:
                    self.publish(gcmd)
                except Exception:
                    logging.exception('Diagnostic report output failed')
            if isinstance(exc, self.printer.command_error):
                raise
            raise gcmd.error('Diagnostic acquisition failed: %s' % (exc,))

    def release_collector(self):
        collector, self.collector = self.collector, None
        if collector is not None:
            try:
                collector.stop_collecting()
            except Exception as exc:
                self.cleanup_error = str(exc)
                logging.exception('Diagnostic collector cleanup failed')

    def acquire(self):
        self.reactor.pause(self.reactor.monotonic() + self.settle)
        self.check_state()
        sensor = self.cell.sensor
        mcu = sensor.get_mcu()
        if mcu.is_fileoutput():
            raise self.printer.command_error('Diagnostic needs real ADC data')
        start = mcu.estimated_print_time(self.reactor.monotonic())
        end = start + 10.
        before = sensor.get_status(self.reactor.monotonic())
        collector = self.collector = self.cell.get_collector()
        try:
            collector.start_collecting(min_time=start)
            rows, errors = collector.collect_until(end)
            self.check_state()
        finally:
            if self.collector is collector:
                self.release_collector()
        if self.cleanup_error:
            raise self.printer.command_error('Collector cleanup failed: %s'
                                             % (self.cleanup_error,))
        after = sensor.get_status(self.reactor.monotonic())
        error_count, overflows = errors if errors else (0, 0)
        for key in ('errors', 'overflows'):
            for status in (before, after):
                value = finite(status[key])
                if value < 0. or value != int(value):
                    raise self.printer.command_error('Invalid sensor counter')
            if after[key] < before[key]:
                raise self.printer.command_error('Sensor counters reset')
        error_count = max(error_count, after['errors'] - before['errors'])
        overflows = max(overflows, after['overflows'] - before['overflows'])
        pairs = []
        previous = None
        low, high = self.cell.saturation_range()
        saturated = 0
        for row in rows:
            time, count = finite(row[0]), row[2]
            if previous is not None and time <= previous:
                raise self.printer.command_error('Invalid sample ordering')
            previous = time
            if start <= time <= end:
                pairs.append([row[0], count])
                if count <= low or count >= high:
                    saturated += 1
        self.acquisition.append({'start_time': start, 'end_time': end,
                                 'samples': len(pairs)})
        edge = 3. / self.policy['expected_sample_rate']
        if (not pairs or pairs[0][0] > start + edge
                or pairs[-1][0] < end - edge):
            raise self.printer.command_error('Incomplete acquisition: '
                                             'missing 10-second coverage')
        return {'samples': pairs, 'errors': error_count, 'overflows': overflows,
                'saturated': saturated}

    def analyze(self):
        cycles = self.cycles + ([self.partial] if self.partial else [])
        report = calibration.analyze_calibration(
            cycles, self.mass, self.settings[3], self.tolerance, self.policy)
        reasons = report['rejections']
        complete = len(self.cycles) == 3 and not self.partial
        integrity = [w['valid'] for c in report['cycles']
                     for w in c.get('windows', {}).values()]
        baseline_bad = any('zero_return' in r or 'drift' in r or 'span' in r
                           or 'stale_' in r for r in reasons)
        report['checks'] = {
            'data_integrity': ('PASS' if complete and all(integrity)
                               else 'REJECT' if not all(integrity)
                               else 'PENDING'),
            'baseline_stability': ('REJECT' if baseline_bad else
                                   'PASS' if complete else 'PENDING'),
            'repeatability': ('REJECT' if 'repeatability_exceeded' in reasons
                              else 'PASS' if complete else 'PENDING')}
        report.update({'mode': 'DIAGNOSTIC', 'state': self.state,
                       'session_id': self.identifier,
                       'sensor': self.sensor_name,
                       'acquisition': list(self.acquisition),
                       'initial_active_calibration': {
                           'counts_per_gram': self.settings[4],
                           'reference_tare_counts': self.settings[5],
                           'tare_counts': self.settings[6]},
                       'active_calibration_unchanged': (
                           self.sensor_settings() == self.settings),
                       'calibration_applied': False,
                       'configuration_modified': False})
        return report

    def finish(self, state, error=None):
        if self.manager.session is not self:
            return
        self.state = self.manager.state = state
        self.error = error
        try:
            report = self.analyze()
            if state != 'COMPLETED':
                report['quality_passed'] = False
                report['usable_candidate_counts_per_gram'] = None
                if error:
                    report['rejections'].append(error)
            self.manager.report = report
        except Exception as exc:
            # Diagnostic reporting must not replace a lifecycle/sensor error.
            logging.exception('Diagnostic report construction failed')
            if self.state == 'COMPLETED':
                self.state = self.manager.state = 'REJECTED'
            report = calibration.analyze_calibration(
                [], self.mass, self.settings[3], self.tolerance, self.policy)
            report.update({'mode': 'DIAGNOSTIC', 'state': self.state,
                'session_id': self.identifier, 'sensor': self.sensor_name,
                'calibration_applied': False, 'configuration_modified': False,
                'checks': {key: 'REJECT' for key in (
                    'data_integrity', 'baseline_stability', 'repeatability')}})
            report['rejections'].append('Report generation failed: %s' % exc)
            self.manager.report = report
        finally:
            try:
                self.release_collector()
            finally:
                for name in self.commands:
                    self.manager.gcode.register_command(name, None)
                self.commands = []
                self.cycles = []
                self.partial = {}
                self.manager.session = None

    def publish(self, gcmd):
        report = self.manager.report or self.analyze()
        lines = ['GUIDED LOAD CELL CALIBRATION - DIAGNOSTIC',
                 'Sensor: %s; reference: %.3f g; cycles: %d; state: %s'
                 % (report['sensor'], self.mass, len(report['cycles']),
                    report['state'])]
        for cycle in report['cycles']:
            if 'counts_per_gram' in cycle:
                lines.append('Cycle %d: before %.3f, loaded %.3f, after %.3f; '
                    'delta %.3f counts; scale %.6f counts/g; return %s; %s'
                    % (cycle['index'], cycle['before_counts'],
                       cycle['loaded_counts'], cycle['after_counts'],
                       cycle['delta_counts'], cycle['counts_per_gram'],
                       ('%.3f%%' % (cycle['zero_return_fraction'] * 100.)
                        if 'zero_return_fraction' in cycle else 'UNKNOWN'),
                       'PASS' if cycle['quality_passed'] else 'REJECT'))
        lines.extend([
            'Consolidated scale: %s counts/g; variation: %s'
            % (report['consolidated_counts_per_gram'],
               report['cycle_variation_fraction']),
            'Mass tolerance: %s; numerical candidate: %s'
            % ('UNKNOWN' if self.tolerance is None else str(self.tolerance),
               'VALID' if report['quality_passed'] else 'REJECTED'),
            'Checks: %s' % json.dumps(report['checks'], sort_keys=True),
            'Observed ADC scale envelope: %s; combined mass envelope: %s'
            % (report['uncertainty']['adc_only_counts_per_gram_interval'],
               report['uncertainty']['combined_counts_per_gram_interval']),
            'Physical validation: NOT PERFORMED; authorization: NOT GRANTED',
            'Calibration applied: NO; configuration modified: NO',
            'Rejections: %s' % ', '.join(report['rejections']),
            'Diagnostic JSON: ' + json.dumps(report, allow_nan=False)])
        gcmd.respond_info('\n'.join(lines))
