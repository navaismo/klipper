# Independent, offline PRTouch contact-index estimation
# Original PRTouch: Copyright (C) 2022-12-09 CC <wangyulong878@sina.com>
# Original filters: Copyright (C) 2020 Alan Lord <alanslists@gmail.com>
# This port: Copyright (C) 2026
#
# This file may be distributed under the terms of the GNU GPLv3 license.
# Sources: giorgiomaccari/e3v3se_klipper_display_customCal, master,
# klippy/extras/prtouch.py (_check_trigger, _cal_min_z) and filter.py.
# https://github.com/giorgiomaccari/e3v3se_klipper_display_customCal
import math

PI_COUNT = 32
BUFFER_COUNT = PI_COUNT * 2
MIN_HOLD = 3000
MAX_HOLD = 50000
ACQUISITION_HZ = 80.
HIGH_PASS_HZ = 5.
LOW_PASS_K = OFFSET_K = CALIBRATION_K = .8
# hx711s.py marks readings with it > 20 ms dirty. Never silently remove
# missing samples or resample a recording to satisfy the original filters.
MAX_INTERVAL_S = .020


class RCTFilter:
    def ftr_val(self, vals):
        out_vals = []
        if len(vals) < 3:
            return vals
        for i in range(len(vals) - 2):
            tmp = [math.fabs(vals[i]), math.fabs(vals[i + 1]),
                   math.fabs(vals[i + 2])]
            index = tmp.index(min(tmp))
            out_vals.append(vals[index + i])
        out_vals.append(vals[-2])
        out_vals.append(vals[-1])
        return out_vals


class RCHFilter:
    def __init__(self, cut_frq_hz, acq_frq_hz):
        self.cut_frq_hz = cut_frq_hz
        self.acq_frq_hz = acq_frq_hz

    def ftr_val(self, vals):
        out_vals = [0]
        rc = 1. / 2. / math.pi / self.cut_frq_hz
        coff = rc / (rc + 1. / self.acq_frq_hz)
        for i in range(1, len(vals)):
            out_vals.append((vals[i] - vals[i - 1] + out_vals[-1]) * coff)
        return out_vals


class RCLFilter:
    def __init__(self, k1_new):
        self.k1_new = k1_new

    def ftr_val(self, vals):
        out_vals = [vals[0]]
        for i in range(1, len(vals)):
            out_vals.append(out_vals[-1] * (1 - self.k1_new)
                            + vals[i] * self.k1_new)
        return out_vals


def _filter(vals, coefficient):
    # Original per-channel processing, including magnitude after filtering.
    vals = RCTFilter().ftr_val(vals)
    vals = RCHFilter(HIGH_PASS_HZ, ACQUISITION_HZ).ftr_val(vals)
    vals = RCLFilter(coefficient).ftr_val(vals)
    return [abs(v) for v in vals[-PI_COUNT:]]


def _contact_index(vals):
    # _cal_min_z mathematics: the normalized, rotated curve's first minimum.
    # The denominator is len(vals), deliberately not len(vals) - 1.
    maximum, minimum = max(vals), min(vals)
    if maximum == minimum:
        return None
    vals_p = [(v - minimum) / (maximum - minimum) for v in vals]
    angle = math.atan((vals_p[-1] - vals_p[0]) / len(vals_p))
    sin_angle, cos_angle = math.sin(-angle), math.cos(-angle)
    rotated = [i * sin_angle + v * cos_angle
               for i, v in enumerate(vals_p)]
    return rotated.index(min(rotated))


def _check_trigger(fit_vals):
    # Original signal rules only. Exhausting dirzctl travel is NOT contact.
    if (len(fit_vals) >= PI_COUNT / 2
            and all(math.fabs(v) >= MAX_HOLD for v in fit_vals[-3:])):
        return 'max_hold'
    if len(fit_vals) != PI_COUNT:
        return None
    vals = list(fit_vals)
    for i in range(1, PI_COUNT - 1):
        if (vals[i] >= MAX_HOLD and vals[i - 1] < MAX_HOLD / 2
                and vals[i + 1] < MAX_HOLD / 2):
            vals[i] = vals[i - 1]
    index = _contact_index(vals)
    # In the original constant-signal trigger normalization the artificial
    # unit range gives index 0; its strict-increase rule then rejects it.
    index = 0 if index is None else index
    if index > 0:
        for unused in range(index, PI_COUNT):
            # Preserve the original repeated update of ONE index. Do not
            # silently turn this into a different smoothing algorithm.
            vals[index] = (vals[index] * (OFFSET_K / 2)
                           + vals[index - 1] * (1 - OFFSET_K / 2))
    if not vals[-1] > vals[-2] > vals[-3]:
        return None
    maximum = max(vals[:PI_COUNT - 3])
    if not all(v > maximum for v in vals[-3:]):
        return None
    maximum, minimum = max(vals), min(vals)
    vals = [(v - minimum) / (maximum - minimum) for v in vals]
    for i in range(PI_COUNT - 1):
        if ((vals[-1] - vals[i]) / ((PI_COUNT - i) * 1. / PI_COUNT)
                < .8):
            return None
    if (fit_vals[-1] < MIN_HOLD or fit_vals[-2] < MIN_HOLD / 2
            or fit_vals[-3] < MIN_HOLD / 3):
        return None
    return 'signal_rules'


def _number(value):
    try:
        value = float(value)
        return value if not (math.isnan(value) or math.isinf(value)) else None
    except (TypeError, ValueError, OverflowError):
        return None


def estimate(report, sample_period=None):
    """Replay PRTouch on original ADC samples; never move or apply an offset.

    The acquisition's recorded per-tap tare replaces hx711s.read_base().
    No force-to-count conversion, resampling, signal-delay compensation or
    dirzctl endpoint interpolation is performed. The selected original row
    already carries Z reconstructed from Klipper MCU-time motion history.
    """
    result = {'version': 1, 'estimator': 'prtouch',
        'status': 'not_evaluable', 'z': None, 'time': None, 'reasons': [],
        'diagnostic_only': True, 'eligible_for_application': False,
        'physical_accuracy_validated': False,
        'parameters': {'pi_count': PI_COUNT, 'buffer_count': BUFFER_COUNT,
            'min_hold_counts': MIN_HOLD, 'max_hold_counts': MAX_HOLD,
            'acquisition_hz': ACQUISITION_HZ, 'high_pass_hz': HIGH_PASS_HZ,
            'low_pass_k': LOW_PASS_K, 'calibration_k': CALIBRATION_K},
        'reference': 'recorded_operational_tare',
        'z_mapping': 'original_sample_mcu_time_motion_history',
        'replay_limitations': [
            'original_read_base_not_repeated',
            'original_post_stop_transport_buffer_not_reproduced',
            'filtered_index_is_not_proof_of_physical_first_contact']}
    reasons = result['reasons']
    if not isinstance(report, dict):
        reasons.append('missing_acquisition')
        return result
    quality, motion = report.get('quality', {}), report.get('motion', {})
    if (not isinstance(quality, dict) or not isinstance(motion, dict)
            or quality.get('references_complete') is not True
            or quality.get('frame_state') != 'consistent'
            or quality.get('valid_time_bounds') is not True
            or any(quality.get(k) != 0 for k in ('bad_timestamps',
                'invalid_samples', 'expired_history_samples', 'lookup_errors'))
            or motion.get('sensor_errors') or report.get('error')):
        reasons.append('invalid_acquisition_or_coordinates')
        return result
    rows = report.get('samples')
    if (not isinstance(rows, list) or report.get('stride') != 1
            or len(rows) != report.get('sample_count')):
        reasons.append('incomplete_acquisition')
        return result
    period = _number(motion.get('sample_period')
                     if sample_period is None else sample_period)
    if period is None or abs(period * ACQUISITION_HZ - 1.) > 1.e-9:
        reasons.append('requires_original_80_sps_configuration')
        return result
    end = _number(report.get('descent_end_time', report.get('trigger_time')))
    if end is None:
        reasons.append('missing_descent_end_time')
        return result
    selected, previous, tare = [], None, None
    for row in rows:
        if (not isinstance(row, (list, tuple)) or len(row) < 6
                or any(_number(row[i]) is None for i in (0, 2, 4, 5))):
            reasons.append('missing_original_adc_time_or_z')
            return result
        time, z, raw, zero = [float(row[i]) for i in (0, 2, 4, 5)]
        if previous is not None and time <= previous:
            reasons.append('nonmonotonic_timestamps')
            return result
        previous = time
        if time > end:
            continue
        if raw != int(raw) or not -8388608 <= raw <= 8388607:
            reasons.append('invalid_original_adc_counts')
            return result
        if tare is not None and zero != tare:
            reasons.append('changed_operational_tare')
            return result
        tare = zero
        if selected and time - selected[-1][0] > MAX_INTERVAL_S + 1.e-9:
            reasons.append('sampling_gap_exceeds_original_dirty_limit')
            return result
        if selected and z > selected[-1][2]:
            reasons.append('non_descent_motion')
            return result
        selected.append(row)
    result['descent_sample_count'] = len(selected)
    capture_start = _number(motion.get('collector_start_time'))
    known_start = (bool(selected) and capture_start is not None
                   and 0. <= selected[0][0] - capture_start
                   <= MAX_INTERVAL_S + 1.e-9)
    # A rolling report may omit earlier samples. Do not reset the original
    # filter at its arbitrary left edge and interpret that transient as a
    # detector event. A complete 64-row buffer then reproduces the original
    # rolling filter without needing any unavailable prehistory.
    minimum = PI_COUNT if known_start else BUFFER_COUNT
    result['buffer_initialization'] = ('recorded_acquisition_start'
        if known_start else 'complete_rolling_buffer_required')
    if len(selected) < minimum:
        reasons.append('insufficient_original_samples')
        return result
    for end_index in range(minimum - 1, len(selected)):
        start = max(0, end_index + 1 - BUFFER_COUNT)
        window = selected[start:end_index + 1]
        values = [float(r[4]) - tare for r in window]
        fit_vals = _filter(values, LOW_PASS_K)
        result['maximum_filtered_counts'] = max(
            result.get('maximum_filtered_counts', 0.), max(fit_vals))
        trigger = _check_trigger(fit_vals)
        if trigger is None:
            continue
        result.update({'trigger_rule': trigger,
            'detection_time': window[-1][0],
            'detection_z': window[-1][2],
            'detection_sample_index': end_index,
            'detection_filtered_counts': fit_vals[-3:]})
        if trigger != 'signal_rules':
            reasons.append('max_hold_is_protection_not_contact')
            return result
        calibrated = _filter(values, CALIBRATION_K)
        index = _contact_index(calibrated)
        if index is None:
            reasons.append('constant_filtered_signal')
            return result
        index += len(window) - len(calibrated)
        row = window[index]
        if window[0][2] == window[-1][2]:
            reasons.append('no_recorded_descent_in_detection_window')
            return result
        result.update({'status': 'identified', 'z': row[2], 'time': row[0],
            'sample_index': start + index,
            'contact_index': index - (len(window) - len(calibrated)),
            'window_start_time': window[0][0],
            'window_end_time': window[-1][0],
            'tare_counts': tare, 'raw_counts': row[4],
            'stationary_intervals_in_window': sum(a[2] == b[2]
                for a, b in zip(window, window[1:])),
            'filtered_window_counts': calibrated})
        return result
    reasons.append('original_signal_rules_not_met_before_acquisition_end')
    return result
