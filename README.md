# Klipper for the Ender-3 V3 SE

This Klipper fork reuses the original TJC LCD and knob while keeping the
screen connected to the printer mainboard. The owner confirmed the current
appearance on an STM32F103RET6 mainboard with a TJC3224T1 display; the
photos below show this setup on hardware.

## Fork features

- Idle screen at startup and after completed/cancelled jobs, with the
  connected MCU's firmware revision and compilation date below `Idle`.
- Host-designed knob menus for homing, preheating, 10 mm XYZ movements,
  and filament loading/unloading at 240 C with press-to-cancel support.
- A static card rendered before the first print G-code: 120x120 displayed
  thumbnail, layers, layer height, estimated total duration, material,
  wrapped filename and first-layer nozzle/bed targets. `PRINTING` is orange.
- A debounced knob press during printing runs the configured `PAUSE`
  command once. The card stays visible; resume through Fluidd/Moonraker.
- On-demand CR Touch Z-offset calibration and load-cell diagnostics;
  see the experimental workflow and qualification notes below.
- Screen designs, colors, labels, artwork and navigation stay on the host.
  Once generic drawing firmware is installed, visual updates need no flash.

**The print card is intentionally static.** There is no live progress,
current-layer, remaining-time or temperature refresh during printing or
pause. Card temperatures come from the file, not heater readings. Only an
active idle Load/Unload operation refreshes changed nozzle temperatures.
The mainboard C driver owns LCD USART2, framing and bounded drawing work;
the host never opens an LCD serial port.

## Hardware photos

These owner-supplied photos replace the earlier idle/card examples. They
show the current idle footer, both menus and two print cards. They confirm
appearance, not measured rendering time or print-quality impact.

<img src="Screenshots/IdleV7.jpg" alt="Idle screen with Klipper branding and MCU firmware revision and build date" width="300">

| Main menu | Movement submenu |
| --- | --- |
| <img src="Screenshots/Menu1.jpg" alt="Main knob menu with Home, Load, Unload, Preheat, More Moves and hold-to-idle hint" width="300"> | <img src="Screenshots/Menu2.jpg" alt="Movement submenu with six 10 mm XYZ actions and Back" width="300"> |

| Print card: skull case | Print card: slingshot |
| --- | --- |
| <img src="Screenshots/PrintPage1.jpg" alt="Static skull-case print card with enlarged thumbnail and Press Knob to Pause footer" width="300"> | <img src="Screenshots/PrintPage2.jpg" alt="Static slingshot print card with orange PRINTING label and file temperature targets" width="300"> |

## Setup

Follow the [complete LCD and knob setup guide](docs/TJC_LCD_Setup.md)
for first installation, firmware settings, dependencies, configuration,
commands, troubleshooting, updates and rollback.

The required setup is:

1. Run this complete fork in the Klippy service. The current development
   LCD branch is `feature/lcd_status`; the combined development branch is
   `feature/Auto_Z_offset`. Copying only extras into upstream
   Klipper does not install the virtual SD hooks.
2. Use mainboard firmware with `TJC_LCD_VERSION=1`, `TJC_JOB_VERSION=5`,
   `TJC_DRAW_VERSION=1`, and GPIO button support. Keep the confirmed 28 KiB
   bootloader, 8 MHz crystal and USART1 host settings. An already compatible
   MCU needs no reflash for these new designs.
3. Install [Klippy requirements](scripts/klippy-requirements.txt), including
   Pillow, in the running service's Python environment.
4. Include [sample-tjc-lcd.cfg](config/sample-tjc-lcd.cfg) once in the active
   printer configuration. It enables `[tjc_lcd]`, `[lcd_status]` and
   `[tjc_menu]`; retain the existing virtual SD path and printer settings.
5. Reload Klipper while idle. Enable slicer PNG/JPEG thumbnails, preferably
   at least 192x192, and start prints normally from Fluidd/Moonraker.

Do not add LCD commands or scripts to `START_PRINT`. With the default
`auto_prepare: True`, metadata and all drawing complete before the first
file G-code. Metadata/render failures prevent start; thumbnail failures
allow a text-only card and report warnings. The prepared 96x96 RGB565 image
is displayed at 120x120 with its colors preserved.

The working host link is USART1 PA9/PA10 at 250000 baud over the mainboard's
USB-C connection. LCD USART2 reserves PA2/PA3 at 115200 baud. The knob pins
are `encoder_pins: ^PA11, ^PA12` and `click_pin: ^!PB1`. Do not assign them
to other devices or enable a generic `[display]` driver for this LCD.

## Knob operation

Turn either way from visible Idle to open Menu. Clockwise highlights the
next entry down; counterclockwise highlights the previous entry. Click to
enter/execute. Hold for 0.8 seconds to return to Idle, as indicated by
`Press&Hold Knob: Idle Screen`. An idle click does nothing.

Fast turns share one pending redraw, with a 200 ms cooldown after the
previous draw completes. Successful Home, Preheat and Movement actions
keep the menu/highlight without redrawing. Home uses the configured `G28`;
preheat sets bed 50 C/nozzle 170 C. Movement requires homing and a full
in-range 10 mm destination; Front is Y +10 mm and Back is Y -10 mm.

Load heats to 240 C, feeds 50 mm at F800 and 40 mm at F400. Unload heats
to 240 C, purges 15 mm at F400 and retracts 90 mm at F800. Speeds are
mm/min. `Press to cancel` stops after the active drawing transaction or
queued extrusion segment (at most 1 mm), turns the nozzle off and returns
to Menu. Completion and recoverable errors also turn the nozzle off;
G-code state is restored and the bed target is unchanged.

During printing only a fresh debounced press runs `PAUSE`; rotations and
paused clicks do nothing. Pause/cancel clicks do not use the navigation
cooldown. Resume through the normal host UI. The full
[controls and action table](docs/TJC_LCD_Setup.md#knob-controls-and-actions)
describes the menu and its limits.

## Commands and diagnostics

While idle, `LCD_PREPARE FILENAME="parts/example.gcode"` prepares data only;
`LCD_RENDER FILENAME="parts/example.gcode"` draws a test card without
starting a print. After an LCD error, explicit idle `LCD_RENDER` retries
bounded initialization before drawing; automatic print preparation still
rejects a failed LCD. No timeout is accepted as success.

`TJC_LCD_IDLE` restores Idle and closes the menu. `TJC_LCD_INIT` requests
initialization; `TJC_LCD_CLEAR COLOR=0` requests a black clear. Wait for
`tjc_lcd.state: ready` after initialization/clear. Inspect `lcd_status`,
`tjc_lcd` and `tjc_menu` through the usual printer object API; reads are
passive. All manual LCD commands reject printing/paused jobs.

References: [setup and recovery](docs/TJC_LCD_Setup.md),
[configuration](docs/Config_Reference.md#lcd_status),
[commands](docs/G-Codes.md#lcd_status),
[status fields](docs/Status_Reference.md#lcd_status),
[architecture and history](docs/LCD_STATUS_PLAN.md).

## Automatic Z-offset calibration

> [!WARNING]
> **AUTOMATIC Z-OFFSET IS EXPERIMENTAL. VALIDATE BEFORE PRINTING.**
>
> The calculation is not perfect and may produce an inaccurate offset even
> when measurements are repeatable and all checks pass. Keep your known-good
> offset and verify nozzle-to-bed clearance before accepting or saving a new
> value. **AN INCORRECT OFFSET CAN DRIVE THE NOZZLE INTO THE BUILD SURFACE
> AND DAMAGE IT.**

CR Touch remains the primary probe for homing and bed mesh. The stock load
cell provides nozzle-contact measurements only to calculate its Z-offset;
it does not replace CR Touch or register another virtual endstop. Start
calibration on demand while the printer is idle and unpaused.
**Do not add it to PRINT_START, startup or KAMP.** Printing continues to use
the saved offset and the usual adaptive mesh.

The two-point workflow uses physical bed coordinates X28/Y28 near the cell
as P1, and X110/Y110 at the center as P2. Each CR Touch measurement is paired
with nozzle contact at the same physical point, accounting for probe XY
offsets. P1 supplies the candidate; P2 validates agreement without averaging.
The owner has completed two-point runs with LCD phase updates on hardware;
initial first-layer qualification remains separate from measurement success.
An investigation of a systematic difference from the working printing offset
is documented in the [diagnostic audit](docs/AUTO_Z_OFFSET_PLAN.md#systematic-offset-investigation-2026-10-30).
Keep the working offset; experimental candidates remain diagnostic-only until
the cause is understood. Repeatability alone does not establish accuracy.

### 1. Configure and check the load cell

Use this fork's host and matching MCU firmware, including HX711 acquisition
and analog-trigger support (`CONFIG_WANT_HX71X` and
`CONFIG_WANT_TRIGGER_ANALOG`, normally enabled). The ADC setup below has been
tested on the stock Ender-3 V3 SE board. Add it to `printer.cfg` or an included
file, keeping your existing `[bltouch]` and its working `z_offset`:

```ini
[load_cell bed_force]
sensor_type: hx711
sclk_pin: PA4
dout_pin: PC6
gain: A-128
sample_rate: 80
sensor_orientation: inverted
```

Configure the sensor only once. Do not add `[load_cell_probe]`; Auto Z-offset
reuses this named sensor and its calibration. After reloading Klipper while
idle, check acquisition without motion or nozzle contact:

```gcode
LOAD_CELL_DIAGNOSTIC LOAD_CELL=bed_force
LOAD_CELL_READ LOAD_CELL=bed_force
```

The diagnostic collects ten seconds of samples. Check for no acquisition
errors or saturation and a measured rate close to the configured 80 SPS.
Before calibration, `LOAD_CELL_READ` displays raw percentage and `---.-g`.
Apply and remove a small, controlled load at the sensing area to verify a
repeatable response; the reading must change when load is applied.

### 2. Calibrate the cell: tare first, then a known load

**Do not start nozzle-contact tests with guessed or copied calibration
values. TARE is essential:** it measures the unloaded reference used to
calculate `counts_per_gram` and to save `reference_tare_counts`.

Keep the normal build plate installed, the nozzle clear of the bed and the
printer stationary. Remove added weights and external pressure, then start
the guided session and take the unloaded reference:

```gcode
LOAD_CELL_CALIBRATE LOAD_CELL=bed_force
TARE
```

Wait for the tare response. Apply a stable load of known mass to the sensing
area near P1, without nozzle contact. Enter its actual mass in grams; for an
actual 1100 g calibration load, for example:

```gcode
CALIBRATE GRAMS=1100
```

Do not enter `1100` unless that is the load actually applied, and do not use
unmeasured hand pressure as a known mass. If Klipper reports identical
readings, less than 1% difference, saturation or another calibration error,
do not accept the result. Use `ABORT`, check load transfer and sensor response,
then repeat the guided calibration with an appropriate known load.

Only after `CALIBRATE` succeeds, accept the sensor calibration:

```gcode
ACCEPT
```

Klipper reports the measured `counts_per_gram` and `reference_tare_counts`
and stages them for saving. Remove the calibration weight, then persist them:

```gcode
SAVE_CONFIG
```

`SAVE_CONFIG` writes all pending settings to the active configuration and
restarts Klipper. The generated `[load_cell bed_force]` entry in the
SAVE_CONFIG block contains this printer's scale and unloaded reference.
Do not replace these with numbers from another printer or edit that block
by hand. This step calibrates the force sensor; it does not change CR Touch's
Z-offset.

After restart, with no added load, verify the calibrated sensor:

```gcode
LOAD_CELL_TARE LOAD_CELL=bed_force
LOAD_CELL_READ LOAD_CELL=bed_force
LOAD_CELL_DIAGNOSTIC LOAD_CELL=bed_force
```

The unloaded reading should be near zero, and an applied known load should
produce a corresponding positive gram reading. In this workflow, `TARE`,
`CALIBRATE`, `ACCEPT` and `ABORT` belong to the guided calibration session;
a standalone `TARE` otherwise returns "Unknown command". Outside that session,
`LOAD_CELL_TARE` adjusts the current zero only: **it does not calibrate the
scale or save a new `reference_tare_counts`.** Auto Z-offset performs its
own unloaded point-reference capture and local tares after warm-up; those
do not replace the saved sensor calibration.

### 3. Configure Auto Z-offset

Once sensor acquisition and calibration work, add:

```ini
[auto_z_offset]
load_cell: bed_force
probe_points:
    28, 28
    110, 110
speed: 0.5
lift_speed: 0.1
sample_retract_dist: 0.5
trigger_force: 75
force_safety_limit: 200
horizontal_move_z: 5
travel_speed: 50
z_hop_speed: 5
bed_temp: 50
nozzle_temp: 170
temperature_tolerance: 2
temperature_stable_time: 5
temperature_recovery_time: 10
heating_timeout: 600
offset_tolerance: 0.025
spatial_tolerance: 0.025
```

Speeds are in mm/s and forces are in grams. The contact speeds, retract
distance and 75/200 g thresholds above come from this printer's test setup;
they require local qualification, not just a successful config load.
Keep the established force limits when updating an already qualified setup.
`horizontal_move_z` must exceed the current probe offset. Both nozzle and
CR Touch positions must be reachable for each physical point. This workflow
does not support configured axis-twist, skew or thermal-Z compensation.

The temperature settings shown use the software defaults: bed/nozzle minima
are 50/170 C. Normal PID excursions pause warm-up accumulation or allow a
bounded recovery wait; changed targets and invalid readings reject the run.
Select tolerances for normal temperature variation, not to hide sensor faults.
The existing MCU analog trigger stops nozzle descent, while force, raw-range,
acquisition and contact-fit checks reject invalid measurements.

### 4. Run the automatic cycle and review the result

Reload Klipper while idle after adding the Auto Z-offset section. With a clean
nozzle, an unobstructed bed and an idle printer, run:

```gcode
AUTO_Z_OFFSET
```

Or explicitly select the calibration targets:

```gcode
AUTO_Z_OFFSET BED_TEMP=50 NOZZLE_TEMP=170
```

The command warms up, clears the mesh and performs full `G28`; no separate
homing or mesh clear is needed. It then runs two distinct measurement stages:

| Stage | P1 | P2 | Purpose |
| --- | --- | --- | --- |
| Contact test | 1 CR Touch/nozzle pair | 1 CR Touch/nozzle pair | Check contact acquisition at both locations before calibration. |
| Calibration | 3 fresh pairs | 3 fresh pairs | Calculate the candidate and validate repeatability and point agreement. |

**Calibration starts only if both contact tests pass.** The two preliminary
test pairs are excluded from the six-pair calibration result. Within each
pair, CR Touch can take several readings according to its configured
`[bltouch] samples`; three probe readings in the console do not mean three
independent contact tests.

Each local range must pass
`offset_tolerance`; the difference between point medians must pass
`spatial_tolerance`. P1's median is the candidate, and P2 only validates it.

**Any failed contact or invalid measurement stops the sequence:** it does not
continue with the next test/pair or accept the remaining samples. Force/raw
limits, acquisition errors, thermal failures, invalid contact fits and
excessive local/spatial spread reject the run. The error identifies the stage,
point/pair and reason.
Previous heater targets are restored on exit, the mesh stays cleared and the
existing offset remains active. KAMP can create a fresh mesh for the next
print as usual. Successful measurement leaves a candidate in memory only.

For initial qualification, keep a backup of the working configuration and
validate first-layer behavior before adopting a new offset.

To explicitly approve a validated candidate and then persist it:

```gcode
AUTO_Z_OFFSET_ACCEPT
SAVE_CONFIG
```

`AUTO_Z_OFFSET_ACCEPT` stages `[bltouch] z_offset`; it does not immediately
apply it. `SAVE_CONFIG` writes the staged value along with any other pending
settings and restarts Klipper. No measurement command automatically saves or
overwrites the working offset.

### Optional diagnostic commands

For separate supervised tests, home and clear the mesh first:

```gcode
G28
BED_MESH_CLEAR
AUTO_Z_OFFSET_TEST_CONTACT POINT=1 BED_TEMP=50 NOZZLE_TEMP=170
AUTO_Z_OFFSET_TEST_CONTACT POINT=2 BED_TEMP=50 NOZZLE_TEMP=170
AUTO_Z_OFFSET_CALIBRATE BED_TEMP=50 NOZZLE_TEMP=170
```

`TEST_CONTACT` takes one pair at the selected point without saving.
`CALIBRATE` takes three fresh pairs per point without requiring prior manual
contact tests. These manual commands retain the homed/inactive-mesh
prerequisites and qualify each fresh acquired contact before use.
They are alternatives for diagnosis; the normal `AUTO_Z_OFFSET` command
already includes their sequence and preparation. All measurement commands
accept the same optional bed/nozzle targets.

### LCD calibration views

The optional LCD calibration view highlights CR Touch Reference, Nozzle Contact
and Computing Offset without clearing the screen between phases. It requires
mainboard firmware with `TJC_CANVAS_VERSION=1` or the menus' existing
`TJC_DRAW_VERSION=1` under the LCD build option. A drawing-only MCU reuses
its installed generic executor, without a new flash. The panel firmware
and print-card protocol v5 are unchanged. The host
supplies bounded drawing coordinates, and each update completes before sensor
measurements. Only changed rows and the status strip are repainted; no LCD
traffic runs during sensor acquisition or contact fitting. These views run
only during idle calibration, never during a print.

| Warm-up | CR Touch reference | Nozzle contact |
| --- | --- | --- |
| <img src="Screenshots/autoz_s1.jpg" alt="Auto Z-offset screen warming the bed to 50 C and nozzle to 170 C" width="250"> | <img src="Screenshots/autoz_s2.jpg" alt="Auto Z-offset screen highlighting CR Touch Reference" width="250"> | <img src="Screenshots/autoz_s3.jpg" alt="Auto Z-offset screen highlighting Nozzle Contact" width="250"> |

Further details: [setup and qualification](docs/AUTO_Z_OFFSET_PLAN.md),
[configuration](docs/Config_Reference.md#auto_z_offset),
[commands](docs/G-Codes.md#auto_z_offset) and
[load-cell configuration](docs/Config_Reference.md#load_cell).

---

## Original Klipper README

Welcome to the Klipper project!

[![Klipper](docs/img/klipper-logo-small.png)](https://www.klipper3d.org/)

https://www.klipper3d.org/

The Klipper firmware controls 3d-Printers. It combines the power of a
general purpose computer with one or more micro-controllers. See the
[features document](https://www.klipper3d.org/Features.html) for more
information on why you should use the Klipper software.

Start by [installing Klipper software](https://www.klipper3d.org/Installation.html).

Klipper software is Free Software. See the [license](COPYING) or read
the [documentation](https://www.klipper3d.org/Overview.html). We
depend on the generous support from our
[sponsors](https://www.klipper3d.org/Sponsors.html).
