"""
Data retention policies and soft-deletion.

340B compliance data must be retained for a minimum of 7 years per HRSA
program integrity requirements. This module enforces:
  - Minimum retention floors (no accidental early deletion)
  - Soft-delete with retention timestamps
  - Archival workflow triggers (investigation → archive after N days)
  - Scheduled purge of expired, archived, soft-deleted records

Soft delete: records gain a deleted_at timestamp; hard delete never runs during
retention window. After retention window expires, records are purged by the
scheduled retention job (scripts/purge_expired.py).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing   import Optional

from config.settings import settings

log = logging.getLogger(__name__)


# ─── Retention Constants ───────────────────────────────────────────────────────

MINIMUM_RETENTION_DAYS = 365 * 7    # 7 years (HRSA minimum)
DEFAULT_RETENTION_DAYS = settings.audit_retention_days
ARCHIVE_AFTER_DAYS     = settings.investigation_archive_days


class RetentionPolicy:
    """
    Retention policy evaluator.

    Encapsulates all retention decisions so they can be changed in one place
    and tested independently.
    """

    def __init__(
        self,
        retention_days:  int = DEFAULT_RETENTION_DAYS,
        archive_after:   int = ARCHIVE_AFTER_DAYS,
    ) -> None:
        if retention_days < MINIMUM_RETENTION_DAYS:
            raise ValueError(
                f"Retention period {retention_days}d is below the "
                f"minimum {MINIMUM_RETENTION_DAYS}d (7 years HRSA)"
            )
        self.retention_days = retention_days
        self.archive_after  = archive_after

    def is_eligible_for_deletion(self, created_at: datetime) -> bool:
        """
        Return True if a record is past its retention window and may be
        permanently deleted. This is the ONLY gate for hard deletes.
        """
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=self.retention_days)
        return created_at < cutoff

    def is_eligible_for_archive(self, closed_at: Optional[datetime]) -> bool:
        """
        Return True if a closed investigation has been inactive long enough
        to move to cold storage / archive tier.
        """
        if closed_at is None:
            return False
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=self.archive_after)
        return closed_at < cutoff

    def deletion_eligible_after(self, created_at: datetime) -> datetime:
        """Return the earliest datetime this record may be hard-deleted."""
        return created_at + timedelta(days=self.retention_days)

    def soft_delete(self) -> datetime:
        """Return a soft-delete timestamp (now)."""
        return datetime.now(tz=timezone.utc)


# ─── Soft Deletion Helpers ────────────────────────────────────────────────────

def apply_soft_delete(record: dict, actor_id: str) -> dict:
    """
    Apply soft-delete fields to a record dict.
    Does NOT write to DB — caller is responsible for the update.
    """
    now = datetime.now(tz=timezone.utc)
    return {
        **record,
        "deleted_at":  now.isoformat(),
        "deleted_by":  actor_id,
        "is_deleted":  True,
    }


def verify_deletion_allowed(
    retention_policy: RetentionPolicy,
    created_at:       datetime,
    actor_role:       str,
) -> tuple[bool, str]:
    """
    Check whether a hard-delete is permitted.
    Returns (allowed, reason).
    """
    if actor_role not in ("admin", "system"):
        return False, "Only admin or system role may hard-delete records"

    if not retention_policy.is_eligible_for_deletion(created_at):
        eligible_after = retention_policy.deletion_eligible_after(created_at)
        return False, (
            f"Record is within retention window. "
            f"Eligible for deletion after {eligible_after.date().isoformat()}"
        )

    return True, "deletion_permitted"


# Singleton policy instance
retention_policy = RetentionPolicy()
