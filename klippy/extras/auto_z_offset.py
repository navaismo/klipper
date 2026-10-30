# Standalone CR Touch offset calibration using an existing load cell
# Copyright (C) 2026
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import json
import logging
import math
import uuid
from . import load_cell_diagnostics, load_cell_probe, manual_probe


class AutoZOffset:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.reactor = self.printer.get_reactor()
        if not config.has_section('bltouch'):
            raise config.error("[auto_z_offset] requires [bltouch]")
        self.primary = self.printer.load_object(config, 'bltouch')
        self.load_cell = self.printer.load_object(
            config, 'load_cell ' + config.get('load_cell'))
        if config.get('probe_points', None) is not None:
            if config.get('probe_point', None) is not None:
                raise config.error("Use probe_points or probe_point, not both")
            self.points = config.getlists('probe_points', seps=(',', '\n'),
                                           parser=float, count=2)
            distinct = set(tuple(p) for p in self.points)
            if (len(self.points) not in (2, 4)
                    or len(distinct) != len(self.points)):
                raise config.error(
                    "probe_points requires two or four distinct points")
        else:
            self.points = [config.getfloatlist('probe_point', count=2)]
        self.clearance = config.getfloat('horizontal_move_z', 5., above=0.)
        self.travel_speed = config.getfloat('travel_speed', 50., above=0.)
        self.z_hop_speed = config.getfloat('z_hop_speed', 5., above=0.)
        # Require explicit contact speeds and locally validated force limits.
        speed = config.getfloat('speed', above=0.)
        lift = config.getfloat('lift_speed', above=0.)
        trigger = config.getfloat('trigger_force', minval=10., maxval=250.)
        safety = config.getfloat('force_safety_limit', above=trigger,
                                 minval=100., maxval=10000.)
        self.bed_temp = config.getfloat('bed_temp', 50., minval=50.)
        self.nozzle_temp = config.getfloat('nozzle_temp', 170., minval=170.)
        self.temp_tolerance = config.getfloat('temperature_tolerance', 2.,
                                             above=0.)
        self.stable_time = config.getfloat('temperature_stable_time', 5.,
                                          above=0.)
        self.recovery_time = config.getfloat('temperature_recovery_time', 10.,
                                            above=0.)
        self.heating_timeout = config.getfloat('heating_timeout', 600.,
                                              above=self.stable_time)
        self.tolerance = config.getfloat('offset_tolerance', 0.025, above=0.)
        self.spatial_tolerance = config.getfloat('spatial_tolerance', 0.025,
                                                 above=0., maxval=0.025)
        self.min_offset = config.getfloat('minimum_offset', 0., minval=0.)
        self.max_offset = config.getfloat('maximum_offset', 5.,
                                         above=self.min_offset)
        values = [v for point in self.points for v in point] + [
            speed, lift, trigger, safety,
            self.clearance, self.travel_speed, self.z_hop_speed,
            self.bed_temp, self.nozzle_temp, self.temp_tolerance,
            self.stable_time, self.recovery_time, self.heating_timeout,
            self.tolerance, self.spatial_tolerance,
            self.min_offset, self.max_offset]
        if not all(self._finite(v) for v in values):
            raise config.error("Auto Z-offset parameters must be finite")
        self.tap = load_cell_probe.LoadCellTapHelper(
            config, self.load_cell, ascent_sample_time=5., log_trace=True)
        self.connected = self.busy = self.contact_verified = False
        self.verified_points = {}
        self.candidate = self.result = self.error = None
        self.last_contact = None
        self.stage = None
        self.state = 'disconnected'
        self.printer.register_event_handler('klippy:connect', self._connect)
        for event in ('klippy:disconnect', 'klippy:shutdown'):
            self.printer.register_event_handler(event, self._disconnect)
        for event in ('homing:home_rails_begin', 'load_cell:calibrate'):
            self.printer.register_event_handler(event, self._invalidate)
        self._frame_session = None
        self._frame_pair = self._frame_auto_home = None
        self._frame_rail_home = self._frame_last_set = None
        self._frame_epoch = 0
        try:
            self._frame_session = uuid.uuid4().hex
            self.printer.register_event_handler('toolhead:set_position',
                                                self._frame_set_position)
            self.printer.register_event_handler('homing:home_rails_begin',
                                                self._frame_home_begin)
        except Exception:
            load_cell_diagnostics.warn('pair observer setup')
        self.gcode.register_command('AUTO_Z_OFFSET_TEST_CONTACT',
            self.cmd_TEST_CONTACT, desc="Perform one supervised nozzle tap")
        self.gcode.register_command('AUTO_Z_OFFSET_CALIBRATE',
            self.cmd_CALIBRATE, desc="Measure paired CR Touch/nozzle offsets")
        self.gcode.register_command('AUTO_Z_OFFSET_ACCEPT',
            self.cmd_ACCEPT, desc="Stage the validated CR Touch Z offset")
        self.gcode.register_command('AUTO_Z_OFFSET', self.cmd_AUTO_Z_OFFSET,
            desc="Test both points and measure the CR Touch Z offset")

    def _frame_set_position(self, *args):
        # This event follows the existing setter; never wrap or call it.
        try:
            self._frame_epoch += 1
            self._frame_last_set = self.reactor.monotonic()
            if self.busy:
                self._frame_event('set_position')
        except Exception:
            load_cell_diagnostics.warn('pair position event')

    def _frame_home_begin(self, homing_state, rails):
        try:
            self._frame_rail_home = {
                'id': uuid.uuid4().hex,
                'eventtime': self.reactor.monotonic(),
                'axes': list(homing_state.get_axes())}
            if self.busy:
                self._frame_event('home_rails_begin')
        except Exception:
            load_cell_diagnostics.warn('pair rail home event')

    def _frame_event(self, event, cr=None, pos=None, trigger_z=None,
                      offset=None, error=None, **fields):
        # Observe cached state only; never flush, wait, query or change motion.
        try:
            if event == 'run_begin':
                self._frame_pair = None
            if event == 'pair_begin':
                self._frame_pair = uuid.uuid4().hex
            if event == 'home_requested':
                self._frame_auto_home = {
                    'id': uuid.uuid4().hex,
                    'eventtime': self.reactor.monotonic()}
            now = self.reactor.monotonic()
            frame = load_cell_diagnostics.snapshot(self.printer, self.toolhead)
            record = dict(fields, version=1, event=event, eventtime=now,
                session_id=self._frame_session, pair_id=self._frame_pair,
                stage=self.stage, frame=frame,
                coordinate_epoch=self._frame_epoch,
                last_set_position_time=self._frame_last_set,
                automatic_home_request=self._frame_auto_home,
                last_observed_rail_home=self._frame_rail_home,
                cached_print_time=getattr(self.toolhead, 'print_time', None))
            offsets = frame.get('probe_offsets')
            record['active_z_offset'] = offsets[2] if offsets else None
            try:
                record['homed_axes'] = self.toolhead.get_status(now)[
                    'homed_axes']
                transform = (self.printer.lookup_object('gcode_move')
                             .move_transform)
                record['transform'] = {'type': type(transform).__name__,
                                       'id': id(transform)}
            except Exception as exc:
                record['extra_reference_error'] = str(exc)[:160]
            if cr is not None:
                record['cr'] = {'test_z': cr.test_z, 'bed_z': cr.bed_z}
            if event in ('tap_returned', 'pair_result'):
                motion = getattr(self.tap.probing_move,
                                 '_diagnostic_motion', None)
                if isinstance(motion, dict):
                    # Join the existing HX711 report; it precedes this event.
                    record['tap_link'] = {key: motion.get(key) for key in (
                        'name', 'tap_id', 'collector_start_time',
                        'trigger_time', 'raw_z')}
                record['regression_z'] = pos[2] if pos is not None else None
                record['mcu_trigger_z'] = (trigger_z if trigger_z is not None
                    else motion.get('raw_z')
                    if isinstance(motion, dict) else None)
            if offset is not None:
                record['formula'] = {'candidate': offset,
                    'cr_test_z': cr.test_z, 'cr_bed_z': cr.bed_z,
                    'nozzle_regression_z': pos[2],
                    'candidate_from_active_offset': offsets[2]
                        + cr.bed_z - pos[2] if offsets else None}
            if error is not None:
                record['error'] = str(error)[:160]
            logging.info('Auto Z-offset frame: %s',
                         json.dumps(record, sort_keys=True, allow_nan=False))
        except Exception:
            load_cell_diagnostics.warn('pair frame event ' + event)

    def _connect(self):
        self.toolhead = self.printer.lookup_object('toolhead')
        self.heaters = self.printer.lookup_object('heaters')
        self.bed = self.heaters.lookup_heater('heater_bed')
        self.nozzle = self.heaters.lookup_heater('extruder')
        self.connected = True
        self.state = 'idle'

    def _disconnect(self):
        self.connected = self.contact_verified = False
        self._invalidate()
        self.state = 'disconnected'

    def _invalidate(self, *args):
        self.candidate = self.result = None
        self.last_contact = None
        self.contact_verified = False
        self.verified_points.clear()
        if not self.busy and self.state in ('candidate', 'accepted',
                                            'contact_ready', 'rejected'):
            self.state = 'idle'

    def _fingerprint(self):
        return (self.primary.get_offsets(),
                self.load_cell.get_counts_per_gram(),
                self.load_cell.get_reference_tare_counts(),
                self.load_cell.invert, tuple(tuple(p) for p in self.points))

    def _check_idle(self):
        if not self.connected or self.printer.is_shutdown():
            raise self.gcode.error("Auto Z-offset requires a ready printer")
        now = self.reactor.monotonic()
        stats = self.printer.lookup_object('print_stats', None)
        pause = self.printer.lookup_object('pause_resume', None)
        sd = self.printer.lookup_object('virtual_sdcard', None)
        if ((stats is not None and stats.get_status(now)['state']
             in ('printing', 'paused'))
                or (pause is not None and pause.is_paused)
                or (sd is not None and (sd.is_active()
                    or getattr(sd, 'preflight_pending', False)))):
            raise self.gcode.error("Auto Z-offset requires an idle printer")
        manual_probe.verify_no_manual_probe(self.printer)

    def _check_pending(self):
        cfg = self.printer.lookup_object('configfile')
        items = cfg.get_status(self.reactor.monotonic())[
            'save_config_pending_items']
        pending = items.get('bltouch')
        if 'bltouch' in items and (pending is None or 'z_offset' in pending):
            raise self.gcode.error("A bltouch z_offset is already pending save")
        return cfg

    def _check_geometry(self, prepared=True):
        status = self.toolhead.get_status(self.reactor.monotonic())
        if prepared and not all(axis in status['homed_axes'] for axis in 'xyz'):
            raise self.gcode.error("Home XYZ before Auto Z-offset")
        if self.printer.lookup_object('probe') is not self.primary:
            raise self.gcode.error("CR Touch must remain the primary probe")
        mesh = self.printer.lookup_object('bed_mesh', None)
        if prepared and mesh is not None and mesh.get_mesh() is not None:
            raise self.gcode.error("Run BED_MESH_CLEAR before Auto Z-offset")
        for name in ('axis_twist_compensation', 'skew_correction',
                     'z_thermal_adjust'):
            if self.printer.lookup_object(name, None) is not None:
                raise self.gcode.error("Auto Z-offset MVP does not support %s"
                                       % (name,))
        offsets = self.primary.get_offsets()
        if not all(self._finite(v) for v in offsets):
            raise self.gcode.error("Probe offsets must be finite")
        if self.clearance <= offsets[2]:
            raise self.gcode.error("horizontal_move_z must exceed probe offset")
        self.probe_positions = [[point[i] - offsets[i] for i in (0, 1)]
                                for point in self.points]
        for point, probe_xy in zip(self.points, self.probe_positions):
            for xy in (point, probe_xy):
                for i in (0, 1):
                    if not status['axis_minimum'][i] <= xy[i] \
                            <= status['axis_maximum'][i]:
                        raise self.gcode.error(
                            "Auto Z-offset point out of range")
        if self.clearance > status['axis_maximum'][2]:
            raise self.gcode.error("Auto Z-offset clearance out of range")

    def _thermal_error(self, targets, check_band=True):
        now = self.reactor.monotonic()
        for heater, target, minimum in targets:
            measured, active_target = heater.get_temp(now)
            if (not self._finite(measured) or measured < heater.min_temp
                    or measured > heater.max_temp
                    or (check_band
                        and abs(measured - target) > self.temp_tolerance)
                    or active_target != target):
                return ("%s: measured %.2f C, target %.2f C, expected %.2f C "
                        "(+/- %.2f C), minimum target %.2f C"
                        % (heater.get_name(), measured, active_target, target,
                           self.temp_tolerance, minimum))
        return None

    def _thermal_ok(self, targets):
        return self._thermal_error(targets) is None

    def _check_thermal(self, targets):
        deadline = self.reactor.monotonic() + self.recovery_time
        waiting = False
        while True:
            self._check_idle()
            fault = self._thermal_error(targets, check_band=False)
            if fault is not None:
                raise self.gcode.error(
                    "Calibration temperature lost or changed: %s" % (fault,))
            error = self._thermal_error(targets)
            if error is None:
                if waiting:
                    self.gcode.respond_info(
                        "Auto Z-offset temperature recovered; continuing")
                return
            now = self.reactor.monotonic()
            if now >= deadline:
                raise self.gcode.error(
                    "Calibration temperature lost or changed: recovery timed "
                    "out after %.1f s; %s" % (self.recovery_time, error))
            if not waiting:
                self.gcode.respond_info(
                    "Auto Z-offset waiting for temperature recovery: %s"
                    % (error,))
                waiting = True
            self.reactor.pause(min(now + 0.25, deadline))

    def _heat(self, targets):
        for heater, target, minimum in targets:
            self.heaters.set_temperature(heater, target, wait=False)
        self.state = 'heating'
        start = self.reactor.monotonic()
        in_band_time = 0.
        last_check = start
        was_in_band = False
        minimum_reached = set()
        self.gcode.respond_info(
            "Auto Z-offset warming up; waiting %.1f s within +/- %.1f C"
            % (self.stable_time, self.temp_tolerance))
        while True:
            self._check_idle()
            now = self.reactor.monotonic()
            fault = self._thermal_error(targets, check_band=False)
            if fault is not None:
                raise self.gcode.error(
                    "Calibration temperature lost or changed: %s" % (fault,))
            for heater, target, minimum in targets:
                if heater.get_temp(now)[0] >= minimum:
                    minimum_reached.add(heater)
            # Accumulate time in band instead of restarting on every PID
            # cycle. Only leave warm-up while both heaters are in band.
            if was_in_band:
                in_band_time += now - last_check
            in_band = (len(minimum_reached) == len(targets)
                       and self._thermal_ok(targets))
            if in_band and in_band_time >= self.stable_time:
                return
            was_in_band = in_band
            last_check = now
            if now - start >= self.heating_timeout:
                raise self.gcode.error("Auto Z-offset heating timed out")
            self.reactor.pause(now + 0.25)

    def _raise_z(self):
        if self.toolhead.get_position()[2] < self.clearance:
            self.toolhead.manual_move([None, None, self.clearance],
                                      self.z_hop_speed)

    def _position(self, xy):
        self._raise_z()
        self.toolhead.manual_move([xy[0], xy[1], None], self.travel_speed)
        self.toolhead.manual_move([None, None, self.clearance],
                                  self.z_hop_speed)
        self.toolhead.wait_moves()

    def _probe(self):
        # Use CR Touch's own parameters, independent of nozzle tap speeds.
        gcmd = self.gcode.create_gcode_command('PROBE', 'PROBE', {})
        session = self.primary.start_probe_session(gcmd)
        try:
            session.run_probe(gcmd)
            return session.pull_probed_results()[0]
        finally:
            session.end_probe_session()

    def _temperatures(self):
        now = self.reactor.monotonic()
        temperatures = {}
        for name, heater in (('bed', self.bed), ('nozzle', self.nozzle)):
            measured, target = heater.get_temp(now)
            temperatures[name] = {'measured': measured, 'target': target}
        return temperatures

    @staticmethod
    def _finite(value):
        return not (math.isnan(value) or math.isinf(value))

    def _measure_pair(self, gcmd, tap_gcmd, targets, point_index, sample_index,
                      fingerprint, lcd=None):
        point = self.points[point_index]
        self._frame_event('pair_begin', point_index=point_index + 1,
                          sample_index=sample_index + 1,
                          saved_offset=fingerprint[0][2])
        self._check_thermal(targets)
        self._position(self.probe_positions[point_index])
        self._check_thermal(targets)
        self._update_lcd(lcd, 'cr_touch')
        self._check_thermal(targets)
        self._frame_event('before_cr')
        cr = self._probe()
        self._frame_event('after_cr', cr=cr)
        self._check_thermal(targets)
        self._frame_event('before_nozzle_xy', cr=cr)
        self._position(point)
        self._frame_event('after_nozzle_xy', cr=cr)
        self._check_thermal(targets)
        self._update_lcd(lcd, 'nozzle')
        self._check_thermal(targets)
        if sample_index == 0:
            # Fix the raw limit from unloaded clearance, once per point.
            reference = self.load_cell.avg_counts()
            self.tap.probing_move.set_safety_reference(reference)
            self._check_thermal(targets)
        before = self._temperatures()
        self._frame_event('before_tap', cr=cr)
        try:
            pos, valid = self.tap.tapping_move.run_tap(tap_gcmd)
        except Exception as exc:
            self._frame_event('tap_failed', cr=cr, error=exc)
            raise
        self._frame_event('tap_returned', cr=cr, pos=pos)
        after = self._temperatures()
        self._check_thermal(targets)
        if not valid or not self._finite(pos[2]):
            raise gcmd.error("Invalid nozzle contact measurement")
        # Direct measured separation; the old offset only defines
        # the coordinate origin shared by both measurements.
        offset = cr.test_z - pos[2]
        trigger_z = self.tap.tapping_move.get_status(
            self.reactor.monotonic())['last_trigger_z']
        if trigger_z is None or not self._finite(trigger_z):
            raise gcmd.error("Missing force trigger position")
        if not self._finite(offset) or not \
                self.min_offset <= offset <= self.max_offset:
            raise gcmd.error("Measured Z-offset outside valid range")
        self._frame_event('pair_result', cr=cr, pos=pos,
                          trigger_z=trigger_z, offset=offset)
        self.last_contact = {
            'point_index': point_index + 1, 'sample_index': sample_index + 1,
            'point': list(point), 'cr_trigger_z': cr.test_z,
            'force_trigger_z': trigger_z, 'regression_z': pos[2],
            'regression_delta': pos[2] - trigger_z, 'offset': offset,
            'saved_offset': fingerprint[0][2], 'mesh_active': False,
            'temperatures_before': before, 'temperatures_after': after}
        gcmd.respond_info(
            "Contact at X%.3f Y%.3f: force_trigger_z=%.6f mm, "
            "regression_z=%.6f mm, difference=%.6f mm"
            % (point[0], point[1], trigger_z, pos[2], pos[2] - trigger_z))
        logging.info("Auto Z-offset contact: %s", self.last_contact)
        logging.info(
            "Auto Z-offset point %d pair %d: saved=%.6f cr_trigger_z=%.6f "
            "cr_bed_z=%.6f nozzle_contact_z=%.6f offset=%.6f",
            point_index + 1, sample_index + 1, fingerprint[0][2], cr.test_z,
            cr.bed_z, pos[2], offset)
        if len(self.points) == 1:
            gcmd.respond_info("Z-offset pair %d: %.6f mm"
                              % (sample_index + 1, offset))
        else:
            gcmd.respond_info("Point %d pair %d: %.6f mm"
                              % (point_index + 1, sample_index + 1, offset))
        return offset

    @staticmethod
    def _median(values):
        ordered = sorted(values)
        middle = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[middle]
        return (ordered[middle - 1] + ordered[middle]) * .5

    def _report_result(self, gcmd, point_results, fingerprint):
        offsets = [v for point in point_results for v in point['offsets']]
        medians = [point['median'] for point in point_results]
        spread = max(medians) - min(medians)
        valid = spread <= self.spatial_tolerance
        # With two points, the second validates the first without averaging.
        offset = medians[0] if len(self.points) == 2 else self._median(medians)
        self.result = {'points': point_results, 'offsets': offsets,
            'minimum': min(offsets), 'maximum': max(offsets),
            'range': max(offsets) - min(offsets), 'spatial_range': spread,
            'valid': valid,
            'z_offset': offset if valid else None}
        if not valid:
            self.state = 'rejected'
            self.error = ("Spatial range %.6f mm exceeds spatial_tolerance "
                          "%.6f mm" % (spread, self.spatial_tolerance))
            gcmd.respond_info(self.error + "; no global candidate. "
                              "Inspect the point reports; no save.")
            return
        self.candidate = fingerprint
        self.state = 'candidate'
        if len(self.points) == 2:
            gcmd.respond_info("Using point 1 as reference; point 2 validates "
                              "agreement without averaging.")
        gcmd.respond_info(
            "CR Touch z_offset: %.6f mm (range %.6f mm; spatial %.6f mm).\n"
            "Inspect the result; confirm with AUTO_Z_OFFSET_ACCEPT."
            % (self.result['z_offset'], self.result['range'], spread))

    def _run(self, gcmd, contact_only, automatic=False):
        if self.busy:
            raise gcmd.error("Auto Z-offset is already running")
        self.stage = 'preflight'
        try:
            self._execute_run(gcmd, contact_only, automatic)
        except Exception as exc:
            self.state = 'error'
            self.error = "Auto Z-offset %s failed: %s" % (self.stage, exc)
            raise gcmd.error(self.error)

    def _measure_points(self, gcmd, tap_gcmd, targets, indices, supervised,
                        fingerprint, lcd):
        results = []
        samples = 1 if supervised else (5 if len(self.points) == 1 else 3)
        for index in indices:
            offsets, contacts = [], []
            for sample in range(samples):
                self.stage = '%s P%d pair %d' % (
                    'Test' if supervised else 'Calibrate', index+1, sample+1)
                gcmd.respond_info('Auto Z-offset: ' + self.stage)
                offset = self._measure_pair(gcmd, tap_gcmd, targets,
                                            index, sample, fingerprint, lcd)
                offsets.append(offset)
                contacts.append(self.last_contact)
                if max(offsets) - min(offsets) > self.tolerance:
                    raise gcmd.error("Point %d pairs exceed offset_tolerance"
                                     % (index + 1,))
            local = {'point_index': index + 1,
                'point': list(self.points[index]), 'offsets': offsets,
                'median': self._median(offsets), 'minimum': min(offsets),
                'maximum': max(offsets), 'range': max(offsets)-min(offsets),
                'contacts': contacts}
            results.append(local)
            self._raise_z()
            self.toolhead.wait_moves()
            self._check_thermal(targets)
            if fingerprint != self._fingerprint():
                raise gcmd.error("Calibration references changed")
            if supervised:
                self.verified_points[index] = fingerprint
            else:
                gcmd.respond_info(
                    "Point %d X%.3f Y%.3f: median %.6f mm, range %.6f mm"
                    % (index+1, self.points[index][0], self.points[index][1],
                       local['median'], local['range']))
        return results

    def _update_lcd(self, lcd, phase):
        if lcd is not None:
            # A bounded update and final fence finish before any acquisition.
            lcd.update_calibration(phase, self.stage)

    def _execute_run(self, gcmd, contact_only, automatic):
        self.tap.probing_move.clear_safety_reference()
        self.candidate = self.result = self.error = None
        self.last_contact = None
        self.state = 'idle' if self.connected else 'disconnected'
        self._check_idle()
        self._check_pending()
        self._check_geometry(prepared=not automatic)
        if automatic and len(self.points) != 2:
            raise gcmd.error("AUTO_Z_OFFSET requires two probe_points")
        if not self.load_cell.is_calibrated():
            raise gcmd.error("Calibrate the load cell before nozzle contact")
        scale = self.load_cell.get_counts_per_gram()
        if not self._finite(scale) or scale < 1.:
            raise gcmd.error("Invalid load cell force calibration")
        point_index = gcmd.get_int('POINT', 1, minval=1,
                                   maxval=len(self.points)) - 1 \
            if contact_only else None
        fingerprint = self._fingerprint()
        self.verified_points = {
            i: ref for i, ref in self.verified_points.items()
            if ref == fingerprint}
        self.contact_verified = (
            len(self.verified_points) == len(self.points))
        if not contact_only and not automatic and not self.contact_verified:
            raise gcmd.error(
                "Run AUTO_Z_OFFSET_TEST_CONTACT POINT=n successfully "
                "for every configured point first")
        targets = [(self.bed, gcmd.get_float('BED_TEMP', self.bed_temp,
                    minval=50., maxval=self.bed.max_temp), 50.),
                   (self.nozzle, gcmd.get_float('NOZZLE_TEMP', self.nozzle_temp,
                    minval=170., maxval=self.nozzle.max_temp), 170.)]
        if any(not self._finite(t) or not minimum <= t <= h.max_temp
               for h, t, minimum in targets):
            raise gcmd.error("Invalid Auto Z-offset temperature target")
        previous = [(h, h.get_temp(self.reactor.monotonic())[1])
                    for h, target, minimum in targets]
        motion_started = False
        lcd = None
        self.busy = True
        self._frame_event('run_begin')
        try:
            self.toolhead.wait_moves()
            self.stage = 'warm-up'
            display = self.printer.lookup_object('tjc_lcd', None)
            if display is not None:
                try:
                    if display.begin_calibration('Warming up',
                            nozzle_target=targets[1][1],
                            bed_target=targets[0][1]):
                        lcd = display
                    else:
                        gcmd.respond_info('LCD phase screen unavailable; '
                                          'TJC_CANVAS_VERSION=1 is needed')
                except Exception as exc:
                    logging.exception('Auto Z-offset LCD start failed')
                    gcmd.respond_info('LCD phase screen unavailable: %s'
                                      % (exc,))
            self._heat(targets)
            if automatic:
                self.stage = 'mesh clear'
                if self.printer.lookup_object('bed_mesh', None) is not None:
                    self.gcode.run_script_from_command('BED_MESH_CLEAR')
                self.stage = 'homing'
                self._frame_event('home_requested')
                self.gcode.run_script_from_command('G28')
                self._frame_event('home_returned')
                self.toolhead.wait_moves()
                self._check_idle()
                mesh = self.printer.lookup_object('bed_mesh', None)
                if mesh is not None and mesh.get_mesh() is not None:
                    self.stage = 'mesh clear after homing'
                    self.gcode.run_script_from_command('BED_MESH_CLEAR')
                self._check_geometry()
                self._check_thermal(targets)
            self.state = 'measuring'
            # Do not allow command parameters to override validated tap limits.
            tap_gcmd = self.gcode.create_gcode_command('AUTO_Z_OFFSET_TAP',
                                                      'AUTO_Z_OFFSET_TAP', {})
            point_indices = [point_index] if contact_only \
                else range(len(self.points))
            motion_started = True
            if automatic:
                self.verified_points.clear()
                self.contact_verified = False
                self._measure_points(gcmd, tap_gcmd, targets, point_indices,
                                     True, fingerprint, lcd)
                self.contact_verified = (
                    len(self.verified_points) == len(self.points))
                if not self.contact_verified:
                    raise gcmd.error("Supervised contact qualification failed")
            point_results = self._measure_points(
                gcmd, tap_gcmd, targets, point_indices, contact_only,
                fingerprint, lcd)
            if contact_only:
                self.contact_verified = (
                    len(self.verified_points) == len(self.points))
                self.state = 'contact_ready'
                gcmd.respond_info("Supervised nozzle contact passed; no save")
            else:
                self.stage = 'spatial validation'
                self._update_lcd(lcd, 'computing')
                self._report_result(gcmd, point_results, fingerprint)
            if self.state != 'rejected':
                self.stage = 'complete'
        except Exception as exc:
            self._invalidate()
            self.error = str(exc)
            self.state = 'error'
            raise
        finally:
            had_error = self.state == 'error'
            cleanup_error = None
            try:
                if self.connected and not self.printer.is_shutdown():
                    if had_error:
                        gcmd.respond_info(
                            "Auto Z-offset aborted; restoring previous heater "
                            "targets: heater_bed=%.1f C, extruder=%.1f C"
                            % (previous[0][1], previous[1][1]))
                    if motion_started:
                        try:
                            self._raise_z()
                            self.toolhead.wait_moves()
                        except Exception as exc:
                            cleanup_error = exc
                            logging.exception("Auto Z-offset retract failed")
                    for heater, target in previous:
                        try:
                            self.heaters.set_temperature(heater, target,
                                                         wait=False)
                        except Exception as exc:
                            cleanup_error = exc
                            logging.exception("Auto Z-offset heater restore")
            finally:
                self.tap.probing_move.clear_safety_reference()
                self.busy = False
                if lcd is not None:
                    success = (not had_error and cleanup_error is None
                               and self.state != 'rejected')
                    text = ('Offset %.3fmm - review' % self.result['z_offset']
                            if success and self.result is not None else
                            'Contact passed' if success else
                            'Failed: ' + self.stage)
                    try:
                        lcd.end_calibration(text, success)
                    except Exception as exc:
                        logging.exception('Auto Z-offset LCD finish failed')
                        gcmd.respond_info('LCD phase screen finish failed: %s'
                                          % (exc,))
            if cleanup_error is not None:
                self._invalidate()
                self.state = 'error'
                if not had_error:
                    self.error = str(cleanup_error)
                    raise gcmd.error("Auto Z-offset cleanup failed: %s"
                                     % (cleanup_error,))

    def cmd_TEST_CONTACT(self, gcmd):
        self._run(gcmd, True)

    def cmd_CALIBRATE(self, gcmd):
        self._run(gcmd, False)

    def cmd_AUTO_Z_OFFSET(self, gcmd):
        self._run(gcmd, False, automatic=True)
        if self.state == 'rejected':
            raise gcmd.error(self.error)

    def cmd_ACCEPT(self, gcmd):
        self._check_idle()
        if self.busy or self.candidate is None or self.result is None:
            raise gcmd.error("No validated Z-offset candidate to accept")
        if self.candidate != self._fingerprint():
            self._invalidate()
            raise gcmd.error("Z-offset candidate is stale; calibrate again")
        cfg = self._check_pending()
        cfg.set('bltouch', 'z_offset', '%.6f' % self.result['z_offset'])
        self.candidate = None
        self.state = 'accepted'
        gcmd.respond_info(
            "Z-offset staged. SAVE_CONFIG writes the configuration"
            " and restarts Klipper; runtime offset is unchanged.")

    def get_status(self, eventtime):
        return {'state': self.state, 'contact_verified': self.contact_verified,
                'verified_points': [i + 1
                                    for i in sorted(self.verified_points)],
                'candidate': self.candidate is not None,
                'result': self.result, 'error': self.error,
                'last_contact': self.last_contact, 'stage': self.stage}


def load_config(config):
    return AutoZOffset(config)
