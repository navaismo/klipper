# Load Cell Probe
#
# Copyright (C) 2025  Gareth Farrington <gareth@waves.ky>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import json
import logging
import math, sys
import mathutil

from . import hx71x
from . import ads1220
from . import cs1237
from . import ads131m0x
from . import probe, manual_probe, trigger_analog, load_cell
from . import load_cell_diagnostics

# MCU SOS filter scaled to "fractional grams" for consistent sensor precision
FRAC_GRAMS_CONV = 32768.0

# Minimum ascent samples needed for piecewise least-squares fit
FIT_MIN_POINTS = 3

# Time window for collecting ascent data in seconds
ASCENT_DATA_WINDOW_SECONDS = 0.3


class TapAnalysis:
    def __init__(self, samples):
        self.time = [float(s[0]) for s in samples]
        self.force = [float(s[1]) for s in samples]

    # convert to dictionary for JSON encoder
    def to_dict(self):
        return {
            'time': self.time, 'force': self.force,
            'is_valid': True,
        }


# Access a parameter from config or GCode command via a consistent interface
# stores name and constraints to keep things DRY
class ParamHelper:
    def __init__(self, config, name, type_name, default=None, minval=None,
            maxval=None, above=None, below=None, max_len=None):
        self._config_section = config.get_name()
        self._config_error = config.error
        self.name = name
        self._type_name = type_name
        self.value = default
        self.minval = minval
        self.maxval = maxval
        self.above = above
        self.below = below
        self.max_len = max_len
        # read from config once
        self.value = self.get(config=config)

    def _get_name(self, gcmd):
        return self.name.upper() if gcmd else self.name

    def _validate_float(self, description, error, value, above, below):
        above = above or self.above
        if above is not None and value <= above:
            raise error("%s must be above %s" % (description, above))
        below = below or self.below
        if below is not None and value >= below:
            raise error("%s must be below %s" % (description, below))

    # support for validating individual options in a list of floats
    def _validate_float_list(self, gcmd, values, above, below):
        if gcmd:
            description = ("Error on '%s': %s" % (
                gcmd.get_commandline(), self._get_name(gcmd)))
            error = gcmd.error
        else:
            description = ("Option '%s' in section '%s'" % (
                self._get_name(gcmd), self._config_section))
            error = self._config_error
        if self.max_len is not None and len(values) > self.max_len:
            raise error(
                "%s has maximum length %s" % (description, self.max_len))
        for value in values:
            self._validate_float(description, error, value, above, below)

    def _get_int(self, config, gcmd, minval, maxval):
        get = gcmd.get_int if gcmd else config.getint
        return get(self._get_name(gcmd), self.value, minval or self.minval,
            maxval or self.maxval)

    def _get_float(self, config, gcmd, minval, maxval, above, below):
        get = gcmd.get_float if gcmd else config.getfloat
        return get(self._get_name(gcmd), self.value, minval or self.minval,
            maxval or self.maxval, above or self.above, below or self.below)

    def _get_float_list(self, config, gcmd, above, below):
        # this code defaults to the empty list, never return None
        default = (self.value or [])
        if gcmd:
            # if the parameter isn't part of the command, return the default
            if not self._get_name(gcmd) in gcmd.get_command_parameters():
                return default
            # parameter exists, always prefer whatever is in the command
            value = gcmd.get(self._get_name(gcmd), default='')
            # Return an empty list for empty value
            if len(value.strip()) == 0:
                return []
            try:
                float_list = [float(p.strip()) for p in value.split(',')]
            except:
                raise gcmd.error("Error on '%s': unable to parse %s" % (
                    gcmd.get_commandline(), value))
        else:
            float_list = config.getfloatlist(self._get_name(gcmd),
                default=default)
        if float_list:
            self._validate_float_list(gcmd, float_list, above, below)
        return float_list

    def get(self, gcmd=None, minval=None, maxval=None, above=None, below=None,
            config=None):
        if config is None and gcmd is None:
            return self.value
        if self._type_name == 'int':
            return self._get_int(config, gcmd, minval, maxval)
        elif self._type_name == 'float':
            return self._get_float(config, gcmd, minval, maxval, above, below)
        else:
            return self._get_float_list(config, gcmd, above, below)


def intParamHelper(config, name, default=None, minval=None, maxval=None):
    return ParamHelper(config, name, 'int', default, minval=minval,
        maxval=maxval)


def floatParamHelper(config, name, default=None, minval=None, maxval=None,
        above=None, below=None):
    return ParamHelper(config, name, 'float', default, minval=minval,
        maxval=maxval, above=above, below=below)


def floatListParamHelper(config, name, default=None, above=None, below=None,
        max_len=None):
    return ParamHelper(config, name, 'float_list', default, above=above,
        below=below, max_len=max_len)


# container for filter parameters
# allows different filter configurations to be compared
class ContinuousTareFilter:
    def __init__(self, sps=None, drift=None, drift_delay=None, buzz=None,
            buzz_delay=None, notches=None, notch_quality=None):
        self.sps = sps
        self.drift = drift
        self.drift_delay = drift_delay
        self.buzz = buzz
        self.buzz_delay = buzz_delay
        self.notches = notches
        self.notch_quality = notch_quality

    def __eq__(self, other):
        if not isinstance(other, ContinuousTareFilter):
            return False
        return (
                self.sps == other.sps and self.drift == other.drift and
                self.drift_delay == other.drift_delay and self.buzz ==
                other.buzz and self.buzz_delay == other.buzz_delay and
                self.notches == other.notches and self.notch_quality ==
                other.notch_quality)

    # create a filter design from the parameters
    def design_filter(self, error_func):
        df = trigger_analog.DigitalFilter(self.sps, error_func)
        if self.drift:
            df.add_highpass(self.drift, self.drift_delay)
        if self.buzz:
            df.add_lowpass(self.buzz, self.buzz_delay)
        for notch in self.notches:
            df.add_notch(notch, self.notch_quality)
        return df


# Combine ContinuousTareFilter and SosFilter into an easy-to-use class
class ContinuousTareFilterHelper:
    def __init__(self, config, sensor, sos_filter):
        self._sensor = sensor
        self._sos_filter = sos_filter
        self._sps = self._sensor.get_samples_per_second()
        max_filter_frequency = math.floor(self._sps / 2.)
        # setup filter parameters
        self._drift_param = floatParamHelper(config,
            "drift_filter_cutoff_frequency", default=None, minval=0.1,
            maxval=20.0)
        self._drift_delay_param = intParamHelper(config, "drift_filter_delay",
            default=2, minval=1, maxval=2)
        self._buzz_param = floatParamHelper(config,
            "buzz_filter_cutoff_frequency", default=None,
            above=min(80.0, max_filter_frequency - 1.0),
            below=max_filter_frequency)
        self._buzz_delay_param = intParamHelper(config, "buzz_filter_delay",
            default=2, minval=1, maxval=2)
        self._notches_param = floatListParamHelper(config,
            "notch_filter_frequencies", default=[], above=0.,
            below=max_filter_frequency, max_len=2)
        self._notch_quality_param = floatParamHelper(config,
            "notch_filter_quality", default=2.0, minval=0.5, maxval=6.0)
        # filter design specified in the config file, used for defaults
        self._config_design = ContinuousTareFilter()  # empty filter
        self._config_design = self._build_filter()
        # filter design currently inside the MCU
        self._active_design = self._config_design
        design = self._active_design.design_filter(config.error)
        self._sos_filter.set_filter_design(design)

    def _build_filter(self, gcmd=None):
        drift = self._drift_param.get(gcmd)
        drift_delay = self._drift_delay_param.get(gcmd)
        buzz = self._buzz_param.get(gcmd)
        buzz_delay = self._buzz_delay_param.get(gcmd)
        # notches must be between drift and buzz:
        notches = self._notches_param.get(gcmd, above=drift, below=buzz)
        notch_quality = self._notch_quality_param.get(gcmd)
        return ContinuousTareFilter(self._sps, drift, drift_delay, buzz,
            buzz_delay, notches, notch_quality)

    def update_from_command(self, gcmd, cq=None):
        gcmd_filter = self._build_filter(gcmd)
        # if filters are identical, no change required
        if self._active_design == gcmd_filter:
            return
        # update MCU filter from GCode command
        design = self._active_design.design_filter(gcmd.error)
        self._sos_filter.set_filter_design(design)


# check results from the collector for errors and raise an exception is found
def check_sensor_errors(results, printer):
    samples, errors = results
    if errors:
        raise printer.command_error("Load cell sensor reported errors while"
                                    " probing: %i errors, %i overflows" % (
                                        errors[0], errors[1]))
    return samples


# compute Z position at a given print_time using stepper history
def _lookup_z_pos(toolhead, pos_time):
    kin = toolhead.get_kinematics()
    steppers = kin.get_steppers()
    kin_spos = {s.get_name(): s.mcu_to_commanded_position(
                                s.get_past_mcu_position(pos_time))
                for s in steppers}
    return kin.calc_position(kin_spos)[2]


class LoadCellProbeConfigHelper:
    def __init__(self, config, load_cell_inst):
        self._printer = config.get_printer()
        self._load_cell = load_cell_inst
        self._sensor = load_cell_inst.get_sensor()
        # Collect 4 x 60hz power cycles of data to average across power noise
        self._tare_time_param = floatParamHelper(config, 'tare_time',
            default=4. / 60., minval=0.01, maxval=1.0)
        # triggering options
        self._trigger_force_param = floatParamHelper(config, 'trigger_force',
            default=75, minval=10, maxval=250)
        self._force_safety_limit_param = floatParamHelper(config,
            'force_safety_limit', minval=100, maxval=10000, default=2000)

    def get_tare_samples(self, gcmd=None):
        tare_time = self._tare_time_param.get(gcmd)
        sps = self._sensor.get_samples_per_second()
        return max(2, math.ceil(tare_time * sps))

    def get_trigger_force_grams(self, gcmd=None):
        return self._trigger_force_param.get(gcmd)

    def get_safety_limit_grams(self, gcmd=None):
        return self._force_safety_limit_param.get(gcmd)

    def get_safety_range(self, gcmd=None, reference_tare_counts=None):
        counts_per_gram = self._load_cell.get_counts_per_gram()
        # calculate the safety band
        zero = self._load_cell.get_reference_tare_counts() \
            if reference_tare_counts is None else reference_tare_counts
        safety_counts = int(counts_per_gram * self.get_safety_limit_grams(gcmd))
        safety_min = int(zero - safety_counts)
        safety_max = int(zero + safety_counts)
        # don't allow a safety range outside the sensor's real range
        sensor_min, sensor_max = self._load_cell.get_sensor().get_range()
        if safety_min <= sensor_min or safety_max >= sensor_max:
            cmd_err = self._printer.command_error
            raise cmd_err("Load cell force_safety_limit exceeds sensor range!")
        return safety_min, safety_max

    # calculate 1/counts_per_gram
    def get_grams_per_count(self):
        counts_per_gram = self._load_cell.get_counts_per_gram()
        # The counts_per_gram could be so large that it becomes 0.0 when
        # sent to the mcu. This would mean the ADC range only measures
        # a few grams which seems very unlikely. Treat this as an error:
        if counts_per_gram >= (1<<29):
            raise OverflowError("counts_per_gram value is too large to filter")
        return 1. / counts_per_gram


# Execute probing moves using the MCU_trigger_analog
class LoadCellProbingMove:
    def __init__(self, config, load_cell_inst, mcu_trigger_analog, param_helper,
            continuous_tare_filter_helper, config_helper, log_trace=False):
        self._printer = config.get_printer()
        self._load_cell = load_cell_inst
        self._mcu_trigger_analog = mcu_trigger_analog
        self._param_helper = param_helper
        self._continuous_tare_filter_helper = continuous_tare_filter_helper
        self._config_helper = config_helper
        self._mcu = mcu_trigger_analog.get_mcu()
        self._z_min_position = probe.lookup_minimum_z(config)
        dispatch = mcu_trigger_analog.get_dispatch()
        probe.LookupZSteppers(config, dispatch.add_stepper)
        # internal state tracking
        self._tare_counts = 0
        self._log_trace = log_trace
        self._name = config.get_name()
        self._last_safety = None
        self._safety_reference_counts = None
        self._diagnostic_sequence = 0
        self._diagnostic_motion = None

    def set_safety_reference(self, reference_counts):
        # A caller may anchor a point after retracting into unloaded clearance.
        if math.isnan(reference_counts) or math.isinf(reference_counts):
            raise self._printer.command_error("Invalid load cell point tare")
        reference_counts = int(reference_counts)
        self._config_helper.get_safety_range(
            reference_tare_counts=reference_counts)
        self._safety_reference_counts = reference_counts

    def clear_safety_reference(self):
        self._safety_reference_counts = None

    def _compression_margin(self, tare_counts, safety_min, safety_max):
        counts = safety_max - tare_counts
        if self._load_cell.invert < 0:
            counts = tare_counts - safety_min
        return counts / self._load_cell.get_counts_per_gram()

    def _log_safety(self, tare_counts, safety_min, safety_max, gcmd):
        scale = self._load_cell.get_counts_per_gram()
        reference = self._load_cell.get_reference_tare_counts()
        toolhead = self._printer.lookup_object('toolhead')
        self._last_safety = {
            'name': self._name, 'reference_tare_counts': reference,
            'position': toolhead.get_position()[:3],
            'tare_counts': tare_counts, 'counts_per_gram': scale,
            'safety_reference_counts': reference
                if self._safety_reference_counts is None
                else self._safety_reference_counts,
            'orientation': self._load_cell.invert,
            'raw_min': safety_min, 'raw_max': safety_max,
            'baseline_force': self._load_cell.invert
                * (tare_counts - reference) / scale,
            'margin_to_raw_min_grams': (tare_counts - safety_min) / scale,
            'margin_to_raw_max_grams': (safety_max - tare_counts) / scale,
            'compression_margin_grams': self._compression_margin(
                tare_counts, safety_min, safety_max),
            'trigger_force': self._config_helper.get_trigger_force_grams(gcmd),
            'force_safety_limit':
                self._config_helper.get_safety_limit_grams(gcmd)}
        logging.info("Load cell safety: %s", json.dumps(self._last_safety))

    def _log_probe_failure(self, exc, results):
        samples, errors = results
        raw = [s[2] for s in samples]
        stride = max(1, (len(samples) + 255) // 256)
        selected = samples[::stride]
        if samples and selected[-1] != samples[-1]:
            selected.append(samples[-1])
        report = {'name': self._name, 'error': str(exc),
            'safety': self._last_safety, 'sample_count': len(samples),
            'sensor_errors': errors, 'stride': stride,
            'observed_raw_min': min(raw) if raw else None,
            'observed_raw_max': max(raw) if raw else None,
            'time_counts': [[s[0], s[2]] for s in selected]}
        # Host bulk samples may omit the exact sample which stopped the MCU.
        logging.info("Load cell probe failure: %s", json.dumps(report))
        try:
            motion = getattr(self, '_diagnostic_motion', None)
            if motion is not None:
                toolhead = self._printer.lookup_object('toolhead')
                motion['frames']['after_probe'] = (
                    load_cell_diagnostics.snapshot(self._printer, toolhead))
                load_cell_diagnostics.log_report(samples, motion, toolhead,
                    _lookup_z_pos, None, error=exc)
        except Exception:
            load_cell_diagnostics.warn('probe failure report')

    def _start_collector(self):
        toolhead = self._printer.lookup_object('toolhead')
        # homing uses the toolhead last move time which gets special handling
        # to significantly buffer print_time if the move queue has drained
        print_time = toolhead.get_last_move_time()
        collector = self._load_cell.get_collector()
        collector.start_collecting(min_time=print_time)
        return collector

    # pauses for the last move to complete and then
    # sets the endstop tare value and range
    def _pause_and_tare(self, gcmd):
        self._last_safety = None
        if not self._load_cell.is_calibrated():
            raise self._printer.command_error("Load Cell not calibrated")
        collector = self._start_collector()
        num_samples = self._config_helper.get_tare_samples(gcmd)
        # use collect_min collected samples are not wasted
        try:
            results = collector.collect_min(num_samples)
        finally:
            collector.stop_collecting()
        tare_samples = check_sensor_errors(results, self._printer)
        if len(tare_samples) < num_samples and not self._mcu.is_fileoutput():
            raise self._printer.command_error(
                "Insufficient load cell tare data")
        tare_counts = load_cell.avg([float(s[2]) for s in tare_samples])
        # update sos_filter with any gcode parameter changes
        self._continuous_tare_filter_helper.update_from_command(gcmd)
        # update the load cell so it reflects the new tare value
        # update raw range
        safety_min, safety_max = self._config_helper.get_safety_range(
            gcmd, reference_tare_counts=self._safety_reference_counts)
        if self._log_trace:
            self._log_safety(tare_counts, safety_min, safety_max, gcmd)
        if not safety_min < tare_counts < safety_max:
            raise self._printer.command_error(
                "Load cell tare outside force limit")
        margin = self._compression_margin(tare_counts, safety_min, safety_max)
        trigger = self._config_helper.get_trigger_force_grams(gcmd)
        if margin <= trigger:
            raise self._printer.command_error(
                "Insufficient load cell compression margin: %.2f g available, "
                "%.2f g trigger; force limit unchanged"
                % (margin, trigger))
        self._load_cell.tare(tare_counts)
        self._tare_counts = int(tare_counts)
        self._mcu_trigger_analog.set_raw_range(safety_min, safety_max)
        # update internal tare value
        gpc = self._config_helper.get_grams_per_count() * FRAC_GRAMS_CONV
        sos_filter = self._mcu_trigger_analog.get_sos_filter()
        sos_filter.set_offset_scale(int(-tare_counts), gpc)
        # update trigger
        trigger_val = self._config_helper.get_trigger_force_grams(gcmd)
        trigger_frac_grams = int(trigger_val * FRAC_GRAMS_CONV)
        self._mcu_trigger_analog.set_trigger("abs_ge", trigger_frac_grams)

    # Probe towards z_min until the trigger_analog on the MCU triggers
    def probing_move(self, gcmd):
        # do not permit probing if the load cell is not calibrated
        if not self._load_cell.is_calibrated():
            raise self._printer.command_error("Load Cell not calibrated")
        # tare the sensor just before probing
        self._pause_and_tare(gcmd)
        # get params for the homing move
        toolhead = self._printer.lookup_object('toolhead')
        pos = toolhead.get_position()
        pos[2] = self._z_min_position
        speed = self._param_helper.get_probe_params(gcmd)['probe_speed']
        phoming = self._printer.lookup_object('homing')
        # start collector after tare samples are consumed
        collector = self._start_collector()
        if self._log_trace:
            try:
                self._diagnostic_sequence = getattr(
                    self, '_diagnostic_sequence', 0) + 1
                sensor = self._load_cell.get_sensor()
                self._diagnostic_motion = {
                    'name': self._name, 'tap_id': self._diagnostic_sequence,
                    'collector_start_time': load_cell_diagnostics.number(
                        collector.min_time),
                    'target': list(pos[:3]), 'speed': speed,
                    'sample_period': 1. / sensor.get_samples_per_second(),
                    'safety': self._last_safety,
                    'frames': {'before_probe': load_cell_diagnostics.snapshot(
                        self._printer, toolhead)}}
            except Exception:
                self._diagnostic_motion = None
                load_cell_diagnostics.warn('probe start')
        # do homing move
        try:
            epos = phoming.probing_move(self._mcu_trigger_analog, pos, speed)
        except Exception as exc:
            try:
                results = collector.stop_collecting()
                if self._log_trace:
                    self._log_probe_failure(exc, results)
            except Exception:
                load_cell_diagnostics.warn('failure data collection')
            raise
        if self._log_trace:
            try:
                motion = getattr(self, '_diagnostic_motion', None)
                if isinstance(motion, dict):
                    motion.update({'trigger_time':
                        self._mcu_trigger_analog.get_last_trigger_time(),
                        'raw_z': epos[2]})
                    motion['frames']['after_probe'] = (
                        load_cell_diagnostics.snapshot(self._printer, toolhead))
            except Exception:
                load_cell_diagnostics.warn('probe end')
        return epos, collector

    # Wait for the MCU to trigger with no movement
    def probing_test(self, gcmd, timeout):
        self._pause_and_tare(gcmd)
        toolhead = self._printer.lookup_object('toolhead')
        print_time = toolhead.get_last_move_time()
        self._mcu_trigger_analog.home_start(print_time, 0., 0, 0.)
        return self._mcu_trigger_analog.home_wait(print_time + timeout)

    def get_status(self, eventtime):
        trig_time = self._mcu_trigger_analog.get_last_trigger_time()
        return {
            'tare_counts': self._tare_counts,
            'last_trigger_time': trig_time,
        }


# Perform a single complete tap
class TappingMove:
    def __init__(self, config, load_cell_probing_move, config_helper,
                 ascent_sample_time=ASCENT_DATA_WINDOW_SECONDS,
                 log_trace=False):
        self._printer = config.get_printer()
        self._load_cell_probing_move = load_cell_probing_move
        self._config_helper = config_helper
        self._ascent_sample_time = ascent_sample_time
        self._log_trace = log_trace
        self._name = config.get_name()
        # track results of the last tap
        self._last_result = None
        self._last_trigger_z = None
        self._is_last_result_valid = False
        self._collector = None
        # webhooks support
        self._clients = load_cell.ApiClientHelper(config.get_printer())
        name = config.get_name()
        header = {"header": ["probe_tap_event"]}
        self._clients.add_mux_endpoint("load_cell_probe/dump_taps",
            "load_cell_probe", name, header)
        self._best_fit = LCBestFit(self._printer)

    def run_tap(self, gcmd):
        self._is_last_result_valid = False
        self._last_result = None
        self._last_trigger_z = None
        try:
            return self._run_tap(gcmd)
        finally:
            if self._collector is not None:
                self._collector.stop_collecting()
                self._collector = None

    def _run_tap(self, gcmd):
        # do the descending move
        epos, collector = self._load_cell_probing_move.probing_move(gcmd)
        self._collector = collector
        self._last_trigger_z = epos[2]
        # collect samples from the tap
        toolhead = self._printer.lookup_object('toolhead')
        # Homing workaround
        phoming = self._printer.lookup_object('homing')
        first_home = phoming.check_probe_first_home(gcmd)
        if first_home:
            curpos = toolhead.get_position()
            curpos[2] -= epos[2]
            toolhead.set_position(curpos)
        if self._log_trace:
            try:
                motion = getattr(self._load_cell_probing_move,
                                 '_diagnostic_motion', None)
                if isinstance(motion, dict):
                    motion['homing_workaround'] = bool(first_home)
                    motion['frames']['after_workaround'] = (
                        load_cell_diagnostics.snapshot(self._printer, toolhead))
            except Exception:
                load_cell_diagnostics.warn('homing reference')

        # Lift the toolhead while collecting the samples we will use for
        # the fit. The ascent data shall cover both the contact region
        # (force still applied) and free-air region (no force = tare).
        ascent_start_time = toolhead.get_last_move_time()

        # load_cell_retract_dist is mapped to sample_retract_dist in
        # LoadCellParameterHelper
        params = \
            self._load_cell_probing_move._param_helper.get_probe_params(gcmd)
        lift_dist = params['load_cell_retract_dist']
        lift_pos = toolhead.get_position()
        lift_pos[2] += lift_dist
        toolhead.manual_move(lift_pos, params['lift_speed'])

        # Collect samples until the end of the ascent
        move_end = toolhead.get_last_move_time()
        results = collector.collect_until(move_end)
        corrected_z, fit_error = None, None
        try:
            samples = check_sensor_errors(results, self._printer)
            # Perform fit on the ascent data
            corrected_z = self._analyze_ascent(gcmd, samples, ascent_start_time,
                                                toolhead, epos[2])
        except Exception as exc:
            fit_error = exc
            raise
        finally:
            if self._log_trace:
                try:
                    motion = getattr(self._load_cell_probing_move,
                                     '_diagnostic_motion', None)
                    if isinstance(motion, dict):
                        motion.update({'ascent_start_time': ascent_start_time,
                            'ascent_end_time': move_end,
                            'ascent_sample_time': self._ascent_sample_time,
                            'lift_speed': params['lift_speed'],
                            'retract_distance': lift_dist,
                            'sensor_errors': results[1]})
                        motion['frames']['after_ascent'] = (
                            load_cell_diagnostics.snapshot(
                                self._printer, toolhead))
                        load_cell_diagnostics.log_report(results[0], motion,
                            toolhead, _lookup_z_pos,
                            self._best_fit.find_best_fit,
                            corrected_z, fit_error)
                except Exception:
                    load_cell_diagnostics.warn('ascent report')
        # Replace the probe result with the fitted Z position
        epos[2] = corrected_z

        # Analyze the tap data
        ppa = TapAnalysis(samples)
        # broadcast tap event data:
        self._clients.send({'tap': ppa.to_dict()})

        self._is_last_result_valid = True
        self._last_result = epos[2]
        return epos, self._is_last_result_valid

    def get_status(self, eventtime):
        return {
            'last_z_result': self._last_result,
            'last_trigger_z': self._last_trigger_z,
            'is_last_tap_valid': self._is_last_result_valid
        }

    def _analyze_ascent(self, gcmd, all_samples, ascent_start_time, toolhead,
                        raw_z):
        # Bound the ascent fit window to limit baseline drift. Slow contact
        # release can require a longer window to include unloaded samples.
        data = []
        sample_times = []
        for s in all_samples:
            if s[0] >= ascent_start_time and \
               s[0] <= ascent_start_time + self._ascent_sample_time:
                data.append((s[1], _lookup_z_pos(toolhead, s[0])))
                sample_times.append(s[0])

        if self._load_cell_probing_move._mcu.is_fileoutput():
            # In debugging mode: inject dummy data
            data = [(0.0, 0.0), (10.0, 0.1), (20.0, 0.2), (25.0, 0.3),
                    (25.0, 0.4), (25.0, 0.5)]

        if self._log_trace:
            # Keep the complete 80 SPS trace; bound logs for faster sensors.
            stride = max(1, (len(data) + 4094) // 4095)
            trace = data[::stride]
            times = (sample_times[::stride]
                     if len(sample_times) == len(data) else None)
            if data and (len(data) - 1) % stride:
                trace.append(data[-1])
                if times is not None:
                    times.append(sample_times[-1])
            logging.info("Load cell ascent: %s", json.dumps({
                'name': self._name, 'raw_z': raw_z,
                'start_time': ascent_start_time,
                'sample_count': len(data), 'stride': stride,
                'force_z': trace, 'sample_times': times}))

        # Check that we have enough samples early to avoid exceptions
        if len(data) < 2*FIT_MIN_POINTS:
            raise self._printer.command_error(
                "Insufficient ascent samples (%d total, need >= %d "
                "each) for piecewise fit" % (len(data), 2*FIT_MIN_POINTS))

        if any(math.isnan(v) or math.isinf(v) for row in data for v in row):
            raise self._printer.command_error("Non-finite load cell tap data")

        # Perform the actual fit
        z_contact, below_count, above_count, depress_slope = \
            self._best_fit.find_best_fit(data)

        if self._log_trace:
            logging.info("Load cell fit: %s", json.dumps({
                'name': self._name, 'raw_z': raw_z, 'contact_z': z_contact,
                'release_distance': z_contact - raw_z,
                'below_count': below_count, 'above_count': above_count,
                'slope': depress_slope}))

        if (math.isnan(z_contact) or math.isinf(z_contact)
                or math.isnan(depress_slope) or math.isinf(depress_slope)
                or depress_slope >= 0.
                or not data[0][1] < z_contact < data[-1][1]):
            raise self._printer.command_error("Invalid load cell contact fit")

        # We require at least 3 samples on each side of the split point to
        # ensure a good fit and precise tare compensation.
        if below_count < FIT_MIN_POINTS or above_count < FIT_MIN_POINTS:
            raise self._printer.command_error(
                "Insufficient ascent samples (%d below, %d above, need >= %d "
                "each) for piecewise fit" % (below_count, above_count,
                                             FIT_MIN_POINTS))
        if self._load_cell_probing_move._mcu.is_fileoutput():
            # In debugging mode: check fit result
            if abs(z_contact - 0.25) > 0.01:
                raise self._printer.command_error(
                    "Load cell probe fit result incorrect")

        return z_contact


# Given a list of (grams, z) pairs, find the coefficients z_contact,
# grams_contact, depress_slope, slope that best fit the data to the
# formulas `grams = grams_contact + depress_slope*(z-z_contact)` when
# z<=z_contact and `grams = grams_contact` when z>=z_contact. This
# implements a form of non-linear least squares.
class LCBestFit:
    def __init__(self, printer):
        self._printer = printer

    def _calc_least_squares(self, samples, est_z_contact):
        len_samples = len(samples)
        eqs = [[0.] * 2 for i in range(len_samples)]
        ans = [[0.] for i in range(len_samples)]
        for i, (step_z, sensor_grams) in enumerate(samples):
            a = ans[i]
            eq = eqs[i]
            if step_z <= est_z_contact:
                # 1*c0 + (z-ezc)*c1 = grams
                eq[0] = 1.
                eq[1] = step_z - est_z_contact
            else:
                # 1*c0 = grams
                eq[0] = 1.
                eq[1] = 0.
            a[0] = sensor_grams
        eqst = mathutil.mat_transp(eqs)
        eqst_eqs = mathutil.mat_mat_mul(eqst, eqs)
        eqst_ans = mathutil.mat_mat_mul(eqst, ans)
        coeffs = mathutil.gaussian_solve(eqst_eqs, eqst_ans)
        if coeffs is None:
            return sys.float_info.max, [[0.]] * 2
        rel_err = -sum([c[0]*a[0] for c, a in zip(coeffs, eqst_ans)])
        return rel_err, coeffs

    def find_best_fit(self, data):
        # Change base of grams/z measurements to improve numerical stability
        base_z = .5 * (data[0][1] + data[-1][1])
        base_grams = .5 * (data[0][0] + data[-1][0])
        samples = [(d[1] - base_z, d[0] - base_grams) for d in data]

        count = len(samples)
        total_y = sum(y for z, y in samples)
        best = [sys.float_info.max, None, 0.]

        def consider(contact, n, sx, sy, sxx, sxy):
            # Solve the original two-coefficient hinge model from prefix
            # sums. The omitted sum(y*y) is constant for all candidates.
            sh = sx - n * contact
            shh = sxx - 2. * contact * sx + n * contact * contact
            shy = sxy - contact * sy
            variance = shh - sh * sh / count
            if variance <= 0.:
                return
            slope = (shy - sh * total_y / count) / variance
            baseline = (total_y - slope * sh) / count
            error = -(baseline * total_y + slope * shy)
            if error < best[0]:
                best[:] = [error, contact, slope]

        sx = sy = sxx = sxy = 0.
        for index in range(count - 1):
            z, y = samples[index]
            sx += z
            sy += y
            sxx += z * z
            sxy += z * y
            n = index + 1
            upper = samples[index + 1][0]
            if upper < z:
                raise self._printer.command_error(
                    "Non-monotonic load cell ascent positions")
            if upper == z:
                continue
            consider(z, n, sx, sy, sxx, sxy)
            consider(upper, n, sx, sy, sxx, sxy)
            # Within this split, the unconstrained optimum is the
            # intersection of the loaded regression and unloaded mean.
            # An optimum outside the interval lies at one of its ends.
            variance = sxx - sx * sx / n
            if variance <= 0.:
                continue
            slope = (sxy - sx * sy / n) / variance
            if not slope:
                continue
            free_mean = (total_y - sy) / (count - n)
            contact = sx / n + (free_mean - sy / n) / slope
            if z <= contact <= upper:
                consider(contact, n, sx, sy, sxx, sxy)

        if best[1] is None:
            raise self._printer.command_error("Invalid load cell contact fit")
        est_z, depress_slope = best[1:]

        # Count number of samples below the estimated z_contact
        n_below = sum(1 for z, y in samples if z <= est_z)

        return base_z + est_z, n_below, len(samples) - n_below, depress_slope

# ProbeSession that implements Tap logic
class TapSession:
    def __init__(self, config, tapping_move,
                 probe_offsets, probe_params_helper):
        self._printer = config.get_printer()
        self._tapping_move = tapping_move
        self._probe_offsets = probe_offsets
        self._probe_params_helper = probe_params_helper
        # Session state
        self._results = []

    def start_probe_session(self, gcmd):
        return self

    def end_probe_session(self):
        self._results = []

    # probe until a single good sample is returned or retries are exhausted
    def run_probe(self, gcmd):
        epos, is_good = self._tapping_move.run_tap(gcmd)
        offsets = self._probe_offsets.get_offsets()
        res = manual_probe.create_probe_result(epos, offsets)
        self._results.append(res)

    def pull_probed_results(self):
        res = self._results
        self._results = []
        return res


class LoadCellProbeCommands:
    def __init__(self, config, load_cell_probing_move):
        self._printer = config.get_printer()
        self._load_cell_probing_move = load_cell_probing_move
        self._register_commands()

    def _register_commands(self):
        # Register commands
        gcode = self._printer.lookup_object('gcode')
        gcode.register_command("LOAD_CELL_TEST_TAP",
            self.cmd_LOAD_CELL_TEST_TAP, desc=self.cmd_LOAD_CELL_TEST_TAP_help)

    cmd_LOAD_CELL_TEST_TAP_help = "Tap the load cell probe to verify operation"

    def cmd_LOAD_CELL_TEST_TAP(self, gcmd):
        taps = gcmd.get_int("TAPS", 3, minval=1, maxval=10)
        timeout = gcmd.get_float("TIMEOUT", 30., minval=1., maxval=120.)
        gcmd.respond_info("Tap the load cell %s times:" % (taps,))
        reactor = self._printer.get_reactor()
        for i in range(0, taps):
            result = self._load_cell_probing_move.probing_test(gcmd, timeout)
            if result == 0.:
                # notify of error, likely due to timeout
                raise gcmd.error("Test timeout out")
            gcmd.respond_info("Tap Detected!")
            # give the user some time for their finger to move away
            reactor.pause(reactor.monotonic() + 0.2)
        gcmd.respond_info("Test complete, %s taps detected" % (taps,))


class LoadCellParameterHelper:
    def __init__(self, config):
        self._param_helper = probe.ProbeParameterHelper(config)
    def get_probe_params(self, gcmd=None):
        params = self._param_helper.get_probe_params(gcmd)
        # Disable lift in calling code as it is done within tap process
        params['load_cell_retract_dist'] = params['sample_retract_dist']
        params['sample_retract_dist'] = 0.
        return params


class LoadCellTapHelper:
    """Build the contact engine without creating a sensor or primary probe."""
    def __init__(self, config, load_cell_inst,
                 ascent_sample_time=ASCENT_DATA_WINDOW_SECONDS,
                 log_trace=False):
        self.load_cell = load_cell_inst
        sensor = load_cell_inst.get_sensor()
        self.mcu = sensor.get_mcu()
        config_helper = LoadCellProbeConfigHelper(config, load_cell_inst)
        self.trigger = trigger_analog.MCU_trigger_analog(sensor)
        cmd_queue = self.trigger.get_dispatch().get_command_queue()
        sos_filter = trigger_analog.MCU_SosFilter(self.mcu, cmd_queue, 4)
        self.trigger.setup_sos_filter(sos_filter)
        filter_helper = ContinuousTareFilterHelper(config, sensor, sos_filter)
        self.param_helper = LoadCellParameterHelper(config)
        self.probing_move = LoadCellProbingMove(
            config, load_cell_inst, self.trigger, self.param_helper,
            filter_helper, config_helper, log_trace=log_trace)
        self.tapping_move = TappingMove(config, self.probing_move,
                                       config_helper, ascent_sample_time,
                                       log_trace)


class LoadCellPrinterProbe:
    def __init__(self, config):
        self._printer = config.get_printer()
        # Sensor types supported by load_cell_probe
        sensors = {}
        sensors.update(hx71x.HX71X_SENSOR_TYPES)
        sensors.update(ads1220.ADS1220_SENSOR_TYPE)
        sensors.update(cs1237.CS1237_SENSOR_TYPE)
        sensors.update(ads131m0x.ADS131M0X_SENSOR_TYPES)
        sensor_class = config.getchoice('sensor_type', sensors)
        sensor = sensor_class(config)
        self._load_cell = load_cell.LoadCell(config, sensor)
        # Read all user configuration and build modules
        tap_helper = LoadCellTapHelper(config, self._load_cell)
        self._mcu = tap_helper.mcu
        self._mcu_trigger_analog = tap_helper.trigger
        # Probe Interface
        self._param_helper = tap_helper.param_helper
        self._cmd_helper = probe.ProbeCommandHelper(config, self)
        self._probe_offsets = probe.ProbeOffsetsHelper(config)
        load_cell_probing_move = tap_helper.probing_move
        self._tapping_move = tap_helper.tapping_move
        tap_session = TapSession(config, self._tapping_move,
                                 self._probe_offsets, self._param_helper)
        self._probe_session = probe.SampleAveragingHelper(config,
            self._param_helper, tap_session.start_probe_session)
        # printer integration
        LoadCellProbeCommands(config, load_cell_probing_move)
        probe.HomingViaProbeHelper(config, self.get_offsets()[2])
        self._printer.add_object('probe', self)

    def get_probe_params(self, gcmd=None):
        return self._param_helper.get_probe_params(gcmd)

    def get_offsets(self, gcmd=None):
        return self._probe_offsets.get_offsets(gcmd)

    def start_probe_session(self, gcmd):
        return self._probe_session.start_probe_session(gcmd)

    def get_status(self, eventtime):
        status = self._cmd_helper.get_status(eventtime)
        status.update(self._load_cell.get_status(eventtime))
        status.update(self._tapping_move.get_status(eventtime))
        return status


def load_config(config):
    return LoadCellPrinterProbe(config)
