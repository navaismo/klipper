# Descent-only contact-onset models; no printer, ADC or motion ownership
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import bisect
import math

from . import load_cell_diagnostics as diagnostics

# Statistical convention and finite computation budgets, not printer settings.
PROFILE_CHI2_95 = 3.841458820694124
SEARCH_STEPS = 32
MIN_POINTS = diagnostics.MIN_POINTS


def _effective_count(errors):
    # Initial-positive, monotone paired autocorrelation sums. Never assume
    # independent samples or increase information above the observed count.
    n = len(errors)
    mean = sum(errors) / n
    centered = [e - mean for e in errors]
    variance = sum(e*e for e in centered)
    if variance <= 0.:
        return 0.
    tau, previous = 1., float('inf')
    for lag in range(1, n // 2, 2):
        pair = sum(sum(a*b for a, b in zip(centered, centered[k:]))
                   / variance for k in (lag, lag + 1))
        if pair <= 0.:
            break
        pair = min(pair, previous)
        tau += 2.*pair
        previous = pair
    return n / tau


class _Profile:
    def __init__(self, rows):
        self.rows = rows
        self.z0 = rows[0][2]
        self.t0 = rows[0][0]
        self.duration = rows[-1][0] - self.t0
        self.f0 = rows[0][1]
        self.x = [self.z0 - r[2] for r in rows]
        self.t = [(r[0] - self.t0) / self.duration for r in rows]
        self.y = [r[1] - self.f0 for r in rows]
        # Prefix sums for a baseline in real time and a compression hinge in Z.
        self.prefix = [[0.] * 8]
        for x, t, y in zip(self.x, self.t, self.y):
            values = (x, x*x, t, t*t, y, y*y, x*t, x*y)
            self.prefix.append([a + b for a, b in
                                zip(self.prefix[-1], values)])
        self.total = self.prefix[-1]
        self.ty = sum(t*y for t, y in zip(self.t, self.y))
        n = len(rows)
        self.vt = self.total[3] - self.total[2]**2 / n
        self.vy = self.total[5] - self.total[4]**2 / n
        self.cty = self.ty - self.total[2]*self.total[4] / n
        self.null_sse = max(0., self.vy - self.cty**2 / self.vt)

    def at(self, contact):
        n = len(self.rows)
        split = bisect.bisect_right(self.x, contact)
        loaded = n - split
        if min(split, loaded) < MIN_POINTS:
            return None
        sx, sxx, st, unused, sy, unused, sxt, sxy = [
            a - b for a, b in zip(self.total, self.prefix[split])]
        sh = sx - loaded*contact
        shh = sxx - 2.*contact*sx + loaded*contact*contact
        sth = sxt - contact*st
        syh = sxy - contact*sy
        vh = shh - sh*sh/n
        cth = sth - self.total[2]*sh/n
        cyh = syh - self.total[4]*sh/n
        determinant = self.vt*vh - cth*cth
        if determinant <= 0. or determinant <= self.vt*vh*1.e-12:
            return None
        drift = (self.cty*vh - cyh*cth) / determinant
        compression = (cyh*self.vt - self.cty*cth) / determinant
        if compression <= 0.:
            return None
        baseline = (self.total[4] - drift*self.total[2]
                    - compression*sh) / n
        sse = max(0., self.vy - drift*self.cty - compression*cyh)
        return {'x': contact, 'z': self.z0 - contact, 'split': split,
                'sse': sse, 'drift': drift, 'compression': compression,
                'baseline': baseline}

    def errors(self, fitted):
        return [y - fitted['baseline'] - fitted['drift']*t
                - fitted['compression']*max(x - fitted['x'], 0.)
                for x, t, y in zip(self.x, self.t, self.y)]


def _fit_window(rows):
    result = {'status': 'unresolved', 'reasons': [], 'z': None,
              'model_z': None, 'sample_count': len(rows)}
    reasons = result['reasons']
    if len(rows) < 2*MIN_POINTS or rows[-1][0] <= rows[0][0]:
        reasons.append('insufficient_samples')
        return result
    profile = _Profile(rows)
    grid = sorted(set(profile.x[MIN_POINTS-1:-MIN_POINTS]))
    fits = [(x, profile.at(x)) for x in grid]
    valid = [f for x, f in fits if f is not None]
    if not valid:
        reasons.append('no_positive_compression_change')
        return result
    best = min(valid, key=lambda f: f['sse'])
    index = bisect.bisect_left(grid, best['x'])
    lo, hi = grid[max(0, index-1)], grid[min(len(grid)-1, index+1)]
    # Refine the winning grid interval; retain the global grid winner.
    for unused in range(SEARCH_STEPS):
        a, b = lo + (hi-lo)/3., hi - (hi-lo)/3.
        fa, fb = profile.at(a), profile.at(b)
        for f in (fa, fb):
            if f is not None and f['sse'] < best['sse']:
                best = f
        if fa is not None and (fb is None or fa['sse'] < fb['sse']):
            hi = b
        else:
            lo = a
    errors = profile.errors(best)
    split = best['split']
    free, loaded = errors[:split], errors[split:]
    free_eff, loaded_eff = _effective_count(free), _effective_count(loaded)
    # Use the slower observed decorrelation for the entire window. Do not
    # pool the two regions as independent repetitions of the experiment.
    effective = (len(errors) / max(len(free)/free_eff,
                                   len(loaded)/loaded_eff)
                 if min(free_eff, loaded_eff) > 0. else 0.)
    rms = math.sqrt(sum(e*e for e in errors) / len(errors))
    free_rms = math.sqrt(sum(e*e for e in free) / len(free))
    result.update({'model_z': best['z'], 'baseline_force_g':
        profile.f0 + best['baseline'], 'baseline_time_s': profile.t0,
        'baseline_drift_g_per_s': best['drift'] / profile.duration,
        'compression_slope_g_per_mm': best['compression'],
        'residual_rms_g': rms, 'baseline_rms_g': free_rms,
        'loaded_samples': len(loaded), 'free_samples': len(free),
        'effective_free_samples': free_eff,
        'effective_loaded_samples': loaded_eff,
        'effective_samples': effective,
        'sse': sum(e*e for e in errors), 'null_sse': profile.null_sse,
        'time_bounds_s': [rows[0][0], rows[-1][0]],
        'data_z_bounds_mm': [rows[-1][2], rows[0][2]]})
    if free_rms <= 0. or effective <= 4. or min(free_eff, loaded_eff) <= 2.:
        reasons.append('noise_or_effective_support_unverifiable')
        return result
    sse = result['sse']
    if sse <= 0. or profile.null_sse <= 0.:
        reasons.append('noise_or_change_unverifiable')
        return result
    # Four fitted parameters versus two for a drifting baseline alone.
    delta_bic = (effective*math.log(profile.null_sse/sse)
                 - 2.*math.log(effective))
    result['delta_bic'] = delta_bic
    if delta_bic <= 0.:
        reasons.append('drifting_baseline_explains_signal')
        return result
    threshold = sse * (1. + PROFILE_CHI2_95 / (effective - 4.))
    accepted = [f for f in valid if f['sse'] <= threshold] + [best]
    bounds = [min(f['x'] for f in accepted), max(f['x'] for f in accepted)]
    components, previous = 0, False
    for x, f in fits:
        inside = f is not None and f['sse'] <= threshold
        components += int(inside and not previous)
        previous = inside
    if components > 1:
        reasons.append('multimodal_profile')
    for side in (0, 1):
        outside = [x for x, f in fits if f is not None
                   and (x < bounds[0] if side == 0 else x > bounds[1])]
        if not outside:
            reasons.append('open_profile_interval')
            continue
        out = max(outside) if side == 0 else min(outside)
        inside = bounds[side]
        for unused in range(SEARCH_STEPS):
            middle = .5*(inside + out)
            f = profile.at(middle)
            if f is not None and f['sse'] <= threshold:
                inside = middle
            else:
                out = middle
        bounds[side] = inside
    # Expose finite sampling resolution; sub-sample fitting is not metrology.
    gaps = sorted(b-a for a, b in zip(profile.x, profile.x[1:]) if b > a)
    spacing = gaps[len(gaps)//2] if gaps else 0.
    interval = [profile.z0 - bounds[1] - spacing/2.,
                profile.z0 - bounds[0] + spacing/2.]
    result.update({'profile_interval_z_mm': interval,
        'sample_spacing_mm': spacing,
        'noise_to_compression_z_mm': free_rms / best['compression']})
    if not reasons:
        result.update({'status': 'identified', 'z': best['z']})
    return result


def estimate(report):
    """Infer an observational onset; never qualify or apply a probe offset."""
    result = {'version': 1, 'diagnostic_only': True, 'status': 'unresolved',
        'z': None, 'reasons': [], 'windows': [],
        'eligible_for_application': False, 'physical_accuracy_validated': False,
        'physical_uncertainty_mm': None,
        'interval_interpretation': 'approximate_correlation_adjusted_95_'
            'profile_and_sampling_envelope_not_physical_accuracy'}
    reasons = result['reasons']
    if not isinstance(report, dict):
        reasons.append('missing_acquisition')
        return result
    q = report.get('quality', {})
    motion = report.get('motion', {})
    if (not isinstance(q, dict) or not isinstance(motion, dict)
            or q.get('references_complete') is not True
            or q.get('frame_state') != 'consistent'
            or q.get('valid_time_bounds') is not True
            or any(q.get(k) != 0 for k in ('bad_timestamps', 'invalid_samples',
                'expired_history_samples', 'lookup_errors'))
            or motion.get('sensor_errors') or report.get('error')):
        reasons.append('invalid_acquisition_or_coordinates')
        return result
    original = report.get('samples', [])
    if (report.get('stride') != 1
            or len(original) != report.get('sample_count')):
        reasons.append('incomplete_acquisition')
        return result
    trigger = diagnostics.number(report.get('descent_end_time',
                                             report.get('trigger_time')))
    if trigger is None:
        reasons.append('missing_trigger_time')
        return result
    if any(not isinstance(r, (list, tuple)) or len(r) < 3
           or any(diagnostics.number(v) is None for v in r[:3])
           for r in original):
        reasons.append('invalid_descent_samples')
        return result
    rows = [r[:3] for r in original if r[0] <= trigger]
    if (len(rows) > diagnostics.MAX_SAMPLES or not rows
            or any(diagnostics.number(v) is None for r in rows for v in r)
            or any(b[0] <= a[0] or b[2] > a[2]
                   for a, b in zip(rows, rows[1:]))):
        reasons.append('invalid_descent_samples')
        return result
    basis = report.get('descent_window_basis', 'time')
    speed = diagnostics.number(report.get('descent_window_speed'))
    if basis not in ('time', 'travel') or (basis == 'travel'
                                          and (speed is None or speed <= 0.)):
        reasons.append('invalid_descent_window_reference')
        return result
    if basis == 'travel':
        result.update(window_basis=basis, window_speed_mm_s=speed)
    for duration in diagnostics.DESCENT_WINDOWS:
        # Stationary acquisition/analysis waits must not shrink the spatial
        # coverage of a window designed for continuous descent at this speed.
        # Keep real timestamps in the drift model; never compress sample time.
        span = duration * speed if basis == 'travel' and duration else None
        lower = trigger - duration if duration is not None else None
        selected = [r for r in rows if (r[2] <= rows[-1][2] + span
                    if span is not None else lower is None or r[0] >= lower)]
        missing = (rows[0][2] < rows[-1][2] + span if span is not None
                   else lower is not None and rows[0][0] > lower)
        if missing:
            fitted = {'status': 'unresolved', 'z': None,
                      'reasons': ['insufficient_window_coverage']}
        else:
            fitted = _fit_window(selected)
        entry = dict(fitted, window_s=duration)
        if basis == 'travel':
            entry.update(window_basis=basis, travel_span_mm=span)
        result['windows'].append(entry)
    valid = [w for w in result['windows'] if w['status'] == 'identified']
    result['supported_window_count'] = len(valid)
    if len(valid) < 2:
        reasons.append('insufficient_supported_windows')
        return result
    intervals = [w['profile_interval_z_mm'] for w in valid]
    result['window_span_mm'] = max(w['z'] for w in valid) - min(
        w['z'] for w in valid)
    result['interval_z_mm'] = [min(i[0] for i in intervals),
                               max(i[1] for i in intervals)]
    if max(i[0] for i in intervals) > min(i[1] for i in intervals):
        reasons.append('window_models_disagree')
        return result
    # Use the shortest supported local window, never average nested windows
    # as independent observations or choose a result using a printing offset.
    selected = valid[0]
    result.update({'status': 'identified', 'z': selected['z'],
        'selected_window_s': selected['window_s'],
        'baseline_force_g': selected['baseline_force_g'],
        'baseline_time_s': selected['baseline_time_s'],
        'baseline_drift_g_per_s': selected['baseline_drift_g_per_s'],
        'baseline_rms_g': selected['baseline_rms_g'],
        'compression_slope_g_per_mm': selected['compression_slope_g_per_mm'],
        'effective_samples': selected['effective_samples'],
        'delta_bic': selected['delta_bic']})
    return result
