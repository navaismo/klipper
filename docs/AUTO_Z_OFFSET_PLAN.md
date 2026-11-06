# Automatic CR Touch Z-offset: Ender-3 V3 SE

## Scope and implementation status

The standalone MVP is implemented in the development source and validated
offline. The owner has completed two-point runs with LCD phase updates on
hardware, but no saved automatic offset or first-layer qualification has
been reported. This document records its design, staged
setup and outstanding hardware qualification.

Keep CR Touch as the primary probe for Z homing and bed mesh. Use nozzle
contact through the load cell as a secondary reference for calibrating the
CR Touch offset. The current workflow uses two physical points: X28/Y28 near
the cell as the reference and X110/Y110 at the bed center for validation.
Take three fresh pairs per location. Reject local or spatial disagreement;
otherwise use the reference point's median without averaging the two points.
The initial single-point workflow and optional four-point diagnostics remain
available for compatibility.
The owner explicitly chose a user-invoked calibration while the
printer is idle, followed by saving the validated value. Do not integrate it
into PRINT_START, virtual-SD preflight, startup, or an idle event. Prints use
the saved CR Touch offset and continue generating their usual adaptive mesh.
Print-start macros and static LCD behavior during printing remain unchanged.
An optional static LCD layout highlights each phase of this idle calibration.

The implementation reuses the existing contact engine through a small shared
helper. `probe_points` selects two locations with three pairs at each;
legacy `probe_point` retains five pairs at one location. Advanced thermal
compensation and experimental statistics remain excluded. Results are
presented first; staging the probe offset requires explicit user confirmation,
followed by the existing SAVE_CONFIG workflow. Physical qualification and a
supervised first-layer trial precede accepting a new candidate. The historical
four-point qualification below explains the move to reference/validation.

## Native contact acquisition restored (2026-11-06)

The segmented onset-stop experiment below is historical and superseded.
DEV now uses the direct native contact path recovered from
`7f7f921964839a4dd5fcacb5e9554a0d0700bfc8`. Reversing the seven recorded
post-target `load_cell_probe.py` patches recovered an exact byte match with
that historical Git file, establishing the source used before the November 3
segmentation change and consistent with the 11:37 P1/P2 records.

Each tap makes one native HomingMove/drip descent at the historical 0.5 mm/s
HX711 speed. The unchanged 75 g MCU threshold provides normal stopping;
the 200 g raw limit, MCU sensor/communication checks and existing per-point
endpoint remain active. A missed trigger rejects at the endpoint, without a
deeper retry. CR Touch, the original withdrawal, LCBestFit, current onset,
PRTouch comparison/selection and explicit APPLY/ROLLBACK are preserved.
There is no host-side segment executor, streaming snapshot poll, repeated
load-confirmation wait or onset-controlled stop. The native MCU sample
watchdog is retained. Offset accuracy and a fresh-install-safe search range
are not established merely by restoring this motion path.

## Systematic offset investigation (2026-10-30)

### Independent PRTouch comparison (2026-11-03)

`contact_estimator: current` preserves the existing descent-onset selection.
`contact_estimator: prtouch` selects the independent PRTouch numerical result
from giorgiomaccari's `e3v3se_klipper_display_customCal` (`master`,
`klippy/extras/prtouch.py` and `filter.py`). It does not select another
acquisition or movement controller. LCBestFit remains unchanged as the
legacy withdrawal diagnostic; all three methods observe one acquisition.
No active configuration is changed by adding the option.

PRTouch preserves the original minimum-magnitude three-sample filter,
80-Hz/5-Hz high-pass filter, 0.8 low-pass coefficients, 64-sample input
buffer, 32-sample analysis, 3000/50000 filtered-count rules, normalization,
rotation and first-minimum contact index. These are original algorithm
defaults, not force recommendations or values fitted to this printer.
The original `_check_trigger` update repeatedly smoothing a single index is
preserved. Exhausted motion and protective max_hold cannot establish contact.
Constant signals and invalid/incomplete acquisitions return `not_evaluable`.

The data adapter uses recorded raw ADC counts minus the recorded operational
tare; it does not repeat the original driver's trimmed `read_base` procedure.
It also does not reproduce the original post-stop transport buffer. The
original filter's sample-index conventions are preserved, without inventing
a filter-delay correction. The contact index maps directly to that sample's
original MCU-derived timestamp and reconstructed Klipper-history Z. It never
interpolates between movement endpoints or compresses stationary time into
continuous descent. An 80-SPS configured rate and intact samples with no gaps
above the original 20-ms dirty-reading criterion are required; no resampling
or conversion of filtered-count thresholds to grams is performed. Segmented
recordings retain their stationary samples and their real staircase Z.
Rolling recordings whose left edge is not the recorded acquisition start
require a complete original 64-sample buffer before evaluating a prefix;
an artificial filter reset at a truncated left edge cannot become contact.

PRTouch runs alongside the current estimator even with the default selection.
If selected but unsupported, it rejects without falling back to the current
result. A supported PRTouch result remains observational: complete numerical
agreement does not enable APPLY or ACCEPT. There is no configuration switch
to bypass physical-validation requirements. The current estimator retains
its existing explicit APPLY/ROLLBACK and confirmation behavior.

`scripts/replay_prtouch_estimators.py` replays recorded JSON acquisitions
without printer access. The source excerpts and SHA256 fingerprints used by
the algorithm-fidelity tests live in
`test/fixtures/prtouch_giorgiomaccari_reference.json`. No original PRTouch
motor driver, firmware, offset application or configuration writer is ported.

### Historical onset-controlled acquisition (2026-11-03)

The 75 g MCU trigger previously served both normal completion and protection.
The four November 3 endpoint records show why that fails operationally:
the exhausted approaches reached 61.84, 70.16 and 63.08 indicated g, while
the successful approach reached 83.35 g at its endpoint. On causal prefixes
of all four recordings, the unchanged onset-confidence checks identify a
contact model before 75 g, with recorded maxima of approximately 34-44 g.
This is offline signal evidence, not physical validation of a new motion.

Acquisition now keeps one native HomingMove and one collector active across
finite MCU-scheduled advances. Native analog monitoring remains armed
throughout, including stationary host analysis and thermal checkpoints.
The provisional search reference remains `cr.test_z - minimum_offset`, based
on the existing permitted probe-separation range. Every advance, including
the initial approach from clearance to that reference, now has a fixed
0.01875 mm spatial envelope independent of speed. One largest participating
Z step is reserved for native quantization: with the stock 0.0025 mm step,
commanded advances are at most 0.01625 mm and the pulse-rounding allowance
keeps travel below the envelope. Invalid step geometry rejects before motion.
The unchanged overall floor clamps every advance. DEV's HX711 `speed` is
2.0 mm/s; CR Touch speed, release speed and other moves are unchanged.

Each advance returns its scheduled native endpoint time directly. Calling
`get_last_move_time()` here would re-prime an idle queue and wait for a later
time instead of the actual endpoint. Acquisition checks the endpoint's
original samples before scheduling another advance. There is no unconditional
three-period dwell after every advance. Only an identified onset lacking
fresh independent load confirmation holds position for the existing
three-period budget (37.5 ms at 80 Hz), then repeats the unchanged estimator
and confirmation checks. An empty acquisition can wait one nominal conversion
to obtain its first sample. Sample timestamps and filter rates are unchanged.
Previously reconstructed sample Z values are reused only within the checked
coordinate frame and retained rolling window. Fitting still begins only at
the provisional search reference. No later descent is queued while the host
waits, fits or fails. Host delay can extend stationary compression and
acquisition duration, but cannot extend the queued downward segment.
The current MCU force stop, raw band, sample/error and communication checks
remain independent. Frozen arriving data cannot manufacture onset and can
only consume the unchanged finite envelope. A hard-force stop, missing data,
invalid references or an unresolved onset at the floor fails without retry.
No firmware changes, new settings or force/sensor calibration are involved.
The MCU sample watchdog is unchanged: its strict greater-than counter check
can take five nominal periods (62.5 ms at 80 Hz), not three. That timeout or
host latency cannot enlarge an already finite segment. The 75 g threshold
remains an MCU measurement threshold, not a guaranteed peak physical force.
This change has offline scheduling/abort coverage, not physical validation
at 2.0 mm/s. Native transport lead, sample delivery and analysis still separate
the finite advances. A 2.0 mm/s segment feedrate is not a continuous 2.0 mm/s
descent or a promise of fourfold cycle-time reduction. At 80 Hz, continuous
2.0 mm/s would travel 0.025 mm per nominal conversion, exceeding the existing
0.01875 mm unobserved-travel envelope even before bulk-delivery latency.
The host therefore must not stream advances while awaiting their samples.

A reliable live onset ends normal acquisition. Its value selects the paired
offset using the existing formula. The unchanged withdrawal estimator is
diagnostic and may be unsupported; sensor/reference errors still reject.
Actual MCU trigger fields remain null on a normal onset stop; separate halt
position and segment-analysis time identify how acquisition ended. Rolling
analysis retains at most 2048 contiguous original samples, without inventing
timestamps or decimating the live fit. The causal decision report is retained
separately from the bounded post-withdrawal report.

Live finite advances use travel-based analysis windows. The existing nominal
descent durations are multiplied by the commanded probe speed to determine
spatial coverage; all selected samples keep their original real timestamps
for the drifting-baseline model and autocorrelation checks. Stationary host
waits therefore do not shrink the observed travel window. No new duration,
distance, confidence threshold or setting is introduced. A window without
enough recorded spatial coverage remains unsupported. Historical continuous
recordings without this metadata retain their original time-window replay.

The exact failed live P1 decision at 15:04 on November 3 is retained in
`test/fixtures/load_cell_onset_segmented_20261103.json`. Wall-time windows
reproduce `window_models_disagree`: the two-second fit is -0.335561190 mm
and the full-window fit is -0.298357333 mm, with disjoint profile intervals.
The same original samples selected by travel yield four supported compatible
windows, selecting -0.302581244 mm with a 0.004302339 mm inter-window span.
This repairs the window coverage mismatch introduced by stop-and-wait
acquisition; it neither validates absolute physical contact nor proves
completion of a future P1/P2 run.

The subsequent 15:11-15:14 run exposed weak-signal stops: indicated peaks
were 11.69, 11.24, 4.60 and 4.83 g. Compatible nested windows alone accepted
curvature with selected profile-interval widths of 0.058259, 0.049983,
0.134305 and 0.107745 mm. Those four exact causal decisions are retained in
`test/fixtures/load_cell_false_onsets_20261103.json`.

Normal acquisition now also requires the selected fit's existing profile/
sampling interval to fit within one bounded search advance. This is an
explicit conservative stop-precision policy, derived from the already
computed advance budget rather than a new printer setting or correction.
It is not a physical force limit, an absolute accuracy guarantee or proof
that signal drift cannot mimic contact. The fit and its mathematical status
remain unchanged; `stop_qualification` separately rejects inadequate
localization. Such a result cannot stop normal acquisition or become an
offset candidate. Searching may continue only inside the original endpoint
with the native guard still armed. An unresolved floor still fails.

The stop decision additionally requires observed compression, independently
of change-point stability and fit precision. Use the selected fit's existing
free-motion baseline and real-time drift only to detrend the observed free
samples above its upper profile bound. Compute their RMS residual directly,
without dividing by the square root of their count. Reuse the existing
diagnostic force-rise criterion, max(5 times this RMS, 1 g), and its three
consecutive confirmation samples; no new threshold or setting is added.
All three newest samples must be below the lower profile bound, exceed that
rise both relative to the local operational tare and above the drifting
baseline, and be fresh within the native sample-monitor budget. An old force
spike or an extrapolated transition cannot confirm the current stop. This
does not establish that all externally imposed loads are nozzle contact.

The four weak decisions have ordered timestamps/Z, unchanged per-contact
tares, and latest-sample ages of 0.0003-0.0078 s. No stale replay or inter-pair
state leak is demonstrated by them. Their recent detrended excesses are only
about 0-5 g against roughly 10-11 g required from observed free noise. The
defect was treating fit agreement as sufficient without this measured-load
condition. The precision policy alone was insufficient.

Each new acquisition captures its own operational tare and sample epoch;
changed tare, pre-epoch samples, nonadvancing decision times and stale data
reject. Original force/count/tare samples remain paired with step-history Z
at their original timestamps. `stop_qualification.load_evidence` records the
observations and rejection reason. The normal orchestrator also requires a
qualified stop and qualified load evidence before forming any paired result.
Missing/rejected evidence cannot authorize APPLY; the existing full-run
local/spatial checks and explicit application remain mandatory.

The first-P1 bootstrap limitation below remains: a native numerical homing
seed may leave physical contact outside the existing bounded envelope.
This change neither invents a fresh-install-safe floor nor uses 1.065 mm as
a calibration target. It must fail when the envelope cannot support contact.
Only a complete valid P1/P2 run can produce a provisional candidate; explicit
APPLY/ROLLBACK and saved configuration remain unchanged.

### Repeated P1 endpoint correction and bootstrap limitation (2026-11-03)

The 11:37 P1 success triggered at the approach endpoint (-0.345 mm).
The 11:38 failure was Calibrate P1 pair 2, not an initial pair; additional
11:40/11:43 runs failed at their first P1 pair. Their recorded maximum loads
were 61.835017, 70.164987 and 63.079725 indicated g respectively, below the
75 g setting. Complete acquisitions and endpoint summaries are retained in
`test/fixtures/auto_z_offset_p1_endpoints_20261103.json`. Before the first
sample at the final Z, maximum recorded forces were lower still. There is
no logged MCU arming acknowledgement. Native source arms/reset filters and
dispatch for every approach; no arming defect is demonstrated by these logs.

The P2 reference correction unnecessarily excluded later P1 pairs. Those
pairs now use the same supported P1 reference and existing 0.5 mm allowance;
the reference is still discarded on exit, and failures never trigger retries.

This corrects the repeated-pair defect only. The first P1 still uses
`floor = cr.test_z - configured_offset - retract_distance`. Its physical
reach therefore depends on the initial offset; a 0.5 mm retraction is not
evidence of the missing probe-to-nozzle separation on a fresh installation.
Existing logs do not justify choosing a universally safe deeper bootstrap
endpoint. Reusing previous-run contact depths, doubling a distance or falling
back to the global minimum would conceal this limitation. No such change
is made, and a new initial-contact trial is not qualified by this fix.

### P2 approach reference correction (2026-11-03)

The 11:07-11:08 run completed P1 but exhausted P2's HX711 approach without
triggering. P2 CR Touch completed at test_z 1.066666667 mm; nozzle motion
started at Z5, targeted -0.498333333 and terminated at -0.497500000 mm.
All 928 failed-descent samples are retained with the preceding complete
1,324-sample P1 acquisition in
`test/fixtures/auto_z_offset_p2_no_trigger_20261103.json`.
Indicated load rose near the endpoint but peaked at 47.316322 g, below the
configured 75 g trigger. Raw protection and acquisition reported no error.
The failed trace has no trigger/ascent and cannot supply a contact candidate.

The old bound already included P2's -0.1325 mm relative CR Touch change.
However, subtracting the existing 0.5 mm allowance from cr.bed_z still used
the old 1.065 mm offset as a surface estimate. The supported P1 separation
was 1.478893422 mm, consuming 0.413893422 mm of that allowance before the
estimated surface; only 0.086106578 mm remained below it at P2.

Later locations now translate the first supported P1 nozzle onset using
the current relative CR Touch Z. For this recorded geometry the estimated
P2 surface is -0.412226756 mm and the derived floor is -0.912226756 mm,
retaining exactly the existing 0.5 mm allowance. The reference is frozen
within a run and discarded on success/failure. P1's approach and estimate,
force thresholds, native Z range, estimator and explicit application remain
unchanged. This is an initial endpoint calculation from same-run evidence,
not an automatic deeper retry after failure. Actual P2 force-trigger/model
success still requires a future operator-controlled run; offline sequencing
tests do not establish it.

### Normal workflow uses contact onset (2026-11-03)

Normal AUTO_Z_OFFSET, CALIBRATE and TEST_CONTACT now reuse the existing
descent-onset estimator and its unmodified confidence criteria. The paired
separation is `cr.test_z - onset.z`. Unsupported onset, invalid coordinates,
insufficient signal or incompatible supported windows reject the pair with
no legacy-fit fallback. No printing-offset target or new constant is used.
The old withdrawal/descending fits, branch gap and window sensitivity remain
diagnostics for hysteresis and relaxation; their agreement no longer gates
the candidate. The acquisition/retraction engine, fatal errors, MCU, force,
travel, thermal and local/spatial checks remain unchanged. A complete normal
run must pass both preliminary pairs and all six calibration pairs before
explicit provisional APPLY is possible. DIAGNOSTIC remains non-applying.

The complete 1,327-sample hot P1 trace of November 3 at 10:52 is preserved in
`test/fixtures/load_cell_contact_hot_20261103_1052.json`. Its onset is
-0.205713376 mm and paired separation 1.399046709 mm. Legacy branch gap
0.050474499 mm remains visible; it is not hidden by changing 0.025 mm.
Three earlier cold traces also support onset; the three earlier hot traces
remain unresolved because supported-window models disagree. Replaying P1
data through mocked P2 delivery tests command sequencing, not actual spatial
agreement: a new live full run must measure P2 and repeatability itself.
No saved result or first-layer qualification follows from this offline test.

Earlier sections below describe the historical qualification policies;
this integration supersedes their normal-mode branch-agreement gate.

### Unsaved first-layer trial (2026-11-03)

The real two-point workflow remains `AUTO_Z_OFFSET BED_TEMP=60
NOZZLE_TEMP=170`: preliminary P1/P2 pairs followed by three fresh pairs at
each point. Local/spatial, thermal, raw-force and bounded-motion checks
remain unchanged. Rejection cannot create an applicable result. Supported
descent onset now selects the paired contact; withdrawal is diagnostic only.

After a successful normal run, explicit AUTO_Z_OFFSET_APPLY consumes the
validated candidate and sets a temporary native G-code Z correction equal to
configured probe offset minus measured offset, with MOVE=0. The configured
probe/endstop, sensor calibration and saved configuration are untouched.
The old probe offset defines a coordinate origin, not the measurement target.
The paired calculation subtracts two measurements in that same origin.
An arbitrary initial offset can nevertheless leave contact outside the
existing bounded approach: report failure instead of expanding travel.

The temporary correction persists through native homing and the current
PRINT_START, which does not reset it. Other macros/slicer commands that reset
G-code Z offsets can cancel the trial. Once the print has ended or been
cancelled, AUTO_Z_OFFSET_ROLLBACK restores the pre-trial correction without
movement. Restart discards the temporary trial and reloads saved settings.
Never use ACCEPT or SAVE_CONFIG merely to run this first-layer experiment.
The trial command cannot validate physical offsets or turn unresolved onset
into a candidate. It requires a successful normal full measurement run.

### Diagnostic descent contact onset (2026-11-02)

DIAGNOSTIC=1 additionally fits `force = baseline + drift * time +
compression * max(contact_z - z, 0)` to the recorded descent. Time is the
original sample print_time, not host arrival or a substituted nominal rate.
The baseline can drift; compression must be positive. Candidate change points
cover the observed Z grid with local refinement. The original LCBestFit,
MCU trigger and movement endpoints remain unchanged. Retraction is compared
for hysteresis/relaxation but is not required to match the new diagnostic
contact coordinate. No normal calibration/acceptance path uses the new onset.

The existing 0.25/0.5/1/2-second and full-descent windows provide local models.
A drifting baseline alone is the null model. Positive BIC improvement uses an
observed effective sample count; initial-positive monotone autocorrelation
sums estimate residual information separately in free/compressed regions.
The slower decorrelation is used for the whole window, not a pool of presumed
independent repetitions. A nominal 95% profile-likelihood convention plus
sample spacing gives approximate conditional intervals. Too little effective
support, absent compression evidence, open/multimodal profiles or incompatible
supported windows leave the onset unresolved. Nested windows are never
averaged as independent data. The shortest supported local window supplies
an estimate only when its interval overlaps all supported-window intervals.
The envelope retains all their variation. No printing offset or fixed
millimeter rejection/correction constant is used.

This confidence describes fitted-signal support, not independently verified
physical contact. ADC delay, mechanical force transfer and unmodeled baseline
curvature remain possible shared biases. Unresolved traces retain per-window
models/reasons but have null onset Z and null onset-derived offset. Even
identified diagnostic onsets have `eligible_for_application: False` and
cannot be staged with ACCEPT. Legacy ascent results and branch verdicts are
still reported separately. Diagnostic state follows the onset result only;
normal AUTO_Z_OFFSET continues using its existing estimator/qualification.
All original MCU, thermal, fit-error and cleanup errors still propagate.

Six full original traces (three cold and the three hot runs discussed with
the owner) are in `test/fixtures/load_cell_contact_six_20261102.json`.
Their provenance explicitly identifies the selected acquisitions. Replay:

```bash
python3 -B scripts/replay_load_cell_contact.py --fixture test/fixtures/load_cell_contact_six_20261102.json
PYTHONPATH=test/unit python3 -B -m unittest test_load_cell_contact_onset
```

### DEV contact-model qualification and real replay (2026-11-02)

The five complete acquisitions from 2026-11-02 (three cold, two at targets
60/170 C) are retained in
`test/fixtures/load_cell_contact_cold_hot_20261102.json`: 1270, 1266, 1272,
1305 and 1310 original samples. All five replay with the original primary
offsets and rejection decisions. Both hot descents are `identified`; both
hot ascents are `insufficient_signal`, producing the pair rejection
`unsupported_contact_branches`. The previous console incorrectly showed an
unavailable descent because qualification returns before copying either fit
when one branch is unsupported. It also displayed the primary ascent result
as if it were a supported ascent estimate. Reporting now separates the two
branch estimates/statuses from the unchanged `primary_fit_z` and observational
offset. There is no estimator correction, window substitution or relaxed gate.

In the hot ascent fits, observed peak minus fitted free-region mean is
45.912/49.576 indicated g; the existing five-noise-RMS check requires
52.043/50.370 g. The free-region force/Z slopes are +199.190/+183.816 g/mm,
also incompatible with the existing flat-region check (19.344/20.779 g/mm).
The negative valleys reach -23.327/-31.161 g and then rebound in the original
ADC counts. Sample times are ordered, both coordinate frames are consistent,
and the recorded rate is approximately 82.624 Hz. There is no evidence here
of a unit-conversion, branch-ordering or sample-window indexing defect.
The full five-second ascent does not support the stationary unloaded region
of the hinge model. Shorter fits are not an established physical surface;
neither temperature compensation nor a mechanical cause is inferred.

```bash
PYTHONPATH=test/unit python3 -B -m unittest test_load_cell_hot_contact
python3 -B scripts/replay_load_cell_contact.py --fixture test/fixtures/load_cell_contact_cold_hot_20261102.json
```

For a prepared, homed, mesh-free cold DEV printer, the existing Auto Z-offset
pipeline can perform one observational P1 pair:

```text
AUTO_Z_OFFSET DIAGNOSTIC=1 COLD=1
```

This uses native CR Touch probing and an integrated, fresh-reference-relative
HX711 endpoint. It does not home, visit P2, retry contact or run a
multi-pair calibration. CR Touch's existing internal sample count is retained.
The existing sensor, tare/raw protections, LCBestFit and offset formula are
unchanged. Omit COLD to use the existing configured heated preparation;
COLD cannot be combined with temperature parameters.

Branch disagreement is reported with both fits and the observational offset,
without turning the rejected model into a candidate. Diagnostic reports have
`diagnostic_only: True` and `eligible_for_application: False`; even internal
agreement cannot qualify points, stage a value or enable ACCEPT. Safety and
cleanup errors still propagate. Full AUTO_Z_OFFSET and AUTO_Z_OFFSET_CALIBRATE
are enabled without separate manual qualification. No MCU or additional
configuration is required. The owner reloads DEV host modules; no
physical experiment is run by the offline tests.

The C0 command blocks and seven mandatory `test_*` limits have been removed.
Old `test_*` entries are consumed only for configuration compatibility and
cannot select motion endpoints. The C0-only TEST_PROBE command is retired;
normal PROBE, G28, primary-probe endstops and coordinate handling are unchanged.
Every nozzle tap instead passes explicit bounds to the existing contact
engine: `floor = cr.bed_z - load_cell_retract_dist`, using the existing tap
retraction distance. The maximum approach distance is clearance minus this
floor. A non-finite endpoint, endpoint at/below the native minimum or endpoint
at/above clearance rejects the nozzle approach. There is no fallback to the
global minimum, deeper retry or stale cold Z0 endpoint. Fresh paired CR Touch
readings update the reference in the current thermal/coordinate state. This
is finite-travel fault containment, not a guarantee of physical force safety.
Raw MCU force/range/sensor guards and model-consistency checks remain active.

The three original acquisitions are retained without decimation in
`test/fixtures/load_cell_contact_real.json` (1279, 1337 and 1342 samples).
The fixture includes original raw ADC counts, forces, sample timestamps,
reconstructed Z, reference snapshots, paired CR Touch records, source log
line numbers and its SHA256. It preserves the active 114.88636 counts/g
conversion. These are reconstructed coordinates, not independent physical
displacement measurements.

Run the deterministic offline replay from the checkout root:

```bash
python3 -B scripts/replay_load_cell_contact.py
python3 -B -m unittest discover -s test/unit -p 'test_load_cell_contact.py'
```

The replay uses the installed LCBestFit and the existing diagnostic branch
processing. Every original sample timestamp and reconstructed position is
reused; no configured nominal rate substitutes for timestamps. No force
rescaling, tare correction, midpoint replacement or printing-offset target
is used. The first fixture matches the original standalone P1 diagnostic.
The command reports source hashes and never connects to Klipper or a printer.

| Acquisition | Original ascent-based offset | Descent-based interpretation | Branch difference | New decision at 0.025 mm |
| --- | --- | --- | --- | --- |
| 14:24 | 1.094167 mm | 1.028626 mm | 0.065541 mm | Reject inconsistent model |
| 15:39 | 1.502229 mm | 1.445725 mm | 0.056504 mm | Reject inconsistent model |
| Later | 1.515000 mm | 1.526166 mm | 0.011166 mm | Internally consistent; physical bias unknown |

`load_cell_contact.qualify_contact()` checks both identified branches,
coordinate/timing integrity, acquisition errors and a sustained descending
force rise. It requires the span of descent/ascent/primary contact estimates
to fit within the existing `offset_tolerance`. This is an experimental
agreement budget, not a statistically derived confidence interval. Samples
are not assumed independent, and disagreement is not divided by sqrt(N).
The primary result is still the existing ascent fit; no estimator is replaced.

Auto Z-offset consumes the already computed report after the original
retraction and primary fit. Missing diagnostics or failed qualification stop
the pair without a candidate/retry. Model rejection retains `last_contact`
with null `offset`, original `model_offset` and `contact_qualification`.
The ordinary load-cell probe's result/error behavior remains unchanged;
MCU errors continue to propagate before qualification is reached.

This intentionally rejects the first acquisition too: closeness to the
functional 1.065 mm is not an acceptance criterion. Conversely, the third
acquisition cannot honestly be rejected solely because it yields 1.515 mm.
Its two branches agree despite the suspected shared bias. The reported
`physical_contact_verified` remains False and `physical_uncertainty_mm`
remains null (unknown), including for an internally consistent model.
`contact_model_span_mm` describes the available model estimates only and
does not bound the true physical surface.

The large between-acquisition shift is already present on descent. Descent
slopes are approximately -500, -554 and -1006 indicated g/mm, so the complete
series is not explained by a rigid Z translation alone. Force-transfer
preload/compliance, nonstationary signal behavior and actual motion/reference
differences remain competing explanations. Without an independent reference,
a common displacement of both branches is unidentifiable: translating both
fits preserves their internal agreement. The new gate detects branch
inconsistency; it does not claim to solve the approximately 0.4 mm bias.
Integrated nozzle bounds, MCU stopping, B1 calibration and
saved printer configuration are unchanged. No physical operation is part
of this replay or implementation.

The runtime now adds the same window-sensitivity measurements as diagnostics
only. `window_sensitivity` is retained in the tap JSON, pair frame and
`auto_z_offset.last_contact`, alongside the existing `contact_qualification`.
It does not select another Z or change that qualification verdict. The full
descent and full retraction fits are reused when their windows match; other
windows call the unchanged LCBestFit on the already reconstructed samples.
Analysis is bounded to the existing per-fit sample budget and fixed windows.
Residuals use the least-squares baseline at each window's fitted hinge/slope;
they are descriptive values, not independent-sample standard errors.

| Acquisition | Previous runtime sensitivity | Added descent variation | Added retraction variation | Existing verdict |
| --- | --- | --- | --- | --- |
| 14:24 | Not reported | 0.057488 mm | 0.029794 mm | Rejected |
| 15:39 | Not reported | 0.046314 mm | 0.013062 mm | Rejected |
| Later hot | Not reported | 0.001757 mm | 0.001991 mm | Internally consistent, physical bias unknown |
| 2026-11-02 cold | Not reported | 0.000783 mm | 0.000070 mm | Rejected |

The cold trace retains all 1270 original samples in
`test/fixtures/load_cell_contact_cold_20261102.json`. Fixed pre-integration
window-fit values for all four acquisitions are retained in
`test/fixtures/load_cell_window_reference.json`. Focused tests check all 36
window fits, their residuals/slopes, unchanged primary results/verdicts,
incomplete windows/references and diagnostic-error isolation. Insufficient
coverage or fewer than two distinct valid windows produces null sensitivity.
There is no stable/unstable threshold and no claim that numerical stability
locates the true physical surface. MCU, motion bounds, saved calibration and
the current experimental 0.025 mm branch protection are unchanged.

```bash
python3 -B -m unittest discover -s test/unit -p 'test_load_cell_window_diagnostics.py'
python3 -B scripts/replay_load_cell_contact.py --fixture test/fixtures/load_cell_contact_cold_20261102.json
```

The owner reports a working printing offset of 1.065 mm, automatic results
around 1.48-1.52 mm, and excessive nozzle pressure/PEI marks when adopting
the automatic result. Manual PROBE_CALIBRATE at comparable hot conditions
also returned about 1.482 mm. The unrelated 0.923 mm paper-contact result
is excluded: inconsistent paper contact does not demonstrate thermal drift.
Keep 1.065 mm unchanged. Experimental candidates remain diagnostic-only;
do not accept/save them or print with them during this investigation.

### Phase 1: confirmed coordinate and timing findings

| Chain element | Confirmed source behavior | Consequence |
| --- | --- | --- |
| CR Touch result | `test_z` is the toolhead-frame trigger coordinate; `bed_z = test_z - old_offset`. | Do not subtract the old offset twice. |
| Manual calibration | `old_offset - manual_bed_z + probe_bed_z`. | With the manual probe's zero offsets this reduces to `test_z - nozzle_z`. |
| Automatic pair | `cr.test_z - fitted_nozzle_z`, at one physical bed XY. | Same sign and coordinate convention as manual calibration; the old offset cancels. |
| Homing move | Returns the trigger position but sets toolhead position to the halt position, using step history. | Trigger and halt are not interchangeable. Both must be logged. |
| First-home workaround | Only active for command G28 with HOME_ATTEMPT_NUM=1. | AUTO_Z_OFFSET_TAP does not enable it; a generic load-cell G28 may rebase Z. |
| Z reconstruction | Converts historical MCU step counts through the current stepper coordinate mapping and kinematics. | A changed origin affects historical coordinates too; record mappings around the move. |
| HX711 timestamps | FixedFreqReader estimates sample times from bulk counts and MCU clock regression; they are not host arrival times. | Configured 80 SPS is not blindly substituted for the measured clock rate. |
| MCU trigger time | The analog trigger records its MCU clock and converts it to print_time on the host. | Compare it with bulk sample times in the same print_time domain. |

The mathematical LCBestFit solution is the loaded-line/unloaded-constant
hinge model. It estimates release during ascent, not first physical contact
during descent. Current ascent logs alone cannot measure the gap between
these events. The existing primary fit checks force sign, finite values,
monotonic Z and sample support, but does not explicitly validate every sample
timestamp for strict ordering before selection. This is a diagnostic gap,
not proof that a timing fault caused the observed offset error.

The local Marlin reference checkout was inspected on branch c13_sd, revision
0cdabb7337ff08c6ab8c668907474cc9107694ea. The remote GitHub content could not
be fetched during this audit, so equivalence to today's remote is unverified.
Marlin descends in steps, filters a trailing force window, and calMinZ()
selects a point from the normalized/rotated descent signal. It does not use
Klipper's ascent-release estimator. Active NOZ_TEMP_OFT_MM adds 0.05 mm to
the measured press value; the 0.02 mm NOZ_AUTO_OFT_MM application is commented
out. Robust spatial weighting and a median-deviation clamp are active, but
are not fixed corrections. SET_Z_OFFSET also schedules babystepping, with
Marlin's negative offset convention and subsequent homing. None of these
operations should be copied as a Klipper sign/origin correction.
The reference's printTestResult reads three elements while Hight_One passes
one-element arrays; those extra debug values are not reliable measurements.

Twelve previously retained ascent traces replay with zero difference from
their recorded fits. Their release-minus-trigger distances span roughly
0.085-0.286 mm across the historical locations, and fitted free baselines
range from -6.94 to +24.71 g relative to local tare. Model RMS is 2.08-4.52 g.
These artifacts lack descent data and MCU trigger timestamps; they cannot
establish the cause of the latest two-point error. Two representative traces
are retained in `test/fixtures/load_cell_ascent_reference.json` for regression.

### Hypotheses ranked by current evidence

1. A shared physical reference or load-path bias remains a leading hypothesis:
   automatic and manual calibration agree, yet the known printing adjustment
   is smaller. Bed/plate compliance, preload/friction, nozzle contact state,
   and CR Touch/toolhead mounting need separation. Repeatability and agreement
   between two points do not demonstrate an unloaded, mechanically correct Z.
   Ideal linear compliance alone is removed by the hinge extrapolation;
   nonlinear load transfer, preload and friction are hypotheses to measure.
2. Descent contact versus ascent release, with baseline changes, may explain
   part of the bias. It has not been measured in both directions. The latest
   reported 0.146979 mm trigger-to-regression difference is not a measurement
   of full hysteresis and does not establish the entire approximately
   0.46 mm discrepancy. No constant is subtracted.
3. Sample-time alignment or a coordinate rebase can bias reconstructed Z.
   No such event is proven in the reported run. At the configured 0.5/0.1 mm/s
   speeds, a 0.1-second delay corresponds to about 0.05/0.01 mm on a steady
   descent/ascent. A large timing explanation needs correspondingly large
   measured errors, not an assumption about a single ADC conversion.
4. A printing/calibration state difference remains possible but less supported.
   The owner reports no hidden Z-offset commands in the print file. Available
   configuration retains 1.065 mm; auto calibration clears the mesh and uses
   toolhead-frame moves. Default mesh fade is disabled in the inspected setup.
   The current Fluidd/KAMP include targets are inaccessible through their
   symlinks, so their current contents and live G-code origins are not newly
   certified. Earlier audits do not replace the new runtime snapshots.

No unique physical root cause is established. In particular, a paper procedure
and an automatic procedure agreeing does not prove either matches the desired
first layer, but the bed marks make adopting the current candidate unsuitable.

### Phase 2: passive tap instrumentation

`load_cell_diagnostics.py` observes the existing collector and step history.
It is enabled by the existing log_trace=True used by Auto Z-offset; the
ordinary primary load-cell probe remains unchanged with tracing disabled.
The collector still starts before descent, stays active through the same
retraction and closes as before. No additional sampling, movement, queue
flush or get_last_move_time call is introduced for diagnostics. Approach
speed, Z minimum, trigger/force limits, retract distance, ascent window,
primary LCBestFit result and paired formula are unchanged.

Each `Load cell tap diagnostic: ` log entry contains one JSON object:

- Original sample time, calibrated force, reconstructed Z, phase, raw counts
  and per-sample tare. Times use seconds in print_time; Z uses millimeters
  in the current kinematic frame, not G-code coordinates or absolute metrology.
- MCU-derived trigger time, returned force-trigger Z, reconstructed Z at that
  time, nearest bulk sample's time difference/force, and primary regression Z.
- Version 2 distinguishes `descent_fit_z` (a diagnostic hinge-model estimate),
  `descent_threshold_z` (the first sample in a sustained force rise),
  `mcu_trigger_z` (the original returned MCU trigger position) and
  `ascent_fit_z` (the independent diagnostic release fit). Neither fit is an
  exact measurement of physical contact. `regression_contact_z` remains the
  existing primary result; no diagnostic estimate replaces it.
- Independent diagnostic hinge fits for descent and ascent, using the same
  LCBestFit model. The descent rows are reversed in time, never sorted by Z.
  Estimates require finite, ordered times/coordinates, positive compression,
  three samples on each side, adequate signal relative to free-region noise,
  and an approximately flat free region. These quality checks affect only
  diagnostic estimates, never the existing probing result or safety trigger.
- When both fits are supported and the references are complete and consistent,
  `ascent_minus_descent_fit_mm` and `baseline_change_g`. The historical
  `release_minus_onset_mm` is retained as an alias of the fit difference,
  not an independently measured physical hysteresis. They remain null when data
  cannot support comparison; an identified fit is not certified physical zero.
- Local/saved tare, baseline force, point safety reference, configured limits
  and the maximum force among all host-collected samples. This observed peak
  is not a guarantee of the exact MCU/physical maximum between samples.
- Cached actual/target temperatures, active probe offsets, G-code origin,
  G-code position, mesh state, stepper coordinate mappings before/after
  probing and the first-home workaround, and the motion-history cutoff.

The trigger region is a timestamp classification from one nominal sample
period before the trigger through ascent start; it is not a separately
detected physical contact event. Actual sample interval statistics are also
logged. Reconstruction older than available history is marked unavailable.
Missing/invalid/non-increasing timestamps, missing boundaries, sensor errors
or changed origins prevent a comparable estimate. Each reference getter is
isolated: a failed probe read does not suppress mesh, G-code origin or history
reads, and a failed heater/stepper does not suppress its siblings.
`quality.references_complete` requires all four snapshots, finite coordinates,
complete stepper-name/mapping sets, probe offsets, G-code origin/position,
known inactive mesh, history cutoff and successful coordinate-reference reads.
Transient mapping/origin/offset changes are checked at every snapshot, even
if restored later. Optional temperature-read failures remain visible but do
not by themselves invalidate coordinate references; this observer does not
replace the existing thermal safety checks. Missing references produce
`incomplete_references`, never a comparable pair of curves.
Diagnostic fitting errors
produce a reason, and observer exceptions never replace a primary result or
mask an original probing error. If acquisition fails before returning any
samples, no complete curve can be recovered from those unavailable data.
Every diagnostic integration boundary is guarded, including metadata updates,
snapshots and final reporting while a primary exception is being propagated.
Failures emit a warning; a failed warning handler is also contained.

The threshold detector is independent of either fit. It assumes the first
eight descent samples are unloaded, estimates their mean and RMS noise, and
requires three consecutive later samples above mean + max(5 * RMS, 1 g).
An obviously trending baseline, insufficient data, reversed descent or no
sustained crossing leaves the threshold unavailable with a reason. A lone
spike is insufficient. The reported Z/time belong to the first sample of the
confirmed run; there is no interpolation or ADC-latency correction. The
unloaded-prefix assumption cannot be proven from force alone. This threshold
is diagnostic only and is neither the MCU stopping threshold nor physical zero.

Logs contain at most 2048 sample rows, with count/stride metadata and retained
endpoints, trigger neighbourhood, peak and phase boundaries. At the working
80 SPS the usual tap fits within this budget. Faster/longer captures are
explicitly decimated; no missing curve is fabricated. Full extrema are taken
before decimation. Captures of at most 2048 samples retain every sample and
its original timestamp. Existing ascent-only and safety logs remain available.
No MCU or panel firmware change is required. Update the host helper together
with load_cell_probe.py; deployment/reload is an operator step, not performed
by this work.

### Phase 3: offline checks and proposed hardware test

The focused suite covers ideal contact, hysteresis, mechanical compliance,
baseline drift, delayed/bad/missing timestamps, coordinate shifts, different
probe offsets, incomplete release, noise, failed fits, bounded JSON, legacy
real ascent replay, and observer failure. Integration mocks compare logging
enabled/disabled and require identical moves, primary result, force-trigger
Z, collector reuse/closure, and the same two existing move-time reads.
The original 106 contact/workflow tests remain required alongside these
diagnostic tests. Tests are offline and do not qualify physical contact.

Run all offline unit/native harness tests with:

```bash
python3 -B -m unittest discover -s test/unit -p 'test_*.py'
```

The reproducible report benchmark uses synthetic 500/2048-sample taps, both
valid diagnostic fits and the real shared LCBestFit implementation:

```bash
python3 -B scripts/bench_load_cell_diagnostics.py --iterations 200 --warmup 10
```

It reports actual CPU/architecture/Python identity, source hashes, sample/log
counts, JSON size and minimum/median/p95/maximum/mean processing times. Separate
timings cover report construction and construction plus JSON serialization.
Synthetic position lookups exclude real MCU step-history costs, disk logging,
sensor/UART traffic and scheduler contention. Results from a development PC
are not Raspberry Pi 3B+ measurements; no reliable Pi time follows from them.
The script imports offline printer mocks and never connects to a printer.

Proposed next hardware test, pending explicit owner approval: deploy/reload
the diagnostic host files while idle, verify the working offset is still
1.065 mm, and perform one supervised existing TEST_CONTACT at P1 with the
current validated parameters. Confirm homed XYZ and an inactive mesh first;
any needed homing/mesh-clear/heating is part of that approval, not an action
already performed. Keep temperatures consistent with the comparison and use
the existing targets/force limits. Retrieve the JSON entry together with the
paired CR Touch result, safety and ascent logs. Stop on any measurement error.
Do not accept a candidate, save configuration or print. Compare onset,
release, trigger reconstruction, baseline shift and frame/timing flags offline
before proposing further tests or any change to contact detection.

### Current two-point setup

In the existing `[auto_z_offset]` section, replace the four-row `probe_points`
value with the following. Do not also specify `probe_point`:

```ini
probe_points:
    28, 28
    110, 110
```

Keep the existing contact speeds and force limits, and preserve the functional
`[bltouch] z_offset: 1.065`. The fully automatic preparation is now included:

```gcode
AUTO_Z_OFFSET
```

Default targets are 50 C bed and 170 C nozzle. Existing explicit `bed_temp` /
`nozzle_temp` configuration values still override these defaults. A command
can override them again, for example `AUTO_Z_OFFSET BED_TEMP=65 NOZZLE_TEMP=175`.
After validating idle state, sensor calibration, travel bounds and parameters,
the command warms up once, clears the active mesh if configured, runs full G28
and rechecks readiness, geometry and temperature. Clearing precedes homing to
exclude the previous print's mesh from its travel; the mesh remains cleared.
It then tests P1 and P2 and acquires six fresh calibration pairs only if both
pass. The preliminary pairs do not enter the calibration result. It does not
accept or save. PRINT_START and KAMP are unchanged.

The contact-test stage is one paired measurement per point (two total).
The calibration stage is three fresh paired measurements per point (six
total), and starts only after both tests pass. Multiple CR Touch readings
inside a pair follow its configured sample count; they are not additional
contact tests. Any failed contact, invalid measurement or failed validation
stops the sequence without using the remaining samples or changing the offset.

The separate TEST_CONTACT and CALIBRATE commands remain manual diagnostics:
they still require homed XYZ and an explicitly inactive mesh. Homing/restart
invalidates their qualifications. The automatic command always takes fresh
supervised pairs after its own homing.

### Partial LCD phase screen

The owner supplied a portrait reference showing a nozzle/bed illustration,
Auto Z-Offset Calibration title and three rows: CR Touch Reference, Nozzle
Contact, Computing Offset. The implementation approximates this with bounded
rectangles, code-native icons and the panel fonts. It paints the background
once, highlights CR Touch before that reading, Nozzle Contact before
baseline/tare/contact, and Computing Offset before final spatial/candidate
validation. A transition repaints only old/new row bounds and a small status
strip; the illustration/title and other rows stay unchanged. No periodic
animation, motion callbacks or whole-screen erase is used for transitions.

The owner authorized extending the mainboard receiver after its existing v5
full-card erase caused visible flicker. `src/tjc_canvas.c` advertises
`TJC_CANVAS_VERSION=1` and accepts host-supplied rectangle/text coordinates.
The host's `lcd_canvas.py` prepares semantic operations; the MCU validates
bounds, dimensions, fonts, GBK and sequence, constructs actual LCD frames and
owns USART2. This is not a raw UART bridge. The original print/idle v5
transport and panel firmware stay unchanged. Update/rebuild/reflash the
mainboard firmware with the same existing CONFIG_TJC_LCD setting to enable
this feature; no new menu flag or sensor firmware protocol is introduced.
With older mainboard firmware, calibration reports the missing capability
and leaves the screen untouched; there is no blinking fallback.

Every screen update has an exclusive token and sequential operation indices,
<=511 drawing operations, a five-second MCU inactivity timeout and a 30-second
host limit. One pending operation and one LCD frame per task keep work bounded.
Font 1/3 text and rectangles must fit logical 240x320 coordinates. Filling a
rectangle with the desired background erases only that region. End with the
existing handshake plus 50 ms settling, then stop UART/timer before measuring.
No LCD traffic occurs during CR Touch sampling, ADC baseline/tare, force
contact or its fit. Phase updates run before those operations; the final
Computing Offset row describes final paired/spatial result validation.

Mainboard build/flash and visual hardware qualification remain operator steps;
the agent has not run heaters, homing, motion, restart, install or flash.
Native C simulations validate drawing bounds, coordinate errors, partial
updates preserving static artwork, tokens, timeouts, abort and shutdown.
No new Pillow dependency or stored raster image is required.
The local ARM build was attempted with isolated /tmp configuration/output;
it is blocked by missing newlib headers (string.h/setjmp.h) in this development
toolchain. No dependencies were installed and the checkout .config was
preserved. A successful target build and hardware review are still required.
Current focused checks pass 106 automatic-offset/shared-engine tests,
63 LCD host/native-C tests and 14 virtual-SD lifecycle tests (183 total).
Other Pillow-dependent unit modules remain unavailable in this interpreter.

An owner-reported Test P1 pair 1 failure during the LCD highlight exposed a
canvas clock-initialization bug reproduced in native tests. Canvas begin now
initializes its send deadline from the current MCU clock instead of zero.
This requires updated mainboard firmware; see docs/LCD_STATUS_PLAN.md. It does
not change contact estimation, force/thermal guards or ACK timeout limits.
The owner subsequently confirmed a complete run with LCD phase updates and
no ACK errors; see the hardware qualification below.

Failures report the stage and point/pair plus the original error; the first
failed supervised test prevents all calibration pairs. Spatial rejection
retains both reports. Old heater targets are restored and 1.065 remains
unchanged unless separately accepted/saved. Inspect `auto_z_offset.stage`
and `error`; full error details stay in the console/log while LCD wording
is bounded to fit the existing metadata rows.

Each local range must pass `offset_tolerance`. The absolute difference
between the two medians must pass `spatial_tolerance` (maximum 0.025 mm).
Only then is P1's median the in-memory candidate. P2 validates P1 and never
changes its value. A failed spatial check retains both reports and creates
no candidate. Force limits, MCU stop handling and thermal checks remain active.
The unloaded raw-safety reference is fixed for the three pairs at each point,
then recaptured at the next point; saved sensor calibration is unchanged.

The owner's hot four-point run produced P1/P2 medians of 1.462007/1.464676 mm,
a difference of 0.002669 mm. Replaying those six offsets would therefore yield
a candidate of 1.462007 mm, not their mean. This is an offline replay, not a
new hardware qualification. Agreement at these two points does not establish
accuracy across the bed or resolve the distal-point bias recorded below.
Keep 1.065 mm until the candidate has been reviewed and a supervised
first-layer trial is explicitly arranged. Measurement does not stage/save an
offset; `AUTO_Z_OFFSET_ACCEPT` and then `SAVE_CONFIG` remain separate actions.

The owner requires a hot bed and nozzle for nozzle-contact and Z-offset
calibration: the calibration targets default to 50 C for the bed and 170 C
for the nozzle. The owner subsequently lowered the permitted bed minimum to
50 C and later selected it as the default; the nozzle minimum remains 170 C.
Before taking measurements, require
measured bed/nozzle temperatures to reach 50/170 C at
least once, then accumulate five seconds within the selected target band.
The owner's later requirement permits normal PID fluctuations: brief band
excursions pause accumulation instead of restarting it, and in-band readings
may cross below the nominal minima after the initial warm-up. Start contacts
in band and take the load-cell tare after warm-up. Check both temperatures
before and after contacts; wait up to 10 seconds at a checkpoint for an ordinary
out-of-band reading to recover. Do not reapply changed targets or retry contacts.
Changed targets, invalid sensor readings and recovery timeouts reject the
calibration. Higher targets must respect configured heater limits. The default
target band is +/-2 C and the initial heating timeout is 600 seconds. These
settings do not establish complete bed thermal equilibrium or measurement
accuracy. The previous heater targets are restored after each command unless
the printer has shut down.
Passive sensor diagnostics without nozzle movement can precede this stage.

No production configuration changes, service restarts, sensor commands,
heating, homing, or printer movements were performed during this analysis.

Earlier offline validation passed all 195 Python/native-C unit tests,
including 80 tests for this feature and the existing LCD suite. Coverage
includes the real contact estimator on a synthetic release trace, the shared
primary-probe constructor, paired coordinate/sign calculations, per-location
qualification, local medians, spatial rejection with retained reports, heater
and motion guards, sensor/fit failures, cleanup and confirmation-only staging.
Standard Klippy import/regression checks cannot run in this local interpreter:
greenlet is missing, as are the usual host dependencies and atmega2560.dict.
Two `.test` fixtures were added for primary-probe coexistence and pre-homing
rejection, but have not been run through that runner. No dependencies were
installed. Offline tests do not establish physical force or measurement accuracy.

### Owner-reported hardware qualification

Two completed two-point cycles on 2026-10-29 returned P1 candidates of
1.484710 and 1.482512 mm, a difference of 0.002198 mm. The latter ran with
LCD phase updates and no reported ACK or RAW_RANGE errors. Its P1/P2 medians
were 1.482512/1.479191 mm, local ranges 0.010604/0.007791 mm and spatial
difference 0.003321 mm. These observations support preliminary repeatability
and point agreement; they do not establish first-layer accuracy or adoption
of the candidate. The working 1.065 mm offset has not been reported replaced.
The owner supplied Screenshots/autoz_s1.jpg, autoz_s2.jpg and autoz_s3.jpg
for the README, showing warm-up, CR Touch reference and nozzle-contact phases.

The owner has enabled HX711 A-128 at 80 SPS on PA4/PC6, confirmed response to
an applied/removed load and saved `counts_per_gram: 114.88636`,
`reference_tare_counts: -436490` and `sensor_orientation: inverted`.
Warm diagnostics reported 840 valid samples, no saturation/errors, 82.7 SPS,
approximately 21.6 g peak-to-peak variation and -0.5 g after tare.
These observations establish acquisition and calibration response, not nozzle
contact accuracy or a mechanically qualified maximum force.

The first supervised tests did not produce an accepted offset. One failed
the thermal guard; another had only one unloaded sample in the original
0.3-second ascent fit window. At 0.1 mm/s that window covers only 0.03 mm.
Auto Z-offset now opts into up to five seconds of the already acquired
ascent, preserving the existing estimator and all minimum-sample/fit checks.
The regular load-cell probe retains its 0.3-second default. This correction
has offline coverage but still requires hardware qualification.

The development log shows heater targets returning to zero after failures,
consistent with restoration of their previous values. Before those failures
the targets were still 65/175 C. The bed readout has abrupt excursions,
including -18.1 C during a warm test, and negative excursions also appear in
older print logs before this feature was enabled. The cause of those readings
was identified by the owner as a damaged bed thermistor; replacement has not
been confirmed. Do not mask that fault by widening thermal tolerance. Thermal
errors now report the failing heater and measured/current/expected targets,
and cleanup separately reports the restored targets. Investigate the bed
temperature measurement before further nozzle contacts.

The inactive-mesh guard uses `bed_mesh.get_mesh() is None`; an empty status
matrix is `[[]]` and cannot be tested by list truthiness. A regression invokes
the real `BED_MESH_CLEAR` implementation with stored profiles retained.

A subsequent owner-run supervised test at targets 60/170 C passed with
`temperature_tolerance: 4` and `temperature_stable_time: 2`. The CR Touch
samples reported bed_z values 0.1375, 0.1375 and 0.135 mm. The paired offset
was 1.376125 mm, 0.311125 mm above the owner's proven saved value of 1.065 mm.
No offset was staged or saved by that test. The calculation matches the
standard PROBE_CALIBRATE coordinate convention; the source of the discrepancy
is unresolved. Five-pair repeatability and physical accuracy remain separate
qualification steps. Do not force agreement with an arbitrary correction.

Later individual tests returned 1.410007 and 1.425876 mm at targets 60/170 C,
then 1.441996 mm at 65/175 C. The corresponding mean CR Touch bed_z values
were 0.170000, 0.178333 and 0.176667 mm. With the unchanged 1.065 mm offset,
the formula implies fitted nozzle Z values of -0.175007, -0.182543 and
-0.200329 mm. Both references changed; these data do not establish a single
cause. The log confirms restoration to 55/150 C between tests, matching
HOME_N_PREHEAT. Repeated heating/cooling and the final target change confound
comparison. Source inspection found no accumulation of the computed offset:
each run uses the unchanged primary offset and a fresh nozzle tare.

For a controlled repeatability trial, first set both heater targets to the
chosen calibration targets outside the command and allow a thermal soak.
Then restoration returns to the same targets rather than 55/150 C. Keep
temperatures unchanged throughout the five-pair run. A single TEST_CONTACT
validates its own contact only; it does not test spread across separate runs.
CALIBRATE enforces the configured spread within its five pairs. A stable
result still needs independent accuracy validation before acceptance.

The owner then completed all five pairs at targets 60/170 C: 1.463246,
1.458672, 1.460940, 1.465170 and 1.459083 mm. The median was 1.460940 mm
and the range 0.006498 mm, passing the 0.025 mm spread limit. Recovery waits
completed without discarding contacts. This establishes within-run
repeatability, not accuracy: the median is 0.395940 mm above the saved
1.065 mm value. The owner initially described a manual PROBE_CALIBRATE
reference, then clarified that the final functional 1.065 value was obtained
with babystepping during a print after the paper calibration left the nozzle
too close. No new offset was saved. The owner rejected adding recurring
paper/gauge measurements to this workflow: qualify the algorithm from acquired
data instead. The print-tuned value remains a reference, not a numeric target
for fitting. Paper/gauge contact is above the surface by its thickness and
must not be equated directly with zero-gap nozzle contact.

### Contact-algorithm review and automatic diagnostics

The paired formula directly measures the separation:
`new_offset = cr.test_z - nozzle_contact_z`. This is equivalent to standard
PROBE_CALIBRATE's `old_offset + cr.bed_z - nozzle_contact_z`, because
`cr.bed_z = cr.test_z - old_offset`. The old offset cancels; it is not a
correction or fitting target. Offline tests cover changing the initial offset,
repeated runs
and common coordinate translations without changing the measured distance.

A reproducible defect was found in the contact estimator's local search:
its objective is not generally unimodal, so discarding half the interval
can discard the best contact. One deterministic noisy/drifting synthetic
trace returned Z0.493668 with the former search, while the same model has
a lower-error optimum at Z0.055142 (simulated contact Z0.075390). This proves
a solver defect; it does not establish the cause of the owner's physical bias.

The solver now checks every split interval of the original model. Within
each interval, it evaluates the interval boundaries and, when admissible,
the intersection of the loaded regression line and the unloaded mean.
Prefix sums keep total work linear in the sample count. The force model,
ADC acquisition, MCU trigger and five-pair workflow are unchanged. The shared
release validation now rejects nonnegative force slopes; compression must
be positive with the configured sensor orientation. Existing finite-data,
sample-support and geometric checks still apply. Independent matrix/grid
tests cover noisy curves, translated coordinates and quantized step plateaus.

Auto Z-offset enables automatic `Load cell ascent:` and `Load cell fit:` JSON
records in the Klippy log, followed by a paired-coordinate record. The trace
contains raw trigger Z, ascent start time, sample times and `[force_g, Z_mm]`
pairs. It logs all samples at the installed 80 SPS. Faster/longer traces are
decimated to at most 4096 points, preserving both endpoints and recording
the original sample count and stride. Fits still use the complete selected
data, not the decimated log. The regular load-cell probe does not enable
these detailed traces by default. This records the evidence for replay from
a normal TEST_CONTACT/CALIBRATE run, without additional operator measurements.

Physical confirmation of the corrected solver is pending. Do not assume that
it removes the observed 0.395940 mm difference, and do not subtract that
difference as a correction. The intended operator flow remains the initial
sensor setup, supervised contact, automatic five-pair calibration and explicit
accept/save after qualification; no manual paper reference is a runtime input.

After the solver correction, the owner completed five pairs at 60/170 C:
1.459930, 1.462246, 1.464628, 1.462630 and 1.464604 mm. The median was
1.462630 mm with range 0.004698 mm. The automatically recorded force/Z curves
showed raw trigger Z near -0.3375 mm, release contact near -0.2335 mm and
roughly 80--90 loaded samples followed by over 320 unloaded samples. For one
pair, CR Touch trigger Z1.229167 minus nozzle contact Z-0.233463 gives
1.462630 mm directly. Thus the solver correction did not remove the observed
discrepancy with 1.065 mm; its physical cause remains unresolved.

The owner reported a recent manual PROBE_CALIBRATE result of 1.460 mm,
0.002630 mm from the automatic median. They state that CR Touch and nozzle
were compared at the same physical location using PROBE_CALIBRATE's XY
repositioning. The owner considers this comparison validated and explicitly
does not want it repeated as a prerequisite to four-point work. This agreement
supports the estimator at that point; spatial agreement is the next objective.
The owner later clarified that the working 1.065 was tuned with babystepping
during printing; it is not an independently measured zero-gap contact offset.
Keep it as the functional baseline without adding a fixed numeric correction.

Four-point diagnostics are now implemented offline. Report within-point
spread and between-point differences before creating a global estimate. Paired
measurements already cancel local bed height; averaging disagreeing offsets
can hide tilt/twist or load-path sensitivity. The single bed load cell's
force response must be qualified at each proposed point; do not assume a
force threshold calibrated near its support is safe over the whole bed.

### Earlier same-point manual comparison

The owner explicitly requested this one-off comparison before expanding to
four points. Keep the functional `[bltouch] z_offset: 1.065` unchanged. This
is diagnostic qualification, not an added manual input to normal calibration.
Do not use PROBE_CALIBRATE/ACCEPT, AUTO_Z_OFFSET_ACCEPT or SAVE_CONFIG here.

Load matching updated `auto_z_offset.py` and `load_cell_probe.py` host files
in the development instance. No MCU changes are required. With a healthy
temperature sensor, a clean nozzle and no active/paused job, the operator
runs the following sequence; the development agent does not execute it:

```gcode
G28
BED_MESH_CLEAR
SET_HEATER_TEMPERATURE HEATER=heater_bed TARGET=60
SET_HEATER_TEMPERATURE HEATER=extruder TARGET=170
AUTO_Z_OFFSET_TEST_CONTACT BED_TEMP=60 NOZZLE_TEMP=170
```

Preset targets before the test so cleanup restores the same 60/170 C targets.
Do not run HOME_N_PREHEAT, home again, change G-code offsets or load a mesh
between the automatic and manual measurements. The module requires inactive
mesh and rejects axis-twist, skew and thermal-Z compensation modules, retaining
the same toolhead frame. The configured point must remain X25/Y28; the CR Touch
reaches it with toolhead X49/Y42.5 using offsets -24/-14.5. A successful test
ends with the nozzle at X25/Y28 and the configured Z clearance.

The console and `auto_z_offset.last_contact` now retain the force-trigger Z,
regression Z, their signed difference and temperatures around the tap. The
raw trigger is captured before the regression overwrites the returned result.
No candidate is staged or saved by TEST_CONTACT.

Without moving XY, start the existing manual helper:

```gcode
MANUAL_PROBE
```

Use TESTZ with small supervised moves to establish the usual paper/gauge
contact. Prefer a feeler gauge of known thickness. At the chosen position:

```gcode
M105
GET_POSITION
ABORT
```

Record the full output, the manual kinematic Z and the gauge thickness.
ABORT closes the helper without staging any calibration. The nozzle stays at
the last manual height; raise it to clearance afterwards using the usual
manual controls. Check measured temperatures, not only targets, against the
automatic report before treating the two measurements as thermally comparable.
Do not substitute G-code Z for kinematic/toolhead Z.

For a gauge of thickness h, use `manual_surface_z = manual_z - h` and
`manual_offset = cr_trigger_z - manual_surface_z`. Compare this with
`regression_z` and `offset` from the same automatic pair, and record
`regression_z - force_trigger_z` separately. If paper thickness is unknown,
retain the uncorrected reference and mark its unknown gap rather than inventing
an exact surface Z. This controls XY, coordinate frame and compensations while
checking whether the difference is associated with the contact estimate or
the previous calibration location. It does not alone prove a mechanical cause.

## Staged development setup

All examples below are instructions for a later explicitly enabled development
installation. No production printer configuration was changed. Retain the
existing `[bltouch]` and its saved offset. No NumPy installation is needed;
optional filter designs can still require SciPy. Do not add `[load_cell_probe]`.

### Stage 1: passive sensor diagnostics

Enable only the named sensor initially, using the working Marlin wiring:

```ini
[load_cell bed_force]
sensor_type: hx711
sclk_pin: PA4
dout_pin: PC6
gain: A-128
sample_rate: 80
```

This is the source-derived starting profile, not confirmed physical ADC
identity. The installed MCU must support HX71x acquisition. With the printer
idle, use the existing LOAD_CELL_DIAGNOSTIC LOAD_CELL=bed_force command to
check data and LOAD_CELL_CALIBRATE LOAD_CELL=bed_force for the standard guided
force calibration. Follow its TARE, CALIBRATE GRAMS=..., ACCEPT and explicit
SAVE_CONFIG workflow. These commands do not move the printer; use an appropriate
known local load. Check polarity, rate, errors, saturation and response before
proceeding. Never invent counts_per_gram or force limits from Marlin ADC counts.

### Stage 2: legacy supervised contact and five-pair measurement

After sensor qualification and force calibration, enable the new module with
explicit contact parameters. The force placeholders below must be replaced
with locally validated values; this is a template, not a ready-to-run config.

```ini
[auto_z_offset]
load_cell: bed_force
probe_point: 25, 28
speed: 0.5
lift_speed: 0.1
sample_retract_dist: 0.5
trigger_force: <measured trigger force in grams>
force_safety_limit: <validated maximum force in grams>
bed_temp: 60
nozzle_temp: 170
```

The suggested approach/release speeds are initial qualification settings,
not a claim of safe force or sufficient samples on the installed hardware.
The existing ascent estimator needs at least three samples in each region.
Auto Z-offset permits up to five seconds of ascent data; the regular load-cell
probe still defaults to 0.3 seconds. Qualify release speed using actual traces
and require an unloaded region within the selected window.
X25/Y28 is reachable in the supplied configuration, but the contact area's
physical suitability must be checked. A clean nozzle is required at 170 C.

Once enabled and supervised, the sequence is G28, BED_MESH_CLEAR,
AUTO_Z_OFFSET_TEST_CONTACT, then AUTO_Z_OFFSET_CALIBRATE. Both measurement
commands heat and move the printer. The contact test performs one paired
measurement without creating a save candidate. Only its success enables the
five-pair command. Existing homing and mesh commands retain their normal roles.
No command here is run automatically at startup or print start.

The five-pair command prints its median and spread without writing settings.
Inspect and physically qualify the result before AUTO_Z_OFFSET_ACCEPT.
Acceptance only
stages `[bltouch] z_offset`;
the runtime offset remains unchanged. SAVE_CONFIG then writes all pending
configuration values and restarts Klipper. The next print uses the saved
offset and generates a new KAMP mesh as usual. Repeatability alone does not
establish accuracy; first-layer qualification is described below.

The MVP requires inactive mesh compensation and rejects configured axis-twist,
skew and thermal-Z compensation modules. It does not alter KAMP or clear a mesh
silently. Previous heater targets are restored on success and error; contact
failures attempt only a Z clearance move when the printer remains connected
and operational. Shutdown never initiates recovery moves. Missing calibration,
invalid measurements, excessive spread or cleanup failure leave no acceptable
candidate and do not stage a new offset.

Rollback consists of disabling `[auto_z_offset]` in the development config;
the existing CR Touch implementation is unchanged. No saved offset is changed
until acceptance and SAVE_CONFIG. If a physically tested saved calibration
must be reverted later, restore the previously recorded 1.065 mm value using
the normal configuration workflow; do not change offsets during a print.

### Stage 3: optional four-point diagnostics (earlier qualification)

The following records the earlier four-point setup and remains available as
an optional diagnostic mode. For normal calibration use the two-point setup
at the top of this document instead.

The four coordinates come from `D_ROUTINE_AUTO_OFFSET` in the supplied
Marlin `AutoOffset.cpp`, in this order:

| Point | Physical bed XY | Nozzle XY during CR Touch measurement |
| --- | --- | --- |
| 1 | 28, 28 | 52, 42.5 |
| 2 | 110, 110 | 134, 124.5 |
| 3 | 180, 28 | 204, 42.5 |
| 4 | 28, 180 | 52, 194.5 |

The last column uses the installed offsets -24/-14.5, not Marlin's offsets.
Nozzle contact is always at the physical XY in the middle column.
Marlin helper also shifts Y between some local samples and conditionally
selects readings. This implementation takes all three pairs at the exact
configured point, with no local XY shifts or sample selection. Only the base
four-point layout is reused.
The five-point Marlin variant additionally includes (180,180) with a warning
about possible bed damage due to its distance from the sensor. That point is
excluded here. Reachability is confirmed from the configured travel limits;
load transfer and safe force sensitivity still require qualification at each
of the four included locations. A force reading calibrated near the cell
does not prove equivalent actual nozzle force at a remote point.

Before deployment, preserve the functional configuration. Replace the generic
paths below with the active configuration file and its backup destination:

```bash
cp -p /path/to/printer.cfg /path/to/printer.cfg.auto-z-baseline.bak
```

Keep any relevant included configuration files as well. Confirm that the
baseline's saved `[bltouch] z_offset` is 1.065. An earlier manual ACCEPT may
have staged 1.460 without saving it; discard that pending edit with a normal
idle RESTART without SAVE_CONFIG before proceeding. RESTART also discards
other pending settings and ends the current qualification session. Do not
restart with the nozzle left touching the bed. These are operator steps;
the agent has not backed up or changed the active configuration remotely.

Deploy the updated `auto_z_offset.py` host module together with the previously
updated `load_cell_probe.py` shared engine, using the manual development
instance. No additional firmware changes or Python dependencies are needed
for four-point orchestration. In `[auto_z_offset]`, replace `probe_point`
with the following block, retaining the already qualified force, motion,
thermal and sample-retract settings:

```ini
probe_points:
    28, 28
    110, 110
    180, 28
    28, 180
offset_tolerance: 0.025
spatial_tolerance: 0.025
```

This diagnostic mode requires four distinct finite points. It refuses simultaneous
`probe_point` and `probe_points`. The existing single-point configuration stays
unchanged until explicitly replaced; the first new point is X28/Y28, not the
previous X25/Y28. All nozzle/probe positions are checked before any heating or
motion, including when testing only one point.

After the printer is ready and the contact/thermal conditions are qualified,
the operator sequence is:

```gcode
G28
BED_MESH_CLEAR
SET_HEATER_TEMPERATURE HEATER=heater_bed TARGET=60
SET_HEATER_TEMPERATURE HEATER=extruder TARGET=170
```

Run each test separately under supervision and inspect the bed/nozzle response
and force/Z trace before moving to the next location:

```gcode
AUTO_Z_OFFSET_TEST_CONTACT POINT=1 BED_TEMP=60 NOZZLE_TEMP=170
AUTO_Z_OFFSET_TEST_CONTACT POINT=2 BED_TEMP=60 NOZZLE_TEMP=170
AUTO_Z_OFFSET_TEST_CONTACT POINT=3 BED_TEMP=60 NOZZLE_TEMP=170
AUTO_Z_OFFSET_TEST_CONTACT POINT=4 BED_TEMP=60 NOZZLE_TEMP=170
```

Each command performs one fresh CR Touch/nozzle pair through the existing MCU
trigger and release estimator. There are no force increases, automatic retries
or synthetic contacts. An error invalidates qualification; do not proceed if
a remote location fails to trigger or visibly deforms excessively. A successful
fit is necessary but does not certify actual mechanical force at that point.
The separate contact tests are optional diagnostics. Calibration does not
require their prior completion. `verified_points` and `contact_verified`
record successful tests for information only; they are not a manual gate.

Then request the twelve-pair measurement:

```gcode
AUTO_Z_OFFSET_CALIBRATE BED_TEMP=60 NOZZLE_TEMP=170
```

At each point, take three new pairs and calculate the local median; the
qualification tap is not reused. Preserve CR Touch's own samples per pair.
Reject the run immediately on invalid contact, local spread above
`offset_tolerance`, or existing force/temperature/sample/travel errors. No
outliers are dropped. Every successful pair reports and logs force-trigger Z,
regression Z and their signed difference, plus physical XY and temperatures.

After all points, compare max(local medians) minus min(local medians) with
`spatial_tolerance`, whose maximum is 0.025 mm. This is the location-bias
criterion documented by [Klipper](https://www.klipper3d.org/Probe_Calibrate.html#location-bias-check).
If exceeded, retain all four local reports with `state: rejected`,
`result.valid: false`, `result.z_offset: null` and no acceptable candidate.
The illustrative medians 1.462/1.438/1.471/1.445 span 0.033 mm and are rejected.
Investigate mounting geometry, twist, thermal drift and position-dependent
load transfer before repeating; do not average away the discrepancy.

Only if all checks pass, use the median of the four local medians as the
global in-memory candidate. For four values, this is the midpoint of the
middle two. The paired measurements, local summaries and diagnostics remain
in `result.points`, and the saved/runtime offset stays 1.065. No acceptance,
printing or saving occurs automatically.

### First four-point hardware qualification: RAW_RANGE

The owner tested each configured location. Point 1 at X28/Y28 passed:
CR Touch trigger Z1.192500, force-trigger Z-0.377500, regression Z-0.277213
and offset 1.469713 mm. The release correction was +0.100287 mm. Point 2
failed three attempts with RAW_RANGE; points 3 and 4 also failed before a
contact estimate was produced. No four-point candidate exists. Failures
correctly invalidate every location's qualification and preserve offset 1.065.

The owner's subsequent read-only query reported counts_per_gram 114.88636,
reference_tare_counts -436490 and the last local tare -451195, with inverted
orientation, zero ADC errors and zero overflows. The configured absolute
force limit remains 200 g and the filtered local trigger is 75 g.
The MCU bounds are therefore raw_min -459467 and raw_max -413513. The last
unloaded local tare already represents about +128.0 g relative to the saved
reference, leaving only about 72.0 g before the compression-side raw bound.
This margin is below the 75 g trigger. It establishes insufficient headroom
for the last location; local tares for points 2 and 3 were not recorded, so
their exact causes remain unproven.

A subsequent passive diagnostic reported 828 good samples at 82.6 SPS with
zero errors or saturation. With both heater targets zero, reported bed/nozzle
temperatures were 35.46/36.36 C. Current and reference tares both read -436490;
force was +86.6 g (recent window 83.6..89.5 g), leaving about 113.4 g of
compression margin relative to the unchanged 200 g bound. The inspected log
contains a Klippy restart with the saved calibration unchanged; its reported
XYZ are zero without a valid homed physical location for this comparison.
Thus the baseline displacement is present with the heaters off, but these
readings cannot isolate thermal drift from position, mechanics or handling.
Do not re-tare to hide the difference before a controlled comparison.
The owner subsequently confirmed that replacement is pending, with about one
month of delivery time, and explicitly wants development to continue meanwhile.

RAW_RANGE is checked on the MCU before filtering. It means a raw ADC sample
crossed the configured reference-centered safety band, not necessarily an ADC
rail or a known excessive physical nozzle force. The contact estimator is
not reached on these failed approaches. Local tare and LOAD_CELL_TARE do not
update reference_tare_counts or recenter that band. A displaced baseline can
consume it even when a local read appears near zero.

The first correction added an approach rejection when compression-side margin
was no greater than trigger_force, but kept the saved-reference raw band.
That rejection detected insufficient margin without resolving the different
references used for the filtered trigger and raw limit. The current Auto
Z-offset correction captures one unloaded reference per point, as described
below. The generic load_cell_probe retains its saved-reference policy.
Auto Z-offset logs `Load cell safety:` with physical toolhead position,
saved/point/local references, raw bounds, force scale/orientation and margins.
If an armed approach fails, `Load cell probe failure:` records the available
raw extrema and up to 257 timestamp/count samples, including endpoints.
Host bulk delivery may omit the exact sample that stopped the MCU. Diagnostic
errors cannot replace the original MCU error. Normal load-cell probing keeps
detailed logging disabled, but shares the new margin rejection.

No agent-side heating, motion, reference calibration or service restart was
performed. The updated point-reference policy requires a host reload before use.

### Earlier focus: hot four-point measurement

The owner explicitly requested continuing hot four-point work now, without
repeating the validated manual comparison or adding a cold diagnostic sequence
as a prerequisite. The bed thermistor replacement is pending. Preserve the
configured temperature tolerance/stabilization/recovery behavior and all
heater/MCU protections. The point-reference correction does not change them
or disable invalid-reading/target-change/timeout errors. The recorded abrupt
temperature readings remain a measurement limitation, not proof of stability.

For each hot point, measure CR Touch, retract/reposition the nozzle to the
configured clearance and drain moves. Read an unloaded average with the
existing load_cell.avg_counts() API, which checks sensor errors and saturation.
Use this average only as the temporary raw force-safety reference for that
point. It does not change saved reference_tare_counts, counts_per_gram or
probe offset, and does not emit a sensor-recalibration event.

Keep the raw safety reference fixed throughout all three pairs at this point
(five in legacy single-point mode). Each contact still tares the filtered
trigger locally. Those later tares cannot move the raw safety bound, so
cumulative preload within the point remains detectable. Require sufficient
remaining compression margin and reject a bound that would exceed the actual
ADC range. After retracting into unloaded clearance at a new point, capture
its reference. Discard the temporary reference on exit, including failure
and shutdown. Generic load-cell probing never opts into this override.

For the observed point-4 baseline -451195 and scale 114.88636 counts/g,
the unchanged 200 g limit gives raw bounds -474172..-428218. The configured
75 g trigger and raw guard now use compatible force origins. Raw checks
remain on the MCU before filtering; RAW_RANGE can still reject an actual
excursion from this fixed point baseline. Do not suppress it if it recurs.

Deploy matching auto_z_offset.py and load_cell_probe.py host modules and reload
the idle manual development instance. No new configuration or MCU build is
required. Retain the existing four coordinates, 75/200 g settings, and thermal
settings. Follow the hot Stage 3 sequence: supervised TEST_CONTACT POINT=1..4,
then CALIBRATE for the twelve new pairs, with no intervening homing/restart.
Each qualification test uses its own clearance reference; calibration captures
fresh references for its actual three-pair sequences. Report all local medians
and spatial rejection exactly as before, preserving functional offset 1.065.

LOAD_CELL_DIAGNOSTIC's added reference/raw/noise statistics remain available as
optional diagnostics, not another required operator sequence. No new manual
PROBE_CALIBRATE, cold characterization or known-weight calibration is required
to test this correction. Offline tests cover the actual owner baseline,
fixed-reference behavior across repetitions, ADC-headroom rejection and
cleanup.

The owner subsequently completed hot TEST_CONTACT at all four points without
RAW_RANGE. Logs confirm roughly 199--202 g of compression headroom against
the unchanged 75 g trigger. Each test supplied only one pair:

| Physical XY | Offset (mm) | Regression minus force-trigger Z (mm) |
| --- | --- | --- |
| 28,28 | 1.463895 | +0.100272 |
| 110,110 | 1.467845 | +0.128822 |
| 180,28 | 1.484505 | +0.197162 |
| 28,180 | 1.508083 | +0.250250 |

The provisional between-point range is 0.044188 mm, greater than 0.025 mm.
This is not yet the three-pair local-median result. Release fits have 84/329,
106/306, 161/251 and 207/205 samples below/above contact, respectively, so
none lacks the required sample coverage. Loaded-line slope magnitudes fall
from 1053 to 710, 399 and 323 g/mm. These traces establish different force/Z
responses over the sequential tests; position-dependent compliance or force
transfer is plausible, but location, drift and temperature are not independently
separated. Do not attribute the offset range to one cause or average it away.
The next operator step is hot CALIBRATE for three fresh pairs per point,
without intervening homing/restart. Retain spatial rejection and offset 1.065.

At the owner's subsequent 50 C bed target and +/-6 C tolerance, all four
supervised contacts again passed without RAW_RANGE: offsets 1.474197,
1.469315, 1.491321 and 1.506807 mm. Their provisional range was 0.037492 mm;
these were single-pair checks, not four local medians. The following CALIBRATE
completed point 1 with offsets 1.472026, 1.474248 and 1.471427 mm, median
1.472026 mm and local range 0.002821 mm. It stopped at point 2 before nozzle
contact because temperature recovery exceeded the configured 35 seconds.
The final reported bed reading was 58.37 C against target 50 C and allowed
band 44..56 C. No point-2/3/4 calibration medians or global candidate exist.

Cleanup restored previous targets of 0/0 C. Those were the values at command
entry; this does not indicate that motion turned the heaters off. Preset the
chosen targets outside measurement commands to retain them across tests and
calibration. A longer bounded temperature_recovery_time can allow a slow
return to the same band; it does not widen the band or guarantee recovery.
At the time of this historical run, thermal timeouts invalidated all location
qualifications and required fresh TEST_CONTACT results before CALIBRATE.
That manual prerequisite has since been removed; errors still invalidate
candidates and every calibration acquires fresh paired measurements.

### 2026-10-29: complete hot run after thermistor cable replacement

The owner replaced the bed thermistor cable and reports that bed motion no
longer disrupts its reading. A complete CALIBRATE at bed/nozzle targets
50/170 C and +/-6 C tolerance finished all twelve pairs without thermal
interruption or RAW_RANGE. This supersedes treating the cable fault as an
unresolved prerequisite. It does not establish full thermal equilibrium.

| Point | Physical XY | Median offset (mm) | Local range (mm) |
| --- | --- | --- | --- |
| 1 | 28,28 | 1.462007 | 0.017410 |
| 2 | 110,110 | 1.464676 | 0.008254 |
| 3 | 180,28 | 1.502586 | 0.022240 |
| 4 | 28,180 | 1.506984 | 0.011899 |

All local ranges pass 0.025 mm; point 3 is close to the limit. The spatial
range is 0.044978 mm, so the existing rejection is correct. There is no global
candidate or saved offset change. Preserve functional offset 1.065 and the
spatial limit rather than averaging away the difference.

All twelve logged fits have substantial support on both sides of contact:
72--237 loaded and 177--342 unloaded samples. Loaded slope magnitudes are
approximately 899--970, 567--611, 360--373 and 267--284 g/mm at points 1--4.
Mean release corrections are approximately 0.099, 0.148, 0.224 and 0.273 mm.
This establishes different measured force/Z responses over the ordered run.
Position-dependent compliance or force transfer is plausible, but probe/gantry
geometry, signal drift and elapsed time have not been independently separated.
The free-air baseline also shifts between pairs; the existing fitted constant
accounts for a nonzero baseline, so its value alone is not a contact error.

Offline replay with the existing LCBestFit exactly reproduced the logged
contacts. Diagnostic crops of the recorded data, centered on the original fit,
gave these spatial ranges: 0.041462 mm with only 0.10 mm of free-air data;
0.038137 mm with 0.08/0.10 mm below/above contact; and 0.037341 mm with
0.12/0.15 mm below/above contact. These post-hoc crops assess sensitivity,
not independent accuracy or a proposed production algorithm. All still fail
the spatial criterion. No estimator, force, thermal or configuration change
was made from these diagnostics.

A reversed-order hot comparison would help distinguish location dependence
from time/order drift using the same four points and paired method, without
another manual calibration or a cold sequence. No reversed-order mode or
hardware operation has been implemented/executed in this analysis.

### Stage 4: supervised first-layer qualification

Review the calibration report before enabling a small first-layer trial with
the usual KAMP mesh and a deliberate initial clearance. The functional 1.065
baseline and its backup remain available. The trial must preserve normal
print-start/KAMP behavior and use one explicit temporary offset model;
ensure the print macros do not reset that temporary adjustment. This stage
has no automatic command in this change, and no test print has been run.

Increasing a Klipper probe z_offset lowers the nozzle for the same commanded
Z after homing. Consequently, replacing 1.065 with 1.460 would move the nozzle
about 0.395 mm closer before any trial clearance. Do not treat the geometric
candidate as an already qualified first-layer setting. While retaining the
old probe offset, the relative temporary G-code adjustment for a trial would
be `old_offset - candidate + clearance`, with positive clearance raising the
nozzle. Choose the initial clearance after reviewing the measurements and
the existing printing baseline, rather than hard-coding a correction here.
This is a coordinate relationship, not an instruction to start an unattended
print with a negative adjustment.

During the supervised trial, record the candidate, initial adjustment,
additional babystepping, temperatures and resulting first layer. Compare the
effective print offset with the geometric result. A repeatable discrepancy
needs an explanation before persistence; do not automatically subtract the
historical 0.397630 mm difference. Only a qualified result may be staged with
AUTO_Z_OFFSET_ACCEPT and written with SAVE_CONFIG. No extra paper measurement
is required as a recurring feature input.

## Confirmed source findings

These are facts about this checkout and the supplied Marlin sources, not
confirmation of the printer's active configuration or ADC hardware.

| Component | Available functionality | Integration consequence |
| --- | --- | --- |
| `klippy/extras/load_cell.py` | Named load cells, tare, calibration, diagnostics, timestamped samples and collectors | Reuse one sensor instance and its calibration. |
| `klippy/extras/hx71x.py`, `src/sensor_hx71x.c` | HX711/HX717 acquisition and analog-trigger attachment | An HX711 driver already exists; its name does not identify the installed ADC. |
| `klippy/extras/cs1237.py`, `src/sensor_cs1237.c` | CS1237 acquisition, configurable sample rate and analog-trigger attachment | This fork also supports CS1237; verify the working hardware configuration before selecting a backend. |
| `klippy/extras/trigger_analog.py`, `src/trigger_analog.c` | MCU filtering, raw range checks, sensor monitoring and `trsync` triggering | Stop probing through the MCU path, not a Python force-polling loop. |
| `klippy/extras/load_cell_probe.py` | Tare, probing moves, retract sampling and a piecewise force/Z fit on ascent | Reuse and qualify these components instead of recreating ADC reads or step pulses. |
| `klippy/extras/bltouch.py` | Primary probe, standard commands and virtual Z endstop | Retain this integration for CR Touch. |
| `klippy/extras/probe.py`, `manual_probe.py` | Probe sessions, explicit coordinate semantics and calibration arithmetic | Use their conventions to avoid sign and coordinate-frame errors. |

`LoadCellPrinterProbe` and `PrinterBLTouch` both register the object `probe`.
Their constructors also register standard probe commands and the `probe`
virtual-endstop pin provider. Adding both configuration sections is therefore
not a coexistence solution. Runtime replacement of `printer.objects['probe']`
would also leave other modules holding the original probe object.

The shared contact engine no longer requires NumPy: its tare uses the existing
load_cell averaging helper and tap diagnostics use ordinary lists. LCBestFit
and its numerical algorithm are reused. Optional filter designs can require
SciPy; no packages are installed by any measurement command.

`load_cell.LoadCell` subscribes to the ADC at `klippy:ready` and retains a
force-tracking client. Stopping a new calibration collector alone will not
stop that existing acquisition. Measure Pi/MCU/serial overhead, especially at
higher sample rates, before promising acquisition only during calibration.
Any lifecycle change needs separate tests and an explicit design.

### Marlin reference

The owner supplied the customized Marlin reference checkout. Paths here are
relative to that checkout:

`Marlin/src/e3v3se/AutoOffset.cpp`

Its adjacent `AutoOffset.h`, `Configuration.h` and `Configuration_adv.h`
provide the following references:

- The class named HX711 reads 24-bit values, assumes 80 samples/second, and
  uses clock PA4 and data PC6. These are source wiring references; ADC identity
  and compatibility with the active Klipper configuration remain to be checked.
- `NOZZLE_TO_PROBE_OFFSET` is `{-24.25, -15, 0}` in that configuration.
  The configured reference is X25/Y28 when `PLATFORM_OFFSET` is enabled,
  as it is in the inspected source. Other branches use X31/Y35 plus origins.
  Confirm the usable physical contact point with the current bed surface.
- The enabled D routine measures four locations, including locations away
  from the load-cell reference. It uses a median/MAD-based estimator with
  residual and distance weights.
- Contact acquisition uses incremental moves, filtered ADC history and a
  geometric estimate of the onset of contact. The header defines raw-count
  trigger thresholds and fixed thermal/automatic offsets.

Useful ideas to retain are paired probe/nozzle measurements, repeat sampling,
bounded searches and robust aggregation. Adapt the measurement concept, not
the Marlin scheduler, interrupt handling, raw step control or EEPROM writes.

### What is already known about acquisition

The owner clarified that the working Marlin implementation is the wiring and
initialization reference. No additional pin measurements, ADC photo or chip
datasheet are prerequisites for preparing the passive acquisition stage.

Inspection of HX711::init/getVal shows GPIO clock PA4 and input PC6, clock low
while waiting for data-ready, a 24-bit signed read and one final clock pulse.
This is a dedicated two-wire clock/data interface implemented with GPIO; it
does not use the STM32 hardware SPI peripheral or require an SPI bus selection.
The source assumes 80 SPS. On an HX711, the final single pulse corresponds to
channel A at gain 128, which Klipper's existing HX711 backend supports.

Use that evidence for the initial diagnostic profile, not a new ADC driver:

```ini
# Proposed diagnostic configuration; not installed or hardware-validated.
[load_cell bed_force]
sensor_type: hx711
sclk_pin: PA4
dout_pin: PC6
gain: A-128
sample_rate: 80
```

Do not add guessed counts_per_gram or reference_tare_counts. This profile
enables force-sensor acquisition, not a second primary probe or nozzle motion.
Its proposed first test is LOAD_CELL_DIAGNOSTIC LOAD_CELL=bed_force, only in an
explicitly enabled development configuration; it has not been executed.

Physical ADC identity is still not proven: AutoOffset.h itself describes the
member named HX711 as acquisition for "CS123X or HX711". The HX711 profile is a
source-derived starting hypothesis, not a claim that all such ADCs are protocol
equivalent. Qualification must check plausible changing counts, errors and
saturation, actual conversion rate, load response and polarity. A sensible
observed rate alone does not establish that an ADC is being decoded correctly.
If this profile fails those checks, investigate framing/backend compatibility
before changing thresholds or attempting contact. Identify the physical ADC
only if the working reference and observed data do not resolve that issue.

The data not obtainable from source are the actual zero/noise/drift on this
printer, counts_per_gram under a known local load, polarity and a validated
trigger/force limit. Those require measurements, not more configuration files.
They gate moving/contact tests, not preparation of the passive diagnostic.

Some reference behavior should not become the acceptance policy:

- Sustained overpressure in `checkTrigger()` returns the same success value
  as contact. In the new design an overload must invalidate the measurement.
- `probeTimes()` and `Multiple_Hight_At()` can return a value despite failed
  repeatability. The new routine must report failure when its quality gates
  are not met, with bounded retries and no forced result.
- `probeByPress()` returns success unconditionally; a no-contact zero sentinel
  must not be converted into a plausible offset by subsequent arithmetic.
- Fixed thermal corrections and spatial weights require measurements to
  justify them. They are not transferable calibration constants.

## Owner-supplied configuration and KAMP workflow

The owner supplied the main printer configuration on 2026-10-23 and confirmed
that KAMP generates a new adaptive mesh for each print. The following values
are confirmed in that supplied file. The readable local includes were later
inspected as described below; live printer state has not been inspected.

| Item | Supplied configuration |
| --- | --- |
| CR Touch pins | Sensor `^PC14`, control `PC13` |
| CR Touch XY offsets | X -24.0 mm, Y -14.5 mm; use these rather than the Marlin values |
| Saved CR Touch Z offset | 1.065 mm in the `SAVE_CONFIG` block |
| Probe sampling | Speed 5 mm/s, 3 samples, lift speed 10 mm/s |
| CR Touch mode | `stow_on_each_sample: False`, `probe_with_touch_mode: True` |
| Z homing | `probe:z_virtual_endstop`, speeds 7/1 mm/s, retract 2.5 mm |
| Z travel | -3 to 250 mm, rotation distance 8 mm, 16 microsteps |
| XY travel | X -6 to 230 mm, Y -14 to 225 mm |
| Safe Z home | Toolhead X139/Y127, Z hop 10 mm at 7 mm/s |
| Base mesh bounds | X10/Y10 through X206/Y210.5, nominal 5x5 bicubic |
| Includes | `fluidd.cfg`, `macros.cfg`, `KAMP_Settings.cfg` |

There is no load-cell section, calibration, axis-twist compensation or thermal
Z adjustment in the main file or the six active includes now inspected. The
configuration does not identify the ADC. PA4/PC6 remain the Marlin wiring
reference. No live diagnostic or force-calibration data has been supplied.

The saved `bed_mesh default` is a 3x3 profile covering approximately
X81.382..100.542 and Y42.892..62.732. A saved profile does not establish the
currently active mesh. The owner describes per-print adaptive meshing, so no
saved mesh should serve as the nozzle-contact reference or be assumed to cover
the load-cell location.

Using the supplied XY offsets, a candidate physical point P=X25/Y28 requires
toolhead X49/Y42.5 for CR Touch and X25/Y28 for nozzle contact. Both are within
configured XY travel. This verifies coordinate reach only; bed-surface coverage,
support sensitivity and suitability for physical contact remain unverified.
The safe-home coordinates put CR Touch at approximately X115/Y112.5; that is
a separate physical location from the candidate load-cell point.

KAMP changes the XY area sampled by the mesh. The proposed nozzle-contact
calibration point stays independent of the current object's bounding box.
During initial experiments require no active mesh compensation, or explicitly
suspend it with state handling before motion. Retain the existing 1.065 mm
probe offset until a candidate has been validated. Do not reset it to zero,
lower Z limits, or reuse mesh values as a substitute for physical contact.

The agreed standalone workflow is:

```text
user invokes calibration while no print or pause is active
  -> confirm homing and nozzle preparation
  -> heat bed to a target of at least 50 C and wait for thermal stability
  -> heat nozzle to a target of at least 170 C and wait for thermal stability
  -> tare the unloaded sensor at the calibration temperature
  -> local offset measurement with mesh compensation inactive
  -> validate measurements and report the candidate
  -> stage a successful result for [bltouch] z_offset
  -> user saves with SAVE_CONFIG; Klipper restarts
  -> subsequent prints home and generate a fresh KAMP mesh as usual
```

These commands are implemented but not installed on the printer. Measurements
do not change or stage the probe offset. AUTO_Z_OFFSET_ACCEPT may stage only a
validated candidate using Klipper's configuration API. Standard SAVE_CONFIG
writes the active configuration file and requests a host restart; it is not
a runtime babystep. The command must explain this behavior before the user
saves. The existing runtime probe offset remains effective until restart.
Failed calibration must preserve both the old offset and unrelated pending
configuration changes. Do not restore an old mesh after changing the offset;
the next print generates a fresh one under the saved coordinate model.

Here, idle means no active/paused print, conflicting calibration or queued
movement. Do not require `idle_timeout.state == 'Idle'`: that state follows
the configured 600-second timeout and OFF macro, and normal calibration
movements themselves can change idle-timeout state to Printing. Use actual
job/pause state, G-code serialization and motion draining to enforce the
standalone operation. Entry into idle does not trigger calibration.

### Local configuration audit

The owner made the printer configuration directory available for read-only
analysis. The main `printer.cfg`, `macros.cfg`
and `KAMP_Settings.cfg` were read on 2026-10-23. Use these files for further
analysis instead of requesting their contents again. The mount is a read-only
analysis source for this project; no files there were modified.

The actual start macro is `PRINT_START` in `macros.cfg`. Its current sequence is:

```text
CLEAR_PAUSE
M140 S<bed target>
M104 S150
M190 S<bed target>
G28
BED_MESH_CALIBRATE ADAPTIVE=1 ADAPTIVE_MARGIN=5
SMART_PARK
M109 S<final nozzle target>
ADAPTIVE_LINE_PURGE
```

`M104 S150` sets a target without waiting. The explicit nozzle-temperature wait
is after the mesh. The separate calibration must therefore define its own
validated temperature/settling condition rather than inherit PRINT_START's
preheating. A bed-temperature wait alone also does not establish a thermal
soak of the complete bed assembly. No auto-offset insertion into this macro
is planned or authorized by the agreed workflow.

No active `SET_GCODE_OFFSET`, `Z_OFFSET_APPLY_PROBE`, `PROBE_CALIBRATE`,
load-cell section, calibration values or auto-Z command was found in the main
file or inspected active includes. `PRINT_START` does not explicitly clear an
existing mesh before homing. These are static source findings, not live state
or an audit of the slicer's start G-code.

Both `macros.cfg` and the later part of `printer.cfg` define `[gcode_macro M190]`.
Klipper's `ConfigFileReader` applies includes and subsequent options in order.
The main-file implementation supersedes the
one in `macros.cfg` and waits within +/-2 C of the requested bed temperature.
This is an include-order observation, not a request to change the macro.

`KAMP_Settings.cfg` actively includes:

- `./KAMP/Adaptive_Meshing.cfg`
- `./KAMP/Line_Purge.cfg`
- `./KAMP/Smart_Park.cfg`

Its mesh margin and fuzz amount are both zero, docking is disabled, and smart
park height is 10 mm. `PRINT_START` calls the locally defined
`ADAPTIVE_LINE_PURGE`, not KAMP's `LINE_PURGE`; settings for those two purge
macros are separate. The standalone calibration must verify its contact area
is clear of any purge material left by previous prints.

The config-directory `fluidd.cfg` and `KAMP` symlink entries could not be read
through that mount. The owner subsequently provided separate accessible
mounts, resolving the content-access limitation:

- Fluidd's `client.cfg` supplies the client
  configuration, including virtual SD, pause/resume and client macros.
- KAMP's `Configuration/Adaptive_Meshing.cfg`
- KAMP's `Configuration/Line_Purge.cfg`
- KAMP's `Configuration/Smart_Park.cfg`

All these files have now been inspected; do not request their contents again.
The template KAMP_Settings.cfg in the KAMP repository is not the active
settings file. Use the enabled includes and variables in the active printer
configuration directory's `KAMP_Settings.cfg`.

The installed `Adaptive_Meshing.cfg` wraps `BED_MESH_CALIBRATE`, derives bounds
from object polygons,
uses `_KAMP_Settings.mesh_margin`, and calls `_BED_MESH_CALIBRATE` with explicit
bounds without forwarding `ADAPTIVE=1` or `ADAPTIVE_MARGIN=5`. Thus the margin
of 5 in PRINT_START does not set the KAMP margin in this inspected macro; the
active settings value of zero controls it. This is now a confirmed source
finding. No change to meshing is part of the automatic-offset work.

Offline verification used this checkout's ConfigFileReader with the six
include paths explicitly mapped to the owner-provided readable mounts.
It parsed 54 configuration sections and verified M190 precedence, the KAMP
override and active margin, the selected purge macro, and absence of load-cell,
axis-twist and thermal-Z sections. This was an in-memory parsing check, not
printer startup or firmware/configuration validation against hardware.
ADC identity, calibrated force response and contact conditions remain open.

## Implemented architecture

- `klippy/extras/load_cell_probe.py`: LoadCellTapHelper accepts the existing
  LoadCell and assembles its parameter/filter helpers, MCU_trigger_analog,
  LoadCellProbingMove and TappingMove. LoadCellPrinterProbe uses the same helper
  while retaining its original primary-probe registration. Collector cleanup,
  tare-range checks and rejection of non-finite/degenerate fits are shared.
- `klippy/extras/auto_z_offset.py`: references `[bltouch]` and the named sensor,
  enforces readiness/geometry/thermal conditions, runs one supervised pair or
  calibration at the configured points, reports local medians and spatial
  agreement, and stages an explicitly accepted candidate. Two-point mode
  takes three pairs per point and uses P1's median with P2 as validation.
  Optional four-point mode retains its median-of-local-medians policy;
  legacy single-point mode retains five pairs.
- Existing acquisition, trigger_analog.py, probe.py and C implementations are
  reused without modification. Only CR Touch owns `probe:z_virtual_endstop`.
  A secondary analog trigger is associated with the existing Z steppers for
  its own contact moves, without replacing the CR Touch virtual endstop.

The measurement engine requires no new MCU protocol: it reuses the existing
ADC, analog-trigger and filter commands. The optional partial LCD view uses
the separately authorized TJC coordinate canvas extension described above;
it does not change force acquisition or stopping behavior.

## Measurement and coordinate model

For each configured point P, require overlapping nozzle/CR Touch reach and
qualified load-cell response. With configured probe XY offsets `(ox, oy)`,
place the nozzle at `(Px - ox, Py - oy)` for the CR Touch measurement, and at
`(Px, Py)` for nozzle contact. Raise Z before each XY move. Stow CR Touch for
nozzle contact. Compare the same physical point, not the same toolhead XY.

All heights must be in the same Klipper frame. Define:

```text
zt = toolhead Z at CR Touch trigger
zn = estimated toolhead Z at unloaded nozzle contact
new CR Touch z_offset = zt - zn
```

This normally gives a positive Klipper probe offset, unlike the negative
Marlin offset convention. Do not negate an arbitrary Marlin result or simply
replace a runtime G-code offset.

When consuming a Klipper `ProbeResult` rather than raw trigger coordinates:

```text
new_offset = old_offset + crtouch_result.bed_z - nozzle_result.bed_z
```

The nozzle reference must have zero probe offset for this expression. A
synthetic test with `old_offset=2.0`, raw `zt=2.1`, and `zn=0.1` must produce
`new_offset=2.0`, not zero. Test nonzero G-code origins separately. Define
handling of active bed mesh, axis-twist compensation, skew and thermal Z
adjustment explicitly; transformations must not contaminate the comparison
or silently remain changed after an error. Initial hardware qualification
should use a known configuration without unaccounted compensation.

### Acquisition sequence

1. Require an idle, homed printer, a calibrated healthy load cell, valid probe
   geometry, a clean nozzle and an empty contact region. Preserve the existing
   offset. Initial commands should require homing explicitly rather than
   silently starting a homing or cleaning sequence.
2. Establish a documented bed/nozzle thermal condition using targets of at
   least 50/170 C and the warm-up/recovery policy above. Assess drift, noise,
   saturation,
   missing data and available ADC range with no contact. Tare only after
   heating, retracting, settling and confirming an unloaded baseline.
3. Measure CR Touch at P. Retract, reposition the nozzle to P, settle and tare.
4. Perform a bounded, slow, MCU-triggered nozzle approach. Retract immediately
   and collect force/Z samples through release into free air. Force and travel
   limits must invalidate failed measurements, never synthesize a contact.
5. Evaluate the existing ascent fit as a starting estimator of unloaded
   contact. Validate sample coverage on both sides, force slope, residuals,
   plausible contact location and recovery to baseline. Fitting release data
   can reduce threshold/deflection bias, but hysteresis or adhesion can bias
   it; it is not assumed more accurate without recorded comparisons.
6. Repeat the CR Touch/nozzle pair at P until three valid paired measurements
   have been obtained in two/four-point mode (five for legacy single-point mode).
   The implementation does not add a second bracketing CR Touch
   measurement per pair or discard failed pairs to force a successful result.
   Reject the run if a measurement fails or the thermal condition is lost.
7. Check local offsets against validity and repeatability limits. Report their
   median, minimum, maximum and range, then repeat at the other configured
   locations. Compare the local medians before forming a global candidate;
   reject excessive spatial disagreement while retaining the completed
   reports. No MAD weighting, outlier selection or unconditional averaging
   is used. Agreeing local medians allow a candidate: P1's median in two-point
   mode, or the median of local medians in optional four-point diagnostics.
8. Return the candidate, raw measurements and quality diagnostics. Initially
   do not apply or persist it. Later application must use one documented
   offset model, invalidate stale results, and avoid double application via
   `SET_GCODE_OFFSET` plus a modified probe offset.

Repeated samples estimate repeatability, not absolute accuracy. A consistent
filament blob, geometric bias or bed deflection can produce a repeatable but
incorrect result. Qualify the result against an independent gap/first-layer
check under controlled conditions before enabling persistent calibration.

## Reliability requirements and unproven improvements

- **Local force calibration:** an under-bed load cell measures reaction through
  the bed support. Counts per gram at one XY location need not represent nozzle
  force elsewhere. Calibrate/validate at the intended contact location using
  a mechanically appropriate known load. Do not assume a whole-bed weight
  calibration provides a universal nozzle-force limit.
- **Sampling and latency:** confirm actual ADC identity, measured rate and
  conversion delay. At 80 SPS and 1 mm/s, motion between samples is 0.0125 mm;
  at 640 SPS it is 0.0015625 mm. These are sample spacings, not accuracy claims.
  ADC/filter delay and mechanical stopping add uncertainty; qualify speed and
  peak force experimentally. Do not select 640 SPS unless the ADC supports it.
- **MCU protection:** retain raw force-range checks, sensor timeout/error
  handling and `trsync` stop during armed probing. Host quality checks supplement
  these mechanisms. Safety thresholds must come from hardware characterization,
  not generic defaults or ADC counts copied from Marlin.
- **Thermal control:** use bed/nozzle targets of at least 50/170 C with the
  warm-up/recovery policy above for normal PID fluctuations. Keep the nozzle
  clean and use conditions suitable for the bed surface and filament.
  Fixed `+0.05 mm` compensation is not a model. Start
  without a fitted thermal correction; consider one only after repeatable
  temperature-dependent measurements. Initial cleaning should be manual;
  automatic bed wiping needs a separate surface-specific design.
- **Spatial bias:** after one-point qualification, compare a few additional
  reachable locations at validated forces. A location-dependent result is a
  diagnostic for mechanics, axis twist, geometry or force sensitivity. Do not
  average a systematic gradient into a global offset or immediately build a
  second mesh from it. Distant points may be unsuitable for this bed sensor.
- **Failure behavior:** retain the old offset; clean up collectors/sessions on
  every exit. Retract only when the motion state permits it, and never issue
  recovery moves after MCU shutdown. No unbounded retry, silent success,
  automatic `SAVE_CONFIG` or restart. Calibration must exclude simultaneous
  print/manual motion and an active manual-probe session.

## Phases and completion criteria

1. **Source and configuration audit (completed).** Record this design,
   active configuration, sensor identity, wiring reference, mechanical contact
   point and firmware capabilities. The owner approved the simplified design.
   No printer operations were performed.
2. **Sensor qualification without commanded motion.** In an explicitly enabled
   development configuration, use existing load-cell diagnostics and record
   timestamped counts at rest and with controlled manual loading. Verify
   polarity, saturation margin, actual rate, calibration and noise/drift under
   representative heater/fan/motor states. These measurements are planned,
   not yet executed. `[load_cell]` can coexist with `[bltouch]`; do not enable
   `[load_cell_probe]` next to it.
3. **Offline estimator and integration tests.** Build deterministic fixtures
   for offset signs, XY mapping, nonzero origins, drift, outliers, noisy/no
   contact traces, insufficient fit samples, sensor errors and cleanup. Compare
   contact estimators on recorded traces. Test that only one primary probe and
   one ADC instance exist, that no persistence occurs on failure, and that
   print/manual motion cannot interleave with calibration.
4. **Supervised one-point measurement.** Only after the hardware checks, enable
   bounded MCU-triggered contacts at the local point and conservative validated
   settings. Record repeatability, contact fits, peak force and
   speed sensitivity. Initially report candidates without changing the offset.
   Establish numerical acceptance limits from those results and the intended
   layer height; no precision target is claimed as achieved.
5. **Standalone calibration and persistence.** Validate a candidate with an
   independent physical check, implement staging of a valid [bltouch] z_offset
   through the standard configuration API, and document SAVE_CONFIG and
   rollback. Validate reboot/re-home, temperature repeatability, aborts,
   preservation of unrelated pending settings, and exclusion of printing or
   paused jobs. SAVE_CONFIG remains an explicit user action because it restarts
   Klipper. Do not add startup, idle-event, virtual-SD or PRINT_START hooks.
   Advanced thermal compensation remains optional later research.

## Information required before hardware qualification

- No configuration-file access is currently missing for the inspected include
  graph. The main file, macros, active KAMP settings, Fluidd client and three
  active KAMP includes are available through the mounts listed above. Do not
  request these files again. Live hardware/measurement data remains pending.
- No further ADC wiring information is needed for the passive stage: use the
  source-derived PA4/PC6, HX711 A-128/80-SPS diagnostic hypothesis above. Record
  actual raw data, sensor errors, rate, noise/drift and load response before
  approving that profile for contact probing. ADC identification is a fallback
  for unresolved compatibility, not a request the owner must satisfy up front.
- Existing `counts_per_gram`, `reference_tare_counts`, orientation and the
  position/method used for calibration, if these have already been measured.
- Bed surface, nozzle/hotend modifications, usual material and thermal
  conditions; whether the nozzle can be kept clean without an automatic wipe.
- Confirmation of a usable contact point near the sensor, with nozzle and
  CR Touch both able to reach the same physical point within configured travel.

No extra wiring measurement is requested merely to rediscover Marlin's PA4/PC6
reference. A working active configuration can confirm it. Physical uncertainty
must be resolved only where it affects ADC compatibility, geometry or force.

## References

- [Local load-cell guide](Load_Cell.md), [configuration reference](Config_Reference.md#load_cell)
  and [probe calibration conventions](Probe_Calibrate.md).
- [Official Klipper load-cell documentation](https://www.klipper3d.org/Load_Cell.html)
  for diagnostics and calibration concepts. The source files in this fork
  determine its actual available interfaces; do not assume a current upstream
  page describes every local probe/backend implementation.
- [KAMP project documentation](https://github.com/kyleisah/Klipper-Adaptive-Meshing-Purging)
  for adaptive mesh bounds and the include structure. The owner's installed
  macros remain the authority for the actual print-start workflow.
- [Upstream KAMP adaptive-meshing macro](https://github.com/kyleisah/Klipper-Adaptive-Meshing-Purging/blob/main/Configuration/Adaptive_Meshing.cfg)
  as a reference; the parameter-forwarding observation above has also been
  confirmed directly in the owner's mounted KAMP source.
- The owner-supplied Marlin files listed above, inspected on 2026-10-23.
