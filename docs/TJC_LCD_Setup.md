# Ender-3 V3 SE TJC LCD setup

This guide installs the fork's current LCD setup: an idle screen with MCU
firmware identification, knob menus, filament controls, and a static print
card with a 120x120 displayed thumbnail. The owner confirmed the current
appearance on the original STM32F103RET6/TJC3224T1 hardware; see the
[hardware photos](../README.md#hardware-photos).

Use an existing working Klipper, Moonraker and Fluidd installation. Keep
the printer's calibrated motor, sensor, heater and macro configuration.
The stock LCD stays connected to the mainboard; it does not connect to
the Raspberry Pi as a serial device.

## 1. Use the complete fork

The Klippy service must run this fork, including its virtual SD lifecycle
hooks and host extras. Copying only the LCD Python files into upstream
Klipper is insufficient. The development branch is `feature/lcd_status`:

```sh
git clone --branch feature/lcd_status https://github.com/navaismo/klipper.git ~/klipper-dev
cd ~/klipper-dev
```

For an existing checkout, use its actual directory instead of cloning
over it. Verify the checkout used by the running Klippy service in the
`Args:` entry of `klippy.log`; a build in another checkout does not update
the service. Keep Moonraker's updater pointed at this fork and the branch
containing LCD support, with `scripts/klippy-requirements.txt` as its
requirements file. Updating from upstream alone removes this integration.

Do installation, firmware flashing and service reloads only while idle,
with no paused job or active menu operation. Save the working `.config`,
printer configuration and matching host/firmware versions before updating.

## 2. Check or build mainboard firmware

The full setup requires these constants in the **connected MCU**:

| Constant | Required value | Purpose |
| --- | --- | --- |
| `TJC_LCD_VERSION` | `1` | LCD initialization, UART and clear |
| `TJC_JOB_VERSION` | `5` | Existing idle and legacy card transport |
| `TJC_DRAW_VERSION` | `1` | Host-designed screens and firmware/date footer |

If these are already present, design, label, thumbnail-size and menu
updates need only host files and an idle Klipper reload. Do not reflash
for layout changes. The MCU owns bounded drawing operations, framing,
validation, UART timing and completion; designs stay on the host.

For an older firmware build, preserve the working `.config` and run
`make menuconfig` from the fork checkout. The confirmed board settings are:

| Setting | Value |
| --- | --- |
| Extra low-level configuration options | Enabled |
| Micro-controller architecture | STMicroelectronics STM32 |
| Processor model | STM32F103, STM32F103RET6 board |
| Bootloader | 28 KiB, application at `0x08007000` |
| Clock reference | 8 MHz crystal, 72 MHz MCU clock |
| Host communication | Serial USART1, PA10/PA9 |
| Host baud | 250000 |
| TJC LCD on USART2 PA2/PA3 | Enabled (`CONFIG_TJC_LCD=y`) |
| GPIO button support | Enabled (`CONFIG_WANT_BUTTONS=y`) |

The USB-C cable to the mainboard carries the working USART1 host link.
Do not select USART2 for Klipper communication. LCD USART2 uses PA2 TX /
PA3 RX at 115200 baud, 8N1; the driver reserves those pins. The knob uses
PA11/PA12 and PB1, which must not be assigned to another device, USB or CAN.

```sh
make -j2
```

Flash `out/klipper.bin` using the board bootloader's SD procedure. The
owner successfully used the SD filename `vlcd.bin`. This updates the
mainboard, not the LCD panel's own firmware. The build reference is
[stm32f103-tjc.config](../test/configs/stm32f103-tjc.config); do not replace
a working `.config` blindly with a test configuration.

After connecting, inspect `Loaded MCU 'mcu'` and `MCU 'mcu' config:` in
`klippy.log`. Confirm the running revision and all three constants above.
The presence of an updated `out/klipper.bin` or `out/klipper.dict` proves
what was built, not what the board accepted from the SD card. Idle's
small `FW:` footer also shows the connected MCU revision and compilation
date, rather than the host checkout's version/date.

## 3. Install host dependencies

Run the requirements installer in the Python environment used by Klippy.
For the usual environment, from the fork checkout:

```sh
~/klippy-env/bin/python -m pip install -r scripts/klippy-requirements.txt
~/klippy-env/bin/python -c "from PIL import Image; print(Image.__version__)"
```

Adjust the interpreter path to match the service. Pillow is already part
of the normal Klippy requirements; requirement-aware installers/updaters
install it there. Installing it only in a separate diagnostic environment
does not make it available to Klippy. No dependency installation runs
inside a rendering callback.

## 4. Configure the three modules

Copy [sample-tjc-lcd.cfg](../config/sample-tjc-lcd.cfg) into a new file in
the active printer configuration directory, for example `tjc-lcd.cfg`,
then add `[include tjc-lcd.cfg]` to `printer.cfg`. Alternatively, add its
contents directly. Define each section once, including across includes:

```ini
[tjc_lcd]

[lcd_status]
auto_prepare: True
moonraker_url: http://127.0.0.1:7125

[tjc_menu]
encoder_pins: ^PA11, ^PA12
click_pin: ^!PB1
```

`[tjc_lcd]` enables the MCU driver lifecycle, `[lcd_status]` prepares and
renders job metadata, and `[tjc_menu]` registers the knob. Compiling the
firmware alone does not enable menus or knob input. The decoder defaults
to four transitions per detent; switch debounce defaults to 25 ms.

Keep the existing `[virtual_sdcard]` and its correct G-code `path`.
Change `moonraker_url` only for a different server/address. If Moonraker
requires it, add `moonraker_api_key` under `[lcd_status]`. No generic
`[display]` configuration, custom LCD UART pins or host serial LCD port
are needed. The menu loads standard `pause_resume` support; keep existing
`PAUSE`, `RESUME` and `CANCEL_PRINT` macros, including their base renames.

Reload Klipper from Fluidd while idle after deploying the matching files
and configuration. Startup performs handshake/clear and draws Idle once.
All parameters and bounds are in the [configuration reference](
Config_Reference.md#lcd_status).

## 5. Prepare slicer thumbnails and start prints normally

Enable embedded PNG/JPEG thumbnails and normal Klipper metadata in the
slicer. A source of at least 192x192 is preferred. The host downsamples
to 96x96 RGB565 with up to 64 colors, then scales its drawing rectangles
to the card's 120x120 model area. The enlargement preserves those colors
and black-pixel skipping without another image conversion.

Start prints normally through Fluidd/Moonraker (`SDCARD_PRINT_FILE`), or
select a file with `M23` and start with `M24`. With `auto_prepare: True`,
metadata and the entire card finish before the first file G-code. Do not
add `LCD_RENDER`, `LCD_PREPARE` or a CLI invocation to `START_PRINT`.
Metadata/rendering errors prevent start; unavailable thumbnails produce
a text-only card and warnings. Pause/resume does not prepare the card again.

`PRINTING` is orange. The card shows total layers, layer height, total
estimated duration (`hh:mm:ss`, not remaining time), material, filename,
and file first-layer nozzle/bed targets. Targets are not live readings.
Unknown values show `--`; a zero temperature means off. The card stays
static while printing and paused, with no LCD refresh or progress polling.
Completion/cancellation restores Idle after macros and queued moves finish.

## Knob controls and actions

| Context / input | Result |
| --- | --- |
| Idle: turn either way | Open Menu; no printer action |
| Idle: click | No action |
| Menu: clockwise / counterclockwise | Highlight next / previous entry |
| Menu: click | Enter submenu or execute highlighted action |
| Menu: hold for 0.8 s | Return to Idle (`Press&Hold Knob: Idle Screen`) |
| Load/Unload: press | Cancel, clean up and return to Menu |
| Printing: new stable press | Run configured `PAUSE` once; retain card |
| Paused: press or turn | No action; resume through the normal host UI |

Navigation has a 200 ms cooldown after each completed redraw. Rapid turns
share one pending redraw of the latest selection; old highlights are
not replayed. Navigation captured during drawing/actions is discarded.
Action, print-pause and filament-cancel clicks do not use this cooldown.
Pre-print preparation blocks menu input. A press captured for an old job
cannot pause a new job.

| Menu entry | Action |
| --- | --- |
| Home All Axes | Run the configured `G28`, including overrides |
| Preheat - B50, N170 | Set bed to 50 C and nozzle to 170 C, without waiting |
| More Moves... | Open six 10 mm XYZ moves and Back |
| Move Z Up / Down | Z +10 / -10 mm, at 2 mm/s |
| Move X Left / Right | X -10 / +10 mm, at 20 mm/s |
| Move Y Front / Back | Y +10 / -10 mm, at 20 mm/s on this printer |
| Load Filament | Heat nozzle to 240 C; 50 mm at F800, then 40 mm at F400 |
| Unload Filament | Heat nozzle to 240 C; purge 15 mm at F400, retract 90 mm at F800 |

Moves require homing and an in-range full destination; they never home
implicitly. Successful Home, Preheat and Movement actions preserve the
visible menu/highlight without redrawing. Errors appear once; click to
dismiss without repeating the failed action.

Filament speeds are mm/min. The Load/Unload screen shows current nozzle
temperature, phase and `Press to cancel`. Changed temperatures update at
most once per second, only during that operation. Heating has a five-minute
deadline. Extrusion queues at most 1 mm at a time; cancellation waits for
the active drawing transaction or queued segment, then queues no more
extrusion. Completion, cancellation and recoverable errors turn the nozzle
target off and restore G-code modes/extrusion coordinates. The bed target
stays unchanged. Normal heater and extrusion checks remain enabled.

## LCD commands and status

Run manual LCD commands only while idle and unpaused. Quote filenames
containing spaces; paths are relative to Moonraker's `gcodes` root.

| Command | Behavior |
| --- | --- |
| `LCD_PREPARE FILENAME="parts/example.gcode"` | Prepare metadata/thumbnail and report JSON; no LCD traffic or print start |
| `LCD_RENDER FILENAME="parts/example.gcode"` | Prepare/draw a test card and wait for completion; never start printing |
| `TJC_LCD_IDLE` | Wait for queued moves, close the menu and draw Idle |
| `TJC_LCD_INIT` | Request handshake/clear and schedule Idle; inspect status for completion |
| `TJC_LCD_CLEAR COLOR=0` | Request a black clear; requires ready LCD and does not draw Idle |

`COLOR` is optional, defaults to `[tjc_lcd] clear_color`, and accepts an
RGB565 integer from 0 to 65535. Initialization and clear invalidate pending
menu input. Do not send another operation while initialization/clear is
pending; wait for `tjc_lcd.state: ready`. `LCD_RENDER` and `TJC_LCD_IDLE`
wait for the final protocol checkpoint. No extra menu G-code commands are
registered. Full semantics: [G-Codes](G-Codes.md#lcd_status).

The existing Klipper `objects/query` API exposes `lcd_status`, `tjc_lcd`
and, when configured, `tjc_menu`. Reads are passive; they do not trigger
LCD traffic. Check `lcd_status.job.warnings` and `job.thumbnail` for image
preparation, `tjc_lcd.state/error/screen/rendered` for drawing, and
`tjc_menu.page/selected/busy/error` for input/actions. `rendered: true`
means the job transfer/checkpoint completed, not a pixel-level panel ACK.
Prepared image dimensions remain 96x96 in status; the host displays 120x120.
See [status fields](Status_Reference.md#lcd_status) and the
[API example](API_Server.md#objectsquery).

## Troubleshooting, updates and rollback

| Symptom | Check / correction |
| --- | --- |
| Turning the knob does nothing | Include `[tjc_menu]`, verify the running checkout, reload while idle, and start from visible Idle |
| `tjc_menu requires TJC_DRAW_VERSION=1` | Inspect connected MCU constants/version in the log; update old firmware or correct an unsuccessful SD flash |
| Card has text but no thumbnail | Read `job.warnings`; install Pillow in the service environment if requested, or correct the slicer thumbnail/file metadata |
| `No LCD job acknowledgement from MCU` | Inspect token/expected offset/state and last reply in `klippy.log`; while idle retry with explicit `LCD_RENDER` |
| LCD stays in `error` | Idle `LCD_RENDER` performs bounded handshake/clear recovery; `TJC_LCD_INIT` also retries initialization |
| Move rejected | Home the required axes and check the full 10 mm destination against normal limits |

A manual render retries initialization only after a prior LCD error and
does not restart Klipper/MCU or operate heaters/motors. If recovery fails,
the command still fails. Automatic print preparation does not silently
recover an errored LCD and never treats a timeout as success.

For later host updates, keep all matching extras together, update service
requirements if changed and reload only when idle. Do not rebuild/reflash
for visual changes when the required MCU capabilities are already present.
To disable the knob, remove `[tjc_menu]` (or its include) and reload; the
static LCD still works with the other two sections. To undo a host-only
update, restore the previous matching host files and configuration. If a
firmware capability changed, restore a compatible saved host/firmware pair.

Implementation and validation history: [LCD status plan](LCD_STATUS_PLAN.md).
