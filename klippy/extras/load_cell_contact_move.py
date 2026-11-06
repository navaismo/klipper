# Finite MCU-scheduled advances with stationary descent-onset decisions
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import math
from . import load_cell_contact_onset as onset
from . import load_cell_diagnostics as diagnostics

# Preserve the former 0.5 mm/s * 3 / 80 spatial envelope at every speed.
# This is a host scheduling bound, not a force or contact calibration.
MAX_SEGMENT_TRAVEL = .01875


class OnsetStop:
    def __init__(self, owner, collector, lookup_z, check_samples, options):
        self.owner = owner
        self.printer = owner._printer
        self.toolhead = self.printer.lookup_object('toolhead')
        self.cell = owner._load_cell
        self.collector = collector
        self.lookup_z = lookup_z
        self.check_samples = check_samples
        self.options = options
        self.result = self.report = self.error = None
        self.stop_time = self.stop_z = None
        self.segment_count = 0
        self.max_segment = None
        self.command_segment = self.step_allowance = None
        self.motion_end_time = None
        self.qualification = None
        self.tare_counts = self.cell.get_tare_counts()
        self._last_cutoff = None

    def qualify_stop(self, result, report=None):
        # Nested-window agreement can still describe broad, weak curvature.
        # Do not stop on an onset localized less precisely than one already
        # bounded search advance. This is not proof of physical contact.
        reasons, width = [], None
        selected = next((w for w in result.get('windows', [])
            if w.get('status') == 'identified'
            and w.get('window_s') == result.get('selected_window_s')), {})
        bounds = selected.get('profile_interval_z_mm')
        z = diagnostics.number(result.get('z'))
        if (isinstance(bounds, (list, tuple)) and len(bounds) == 2
                and all(diagnostics.number(v) is not None for v in bounds)
                and z is not None and bounds[0] <= z <= bounds[1]):
            width = bounds[1] - bounds[0]
        if width is None or width <= 0.:
            reasons.append('unverifiable_onset_precision')
        elif width > self.max_segment:
            reasons.append('onset_interval_exceeds_search_advance')
        evidence = self._load_evidence(selected, bounds, report)
        if not evidence['qualified']:
            reasons.append(evidence['reason'])
        return {'qualified': not reasons, 'reasons': reasons,
            'selected_interval_width_mm': width,
            'maximum_interval_width_mm': self.max_segment,
            'load_evidence': evidence,
            'physical_contact_verified': False}

    def _load_evidence(self, selected, bounds, report):
        evidence = {'qualified': False, 'reason': 'missing_load_evidence',
            'confirmation_samples': diagnostics.MIN_POINTS,
            'noise_multiplier': diagnostics.THRESHOLD_SIGMA,
            'minimum_rise_g': diagnostics.THRESHOLD_FLOOR_G}
        if not isinstance(report, dict):
            return evidence
        rows = report.get('samples', [])
        end = diagnostics.number(report.get('descent_end_time'))
        age = diagnostics.number(report.get('maximum_sample_age_s'))
        tare = diagnostics.number(report.get('operational_tare_counts'))
        baseline, drift, origin = [diagnostics.number(selected.get(k))
            for k in ('baseline_force_g', 'baseline_drift_g_per_s',
                      'baseline_time_s')]
        window = selected.get('time_bounds_s')
        if (end is None or age is None or age <= 0. or tare is None
                or any(v is None for v in (baseline, drift, origin))
                or not isinstance(window, (list, tuple)) or len(window) != 2
                or not isinstance(bounds, (list, tuple)) or len(bounds) != 2
                or any(diagnostics.number(v) is None for v in
                       list(window) + list(bounds))
                or bounds[0] >= bounds[1]
                or report.get('stride') != 1
                or len(rows) != report.get('sample_count')):
            return evidence
        rows = [r for r in rows if r[0] <= end]
        if (len(rows) < diagnostics.MIN_POINTS or any(
                len(r) < 6 or r[5] != tare for r in rows)):
            evidence['reason'] = 'changed_or_missing_operational_tare'
            return evidence
        tail = rows[-diagnostics.MIN_POINTS:]
        previous = report.get('previous_descent_end_time')
        if (end - tail[0][0] > age or (previous is not None
                and tail[0][0] <= previous)):
            evidence['reason'] = 'no_fresh_load_confirmation'
            return evidence
        free = [r for r in rows if window[0] <= r[0] <= window[1]
                and r[2] > bounds[1]]
        if len(free) < diagnostics.MIN_POINTS or any(
                r[2] >= bounds[0] for r in tail):
            evidence['reason'] = 'unobserved_contact_transition'
            return evidence
        # Check observed load amplitude, not the fitted compression term or
        # a standard error made small by many correlated plateau samples.
        excess = lambda r: r[1] - baseline - drift * (r[0] - origin)
        noise = math.sqrt(sum(excess(r)**2 for r in free) / len(free))
        rise = max(diagnostics.THRESHOLD_SIGMA * noise,
                   diagnostics.THRESHOLD_FLOOR_G)
        minimum_force = min(r[1] for r in tail)
        minimum_excess = min(excess(r) for r in tail)
        evidence.update({'free_sample_count': len(free), 'noise_rms_g': noise,
            'required_rise_g': rise, 'minimum_recent_force_g': minimum_force,
            'minimum_recent_excess_g': minimum_excess,
            'sample_times': [r[0] for r in tail],
            'sample_z': [r[2] for r in tail]})
        if (not all(diagnostics.number(v) is not None
                    for v in (noise, rise, minimum_force, minimum_excess))
                or minimum_force <= rise or minimum_excess <= rise):
            evidence['reason'] = 'no_sustained_compression_above_noise'
            return evidence
        evidence.update(qualified=True, reason=None)
        return evidence

    def _report(self, end_time):
        if self._last_cutoff is not None and end_time <= self._last_cutoff:
            raise self.printer.command_error('Stale onset decision time')
        samples = self.check_samples(self.collector.snapshot_until(
            end_time, diagnostics.MAX_SAMPLES), self.printer)
        if self.cell.get_tare_counts() != self.tare_counts:
            raise self.printer.command_error('Changed onset tare')
        frames = {'before_probe': self.owner._diagnostic_motion[
            'frames']['before_probe'], 'after_probe': diagnostics.snapshot(
                self.printer, self.toolhead)}
        quality, cutoff = diagnostics._references(
            frames, ('before_probe', 'after_probe'))
        rows = []
        previous = None
        for sample in samples:
            time, force, raw, tare = sample
            if time > end_time:
                continue
            if (previous is not None and time <= previous
                    or cutoff is None or time < cutoff
                    or time < self.collector.min_time
                    or tare != self.tare_counts):
                raise self.printer.command_error(
                    'Invalid or expired onset sample history')
            z = self.lookup_z(self.toolhead, time)
            if any(diagnostics.number(v) is None
                   for v in (time, force, raw, tare, z)):
                raise self.printer.command_error('Invalid onset sample')
            rows.append([time, force, z, 'descent', raw, tare])
            previous = time
        if not rows:
            raise self.printer.command_error('No current onset samples')
        sample_age = (float(self.owner._mcu_trigger_analog.MONITOR_MAX)
                      / self.cell.get_sensor().get_samples_per_second())
        if end_time - rows[-1][0] > sample_age:
            raise self.printer.command_error('Stale onset samples')
        if (not quality['references_complete']
                or quality['frame_state'] != 'consistent'):
            raise self.printer.command_error('Invalid onset coordinates')
        quality.update({'valid_time_bounds': bool(rows)
            and rows[0][0] < end_time, 'bad_timestamps': 0,
            'invalid_samples': 0, 'expired_history_samples': 0,
            'lookup_errors': 0})
        last_cutoff, self._last_cutoff = self._last_cutoff, end_time
        return {'quality': quality, 'motion': {'sensor_errors': 0},
            'samples': rows, 'sample_count': len(rows), 'stride': 1,
            'descent_end_time': end_time, 'cutoff_kind': 'segment_endpoint',
            'descent_window_basis': 'travel',
            'descent_window_speed': self.owner._diagnostic_motion['speed'],
            'maximum_sample_age_s': sample_age,
            'previous_descent_end_time': last_cutoff,
            'operational_tare_counts': self.tare_counts,
            'frames': frames}

    def move(self, destination, speed, completion):
        # No later segment is queued while samples/host computation are late.
        # The analog endstop remains armed by native HomingMove throughout.
        try:
            rate = self.cell.get_sensor().get_samples_per_second()
            self.max_segment = MAX_SEGMENT_TRAVEL
            steps = [s.get_step_dist() for s in
                     self.owner._mcu_trigger_analog.get_steppers()]
            if (not steps or any(diagnostics.number(v) is None or v <= 0.
                                 for v in steps)):
                raise self.printer.command_error('Invalid Z step geometry')
            # Reserve one step for native position quantization. Even an
            # arbitrary fractional starting phase cannot add a pulse beyond
            # the spatial envelope on any participating Z stepper.
            self.step_allowance = max(steps)
            self.command_segment = self.max_segment - self.step_allowance
            search = self.options['search_start_z']
            if (not all(diagnostics.number(v) is not None
                        for v in (search, rate, speed, self.command_segment,
                                  destination[2]))
                    or rate <= 0. or speed <= 0. or self.command_segment <= 0.
                    or search <= destination[2]):
                raise self.printer.command_error('Invalid onset search bounds')
            while not completion.test():
                self.options['checkpoint']()
                if completion.test():
                    return
                if self.cell.get_tare_counts() != self.tare_counts:
                    raise self.printer.command_error('Changed onset tare')
                current = self.toolhead.get_position()
                if diagnostics.number(current[2]) is None:
                    raise self.printer.command_error('Invalid onset position')
                if current[2] <= destination[2]:
                    return
                # The initial approach has the same cap as contact search;
                # the provisional search reference cannot authorize a jump.
                phase_floor = search if current[2] > search else destination[2]
                next_z = max(phase_floor,
                             current[2] - self.command_segment)
                target = list(destination)
                target[2] = min(current[2], max(destination[2], next_z))
                if target[2] >= current[2]:
                    raise self.printer.command_error(
                        'Onset move cannot advance')
                self.toolhead.drip_move(target, speed, completion)
                self.segment_count += 1
                if completion.test():
                    return
                end_time = self.toolhead.get_last_move_time()
                self.motion_end_time = end_time
                # At 2 mm/s an advance can finish before the first 80-Hz
                # conversion. Wait for the existing three-period sample
                # budget with position held, keeping the MCU guard armed.
                # Keep original sample timestamps and stationary history Z.
                decision_time = end_time + (
                    self.owner._mcu_trigger_analog.MONITOR_MAX / float(rate))
                self.report = self._report(decision_time)
                if completion.test():
                    return
                if target[2] > search:
                    # Preserve the original rule: fitting starts only upon
                    # reaching the existing provisional search reference.
                    continue
                self.result = onset.estimate(self.report)
                self.qualification = None
                if (self.result['status'] == 'identified'
                        and diagnostics.number(self.result['z']) is not None
                        and not self.result['reasons']):
                    self.qualification = self.qualify_stop(self.result,
                                                           self.report)
                    if self.qualification['qualified']:
                        self.stop_time = decision_time
                        self.stop_z = target[2]
                        return
        except Exception as exc:
            # Let native home_wait report any MCU safety error first.
            self.error = exc

    def finish(self, trigger_time):
        if trigger_time > 0.:
            raise self.printer.command_error(
                'MCU hard-force stop before a reliable contact onset')
        if self.error is not None:
            raise self.error
        if (self.stop_time is None or not self.qualification
                or not self.qualification['qualified']):
            raise self.printer.command_error(
                'No reliable contact onset before bounded endpoint')
        return {'reason': 'onset', 'stop_time': self.stop_time,
            'stop_z': self.stop_z, 'onset': self.result,
            'decision_report': self.report,
            'max_search_segment_mm': self.max_segment,
            'max_command_segment_mm': self.command_segment,
            'step_allowance_mm': self.step_allowance,
            'motion_end_time': self.motion_end_time,
            'stop_qualification': self.qualification,
            'segment_count': self.segment_count,
            'search_start_z': self.options['search_start_z']}
