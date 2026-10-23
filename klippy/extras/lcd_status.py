# Static job preparation and integration with the mainboard LCD renderer
#
# Copyright (C) 2026 navaismo
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import copy, json, logging, math, posixpath
try:
    from . import lcd_thumbnail, lcd_transfer
except ImportError:
    import lcd_thumbnail, lcd_transfer
try:
    from http.client import HTTPConnection, HTTPSConnection, HTTPException
    from urllib.parse import quote, urlencode, urlsplit
except ImportError:
    from httplib import HTTPConnection, HTTPSConnection, HTTPException
    from urllib import quote, urlencode
    from urlparse import urlsplit

try:
    string_types = (basestring,)
except NameError:
    string_types = (str,)

MAX_METADATA_BYTES = 256 * 1024
MAX_THUMBNAIL_BYTES = 2 * 1024 * 1024
THUMBNAIL_SIZE = 96


class DataError(Exception):
    pass


def relative_path(value):
    if not isinstance(value, string_types):
        raise DataError("Expected a relative path within the gcodes root")
    if isinstance(value, bytes):
        try:
            value = value.decode('utf-8')
        except UnicodeError:
            raise DataError("Expected a UTF-8 file path")
    if (not value or value.startswith('/') or '\\' in value
        or '..' in value.split('/')
        or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise DataError("Expected a relative path within the gcodes root")
    value = posixpath.normpath(value)
    if value == '.':
        raise DataError("Expected a file path")
    return value


def positive_number(value, integer=False):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if math.isnan(number) or math.isinf(number) or number <= 0.:
        return None
    if integer:
        if number != int(number) or number > 2147483647:
            return None
        return int(number)
    return number


def metadata_text(value):
    if not isinstance(value, string_types):
        return None
    value = ''.join(c for c in value if ord(c) >= 32 and ord(c) != 127)
    return value.strip()[:128] or None


def target_temperature(value):
    # File metadata only: zero is a valid disabled heater, never a live reading.
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if math.isnan(value) or math.isinf(value) or not 0. <= value <= 500.:
        return None
    return value


def select_thumbnail(filename, thumbnails):
    # Moonraker paths here are relative to the G-code's parent directory.
    if not isinstance(thumbnails, list):
        return None
    candidates = []
    for thumb in thumbnails:
        if not isinstance(thumb, dict):
            continue
        width = positive_number(thumb.get('width'), integer=True)
        height = positive_number(thumb.get('height'), integer=True)
        size = positive_number(thumb.get('size'), integer=True)
        if (width is None or height is None or max(width, height) > 4096
            or width*height > lcd_thumbnail.MAX_SOURCE_PIXELS
            or (size is not None and size > MAX_THUMBNAIL_BYTES)):
            continue
        try:
            path = relative_path(thumb.get('relative_path'))
        except DataError:
            continue
        ext = posixpath.splitext(path)[1].lower()
        if ext not in ('.png', '.jpg', '.jpeg'):
            continue
        path = posixpath.join(posixpath.dirname(filename), path)
        rank = lcd_thumbnail.source_rank(width, height)
        candidates.append((rank, path, {
            'path': path, 'width': width, 'height': height,
            'format': 'png' if ext == '.png' else 'jpeg',
        }))
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item[0], item[1]))[2]


def prepare_metadata(filename, metadata):
    filename = relative_path(filename)
    if not isinstance(metadata, dict):
        raise DataError("Moonraker metadata must be an object")
    reported_filename = metadata.get('filename')
    if (reported_filename is not None
        and reported_filename != filename):
        raise DataError("Moonraker returned metadata for a different file")
    filament_type = metadata_text(metadata.get('filament_type'))
    filament_name = metadata_text(metadata.get('filament_name'))
    return {
        'filename': filename,
        'total_layers': positive_number(metadata.get('layer_count'), True),
        'layer_height': positive_number(metadata.get('layer_height')),
        'estimated_time': positive_number(metadata.get('estimated_time')),
        'material': filament_type or filament_name,
        'filament_name': filament_name,
        'nozzle_target': target_temperature(
            metadata.get('first_layer_extr_temp')),
        'bed_target': target_temperature(metadata.get('first_layer_bed_temp')),
        'thumbnail': select_thumbnail(filename, metadata.get('thumbnails')),
        'transfer': None,
        'warnings': [],
    }


def attach_thumbnail(job, data):
    thumb = job['thumbnail']
    if len(data) > MAX_THUMBNAIL_BYTES:
        raise DataError("Thumbnail exceeds the download size limit")
    signature = (b'\x89PNG\r\n\x1a\n' if thumb['format'] == 'png'
                 else b'\xff\xd8\xff')
    if not data.startswith(signature):
        raise DataError("Thumbnail does not match its PNG/JPEG format")
    try:
        image = lcd_thumbnail.convert_image(data, thumb['format'])
    except lcd_thumbnail.ThumbnailError as e:
        raise DataError(str(e))
    if (image['source_width'], image['source_height']) != (
            thumb['width'], thumb['height']):
        raise DataError("Thumbnail dimensions differ from metadata")
    thumb['data'] = data
    thumb['size'] = len(data)
    thumb['rgb565'] = image


def attach_gcode_thumbnail(job, data):
    try:
        thumb = lcd_thumbnail.extract_gcode_thumbnail(data)
    except lcd_thumbnail.ThumbnailError as e:
        raise DataError(str(e))
    job['thumbnail'] = thumb
    attach_thumbnail(job, thumb['data'])


def finalize_job(job):
    job['transfer'] = None
    job['display'] = None
    job['estimated_time_formatted'] = None
    try:
        job['transfer'] = lcd_transfer.prepare_transfer(job)
    except lcd_transfer.TransferError as e:
        raise DataError(str(e))
    return job


def job_summary(job):
    if job is None:
        return None
    result = copy.deepcopy(job)
    if result['thumbnail'] is not None:
        result['thumbnail'].pop('data', None)
        if 'rgb565' in result['thumbnail']:
            result['thumbnail']['rgb565'].pop('data', None)
    if result.get('transfer') is not None:
        result['transfer'].pop('data', None)
    return result


class MoonrakerData:
    def __init__(self, url='http://127.0.0.1:7125', timeout=3., api_key=''):
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            raise DataError("Invalid Moonraker URL")
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment):
            raise DataError("Expected an HTTP(S) Moonraker base URL")
        self.connection_type = (HTTPSConnection if parsed.scheme == 'https'
                                else HTTPConnection)
        self.host = parsed.hostname
        self.port = port
        self.prefix = parsed.path.rstrip('/')
        self.timeout = timeout
        self.api_key = api_key

    def _get(self, path, limit):
        connection = self.connection_type(self.host, self.port,
                                          timeout=self.timeout)
        try:
            headers = {'Connection': 'close'}
            if self.api_key:
                headers['X-Api-Key'] = self.api_key
            connection.request('GET', self.prefix + path, headers=headers)
            response = connection.getresponse()
            if response.status != 200:
                raise DataError("Moonraker GET failed (HTTP %d)"
                                % (response.status,))
            data = response.read(limit + 1)
            if len(data) > limit:
                raise DataError("Moonraker response exceeds the size limit")
            return data
        except (IOError, OSError, HTTPException, ValueError):
            # Do not expose authentication headers or raw server responses.
            raise DataError("Unable to read from Moonraker")
        finally:
            connection.close()

    def prepare(self, filename):
        filename = relative_path(filename)
        query = urlencode({'filename': filename.encode('utf-8')})
        raw = self._get('/server/files/metadata?' + query, MAX_METADATA_BYTES)
        try:
            response = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeError):
            raise DataError("Moonraker returned invalid metadata JSON")
        if not isinstance(response, dict) or 'error' in response:
            raise DataError("Moonraker returned an invalid metadata response")
        job = prepare_metadata(filename, response.get('result'))
        if job['thumbnail'] is not None:
            path = quote(job['thumbnail']['path'].encode('utf-8'), safe='/')
            try:
                data = self._get('/server/files/gcodes/' + path,
                                 MAX_THUMBNAIL_BYTES)
                attach_thumbnail(job, data)
            except DataError as e:
                job['warnings'].append(str(e))
                job['thumbnail'] = None
        else:
            job['warnings'].append("No supported PNG/JPEG thumbnail")
        return finalize_job(job)


class LCDStatus:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.auto_prepare = config.getboolean('auto_prepare', True)
        try:
            self.source = MoonrakerData(
                config.get('moonraker_url', 'http://127.0.0.1:7125'),
                config.getfloat('request_timeout', 3., above=0., maxval=30.),
                config.get('moonraker_api_key', ''))
        except DataError as e:
            raise config.error(str(e))
        aio = self.printer.load_object(config, 'aio_executor')
        self.executor = aio.allocate_executor('lcd_metadata')
        self.job = None
        self.state = 'empty'
        self.error = None
        self.connected = False
        self.generation = 0
        self.printer.register_event_handler('klippy:ready', self._handle_ready)
        self.printer.register_event_handler('klippy:disconnect',
                                            self._handle_disconnect)
        self.printer.register_event_handler('klippy:shutdown',
                                            self._handle_disconnect)
        if self.auto_prepare:
            self.printer.register_event_handler('virtual_sdcard:before_print',
                                                self._handle_before_print)
            self.printer.register_event_handler('virtual_sdcard:reset_file',
                                                self._clear_job)
        self.gcode = self.printer.lookup_object('gcode')
        self.gcode.register_command('LCD_PREPARE', self.cmd_LCD_PREPARE,
                                    desc=self.cmd_LCD_PREPARE_help)
        self.gcode.register_command('LCD_RENDER', self.cmd_LCD_RENDER,
                                    desc=self.cmd_LCD_RENDER_help)

    def _handle_ready(self):
        self.connected = True

    def _handle_disconnect(self):
        self.connected = False
        self._clear_job()

    def _clear_job(self):
        self.generation += 1
        self.job = None
        self.state = 'empty'
        self.error = None

    def _handle_before_print(self, filename):
        logging.info("LCD preparing static job data: %s", filename)
        try:
            job = self.prepare_job(filename)
            for warning in job['warnings']:
                logging.warning('LCD job warning (%s): %s', filename, warning)
            display = self.printer.lookup_object('tjc_lcd', None)
            if display is not None:
                display.render_job(job)
        except Exception as e:
            logging.exception("LCD pre-print preparation failed")
            raise self.gcode.error("LCD preparation failed before print: %s"
                                   % (e,))
        logging.info("LCD static job data prepared: %s", filename)
        return job

    cmd_LCD_RENDER_help = 'Prepare and render a static LCD card while idle'
    def cmd_LCD_RENDER(self, gcmd):
        if self.printer.lookup_object('tjc_lcd', None) is None:
            raise gcmd.error('LCD_RENDER requires [tjc_lcd]')
        job = self._handle_before_print(gcmd.get('FILENAME'))
        if job['thumbnail'] is None:
            gcmd.respond_info('LCD job card rendered'
                              ' (text only; thumbnail unavailable)')
        else:
            gcmd.respond_info('LCD job card rendered'
                              ' (96x96 thumbnail included)')
        for warning in job['warnings']:
            gcmd.respond_info('LCD warning: %s' % (warning,))

    def _check_idle(self):
        if not self.connected:
            raise DataError("LCD metadata preparation requires ready Klipper")
        sdcard = self.printer.lookup_object('virtual_sdcard', None)
        stats = self.printer.lookup_object('print_stats', None)
        eventtime = self.reactor.monotonic()
        if ((sdcard is not None and sdcard.is_active())
            or (stats is not None and stats.get_status(eventtime)['state']
                in ('printing', 'paused'))):
            raise DataError("Prepare LCD data before starting the print")

    def prepare_job(self, filename):
        self._check_idle()
        if self.state == 'preparing':
            raise DataError("LCD metadata preparation is already in progress")
        # An unsuccessful new request must not leave the previous job usable.
        self._clear_job()
        generation = self.generation
        self.state = 'preparing'
        try:
            job = self.executor.submit(self.source.prepare, filename)
            self._check_idle()
            if generation != self.generation:
                raise DataError("LCD preparation invalidated by a job reset")
        except Exception as e:
            if self.connected and generation == self.generation:
                self.state = 'error'
                self.error = str(e)
            raise
        self.job = job
        self.state = 'prepared'
        return self.get_job_data()

    def get_job_data(self):
        # Future C-driver integration consumes this data, never status polling.
        return copy.deepcopy(self.job)

    def get_transfer_chunks(self):
        self._check_idle()
        if self.job is None or self.job.get('transfer') is None:
            raise DataError("Prepare LCD job data before requesting chunks")
        transfer = self.job['transfer']
        def chunks():
            for offset, data in lcd_transfer.iter_chunks(transfer):
                self._check_idle()
                if self.job is None or self.job['transfer'] is not transfer:
                    raise DataError("LCD job changed during chunk preparation")
                yield offset, data
        return chunks()

    def get_status(self, eventtime):
        return {'state': self.state, 'error': self.error,
                'job': job_summary(self.job)}

    cmd_LCD_PREPARE_help = 'Prepare static LCD job data without sending to MCU'
    def cmd_LCD_PREPARE(self, gcmd):
        try:
            self.prepare_job(gcmd.get('FILENAME'))
        except DataError as e:
            raise gcmd.error(str(e))
        gcmd.respond_info(json.dumps(job_summary(self.job), ensure_ascii=True))


def load_config(config):
    return LCDStatus(config)
