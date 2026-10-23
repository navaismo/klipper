# Tests of LCD startup with fake printer/MCU dependencies and native C mocks
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import os, shutil, subprocess, sys, tempfile, unittest
from unittest import mock

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'klippy', 'extras'))
import tjc_lcd


class Completion:
    def __init__(self):
        self.value = None
        self.wait = mock.Mock(side_effect=lambda *args: self.value)
    def complete(self, value):
        self.value = value


class MCU:
    def __init__(self):
        self.create_oid = mock.Mock(return_value=7)
        self.register_config_callback = mock.Mock()
        self.register_serial_response = mock.Mock()
        self.get_constants = mock.Mock(return_value={'TJC_LCD_VERSION': 1})
        self.add_config_cmd = mock.Mock()
        self.seconds_to_clock = lambda seconds: int(seconds * 72000000)
        self.alloc_command_queue = mock.Mock(return_value=object())
        self.start = mock.Mock()
        self.clear = mock.Mock()
        self.lookup_command = mock.Mock(side_effect=[self.start, self.clear])


class HostTests(unittest.TestCase):
    def setUp(self):
        self.mcu = MCU()
        self.gcode = mock.Mock(error=RuntimeError)
        self.gcode.get_mutex.return_value = mock.MagicMock()
        self.stats = mock.Mock()
        self.stats.get_status.return_value = {'state': 'standby'}
        self.sd = mock.Mock()
        self.sd.is_active.return_value = False
        self.sd.preflight_pending = False
        self.objects = {'mcu': self.mcu, 'gcode': self.gcode,
                        'virtual_sdcard': self.sd, 'print_stats': self.stats,
                        'toolhead': mock.Mock()}
        self.printer = mock.Mock()
        self.printer.lookup_object.side_effect = (
            lambda name, default=None: self.objects.get(name, default))
        self.printer.config_error = RuntimeError
        self.printer.is_shutdown.return_value = False
        self.printer.get_start_args.return_value = {}
        self.reactor = mock.Mock(NEVER=9999999.)
        self.reactor.monotonic.return_value = 10.
        self.reactor.completion.side_effect = Completion
        self.printer.get_reactor.return_value = self.reactor
        self.config = mock.Mock()
        self.config.get_printer.return_value = self.printer
        self.config.get.side_effect = lambda name, default: default
        self.config.getfloat.side_effect = lambda name, default, **kw: default
        self.config.getint.side_effect = lambda name, default, **kw: default
        self.module = tjc_lcd.TJCLCD(self.config)
        self.module._build_config()

    def start(self):
        self.module._handle_ready()
        callback = self.reactor.register_callback.call_args[0][0]
        callback(10.)

    def response(self, state, error=0):
        self.module._handle_state({'state': state, 'error': error})
        callback = self.reactor.register_async_callback.call_args[0][0]
        callback(11.)

    def test_build_contract_and_deferred_start(self):
        self.mcu.add_config_cmd.assert_called_once_with(
            'config_tjc_lcd oid=7 startup_ticks=72000000 '
            'timeout_ticks=72000000 color=0')
        self.mcu.register_serial_response.assert_any_call(
            self.module._handle_state,
            'tjc_lcd_state oid=%c state=%c error=%c', 7)
        self.mcu.start.send.assert_not_called()
        self.start()
        self.mcu.start.send.assert_called_once_with([7])
        self.assertEqual(self.module.state, 'initializing')
        self.reactor.update_timer.assert_called_with(self.module.watchdog, 15.)

    def test_missing_firmware_fails_before_ready(self):
        self.mcu.get_constants.return_value = {}
        with self.assertRaisesRegex(RuntimeError, 'firmware built'):
            self.module._build_config()

    def test_response_publishes_on_reactor_and_stops_timeout(self):
        self.start()
        completion = self.module.completion
        self.module._handle_state({'state': 3, 'error': 0})
        self.assertEqual(self.module.state, 'initializing')
        self.reactor.register_async_callback.call_args[0][0](11.)
        self.assertEqual(self.module.get_status(11.),
                         {'state': 'ready', 'error': None,
                          'rendered': False, 'job_filename': None,
                          'screen': 'blank'})
        self.assertTrue(completion.value)
        self.assertIsNone(self.module.completion)
        self.reactor.update_timer.assert_called_with(self.module.watchdog,
                                                     self.reactor.NEVER)

    def test_error_timeout_and_retry(self):
        self.start()
        with self.assertLogs(level='WARNING'):
            self.response(4, 1)
        self.assertEqual(self.module.state, 'error')
        self.module._start()
        completion = self.module.completion
        with self.assertLogs(level='WARNING'):
            self.module._timeout(99.)
        self.assertFalse(completion.value)
        self.assertEqual(self.module.state, 'error')
        self.assertEqual(self.mcu.start.send.call_count, 2)

    def test_print_and_pause_guards(self):
        self.module.connected = True
        for state in ('printing', 'paused'):
            self.stats.get_status.return_value = {'state': state}
            with self.assertRaisesRegex(RuntimeError, 'idle printer'):
                self.module._start()
        self.mcu.start.send.assert_not_called()

    def test_inflight_clear_and_preprint_wait(self):
        self.start()
        completion = self.module.completion
        completion.wait.side_effect = lambda: self.response(3)
        self.module._handle_before_print('cube.gcode')
        completion.wait.assert_called_once()
        gcmd = mock.Mock()
        gcmd.get_int.return_value = 0x1234
        self.module.cmd_TJC_LCD_CLEAR(gcmd)
        self.mcu.clear.send.assert_called_once_with([7, 0x1234])
        with self.assertRaisesRegex(RuntimeError, 'already in progress'):
            self.module._start()

    def test_preprint_refuses_failed_lcd(self):
        self.start()
        with self.assertLogs(level='WARNING'):
            self.response(4, 2)
        with self.assertRaisesRegex(RuntimeError, 'not ready'):
            self.module._handle_before_print('cube.gcode')

    def test_disconnect_discards_queued_responses_and_wakes_waiter(self):
        self.start()
        completion = self.module.completion
        self.module._handle_state({'state': 3, 'error': 0})
        callback = self.reactor.register_async_callback.call_args[0][0]
        self.module._handle_disconnect()
        callback(11.)
        self.assertFalse(completion.value)
        self.assertEqual(self.module.state, 'disconnected')

    def test_status_reads_do_not_send_and_automatic_failure_is_nonfatal(self):
        self.module.get_status(0.)
        self.mcu.start.send.assert_not_called()
        self.mcu.start.send.side_effect = OSError('lost transport')
        with self.assertLogs(level='WARNING'):
            self.start()
        self.assertEqual(self.module.state, 'error')

    def test_debug_output_skips_hardware_requests_and_preprint_gate(self):
        self.printer.get_start_args.return_value = {'debugoutput': 'test.bin'}
        self.start()
        self.module._handle_before_print('cube.gcode')
        self.mcu.start.send.assert_not_called()

    def test_clear_requires_ready_and_rejects_printing(self):
        gcmd = mock.Mock(error=RuntimeError)
        self.module.connected = True
        with self.assertRaisesRegex(RuntimeError, 'Initialize'):
            self.module.cmd_TJC_LCD_CLEAR(gcmd)
        self.module.state = 'ready'
        self.stats.get_status.return_value = {'state': 'printing'}
        with self.assertRaisesRegex(RuntimeError, 'idle printer'):
            self.module.cmd_TJC_LCD_CLEAR(gcmd)
        self.mcu.clear.send.assert_not_called()

    def test_preprint_can_start_before_deferred_ready_callback(self):
        self.module._handle_ready()
        callback = self.reactor.register_callback.call_args[0][0]
        self.module._start()
        callback(10.)
        self.mcu.start.send.assert_called_once()
        self.assertEqual(self.module.state, 'initializing')


STUBS = {
    'basecmd.h': '''
#include <stdint.h>
void *oid_alloc(uint8_t, void *, uint16_t);
void *oid_lookup(uint8_t, void *);
''',
    'command.h': '''
#define DECL_CONSTANT(...)
#define DECL_CONSTANT_STR(...)
#define DECL_COMMAND(...)
#define shutdown(message) abort()
#define sendf test_sendf
void test_sendf(const char *, ...);
''',
    'sched.h': '''
#include <stdint.h>
struct timer { struct timer *next;
    uint_fast8_t (*func)(struct timer *); uint32_t waketime; };
struct task_wake { uint8_t wake; };
#define SF_RESCHEDULE 1
#define DECL_TASK(...)
#define DECL_SHUTDOWN(...)
void sched_add_timer(struct timer *);
void sched_del_timer(struct timer *);
void sched_wake_task(struct task_wake *);
uint8_t sched_check_wake(struct task_wake *);
''',
    'board/misc.h': '''
#include <stdint.h>
uint32_t timer_read_time(void);
#define timer_from_us(value) ((uint32_t)(value))
#define timer_is_before(a, b) ((int32_t)((a) - (b)) < 0)
''',
    'autoconf.h': '',
    'board/irq.h': '''
typedef unsigned long irqstatus_t;
irqstatus_t irq_save(void);
void irq_restore(irqstatus_t);
''',
    'board/armcm_boot.h': '''
void armcm_enable_irq(void (*handler)(void), int number, int priority);
''',
    'internal.h': '''
#include <stdint.h>
struct usart_regs { uint32_t SR, DR, BRR, CR1, CR2, CR3; };
extern struct usart_regs fake_usart;
#define USART2 (&fake_usart)
#define USART2_IRQn 38
#define USART_CR1_UE (1 << 13)
#define USART_CR1_RE (1 << 2)
#define USART_CR1_TE (1 << 3)
#define USART_CR1_RXNEIE (1 << 5)
#define USART_CR1_TXEIE (1 << 7)
#define USART_SR_RXNE (1 << 5)
#define USART_SR_TXE (1 << 7)
#define USART_SR_TC (1 << 6)
#define USART_SR_ORE (1 << 3)
#define USART_SR_FE (1 << 1)
#define USART_SR_NE (1 << 2)
#define GPIO(port, pin) (((port) - 'A') * 16 + (pin))
#define GPIO_FUNCTION(mode) (2 | ((mode) << 4))
#define DIV_ROUND_CLOSEST(a, b) (((a) + (b) / 2) / (b))
void enable_pclock(uint32_t);
int is_enabled_pclock(uint32_t);
uint32_t get_pclock_frequency(uint32_t);
void gpio_peripheral(uint32_t, uint32_t, int);
'''
}


class FirmwareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which('gcc') is None:
            raise unittest.SkipTest('Native GCC is required for MCU mocks')
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        for name, content in STUBS.items():
            path = os.path.join(cls.tmp.name, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w') as f:
                f.write(content)
        for name in ('tjc_lcd.c', 'tjc_uart.h', 'tjc_lcd.h'):
            shutil.copyfile(os.path.join(ROOT, 'src', name),
                            os.path.join(cls.tmp.name, name))
        shutil.copyfile(os.path.join(ROOT, 'test', 'unit',
                                     'tjc_lcd_harness.c'),
                        os.path.join(cls.tmp.name, 'harness.c'))
        cls.binary = os.path.join(cls.tmp.name, 'tjc-test')
        subprocess.run(['gcc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                        '-Wno-unused-parameter', '-I', cls.tmp.name,
                        os.path.join(cls.tmp.name, 'harness.c'),
                        '-o', cls.binary], check=True, capture_output=True)
        shutil.copyfile(os.path.join(ROOT, 'src', 'stm32', 'tjc_uart.c'),
                        os.path.join(cls.tmp.name, 'tjc_uart.c'))
        shutil.copyfile(os.path.join(ROOT, 'test', 'unit',
                                     'tjc_uart_harness.c'),
                        os.path.join(cls.tmp.name, 'uart_harness.c'))
        cls.uart_binary = os.path.join(cls.tmp.name, 'uart-test')
        subprocess.run(['gcc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                        '-Wno-unused-parameter', '-Wno-pointer-to-int-cast',
                        '-I', cls.tmp.name,
                        os.path.join(cls.tmp.name, 'uart_harness.c'),
                        '-o', cls.uart_binary], check=True, capture_output=True)

    def test_uart_tx_completion_rx_overflow_and_restart(self):
        subprocess.run([self.uart_binary], check=True, capture_output=True)

    def test_mcu_protocol_and_lifecycle(self):
        for scenario in ('success', 'rollover', 'timeout', 'partial', 'retry',
                         'uart_error', 'shutdown', 'tx_stall',
                         'oversized_reply', 'manual_clear'):
            with self.subTest(scenario=scenario):
                subprocess.run([self.binary, scenario], check=True,
                                capture_output=True)


class BuildConfigTests(unittest.TestCase):
    def test_opt_in_and_usart2_ownership(self):
        sys.path.insert(0, os.path.join(ROOT, 'lib', 'kconfiglib'))
        import kconfiglib
        sys.path.pop(0)
        with mock.patch.dict(os.environ, {'srctree': ROOT}):
            kconfig = kconfiglib.Kconfig(os.path.join(ROOT, 'src', 'Kconfig'),
                                        warn=False)
            symbols = kconfig.syms
            symbols['MACH_STM32'].set_value('y')
            symbols['LOW_LEVEL_OPTIONS'].set_value('y')
            symbols['MACH_STM32F103'].set_value('y')
            symbols['STM32_SERIAL_USART1'].set_value('y')
            self.assertEqual(symbols['TJC_LCD'].str_value, 'n')
            symbols['TJC_LCD'].set_value('y')
            self.assertEqual(symbols['TJC_LCD'].str_value, 'y')
            for port in ('STM32_SERIAL_USART2',
                         'STM32_SERIAL_USART2_ALT_PD6_PD5'):
                symbols[port].set_value('y')
                self.assertEqual(symbols['TJC_LCD'].str_value, 'n')
            symbols['STM32_USB_PA11_PA12'].set_value('y')
            self.assertEqual(symbols['TJC_LCD'].str_value, 'y')
            symbols['MACH_STM32F103x6'].set_value('y')
            self.assertEqual(symbols['TJC_LCD'].str_value, 'n')


if __name__ == '__main__':
    unittest.main()
