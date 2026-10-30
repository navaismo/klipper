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
- Keep the stable installation and development checkout separate on the Raspberry Pi 3B+.
- Developer uses VS Code on Fedora with SSHFS rather than VS Code Server on the Raspberry Pi, because the Pi has only ~1 GB RAM and Remote-SSH caused severe swap thrashing.
- Runtime: Raspberry Pi 3B+, Debian 13 aarch64; Klipper, Moonraker, Fluidd, Crowsnest. Printer mainboard MCU: STM32F103RET6.
- Read `docs/LCD_STATUS_PLAN.md` before any LCD-related work.

## Engineering rules
- Keep changes modular, preferably in `klippy/extras/` or a separate integration when possible. Avoid modifying Klipper core unless architecture analysis proves it necessary.
- Do not assume Klipper's generic `[display]` driver supports the stock Creality/TJC display.
- Confirmed by the owner: the LCD stays connected to the printer mainboard over USART. It is not connected to the Raspberry Pi or exposed as a host serial device.
- Implement the LCD driver in C on the mainboard MCU: USART2, handshake, framing, clear, text, and drawing/render operations. Host Python may obtain metadata and prepare image payloads, but does not directly open or control an LCD serial port. Do not replace this agreed design with a Python serial driver or a raw UART bridge without a new user decision.
- Use `Marlin/src/lcd/dwin/creality/dwin_lcd.cpp` and its dependencies in the customized Marlin reference checkout as the primary protocol, initialization, and UART reference. Use `Marlin/src/e3v3se/ThumbRender.cpp` and `DWIN_OptimizationReference/` in that repository for rendering optimizations. Python serial tools there are historical references, not the deployed connection or intended driver.
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
- Do not overwrite or modify the production Klipper checkout or active printer configuration without explicit approval.
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
1. Load-cell and CR Touch automatic Z-offset (current focus: implemented MVP,
   offline validation and staged hardware qualification; no printer operations).
   Read docs/AUTO_Z_OFFSET_PLAN.md
   before work on this feature. The owner supplied Marlin/src/e3v3se/AutoOffset.cpp
   in the existing Marlin reference checkout. This fork has both HX711/HX717 and
   CS1237 backends; the Marlin class name does not confirm the installed ADC.
   Keep CR Touch as the primary probe. Existing load_cell_probe and bltouch
   constructors conflict in probe/command/endstop registration; propose a
   secondary measurement integration using the existing MCU analog trigger.
   Start with paired measurements at one physical point near the cell and
   repeatability validation; multi-point averaging is not yet an agreed design.
   The owner authorized implementation after approving the simplified design;
   hardware deployment/validation remain pending.
   The owner supplied the main printer.cfg on 2026-10-23: bltouch PC14/PC13,
   XY offsets -24.0/-14.5, saved z_offset 1.065, Z range -3..250,
   safe_z_home toolhead XY139/127. KAMP generates a new mesh each print.
   The owner exposed the active printer configuration directory for read-only
   analysis. printer.cfg, macros.cfg and KAMP_Settings.cfg have been inspected;
   do not request those files again. Symlink reads through that mount failed,
   but the owner then exposed Fluidd's client.cfg and KAMP's
   Configuration/{Adaptive_Meshing,Line_Purge,Smart_Park}.cfg. All active
   includes have now been read through those reference checkouts.
   The active KAMP_Settings.cfg is in the printer configuration; the copy in
   the KAMP repository is a template. No configuration-file access is missing.
   PRINT_START is the actual start macro: bed wait, G28, adaptive mesh,
   SMART_PARK, final nozzle wait, local ADAPTIVE_LINE_PURGE. M104 S150 before
   homing does not wait for the nozzle. KAMP mesh_margin/fuzz are zero. The
   installed override does not forward ADAPTIVE/ADAPTIVE_MARGIN parameters;
   it uses _KAMP_Settings.mesh_margin. This is confirmed in the local source,
   not a requested meshing change. Offline ConfigFileReader verification parsed
   54 sections and all six includes. No load-cell, axis-twist or thermal-Z
   sections were found; ADC identity/calibration remain unknown.
   Proposed local point XY25/28 requires toolhead
   XY49/42.5 for CR Touch; reach is checked, contact suitability is not.
   Owner decision: Auto Z-offset is a user-invoked standalone idle calibration,
   followed by persisting a validated bltouch z_offset. Do not add it to
   PRINT_START, startup, virtual-SD preflight or an idle event. Printing uses
   the saved offset and a fresh KAMP mesh as usual. Initial experiments report
   candidates only; later qualified calibration stages a valid result for the
   standard SAVE_CONFIG workflow (writes the active config and restarts).
   Do not execute SAVE_CONFIG or any hardware operations during development.
   Idle means no active/paused job or competing motion; do not require waiting
   for idle_timeout.state == Idle or its OFF macro. Keep calibration independent
   of adaptive mesh bounds and preserve old offsets on failure.
   Owner clarified that the working Marlin acquisition is sufficient to
   prepare the passive ADC stage; do not keep requesting pin maps, chip photos
   or datasheets up front. Verified Marlin GPIO sequence: PA4 clock, PC6 data,
   clock low while awaiting data-ready, 24 signed bits plus one final pulse,
   assumed 80 SPS; this is not STM32 hardware SPI. The existing HX711 A-128/
   80-SPS backend is the source-derived initial diagnostic hypothesis, not
   confirmed chip identity (the header also mentions CS123X). Proposed named
   sensor is load_cell bed_force; no guessed calibration values. Qualify raw
   counts, errors, actual rate, load response and polarity without motion before
   contact. Physical zero/noise/drift, local counts_per_gram and safe force
   limits require real measurements; ADC identity is a fallback if compatibility
   remains unresolved. No diagnostic or configuration activation has run.
   The owner approved the simplified MVP architecture: extract shared tap
   construction from load_cell_probe.py into a small helper accepting an
   existing LoadCell; reuse LoadCellProbingMove, TappingMove and LCBestFit.
   A new auto_z_offset.py coordinates five pairs at one physical point and
   confirmation/persistence. No second probe/ADC/virtual endstop, multi-point
   sampling, bracketing extras, advanced thermal compensation or experimental
   statistics. Preserve force/error checks and reject invalid measurements.
   Additional owner requirements: Z-offset calibration and supervised nozzle
   contacts require hot bed/nozzle conditions. Default targets are 60/170 C;
   allow higher valid targets and reject lower ones. The owner later requested
   tolerance of ordinary PID oscillation: actual temperatures must reach the
   nominal 60/170 C minima at least once, then accumulate five seconds in band.
   Brief excursions pause accumulation instead of resetting it; later in-band
   readings may cross below the nominal minima. Contact starts in band. Tare
   after warm-up. Checkpoints allow up to temperature_recovery_time (default
   10 seconds) for ordinary out-of-band recovery, without repeating contacts or
   reapplying targets. Changed targets, invalid sensor readings and recovery
   timeout still reject calibration and preserve the old offset. Defaults are
   +/-2 C band and 600-second heating timeout. Commands restore
   previous heater targets. These defaults do not establish full thermal soak.
   Source implementation is authorized; no live heating, hardware operations
   or installed printer configuration changes have been authorized/executed.
   LoadCellTapHelper is now shared by primary load_cell_probe and the secondary
   auto_z_offset extra, accepting the existing sensor. NumPy is no longer
   required for tare/diagnostic arrays; the LCBestFit model is reused, with its
   search corrected as described below. Optional filters
   may still require SciPy. Collectors close on failures; invalid/degenerate
   fits are rejected. auto_z_offset requires explicit speed/lift_speed and
   locally validated trigger_force/force_safety_limit. It requires homed XYZ,
   an inactive mesh (explicit BED_MESH_CLEAR), and no axis-twist/skew/thermal-Z
   compensation module in the MVP. No KAMP, print-start or MCU C changes.
   AUTO_Z_OFFSET_TEST_CONTACT performs one supervised pair per selected point.
   Legacy probe_point retains five pairs; optional probe_points requires four
   locations with three fresh pairs each and qualification at every location.
   Default local paired spread tolerance is 0.025 mm; no outlier rescue. Candidate
   remains in memory until AUTO_Z_OFFSET_ACCEPT explicitly stages bltouch's
   z_offset. SAVE_CONFIG is separate and saves all pending changes/restarts.
   Existing pending offset edits are refused; unrelated pending edits survive.
   Actual CR Touch sessions close before nozzle contact. Geometry uses physical
   frame coordinates, independent of G-code origins. Initial diagnostic setup
   and commands are documented in docs/AUTO_Z_OFFSET_PLAN.md. No additional
   dependency installation or firmware protocol is introduced by this feature.
   Offline verification: 195 Python/native-C unit tests pass, including 80
   automatic-offset/shared-engine tests and the previous LCD suite. Normal
   Klippy import/regression runner is unavailable in the local interpreter
   (greenlet/host dependencies and atmega2560.dict missing); new .test fixtures
   exist but were not run. The agent has not executed installation, restart,
   heating or motion. The owner has performed staged hardware tests:
   HX711 acquisition works with the above pins and inverted orientation;
   saved counts_per_gram is 114.88636 and reference_tare_counts is -436490.
   Warm diagnostics show no errors/saturation, 82.7 SPS, about 21.6 g
   peak-to-peak variation and -0.5 g after tare. This does not establish nozzle
   measurement accuracy or a mechanically safe maximum force.
   A cleared bed_mesh reports mesh_matrix=[[]]; use the public get_mesh() API
   to distinguish an active mesh, including a flat one. A real BED_MESH_CLEAR
   regression covers this case without changing KAMP or stored profiles.
   First owner-run contacts failed: a thermal check and an ascent fit with
   24 samples below contact but only one above. At lift_speed=0.1 mm/s the
   original 0.3-second window covers only 0.03 mm. Auto Z-offset now opts into
   a bounded five-second ascent window; the regular load-cell probe retains
   0.3 seconds. The piecewise model and minimum-three-samples checks are retained.
   Heater targets return to their previous values (often zero) during error
   cleanup, after the original error. Thermal errors now include the heater,
   measured/current/expected targets and band; cleanup reports restored targets.
   Logs show abrupt bed temperature readings, including -18.1 C while warm,
   and negative excursions in older print logs before this feature. The cause
   was identified by the owner as a damaged thermistor; replacement is not yet
   confirmed. Resolve that hardware fault before more contacts.
   Do not hide these readings by relaxing thermal guards. No offset has been
   accepted/saved by the owner-run contact tests reported so far.
   The owner subsequently reported a successful supervised contact at targets
   60/170 C, +/-4 C tolerance and two accumulated seconds in band. CR Touch
   reported bed_z values 0.1375, 0.1375 and 0.135 mm; the paired result was
   1.376125 mm, compared with the owner's proven saved offset of 1.065 mm.
   The +0.311125 mm discrepancy is unresolved. The formula matches standard
   PROBE_CALIBRATE conventions; do not subtract an arbitrary constant to force
   agreement. Successful contact does not establish accuracy. Qualify the five
   pairs and contact fit before accepting/saving any new offset.
   Further single-contact tests returned 1.410007 and 1.425876 mm at 60/170 C,
   then 1.441996 mm at 65/175 C. Mean CR Touch bed_z values were 0.170000,
   0.178333 and 0.176667 mm; inferred fitted nozzle Z was -0.175007,
   -0.182543 and -0.200329 mm. Both references changed. Logs confirm targets
   restore to 55/150 C between tests, from HOME_N_PREHEAT. Before a controlled
   repeatability trial, manually set the chosen calibration heater targets
   outside the command and allow a thermal soak; cleanup then restores the
   same targets. Keep them fixed for the full five-pair run. No arithmetic
   accumulation was found: each run starts with the unchanged 1.065 mm primary
   offset and a fresh tare. Single TEST_CONTACT runs do not compare inter-run
   spread; CALIBRATE applies the five-pair spread check. The observed drift
   and bias are unresolved; do not accept/save or force agreement by a constant.
   A complete five-pair run at 60/170 C returned 1.463246, 1.458672,
   1.460940, 1.465170 and 1.459083 mm: median 1.460940, range 0.006498 mm.
   It passed the within-run spread check but differs from 1.065 by 0.395940 mm.
   The owner initially described a manual PROBE_CALIBRATE reference, then
   clarified that the functional 1.065 was tuned with babystepping during a
   print after paper calibration left the nozzle too close. Preserve it.
   No automatic candidate was accepted/saved. The owner rejected recurring
   manual paper/gauge input: qualify the algorithm with automatically
   collected data. The 1.065 mm reference is not a numeric fitting target.
   A deterministic noisy/drifting trace exposed a local-search defect in
   LCBestFit: Z0.493668 was returned despite a better optimum at Z0.055142
   (simulated true contact Z0.075390). The solver now checks all split intervals
   of the same piecewise model using prefix sums in linear time. Reject
   non-monotonic ascent positions and nonnegative force slopes; configured
   compression must be positive. Keep all force/error/sample checks.
   Auto Z-offset automatically logs bounded replayable force/Z traces, sample
   times, raw/fitted Z and paired coordinates from normal test/calibration
   commands. No extra operator measurements are required. Standard load-cell
   probing does not enable detailed trace logging. The corrected solver has
   offline matrix/grid, noise, quantization and coordinate-translation coverage;
   physical validation is pending. Do not claim the 0.395940 mm bias is fixed
   until new hardware traces establish it, and do not apply a fixed correction.
   With the corrected solver the owner obtained median 1.462630 mm and range
   0.004698 mm over five pairs. Captured traces show release near -0.2335 mm
   and raw trigger near -0.3375 mm; the solver correction did not remove the
   discrepancy with 1.065. Source now expresses the equivalent formula
   directly as cr.test_z - nozzle_contact_z. The old offset cancels and is not
   an input correction; existing tests vary it without changing the result.
   The owner clarified that 1.065 was calibrated at another bed point at the
   same temperature and gives a proven first layer. They proposed comparing
   three additional points automatically. This is a proposed scope expansion,
   not an implemented mode. Compare per-point results and qualify cell response
   at each location before averaging; local bed height should cancel within
   each pair. Position-dependent offset and force-transfer bias remain possible.
   Earlier owner decision: before implementing four points, perform a one-off
   manual/automatic comparison at physical X25/Y28, equivalent temperatures and
   compensations, without staging/saving results. This is explicitly requested
   diagnostic qualification, not recurring manual input to the normal feature.
   Preserve 1.065 mm. The documented sequence presets 60/170 C targets before
   TEST_CONTACT, which leaves the nozzle at X25/Y28; then MANUAL_PROBE/TESTZ,
   M105/GET_POSITION and ABORT, without intervening homing, XY/offset changes
   or mesh loading. Use kinematic Z and account for known gauge thickness.
   last_contact status and console now report force_trigger_z, regression_z,
   signed regression_delta, physical point and before/after temperatures;
   capture raw trigger before replacing it with the fit. Reports invalidate on
   new runs/errors/homing. No new comparison command or second probe is added.
   Latest owner decision supersedes the single-point-only MVP: implement
   four-point diagnostics with three paired samples per point, local medians
   and rejection of spatial disagreement. Marlin's D_ROUTINE_AUTO_OFFSET uses
   (28,28), (110,110), (180,28), (28,180) in this order. Do not use its fifth
   (180,180) point; its source warns of possible bed damage away from the cell.
   probe_points is optional and mutually exclusive with legacy probe_point.
   TEST_CONTACT POINT=1..4 must qualify every configured location; changes to
   probe/force/point references, homing, errors or disconnect invalidate it.
   Local offset_tolerance applies independently; spatial_tolerance is at most
   0.025 mm across local medians. Rejected spatial reports retain all four
   points, diagnostics and null z_offset; ACCEPT remains blocked. An agreeing
   report uses the median of local medians as an in-memory candidate only.
   Do not increase force remotely or assume equivalent force sensitivity away
   from the cell. No KAMP/PRINT_START changes, probing, restart or save by the
   agent. The owner confirms manual 1.460 and automatic 1.462630 were compared
   at the same physical point and considers that comparison validated.
   Functional 1.065 is a print-tuned
   baseline, not a verified zero-gap measurement or a correction to force into
   the estimator. Preserve it and require a reviewed first-layer trial before
   saving a four-point candidate; no automatic trial command is implemented.
   First owner-run four-point qualification under the saved-reference policy:
   point 1 (28,28) passed with
   offset 1.469713, raw Z -0.377500, fitted Z -0.277213, delta +0.100287 mm.
   Points 2 (three attempts), 3 and 4 failed MCU RAW_RANGE before estimation.
   Read-only owner status showed counts_per_gram 114.88636, saved reference
   -436490, last local tare -451195, inverted orientation, ADC errors and
   overflows zero. At force_safety_limit 200 and trigger_force 75, absolute
   raw limits are -459467..-413513. Local baseline is +128 g from reference,
   leaving only 72 g compression headroom at the last point. This confirms
   insufficient margin there; other points' tares are unknown. LOAD_CELL_TARE
   changes local tare only. The initial guard only rejected inadequate
   compression margin before a nozzle approach and Auto logs reference/local
   tare, position, raw bounds and margins. Failed approaches log available raw
   extrema and <=257 time/count samples; bulk data may omit the exact MCU
   stopping sample. Original errors and all bounds remain intact. No diagnostic
   retry, new motion, reference update or restart was executed by the agent.
   Unloaded baseline/force transfer across positions and temperature still need
   qualification; logs also show abrupt bed readings (39.65 C then recovery in
   one second). The owner later confirmed replacement is pending. Every
   failed run leaves verified_points empty, candidate false and offset 1.065.
   Later passive diagnostic: 828 good samples, 82.6 SPS, zero errors or
   saturation. Heater targets zero; reported bed/nozzle 35.46/36.36 C.
   Current/reference tare both -436490; force +86.6 g, recent range
   83.6..89.5 g. About 113.4 g compression margin remains in this snapshot.
   A Klippy restart is logged, with saved scale/reference unchanged and
   unhomed XYZ zero. The physical position is not established for a controlled
   thermal/location comparison. Do not infer the 128-to-86.6 g change is purely
   thermal or re-tare away the evidence.
   The owner explicitly confirmed the bed thermistor is still damaged; the
   replacement takes about a month. Do not ask again whether it was replaced
   without new evidence. Latest owner steering: resume HOT four-point work,
   fix RAW_RANGE reference mismatch, and do not require cold comparisons,
   repeated manual PROBE_CALIBRATE or extra sensor calibration as prerequisites.
   Preserve configured 60/170 C targets, tolerance/stabilization/recovery and
   actual heater/MCU fault checks. No thermal protection was disabled or widened.
   Auto now captures an unloaded ADC mean with load_cell.avg_counts(), after
   warm-up, CR measurement and a drained move to nozzle clearance. It sets a
   temporary safety reference on LoadCellProbingMove, fixed for all three pairs
   at that point (five in legacy mode). Re-capture only at the next point after
   retraction; clear on run start/exit, errors and shutdown. Later per-contact
   tares cannot move this raw band, preserving protection against cumulative
   re-taring within the point. Safety strength remains 200 g and trigger 75 g;
   saved reference/scale/offset remain unchanged. Generic load_cell_probe keeps
   its original saved-reference raw policy. Actual ADC range, saturation,
   sensor/fit errors, compression margin and MCU raw stopping stay enforced.
   Owner baseline -451195 now gives point raw bounds -474172..-428218 with
   114.88636 counts/g. The old 72 g margin was from the global reference and is
   no longer the active Auto band. Logs distinguish saved/point/local tares.
   Updated auto_z_offset.py and load_cell_probe.py need host reload only; no
   config or firmware changes. Hot TEST_CONTACT POINT=1..4 and CALIBRATE remain
   the next hardware sequence. The agent has not executed motion/heat/reload.
   LOAD_CELL_DIAGNOSTIC's additional raw/reference/noise statistics remain
   optional, not a required detour before four-point calibration.
   Hardware result after the point-reference correction: owner completed hot
   TEST_CONTACT at all four points without RAW_RANGE. Point offsets were
   1.463895, 1.467845, 1.484505, 1.508083 mm (range 0.044188 mm). These are
   single supervised pairs, not the required three-pair local medians. Release
   corrections were +0.100272, +0.128822, +0.197162, +0.250250 mm; loaded slope
   magnitudes 1053, 710, 399, 323 g/mm. Fits had adequate below/above support
   (84/329,106/306,161/251,207/205). Logged compression headroom stayed near
   199..202 g against the unchanged 75 g trigger. Position-dependent force/Z
   response is plausible; mechanics, drift and temperature are not isolated.
   Next: hot AUTO_Z_OFFSET_CALIBRATE for three fresh pairs at each point,
   without G28/restart or repeating manual calibration. Do not average away
   spatial disagreement or change the working 1.065 offset.
   Latest owner change: permit bed_temp and BED_TEMP down to 50 C. The initial
   measured-bed warm-up latch must also use 50 C, not the old 60 C minimum.
   Default bed target remains 60 C; configured bed_temp: 50 is valid. Nozzle
   minimum/default remains 170 C. Other thermal, force and offset rules remain
   unchanged. This supersedes earlier 60 C minimum notes, not historical tests.
   Next owner log at bed target 50 C and tolerance +/-6: all four TEST_CONTACT
   passed without RAW_RANGE, offsets 1.474197/1.469315/1.491321/1.506807 mm
   (single-pair range 0.037492). CALIBRATE completed point 1 with offsets
   1.472026/1.474248/1.471427, median 1.472026 and local range 0.002821 mm.
   It then aborted before point-2 nozzle contact on the configured 35-second
   temperature recovery timeout: bed 58.37 C versus target 50 and band 44..56.
   No global candidate or remaining local medians exist. Cleanup restored prior
   heater targets 0/0; preset selected targets outside measurement commands to
   preserve heating across tests. Do not attribute this timeout to RAW_RANGE
   or alter the estimator. Current code invalidates all qualified points on
   this error, so another CALIBRATE needs all TEST_CONTACT qualifications again.
   2026-10-29 update: owner replaced the bed thermistor cable and reports stable
   readings during bed motion. Full hot CALIBRATE at 50/170 C, tolerance +/-6,
   completed all twelve pairs without thermal interruption or RAW_RANGE. Do
   not keep treating the earlier cable fault as an unresolved prerequisite.
   Four local medians/ranges (mm): P1 1.462007/0.017410, P2
   1.464676/0.008254, P3 1.502586/0.022240, P4 1.506984/0.011899. All local
   checks pass; spatial range 0.044978 exceeds 0.025 and correctly yields no
   candidate/save. Functional 1.065 remains the baseline. Twelve logged fits
   have 72..237 loaded and 177..342 unloaded samples; slope magnitudes decrease
   from about 900..970 g/mm at P1 to 267..284 at P4. Mean release corrections
   grow about 0.099/0.148/0.224/0.273 mm. Compliance/load transfer is plausible,
   but location, time drift and probe/gantry geometry are not isolated.
   Offline replay exactly reproduced the fits. Cropping recorded windows near
   contact still gave spatial ranges 0.037341..0.041462 mm; this does not justify
   changing the estimator or raising the spatial limit. A reversed-order hot
   comparison could separate location from time/order drift. No reverse mode,
   hardware operations or runtime/config changes were made for this analysis.
   Latest owner decision (2026-10-29): normal calibration now uses two points,
   P1 physical X28/Y28 near the cell as the reference and P2 X110/Y110 at the
   center as validation only. Take three fresh pairs at each, six total.
   Require both local spreads and the difference of medians to pass existing
   limits; candidate is P1's median, never an average with P2. Both locations
   require supervised qualification. Keep optional four-point diagnostics
   compatible and legacy single-point mode unchanged. Preserve 1.065 mm until
   explicit acceptance; no print-start/KAMP, MCU, force or thermal changes.
   The agent updates source, documentation and offline tests only; active
   printer configuration and all hardware operations remain operator actions.
   Two-point implementation passes 92 focused automatic-offset/shared-engine
   unit tests, including spatial/local rejection, qualification, force failure,
   fixed per-point references and staging P1 without changing runtime 1.065.
   Full unit discovery is incomplete in this local environment because Pillow
   is missing from three LCD test modules; no dependencies were installed.
   Latest owner addition (2026-10-29): AUTO_Z_OFFSET is a single command for
   two supervised tests (P1, P2), then six fresh calibration pairs only when
   both pass, using one warm-up and the existing accept/save separation.
   Keep explicit homing/BED_MESH_CLEAR prerequisites. Report failed stage,
   point/pair and original error; expose stage in status. Owner rejected
   adding MCU/panel firmware for the illustration: host Python must reuse
   existing v5 card/image payloads, with C still owning USART2 and framing.
   Animate only during positioning travel, and finish every bounded render
   and final fence before CR Touch, ADC baseline/tare, nozzle contact or fit.
   No background animation, warm-up animation or live print updates. Existing
   firmware redraws the full card per pose in its 96x96 image area, so rate
   and visual smoothness need hardware qualification. Current host limit is
   0.4 s minimum between frame starts and eight frames per travel. A queued
   frame may finish after travel ends; no next pose after motion completion.
   Calibration remains standalone/idle, never PRINT_START or KAMP. Preserve
   1.065 until explicit acceptance; source/offline tests only, no printer ops.
   Focused verification now passes 100 automatic-offset/shared-engine tests,
   57 LCD host/native-C tests and 14 virtual-SD lifecycle tests (171 total).
   Actual unchanged v5 C receiver validates all eight poses, erase rectangles
   and frame bounds. Full-suite Pillow-dependent tests remain unavailable in
   this interpreter. No MCU source/config changes, build, flash or restart.
   Latest owner steering supersedes the previous preparation/animation rules:
   AUTO_Z_OFFSET now performs warm-up, mesh clear, full G28 and readiness
   checks as part of its cycle. Clear before homing and again only if a homing
   macro reloads a mesh. Default bed/nozzle targets are now 50/170 C; explicit
   section settings or command parameters still override them. Manual
   TEST_CONTACT/CALIBRATE retain homed/inactive-mesh prerequisites. A failed
   homing must not initiate recovery Z moves or nozzle contact. No save.
   Owner supplied and agent viewed the portrait Z-offset calibration PNG.
   Replace the visibly blinking travel animation
   with a fixed nozzle/bed/title layout and phase highlights: CR Touch
   Reference, Nozzle Contact, Computing Offset. Highlight before CR Touch,
   before baseline/tare/nozzle contact, and before final candidate validation;
   finish UART/fence before measuring, with no periodic updates.
   Owner explicitly authorized extending the mainboard C receiver and a
   future firmware update, then selected host-supplied drawing coordinates.
   TJC_CANVAS_VERSION=1 provides bounded rectangle/text sessions (logical
   240x320, fonts 1/3, GBK, exclusive token/sequence, <=511 draw operations,
   five-second MCU inactivity timeout, 30-second host limit, one pending op
   and one LCD frame per task). MCU retains frame/opcode/USART2 ownership;
   this is not a raw UART bridge. Clear once initially, repaint only changed
   rows/status strip. Panel firmware and original v5 cards stay unchanged.
   TJC_LCD_RECT supplies an idle-only coordinate fill/erase command; it waits
   for motion and the final fence. Missing canvas capability gives a console
   warning and no blinking fallback. Existing LCD build flag is sufficient.
   Firmware build attempted entirely under /tmp on the Fedora development
   host, preserving checkout .config. ARM build cannot finish because the
   installed compiler lacks newlib headers (string.h/setjmp.h). No dependency
   installation, flash, service restart or printer operation was performed.
   Native C/host checks cover coordinate bounds, partial updates preserving
   artwork, palette ownership, token/order errors, timeout, abort and shutdown.
   Latest focused checks pass 106 automatic-offset/shared-engine tests,
   63 LCD host/native-C tests and 14 virtual-SD lifecycle tests (183 total).
   Mainboard ARM firmware build is still pending newlib headers; native
   success does not establish target linking or physical LCD appearance.
   Subsequent read-only validation found checkout out/klipper.bin at 52976
   bytes with embedded revision 43fc76cb and build time 20261029_211120,
   TJC_CANVAS_VERSION=1 and all five coordinate commands. BIN flash sections
   match ELF and the embedded dictionary matches out/klipper.dict. This
   validates the available artifact, not flashing or the running mainboard.
   Latest owner request: show ONLY reported MCU firmware identity on idle,
   not canvas/coordinate diagnostics. Host render_idle reads the configured
   MCU's public get_status().mcu_version from serial identification, never
   checkout version or build files. Show
   FW <8-char revision> <YYYYMMDD_HHMMSS> (27 bytes) for normal build strings;
   retain the build timestamp to distinguish rebuilds of one revision.
   Fallback is bounded FW: <reported version>, or FW: unknown, never a local
   guess. Keep branding, model, artwork and Idle. No periodic refresh or live
   print updates. Owner correction: retain the full repository URL in its
   original x12/y207 position and font 1 (8x16). Put only the reported MCU
   revision/build timestamp at the bottom, centered y304 in font 1 (8x16).
   Move Idle to y276 only when that footer is present to avoid overlap.
   Mainboard capability TJC_IDLE_VERSION=1 supports that footer. Idle slot 3
   carries that label; normal job cards and payload version 5 stay unchanged.
   This placement requires rebuilding/flashing the mainboard, not the panel.
   Until the capability is present, keep the URL in its original position and
   use the model line for a compact <revision> <MMDD_HHMMSS> identity, retaining
   the build time to distinguish firmware rebuilds. Never infer the running
   version from the host checkout or build artifacts. The owner reported build
   43fc76cb/20261029_213642 after flashing; its URL/FW positions were reversed.
   Source now corrects those positions; hardware validation remains pending.
   No hardware operations have been performed by the agent.
   The owner subsequently confirmed LCD operation after rebuilding/flashing,
   and supplied Screenshots/Zoffset.jpg showing a right-shifted/clipped FW
   footer. Font 0 does not provide the assumed 6-pixel cells on this panel.
   Use verified font 1, with width len*8 (27 bytes = 216 pixels, centered x12).
   Preserve the complete revision/build timestamp. The source centering fix
   has native frame/bounds checks; its physical appearance remains pending.
   Owner later reported AUTO_Z_OFFSET aborting at Test P1 pair 1 after warm-up
   with "LCD canvas failed: No LCD job acknowledgement from MCU". Native C
   tests reproduced a canvas begin bug at clocks such as 0x90000000: memset
   left next_frame=0, making rollover-safe comparison wait until timeout.
   Initialize next_frame from timer_read_time(), as in the job renderer.
   Test initial and partial sessions after a simulated warm-up gap, requiring
   begin ACK within 100 ms in native simulation. Keep all existing timeouts,
   guards and offset calculations. This C fix needs mainboard rebuild/flash;
   no hardware operations were performed and physical confirmation is pending.
   Latest hardware confirmation (2026-10-29/30): the owner completed the full
   two-point AUTO_Z_OFFSET workflow with LCD active and no ACK/RAW_RANGE errors.
   Candidate 1.482512 mm, P2 median 1.479191 mm, spatial 0.003321 mm; previous
   candidate 1.484710 mm differs by 0.002198 mm. No saved automatic offset or
   first-layer qualification has been reported; preserve 1.065 mm.
   Screenshots/idle_w_fw.jpg confirms the complete centered firmware footer.
   Owner supplied autoz_s1.jpg (warm-up), autoz_s2.jpg (CR Touch reference) and
   autoz_s3.jpg (nozzle contact) for the English README setup/calibration guide.
   These four final photos have explicit .gitignore exceptions. The README
   explains guided LOAD_CELL_CALIBRATE -> unloaded TARE -> known-load CALIBRATE
   -> ACCEPT -> SAVE_CONFIG, distinguishing persistent calibration/reference
   from a session-only LOAD_CELL_TARE. Never copy another printer's calibration.
2. Read-only static LCD print-status display (owner-confirmed; preserve behavior).
3. Power-loss recovery, PLR (future project; not in current scope).

## Collaboration
- Communicate with the user in Spanish. README files, project documentation, and code comments must always be written in English. This is an explicit owner preference; do not write repository prose or comments in Spanish.
- Keep personal directories, mount names and hostnames out of README files and project documentation, including this guide. Use repository-relative paths or clearly marked generic installation paths.
- For LCD development, begin with discovery and an architecture proposal; do not implement until the hardware communication path and plan have been reviewed.
