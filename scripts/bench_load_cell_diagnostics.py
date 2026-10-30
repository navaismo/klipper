#!/usr/bin/env python3
# Offline passive-report benchmark; imports only the unit-test printer mocks
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import argparse
import gc
import hashlib
import json
import os
import platform
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'test', 'unit'))
from test_load_cell_diagnostics import curve, diag, lcprobe, Printer


def measure(callback, warmup, iterations):
    for _ in range(warmup):
        callback()
    durations = []
    for _ in range(iterations):
        start = time.perf_counter()
        callback()
        durations.append((time.perf_counter() - start) * 1000.)
    ordered = sorted(durations)
    return {'minimum_ms': ordered[0],
        'median_ms': statistics.median(ordered),
        'p95_ms': ordered[int((len(ordered) - 1) * .95)],
        'maximum_ms': ordered[-1], 'mean_ms': statistics.mean(ordered)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iterations', type=int, default=200)
    parser.add_argument('--warmup', type=int, default=10)
    args = parser.parse_args()
    if args.iterations < 1 or args.warmup < 0:
        parser.error('iterations must be positive; warmup must be nonnegative')
    fit = lcprobe.LCBestFit(Printer()).find_best_fit
    result = {'python': platform.python_version(),
        'architecture': platform.machine(), 'system': platform.system(),
        'iterations': args.iterations, 'warmup': args.warmup,
        'gc_enabled': gc.isenabled(), 'synthetic': True,
        'limitations': ['synthetic position lookup, not MCU step history',
            'no disk logging, sensor acquisition, UART or printer connection',
            'host results are not Raspberry Pi performance measurements'],
        'source_sha256': {}, 'reports': []}
    try:
        with open('/proc/cpuinfo') as source:
            result['cpu'] = next(line.split(':', 1)[1].strip()
                for line in source if line.startswith(('model name', 'Model')))
    except (IOError, StopIteration):
        result['cpu'] = platform.processor()
    for path in ('klippy/extras/load_cell_diagnostics.py',
                 'klippy/extras/load_cell_probe.py',
                 'test/unit/test_load_cell_diagnostics.py',
                 'scripts/bench_load_cell_diagnostics.py'):
        with open(os.path.join(ROOT, path), 'rb') as source:
            result['source_sha256'][path] = hashlib.sha256(
                source.read()).hexdigest()
    for count in (500, 2048):
        samples, motion, position = curve(count=count, release=.04, noise=.1)

        def build():
            return diag.build_report(samples, motion, position, fit,
                                     regression_z=.04)

        def encode():
            return json.dumps(diag._json_safe(build()), separators=(',', ':'),
                              allow_nan=False)

        report = build()
        if not report['estimates_comparable']:
            raise RuntimeError('Benchmark did not exercise both valid fits')
        result['reports'].append({'sample_count': count,
            'logged_count': report['logged_count'],
            'json_bytes': len(encode().encode('utf-8')),
            'build_report': measure(build, args.warmup, args.iterations),
            'build_and_json': measure(encode, args.warmup, args.iterations)})
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
