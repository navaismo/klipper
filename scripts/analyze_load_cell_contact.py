#!/usr/bin/env python3
# Offline window, residual and force-ramp comparison of recorded contacts
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import argparse
import hashlib
import json
import math
import os

from replay_load_cell_contact import lcprobe, Printer, ROOT


def mean(values):
    return sum(values) / len(values)


def correlation(xs, ys):
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mx, my = mean(xs), mean(ys)
    xx = sum((x - mx)**2 for x in xs)
    yy = sum((y - my)**2 for y in ys)
    if not xx or not yy:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(xx*yy)


def ranks(values):
    ordered = sorted(values)
    return [mean([i + 1 for i, x in enumerate(ordered) if x == value])
            for value in values]


def fit_rows(rows, fit):
    if len(rows) < 6:
        return {'status': 'insufficient_samples', 'sample_count': len(rows)}
    z, loaded, free, slope = fit([(r[1], r[2]) for r in rows])
    # Recover the actual least-squares intercept at this hinge and slope.
    baseline = mean([r[1] - slope * min(r[2] - z, 0.) for r in rows])
    residuals = [r[1] - baseline - slope * min(r[2] - z, 0.) for r in rows]
    compressed = [e for r, e in zip(rows, residuals) if r[2] <= z]
    unloaded = [e for r, e in zip(rows, residuals) if r[2] > z]
    rms = lambda values: math.sqrt(mean([v*v for v in values])) \
        if values else None
    supported = (min(loaded, free) >= 3 and slope < 0.
                 and rows[0][2] < z < rows[-1][2])
    return {'status': 'supported' if supported else 'unsupported_fit',
        'sample_count': len(rows), 'loaded_samples': loaded,
        'free_samples': free, 'z': z, 'slope_g_per_mm': slope,
        'baseline_g': baseline, 'rms_g': rms(residuals),
        'loaded_rms_g': rms(compressed), 'free_rms_g': rms(unloaded),
        'residual_lag1_correlation': correlation(residuals[:-1], residuals[1:]),
        'time_interval_s': [min(r[0] for r in rows), max(r[0] for r in rows)],
        'z_interval_mm': [min(r[2] for r in rows), max(r[2] for r in rows)]}


def span(windows):
    values = [w['z'] for w in windows if w['status'] == 'supported']
    return max(values) - min(values) if values else None


def band_fit(rows, baseline, lower, upper):
    selected = [r for r in rows if lower <= r[1] - baseline <= upper]
    result = {'force_above_baseline_g': [lower, upper],
              'sample_count': len(selected), 'z_intercept': None}
    if len(selected) < 3:
        result['status'] = 'insufficient_samples'
        return result
    zs, fs = [r[2] for r in selected], [r[1] for r in selected]
    mz, mf = mean(zs), mean(fs)
    zz = sum((z - mz)**2 for z in zs)
    if not zz:
        result['status'] = 'no_position_range'
        return result
    slope = sum((z - mz)*(f - mf) for z, f in zip(zs, fs)) / zz
    if slope >= 0.:
        result['status'] = 'invalid_slope'
        return result
    result.update({'status': 'supported', 'slope_g_per_mm': slope,
        'z_intercept': mz + (baseline - mf) / slope,
        'rms_g': math.sqrt(mean([(f - mf - slope*(z - mz))**2
                                for z, f in zip(zs, fs)]))})
    return result


def first_crossing(rows, level, rising):
    # Interpolate within the first observed bracket; do not smooth or fit it.
    crossings = []
    for a, b in zip(rows, rows[1:]):
        crossed = a[1] < level <= b[1] if rising else a[1] > level >= b[1]
        if crossed:
            fraction = (level - a[1]) / (b[1] - a[1])
            crossings.append({'time': a[0] + fraction*(b[0] - a[0]),
                'z': a[2] + fraction*(b[2] - a[2]),
                'bracket_times': [a[0], b[0]],
                'bracket_z': [a[2], b[2]]})
    return {'crossing_count': len(crossings),
            'first': crossings[0] if crossings else None}


def analyze(record, fit):
    d = record['diagnostic']
    motion, rows = d['motion'], d['samples']
    if d['stride'] != 1 or len(rows) != d['sample_count']:
        raise ValueError('Complete original samples are required')
    trigger, start = d['trigger_time'], motion['ascent_start_time']
    descent = [r for r in rows if r[0] <= trigger]
    ascent = [r for r in rows if start <= r[0] <= min(
        motion['ascent_end_time'], start + motion['ascent_sample_time'])]
    df, af = fit_rows(list(reversed(descent)), fit), fit_rows(ascent, fit)
    assert abs(df['z'] - d['descent_fit_z']) < 1.e-9
    assert abs(af['z'] - d['ascent_fit_z']) < 1.e-9
    windows_d, windows_a = [], []
    for duration in (.25, .5, 1., 2., None):
        selected = [r for r in descent
                    if duration is None or r[0] >= trigger - duration]
        result = fit_rows(list(reversed(selected)), fit)
        windows_d.append(dict(result, window_s=duration))
    for duration in (1.5, 2., 3., 5.):
        selected = [r for r in ascent if r[0] <= start + duration]
        windows_a.append(dict(fit_rows(selected, fit), window_s=duration))
    # Limit band fits to the detected ramp, avoiding isolated free-air noise.
    onset = d['descent_threshold']['time']
    ramp_start = max(0, next(i for i, r in enumerate(descent)
                            if r[0] >= onset) - 1)
    ramp = descent[ramp_start:]
    bands = [band_fit(ramp, df['baseline_g'], lo, hi)
             for lo, hi in ((5., 25.), (25., 50.), (50., 75.))]
    loops = []
    for force in (20., 50.):
        down = first_crossing(ramp, df['baseline_g'] + force, True)
        up = first_crossing(ascent, af['baseline_g'] + force, False)
        loops.append({'force_above_branch_baseline_g': force,
            'descent': down, 'ascent': up,
            'ascent_minus_descent_z_mm': up['first']['z'] - down['first']['z']
                if up['first'] and down['first'] else None})
    raw_crossing = next(r for r in rows if r[0] <= trigger
                        and r[1] >= motion['safety']['trigger_force'])
    target = motion['target'][2]
    at_floor = abs(d['mcu_trigger_z'] - target) < 1.e-8
    # Only test numerical endpoint equality, not a physical clearance margin.
    at_endpoint = [r for r in rows if abs(r[2] - target) < 1.e-8]
    endpoint = {'classification': 'indeterminate' if at_floor
        else 'force_triggered_before_endpoint',
        'force_trigger_event': 'ENDSTOP_HIT inferred from successful home_wait',
        'raw_reason_code_logged': False,
        'trigger_z': d['mcu_trigger_z'], 'target_z': target,
        'trigger_above_endpoint_mm': d['mcu_trigger_z'] - target,
        'trigger_time': trigger,
        'first_endpoint_bulk_sample_time': at_endpoint[0][0]
            if at_endpoint else None,
        'first_raw_threshold_sample': raw_crossing,
        'trigger_minus_raw_threshold_s': trigger - raw_crossing[0],
        'limitation': 'No last-step clock or explicit floor-vs-force stop '
            'cause is present; coincident trigger and endpoint cannot '
            'establish which stopped motion first.' if at_floor else None}
    halt_z = motion['frames']['after_probe']['position'][2]
    hold = [r for r in rows if trigger <= r[0] < start
            and abs(r[2] - halt_z) < 1.e-8]
    hold_report = {'halt_z': halt_z, 'sample_count': len(hold),
        'time_before_ascent_s': start - trigger,
        'constant_z_duration_s': hold[-1][0] - hold[0][0] if hold else None,
        'first_force_g': hold[0][1] if hold else None,
        'last_force_g': hold[-1][1] if hold else None,
        'force_change_g': hold[-1][1] - hold[0][1] if hold else None}
    cr = record['cr_test_z']
    return {'id': record['id'], 'sample_count': len(rows), 'cr_test_z': cr,
        'temperatures': motion['frames']['before_probe']['temperatures'],
        'counts_per_gram': motion['safety']['counts_per_gram'],
        'tare_counts': motion['safety']['tare_counts'],
        'reference_tare_counts': motion['safety']['reference_tare_counts'],
        'fixed_safety_reference_counts': motion['safety'][
            'safety_reference_counts'],
        'descent': df, 'ascent': af,
        'descent_threshold': d['descent_threshold'],
        'descent_windows': windows_d, 'ascent_windows': windows_a,
        'descent_window_span_mm': span(windows_d),
        'ascent_window_span_mm': span(windows_a),
        'descent_force_bands': bands, 'force_level_loops': loops,
        'constant_z_hold': hold_report,
        'fit_ascent_minus_descent_mm': af['z'] - df['z'],
        'minimum_ascent_force_g': min(r[1] for r in ascent),
        'peak_force_g': max(r[1] for r in rows),
        'offset_from_descent_mm': cr - df['z'],
        'offset_from_ascent_mm': cr - af['z'], 'endpoint': endpoint}


def correlations(results):
    features = {'tare_counts': [r['tare_counts'] for r in results],
        'descent_stiffness': [-r['descent']['slope_g_per_mm'] for r in results],
        'ascent_stiffness': [-r['ascent']['slope_g_per_mm'] for r in results],
        'ascent_free_rms': [r['ascent']['free_rms_g'] for r in results],
        'negative_valley': [r['minimum_ascent_force_g'] for r in results],
        'descent_window_span': [r['descent_window_span_mm'] for r in results]}
    targets = {key: [r[key] for r in results] for key in (
        'offset_from_descent_mm', 'offset_from_ascent_mm')}
    return {name: {target: {'pearson_r': correlation(xs, ys),
        'spearman_rho': correlation(ranks(xs), ranks(ys))}
        for target, ys in targets.items()} for name, xs in features.items()}


def markdown(result):
    lines = ['# Four recorded contact curves: offline comparison', '',
        'Forces are indicated grams under the unchanged active scale. '
        'No physical surface reference is inferred from model agreement.', '',
        '| Record | D fit Z | A fit Z | D slope | A slope | D RMS | A RMS | '
        'A-D fit | D model offset | A model offset |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: '
        '| ---: |']
    for r in result['records']:
        values = [r['descent']['z'], r['ascent']['z'],
            r['descent']['slope_g_per_mm'], r['ascent']['slope_g_per_mm'],
            r['descent']['rms_g'], r['ascent']['rms_g'],
            r['fit_ascent_minus_descent_mm'], r['offset_from_descent_mm'],
            r['offset_from_ascent_mm']]
        lines.append('| %s | %s |' % (r['id'], ' | '.join(
            '%.6f' % v for v in values)))
    lines += ['', 'Units: Z/offset/difference in mm; slope in indicated '
        'g/mm; residual RMS in indicated g.', '',
        '## Window sensitivity', '',
        'Descending windows end at the recorded trigger; full includes the '
        'entire original descent. Ascending windows start at the original '
        'retraction. These are common exploratory windows, not tuned offsets.',
        '', '| Record | D window s | D fit Z | A window s | A fit Z |',
        '| --- | ---: | ---: | ---: | ---: |']
    for r in result['records']:
        for i, d in enumerate(r['descent_windows']):
            a = r['ascent_windows'][i] if i < len(r['ascent_windows']) else None
            lines.append('| %s | %s | %.6f | %s | %s |' % (
                r['id'], d['window_s'] if d['window_s'] else 'full', d['z'],
                a['window_s'] if a else '', '%.6f' % a['z'] if a else ''))
    lines += ['', '## Force-ramp shape', '',
        'Band fits use indicated force above the full-fit free baseline, '
        'starting one sample before the existing sustained-rise detection. '
        'They are descriptive extrapolations, not physical contact.', '',
        '| Record | Force band g | Samples | Slope g/mm | Z intercept mm |',
        '| --- | --- | ---: | ---: | ---: |']
    for r in result['records']:
        for b in r['descent_force_bands']:
            lines.append('| %s | %g-%g | %d | %s | %s |' % (
                r['id'], b['force_above_baseline_g'][0],
                b['force_above_baseline_g'][1], b['sample_count'],
                '%.3f' % b['slope_g_per_mm'] if b['z_intercept'] is not None
                else 'unsupported', '%.6f' % b['z_intercept']
                if b['z_intercept'] is not None else 'unsupported'))
    lines += ['', '## Residuals, apparent hysteresis and constant-Z hold', '',
        'Loop differences use the first interpolated crossings at equal '
        'force above each branch baseline. Recrossings are retained in JSON. '
        'Sensor/filter dynamics are not separated from mechanics.', '',
        '| Record | D loaded/free RMS g | A loaded/free RMS g | '
        'D/A lag-1 residual r | Loop 20g mm | Loop 50g mm | '
        'Hold duration s | Hold force change g |',
        '| --- | --- | --- | --- | ---: | ---: | ---: | ---: |']
    for r in result['records']:
        d, a, h = r['descent'], r['ascent'], r['constant_z_hold']
        loops = [l['ascent_minus_descent_z_mm'] for l in r['force_level_loops']]
        lines.append('| %s | %.3f / %.3f | %.3f / %.3f | %.3f / %.3f '
            '| %.6f | %.6f | %.6f | %.3f |' % (
                r['id'], d['loaded_rms_g'], d['free_rms_g'],
                a['loaded_rms_g'], a['free_rms_g'],
                d['residual_lag1_correlation'], a['residual_lag1_correlation'],
                loops[0], loops[1], h['constant_z_duration_s'],
                h['force_change_g']))
    lines += ['', '## Exploratory associations (n=4)', '',
        'Offsets are CR-relative model separations, avoiding direct '
        'comparison of absolute Z across homing epochs. Correlations use '
        'all four acquisitions; no significance or causal claim is made.', '',
        '| Feature | Pearson r: D offset | Pearson r: A offset | '
        'Spearman rho: D / A |',
        '| --- | ---: | ---: | --- |']
    for feature, targets in result['exploratory_correlations_n4'].items():
        d, a = (targets['offset_from_descent_mm'],
                targets['offset_from_ascent_mm'])
        lines.append('| %s | %.6f | %.6f | %.3f / %.3f |' % (
            feature, d['pearson_r'], a['pearson_r'],
            d['spearman_rho'], a['spearman_rho']))
    lines += ['', '## Endpoint classification', '']
    for r in result['records']:
        e = r['endpoint']
        lines.append('- %s: **%s**; trigger Z %.6f, target Z %.6f; '
            'trigger clock minus first raw threshold sample %.6f s.' % (
                r['id'], e['classification'], e['trigger_z'], e['target_z'],
                e['trigger_minus_raw_threshold_s']))
    lines += ['', 'The cold run has an acknowledged trigger path, but '
        'trigger and endpoint coincide. The raw trigger reason code and '
        'last-step clock were not captured. Therefore the mechanical stop '
        'cause is indeterminate, not proven force-first or floor-first.', '',
        '## Algorithm decision', '',
        'Propose one diagnostic addition: report descent/ascent window '
        'sensitivity alongside branch disagreement. The first two records '
        'are window-sensitive; the later hot record and the cold record '
        'are stable despite different model offsets. Do not change '
        'LCBestFit, the chosen Z, or the 0.025 mm budget from these four '
        'traces. Window stability measures model robustness, not absolute '
        'physical accuracy.', '',
        'For each acquisition and branch, the observable model separation '
        'is O_model = O_physical + b_branch, where b includes unmeasured '
        'load-transfer, timing and reference effects. Changing O_physical '
        'by delta and subtracting delta from both branch biases preserves '
        'all observed separations and branch agreement. The force curves '
        'alone do not determine that common bias. Raw ADC baseline '
        'correlation cannot remove this ambiguity.', '',
        'No runtime implementation change is made by this investigation.']
    lines += ['', '## Interpretation limits', '']
    lines += ['- ' + text for text in result['limitations']]
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', default=os.path.join(
        ROOT, 'out', 'contact-curve-comparison'))
    args = parser.parse_args()
    records, hashes = [], {}
    for relative in ('test/fixtures/load_cell_contact_real.json',
                     'test/fixtures/load_cell_contact_cold_20261102.json'):
        with open(os.path.join(ROOT, relative), 'rb') as source:
            data = source.read()
        hashes[relative] = hashlib.sha256(data).hexdigest()
        records.extend(json.loads(data)['records'])
    fit = lcprobe.LCBestFit(Printer()).find_best_fit
    results = [analyze(record, fit) for record in records]
    implementation = {}
    for relative in ('scripts/analyze_load_cell_contact.py',
                     'klippy/extras/load_cell_probe.py',
                     'klippy/extras/load_cell_contact.py',
                     'klippy/extras/trigger_analog.py', 'src/trigger_analog.c'):
        with open(os.path.join(ROOT, relative), 'rb') as source:
            implementation[relative] = hashlib.sha256(source.read()).hexdigest()
    result = {'offline_only': True, 'fixture_sha256': hashes,
        'implementation_sha256': implementation,
        'records': results,
        'exploratory_correlations_n4': correlations(results),
        'limitations': [
            'Four nonrandom acquisitions, three hot and one cold; '
            'correlation does not establish causation or generalization.',
            'Residual RMS and autocorrelation are descriptive; no IID '
            'assumption, standard error, confidence interval or sqrt(N) used.',
            'Force-band fits start at the existing sustained-rise detector; '
            'they do not independently locate earlier physical contact.',
            'Loop crossings are interpolated sample brackets relative to '
            'each fitted free baseline, not physical hysteresis metrology.',
            'The fixed 0.025 mm branch budget is experimental, not a '
            'calibrated accuracy or statistical significance threshold.',
            'A shared contact/reference bias leaves branch agreement and '
            'window stability unchanged; physical contact is unidentifiable.',
            'No movement, firmware, scale, offset or travel limit modified.']}
    os.makedirs(args.output_dir, exist_ok=True)
    with open(os.path.join(args.output_dir, 'comparison.json'), 'w') as out:
        json.dump(result, out, indent=2, allow_nan=False)
        out.write('\n')
    with open(os.path.join(args.output_dir, 'comparison.md'), 'w') as out:
        out.write(markdown(result))
    print(markdown(result))


if __name__ == '__main__':
    main()
