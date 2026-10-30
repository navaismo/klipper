# LCD print-status project: Ender-3 V3 SE + Klipper

## Objective
Reuse the printer's original LCD **without physically disconnecting it** while Klipper runs. It is a **read-only, static** display. No touch, menus, or printer control in v1.

## Reported MCU firmware on idle (2026-10-29)

The idle screen retains the complete repository URL and shows the firmware
identity reported by the MCU driving the display. The host reads that MCU's
public get_status().mcu_version, populated during serial identification;
it never substitutes the checkout version, out/klipper.dict or the binary
being prepared for flashing. No canvas/coordinate diagnostics are shown.

With mainboard firmware advertising TJC_IDLE_VERSION=1, the original logo and
printer name remain unchanged. The reported build appears centered at y304
in the panel-verified font 1 (8x16), e.g. `FW 43fc76cb 20261029_211120`, fitting
within y319. The complete URL retains its original x12/y207 and font 1 (8x16).
Idle moves up six pixels to y276 when the footer is present so its 12x24 text
does not overlap the firmware line. Legacy idle payloads keep Idle at y282.
Slot 3 of the existing v5 idle text payload carries up to 27 bytes of firmware
identity. The C renderer draws it only when that slot is not the legacy `--`.
Original v5 job cards and idle payloads from older hosts remain supported.

An older MCU without that footer capability cannot reposition its fixed
text slots. The host therefore preserves the URL at y207 and uses the
20-cell model line for `<revision> <MMDD_HHMMSS>`, e.g.
`43fc76cb 1029_211120`. This keeps revision/build identity visible without
reflashing, rather than removing the URL. The displayed year is omitted only
in this compact compatibility mode. Unrecognized versions use a bounded
`FW: <reported version>` fallback; missing identity shows `FW: unknown`.
The full identification string remains available through MCU status.

Adding or correcting the firmware footer requires rebuilding mainboard firmware
with the existing LCD option; panel firmware is unchanged. Until that flash
succeeds, updating/restarting the host while idle still gives the compatible
version-plus-URL layout. TJC_LCD_IDLE redraws manually while idle; startup and
completion/cancellation use the existing one-shot lifecycle. No polling or
LCD traffic is added during printing. The owner reported firmware identity
43fc76cb with build timestamp 20261029_213642 on the physical LCD after flashing.
That build had the URL and firmware positions swapped. The corrected layout
restores the URL and moves the firmware line to the bottom. The owner later
confirmed LCD operation with the new build and supplied Screenshots/Zoffset.jpg,
which shows the footer shifted right and clipped. The assumed 6-pixel width
of font 0 is not valid on this panel. The corrected source explicitly uses
font 1 and centers its 27-byte label within 216 pixels (x12), retaining the
full build timestamp. The owner's 2026-10-30 photo
[idle with firmware](../Screenshots/idle_w_fw.jpg) shows the complete footer
centered below Idle, confirming the corrected layout on the physical panel.

## Idle Z-offset phase highlights (2026-10-29)

The earlier full-card travel animation visibly flickered. The owner supplied
an Auto Z-Offset Calibration design with three rows and requested partial
highlighting instead: CR Touch Reference, Nozzle Contact, Computing Offset.
After reviewing the limitation of v5, the owner explicitly authorized a
mainboard firmware extension and chose host-supplied drawing coordinates.
This supersedes the earlier no-firmware/travel-animation plan. Panel firmware
and static print/idle behavior remain unchanged.

`lcd_canvas.py` approximates the artwork using semantic rectangle/text
operations. `tjc_canvas.c` exposes TJC_CANVAS_VERSION=1 and validates logical
240x320 bounds, fonts 1/3, GBK, token/sequence and session limits. The MCU
retains frame construction and USART2 ownership; host Python never opens
an LCD serial port or submits raw serial frames. A filled rectangle erases
only its specified region. The initial view clears once; phase transitions
repaint only previous/current row bounds and the small status strip.

AUTO_Z_OFFSET warms to default 50/170 C, clears the active mesh, homes XYZ,
tests P1/P2 and then takes six fresh pairs. Existing explicit configuration
or command temperatures override the defaults. The separate manual commands
retain homing/mesh prerequisites. All three commands share phase highlighting.
Complete each update and its final handshake/50 ms settling before sampling;
UART and canvas timer stop afterwards. No periodic animation or screen
updates run during CR Touch, ADC baseline/tare, force contact or contact fit.
Computing Offset highlights final result validation. Errors retain detailed
console/log messages and show a bounded static failure view.

Canvas sessions use <=511 drawing operations, one pending operation at a
time, at most one LCD frame per task, 16 ms pacing per ten frames, five-second
MCU inactivity timeout and a 30-second host deadline. Printing/paused states
reject drawing, and a calibration reserves the LCD against idle callbacks or
new print preparation. Original v5 cards/thumbnails are unchanged. New
mainboard firmware is required once, under the existing CONFIG_TJC_LCD flag;
no panel firmware update or Pillow dependency is introduced. An old MCU
without the capability gives a console warning and no blinking fallback.

The owner has since confirmed a complete Auto Z-offset cycle with the LCD
active and supplied physical warm-up/CR Touch/nozzle-contact photos for the
README. This confirms phase-screen operation, not print-quality measurements.
No printer operations have been performed by the agent.

### Canvas session clock initialization fix (2026-10-29)

The owner reported AUTO_Z_OFFSET stopping at Test P1 pair 1 with
`LCD canvas failed: No LCD job acknowledgement from MCU` after warm-up.
Native replay found that canvas begin zeroed next_frame without setting it
to the current MCU clock. Signed rollover-safe comparison can treat zero as
a future time during half of the 32-bit clock cycle. Both the initial view
and a partial phase update could then stall until the host's three-second
ACK deadline, before the MCU's five-second inactivity error.

Canvas begin now initializes next_frame from timer_read_time(), as the job
renderer already does. Native tests reproduce both failures before the fix
and require prompt begin acknowledgements after a simulated warm-up gap;
existing rollover, UART, fence and timeout checks remain active. This is a
mainboard C correction requiring rebuild/flash under the existing LCD option;
no timeout increase, sensor algorithm or panel firmware change is involved.
The owner subsequently confirmed a full two-point cycle with LCD updates
and no ACK errors after deploying the fix. No hardware actions were executed
by the agent.

## Agreed architecture and physical connection
- **Owner-confirmed connection:** the LCD is connected by USART to the printer mainboard. It is not connected to the Raspberry Pi and is not a host serial device.
- **Mainboard C driver:** own USART2 initialization, TX/RX, handshake, LCD packet framing, clear, text, drawing primitives, and execution of the render. Adapt the existing Marlin C++ routines into Klipper MCU C code.
- **Host Python:** obtain job metadata and prepare image payloads, then deliver data/requests through the existing Klipper host-to-MCU protocol. It does not directly open an LCD serial port. The MCU is an LCD controller, not merely a transparent UART bridge with a Python LCD driver.
- Python serial tools in `DWIN_OptimizationReference/` are historical protocol/optimization references only. Their direct-serial test setup does not describe the printer's current connection.
- This decision supersedes earlier proposals for a Python LCD driver, direct Raspberry Pi serial access, or alternate hardware transports. Do not reopen those alternatives without a new user decision.

### Desired on-screen information
- Current job thumbnail, initially 96x96 using the prior rendering work as reference.
- Total layer count.
- Print layer height.
- Estimated total print time from file metadata, clearly identified as an estimate.
- Filament/material name (e.g., PETG).
- File basename and initial-layer nozzle/bed temperature targets from the G-code. The owner explicitly chose targets only; no live current/target pairs.
- Render the complete card before printing starts. It remains unchanged throughout the print, including pauses. No live progress, current-layer, elapsed/remaining-time, or Z updates.
- At startup/idle and after the print finishes, render a static approximation of `Screenshots/IdleScreen.png`. Do not periodically redraw it.
- The start workflow prepares and transfers the card, then waits for the MCU's final protocol checkpoint before starting the print. The completion workflow must wait for pending movements before rendering the idle image; the host now schedules that transition after `toolhead.wait_moves()`. The handshake checkpoint is not a documented pixel-level panel acknowledgement. A `printing` notification alone is insufficient to guarantee pre-print drawing.

## Cancellation reset fix and metadata spacing

A cancel macro can execute SDCARD_RESET_FILE after CANCEL_PRINT_BASE.
Previously, that reset invalidated the pending idle callback and left the
job card visible. A ready LCD still showing a job card now queues a fresh
idle render after reset, including macros that reset without the base
cancel command. The callback acquires the G-code mutex and waits for all
pending movements; a newly starting job supersedes it. Pauses and active
prints still prevent drawing. The fix is isolated to the LCD host module;
virtual_sdcard and pause_resume cancellation behavior is unchanged.

Metadata text moves to x126, leaving eight pixels after the 16-pixel icons
at x102. Fourteen GBK cells fit within the panel. Height strings now use
`Height: 0.18mm`, preserving a single line for common values. These visual
changes require updated host text preparation and MCU firmware; the
application format remains v5. They have not yet been tested on hardware.

README files, documentation and code comments must always be written in
English; the owner's conversation language remains Spanish.

Offline validation: all 115 focused Python/native-C tests pass. The
cancellation integration test executes the actual CANCEL_PRINT handler
and virtual SD reset with fake printer/MCU dependencies, checking that
macro completion and motion draining precede LCD traffic. Reset-only
cancel macros and a new print superseding that idle render are covered.
The STM32F103 firmware build succeeds with the owner's settings; the
binary is 50096 bytes and remains protocol v5. Updated firmware and its
SHA256 manifest are in /tmp/klipper-tjc-build-printer (deployed: false).
Source whitespace checks pass. No printer actions or service restarts
were performed; physical validation remains pending.

## Final hardware confirmation and public setup guide

The owner confirmed success with the final layout and supplied
[final idle photo](../Screenshots/final_idle_lcd.jpg) and
[final job card photo](../Screenshots/final_card_lcd.jpg). The degree
symbols, balanced filename rows, Height label and lowered metadata are
now owner-confirmed on hardware. This does not replace timing or print
quality measurements. README.md now documents the fork, compilation,
host requirements and printer.cfg setup above the original Klipper README.
Views remain intentionally static to avoid LCD work during printing.

Intermediate preview images remain local under .gitignore. The original
design references and final physical photos are eligible for publication.

## Final card adjustments after v5 hardware validation (2026-10-22)

The owner supplied Screenshots/idleLCD-v5.jpg and cardLCD-v5.jpg, confirming
startup idle and the v5 card on the real panel. The owner reports a small,
almost imperceptible speed improvement and better perceived image quality;
this is feedback, not a measured render-time benchmark. The idle layout
and thumbnail transfer remain unchanged in these final card adjustments.

- Host label is now `Height:`. C metadata uses explicit font 1 at x=118,
  with 15 GBK cells per row; `Height: 0.12 mm` fits on one line.
- Metadata and its four icons move down eight pixels to y=16/52/88/124.
  The separator moves to y=162, allowing two-line material/height values
  to end by y=158. Filename rows remain y=174/192.
- Names longer than 27 GBK bytes split into two balanced centered rows,
  preferring underscores/spaces near the midpoint that keep both rows
  within 27 bytes. The boundary separator is omitted. With no suitable
  separator, balance at a valid GBK boundary; short names use one row.
  `Angel_0.12mm_PLA_Klipper_1h23m` displays as `Angel_0.12mm_PLA` and
  `Klipper_1h23m`. Full source names remain unchanged in host metadata.
- The real photo shows the degree position blank, although host strings
  already contain GBK A1 E3. C replaces that glyph with one blank ASCII
  cell in the outgoing temperature text, then draws a white 7x7 hollow
  degree ring with eight bounded rectangles. The MCU does no numeric
  formatting. Unknowns remain `--`; zero has a degree ring as usual.
  Work stays paced, and the existing CRC/final handshake gates print start.

Validation: all 112 focused Python/native-C tests pass, including actual
C frame/pixel checks for degree rings, missing/zero targets, balanced
underscore/space/GBK-safe filename wrapping and single-row Height values.
The STM32F103 ARM build succeeds with the owner's settings; binary size
is 50096 bytes. The development firmware and refreshed SHA256 manifest are
in /tmp/klipper-tjc-build-printer. Source whitespace checks pass. No
production files, services, printer commands or flashing were performed.

These are compatible visual changes within payload v5, not a new wire
format. Update host extras for `Height:` and rebuild/reflash MCU firmware
for wrapping, positions and degree primitives. The owner subsequently confirmed this refined appearance; see the final
photos above. Owner's final photo: [final job card photo](../Screenshots/final_card_lcd.jpg).

## Initial v5 refinement from physical photos (history) (2026-10-22)

`Screenshots/idleLCD.jpg` and `Screenshots/cardLCD.jpg` confirm the owner's
v4 renderer draws on the real panel. These photos show clipped URL/text;
font 0 having the assumed 6-pixel cells was not established on hardware.
V5 selects font 1 explicitly (8x16), already used for the filename, and
positions/wraps text using that cell width. The full URL is 27 bytes and
starts at x=12. The new v5 layout still needs a physical check.

The host retains startup idle intent through the MCU clearing state;
previous logic incorrectly required the preceding state to be initializing.
It also handles idle_timeout:idle once when ready, not printing/paused
and not already showing idle. Motion draining, G-code mutex ownership,
new-job cancellation and no periodic refresh remain in effect.

Thumbnail preparation uses 64 colors and a larger source when available,
then the same 96x96 RGB565/black rules. V5 image data starts with codec 0
(raw RGB565BE, exactly 18433 bytes including prefix) or codec 1 (six-byte
records: uint16 color, uint8 x0/y0/x1/y1, inclusive image-local 0..95).
Codec 1 omits black, merges rectangles and orders them globally by color.
An all-black image is the single codec byte 1. Use raw fallback whenever
rectangle encoding would be larger. Transport compression is lossless
relative to the prepared RGB565 image; quantization is a separate step.
MCU validation rejects unknown codecs, invalid sizes and out-of-bounds
rectangles. CRC, pacing, final handshake and print-start gating remain.

On test/fixtures/lcd_thumbnail.gcode, the previous 32-color preparation
used 18432 raw wire image bytes; v5 uses 7033 bytes with 64-color preparation
(about 62% fewer image bytes). Actual RGB565 colors increase from 16 to 41.
A frame-count model drops from 1627 to 1212 image/palette commands (about
25% fewer), including the extra colors. These are offline byte/count
results, not measured panel render times; complex images can use fallback.
Chunks increase from 32 to 40 bytes within Klipper's maximum payload.

Deployment requires matching v5 host and MCU updates and regenerated .bin
jobs. Preserve the owner's bootloader/clock/USART configuration. No printer
configuration option, baud or panel pacing changes are required.

### Initial v5 offline validation (history)

All 108 focused Python/native-C tests pass. Coverage includes the real
initializing/clearing/ready transition, complete idle URL and bounds,
idle-timeout/pause/print behavior, compressed image pixel equivalence,
cross-chunk GBK/rectangle records, raw fallback, codec errors, CRC, timing
and maximum Klipper message size. Source whitespace and scoped Git diff
checks pass. Tests use the existing Pillow in /tmp/klipper-lcd-test-deps;
no service environment was changed.

The final STM32F103 ARM build succeeds with the owner's configuration and
CONFIG_TJC_LCD=y. Firmware is /tmp/klipper-tjc-build-printer/klipper.bin;
the adjacent manifest records version, size, SHA256 and deployed: false.
Its development version uses Git revision ee2c6fb6 with a dirty-lcd-v5-dev
suffix through a temporary version wrapper to avoid the slow SSHFS Git
dirty scan; repository build scripts were not changed. Full Klippy
service regressions and physical font/timing tests remain pending.

## Yellow labels and idle artwork (2026-10-22)

The owner selected `Screenshots/IdleScreen.png` as the idle composition.
It is approximated with C drawing primitives in logical 240x320 pixels:
red/blue nozzle-shaped logo, red K and white lipper, centered printer name,
repository URL, blue separators and corner strokes, cyan check and Idle.
The source PNG is a visual reference, not an asset loaded from the host
at runtime. Host-supplied strings use existing panel fonts; exact glyph
appearance and timing still need a physical test.

Job labels Layers, Height, ETA, Mat, Nozzle and Bed are yellow
RGB565 `0xffe0`; their values and units remain white. Only styling occurs
in C: all text and numbers still come ready-to-draw from the host.
Metadata uses explicit font 1 (8x16), 15 GBK bytes per line at x=118;
`Height: 0.18 mm` fits on one line. Labels remain yellow.
The four metadata icons are cyan, and the material label is `Mat:`.

Lifecycle is event-driven, with no periodic redraw:

1. After startup handshake/clear succeeds, queue one idle screen.
2. Before a new print, invalidate queued idle callbacks and wait for any
   in-flight idle render before sending the job card. The first G-code
   still waits for the final MCU checkpoint. A file selected while idle
   is drawing does not abort initialization or fail the next print.
3. `virtual_sdcard:finished(state)` fires only after the worker stops,
   for complete/cancelled files. The callback acquires the G-code mutex to let end/cancel macros finish
   queuing movements, then waits for `toolhead.wait_moves()`
   before rendering idle and holds that mutex through rendering; it sends no
   motion/heater commands. A new print,
   reset or disconnect supersedes a queued/waiting callback.
4. A pause or print error preserves the card. `TJC_LCD_IDLE` manually draws
   the idle screen when safe, after waiting for any queued movements.
   `TJC_LCD_CLEAR` remains an explicit clear without an automatic redraw.

The host reports `tjc_lcd.screen` as blank, job, idle or null (unknown,
rendering, error or disconnected). `rendered` remains specific to job cards;
an idle render clears it and `job_filename`.

Protocol v5 retains the exclusive idle flag 128 to the existing 34-byte header;
normal job flags and dimensions retain their meaning; image encoding and
text limits change as specified below.
Idle transfers have flags exactly 128, zero image dimensions/bytes and
nine nonempty GBK slots: Klipper, Ender 3 V3 SE, the repository URL, --,
Idle, --, --, --, --. Only slots 0/1/2/4 are drawn; unused slots retain
validation consistency. Idle limits additionally cap slot 0 at 12 bytes
and slots 1/4 at 20 to keep their larger fonts on screen. Slot 2 permits
the complete 27-byte URL in font 1 at x=12/y=207 (ending at x=228). Both layouts use
the same chunk acknowledgements, CRC, pacing, timeout and final handshake.
Rebuild/reflash with `CONFIG_TJC_LCD=y` and update host extras plus the
fork's `virtual_sdcard.py` together. No new configuration section is needed.
Older exported binary payloads must be regenerated.

Final physical photos supplied by the owner:
[final job card photo](../Screenshots/final_card_lcd.jpg) and
[final idle photo](../Screenshots/final_idle_lcd.jpg).
No production files/services or printer hardware were changed by this work.

## Known hardware and runtime
- Printer: Creality Ender-3 V3 SE.
- Main MCU: STM32F103RET6; Klipper firmware in production.
- LCD: TJC3224T1, 3.2-inch, 320x240, connected to the mainboard USART as confirmed by the owner. The previous Marlin driver uses logical coordinates 240x320. Use that known implementation and its UART mapping as the reference; check compatibility with production Klipper's peripheral allocation before implementation. Do not substitute a generic Nextion, DWIN, or standard Klipper character LCD driver.
- Host: Raspberry Pi 3B+ (about 1 GB RAM), Debian 13 aarch64, Klipper + Moonraker + Fluidd + Crowsnest.
- Owner clarified that the Raspberry Pi connects by USB-C with serial transport. The connector alone does not identify the MCU host peripheral or a USB-to-UART bridge. Check the actual firmware configuration; USART2 remains assigned to the LCD.
- The development checkout is accessed from Fedora VS Code over SSHFS and is separate from the stable production checkout.
- Slicer: OrcaSlicer. Filament/file metadata and G-code headers need inspection before assuming fields are provided.

## Previous related implementation in customized Marlin
The owner developed LCD customizations in a Marlin 2.1.x fork for this printer. Useful historical clues, **not** APIs automatically present in Klipper:
- `O9000` (LCD settings/control K1..K6)
- `O9001` (ET/PG/CL)
- `O9002` (96x96 image transfer)
- `M5000` (print job card)
- `M73 P/R` print percent/remaining time
- Layer-change comments / thumbnail metadata in prior tooling
- Additional customized LCD menus and mesh visualizer.

Source paths below are relative to the customized Marlin reference checkout.
Remote reference: https://github.com/navaismo/Marlin_bugfix_2.1_E3V3SE/tree/c13_sd

### Source analysis and dependencies (2026-10-21)
These are source-confirmed findings and historical test notes, not new live hardware tests:

- `Marlin/src/lcd/dwin/creality/dwin_lcd.cpp` implements packet construction, handshake, and drawing. Packets start with `AA` and end with `CC 33 C3 3C`; words are big-endian. `DWIN_Handshake()` starts the serial port at 115200 by default, sends command `00`, and checks for an `AA 00 4F 4B` reply prefix.
- `dwin_lcd.h` supplies the declarations, `FHONE`, and logical dimensions 240x320. `dwin.h` supplies font and color constants. Extract only the constants/primitives needed for the static card; do not import the menu, motion, heater, or other printer-control dependencies of the full UI.
- `Marlin/src/inc/Conditionals-2-LCD.h` defaults `LCD_SERIAL_PORT` to 2 for `DWIN_CREALITY_LCD`. `Marlin/src/HAL/shared/serial_ports.h` maps this to `MSerial2`; `Marlin/src/HAL/STM32/MarlinSerial.h` and `.cpp` supply the Arduino/STM32 serial implementation. The serial wrapper defaults to 8N1.
- `buildroot/share/PlatformIO/variants/MARLIN_F103Rx/variant.h` and `PeripheralPins.c` map USART2 TX to PA2 and RX to PA3 without remapping. This is the software mapping to use as the hardware reference, subject to checking production Klipper does not already allocate those resources.
- `DWIN_Set_Color()` uses `40`, `DWIN_Fill_Rect_Raw()` uses `5B`, and `DWIN_Draw_String()` uses `98`. The text routine includes coordinates, font/mode, size, foreground/background colors, and string bytes.
- `DWIN_Frame_Clear()` in Marlin sends `01` plus a color, but historical tests in `DWIN_OptimizationReference/tjc_dwin.py` document that generic opcode as ineffective on the tested panel and implement clear with a palette command followed by `52`. Resolve this specific discrepancy before selecting the clear sequence; a named function alone does not prove a command works on this panel. A full-screen fill is another existing primitive to assess.
- `DWIN_Frame_SetDir()` is a no-op in this source. `DWIN_Startup()` is declared in the header; the inspected LCD/UI source does not provide an implementation. The full UI's `HMI_Init()` in `dwin.cpp` is coupled to SD, language, boot settings, and icon animation; do not port that entire startup flow.
- `Marlin/src/e3v3se/ThumbRender.cpp` owns the thumbnail implementation: `E3V3SE_THUMB_RAW16` input, row RLE, vertical merging, and color-grouped fills. Its SD `card` access, Arduino delays, watchdog refreshes, C++ references, and static buffers need adaptation rather than verbatim copying.
- The thumbnail implementation throttles every 10 frames with a 16 ms delay. Treat these as historical Marlin settings, not validated Klipper timing. Preserve pacing with a nonblocking MCU state machine/tasks/timers, with bounded work and buffers. Static pre-print rendering still must not block Klipper communications or its scheduler.
- `DWIN_OptimizationReference/` supplies optimization and benchmark references, including Python and C++ tools; do not run its serial tools against the printer during source analysis.
- `src/stm32/serial.c` in Klipper is the host communication driver, not a secondary LCD UART driver. Implement USART2 for the LCD separately and check peripheral/pin/IRQ ownership against the actual firmware configuration.
- The owner identifies the MCU as STM32F103RET6; historical Marlin benchmark notes identify GD32F303RET6 under an STM32-named build environment. Keep that distinction visible until the actual chip/configuration is confirmed; it does not change the owner-confirmed mainboard-to-LCD connection.

## Discover before coding
1. Inspect current Klipper tree: MCU UART, host display and status modules; locate potential supported mechanisms and limitations.
2. Inspect existing `printer.cfg` (read-only), MCU identifiers and relevant LCD/wiring config if available. Do **not** alter production config.
3. Trace the Marlin USART2 initialization and pin mapping through its dependencies; compare with production Klipper configuration for resource conflicts. The physical mainboard-to-LCD USART connection is already confirmed by the owner.
4. Define a modular C LCD driver on the mainboard and a small host module that supplies data over Klipper's existing protocol. Keep the LCD on its original connector without new serial hardware.
5. Inspect Moonraker metadata and a representative OrcaSlicer G-code for thumbnail, total layers, layer height, estimated total time, and material. Missing values remain unknown; live layer tracking is outside this scope.
6. Design nonblocking transfer and render completion signaling. Prepare/cache data before printing; send no LCD updates during the print. Keep host and MCU work bounded even during pre-print rendering.
7. Propose hardware-safe test plan with mocked serial/LCD transport before live use.

## Proposed stages (subject to verification)
- Stage 0: finish source/dependency analysis, confirm production resource allocation, and review the MCU/host command contract.
- Stage 1: C USART2 driver, handshake, verified clear sequence, and one static text; prepare mocks and rollback before live validation.
- Stage 2: render the custom idle image, then the pre-print text card from file metadata.
- Stage 3: add the optimized 96x96 thumbnail and explicit render completion before starting the print.
- Stage 4: integrate startup/pre-print/completion transitions, caching, recovery, and measured performance. Keep the display static during printing.

## Startup C driver and host lifecycle (2026-10-22)

Implemented in this development checkout. The owner has since confirmed
that startup clearing and the original text/thumbnail card work on the
physical LCD. The revised v5 layouts below still need a physical test:

- `src/stm32/tjc_uart.c`: dedicated USART2 on PA2 TX / PA3 RX, 115200
  baud, 8N1; 64-byte TX buffer and 64-byte RX ring (63 usable bytes).
  Interrupt handlers do bounded work, detect framing/noise/overrun and
  overflow, and keep LCD traffic separate from the host protocol handler.
  Hardware TC confirms that the final stop bit has left the UART.
- `src/tjc_lcd.c`: C frame builder and timer/task state machine for
  startup, handshake response parsing, palette and clear. A 5 ms timer
  wakes a task only during an operation. Each task invocation consumes
  at most 32 reply bytes, without busy waits or Arduino delays.
- `klippy/extras/tjc_lcd.py`: separate `[tjc_lcd]` host lifecycle module.
  At `klippy:ready`, it schedules one initialization request over the
  existing Klipper MCU connection. Moonraker reconnections do not redraw
  the LCD. Python constructs no LCD frames and opens no LCD serial port.

`CONFIG_TJC_LCD` is off by default and limited to STM32F103 builds other
than the small x6 variant. Enabling it reserves PA2/PA3 in the firmware
dictionary. Kconfig and a C guard prevent sharing USART2 with the host
link, including the PD5/PD6 remap. A configuration-time check also rejects
an already allocated USART2 peripheral clock. Only one LCD is supported
per MCU. The UART stays disabled until an initialization request.

Startup sequence:

1. Wait `startup_delay` (default 1 second) on the MCU, then send
   `AA 00 CC 33 C3 3C`.
2. Accept a complete reply beginning `AA 00 4F 4B`, followed by optional
   version text and the `CC 33 C3 3C` tail. Leading noise and fragmented
   reads are supported; the reply body is capped at 48 bytes. A prefix
   alone does not authorize clearing. Retry timed-out handshakes up to
   three total attempts, each with `handshake_timeout` (default 1 second).
3. Send palette `AA 40 FF FF <color-be16>` plus tail, wait for wire
   completion, then bare `AA 52` plus tail. Default color is black.
   This is the historically tested panel sequence, instead of Marlin's
   disputed `0x01` clear. No automatic fallback tries unverified opcodes.
4. After the clear's last stop bit and 50 ms settling, report `ready`.
   Stop the UART and operation timer on success or error. A manual
   operation can re-enable them. Shutdown also releases the singleton
   reference so a later MCU `config_reset` can allocate a new instance.

`ready` confirms a complete handshake and transmitted clear frames, not
that the screen is visibly clear or that panel rendering is finished.
The owner confirmed that this clear sequence works at startup with the
new firmware. Drawing and the final job checkpoint still need their own
physical validation; no job-render hardware test was performed here.

The host publishes `tjc_lcd.state` and `tjc_lcd.error` without polling.
MCU responses are handed from the serial thread to the reactor, and a
host timeout bounds a missing MCU completion response. Ordinary LCD errors
do not shut down Klipper. A new virtual SD print waits for a pending LCD
operation and requires `ready` before executing the first file G-code;
a failed LCD prevents that start request. `TJC_LCD_INIT` retries while
idle. `TJC_LCD_CLEAR [COLOR=<0..65535>]` clears an initialized LCD while
idle. Both reject printing, paused jobs and overlapping operations.
Pause/resume never repeats initialization.

Implemented Klipper MCU messages (semantic commands, not a UART bridge):

| Direction | Message | Meaning |
| --- | --- | --- |
| Host to MCU, config | `config_tjc_lcd oid=%c startup_ticks=%u timeout_ticks=%u color=%hu` | Allocate the LCD and configure timing/color |
| Host to MCU | `tjc_lcd_start oid=%c` | Start handshake and clear |
| Host to MCU | `tjc_lcd_clear oid=%c color=%hu` | Clear an initialized LCD |
| MCU to host | `tjc_lcd_state oid=%c state=%c error=%c` | State transition or terminal result |

States: idle=0, handshake=1, clearing=2, ready=3, error=4. Errors: none=0,
timeout=1, UART/overflow=2. The streaming job extension is documented
below. The idle logo is implemented as described above; a documented panel render acknowledgement remains unverified.

### Static job rendering extension (2026-10-22)

The owner's first hardware test confirmed startup clearing, followed by
no card on print start. The installed version only implemented startup;
the following missing host-to-MCU/render path is now added in development.

With both `[lcd_status]` and `[tjc_lcd]` enabled, automatic pre-print
collection now calls `tjc_lcd.render_job()` before the virtual SD work
timer is scheduled. No `START_PRINT` change is needed. For an idle test
without printing, use `LCD_RENDER FILENAME="parts/example.gcode"`.
`LCD_PREPARE` and the standalone CLI still only prepare data.
`LCD_RENDER` reports whether the result included a 96x96 thumbnail or
was text-only, followed by its warnings. Automatic pre-print preparation
logs the same warnings. If text appears but the image does not, query
the existing `lcd_status.job.thumbnail`, `.transfer.size` and `.warnings`
first. A null thumbnail means image bytes were unavailable in that host
preparation; Pillow must be installed in the Python environment running
Klippy, even if a separate CLI test environment already has it. A full
image has 18432 prepared RGB565 bytes; its wire image is compressed when
that saves space (see transfer.image), or 18433 bytes with raw codec prefix. These status reads do not redraw.

The semantic MCU protocol extension is advertised as `TJC_JOB_VERSION=5`:

| Message | Purpose |
| --- | --- |
| `tjc_lcd_job_begin oid=%c token=%u size=%hu crc=%u` | Claim the initialized LCD for one bounded job |
| `tjc_lcd_job_chunk oid=%c token=%u offset=%hu data=%*s` | Consume up to 40 bytes at the expected offset |
| `tjc_lcd_job_end oid=%c token=%u` | Verify size/CRC and finish drawing/checkpoint |
| `tjc_lcd_job_abort oid=%c token=%u` | Stop the job's UART and timer |
| `tjc_lcd_job_result oid=%c token=%u offset=%hu state=%c error=%c` | Correlated acknowledgement or terminal result |

Result states are receiving=1, rendered=2, error=3. Errors are busy=1,
format=2, offset=3, CRC=4, UART=5, timeout=6, abort=7. The host permits
one outstanding block and requires the matching token, offset and result
state. Each acknowledgement has a 3-second host timeout; total rendering
is limited to 180 seconds. MCU inactivity is limited to 5 seconds. A
failure aborts the start request and stops drawing; retry initialization
while idle after correcting the failure. Old startup-only firmware still
supports startup but explicitly rejects card rendering until rebuilt.

`src/tjc_job.c` incrementally parses the 34-byte v5 header, nine bounded
GBK fields, and a codec-prefixed image across arbitrary chunk boundaries. It checks
magic/version/flags/lengths/dimensions, offsets, GBK character boundaries,
and CRC32. Text and partial image spans may already be visible before the
final CRC check; an invalid transfer never authorizes printing.

The v5 renderer follows `Screenshots/RenderJob.png` in the panel's logical
240x320 coordinates. It clears to dark navy (`0x0863`), draws outlined blue
panels and small code-native layer/height/clock/spool/nozzle/bed icons.
The 96x96 thumbnail sits at x=2/y=28 on a black inset; its border ends
at x=99. Metadata starts at x=118 and y=16/52/88/124 in explicit font 1
(8x16). Each field wraps at 15 GBK bytes, without splitting characters,
to at most two lines 18 pixels apart. A separator is at y=162.
The basename, without .gcode/.gco/.g, is centered at y=174, optionally y=192, in font8x16, limited
to 52 GBK bytes. The bottom panel spans y=226..312; targets are centered
in font12x24 at y=264 with host-supplied Nozzle/Bed captions at y=296.
Only objectives are shown, without the reference image's current/target
pair. These panel fonts and exact appearance need physical validation;
the image is a composition reference, not a font asset stored on the LCD.
All labels, units and `hh:mm:ss` text come from the host payload.

The host skips black RGB565 zero, merges equal horizontal runs vertically
and groups rectangles globally by color. The MCU consumes six-byte records
and emits bounded 0x40 palette / 0x5B rectangle frames; it owns placement,
coordinates, UART and protocol framing. Raw fallback uses the previous
two-row span renderer (up to 96 runs per row and one 192-byte row), without
a framebuffer. Neither mode blocks tasks or changes the 115200 baud rate
or the 16 ms pause after every ten frames.

After all drawing frames leave the UART, a trailing handshake and 50 ms
settling interval provide an ordered protocol checkpoint. This follows
the historical handshake timing reference, but its relationship to final
panel scan-out is not established by mocks. Both job timer and UART stop
before the final result permits the first print G-code. No traffic or
redraw is scheduled during printing or pause. End-of-print idle artwork is scheduled after pending motion finishes.

Deployment needs the updated host extras and rebuilt/flashed firmware,
keeping the existing `CONFIG_TJC_LCD=y` and supplied bootloader/clock/
USART1 settings. After an authorized update, first run `LCD_RENDER` while
idle and inspect `tjc_lcd.rendered`, `job_filename`, errors and the visible
card. These updates have not been deployed or flashed by the agent.

### Validation and first physical test

- Full ARM builds succeed with LCD enabled for STM32F103 with host
  USART1 and, separately, native USB. Builds were isolated in
  `/tmp/klipper-tjc-build-usart1/` and `/tmp/klipper-tjc-build-usb/` on
  the x86_64 development machine, using the installed PlatformIO ARM
  GCC 12.3.1 toolchain. System ARM GCC lacked newlib headers; no packages
  were installed. These generic test binaries are **not** flash images
  prepared for the production printer's bootloader/clock configuration.
- The USART1 dictionary reserves PA9/PA10 for the host and PA2/PA3 for
  the LCD, and contains all four messages above. Kconfig checks verify
  opt-in defaults and exclusion of USART2 host links.
- The focused suite now passes 96 tests, including 15 startup-driver checks.
  Two compile and run the actual C sources with fake scheduler/registers;
  the protocol harness covers ten scenarios: frame ordering, fragmented
  and oversized replies, retries/timeouts, rollover, UART errors/stalls,
  duplicate starts, shutdown/config reset and manual clear. UART mocks
  additionally check final-stop-bit handling, RX overflow and re-enable.
  Host mocks cover lifecycle, start gating, failure recovery and guards.
  Additional native job tests reconstruct a simulated panel pixel for
  pixel for solid, black, alternating adjacent colors, random spans and
  GBK text wrapping. CRC expectations come from Python zlib independently
  of the MCU algorithm. Host tests cover transfer correlation, missing/
  stale acknowledgements, abort, metadata-to-render integration and
  rendering before virtual SD schedules the first G-code.
- Full Klippy imports/regressions were not run: local Python lacks
  `greenlet` and `cffi`, and the complete upstream dictionary collection
  is unavailable. Mocks do not validate hardware timing or a real service.

The owner subsequently supplied the installed Klipper build configuration
on 2026-10-22. It selects STM32F103 (`stm32f103xe`), 72 MHz with an 8 MHz
crystal, a 28 KiB bootloader (application at `0x08007000`), and host USART1
PA9/PA10 at 250000 baud. Flash linker length remains `0x10000`, RAM
`0x5000`, and stack 512 bytes. These working settings are preserved;
the only new feature selection is `CONFIG_TJC_LCD=y`.

The working Marlin configuration on this same board/display confirms
LCD USART2 TX PA2 / RX PA3, 115200 baud. No new wiring measurements are
needed. `test/configs/stm32f103-tjc.config` now includes the supplied
bootloader, clock and host-interface selections for build validation.
Production `printer.cfg` and includes remain unavailable locally; check
for other PA2/PA3 assignments before activation. Firmware pin reservation
also rejects conflicting configured uses when Klippy connects.

The earlier startup-only build in `/tmp/klipper-tjc-build-printer/` succeeded
with these supplied settings and `CONFIG_TJC_LCD=y`. The binary is 43236
bytes, within the configured 64 KiB application linker length. ELF and
binary checks confirm the vector table at `0x08007000`, initial stack at
`0x20005000`, host USART1 at 250000, and separate LCD USART2 at 115200.
`manifest.json` alongside `klipper.bin` records the size, SHA256 and
verified constants. This is a prepared development artifact; it has not
been flashed or tested by the agent. The owner subsequently confirmed
startup clearing on the LCD. The earlier streaming-render build in the
same directory was 46508 bytes and advertised `TJC_JOB_VERSION=2`; its
manifest was regenerated for that original v2 card. The owner subsequently
confirmed text and thumbnail rendering after installing Pillow. The v5
layouts require another build and matching host update before testing.

For a separately authorized deployment, build the matching firmware,
deploy the host module and add this independent section alongside
`[lcd_status]` if used:

```ini
[tjc_lcd]
mcu: mcu
startup_delay: 1.0
handshake_timeout: 1.0
clear_color: 0
```

After the separately authorized flash/restart with the printer idle,
check the handshake, visibly black screen, `tjc_lcd.state/error` and logs.
Then test manual clear/retry while idle. No print motion is needed.
If clear has no visible effect or handshake fails, stop before adding
text or thumbnails and compare a UART trace against the reference.

Rollback: remove `[tjc_lcd]` and reload only when authorized and idle to
stop host requests. Rebuild without `CONFIG_TJC_LCD` or restore the saved
production firmware in a separately authorized maintenance step to
release the firmware's PA2/PA3 reservation. No production firmware,
configuration, services or printer hardware were changed by this work.

## Host data module (2026-10-22)

`klippy/extras/lcd_status.py` implements host-only collection and prepares
image/job data with `lcd_thumbnail.py` and `lcd_transfer.py`.
It does not implement or replace the agreed C mainboard driver.
This module does not construct LCD serial frames; it delegates prepared
job rendering to `tjc_lcd`. Automatic preparation uses the pre-print hook
described below.

- `[lcd_status]` defaults to `auto_prepare: True` and also provides
  `LCD_PREPARE FILENAME=<gcodes-relative-path>` for manual inspection.
  It makes one metadata GET and, when available, one thumbnail GET to
  Moonraker. HTTP work and image conversion use the existing `aio_executor`
  worker, outside the reactor thread.
  Status reads never trigger collection. Printing and paused jobs reject
  collection, so do not put this command in an executing print file.
- The result contains total layers, nominal layer height in mm, estimated
  total time in seconds, material (type, with name as fallback), and source
  thumbnail data. Unknown values are `None`, not inferred from Z or time.
- `nozzle_target` and `bed_target` come exclusively from Moonraker file
  metadata `first_layer_extr_temp` and `first_layer_bed_temp` in Celsius.
  Moonraker's slicer parsers extract these from first-layer settings or
  literal temperature commands in the G-code. No heater status endpoint
  is queried and no heater command is executed. Missing/invalid targets
  show `--`, rather than guessing macro parameters or material presets.
  Zero is valid (heater off); accepted metadata range is 0..500 Celsius.
  Targets are rounded to whole degrees for their display strings.
- Raw metadata remains in host units. The host also prepares
  `estimated_time_formatted` and the nine `display` strings, including labels
  and units. Estimated duration uses `hh:mm:ss`, with at least two hour
  digits and no wrap at 24 hours: 12552 seconds becomes `ETA: 03:29:12`.
  Fractional seconds round half up to the nearest whole second.
  Other examples are `Layers: 221`, `Height: 0.18 mm`, and
  `Mat: PETG`.
  Unknown fields render as `--`. The C driver does not convert durations,
  format numbers, or append labels/units. ETA is the static estimated total,
  not a live remaining time.
- Thumbnail selection prefers the smallest source at least 192x192,
  then the largest usable source below that threshold. PNG/JPEG
  are supported; other formats are left unavailable. Downloads are capped
  at 2 MiB; metadata responses at 256 KiB. Paths remain within `gcodes`.
- A failed thumbnail download preserves textual data and adds a warning.
  Metadata failures clear the previous job. There are no retries or
  periodic requests. Each preparation fetches fresh data; the stored
  snapshot does not follow later file changes automatically.
- `get_job_data()` returns a detached copy for future host/MCU integration.
  `thumbnail.data` holds compressed source bytes; `thumbnail.rgb565.data`
  holds 18432 bytes of row-major, big-endian RGB565 pixel data. The C
  mainboard driver remains responsible for LCD commands and rendering.
- Image preparation derives from `SlicerScripts/orca_parser.py`: RGB conversion,
  forced 96x96 LANCZOS resize, median-cut quantization to 64 palette colors,
  preservation of original black pixels, and scaled-integer RGB565 channels.
  The internal colors=32 option retains the historical comparison mode;
  the new default intentionally favors higher color quality.
  As in that script, black after quantization remains the skip sentinel;
  non-black output that rounds to RGB565 zero is clamped to `0x0001`.
  Alpha handling matches its `convert('RGB')`, rather than introducing
  a different compositing policy. PNG/JPEG decoding is checked fully,
  dimensions must match metadata, and sources are limited to 1 Mi pixels.
- Image conversion requires Pillow, imported only when needed. It is now
  included directly in `scripts/klippy-requirements.txt`, used by normal
  host installers and requirement-aware Moonraker updates. For standalone
  tools, `scripts/lcd-requirements.txt` remains available. No pip/network
  installation is performed from Klippy callbacks. Missing Pillow still
  produces a thumbnail warning and a text-only card. Copying only `.py`
  files does not update the service environment; use the managed update
  workflow for automatic dependency installation.
- `transfer.data` contains the versioned binary job described below.
  `get_transfer_chunks()` exposes offset/data pairs in at most 40-byte
  chunks; it performs no I/O. It rejects printing/paused states and
  invalidates pending iterators after disconnect or job replacement.
- `get_status()` and command output expose metadata and image dimensions/
  byte count, never image bytes or the configured API key. See
  [Config Reference](Config_Reference.md#lcd_status),
  [G-Codes](G-Codes.md#lcd_prepare), and
  [Status Reference](Status_Reference.md#lcd_status).

Offline inspection from the repository root, without network, printer,
MCU, or LCD access:

```
python3 -B scripts/lcd_prepare.py parts/cube.gcode --metadata-json test/fixtures/lcd_metadata.json
python3 -B -m unittest discover -s test/unit -p 'test_lcd*.py' -v
```

The JSON fixture is synthetic. For a saved real Moonraker response,
replace the JSON path and filename. Optional `--thumbnail-file image.png`
loads a local source image for the thumbnail selected in that metadata;
without it the offline tool prepares a text-only payload and reports that
image bytes were not loaded. Alternatively, `--gcode-file path.gcode`
extracts `thumbnail`, `thumbnail_PNG`, `thumbnail_JPG`, or `thumbnail_JPEG`
base64 blocks from a local G-code prefix of up to 2 MiB. Incomplete blocks,
bad base64, or incorrect declared lengths are rejected. Unlike Marlin's
postprocessor, this tool never inserts commands or modifies the G-code.
Choose one local image source, not both options.

Optional `--output-raw16 /tmp/thumbnail.txt` exports the same
`E3V3SE_THUMB_RAW16_BEGIN 96x96` comment block as the Marlin script.
`--output-transfer /tmp/lcd-job.bin` exports the binary job; both outputs
are local files, not transmissions. Output paths must differ from inputs.
Omitting `--metadata-json` explicitly queries Moonraker using GET only
(with `--moonraker-url` if needed); no live request is needed for offline
validation. This standalone tool has no connection to Klippy or the MCU;
the active/paused-print guard belongs to the Klippy extra.

Production configuration has not been changed or activated. Adding the
optional section to a development configuration is a separate deployment
step. Removing it disables this extra. A small virtual SD hook is the
only change to the existing print path; without the extra no collection
occurs. With `[tjc_lcd]`, the prepared payload is now streamed and rendered
before starting. Startup and normal completion/cancellation now render the idle screen after pending motion finishes.

### Automatic preparation before printing (2026-10-22)

The trigger runs inside Klippy, before a new virtual SD print executes
any file G-code. It does not spawn the standalone CLI or parse a file
already printing:

1. Moonraker requests `SDCARD_PRINT_FILE FILENAME=...`; selecting a file
   with `M23` and then starting with `M24` follows the same hook.
2. `virtual_sdcard.do_resume()` emits
   `virtual_sdcard:before_print(filename)` before scheduling its work
   timer. The filename comes from the opened file, relative to the
   virtual SD root, preserving actual casing and subdirectories.
3. With automatic preparation enabled, `lcd_status` waits for its
   existing worker to GET Moonraker metadata/thumbnail, convert the
   image, format strings, and build the binary payload. The reactor
   remains available while the command waits for the worker.
4. When `[tjc_lcd]` is enabled, stream the payload to its C job renderer
   and wait for the final result before starting the virtual SD timer.
   The first file G-code, including any `START_PRINT` macro, runs afterwards.

The generic virtual SD event is necessary to cover both start paths
before file execution without replacing existing macros or commands.
With no listener it performs no preparation. It runs once per loaded
file; pause/resume skips it even if paused at byte zero. Loading the same
filename as a new job collects fresh data. A failed preparation leaves
the file unread, with no virtual SD timer scheduled, and returns a
recoverable G-code error. After fixing Moonraker, `M24` or a new start
request can retry. Missing/failed thumbnails retain the existing
warning and text-only fallback; there are no automatic retries or polls.

The hook rejects reentrant starts and file/position changes during
preparation. Reset, disconnect, or shutdown invalidates in-flight LCD
results. With the renderer configured it sends the payload and waits for
the final MCU checkpoint; completion schedules the idle logo after pending motion finishes. The data
object's `state: prepared` means collection only; use `tjc_lcd.rendered`
for the render result, then verify the visible card in physical tests.

Example for a separately authorized development deployment:

```ini
[lcd_status]
auto_prepare: True
moonraker_url: http://127.0.0.1:7125
```

The running Klippy must use this fork's updated `virtual_sdcard.py` as
well as the LCD extras. Pillow must be available in that Klippy Python
environment to prepare thumbnails; installing it only in a standalone
test environment does not provide it to the service. No change to
OrcaSlicer G-code or `START_PRINT` is needed. This checkout has not been
deployed and services have not been restarted.

To disable automatic collection while keeping manual diagnostics, set
`auto_prepare: False` at the next authorized configuration reload. To
disable the extra entirely, remove `[lcd_status]`. Neither operation
requires MCU firmware changes for this host-only stage.

### Prepared host-to-MCU application payload, version 5

This is the application contract implemented by the Python preparer and
C receiver, not a TJC serial packet. The C MCU driver generates LCD frames.
Multibyte fields and RGB565 words are big-endian. The fixed header is
34 bytes (`>4sBBHHHHHHHHHHHHI`), followed by nine GBK strings in order, then
a codec-prefixed image. Version 5 replaces v1/v2/v3/v4; regenerate old `.bin` files and update
both the host extras and MCU firmware. Startup remains protocol version 1. Idle mode uses exclusive flag 128, as described above.

| Offset | Type | Meaning |
| --- | --- | --- |
| 0 | 4 bytes | Magic `KLCD` |
| 4 | uint8 | Version 5 |
| 5 | uint8 | Known-field flags: layers 1, height 2, time 4, material 8, image 16, nozzle 32, bed 64 |
| 6 | uint16 | Header size, 34 |
| 8 | uint16 | Image width, 96 or 0 when absent |
| 10 | uint16 | Image height, 96 or 0 when absent |
| 12 | uint16 | Layer-count text byte length, at most 26 |
| 14 | uint16 | Layer-height text byte length, at most 26 |
| 16 | uint16 | Estimated-total-time text byte length, at most 26 |
| 18 | uint16 | Material text byte length, at most 26 |
| 20 | uint16 | Basename text byte length, at most 52 |
| 22 | uint16 | Nozzle target text byte length, at most 8 |
| 24 | uint16 | Bed target text byte length, at most 8 |
| 26 | uint16 | Nozzle caption byte length, at most 8 |
| 28 | uint16 | Bed caption byte length, at most 8 |
| 30 | uint32 | Encoded image byte length, 1..18433 or 0 when absent |

All nine strings are present, without terminators. Examples:
`Layers: 221`, `Height: 0.18 mm`, `ETA: 03:29:12`, `Mat: PETG`,
`Spool_Holder`, `237°C`, `62°C`, `Nozzle`, `Bed`. Unknown values use
`--`. Truncation never splits GBK characters; unsupported characters
become `?`. The original Unicode basename and metadata remain in the host
snapshot. Layer height uses up to six significant digits in mm. No raw
layer/height/duration/temperature numbers need formatting on the MCU.

The transfer descriptor contains total size and CRC32 of the entire
payload. Chunks carry absolute offsets and at most 40 bytes; boundaries
may split a field, GBK character or pixel. Maximum payload is 18655 bytes
(467 chunks); text storage is bounded to 188 bytes. The receiver parses
incrementally without a full framebuffer. Native tests validate fields,
wrapping, all thumbnail pixels, bounds, pacing, CRC/error handling, and
stopping the timer/UART before the final result permits printing.

### Dependency installation and layout deployment

Pillow is part of normal Klippy requirements in this fork. The existing
installers already install that file into `klippy-env`; no new installer
or render-time installation is needed. A Moonraker-managed Git update
configured for this fork and its Klippy virtualenv detects the added
requirements and installs them through pip. Thus the user may update from
Fluidd without a separate Pillow console command. This depends on the
managed update configuration; a manual copy of host modules does not run
the requirements installer. Development-only tests here use the existing
Pillow installation in `/tmp/klipper-lcd-test-deps`, not the printer service.

The original v2 card is owner-confirmed working after installing Pillow.
For v5, update host files and compile/flash the matching firmware only in
an authorized idle maintenance window, preserving the working .config.
Then run `LCD_PREPARE FILENAME=...` to inspect basename and targets, followed
by `LCD_RENDER FILENAME=...` to validate layout without starting a print.
A file without temperature metadata should show `--`. Compare font sizes,
degree symbols, icons, GBK wrapping and the thumbnail against the reference.
Afterward confirm the automatic pre-print hook and static card behavior.
Startup and completion/cancellation now render the idle screen after pending motion finishes. Roll back both host and firmware
together to the saved known-working installation if needed. No production files,
services, heater targets or printer hardware were changed by this work.

Historical validation of the previous v4 change on the x86_64 development host:
96 focused Python/native-C tests pass. They cover yellow label/white value
frames, compact height text, idle geometry/GBK bounds/CRC/timeouts,
startup/completion/cancel/pause transitions, macro/motion drain ordering,
stale callbacks and idle-to-print races. Source whitespace and scoped
Git diff checks pass. The full STM32F103 build with the owner's settings
succeeds: binary 48700 bytes, text 48645, data 52, BSS 3052 (including reserved
stack). Vector table is at `0x08007000`, initial stack `0x20005000`.
The dictionary confirms host USART1 PA9/PA10 at 250000, LCD PA2/PA3 at
115200 and `TJC_JOB_VERSION=4`. The normal Git version query was very
slow over SSHFS but eventually completed; the final binary uses its
actual Git version. A fallback tree in `/tmp` was prepared but required
no rebuild once that normal build completed.
The then-current `/tmp/klipper-tjc-build-printer/manifest.json` recorded the SHA256 and
`deployed: false`. Full Klippy service/regressions remain untested here
because the local Python environment lacks cffi/greenlet and dictionaries.
Physical font appearance and panel timings still need owner validation.

Historical layout previews were generated from actual C frame traces
using synthetic metadata, the Marlin regression thumbnail and approximate
host fonts. They remain local, outside the published screenshots. The
final hardware photos are linked above. The native harness accepts an
optional fifth argument to save frames for offline inspection.



Metadata source: [Moonraker slicer metadata parsers](https://github.com/Arksine/moonraker/blob/master/moonraker/components/file_manager/metadata.py).
Dependency update behavior: [Moonraker application deployment](https://github.com/Arksine/moonraker/blob/master/moonraker/components/update_manager/app_deploy.py).

### Offline validation (2026-10-22)

- 48 host-data unit tests pass for collection, image decoding, RAW16 export, binary
  headers, GBK boundaries, CRC/chunk reconstruction, stale-job rejection,
  preservation of source G-code files, and automatic pre-print preparation.
- Eleven automatic-start integration tests exercise the actual
  `virtual_sdcard` methods with fake printer/reactor/HTTP dependencies:
  start before the first G-code, filenames with spaces/subdirectories and
  case resolution, M23/M24, no reparse on resume, a fresh repeated job,
  metadata failure/retry, disabled/absent LCD integration, reset/shutdown
  during preparation, and rejection of reentrant/file-changing hooks.
  Their G-code recorder never contacts printer hardware.
- With the startup and streaming-render checks above, the complete focused
  suite is run as follows (v5 results are recorded above). Run all of them from the repository root:
  `python3 -B -m unittest discover -s test/unit -p 'test_*.py' -v`.
- The real host result supplied by the owner (221 layers, 0.18 mm,
  12552 seconds, PETG) is covered by the duration-format regression:
  the time sent for drawing is `ETA: 03:29:12`.
- Compared RGB565/RAW16 output against the owner's unmodified
  `SlicerScripts/orca_parser.py` on all 18 `test_*_{full,q32}.gcode`
  benchmark thumbnails using Pillow 13.0.0: identical bytes for all 18
  in the historical 32-color mode. The v5 default uses 64 colors.
- The stricter embedded-block reader accepts 16 complete benchmark files.
  Both `test_9` variants lack the closing thumbnail marker and are rejected;
  the RGB565 comparison above uses their reference-extracted image bytes.
- Pillow for these checks was installed only in a temporary directory on
  the x86_64 development machine. No production dependencies/configuration,
  services, MCU firmware, or printer hardware were changed or exercised.

## Acceptance criteria
- Printer LCD stays connected to its normal electronics; first version remains display-only.
- No adverse impact on MCU communications, step generation, print quality, or host responsiveness.
- No expensive image processing in the time-critical print path.
- The C mainboard driver controls the LCD over its existing USART connection. No direct host serial LCD driver.
- No periodic LCD communication or changing print information during printing. Render the idle image only at the agreed lifecycle transitions.
- Compatibility with upstream Klipper updates maintained where feasible.
- Missing metadata is represented as unknown/unavailable, never invented.
- Tests and a rollback procedure precede any live deployment.

## Out of scope for now
- Touch/UI interaction, changing temperatures, manual movement, LCD menus.
- Live current layer, elapsed/remaining time, Z, progress, and periodic LCD refresh.
- HX711 auto Z-offset and power-loss recovery (separate planned projects).
