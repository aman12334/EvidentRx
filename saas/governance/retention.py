"""
Data retention policies and legal holds.

HIPAA and CMS 340B audit requirements mandate specific retention periods
for investigation records, audit logs, and evidence packages. This module
manages per-tenant retention policies and legal holds that temporarily
suspend deletion of specific record sets.

Retention hierarchy (most restrictive wins)
────────────────────────────────────────────
  Legal hold (indefinite)  >  Custom tenant policy  >  Platform default
  Platform default: 7 years (2555 days) for all investigation records
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime    import datetime, timedelta, timezone
from enum        import Enum
from typing      import Any, Optional

log = logging.getLogger("evidentrx.saas.governance.retention")

# Regulatory floor — 7 years (HIPAA Safe Harbor minimum)
_PLATFORM_DEFAULT_DAYS = 2555


class RecordCategory(str, Enum):
    INVESTIGATION   = "investigation"
    AUDIT_LOG       = "audit_log"
    EVIDENCE        = "evidence"
    COMMUNICATION   = "communication"
    BILLING         = "billing"
    CONFIGURATION   = "configuration"


class RetentionAction(str, Enum):
    ARCHIVE = "archive"   # move to cold storage
    PURGE   = "purge"     # destroy after retention period


@dataclass
class RetentionPolicy:
    """
    Per-tenant data retention configuration for a specific record category.

    retention_days must be ≥ _PLATFORM_DEFAULT_DAYS.
    """
    policy_id:       str
    tenant_id:       str
    category:        RecordCategory
    retention_days:  int
    action:          RetentionAction
    created_by:      str
    created_at:      datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    description:     str      = ""
    active:          bool     = True

    def __post_init__(self) -> None:
        if self.retention_days < _PLATFORM_DEFAULT_DAYS:
            raise ValueError(
                f"Retention policy must meet the 7-year minimum "
                f"({_PLATFORM_DEFAULT_DAYS} days); got {self.retention_days}"
            )

    def eligible_for_action_after(self, created_at: datetime) -> datetime:
        """Return the earliest datetime the action may be applied to a record."""
        return created_at + timedelta(days=self.retention_days)

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id":      self.policy_id,
            "tenant_id":      self.tenant_id,
            "category":       self.category.value,
            "retention_days": self.retention_days,
            "action":         self.action.value,
            "active":         self.active,
            "created_by":     self.created_by,
        }


@dataclass
class LegalHold:
    """
    A legal hold suspends all retention actions for specified records.

    While a legal hold is active, no records matching scope_query may
    be archived or purged — regardless of their retention policy.
    scope_query is a freeform dict that callers interpret (e.g.
    {"investigation_ids": [...]} or {"org_id": "org_xxx"}).
    """
    hold_id:     str
    tenant_id:   str
    name:        str
    scope_query: dict[str, Any]    # criteria identifying covered records
    reason:      str
    imposed_by:  str
    imposed_at:  datetime
    released_at: Optional[datetime] = None
    released_by: Optional[str]      = None

    @property
    def is_active(self) -> bool:
        return self.released_at is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "hold_id":     self.hold_id,
            "tenant_id":   self.tenant_id,
            "name":        self.name,
            "reason":      self.reason,
            "imposed_by":  self.imposed_by,
            "imposed_at":  self.imposed_at.isoformat(),
            "is_active":   self.is_active,
            "released_at": self.released_at.isoformat() if self.released_at else None,
        }


class RetentionManager:
    """
    Manages retention policies and legal holds for all tenants.

    Expiry decisions
    ────────────────
    is_eligible_for_action() returns True only if:
    1. The record is past its retention period, AND
    2. There is no active legal hold that covers it
    """

    def __init__(self) -> None:
        # (tenant_id, category) → RetentionPolicy
        self._policies: dict[tuple[str, str], RetentionPolicy] = {}
        # hold_id → LegalHold
        self._holds: dict[str, LegalHold] = {}

    # ── Policy management ──────────────────────────────────────────────────────

    def set_policy(
        self,
        tenant_id:      str,
        category:       RecordCategory,
        retention_days: int,
        action:         RetentionAction,
        created_by:     str,
        description:    str = "",
    ) -> RetentionPolicy:
        policy = RetentionPolicy(
            policy_id      = str(uuid.uuid4()),
            tenant_id      = tenant_id,
            category       = category,
            retention_days = retention_days,
            action         = action,
            created_by     = created_by,
            description    = description,
        )
        self._policies[(tenant_id, category.value)] = policy
        log.info(
            "RetentionManager: set %s policy for tenant %s: %d days → %s",
            category.value, tenant_id[:8], retention_days, action.value,
        )
        return policy

    def get_policy(
        self,
        tenant_id: str,
        category:  RecordCategory,
    ) -> RetentionPolicy:
        """Return the effective policy (custom or platform default)."""
        key = (tenant_id, category.value)
        return self._policies.get(key) or self._default_policy(tenant_id, category)

    def list_policies(self, tenant_id: str) -> list[RetentionPolicy]:
        return [p for p in self._policies.values() if p.tenant_id == tenant_id]

    # ── Legal holds ────────────────────────────────────────────────────────────

    def impose_hold(
        self,
        tenant_id:   str,
        name:        str,
        scope_query: dict[str, Any],
        reason:      str,
        imposed_by:  str,
    ) -> LegalHold:
        hold = LegalHold(
            hold_id     = str(uuid.uuid4()),
            tenant_id   = tenant_id,
            name        = name,
            scope_query = scope_query,
            reason      = reason,
            imposed_by  = imposed_by,
            imposed_at  = datetime.now(tz=timezone.utc),
        )
        self._holds[hold.hold_id] = hold
        log.info(
            "RetentionManager: legal hold '%s' imposed for tenant %s",
            name, tenant_id[:8],
        )
        return hold

    def release_hold(
        self,
        tenant_id:   str,
        hold_id:     str,
        released_by: str,
    ) -> LegalHold:
        hold = self._get_hold(tenant_id, hold_id)
        if not hold.is_active:
            raise RetentionError(f"Hold {hold_id[:8]} is already released")
        hold.released_at = datetime.now(tz=timezone.utc)
        hold.released_by = released_by
        log.info(
            "RetentionManager: legal hold '%s' released for tenant %s",
            hold.name, tenant_id[:8],
        )
        return hold

    def list_holds(
        self,
        tenant_id:   str,
        active_only: bool = True,
    ) -> list[LegalHold]:
        return [
            h for h in self._holds.values()
            if h.tenant_id == tenant_id
            and (not active_only or h.is_active)
        ]

    # ── Eligibility check ──────────────────────────────────────────────────────

    def is_eligible_for_action(
        self,
        tenant_id:    str,
        category:     RecordCategory,
        record_created_at: datetime,
    ) -> tuple[bool, str]:
        """
        Return (eligible, reason).

        eligible=True means the record may be archived/purged.
        """
        # Check retention period
        policy     = self.get_policy(tenant_id, category)
        eligible_at = policy.eligible_for_action_after(record_created_at)
        now         = datetime.now(tz=timezone.utc)
        if now < eligible_at:
            return False, f"Retention period not yet elapsed (eligible {eligible_at.isoformat()})"

        # Check legal holds
        active_holds = self.list_holds(tenant_id, active_only=True)
        if active_holds:
            return False, f"{len(active_holds)} active legal hold(s) prevent action"

        return True, "eligible"

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _default_policy(tenant_id: str, category: RecordCategory) -> RetentionPolicy:
        return RetentionPolicy(
            policy_id      = "platform_default",
            tenant_id      = tenant_id,
            category       = category,
            retention_days = _PLATFORM_DEFAULT_DAYS,
            action         = RetentionAction.ARCHIVE,
            created_by     = "platform",
            description    = "Platform default 7-year retention (HIPAA)",
        )

    def _get_hold(self, tenant_id: str, hold_id: str) -> LegalHold:
        hold = self._holds.get(hold_id)
        if hold is None or hold.tenant_id != tenant_id:
            raise RetentionError(f"LegalHold {hold_id} not found")
        return hold


# ── Exceptions ─────────────────────────────────────────────────────────────────

class RetentionError(Exception):
    pass


# ── Singleton ──────────────────────────────────────────────────────────────────

_manager: Optional[RetentionManager] = None


def get_retention_manager() -> RetentionManager:
    global _manager
    if _manager is None:
        _manager = RetentionManager()
    return _manager
