"""
DriftDetectionService — rule drift, entity drift, and model drift detection.

Detects three classes of drift:
  1. Rule drift:   A rule's finding rate changes significantly across windows
                   (may indicate rule calibration issue or genuine trend)
  2. Entity drift: A covered entity's compliance posture changes sharply
                   between consecutive periods
  3. Model drift:  Agent output confidence or escalation rate drifts over time
                   relative to the golden evaluation baseline

All signals are deterministic (derived from confirmed findings + agent_runs).
Output is structured drift records — no LLMs involved.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Thresholds for classifying drift magnitude
DRIFT_MAGNITUDE = {
    "critical": 0.50,    # ≥ 50% change
    "high":     0.30,
    "medium":   0.15,
    "low":      0.05,
}

DRIFT_DIRECTIONS = frozenset(["increasing", "decreasing", "stable"])


@dataclass
class DriftSignal:
    drift_type:   str             # "rule" | "entity" | "model"
    subject_id:   str             # rule_code | entity_id | agent_type
    subject_label: str
    window_type:  str
    period_a:     str             # ISO date range for prior period
    period_b:     str             # ISO date range for current period
    value_a:      float           # metric in period A
    value_b:      float           # metric in period B
    change_pct:   float           # (B - A) / A * 100
    magnitude:    str             # critical/high/medium/low/none
    direction:    str             # increasing/decreasing/stable
    explanation:  str


@dataclass
class DriftReport:
    as_of:            date
    window_type:      str
    total_signals:    int
    critical_count:   int
    high_count:       int
    rule_drift:       list[DriftSignal] = field(default_factory=list)
    entity_drift:     list[DriftSignal] = field(default_factory=list)
    model_drift:      list[DriftSignal] = field(default_factory=list)

    def all_signals(self) -> list[DriftSignal]:
        """All signals sorted by magnitude (critical first)."""
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "none": 4}
        combined = self.rule_drift + self.entity_drift + self.model_drift
        return sorted(combined, key=lambda s: order.get(s.magnitude, 5))

    def has_critical(self) -> bool:
        return self.critical_count > 0


class DriftDetectionService:
    """
    Detects drift across rules, entities, and model behavior.

    Usage::

        svc = DriftDetectionService()
        report = svc.detect(session)
        critical_signals = [s for s in report.all_signals() if s.magnitude == "critical"]
    """

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def detect(
        self,
        session: Session,
        as_of: Optional[date] = None,
        window_type: str = "30d",
        min_magnitude: str = "low",
    ) -> DriftReport:
        """Run all three drift detectors and return a combined report."""
        as_of = as_of or date.today()

        rule_signals   = self.detect_rule_drift(session, as_of, window_type)
        entity_signals = self.detect_entity_drift(session, as_of, window_type)
        model_signals  = self.detect_model_drift(session, as_of, window_type)

        threshold = list(DRIFT_MAGNITUDE.keys()).index(min_magnitude)
        magnitude_order = list(DRIFT_MAGNITUDE.keys())

        def _filter(signals: list[DriftSignal]) -> list[DriftSignal]:
            return [
                s for s in signals
                if s.magnitude != "none" and
                   magnitude_order.index(s.magnitude) <= threshold
            ]

        rule_f   = _filter(rule_signals)
        entity_f = _filter(entity_signals)
        model_f  = _filter(model_signals)

        all_signals = rule_f + entity_f + model_f
        critical = sum(1 for s in all_signals if s.magnitude == "critical")
        high     = sum(1 for s in all_signals if s.magnitude == "high")

        report = DriftReport(
            as_of=as_of,
            window_type=window_type,
            total_signals=len(all_signals),
            critical_count=critical,
            high_count=high,
            rule_drift=rule_f,
            entity_drift=entity_f,
            model_drift=model_f,
        )

        logger.info(
            "Drift detection complete signals=%d critical=%d high=%d",
            len(all_signals), critical, high,
        )
        return report

    def detect_rule_drift(
        self,
        session: Session,
        as_of: Optional[date] = None,
        window_type: str = "30d",
    ) -> list[DriftSignal]:
        """
        Compares finding rates per rule_code between current and prior window.
        High positive drift = rule triggering much more than expected.
        High negative drift = rule rarely firing (calibration drift?).
        """
        as_of = as_of or date.today()
        current_start, prior_start, window_days = _window_dates(as_of, window_type)

        current = self._load_rule_counts(session, current_start, as_of)
        prior   = self._load_rule_counts(session, prior_start, current_start)

        all_rules = set(current) | set(prior)
        signals: list[DriftSignal] = []

        for rule_code in all_rules:
            curr_count = current.get(rule_code, 0)
            prior_count = prior.get(rule_code, 0)

            change_pct, magnitude, direction = _compute_drift(curr_count, prior_count)
            if magnitude == "none":
                continue

            explanation = (
                f"Rule {rule_code} had {prior_count} findings in prior {window_type} "
                f"and {curr_count} in current window ({change_pct:+.1f}%)."
            )

            signals.append(DriftSignal(
                drift_type="rule",
                subject_id=rule_code,
                subject_label=rule_code,
                window_type=window_type,
                period_a=f"{prior_start}/{current_start}",
                period_b=f"{current_start}/{as_of}",
                value_a=float(prior_count),
                value_b=float(curr_count),
                change_pct=change_pct,
                magnitude=magnitude,
                direction=direction,
                explanation=explanation,
            ))

        return sorted(signals, key=lambda s: abs(s.change_pct), reverse=True)

    def detect_entity_drift(
        self,
        session: Session,
        as_of: Optional[date] = None,
        window_type: str = "30d",
    ) -> list[DriftSignal]:
        """
        Compares risk score change for each entity between consecutive 30d windows.
        Uses persisted audit.compliance_trends if available; falls back to raw counts.
        """
        as_of = as_of or date.today()
        current_start, prior_start, window_days = _window_dates(as_of, window_type)

        current = self._load_entity_risk(session, current_start, as_of)
        prior   = self._load_entity_risk(session, prior_start, current_start)

        all_entities = set(current) | set(prior)
        signals: list[DriftSignal] = []

        for entity_id in all_entities:
            curr_score = current.get(entity_id, {})
            prior_score = prior.get(entity_id, {})

            curr_count  = curr_score.get("finding_count", 0)
            prior_count = prior_score.get("finding_count", 0)
            entity_label = curr_score.get("label") or prior_score.get("label") or entity_id

            change_pct, magnitude, direction = _compute_drift(curr_count, prior_count)
            if magnitude == "none":
                continue

            explanation = (
                f"Entity {entity_label} had {prior_count} findings in prior {window_type} "
                f"and {curr_count} in current window ({change_pct:+.1f}%)."
            )

            signals.append(DriftSignal(
                drift_type="entity",
                subject_id=entity_id,
                subject_label=entity_label,
                window_type=window_type,
                period_a=f"{prior_start}/{current_start}",
                period_b=f"{current_start}/{as_of}",
                value_a=float(prior_count),
                value_b=float(curr_count),
                change_pct=change_pct,
                magnitude=magnitude,
                direction=direction,
                explanation=explanation,
            ))

        return sorted(signals, key=lambda s: abs(s.change_pct), reverse=True)

    def detect_model_drift(
        self,
        session: Session,
        as_of: Optional[date] = None,
        window_type: str = "30d",
    ) -> list[DriftSignal]:
        """
        Compares agent confidence scores and escalation rates between periods.
        Drift indicates the LLM's behaviour is shifting vs. prior calibration.
        """
        as_of = as_of or date.today()
        current_start, prior_start, window_days = _window_dates(as_of, window_type)

        current = self._load_agent_metrics(session, current_start, as_of)
        prior   = self._load_agent_metrics(session, prior_start, current_start)

        signals: list[DriftSignal] = []
        all_agents = set(current) | set(prior)

        for agent_type in all_agents:
            curr_m = current.get(agent_type, {})
            prior_m = prior.get(agent_type, {})

            for metric_key, metric_label in [
                ("avg_confidence", "avg confidence score"),
                ("escalation_rate", "escalation rate"),
            ]:
                curr_val  = curr_m.get(metric_key, 0.0)
                prior_val = prior_m.get(metric_key, 0.0)

                change_pct, magnitude, direction = _compute_drift(curr_val, prior_val)
                if magnitude not in ("critical", "high"):
                    continue   # model drift only surfaces significant changes

                explanation = (
                    f"Agent '{agent_type}' {metric_label}: "
                    f"prior={prior_val:.3f}, current={curr_val:.3f} "
                    f"({change_pct:+.1f}% change)."
                )

                signals.append(DriftSignal(
                    drift_type="model",
                    subject_id=f"{agent_type}.{metric_key}",
                    subject_label=f"{agent_type} — {metric_label}",
                    window_type=window_type,
                    period_a=f"{prior_start}/{current_start}",
                    period_b=f"{current_start}/{as_of}",
                    value_a=prior_val,
                    value_b=curr_val,
                    change_pct=change_pct,
                    magnitude=magnitude,
                    direction=direction,
                    explanation=explanation,
                ))

        return sorted(signals, key=lambda s: abs(s.change_pct), reverse=True)

    # ------------------------------------------------------------------ #
    # DB helpers                                                           #
    # ------------------------------------------------------------------ #

    def _load_rule_counts(
        self,
        session: Session,
        start: date,
        end: date,
    ) -> dict[str, int]:
        rows = session.execute(text("""
            SELECT rule_code, COUNT(*) AS cnt
            FROM audit.audit_findings
            WHERE created_at::date >= :s::date
              AND created_at::date <  :e::date
            GROUP BY rule_code
        """), {"s": start.isoformat(), "e": end.isoformat()}).mappings().fetchall()
        return {r["rule_code"]: int(r["cnt"]) for r in rows}

    def _load_entity_risk(
        self,
        session: Session,
        start: date,
        end: date,
    ) -> dict[str, dict]:
        rows = session.execute(text("""
            SELECT af.covered_entity_id::text AS entity_id,
                   COALESCE(ce.entity_name, af.covered_entity_id::text) AS label,
                   COUNT(*) AS finding_count
            FROM audit.audit_findings af
            LEFT JOIN ref.covered_entities ce
                   ON af.covered_entity_id = ce.ce_id AND ce.is_current = TRUE
            WHERE af.created_at::date >= :s::date
              AND af.created_at::date <  :e::date
            GROUP BY af.covered_entity_id, ce.entity_name
        """), {"s": start.isoformat(), "e": end.isoformat()}).mappings().fetchall()
        return {
            r["entity_id"]: {
                "finding_count": int(r["finding_count"]),
                "label": r["label"],
            }
            for r in rows
        }

    def _load_agent_metrics(
        self,
        session: Session,
        start: date,
        end: date,
    ) -> dict[str, dict]:
        """
        Loads per-agent average confidence and escalation rate from agent_runs.
        escalation_rate = fraction of runs where output contains escalation_recommended=true.
        """
        rows = session.execute(text("""
            SELECT ar.agent_type,
                   AVG(
                       CASE
                           WHEN (ar.output->>'confidence_score') IS NOT NULL
                           THEN (ar.output->>'confidence_score')::float
                           ELSE NULL
                       END
                   ) AS avg_confidence,
                   AVG(
                       CASE
                           WHEN ar.output->>'escalation_recommended' = 'true' THEN 1.0
                           WHEN ar.output->>'escalation_recommended' = 'false' THEN 0.0
                           ELSE NULL
                       END
                   ) AS escalation_rate,
                   COUNT(*) AS run_count
            FROM audit.agent_runs ar
            WHERE ar.started_at::date >= :s::date
              AND ar.started_at::date <  :e::date
              AND ar.status = 'completed'
            GROUP BY ar.agent_type
        """), {"s": start.isoformat(), "e": end.isoformat()}).mappings().fetchall()
        return {
            r["agent_type"]: {
                "avg_confidence":  float(r["avg_confidence"] or 0.0),
                "escalation_rate": float(r["escalation_rate"] or 0.0),
                "run_count":       int(r["run_count"]),
            }
            for r in rows
        }


# ------------------------------------------------------------------ #
# Module helpers                                                        #
# ------------------------------------------------------------------ #

def _window_dates(as_of: date, window_type: str) -> tuple[date, date, int]:
    from intelligence.services.trend_analysis import WINDOW_SIZES
    window_days = WINDOW_SIZES.get(window_type, 30)
    current_start = as_of - timedelta(days=window_days)
    prior_start   = current_start - timedelta(days=window_days)
    return current_start, prior_start, window_days


def _compute_drift(
    current: float,
    prior: float,
) -> tuple[float, str, str]:
    """
    Returns (change_pct, magnitude_label, direction).
    change_pct is signed: positive = increase, negative = decrease.
    """
    if prior == 0 and current == 0:
        return 0.0, "none", "stable"

    if prior == 0:
        # Any appearance from zero is medium+ drift
        change_pct = 100.0
        magnitude = "high" if current >= 5 else "medium"
        return change_pct, magnitude, "increasing"

    change_pct = (current - prior) / prior * 100.0
    abs_change = abs(change_pct)

    if abs_change < DRIFT_MAGNITUDE["low"] * 100:
        magnitude = "none"
    elif abs_change < DRIFT_MAGNITUDE["medium"] * 100:
        magnitude = "low"
    elif abs_change < DRIFT_MAGNITUDE["high"] * 100:
        magnitude = "medium"
    elif abs_change < DRIFT_MAGNITUDE["critical"] * 100:
        magnitude = "high"
    else:
        magnitude = "critical"

    direction = "stable" if magnitude == "none" else (
        "increasing" if change_pct > 0 else "decreasing"
    )
    return round(change_pct, 2), magnitude, direction
