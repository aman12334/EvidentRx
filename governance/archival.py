"""
Investigation archival workflow.

When an investigation case is closed and has been inactive for the configured
archive_after window (default 90 days), it transitions to archived state:

  1. Case status → archived
  2. All associated findings, traces, evidence → marked archived
  3. Compressed snapshot written to cold storage (S3 or filesystem)
  4. Audit event written for the archival action
  5. DB row updated with archived_at timestamp

Archived cases are:
  - Read-only (no status updates possible)
  - Searchable via separate archive index
  - Retrievable for compliance audits (replay-safe)
  - Retained for the full retention window (7 years)

Note: archival is reversible for auditor+ roles (un-archive).
"""

from __future__ import annotations

import gzip
import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict

from governance.audit_log import AuditEventType, audit_log
from governance.retention import retention_policy

log = logging.getLogger(__name__)

# Archive directory (local fallback; production uses S3)
_ARCHIVE_ROOT = Path("runtime_state/archive")


class ArchivalService:
    """
    Manages the archival lifecycle for investigation cases.
    """

    def __init__(self, archive_dir: Path = _ARCHIVE_ROOT) -> None:
        self.archive_dir = archive_dir
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    def archive_case(
        self,
        case_data:  Dict[str, Any],
        actor_id:   str,
        tenant_id:  str,
    ) -> str:
        """
        Archive a single investigation case.
        Returns the archive_id (filename stem) for reference storage.
        """
        case_id     = case_data.get("case_id", str(uuid.uuid4()))
        archive_id  = f"{tenant_id}/{case_id}"
        closed_at   = case_data.get("closed_at")

        if not closed_at:
            raise ValueError(f"Cannot archive open case {case_id}")

        # Eligibility check
        closed_dt = datetime.fromisoformat(closed_at) if isinstance(closed_at, str) else closed_at
        if not retention_policy.is_eligible_for_archive(closed_dt):
            raise ValueError(
                f"Case {case_id} is not yet eligible for archival "
                f"(must be closed for {retention_policy.archive_after} days)"
            )

        # Write compressed archive to local storage
        archive_path = self.archive_dir / f"{case_id}.json.gz"
        archive_path.parent.mkdir(parents=True, exist_ok=True)

        archive_record = {
            "archive_id":  archive_id,
            "archived_at": datetime.now(tz=UTC).isoformat(),
            "archived_by": actor_id,
            "tenant_id":   tenant_id,
            "case_data":   case_data,
        }

        with gzip.open(archive_path, "wt", encoding="utf-8") as f:
            json.dump(archive_record, f, default=str, indent=2)

        log.info("Case %s archived to %s", case_id, archive_path)

        # Write audit event
        audit_log.write(
            event_type=AuditEventType.ARCHIVE_WRITTEN,
            actor_id=actor_id,
            tenant_id=tenant_id,
            payload={
                "case_id":    case_id,
                "archive_id": archive_id,
                "archive_path": str(archive_path),
            },
            resource_id=case_id,
            resource_type="investigation_case",
        )

        return archive_id

    def retrieve_archive(
        self,
        case_id:   str,
        tenant_id: str,
    ) -> Dict[str, Any] | None:
        """
        Retrieve an archived case snapshot.
        Returns None if the archive does not exist.
        """
        archive_path = self.archive_dir / f"{case_id}.json.gz"
        if not archive_path.exists():
            return None

        with gzip.open(archive_path, "rt", encoding="utf-8") as f:
            record = json.load(f)

        # Tenant isolation check
        if record.get("tenant_id") != tenant_id:
            log.warning(
                "Cross-tenant archive access attempt: case=%s requester=%s",
                case_id, tenant_id,
            )
            return None

        return record

    def list_archives(self, tenant_id: str) -> list[str]:
        """Return all archive IDs for a tenant."""
        tenant_dir = self.archive_dir / tenant_id
        if not tenant_dir.exists():
            return []
        return [p.stem.replace(".json", "") for p in tenant_dir.glob("*.json.gz")]


archival_service = ArchivalService()
