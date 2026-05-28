"""
Recommendation effectiveness scoring.

Computes per-recommendation and per-template effectiveness scores from
the tracked lifecycle events. Scores drive the recommendation versioning
system — low-scoring templates are flagged for revision or retirement.

Scoring model
─────────────
  follow_rate          = followed / presented
  effectiveness_rate   = effective / followed
  composite_score      = follow_rate × effectiveness_rate × confidence_weight
  decay_factor         = e^(-λ × age_days)  (recent outcomes weighted higher)

Templates (identified by recommendation_type + version) accumulate
scores across all recommendations generated from that template.
"""

from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime

from learning.recommendations.tracker import (
    RecommendationRecord,
    RecommendationStatus,
    RecommendationType,
)

log = logging.getLogger("evidentrx.learning.recommendations.scorer")

# Decay half-life: outcomes older than 60 days carry half weight
_DECAY_HALF_LIFE_DAYS = 60.0
_MIN_SAMPLE_COUNT     = 5


@dataclass
class TemplateScore:
    """Effectiveness score for a recommendation template (type + version)."""
    recommendation_type: str
    version:             str
    tenant_id:           str
    follow_rate:         float          # fraction of presented recs that were followed
    effectiveness_rate:  float          # fraction of followed recs that were effective
    composite_score:     float          # weighted composite
    sample_count:        int
    dismissed_count:     int
    effective_count:     int
    ineffective_count:   int
    avg_time_to_decision_hours: float | None
    computed_at:         datetime       = field(default_factory=lambda: datetime.now(tz=UTC))

    @property
    def is_performing(self) -> bool:
        """True if the template meets minimum quality thresholds."""
        return (
            self.sample_count >= _MIN_SAMPLE_COUNT
            and self.follow_rate >= 0.30
            and self.effectiveness_rate >= 0.50
        )

    @property
    def quality_label(self) -> str:
        if self.composite_score >= 0.70:
            return "high_performing"
        elif self.composite_score >= 0.45:
            return "acceptable"
        elif self.composite_score >= 0.25:
            return "under_performing"
        return "flagged_for_review"


class RecommendationScorer:
    """
    Computes effectiveness scores for recommendation templates.

    Aggregates outcome data from a RecommendationTracker and produces
    TemplateScore objects suitable for the versioning / governance layers.
    """

    def score_template(
        self,
        records:     list[RecommendationRecord],
        rec_type:    RecommendationType,
        version:     str,
        tenant_id:   str,
    ) -> TemplateScore:
        """
        Compute the effectiveness score for one template (type + version).

        Parameters
        ----------
        records   : All recommendation records for this template
        rec_type  : Recommendation type being scored
        version   : Template version
        tenant_id : Tenant context
        """
        now = datetime.now(tz=UTC)

        # Filter to this template
        template_records = [
            r for r in records
            if r.recommendation_type == rec_type and r.version == version
        ]
        if not template_records:
            return _empty_score(rec_type.value, version, tenant_id)

        presented   = [r for r in template_records if _has_status(r, RecommendationStatus.PRESENTED)]
        followed    = [r for r in template_records if r.was_followed]
        dismissed   = [r for r in template_records if _has_status(r, RecommendationStatus.DISMISSED)]
        effective   = [r for r in template_records if r.outcome == RecommendationStatus.EFFECTIVE]
        ineffective = [r for r in template_records if r.outcome == RecommendationStatus.INEFFECTIVE]

        follow_rate       = len(followed) / len(presented) if presented else 0.0
        effectiveness_rate= len(effective) / len(followed) if followed else 0.0

        # Decay-weighted composite
        decay_weights = [
            _decay_weight(r.generated_at, now) for r in template_records
        ]
        total_weight = sum(decay_weights) or 1.0

        # Composite: follow_rate * effectiveness_rate, decay-weighted
        composite = 0.0
        for r, w in zip(template_records, decay_weights):
            rec_score = (1.0 if r.was_followed else 0.0) * (
                1.0 if r.outcome == RecommendationStatus.EFFECTIVE
                else 0.5 if r.outcome is None and r.was_followed
                else 0.0
            )
            composite += rec_score * (w / total_weight)

        decision_times = [
            r.time_to_decision_hours
            for r in template_records
            if r.time_to_decision_hours is not None
        ]

        return TemplateScore(
            recommendation_type          = rec_type.value,
            version                      = version,
            tenant_id                    = tenant_id,
            follow_rate                  = round(follow_rate, 4),
            effectiveness_rate           = round(effectiveness_rate, 4),
            composite_score              = round(composite, 4),
            sample_count                 = len(template_records),
            dismissed_count              = len(dismissed),
            effective_count              = len(effective),
            ineffective_count            = len(ineffective),
            avg_time_to_decision_hours   = (
                round(statistics.mean(decision_times), 2) if decision_times else None
            ),
        )

    def score_all_templates(
        self,
        records:   list[RecommendationRecord],
        tenant_id: str,
    ) -> list[TemplateScore]:
        """Score every (type, version) combination present in the records."""
        templates: set[tuple[str, str]] = {
            (r.recommendation_type.value, r.version)
            for r in records
        }
        scores = []
        for type_str, version in templates:
            try:
                rec_type = RecommendationType(type_str)
            except ValueError:
                continue
            score = self.score_template(
                records   = records,
                rec_type  = rec_type,
                version   = version,
                tenant_id = tenant_id,
            )
            scores.append(score)
        return sorted(scores, key=lambda s: s.composite_score, reverse=True)

    def flagged_templates(
        self,
        scores: list[TemplateScore],
        min_samples: int = _MIN_SAMPLE_COUNT,
    ) -> list[TemplateScore]:
        """Return templates that are under-performing and have enough samples."""
        return [
            s for s in scores
            if s.sample_count >= min_samples and not s.is_performing
        ]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _decay_weight(generated_at: datetime, now: datetime) -> float:
    age_days = (now - generated_at).total_seconds() / 86400
    return math.exp(-math.log(2) * age_days / _DECAY_HALF_LIFE_DAYS)


def _has_status(r: RecommendationRecord, status: RecommendationStatus) -> bool:
    return any(e.event_type == status for e in r.events)


def _empty_score(rec_type: str, version: str, tenant_id: str) -> TemplateScore:
    return TemplateScore(
        recommendation_type         = rec_type,
        version                     = version,
        tenant_id                   = tenant_id,
        follow_rate                 = 0.0,
        effectiveness_rate          = 0.0,
        composite_score             = 0.0,
        sample_count                = 0,
        dismissed_count             = 0,
        effective_count             = 0,
        ineffective_count           = 0,
        avg_time_to_decision_hours  = None,
    )
