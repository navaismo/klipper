# Passive tap diagnostics; never used to control motion or choose probe Z
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import json
import logging
import math

MAX_SAMPLES = 2048
MIN_POINTS = 3
BASELINE_POINTS = 8
THRESHOLD_SIGMA = 5.
THRESHOLD_FLOOR_G = 1.
FRAME_NAMES = ('before_probe', 'after_probe', 'after_workaround',
               'after_ascent')


def number(value):
    try:
        value = float(value)
        return value if not (math.isnan(value) or math.isinf(value)) else None
    except (TypeError, ValueError, OverflowError):
        return None


def warn(operation):
    # Even a broken logging handler must not escape an observer boundary.
    try:
        logging.warning('Passive load cell diagnostic failed: %s', operation,
                        exc_info=True)
    except Exception:
        pass


def _finite(value):
    value = number(value)
    if value is None:
        raise ValueError('Missing or non-finite reference')
    return value


def _vector(values):
    result = [_finite(v) for v in values]
    if len(result) < 3:
        raise ValueError('Incomplete coordinate reference')
    return result


def snapshot(printer, toolhead):
    # Read cached host state only: no queries, queue flushes or move-time reads.
    result = {'stepper_zero': {}, 'temperatures': {}, 'read_errors': []}

    def read(field, reader):
        try:
            return reader()
        except Exception as exc:
            result['read_errors'].append(
                {'field': field, 'error': str(exc)[:160]})
            warn(field)
        return None

    result['position'] = read('position',
        lambda: _vector(toolhead.get_position()[:3]))
    steppers = read('steppers',
        lambda: toolhead.get_kinematics().get_steppers())
    result['stepper_names'] = read('stepper_names',
        lambda: [s.get_name() for s in steppers])
    for index, stepper in enumerate(steppers or []):
        name = read('stepper_name.%d' % index, stepper.get_name)
        if name is not None:
            result['stepper_zero'][name] = read('stepper_zero.' + name,
                lambda: _finite(stepper.mcu_to_commanded_position(0)))
    now = read('eventtime', lambda: _finite(
        printer.get_reactor().monotonic()))
    for name in ('heater_bed', 'extruder'):
        def temperature():
            heater = printer.lookup_object('heaters').lookup_heater(name)
            measured, target = heater.get_temp(_finite(now))
            return {'measured': _finite(measured), 'target': _finite(target)}
        result['temperatures'][name] = read('temperatures.' + name,
                                             temperature)
    result['probe_offsets'] = read('probe_offsets', lambda: _vector(
        printer.lookup_object('probe').get_offsets()))

    def mesh_active():
        mesh = printer.lookup_object('bed_mesh', None)
        return mesh is not None and mesh.get_mesh() is not None
    result['mesh_active'] = read('mesh_active', mesh_active)
    status = read('gcode_move', lambda:
        printer.lookup_object('gcode_move').get_status(_finite(now)))
    for key in ('homing_origin', 'gcode_position'):
        result[key] = read(key, lambda: _vector(status[key]))
    result['history_clear_time'] = read('history_clear_time', lambda: _finite(
        printer.lookup_object('motion_queuing').clear_history_time))
    return result


def _references(frames):
    errors, changes, cutoffs = [], [], []
    initial, final, deltas = {}, {}, {}
    for frame_name in FRAME_NAMES:
        frame = frames.get(frame_name)
        if not isinstance(frame, dict):
            errors.append(frame_name + ': missing snapshot')
            continue
        for key in ('position', 'probe_offsets', 'homing_origin',
                    'gcode_position'):
            try:
                _vector(frame.get(key))
            except Exception:
                errors.append(frame_name + '.' + key)
        cutoff = number(frame.get('history_clear_time'))
        if cutoff is None:
            errors.append(frame_name + '.history_clear_time')
        else:
            cutoffs.append(cutoff)
        if frame.get('mesh_active') is not False:
            errors.append(frame_name + '.mesh_active: not known inactive')
        names, zero = frame.get('stepper_names'), frame.get('stepper_zero')
        zero = zero if isinstance(zero, dict) else {}
        valid_zero = (isinstance(names, list) and bool(names)
                      and all(isinstance(n, (str, type(u''))) and n
                              for n in names)
                      and len(names) == len(set(names))
                      and set(names) == set(zero)
                      and all(number(v) is not None for v in zero.values()))
        if not valid_zero:
            errors.append(frame_name + '.stepper_zero: incomplete mapping')
        failures = frame.get('read_errors')
        if not isinstance(failures, list):
            errors.append(frame_name + '.read_errors: missing read status')
            failures = []
        for failure in failures:
            field = (failure.get('field', '')
                     if isinstance(failure, dict) else '')
            if not field.startswith('temperatures.'):
                errors.append(frame_name + ': failed reference read ' + field)
        if frame_name == 'before_probe':
            initial = frame
        final = frame
        if initial and valid_zero:
            base_zero = initial.get('stepper_zero')
            base_zero = base_zero if isinstance(base_zero, dict) else {}
            for name, value in zero.items():
                old = number(base_zero.get(name))
                if old is None or abs(number(value) - old) > 1.e-7:
                    changes.append(frame_name + '.stepper_zero.' + name)
            if set(zero) != set(base_zero):
                changes.append(frame_name + '.stepper_names')
            for key in ('probe_offsets', 'homing_origin'):
                before, after = initial.get(key), frame.get(key)
                try:
                    before, after = _vector(before), _vector(after)
                    if len(before) != len(after) or any(
                            abs(a - b) > 1.e-7 for a, b in zip(before, after)):
                        changes.append(frame_name + '.' + key)
                except Exception:
                    pass
    initial_zero, final_zero = (frame.get('stepper_zero') for frame in (
        initial, final))
    initial_zero = initial_zero if isinstance(initial_zero, dict) else {}
    final_zero = final_zero if isinstance(final_zero, dict) else {}
    for name, value in final_zero.items():
        old, value = number(initial_zero.get(name)), number(value)
        if old is not None and value is not None:
            deltas[name] = value - old
    return {'references_complete': not errors, 'reference_errors': errors,
        'coordinate_changes': changes,
        'frame_state': 'incomplete' if errors else
                       'changed' if changes else 'consistent',
        'stepper_origin_changes_mm': deltas}, max(cutoffs) if cutoffs else None


def _bounded_indices(size, anchors=()):
    if size <= MAX_SAMPLES:
        return list(range(size)), 1
    stride = max(1, int(math.ceil(size / float(MAX_SAMPLES - 32))))
    indices = set(range(0, size, stride))
    indices.update(i for i in list(anchors)[:16] if 0 <= i < size)
    if size:
        indices.update((0, size - 1))
    return sorted(indices), stride


def _slope(points):
    if len(points) < 2:
        return None
    x0, y0 = points[0]
    xs = [x - x0 for x, y in points]
    ys = [y - y0 for x, y in points]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    variance = sum((x - mx)**2 for x in xs)
    if not variance:
        return None
    return sum((x - mx)*(y - my) for x, y in zip(xs, ys)) / variance


def _branch(rows, fit, descending=False):
    if descending:
        rows = list(reversed(rows))
    total = len(rows)
    indices, stride = _bounded_indices(total)
    rows = [rows[i] for i in indices]
    result = {'status': 'insufficient_samples', 'sample_count': total,
              'analyzed_count': len(rows), 'stride': stride, 'z': None}
    if len(rows) < 2 * MIN_POINTS:
        return result
    data = [(r[1], r[2]) for r in rows]
    if any(b[1] < a[1] for a, b in zip(data, data[1:])):
        result['status'] = 'non_monotonic_z'
        return result
    try:
        z, below, above, slope = fit(data)
        if (number(z) is None or number(slope) is None or slope >= 0.
                or not data[0][1] < z < data[-1][1]
                or min(below, above) < MIN_POINTS):
            result['status'] = 'unsupported_fit'
            return result
        free = [r for r in rows if r[2] > z]
        baseline = sum(r[1] for r in free) / len(free)
        noise = (sum((r[1] - baseline)**2 for r in free) / len(free))**.5
        free_slope = _slope([(r[2], r[1]) for r in free])
        rms = (sum((f - baseline - slope * min(p - z, 0.))**2
                   for f, p in data) / len(data))**.5
        result.update({'loaded_count': below, 'free_count': above,
            'force_slope_g_per_mm': slope, 'baseline_g': baseline,
            'free_noise_rms_g': noise, 'model_rms_g': rms,
            'free_slope_g_per_mm': free_slope,
            'baseline_trend_g_per_s': _slope([(r[0], r[1]) for r in free])})
        if max(f for f, p in data) - baseline <= max(5. * noise, 1.e-6):
            result['status'] = 'insufficient_signal'
        elif free_slope is None or abs(free_slope) > abs(slope) * .2:
            result['status'] = 'free_region_not_flat'
        else:
            result.update({'status': 'identified', 'z': z})
    except Exception as exc:
        result.update({'status': 'fit_error', 'error': str(exc)[:160]})
        warn('branch fit')
    return result


def _descent_threshold(rows):
    # This is a sampled force threshold, conditional on an unloaded prefix.
    result = {'status': 'insufficient_samples', 'z': None, 'time': None,
        'baseline_sample_count': BASELINE_POINTS,
        'confirmation_samples': MIN_POINTS, 'noise_multiplier': THRESHOLD_SIGMA,
        'minimum_rise_g': THRESHOLD_FLOOR_G,
        'baseline_assumption': 'initial_prefix_unloaded'}
    if len(rows) < BASELINE_POINTS + MIN_POINTS:
        return result
    if any(b[2] > a[2] for a, b in zip(rows, rows[1:])):
        result['status'] = 'non_monotonic_z'
        return result
    prefix = rows[:BASELINE_POINTS]
    baseline = sum(r[1] for r in prefix) / len(prefix)
    noise = (sum((r[1] - baseline)**2 for r in prefix) / len(prefix))**.5
    trend = _slope([(r[0], r[1]) for r in prefix])
    rise = max(THRESHOLD_SIGMA * noise, THRESHOLD_FLOOR_G)
    result.update({'baseline_g': baseline, 'noise_rms_g': noise,
        'threshold_g': baseline + rise, 'baseline_trend_g_per_s': trend})
    if trend is None or abs(trend) * (prefix[-1][0] - prefix[0][0]) > max(
            3. * noise, THRESHOLD_FLOOR_G):
        result['status'] = 'baseline_not_flat'
        return result
    run = 0
    for index in range(BASELINE_POINTS, len(rows)):
        run = run + 1 if rows[index][1] > baseline + rise else 0
        if run == MIN_POINTS:
            first = index - MIN_POINTS + 1
            result.update({'status': 'identified', 'z': rows[first][2],
                'time': rows[first][0], 'force_g': rows[first][1],
                'sample_index': first})
            return result
    result['status'] = 'no_sustained_crossing'
    return result


def build_report(samples, motion, lookup_z, fit, regression_z=None, error=None):
    # All estimates here are diagnostic only, including the descent hinge fit.
    frames = motion.get('frames', {})
    quality, cutoff = _references(frames)
    trigger = number(motion.get('trigger_time'))
    start, end = (number(motion.get(key))
                  for key in ('ascent_start_time', 'ascent_end_time'))
    period = number(motion.get('sample_period'))
    period = period if period is not None and period > 0. else 0.
    valid_bounds = (trigger is not None and trigger > 0. and start is not None
                    and end is not None and trigger <= start < end)
    rows, counts = [], {}
    invalid = bad_times = expired = lookup_errors = 0
    previous_time = None
    for sample in samples:
        values = [number(v) for v in sample[:4]]
        values += [None] * (4 - len(values))
        time, force, raw, tare = values
        phase, z = 'unknown', None
        if (time is None
            or (previous_time is not None and time <= previous_time)):
            bad_times += 1
        if time is not None:
            previous_time = time
            if cutoff is not None and time < cutoff:
                expired += 1
            else:
                try:
                    z = number(lookup_z(time))
                except Exception:
                    lookup_errors += 1
            if valid_bounds:
                phase = ('post_ascent' if time > end else
                         'ascent' if time >= start else
                         'trigger_region' if time >= trigger - period else
                         'descent')
        if time is None or force is None or z is None:
            invalid += 1
        rows.append([time, force, z, phase, raw, tare])
        counts[phase] = counts.get(phase, 0) + 1
    usable = valid_bounds and quality['references_complete'] and not (
        invalid or bad_times or expired or error or motion.get('sensor_errors'))
    invalid_reason = ('incomplete_references' if not
        quality['references_complete'] else 'invalid_timing_or_coordinates')
    descent = {'status': invalid_reason, 'z': None}
    ascent = dict(descent)
    threshold = dict(descent, time=None)
    if usable:
        # Include the trigger neighbourhood in descent; never sort by Z.
        descent_rows = [r for r in rows if r[0] <= trigger]
        window = number(motion.get('ascent_sample_time'))
        ascent_rows = [r for r in rows if r[3] == 'ascent'
                       and (window is None or r[0] <= start + window)]
        descent = _branch(descent_rows, fit, descending=True)
        threshold = _descent_threshold(descent_rows)
        ascent = _branch(ascent_rows, fit)
    comparable = (usable and quality['frame_state'] == 'consistent'
                  and descent['z'] is not None and ascent['z'] is not None)
    forces = [(i, r[1]) for i, r in enumerate(rows) if r[1] is not None]
    peak = max(forces, key=lambda item: item[1])[0] if forces else 0
    timed = [(i, r[0]) for i, r in enumerate(rows) if r[0] is not None]
    intervals = sorted(b[1] - a[1] for a, b in zip(timed, timed[1:])
                       if b[1] > a[1])
    sample_timing = None
    if intervals:
        middle = len(intervals) // 2
        median = (intervals[middle] if len(intervals) % 2 else
                  (intervals[middle-1] + intervals[middle]) * .5)
        sample_timing = {'minimum_interval_s': intervals[0],
            'median_interval_s': median, 'maximum_interval_s': intervals[-1],
            'measured_rate_hz': 1. / median}
    nearest = min(timed, key=lambda item: abs(item[1] - trigger))[0] \
        if timed and trigger is not None else 0
    anchors = [peak, nearest]
    if threshold.get('sample_index') is not None:
        anchors.append(threshold['sample_index'])
    for i in range(1, len(rows)):
        if rows[i][3] != rows[i-1][3]:
            anchors.extend((i-1, i))
    indices, stride = _bounded_indices(len(rows), anchors)
    trigger_z = None
    if trigger is not None and (cutoff is None or trigger >= cutoff):
        try:
            trigger_z = number(lookup_z(trigger))
        except Exception:
            pass
    quality.update({'bad_timestamps': bad_times, 'invalid_samples': invalid,
        'expired_history_samples': expired, 'lookup_errors': lookup_errors,
        'valid_time_bounds': valid_bounds})
    return {'version': 2, 'diagnostic_only': True,
        'name': motion.get('name'), 'tap_id': motion.get('tap_id'),
        'motion': motion, 'error': str(error)[:240] if error else None,
        'sample_count': len(rows), 'logged_count': len(indices),
        'stride': stride, 'phase_counts': counts,
        'sample_timing': sample_timing,
        'columns': ['time', 'force_g', 'z', 'phase', 'counts', 'tare_counts'],
        'samples': [rows[i] for i in indices],
        'quality': quality,
        'observed_max_force_g': max(f for i, f in forces) if forces else None,
        'trigger_time': trigger, 'force_trigger_z': number(motion.get('raw_z')),
        'trigger_reconstructed_z': trigger_z,
        'trigger_nearest_sample_dt': rows[nearest][0] - trigger
            if timed and trigger is not None else None,
        'trigger_nearest_sample_force_g': rows[nearest][1] if timed else None,
        'regression_contact_z': number(regression_z),
        'descent_fit_z': descent['z'], 'descent_threshold_z': threshold['z'],
        'mcu_trigger_z': number(motion.get('raw_z')),
        'ascent_fit_z': ascent['z'],
        'fit_interpretation': 'model_estimate_not_exact_physical_contact',
        'descent': descent, 'descent_threshold': threshold, 'ascent': ascent,
        'estimates_comparable': comparable,
        'ascent_minus_descent_fit_mm': ascent['z'] - descent['z']
            if comparable else None,
        'release_minus_onset_mm': ascent['z'] - descent['z']
            if comparable else None,
        'baseline_change_g': ascent['baseline_g'] - descent['baseline_g']
            if comparable else None}


def log_report(samples, motion, toolhead, lookup_z, fit, regression_z=None,
               error=None):
    # Observer failures must never replace a result or mask a safety exception.
    try:
        report = build_report(samples, motion,
            lambda time: lookup_z(toolhead, time), fit, regression_z, error)
        logging.info('Load cell tap diagnostic: %s',
                     json.dumps(_json_safe(report), separators=(',', ':'),
                                allow_nan=False))
    except Exception:
        warn('report')


def _json_safe(value):
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return number(value) if isinstance(value, float) else value
