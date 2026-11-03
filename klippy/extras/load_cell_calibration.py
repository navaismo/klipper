# Offline ADC scale analysis; no printer, acquisition or persistence interfaces
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import math
import numbers


class CalibrationPolicy:
    # Experimental data-quality criteria, not physical safety guarantees.
    DEFAULTS = {
        'adc_min': -8388608., 'adc_max': 8388607.,
        'expected_sample_rate': 80., 'min_sample_fraction': .9,
        'min_duration': 8., 'min_samples': 32, 'max_gap_factor': 3.,
        'block_duration': 1., 'min_blocks': 4,
        'max_reference_age': 120., 'min_cycles': 3,
        'min_counts_per_gram': 1., 'min_signal_to_noise': 20.,
        'max_return_fraction': .01, 'max_repeatability_fraction': .02,
        'max_window_drift_fraction': .01,
        'max_block_span_fraction': .02,
        'max_peak_to_peak_fraction': .2,
    }

    def __init__(self, **options):
        unknown = set(options) - set(self.DEFAULTS)
        if unknown:
            raise ValueError('Unknown policy option: %s' % sorted(unknown))
        self.values = dict(self.DEFAULTS)
        self.values.update(options)
        for key, value in list(self.values.items()):
            self.values[key] = _number(value, key)
        for key, value in self.values.items():
            if key not in ('adc_min', 'adc_max') and value <= 0.:
                raise ValueError('%s must be positive' % key)
        for key in ('min_samples', 'min_blocks', 'min_cycles'):
            if self.values[key] != int(self.values[key]):
                raise ValueError('%s must be an integer' % key)
        if self.values['min_samples'] < 3 or self.values['min_blocks'] < 2:
            raise ValueError('Need at least three samples and two blocks')
        if self.values['min_cycles'] < 3:
            raise ValueError('Repeatability requires at least three cycles')
        if self.values['adc_min'] >= self.values['adc_max']:
            raise ValueError('Invalid ADC range')
        if self.values['min_sample_fraction'] > 1.:
            raise ValueError('min_sample_fraction must be <= 1')

    def __getitem__(self, name):
        return self.values[name]


def _number(value, name):
    try:
        if isinstance(value, bool):
            raise ValueError()
        value = float(value)
        if not (math.isnan(value) or math.isinf(value)):
            return value
    except (TypeError, ValueError, OverflowError):
        pass
    raise ValueError('%s must be finite' % name)


def _mean(values):
    return math.fsum(values) / len(values)


def _median(values):
    values = sorted(values)
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return .5 * (values[middle - 1] + values[middle])


def _load(mass_g, orientation):
    mass_g = _number(mass_g, 'mass_g')
    orientation = _number(orientation, 'orientation')
    if mass_g <= 0. or orientation not in (-1., 1.):
        raise ValueError('Need positive mass and orientation +1 or -1')
    return mass_g, orientation


def estimate_scale(before_counts, before_time, loaded_counts, loaded_time,
                   after_counts, after_time, mass_g, orientation):
    """Interpolate a hypothesized linear zero drift; not a quality verdict.

    Summary means alone cannot establish stability or uncertainty. Times must
    be measured in seconds in the same clock domain, with no intervening reset.
    """
    mass_g, orientation = _load(mass_g, orientation)
    a, b, c = [_number(v, 'ADC mean') for v in (
        before_counts, loaded_counts, after_counts)]
    ta, tb, tc = [_number(v, 'mean timestamp') for v in (
        before_time, loaded_time, after_time)]
    if not ta < tb < tc:
        raise ValueError('Reference timestamps must bracket the load')
    alpha = (tb - ta) / (tc - ta)
    zero = a + alpha * (c - a)
    delta = b - zero
    return {
        'reference_counts': zero, 'loaded_counts': b,
        'before_counts': a, 'after_counts': c,
        'interpolation_fraction': alpha,
        'baseline_model': 'linear_drift_hypothesis',
        'delta_counts': delta, 'oriented_delta_counts': orientation * delta,
        'counts_per_gram': orientation * delta / mass_g,
        'before_only_counts_per_gram': orientation * (b - a) / mass_g,
        'after_only_counts_per_gram': orientation * (b - c) / mass_g,
        'zero_return_counts': c - a,
        'baseline_drift_counts_per_second': (c - a) / (tc - ta),
        'before_reference_age': tb - ta,
        'after_reference_age': tc - tb,
    }


def _window(window, policy):
    result = {'valid': False, 'rejections': []}
    reject = result['rejections'].append
    if not isinstance(window, dict):
        reject('missing_window')
        return result
    for key in ('errors', 'overflows', 'saturated'):
        try:
            count = _number(window.get(key), key)
            if count < 0. or count != int(count):
                raise ValueError()
            if count:
                reject('sensor_%s' % key)
        except ValueError:
            reject('missing_or_invalid_%s' % key)
    try:
        samples = []
        for row in window['samples']:
            timestamp = _number(row[0], 'timestamp')
            count = _number(row[1], 'ADC count')
            # Preserve original integer ADC values, including integral floats.
            # Reject fractional/converted values instead of rounding them.
            if not isinstance(row[1], numbers.Real) or count != int(count):
                reject('non_integer_adc_counts')
                return result
            samples.append((timestamp, int(count)))
        if len(samples) < 3:
            raise ValueError()
    except (KeyError, TypeError, IndexError, ValueError):
        reject('missing_or_invalid_samples')
        return result
    times, counts = zip(*samples)
    gaps = [b - a for a, b in zip(times, times[1:])]
    if min(gaps) <= 0.:
        reject('non_increasing_timestamps')
        return result
    if min(counts) <= policy['adc_min'] or max(counts) >= policy['adc_max']:
        reject('adc_at_or_outside_rails')
        return result
    duration = times[-1] - times[0]
    if math.isinf(duration):
        reject('invalid_time_span')
        return result
    if duration < policy['min_duration']:
        reject('insufficient_duration')
        return result
    required = max(policy['min_samples'],
                   policy['min_sample_fraction']
                   * policy['expected_sample_rate'] * duration)
    if len(samples) < required:
        reject('insufficient_sample_coverage')
        return result
    if max(gaps) > policy['max_gap_factor'] * _median(gaps):
        reject('acquisition_gap')
    # Center both axes to avoid cancellation at large ADC/time origins.
    mean_t = times[0] + _mean([t - times[0] for t in times])
    mean_c = _mean(counts)
    dt = [t - mean_t for t in times]
    dc = [c - mean_c for c in counts]
    slope = math.fsum(t * c for t, c in zip(dt, dc)) / math.fsum(
        t * t for t in dt)
    sd = math.sqrt(math.fsum(c * c for c in dc) / (len(dc) - 1))
    if sd == 0.:
        # A frozen or quantized window cannot establish measured noise.
        # Keep its statistics for diagnosis without qualifying the acquisition.
        reject('zero_dispersion_unverifiable')
    median_c = _median(counts)
    # MAD is descriptive; it is not a confidence interval for the mean.
    mad = _median([abs(c - median_c) for c in counts])
    blocks = {}
    for t, c in samples:
        index = int((t - times[0]) / policy['block_duration'])
        blocks.setdefault(index, []).append((t, c))
    block_means = []
    for index in sorted(blocks):
        block = blocks[index]
        span = block[-1][0] - block[0][0]
        if span >= .8 * policy['block_duration']:
            block_means.append(_mean([row[1] for row in block]))
    if len(block_means) < policy['min_blocks']:
        reject('insufficient_time_blocks')
    block_span = (max(block_means) - min(block_means)
                  if block_means else None)
    # No sqrt(N) reduction: keep raw dispersion and low-frequency block motion.
    envelope = max([sd, abs(slope) * duration] +
                   [abs(b - mean_c) for b in block_means])
    result.update({
        'samples': len(samples), 'start_time': times[0], 'end_time': times[-1],
        'mean_time': mean_t, 'duration': duration, 'mean_counts': mean_c,
        'standard_deviation_counts': sd, 'mad_counts': mad,
        'peak_to_peak_counts': max(counts) - min(counts),
        'slope_counts_per_second': slope,
        'window_drift_counts': slope * duration,
        'observed_sample_rate': (len(samples) - 1) / duration,
        'max_gap_seconds': max(gaps), 'block_means_counts': block_means,
        'block_span_counts': block_span,
        'observed_envelope_counts': envelope,
    })
    result['valid'] = not result['rejections']
    return result


def _cycle(cycle, index, mass_g, orientation, policy, mass_tolerance_g):
    result = {'index': index, 'quality_passed': False, 'rejections': []}
    reject = result['rejections'].append
    if not isinstance(cycle, dict):
        reject('invalid_cycle')
        return result
    windows = {key: _window(cycle.get(key), policy)
               for key in ('before', 'loaded', 'after')}
    result['windows'] = windows
    for key, window in windows.items():
        result['rejections'].extend('%s:%s' % (key, reason)
                                    for reason in window['rejections'])
    if any('mean_time' not in w for w in windows.values()):
        return result
    a, b, c = [windows[key] for key in ('before', 'loaded', 'after')]
    if not (a['end_time'] < b['start_time']
            and b['end_time'] < c['start_time']):
        reject('overlapping_or_unordered_windows')
        return result
    scale = estimate_scale(
        a['mean_counts'], a['mean_time'], b['mean_counts'], b['mean_time'],
        c['mean_counts'], c['mean_time'], mass_g, orientation)
    result.update(scale)
    signal = scale['oriented_delta_counts']
    if signal <= 0.:
        reject('wrong_direction_or_zero_signal')
        return result
    if scale['counts_per_gram'] < policy['min_counts_per_gram']:
        reject('insufficient_sensitivity')
    for key in ('before_reference_age', 'after_reference_age'):
        if scale[key] > policy['max_reference_age']:
            reject('stale_%s' % key)
    noise = max(w['standard_deviation_counts'] for w in windows.values())
    # Zero measured dispersion does not prove absence of unobserved error.
    result['signal_to_noise'] = signal / noise if noise else None
    result['noise_counts'] = noise
    if noise == 0.:
        reject('signal_to_noise_unverifiable')
    elif noise * policy['min_signal_to_noise'] > signal:
        reject('insufficient_signal_to_noise')
    result['zero_return_fraction'] = abs(scale['zero_return_counts']) / signal
    if result['zero_return_fraction'] > policy['max_return_fraction']:
        reject('zero_return_exceeded')
    for key, window in windows.items():
        for field, limit, reason in (
                ('window_drift_counts', 'max_window_drift_fraction',
                 'window_drift_exceeded'),
                ('block_span_counts', 'max_block_span_fraction',
                 'block_span_exceeded'),
                ('peak_to_peak_counts', 'max_peak_to_peak_fraction',
                 'peak_to_peak_exceeded')):
            value = window[field]
            if value is not None and abs(value) > signal * policy[limit]:
                reject('%s:%s' % (key, reason))
    alpha = scale['interpolation_fraction']
    acquisition = (b['observed_envelope_counts']
                   + (1. - alpha) * a['observed_envelope_counts']
                   + alpha * c['observed_envelope_counts'])
    # Sensitivity to choosing either measured zero instead of linear drift.
    # This observed allowance cannot bound arbitrary drift between windows.
    baseline = max(abs(scale['reference_counts'] - w['mean_counts'])
                   for w in (a, c))
    envelope = acquisition + baseline
    interval = [max(0., signal - envelope) / mass_g,
                (signal + envelope) / mass_g]
    total = None
    if mass_tolerance_g is not None:
        total = [max(0., signal - envelope) / (mass_g + mass_tolerance_g),
                 (signal + envelope) / (mass_g - mass_tolerance_g)]
    result['uncertainty'] = {
        'method': 'observed_envelope_not_confidence_interval',
        'acquisition_counts': acquisition,
        'baseline_model_allowance_counts': baseline,
        'adc_envelope_counts': envelope,
        'adc_only_counts_per_gram_interval': interval,
        'mass_tolerance_g': mass_tolerance_g,
        'mass_tolerance_known': mass_tolerance_g is not None,
        'combined_counts_per_gram_interval': total,
        'complete_metrological_uncertainty': False,
    }
    result['quality_passed'] = not result['rejections']
    return result


def analyze_calibration(cycles, mass_g, orientation, mass_tolerance_g=None,
                        policy=None):
    """Analyze timestamped static-load cycles without applying any result.

    Each cycle has before/loaded/after windows with samples [seconds, ADC]
    and mandatory errors/overflows/saturated counters. Clock domain and ADC
    gain/channel must remain identical; callers must establish those facts.
    mass_tolerance_g is an optional absolute +/- bound, not a standard error.
    """
    mass_g, orientation = _load(mass_g, orientation)
    if mass_tolerance_g is not None:
        mass_tolerance_g = _number(mass_tolerance_g, 'mass_tolerance_g')
        if not 0. <= mass_tolerance_g < mass_g:
            raise ValueError('Mass tolerance must be >= 0 and < mass')
    if policy is None:
        policy = CalibrationPolicy()
    reports = [_cycle(c, i + 1, mass_g, orientation, policy, mass_tolerance_g)
               for i, c in enumerate(cycles)]
    rejections = ['cycle_%s:%s' % (r['index'], reason)
                  for r in reports for reason in r['rejections']]
    if len(reports) < policy['min_cycles']:
        rejections.append('insufficient_cycles')
    # Chronological cycles cannot silently reuse the same reference/load data.
    for prev, curr in zip(reports, reports[1:]):
        try:
            end = prev['windows']['after']['end_time']
            start = curr['windows']['before']['start_time']
            if start <= end:
                rejections.append('overlapping_or_unordered_cycles')
        except KeyError:
            pass
    scales = [r['counts_per_gram'] for r in reports
              if r.get('counts_per_gram', 0.) > 0.]
    consolidated = _median(scales) if len(scales) == len(reports) and scales \
        else None
    variation = None
    if consolidated is not None:
        variation = (max(scales) - min(scales)) / consolidated
        if variation > policy['max_repeatability_fraction']:
            rejections.append('repeatability_exceeded')
    intervals = [r['uncertainty'] for r in reports if 'uncertainty' in r]
    adc_interval, combined_interval = None, None
    if intervals and len(intervals) == len(reports):
        bounds = [u['adc_only_counts_per_gram_interval'] for u in intervals]
        adc_interval = [min(b[0] for b in bounds), max(b[1] for b in bounds)]
        if mass_tolerance_g is not None:
            bounds = [u['combined_counts_per_gram_interval'] for u in intervals]
            combined_interval = [min(b[0] for b in bounds),
                                 max(b[1] for b in bounds)]
    passed = not rejections
    return {
        'schema_version': 1, 'quality_passed': passed,
        'rejections': rejections, 'mass_g': mass_g, 'orientation': orientation,
        'policy': dict(policy.values), 'cycles': reports,
        'consolidated_counts_per_gram': consolidated,
        'cycle_variation_fraction': variation,
        'usable_candidate_counts_per_gram': consolidated if passed else None,
        # Numerical eligibility never grants permission to apply calibration.
        # A future integration must own validation and acceptance separately.
        'authorization': {
            'physical_validation': 'not_assessed',
            'user_acceptance': 'not_requested',
            'application_authorized': False,
        },
        'uncertainty': {
            'method': 'observed_envelope_not_confidence_interval',
            'adc_only_counts_per_gram_interval': adc_interval,
            'mass_tolerance_g': mass_tolerance_g,
            'mass_tolerance_known': mass_tolerance_g is not None,
            'combined_counts_per_gram_interval': combined_interval,
            'complete_metrological_uncertainty': False,
        },
        'warnings': [
            'Linear baseline drift is a hypothesis, '
            'not a universal correction.',
            'Observed envelopes do not bound unobserved drift or systematic '
            'mechanical, thermal, timing or reference-mass errors.',
            'No independence assumption or sqrt(N) uncertainty reduction.',
            'Experimental quality criteria are not physical safety limits.',
        ] + (['Reference mass tolerance is unknown.']
             if mass_tolerance_g is None else []),
    }
