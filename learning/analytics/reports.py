"""
Intelligence analytics reports.

Aggregates data from across the learning layer to produce executive-level
and operational intelligence reports. All reports are computed on-demand
from structured memory and outcome data — no report data is stored as a
separate derivative.

Reports produced
────────────────
  FalsePositiveTrendReport     — FP rate trend over configurable windows
  InvestigationQualityReport   — reasoning/outcome quality across analysts
  CalibrationEffectivenessReport — how well calibration is tracking reality
  RecommendationImpactReport   — follow rates, effectiveness, and ROI signals
  LearningSystemHealthReport   — end-to-end health of the learning pipeline
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

log = logging.getLogger("evidentrx.learning.analytics.reports")


@dataclass
class FalsePositiveTrendReport:
    """False positive rates over rolling time windows."""
    tenant_id:          str
    computed_at:        datetime
    window_days:        int
    total_cases:        int
    fp_count:           int
    fp_rate:            float
    fp_rate_prior:      float | None     # same window, prior period
    delta_vs_prior:     float | None     # fp_rate – fp_rate_prior
    by_rule_code:       dict[str, float]   # rule_code → fp_rate
    trend:              str                 # "improving" | "stable" | "degrading"

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id":     self.tenant_id,
            "computed_at":   self.computed_at.isoformat(),
            "window_days":   self.window_days,
            "total_cases":   self.total_cases,
            "fp_count":      self.fp_count,
            "fp_rate":       self.fp_rate,
            "delta_vs_prior":self.delta_vs_prior,
            "by_rule_code":  self.by_rule_code,
            "trend":         self.trend,
        }


@dataclass
class InvestigationQualityReport:
    """Aggregated investigation quality metrics."""
    tenant_id:                str
    computed_at:              datetime
    period_days:              int
    total_rated:              int
    avg_reasoning_quality:    float | None
    avg_evidence_completeness: float | None
    avg_recommendation_quality: float | None
    avg_overall_score:        float | None
    hallucination_rate:       float
    quality_distribution:     dict[str, int]   # "1"–"5" → count
    low_quality_rate:         float             # fraction scoring ≤ 2

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id":               self.tenant_id,
            "computed_at":             self.computed_at.isoformat(),
            "period_days":             self.period_days,
            "total_rated":             self.total_rated,
            "avg_overall_score":       self.avg_overall_score,
            "hallucination_rate":      self.hallucination_rate,
            "low_quality_rate":        self.low_quality_rate,
            "quality_distribution":    self.quality_distribution,
        }


@dataclass
class CalibrationEffectivenessReport:
    """How well the calibration layer is tracking actual outcomes."""
    tenant_id:              str
    computed_at:            datetime
    period_days:            int
    calibration_version:    str | None
    avg_confidence_error:   float | None
    median_confidence_error: float | None
    overconfident_rate:     float    # fraction where confidence > actual outcome
    underconfident_rate:    float
    ece:                    float | None   # Expected Calibration Error
    sample_count:           int
    is_reliable:            bool             # sample_count ≥ 20

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id":             self.tenant_id,
            "computed_at":           self.computed_at.isoformat(),
            "period_days":           self.period_days,
            "calibration_version":   self.calibration_version,
            "avg_confidence_error":  self.avg_confidence_error,
            "ece":                   self.ece,
            "overconfident_rate":    self.overconfident_rate,
            "underconfident_rate":   self.underconfident_rate,
            "sample_count":          self.sample_count,
            "is_reliable":           self.is_reliable,
        }


@dataclass
class RecommendationImpactReport:
    """Effectiveness and follow-rate signals for recommendations."""
    tenant_id:              str
    computed_at:            datetime
    period_days:            int
    total_presented:        int
    total_followed:         int
    total_dismissed:        int
    total_effective:        int
    total_ineffective:      int
    follow_rate:            float
    effectiveness_rate:     float
    avg_time_to_decision_hours: float | None
    by_type:                dict[str, dict[str, float]]  # rec_type → {follow_rate, eff_rate}

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id":       self.tenant_id,
            "computed_at":     self.computed_at.isoformat(),
            "period_days":     self.period_days,
            "total_presented": self.total_presented,
            "follow_rate":     self.follow_rate,
            "effectiveness_rate": self.effectiveness_rate,
            "by_type":         self.by_type,
        }


@dataclass
class LearningSystemHealthReport:
    """End-to-end health of the learning pipeline."""
    tenant_id:              str
    computed_at:            datetime
    feedback_count_7d:      int
    feedback_count_30d:     int
    active_calibration:     str | None   # snapshot version
    prompt_versions_active: int
    pending_approvals:      int
    running_experiments:    int
    calibration_ece:        float | None
    fp_rate_30d:            float | None
    investigation_quality_avg: float | None
    alerts:                 list[str]       # health warnings

    @property
    def is_healthy(self) -> bool:
        return len(self.alerts) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id":               self.tenant_id,
            "computed_at":             self.computed_at.isoformat(),
            "feedback_count_7d":       self.feedback_count_7d,
            "feedback_count_30d":      self.feedback_count_30d,
            "active_calibration":      self.active_calibration,
            "prompt_versions_active":  self.prompt_versions_active,
            "pending_approvals":       self.pending_approvals,
            "running_experiments":     self.running_experiments,
            "calibration_ece":         self.calibration_ece,
            "fp_rate_30d":             self.fp_rate_30d,
            "investigation_quality_avg": self.investigation_quality_avg,
            "is_healthy":              self.is_healthy,
            "alerts":                  self.alerts,
        }


class LearningAnalyticsEngine:
    """
    Computes learning analytics reports from raw data sources.

    Methods are pure functions — they take structured data as input and
    return report objects. No state is stored.
    """

    # ── FP trend ───────────────────────────────────────────────────────────────

    def fp_trend_report(
        self,
        outcomes:       list[dict[str, Any]],  # {verdict, rule_code, recorded_at}
        tenant_id:      str,
        window_days:    int = 30,
    ) -> FalsePositiveTrendReport:
        now    = datetime.now(tz=UTC)
        cutoff = now - timedelta(days=window_days)
        prior_cutoff = cutoff - timedelta(days=window_days)

        current = [o for o in outcomes if o.get("recorded_at", now) >= cutoff]
        prior   = [
            o for o in outcomes
            if prior_cutoff <= o.get("recorded_at", now) < cutoff
        ]

        def _fp_rate(records: list[dict]) -> float:
            total = len(records)
            fps   = sum(1 for r in records if r.get("verdict") == "false_positive")
            return round(fps / total, 4) if total > 0 else 0.0

        fp_rate       = _fp_rate(current)
        fp_rate_prior = _fp_rate(prior) if prior else None
        delta         = (fp_rate - fp_rate_prior) if fp_rate_prior is not None else None

        # Per-rule FP rates
        by_rule: dict[str, dict] = {}
        for o in current:
            rule = o.get("rule_code", "unknown")
            by_rule.setdefault(rule, {"total": 0, "fp": 0})
            by_rule[rule]["total"] += 1
            if o.get("verdict") == "false_positive":
                by_rule[rule]["fp"] += 1
        rule_rates = {
            rule: round(v["fp"] / v["total"], 4)
            for rule, v in by_rule.items() if v["total"] > 0
        }

        # Trend direction
        if delta is None:
            trend = "stable"
        elif delta < -0.02:
            trend = "improving"
        elif delta > 0.02:
            trend = "degrading"
        else:
            trend = "stable"

        fp_count = sum(1 for o in current if o.get("verdict") == "false_positive")

        return FalsePositiveTrendReport(
            tenant_id      = tenant_id,
            computed_at    = now,
            window_days    = window_days,
            total_cases    = len(current),
            fp_count       = fp_count,
            fp_rate        = fp_rate,
            fp_rate_prior  = fp_rate_prior,
            delta_vs_prior = round(delta, 4) if delta is not None else None,
            by_rule_code   = rule_rates,
            trend          = trend,
        )

    # ── Investigation quality ──────────────────────────────────────────────────

    def investigation_quality_report(
        self,
        quality_scores: list[dict[str, Any]],  # {overall, reasoning, evidence, recommendation, hallucination, recorded_at}
        tenant_id:      str,
        period_days:    int = 30,
    ) -> InvestigationQualityReport:
        now    = datetime.now(tz=UTC)
        cutoff = now - timedelta(days=period_days)
        scores = [
            s for s in quality_scores
            if s.get("recorded_at", now) >= cutoff
        ]

        def _avg(field: str) -> float | None:
            vals = [s[field] for s in scores if s.get(field) is not None]
            return round(statistics.mean(vals), 4) if vals else None

        distribution: dict[str, int] = {str(i): 0 for i in range(1, 6)}
        for s in scores:
            overall = s.get("overall")
            if overall is not None:
                distribution[str(int(overall))] = distribution.get(str(int(overall)), 0) + 1

        hallucination_count = sum(1 for s in scores if s.get("hallucination_observed"))
        total = len(scores)
        low_quality = sum(1 for s in scores if (s.get("overall") or 5) <= 2)

        return InvestigationQualityReport(
            tenant_id                   = tenant_id,
            computed_at                 = now,
            period_days                 = period_days,
            total_rated                 = total,
            avg_reasoning_quality       = _avg("reasoning_quality"),
            avg_evidence_completeness   = _avg("evidence_completeness"),
            avg_recommendation_quality  = _avg("recommendation_quality"),
            avg_overall_score           = _avg("overall"),
            hallucination_rate          = round(hallucination_count / total, 4) if total else 0.0,
            quality_distribution        = distribution,
            low_quality_rate            = round(low_quality / total, 4) if total else 0.0,
        )

    # ── Calibration effectiveness ──────────────────────────────────────────────

    def calibration_effectiveness_report(
        self,
        calibration_samples: list[dict[str, Any]],  # {predicted_confidence, actual_outcome}
        tenant_id:           str,
        period_days:         int         = 30,
        calibration_version: str | None = None,
    ) -> CalibrationEffectivenessReport:
        now = datetime.now(tz=UTC)
        cutoff = now - timedelta(days=period_days)
        samples = [
            s for s in calibration_samples
            if s.get("recorded_at", now) >= cutoff
            and s.get("predicted_confidence") is not None
            and s.get("actual_outcome") is not None
        ]

        if not samples:
            return CalibrationEffectivenessReport(
                tenant_id=tenant_id, computed_at=now, period_days=period_days,
                calibration_version=calibration_version,
                avg_confidence_error=None, median_confidence_error=None,
                overconfident_rate=0.0, underconfident_rate=0.0,
                ece=None, sample_count=0, is_reliable=False,
            )

        errors = [
            abs(s["predicted_confidence"] - float(s["actual_outcome"]))
            for s in samples
        ]
        overconfident  = sum(
            1 for s in samples
            if s["predicted_confidence"] > float(s["actual_outcome"])
        )
        underconfident = sum(
            1 for s in samples
            if s["predicted_confidence"] < float(s["actual_outcome"])
        )
        n = len(samples)

        # ECE: bin-based calibration error
        ece = _compute_ece(samples, n_bins=10)

        return CalibrationEffectivenessReport(
            tenant_id              = tenant_id,
            computed_at            = now,
            period_days            = period_days,
            calibration_version    = calibration_version,
            avg_confidence_error   = round(statistics.mean(errors), 4),
            median_confidence_error= round(statistics.median(errors), 4),
            overconfident_rate     = round(overconfident / n, 4),
            underconfident_rate    = round(underconfident / n, 4),
            ece                    = round(ece, 4),
            sample_count           = n,
            is_reliable            = n >= 20,
        )

    # ── Recommendation impact ──────────────────────────────────────────────────

    def recommendation_impact_report(
        self,
        rec_events: list[dict[str, Any]],  # {type, event_type, recorded_at}
        tenant_id:  str,
        period_days: int = 30,
    ) -> RecommendationImpactReport:
        now    = datetime.now(tz=UTC)
        cutoff = now - timedelta(days=period_days)
        events = [
            e for e in rec_events
            if e.get("recorded_at", now) >= cutoff
        ]

        presented   = sum(1 for e in events if e.get("event_type") == "presented")
        followed    = sum(1 for e in events if e.get("event_type") == "followed")
        dismissed   = sum(1 for e in events if e.get("event_type") == "dismissed")
        effective   = sum(1 for e in events if e.get("event_type") == "effective")
        ineffective = sum(1 for e in events if e.get("event_type") == "ineffective")

        follow_rate       = round(followed / presented, 4) if presented else 0.0
        effectiveness_rate= round(effective / followed, 4) if followed else 0.0

        decision_times = [
            e["time_to_decision_hours"] for e in events
            if e.get("time_to_decision_hours") is not None
        ]
        avg_decision = round(statistics.mean(decision_times), 2) if decision_times else None

        # By type breakdown
        type_groups: dict[str, dict] = {}
        for e in events:
            t = e.get("rec_type", "unknown")
            g = type_groups.setdefault(t, {"presented": 0, "followed": 0, "effective": 0})
            et = e.get("event_type", "")
            if et == "presented":
                g["presented"] += 1
            elif et == "followed":
                g["followed"] += 1
            elif et == "effective":
                g["effective"] += 1

        by_type = {
            t: {
                "follow_rate":       round(g["followed"] / g["presented"], 4) if g["presented"] else 0.0,
                "effectiveness_rate":round(g["effective"] / g["followed"], 4) if g["followed"] else 0.0,
            }
            for t, g in type_groups.items()
        }

        return RecommendationImpactReport(
            tenant_id               = tenant_id,
            computed_at             = now,
            period_days             = period_days,
            total_presented         = presented,
            total_followed          = followed,
            total_dismissed         = dismissed,
            total_effective         = effective,
            total_ineffective       = ineffective,
            follow_rate             = follow_rate,
            effectiveness_rate      = effectiveness_rate,
            avg_time_to_decision_hours = avg_decision,
            by_type                 = by_type,
        )

    # ── Learning system health ─────────────────────────────────────────────────

    def learning_health_report(
        self,
        tenant_id:            str,
        feedback_count_7d:    int,
        feedback_count_30d:   int,
        active_calibration:   str | None,
        prompt_versions_active: int,
        pending_approvals:    int,
        running_experiments:  int,
        calibration_ece:      float | None,
        fp_rate_30d:          float | None,
        investigation_quality_avg: float | None,
    ) -> LearningSystemHealthReport:
        alerts: list[str] = []

        if feedback_count_7d < 5:
            alerts.append(f"Low feedback volume: {feedback_count_7d} submissions in last 7 days")
        if active_calibration is None:
            alerts.append("No active calibration snapshot — system using default thresholds")
        if calibration_ece is not None and calibration_ece > 0.15:
            alerts.append(f"High calibration ECE: {calibration_ece:.3f} (threshold: 0.15)")
        if fp_rate_30d is not None and fp_rate_30d > 0.35:
            alerts.append(f"High FP rate: {fp_rate_30d:.2%} in last 30 days")
        if investigation_quality_avg is not None and investigation_quality_avg < 3.0:
            alerts.append(f"Low investigation quality avg: {investigation_quality_avg:.2f}/5")
        if pending_approvals > 10:
            alerts.append(f"High pending approval backlog: {pending_approvals} requests")

        return LearningSystemHealthReport(
            tenant_id               = tenant_id,
            computed_at             = datetime.now(tz=UTC),
            feedback_count_7d       = feedback_count_7d,
            feedback_count_30d      = feedback_count_30d,
            active_calibration      = active_calibration,
            prompt_versions_active  = prompt_versions_active,
            pending_approvals       = pending_approvals,
            running_experiments     = running_experiments,
            calibration_ece         = calibration_ece,
            fp_rate_30d             = fp_rate_30d,
            investigation_quality_avg = investigation_quality_avg,
            alerts                  = alerts,
        )


# ── Statistical helpers ────────────────────────────────────────────────────────

def _compute_ece(
    samples: list[dict[str, Any]],
    n_bins:  int = 10,
) -> float:
    """Expected Calibration Error (ECE) from prediction samples."""
    bins = [[] for _ in range(n_bins)]
    for s in samples:
        conf = s["predicted_confidence"]
        idx  = min(int(conf * n_bins), n_bins - 1)
        bins[idx].append((conf, float(s["actual_outcome"])))

    ece   = 0.0
    total = len(samples)
    for b in bins:
        if not b:
            continue
        avg_conf    = statistics.mean(c for c, _ in b)
        avg_outcome = statistics.mean(o for _, o in b)
        ece        += (len(b) / total) * abs(avg_conf - avg_outcome)
    return ece
