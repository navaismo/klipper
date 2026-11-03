# Post-acquisition contact-model qualification; no motion or sensor access
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import math


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if not (math.isnan(value) or math.isinf(value)) else None
    except (TypeError, ValueError, OverflowError):
        return None


def qualify_contact(report, primary_z, tolerance):
    """Check model agreement, never certify physical contact or choose a Z."""
    result = {'status': 'rejected', 'eligible_for_candidate': False,
        'physical_contact_verified': False, 'physical_uncertainty_mm': None,
        'reasons': [], 'maximum_span_mm': _number(tolerance),
        'branch_disagreement_mm': None, 'contact_model_span_mm': None,
        'interpretation': 'branch_agreement_is_not_physical_validation'}
    reasons = result['reasons']
    if result['maximum_span_mm'] is None or result['maximum_span_mm'] <= 0.:
        reasons.append('invalid_tolerance')
        return result
    if not isinstance(report, dict):
        reasons.append('missing_contact_report')
        return result
    quality = report.get('quality')
    if not isinstance(quality, dict):
        quality = {}
    if (quality.get('references_complete') is not True
            or quality.get('frame_state') != 'consistent'):
        reasons.append('unverified_coordinate_references')
    if (quality.get('valid_time_bounds') is not True
            or any(quality.get(k) != 0 for k in (
                'bad_timestamps', 'invalid_samples', 'expired_history_samples',
                'lookup_errors'))):
        reasons.append('invalid_timing_or_coordinates')
    motion = report.get('motion')
    if report.get('error') or not isinstance(motion, dict) \
            or motion.get('sensor_errors'):
        reasons.append('acquisition_error')
    branches = [report.get(key) for key in ('descent', 'ascent')]
    if (report.get('estimates_comparable') is not True
            or any(not isinstance(b, dict) or b.get('status') != 'identified'
                   for b in branches)):
        reasons.append('unsupported_contact_branches')
        return result
    descent, ascent = [_number(b.get('z')) for b in branches]
    primary = _number(primary_z)
    if any(z is None for z in (descent, ascent, primary)):
        reasons.append('non_finite_contact_estimate')
        return result
    lo, hi = min(descent, ascent, primary), max(descent, ascent, primary)
    result.update({'descent_fit_z': descent, 'ascent_fit_z': ascent,
        'primary_fit_z': primary, 'contact_model_span_mm': [lo, hi],
        'branch_disagreement_mm': abs(descent - ascent),
        'primary_ascent_difference_mm': abs(primary - ascent),
        'model_span_mm': hi - lo})
    # This is a deterministic agreement budget, not a confidence interval.
    # Reuse the configured paired-offset tolerance, never the saved offset.
    if hi - lo > result['maximum_span_mm']:
        reasons.append('contact_branches_disagree')
    threshold = report.get('descent_threshold')
    if not isinstance(threshold, dict) \
            or threshold.get('status') != 'identified':
        reasons.append('no_sustained_descent_force_rise')
    if not reasons:
        result.update({'status': 'internally_consistent',
                       'eligible_for_candidate': True})
    return result
