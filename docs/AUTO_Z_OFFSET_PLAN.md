# Automatic Z-offset: Contact Regression and Force Trigger

**EXPERIMENTAL. VALIDATE BEFORE PRINTING. A REPEATABLE OFFSET CAN STILL DAMAGE
THE BUILD SURFACE. KEEP YOUR WORKING OFFSET UNTIL INDEPENDENT VALIDATION.**

Use CR Touch as the primary probe and the existing named load cell as the
nozzle reference. Run calibration manually while idle, never from PRINT_START,
startup, KAMP or virtual-SD preflight. Neither method automatically applies or
saves a measurement.

## Choose a method

| Method | Identifier | Reference | Section |
| --- | --- | --- | --- |
| Contact Regression (CR) | `regression` | LCBestFit contact estimate from the force/Z withdrawal curve | `[auto_z_offset_cr]` |
| Force Trigger (FT) | `force_trigger` | Native MCU force-trigger Z under load | `[auto_z_offset_ft]` |

Use `[auto_z_offset_cr]` for CR and `[auto_z_offset_ft]` for FT.
Select the method with the descriptive include names. Copy the two files from `config/` into your printer
configuration directory and enable exactly one:

```ini
# Contact Regression
[include auto_z_offset_regression.cfg]

# Force Trigger
# [include auto_z_offset_force_trigger.cfg]
```

For FT, reverse the comments. Keep `[bltouch]`, `[load_cell bed_force]` and
saved calibration outside both includes. Remove duplicate inline backend
sections. Do not enable `[load_cell_probe]` or claim the HX711 pins twice.
Restart Klipper while idle after switching; no MCU reflash is needed.
Update section headers in existing include copies before restarting.
Keep only the selected section enabled.

## Prepare the sensor

Follow [load-cell setup and tare calibration](../README.md#configure-and-calibrate-the-load-cell).
Use raw HX711 acquisition at 80 SPS and measured scale/orientation/reference
values. Run `LOAD_CELL_DIAGNOSTIC LOAD_CELL=bed_force` without nozzle contact;
check for errors, saturation and sample-rate problems.

In guided `LOAD_CELL_CALIBRATE LOAD_CELL=bed_force`, run `TARE` without added
load before `CALIBRATE GRAMS=<actual_mass_in_grams>`. Only accept a successful,
verified sensor calibration. Save its `counts_per_gram` and
`reference_tare_counts`; do not copy values from another printer.
Outside that session, `LOAD_CELL_TARE` changes operational zero only.
Do not confuse sensor-calibration `ACCEPT` with `AUTO_Z_OFFSET_ACCEPT`.

## Contact Regression

Use `config/auto_z_offset_regression.cfg` for the two-point workflow. Configure
physical P1/P2 coordinates, explicit contact/release speeds, force limits and
temperature settings. Refer to [all options](Config_Reference.md#auto_z_offset_cr).

```gcode
AUTO_Z_OFFSET BED_TEMP=60 NOZZLE_TEMP=170
```

Heat, clear the mesh and run full homing. Test one fresh CR Touch/nozzle pair
at each point before taking three new pairs at each. Exclude preliminary test
pairs from the calibration result. Within each pair, follow the configured
CR Touch sample policy, move the nozzle to the same physical point and acquire
a force-triggered tap followed by withdrawal.

Estimate contact with LCBestFit from the loaded/unloaded withdrawal branches.
Do not substitute the MCU stop position for the fitted position:

```text
paired_offset = cr.test_z - fitted_nozzle_z
candidate = median(P1 paired offsets)
```

Validate each local spread against `offset_tolerance` and the difference
between point medians against `spatial_tolerance`. Use P2 to validate P1;
do not average the two locations. Stop the sequence on any failed pair or
invalid measurement. The formula uses a shared kinematic frame; do not add
the saved offset a second time or treat it as a calibration target.

Example console fields, with illustrative numbers:

```text
Auto Z-offset method: Contact Regression
Contact estimated Z: -0.304019 mm
Z-offset: 1.519695 mm
```

Pair estimates and the final median may differ. Keep the force-trigger Z and
regression difference in diagnostics for comparison; fit repeatability alone
does not prove zero-force accuracy.

### Separate contact and calibration commands

For manual diagnosis, home and clear mesh before running:

```gcode
AUTO_Z_OFFSET_TEST_CONTACT POINT=1 BED_TEMP=60 NOZZLE_TEMP=170
AUTO_Z_OFFSET_TEST_CONTACT POINT=2 BED_TEMP=60 NOZZLE_TEMP=170
AUTO_Z_OFFSET_CALIBRATE BED_TEMP=60 NOZZLE_TEMP=170
```

Use CALIBRATE only after successful tests at every point with unchanged
references. The automatic command already includes these stages. With the
single-point `probe_point` interface, take five calibration pairs; with four
configured points, retain the median-of-point-medians policy. Use two points
for the normal automatic command.

### Review and save explicitly

A successful CR cycle leaves a candidate in memory. Keep the existing runtime
offset until independent validation. Only then explicitly run
`AUTO_Z_OFFSET_ACCEPT` to stage `[bltouch] z_offset`. Review all pending
settings before `SAVE_CONFIG`, which writes them and restarts Klipper.
Reject stale, failed or incomplete candidates. No provisional APPLY/ROLLBACK
or DIAGNOSTIC/COLD mode is provided by this interface.

## Force Trigger

Use `config/auto_z_offset_force_trigger.cfg`. Follow the
[Force Trigger guide](Auto_Z_Offset_Force_Trigger.md) for acquisition and result fields.
Use the same AUTO_Z_OFFSET command, with report-only output and no acceptance.
Keep withdrawal regression out of this method's numerical result.

## Shared protections and limitations

Keep the measured sensor scale, native analog force trigger, raw safety band,
sensor-error handling, emergency shutdown and mechanical travel bounds active.
Capture a local unloaded ADC safety reference at nozzle clearance for each
point; keep that reference fixed across the point's measurements. Per-tap tare
must not move the raw safety band. Leave saved calibration unchanged.

Use the configured native Z minimum, clipped to the kinematic minimum, as the
finite search endpoint. Reject no-trigger and step-quantized endpoint results.
Do not treat a late force notification at the endpoint as valid contact.
**A configured mechanical endpoint does not establish a safe nozzle-to-PEI
clearance under failed force detection.** Do not deepen bounds or raise force
limits to force success.

Keep mesh disabled throughout measurement. Account for CR Touch XY offsets:
with offsets -24/-14.5, place the nozzle at X52/Y42.5 to probe physical X28/Y28,
then at X28/Y28 for nozzle measurement. Check every destination against travel
limits. Do not combine unsupported axis-twist, skew or thermal-Z corrections.

Set `temperature_tolerance` in each selected include. The supplied value is
3 C. CR additionally accumulates `temperature_stable_time` in band and allows
bounded recovery at checkpoints; FT starts when both heaters enter the band
and rejects out-of-band checkpoint readings. Keep native heater protections.

Use either method with the existing host-owned LCD phase screen. Finish each
screen update before acquisition; do not add LCD traffic during force sampling.
No changes to print-start or KAMP are needed.
