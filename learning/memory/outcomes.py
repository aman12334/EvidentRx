"""
Historical investigation outcome memory.

Records the verified outcomes of investigations — what actually happened
after a case was closed, escalated, or remediated. This ground-truth
memory is the primary training signal for the adaptive calibration layer.

Outcome types
─────────────
  TRUE_POSITIVE     — alert was correct; violation/anomaly confirmed
  FALSE_POSITIVE    — alert was incorrect; no violation found
  TRUE_NEGATIVE     — no alert; confirmed no violation
  FALSE_NEGATIVE    — no alert; but violation discovered later
  ESCALATED         — case escalated to higher authority
  REMEDIATED        — case closed with remediation completed
  INCONCLUSIVE      — case could not be definitively resolved
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from learning.memory.store import MemoryStore, MemoryType, get_memory_store

log = logging.getLogger("evidentrx.learning.memory.outcomes")


class OutcomeVerdict(str, Enum):
    TRUE_POSITIVE  = "true_positive"
    FALSE_POSITIVE = "false_positive"
    TRUE_NEGATIVE  = "true_negative"
    FALSE_NEGATIVE = "false_negative"
    ESCALATED      = "escalated"
    REMEDIATED     = "remediated"
    INCONCLUSIVE   = "inconclusive"


@dataclass
class InvestigationOutcome:
    """
    The verified outcome of a completed investigation.

    Recorded by ClinicalOps / Compliance team after case resolution.
    Provides ground-truth labels for model evaluation and calibration.
    """
    entry_id:              str
    tenant_id:             str
    case_id:               str
    verdict:               OutcomeVerdict
    rule_code:             str | None
    initial_severity:      str            # system-assigned severity at alert time
    actual_severity:       str | None  # verified severity (may differ)
    system_confidence:     float | None
    resolution_hours:      float | None
    estimated_exposure:    float | None  # dollar amount if applicable
    actual_exposure:       float | None
    recorded_by:           str
    recorded_at:           datetime
    investigation_quality: int | None   = None  # 1–5 analyst rating
    notes:                 str             = ""


class OutcomeMemory:
    """
    Typed interface over MemoryStore for investigation outcomes.

    Provides domain-specific query methods used by:
    - RiskCalibrationEngine (FP/FN signal aggregation)
    - EvaluationHarness (ground-truth labelling)
    - AnalystBehaviorAnalyzer (resolution latency)
    - Intelligence analytics reports
    """

    def __init__(self, store: MemoryStore | None = None) -> None:
        self._store = store or get_memory_store()

    # ── Write ──────────────────────────────────────────────────────────────────

    async def record_outcome(
        self,
        tenant_id:          str,
        case_id:            str,
        verdict:            OutcomeVerdict,
        recorded_by:        str,
        rule_code:          str | None   = None,
        initial_severity:   str             = "unknown",
        actual_severity:    str | None   = None,
        system_confidence:  float | None = None,
        resolution_hours:   float | None = None,
        estimated_exposure: float | None = None,
        actual_exposure:    float | None = None,
        investigation_quality: int | None = None,
        notes:              str             = "",
    ) -> InvestigationOutcome:
        """Record the verified outcome of an investigation."""
        content = {
            "case_id":              case_id,
            "verdict":              verdict.value,
            "rule_code":            rule_code,
            "initial_severity":     initial_severity,
            "actual_severity":      actual_severity,
            "system_confidence":    system_confidence,
            "resolution_hours":     resolution_hours,
            "estimated_exposure":   estimated_exposure,
            "actual_exposure":      actual_exposure,
            "investigation_quality":investigation_quality,
            "notes":                notes,
        }
        entry = await self._store.record(
            tenant_id   = tenant_id,
            memory_type = MemoryType.INVESTIGATION_OUTCOME,
            content     = content,
            recorded_by = recorded_by,
            artifact_id = case_id,
            tags        = [verdict.value] + ([rule_code] if rule_code else []),
        )
        log.info(
            "OutcomeMemory: %s outcome for case %s [%s]",
            verdict.value, case_id[:8], tenant_id,
        )
        return InvestigationOutcome(
            entry_id              = entry.entry_id,
            tenant_id             = tenant_id,
            case_id               = case_id,
            verdict               = verdict,
            rule_code             = rule_code,
            initial_severity      = initial_severity,
            actual_severity       = actual_severity,
            system_confidence     = system_confidence,
            resolution_hours      = resolution_hours,
            estimated_exposure    = estimated_exposure,
            actual_exposure       = actual_exposure,
            recorded_by           = recorded_by,
            recorded_at           = entry.recorded_at,
            investigation_quality = investigation_quality,
            notes                 = notes,
        )

    # ── Read ───────────────────────────────────────────────────────────────────

    def get_outcomes(
        self,
        tenant_id:  str,
        verdict:    OutcomeVerdict | None = None,
        rule_code:  str | None            = None,
        since:      datetime | None       = None,
        limit:      int                      = 500,
    ) -> list[InvestigationOutcome]:
        """Query outcomes with optional filters."""
        tags = []
        if verdict:
            tags.append(verdict.value)
        if rule_code:
            tags.append(rule_code)

        entries = self._store.query(
            tenant_id   = tenant_id,
            memory_type = MemoryType.INVESTIGATION_OUTCOME,
            since       = since,
            tags        = tags or None,
            limit       = limit,
        )
        return [_entry_to_outcome(e) for e in entries]

    def get_outcome_for_case(
        self,
        tenant_id: str,
        case_id:   str,
    ) -> InvestigationOutcome | None:
        """Return the most recent outcome record for a specific case."""
        entries = self._store.query(
            tenant_id   = tenant_id,
            memory_type = MemoryType.INVESTIGATION_OUTCOME,
            artifact_id = case_id,
            limit       = 1,
        )
        return _entry_to_outcome(entries[0]) if entries else None

    # ── Aggregation ────────────────────────────────────────────────────────────

    def verdict_summary(
        self,
        tenant_id:  str,
        rule_code:  str | None      = None,
        since:      datetime | None = None,
    ) -> dict[str, Any]:
        """
        Aggregate verdict counts and rates for a tenant (optionally per rule).

        Returns:
          total, true_positive_rate, false_positive_rate, false_negative_rate,
          precision, recall
        """
        outcomes = self.get_outcomes(
            tenant_id = tenant_id,
            rule_code = rule_code,
            since     = since,
            limit     = 10_000,
        )
        if not outcomes:
            return {"total": 0, "rule_code": rule_code}

        counts: dict[str, int] = {}
        for o in outcomes:
            counts[o.verdict.value] = counts.get(o.verdict.value, 0) + 1

        total = len(outcomes)
        tp    = counts.get(OutcomeVerdict.TRUE_POSITIVE.value, 0)
        fp    = counts.get(OutcomeVerdict.FALSE_POSITIVE.value, 0)
        fn    = counts.get(OutcomeVerdict.FALSE_NEGATIVE.value, 0)

        precision = tp / (tp + fp) if (tp + fp) > 0 else None
        recall    = tp / (tp + fn) if (tp + fn) > 0 else None

        return {
            "rule_code":           rule_code,
            "total":               total,
            "verdict_counts":      counts,
            "true_positive_rate":  round(tp / total, 4) if total else 0.0,
            "false_positive_rate": round(fp / total, 4) if total else 0.0,
            "false_negative_rate": round(fn / total, 4) if total else 0.0,
            "precision":           round(precision, 4) if precision is not None else None,
            "recall":              round(recall, 4) if recall is not None else None,
        }

    def calibration_error_summary(
        self,
        tenant_id: str,
        since:     datetime | None = None,
    ) -> dict[str, Any]:
        """
        Compute average confidence calibration error from outcome records.

        For outcomes where system_confidence is recorded, measures how
        often the system's confidence matched the actual verdict:
          - TP/TN: confidence should be high (>0.5 = correct)
          - FP/FN: confidence was high but verdict was negative
        """
        outcomes = self.get_outcomes(tenant_id=tenant_id, since=since, limit=10_000)
        calibrated = [
            o for o in outcomes
            if o.system_confidence is not None
            and o.verdict in (
                OutcomeVerdict.TRUE_POSITIVE,
                OutcomeVerdict.FALSE_POSITIVE,
                OutcomeVerdict.TRUE_NEGATIVE,
                OutcomeVerdict.FALSE_NEGATIVE,
            )
        ]
        if not calibrated:
            return {"sample_count": 0}

        errors = []
        for o in calibrated:
            # True label: 1.0 if TP/FP (alert fired), 0.0 if TN/FN
            true_label = 1.0 if o.verdict in (
                OutcomeVerdict.TRUE_POSITIVE, OutcomeVerdict.TRUE_NEGATIVE
            ) else 0.0
            errors.append(abs(o.system_confidence - true_label))

        return {
            "sample_count": len(errors),
            "avg_calibration_error": round(statistics.mean(errors), 4),
            "median_calibration_error": round(statistics.median(errors), 4),
        }

    def resolution_latency_summary(
        self,
        tenant_id: str,
        since:     datetime | None = None,
    ) -> dict[str, Any]:
        """Average and p90 resolution time from outcome records."""
        outcomes = self.get_outcomes(tenant_id=tenant_id, since=since, limit=10_000)
        hours = sorted(
            o.resolution_hours for o in outcomes if o.resolution_hours is not None
        )
        if not hours:
            return {"sample_count": 0}

        n   = len(hours)
        avg = statistics.mean(hours)
        med = statistics.median(hours)
        p90_idx = min(int(n * 0.90), n - 1)
        p90 = hours[p90_idx]

        return {
            "sample_count":           n,
            "avg_resolution_hours":   round(avg, 2),
            "median_resolution_hours":round(med, 2),
            "p90_resolution_hours":   round(p90, 2),
        }


# ── Conversion helper ──────────────────────────────────────────────────────────

def _entry_to_outcome(e: Any) -> InvestigationOutcome:
    c = e.content
    return InvestigationOutcome(
        entry_id              = e.entry_id,
        tenant_id             = e.tenant_id,
        case_id               = e.artifact_id or c.get("case_id", ""),
        verdict               = OutcomeVerdict(c.get("verdict", "inconclusive")),
        rule_code             = c.get("rule_code"),
        initial_severity      = c.get("initial_severity", "unknown"),
        actual_severity       = c.get("actual_severity"),
        system_confidence     = c.get("system_confidence"),
        resolution_hours      = c.get("resolution_hours"),
        estimated_exposure    = c.get("estimated_exposure"),
        actual_exposure       = c.get("actual_exposure"),
        recorded_by           = e.recorded_by,
        recorded_at           = e.recorded_at,
        investigation_quality = c.get("investigation_quality"),
        notes                 = c.get("notes", ""),
    )
