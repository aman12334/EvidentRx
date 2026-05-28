"""
Regulatory timeline service.

Maintains a chronological, tenant-scoped record of all regulatory events
that affect a 340B compliance operation:

  - Document ingestion events (new versions, initial ingestion)
  - Policy diff events (material changes detected between versions)
  - Drift detection events (snapshot results)
  - Recommendation lifecycle events (created, submitted, approved, implemented)
  - Investigation policy context events (context built, citation added)
  - Compliance readiness assessments

The timeline is append-only and immutable.  Events are never deleted or
modified — only new events are added.  This enables full historical replay
of the regulatory environment at any point in time.

Design constraints
──────────────────
- Timeline events are immutable once created
- All events are linked to the source entity via external_id
- Temporal queries are O(n) scans — suitable for compliance audit volumes
  (not high-throughput analytics; use a time-series store for that)
- No LLM reasoning involved in any timeline operation
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime    import datetime, timezone
from enum        import Enum
from typing      import Any, Optional

log = logging.getLogger("evidentrx.regulatory.timeline.service")


class TimelineEventType(str, Enum):
    # Document lifecycle
    DOCUMENT_INGESTED    = "document_ingested"
    DOCUMENT_VERSIONED   = "document_versioned"
    DOCUMENT_ARCHIVED    = "document_archived"
    # Diff / drift
    POLICY_DIFF_COMPUTED = "policy_diff_computed"
    DRIFT_DETECTED       = "drift_detected"
    # Recommendations
    RECOMMENDATION_CREATED     = "recommendation_created"
    RECOMMENDATION_SUBMITTED   = "recommendation_submitted"
    RECOMMENDATION_APPROVED    = "recommendation_approved"
    RECOMMENDATION_REJECTED    = "recommendation_rejected"
    RECOMMENDATION_IMPLEMENTED = "recommendation_implemented"
    RECOMMENDATION_SUPERSEDED  = "recommendation_superseded"
    RECOMMENDATION_WITHDRAWN   = "recommendation_withdrawn"
    # Investigation intelligence
    POLICY_CONTEXT_BUILT   = "policy_context_built"
    CITATION_ADDED         = "citation_added"
    # Readiness
    READINESS_ASSESSED     = "readiness_assessed"
    # Manual annotation
    ANALYST_NOTE           = "analyst_note"


class TimelineEventSeverity(str, Enum):
    INFORMATIONAL = "informational"
    LOW           = "low"
    MEDIUM        = "medium"
    HIGH          = "high"
    CRITICAL      = "critical"


@dataclass
class TimelineEvent:
    """
    An immutable regulatory timeline event.

    Once created, no fields may be modified.  The only valid operation is
    reading and querying events.
    """
    event_id:     str
    tenant_id:    str
    event_type:   TimelineEventType
    occurred_at:  datetime
    title:        str
    description:  str
    external_id:  str          # ID of the source entity (doc_id, rec_id, etc.)
    external_type: str         # "document" | "diff" | "drift" | "recommendation" | ...
    severity:     TimelineEventSeverity  = TimelineEventSeverity.INFORMATIONAL
    actor_id:     str                    = "system"
    domain:       Optional[str]          = None    # PolicyDomain.value if applicable
    tags:         list[str]              = field(default_factory=list)
    metadata:     dict[str, Any]         = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id":     self.event_id,
            "tenant_id":    self.tenant_id,
            "event_type":   self.event_type.value,
            "occurred_at":  self.occurred_at.isoformat(),
            "title":        self.title,
            "description":  self.description,
            "external_id":  self.external_id,
            "external_type":self.external_type,
            "severity":     self.severity.value,
            "actor_id":     self.actor_id,
            "domain":       self.domain,
            "tags":         self.tags,
        }


@dataclass
class TimelineQuery:
    """Parameters for a timeline query."""
    tenant_id:    str
    since:        Optional[datetime]                = None
    until:        Optional[datetime]                = None
    event_types:  Optional[list[TimelineEventType]] = None
    severity_min: Optional[TimelineEventSeverity]   = None
    external_id:  Optional[str]                     = None
    domain:       Optional[str]                     = None
    actor_id:     Optional[str]                     = None
    limit:        int                               = 100

    def matches(self, event: TimelineEvent) -> bool:
        if event.tenant_id != self.tenant_id:
            return False
        if self.since and event.occurred_at < self.since:
            return False
        if self.until and event.occurred_at > self.until:
            return False
        if self.event_types and event.event_type not in self.event_types:
            return False
        if self.severity_min:
            _order = [
                TimelineEventSeverity.INFORMATIONAL,
                TimelineEventSeverity.LOW,
                TimelineEventSeverity.MEDIUM,
                TimelineEventSeverity.HIGH,
                TimelineEventSeverity.CRITICAL,
            ]
            if _order.index(event.severity) < _order.index(self.severity_min):
                return False
        if self.external_id and event.external_id != self.external_id:
            return False
        if self.domain and event.domain != self.domain:
            return False
        if self.actor_id and event.actor_id != self.actor_id:
            return False
        return True


class RegulatoryTimelineService:
    """
    Append-only regulatory event timeline for a multi-tenant platform.

    Supports:
    - Recording events from any Phase 13 service
    - Point-in-time queries for audit replay
    - Entity-scoped timelines (e.g. "all events for document X")
    - Severity-filtered views for operational dashboards
    """

    def __init__(self) -> None:
        # event_id → TimelineEvent  (insertion-ordered dict preserves append order)
        self._events: dict[str, TimelineEvent] = {}

    # ── Recording ───────────────────────────────────────────────────────────────

    def record(
        self,
        tenant_id:     str,
        event_type:    TimelineEventType,
        title:         str,
        description:   str,
        external_id:   str,
        external_type: str,
        severity:      TimelineEventSeverity = TimelineEventSeverity.INFORMATIONAL,
        actor_id:      str                   = "system",
        domain:        Optional[str]         = None,
        tags:          Optional[list[str]]   = None,
        metadata:      Optional[dict]        = None,
        occurred_at:   Optional[datetime]    = None,
    ) -> TimelineEvent:
        """Append a new immutable event to the timeline."""
        event = TimelineEvent(
            event_id      = str(uuid.uuid4()),
            tenant_id     = tenant_id,
            event_type    = event_type,
            occurred_at   = occurred_at or datetime.now(tz=timezone.utc),
            title         = title,
            description   = description,
            external_id   = external_id,
            external_type = external_type,
            severity      = severity,
            actor_id      = actor_id,
            domain        = domain,
            tags          = tags or [],
            metadata      = metadata or {},
        )
        self._events[event.event_id] = event
        log.debug(
            "RegulatoryTimelineService: [%s] %s for tenant %s",
            event_type.value, external_id[:12], tenant_id[:8],
        )
        return event

    # ── Convenience recorders ───────────────────────────────────────────────────

    def record_document_ingested(
        self,
        tenant_id:  str,
        doc_id:     str,
        title:      str,
        version:    str,
        domains:    list[str],
        is_update:  bool = False,
    ) -> TimelineEvent:
        event_type = (
            TimelineEventType.DOCUMENT_VERSIONED
            if is_update
            else TimelineEventType.DOCUMENT_INGESTED
        )
        action = "versioned" if is_update else "ingested"
        return self.record(
            tenant_id     = tenant_id,
            event_type    = event_type,
            title         = f"Document {action}: {title[:60]}",
            description   = f"Version {version} of '{title}' was {action} covering: {', '.join(domains)}.",
            external_id   = doc_id,
            external_type = "document",
            severity      = TimelineEventSeverity.INFORMATIONAL,
            domain        = domains[0] if domains else None,
            tags          = domains,
        )

    def record_drift_detected(
        self,
        tenant_id:         str,
        report_id:         str,
        overall_severity:  str,
        finding_count:     int,
        summary:           str,
    ) -> TimelineEvent:
        severity_map = {
            "critical":      TimelineEventSeverity.CRITICAL,
            "high":          TimelineEventSeverity.HIGH,
            "medium":        TimelineEventSeverity.MEDIUM,
            "low":           TimelineEventSeverity.LOW,
            "informational": TimelineEventSeverity.INFORMATIONAL,
        }
        return self.record(
            tenant_id     = tenant_id,
            event_type    = TimelineEventType.DRIFT_DETECTED,
            title         = f"Regulatory drift detected ({finding_count} findings)",
            description   = summary,
            external_id   = report_id,
            external_type = "drift_report",
            severity      = severity_map.get(overall_severity, TimelineEventSeverity.MEDIUM),
        )

    def record_recommendation_event(
        self,
        tenant_id: str,
        rec_id:    str,
        event:     str,        # matches RecommendationStatus/event label
        title:     str,
        actor_id:  str,
        priority:  str,
    ) -> TimelineEvent:
        _event_type_map = {
            "created":     TimelineEventType.RECOMMENDATION_CREATED,
            "submitted":   TimelineEventType.RECOMMENDATION_SUBMITTED,
            "approved":    TimelineEventType.RECOMMENDATION_APPROVED,
            "rejected":    TimelineEventType.RECOMMENDATION_REJECTED,
            "implemented": TimelineEventType.RECOMMENDATION_IMPLEMENTED,
            "superseded":  TimelineEventType.RECOMMENDATION_SUPERSEDED,
            "withdrawn":   TimelineEventType.RECOMMENDATION_WITHDRAWN,
        }
        _sev_map = {
            "urgent": TimelineEventSeverity.CRITICAL,
            "high":   TimelineEventSeverity.HIGH,
            "normal": TimelineEventSeverity.MEDIUM,
            "low":    TimelineEventSeverity.LOW,
        }
        return self.record(
            tenant_id     = tenant_id,
            event_type    = _event_type_map.get(event, TimelineEventType.RECOMMENDATION_CREATED),
            title         = f"Recommendation {event}: {title[:60]}",
            description   = f"Recommendation '{title}' was {event} by {actor_id}.",
            external_id   = rec_id,
            external_type = "recommendation",
            severity      = _sev_map.get(priority, TimelineEventSeverity.INFORMATIONAL),
            actor_id      = actor_id,
        )

    def record_readiness_assessed(
        self,
        tenant_id:   str,
        snapshot_id: str,
        score:       float,
        band:        str,
        signal_count: int,
    ) -> TimelineEvent:
        severity_map = {
            "strong":    TimelineEventSeverity.INFORMATIONAL,
            "adequate":  TimelineEventSeverity.LOW,
            "at_risk":   TimelineEventSeverity.MEDIUM,
            "deficient": TimelineEventSeverity.HIGH,
            "critical":  TimelineEventSeverity.CRITICAL,
        }
        return self.record(
            tenant_id     = tenant_id,
            event_type    = TimelineEventType.READINESS_ASSESSED,
            title         = f"Readiness assessed: {band.upper()} (score {score:.2f})",
            description   = (
                f"Compliance readiness score: {score:.4f} [{band}]. "
                f"{signal_count} scoring signal(s) identified."
            ),
            external_id   = snapshot_id,
            external_type = "readiness_snapshot",
            severity      = severity_map.get(band, TimelineEventSeverity.MEDIUM),
        )

    def add_analyst_note(
        self,
        tenant_id:   str,
        external_id: str,
        note:        str,
        analyst_id:  str,
    ) -> TimelineEvent:
        return self.record(
            tenant_id     = tenant_id,
            event_type    = TimelineEventType.ANALYST_NOTE,
            title         = f"Analyst note on {external_id[:16]}",
            description   = note,
            external_id   = external_id,
            external_type = "annotation",
            actor_id      = analyst_id,
        )

    # ── Querying ────────────────────────────────────────────────────────────────

    def query(self, q: TimelineQuery) -> list[TimelineEvent]:
        """Return events matching query, newest first, capped at q.limit."""
        results = [e for e in self._events.values() if q.matches(e)]
        results.sort(key=lambda e: e.occurred_at, reverse=True)
        return results[: q.limit]

    def entity_timeline(
        self,
        tenant_id:     str,
        external_id:   str,
        limit:         int = 50,
    ) -> list[TimelineEvent]:
        """Return all events for a specific entity (document, recommendation, etc.)."""
        return self.query(TimelineQuery(
            tenant_id   = tenant_id,
            external_id = external_id,
            limit       = limit,
        ))

    def tenant_timeline(
        self,
        tenant_id: str,
        since:     Optional[datetime] = None,
        until:     Optional[datetime] = None,
        limit:     int = 100,
    ) -> list[TimelineEvent]:
        """Full regulatory timeline for a tenant between two timestamps."""
        return self.query(TimelineQuery(
            tenant_id = tenant_id,
            since     = since,
            until     = until,
            limit     = limit,
        ))

    def critical_events(
        self,
        tenant_id: str,
        since:     Optional[datetime] = None,
    ) -> list[TimelineEvent]:
        """Return CRITICAL and HIGH severity events for rapid triage."""
        return self.query(TimelineQuery(
            tenant_id    = tenant_id,
            since        = since,
            severity_min = TimelineEventSeverity.HIGH,
            limit        = 200,
        ))

    def event_count(self, tenant_id: str) -> int:
        return sum(1 for e in self._events.values() if e.tenant_id == tenant_id)


# ── Singleton ──────────────────────────────────────────────────────────────────

_service: Optional[RegulatoryTimelineService] = None


def get_timeline_service() -> RegulatoryTimelineService:
    global _service
    if _service is None:
        _service = RegulatoryTimelineService()
    return _service
