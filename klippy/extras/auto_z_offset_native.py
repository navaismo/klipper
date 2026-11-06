# Native CR Touch / load-cell pressure-reference calibration
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import json
import logging
import math
from . import load_cell_probe, manual_probe, probe, trigger_analog


class NativeAutoZOffset:
    def __init__(self, config):
        self.printer = config.get_printer()
        if any(config.has_section(s) for s in
               ('auto_z_offset', 'load_cell_probe', 'prtouch')):
            raise config.error('Select [auto_z_offset_native] OR the existing '
                               'calibration backend, not both')
        if not config.has_section('bltouch'):
            raise config.error('[auto_z_offset_native] requires [bltouch]')
        self.gcode = self.printer.lookup_object('gcode')
        self.reactor = self.printer.get_reactor()
        self.primary = self.printer.load_object(config, 'bltouch')
        self.cell = self.printer.load_object(
            config, 'load_cell ' + config.get('load_cell'))
        if config.get('probe_points', None) is not None:
            if config.get('probe_point', None) is not None:
                raise config.error('Use probe_points or probe_point, not both')
            self.points = config.getlists('probe_points', seps=(',', '\n'),
                                          parser=float, count=2)
            if (len(self.points) < 3
                    or len(set(tuple(p) for p in self.points))
                       != len(self.points)):
                raise config.error(
                    'probe_points requires at least three distinct points')
        else:
            self.points = [config.getfloatlist('probe_point', count=2)]
        self.clearance = config.getfloat('horizontal_move_z', 5., above=0.)
        self.travel_speed = config.getfloat('travel_speed', 50., above=0.)
        self.bed_temp = config.getfloat('bed_temp', 60., minval=50.)
        self.nozzle_temp = config.getfloat('nozzle_temp', 170., minval=170.)
        self.temp_tolerance = config.getfloat(
            'temperature_tolerance', 3., above=0.)
        # Retain the current force/travel ceilings, not Marlin's ADC thresholds.
        force = config.getfloat('trigger_force', 75., minval=10., maxval=75.)
        limit = config.getfloat('force_safety_limit', 200., minval=100.,
                                maxval=200., above=force)
        self.params = probe.ProbeParameterHelper(config)
        self.retract = self.params.get_probe_params()['sample_retract_dist']
        values = [v for point in self.points for v in point] + [
            self.clearance, self.travel_speed,
            self.bed_temp, self.nozzle_temp, self.temp_tolerance,
            force, limit, self.retract,
            self.params.speed, self.params.lift_speed,
            self.params.samples_tolerance]
        if not all(self._finite(v) for v in values) or self.retract > .5:
            raise config.error('Native calibration requires finite parameters '
                               'and sample_retract_dist <= 0.5')
        # Construct only native raw probing; no TappingMove or fitted result.
        sensor = self.cell.get_sensor()
        self.trigger = trigger_analog.MCU_trigger_analog(sensor)
        cq = self.trigger.get_dispatch().get_command_queue()
        sf = trigger_analog.MCU_SosFilter(sensor.get_mcu(), cq, 4)
        self.trigger.setup_sos_filter(sf)
        filters = load_cell_probe.ContinuousTareFilterHelper(config, sensor, sf)
        settings = load_cell_probe.LoadCellProbeConfigHelper(config, self.cell)
        self.press_move = load_cell_probe.LoadCellProbingMove(
            config, self.cell, self.trigger, self.params, filters, settings,
            log_trace=True)
        self.busy = self.connected = False
        self.result = self.error = None
        self._lcd = None
        self._lcd_failed = False
        self._stage = 'Warming up'
        self.printer.register_event_handler('klippy:connect', self._connect)
        self.printer.register_event_handler(
            'klippy:disconnect', self._disconnect)
        self.printer.register_event_handler('klippy:shutdown', self._disconnect)
        for event in ('load_cell:calibrate', 'homing:home_rails_begin'):
            self.printer.register_event_handler(event, self._invalidate)
        self.gcode.register_command('AUTO_Z_OFFSET', self.cmd_AUTO_Z_OFFSET,
            desc='Native pressure-reference calibration; report only')

    @staticmethod
    def _finite(value):
        return not math.isnan(value) and not math.isinf(value)

    def _connect(self):
        self.toolhead = self.printer.lookup_object('toolhead')
        self.heaters = self.printer.lookup_object('heaters')
        self.bed = self.heaters.lookup_heater('heater_bed')
        self.nozzle = self.heaters.lookup_heater('extruder')
        self.mesh = self.printer.lookup_object('bed_mesh', None)
        self.connected = True

    def _disconnect(self, *args):
        self.press_move.clear_safety_reference()
        self.connected = False
        self.result = None

    def _invalidate(self, *args):
        self.result = None

    def _idle(self):
        if not self.connected or self.printer.is_shutdown():
            raise self.gcode.error('Native calibration is not connected')
        stats = self.printer.lookup_object('print_stats', None)
        if stats and stats.get_status(self.reactor.monotonic())['state'] in (
                'printing', 'paused'):
            raise self.gcode.error('Native calibration requires an idle job')
        pause = self.printer.lookup_object('pause_resume', None)
        sd = self.printer.lookup_object('virtual_sdcard', None)
        if ((pause and pause.is_paused)
                or (sd and (sd.is_active() or sd.preflight_pending))):
            raise self.gcode.error(
                'Native calibration excludes print preflight')
        if not self.cell.is_calibrated():
            raise self.gcode.error('Load cell must already be calibrated')

    def _check_context(self, targets, calibration, check_band=True):
        self._idle()
        if (self.mesh is not None
                and self.mesh.get_mesh() is not None):
            raise self.gcode.error('Bed mesh became active during calibration')
        current = (self.primary.get_offsets(), self.cell.get_counts_per_gram(),
                   self.cell.get_reference_tare_counts())
        if current != calibration:
            raise self.gcode.error('Calibration reference changed')
        in_band = True
        for heater, target in targets:
            measured, actual = heater.get_temp(self.reactor.monotonic())
            if (not self._finite(measured) or actual != target
                    or measured <= 0. or measured < heater.min_temp
                    or measured > heater.max_temp):
                raise self.gcode.error(
                    'Native calibration heater state changed')
            if abs(measured - target) <= self.temp_tolerance:
                continue
            in_band = False
            if check_band:
                raise self.gcode.error(
                    'Native calibration temperature out of range: '
                    '%s measured %.2f C, expected %.2f +/- %.2f C'
                    % (heater.get_name(), measured, target,
                       self.temp_tolerance))
        return in_band

    def _heat(self, gcmd, targets, calibration):
        # Native heater control/verification stays active. Only the wait
        # criterion differs from heater.check_busy(): both targets +/- margin.
        for heater, target in targets:
            self.heaters.set_temperature(heater, target, False)
        gcmd.respond_info('Native Auto Z warming up; waiting for both heaters '
                          'within +/- %.1f C' % self.temp_tolerance)
        while not self._check_context(targets, calibration, check_band=False):
            self.reactor.pause(self.reactor.monotonic() + .25)

    def _lcd_warning(self, gcmd, action, exc):
        logging.exception('Native Auto Z LCD %s failed', action)
        try:
            gcmd.respond_info('LCD calibration %s failed: %s' % (action, exc))
        except Exception:
            logging.exception('Native Auto Z LCD warning failed')

    def _begin_lcd(self, gcmd, bed, nozzle):
        self._lcd = None
        self._lcd_failed = False
        self._stage = 'Warming up'
        display = self.printer.lookup_object('tjc_lcd', None)
        if display is None:
            return
        try:
            if display.begin_calibration(self._stage, nozzle_target=nozzle,
                                         bed_target=bed):
                self._lcd = display
            else:
                gcmd.respond_info('LCD phase screen unavailable; '
                    'TJC_CANVAS_VERSION=1 or TJC_DRAW_VERSION=1 is needed')
        except Exception as exc:
            self._lcd_warning(gcmd, 'start', exc)

    def _update_lcd(self, gcmd, phase, text):
        self._stage = text
        if (self._lcd is None or self._lcd_failed or not self.connected
                or self.printer.is_shutdown()):
            return
        try:
            # Synchronous drawing finishes before any probe/tare collector.
            self._lcd.update_calibration(phase, text)
        except Exception as exc:
            # Keep the reservation until final cleanup; do not retry frames
            # during probing or let UI failures change pressure results.
            self._lcd_failed = True
            self._lcd_warning(gcmd, 'update', exc)

    def _end_lcd(self, gcmd):
        display = self._lcd
        self._lcd = None
        if display is None:
            return
        success = self.result is not None and self.error is None
        text = ('P1 %.3fmm - review' % self.result['provisional_z_offset']
                if success else 'Failed: ' + self._stage)
        try:
            display.end_calibration(text, success)
        except Exception as exc:
            self._lcd_warning(gcmd, 'finish', exc)

    def _move(self, position, speed):
        self.toolhead.manual_move(position, speed)
        self.toolhead.wait_moves()

    def _press(self, gcmd, floor, targets, calibration):
        self._check_context(targets, calibration)
        start = self.toolhead.get_position()[2]
        if not self._finite(start) or start != self.clearance or floor >= start:
            raise gcmd.error('Native pressure move has invalid start/bounds')
        collector = None
        try:
            position, collector = self.press_move.probing_move(
                gcmd, minimum_z=floor, maximum_descent=start-floor)
            stamp = self.trigger.get_last_trigger_time()
            if (not self._finite(position[2]) or not self._finite(stamp)
                    or stamp <= 0.):
                raise gcmd.error('Native MCU pressure trigger unavailable')
            # Release only after native probing returned a successful trigger.
            self._move([None, None, self.clearance], self.params.lift_speed)
            end = self.toolhead.get_last_move_time()
            samples = load_cell_probe.check_sensor_errors(
                collector.collect_until(end), self.printer)
            if not samples:
                raise gcmd.error('Native pressure acquisition is empty')
            logging.info('Native Auto Z pressure acquisition: %s', json.dumps({
                'trigger_time': stamp, 'trigger_z': position[2],
                'minimum_z': floor, 'samples': samples}))
            self._check_context(targets, calibration)
            return {'z': position[2], 'mcu_time': stamp,
                    'sample_count': len(samples)}
        finally:
            if collector is not None:
                collector.stop_collecting()

    def _validate_points(self, calibration):
        offsets = calibration[0]
        status = self.toolhead.get_status(self.reactor.monotonic())
        if not all(self._finite(v) for v in offsets):
            raise self.gcode.error('Probe offsets must be finite')
        if not status['axis_minimum'][2] < self.clearance \
                <= status['axis_maximum'][2]:
            raise self.gcode.error('Native calibration clearance out of range')
        for index, point in enumerate(self.points):
            for xy in (point, [point[i]-offsets[i] for i in (0, 1)]):
                if any(not status['axis_minimum'][i] <= xy[i]
                           <= status['axis_maximum'][i] for i in (0, 1)):
                    raise self.gcode.error(
                        'Native calibration point %d out of range'
                        % (index+1,))

    def _measure_point(self, gcmd, point, point_index, targets, calibration):
        gcmd.respond_info('Native measuring P%d at X%.3f Y%.3f'
                          % (point_index, point[0], point[1]))
        x, y = point
        ox, oy, oz = calibration[0]
        p = self.toolhead.get_position()
        height = max(p[2], self.clearance)
        self._move([None, None, height], self.params.lift_speed)
        self._move([x-ox, y-oy, height], self.travel_speed)
        self._move([None, None, self.clearance], self.params.lift_speed)
        self._check_context(targets, calibration)
        self._update_lcd(gcmd, 'cr_touch', 'P%d CR Touch' % point_index)
        self._check_context(targets, calibration)
        # Use native sample/retry policy of the existing CR Touch.
        touch_cmd = self.gcode.create_gcode_command('PROBE', 'PROBE', {})
        session = self.primary.start_probe_session(touch_cmd)
        try:
            session.run_probe(touch_cmd)
            cr = session.pull_probed_results()[0]
        finally:
            session.end_probe_session()
        if not all(self._finite(v) for v in cr):
            raise gcmd.error('Invalid native CR Touch result')
        # Use uncorrected trigger coordinates, never mesh/twist bed_z.
        floor = cr.test_z - oz - self.retract
        minimum = self.toolhead.get_status(self.reactor.monotonic())[
            'axis_minimum'][2]
        if (not self._finite(floor) or floor <= minimum
                or floor >= self.clearance):
            raise gcmd.error('Paired pressure bound outside native Z range')
        self._move([None, None, self.clearance], self.params.lift_speed)
        self._move([x, y, self.clearance], self.travel_speed)
        self._check_context(targets, calibration)
        self._update_lcd(gcmd, 'nozzle', 'P%d nozzle reference' % point_index)
        self._check_context(targets, calibration)
        # Anchor once at heated point clearance, never under nozzle pressure.
        # Native per-press tare must stay inside this fixed raw band.
        reference = self.cell.avg_counts()
        self._check_context(targets, calibration)
        self.press_move.set_safety_reference(reference)
        logging.info('Native Auto Z unloaded reference: %s', json.dumps({
            'point': list(point),
            'position': self.toolhead.get_position()[:3],
            'safety_reference_counts': int(reference),
            'saved_reference_tare_counts': calibration[2],
            'counts_per_gram': calibration[1]}))
        press_cmd = self.gcode.create_gcode_command('PROBE', 'PROBE', {})
        presses = []
        for index in range(3):
            self._update_lcd(gcmd, 'nozzle', 'P%d PRESS %d/3'
                             % (point_index, index+1))
            presses.append(self._press(
                press_cmd, floor, targets, calibration))
            gcmd.respond_info('Native P%d PROBE BY PRESS %d: z=%.6f'
                              % (point_index, index+1, presses[-1]['z']))
        self._update_lcd(gcmd, 'computing',
                         'P%d computing offset' % point_index)
        self._check_context(targets, calibration)
        zs = sorted(p['z'] for p in presses)
        if zs[-1]-zs[0] > self.params.samples_tolerance:
            raise gcmd.error(
                'Native pressure samples exceed samples_tolerance')
        pressure_z = zs[1]
        value = cr.test_z - pressure_z
        report = {'backend': 'native', 'point': list(point),
            'point_index': point_index,
            'cr_test_z': cr.test_z, 'cr_bed_z': cr.bed_z,
            'raw_bed_reference_z': cr.test_z-oz, 'minimum_z': floor,
            'safety_reference_counts': int(reference),
            'pressure_z': pressure_z, 'pressure_samples': presses,
            'pressure_range': zs[-1]-zs[0], 'provisional_z_offset': value,
            'reference_kind': 'mcu_force_trigger_not_zero_force_contact',
            'applied_adjustment_mm': 0., 'eligible_for_application': False,
            'saved': False}
        logging.info('Native Auto Z point result: %s', json.dumps(report))
        gcmd.respond_info('Native P%d PROBE BY TOUCH: test_z=%.6f; '
            'PRESS median_z=%.6f; provisional z_offset=%.6f mm. '
            'Force-trigger reference, not zero-force contact; '
            'no apply or save.'
            % (point_index, cr.test_z, pressure_z, value))
        return report

    def cmd_AUTO_Z_OFFSET(self, gcmd):
        if self.busy:
            raise gcmd.error('Native calibration already running')
        self.result = self.error = None
        extra = set(gcmd.get_command_parameters()) - {'BED_TEMP', 'NOZZLE_TEMP'}
        if extra:
            raise gcmd.error('Unsupported native calibration parameters: %s'
                             % (', '.join(sorted(extra)),))
        self._idle()
        manual_probe.verify_no_manual_probe(self.printer)
        bed = gcmd.get_float('BED_TEMP', self.bed_temp, minval=50.)
        nozzle = gcmd.get_float('NOZZLE_TEMP', self.nozzle_temp, minval=170.)
        if not all(self._finite(v) for v in (bed, nozzle)):
            raise gcmd.error('Calibration temperatures must be finite')
        targets = [(self.bed, bed), (self.nozzle, nozzle)]
        previous = [(h, h.get_temp(self.reactor.monotonic())[1])
                    for h, target in targets]
        calibration = (self.primary.get_offsets(),
                       self.cell.get_counts_per_gram(),
                       self.cell.get_reference_tare_counts())
        self._validate_points(calibration)
        self.busy = True
        try:
            self.press_move.clear_safety_reference()
            self.toolhead.wait_moves()
            if self.mesh is not None:
                self.mesh.set_mesh(None)
            self._begin_lcd(gcmd, bed, nozzle)
            self._heat(gcmd, targets, calibration)
            if self.toolhead.get_status(self.reactor.monotonic())[
                    'homed_axes'] != 'xyz':
                self._stage = 'Homing'
                self.gcode.run_script_from_command('G28')
            if self.mesh is not None:
                self.mesh.set_mesh(None)
            self._check_context(targets, calibration)
            reports = []
            for index, point in enumerate(self.points):
                reports.append(self._measure_point(
                    gcmd, point, index+1, targets, calibration))
            offsets = [r['provisional_z_offset'] for r in reports]
            # Keep point 1 as the provisional reference; never average away
            # spatial differences in loaded pressure measurements.
            self.result = dict(reports[0])
            self.result.update({'points': reports, 'reference_point': 1,
                                'spatial_range': max(offsets)-min(offsets)})
            logging.info('Native Auto Z result: %s', json.dumps(self.result))
            if len(reports) > 1:
                gcmd.respond_info('Native %d-point pressure-reference report: '
                    'P1 provisional z_offset=%.6f mm; spatial range=%.6f mm. '
                    'No spatial averaging, no apply or save.'
                    % (len(reports), offsets[0], max(offsets)-min(offsets)))
        except Exception as exc:
            self.result = None
            self.error = str(exc)
            # Do not retract, retry or re-home after a failed native move.
            if not self.printer.is_shutdown():
                for (heater, target), (unused, old) in zip(targets, previous):
                    if heater.get_temp(self.reactor.monotonic())[1] == target:
                        try:
                            self.heaters.set_temperature(heater, old, False)
                        except Exception:
                            logging.exception('Native calibration heater '
                                              'cleanup failed')
            raise
        finally:
            self.press_move.clear_safety_reference()
            self.busy = False
            self._end_lcd(gcmd)

    def get_status(self, eventtime):
        return {'backend': 'native', 'busy': self.busy, 'result': self.result,
                'error': self.error, 'eligible_for_application': False}


def load_config(config):
    return NativeAutoZOffset(config)
