# Updating this fork from upstream

Keep CR Touch as the primary probe, share the existing load-cell contact
engine, and keep the LCD UART/rendering code in its opt-in MCU modules.
Upstream updates do not require a replacement probe or a host serial driver.

## Offline validation

Install `scripts/lcd-requirements.txt` in a separate test environment and run:

```sh
python3 -B scripts/test_fork.py --require-native -v
```

This runs the fork's Python tests and native-C LCD harnesses. It does not
connect to a printer, execute printer commands, install firmware or restart
services. A C compiler named `gcc` is required with `--require-native`.
Diagnostic warnings are retained in the reported log file. Missing dependencies
or failed tests produce a nonzero exit status; tests are not silently skipped
because Pillow is missing.

The separate `Fork validation` workflow checks both the pushed branch and a
temporary merge with upstream master. It uses read-only repository permission,
does not publish commits, and does not update any branch. Merge conflicts fail
the job. Both variants run the unit tests and build
`test/configs/stm32f103-tjc.config`. Temporary firmware builds are validation
artifacts and are not installed on a printer.

The inherited `Build test` workflow remains responsible for the full MCU
matrix, host imports and `.test` regressions. The fork workflow supplements
that coverage; it does not replace it. Local unit tests alone do not establish
physical load-cell accuracy or a safe printing offset.

## Integration procedure

1. Keep the printer installation and its configuration separate from the
   checkout used to test an update. Preserve local build settings and untracked
   files. Do not update a running printer as part of an automated check.
2. Refresh the fork and upstream refs in an isolated repository. Record the
   exact branch and upstream revisions being reviewed. A local tracking ref
   can be older than the published branch.
3. Merge upstream into each intended branch without moving unrelated features
   between branches. The LCD-only branch does not acquire Auto Z-offset merely
   because the integration branch already contains it. Preserve published
   history rather than rebasing shared branches.
4. Run the fork workflow and the inherited host/build checks on the resulting
   sources. Review changes to probing, homing, transforms and event contracts
   even when Git reports no textual conflicts.
5. Review the final diff before any separately authorized deployment. Record
   the previous revision for rollback. Host reloads, firmware flashing and
   physical qualification remain explicit deployment steps.

## Combining LCD menus with Auto Z-offset

`feature/Auto_Z_offset` is the combined development branch. Keep the
host-designed menu/card modules alongside `lcd_canvas.py` and the existing
calibration interfaces. Generic drawing, legacy coordinate canvas and v5
jobs use their own registered commands and matching abort command; do not
resolve a merge by replacing one backend wholesale with the other.

Calibration invalidates and reserves the knob menu, suppresses queued idle
drawing and rejects print preflight until its session ends. A drawing-only
MCU can render the existing calibration operations through `TJC_DRAW_VERSION=1`
without reflashing; `TJC_CANVAS_VERSION=1` remains supported. Generic firmware
uses the host-owned small idle footer even if the legacy footer capability
is also present. Preserve the static print card and printing-time PAUSE.

The current backends are `[auto_z_offset_cr]` and `[auto_z_offset_ft]`; enable
one at a time. CR uses the native withdrawal regression and validates fresh
paired measurements before offering ACCEPT. FT reports the native force-trigger
position and does not apply or save an offset. Keep fresh-measurement, contact,
spatial and acceptance checks in the tests. Do not restore retired onset,
PRTouch-selection or provisional APPLY/ROLLBACK commands to satisfy old tests.

Historical contact, onset and PRTouch helpers remain available for offline
analysis. Their tests import the analytical modules directly, without relying
on the calibration backend to re-export them. The replay tools still reproduce
the original recorded curves and offsets. Tests of removed runtime workflows
have been replaced by coverage of the current CR paired measurements and FT
point handling; analytical replay coverage remains active.

The test fixtures provide native Z steppers and printer events so the tests
exercise endpoint rejection and MCU cleanup. A result within one motor step
of the travel endpoint is rejected, including floating-point rounding at that
boundary. FT accepts two or more distinct finite points; malformed lists must
fail before trigger creation or command registration.

Run the full suite after updating upstream; use its reported count and result
rather than a historical total. The isolated STM32F103 integration build linked
with both drawing backends (53876-byte binary, within the configured 65536-byte
flash region). These checks do not validate physical offset accuracy or panel
timing. Keep backups until the reviewed merge has been committed.

## Homing and probe event contract

Upstream `homing:home_axes` reports completed homing for both conventional
endstops and Z homing through a probe. The probe path does not emit the normal
rail-begin event. Features that invalidate results after homing must therefore
observe `home_axes` as well as any early rail invalidation they require.

Where Auto Z-offset is available, completed homing discards its candidate and
contact qualification. It does not change the saved probe offset or sensor
calibration. The ordinary automatic cycle homes before collecting fresh
qualification and calibration pairs. Tests cover actual homing event dispatch,
G-code Z-origin reset, retained CR Touch result updates, and the nozzle-probe
axis-twist exclusion. Homing failures do not emit a successful completion event.

Keep virtual-SD preflight/completion hooks synchronous and small: LCD preparation
must finish before the first print command, once per loaded job. Preserve tests
for failed preparation, cancellation and pause/resume. Keep diagnostics in their
observer module and reuse the contact helper rather than duplicating upstream
probe or homing implementations. None of these maintenance rules authorizes
changes to contact speeds, force limits, the offset estimator or acceptance.
