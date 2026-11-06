# Native Auto Z-offset pressure-reference backend

This separate DEV backend uses the existing CR Touch and named HX711 load cell.
It does not replace or edit the existing `[auto_z_offset]` implementation.

**EXPERIMENTAL AND REPORT-ONLY. THE MCU FORCE-TRIGGER POSITION IS A LOADED
PRESSURE REFERENCE, NOT A MEASUREMENT OF ZERO-FORCE FIRST CONTACT. THE REPORTED
OFFSET MAY CONTAIN COMPRESSION OR SIGNAL DELAY. IT IS NOT AUTHORIZATION TO APPLY
AN OFFSET OR PRINT A FIRST LAYER.**

## Mapping the Marlin procedure

The inspected reference is
[navaismo/Marlin_bugfix_2.1_E3V3SE, AutoOffset.cpp](https://github.com/navaismo/Marlin_bugfix_2.1_E3V3SE/blob/701867852f7e2fe8125b649355dfc165f403eb92/Marlin/src/e3v3se/AutoOffset.cpp),
with `AutoOffset.h` and `M8015.cpp` in the same directory. The inspected local
files are archived with SHA256 hashes in the DEV backup.

- `M8015` calls `getZOffset`, which disables leveling, temporarily zeroes the
  Marlin probe offset, homes, measures points and ultimately saves settings.
  The native backend uses normal Klipper homing without zeroing the existing
  offset, disables bed mesh and never writes settings.
- `Hight_One` performs CR Touch probing, then `probeByPress` takes three new
  pressure measurements and selects their median. This backend takes one
  native CR Touch result (honoring its native sampling policy) and three
  native force-triggered pressure measurements at the same physical XY.
- The current reference does not cache one global `press_z` for all points.
  Its shared `acqVals` array stores printed history, not a reusable pressure
  reference. Identical historical `press_z` values do not prove shared sampling.
- There is a coordinate distinction in that reference: `probePointByStep`
  stores `relZ = current_z - basePos.z`, and `calMinZ` returns a selected
  **relative** `posZ`. `Hight_One` sets `basePos.z = touch_z` and later computes
  `press_z - touch_z`. A relative displacement must first be converted to an
  absolute position before subtracting an absolute touch coordinate. This
  backend avoids that mixture by using native absolute trigger coordinates.
- Marlin adds `NOZ_TEMP_OFT_MM = 0.05` to its pressure result. Its header calls
  this a nozzle thermal expansion allowance. That empirical adjustment is not
  established for Klipper's force-trigger reference and is **not applied**.
  Marlin's approximately 1.57-mm result is not a correctness target.

Klipper's manual probe calibration computes
`old_offset + cr.bed_z - manual_nozzle_z`. With an uncorrected native result,
`cr.test_z = old_offset + cr.bed_z`, so the corresponding difference is:

```text
pressure_z = median(three native MCU force-trigger Z positions)
provisional_z_offset = cr.test_z - pressure_z
```

Both terms use the same native kinematic frame. A common Z-origin shift cancels.
The previous saved offset is not added again or used as a numerical target.
The native force threshold replaces Marlin's contact-index calculation;
therefore these two methods do not define exactly the same physical reference.
No new estimator or compression correction is introduced.

## Native acquisition and bounds

`auto_z_offset_native.py` constructs `MCU_trigger_analog`, its native SOS/tare
helpers and `LoadCellProbingMove` around the **existing** `load_cell bed_force`.
The latter calls native `homing.probing_move()`. No second `probe`, ADC, pin
claim, direct stepper control or segmented motion is created. `TappingMove`,
LCBestFit, the custom onset/windows chain and PRTouch are never instantiated or
called by this backend. Default filtering is the native empty filter design;
there is no port of Marlin's high-pass/rotation estimator.

The existing HX711 remains at 80 SPS with its saved calibration. Each descent
uses the existing MCU 75-g trigger, 200-g raw limit and native sample/error/
communication handling. After heating and CR Touch, the backend captures one
local ADC reference at each configured point with the nozzle retracted to
`horizontal_move_z`.
This position must be physically unloaded. The 200-g raw band is anchored to
that reference for all three pressure measurements, not to the saved baseline
from a different thermal/mechanical state. Per-press operational tare cannot
move this band or bypass the native compression-margin check. The local anchor
is cleared at session end/disconnection; saved reference and scale are unchanged.
The local and saved reference counts are logged separately. Capturing a local
reference does not establish that an unknown preload is safe or identify the
physical cause of baseline drift. Native tare checks include available compression
margin.
The supplied settings preserve these ceilings; command parameters cannot raise
them. Native motor/endstop/position handling and emergency shutdown remain active.

The finite pressure endpoint is the existing initial contact envelope:
`cr.test_z - saved_probe_offset - sample_retract_dist`, with a maximum 0.5-mm
allowance. It must stay above the native global Z minimum. This is a logical
search reference, not proof of independently known surface height. The saved
offset can influence search coverage even though it is not added to the final
measured difference. If that coverage is insufficient, native probing fails;
the backend never deepens the floor, increases force or retries a failed move.
It does not turn the global Z minimum into an automatic search endpoint.

After each successful MCU trigger, native positioning lifts to clearance and
closes/checks the collector. Raw sample timestamps and counts are logged without
fitting. The native probing helper's existing trace is enabled: it logs the
operational tare, raw safety band, requested endpoint and start frame. On a failed
descent it also records collected ADC samples/extrema and the position frame at
the error, while preserving the original exception. Host bulk samples may omit
the exact MCU decision sample; trace data are not proof of the trigger cause.
No extra fit or contact estimator is invoked by this failure path.
A pressure error ends the run immediately. No blind lift, rehome or retry
is issued after a failed move. The native `samples_tolerance` checks spread of
the three pressure positions; this is repeatability, not an accuracy guarantee.

Mesh is cleared before heating and after any required homing, and its absence
is checked throughout measurement. The calculation uses `cr.test_z`, not a mesh
or axis-twist-corrected `bed_z`. Positioning uses native coordinates rather than
G90/G91 or G-code offset arithmetic. Primary probe offsets, saved load-cell scale
and reference must remain unchanged during the session.

## LCD calibration views

With the existing `[tjc_lcd]`, both backends use the same host-owned calibration
artwork and driver APIs. The native backend opens the warming-up view before
heating, highlights CR Touch before its sampling session, nozzle contact before
the local reference/each pressure descent, then computing offset for each point.
The status line identifies the point and pressure sample. A final review or
failure view closes the session; a successful screen still represents a
provisional loaded-force reference and does not authorize application.

The driver reserves calibration against knob menus, queued idle drawing and
print preflight. Phase drawing completes synchronously before acquisition starts;
there is no LCD traffic during pressure descent or its sample collection.
Only changed phase/status regions are drawn after the initial screen. Existing
`TJC_DRAW_VERSION=1` or `TJC_CANVAS_VERSION=1` firmware is sufficient, with no new
MCU assets, configuration or reflash.

LCD startup/update/finish errors are reported separately and cannot replace
native safety errors or change the numerical result. After an update failure,
further phase updates are suppressed until the session ends. The driver's
normal end API is still called to release its reservation. The native backend
also works without an LCD.

## Select the backend in DEV

Keep `[bltouch]`, `[load_cell bed_force]` and their saved calibration common in
`printer.cfg`. Do not duplicate those sections or their pins. No change to the
saved BLTouch offset or HX711 scale is needed. No MCU rebuild or flash is needed.

Copy `config/sample-auto-z-offset-current.cfg` and
`config/sample-auto-z-offset-native.cfg` to the DEV configuration directory.
Move the existing inline `[auto_z_offset]` section to the current include, or
use the supplied current include, which preserves the inspected effective DEV
settings. Do not leave the inline section active together with the native one.
Back up `printer.cfg` before changing includes; the implementation does not edit
that active file.

Current implementation:

```ini
[include sample-auto-z-offset-current.cfg]
# [include sample-auto-z-offset-native.cfg]
```

New native implementation:

```ini
# [include sample-auto-z-offset-current.cfg]
[include sample-auto-z-offset-native.cfg]
```

The native sample contains:

```ini
[auto_z_offset_native]
load_cell: bed_force
probe_points:
    28, 28
    110, 110
    180, 28
horizontal_move_z: 5
travel_speed: 50
speed: 0.5
lift_speed: 5
sample_retract_dist: 0.5
samples_tolerance: 0.025
trigger_force: 75
force_safety_limit: 200
bed_temp: 60
nozzle_temp: 170
temperature_tolerance: 3
```

`probe_points` accepts at least three distinct physical bed XY locations, one
pair per line, visited in the configured order. For compatibility,
`probe_point: 28, 28` retains the original single-point cycle. Use only one of
these options.
Both nozzle and offset-adjusted CR Touch destinations for every point are checked
against native travel limits before heating or movement.

With CR Touch offsets X-24/Y-14.5, nozzle XY52/42.5 puts the sensor above physical
P1 X28/Y28. Pressure measurements then put the nozzle itself above X28/Y28.
Each location obtains its own native CR Touch result, a pressure endpoint derived
from that result, one local unloaded ADC safety reference and three fresh pressure
descents. The local raw band stays fixed during that location's three descents.
No pressure result or endpoint is reused from another location. XY transfers
occur at or above `horizontal_move_z`.

Every point reports the median pressure Z and its own provisional offset.
`spatial_range` reports the largest minus smallest point offset for comparison;
it is diagnostic and adds no acceptance threshold. The top-level provisional
reference remains point 1; no spatial average is calculated. Multiple matching
loaded-pressure references do not prove zero-force accuracy. A failure at any
point stops the cycle without retries, visiting later points or leaving a valid
completed result. Completed point reports remain in the log. Nothing is applied
or saved.

Selecting both backends, a separate `[load_cell_probe]` or `[prtouch]` is rejected
before the new trigger is created. B1 and other read-only clients may keep using
the same named load-cell instance. Do not run an interactive sensor calibration
or place a calibration mass on the bed during an Auto Z-offset session.

The owner controls restarts and separately authorizes physical commands. After
selecting the native include and restarting DEV, the real command is:

```gcode
AUTO_Z_OFFSET BED_TEMP=60 NOZZLE_TEMP=170
```

The positive, finite `temperature_tolerance` setting defaults to 3 degrees C.
Both heater targets are set through native Klipper control without waiting for
PID settling. Warm-up ends at the first check where both measured temperatures
are within their respective target plus or minus this margin (including the
boundaries); there is no extra stability dwell or requirement to reach the exact
target first. The same band is enforced at measurement checkpoints: before
CR Touch, and before and after every pressure
measurement. An out-of-band reading rejects the session without a result;
changed targets or invalid readings still reject it regardless of tolerance.
Native heater limits and `verify_heater` protections remain active. Changed
targets, invalid readings or shutdown also abort during the warm-up wait.

It requires an idle/unpaused printer, waits for both heaters within the band,
homes only if axes are not already homed, and clears mesh. It has no APPLY,
ACCEPT or SAVE_CONFIG path. On failure it clears its result and restores the
previous heater targets only if those targets still belong to this command and
Klipper is not shut down. No printing macro, KAMP configuration or startup hook
is modified.

To revert, select the current include again (or restore the backed-up original
inline configuration) and let the owner restart DEV. Both implementations remain
on disk; the unselected backend does not allocate an analog trigger.
