# Repository guidance

## Project and layout

This checkout contains Klipper, the 3D printer firmware project. It uses
a Python host process (Klippy) and C firmware on one or more MCUs.

- `klippy/`: host runtime; `klippy.py` is the entry point.
- `klippy/extras/`: dynamically loaded features and device support.
- `klippy/kinematics/`: host kinematics implementations.
- `klippy/chelper/`: C helpers loaded by the host through CFFI.
- `src/`: MCU firmware, architecture directories, and Kconfig definitions;
  `src/generic/` contains shared hardware helpers.
- `config/`: example printer, board, and feature configurations.
- `test/klippy/`: regression `.test` files and associated `.cfg` fixtures.
- `test/configs/`: MCU build configurations used by CI.
- `scripts/`: build, test, installation, flashing, and diagnostic tools.
- `docs/`: user and developer documentation.
- `lib/`: third-party code; avoid unrelated edits to vendored sources.

Read `docs/CONTRIBUTING.md` and `docs/Code_Overview.md` for detailed
contribution and architecture guidance. For configuration changes, also
consult `docs/Example_Configs.md`.

## Build and validation

Run commands from the repository root. Use a Python environment with
`scripts/klippy-requirements.txt` installed; regression tests additionally
need `scripts/tests-requirements.txt`. Firmware targets require the
appropriate compiler toolchain.

- Configure firmware: `make menuconfig`.
- Build firmware: `make` (or `make V=1` for compiler commands).
- Build a predefined target: copy the relevant `test/configs/*.config`
  to `.config`, then run `make olddefconfig` and `make`. Preserve any
  existing local `.config` before replacing it, and clean build output
  when switching targets.
- Check source whitespace: `./scripts/check_whitespace.sh`.
- Check a specific file, including this guide:
  `python3 scripts/check_whitespace.py AGENTS.md`.
- Check host imports: `python3 klippy/klippy.py --import-test`.
- Run a focused regression, with dictionaries available in `dict/`:
  `python3 scripts/test_klippy.py -d dict/ test/klippy/bed_mesh.test`.
- Run the host regression suite:
  `python3 scripts/test_klippy.py -d dict/ test/klippy/*.test`.

The regression runner requires the MCU dictionary filenames specified
by each test's `DICTIONARY` directive, such as `atmega2560.dict`.
Firmware builds generate `out/klipper.dict`; CI builds the MCU targets
and collects named dictionaries in `ci_build/dict/`. See
`docs/Debugging.md` for using downloaded dictionaries.

`.github/workflows/build-test.yaml` runs `scripts/ci-install.sh` followed
by `scripts/ci-build.sh`. The full CI script checks whitespace, compiles
all test targets, and runs host imports and regressions under Python 3
and Python 2. It expects CI toolchains and virtual environments and
replaces `.config`; use focused checks for local changes. The installer
installs system packages and downloads toolchains.

Choose checks relevant to the change and report missing dependencies or
dictionaries explicitly. Build products live in `out/`; `.config`,
shared objects, and Python bytecode are generated local files.

## Implementation conventions

- Follow nearby code's naming, indentation, and structure; avoid
  unrelated reformatting. Python generally uses four-space indentation;
  C generally uses four spaces and function braces on a separate line.
- Preserve the Python 2/3 compatibility exercised by CI in host code.
- Prefer the `klippy/extras/` module system for new host features, using
  `load_config(config)` or `load_config_prefix(config)` as appropriate.
- Keep printer state on printer-owned instances so `RESTART` can rebuild
  it. Close external resources on `klippy:disconnect`, and use other
  printer objects' public interfaces.
- Respect reactor callbacks and MCU scheduling constraints; avoid
  blocking host callbacks and long-running firmware tasks or timers.
  Use `timer_is_before()` for MCU clock comparisons to handle rollover.
- Use the existing `.test` regression format for behavioral coverage;
  it combines configuration, MCU dictionaries, and G-code input.
- New Python and C files need copyright and GPLv3 headers consistent
  with existing files. Do not invent author identities or sign-offs.

## Documentation and configuration changes

Update reference documentation with corresponding user-facing changes:

- Commands and parameters: `docs/G-Codes.md`.
- Modules and configuration options: `docs/Config_Reference.md`.
- Exported status fields: `docs/Status_Reference.md`.
- Webhooks and parameters: `docs/API_Server.md`.
- Incompatible command or configuration changes: `docs/Config_Changes.md`.

Add new documents to `docs/Overview.md` and
`docs/_klipper3d/mkdocs.yml`. Follow example configuration naming and
formatting rules, use `field: value`, and register new printer, generic,
or kit configs in the appropriate alphabetical section of
`test/klippy/printers.test`. Preserve default safety checks.

For requested commits, follow `docs/CONTRIBUTING.md`: use a short
`module: Capitalized summary` subject and the contributor's real
`Signed-off-by` identity. Keep each commit focused on one topic.


# Project guidance: Klipper fork for Ender-3 V3 SE

## Repository and environment
- This repository is a fork of upstream Klipper. Preserve an easy update/merge path from Klipper3d/klipper.
- The stable installation is `/home/shaka/klipper` on a Raspberry Pi 3B+. The development checkout is `/home/shaka/klipper-dev`.
- Developer uses VS Code on Fedora with SSHFS (`~/KlipperRemote`), rather than VS Code Server on the Raspberry Pi, because the Pi has only ~1 GB RAM and Remote-SSH caused severe swap thrashing.
- Runtime: Raspberry Pi 3B+, Debian 13 aarch64; Klipper, Moonraker, Fluidd, Crowsnest. Printer mainboard MCU: STM32F103RET6.
- Read `docs/LCD_STATUS_PLAN.md` before any LCD-related work.

## Engineering rules
- Keep changes modular, preferably in `klippy/extras/` or a separate integration when possible. Avoid modifying Klipper core unless architecture analysis proves it necessary.
- Do not assume Klipper's generic `[display]` driver supports the stock Creality/TJC display.
- Confirmed by the owner: the LCD stays connected to the printer mainboard over USART. It is not connected to the Raspberry Pi or exposed as a host serial device.
- Implement the LCD driver in C on the mainboard MCU: USART2, handshake, framing, clear, text, and drawing/render operations. Host Python may obtain metadata and prepare image payloads, but does not directly open or control an LCD serial port. Do not replace this agreed design with a Python serial driver or a raw UART bridge without a new user decision.
- Use `/home/shaka/Projects/Marlin/Marlin_bugfix_2.1_E3V3SE/Marlin/src/lcd/dwin/creality/dwin_lcd.cpp` and its dependencies as the primary protocol, initialization, and UART reference. Use `Marlin/src/e3v3se/ThumbRender.cpp` and `DWIN_OptimizationReference/` in that repository for rendering optimizations. Python serial tools there are historical references, not the deployed connection or intended driver.
- The LCD shows a static job card rendered before printing: thumbnail, total layers, layer height, estimated time, and material. No live layer, elapsed/remaining time, Z, progress, polling, or periodic LCD refresh during printing. At startup/idle and after completion, show a static approximation of `Screenshots/IdleScreen.png`; a pause preserves the job card. See `docs/LCD_STATUS_PLAN.md` for lifecycle and porting notes.
- Host thumbnail preparation in `lcd_thumbnail.py` derives from `SlicerScripts/orca_parser.py` (96x96 LANCZOS and RGB565/black-skip behavior; v5 uses 64 colors and prefers a source at least 192x192 for downsampling). `lcd_transfer.py` prepares a versioned binary job and bounded chunks, not LCD serial frames. `tjc_lcd.py` streams payload v5 to the C receiver in `src/tjc_job.c`; the Pillow dependency and protocol are documented in `docs/LCD_STATUS_PLAN.md`.
- Prepare all LCD text in the host, including labels, layer height units, and estimated total duration in `hh:mm:ss` (e.g. `12552` seconds becomes `03:29:12`). Hours use at least two digits and do not wrap at 24. Payload v5 sends ready-to-draw GBK strings, not raw layer/time numbers for the MCU to format. Raw Moonraker seconds remain host metadata only; fractional seconds round half up to a whole second. Do not add live ETA updates.
- `[lcd_status]` defaults to `auto_prepare: True`: `virtual_sdcard:before_print(filename)` collects fresh metadata/thumbnail and, with `[tjc_lcd]`, renders through the C MCU driver before scheduling the first G-code, for Moonraker's `SDCARD_PRINT_FILE` and M23/M24. It runs once per loaded job, never on pause/resume or during printing. Metadata/render failures prevent start; thumbnail failures retain text-only data. `auto_prepare: False` keeps manual `LCD_PREPARE` (prepare only) and `LCD_RENDER FILENAME=...` (prepare and draw while idle). Do not launch the CLI from `START_PRINT`; `scripts/lcd_prepare.py` is a diagnostic frontend.
- Startup C driver is `src/tjc_lcd.c` plus `src/stm32/tjc_uart.c`, opt-in `CONFIG_TJC_LCD` on STM32F103, with `[tjc_lcd]` host lifecycle. On `klippy:ready`, it requests bounded handshake and clear using 0x40 palette + bare 0x52. USART2 PA2/PA3, 115200 8N1, are reserved independently of the host link; USART2 host-link builds cannot enable this driver. Complete replies include AA 00 OK plus frame tail; after clear and 50 ms settling, UART/timer stop. `ready` is not a panel render ACK. Prints wait for startup/clear and, with [lcd_status], the streaming job renderer below. TJC_LCD_INIT / TJC_LCD_CLEAR are idle-only. Rendering requires updated MCU firmware advertising TJC_JOB_VERSION=5.
- Owner clarified that Raspberry Pi to mainboard uses a USB-C cable with serial transport. The supplied installed Klipper configuration below confirms host USART1; USART2 remains assigned to the LCD. Owner confirmed startup clears the physical LCD successfully on 2026-10-22. The owner also confirmed the original text/thumbnail card works after installing Pillow; the owner supplied physical v4 layout photos; the owner then supplied idleLCD-v5.jpg/cardLCD-v5.jpg confirming startup idle and the v5 card. The owner reports a slight, almost imperceptible speedup and better perceived thumbnail quality; no panel timing measurement is available.
- Owner subsequently confirmed LCD_RENDER returns success and job text is visible on the physical display, but the thumbnail is missing. Diagnose lcd_status.job.thumbnail / transfer.size / warnings before changing the MCU renderer. Missing Pillow in the Klippy service environment is a hypothesis until warnings confirm it; successful standalone CLI conversion may use a different Python environment. LCD_RENDER now explicitly distinguishes text-only cards from cards containing thumbnail bytes and reports warnings; automatic preparation logs warnings.
- The owner then supplied job.warnings confirming `Thumbnail conversion requires Pillow; see scripts/lcd-requirements.txt`. The observed text-only card is caused by missing Pillow in the running Klippy Python environment. That initial dependency fix used the Klippy service interpreter, not a separate CLI test environment. Pillow is now included in normal Klippy requirements as described below. Pillow is imported lazily in each conversion, so retrying LCD_RENDER while idle after installation can load it without a service restart. No MCU firmware change is needed for this dependency issue.
- Job renderer `src/tjc_job.c` advertises TJC_JOB_VERSION=5, checks token/offset/length/CRC32 and GBK fields, uses <=40-byte chunks, draws nine host-formatted strings and RGB565 at x2/y28. The RenderJob.png composition uses a dark background, outlined thumbnail, icons, metadata on the right, centered basename and separate nozzle/bed target panels. It stores two rows of at most 96 runs (adjacent colors can require 96, not 48), merges vertically, groups flushed spans by color and preserves Marlin's 16 ms pause per ten frames without blocking tasks. A trailing handshake plus 50 ms settling gates the final result; this is not a documented pixel-level panel ACK. Job timer/UART stop before print start. Startup-only firmware needs rebuilding/reflashing for job rendering; no new menu flag is needed beyond CONFIG_TJC_LCD. V5 host preparation groups lossless image-local rectangles globally by color, falling back to raw RGB565 when smaller. MCU C retains frame construction, placement, UART ownership and pacing. Idle artwork now approximates `Screenshots/IdleScreen.png` at startup and after completion/cancellation, after draining motion; pauses and errors preserve the card.
- Owner supplied the installed Klipper build configuration on 2026-10-22: STM32F103 (`stm32f103xe`), 72 MHz, 8 MHz crystal, 28 KiB bootloader (`STM32_FLASH_START_7000`, application 0x08007000), host USART1 PA9/PA10 at 250000 baud, flash linker length 0x10000, RAM 0x5000, stack 512. Preserve these working settings when enabling TJC_LCD; no need to infer the host interface from USB-C again. LCD USART2 PA2/PA3 at 115200 is confirmed by the working Marlin configuration on the same board/display. Treat that configuration and owner confirmation as the pin reference; no new wiring measurements are required. Production printer.cfg/includes are still unavailable locally for checking other pin assignments.
- Do not overwrite or modify production files in `/home/shaka/klipper` or `/home/shaka/printer_data/config` without explicit approval.
- Do not restart Klipper, Moonraker, firmware, heaters, motors, or initiate any motion or probing without explicit approval. A printing printer must not be interrupted.
- Prefer read-only source analysis, small reviewable patches, tests/mocks, and staged hardware validation.
- Do not perform large Git indexing, dependency installs, builds, or scans on the memory-constrained Raspberry Pi without approval. Git operations on SSHFS may be slow.
- Keep compatibility with upstream changes and provide concise test instructions and rollback for hardware-dependent code.
- Clearly distinguish confirmed hardware facts, assumptions, and information that must be measured or inspected.

- Owner confirmed both text and thumbnail rendering work on 2026-10-22. Pillow is now part of `scripts/klippy-requirements.txt`, so normal installers and requirement-aware Moonraker updates install it in the Klippy environment. Do not run pip from a render callback. The separate LCD requirements remain for standalone tools.
- Owner requested the layout in `Screenshots/RenderJob.png`. Show ONLY file target temperatures, from Moonraker metadata `first_layer_extr_temp` and `first_layer_bed_temp` (initial layer); do not read live heaters or duplicate current/target values. Unknown targets render `--`, zero means heater off. Include the basename, with bounded GBK wrapping. The display basename omits .gcode/.gco/.g only; retain the full source filename in host metadata. Payload v5 requires matching host and MCU updates; all labels, units and values are host text, with C responsible for fonts/layout/icons.

- The owner requested yellow labels (`0xffe0`) with white values on the job card and the shorter host labels `Height:` and `Mat:`. MCU text styling splits at the first colon, preserving GBK wrapping; do not recolor values or units yellow.
- Idle artwork is now defined by `Screenshots/IdleScreen.png`. Payload v5 retains the 34-byte/nine-string transport and reserves exclusive flag 128 for an idle layout without pixels. Host sends branding/URL/Idle text; C approximates the red/blue logo, separators, corner strokes and check icon with bounded primitives. No full-screen raster/framebuffer is transferred.
- `tjc_lcd` schedules idle once after startup handshake/clear and on `virtual_sdcard:finished(state)` for complete/cancelled prints. Acquire the G-code mutex so end/cancel macros finish queuing movements, then wait for `toolhead.wait_moves()` before completion/cancellation drawing. Keep the mutex through the render to exclude manual motion. A new job/reset/disconnect invalidates queued idle callbacks; an already running idle render finishes before pre-print rendering. Pauses/errors preserve the card. Retain startup idle intent through the intermediate clearing state. Also handle idle_timeout:idle once when ready, not printing/paused and not already showing idle. No periodic idle redraw or motion/heater commands. `TJC_LCD_IDLE` provides an idle-only manual test; status `screen` identifies blank/job/idle or null. Matching v5 host/MCU deployment is required for these refinements; the original v5 startup/card are physically confirmed.

- Final card refinements retain wire protocol v5. Metadata uses font 1 at x126/y16+36*field (14 GBK cells, second line +18), cyan icons also move down 8 pixels, and the separator moves to y162. Common `Height: 0.12mm` stays on one row. Long displayed filenames use balanced two-row wrapping (27 bytes maximum each), preferring underscore/space boundaries and suppressing the boundary separator; short names stay on one row, GBK boundaries remain intact. The host still sends the original bounded basename without its G-code extension.
- The physical v5 photo shows a blank degree glyph despite host-supplied GBK A1 E3. Keep host temperature strings as degrees Celsius; C substitutes a single blank display cell and draws a small white hollow degree circle with bounded rectangles. Do not rely on that panel font's degree glyph. Unknown temperatures remain `--`; zero renders `0°C`. The owner confirmed the final layout works and supplied final_idle_lcd.jpg/final_card_lcd.jpg. README.md documents setup and shows these photos; keep final screenshots eligible for Git and intermediate previews ignored. Do not change idle behavior or thumbnail transport without a new request.

- Cancellation macros can call SDCARD_RESET_FILE after CANCEL_PRINT_BASE or reset the SD file without the base command. A reset that leaves a ready LCD showing a job card must queue a fresh idle render, replacing any invalidated callback. Keep the G-code mutex and toolhead.wait_moves() before drawing; a new print still invalidates that callback. No LCD traffic may occur while printing/paused. Metadata labels begin at x126, leaving an eight-pixel gap after the icons; height units are attached to the value (e.g. 0.18mm).

## Work priorities
1. Read-only LCD print-status display (current focus).
2. HX711/load-cell automated multi-point Z-offset (future project; not in current scope).
3. Power-loss recovery, PLR (future project; not in current scope).

## Collaboration
- Communicate with the user in Spanish. README files, project documentation, and code comments must always be written in English. This is an explicit owner preference; do not write repository prose or comments in Spanish.
- For LCD development, begin with discovery and an architecture proposal; do not implement until the hardware communication path and plan have been reviewed.
