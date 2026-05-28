"""
Policy synchronisation scheduler.

Maintains a registry of monitored regulatory sources and their polling
schedules. On each tick, produces a list of SyncJob records for sources
that are due for re-check. A background worker claims jobs and passes
them to the RegulatoryIngestionPipeline.

Sync frequencies
────────────────
  HOURLY   — payer bulletins, emergency guidance
  DAILY    — HRSA operational notices, CMS updates
  WEEKLY   — Medicaid policy bulletins, Federal Register
  MONTHLY  — audit guidance, compliance manuals
  MANUAL   — triggered on demand only
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime    import datetime, timedelta, timezone
from enum        import Enum
from typing      import Any, Optional

from regulatory.ingestion.models import DocumentFormat, DocumentSource, PolicyDomain

log = logging.getLogger("evidentrx.regulatory.ingestion.scheduler")


class SyncFrequency(str, Enum):
    HOURLY  = "hourly"
    DAILY   = "daily"
    WEEKLY  = "weekly"
    MONTHLY = "monthly"
    MANUAL  = "manual"


_FREQ_DELTAS: dict[SyncFrequency, timedelta] = {
    SyncFrequency.HOURLY:  timedelta(hours=1),
    SyncFrequency.DAILY:   timedelta(days=1),
    SyncFrequency.WEEKLY:  timedelta(weeks=1),
    SyncFrequency.MONTHLY: timedelta(days=30),
    SyncFrequency.MANUAL:  timedelta(days=36500),   # never auto-triggers
}


@dataclass
class MonitoredSource:
    """A regulatory source endpoint under scheduled monitoring."""
    source_id:    str
    tenant_id:    str
    name:         str
    source:       DocumentSource
    fmt:          DocumentFormat
    domains:      list[PolicyDomain]
    fetch_url:    str
    frequency:    SyncFrequency
    active:       bool           = True
    created_by:   str            = "system"
    created_at:   datetime       = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    last_synced_at: Optional[datetime] = None
    next_sync_at: Optional[datetime]   = None
    sync_count:   int            = 0
    error_count:  int            = 0
    metadata:     dict[str, Any] = field(default_factory=dict)

    @property
    def is_due(self) -> bool:
        if not self.active or self.frequency == SyncFrequency.MANUAL:
            return False
        if self.next_sync_at is None:
            return True
        return datetime.now(tz=timezone.utc) >= self.next_sync_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id":     self.source_id,
            "tenant_id":     self.tenant_id,
            "name":          self.name,
            "source":        self.source.value,
            "fetch_url":     self.fetch_url,
            "frequency":     self.frequency.value,
            "active":        self.active,
            "last_synced_at":self.last_synced_at.isoformat() if self.last_synced_at else None,
            "next_sync_at":  self.next_sync_at.isoformat() if self.next_sync_at else None,
            "sync_count":    self.sync_count,
            "error_count":   self.error_count,
        }


@dataclass
class SyncJob:
    """A queued synchronisation task produced by the scheduler."""
    job_id:    str
    source_id: str
    tenant_id: str
    fetch_url: str
    source:    DocumentSource
    fmt:       DocumentFormat
    domains:   list[PolicyDomain]
    queued_at: datetime
    status:    str     = "queued"   # "queued"|"running"|"done"|"failed"
    result_doc_id: Optional[str] = None
    error:     Optional[str]     = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id":        self.job_id,
            "source_id":     self.source_id,
            "tenant_id":     self.tenant_id,
            "fetch_url":     self.fetch_url,
            "queued_at":     self.queued_at.isoformat(),
            "status":        self.status,
            "result_doc_id": self.result_doc_id,
        }


class PolicySyncScheduler:
    """
    Registry of monitored sources and job queue for sync workers.

    call collect_due_jobs() on each scheduler tick; a background worker
    processes the returned jobs and calls mark_job_done() / mark_job_failed().
    """

    def __init__(self) -> None:
        self._sources: dict[str, MonitoredSource] = {}
        self._jobs:    dict[str, SyncJob]         = {}

    # ── Source management ──────────────────────────────────────────────────────

    def register_source(
        self,
        tenant_id:  str,
        name:       str,
        source:     DocumentSource,
        fmt:        DocumentFormat,
        domains:    list[PolicyDomain],
        fetch_url:  str,
        frequency:  SyncFrequency,
        created_by: str = "system",
        metadata:   Optional[dict[str, Any]] = None,
    ) -> MonitoredSource:
        ms = MonitoredSource(
            source_id   = str(uuid.uuid4()),
            tenant_id   = tenant_id,
            name        = name,
            source      = source,
            fmt         = fmt,
            domains     = domains,
            fetch_url   = fetch_url,
            frequency   = frequency,
            created_by  = created_by,
            next_sync_at = datetime.now(tz=timezone.utc),   # due immediately
            metadata    = metadata or {},
        )
        self._sources[ms.source_id] = ms
        log.info(
            "PolicySyncScheduler: registered source '%s' (%s, %s)",
            name, source.value, frequency.value,
        )
        return ms

    def deactivate_source(self, source_id: str) -> None:
        src = self._sources.get(source_id)
        if src:
            src.active = False

    def list_sources(
        self,
        tenant_id:   str,
        active_only: bool = True,
    ) -> list[MonitoredSource]:
        return [
            s for s in self._sources.values()
            if s.tenant_id == tenant_id
            and (not active_only or s.active)
        ]

    # ── Job production ─────────────────────────────────────────────────────────

    def collect_due_jobs(self) -> list[SyncJob]:
        """
        Scan all active sources for ones due for sync and produce SyncJobs.

        Advances next_sync_at immediately to prevent double-firing.
        """
        now  = datetime.now(tz=timezone.utc)
        jobs: list[SyncJob] = []

        for src in self._sources.values():
            if not src.is_due:
                continue
            job = SyncJob(
                job_id    = str(uuid.uuid4()),
                source_id = src.source_id,
                tenant_id = src.tenant_id,
                fetch_url = src.fetch_url,
                source    = src.source,
                fmt       = src.fmt,
                domains   = src.domains,
                queued_at = now,
            )
            self._jobs[job.job_id] = job
            jobs.append(job)

            # Advance schedule
            delta             = _FREQ_DELTAS[src.frequency]
            src.last_synced_at = now
            src.next_sync_at  = now + delta
            src.sync_count   += 1

        return jobs

    def trigger_manual(self, source_id: str) -> Optional[SyncJob]:
        """Force an immediate sync job for a specific source."""
        src = self._sources.get(source_id)
        if src is None or not src.active:
            return None
        job = SyncJob(
            job_id    = str(uuid.uuid4()),
            source_id = src.source_id,
            tenant_id = src.tenant_id,
            fetch_url = src.fetch_url,
            source    = src.source,
            fmt       = src.fmt,
            domains   = src.domains,
            queued_at = datetime.now(tz=timezone.utc),
        )
        self._jobs[job.job_id] = job
        log.info("PolicySyncScheduler: manual sync triggered for source %s", source_id[:8])
        return job

    def mark_job_done(self, job_id: str, doc_id: str) -> None:
        job = self._jobs.get(job_id)
        if job:
            job.status        = "done"
            job.result_doc_id = doc_id

    def mark_job_failed(self, job_id: str, error: str) -> None:
        job = self._jobs.get(job_id)
        if job:
            job.status = "failed"
            job.error  = error
            src = self._sources.get(job.source_id)
            if src:
                src.error_count += 1

    def pending_jobs(self) -> list[SyncJob]:
        return [j for j in self._jobs.values() if j.status == "queued"]

    def job_history(self, limit: int = 50) -> list[SyncJob]:
        jobs = sorted(self._jobs.values(), key=lambda j: j.queued_at, reverse=True)
        return jobs[:limit]


# ── Singleton ──────────────────────────────────────────────────────────────────

_scheduler: Optional[PolicySyncScheduler] = None


def get_policy_scheduler() -> PolicySyncScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = PolicySyncScheduler()
    return _scheduler
