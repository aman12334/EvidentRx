"""
EvaluationMemory — analyst override history and confidence calibration data.

Tracks analyst overrides (human corrections to agent outputs), false positive
patterns, and per-agent confidence calibration data.  Agents can query this
memory to understand where their prior outputs were corrected — enabling
calibration-aware reasoning without modifying deterministic finding logic.

This module READS override history — it never creates or modifies findings,
rules, or agent outputs directly.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class OverrideSummary:
    """Summary of analyst corrections for a given scope."""
    total_overrides:          int
    false_positive_count:     int       # agent flagged, analyst cleared
    false_negative_count:     int       # agent missed, analyst added
    confidence_adjustments:   int       # analyst adjusted confidence score
    top_override_agents:      list[str] # agents most frequently overridden
    top_rule_codes_corrected: list[str] # rules most frequently corrected
    lookback_days:            int


@dataclass
class CalibrationData:
    """Per-agent confidence calibration summary."""
    agent_type:           str
    n_outputs:            int
    mean_confidence:      float          # average reported confidence
    override_rate:        float          # fraction of outputs overridden
    false_positive_rate:  float
    false_negative_rate:  float
    calibration_bias:     float          # positive = over-confident
    lookback_days:        int


@dataclass
class OverrideRecord:
    override_id:    str
    finding_id:     str
    case_id:        str
    analyst_id:     str
    override_type:  str          # false_positive | false_negative | confidence_adjustment | other
    original_value: str
    override_value: str
    rationale:      str
    created_at:     str


class EvaluationMemory:
    """
    Loads analyst override history and calibration data.

    Usage::

        mem = EvaluationMemory()
        summary = mem.override_summary(session, lookback_days=90)
        calibration = mem.calibration_for_agent(session, "risk_prioritization")
    """

    def override_summary(
        self,
        session: Session,
        lookback_days: int = 90,
        agent_type: Optional[str] = None,
    ) -> OverrideSummary:
        """
        Returns aggregate override statistics for the given lookback window.
        """
        since = (date.today() - timedelta(days=lookback_days)).isoformat()
        params: dict = {"since": since}

        rows = session.execute(text("""
            SELECT ao.override_type, COUNT(*) AS cnt
            FROM audit.analyst_overrides ao
            WHERE ao.created_at::date >= :since::date
            GROUP BY ao.override_type
        """), params).mappings().fetchall()

        type_counts = {r["override_type"]: int(r["cnt"]) for r in rows}

        # Top overridden agents (by case/finding linkage — join via finding)
        agent_rows = session.execute(text("""
            SELECT ar.agent_type, COUNT(*) AS cnt
            FROM audit.analyst_overrides ao
            JOIN audit.agent_runs ar ON ar.case_id = ao.case_id::uuid
            WHERE ao.created_at::date >= :since::date
            GROUP BY ar.agent_type
            ORDER BY cnt DESC
            LIMIT 5
        """), params).mappings().fetchall()
        top_agents = [r["agent_type"] for r in agent_rows]

        # Top corrected rule codes
        rule_rows = session.execute(text("""
            SELECT af.rule_code, COUNT(*) AS cnt
            FROM audit.analyst_overrides ao
            JOIN audit.audit_findings af ON ao.finding_id = af.finding_id::uuid
            WHERE ao.created_at::date >= :since::date
            GROUP BY af.rule_code
            ORDER BY cnt DESC
            LIMIT 5
        """), params).mappings().fetchall()
        top_rules = [r["rule_code"] for r in rule_rows]

        total = sum(type_counts.values())
        return OverrideSummary(
            total_overrides=total,
            false_positive_count=type_counts.get("false_positive", 0),
            false_negative_count=type_counts.get("false_negative", 0),
            confidence_adjustments=type_counts.get("confidence_adjustment", 0),
            top_override_agents=top_agents,
            top_rule_codes_corrected=top_rules,
            lookback_days=lookback_days,
        )

    def calibration_for_agent(
        self,
        session: Session,
        agent_type: str,
        lookback_days: int = 90,
    ) -> CalibrationData:
        """
        Computes confidence calibration metrics for a specific agent type.
        """
        since = (date.today() - timedelta(days=lookback_days)).isoformat()

        run_row = session.execute(text("""
            SELECT
                COUNT(*) AS n_outputs,
                AVG(
                    CASE WHEN (ar.output->>'confidence_score') IS NOT NULL
                    THEN (ar.output->>'confidence_score')::float
                    ELSE NULL END
                ) AS mean_confidence
            FROM audit.agent_runs ar
            WHERE ar.agent_type = :at
              AND ar.status = 'completed'
              AND ar.started_at::date >= :since::date
        """), {"at": agent_type, "since": since}).mappings().fetchone()

        n_outputs = int(run_row["n_outputs"] or 0)
        mean_conf = float(run_row["mean_confidence"] or 0.5)

        # Override rate for this agent's cases
        override_row = session.execute(text("""
            SELECT
                COUNT(*) AS total_overrides,
                COUNT(*) FILTER (WHERE ao.override_type = 'false_positive') AS fp,
                COUNT(*) FILTER (WHERE ao.override_type = 'false_negative') AS fn
            FROM audit.analyst_overrides ao
            JOIN audit.agent_runs ar ON ar.case_id = ao.case_id::uuid
            WHERE ar.agent_type = :at
              AND ao.created_at::date >= :since::date
        """), {"at": agent_type, "since": since}).mappings().fetchone()

        total_ov = int(override_row["total_overrides"] or 0)
        fp_count = int(override_row["fp"] or 0)
        fn_count = int(override_row["fn"] or 0)

        override_rate = total_ov / max(n_outputs, 1)
        fp_rate = fp_count / max(n_outputs, 1)
        fn_rate = fn_count / max(n_outputs, 1)

        # Calibration bias: if agent is over-confident (high confidence + high override rate)
        # bias > 0 = over-confident, bias < 0 = under-confident
        calibration_bias = round(mean_conf - (1.0 - override_rate), 4)

        return CalibrationData(
            agent_type=agent_type,
            n_outputs=n_outputs,
            mean_confidence=round(mean_conf, 4),
            override_rate=round(override_rate, 4),
            false_positive_rate=round(fp_rate, 4),
            false_negative_rate=round(fn_rate, 4),
            calibration_bias=calibration_bias,
            lookback_days=lookback_days,
        )

    def recent_overrides(
        self,
        session: Session,
        case_id: Optional[str] = None,
        limit: int = 20,
    ) -> list[OverrideRecord]:
        """Returns recent override records, optionally filtered by case."""
        filters = ""
        params: dict = {"lim": limit}
        if case_id:
            filters = "WHERE ao.case_id = :cid::uuid"
            params["cid"] = case_id

        rows = session.execute(text(f"""
            SELECT ao.override_id, ao.finding_id, ao.case_id,
                   ao.analyst_id, ao.override_type,
                   ao.original_value, ao.override_value,
                   ao.rationale, ao.created_at
            FROM audit.analyst_overrides ao
            {filters}
            ORDER BY ao.created_at DESC
            LIMIT :lim
        """), params).mappings().fetchall()

        return [
            OverrideRecord(
                override_id=str(r["override_id"]),
                finding_id=str(r["finding_id"]),
                case_id=str(r["case_id"]),
                analyst_id=r["analyst_id"],
                override_type=r["override_type"],
                original_value=r["original_value"],
                override_value=r["override_value"],
                rationale=r["rationale"] or "",
                created_at=str(r["created_at"]),
            )
            for r in rows
        ]

    def to_agent_context(
        self,
        calibration: CalibrationData,
        summary: OverrideSummary,
    ) -> dict:
        """
        Packages calibration + override data for injection into agent context.
        """
        return {
            "calibration_context": {
                "agent_type":        calibration.agent_type,
                "n_prior_outputs":   calibration.n_outputs,
                "mean_confidence":   calibration.mean_confidence,
                "override_rate":     calibration.override_rate,
                "fp_rate":           calibration.false_positive_rate,
                "fn_rate":           calibration.false_negative_rate,
                "calibration_bias":  calibration.calibration_bias,
                "note": (
                    "Your prior outputs have been corrected by analysts "
                    f"{calibration.override_rate:.1%} of the time. "
                    "Adjust your confidence accordingly."
                    if calibration.override_rate > 0.1 else
                    "Your prior outputs have a low analyst override rate."
                ),
            },
            "top_corrected_rules":  summary.top_rule_codes_corrected,
        }
