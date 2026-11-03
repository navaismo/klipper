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

The integration retains the November 2 Auto Z-offset runtime and diagnostic
work, including previously untracked helper modules and fixtures. Older
workflow test mocks must provide current bounded tap parameters and acquired
contact reports. Manual qualification is optional in the native workflow;
tests must retain fresh-measurement, contact, spatial and acceptance checks.
Do not reinstate retired runtime prerequisites to satisfy old tests.

The combined offline suite passes 465 Python/native-C tests. The combined
STM32F103 firmware linked in an isolated validation build with both drawing
backends (53876-byte binary, within the configured 65536-byte flash region).
This validates integration, not flashing, physical offset accuracy or panel
timing. Keep owner stashes/backups until the owner commits the reviewed merge.

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
