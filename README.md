# Klipper for the Ender-3 V3 SE

I'm using this Klipper fork on my Ender-3 V3 SE to keep the original LCD
and knob working, with the screen still connected to the printer mainboard.
My setup uses an STM32F103RET6 mainboard and a TJC3224T1 display. Alongside
the LCD support, I'm working on automatic Z-offset calibration using
CR Touch and the stock load cell.

## What it adds

- An idle screen at startup and after a print finishes or is cancelled,
  with the connected MCU's firmware revision and build date below `Idle`.
- Knob menus for homing, preheating, moving each axis in 10 mm steps,
  and loading or unloading filament at 240 C. Press the knob to cancel
  a filament operation.
- A print card with a 120x120 thumbnail, layer count, layer height,
  estimated total time, material, filename and first-layer nozzle/bed
  targets. The card appears before printing starts, with `PRINTING` in orange.
- Press the knob during a print to run your configured `PAUSE` command.
  The card stays visible; resume from Fluidd or Moonraker.
- Automatic CR Touch Z-offset calibration and load-cell diagnostics,
  started manually while idle. This part is experimental; see the guide below.
- Screen layouts, colors, labels, artwork and navigation live on the host.
  Once the generic drawing firmware is installed, you can change the designs
  without reflashing the mainboard.

The print card stays unchanged while printing or paused. It shows the file's
estimated total time and temperature targets, with no live progress, layer,
remaining-time or heater updates. The Load/Unload screen shows the current
nozzle temperature while that operation runs with the printer idle.

The mainboard's C driver handles the LCD connection over USART2, serial
frames and drawing commands. The host prepares the screen content without
opening an LCD serial port.

## On my printer

Here are the idle screen, both menus and two print cards on my printer.

<img src="Screenshots/IdleV7.jpg" alt="Idle screen with Klipper branding and MCU firmware revision and build date" width="300">

| Main menu | Movement submenu |
| --- | --- |
| <img src="Screenshots/Menu1.jpg" alt="Main knob menu with Home, Load, Unload, Preheat, More Moves and hold-to-idle hint" width="300"> | <img src="Screenshots/Menu2.jpg" alt="Movement submenu with six 10 mm XYZ actions and Back" width="300"> |

| Print card: skull case | Print card: slingshot |
| --- | --- |
| <img src="Screenshots/PrintPage1.jpg" alt="Static skull-case print card with enlarged thumbnail and Press Knob to Pause footer" width="300"> | <img src="Screenshots/PrintPage2.jpg" alt="Static slingshot print card with orange PRINTING label and file temperature targets" width="300"> |

## Setup

The [LCD and knob setup guide](docs/TJC_LCD_Setup.md) covers installation,
firmware settings, dependencies, commands, troubleshooting and rollback.
To get started:

1. Run the complete fork as your Klippy service. `feature/lcd_status`
   contains the LCD work; `feature/Auto_Z_offset` combines it with Z-offset
   development. Copying just the extras into upstream Klipper leaves out
   the virtual SD hooks needed to prepare the screen before a print.
2. Use mainboard firmware with `TJC_LCD_VERSION=1`, `TJC_JOB_VERSION=5`,
   `TJC_DRAW_VERSION=1`, and GPIO button support. For this board, keep the
   28 KiB bootloader, 8 MHz crystal and USART1 host settings. If your MCU
   already has these capabilities, the screen designs need no reflash.
3. Install [Klippy requirements](scripts/klippy-requirements.txt), including
   Pillow, in the running service's Python environment.
4. Include [sample-tjc-lcd.cfg](config/sample-tjc-lcd.cfg) once in the active
   printer configuration. It enables `[tjc_lcd]`, `[lcd_status]` and
   `[tjc_menu]`. Keep your existing virtual SD path and printer settings.
5. Reload Klipper while idle. Enable slicer PNG/JPEG thumbnails, preferably
   at least 192x192, and start prints normally from Fluidd/Moonraker.

Do not add LCD commands or scripts to `START_PRINT`. With the default
`auto_prepare: True`, metadata and all drawing complete before the first
file G-code runs. If metadata collection or rendering fails, the print
will not start. If only the thumbnail fails, you get a warning and a
text-only card. Thumbnails are prepared as 96x96 RGB565 images and displayed
at 120x120 without changing their colors.

My setup connects the host through USART1 PA9/PA10 at 250000 baud over the
mainboard's USB-C connection. The LCD uses USART2 PA2/PA3 at 115200 baud.
The knob pins are `encoder_pins: ^PA11, ^PA12` and `click_pin: ^!PB1`.
Do not assign them to other devices or enable a generic `[display]` driver
for this LCD.

## Knob operation

Turn the knob either way while the idle screen is visible to open the menu.
Turn clockwise to move down or counterclockwise to move up, then click to
open a submenu or run the selected action. Hold for 0.8 seconds to return
to Idle, following the `Press&Hold Knob: Idle Screen` hint. Clicking while
idle does nothing.

Fast turns are limited to one pending selection update, with a 200 ms
pause after each redraw to avoid building up a queue. Home, Preheat and
Movement leave the menu and highlight visible without redrawing after a
successful action. Home runs your configured `G28`; Preheat sets the bed
to 50 C and the nozzle to 170 C. Movement requires homed axes and enough
travel for the full 10 mm step. Front moves Y +10 mm; Back moves Y -10 mm.

Load heats to 240 C, feeds 50 mm at F800 and 40 mm at F400. Unload heats
to 240 C, purges 15 mm at F400 and retracts 90 mm at F800. Speeds are
mm/min. Press the knob to cancel: the active drawing transaction or queued
extrusion segment finishes first (at most 1 mm of extrusion), then the
nozzle turns off and the main menu returns. Completion and recoverable
errors also turn the nozzle off. The operation restores G-code state and
leaves the bed target unchanged.

During a print, each new debounced press runs `PAUSE` once. Rotations do
nothing during printing or pause, and clicks do nothing while paused.
Pause and filament-cancel clicks bypass the navigation delay. Resume from
Fluidd or your usual host UI. The full
[controls and action table](docs/TJC_LCD_Setup.md#knob-controls-and-actions)
describes the menu and its limits.

## Commands and diagnostics

To check a file while idle, run
`LCD_PREPARE FILENAME="parts/example.gcode"` to prepare its data, or
`LCD_RENDER FILENAME="parts/example.gcode"` to draw a test card without
starting a print. If the LCD previously failed, `LCD_RENDER` attempts to
initialize it again before drawing. The retry has a timeout and must
succeed before drawing starts. Automatic print preparation still blocks
printing if the LCD has failed.

Use `TJC_LCD_IDLE` to return to Idle and close the menu, `TJC_LCD_INIT`
to initialize the LCD, or `TJC_LCD_CLEAR COLOR=0` to clear it to black.
After initializing or clearing, wait for `tjc_lcd.state: ready`.
You can inspect `lcd_status`, `tjc_lcd` and `tjc_menu` through the printer
object API without triggering any action. Manual LCD commands are only
available when no job is printing or paused.

References: [setup and recovery](docs/TJC_LCD_Setup.md),
[configuration](docs/Config_Reference.md#lcd_status),
[commands](docs/G-Codes.md#lcd_status),
[status fields](docs/Status_Reference.md#lcd_status),
[architecture and history](docs/LCD_STATUS_PLAN.md).

## Automatic Z-offset calibration

> [!WARNING]
> **EXPERIMENTAL — VALIDATE BEFORE PRINTING.**
>
> **DO NOT TRUST A REPEATABLE RESULT AS PROOF OF ACCURACY. AN INCORRECT
> Z-OFFSET CAN DRIVE THE NOZZLE INTO THE BUILD SURFACE AND DAMAGE IT.**
> Keep a backup of your working offset. Verify physical clearance and validate
> the result before accepting, saving or printing. Neither method automatically
> applies or saves an offset.

Keep CR Touch for homing and bed mesh. Use the stock load cell as a separate
nozzle reference, then calculate `CR Touch test_z - nozzle_reference_z` in
Klipper's kinematic coordinates. Choose one method:

| Method | Identifier | Nozzle reference | Result |
| --- | --- | --- | --- |
| **Contact Regression (CR)** | `regression` | Estimate contact from the force/Z withdrawal curve with LCBestFit. | Review a candidate after repeatability and spatial checks. |
| **Force Trigger (FT)** | `force_trigger` | Use Z at the MCU force threshold, 75 g in the supplied configuration. | Review a provisional, report-only offset under load. |

With CR, distinguish the fitted contact position from the MCU stop position.
With FT, expect compression and possible signal delay at the force threshold;
do not interpret that coordinate as zero-force first contact. Neither method
establishes absolute accuracy merely by producing consistent measurements.

Run calibration manually while idle and unpaused. **Do not add it to
PRINT_START, startup or KAMP.** Keep the saved offset and normal adaptive mesh
workflow for printing.

### Configure and calibrate the load cell

Use this fork's host and matching MCU firmware with HX711 acquisition and
analog-trigger support (`CONFIG_WANT_HX71X`, `CONFIG_WANT_TRIGGER_ANALOG`).
Keep your existing `[bltouch]` and configure the sensor **once**:

```ini
[load_cell bed_force]
sensor_type: hx711
sclk_pin: PA4
dout_pin: PC6
gain: A-128
sample_rate: 80
sensor_orientation: inverted
```

Do not add `[load_cell_probe]` or duplicate the sensor pins. Check acquisition
with `LOAD_CELL_DIAGNOSTIC LOAD_CELL=bed_force`: expect no sensor errors or
saturation and approximately 80 samples/s.

**CALIBRATE WITH TARE FIRST. DO NOT COPY ANOTHER PRINTER'S SCALE OR REFERENCE.**
Keep the build plate installed, remove added load and keep the nozzle clear.
Start the guided calibration, then capture the unloaded reference:

```gcode
LOAD_CELL_CALIBRATE LOAD_CELL=bed_force
TARE
```

Wait for the tare response, place a stable known mass at the sensing area,
then run `CALIBRATE GRAMS=<actual_mass_in_grams>`. Enter the actual mass;
do not use unmeasured hand pressure. On any error, run `ABORT` and resolve it
before continuing. After successful calibration, run `ACCEPT`, remove the
mass and review pending settings before `SAVE_CONFIG`.

Save both measured `counts_per_gram` and `reference_tare_counts` through that
workflow. `SAVE_CONFIG` writes pending settings and restarts Klipper. After
restart, check with `LOAD_CELL_READ LOAD_CELL=bed_force` and the diagnostic.
Outside guided calibration, use `LOAD_CELL_TARE LOAD_CELL=bed_force` for an
operational zero; **that command does not calibrate the scale or save the
reference**. Automatic local tares do not replace this sensor calibration.

### Select one method

| Method | Configuration section and API object |
| --- | --- |
| Contact Regression | `auto_z_offset_cr` |
| Force Trigger | `auto_z_offset_ft` |

Copy [auto_z_offset_regression.cfg](config/auto_z_offset_regression.cfg) and
[auto_z_offset_force_trigger.cfg](config/auto_z_offset_force_trigger.cfg)
into your printer configuration directory. Keep the shared `[bltouch]` and
`[load_cell bed_force]` outside these files, then enable **exactly one**:

```ini
# Contact Regression
[include auto_z_offset_regression.cfg]

# Force Trigger
# [include auto_z_offset_force_trigger.cfg]
```

For FT, comment out the CR include and uncomment the FT include. Remove any
duplicate inline backend section before enabling its include. Restart Klipper
while idle after switching. Use `[auto_z_offset_cr]` for CR or
`[auto_z_offset_ft]` for FT, including in any existing include copies.
No MCU reflash is needed to switch methods.

Set physical bed coordinates in `probe_points`, contact speed in mm/s,
`trigger_force` and `force_safety_limit` in grams. The supplied files retain
0.5 mm/s descent, 75 g trigger, 200 g raw limit and `temperature_tolerance: 3`.
Use your validated sensor calibration and force limits; these examples do not
certify safe loading on another printer.

### Run and review

With a clean nozzle and unobstructed bed, run the same command for either
method:

```gcode
AUTO_Z_OFFSET BED_TEMP=60 NOZZLE_TEMP=170
```

- **CR:** heat, clear mesh and run full `G28`; test one paired CR Touch/nozzle
  contact at each of P1/P2, then measure three fresh pairs per point. Use P1's
  median and validate P2 agreement without averaging. Stop on any failed pair,
  invalid fit, or excessive local/spatial spread.
- **FT:** heat, clear mesh and home if needed; take CR Touch readings and three
  force-triggered nozzle measurements at each configured point. Report each
  median, provisional offset and spatial range. Keep P1 as the report reference
  without averaging or enabling acceptance.

Both methods account for CR Touch XY offsets to measure the same physical
point with the sensor and nozzle. Keep mesh corrections out of the calculation.
MCU force/raw limits and native motion bounds remain active. Reaching the
travel endpoint does not count as valid contact. A configured mechanical
minimum alone does not guarantee nozzle clearance if force detection fails.

Check `Auto Z-offset method` in the console. CR reports `Contact estimated Z`
and `Z-offset`; FT reports `Force threshold`, `Triggered Z` and
`Provisional Z-offset`. Compare the references as different measurements,
not interchangeable definitions of first physical contact.

For CR only, after independent validation, explicitly stage with
`AUTO_Z_OFFSET_ACCEPT`. Review all pending settings before `SAVE_CONFIG`;
acceptance alone leaves the runtime offset unchanged. FT is report-only,
without an ACCEPT/APPLY command. No measurement automatically saves settings.

See the [method and command guide](docs/AUTO_Z_OFFSET_PLAN.md),
[Force Trigger details](docs/Auto_Z_Offset_Force_Trigger.md),
[configuration reference](docs/Config_Reference.md#auto_z_offset_cr) and
[command reference](docs/G-Codes.md#auto_z_offset_cr).

### LCD calibration views

Follow CR Touch Reference, Nozzle Contact and Computing Offset on the optional
LCD with either method. Use the existing generic drawing firmware; no reflash
is needed for layouts. Screen updates finish before sensor acquisition.

| Warm-up | CR Touch reference | Nozzle contact |
| --- | --- | --- |
| <img src="Screenshots/autoz_s1.jpg" alt="Auto Z-offset warm-up screen" width="250"> | <img src="Screenshots/autoz_s2.jpg" alt="CR Touch reference screen" width="250"> | <img src="Screenshots/autoz_s3.jpg" alt="Nozzle contact screen" width="250"> |

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
