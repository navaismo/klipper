# Host lifecycle for the mainboard C driver of the Ender-3 V3 SE TJC LCD
# Copyright (C) 2026
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
try:
    from . import lcd_transfer
except ImportError:
    import lcd_transfer

JOB_ERRORS = {1: 'LCD is busy', 2: 'Invalid LCD payload/header/text',
              3: 'Unexpected LCD transfer offset',
              4: 'LCD payload CRC mismatch',
              5: 'LCD UART error', 6: 'LCD render timed out',
              7: 'LCD job aborted'}

STATES = ('idle', 'initializing', 'clearing', 'ready', 'error')
ERRORS = {0: None, 1: 'LCD handshake/transfer timed out',
          2: 'LCD UART error or receive buffer overflow'}


class TJCLCD:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        mcu_name = config.get('mcu', 'mcu')
        self.mcu = self.printer.lookup_object(
            mcu_name if mcu_name == 'mcu' else 'mcu ' + mcu_name)
        self.oid = self.mcu.create_oid()
        self.startup_delay = config.getfloat('startup_delay', 1.,
                                              minval=0., maxval=5.)
        self.handshake_timeout = config.getfloat('handshake_timeout', 1.,
                                                  minval=.1, maxval=2.)
        self.clear_color = config.getint('clear_color', 0,
                                          minval=0, maxval=65535)
        self.state = 'disconnected'
        self.error = None
        self.connected = False
        self.generation = 0
        self.completion = None
        self.job_completion = self.job_expected = None
        self.job_token = 0
        self.job_filename = None
        self.rendered = False
        self.screen = None
        self.idle_generation = 0
        self.render_completion = None
        self.render_kind = None
        self.startup_idle_pending = False
        self.job_begin_cmd = self.job_chunk_cmd = None
        self.job_end_cmd = self.job_abort_cmd = None
        self.start_cmd = self.clear_cmd = None
        self.watchdog = self.reactor.register_timer(self._timeout)
        self.mcu.register_config_callback(self._build_config)
        self.mcu.register_serial_response(
            self._handle_state, 'tjc_lcd_state oid=%c state=%c error=%c',
            self.oid)
        self.printer.register_event_handler('klippy:ready', self._handle_ready)
        self.printer.register_event_handler('klippy:shutdown',
                                            self._handle_disconnect)
        self.printer.register_event_handler('klippy:disconnect',
                                            self._handle_disconnect)
        self.printer.register_event_handler('virtual_sdcard:before_print',
                                            self._handle_before_print)
        self.printer.register_event_handler('virtual_sdcard:reset_file',
                                            self._handle_reset_file)
        self.printer.register_event_handler('virtual_sdcard:finished',
                                            self._handle_finished)
        self.printer.register_event_handler('idle_timeout:idle',
                                            self._handle_idle_timeout)
        self.gcode.register_command('TJC_LCD_INIT', self.cmd_TJC_LCD_INIT,
                                    desc=self.cmd_TJC_LCD_INIT_help)
        self.gcode.register_command('TJC_LCD_CLEAR', self.cmd_TJC_LCD_CLEAR,
                                    desc=self.cmd_TJC_LCD_CLEAR_help)
        self.gcode.register_command('TJC_LCD_IDLE', self.cmd_TJC_LCD_IDLE,
                                    desc=self.cmd_TJC_LCD_IDLE_help)

    def _build_config(self):
        if self.mcu.get_constants().get('TJC_LCD_VERSION') != 1:
            raise self.printer.config_error(
                'tjc_lcd requires firmware built with the TJC USART2 driver')
        self.mcu.add_config_cmd(
            'config_tjc_lcd oid=%d startup_ticks=%d timeout_ticks=%d color=%d'
            % (self.oid, self.mcu.seconds_to_clock(self.startup_delay),
               self.mcu.seconds_to_clock(self.handshake_timeout),
               self.clear_color))
        cq = self.mcu.alloc_command_queue()
        self.start_cmd = self.mcu.lookup_command('tjc_lcd_start oid=%c', cq=cq)
        self.clear_cmd = self.mcu.lookup_command(
            'tjc_lcd_clear oid=%c color=%hu', cq=cq)
        if (self.mcu.get_constants().get('TJC_JOB_VERSION')
            == lcd_transfer.VERSION):
            self.mcu.register_serial_response(
                self._handle_job_result,
                'tjc_lcd_job_result oid=%c token=%u offset=%hu'
                ' state=%c error=%c',
                self.oid)
            self.job_begin_cmd = self.mcu.lookup_command(
                'tjc_lcd_job_begin oid=%c token=%u size=%hu crc=%u', cq=cq)
            self.job_chunk_cmd = self.mcu.lookup_command(
                'tjc_lcd_job_chunk oid=%c token=%u offset=%hu data=%*s', cq=cq)
            self.job_end_cmd = self.mcu.lookup_command(
                'tjc_lcd_job_end oid=%c token=%u', cq=cq)
            self.job_abort_cmd = self.mcu.lookup_command(
                'tjc_lcd_job_abort oid=%c token=%u', cq=cq)

    def _handle_ready(self):
        self.connected = True
        self.state = 'idle'
        generation = self.generation
        def initialize(eventtime):
            if (not self.connected or generation != self.generation
                or self.state != 'idle'):
                return
            try:
                self._start()
            except self.gcode.error as e:
                self._fail(str(e))
        self.reactor.register_callback(initialize)

    def _handle_disconnect(self):
        self.startup_idle_pending = False
        self.connected = False
        self.generation += 1
        self.idle_generation += 1
        self.state = 'disconnected'
        self.error = None
        self.reactor.update_timer(self.watchdog, self.reactor.NEVER)
        self._complete(False)
        if self.job_completion is not None:
            self.job_completion.complete(None)
        self.rendered = False
        self.job_filename = None
        self.screen = None

    def _handle_reset_file(self):
        self.idle_generation += 1
        if self.state == 'rendering' and self.render_kind != 'idle':
            self._abort_job()
            self._fail('LCD preparation invalidated by file reset')
            if self.job_completion is not None:
                self.job_completion.complete(None)

        # Cancel macros may reset the file after CANCEL_PRINT_BASE, or use
        # SDCARD_RESET_FILE alone. Replace the invalidated idle callback;
        # the G-code mutex and motion drain still precede all LCD traffic.
        if self.connected and self.state == 'ready' and self.screen == 'job':
            self._schedule_idle(wait_moves=True)

    def _handle_finished(self, state):
        if state in ('complete', 'cancelled'):
            self._schedule_idle(wait_moves=True)

    def _handle_idle_timeout(self, print_time):
        if not self.connected or self.state != 'ready' or self.screen == 'idle':
            return
        stats = self.printer.lookup_object('print_stats', None)
        if stats is not None and stats.get_status(
                self.reactor.monotonic())['state'] in ('printing', 'paused'):
            return
        self._schedule_idle(wait_moves=True)

    def _schedule_idle(self, wait_moves=False):
        self.idle_generation += 1
        token, generation = self.idle_generation, self.generation
        def show_idle(eventtime):
            if (not self.connected or generation != self.generation
                or token != self.idle_generation or self.state != 'ready'
                or self.job_begin_cmd is None):
                return
            try:
                # Let a running end/cancel macro finish queuing its moves.
                # Holding the G-code mutex also excludes new manual motion.
                with self.gcode.get_mutex():
                    if (token != self.idle_generation
                        or generation != self.generation):
                        return
                    sd = self.printer.lookup_object('virtual_sdcard', None)
                    if sd is not None and sd.preflight_pending:
                        return
                    self._check_idle()
                    if wait_moves:
                        self.printer.lookup_object('toolhead').wait_moves()
                    # Waiting yields: a reset/disconnect supersedes this.
                    if (token != self.idle_generation
                        or generation != self.generation):
                        return
                    self.render_idle()
            except self.gcode.error as e:
                logging.warning('TJC idle screen skipped: %s', e)
        self.reactor.register_callback(show_idle)

    def _complete(self, success):
        if self.completion is not None:
            self.completion.complete(success)
            self.completion = None

    def _check_idle(self, allow_rendering=False):
        if not self.connected or self.printer.is_shutdown():
            raise self.gcode.error('TJC LCD requires ready Klipper')
        sd = self.printer.lookup_object('virtual_sdcard', None)
        stats = self.printer.lookup_object('print_stats', None)
        if ((sd is not None and sd.is_active())
            or (stats is not None and stats.get_status(
                self.reactor.monotonic())['state'] in ('printing', 'paused'))):
            raise self.gcode.error('TJC LCD commands require an idle printer')
        if (self.state in ('initializing', 'clearing')
            or (self.state == 'rendering' and not allow_rendering)):
            raise self.gcode.error('TJC LCD operation already in progress')

    def _fail(self, message):
        self.startup_idle_pending = False
        self.state = 'error'
        self.error = message
        self.rendered = False
        self.job_filename = None
        self.screen = None
        self.reactor.update_timer(self.watchdog, self.reactor.NEVER)
        self._complete(False)
        logging.warning('TJC LCD: %s', message)

    def _send(self, command, args, state, duration):
        self.state = state
        self.error = None
        self.completion = self.reactor.completion()
        self.reactor.update_timer(self.watchdog,
                                  self.reactor.monotonic() + duration)
        try:
            command.send(args)
        except Exception as e:
            self._fail(str(e))
            raise self.gcode.error('TJC LCD request failed: %s' % (e,))

    def _start(self):
        self.idle_generation += 1
        self._check_idle()
        if self.printer.get_start_args().get('debugoutput') is not None:
            self.state = 'idle'
            return
        self.startup_idle_pending = True
        self._send(self.start_cmd, [self.oid], 'initializing',
                   self.startup_delay + 3 * self.handshake_timeout + 1.)
        self.rendered = False
        self.job_filename = None
        self.screen = None

    def _handle_job_result(self, params):
        generation = self.generation
        def publish(eventtime):
            if (not self.connected or generation != self.generation
                or self.state != 'rendering' or self.job_completion is None
                or params['token'] != self.job_token):
                return
            if params['error'] or (params['offset'], params['state']) == (
                    self.job_expected):
                self.job_completion.complete(params)
        self.reactor.register_async_callback(publish)

    def _job_request(self, command, args, offset, state, deadline):
        self._check_idle(allow_rendering=True)
        self.job_expected = (offset, state)
        completion = self.job_completion = self.reactor.completion()
        try:
            command.send(args)
            response = completion.wait(min(deadline,
                                           self.reactor.monotonic() + 3.))
            if response is None:
                raise self.gcode.error('No LCD job acknowledgement from MCU')
            if response['error']:
                raise self.gcode.error(JOB_ERRORS.get(response['error'],
                                                      'Unknown LCD job error'))
            if not self.connected or self.state != 'rendering':
                raise self.gcode.error('LCD render invalidated')
        finally:
            self.job_completion = self.job_expected = None

    def _abort_job(self):
        if (self.connected and not self.printer.is_shutdown()
            and self.job_abort_cmd is not None):
            self.job_abort_cmd.send([self.oid, self.job_token])

    def render_job(self, job):
        if self.printer.get_start_args().get('debugoutput') is not None:
            return
        self._handle_before_print(job['filename'])
        self._render(job['transfer'], 'job', job['filename'])

    def render_idle(self):
        if self.printer.get_start_args().get('debugoutput') is not None:
            return
        self._handle_before_print(None)
        self._render(lcd_transfer.prepare_idle(), 'idle')

    def _render(self, transfer, kind, filename=None):
        self._check_idle()
        if self.job_begin_cmd is None:
            raise self.gcode.error(
                'LCD rendering requires rebuilt firmware'
                ' with TJC_JOB_VERSION=%d' % lcd_transfer.VERSION)
        # Validate before claiming the UART or changing the visible card.
        chunks = list(lcd_transfer.iter_chunks(transfer))
        self.job_token = (self.job_token + 1) & 0xffffffff
        self.state = 'rendering'
        self.error = None
        self.rendered = False
        self.job_filename = None
        self.screen = None
        self.render_kind = kind
        completion = self.render_completion = self.reactor.completion()
        deadline = self.reactor.monotonic() + 180.
        token = self.job_token
        try:
            self._job_request(self.job_begin_cmd,
                              [self.oid, token, transfer['size'],
                               transfer['crc32']], 0, 1, deadline)
            for offset, data in chunks:
                # The virtual-SD timer is still stopped throughout rendering.
                if self.reactor.monotonic() >= deadline:
                    raise self.gcode.error('LCD job preparation exceeded 180s')
                self._job_request(self.job_chunk_cmd,
                                  [self.oid, token, offset, data],
                                  offset + len(data), 1, deadline)
            self._job_request(self.job_end_cmd, [self.oid, token],
                              transfer['size'], 2, deadline)
        except Exception as e:
            try:
                self._abort_job()
            except Exception:
                logging.exception('Could not abort LCD job')
            if self.connected:
                self._fail(str(e))
            completion.complete(False)
            self.render_completion = self.render_kind = None
            raise self.gcode.error('LCD job render failed: %s' % (e,))
        self.state = 'ready'
        self.rendered = kind == 'job'
        self.job_filename = filename
        self.screen = kind
        completion.complete(True)
        self.render_completion = self.render_kind = None
        logging.info('LCD %s screen rendered: %s', kind, filename)

    def _handle_before_print(self, filename):
        self.idle_generation += 1
        if self.printer.get_start_args().get('debugoutput') is not None:
            return
        if self.state == 'idle':
            self._start()
        completion = self.completion
        if completion is not None:
            completion.wait()
        if self.render_kind == 'idle' and self.render_completion is not None:
            self.render_completion.wait()
        if self.state != 'ready':
            raise self.gcode.error('TJC LCD is not ready: %s'
                                   % (self.error or self.state,))

    def _handle_state(self, params):
        # MCU responses arrive on the serial thread; publish on the reactor.
        generation = self.generation
        def publish(eventtime):
            if (not self.connected or generation != self.generation
                or self.state not in ('initializing', 'clearing')):
                return
            state = params['state']
            error = params['error']
            if state not in range(len(STATES)) or error not in ERRORS:
                self._fail('Invalid LCD state response from MCU')
            elif error or state == 4:
                self._fail(ERRORS[error] or 'LCD initialization failed')
            else:
                self.state = STATES[state]
                self.error = None
                if state in (0, 3):
                    self.reactor.update_timer(self.watchdog,
                                              self.reactor.NEVER)
                    self._complete(state == 3)
                    logging.info('TJC LCD: %s', self.state)
                    if state == 3:
                        self.screen = 'blank'
                        if self.startup_idle_pending:
                            self.startup_idle_pending = False
                            self._schedule_idle(wait_moves=True)
        self.reactor.register_async_callback(publish)

    def _timeout(self, eventtime):
        if self.connected and self.state in ('initializing', 'clearing'):
            self._fail('No completion response from LCD MCU driver')
        return self.reactor.NEVER

    def get_status(self, eventtime):
        return {'state': self.state, 'error': self.error,
                'rendered': self.rendered, 'job_filename': self.job_filename,
                'screen': self.screen}

    cmd_TJC_LCD_INIT_help = 'Handshake and clear the mainboard-connected LCD'
    def cmd_TJC_LCD_INIT(self, gcmd):
        self._start()
        gcmd.respond_info('TJC LCD initialization requested')

    cmd_TJC_LCD_CLEAR_help = 'Clear the LCD when the printer is idle'
    def cmd_TJC_LCD_CLEAR(self, gcmd):
        self._check_idle()
        self.startup_idle_pending = False
        self.idle_generation += 1
        if self.state != 'ready':
            raise gcmd.error('Initialize the TJC LCD before clearing it')
        color = gcmd.get_int('COLOR', self.clear_color, minval=0, maxval=65535)
        self._send(self.clear_cmd, [self.oid, color], 'clearing',
                   self.handshake_timeout + 1.)
        self.rendered = False
        self.job_filename = None
        self.screen = None
        gcmd.respond_info('TJC LCD clear requested')

    cmd_TJC_LCD_IDLE_help = 'Render the idle screen when the printer is idle'
    def cmd_TJC_LCD_IDLE(self, gcmd):
        self._check_idle()
        self.printer.lookup_object('toolhead').wait_moves()
        self.render_idle()
        gcmd.respond_info('TJC LCD idle screen rendered')


def load_config(config):
    return TJCLCD(config)
