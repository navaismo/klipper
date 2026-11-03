# Host-designed menu, printer-action, and native generic drawing tests
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import os, random, shutil, struct, subprocess, tempfile, unittest, zlib
from unittest import mock
import test_tjc_lcd as startup
import lcd_draw
import tjc_menu
import tjc_menu_layout as layout
import lcd_canvas
import buttons
import gcode_move


class DrawingTests(unittest.TestCase):
    @staticmethod
    def rectangle_pixels(ops):
        pixels = {}
        for op in ops:
            color, x0, y0, x1, y1 = struct.unpack('>HHHHH', op[1:])
            for y in range(y0, y1+1):
                for x in range(x0, x1+1):
                    pixels[x, y] = color
        return pixels

    def test_compaction_preserves_overpaint_and_untouched_pixels(self):
        rng = random.Random(20261102)
        for unused in range(40):
            ops = []
            for i in range(35):
                x0, y0 = rng.randrange(25), rng.randrange(35)
                ops.append(lcd_draw.rectangle(rng.randrange(4), x0, y0,
                    x0+rng.randrange(8), y0+rng.randrange(8)))
            transfer = lcd_draw.prepare(ops)
            compact = [op for offset, op in transfer['chunks']]
            self.assertEqual(self.rectangle_pixels(ops),
                             self.rectangle_pixels(compact))
            self.assertLessEqual(len(compact), len(ops))

    def test_rectangles_merge_without_crossing_text_or_clear(self):
        solid = [lcd_draw.rectangle(1, 20, y, 29, y) for y in range(20)]
        text = lcd_draw.text('Test', 20, 20)
        clear = lcd_draw.clear(0)
        source = solid + [text] + solid + [clear] + solid
        ops = [op for off, op in lcd_draw.prepare(source)['chunks']]
        merged = lcd_draw.rectangle(1, 20, 0, 29, 19)
        self.assertEqual(ops, [merged, text, merged, clear, merged])

    def test_branding_colors_and_page_transitions_keep_header(self):
        for ops in (layout.screen('main', 0), layout.screen('move', 0),
                    layout.filament_screen('load', 170, 'Heating...'),
                    layout.filament_screen('unload', 170, 'Heating...')):
            for text, color in ((b'K', layout.RED), (b'lipper', layout.WHITE)):
                wordmark = [op for op in ops if op[0] == 3 and op[10:] == text]
                self.assertEqual(len(wordmark), 1)
                self.assertEqual(struct.unpack('>H', wordmark[0][2:4])[0],
                                 color)
            logo = [op for op in ops if op[0] == 2
                    and struct.unpack('>H', op[5:7])[0] < 50
                    and struct.unpack('>H', op[3:5])[0] > 40
                    and struct.unpack('>H', op[7:9])[0] < 200]
            self.assertTrue(logo)
            colors = set(struct.unpack('>H', op[1:3])[0] for op in logo)
            self.assertEqual(colors,
                             set((layout.CYAN, layout.ORANGE)))
        for page in ('main', 'move'):
            body = layout.screen(page, 0, header=False)
            self.assertNotIn(1, [op[0] for op in body])
            self.assertFalse(any(op[10:] in (b'K', b'lipper') for op in body
                                 if op[0] == 3))

    def test_full_pages_stay_within_reduced_transfer_budgets(self):
        for ops, limit in ((layout.screen('main', 0), 145),
                           (layout.screen('move', 0), 170),
                           (layout.filament_screen('load', 170, 'Heating...'),
                            350),
                           (layout.filament_screen('unload', 170, 'Heating...'),
                            350)):
            self.assertLessEqual(len(lcd_draw.prepare(ops)['chunks']), limit)

    def test_encoder_phases_match_marlin_clockwise_and_reject_bounce(self):
        cw, ccw = mock.Mock(), mock.Mock()
        encoder = buttons.FullStepRotaryEncoder(cw, ccw)
        # Marlin CW pressed phases 0,2,3,1,0 become physical levels 3,1,0,2,3.
        for state in (3, 1, 3, 1, 0, 1, 0, 2, 3):
            encoder.encoder_callback(10., state)
        cw.assert_called_once()
        ccw.assert_not_called()
        for state in (3, 2, 0, 1, 3):
            encoder.encoder_callback(11., state)
        ccw.assert_called_once()
        for state in (0, 3, 0, 3):
            encoder.encoder_callback(12., state)
        self.assertEqual(cw.call_count, 1)
        self.assertEqual(ccw.call_count, 1)

    def test_all_layouts_validate_and_partial_redraw_is_small(self):
        for page, labels in (('main', layout.MAIN), ('move', layout.MOVES)):
            for selected in range(len(labels)):
                full = lcd_draw.prepare(layout.screen(page, selected))
                partial = lcd_draw.prepare(layout.selection(page, selected, 0))
                self.assertLess(partial['size'], full['size']/10)
        lcd_draw.prepare(layout.message('Error', 'Long GBK ' + '\u4e2d'*100))

    def test_filament_design_temperature_units_and_partial_updates(self):
        for action in ('load', 'unload'):
            for value in (None, 0, 170, 240, 500):
                ops = layout.filament_screen(action, value, 'Heating...')
                transfer = lcd_draw.prepare(ops)
                self.assertLess(transfer['size'], lcd_draw.MAX_SIZE)
                texts = [op[10:] for op in ops if op[0] == 3]
                self.assertIn(b'Press to cancel', texts)
                self.assertIn(('Load Filament' if action == 'load' else
                               'Unload Filament').encode(), texts)
                self.assertFalse(any(b'\xa1\xe3' in text for text in texts))
                partial = layout.filament_temperature(value)
                lcd_draw.prepare(partial)
                self.assertNotIn(1, [op[0] for op in partial])
                self.assertLess(len(partial), len(ops)/20)
        for value in (None, float('nan'), float('inf'), -1., 501.):
            self.assertIsNone(layout.temperature_value(value))
        self.assertEqual(layout.temperature_value(170.5), 171)

    def test_rejects_controls_truncated_gbk_unknown_fonts_and_bounds(self):
        bad = [lcd_draw.rectangle(1, 5, 0, 4, 0),
               lcd_draw.rectangle(1, 0, 0, 240, 0),
               lcd_draw.text('abc', 220, 0),
               lcd_draw.text('a', 0, 310),
               lcd_draw.text('a', 0, 0, font=0),
               lcd_draw.text('\n', 0, 0),
               lcd_draw.text('a', 0, 0)[:-1]+b'\x81', b'\x01',
               b'\xff', b'\x03'*41]
        for operation in bad:
            with self.subTest(op=operation):
                with self.assertRaises(ValueError):
                    lcd_draw.prepare([operation])
        with self.assertRaises(ValueError):
            lcd_draw.prepare([])
        with self.assertRaises(ValueError):
            lcd_draw.prepare([lcd_draw.clear(0)]*6000)


class MenuTests(unittest.TestCase):
    def setUp(self):
        startup.HostTests.setUp(self)
        self.lcd = self.module
        self.lcd.connected, self.lcd.state = True, 'ready'
        self.lcd.screen = 'idle'
        self.lcd.job_begin_cmd = mock.Mock()
        self.lcd.draw_commands = [mock.Mock() for i in range(4)]
        self.lcd.render_drawing = mock.Mock(side_effect=self.render)
        self.lcd.render_idle = mock.Mock(side_effect=self.idle)
        self.callbacks, self.operations, self.log = [], [], []
        self.callback_times = {}
        def register(callback, waketime=0.):
            self.callbacks.append(callback)
            self.callback_times[callback] = waketime
        self.reactor.register_callback.side_effect = register
        self.now = 10.
        self.reactor.monotonic.side_effect = lambda: self.now
        self.reactor.pause.side_effect = self.pause
        self.buttons = mock.Mock()
        self.pause_resume = mock.Mock()
        self.pause_resume.get_status.return_value = {'is_paused': False}
        self.printer.load_object.side_effect = lambda config, name: {
            'buttons': self.buttons, 'tjc_lcd': self.lcd,
            'pause_resume': self.pause_resume}[name]
        self.config.getlist.return_value = ['^PA11', '^PA12']
        self.config.getchoice.side_effect = (
            lambda name, choices, default: default)
        self.config.get.side_effect = lambda name, default=None: (
            '^!PB1' if name == 'click_pin' else default)
        self.module = tjc_menu.TJCMenu(self.config)
        self.objects['tjc_menu'] = self.module
        self.position = [100., 100., 100., 0.]
        self.toolhead = self.objects['toolhead']
        self.toolhead.get_position.side_effect = lambda: list(self.position)
        self.toolhead.get_status.return_value = {
            'homed_axes': 'xyz', 'axis_minimum': [0., 0., 0.],
            'axis_maximum': [220., 220., 250.]}
        self.toolhead.manual_move.side_effect = self.move
        self.toolhead.wait_moves.side_effect = lambda: self.log.append('drain')
        self.heater, self.bed = mock.Mock(), mock.Mock()
        self.heater.check_busy.return_value = False
        self.heater.get_temp.return_value = (25., 240.)
        self.toolhead.get_extruder.return_value.get_heater.return_value = (
            self.heater)
        self.heaters = mock.Mock()
        self.heaters.lookup_heater.return_value = self.bed
        self.heaters.set_temperature.side_effect = self.heat
        self.objects['heaters'] = self.heaters
        self.gcode.run_script_from_command.side_effect = (
            lambda value: self.log.append(value))

    def pause(self, when):
        self.now = when

    def render(self, ops):
        lcd_draw.prepare(ops)
        self.operations.append(ops)
        self.lcd.screen = 'menu'

    def idle(self):
        self.module._invalidate()
        self.lcd.screen = 'idle'

    def move(self, coord, speed):
        before = list(self.position)
        for index, value in enumerate(coord):
            if value is not None:
                self.position[index] = value
        self.log.append(('move', [a-b for a, b in zip(self.position, before)],
                         speed))

    def heat(self, heater, temperature):
        self.log.append(('heat', heater, temperature))

    def flush(self):
        while self.callbacks:
            callback = self.callbacks.pop(0)
            self.now = max(self.now, self.callback_times.pop(callback))
            callback(self.now)

    def open(self):
        if not self.module.is_active():
            self.module._cw(self.now)
            self.flush()

    def click(self):
        self.module._button(self.now, True)
        self.now += .03
        self.module._debounced(self.now)
        self.module._button(self.now, False)
        self.now += .03
        self.module._debounced(self.now)
        self.flush()

    def choose(self, index, page='main'):
        self.open()
        self.module.page, self.module.selected = page, index
        self.module.drawn = None
        self.module._draw()
        self.click()

    def test_pin_contract_and_old_firmware_rejected(self):
        self.buttons.register_rotary_encoder.assert_called_once_with(
            '^PA11', '^PA12', self.module._cw, self.module._ccw, 4)
        self.module._connect()
        self.lcd.draw_commands = None
        with self.assertRaisesRegex(RuntimeError,
                'TJC_DRAW_VERSION=missing; firmware: 3ae38934-dirty'):
            self.module._connect()

    def test_idle_click_does_not_open_menu_or_execute_action(self):
        self.click()
        self.assertFalse(self.module.is_active())
        self.assertFalse(self.callbacks)
        self.assertFalse(self.operations)
        self.gcode.run_script_from_command.assert_not_called()
        self.toolhead.manual_move.assert_not_called()
        self.heaters.set_temperature.assert_not_called()

    def test_either_rotation_opens_then_right_down_and_left_up(self):
        for rotate in (self.module._cw, self.module._ccw):
            self.module._invalidate()
            self.lcd.screen = 'idle'
            rotate(self.now)
            self.flush()
            self.assertEqual(self.module.drawn, ('main', 0))
            self.module._cw(self.now)
            self.flush()
            self.assertEqual(self.module.drawn, ('main', 1))
            self.assertEqual(self.module.get_status(self.now)['selected'],
                             'Load Filament')
            self.module._ccw(self.now)
            self.flush()
            self.assertEqual(self.module.drawn, ('main', 0))
        self.gcode.run_script_from_command.assert_not_called()
        self.toolhead.manual_move.assert_not_called()
        self.heaters.set_temperature.assert_not_called()

    def test_rotation_coalesces_and_partially_redraws(self):
        self.open()
        self.module._cw(self.now)
        self.module._cw(self.now)
        self.module._cw(self.now)
        self.assertEqual(len(self.callbacks), 1)
        self.flush()
        self.assertEqual(self.module.selected, 3)
        self.toolhead.manual_move.assert_not_called()
        self.gcode.run_script_from_command.assert_not_called()
        self.module._ccw(self.now)
        self.flush()
        self.assertEqual(self.module.selected, 2)
        self.assertEqual(len(self.operations[-1]), 8)

    def test_fast_rotation_keeps_one_latest_selection_and_draw_cooldown(self):
        self.open()
        end = self.now
        for i in range(1001):
            self.module._cw(end+.01)
        self.assertEqual(len(self.callbacks), 1)
        self.assertEqual(self.module.selected, 1)
        self.assertEqual(self.module.drawn, ('main', 0))
        callback = self.callbacks[0]
        self.assertAlmostEqual(self.callback_times[callback], end+.2)
        self.flush()
        self.assertEqual(self.module.drawn, ('main', 1))
        self.assertEqual(len(self.operations), 2)
        self.assertFalse(self.module.pending)
        self.assertFalse(self.callbacks)
        self.gcode.run_script_from_command.assert_not_called()

    def test_sustained_rotation_never_exceeds_five_draws_per_second(self):
        self.open()
        start = self.now
        rendered = []
        def render(ops):
            self.render(ops)
            rendered.append(self.now)
        self.lcd.render_drawing.side_effect = render
        for i in range(1, 143):
            self.now = start+i*.007
            self.module._cw(self.now)
            if self.callbacks:
                callback = self.callbacks[0]
                if self.callback_times[callback] <= self.now:
                    self.flush()
            self.assertLessEqual(len(self.callbacks), 1)
        self.assertGreaterEqual(len(rendered), 4)
        self.assertLessEqual(len(rendered), 5)
        for a, b in zip([start]+rendered, rendered):
            self.assertGreaterEqual(b-a, .2-1.e-9)

    def test_mutex_wait_claims_busy_before_input_can_queue_more_work(self):
        self.open()
        self.module._cw(self.now)
        def enter():
            self.assertTrue(self.module.busy)
            selected = self.module.selected
            self.module._cw(self.now)
            self.module._activate(self.now)
            self.assertEqual(self.module.selected, selected)
            self.assertFalse(self.callbacks)
        self.gcode.get_mutex.return_value.__enter__.side_effect = enter
        self.flush()
        self.assertFalse(self.module.busy)
        self.gcode.run_script_from_command.assert_not_called()

    def test_invalidated_deferred_redraw_cannot_release_new_request(self):
        self.open()
        self.module._cw(self.now)
        stale = self.callbacks.pop(0)
        self.module._invalidate()
        self.lcd.screen = 'idle'
        self.module._cw(self.now)
        request = self.module.redraw_request
        stale(self.now)
        self.assertIs(self.module.redraw_request, request)
        self.assertTrue(self.module.pending)
        self.flush()
        self.assertEqual(self.module.drawn, ('main', 0))

    def test_navigation_cooldown_does_not_delay_action_or_print_pause(self):
        self.open()
        self.module.next_redraw = self.now+30.
        self.click()
        self.gcode.run_script_from_command.assert_called_once_with('G28')
        self.printing()
        self.module.next_redraw = self.now+30.
        self.gcode.run_script_from_command.reset_mock()
        before = self.now
        self.click()
        self.assertLess(self.now-before, .1)
        self.gcode.run_script_from_command.assert_called_once_with('PAUSE')

    def test_only_visible_idle_can_open(self):
        for screen in ('job', 'blank', None):
            self.lcd.screen = screen
            self.module._cw(self.now)
            self.module._ccw(self.now)
            self.assertFalse(self.module.is_active())

    def test_calibration_closes_menu_and_excludes_knob_actions(self):
        self.open()
        self.printer.send_event.side_effect = lambda event: (
            self.module._invalidate() if event == 'tjc_lcd:invalidate'
            else None)
        self.lcd.canvas_begin_cmd = mock.Mock()
        self.lcd.render_canvas = mock.Mock()
        self.assertTrue(self.lcd.begin_calibration('Warming up', 170, 50))
        self.assertFalse(self.module.is_active())
        self.assertFalse(self.lcd.can_interact())
        self.lcd.screen = 'idle'
        self.module._cw(self.now)
        self.click()
        self.assertFalse(self.module.is_active())
        self.assertFalse(self.callbacks)
        self.gcode.run_script_from_command.assert_not_called()
        self.heaters.set_temperature.assert_not_called()
        self.toolhead.manual_move.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, 'reserved'):
            self.lcd._handle_before_print('new.gcode')

    def test_print_pause_preflight_and_shutdown_reject_all_input(self):
        self.open()
        for state in ('printing', 'paused'):
            self.stats.get_status.return_value = {'state': state}
            self.module._cw(self.now)
            self.module._activate(self.now)
        self.stats.get_status.return_value = {'state': 'standby'}
        self.sd.preflight_pending = True
        self.module._cw(self.now)
        self.module._activate(self.now)
        self.sd.preflight_pending = False
        pause = self.objects['pause_resume'] = mock.Mock()
        pause.get_status.return_value = {'is_paused': True}
        self.module._activate(self.now)
        self.printer.is_shutdown.return_value = True
        self.module._activate(self.now)
        self.assertFalse(self.callbacks)
        self.gcode.run_script_from_command.assert_not_called()

    def printing(self):
        self.module._invalidate()
        self.lcd.screen, self.lcd.rendered = 'job', True
        self.lcd.job_filename = 'part.gcode'
        self.sd.is_active.return_value = True
        self.stats.get_status.return_value = {'state': 'printing'}
        self.pause_resume.get_status.return_value = {'is_paused': False}

    def test_print_press_runs_configured_pause_once_without_lcd_traffic(self):
        self.printing()
        self.module._button(self.now, True)
        self.now += .03
        self.module._debounced(self.now)
        self.gcode.run_script_from_command.assert_not_called()
        self.flush()
        self.gcode.run_script_from_command.assert_called_once_with('PAUSE')
        self.module._button(self.now, True)
        self.module._debounced(self.now)
        self.now += 3.
        self.module._hold(self.now)
        self.flush()
        self.gcode.run_script_from_command.assert_called_once_with('PAUSE')
        self.lcd.render_drawing.assert_not_called()
        self.lcd.render_idle.assert_not_called()
        self.toolhead.manual_move.assert_not_called()
        self.heaters.set_temperature.assert_not_called()

    def test_printing_rotation_and_paused_click_never_resume(self):
        self.printing()
        self.module._cw(self.now)
        self.module._ccw(self.now)
        self.stats.get_status.return_value = {'state': 'paused'}
        self.pause_resume.get_status.return_value = {'is_paused': True}
        self.click()
        self.gcode.run_script_from_command.assert_not_called()
        self.assertFalse(self.module.is_active())
        self.lcd.render_drawing.assert_not_called()

    def test_preflight_or_idle_press_cannot_pause_a_subsequent_print(self):
        for preflight in (False, True):
            self.module._invalidate()
            self.stats.get_status.return_value = {'state': 'standby'}
            self.lcd.screen = 'idle'
            self.sd.preflight_pending = preflight
            self.module._button(self.now, True)
            self.sd.preflight_pending = False
            self.stats.get_status.return_value = {'state': 'printing'}
            self.lcd.screen, self.lcd.rendered = 'job', True
            self.now += .03
            self.module._debounced(self.now)
            self.flush()
        self.gcode.run_script_from_command.assert_not_called()

    def test_queued_print_pause_is_invalidated_by_stop_pause_or_new_job(self):
        for state in ('complete', 'cancelled', 'paused', 'new_job'):
            self.printing()
            self.module._button(self.now, True)
            self.now += .03
            self.module._debounced(self.now)
            if state == 'new_job':
                self.module._invalidate()
                self.lcd.job_filename = 'other.gcode'
            else:
                self.stats.get_status.return_value = {'state': state}
            self.flush()
        self.gcode.run_script_from_command.assert_not_called()

    def test_print_switch_bounce_and_pause_error_do_not_draw(self):
        self.printing()
        self.module._button(self.now, True)
        self.module._button(self.now, False)
        self.module._button(self.now, True)
        self.now += .03
        self.module._debounced(self.now)
        self.gcode.run_script_from_command.side_effect = RuntimeError('PAUSE')
        with self.assertLogs(level='WARNING'):
            self.flush()
        self.gcode.run_script_from_command.assert_called_once_with('PAUSE')
        self.lcd.render_drawing.assert_not_called()
        self.lcd.render_idle.assert_not_called()

    def test_print_click_integrates_with_real_pause_resume_helper(self):
        import pause_resume
        self.printing()
        self.objects['webhooks'] = mock.Mock()
        helper = pause_resume.PauseResume(self.config)
        helper.handle_connect()
        self.module.pause_resume = helper
        def run(script):
            self.log.append(script)
            if script == 'PAUSE':
                helper.cmd_PAUSE(mock.Mock())
        self.gcode.run_script_from_command.side_effect = run
        self.click()
        self.assertTrue(helper.is_paused)
        self.sd.do_pause.assert_called_once()
        self.assertEqual(self.log, ['PAUSE',
                                   'SAVE_GCODE_STATE NAME=PAUSE_STATE'])
        self.click()
        self.assertEqual(self.log.count('PAUSE'), 1)
        self.lcd.render_drawing.assert_not_called()

    def test_queued_callback_rechecks_state_and_invalidation(self):
        self.module._cw(self.now)
        self.sd.preflight_pending = True
        self.flush()
        self.assertFalse(self.operations)
        self.sd.preflight_pending = False
        self.module._queue_redraw()
        self.module._invalidate()
        self.flush()
        self.assertFalse(self.operations)

    def test_back_and_long_press_idle_do_not_execute_an_action(self):
        self.choose(4)
        self.assertEqual(self.module.page, 'move')
        self.module.selected = 6
        self.click()
        self.assertEqual((self.module.page, self.module.selected), ('main', 4))
        self.module._button(self.now, True)
        self.now += .03
        self.module._debounced(self.now)
        self.now += .8
        self.module._hold(self.now)
        self.flush()
        self.module._button(self.now, False)
        self.now += .03
        self.module._debounced(self.now)
        self.assertFalse(self.module.is_active())
        self.gcode.run_script_from_command.assert_not_called()

    def test_button_bounce_and_busy_events_do_not_repeat_action(self):
        self.open()
        self.module._button(self.now, True)
        self.module._button(self.now, False)
        self.now += .03
        self.module._debounced(self.now)
        self.assertFalse(self.callbacks)
        self.module._activate(self.now)
        self.module._activate(self.now)
        self.module._cw(self.now)
        self.module._button(self.now, True)
        self.flush()
        self.gcode.run_script_from_command.assert_called_once_with('G28')

    def test_home_uses_configured_gcode_path(self):
        self.choose(0)
        self.gcode.run_script_from_command.assert_called_once_with('G28')

    def test_successful_home_preheat_and_moves_send_no_lcd_traffic(self):
        for page, selected in [('main', 0), ('main', 3)] + [
                ('move', index) for index in range(6)]:
            self.open()
            self.module.page, self.module.selected = page, selected
            self.module.drawn = None
            self.module._draw()
            self.lcd.render_drawing.reset_mock()
            self.lcd.render_idle.reset_mock()
            self.click()
            self.assertIsNone(self.module.error)
            self.lcd.render_drawing.assert_not_called()
            self.lcd.render_idle.assert_not_called()
            self.assertEqual(self.module.drawn, (page, selected))
            # Repeating the action still executes without disturbing selection.
            self.click()
            self.lcd.render_drawing.assert_not_called()
            self.module._cw(self.now)
            self.flush()
            self.lcd.render_drawing.assert_called_once()
            self.assertEqual(len(self.lcd.render_drawing.call_args[0][0]), 8)

    def test_changing_pages_only_replaces_body(self):
        self.open()
        self.module.selected = 4
        self.click()
        ops = self.operations[-1]
        self.assertNotIn(1, [op[0] for op in ops])
        self.assertFalse(any(op[10:] in (b'K', b'lipper') for op in ops
                             if op[0] == 3))
        self.module.selected = 6
        self.click()
        self.assertNotIn(1, [op[0] for op in self.operations[-1]])

    def test_rotations_that_return_to_same_selection_need_no_redraw(self):
        self.open()
        self.lcd.render_drawing.reset_mock()
        self.module._cw(self.now)
        self.module._ccw(self.now)
        self.flush()
        self.lcd.render_drawing.assert_not_called()

    def test_preheat_only_sets_requested_targets(self):
        self.choose(3)
        self.heaters.set_temperature.assert_has_calls([
            mock.call(self.heater, 170.), mock.call(self.bed, 50.)])
        self.toolhead.manual_move.assert_not_called()

    def test_each_axis_moves_ten_mm_with_correct_sign(self):
        for selected, axis, sign in ((0, 2, 1), (1, 2, -1), (2, 0, -1),
                                      (3, 0, 1), (4, 1, 1), (5, 1, -1)):
            self.log.clear()
            self.choose(selected, 'move')
            moves = [entry for entry in self.log if isinstance(entry, tuple)
                     and entry[0] == 'move']
            self.assertEqual(len(moves), 1)
            self.assertEqual(moves[0][1][axis], sign*10.)
            self.assertEqual(sum(abs(v) for v in moves[0][1]), 10.)

    def test_unhomed_and_out_of_range_reject_without_movement(self):
        for homed, position in (('', 100.), ('xyz', 245.)):
            self.toolhead.get_status.return_value['homed_axes'] = homed
            self.position[2] = position
            with self.assertLogs(level='WARNING'):
                self.choose(0, 'move')
            self.toolhead.manual_move.assert_not_called()
            self.assertIsNotNone(self.module.error)
            # The first click dismisses an error without repeating a move.
            self.click()
            self.assertIsNone(self.module.error)

    def check_filament(self, index, expected):
        self.choose(index)
        moves = [e for e in self.log if isinstance(e, tuple) and e[0] == 'move']
        grouped = []
        for unused, delta, speed in moves:
            self.assertEqual(delta[:3], [0., 0., 0.])
            self.assertLessEqual(abs(delta[3]), 1.)
            key = (speed, delta[3] > 0.)
            if not grouped or grouped[-1][0] != key:
                grouped.append([key, 0.])
            grouped[-1][1] += delta[3]
        self.assertEqual(grouped, expected)
        on, off = ('heat', self.heater, 240.), ('heat', self.heater, 0.)
        self.assertLess(self.log.index(on), self.log.index(moves[0]))
        self.assertLess(self.log.index(moves[-1]), self.log.index(off))
        self.assertEqual(self.log[self.log.index(off)-1], 'drain')
        self.assertEqual(self.heaters.set_temperature.call_count, 2)
        self.gcode.run_script_from_command.assert_has_calls([
            mock.call('SAVE_GCODE_STATE NAME=tjc_menu'),
            mock.call('RESTORE_GCODE_STATE NAME=tjc_menu')])

    def test_load_distances_speeds_and_heater_off_after_motion(self):
        self.check_filament(1, [[(800./60., True), 50.],
                                [(400./60., True), 40.]])

    def test_unload_purges_then_retracts_at_requested_speeds(self):
        self.check_filament(2, [[(400./60., True), 15.],
                                [(800./60., False), -90.]])

    def test_extrusion_waits_for_heating_and_drops_knob_events(self):
        def busy(eventtime):
            self.module._cw(eventtime)
            self.module._activate(eventtime)
            return eventtime < 11.
        self.heater.check_busy.side_effect = busy
        self.choose(1)
        self.assertGreaterEqual(self.now, 11.)
        self.assertEqual(self.module.selected, 1)

    def test_heating_timeout_and_invalidated_wait_turn_heater_off(self):
        self.heater.check_busy.return_value = True
        with self.assertLogs(level='WARNING'):
            self.choose(1)
        self.toolhead.manual_move.assert_not_called()
        self.heaters.set_temperature.assert_called_with(self.heater, 0.)
        self.assertIn('timed out', self.module.error)
        self.module.error = None
        def invalidate(when):
            self.now = when
            self.module._invalidate()
        self.reactor.pause.side_effect = invalidate
        with self.assertLogs(level='WARNING'):
            self.choose(1)
        self.toolhead.manual_move.assert_not_called()
        self.heaters.set_temperature.assert_called_with(self.heater, 0.)

    def test_motion_error_restores_gcode_and_turns_off_heater(self):
        self.toolhead.manual_move.side_effect = RuntimeError('Extrude limit')
        with self.assertLogs(level='WARNING'):
            self.choose(1)
        self.heaters.set_temperature.assert_called_with(self.heater, 0.)
        self.gcode.run_script_from_command.assert_called_with(
            'RESTORE_GCODE_STATE NAME=tjc_menu')

    def cancel_press(self):
        # Navigation throttling must never delay an active filament cancel.
        self.module.next_redraw = self.now+30.
        self.module._button(self.now, True)
        self.now += .03
        self.module._debounced(self.now)

    def test_click_cancels_heating_and_release_does_not_restart_action(self):
        self.heater.check_busy.return_value = True
        def pause(when):
            self.now = when
            self.cancel_press()
        self.reactor.pause.side_effect = pause
        self.choose(1)
        self.toolhead.manual_move.assert_not_called()
        self.gcode.run_script_from_command.assert_not_called()
        self.heaters.set_temperature.assert_has_calls([
            mock.call(self.heater, 240.), mock.call(self.heater, 0.)])
        self.assertEqual(self.module.drawn, ('main', 1))
        self.assertFalse(self.module.busy)
        self.assertIsNone(self.module.error)
        self.assertIsNone(self.module.filament_action)
        self.module._button(self.now, False)
        self.now += .03
        self.module._debounced(self.now)
        self.flush()
        self.assertEqual(self.heaters.set_temperature.call_count, 2)

    def test_cancel_during_full_screen_transfer_never_starts_heating(self):
        def render(ops):
            self.render(ops)
            if self.module.filament_action is not None:
                self.lcd.state = 'rendering'
                self.cancel_press()
                self.assertTrue(self.module.cancel_requested)
                self.lcd.state = 'ready'
        self.lcd.render_drawing.side_effect = render
        self.choose(2)
        self.heaters.set_temperature.assert_called_once_with(self.heater, 0.)
        self.toolhead.manual_move.assert_not_called()
        self.gcode.run_script_from_command.assert_not_called()
        self.assertEqual(self.module.drawn, ('main', 2))

    def cancel_after_moves(self, selected, count):
        cancelled = []
        def drain():
            self.log.append('drain')
            moves = [entry for entry in self.log if isinstance(entry, tuple)
                     and entry[0] == 'move']
            if len(moves) == count and not cancelled:
                cancelled.append(True)
                self.cancel_press()
        self.toolhead.wait_moves.side_effect = drain
        self.choose(selected)
        moves = [entry for entry in self.log if isinstance(entry, tuple)
                 and entry[0] == 'move']
        self.assertEqual(len(moves), count)
        indices = [index for index, entry in enumerate(self.log)
                   if isinstance(entry, tuple) and entry[0] == 'move']
        for start, end in zip(indices, indices[1:]):
            self.assertIn('drain', self.log[start+1:end])
        self.heaters.set_temperature.assert_called_with(self.heater, 0.)
        self.gcode.run_script_from_command.assert_called_with(
            'RESTORE_GCODE_STATE NAME=tjc_menu')
        self.assertEqual(self.module.drawn, ('main', selected))
        self.assertIsNone(self.module.error)
        return moves

    def test_cancel_loading_never_queues_more_than_one_mm(self):
        moves = self.cancel_after_moves(1, 2)
        self.assertEqual(sum(entry[1][3] for entry in moves), 2.)

    def test_cancel_unloading_preserves_purge_and_stops_retracting(self):
        moves = self.cancel_after_moves(2, 17)
        self.assertEqual(sum(entry[1][3] for entry in moves[:15]), 15.)
        self.assertEqual(sum(entry[1][3] for entry in moves[15:]), -2.)

    def test_cancel_bounce_and_stale_press_do_not_cancel_heating(self):
        self.heater.check_busy.side_effect = lambda et: et < 11.
        def pause(when):
            self.now = when
            self.module._button(9., True)
            self.module._button(self.now, True)
            self.module._button(self.now, False)
            self.now += .03
            self.module._debounced(self.now)
        self.reactor.pause.side_effect = pause
        self.choose(1)
        self.assertEqual(self.position[3], 90.)
        self.assertIsNone(self.module.error)

    def test_temperature_refresh_is_partial_once_per_second_and_stops(self):
        started = self.now
        self.heater.check_busy.side_effect = lambda et: et < started+3.4
        self.heater.get_temp.side_effect = lambda et: (
            25.+int((et-started)*10.), 240.)
        readings = []
        drained = []
        def drain():
            self.log.append('drain')
            count = self.toolhead.manual_move.call_count
            if count > len(drained):
                drained.append(count)
                self.now += .2
        self.toolhead.wait_moves.side_effect = drain
        def render(ops):
            self.render(ops)
            if any(op[0] == 3 and op[10:].startswith(b'Nozzle:')
                   for op in ops):
                readings.append((self.now, ops))
        self.lcd.render_drawing.side_effect = render
        self.choose(1)
        self.assertGreaterEqual(len(readings), 3)
        for when, ops in readings[1:]:
            self.assertNotIn(1, [op[0] for op in ops])
        heating = [when for when, ops in readings if when < started+3.4]
        times = [when for when, ops in readings]
        for before, after in zip(times, times[1:]):
            self.assertGreaterEqual(after-before, 1.)
        self.assertGreater(len(readings), len(heating)+1)
        before = len(readings)
        self.now += 100.
        self.stats.get_status.return_value = {'state': 'printing'}
        self.flush()
        self.assertEqual(len(readings), before)

    def test_preflight_stops_temperature_traffic_and_extrusion(self):
        self.heater.check_busy.return_value = True
        def pause(when):
            self.now = when
            self.sd.preflight_pending = True
        self.reactor.pause.side_effect = pause
        with self.assertLogs(level='WARNING'):
            self.choose(1)
        self.assertEqual(len(self.operations), 3)
        self.toolhead.manual_move.assert_not_called()
        self.heaters.set_temperature.assert_called_with(self.heater, 0.)

    def test_reset_while_draining_error_does_not_draw_stale_message(self):
        self.open()
        self.module.page, self.module.selected = 'move', 0
        self.toolhead.get_status.return_value['homed_axes'] = ''
        drains = []
        def drain():
            drains.append(True)
            if len(drains) == 2:
                self.module._invalidate()
        self.toolhead.wait_moves.side_effect = drain
        with self.assertLogs(level='WARNING'):
            self.module._activate(self.now)
            self.flush()
        self.lcd.render_drawing.assert_called_once()
        self.toolhead.manual_move.assert_not_called()

    def test_invalidation_during_loading_message_prevents_extrusion(self):
        def render(ops):
            self.render(ops)
            if any(op[0] == 3 and op[10:] == b'Loading...' for op in ops):
                self.module._invalidate()
        self.lcd.render_drawing.side_effect = render
        with self.assertLogs(level='WARNING'):
            self.choose(1)
        self.toolhead.manual_move.assert_not_called()
        self.gcode.run_script_from_command.assert_not_called()
        self.heaters.set_temperature.assert_called_with(self.heater, 0.)

    def test_auto_idle_does_not_overwrite_menu(self):
        self.open()
        self.lcd._handle_idle_timeout(0.)
        self.assertFalse(self.callbacks)
        self.lcd._schedule_idle(wait_moves=True)
        self.flush()
        self.lcd.render_idle.assert_not_called()

    def test_real_lcd_preprint_event_closes_menu_and_discards_old_work(self):
        self.open()
        handlers = [call.args[1] for call in
                    self.printer.register_event_handler.call_args_list
                    if call.args[0] == 'tjc_lcd:invalidate']
        self.printer.send_event.side_effect = lambda event: (
            [callback() for callback in handlers]
            if event == 'tjc_lcd:invalidate' else None)
        self.module._queue_redraw()
        self.lcd._handle_before_print('part.gcode')
        self.flush()
        self.assertFalse(self.module.is_active())
        self.assertEqual(len(self.operations), 1)
        self.toolhead.manual_move.assert_not_called()

    def test_reset_from_menu_queues_idle_after_motion(self):
        self.open()
        self.printer.send_event.side_effect = (
            lambda event: self.module._invalidate())
        self.lcd._handle_reset_file()
        self.flush()
        self.lcd.render_idle.assert_called_once()

    def test_load_preserves_real_gcode_modes_overrides_and_e_coordinates(self):
        state = gcode_move.GCodeMove(self.config)
        state.absolute_coord = state.allow_absolute_extrude = False
        state.speed_factor, state.extrude_factor = .5/60., 1.3
        state.speed = 57.
        state.last_position = list(self.position)
        state.base_position = [0., 0., 0., -13.]
        state.position_with_transform = self.toolhead.get_position
        state.is_printer_ready = True
        original_move = self.toolhead.manual_move.side_effect
        def move(coord, speed):
            original_move(coord, speed)
            state.reset_last_position()
        self.toolhead.manual_move.side_effect = move
        command = mock.Mock()
        command.get.return_value = 'tjc_menu'
        command.get_int.return_value = 0
        def script(value):
            if value.startswith('SAVE'):
                state.cmd_SAVE_GCODE_STATE(command)
            elif value.startswith('RESTORE'):
                state.cmd_RESTORE_GCODE_STATE(command)
        self.gcode.run_script_from_command.side_effect = script
        before = (state.last_position[3]-state.base_position[3])/1.3
        self.choose(1)
        self.assertEqual(state.speed_factor, .5/60.)
        self.assertEqual(state.extrude_factor, 1.3)
        self.assertEqual(state.speed, 57.)
        self.assertFalse(state.absolute_coord)
        self.assertFalse(state.allow_absolute_extrude)
        self.assertAlmostEqual(
            (state.last_position[3]-state.base_position[3])/1.3, before)
        self.assertEqual(self.position[:3], [100., 100., 100.])


class DrawingTransportTests(unittest.TestCase):
    def setUp(self):
        startup.HostTests.setUp(self)
        self.module.connected, self.module.state = True, 'ready'
        self.commands = tuple(mock.Mock() for i in range(4))
        self.module.draw_commands = self.commands
        self.offset = 0
        self.commands[0].send.side_effect = lambda args: self.ack(0, 1)
        def op(args):
            self.offset = args[2]+len(args[3])
            self.ack(self.offset, 1)
        self.commands[1].send.side_effect = op
        self.commands[2].send.side_effect = (
            lambda args: self.ack(self.offset, 2))

    def ack(self, offset, state):
        self.module._handle_job_result({'token': self.module.job_token,
                                       'offset': offset, 'state': state,
                                       'error': 0})
        self.reactor.register_async_callback.call_args[0][0](11.)

    def test_generic_transport_reuses_arbitration_and_final_checkpoint(self):
        self.module.render_drawing(layout.screen('main', 0))
        self.assertEqual(self.module.screen, 'menu')
        self.assertFalse(self.module.rendered)
        self.commands[2].send.assert_called_once()
        self.commands[3].send.assert_not_called()

    def test_calibration_reuses_menu_firmware_for_partial_updates(self):
        self.assertIsNone(self.module.canvas_begin_cmd)
        self.assertTrue(self.module.begin_calibration('Warming up', 170, 50))
        self.assertTrue(self.module.calibration_active)
        self.assertFalse(self.module.can_interact())
        initial = [call.args[0][3] for call in
                   self.commands[1].send.call_args_list]
        self.assertEqual(initial[0], lcd_draw.clear(lcd_canvas.BACKGROUND))
        self.commands[1].send.reset_mock()
        self.module.update_calibration('cr_touch', 'Test P1 pair 1')
        partial = [call.args[0][3] for call in
                   self.commands[1].send.call_args_list]
        self.assertTrue(partial)
        self.assertNotIn(1, [op[0] for op in partial])
        self.assertEqual(self.module.screen, 'calibration')
        with self.assertRaisesRegex(RuntimeError, 'reserved'):
            self.module._handle_before_print('part.gcode')
        self.module.end_calibration('Offset - review', True)
        self.assertFalse(self.module.calibration_active)
        self.assertEqual(self.module.screen, 'calibration_result')
        self.commands[3].send.assert_not_called()

    def test_invalid_canvas_fallback_sends_nothing_and_uses_draw_abort(self):
        with self.assertRaises(ValueError):
            self.module.render_canvas([lcd_canvas.rect(0, 0, 240, 1, 0)])
        self.commands[0].send.assert_not_called()
        self.module.canvas_abort_cmd = mock.Mock()
        self.commands[1].send.side_effect = None
        with self.assertLogs(level='WARNING'):
            with self.assertRaisesRegex(RuntimeError, 'acknowledgement'):
                self.module.render_canvas([lcd_canvas.rect(0, 0, 10, 10, 0)])
        self.commands[3].send.assert_called_once()
        self.module.canvas_abort_cmd.send.assert_not_called()
        self.assertIsNone(self.module.render_abort_cmd)

    def test_capability_registers_generic_commands_and_response(self):
        self.mcu.get_constants.return_value = {
            'TJC_LCD_VERSION': 1, 'TJC_JOB_VERSION': 5, 'TJC_DRAW_VERSION': 1}
        job_commands = [mock.Mock() for i in range(4)]
        commands = [self.mcu.start, self.mcu.clear] + job_commands
        self.mcu.lookup_command.side_effect = commands + list(self.commands)
        self.module._build_config()
        self.assertEqual(self.module.draw_commands, self.commands)
        self.mcu.register_serial_response.assert_any_call(
            self.module._handle_job_result,
            'tjc_lcd_draw_result oid=%c token=%u offset=%hu'
            ' state=%c error=%c', 7)

    def test_drawing_timeout_uses_generic_abort(self):
        self.commands[1].send.side_effect = None
        with self.assertLogs(level='WARNING'):
            with self.assertRaisesRegex(RuntimeError, 'acknowledgement'):
                self.module.render_drawing([lcd_draw.clear(0)])
        self.commands[3].send.assert_called_once()
        self.assertEqual(self.module.state, 'error')

    def test_preflight_and_invalid_ops_never_claim_uart(self):
        self.sd.preflight_pending = True
        with self.assertRaisesRegex(RuntimeError, 'idle printer'):
            self.module.render_drawing([lcd_draw.clear(0)])
        self.sd.preflight_pending = False
        with self.assertRaises(ValueError):
            self.module.render_drawing([b'bad'])
        self.commands[0].send.assert_not_called()

    def test_print_start_waits_for_inflight_generic_drawing(self):
        self.module.state, self.module.render_kind = 'rendering', 'menu'
        completion = self.module.render_completion = startup.Completion()
        def finish():
            self.module.state = 'ready'
        completion.wait.side_effect = finish
        self.module._handle_before_print('part.gcode')
        completion.wait.assert_called_once()
        self.printer.send_event.assert_called_with('tjc_lcd:invalidate')


class NativeDrawingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which('gcc') is None:
            raise unittest.SkipTest('Native GCC is required')
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        stubs = dict(startup.STUBS)
        stubs['command.h'] += '\nvoid *command_decode_ptr(uint32_t);\n'
        for name, content in stubs.items():
            path = os.path.join(cls.tmp.name, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w') as f:
                f.write(content)
        for name in ('tjc_draw.c', 'tjc_lcd.h', 'tjc_uart.h'):
            shutil.copyfile(os.path.join(startup.ROOT, 'src', name),
                            os.path.join(cls.tmp.name, name))
        cls.binary = os.path.join(cls.tmp.name, 'draw-test')
        subprocess.run(['gcc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                        '-Wno-unused-parameter', '-I', cls.tmp.name,
                        os.path.join(startup.ROOT, 'test/unit',
                                     'tjc_draw_harness.c'),
                        '-o', cls.binary], check=True, capture_output=True)

    def run_draw(self, operations, scenario='normal'):
        path = os.path.join(self.tmp.name, 'ops.bin')
        trace = os.path.join(self.tmp.name, 'frames.txt')
        with open(path, 'wb') as f:
            for op in operations:
                f.write(bytes([len(op)])+op)
        crc = zlib.crc32(b''.join(operations)) & 0xffffffff
        result = subprocess.run([self.binary, path, scenario, str(crc), trace],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(trace) as f:
            return [bytes.fromhex(line.strip()) for line in f]

    @staticmethod
    def frame_pixels(frames):
        # Use opaque synthetic glyph cells to verify painter order; the panel
        # font itself is unchanged. Shapes and colors come from real C frames.
        pixels, palette = [None]*(240*320), None
        for frame in frames:
            if frame[0] == 0x40:
                palette = int.from_bytes(frame[1:3], 'big')
            elif frame[0] == 0x52:
                pixels[:] = [palette]*len(pixels)
            elif frame[0] == 0x5b:
                x0, y0, x1, y1 = struct.unpack('>HHHH', frame[1:])
                for y in range(y0, y1+1):
                    pixels[y*240+x0:y*240+x1+1] = [palette]*(x1-x0+1)
            elif frame[0] == 0x98:
                x, y = struct.unpack('>HH', frame[1:5])
                width, height = lcd_draw.FONTS[frame[7]]
                fg, bg = struct.unpack('>HH', frame[8:12])
                for dy in range(height):
                    for dx in range(len(frame[12:])*width):
                        pixels[(y+dy)*240+x+dx] = (
                            fg if (frame[12+dx//width]+dx+dy) % 3 else bg)
        return pixels

    def test_optimized_transfers_keep_the_same_native_rendered_pixels(self):
        import lcd_idle_layout
        import lcd_job_layout
        import test_tjc_job as jobs
        drawings = [layout.screen('main', 0), layout.screen('move', 0),
                    layout.screen('move', 3, header=False),
                    layout.selection('move', 4, 3),
                    layout.filament_screen('load', 170, 'Heating...'),
                    layout.filament_screen('unload', 240, 'Unloading...'),
                    lcd_idle_layout.firmware_footer(
                        '3ae38934-dirty-20261102_182347-host')]
        drawings += [lcd_job_layout.screen(jobs.prepared()),
                     lcd_job_layout.screen(jobs.prepared(b'\xf8\x00'*9216))]
        for ops in drawings:
            optimized = [op for off, op in lcd_draw.prepare(ops)['chunks']]
            raw = self.frame_pixels(self.run_draw(ops))
            compact = self.frame_pixels(self.run_draw(optimized))
            self.assertEqual(raw, compact)

    def test_all_layouts_emit_the_host_specified_graphics_and_text(self):
        import lcd_idle_layout
        drawings = [layout.screen(page, 0) for page in ('main', 'move')]
        drawings.append(lcd_idle_layout.firmware_footer(
            '3ae38934-dirty-20261102_182347-IrohKlipper'))
        drawings += [layout.filament_screen(action, 170, phase)
                     for action, phase in (('load', 'Heating...'),
                                           ('load', 'Loading...'),
                                           ('unload', 'Unloading...'))]
        drawings += [layout.filament_temperature(value) for value in (None, 0)]
        for ops in drawings:
            frames = self.run_draw(ops)
            index = 0
            for op in ops:
                code = op[0]
                if code == 1:
                    self.assertEqual(frames[index], b'\x40'+op[1:3]*2)
                    self.assertEqual(frames[index+1], b'\x52')
                    index += 2
                elif code == 2:
                    self.assertEqual(frames[index], b'\x40'+op[1:3]*2)
                    self.assertEqual(frames[index+1], b'\x5b'+op[3:])
                    index += 2
                else:
                    self.assertEqual(frames[index], b'\x98'+op[6:10]
                                     + b'\x00\x42'+op[1:6]+op[10:])
                    index += 1
            self.assertEqual(index, len(frames))

    def test_error_paths_timeouts_tokens_and_clock_rollover(self):
        ops = [lcd_draw.clear(0), lcd_draw.text('Test', 5, 5)]
        for scenario in ('busy', 'abort', 'timeout', 'early_end', 'offset',
                         'uart', 'stall', 'crc', 'fence_timeout', 'rollover',
                         'stale'):
            with self.subTest(scenario=scenario):
                self.run_draw(ops, scenario)

    def test_mcu_independently_rejects_invalid_operations(self):
        for op in (b'\xff', lcd_draw.rectangle(1, 0, 0, 240, 0),
                   lcd_draw.text('a', 0, 310),
                   lcd_draw.text('a', 0, 0, font=0),
                   lcd_draw.text('\n', 0, 0),
                   lcd_draw.text('a', 0, 0)[:-1]+b'\x81'):
            with self.subTest(op=op):
                self.run_draw([op], 'invalid')


if __name__ == '__main__':
    unittest.main()
