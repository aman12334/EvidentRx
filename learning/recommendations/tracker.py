"""
Recommendation outcome tracking.

Tracks the lifecycle of every system-generated recommendation — from
generation through analyst interaction to measured outcome. The tracking
data drives the recommendation effectiveness scoring and adaptation.

Tracked lifecycle
─────────────────
  GENERATED → PRESENTED → [DISMISSED | FOLLOWED]
                          FOLLOWED → [EFFECTIVE | INEFFECTIVE | UNKNOWN]

Each transition is recorded as an immutable event. No recommendation
record is ever modified — state is computed from the event log.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

log = logging.getLogger("evidentrx.learning.recommendations.tracker")


class RecommendationType(str, Enum):
    REMEDIATION      = "remediation"
    ESCALATION       = "escalation"
    WORKFLOW_ROUTING = "workflow_routing"
    INVESTIGATOR_GUIDANCE = "investigator_guidance"
    EVIDENCE_COLLECTION   = "evidence_collection"
    CASE_CLOSURE     = "case_closure"


class RecommendationStatus(str, Enum):
    GENERATED  = "generated"
    PRESENTED  = "presented"
    DISMISSED  = "dismissed"
    FOLLOWED   = "followed"
    EFFECTIVE  = "effective"
    INEFFECTIVE= "ineffective"
    EXPIRED    = "expired"


@dataclass
class RecommendationEvent:
    """Immutable event in a recommendation's lifecycle."""
    event_id:         str
    recommendation_id: str
    event_type:       RecommendationStatus
    actor_id:         str                   # analyst or "system"
    tenant_id:        str
    occurred_at:      datetime
    payload:          dict[str, Any]        = field(default_factory=dict)


@dataclass
class RecommendationRecord:
    """
    A system-generated recommendation and its full event history.

    The record itself is immutable after creation; the events list is
    append-only and represents state transitions.
    """
    recommendation_id: str
    tenant_id:         str
    recommendation_type: RecommendationType
    case_id:           str
    generated_by:      str                  # agent_run_id or rule_code
    content:           str                  # human-readable recommendation text
    confidence:        float                # system confidence in this recommendation
    version:           str                  # recommendation template version
    generated_at:      datetime
    events:            list[RecommendationEvent] = field(default_factory=list)

    @property
    def current_status(self) -> RecommendationStatus:
        if not self.events:
            return RecommendationStatus.GENERATED
        return self.events[-1].event_type

    @property
    def was_followed(self) -> bool:
        return any(
            e.event_type == RecommendationStatus.FOLLOWED
            for e in self.events
        )

    @property
    def outcome(self) -> RecommendationStatus | None:
        for e in reversed(self.events):
            if e.event_type in (
                RecommendationStatus.EFFECTIVE,
                RecommendationStatus.INEFFECTIVE,
            ):
                return e.event_type
        return None

    @property
    def time_to_decision_hours(self) -> float | None:
        """Hours between generation and analyst decision (followed/dismissed)."""
        for e in self.events:
            if e.event_type in (RecommendationStatus.FOLLOWED, RecommendationStatus.DISMISSED):
                delta = e.occurred_at - self.generated_at
                return delta.total_seconds() / 3600
        return None


class RecommendationTracker:
    """
    Tracks recommendation lifecycle events across all tenants.

    Provides queryable access to recommendation history for the
    effectiveness scorer and analytics layer.
    """

    def __init__(self, db_writer: Callable | None = None) -> None:
        self._records:  dict[str, RecommendationRecord] = {}
        self._by_case:  dict[str, list[str]] = {}         # case_id → [rec_ids]
        self._by_tenant:dict[str, list[str]] = {}         # tenant_id → [rec_ids]
        self._db_writer = db_writer

    # ── Create ─────────────────────────────────────────────────────────────────

    async def record_generated(
        self,
        tenant_id:            str,
        case_id:              str,
        recommendation_type:  RecommendationType,
        content:              str,
        confidence:           float,
        generated_by:         str,
        version:              str = "1.0.0",
    ) -> RecommendationRecord:
        rec_id = str(uuid.uuid4())
        record = RecommendationRecord(
            recommendation_id    = rec_id,
            tenant_id            = tenant_id,
            recommendation_type  = recommendation_type,
            case_id              = case_id,
            generated_by         = generated_by,
            content              = content,
            confidence           = confidence,
            version              = version,
            generated_at         = datetime.now(tz=UTC),
        )
        self._records[rec_id]  = record
        self._by_case.setdefault(case_id, []).append(rec_id)
        self._by_tenant.setdefault(tenant_id, []).append(rec_id)

        evt = self._make_event(rec_id, tenant_id, RecommendationStatus.GENERATED, "system")
        record.events.append(evt)

        await self._persist(record)
        log.info(
            "RecommendationTracker: generated %s [%s] for case %s",
            recommendation_type.value, rec_id[:8], case_id,
        )
        return record

    # ── Lifecycle transitions ──────────────────────────────────────────────────

    async def record_presented(self, rec_id: str, analyst_id: str) -> None:
        self._append_event(rec_id, analyst_id, RecommendationStatus.PRESENTED)

    async def record_followed(self, rec_id: str, analyst_id: str) -> None:
        self._append_event(rec_id, analyst_id, RecommendationStatus.FOLLOWED)

    async def record_dismissed(
        self,
        rec_id:     str,
        analyst_id: str,
        reason:     str = "",
    ) -> None:
        self._append_event(
            rec_id, analyst_id, RecommendationStatus.DISMISSED,
            payload={"reason": reason},
        )

    async def record_effective(
        self,
        rec_id:     str,
        analyst_id: str,
        notes:      str = "",
    ) -> None:
        self._append_event(
            rec_id, analyst_id, RecommendationStatus.EFFECTIVE,
            payload={"notes": notes},
        )

    async def record_ineffective(
        self,
        rec_id:     str,
        analyst_id: str,
        notes:      str = "",
    ) -> None:
        self._append_event(
            rec_id, analyst_id, RecommendationStatus.INEFFECTIVE,
            payload={"notes": notes},
        )

    # ── Queries ────────────────────────────────────────────────────────────────

    def get(self, rec_id: str) -> RecommendationRecord | None:
        return self._records.get(rec_id)

    def for_case(self, case_id: str) -> list[RecommendationRecord]:
        ids = self._by_case.get(case_id, [])
        return [self._records[i] for i in ids if i in self._records]

    def for_tenant(
        self,
        tenant_id: str,
        rec_type:  RecommendationType | None = None,
        status:    RecommendationStatus | None = None,
    ) -> list[RecommendationRecord]:
        ids    = self._by_tenant.get(tenant_id, [])
        recs   = [self._records[i] for i in ids if i in self._records]
        if rec_type:
            recs = [r for r in recs if r.recommendation_type == rec_type]
        if status:
            recs = [r for r in recs if r.current_status == status]
        return recs

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _make_event(
        self,
        rec_id:     str,
        tenant_id:  str,
        event_type: RecommendationStatus,
        actor_id:   str,
        payload:    dict | None = None,
    ) -> RecommendationEvent:
        return RecommendationEvent(
            event_id          = str(uuid.uuid4()),
            recommendation_id = rec_id,
            event_type        = event_type,
            actor_id          = actor_id,
            tenant_id         = tenant_id,
            occurred_at       = datetime.now(tz=UTC),
            payload           = payload or {},
        )

    def _append_event(
        self,
        rec_id:     str,
        actor_id:   str,
        event_type: RecommendationStatus,
        payload:    dict | None = None,
    ) -> None:
        record = self._records.get(rec_id)
        if not record:
            log.warning("RecommendationTracker: unknown rec_id %s", rec_id)
            return
        evt = self._make_event(
            rec_id, record.tenant_id, event_type, actor_id, payload,
        )
        record.events.append(evt)

    async def _persist(self, record: RecommendationRecord) -> None:
        if self._db_writer:
            try:
                await self._db_writer(record)
            except Exception as exc:
                log.error("RecommendationTracker: persist failed: %s", exc)
