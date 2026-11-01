#!/usr/bin/env python3
# Run the fork's offline Python and native-C unit tests.
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import argparse
import importlib.util
import logging
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log', help='Write diagnostic logs to this file')
    parser.add_argument('--require-native', action='store_true',
                        help='Fail if gcc is unavailable for native tests')
    parser.add_argument('-v', '--verbose', action='store_true')
    args = parser.parse_args()
    if importlib.util.find_spec('PIL') is None:
        parser.error('Install scripts/lcd-requirements.txt before testing')
    if args.require_native and shutil.which('gcc') is None:
        parser.error('gcc is required for native LCD tests')
    log_path = args.log
    if log_path is None:
        log_path = os.path.join(tempfile.mkdtemp(prefix='klipper-fork-tests-'),
                                'diagnostics.log')
    # Retain expected observer failures without flooding the console.
    handler = logging.FileHandler(log_path, mode='w')
    handler.setFormatter(logging.Formatter('%(levelname)s:%(message)s'))
    logger = logging.getLogger()
    logger.addHandler(handler)
    try:
        suite = unittest.defaultTestLoader.discover(
            os.path.join(ROOT, 'test', 'unit'))
        result = unittest.TextTestRunner(
            verbosity=2 if args.verbose else 1).run(suite)
    finally:
        logger.removeHandler(handler)
        handler.close()
    print('Diagnostic log: %s' % os.path.abspath(log_path))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
