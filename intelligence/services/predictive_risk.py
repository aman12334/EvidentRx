"""
PredictiveRiskService — composite risk scoring and trajectory forecasting.

Computes a composite risk score for each covered entity by combining:
  1. Current finding velocity  (from TrendAnalysisService)
  2. Severity-weighted exposure (from confirmed findings)
  3. Escalation history        (from investigation_cases)
  4. Trend acceleration        (2nd derivative of finding rate)

Scores are persisted to audit.entity_risk_scores.
Forecast horizon: 30 days (extrapolates velocity).

No LLMs involved — all arithmetic is deterministic.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from intelligence.services.trend_analysis import TrendAnalysisService, TrendRecord

logger = logging.getLogger(__name__)

# Component weights for composite score (must sum to 1.0)
_WEIGHT_VELOCITY     = 0.35
_WEIGHT_EXPOSURE     = 0.25
_WEIGHT_ESCALATION   = 0.25
_WEIGHT_ACCELERATION = 0.15

# Escalation score thresholds
_ESCALATION_LEVELS = {
    "escalated":     1.0,
    "investigating":  0.6,
    "triaged":        0.3,
    "open":           0.1,
}

RISK_TIERS = {
    "critical":  (0.80, 1.00),
    "high":      (0.60, 0.80),
    "medium":    (0.40, 0.60),
    "low":       (0.00, 0.40),
}


@dataclass
class EntityRiskScore:
    entity_id:              str
    entity_type:            str
    score_date:             date
    composite_score:        float          # 0.0 – 1.0
    risk_tier:              str            # critical / high / medium / low
    finding_velocity:       float          # Δ findings/day (30d window)
    exposure_trajectory:    float          # projected 30d financial exposure
    escalation_probability: float          # 0.0 – 1.0
    trend_direction:        str
    score_components:       dict           # {"velocity": .., "exposure": .., ...}
    monitoring_run_id:      str | None = None


@dataclass
class RiskForecast:
    """30-day forward-looking risk projection for an entity."""
    entity_id:           str
    entity_type:         str
    as_of:               date
    forecast_date:       date             # as_of + 30 days
    current_score:       float
    projected_score:     float
    projected_findings:  int              # extrapolated count
    confidence:          float            # 0.0 – 1.0 (lower if sparse data)
    risk_tier_change:    str              # "stable" | "worsening" | "improving"
    narrative:           str              # short deterministic explanation


@dataclass
class RiskScoringReport:
    as_of:               date
    window_type:         str
    total_entities:      int
    critical_count:      int
    high_count:          int
    medium_count:        int
    low_count:           int
    scores:              list[EntityRiskScore] = field(default_factory=list)
    forecasts:           list[RiskForecast]    = field(default_factory=list)

    def top_risk(self, n: int = 10) -> list[EntityRiskScore]:
        return sorted(self.scores, key=lambda s: s.composite_score, reverse=True)[:n]


class PredictiveRiskService:
    """
    Computes composite risk scores and 30-day forecasts per covered entity.

    Usage::

        svc = PredictiveRiskService()
        report = svc.score(session)
        svc.persist(session, report)
    """

    def __init__(self) -> None:
        self._trend_svc = TrendAnalysisService()

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def score(
        self,
        session: Session,
        as_of: date | None = None,
        window_type: str = "30d",
        monitoring_run_id: str | None = None,
    ) -> RiskScoringReport:
        """
        Computes composite risk scores for all active covered entities.
        """
        as_of = as_of or date.today()

        # Trend data feeds velocity + acceleration components
        trend_summary = self._trend_svc.analyse(
            session, window_type=window_type, as_of=as_of,
            monitoring_run_id=monitoring_run_id,
        )

        # Load escalation status per entity
        escalation_map = self._load_escalation_status(session)

        # Load exposure maxima for normalization
        exposure_max = self._load_exposure_ceiling(session, as_of, window_type)

        # Velocity ceiling for normalization (findings/day)
        velocity_max = max(
            (abs(r.velocity) for r in trend_summary.records), default=1.0
        ) or 1.0

        # Group trend records by entity
        by_entity: dict[tuple, list[TrendRecord]] = {}
        for r in trend_summary.records:
            key = (r.entity_id, r.entity_type)
            by_entity.setdefault(key, []).append(r)

        scores: list[EntityRiskScore] = []
        forecasts: list[RiskForecast] = []

        for (entity_id, entity_type), records in by_entity.items():
            score_obj = self._compute_score(
                entity_id=entity_id,
                entity_type=entity_type,
                records=records,
                escalation_score=escalation_map.get(entity_id, 0.0),
                velocity_max=velocity_max,
                exposure_max=exposure_max,
                as_of=as_of,
                monitoring_run_id=monitoring_run_id,
            )
            scores.append(score_obj)

            forecast = self._build_forecast(score_obj, records)
            forecasts.append(forecast)

        scores.sort(key=lambda s: s.composite_score, reverse=True)
        forecasts.sort(key=lambda f: f.projected_score, reverse=True)

        tier_counts = _count_tiers(scores)
        report = RiskScoringReport(
            as_of=as_of,
            window_type=window_type,
            total_entities=len(scores),
            critical_count=tier_counts.get("critical", 0),
            high_count=tier_counts.get("high", 0),
            medium_count=tier_counts.get("medium", 0),
            low_count=tier_counts.get("low", 0),
            scores=scores,
            forecasts=forecasts,
        )

        logger.info(
            "Risk scoring complete entities=%d critical=%d high=%d",
            len(scores), report.critical_count, report.high_count,
        )
        return report

    def score_entity(
        self,
        session: Session,
        entity_id: str,
        entity_type: str = "covered_entity",
        as_of: date | None = None,
        window_type: str = "30d",
    ) -> EntityRiskScore | None:
        """Score a single entity.  Returns None if no findings found."""
        report = self.score(session, as_of=as_of, window_type=window_type)
        for s in report.scores:
            if s.entity_id == entity_id and s.entity_type == entity_type:
                return s
        return None

    def persist(
        self,
        session: Session,
        report: RiskScoringReport,
        monitoring_run_id: str | None = None,
    ) -> int:
        """
        Upserts entity risk scores to audit.entity_risk_scores.
        Returns number of rows written.
        """
        import json
        count = 0
        for s in report.scores:
            session.execute(text("""
                INSERT INTO audit.entity_risk_scores
                    (entity_id, entity_type, score_date,
                     composite_score, finding_velocity, exposure_trajectory,
                     escalation_probability, trend_direction, score_components,
                     computed_at)
                VALUES
                    (:eid, :etype, :sdate::date,
                     :composite, :velocity, :exposure,
                     :escalation, :direction, :components::jsonb,
                     NOW())
                ON CONFLICT (entity_id, entity_type, score_date)
                DO UPDATE SET
                    composite_score        = EXCLUDED.composite_score,
                    finding_velocity       = EXCLUDED.finding_velocity,
                    exposure_trajectory    = EXCLUDED.exposure_trajectory,
                    escalation_probability = EXCLUDED.escalation_probability,
                    trend_direction        = EXCLUDED.trend_direction,
                    score_components       = EXCLUDED.score_components,
                    computed_at            = NOW()
            """), {
                "eid":        s.entity_id,
                "etype":      s.entity_type,
                "sdate":      s.score_date.isoformat(),
                "composite":  s.composite_score,
                "velocity":   s.finding_velocity,
                "exposure":   s.exposure_trajectory,
                "escalation": s.escalation_probability,
                "direction":  s.trend_direction,
                "components": json.dumps(s.score_components),
            })
            count += 1
        logger.info("Persisted %d entity risk scores", count)
        return count

    def load_history(
        self,
        session: Session,
        entity_id: str,
        entity_type: str = "covered_entity",
        days: int = 90,
    ) -> list[dict]:
        """Returns historical daily scores for an entity (for trend charts)."""
        since = (date.today() - timedelta(days=days)).isoformat()
        rows = session.execute(text("""
            SELECT score_date, composite_score, finding_velocity,
                   exposure_trajectory, escalation_probability,
                   trend_direction, score_components
            FROM audit.entity_risk_scores
            WHERE entity_id = :eid AND entity_type = :etype
              AND score_date >= :since::date
            ORDER BY score_date DESC
        """), {"eid": entity_id, "etype": entity_type, "since": since}).mappings().fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _compute_score(
        self,
        entity_id: str,
        entity_type: str,
        records: list[TrendRecord],
        escalation_score: float,
        velocity_max: float,
        exposure_max: float,
        as_of: date,
        monitoring_run_id: str | None,
    ) -> EntityRiskScore:
        # Velocity: take worst (highest) rule velocity
        max_velocity = max((r.velocity for r in records), default=0.0)
        velocity_norm = min(1.0, max(0.0, max_velocity) / velocity_max)

        # Exposure: total across rules, normalized
        total_exposure = sum(r.financial_exposure for r in records)
        exposure_norm = min(1.0, total_exposure / max(exposure_max, 1.0))

        # Acceleration: highest positive acceleration = worsening fastest
        max_accel = max((r.acceleration for r in records), default=0.0)
        accel_norm = min(1.0, max(0.0, max_accel) * 10)  # scale: 0.1 Δ/day² → 1.0

        # Composite
        composite = (
            _WEIGHT_VELOCITY     * velocity_norm +
            _WEIGHT_EXPOSURE     * exposure_norm +
            _WEIGHT_ESCALATION   * escalation_score +
            _WEIGHT_ACCELERATION * accel_norm
        )
        composite = round(min(1.0, max(0.0, composite)), 4)

        # Pick worst trend direction across all rules
        direction = _aggregate_direction([r.trend_direction for r in records])

        return EntityRiskScore(
            entity_id=entity_id,
            entity_type=entity_type,
            score_date=as_of,
            composite_score=composite,
            risk_tier=_tier(composite),
            finding_velocity=round(max_velocity, 6),
            exposure_trajectory=round(total_exposure, 2),
            escalation_probability=round(escalation_score, 4),
            trend_direction=direction,
            score_components={
                "velocity_norm":     round(velocity_norm, 4),
                "exposure_norm":     round(exposure_norm, 4),
                "escalation_score":  round(escalation_score, 4),
                "acceleration_norm": round(accel_norm, 4),
            },
            monitoring_run_id=monitoring_run_id,
        )

    @staticmethod
    def _build_forecast(
        score: EntityRiskScore,
        records: list[TrendRecord],
    ) -> RiskForecast:
        horizon = 30  # days
        velocity = score.finding_velocity

        # Extrapolate: current findings + velocity * horizon
        current_findings = sum(r.finding_count for r in records)
        projected_findings = max(0, int(current_findings + velocity * horizon))

        # Project score: add velocity contribution over horizon
        velocity_delta = min(0.3, max(-0.3, velocity * 0.01 * horizon))
        projected = min(1.0, max(0.0, score.composite_score + velocity_delta))

        current_tier = _tier(score.composite_score)
        projected_tier = _tier(projected)

        if projected_tier == current_tier:
            tier_change = "stable"
        elif projected > score.composite_score:
            tier_change = "worsening"
        else:
            tier_change = "improving"

        # Confidence: lower if few records or very high acceleration
        data_points = len(records)
        confidence = min(1.0, 0.4 + data_points * 0.1)
        if abs(score.finding_velocity) > 0.5:
            confidence = min(confidence, 0.6)  # high velocity → uncertain forecast

        # Deterministic narrative
        narrative = _forecast_narrative(
            score, projected, projected_findings, tier_change, horizon
        )

        return RiskForecast(
            entity_id=score.entity_id,
            entity_type=score.entity_type,
            as_of=score.score_date,
            forecast_date=score.score_date + timedelta(days=horizon),
            current_score=score.composite_score,
            projected_score=round(projected, 4),
            projected_findings=projected_findings,
            confidence=round(confidence, 4),
            risk_tier_change=tier_change,
            narrative=narrative,
        )

    def _load_escalation_status(self, session: Session) -> dict[str, float]:
        """
        Returns escalation score per covered entity based on highest-severity
        active case status.
        """
        rows = session.execute(text("""
            SELECT ic.covered_entity_id::text AS entity_id,
                   MAX(CASE ic.status
                       WHEN 'escalated'     THEN 1.0
                       WHEN 'investigating' THEN 0.6
                       WHEN 'triaged'       THEN 0.3
                       WHEN 'open'          THEN 0.1
                       ELSE 0.0
                   END) AS escalation_score
            FROM audit.investigation_cases ic
            WHERE ic.status IN ('open', 'triaged', 'investigating', 'escalated')
            GROUP BY ic.covered_entity_id
        """)).mappings().fetchall()
        return {r["entity_id"]: float(r["escalation_score"]) for r in rows}

    def _load_exposure_ceiling(
        self,
        session: Session,
        as_of: date,
        window_type: str,
    ) -> float:
        """
        Maximum total financial exposure across all entities in this window,
        used to normalize the exposure component to [0, 1].
        """
        from intelligence.services.trend_analysis import WINDOW_SIZES
        window_days = WINDOW_SIZES.get(window_type, 30)
        window_start = (as_of - timedelta(days=window_days)).isoformat()

        row = session.execute(text("""
            SELECT COALESCE(MAX(entity_exposure), 1.0) AS max_exposure
            FROM (
                SELECT af.covered_entity_id,
                       SUM(
                           CASE
                               WHEN (af.evidence_payload->>'financial_exposure') IS NOT NULL
                               THEN (af.evidence_payload->>'financial_exposure')::float
                               ELSE 0
                           END
                       ) AS entity_exposure
                FROM audit.audit_findings af
                WHERE af.created_at::date >= :ws::date
                GROUP BY af.covered_entity_id
            ) sub
        """), {"ws": window_start}).mappings().fetchone()
        return float(row["max_exposure"]) if row else 1.0


# ------------------------------------------------------------------ #
# Module helpers                                                        #
# ------------------------------------------------------------------ #

def _tier(score: float) -> str:
    for tier, (lo, hi) in RISK_TIERS.items():
        if lo <= score < hi:
            return tier
    return "low"


def _aggregate_direction(directions: list[str]) -> str:
    priority = ["critical", "worsening", "stable", "improving"]
    for p in priority:
        if p in directions:
            return p
    return "stable"


def _count_tiers(scores: list[EntityRiskScore]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for s in scores:
        counts[s.risk_tier] = counts.get(s.risk_tier, 0) + 1
    return counts


def _forecast_narrative(
    score: EntityRiskScore,
    projected: float,
    projected_findings: int,
    tier_change: str,
    horizon: int,
) -> str:
    direction_map = {
        "worsening": f"Compliance posture is expected to worsen over the next {horizon} days.",
        "improving": f"Compliance posture is projected to improve over the next {horizon} days.",
        "stable":    f"Compliance posture is projected to remain stable over the next {horizon} days.",
    }
    base = direction_map.get(tier_change, "")
    velocity_note = ""
    if score.finding_velocity > 0.1:
        velocity_note = (
            f" Current finding rate of {score.finding_velocity:.3f}/day "
            f"projects approximately {projected_findings} additional findings."
        )
    escalation_note = ""
    if score.escalation_probability >= 0.6:
        escalation_note = " Active escalated case increases short-term risk."
    return base + velocity_note + escalation_note
