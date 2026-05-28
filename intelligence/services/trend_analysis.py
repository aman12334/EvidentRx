"""
TrendAnalysisService — rolling window compliance trend computation.

Scans confirmed audit findings over configurable windows (30/60/90 day),
computes finding velocity and acceleration per entity+rule_code, persists
results to audit.compliance_trends, and returns structured trend summaries.

All data sourced from audit.audit_findings (deterministic engine output).
This service READS findings — it never creates or modifies them.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Rolling window sizes in days
WINDOW_SIZES = {
    "30d":  30,
    "60d":  60,
    "90d":  90,
}

TREND_DIRECTIONS = frozenset(["improving", "stable", "worsening", "critical"])

_SEVERITY_WEIGHT = {
    "critical": 4.0,
    "high":     2.0,
    "medium":   1.0,
    "low":      0.5,
}


@dataclass
class WindowStats:
    """Aggregated finding stats for a single window slice."""
    window_type:       str
    window_start:      date
    window_end:        date
    entity_id:         str
    entity_type:       str
    rule_code:         str
    finding_count:     int
    critical_count:    int
    high_count:        int
    financial_exposure: float
    risk_score:        float          # severity-weighted count


@dataclass
class TrendRecord:
    entity_id:          str
    entity_type:        str
    rule_code:          str
    window_type:        str
    window_start:       date
    window_end:         date
    finding_count:      int
    critical_count:     int
    high_count:         int
    financial_exposure: float
    risk_score:         float
    trend_direction:    str
    velocity:           float         # Δ count per day vs prior window
    acceleration:       float         # Δ velocity vs prior period
    prior_period_count: int
    monitoring_run_id:  str | None = None


@dataclass
class TrendSummary:
    """Top-level summary returned by analyse()."""
    as_of:              date
    window_type:        str
    total_entities:     int
    total_rule_codes:   int
    worsening_count:    int
    critical_count:     int
    stable_count:       int
    improving_count:    int
    records:            list[TrendRecord] = field(default_factory=list)
    top_worsening:      list[TrendRecord] = field(default_factory=list)


class TrendAnalysisService:
    """
    Computes rolling window compliance trends over confirmed findings.

    Usage::

        svc = TrendAnalysisService()
        summary = svc.analyse(session, window_type="30d")
        # Optionally persist:
        svc.persist(session, summary, monitoring_run_id="...")
    """

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def analyse(
        self,
        session: Session,
        window_type: str = "30d",
        as_of: date | None = None,
        entity_type_filter: str | None = None,
        monitoring_run_id: str | None = None,
    ) -> TrendSummary:
        """
        Compute trends for all entity+rule_code combinations over the
        given rolling window.  Returns a TrendSummary with per-record
        direction + velocity/acceleration.
        """
        if window_type not in WINDOW_SIZES:
            raise ValueError(f"window_type must be one of {list(WINDOW_SIZES)}")

        as_of       = as_of or date.today()
        window_days = WINDOW_SIZES[window_type]

        current_start = as_of - timedelta(days=window_days)
        prior_start   = current_start - timedelta(days=window_days)

        # Load current and prior window raw data
        current_rows = self._load_window(
            session, current_start, as_of, entity_type_filter
        )
        prior_rows = self._load_window(
            session, prior_start, current_start, entity_type_filter
        )

        # Index prior data: (entity_id, entity_type, rule_code) → WindowStats
        prior_index: dict[tuple, WindowStats] = {
            (r.entity_id, r.entity_type, r.rule_code): r
            for r in prior_rows
        }

        records: list[TrendRecord] = []
        for curr in current_rows:
            key = (curr.entity_id, curr.entity_type, curr.rule_code)
            prior = prior_index.get(key)

            prior_count = prior.finding_count if prior else 0
            velocity    = self._velocity(curr.finding_count, prior_count, window_days)

            # Acceleration: compare current velocity against prior velocity
            pre_prior_count = 0  # we only load 2 windows; treat pre-prior as 0
            prior_velocity  = self._velocity(prior_count, pre_prior_count, window_days)
            acceleration    = round(velocity - prior_velocity, 6)

            direction = self._classify_direction(
                curr.finding_count, prior_count, curr.critical_count
            )

            records.append(TrendRecord(
                entity_id=curr.entity_id,
                entity_type=curr.entity_type,
                rule_code=curr.rule_code,
                window_type=window_type,
                window_start=current_start,
                window_end=as_of,
                finding_count=curr.finding_count,
                critical_count=curr.critical_count,
                high_count=curr.high_count,
                financial_exposure=curr.financial_exposure,
                risk_score=curr.risk_score,
                trend_direction=direction,
                velocity=velocity,
                acceleration=acceleration,
                prior_period_count=prior_count,
                monitoring_run_id=monitoring_run_id,
            ))

        # Sort by risk_score descending
        records.sort(key=lambda r: r.risk_score, reverse=True)

        by_direction = _count_by_direction(records)
        top_worsening = [r for r in records if r.trend_direction in ("worsening", "critical")][:10]

        summary = TrendSummary(
            as_of=as_of,
            window_type=window_type,
            total_entities=len({(r.entity_id, r.entity_type) for r in records}),
            total_rule_codes=len({r.rule_code for r in records}),
            worsening_count=by_direction.get("worsening", 0),
            critical_count=by_direction.get("critical", 0),
            stable_count=by_direction.get("stable", 0),
            improving_count=by_direction.get("improving", 0),
            records=records,
            top_worsening=top_worsening,
        )

        logger.info(
            "Trend analysis complete window=%s as_of=%s records=%d worsening=%d critical=%d",
            window_type, as_of, len(records), summary.worsening_count, summary.critical_count,
        )
        return summary

    def analyse_all_windows(
        self,
        session: Session,
        as_of: date | None = None,
        monitoring_run_id: str | None = None,
    ) -> dict[str, TrendSummary]:
        """Run analysis for all three window sizes."""
        return {
            wt: self.analyse(session, window_type=wt, as_of=as_of,
                             monitoring_run_id=monitoring_run_id)
            for wt in WINDOW_SIZES
        }

    def persist(
        self,
        session: Session,
        summary: TrendSummary,
        monitoring_run_id: str | None = None,
    ) -> int:
        """
        Upserts TrendRecord rows to audit.compliance_trends.
        Returns the number of rows written.
        """
        run_id = monitoring_run_id or (summary.records[0].monitoring_run_id if summary.records else None)
        count = 0
        for r in summary.records:
            session.execute(text("""
                INSERT INTO audit.compliance_trends
                    (entity_id, entity_type, rule_code,
                     window_type, window_start, window_end,
                     finding_count, critical_count, high_count,
                     financial_exposure, risk_score,
                     trend_direction, velocity, acceleration,
                     prior_period_count, computed_at, monitoring_run_id)
                VALUES
                    (:entity_id, :entity_type, :rule_code,
                     :window_type, :window_start::date, :window_end::date,
                     :finding_count, :critical_count, :high_count,
                     :financial_exposure, :risk_score,
                     :trend_direction, :velocity, :acceleration,
                     :prior_period_count, NOW(), :monitoring_run_id)
                ON CONFLICT (entity_id, entity_type, rule_code, window_type, window_start)
                DO UPDATE SET
                    finding_count      = EXCLUDED.finding_count,
                    critical_count     = EXCLUDED.critical_count,
                    high_count         = EXCLUDED.high_count,
                    financial_exposure = EXCLUDED.financial_exposure,
                    risk_score         = EXCLUDED.risk_score,
                    trend_direction    = EXCLUDED.trend_direction,
                    velocity           = EXCLUDED.velocity,
                    acceleration       = EXCLUDED.acceleration,
                    prior_period_count = EXCLUDED.prior_period_count,
                    computed_at        = NOW(),
                    monitoring_run_id  = EXCLUDED.monitoring_run_id
            """), {
                "entity_id":          r.entity_id,
                "entity_type":        r.entity_type,
                "rule_code":          r.rule_code,
                "window_type":        r.window_type,
                "window_start":       r.window_start.isoformat(),
                "window_end":         r.window_end.isoformat(),
                "finding_count":      r.finding_count,
                "critical_count":     r.critical_count,
                "high_count":         r.high_count,
                "financial_exposure": r.financial_exposure,
                "risk_score":         r.risk_score,
                "trend_direction":    r.trend_direction,
                "velocity":           r.velocity,
                "acceleration":       r.acceleration,
                "prior_period_count": r.prior_period_count,
                "monitoring_run_id":  run_id,
            })
            count += 1
        logger.info("Persisted %d trend records (window=%s)", count, summary.window_type)
        return count

    def get_entity_trend_history(
        self,
        session: Session,
        entity_id: str,
        entity_type: str,
        rule_code: str | None = None,
        window_type: str = "30d",
        limit: int = 12,
    ) -> list[dict]:
        """
        Retrieves historical trend records for an entity (for time series).
        """
        filters = "WHERE ct.entity_id = :eid AND ct.entity_type = :etype AND ct.window_type = :wt"
        params: dict = {"eid": entity_id, "etype": entity_type, "wt": window_type}
        if rule_code:
            filters += " AND ct.rule_code = :rc"
            params["rc"] = rule_code

        rows = session.execute(text(f"""
            SELECT ct.rule_code, ct.window_start, ct.window_end,
                   ct.finding_count, ct.critical_count, ct.risk_score,
                   ct.trend_direction, ct.velocity, ct.acceleration
            FROM audit.compliance_trends ct
            {filters}
            ORDER BY ct.window_start DESC
            LIMIT :lim
        """), {**params, "lim": limit}).mappings().fetchall()

        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _load_window(
        self,
        session: Session,
        window_start: date,
        window_end: date,
        entity_type_filter: str | None,
    ) -> list[WindowStats]:
        """
        Aggregates findings by covered_entity + rule_code within a date window.
        Financial exposure is approximated from evidence_payload if available.
        """
        type_clause = ""
        params: dict = {
            "ws": window_start.isoformat(),
            "we": window_end.isoformat(),
        }
        if entity_type_filter:
            type_clause = "AND :etype = 'covered_entity'"  # future: extend for other types
            params["etype"] = entity_type_filter

        rows = session.execute(text(f"""
            SELECT
                af.covered_entity_id::text                   AS entity_id,
                'covered_entity'                             AS entity_type,
                af.rule_code,
                COUNT(*)                                     AS finding_count,
                COUNT(*) FILTER (WHERE af.severity = 'critical') AS critical_count,
                COUNT(*) FILTER (WHERE af.severity = 'high')     AS high_count,
                COALESCE(SUM(
                    CASE
                        WHEN (af.evidence_payload->>'financial_exposure') IS NOT NULL
                        THEN (af.evidence_payload->>'financial_exposure')::float
                        ELSE 0
                    END
                ), 0)                                        AS financial_exposure,
                SUM(
                    CASE af.severity
                        WHEN 'critical' THEN 4.0
                        WHEN 'high'     THEN 2.0
                        WHEN 'medium'   THEN 1.0
                        WHEN 'low'      THEN 0.5
                        ELSE 1.0
                    END
                )                                            AS risk_score
            FROM audit.audit_findings af
            WHERE af.created_at::date >= :ws::date
              AND af.created_at::date <  :we::date
              {type_clause}
            GROUP BY af.covered_entity_id, af.rule_code
        """), params).mappings().fetchall()

        results = []
        for r in rows:
            results.append(WindowStats(
                window_type="",             # filled by caller
                window_start=window_start,
                window_end=window_end,
                entity_id=r["entity_id"],
                entity_type=r["entity_type"],
                rule_code=r["rule_code"],
                finding_count=int(r["finding_count"]),
                critical_count=int(r["critical_count"]),
                high_count=int(r["high_count"]),
                financial_exposure=float(r["financial_exposure"]),
                risk_score=round(float(r["risk_score"]), 4),
            ))
        return results

    @staticmethod
    def _velocity(current: int, prior: int, window_days: int) -> float:
        """Δ findings per day relative to prior window."""
        delta = current - prior
        return round(delta / max(window_days, 1), 6)

    @staticmethod
    def _classify_direction(
        current: int,
        prior: int,
        critical_count: int,
    ) -> str:
        if critical_count >= 3:
            return "critical"
        if current == 0 and prior == 0:
            return "stable"
        if prior == 0:
            return "worsening" if current > 0 else "stable"
        ratio = current / prior
        if ratio >= 1.25:
            return "critical" if current >= 10 else "worsening"
        if ratio <= 0.75:
            return "improving"
        return "stable"


# ------------------------------------------------------------------ #
# Module-level helpers                                                 #
# ------------------------------------------------------------------ #

def _count_by_direction(records: list[TrendRecord]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in records:
        counts[r.trend_direction] = counts.get(r.trend_direction, 0) + 1
    return counts
