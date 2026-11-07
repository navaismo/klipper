#!/usr/bin/env python3
# Replay recorded contacts without a printer connection or hardware imports
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import argparse
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
# The existing offline harness supplies printer/MCU mocks for LCBestFit.
sys.path.insert(0, os.path.join(ROOT, 'test', 'unit'))
from test_auto_z_offset_cr import auto, lcprobe, Printer


def replay(record, tolerance=.025):
    original = record['diagnostic']
    rows = original['samples']
    if (original['stride'] != 1 or len(rows) != original['sample_count']
            or len(set(row[0] for row in rows)) != len(rows)):
        raise ValueError('Replay requires full, uniquely timestamped samples')
    motion = original['motion']
    start = motion['ascent_start_time']
    end = min(motion['ascent_end_time'],
              start + motion['ascent_sample_time'])
    fit = lcprobe.LCBestFit(Printer()).find_best_fit
    # Exactly the original primary ascent window, force values and recorded Z.
    primary = fit([(r[1], r[2]) for r in rows if start <= r[0] <= end])[0]
    positions = {r[0]: r[2] for r in rows}
    positions[original['trigger_time']] = original['trigger_reconstructed_z']
    samples = [[r[0], r[1], r[4], r[5]] for r in rows]
    report = lcprobe.load_cell_diagnostics.build_report(
        samples, motion, positions.__getitem__, fit, primary)
    qualification = auto.load_cell_contact.qualify_contact(
        report, primary, tolerance)
    onset = auto.load_cell_contact_onset.estimate(report)
    onset['observational_offset'] = (record['cr_test_z'] - onset['z']
        if onset['z'] is not None else None)
    cr_z = record['cr_test_z']
    candidate = cr_z - primary
    span = qualification['contact_model_span_mm']
    return {'id': record['id'], 'sample_count': len(rows),
        'cr_test_z': cr_z, 'mcu_trigger_z': report['mcu_trigger_z'],
        'descent_fit_z': report['descent_fit_z'],
        'ascent_fit_z': report['ascent_fit_z'], 'primary_fit_z': primary,
        'descent_status': report['descent']['status'],
        'ascent_status': report['ascent']['status'],
        'recorded_offset_mm': record['recorded_offset_mm'],
        'original_algorithm_offset_mm': candidate,
        'replay_error_mm': candidate - record['recorded_offset_mm'],
        'qualified_offset_mm': candidate
            if qualification['eligible_for_candidate'] else None,
        'offset_model_span_mm': [cr_z - span[1], cr_z - span[0]]
            if span is not None else None,
        'qualification': qualification,
        'contact_onset': onset,
        'window_sensitivity': report['window_sensitivity']}


def source_hashes():
    hashes = {}
    for path in ('klippy/extras/load_cell_contact.py',
                 'klippy/extras/load_cell_contact_onset.py',
                 'klippy/extras/auto_z_offset_cr.py',
                 'klippy/extras/load_cell_probe.py',
                 'klippy/extras/load_cell_diagnostics.py'):
        with open(os.path.join(ROOT, path), 'rb') as source:
            hashes[path] = hashlib.sha256(source.read()).hexdigest()
    return hashes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', default=os.path.join(
        ROOT, 'test', 'fixtures', 'load_cell_contact_real.json'))
    parser.add_argument('--tolerance', type=float, default=.025)
    args = parser.parse_args()
    with open(args.fixture, 'rb') as source:
        fixture_bytes = source.read()
    fixture = json.loads(fixture_bytes)
    results = [replay(record, args.tolerance) for record in fixture['records']]
    print(json.dumps({'offline_only': True,
        'source_log_sha256': fixture['source_log_sha256'],
        'fixture_sha256': hashlib.sha256(fixture_bytes).hexdigest(),
        'implementation_sha256': source_hashes(),
        'limitations': [
            'Model spans are not confidence intervals or physical bounds.',
            'Branch agreement cannot identify a common physical Z bias.',
            'No force samples, positions or estimator coefficients adjusted.'],
        'results': results}, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
