# Auto Z-offset section loading and exclusive sensor ownership; no hardware
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import importlib
import sys
import unittest
from unittest import mock
import test_auto_z_offset_cr as cr
import test_auto_z_offset_ft as ft


class SectionLoadingTests(unittest.TestCase):
    def regression_fixture(self):
        fixture = cr.WorkflowTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def force_fixture(self):
        fixture = ft.NativeWorkflowTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_regression_section_loads_real_backend_without_saving(self):
        f = self.regression_fixture()
        f.printer.gcode.commands.clear()
        config = cr.Config(f.printer)
        module = cr.auto.load_config(config)
        self.assertEqual(config.get_name(), 'auto_z_offset_cr')
        self.assertIsInstance(module, cr.auto.ContactRegressionAutoZOffset)
        self.assertEqual(module.get_status(0.)['backend'], 'regression')
        self.assertIs(module.load_cell, f.printer.cell)
        self.assertIs(f.printer.objects['probe'], f.printer.primary)
        self.assertEqual(f.printer.trace, [])
        f.printer.objects['configfile'].set.assert_not_called()

    def test_force_section_loads_real_backend_without_old_estimators(self):
        f = self.force_fixture()
        f.p.gcode.commands.clear()
        config = ft.Config(f.p)
        module = ft.native.load_config(config)
        self.assertEqual(config.get_name(), 'auto_z_offset_ft')
        self.assertIsInstance(module, ft.native.ForceTriggerAutoZOffset)
        self.assertEqual(module.get_status(0.)['backend'], 'force_trigger')
        self.assertIs(module.cell, f.p.cell)
        self.assertFalse(module.get_status(0.)['eligible_for_application'])
        f.factories['TappingMove'].assert_not_called()
        f.factories['LCBestFit'].assert_not_called()
        self.assertEqual(f.p.trace, [])
        f.p.objects['configfile'].set.assert_not_called()

    def test_regression_refuses_other_backend_before_claiming_resources(self):
        f = self.regression_fixture()
        for section in ('auto_z_offset_ft', 'load_cell_probe', 'prtouch'):
            config = cr.Config(f.printer)
            previous = f.factory.call_count
            commands = dict(f.printer.gcode.commands)
            with mock.patch.object(config, 'has_section',
                                   side_effect=lambda s: s == section):
                with self.assertRaisesRegex(RuntimeError, 'not both'):
                    cr.auto.load_config(config)
            self.assertEqual(f.factory.call_count, previous)
            self.assertEqual(f.printer.gcode.commands, commands)
            self.assertEqual(f.printer.trace, [])

    def test_force_refuses_regression_before_claiming_resources(self):
        f = self.force_fixture()
        previous = f.factories['MCU_trigger_analog'].call_count
        commands = dict(f.p.gcode.commands)
        with self.assertRaisesRegex(RuntimeError, 'not both'):
            ft.native.load_config(
                ft.Config(f.p, sections=('auto_z_offset_cr',)))
        self.assertEqual(f.factories['MCU_trigger_analog'].call_count, previous)
        self.assertEqual(f.p.gcode.commands, commands)
        self.assertEqual(f.p.trace, [])


class DiagnosticOwnershipTests(unittest.TestCase):
    def test_guided_diagnostic_respects_both_renamed_busy_objects(self):
        with mock.patch.dict(sys.modules, {cr.PACKAGE: cr.package}):
            guided = importlib.import_module(cr.PACKAGE+'.load_cell_guided')
        for name, state in [('auto_z_offset_cr', {'state': 'measuring'}),
                            ('auto_z_offset_ft', {'busy': True})]:
            printer = cr.Printer()
            printer.command_error = RuntimeError
            printer.objects['pause_resume'] = mock.Mock()
            printer.objects['pause_resume'].get_status.return_value = {
                'is_paused': False}
            printer.sd.get_status.return_value = {'is_active': False}
            printer.toolhead.check_busy = mock.Mock(
                return_value=(0., 1., True))
            active = mock.Mock()
            active.get_status.return_value = state
            printer.objects[name] = active
            manager = guided.DiagnosticManager.__new__(guided.DiagnosticManager)
            manager.printer = printer
            manager.reactor = printer.reactor
            with self.assertRaisesRegex(RuntimeError, 'Auto Z-offset is busy'):
                manager.check_idle()
            active.get_status.return_value = {'state': 'idle', 'busy': False}
            self.assertEqual(manager.check_idle()[0], 0.)
            self.assertEqual(printer.trace, [])
            printer.objects['configfile'].set.assert_not_called()


if __name__ == '__main__':
    unittest.main()
