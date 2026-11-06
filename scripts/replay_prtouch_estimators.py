#!/usr/bin/env python3
# Replay independent contact estimators on recorded ADC/motion data only
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import argparse
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'test', 'unit'))
# Existing offline harness provides LCBestFit's printer/MCU imports.
from test_auto_z_offset import auto, lcprobe, Printer


def records(data):
    if 'records' in data:
        return data['records']
    # Existing complete log extraction: join each tap with its paired CR
    # event by the actual tap ID, never by proximity or list position.
    frames = {}
    for frame in data['frames']:
        if frame.get('event') == 'pair_result':
            key = frame['tap_link']['tap_id']
            if key in frames:
                raise ValueError('Ambiguous tap ID in session extraction')
            frames[key] = frame
    result = []
    for tap in data['taps']:
        frame = frames[tap['tap_id']]
        result.append({'id': frame['stage'], 'diagnostic': tap,
                       'cr_test_z': frame['cr']['test_z']})
    return result


def replay(record):
    diagnostic = record['diagnostic']
    motion = diagnostic['motion']
    stop = motion.get('contact_stop', {})
    report = stop.get('decision_report', diagnostic)
    current = auto.load_cell_contact_onset.estimate(report)
    prtouch = auto.load_cell_prtouch_estimator.estimate(report,
                                        motion.get('sample_period'))
    fit = lcprobe.LCBestFit(Printer()).find_best_fit
    start = motion['ascent_start_time']
    end = min(motion['ascent_end_time'], start + motion['ascent_sample_time'])
    rows = [(r[1], r[2]) for r in diagnostic['samples']
            if start <= r[0] <= end]
    legacy = fit(rows)[0] if len(rows) >= 3 else None
    cr_z = record['cr_test_z']
    result = {'id': record['id'], 'cr_test_z': cr_z,
        'descent_samples': report['sample_count'],
        'lcbestfit': {'z': legacy,
            'offset': cr_z - legacy if legacy is not None else None,
            'diagnostic_only': True, 'eligible_for_application': False},
        'current': current, 'prtouch': prtouch}
    for method in (current, prtouch):
        method['observational_offset'] = cr_z - method['z'] \
            if method['z'] is not None else None
    return result


def digest(path):
    with open(path, 'rb') as source:
        return hashlib.sha256(source.read()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files', nargs='+', help='Recorded JSON acquisitions')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = {'sources': {path: digest(path) for path in args.files},
        'code_sha256': {name: digest(os.path.join(ROOT, name)) for name in (
            'klippy/extras/load_cell_prtouch_estimator.py',
            'klippy/extras/load_cell_contact_onset.py',
            'klippy/extras/load_cell_probe.py')}, 'records': []}
    for path in args.files:
        with open(path) as source:
            result['records'].extend(replay(r) for r in records(
                                                            json.load(source)))
    with open(args.output, 'w') as output:
        json.dump(result, output, indent=2, sort_keys=True, allow_nan=False)
        output.write('\n')
    print('Trace | LCBestFit Z / offset | Current Z / offset | PRTouch')
    def value(number):
        return 'unresolved' if number is None else '%.6f' % number
    for r in result['records']:
        legacy, current, prtouch = (r[k] for k in (
                                    'lcbestfit', 'current', 'prtouch'))
        print('%s | %s / %s | %s / %s | %s: %s / %s; %s' % (
            r['id'], value(legacy['z']), value(legacy['offset']),
            value(current['z']), value(current['observational_offset']),
            prtouch['status'], value(prtouch['z']),
            value(prtouch['observational_offset']),
            ', '.join(prtouch['reasons']) or 'none'))


if __name__ == '__main__':
    main()
