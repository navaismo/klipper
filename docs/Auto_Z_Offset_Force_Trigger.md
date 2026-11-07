# Force Trigger (FT) Auto Z-offset

**EXPERIMENTAL AND REPORT-ONLY. VALIDATE BEFORE PRINTING. FORCE-TRIGGER Z IS A
LOADED REFERENCE, NOT ZERO-FORCE FIRST CONTACT. DO NOT APPLY THE RESULT BASED
ONLY ON REPEATABILITY.**

Use identifier `force_trigger` and include
[`auto_z_offset_force_trigger.cfg`](../config/auto_z_offset_force_trigger.cfg).
Configure `[auto_z_offset_ft]` and query the matching status object. See [method selection](AUTO_Z_OFFSET_PLAN.md#choose-a-method)
for the mutually exclusive include setup. Keep the existing CR Touch and named
load cell; do not add another probe, ADC or HX711 pin owner.

## Configure and run

```ini
[auto_z_offset_ft]
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

Choose reachable physical bed XY points. Use multiple distinct `probe_points`,
or one `probe_point`, never both. Keep your measured load-cell calibration;
calibrate with unloaded TARE and a known mass before nozzle probing.

Run manually while idle and unpaused:

```gcode
AUTO_Z_OFFSET BED_TEMP=60 NOZZLE_TEMP=170
```

Set both heater targets through native Klipper control. Begin as soon as both
measured temperatures are within target +/- `temperature_tolerance`, including
the boundaries, without a PID-settling wait. Enforce the band at measurement
checkpoints; reject changed targets or invalid readings.

Clear mesh, home if needed, then at each point:

1. Probe with CR Touch using its native sampling policy and XY offsets.
2. Retract and move the nozzle to the same physical XY at clearance.
3. Capture an unloaded local ADC safety reference; retain it for the point.
4. Take three native MCU force-triggered descents, with per-press tare and
   retraction only after a successful trigger.
5. Validate the pressure-Z sample spread and report its median:

```text
pressure_z = median(three MCU force-trigger Z positions)
provisional_z_offset = cr.test_z - pressure_z
```

Use the same kinematic frame for both coordinates. Do not add the saved
Z-offset again, apply a thermal correction or average locations. Keep P1 as
the top-level report reference and report the spatial range separately.

Example console fields, with illustrative numbers:

```text
Auto Z-offset method: Force Trigger
Force threshold: 75 g
Triggered Z: -0.420000 mm
Provisional Z-offset: 1.615833 mm
```

Account for compression and possible signal delay at the force threshold.
Do not compare this loaded reference with an LCBestFit estimate as if both
defined the same first-contact position. No ACCEPT/APPLY or automatic save
is available. On failure, stop without retries, deeper movement or a completed
result; retain the existing sensor scale and probe offset.

## Motion and MCU protection

Reuse `MCU_trigger_analog`, native tare/filter helpers, `LoadCellProbingMove`
and `homing.probing_move()` around the existing load cell. Do not instantiate
TappingMove, LCBestFit, onset models or PRTouch for this method.

Keep the configured force trigger (75 g in the example), raw safety limit
(200 g), sensor-error checks, emergency shutdown and native travel limits.
Anchor raw safety to the local unloaded reference, not a moving per-press
zero. Log the saved reference separately; do not overwrite calibration.

Search only to the configured native Z minimum clipped to the kinematic
minimum; calculate maximum descent as start Z minus endpoint. Do not use the
saved probe offset or `sample_retract_dist` as search travel. Reject no-trigger
or endpoint results, including a force notification within one native motor
step of the endpoint. **Mechanical limits alone do not guarantee nozzle
clearance under sensor failure.**

Log pressure acquisitions with original MCU sample times and trigger Z. Inspect
`auto_z_offset_ft` status: `backend: force_trigger`, `method: Force Trigger`,
per-point reports and `eligible_for_application: false`.

## LCD calibration views

Use the same host-owned CR Touch Reference, Nozzle Contact and Computing Offset
views as Contact Regression. Existing `TJC_DRAW_VERSION=1` or compatible canvas
firmware is sufficient; no reflash is needed for these screens. Complete each
update before sampling. Keep LCD failures separate from force/motion failures.

## Relationship to PROBE BY TOUCH / PROBE BY PRESS

Follow the physical sequence of CR Touch reference followed by three nozzle
pressure measurements, as in the Marlin procedure. Use native Klipper absolute
coordinates for both and subtract directly. Do not copy Marlin's empirical
0.05-mm allowance or its final settings write. FT deliberately reports the MCU
force-trigger reference instead of porting Marlin's contact-index estimator.
