"""
Monitoring scheduler — manages when monitoring runs execute.

This is a lightweight, non-blocking scheduler that records the NEXT run time
in the DB and provides utilities for determining whether a run is due.
It does NOT spawn background threads or use APScheduler — runs are triggered
externally (cron, run_monitor.py CLI, or CI pipeline).

The canonical trigger is: ``run_monitor.py`` invoked by a system cron job.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# Default cadence: daily monitoring run
DEFAULT_CADENCE_HOURS = 24


@dataclass
class ScheduleState:
    last_run_id:         Optional[str]
    last_run_at:         Optional[datetime]
    last_run_status:     Optional[str]
    next_run_due:        datetime
    is_due:              bool
    cadence_hours:       int


class MonitoringScheduler:
    """
    Determines whether a monitoring run is due and records scheduling metadata.

    The scheduler works by inspecting the monitoring_runs table — no external
    state file or scheduler daemon required.
    """

    def __init__(self, cadence_hours: int = DEFAULT_CADENCE_HOURS) -> None:
        self.cadence_hours = cadence_hours

    def check(self, session: Session) -> ScheduleState:
        """
        Returns the current schedule state: last run, next due time, is_due flag.
        """
        row = session.execute(text("""
            SELECT run_id, completed_at, status
            FROM audit.monitoring_runs
            WHERE status IN ('completed', 'failed')
            ORDER BY completed_at DESC
            LIMIT 1
        """)).mappings().fetchone()

        if not row or not row["completed_at"]:
            # No prior run — always due
            next_due = datetime.utcnow()
            return ScheduleState(
                last_run_id=None,
                last_run_at=None,
                last_run_status=None,
                next_run_due=next_due,
                is_due=True,
                cadence_hours=self.cadence_hours,
            )

        last_at = row["completed_at"]
        if isinstance(last_at, str):
            last_at = datetime.fromisoformat(last_at)

        next_due = last_at + timedelta(hours=self.cadence_hours)
        is_due   = datetime.utcnow() >= next_due

        return ScheduleState(
            last_run_id=str(row["run_id"]),
            last_run_at=last_at,
            last_run_status=row["status"],
            next_run_due=next_due,
            is_due=is_due,
            cadence_hours=self.cadence_hours,
        )

    def is_run_in_progress(self, session: Session) -> bool:
        """Returns True if a monitoring run is currently marked as running."""
        row = session.execute(text("""
            SELECT COUNT(*) AS cnt
            FROM audit.monitoring_runs
            WHERE status = 'running'
              AND started_at > NOW() - INTERVAL '2 hours'
        """)).mappings().fetchone()
        return bool(row and int(row["cnt"]) > 0)

    def list_recent_runs(self, session: Session, limit: int = 10) -> list[dict]:
        """Returns recent monitoring run summaries."""
        rows = session.execute(text("""
            SELECT run_id, run_type, status,
                   findings_evaluated, new_findings,
                   drifts_detected, correlations_found,
                   started_at, completed_at, error_message
            FROM audit.monitoring_runs
            ORDER BY started_at DESC
            LIMIT :lim
        """), {"lim": limit}).mappings().fetchall()
        return [dict(r) for r in rows]

    def print_status(self, session: Session) -> None:
        state = self.check(session)
        print(f"\n{'='*50}")
        print("MONITORING SCHEDULE STATUS")
        print(f"{'='*50}")
        if state.last_run_at:
            print(f"  Last run:    {state.last_run_at.strftime('%Y-%m-%d %H:%M UTC')}  [{state.last_run_status}]")
            print(f"  Last run ID: {state.last_run_id}")
        else:
            print("  Last run:    Never")
        print(f"  Cadence:     Every {state.cadence_hours}h")
        print(f"  Next due:    {state.next_run_due.strftime('%Y-%m-%d %H:%M UTC')}")
        print(f"  Status:      {'DUE NOW' if state.is_due else 'Not due'}")
        print(f"{'='*50}\n")
