"""
Tenant archival and health monitoring.

When a tenant is no longer active (trial expired, contract ended,
voluntary deactivation), its data must be preserved for regulatory
purposes but made inaccessible to normal operations. The archival
service manages this transition and the eventual purge timeline.

HealthTracker monitors tenant operational signals and flags tenants
that may need intervention (quota exhaustion, repeated API failures,
suspended billing, etc.).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

log = logging.getLogger("evidentrx.saas.lifecycle.archival")


class ArchivalReason(str, Enum):
    CONTRACT_ENDED     = "contract_ended"
    TRIAL_EXPIRED      = "trial_expired"
    NON_PAYMENT        = "non_payment"
    VOLUNTARY_EXIT     = "voluntary_exit"
    POLICY_VIOLATION   = "policy_violation"
    INACTIVE_LONG_TERM = "inactive_long_term"


class ArchivalStatus(str, Enum):
    SCHEDULED  = "scheduled"
    IN_PROGRESS = "in_progress"
    ARCHIVED   = "archived"
    PURGE_SCHEDULED = "purge_scheduled"
    PURGED     = "purged"
    RESTORED   = "restored"


# Regulatory minimum retention after archival before purge is allowed (days)
_MIN_RETENTION_DAYS = 2555   # ≈ 7 years (HIPAA compliance)


@dataclass
class ArchivalPolicy:
    """
    Defines how a tenant's data should be archived and eventually purged.

    retention_days is counted from the archival date, not the contract
    end date. It must meet or exceed _MIN_RETENTION_DAYS.
    """
    policy_id:      str
    tenant_id:      str
    reason:         ArchivalReason
    retention_days: int            = _MIN_RETENTION_DAYS
    legal_hold:     bool           = False    # prevents purge even after retention
    created_by:     str            = "system"
    created_at:     datetime       = field(default_factory=lambda: datetime.now(tz=UTC))
    notes:          str            = ""

    def __post_init__(self) -> None:
        if self.retention_days < _MIN_RETENTION_DAYS:
            raise ValueError(
                f"Retention must be at least {_MIN_RETENTION_DAYS} days "
                f"(7 years); got {self.retention_days}"
            )

    def purge_eligible_after(self, archived_at: datetime) -> datetime:
        return archived_at + timedelta(days=self.retention_days)

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id":      self.policy_id,
            "tenant_id":      self.tenant_id,
            "reason":         self.reason.value,
            "retention_days": self.retention_days,
            "legal_hold":     self.legal_hold,
            "created_by":     self.created_by,
            "notes":          self.notes,
        }


@dataclass
class ArchivalRecord:
    """
    Tracks the archival lifecycle for a tenant.
    """
    record_id:    str
    tenant_id:    str
    policy_id:    str
    status:       ArchivalStatus
    initiated_by: str
    initiated_at: datetime
    archived_at:  datetime | None  = None
    purge_eligible_at: datetime | None = None
    purged_at:    datetime | None  = None
    restored_at:  datetime | None  = None
    storage_location: str | None  = None    # S3 bucket/prefix or similar
    metadata:     dict[str, Any]     = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id":          self.record_id,
            "tenant_id":          self.tenant_id,
            "policy_id":          self.policy_id,
            "status":             self.status.value,
            "initiated_by":       self.initiated_by,
            "initiated_at":       self.initiated_at.isoformat(),
            "archived_at":        self.archived_at.isoformat() if self.archived_at else None,
            "purge_eligible_at":  self.purge_eligible_at.isoformat() if self.purge_eligible_at else None,
            "purged_at":          self.purged_at.isoformat() if self.purged_at else None,
            "storage_location":   self.storage_location,
        }


class TenantArchivalService:
    """
    Manages tenant archival lifecycle from scheduling through purge.
    """

    def __init__(self) -> None:
        # tenant_id → ArchivalPolicy
        self._policies: dict[str, ArchivalPolicy] = {}
        # record_id → ArchivalRecord
        self._records:  dict[str, ArchivalRecord] = {}

    def schedule_archival(
        self,
        tenant_id:      str,
        reason:         ArchivalReason,
        initiated_by:   str,
        retention_days: int  = _MIN_RETENTION_DAYS,
        legal_hold:     bool = False,
        notes:          str  = "",
    ) -> tuple[ArchivalPolicy, ArchivalRecord]:
        policy = ArchivalPolicy(
            policy_id      = str(uuid.uuid4()),
            tenant_id      = tenant_id,
            reason         = reason,
            retention_days = retention_days,
            legal_hold     = legal_hold,
            created_by     = initiated_by,
            notes          = notes,
        )
        self._policies[policy.policy_id] = policy

        record = ArchivalRecord(
            record_id    = str(uuid.uuid4()),
            tenant_id    = tenant_id,
            policy_id    = policy.policy_id,
            status       = ArchivalStatus.SCHEDULED,
            initiated_by = initiated_by,
            initiated_at = datetime.now(tz=UTC),
        )
        self._records[record.record_id] = record
        log.info(
            "TenantArchivalService: scheduled archival for tenant %s (reason=%s)",
            tenant_id[:8], reason.value,
        )
        return policy, record

    def mark_archived(
        self,
        record_id:        str,
        storage_location: str,
    ) -> ArchivalRecord:
        record = self._get_record(record_id)
        policy = self._policies.get(record.policy_id)
        now    = datetime.now(tz=UTC)

        record.status            = ArchivalStatus.ARCHIVED
        record.archived_at       = now
        record.storage_location  = storage_location
        record.purge_eligible_at = (
            policy.purge_eligible_after(now)
            if policy else now + timedelta(days=_MIN_RETENTION_DAYS)
        )
        log.info(
            "TenantArchivalService: tenant %s archived at %s",
            record.tenant_id[:8], storage_location,
        )
        return record

    def schedule_purge(self, record_id: str) -> ArchivalRecord:
        record = self._get_record(record_id)
        policy = self._policies.get(record.policy_id)

        if policy and policy.legal_hold:
            raise ArchivalError(
                f"Tenant {record.tenant_id[:8]} is under legal hold — purge blocked"
            )
        now = datetime.now(tz=UTC)
        if record.purge_eligible_at and now < record.purge_eligible_at:
            raise ArchivalError(
                f"Purge not eligible until {record.purge_eligible_at.isoformat()}"
            )
        record.status = ArchivalStatus.PURGE_SCHEDULED
        return record

    def mark_purged(self, record_id: str) -> ArchivalRecord:
        record = self._get_record(record_id)
        record.status    = ArchivalStatus.PURGED
        record.purged_at = datetime.now(tz=UTC)
        log.info("TenantArchivalService: tenant %s purged", record.tenant_id[:8])
        return record

    def restore(self, record_id: str, restored_by: str) -> ArchivalRecord:
        record = self._get_record(record_id)
        if record.status not in (ArchivalStatus.ARCHIVED, ArchivalStatus.PURGE_SCHEDULED):
            raise ArchivalError(
                f"Cannot restore from status {record.status.value}"
            )
        record.status      = ArchivalStatus.RESTORED
        record.restored_at = datetime.now(tz=UTC)
        record.metadata["restored_by"] = restored_by
        log.info("TenantArchivalService: tenant %s restored", record.tenant_id[:8])
        return record

    def get_record(self, tenant_id: str) -> ArchivalRecord | None:
        return next(
            (r for r in self._records.values() if r.tenant_id == tenant_id),
            None,
        )

    def _get_record(self, record_id: str) -> ArchivalRecord:
        r = self._records.get(record_id)
        if r is None:
            raise ArchivalError(f"ArchivalRecord {record_id} not found")
        return r


# ── Health tracking ────────────────────────────────────────────────────────────

class HealthSignal(str, Enum):
    QUOTA_CRITICAL      = "quota_critical"
    BILLING_OVERDUE     = "billing_overdue"
    API_FAILURE_SPIKE   = "api_failure_spike"
    NO_RECENT_ACTIVITY  = "no_recent_activity"
    RULE_PACK_EXPIRED   = "rule_pack_expired"
    ONBOARDING_STALLED  = "onboarding_stalled"


@dataclass
class HealthFlag:
    signal:     HealthSignal
    tenant_id:  str
    message:    str
    severity:   str            = "warning"   # "warning" | "critical"
    flagged_at: datetime       = field(default_factory=lambda: datetime.now(tz=UTC))
    resolved:   bool           = False
    resolved_at: datetime | None = None


class HealthTracker:
    """
    Records and queries operational health flags for tenants.

    Flags are lightweight — they record a signal at a point in time.
    Callers poll list_active_flags() to drive intervention workflows.
    """

    def __init__(self) -> None:
        self._flags: list[HealthFlag] = []

    def flag(
        self,
        tenant_id: str,
        signal:    HealthSignal,
        message:   str,
        severity:  str = "warning",
    ) -> HealthFlag:
        hf = HealthFlag(
            signal    = signal,
            tenant_id = tenant_id,
            message   = message,
            severity  = severity,
        )
        self._flags.append(hf)
        log.info(
            "HealthTracker: %s flag for tenant %s — %s",
            severity, tenant_id[:8], message[:80],
        )
        return hf

    def resolve(self, tenant_id: str, signal: HealthSignal) -> int:
        count = 0
        for hf in self._flags:
            if hf.tenant_id == tenant_id and hf.signal == signal and not hf.resolved:
                hf.resolved    = True
                hf.resolved_at = datetime.now(tz=UTC)
                count += 1
        return count

    def list_active_flags(
        self,
        tenant_id: str | None   = None,
        severity:  str | None   = None,
    ) -> list[HealthFlag]:
        return [
            hf for hf in self._flags
            if not hf.resolved
            and (tenant_id is None or hf.tenant_id == tenant_id)
            and (severity is None or hf.severity == severity)
        ]

    def critical_tenants(self) -> list[str]:
        """Return tenant_ids that have at least one unresolved CRITICAL flag."""
        return list({
            hf.tenant_id for hf in self._flags
            if not hf.resolved and hf.severity == "critical"
        })


# ── Exceptions ─────────────────────────────────────────────────────────────────

class ArchivalError(Exception):
    pass


# ── Singletons ─────────────────────────────────────────────────────────────────

_archival: TenantArchivalService | None = None
_health:   HealthTracker | None         = None


def get_archival_service() -> TenantArchivalService:
    global _archival
    if _archival is None:
        _archival = TenantArchivalService()
    return _archival


def get_health_tracker() -> HealthTracker:
    global _health
    if _health is None:
        _health = HealthTracker()
    return _health
