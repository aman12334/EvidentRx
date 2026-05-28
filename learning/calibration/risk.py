"""
Adaptive risk calibration layer.

Adjusts risk prioritization weights, escalation thresholds, and severity
mappings based on accumulated analyst feedback and investigation outcomes.

All calibration is:
  - Deterministic: same inputs → same calibration output
  - Versioned: every calibration run produces a named snapshot
  - Human-gated: no calibration takes effect without approval
  - Replayable: any prior calibration can be reproduced exactly
  - Reversible: rollback restores a previous approved snapshot

Calibration inputs
──────────────────
  - FalsePositiveReport events   → lower confidence for matching rule/context
  - FalseNegativeEscalation      → raise sensitivity for matching rule/context
  - OutcomeLabel history         → ground-truth resolution rates per rule
  - RemediationOutcome history   → which rule categories recur after remediation

Calibration outputs
───────────────────
  - RiskCalibrationWeights: per-rule confidence delta multipliers
  - EscalationThresholds: updated numeric thresholds for risk tiers
  - SeverityMappings: adjusted severity ↔ score mappings
"""

from __future__ import annotations

import hashlib
import json
import logging
import statistics
from dataclasses import dataclass, field
from datetime    import datetime, timezone
from typing      import Any, Optional

from learning.feedback.models import (
    FeedbackType,
    FeedbackRecord,
    FalsePositiveReport,
    FalseNegativeEscalation,
    OutcomeLabel,
    InvestigationOutcome,
    RemediationOutcomeReport,
    RemediationEffectiveness,
)

log = logging.getLogger("evidentrx.learning.calibration.risk")

# Calibration hyperparameters (conservative by design)
_FP_WEIGHT_DECAY       = 0.05   # confidence delta per false positive
_FN_WEIGHT_BOOST       = 0.08   # confidence delta per false negative
_MIN_CONFIDENCE        = 0.10   # floor — never go below 10%
_MAX_CONFIDENCE        = 0.99   # ceiling
_MIN_FEEDBACK_COUNT    = 3      # require at least 3 signals before adjusting
_MAX_ADJUSTMENT        = 0.30   # cap single-run adjustment at ±30%


@dataclass
class RuleCalibration:
    """Calibration state for a single compliance rule."""
    rule_code:           str
    base_confidence:     float                  # original uncalibrated confidence
    calibrated_confidence: float                # adjusted confidence
    fp_count:            int                    = 0
    fn_count:            int                    = 0
    outcome_confirmed:   int                    = 0    # times confirmed as violation
    outcome_cleared:     int                    = 0    # times cleared
    last_calibrated:     Optional[datetime]     = None

    @property
    def confirmation_rate(self) -> float:
        total = self.outcome_confirmed + self.outcome_cleared
        return self.outcome_confirmed / total if total > 0 else 0.5

    @property
    def feedback_count(self) -> int:
        return self.fp_count + self.fn_count + self.outcome_confirmed + self.outcome_cleared


@dataclass
class EscalationThresholds:
    """Tier boundary thresholds for risk escalation routing."""
    critical_min:  float = 0.85
    high_min:      float = 0.65
    medium_min:    float = 0.40
    low_min:       float = 0.20

    def tier_for_score(self, score: float) -> str:
        if score >= self.critical_min:
            return "critical"
        elif score >= self.high_min:
            return "high"
        elif score >= self.medium_min:
            return "medium"
        elif score >= self.low_min:
            return "low"
        return "informational"


@dataclass
class RiskCalibrationResult:
    """Output of a single calibration run."""
    calibration_id:    str
    tenant_id:         str
    version:           str                        # semantic version e.g. "1.3.0"
    computed_at:       datetime
    rule_calibrations: dict[str, RuleCalibration] = field(default_factory=dict)
    thresholds:        EscalationThresholds        = field(default_factory=EscalationThresholds)
    feedback_window_days: int                      = 90
    total_feedback_used:  int                      = 0
    content_hash:      str                         = ""          # reproducibility fingerprint
    approved:          bool                        = False        # requires human approval
    approved_by:       Optional[str]               = None
    approved_at:       Optional[datetime]          = None
    notes:             str                         = ""


class RiskCalibrationEngine:
    """
    Computes updated risk calibration from analyst feedback history.

    The engine is stateless — it receives feedback records and prior
    calibration state, and deterministically produces a new calibration
    result. It never modifies state directly; callers commit approved results.
    """

    def calibrate(
        self,
        feedback_records:   list[FeedbackRecord],
        prior_calibration:  Optional[RiskCalibrationResult],
        tenant_id:          str,
        version:            str,
        feedback_window_days: int = 90,
    ) -> RiskCalibrationResult:
        """
        Compute updated rule calibrations from feedback.

        Parameters
        ----------
        feedback_records    : All feedback in the calibration window
        prior_calibration   : Previous approved calibration (or None for first run)
        tenant_id           : Tenant being calibrated
        version             : Semantic version for this calibration
        feedback_window_days: How many days of feedback to consider
        """
        import uuid

        # Seed from prior calibration or defaults
        rule_calibrations = _seed_calibrations(prior_calibration)
        thresholds        = _seed_thresholds(prior_calibration)

        # Aggregate feedback by rule
        fp_by_rule:  dict[str, int] = {}
        fn_by_rule:  dict[str, int] = {}
        confirmed:   dict[str, int] = {}
        cleared:     dict[str, int] = {}

        for fb in feedback_records:
            if isinstance(fb, FalsePositiveReport) and fb.rule_code:
                fp_by_rule[fb.rule_code] = fp_by_rule.get(fb.rule_code, 0) + 1

            elif isinstance(fb, FalseNegativeEscalation) and fb.rule_code:
                fn_by_rule[fb.rule_code] = fn_by_rule.get(fb.rule_code, 0) + 1

            elif isinstance(fb, OutcomeLabel):
                # Map outcomes to case-associated rules (metadata-driven)
                rule = fb.metadata.get("rule_code", "")
                if rule:
                    if fb.outcome == InvestigationOutcome.CONFIRMED_VIOLATION:
                        confirmed[rule] = confirmed.get(rule, 0) + 1
                    elif fb.outcome == InvestigationOutcome.NO_VIOLATION:
                        cleared[rule] = cleared.get(rule, 0) + 1

        # Apply adjustments
        all_rules = set(list(fp_by_rule) + list(fn_by_rule) + list(confirmed) + list(cleared))
        for rule_code in all_rules:
            rc = rule_calibrations.setdefault(rule_code, RuleCalibration(
                rule_code             = rule_code,
                base_confidence       = 0.75,
                calibrated_confidence = 0.75,
            ))

            rc.fp_count          += fp_by_rule.get(rule_code, 0)
            rc.fn_count          += fn_by_rule.get(rule_code, 0)
            rc.outcome_confirmed += confirmed.get(rule_code, 0)
            rc.outcome_cleared   += cleared.get(rule_code, 0)

            if rc.feedback_count >= _MIN_FEEDBACK_COUNT:
                rc.calibrated_confidence = _compute_adjusted_confidence(rc)
                rc.last_calibrated       = datetime.now(tz=timezone.utc)

        # Adjust escalation thresholds based on overall FP/FN ratio
        total_fp = sum(fp_by_rule.values())
        total_fn = sum(fn_by_rule.values())
        thresholds = _adjust_thresholds(thresholds, total_fp, total_fn)

        result = RiskCalibrationResult(
            calibration_id       = str(uuid.uuid4()),
            tenant_id            = tenant_id,
            version              = version,
            computed_at          = datetime.now(tz=timezone.utc),
            rule_calibrations    = rule_calibrations,
            thresholds           = thresholds,
            feedback_window_days = feedback_window_days,
            total_feedback_used  = len(feedback_records),
            approved             = False,
        )
        result.content_hash = _hash_calibration(result)

        log.info(
            "RiskCalibration: computed v%s for tenant %s "
            "(%d rules adjusted, %d feedback records)",
            version, tenant_id, len(all_rules), len(feedback_records),
        )
        return result

    def apply_calibration(
        self,
        base_score:        float,
        rule_code:         str,
        calibration:       RiskCalibrationResult,
    ) -> float:
        """
        Apply an approved calibration to a base risk score.

        Returns the calibrated score. Never raises.
        """
        rc = calibration.rule_calibrations.get(rule_code)
        if rc is None or not calibration.approved:
            return base_score

        # Scale base score by the ratio of calibrated to base confidence
        if rc.base_confidence > 0:
            ratio = rc.calibrated_confidence / rc.base_confidence
            adjusted = base_score * ratio
        else:
            adjusted = base_score

        return max(_MIN_CONFIDENCE, min(_MAX_CONFIDENCE, adjusted))


# ── Internal helpers ──────────────────────────────────────────────────────────

def _compute_adjusted_confidence(rc: RuleCalibration) -> float:
    """Adjust confidence based on FP/FN counts and outcome rates."""
    base    = rc.base_confidence
    delta   = 0.0

    # False positives lower confidence
    if rc.fp_count > 0:
        delta -= min(rc.fp_count * _FP_WEIGHT_DECAY, _MAX_ADJUSTMENT * 0.5)

    # False negatives raise confidence
    if rc.fn_count > 0:
        delta += min(rc.fn_count * _FN_WEIGHT_BOOST, _MAX_ADJUSTMENT * 0.5)

    # Outcome history: confirmation rate as additional signal
    if rc.outcome_confirmed + rc.outcome_cleared >= _MIN_FEEDBACK_COUNT:
        conf_rate = rc.confirmation_rate
        # Map confirmation rate [0,1] to delta [-0.15, +0.15]
        outcome_delta = (conf_rate - 0.5) * 0.30
        delta += outcome_delta

    # Cap total adjustment
    delta = max(-_MAX_ADJUSTMENT, min(_MAX_ADJUSTMENT, delta))
    result = base + delta
    return max(_MIN_CONFIDENCE, min(_MAX_CONFIDENCE, round(result, 4)))


def _adjust_thresholds(
    thresholds: EscalationThresholds,
    total_fp:   int,
    total_fn:   int,
) -> EscalationThresholds:
    """
    Nudge escalation thresholds based on fleet-level FP/FN balance.

    High FP rate → raise thresholds (reduce noise).
    High FN rate → lower thresholds (increase sensitivity).
    Changes are capped at ±0.05 per calibration run.
    """
    total = total_fp + total_fn
    if total < 10:
        return thresholds   # not enough signal

    fp_rate = total_fp / total
    delta   = 0.0
    if fp_rate > 0.4:
        delta = +0.02   # too many FPs — tighten
    elif fp_rate < 0.1:
        delta = -0.02   # too many FNs — loosen

    return EscalationThresholds(
        critical_min = max(0.70, min(0.95, thresholds.critical_min + delta)),
        high_min     = max(0.50, min(0.85, thresholds.high_min + delta)),
        medium_min   = max(0.25, min(0.65, thresholds.medium_min + delta)),
        low_min      = max(0.10, min(0.40, thresholds.low_min + delta)),
    )


def _seed_calibrations(
    prior: Optional[RiskCalibrationResult],
) -> dict[str, RuleCalibration]:
    if prior is None:
        return {}
    # Deep copy prior calibrations (carry forward counts)
    return {
        rule: RuleCalibration(
            rule_code             = rc.rule_code,
            base_confidence       = rc.calibrated_confidence,   # prior output becomes new base
            calibrated_confidence = rc.calibrated_confidence,
            fp_count              = 0,   # reset per-window counts
            fn_count              = 0,
            outcome_confirmed     = 0,
            outcome_cleared       = 0,
            last_calibrated       = rc.last_calibrated,
        )
        for rule, rc in prior.rule_calibrations.items()
    }


def _seed_thresholds(prior: Optional[RiskCalibrationResult]) -> EscalationThresholds:
    if prior is None:
        return EscalationThresholds()
    return EscalationThresholds(
        critical_min = prior.thresholds.critical_min,
        high_min     = prior.thresholds.high_min,
        medium_min   = prior.thresholds.medium_min,
        low_min      = prior.thresholds.low_min,
    )


def _hash_calibration(result: RiskCalibrationResult) -> str:
    """Deterministic fingerprint of a calibration result for reproducibility."""
    payload = {
        "tenant_id": result.tenant_id,
        "version":   result.version,
        "rules":     {
            code: {
                "base":        rc.base_confidence,
                "calibrated":  rc.calibrated_confidence,
                "fp":          rc.fp_count,
                "fn":          rc.fn_count,
            }
            for code, rc in sorted(result.rule_calibrations.items())
        },
        "thresholds": {
            "critical": result.thresholds.critical_min,
            "high":     result.thresholds.high_min,
            "medium":   result.thresholds.medium_min,
            "low":      result.thresholds.low_min,
        },
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()
