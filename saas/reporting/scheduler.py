"""
Report scheduling — recurring report generation for tenants.

Tenants configure scheduled reports (weekly compliance summary, monthly
executive dashboard) that are generated automatically at the end of each
period. The scheduler maintains a registry of schedules and produces
due-report records that a background worker can process.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import Enum
from typing import Any

from saas.reporting.reports import ReportType

log = logging.getLogger("evidentrx.saas.reporting.scheduler")


class ScheduleFrequency(str, Enum):
    DAILY   = "daily"
    WEEKLY  = "weekly"
    MONTHLY = "monthly"


class ScheduleStatus(str, Enum):
    ACTIVE   = "active"
    PAUSED   = "paused"
    ARCHIVED = "archived"


@dataclass
class ReportSchedule:
    """
    A recurring report schedule for a tenant.

    next_run_at is recalculated after each successful generation.
    """
    schedule_id:  str
    tenant_id:    str
    report_type:  ReportType
    title:        str
    frequency:    ScheduleFrequency
    status:       ScheduleStatus
    created_by:   str
    org_id:       str | None
    recipients:   list[str]         = field(default_factory=list)   # user_ids
    export_format: str              = "pdf"   # "pdf" | "csv" | "json"
    created_at:   datetime          = field(default_factory=lambda: datetime.now(tz=UTC))
    next_run_at:  datetime | None = None
    last_run_at:  datetime | None = None
    run_count:    int               = 0
    metadata:     dict[str, Any]    = field(default_factory=dict)

    @property
    def is_due(self) -> bool:
        if self.status != ScheduleStatus.ACTIVE:
            return False
        if self.next_run_at is None:
            return True
        return datetime.now(tz=UTC) >= self.next_run_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "schedule_id":  self.schedule_id,
            "tenant_id":    self.tenant_id,
            "report_type":  self.report_type.value,
            "title":        self.title,
            "frequency":    self.frequency.value,
            "status":       self.status.value,
            "org_id":       self.org_id,
            "recipients":   self.recipients,
            "export_format":self.export_format,
            "next_run_at":  self.next_run_at.isoformat() if self.next_run_at else None,
            "last_run_at":  self.last_run_at.isoformat() if self.last_run_at else None,
            "run_count":    self.run_count,
        }


@dataclass
class ScheduledReportRun:
    """A queued or completed report generation triggered by a schedule."""
    run_id:       str
    schedule_id:  str
    tenant_id:    str
    report_type:  ReportType
    period_from:  str
    period_to:    str
    org_id:       str | None
    queued_at:    datetime
    started_at:   datetime | None = None
    completed_at: datetime | None = None
    status:       str                = "queued"   # "queued"|"running"|"done"|"failed"
    error:        str | None      = None
    report_id:    str | None      = None       # set on completion

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id":      self.run_id,
            "schedule_id": self.schedule_id,
            "tenant_id":   self.tenant_id,
            "report_type": self.report_type.value,
            "period_from": self.period_from,
            "period_to":   self.period_to,
            "queued_at":   self.queued_at.isoformat(),
            "status":      self.status,
            "report_id":   self.report_id,
        }


class ReportScheduler:
    """
    Manages recurring report schedules and produces due-run records.

    The scheduler does NOT generate reports itself — it yields
    ScheduledReportRun records that a background worker claims and
    passes to ReportEngine. This separation keeps the scheduler
    side-effect-free and easy to test.
    """

    def __init__(self) -> None:
        self._schedules: dict[str, ReportSchedule] = {}
        self._runs:      dict[str, ScheduledReportRun] = {}

    # ── Schedule management ────────────────────────────────────────────────────

    def create_schedule(
        self,
        tenant_id:     str,
        report_type:   ReportType,
        title:         str,
        frequency:     ScheduleFrequency,
        created_by:    str,
        org_id:        str | None   = None,
        recipients:    list[str] | None = None,
        export_format: str             = "pdf",
        metadata:      dict[str, Any] | None = None,
    ) -> ReportSchedule:
        schedule = ReportSchedule(
            schedule_id   = str(uuid.uuid4()),
            tenant_id     = tenant_id,
            report_type   = report_type,
            title         = title,
            frequency     = frequency,
            status        = ScheduleStatus.ACTIVE,
            created_by    = created_by,
            org_id        = org_id,
            recipients    = recipients or [],
            export_format = export_format,
            next_run_at   = self._next_run(frequency),
            metadata      = metadata or {},
        )
        self._schedules[schedule.schedule_id] = schedule
        log.info(
            "ReportScheduler: created %s schedule '%s' for tenant %s",
            frequency.value, title, tenant_id[:8],
        )
        return schedule

    def pause(self, tenant_id: str, schedule_id: str) -> ReportSchedule:
        s = self._get_owned(tenant_id, schedule_id)
        s.status = ScheduleStatus.PAUSED
        return s

    def resume(self, tenant_id: str, schedule_id: str) -> ReportSchedule:
        s = self._get_owned(tenant_id, schedule_id)
        s.status     = ScheduleStatus.ACTIVE
        s.next_run_at = self._next_run(s.frequency)
        return s

    def archive(self, tenant_id: str, schedule_id: str) -> ReportSchedule:
        s = self._get_owned(tenant_id, schedule_id)
        s.status = ScheduleStatus.ARCHIVED
        return s

    def list_schedules(
        self,
        tenant_id: str,
        status:    ScheduleStatus | None = None,
    ) -> list[ReportSchedule]:
        return [
            s for s in self._schedules.values()
            if s.tenant_id == tenant_id
            and (status is None or s.status == status)
        ]

    # ── Due-run generation ─────────────────────────────────────────────────────

    def collect_due_runs(self) -> list[ScheduledReportRun]:
        """
        Scan all active schedules for ones that are due and create run records.

        Advances next_run_at immediately to prevent double-firing.
        """
        now    = datetime.now(tz=UTC)
        today  = now.date()
        queued: list[ScheduledReportRun] = []

        for sched in self._schedules.values():
            if not sched.is_due:
                continue
            period_from, period_to = self._period_bounds(sched.frequency, today)
            run = ScheduledReportRun(
                run_id      = str(uuid.uuid4()),
                schedule_id = sched.schedule_id,
                tenant_id   = sched.tenant_id,
                report_type = sched.report_type,
                period_from = period_from,
                period_to   = period_to,
                org_id      = sched.org_id,
                queued_at   = now,
            )
            self._runs[run.run_id] = run
            queued.append(run)
            # Advance schedule
            sched.last_run_at = now
            sched.next_run_at = self._next_run(sched.frequency)
            sched.run_count  += 1

        return queued

    def mark_run_complete(
        self,
        run_id:    str,
        report_id: str,
    ) -> ScheduledReportRun:
        run = self._runs.get(run_id)
        if run is None:
            raise SchedulerError(f"Run {run_id} not found")
        run.status       = "done"
        run.completed_at = datetime.now(tz=UTC)
        run.report_id    = report_id
        return run

    def mark_run_failed(self, run_id: str, error: str) -> ScheduledReportRun:
        run = self._runs.get(run_id)
        if run is None:
            raise SchedulerError(f"Run {run_id} not found")
        run.status       = "failed"
        run.completed_at = datetime.now(tz=UTC)
        run.error        = error
        return run

    def list_runs(
        self,
        tenant_id:   str,
        schedule_id: str | None = None,
        limit:       int           = 50,
    ) -> list[ScheduledReportRun]:
        runs = [
            r for r in self._runs.values()
            if r.tenant_id == tenant_id
            and (schedule_id is None or r.schedule_id == schedule_id)
        ]
        runs.sort(key=lambda r: r.queued_at, reverse=True)
        return runs[:limit]

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _next_run(freq: ScheduleFrequency) -> datetime:
        now = datetime.now(tz=UTC)
        if freq == ScheduleFrequency.DAILY:
            return now + timedelta(days=1)
        if freq == ScheduleFrequency.WEEKLY:
            return now + timedelta(weeks=1)
        # MONTHLY — approximate 30 days
        return now + timedelta(days=30)

    @staticmethod
    def _period_bounds(
        freq: ScheduleFrequency,
        today: date,
    ) -> tuple[str, str]:
        """Return (period_from, period_to) as ISO-8601 date strings."""
        if freq == ScheduleFrequency.DAILY:
            yesterday = today - timedelta(days=1)
            return str(yesterday), str(yesterday)
        if freq == ScheduleFrequency.WEEKLY:
            end   = today - timedelta(days=1)
            start = end - timedelta(days=6)
            return str(start), str(end)
        # MONTHLY: previous calendar month
        first_of_this = today.replace(day=1)
        last_of_prev  = first_of_this - timedelta(days=1)
        first_of_prev = last_of_prev.replace(day=1)
        return str(first_of_prev), str(last_of_prev)

    def _get_owned(self, tenant_id: str, schedule_id: str) -> ReportSchedule:
        s = self._schedules.get(schedule_id)
        if s is None or s.tenant_id != tenant_id:
            raise SchedulerError(f"Schedule {schedule_id} not found")
        return s


# ── Exceptions ─────────────────────────────────────────────────────────────────

class SchedulerError(Exception):
    pass


# ── Singleton ──────────────────────────────────────────────────────────────────

_scheduler: ReportScheduler | None = None


def get_report_scheduler() -> ReportScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = ReportScheduler()
    return _scheduler
