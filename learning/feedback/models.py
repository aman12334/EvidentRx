"""
Human feedback data models.

Defines the canonical types for all analyst-generated feedback in the
platform. Feedback is the primary signal driving adaptive intelligence —
it must be:
  - Immutable once written (append-only event log)
  - Attributed to a specific analyst
  - Temporally ordered and replayable
  - Scoped to a specific investigation artifact

Feedback taxonomy
─────────────────
  FalsePositiveReport     — analyst marks a finding as incorrect
  FalseNegativeEscalation — analyst escalates a missed finding
  OutcomeLabel            — investigation closed with a resolution label
  RemediationOutcome      — remediation action effectiveness assessment
  ConfidenceOverride      — analyst overrides system confidence score
  RecommendationRating    — usefulness score for a system recommendation
  WorkflowAnnotation      — free-form annotation on a workflow step
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime    import datetime, timezone
from enum        import Enum
from typing      import Any, Optional
import uuid


# ── Feedback types ─────────────────────────────────────────────────────────────

class FeedbackType(str, Enum):
    FALSE_POSITIVE        = "false_positive"
    FALSE_NEGATIVE        = "false_negative"
    OUTCOME_LABEL         = "outcome_label"
    REMEDIATION_OUTCOME   = "remediation_outcome"
    CONFIDENCE_OVERRIDE   = "confidence_override"
    RECOMMENDATION_RATING = "recommendation_rating"
    WORKFLOW_ANNOTATION   = "workflow_annotation"
    INVESTIGATION_QUALITY = "investigation_quality"


class FeedbackStatus(str, Enum):
    PENDING   = "pending"    # submitted, awaiting review
    ACCEPTED  = "accepted"   # incorporated into calibration
    REJECTED  = "rejected"   # reviewed and discarded
    SUPERSEDED= "superseded" # replaced by later feedback from same analyst


# ── Investigation outcome labels ──────────────────────────────────────────────

class InvestigationOutcome(str, Enum):
    CONFIRMED_VIOLATION   = "confirmed_violation"
    NO_VIOLATION          = "no_violation"
    INCONCLUSIVE          = "inconclusive"
    REFERRED_TO_HRSA      = "referred_to_hrsa"
    REMEDIATED            = "remediated"
    CLOSED_INSUFFICIENT   = "closed_insufficient_evidence"


# ── Severity overrides ────────────────────────────────────────────────────────

class SeverityAssessment(str, Enum):
    CRITICAL    = "critical"
    HIGH        = "high"
    MEDIUM      = "medium"
    LOW         = "low"
    INFORMATIONAL = "informational"


# ── Remediation effectiveness ──────────────────────────────────────────────────

class RemediationEffectiveness(str, Enum):
    FULLY_EFFECTIVE   = "fully_effective"
    PARTIALLY_EFFECTIVE = "partially_effective"
    INEFFECTIVE       = "ineffective"
    PREMATURE_CLOSURE = "premature_closure"
    UNKNOWN           = "unknown"


# ── Base feedback record ──────────────────────────────────────────────────────

@dataclass
class FeedbackRecord:
    """
    Immutable base feedback record.

    All feedback types extend this. Once created, feedback records are
    never modified — corrections create new records that supersede old ones.
    """
    feedback_id:     str                         = field(default_factory=lambda: str(uuid.uuid4()))
    feedback_type:   FeedbackType                = FeedbackType.WORKFLOW_ANNOTATION
    tenant_id:       str                         = ""
    analyst_id:      str                         = ""              # attributed to specific analyst
    artifact_type:   str                         = ""              # "finding" | "case" | "agent_run" | "recommendation"
    artifact_id:     str                         = ""              # FK to the artifact being rated
    status:          FeedbackStatus              = FeedbackStatus.PENDING
    created_at:      datetime                    = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    notes:           Optional[str]               = None
    metadata:        dict[str, Any]              = field(default_factory=dict)
    supersedes_id:   Optional[str]               = None            # if this replaces prior feedback
    lineage_hash:    str                         = ""              # computed on creation

    def to_dict(self) -> dict[str, Any]:
        return {
            "feedback_id":   self.feedback_id,
            "feedback_type": self.feedback_type.value,
            "tenant_id":     self.tenant_id,
            "analyst_id":    self.analyst_id,
            "artifact_type": self.artifact_type,
            "artifact_id":   self.artifact_id,
            "status":        self.status.value,
            "created_at":    self.created_at.isoformat(),
            "notes":         self.notes,
            "metadata":      self.metadata,
            "supersedes_id": self.supersedes_id,
            "lineage_hash":  self.lineage_hash,
        }


# ── Specialised feedback types ────────────────────────────────────────────────

@dataclass
class FalsePositiveReport(FeedbackRecord):
    """
    Analyst marks a compliance finding as a false positive.

    Fields
    ──────
    finding_id          : The audit finding being disputed
    rule_code           : Compliance rule that triggered the finding
    analyst_reasoning   : Why this is believed to be a false positive
    suggested_severity  : What severity the analyst thinks is appropriate (if any)
    """
    finding_id:         str                  = ""
    rule_code:          str                  = ""
    analyst_reasoning:  str                  = ""
    suggested_severity: Optional[SeverityAssessment] = None

    def __post_init__(self) -> None:
        self.feedback_type = FeedbackType.FALSE_POSITIVE
        self.artifact_type = "finding"
        self.artifact_id   = self.finding_id


@dataclass
class FalseNegativeEscalation(FeedbackRecord):
    """
    Analyst escalates a compliance risk that the system missed.

    The analyst believes a violation exists that was not flagged.
    """
    case_id:             str                  = ""
    rule_code:           str                  = ""
    analyst_reasoning:   str                  = ""
    estimated_severity:  SeverityAssessment   = SeverityAssessment.MEDIUM
    estimated_exposure:  Optional[float]      = None     # financial exposure estimate

    def __post_init__(self) -> None:
        self.feedback_type = FeedbackType.FALSE_NEGATIVE
        self.artifact_type = "case"
        self.artifact_id   = self.case_id


@dataclass
class OutcomeLabel(FeedbackRecord):
    """
    Analyst labels the outcome of a completed investigation.

    Used to build a ground-truth dataset for calibrating risk scores
    and improving future investigation routing.
    """
    case_id:             str                      = ""
    outcome:             InvestigationOutcome     = InvestigationOutcome.INCONCLUSIVE
    actual_severity:     Optional[SeverityAssessment] = None
    actual_exposure:     Optional[float]          = None
    time_to_resolution_days: Optional[int]        = None
    investigation_quality: Optional[int]          = None  # 1-5 rating of investigation quality

    def __post_init__(self) -> None:
        self.feedback_type = FeedbackType.OUTCOME_LABEL
        self.artifact_type = "case"
        self.artifact_id   = self.case_id


@dataclass
class RemediationOutcomeReport(FeedbackRecord):
    """
    Tracks whether a remediation action was effective.

    Feeds the recommendation learning loop — ineffective remediations
    lower the recommendation score; effective ones increase it.
    """
    case_id:              str                       = ""
    recommendation_id:    str                       = ""
    effectiveness:        RemediationEffectiveness  = RemediationEffectiveness.UNKNOWN
    recurrence_observed:  bool                      = False
    recurrence_window_days: Optional[int]           = None
    analyst_rating:       Optional[int]             = None    # 1-5

    def __post_init__(self) -> None:
        self.feedback_type = FeedbackType.REMEDIATION_OUTCOME
        self.artifact_type = "case"
        self.artifact_id   = self.case_id


@dataclass
class ConfidenceOverride(FeedbackRecord):
    """
    Analyst overrides the system-assigned confidence score for a finding.

    The override is non-destructive — the original system score is preserved
    in metadata. The override is used in calibration but does not modify
    the original finding record.
    """
    finding_id:          str    = ""
    system_confidence:   float  = 0.0    # 0.0–1.0 system-assigned score
    analyst_confidence:  float  = 0.0    # 0.0–1.0 analyst-assigned override
    override_reason:     str    = ""

    def __post_init__(self) -> None:
        self.feedback_type = FeedbackType.CONFIDENCE_OVERRIDE
        self.artifact_type = "finding"
        self.artifact_id   = self.finding_id


@dataclass
class RecommendationRating(FeedbackRecord):
    """
    Analyst rates the usefulness of a system-generated recommendation.

    Used to drive recommendation effectiveness scoring and adaptation.
    """
    recommendation_id:   str   = ""
    usefulness_score:    int   = 3       # 1=not useful, 5=highly useful
    was_followed:        bool  = False
    outcome_if_followed: Optional[str] = None

    def __post_init__(self) -> None:
        self.feedback_type = FeedbackType.RECOMMENDATION_RATING
        self.artifact_type = "recommendation"
        self.artifact_id   = self.recommendation_id


@dataclass
class InvestigationQualityScore(FeedbackRecord):
    """
    Analyst rates the overall quality of an AI-generated investigation.

    Scores across multiple dimensions to enable targeted improvement.
    """
    agent_run_id:           str   = ""
    reasoning_quality:      int   = 3    # 1-5: was the reasoning sound?
    evidence_completeness:  int   = 3    # 1-5: did it surface relevant evidence?
    recommendation_quality: int   = 3    # 1-5: were recommendations actionable?
    overall_score:          int   = 3    # 1-5: overall quality
    missed_issues:          list[str] = field(default_factory=list)
    hallucination_observed: bool  = False

    def __post_init__(self) -> None:
        self.feedback_type = FeedbackType.INVESTIGATION_QUALITY
        self.artifact_type = "agent_run"
        self.artifact_id   = self.agent_run_id
