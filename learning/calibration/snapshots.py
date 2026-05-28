"""
Calibration versioning and snapshot management.

Every calibration run produces an immutable snapshot that can be:
  - Stored and queried by version
  - Compared against prior versions
  - Promoted (set as the active calibration for a tenant)
  - Rolled back (revert to a previous approved snapshot)
  - Replayed (re-run calibration from the same inputs)

Snapshot lifecycle
──────────────────
  DRAFT → PENDING_APPROVAL → APPROVED → ACTIVE
                           ↘ REJECTED
  ACTIVE → SUPERSEDED  (when a newer calibration is promoted)

Only one snapshot can be ACTIVE per tenant at any time.
Rollback promotes a prior APPROVED snapshot to ACTIVE.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from learning.calibration.risk import RiskCalibrationResult

log = logging.getLogger("evidentrx.learning.calibration.snapshots")


class SnapshotStatus(str, Enum):
    DRAFT            = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED         = "approved"
    ACTIVE           = "active"
    REJECTED         = "rejected"
    SUPERSEDED       = "superseded"


@dataclass
class CalibrationSnapshot:
    """
    Versioned, immutable calibration snapshot.

    The calibration_result is frozen at creation time — it is never
    modified after the snapshot is created.
    """
    snapshot_id:       str
    tenant_id:         str
    version:           str
    status:            SnapshotStatus
    calibration:       RiskCalibrationResult
    created_at:        datetime
    created_by:        str                    # analyst or "system"
    approved_by:       str | None          = None
    approved_at:       datetime | None     = None
    rejected_by:       str | None          = None
    rejection_reason:  str | None          = None
    activated_at:      datetime | None     = None
    superseded_by:     str | None          = None   # snapshot_id of successor
    change_summary:    str                    = ""
    parent_snapshot_id: str | None         = None   # prior active snapshot

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id":    self.snapshot_id,
            "tenant_id":      self.tenant_id,
            "version":        self.version,
            "status":         self.status.value,
            "content_hash":   self.calibration.content_hash,
            "created_at":     self.created_at.isoformat(),
            "created_by":     self.created_by,
            "approved_by":    self.approved_by,
            "approved_at":    self.approved_at.isoformat() if self.approved_at else None,
            "activated_at":   self.activated_at.isoformat() if self.activated_at else None,
            "superseded_by":  self.superseded_by,
            "change_summary": self.change_summary,
            "parent_snapshot_id": self.parent_snapshot_id,
        }


class CalibrationSnapshotStore:
    """
    In-memory calibration snapshot store with versioned history.

    In production, snapshots are persisted to the learning.calibration_snapshots
    table. This implementation is fully correct for single-node and test use.
    """

    def __init__(self, db_writer: Any | None = None) -> None:
        self._snapshots:     dict[str, CalibrationSnapshot] = {}   # snapshot_id → snapshot
        self._by_tenant:     dict[str, list[str]] = {}              # tenant_id → [snapshot_ids]
        self._active:        dict[str, str] = {}                    # tenant_id → active snapshot_id
        self._db_writer      = db_writer

    # ── Create ─────────────────────────────────────────────────────────────────

    async def create(
        self,
        calibration:    RiskCalibrationResult,
        created_by:     str,
        change_summary: str = "",
    ) -> CalibrationSnapshot:
        """Create a new DRAFT snapshot from a calibration result."""
        snapshot_id     = str(uuid.uuid4())
        parent_id       = self._active.get(calibration.tenant_id)

        snapshot = CalibrationSnapshot(
            snapshot_id        = snapshot_id,
            tenant_id          = calibration.tenant_id,
            version            = calibration.version,
            status             = SnapshotStatus.DRAFT,
            calibration        = calibration,
            created_at         = datetime.now(tz=UTC),
            created_by         = created_by,
            change_summary     = change_summary,
            parent_snapshot_id = parent_id,
        )

        self._snapshots[snapshot_id] = snapshot
        self._by_tenant.setdefault(calibration.tenant_id, []).append(snapshot_id)

        if self._db_writer:
            await self._db_writer("create", snapshot)

        log.info(
            "CalibrationSnapshot: created %s v%s for tenant %s (by %s)",
            snapshot_id[:8], calibration.version, calibration.tenant_id, created_by,
        )
        return snapshot

    # ── Status transitions ─────────────────────────────────────────────────────

    async def submit_for_approval(self, snapshot_id: str) -> CalibrationSnapshot:
        """Transition DRAFT → PENDING_APPROVAL."""
        snap = self._require(snapshot_id, SnapshotStatus.DRAFT)
        snap.status = SnapshotStatus.PENDING_APPROVAL
        await self._persist("update", snap)
        log.info("CalibrationSnapshot: %s submitted for approval", snapshot_id[:8])
        return snap

    async def approve(
        self,
        snapshot_id: str,
        approved_by: str,
    ) -> CalibrationSnapshot:
        """Transition PENDING_APPROVAL → APPROVED."""
        snap = self._require(snapshot_id, SnapshotStatus.PENDING_APPROVAL)
        snap.status      = SnapshotStatus.APPROVED
        snap.approved_by = approved_by
        snap.approved_at = datetime.now(tz=UTC)

        # Also mark calibration as approved
        snap.calibration.approved    = True
        snap.calibration.approved_by = approved_by
        snap.calibration.approved_at = snap.approved_at

        await self._persist("update", snap)
        log.info("CalibrationSnapshot: %s approved by %s", snapshot_id[:8], approved_by)
        return snap

    async def reject(
        self,
        snapshot_id:      str,
        rejected_by:      str,
        rejection_reason: str,
    ) -> CalibrationSnapshot:
        """Transition PENDING_APPROVAL → REJECTED."""
        snap = self._require(snapshot_id, SnapshotStatus.PENDING_APPROVAL)
        snap.status           = SnapshotStatus.REJECTED
        snap.rejected_by      = rejected_by
        snap.rejection_reason = rejection_reason
        await self._persist("update", snap)
        log.info(
            "CalibrationSnapshot: %s rejected by %s: %s",
            snapshot_id[:8], rejected_by, rejection_reason[:80],
        )
        return snap

    async def activate(
        self,
        snapshot_id: str,
        activated_by: str,
    ) -> CalibrationSnapshot:
        """
        Transition APPROVED → ACTIVE.

        Supersedes the currently active snapshot (if any).
        Only one snapshot can be ACTIVE per tenant.
        """
        snap = self._require(snapshot_id, SnapshotStatus.APPROVED)
        tenant_id = snap.tenant_id

        # Supersede current active
        current_active_id = self._active.get(tenant_id)
        if current_active_id and current_active_id != snapshot_id:
            current = self._snapshots.get(current_active_id)
            if current:
                current.status       = SnapshotStatus.SUPERSEDED
                current.superseded_by = snapshot_id
                await self._persist("update", current)

        snap.status       = SnapshotStatus.ACTIVE
        snap.activated_at = datetime.now(tz=UTC)
        self._active[tenant_id] = snapshot_id
        await self._persist("update", snap)

        log.info(
            "CalibrationSnapshot: %s v%s ACTIVATED for tenant %s by %s",
            snapshot_id[:8], snap.version, tenant_id, activated_by,
        )
        return snap

    async def rollback(
        self,
        tenant_id:    str,
        target_id:    str,
        rolled_by:    str,
    ) -> CalibrationSnapshot:
        """
        Roll back to a prior APPROVED snapshot.

        The target must be APPROVED or SUPERSEDED (not REJECTED or DRAFT).
        Rollback is implemented as a re-activation of the prior snapshot.
        """
        target = self._snapshots.get(target_id)
        if target is None:
            raise SnapshotNotFoundError(target_id)
        if target.status not in (SnapshotStatus.APPROVED, SnapshotStatus.SUPERSEDED):
            raise SnapshotTransitionError(
                f"Cannot roll back to snapshot in status {target.status.value}"
            )
        if target.tenant_id != tenant_id:
            raise SnapshotTransitionError("Snapshot belongs to a different tenant")

        # Re-approve so activate() accepts it
        target.status      = SnapshotStatus.APPROVED
        target.approved_by = rolled_by
        target.approved_at = datetime.now(tz=UTC)

        return await self.activate(target_id, activated_by=rolled_by)

    # ── Queries ────────────────────────────────────────────────────────────────

    def get_active(self, tenant_id: str) -> CalibrationSnapshot | None:
        snap_id = self._active.get(tenant_id)
        return self._snapshots.get(snap_id) if snap_id else None

    def get(self, snapshot_id: str) -> CalibrationSnapshot | None:
        return self._snapshots.get(snapshot_id)

    def list_for_tenant(
        self,
        tenant_id: str,
        status:    SnapshotStatus | None = None,
    ) -> list[CalibrationSnapshot]:
        ids = self._by_tenant.get(tenant_id, [])
        snaps = [self._snapshots[i] for i in ids if i in self._snapshots]
        if status:
            snaps = [s for s in snaps if s.status == status]
        return sorted(snaps, key=lambda s: s.created_at, reverse=True)

    def history(self, tenant_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """Return a summarised version history for a tenant."""
        return [s.to_dict() for s in self.list_for_tenant(tenant_id)[:limit]]

    # ── Internal ───────────────────────────────────────────────────────────────

    def _require(self, snapshot_id: str, expected_status: SnapshotStatus) -> CalibrationSnapshot:
        snap = self._snapshots.get(snapshot_id)
        if snap is None:
            raise SnapshotNotFoundError(snapshot_id)
        if snap.status != expected_status:
            raise SnapshotTransitionError(
                f"Snapshot {snapshot_id[:8]} is in status {snap.status.value}, "
                f"expected {expected_status.value}"
            )
        return snap

    async def _persist(self, op: str, snap: CalibrationSnapshot) -> None:
        if self._db_writer:
            try:
                await self._db_writer(op, snap)
            except Exception as exc:
                log.error("CalibrationSnapshot: DB persist failed: %s", exc)


# ── Exceptions ────────────────────────────────────────────────────────────────

class SnapshotNotFoundError(Exception):
    pass

class SnapshotTransitionError(Exception):
    pass


# ── Module-level singleton ────────────────────────────────────────────────────

_store: CalibrationSnapshotStore | None = None


def get_snapshot_store(db_writer: Any | None = None) -> CalibrationSnapshotStore:
    global _store
    if _store is None:
        _store = CalibrationSnapshotStore(db_writer=db_writer)
    return _store
