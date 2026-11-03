# Idle knob menus and printing-time pause for host-designed TJC screens
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
try:
    from . import tjc_menu_layout as layout
except ImportError:
    import tjc_menu_layout as layout

NAVIGATION_INTERVAL = .200


class FilamentCancelled(Exception):
    pass


class TJCMenu:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self.lcd = self.printer.load_object(config, 'tjc_lcd')
        self.pause_resume = self.printer.load_object(config, 'pause_resume')
        buttons = self.printer.load_object(config, 'buttons')
        pins = config.getlist('encoder_pins', count=2)
        steps = config.getchoice('encoder_steps_per_detent', [2, 4], 4)
        buttons.register_rotary_encoder(pins[0], pins[1],
                                        self._cw, self._ccw, steps)
        buttons.register_buttons([config.get('click_pin')], self._button)
        self.debounce_delay = config.getfloat('debounce_delay', .025,
                                              minval=.005, maxval=.1)
        self.page = None
        self.selected = 0
        self.generation = 0
        self.pending = self.busy = False
        self.redraw_request = None
        self.next_redraw = 0.
        self.drawn = None
        self.error = None
        self.ignore_before = 0.
        self.physical = self.pressed = False
        self.press_time = None
        self.button_generation = None
        self.button_allowed = self.pause_press = False
        self.button_job = None
        self.pause_request = None
        self.long_fired = False
        self.filament_action = None
        self.cancel_requested = False
        self.filament_input_after = 0.
        self.filament_next_update = 0.
        self.filament_temperature = self.filament_phase = None
        self.debounce_timer = self.reactor.register_timer(self._debounced)
        self.hold_timer = self.reactor.register_timer(self._hold)
        self.printer.register_event_handler('klippy:connect', self._connect)
        for event in ('klippy:shutdown', 'klippy:disconnect',
                      'tjc_lcd:invalidate', 'virtual_sdcard:before_print'):
            self.printer.register_event_handler(event, self._invalidate)

    def _connect(self):
        if self.lcd.draw_commands is None:
            raise self.printer.config_error(
                'tjc_menu requires TJC_DRAW_VERSION=1 MCU firmware'
                ' (connected MCU reports TJC_DRAW_VERSION=%s; firmware: %s)'
                % (self.lcd.mcu.get_constants().get('TJC_DRAW_VERSION',
                                                   'missing'),
                   self.lcd.mcu.get_status(self.reactor.monotonic()).get(
                       'mcu_version', 'unknown')))

    def is_active(self):
        return self.page is not None

    def _invalidate(self, *args):
        self.generation += 1
        self.page = None
        self.drawn = None
        self.pending = False
        self.redraw_request = None
        self.next_redraw = 0.
        self.press_time = None
        self.physical = self.pressed = False
        self.button_allowed = self.pause_press = False
        self.pause_request = None
        self.ignore_before = self.reactor.monotonic()
        self.reactor.update_timer(self.debounce_timer, self.reactor.NEVER)
        self.reactor.update_timer(self.hold_timer, self.reactor.NEVER)

    def _allowed(self):
        return self.lcd.can_interact() and not self.printer.is_shutdown()

    def _input(self, eventtime):
        return (eventtime >= self.ignore_before and not self.busy
                and self._allowed())

    def _cw(self, eventtime):
        self._rotate(eventtime, 1)

    def _ccw(self, eventtime):
        self._rotate(eventtime, -1)

    def _rotate(self, eventtime, delta):
        if not self._input(eventtime):
            return
        if not self.is_active():
            if self.lcd.screen != 'idle':
                return
            self.page, self.selected = 'main', 0
            self.drawn = None
        else:
            count = len(layout.MAIN if self.page == 'main' else layout.MOVES)
            self.selected = (self.selected+delta) % count
        self.error = None
        self._queue_redraw()

    def _queue_redraw(self):
        if self.pending:
            return
        self.pending = True
        request = self.redraw_request = object()
        token = self.generation
        def redraw(eventtime):
            if (token != self.generation
                or self.redraw_request is not request):
                return
            # Claim ownership before the mutex can yield to queued GPIO input.
            self.busy = True
            try:
                with self.gcode.get_mutex():
                    if token != self.generation or not self._allowed():
                        return
                    self._draw()
            except Exception as e:
                self._report_error(e)
            finally:
                self.busy = False
                if self.redraw_request is request:
                    self.pending = False
                    self.redraw_request = None
                    self.ignore_before = self.reactor.monotonic()
                    self.next_redraw = self.ignore_before+NAVIGATION_INTERVAL
        # One deferred draw holds only the latest selection. Never replay a
        # backlog of detents, or sleep in a button/reactor callback.
        self.reactor.register_callback(redraw, max(
            self.reactor.monotonic(), self.next_redraw))

    def _draw(self):
        page, selected = self.page, self.selected
        if page is None or self.drawn == (page, selected):
            return
        self.printer.lookup_object('toolhead').wait_moves()
        if not self._allowed() or self.page != page:
            return
        if self.drawn is not None and self.drawn[0] == page:
            ops = layout.selection(page, selected, self.drawn[1])
        else:
            ops = layout.screen(page, selected, header=self.drawn is None)
        self.lcd.render_drawing(ops)
        if self.page == page:
            self.drawn = (page, selected)

    def _button(self, eventtime, state):
        # Reject queued button transitions captured during a busy operation.
        cancellable = (self.filament_action is not None
                       and eventtime >= self.filament_input_after)
        if eventtime < self.ignore_before or (self.busy and not cancellable):
            self.physical = self.pressed = False
            self.press_time = None
            self.reactor.update_timer(self.hold_timer, self.reactor.NEVER)
            return
        if state and not self.physical:
            self.button_generation = self.generation
            self.pause_press = self._pause_available()
            self.button_job = self.lcd.job_filename
            self.button_allowed = (cancellable or self.pause_press
                                   or self._input(eventtime))
        self.physical = bool(state)
        self.reactor.update_timer(self.debounce_timer,
                                  self.reactor.monotonic()+self.debounce_delay)

    def _debounced(self, eventtime):
        if self.physical == self.pressed:
            return self.reactor.NEVER
        self.pressed = self.physical
        if self.pressed:
            self.press_time = None
            if (not self.button_allowed
                or self.button_generation != self.generation):
                return self.reactor.NEVER
            if self.pause_press:
                if self._pause_available():
                    self._queue_pause(self.button_job)
                return self.reactor.NEVER
            if self.filament_action is not None:
                # Never acquire G-code/LCD ownership from an input callback.
                # The action observes this flag after its bounded work yields.
                self.cancel_requested = True
                self.reactor.update_timer(self.hold_timer, self.reactor.NEVER)
                return self.reactor.NEVER
            if (not self._input(eventtime) or self.pending
                or not self.is_active()):
                return self.reactor.NEVER
            self.press_time = eventtime
            self.long_fired = False
            self.reactor.update_timer(self.hold_timer, eventtime+.8)
        else:
            self.reactor.update_timer(self.hold_timer, self.reactor.NEVER)
            if self.press_time is not None and not self.long_fired:
                self._activate(eventtime)
            self.press_time = None
        return self.reactor.NEVER

    def _pause_available(self, request=None):
        if (self.printer.is_shutdown() or not self.lcd.connected
            or self.lcd.state != 'ready' or self.lcd.screen != 'job'
            or not self.lcd.rendered
            or (self.pause_request is not None
                and self.pause_request is not request)):
            return False
        sd = self.printer.lookup_object('virtual_sdcard', None)
        if sd is not None and (sd.preflight_pending or not sd.is_active()):
            return False
        now = self.reactor.monotonic()
        stats = self.printer.lookup_object('print_stats', None)
        return (stats is not None
                and stats.get_status(now)['state'] == 'printing'
                and not self.pause_resume.get_status(now)['is_paused'])

    def _queue_pause(self, filename):
        request = self.pause_request = object()
        token = self.generation
        def pause(eventtime):
            try:
                with self.gcode.get_mutex():
                    if (self.pause_request is not request
                        or token != self.generation
                        or filename != self.lcd.job_filename):
                        return
                    if self._pause_available(request):
                        self.gcode.run_script_from_command('PAUSE')
            except Exception as e:
                # A print-time input must never draw an error screen.
                self._report_error(e)
            finally:
                if self.pause_request is request:
                    self.pause_request = None
        self.reactor.register_callback(pause)

    def _hold(self, eventtime):
        if (self.pressed and self.physical and self.press_time is not None
            and self._input(eventtime) and not self.pending):
            self.long_fired = True
            if self.is_active():
                self._queue_action('idle')
        return self.reactor.NEVER

    def _activate(self, eventtime):
        if not self._input(eventtime) or self.pending or not self.is_active():
            return
        if self.error is not None:
            self.error = None
            self.drawn = None
            self._queue_redraw()
            return
        if self.page == 'main':
            action = ('home', 'load', 'unload', 'preheat',
                      'moves')[self.selected]
        else:
            action = ('z+', 'z-', 'x-', 'x+', 'y+', 'y-', 'back')[self.selected]
        if action in ('moves', 'back'):
            self.page = 'move' if action == 'moves' else 'main'
            self.selected = 0 if action == 'moves' else 4
            self._queue_redraw()
        else:
            self._queue_action(action)

    def _queue_action(self, action):
        self.busy = True
        token = self.generation
        def execute(eventtime):
            try:
                with self.gcode.get_mutex():
                    if token != self.generation or not self._allowed():
                        return
                    if action == 'idle':
                        self.printer.lookup_object('toolhead').wait_moves()
                        if token == self.generation and self._allowed():
                            self.lcd.render_idle()
                        return
                    self._action(action, token)
                    if (token == self.generation and self._allowed()
                        and self.drawn is None):
                        self._draw()
            except Exception as e:
                self._report_error(e)
                if token == self.generation and self._allowed():
                    try:
                        with self.gcode.get_mutex():
                            if token == self.generation and self._allowed():
                                toolhead = self.printer.lookup_object(
                                    'toolhead')
                                toolhead.wait_moves()
                                if (token != self.generation
                                    or not self._allowed()):
                                    return
                                self.lcd.render_drawing(layout.message(
                                    'Action failed', str(e)))
                                self.drawn = None
                    except Exception:
                        logging.exception('Could not show TJC menu error')
            finally:
                self.busy = False
                self.ignore_before = self.reactor.monotonic()
        self.reactor.register_callback(execute)

    def _check_action(self, token):
        if token != self.generation or not self._allowed():
            raise self.gcode.error('LCD menu action invalidated')

    def _action(self, action, token):
        self._check_action(token)
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.wait_moves()
        self._check_action(token)
        if action in ('load', 'unload'):
            self.drawn = None
            self._filament(action, token)
        elif action == 'home':
            self.gcode.run_script_from_command('G28')
            toolhead.wait_moves()
        elif action == 'preheat':
            heaters = self.printer.lookup_object('heaters')
            nozzle = toolhead.get_extruder().get_heater()
            bed = heaters.lookup_heater('heater_bed')
            heaters.set_temperature(nozzle, 170.)
            try:
                heaters.set_temperature(bed, 50.)
            except Exception:
                heaters.set_temperature(nozzle, 0.)
                raise
        else:
            index = 'xyz'.index(action[0])
            status = toolhead.get_status(self.reactor.monotonic())
            if action[0] not in status['homed_axes']:
                raise self.gcode.error('Home the %s axis first'
                                       % action[0].upper())
            destination = toolhead.get_position()[index]
            destination += 10. if action[1] == '+' else -10.
            if not (status['axis_minimum'][index] <= destination
                    <= status['axis_maximum'][index]):
                raise self.gcode.error(
                    'Full 10 mm move would exceed axis limits')
            coord = [None]*(index+1)
            coord[index] = destination
            toolhead.manual_move(coord, 2. if index == 2 else 20.)
            toolhead.wait_moves()

    def _filament(self, action, token):
        toolhead = self.printer.lookup_object('toolhead')
        heaters = self.printer.lookup_object('heaters')
        heater = toolhead.get_extruder().get_heater()
        saved = False
        cancelled = False
        self.filament_action = action
        self.cancel_requested = False
        self.filament_input_after = self.reactor.monotonic()
        self.filament_temperature = self.filament_phase = None
        try:
            # Prepare the full artwork before enabling the heater. A click
            # during this transfer can cancel without heating or extruding.
            self._update_filament(heater, 'Heating...', token, full=True)
            self._check_filament(token)
            heaters.set_temperature(heater, 240.)
            deadline = self.reactor.monotonic()+300.
            while heater.check_busy(self.reactor.monotonic()):
                self._check_filament(token)
                if self.reactor.monotonic() >= deadline:
                    raise self.gcode.error('Filament heating timed out')
                self._update_filament(heater, 'Heating...', token)
                self.reactor.pause(self.reactor.monotonic()+.25)
            self._check_filament(token)
            detail = 'Loading...' if action == 'load' else 'Unloading...'
            self._update_filament(heater, detail, token)
            self._check_filament(token)
            self.gcode.run_script_from_command('SAVE_GCODE_STATE NAME=tjc_menu')
            saved = True
            sequence = ((50., 800.), (40., 400.)) if action == 'load' else (
                (15., 400.), (-90., 800.))
            for distance, feedrate in sequence:
                while abs(distance) > .000001:
                    self._check_filament(token)
                    step = min(abs(distance), 1.)
                    if distance < 0.:
                        step = -step
                    target = toolhead.get_position()[3]+step
                    toolhead.manual_move([None, None, None, target],
                                         feedrate/60.)
                    distance -= step
                    # Do not queue the entire operation ahead of a cancel.
                    # Only this <=1 mm move can remain after the knob press.
                    toolhead.wait_moves()
                    self._check_filament(token)
                    self._update_filament(heater, detail, token)
            toolhead.wait_moves()
        except FilamentCancelled:
            cancelled = True
        finally:
            try:
                try:
                    toolhead.wait_moves()
                finally:
                    try:
                        heaters.set_temperature(heater, 0.)
                    finally:
                        if saved and not self.printer.is_shutdown():
                            self.gcode.run_script_from_command(
                                'RESTORE_GCODE_STATE NAME=tjc_menu')
            finally:
                self.filament_action = None
                self.cancel_requested = False
                self.press_time = None
                self.reactor.update_timer(self.hold_timer, self.reactor.NEVER)
        if cancelled and token == self.generation:
            self.page = 'main'
            self.selected = 1 if action == 'load' else 2
            self.error = None
            self.gcode.respond_info('TJC menu: Filament operation cancelled')

    def _check_filament(self, token):
        self._check_action(token)
        if self.cancel_requested:
            raise FilamentCancelled()

    def _update_filament(self, heater, phase, token, full=False):
        self._check_filament(token)
        now = self.reactor.monotonic()
        if (not full and phase == self.filament_phase
            and now < self.filament_next_update):
            return
        read_temperature = full or now >= self.filament_next_update
        temperature = self.filament_temperature
        if read_temperature:
            temperature = layout.temperature_value(heater.get_temp(now)[0])
        if full:
            ops = layout.filament_screen(self.filament_action,
                                          temperature, phase)
        else:
            ops = []
            if temperature != self.filament_temperature:
                ops += layout.filament_temperature(temperature)
            if phase != self.filament_phase:
                ops += layout.filament_phase(phase)
        if ops:
            self.lcd.render_drawing(ops)
        self._check_filament(token)
        self.filament_temperature, self.filament_phase = temperature, phase
        if read_temperature:
            self.filament_next_update = self.reactor.monotonic()+1.

    def _report_error(self, error):
        self.error = str(error)
        logging.warning('TJC menu: %s', error)
        self.gcode.respond_info('TJC menu: %s' % error)

    def get_status(self, eventtime):
        labels = layout.MAIN if self.page == 'main' else layout.MOVES
        return {'active': self.is_active(), 'page': self.page,
                'selected': labels[self.selected] if self.is_active() else None,
                'busy': self.busy, 'error': self.error,
                'filament_action': self.filament_action,
                'cancel_requested': self.cancel_requested}


def load_config(config):
    return TJCMenu(config)
