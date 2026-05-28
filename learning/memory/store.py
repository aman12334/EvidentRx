"""
Adaptive intelligence memory store.

Base persistence layer for the learning system's long-term memory. Stores
structured knowledge derived from analyst feedback, investigation outcomes,
and calibration events. All writes are append-only — no memory entry is
ever overwritten or deleted within its retention window.

Memory design principles
────────────────────────
  - Append-only: every write creates a new record, never mutates existing ones
  - Tenant-isolated: each tenant's memory is strictly partitioned
  - Retention-bounded: entries expire after a configurable TTL (default 2 years)
  - Type-tagged: each entry carries a MemoryType for efficient retrieval
  - Auditable: every entry records who/what triggered the write
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime    import datetime, timedelta, timezone
from enum        import Enum
from typing      import Any, Callable, Iterator, Optional

log = logging.getLogger("evidentrx.learning.memory.store")

# Default retention: 2 years
_DEFAULT_RETENTION_DAYS = 730


class MemoryType(str, Enum):
    ANALYST_CORRECTION   = "analyst_correction"   # analyst overrode system output
    INVESTIGATION_OUTCOME= "investigation_outcome" # case resolved with verified outcome
    CALIBRATION_EVENT    = "calibration_event"    # calibration snapshot activated
    FALSE_POSITIVE_SIGNAL= "false_positive_signal" # confirmed FP for a rule
    FALSE_NEGATIVE_SIGNAL= "false_negative_signal" # confirmed FN escalation
    RECOMMENDATION_OUTCOME= "recommendation_outcome" # rec followed/dismissed/effective
    WORKFLOW_IMPROVEMENT = "workflow_improvement"  # workflow version change outcome
    PROMPT_REVISION      = "prompt_revision"       # prompt version change outcome


@dataclass
class MemoryEntry:
    """
    A single immutable memory record.

    The content field carries type-specific data. The content_hash
    provides tamper detection. Once written, a MemoryEntry is never
    modified — corrections create new entries that reference the
    original via supersedes_id.
    """
    entry_id:      str
    tenant_id:     str
    memory_type:   MemoryType
    content:       dict[str, Any]
    content_hash:  str
    recorded_at:   datetime
    recorded_by:   str           # analyst_id or "system"
    expires_at:    datetime
    tags:          list[str]     = field(default_factory=list)
    supersedes_id: Optional[str] = None   # links to corrected/superseded entry
    artifact_id:   Optional[str] = None   # case_id / finding_id / recommendation_id

    @property
    def is_expired(self) -> bool:
        return datetime.now(tz=timezone.utc) > self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id":      self.entry_id,
            "tenant_id":     self.tenant_id,
            "memory_type":   self.memory_type.value,
            "content":       self.content,
            "content_hash":  self.content_hash,
            "recorded_at":   self.recorded_at.isoformat(),
            "recorded_by":   self.recorded_by,
            "expires_at":    self.expires_at.isoformat(),
            "tags":          self.tags,
            "supersedes_id": self.supersedes_id,
            "artifact_id":   self.artifact_id,
        }


class MemoryStore:
    """
    Append-only memory store for the adaptive intelligence layer.

    Organises entries by tenant and type for efficient retrieval.
    Expired entries are excluded from query results but retained in the
    underlying store until explicitly purged by a background job.
    """

    def __init__(
        self,
        db_writer:        Optional[Callable] = None,
        retention_days:   int                = _DEFAULT_RETENTION_DAYS,
    ) -> None:
        self._entries:    dict[str, MemoryEntry] = {}
        # (tenant_id, memory_type) → list of entry_ids, insertion order
        self._index:      dict[tuple[str, str], list[str]] = {}
        self._db_writer   = db_writer
        self._retention   = retention_days

    # ── Write ──────────────────────────────────────────────────────────────────

    async def record(
        self,
        tenant_id:     str,
        memory_type:   MemoryType,
        content:       dict[str, Any],
        recorded_by:   str,
        tags:          Optional[list[str]] = None,
        artifact_id:   Optional[str]       = None,
        supersedes_id: Optional[str]       = None,
        retention_days: Optional[int]      = None,
    ) -> MemoryEntry:
        """
        Write a new memory entry.

        Returns the entry immediately; DB persistence is best-effort.
        """
        now        = datetime.now(tz=timezone.utc)
        ttl_days   = retention_days if retention_days is not None else self._retention
        expires_at = now + timedelta(days=ttl_days)
        content_hash = _hash_content(content)

        entry = MemoryEntry(
            entry_id      = str(uuid.uuid4()),
            tenant_id     = tenant_id,
            memory_type   = memory_type,
            content       = content,
            content_hash  = content_hash,
            recorded_at   = now,
            recorded_by   = recorded_by,
            expires_at    = expires_at,
            tags          = tags or [],
            supersedes_id = supersedes_id,
            artifact_id   = artifact_id,
        )

        self._entries[entry.entry_id] = entry
        key = (tenant_id, memory_type.value)
        self._index.setdefault(key, []).append(entry.entry_id)

        if self._db_writer:
            try:
                await self._db_writer("create", entry)
            except Exception as exc:
                log.error("MemoryStore: persist failed: %s", exc)

        log.debug(
            "MemoryStore: recorded %s [%s] for tenant %s",
            memory_type.value, entry.entry_id[:8], tenant_id,
        )
        return entry

    # ── Read ───────────────────────────────────────────────────────────────────

    def get(self, entry_id: str) -> Optional[MemoryEntry]:
        entry = self._entries.get(entry_id)
        return entry if entry and not entry.is_expired else None

    def query(
        self,
        tenant_id:    str,
        memory_type:  Optional[MemoryType] = None,
        since:        Optional[datetime]   = None,
        until:        Optional[datetime]   = None,
        tags:         Optional[list[str]]  = None,
        artifact_id:  Optional[str]        = None,
        limit:        int                  = 500,
        include_expired: bool              = False,
    ) -> list[MemoryEntry]:
        """
        Query memory entries with optional filters.

        Results are returned newest-first and exclude expired entries
        unless include_expired=True.
        """
        if memory_type is not None:
            key = (tenant_id, memory_type.value)
            ids = self._index.get(key, [])
            entries = [self._entries[i] for i in ids if i in self._entries]
        else:
            entries = [e for e in self._entries.values() if e.tenant_id == tenant_id]

        if not include_expired:
            entries = [e for e in entries if not e.is_expired]
        if since is not None:
            entries = [e for e in entries if e.recorded_at >= since]
        if until is not None:
            entries = [e for e in entries if e.recorded_at <= until]
        if tags:
            tag_set = set(tags)
            entries = [e for e in entries if tag_set.intersection(e.tags)]
        if artifact_id is not None:
            entries = [e for e in entries if e.artifact_id == artifact_id]

        entries.sort(key=lambda e: e.recorded_at, reverse=True)
        return entries[:limit]

    def count(
        self,
        tenant_id:   str,
        memory_type: Optional[MemoryType] = None,
    ) -> int:
        return len(self.query(tenant_id, memory_type=memory_type, limit=10_000))

    # ── Maintenance ────────────────────────────────────────────────────────────

    def purge_expired(self, tenant_id: str) -> int:
        """
        Remove expired entries from the in-memory store.

        Does NOT delete from the database — that is handled by a separate
        background retention job. Returns the number of entries purged.
        """
        expired_ids = [
            eid for eid, e in self._entries.items()
            if e.tenant_id == tenant_id and e.is_expired
        ]
        for eid in expired_ids:
            entry = self._entries.pop(eid, None)
            if entry:
                key = (tenant_id, entry.memory_type.value)
                idx = self._index.get(key, [])
                if eid in idx:
                    idx.remove(eid)
        return len(expired_ids)

    def iter_all(self, tenant_id: str) -> Iterator[MemoryEntry]:
        """Iterate all non-expired entries for a tenant (for export)."""
        for entry in self._entries.values():
            if entry.tenant_id == tenant_id and not entry.is_expired:
                yield entry


# ── Hash helper ────────────────────────────────────────────────────────────────

def _hash_content(content: dict[str, Any]) -> str:
    payload = json.dumps(content, sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()


# ── Module-level singleton ─────────────────────────────────────────────────────

_store: Optional[MemoryStore] = None


def get_memory_store(
    db_writer:      Optional[Callable] = None,
    retention_days: int                = _DEFAULT_RETENTION_DAYS,
) -> MemoryStore:
    global _store
    if _store is None:
        _store = MemoryStore(db_writer=db_writer, retention_days=retention_days)
    return _store
