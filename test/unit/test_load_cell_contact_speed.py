# Synthetic bounded HX711 acquisition at the configured contact speed
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import math
import configparser
import unittest
from unittest import mock

import test_load_cell_onset_stop as support
import configfile


class ContactSpeedPreflightTests(unittest.TestCase):
    def run_move(self, sim, speed=2., floor=-.1):
        frame = sim.owner._diagnostic_motion['frames']['before_probe']
        sim.owner._diagnostic_motion['speed'] = speed
        unresolved = {'status': 'unresolved', 'z': None,
                      'reasons': ['synthetic_no_contact']}
        with mock.patch.object(support.diag, 'snapshot', return_value=frame):
            with mock.patch.object(support.movement.onset, 'estimate',
                                   return_value=unresolved):
                sim.stop.move([28., 28., floor], speed, sim.completion)

    def test_segment_bound_is_independent_of_speed_in_both_phases(self):
        slow = support.Simulation(frozen=True)
        fast = support.Simulation(frozen=True)
        self.run_move(slow, .5)
        self.run_move(fast, 2.)
        self.assertAlmostEqual(slow.stop.max_segment, .01875)
        self.assertEqual(fast.stop.max_segment, slow.stop.max_segment)
        self.assertAlmostEqual(fast.stop.command_segment, .01625)
        self.assertEqual(fast.moves, slow.moves)
        for start, end in fast.moves:
            self.assertLessEqual(start-end, .01875)
        self.assertLess(fast.moves[0][0]-fast.moves[0][1], .01875)

    def test_frozen_arriving_samples_stop_at_same_floor_without_candidate(self):
        sim = support.Simulation(frozen=True)
        self.run_move(sim)
        self.assertTrue(all(end >= -.1 for start, end in sim.moves))
        self.assertAlmostEqual(sim.moves[-1][1], -.1)
        with self.assertRaisesRegex(RuntimeError, 'bounded endpoint'):
            sim.stop.finish(0.)
        self.assertIsNone(sim.stop.stop_time)
        self.assertIsNone(sim.stop.stop_z)

    def test_missing_data_cannot_queue_another_segment(self):
        sim = support.Simulation(frozen=True)
        def unavailable(end, maximum):
            self.assertEqual(len(sim.moves), 1)
            raise RuntimeError('stream timeout')
        sim.collector.snapshot_until.side_effect = unavailable
        self.run_move(sim)
        self.assertEqual(len(sim.moves), 1)
        with self.assertRaisesRegex(RuntimeError, 'stream timeout'):
            sim.stop.finish(0.)

    def test_sensor_error_aborts_before_next_segment(self):
        sim = support.Simulation(frozen=True)
        sim.collector.snapshot_until.side_effect = None
        sim.collector.snapshot_until.return_value = ([], (1, 0))
        self.run_move(sim)
        self.assertEqual(len(sim.moves), 1)
        with self.assertRaisesRegex(RuntimeError, 'sensor reported errors'):
            sim.stop.finish(0.)

    def test_host_delay_never_increases_queued_travel(self):
        sim = support.Simulation(frozen=True)
        def delayed(end, maximum):
            before = list(sim.moves)
            position = list(sim.toolhead.position)
            sim.time += 10.
            self.assertEqual(before, sim.moves)
            self.assertEqual(position, sim.toolhead.position)
            raise RuntimeError('delayed host report')
        sim.collector.snapshot_until.side_effect = delayed
        self.run_move(sim)
        self.assertEqual(len(sim.moves), 1)
        with self.assertRaisesRegex(RuntimeError, 'delayed host report'):
            sim.stop.finish(0.)

    def test_mcu_stop_during_segment_preempts_host_estimation(self):
        sim = support.Simulation(frozen=True)
        def hard_stop(target, speed, completion):
            self.assertEqual(speed, 2.)
            sim.completion.test.return_value = True
        sim.toolhead.drip_move = hard_stop
        self.run_move(sim)
        sim.collector.snapshot_until.assert_not_called()
        sim.stop.error = RuntimeError('secondary host error')
        with self.assertRaisesRegex(RuntimeError, 'MCU hard-force stop'):
            sim.stop.finish(1000.01)

    def test_mcu_stop_during_checkpoint_queues_no_move(self):
        sim = support.Simulation(frozen=True)
        sim.stop.options['checkpoint'] = lambda: setattr(
            sim.completion.test, 'return_value', True)
        self.run_move(sim)
        self.assertEqual(sim.moves, [])

    def test_changed_tare_aborts_before_any_move(self):
        sim = support.Simulation(frozen=True)
        sim.printer.cell.tare_counts += 1
        self.run_move(sim)
        self.assertEqual(sim.moves, [])
        with self.assertRaisesRegex(RuntimeError, 'Changed onset tare'):
            sim.stop.finish(0.)

    def test_end_before_onset_never_becomes_a_normal_stop(self):
        sim = support.Simulation(frozen=True)
        self.run_move(sim, floor=.25)
        self.assertAlmostEqual(sim.moves[-1][1], .25)
        self.assertTrue(all(end >= .25 for start, end in sim.moves))
        with self.assertRaisesRegex(RuntimeError, 'bounded endpoint'):
            sim.stop.finish(0.)

    def test_monitor_period_remains_80_hz_at_both_motion_speeds(self):
        trigger = support.lcprobe.trigger_analog.MCU_trigger_analog.__new__(
            support.lcprobe.trigger_analog.MCU_trigger_analog)
        trigger._sensor = mock.Mock()
        trigger._sensor.get_samples_per_second.return_value = 80
        trigger._mcu = mock.Mock()
        trigger._mcu.print_time_to_clock.side_effect = lambda t: int(t*72000000)
        trigger._mcu.seconds_to_clock.side_effect = lambda t: int(t*72000000)
        trigger._dispatch = mock.Mock()
        trigger._dispatch.get_oid.return_value = 8
        trigger._reset_filter = mock.Mock()
        trigger._home_cmd = mock.Mock()
        trigger._oid = 9
        trsync = support.lcprobe.trigger_analog.mcu.MCU_trsync
        with mock.patch.object(trsync, 'REASON_ENDSTOP_HIT', 1, create=True):
            for speed in (.5, 2.):
                # A real step is 0.0025 mm; this only changes the native
                # motion-derived rest_time, which analog monitoring ignores.
                trigger.home_start(100., .000015, 4, .0025/speed)
        first, second = trigger._home_cmd.send.call_args_list
        self.assertEqual(first.args, second.args)
        self.assertEqual(first.args[0][-2:], [900000, 3])

    def test_high_initial_approach_cannot_bypass_segment_bound(self):
        sim = support.Simulation(frozen=True)
        sim.toolhead.position[2] = 5.
        self.run_move(sim, floor=.25)
        self.assertGreater(len(sim.moves), 250)
        self.assertAlmostEqual(sim.moves[-1][1], .25)
        for start, end in sim.moves:
            self.assertLessEqual(start-end, .01875)
            self.assertGreaterEqual(end, .25)

    def test_native_step_quantization_cannot_exceed_spatial_cap(self):
        sim = support.Simulation(frozen=True)
        self.run_move(sim)
        quantum = .0025
        for phase in range(101):
            origin = phase * quantum / 100.
            for start, end in sim.moves:
                a = math.floor((start + origin)/quantum + .5)
                b = math.floor((end + origin)/quantum + .5)
                self.assertLessEqual((a-b)*quantum, .01875)

    def test_sub_period_segment_waits_for_original_stationary_samples(self):
        sim = support.Simulation(frozen=True, delay=0.)
        original = sim.collector.snapshot_until.side_effect
        observed = []
        def snapshot(end, maximum):
            z = sim.toolhead.position[2]
            moves = list(sim.moves)
            samples, errors = original(end, maximum)
            self.assertEqual(moves, sim.moves)
            self.assertEqual(z, sim.toolhead.position[2])
            latest = [s for s in samples if s[0] <= end][-3:]
            self.assertEqual(len(latest), 3)
            observed.append((end, z, [s[0] for s in latest]))
            return samples, errors
        sim.collector.snapshot_until.side_effect = snapshot
        self.run_move(sim, floor=.25)
        self.assertGreater(len(observed), 1)
        self.assertLess(sim.stop.command_segment/2., 1./80.)
        self.assertGreater(observed[0][2][0], sim.collector.min_time)

    def test_invalid_step_geometry_aborts_before_first_move(self):
        for steps in ([], [0.], [float('nan')], [.02]):
            sim = support.Simulation(frozen=True)
            sim.owner._mcu_trigger_analog.get_steppers = lambda: [
                mock.Mock(get_step_dist=mock.Mock(return_value=q))
                for q in steps]
            self.run_move(sim)
            self.assertEqual(sim.moves, [])
            with self.assertRaisesRegex(RuntimeError, 'geometry|bounds'):
                sim.stop.finish(0.)

    def test_speed_passed_to_native_drip_is_two_mm_s(self):
        sim = support.Simulation(frozen=True)
        original = sim.toolhead.drip_move
        speeds = []
        def drip(target, speed, completion):
            speeds.append(speed)
            return original(target, speed, completion)
        sim.toolhead.drip_move = drip
        self.run_move(sim)
        self.assertTrue(speeds)
        self.assertEqual(set(speeds), {2.})

    def test_completion_during_sample_wait_prevents_next_segment(self):
        sim = support.Simulation(frozen=True)
        original = sim.collector.snapshot_until.side_effect
        def stopped(end, maximum):
            samples = original(end, maximum)
            sim.completion.test.return_value = True
            return samples
        sim.collector.snapshot_until.side_effect = stopped
        self.run_move(sim)
        self.assertEqual(len(sim.moves), 1)
        with self.assertRaisesRegex(RuntimeError, 'MCU hard-force stop'):
            sim.stop.finish(1000.01)

    def test_native_parameter_and_probing_move_forward_configured_speed(self):
        sim = support.Simulation(frozen=True)
        parser = configparser.RawConfigParser()
        parser.read_string('[auto_z_offset]\nspeed: 2.0\nlift_speed: 0.1\n'
                           'sample_retract_dist: 0.5\n'
                           '[bltouch]\nspeed: 5\nlift_speed: 10\n')
        cfg = configfile.ConfigWrapper(sim.printer, parser, {}, 'auto_z_offset')
        helper = support.lcprobe.LoadCellParameterHelper(cfg)
        command = support.engine.Gcmd()
        self.assertEqual(helper.get_probe_params(command)['probe_speed'], 2.)
        self.assertEqual(helper.get_probe_params(command)['lift_speed'], .1)
        probe_cfg = configfile.ConfigWrapper(sim.printer, parser, {}, 'bltouch')
        self.assertEqual(support.lcprobe.probe.ProbeParameterHelper(
            probe_cfg).get_probe_params(command)['probe_speed'], 5.)
        move = support.lcprobe.LoadCellProbingMove.__new__(
            support.lcprobe.LoadCellProbingMove)
        move._printer = sim.printer
        move._load_cell = sim.printer.cell
        move._z_min_position = -3.
        move._pause_and_tare = mock.Mock()
        move._start_collector = mock.Mock(return_value=sim.collector)
        move._param_helper = helper
        move._log_trace = False
        move._mcu_trigger_analog = sim.owner._mcu_trigger_analog
        homing = mock.Mock()
        homing.probing_move.side_effect = RuntimeError('offline stop')
        sim.printer.objects['homing'] = homing
        with self.assertRaisesRegex(RuntimeError, 'offline stop'):
            move.probing_move(command, minimum_z=-.1, maximum_descent=.5)
        self.assertEqual(homing.probing_move.call_args.args[2], 2.)
        self.assertAlmostEqual(homing.probing_move.call_args.args[1][2], -.1)
        sim.collector.stop_collecting.assert_called_once()


if __name__ == '__main__':
    unittest.main()
