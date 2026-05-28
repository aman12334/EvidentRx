"""
Analyst correction memory.

Records analyst overrides of system-generated outputs (risk scores,
outcomes, recommendations) with temporal retention. Corrections inform
the calibration layer but never automatically alter production behaviour —
each correction is a signal, not a command.

Correction types
────────────────
  SCORE_OVERRIDE     — analyst changes a risk/confidence score
  OUTCOME_CORRECTION — analyst marks the actual case outcome
  LABEL_CORRECTION   — analyst corrects a classification label
  THRESHOLD_FEEDBACK — analyst flags a threshold as too high/low
  RECOMMENDATION_OVERRIDE — analyst overrides a recommendation action
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from learning.memory.store import MemoryStore, MemoryType, get_memory_store

log = logging.getLogger("evidentrx.learning.memory.corrections")


class CorrectionType(str, Enum):
    SCORE_OVERRIDE          = "score_override"
    OUTCOME_CORRECTION      = "outcome_correction"
    LABEL_CORRECTION        = "label_correction"
    THRESHOLD_FEEDBACK      = "threshold_feedback"
    RECOMMENDATION_OVERRIDE = "recommendation_override"


@dataclass
class CorrectionRecord:
    """
    A structured analyst correction.

    Produced by CorrectionMemory.record_correction() and stored via the
    base MemoryStore. Corrections are query-accessible for the calibration
    and analytics layers.
    """
    entry_id:          str
    tenant_id:         str
    analyst_id:        str
    correction_type:   CorrectionType
    artifact_id:       str            # case_id, finding_id, or recommendation_id
    artifact_type:     str            # "case" | "finding" | "recommendation"
    system_value:      Any            # what the system produced
    corrected_value:   Any            # what the analyst says it should be
    rule_code:         str | None  # rule that produced the original output
    reasoning:         str            # analyst's stated reason
    recorded_at:       datetime
    confidence:        float | None = None   # analyst's confidence in correction (0–1)
    supersedes_id:     str | None  = None    # prior correction this replaces


class CorrectionMemory:
    """
    Typed interface over MemoryStore for analyst corrections.

    Provides domain-specific methods for recording and querying the
    various correction types. All writes are forwarded to the underlying
    MemoryStore for persistence.
    """

    def __init__(self, store: MemoryStore | None = None) -> None:
        self._store = store or get_memory_store()

    # ── Write ──────────────────────────────────────────────────────────────────

    async def record_correction(
        self,
        tenant_id:       str,
        analyst_id:      str,
        correction_type: CorrectionType,
        artifact_id:     str,
        artifact_type:   str,
        system_value:    Any,
        corrected_value: Any,
        reasoning:       str,
        rule_code:       str | None   = None,
        confidence:      float | None = None,
        supersedes_id:   str | None   = None,
    ) -> CorrectionRecord:
        """Record a new analyst correction."""
        content = {
            "analyst_id":      analyst_id,
            "correction_type": correction_type.value,
            "artifact_type":   artifact_type,
            "system_value":    system_value,
            "corrected_value": corrected_value,
            "reasoning":       reasoning,
            "rule_code":       rule_code,
            "confidence":      confidence,
        }

        entry = await self._store.record(
            tenant_id     = tenant_id,
            memory_type   = MemoryType.ANALYST_CORRECTION,
            content       = content,
            recorded_by   = analyst_id,
            artifact_id   = artifact_id,
            supersedes_id = supersedes_id,
            tags          = [correction_type.value] + ([rule_code] if rule_code else []),
        )

        log.info(
            "CorrectionMemory: %s correction by %s on %s [%s]",
            correction_type.value, analyst_id, artifact_id[:8], tenant_id,
        )

        return CorrectionRecord(
            entry_id        = entry.entry_id,
            tenant_id       = tenant_id,
            analyst_id      = analyst_id,
            correction_type = correction_type,
            artifact_id     = artifact_id,
            artifact_type   = artifact_type,
            system_value    = system_value,
            corrected_value = corrected_value,
            rule_code       = rule_code,
            reasoning       = reasoning,
            recorded_at     = entry.recorded_at,
            confidence      = confidence,
            supersedes_id   = supersedes_id,
        )

    async def record_score_override(
        self,
        tenant_id:      str,
        analyst_id:     str,
        finding_id:     str,
        rule_code:      str,
        system_score:   float,
        analyst_score:  float,
        reasoning:      str,
        confidence:     float | None = None,
    ) -> CorrectionRecord:
        """Convenience method for score/confidence overrides."""
        return await self.record_correction(
            tenant_id       = tenant_id,
            analyst_id      = analyst_id,
            correction_type = CorrectionType.SCORE_OVERRIDE,
            artifact_id     = finding_id,
            artifact_type   = "finding",
            system_value    = system_score,
            corrected_value = analyst_score,
            reasoning       = reasoning,
            rule_code       = rule_code,
            confidence      = confidence,
        )

    async def record_outcome_correction(
        self,
        tenant_id:          str,
        analyst_id:         str,
        case_id:            str,
        system_outcome:     str,
        actual_outcome:     str,
        reasoning:          str,
    ) -> CorrectionRecord:
        """Record the verified actual outcome of an investigation."""
        return await self.record_correction(
            tenant_id       = tenant_id,
            analyst_id      = analyst_id,
            correction_type = CorrectionType.OUTCOME_CORRECTION,
            artifact_id     = case_id,
            artifact_type   = "case",
            system_value    = system_outcome,
            corrected_value = actual_outcome,
            reasoning       = reasoning,
        )

    # ── Read ───────────────────────────────────────────────────────────────────

    def get_corrections(
        self,
        tenant_id:       str,
        correction_type: CorrectionType | None = None,
        rule_code:       str | None             = None,
        since:           datetime | None        = None,
        limit:           int                       = 200,
    ) -> list[CorrectionRecord]:
        """Query corrections with optional filters."""
        tags = []
        if correction_type:
            tags.append(correction_type.value)
        if rule_code:
            tags.append(rule_code)

        entries = self._store.query(
            tenant_id   = tenant_id,
            memory_type = MemoryType.ANALYST_CORRECTION,
            since       = since,
            tags        = tags or None,
            limit       = limit,
        )
        return [_entry_to_record(e) for e in entries]

    def get_corrections_for_artifact(
        self,
        tenant_id:   str,
        artifact_id: str,
    ) -> list[CorrectionRecord]:
        """Get all corrections for a specific case/finding/recommendation."""
        entries = self._store.query(
            tenant_id   = tenant_id,
            memory_type = MemoryType.ANALYST_CORRECTION,
            artifact_id = artifact_id,
            limit       = 100,
        )
        return [_entry_to_record(e) for e in entries]

    def score_override_summary(
        self,
        tenant_id: str,
        rule_code: str,
        since:     datetime | None = None,
    ) -> dict[str, Any]:
        """
        Summarise score overrides for a rule code.

        Returns average delta (corrected – system), direction counts, and
        sample count. Used by the calibration layer.
        """
        corrections = self.get_corrections(
            tenant_id       = tenant_id,
            correction_type = CorrectionType.SCORE_OVERRIDE,
            rule_code       = rule_code,
            since           = since,
        )
        if not corrections:
            return {"rule_code": rule_code, "sample_count": 0}

        deltas = [
            c.corrected_value - c.system_value
            for c in corrections
            if isinstance(c.system_value, (int, float))
            and isinstance(c.corrected_value, (int, float))
        ]
        if not deltas:
            return {"rule_code": rule_code, "sample_count": 0}

        avg_delta      = sum(deltas) / len(deltas)
        upward_count   = sum(1 for d in deltas if d > 0.0)
        downward_count = sum(1 for d in deltas if d < 0.0)

        return {
            "rule_code":      rule_code,
            "sample_count":   len(deltas),
            "avg_delta":      round(avg_delta, 4),
            "upward_count":   upward_count,
            "downward_count": downward_count,
            "direction":      "up" if avg_delta > 0 else "down" if avg_delta < 0 else "neutral",
        }


# ── Conversion helper ──────────────────────────────────────────────────────────

def _entry_to_record(e: Any) -> CorrectionRecord:
    c = e.content
    return CorrectionRecord(
        entry_id        = e.entry_id,
        tenant_id       = e.tenant_id,
        analyst_id      = c.get("analyst_id", ""),
        correction_type = CorrectionType(c.get("correction_type", "score_override")),
        artifact_id     = e.artifact_id or "",
        artifact_type   = c.get("artifact_type", ""),
        system_value    = c.get("system_value"),
        corrected_value = c.get("corrected_value"),
        rule_code       = c.get("rule_code"),
        reasoning       = c.get("reasoning", ""),
        recorded_at     = e.recorded_at,
        confidence      = c.get("confidence"),
        supersedes_id   = e.supersedes_id,
    )
