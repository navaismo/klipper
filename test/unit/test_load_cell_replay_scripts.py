# Standalone historical contact replay tools; no printer connection
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import json
import os
import subprocess
import sys
import tempfile
import unittest


ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), '..', '..'))


class ReplayScriptTests(unittest.TestCase):
    def test_contact_cli_reproduces_original_offsets_from_another_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-B', os.path.join(
                ROOT, 'scripts', 'replay_load_cell_contact.py')],
                cwd=directory, check=True, capture_output=True, text=True,
                timeout=60)
        report = json.loads(result.stdout)
        self.assertTrue(report['offline_only'])
        self.assertEqual(len(report['results']), 3)
        for record in report['results']:
            self.assertEqual(record['replay_error_mm'], 0.)
            self.assertFalse(record['qualification']['physical_contact_verified'])
            self.assertFalse(record['contact_onset']['eligible_for_application'])

    def test_comparison_cli_keeps_historical_estimators_report_only(self):
        with tempfile.TemporaryDirectory() as directory:
            output = os.path.join(directory, 'comparison.json')
            subprocess.run([sys.executable, '-B', os.path.join(
                ROOT, 'scripts', 'replay_prtouch_estimators.py'), os.path.join(
                ROOT, 'test', 'fixtures', 'load_cell_contact_real.json'),
                '--output', output], cwd=directory, check=True,
                capture_output=True, text=True, timeout=60)
            with open(output) as source:
                report = json.load(source)
        self.assertEqual(len(report['records']), 3)
        for record in report['records']:
            for key in ('lcbestfit', 'current', 'prtouch'):
                self.assertFalse(record[key]['eligible_for_application'])
            self.assertAlmostEqual(record['lcbestfit']['offset'],
                                   record['cr_test_z'] - record['lcbestfit']['z'])


if __name__ == '__main__':
    unittest.main()
