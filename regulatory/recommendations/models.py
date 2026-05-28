"""
Regulatory recommendation models.

Recommendations are governed advisory outputs produced by the
PolicyRecommendationService. They are NEVER applied autonomously —
every recommendation requires explicit human approval before any
operational change can take effect.

Recommendation lifecycle
────────────────────────
  DRAFT      → generated; not yet submitted for review
  SUBMITTED  → awaiting compliance officer review
  APPROVED   → approved; cleared for implementation
  REJECTED   → declined with reasons; may be revised
  SUPERSEDED → replaced by a newer recommendation
  WITHDRAWN  → author-retracted (e.g. regulatory update changed scope)
  IMPLEMENTED → operational change has been applied

Each state transition is audit-trailed. Rollback is always possible:
a SUPERSEDED recommendation can be re-applied as a new DRAFT.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime    import datetime, timezone
from enum        import Enum
from typing      import Any, Optional


class RecommendationType(str, Enum):
    WORKFLOW_MODIFICATION    = "workflow_modification"
    ESCALATION_POLICY_UPDATE = "escalation_policy_update"
    RULE_REVIEW              = "rule_review"
    OPERATIONAL_REMEDIATION  = "operational_remediation"
    INVESTIGATION_PRIORITY   = "investigation_priority_change"
    THRESHOLD_REVIEW         = "threshold_review"
    COVERAGE_EXPANSION       = "coverage_expansion"


class RecommendationStatus(str, Enum):
    DRAFT       = "draft"
    SUBMITTED   = "submitted"
    APPROVED    = "approved"
    REJECTED    = "rejected"
    SUPERSEDED  = "superseded"
    WITHDRAWN   = "withdrawn"
    IMPLEMENTED = "implemented"


class RecommendationPriority(str, Enum):
    LOW      = "low"
    NORMAL   = "normal"
    HIGH     = "high"
    URGENT   = "urgent"


@dataclass
class RecommendationLineageEntry:
    """One entry in the lineage chain of a recommendation."""
    entry_id:    str
    rec_id:      str
    event:       str        # "created"|"submitted"|"approved"|"rejected"|"superseded"|"rolled_back"
    actor_id:    str
    notes:       str        = ""
    occurred_at: datetime   = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    content_hash: str       = ""   # SHA-256 of (rec_id + event + actor_id + occurred_at)

    def __post_init__(self) -> None:
        if not self.content_hash:
            raw = f"{self.rec_id}:{self.event}:{self.actor_id}:{self.occurred_at.isoformat()}"
            self.content_hash = hashlib.sha256(raw.encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id":    self.entry_id,
            "rec_id":      self.rec_id,
            "event":       self.event,
            "actor_id":    self.actor_id,
            "notes":       self.notes,
            "occurred_at": self.occurred_at.isoformat(),
            "content_hash":self.content_hash,
        }


@dataclass
class PolicyRecommendation:
    """
    A governed recommendation for an operational policy change.

    content_hash is SHA-256 of the recommendation body at creation time.
    Any modification requires creating a new recommendation (the old one
    is marked SUPERSEDED), preserving the immutable record.
    """
    rec_id:           str
    tenant_id:        str
    rec_type:         RecommendationType
    title:            str
    rationale:        str          # why this change is needed
    proposed_change:  str          # what specifically should change
    affected_elements: list[str]   # element_ids from ImpactReport
    source_type:      str          # "diff" | "drift" | "manual"
    source_id:        str          # diff_id, drift report_id, or analyst_id
    status:           RecommendationStatus
    priority:         RecommendationPriority
    content_hash:     str
    version:          int                  = 1
    prior_rec_id:     Optional[str]        = None   # if this supersedes another
    created_by:       str                  = "system"
    created_at:       datetime             = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    submitted_at:     Optional[datetime]   = None
    decided_at:       Optional[datetime]   = None
    decided_by:       Optional[str]        = None
    decision_notes:   str                  = ""
    implemented_at:   Optional[datetime]   = None
    action_by_date:   Optional[str]        = None   # ISO-8601 date deadline
    lineage:          list[RecommendationLineageEntry] = field(default_factory=list)
    metadata:         dict[str, Any]       = field(default_factory=dict)

    @property
    def is_actionable(self) -> bool:
        return self.status == RecommendationStatus.APPROVED

    def to_dict(self) -> dict[str, Any]:
        return {
            "rec_id":            self.rec_id,
            "tenant_id":         self.tenant_id,
            "rec_type":          self.rec_type.value,
            "title":             self.title,
            "rationale":         self.rationale,
            "proposed_change":   self.proposed_change,
            "affected_elements": self.affected_elements,
            "source_type":       self.source_type,
            "source_id":         self.source_id,
            "status":            self.status.value,
            "priority":          self.priority.value,
            "version":           self.version,
            "prior_rec_id":      self.prior_rec_id,
            "created_by":        self.created_by,
            "created_at":        self.created_at.isoformat(),
            "submitted_at":      self.submitted_at.isoformat() if self.submitted_at else None,
            "decided_at":        self.decided_at.isoformat() if self.decided_at else None,
            "decided_by":        self.decided_by,
            "decision_notes":    self.decision_notes,
            "action_by_date":    self.action_by_date,
            "content_hash":      self.content_hash,
            "lineage_length":    len(self.lineage),
        }


def _hash_recommendation(
    rec_type:        str,
    title:           str,
    proposed_change: str,
    source_id:       str,
) -> str:
    raw = json.dumps({
        "rec_type":        rec_type,
        "title":           title,
        "proposed_change": proposed_change,
        "source_id":       source_id,
    }, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def new_rec_id() -> str:
    return f"rec_{uuid.uuid4().hex[:16]}"
