"""
LongitudinalReplay — replays evaluation runs across time to track agent
performance trends.

Queries evaluation_runs from the DB and computes pass rate trajectories,
identifying whether agent quality is improving or degrading over time.
Entirely deterministic — reads persisted eval results, no LLM calls.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class EvalDataPoint:
    eval_date:     date
    eval_type:     str
    passed:        int
    total_checks:  int
    failed_checks: int
    pass_rate:     float


@dataclass
class LongitudinalReport:
    """Time-series view of evaluation performance."""
    eval_type:       str
    lookback_days:   int
    data_points:     list[EvalDataPoint]
    trend_direction: str                    # improving / stable / degrading
    mean_pass_rate:  float
    latest_pass_rate: float | None
    earliest_pass_rate: float | None
    pass_rate_delta: float | None        # latest - earliest


class LongitudinalReplay:
    """
    Analyses evaluation run history to identify performance trends.

    Usage::

        replay = LongitudinalReplay()
        report = replay.analyse(session, eval_type="golden_suite")
        print(report.trend_direction, report.pass_rate_delta)
    """

    def analyse(
        self,
        session: Session,
        eval_type: str = "golden_suite",
        lookback_days: int = 90,
    ) -> LongitudinalReport:
        since = (date.today() - timedelta(days=lookback_days)).isoformat()

        rows = session.execute(text("""
            SELECT eval_type, started_at::date AS eval_date,
                   passed, total_checks, failed_checks
            FROM audit.evaluation_runs
            WHERE eval_type = :etype
              AND status = 'completed'
              AND started_at::date >= :since::date
            ORDER BY started_at ASC
        """), {"etype": eval_type, "since": since}).mappings().fetchall()

        data_points: list[EvalDataPoint] = []
        for r in rows:
            total = int(r["total_checks"] or 0)
            passed = int(r["passed"] or 0)
            failed = int(r["failed_checks"] or 0)
            pass_rate = passed / max(total, 1)
            data_points.append(EvalDataPoint(
                eval_date=r["eval_date"],
                eval_type=r["eval_type"],
                passed=passed,
                total_checks=total,
                failed_checks=failed,
                pass_rate=round(pass_rate, 4),
            ))

        if not data_points:
            return LongitudinalReport(
                eval_type=eval_type,
                lookback_days=lookback_days,
                data_points=[],
                trend_direction="stable",
                mean_pass_rate=0.0,
                latest_pass_rate=None,
                earliest_pass_rate=None,
                pass_rate_delta=None,
            )

        pass_rates = [d.pass_rate for d in data_points]
        mean_pass_rate = round(sum(pass_rates) / len(pass_rates), 4)
        earliest = data_points[0].pass_rate
        latest   = data_points[-1].pass_rate
        delta    = round(latest - earliest, 4)

        if delta >= 0.05:
            direction = "improving"
        elif delta <= -0.05:
            direction = "degrading"
        else:
            direction = "stable"

        return LongitudinalReport(
            eval_type=eval_type,
            lookback_days=lookback_days,
            data_points=data_points,
            trend_direction=direction,
            mean_pass_rate=mean_pass_rate,
            latest_pass_rate=latest,
            earliest_pass_rate=earliest,
            pass_rate_delta=delta,
        )

    def summary_table(self, report: LongitudinalReport) -> str:
        """Returns an ASCII table for CLI display."""
        lines = [
            f"Longitudinal Evaluation — {report.eval_type}",
            f"Lookback: {report.lookback_days}d  |  "
            f"Trend: {report.trend_direction}  |  "
            f"Δ pass rate: {report.pass_rate_delta:+.2%}" if report.pass_rate_delta is not None else "",
            "",
            f"{'Date':<12} {'Passed':>7} {'Total':>7} {'Failed':>7} {'Pass Rate':>10}",
            "-" * 45,
        ]
        for d in report.data_points:
            lines.append(
                f"{str(d.eval_date):<12} {d.passed:>7} {d.total_checks:>7} "
                f"{d.failed_checks:>7} {d.pass_rate:>9.1%}"
            )
        return "\n".join(lines)
