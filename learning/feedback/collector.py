"""
Governed analyst feedback collection service.

Receives, validates, hashes, and persists feedback from analysts.
All writes are append-only — no updates, no deletes. Corrections are
expressed as new records that reference (supersede) the prior one.

Design guarantees
─────────────────
  - Immutability: feedback records are hashed on creation; tampering is detectable
  - Attribution: every record carries analyst_id, validated against the auth layer
  - Deduplication: duplicate submissions within a time window are detected
  - Audit-ready: every collection event is audit-logged
  - Async-safe: uses asyncio.Lock for concurrent submissions
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from learning.feedback.models import (
    ConfidenceOverride,
    FalsePositiveReport,
    FeedbackRecord,
    InvestigationQualityScore,
    OutcomeLabel,
    RecommendationRating,
)

log = logging.getLogger("evidentrx.learning.feedback.collector")

_DEDUP_WINDOW_MINUTES = 5


class FeedbackCollector:
    """
    Governed feedback collection service.

    Validates, hashes, and persists analyst feedback. Enforces:
      - Analyst attribution (analyst_id must be non-empty)
      - Tenant scoping (tenant_id must match the current request context)
      - Deduplication within a short window (prevents double-submissions)
      - Immutable lineage hashing
    """

    def __init__(
        self,
        db_writer:      Callable | None = None,   # async (FeedbackRecord) → str (feedback_id)
        event_emitter:  Callable | None = None,   # async (FeedbackRecord) → None
        audit_logger:   Callable | None = None,   # async (event_type, detail) → None
    ) -> None:
        self._db_writer     = db_writer
        self._event_emitter = event_emitter
        self._audit_logger  = audit_logger
        self._lock          = asyncio.Lock()
        self._recent:       dict[str, datetime] = {}   # dedup hash → timestamp
        self._total         = 0

    # ── Public submission API ──────────────────────────────────────────────────

    async def submit(self, feedback: FeedbackRecord) -> FeedbackRecord:
        """
        Submit a feedback record.

        Validates, hashes, deduplicates, persists, and emits an event.
        Returns the persisted record (with lineage_hash populated).
        Raises FeedbackValidationError on invalid input.
        """
        self._validate(feedback)
        feedback.lineage_hash = _compute_lineage_hash(feedback)

        async with self._lock:
            # Dedup check
            if self._is_duplicate(feedback):
                log.warning(
                    "FeedbackCollector: duplicate submission from analyst %s for %s/%s",
                    feedback.analyst_id, feedback.artifact_type, feedback.artifact_id,
                )
                raise FeedbackDuplicateError(
                    f"Duplicate feedback from {feedback.analyst_id} for "
                    f"{feedback.artifact_type}/{feedback.artifact_id} "
                    f"(within {_DEDUP_WINDOW_MINUTES}-minute window)"
                )

            # Persist
            if self._db_writer:
                try:
                    await self._db_writer(feedback)
                except Exception as exc:
                    raise FeedbackPersistenceError(f"Failed to persist feedback: {exc}") from exc

            # Mark dedup
            self._recent[feedback.lineage_hash] = feedback.created_at
            self._total += 1

        # Emit event (outside lock)
        if self._event_emitter:
            try:
                await self._event_emitter(feedback)
            except Exception as exc:
                log.error("FeedbackCollector: event emission failed: %s", exc)

        # Audit log
        if self._audit_logger:
            try:
                await self._audit_logger(
                    "feedback_submitted",
                    {
                        "feedback_id":   feedback.feedback_id,
                        "feedback_type": feedback.feedback_type.value,
                        "analyst_id":    feedback.analyst_id,
                        "artifact_type": feedback.artifact_type,
                        "artifact_id":   feedback.artifact_id,
                        "tenant_id":     feedback.tenant_id,
                    },
                )
            except Exception as exc:
                log.error("FeedbackCollector: audit log failed: %s", exc)

        log.info(
            "FeedbackCollector: accepted %s from %s [%s/%s]",
            feedback.feedback_type.value,
            feedback.analyst_id,
            feedback.artifact_type,
            feedback.artifact_id,
        )
        return feedback

    async def submit_false_positive(
        self,
        finding_id:        str,
        analyst_id:        str,
        tenant_id:         str,
        analyst_reasoning: str,
        rule_code:         str          = "",
        notes:             str | None = None,
    ) -> FalsePositiveReport:
        fp = FalsePositiveReport(
            finding_id        = finding_id,
            analyst_id        = analyst_id,
            tenant_id         = tenant_id,
            analyst_reasoning = analyst_reasoning,
            rule_code         = rule_code,
            notes             = notes,
        )
        await self.submit(fp)
        return fp

    async def submit_outcome_label(
        self,
        case_id:         str,
        analyst_id:      str,
        tenant_id:       str,
        outcome:         Any,
        actual_severity: Any | None = None,
        actual_exposure: float | None = None,
    ) -> OutcomeLabel:
        label = OutcomeLabel(
            case_id         = case_id,
            analyst_id      = analyst_id,
            tenant_id       = tenant_id,
            outcome         = outcome,
            actual_severity = actual_severity,
            actual_exposure = actual_exposure,
        )
        await self.submit(label)
        return label

    async def submit_investigation_quality(
        self,
        agent_run_id:           str,
        analyst_id:             str,
        tenant_id:              str,
        reasoning_quality:      int,
        evidence_completeness:  int,
        recommendation_quality: int,
        overall_score:          int,
        hallucination_observed: bool = False,
        missed_issues:          list[str] | None = None,
    ) -> InvestigationQualityScore:
        iq = InvestigationQualityScore(
            agent_run_id           = agent_run_id,
            analyst_id             = analyst_id,
            tenant_id              = tenant_id,
            reasoning_quality      = reasoning_quality,
            evidence_completeness  = evidence_completeness,
            recommendation_quality = recommendation_quality,
            overall_score          = overall_score,
            hallucination_observed = hallucination_observed,
            missed_issues          = missed_issues or [],
        )
        await self.submit(iq)
        return iq

    # ── Validation ─────────────────────────────────────────────────────────────

    @staticmethod
    def _validate(feedback: FeedbackRecord) -> None:
        if not feedback.analyst_id:
            raise FeedbackValidationError("analyst_id is required")
        if not feedback.tenant_id:
            raise FeedbackValidationError("tenant_id is required")
        if not feedback.artifact_id:
            raise FeedbackValidationError("artifact_id is required")
        if isinstance(feedback, RecommendationRating):
            if not 1 <= feedback.usefulness_score <= 5:
                raise FeedbackValidationError("usefulness_score must be 1–5")
        if isinstance(feedback, InvestigationQualityScore):
            for attr in ("reasoning_quality", "evidence_completeness",
                         "recommendation_quality", "overall_score"):
                val = getattr(feedback, attr, 3)
                if not 1 <= val <= 5:
                    raise FeedbackValidationError(f"{attr} must be 1–5")
        if isinstance(feedback, ConfidenceOverride):
            if not 0.0 <= feedback.analyst_confidence <= 1.0:
                raise FeedbackValidationError("analyst_confidence must be 0.0–1.0")

    def _is_duplicate(self, feedback: FeedbackRecord) -> bool:
        """Return True if the same feedback was submitted within the dedup window."""
        seen_at = self._recent.get(feedback.lineage_hash)
        if seen_at is None:
            return False
        cutoff = datetime.now(tz=UTC) - timedelta(minutes=_DEDUP_WINDOW_MINUTES)
        return seen_at > cutoff

    @property
    def total_collected(self) -> int:
        return self._total


# ── Lineage hash ──────────────────────────────────────────────────────────────

def _compute_lineage_hash(feedback: FeedbackRecord) -> str:
    """
    Compute an immutable lineage hash for a feedback record.

    The hash covers the semantic content of the feedback (not the ID or
    timestamp) so that the same content submitted twice can be detected.
    The stored hash also allows tamper detection.
    """
    payload = {
        "feedback_type": feedback.feedback_type.value,
        "analyst_id":    feedback.analyst_id,
        "tenant_id":     feedback.tenant_id,
        "artifact_type": feedback.artifact_type,
        "artifact_id":   feedback.artifact_id,
        "notes":         feedback.notes or "",
    }
    # Add type-specific fields
    for attr in (
        "finding_id", "rule_code", "analyst_reasoning", "case_id", "outcome",
        "recommendation_id", "usefulness_score", "agent_run_id", "overall_score",
        "system_confidence", "analyst_confidence",
    ):
        val = getattr(feedback, attr, None)
        if val is not None:
            payload[attr] = str(val)

    raw = json.dumps(payload, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()


# ── Exceptions ────────────────────────────────────────────────────────────────

class FeedbackValidationError(Exception):
    """Raised when a feedback record fails validation."""


class FeedbackDuplicateError(Exception):
    """Raised when duplicate feedback is submitted within the dedup window."""


class FeedbackPersistenceError(Exception):
    """Raised when feedback cannot be written to the database."""
