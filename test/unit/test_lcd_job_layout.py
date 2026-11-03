# Host print-card layout, bounded transfers and static lifecycle tests
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import random, struct, unittest
from unittest import mock
import test_tjc_lcd as startup
import test_tjc_job as jobs
import lcd_draw
import lcd_job_layout as layout


class LayoutTests(unittest.TestCase):
    def test_static_card_fields_targets_and_pause_hint(self):
        job = jobs.prepared()
        ops = layout.screen(job)
        batches = lcd_draw.prepare_batches(ops)
        texts = [op[10:].decode('gbk') for op in ops if op[0] == 3]
        for value in ('KLIPPER', '221', '0.18mm', '03:29:12', 'PETG',
                      '237 C', '62 C'):
            self.assertIn(value, texts)
        self.assertEqual(layout.PAUSE_HINT, 'Press Knob to Pause')
        printing = layout._small('PRINTING', 181, 18, layout.ORANGE)
        self.assertTrue(printing)
        self.assertTrue(all(op in ops for op in printing))
        self.assertNotIn('Ender 3 V3 SE', texts)
        self.assertEqual(len(batches), 1)
        for op in ops:
            lcd_draw.validate(op)

    def test_zero_unknown_and_long_values_stay_bounded(self):
        job = jobs.prepared()
        job.update(nozzle_target=0, bed_target=None,
                   estimated_time=100*3600+1, material='A'*100,
                   filename='part/'+'\u6750'*26+'.gcode')
        jobs.lcd_status.finalize_job(job)
        ops = layout.screen(job)
        lcd_draw.prepare_batches(ops)
        texts = [op[10:].decode('gbk') for op in ops if op[0] == 3]
        self.assertIn('0 C', texts)
        self.assertIn('--', texts)
        self.assertIn('100:00:01', texts)
        lines = layout.filename_lines(job['display']['filename'])
        self.assertEqual(''.join(lines), '\u6750'*26)
        self.assertTrue(all(len(line.encode('gbk')) <= 27 for line in lines))

    def test_thumbnail_is_bounded_and_black_pixels_are_skipped(self):
        self.assertEqual(layout._thumbnail(jobs.prepared(b'\x00\x00'*9216)), [])
        ops = layout._thumbnail(jobs.prepared(b'\xf8\x00'*9216))
        self.assertEqual(len(ops), 1)
        color, x0, y0, x1, y1 = struct.unpack('>HHHHH', ops[0][1:])
        self.assertEqual(color, 0xf800)
        self.assertEqual((x0, y0, x1, y1), (10, 54, 129, 173))

    def test_enlarged_thumbnail_preserves_colors_and_skipped_black(self):
        raw = b''.join(struct.pack('>H', 0 if (x+y) % 3 else (x*100+y+1))
                       for y in range(96) for x in range(96))
        ops = layout._thumbnail(jobs.prepared(raw))
        pixels = [0]*(120*120)
        for op in ops:
            color, x0, y0, x1, y1 = struct.unpack('>HHHHH', op[1:])
            for y in range(y0-54, y1-54+1):
                for x in range(x0-10, x1-10+1):
                    pixels[y*120+x] = color
        source = struct.unpack('>9216H', raw)
        expected = [source[((y+1)*96-1)//120*96+((x+1)*96-1)//120]
                    for y in range(120) for x in range(120)]
        self.assertEqual(pixels, expected)

    def test_complex_thumbnail_uses_multiple_valid_bounded_batches(self):
        rng = random.Random(42)
        raw = struct.pack('>9216H',
                          *[rng.randrange(65536) for i in range(9216)])
        batches = lcd_draw.prepare_batches(layout.screen(jobs.prepared(raw)))
        self.assertGreater(len(batches), 1)
        for transfer in batches:
            self.assertLessEqual(transfer['size'], lcd_draw.MAX_SIZE)
            for off, op in transfer['chunks']:
                lcd_draw.validate(op)


class HostCardTests(unittest.TestCase):
    ack = jobs.JobHostTests.ack

    def setUp(self):
        jobs.JobHostTests.setUp(self)
        self.commands = tuple(mock.Mock() for i in range(4))
        self.module.draw_commands = self.commands
        self.batches, self.offset, self.size = [], 0, 0
        def begin(args):
            self.size = args[2]
            self.offset = 0
            self.batches.append([])
            self.ack(0, 1)
        def op(args):
            self.batches[-1].append(args[3])
            self.offset = args[2]+len(args[3])
            self.ack(self.offset, 1)
        self.commands[0].send.side_effect = begin
        self.commands[1].send.side_effect = op
        self.commands[2].send.side_effect = lambda a: self.ack(self.offset, 2)

    def initialization(self, error=0):
        def start(args):
            self.module._handle_state({'state': 4 if error else 3,
                                        'error': error})
            self.reactor.register_async_callback.call_args[0][0](11.)
        self.mcu.start.send.side_effect = start

    def test_manual_render_recovers_after_failed_ack_without_idle_race(self):
        send_op = self.commands[1].send.side_effect
        self.commands[1].send.side_effect = None
        with self.assertLogs(level='WARNING'):
            with self.assertRaisesRegex(RuntimeError, 'token=.*offset='):
                self.module.render_job(jobs.prepared())
        self.commands[1].send.side_effect = send_op
        self.initialization()
        self.module.render_job(jobs.prepared(), recover=True)
        self.mcu.start.send.assert_called_once()
        self.assertEqual(self.module.screen, 'job')
        self.assertTrue(self.module.rendered)
        self.assertFalse(self.module.startup_idle_pending)
        self.reactor.register_callback.assert_not_called()
        self.gcode.run_script_from_command.assert_not_called()

    def test_manual_command_uses_recovery_path_and_does_not_start_print(self):
        self.module.state = 'error'
        self.module.error = 'No LCD job acknowledgement from MCU'
        self.initialization()
        status = object.__new__(jobs.lcd_status.LCDStatus)
        status.printer, status.gcode = self.printer, self.gcode
        self.objects['tjc_lcd'] = self.module
        job = jobs.prepared()
        status.prepare_job = mock.Mock(return_value=job)
        command = mock.Mock()
        command.get.return_value = job['filename']
        status.cmd_LCD_RENDER(command)
        self.mcu.start.send.assert_called_once()
        self.assertEqual(self.module.screen, 'job')
        self.sd.do_resume.assert_not_called()
        self.gcode.run_script_from_command.assert_not_called()

    def test_failed_manual_recovery_still_blocks_drawing(self):
        self.module.state = 'error'
        self.initialization(error=1)
        with self.assertLogs(level='WARNING'):
            with self.assertRaisesRegex(RuntimeError, 'not ready'):
                self.module.render_job(jobs.prepared(), recover=True)
        self.commands[0].send.assert_not_called()
        self.assertEqual(self.module.state, 'error')

    def test_manual_recovery_is_rejected_during_print_pause_or_preflight(self):
        self.module.state = 'error'
        for state in ('printing', 'paused'):
            self.stats.get_status.return_value = {'state': state}
            with self.assertRaisesRegex(RuntimeError, 'idle printer'):
                self.module.render_job(jobs.prepared(), recover=True)
        self.stats.get_status.return_value = {'state': 'standby'}
        self.sd.preflight_pending = True
        with self.assertRaisesRegex(RuntimeError, 'idle printer'):
            self.module.render_job(jobs.prepared(), recover=True)
        self.mcu.start.send.assert_not_called()

    def test_host_card_uses_generic_driver_then_stays_static(self):
        job = jobs.prepared(b'\xf8\x00'*9216)
        self.module.render_job(job)
        self.begin.send.assert_not_called()
        self.assertEqual(self.module.screen, 'job')
        self.assertTrue(self.module.rendered)
        self.assertEqual(self.module.job_filename, job['filename'])
        self.assertIsNone(self.module.render_abort_cmd)
        before = self.commands[1].send.call_count
        self.stats.get_status.return_value = {'state': 'printing'}
        self.module.get_status(100.)
        self.module._handle_idle_timeout(100.)
        self.module._handle_finished('paused')
        self.assertEqual(self.commands[1].send.call_count, before)

    def test_invalid_late_batch_sends_nothing(self):
        ops = [lcd_draw.rectangle(1, 1, 1, 1, 1)]*1500 + [b'\xff']
        with mock.patch.object(layout, 'screen', return_value=ops):
            with self.assertRaises(ValueError):
                self.module.render_job(jobs.prepared())
        self.commands[0].send.assert_not_called()

    def test_drawing_failure_uses_generic_abort_and_blocks_print(self):
        self.commands[1].send.side_effect = None
        with self.assertLogs(level='WARNING'):
            with self.assertRaisesRegex(RuntimeError, 'acknowledgement'):
                self.module.render_job(jobs.prepared())
        self.commands[3].send.assert_called_once()
        self.abort.send.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, 'not ready'):
            self.module._handle_before_print('next.gcode')

    def test_every_batch_finishes_before_card_is_ready(self):
        ops = [lcd_draw.rectangle(i % 2, i % 240, i // 240,
                                  i % 240, i // 240) for i in range(3200)]
        with mock.patch.object(layout, 'screen', return_value=ops):
            self.module.render_job(jobs.prepared())
        self.assertEqual(len(self.batches), 3)
        self.assertEqual(self.commands[2].send.call_count, 3)
        self.assertEqual(self.module.state, 'ready')
        self.assertEqual(self.module.screen, 'job')


if __name__ == '__main__':
    unittest.main()
