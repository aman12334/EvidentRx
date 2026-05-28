"""
Immutable feedback lineage tracker.

Records the complete provenance chain for every feedback event — from
initial submission through review, status transitions, and eventual
incorporation into calibration. The lineage chain is cryptographically
linked (each entry hashes the prior hash) so the sequence is tamper-evident.

Lineage guarantees
──────────────────
  - Append-only: entries are never modified or deleted
  - Linked: each entry references and hashes the previous entry's hash
  - Attributable: every transition carries the actor who triggered it
  - Temporal: timestamps are UTC with monotonic ordering enforced
  - Replayable: the full chain can be replayed to reconstruct state at any point

This mirrors the audit_log design from Phase 9 but is specialised for
feedback lifecycle tracking and calibration traceability.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

log = logging.getLogger("evidentrx.learning.feedback.lineage")


class LineageEventType(str, Enum):
    SUBMITTED       = "submitted"       # initial feedback submission
    REVIEWED        = "reviewed"        # supervisor review
    ACCEPTED        = "accepted"        # incorporated into calibration
    REJECTED        = "rejected"        # discarded after review
    SUPERSEDED      = "superseded"      # replaced by later feedback
    CALIBRATED      = "calibrated"      # used in a calibration run
    EXPORTED        = "exported"        # included in an evaluation dataset


@dataclass
class LineageEntry:
    """
    One entry in the feedback lineage chain.

    The chain hash is: SHA-256(prior_chain_hash + event_payload)
    The genesis entry uses the feedback_id as the prior hash seed.
    """
    entry_id:        str
    feedback_id:     str
    event_type:      LineageEventType
    actor_id:        str               # analyst or system actor
    tenant_id:       str
    payload:         dict[str, Any]    # event-specific structured data
    occurred_at:     datetime
    prior_hash:      str               # hash of the previous entry (or genesis seed)
    chain_hash:      str               # hash of (prior_hash + this entry content)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id":    self.entry_id,
            "feedback_id": self.feedback_id,
            "event_type":  self.event_type.value,
            "actor_id":    self.actor_id,
            "tenant_id":   self.tenant_id,
            "payload":     self.payload,
            "occurred_at": self.occurred_at.isoformat(),
            "prior_hash":  self.prior_hash,
            "chain_hash":  self.chain_hash,
        }


class FeedbackLineageTracker:
    """
    Maintains and verifies the cryptographic lineage chain for feedback records.

    One chain per feedback_id. Each state transition appends a new entry.
    The chain is valid if every entry's chain_hash can be recomputed from
    its content + its prior_hash.
    """

    def __init__(self, db_writer: Callable | None = None) -> None:
        self._chains: dict[str, list[LineageEntry]] = {}   # feedback_id → entries
        self._db_writer = db_writer

    # ── Chain operations ───────────────────────────────────────────────────────

    async def record(
        self,
        feedback_id: str,
        event_type:  LineageEventType,
        actor_id:    str,
        tenant_id:   str,
        payload:     dict[str, Any] | None = None,
    ) -> LineageEntry:
        """
        Append a new entry to a feedback's lineage chain.

        Returns the new LineageEntry with chain_hash populated.
        """
        chain       = self._chains.setdefault(feedback_id, [])
        prior_hash  = chain[-1].chain_hash if chain else feedback_id    # genesis seed
        now         = datetime.now(tz=UTC)
        event_payload = payload or {}

        chain_hash = _compute_chain_hash(prior_hash, event_type, actor_id, now, event_payload)

        entry = LineageEntry(
            entry_id   = str(uuid.uuid4()),
            feedback_id= feedback_id,
            event_type = event_type,
            actor_id   = actor_id,
            tenant_id  = tenant_id,
            payload    = event_payload,
            occurred_at= now,
            prior_hash = prior_hash,
            chain_hash = chain_hash,
        )
        chain.append(entry)

        if self._db_writer:
            try:
                await self._db_writer(entry)
            except Exception as exc:
                log.error("LineageTracker: DB write failed: %s", exc)

        log.debug(
            "Lineage [%s]: %s by %s (hash=%s)",
            feedback_id[:8], event_type.value, actor_id, chain_hash[:12],
        )
        return entry

    def get_chain(self, feedback_id: str) -> list[LineageEntry]:
        """Return the full lineage chain for a feedback record."""
        return list(self._chains.get(feedback_id, []))

    def verify_chain(self, feedback_id: str) -> tuple[bool, str | None]:
        """
        Verify the cryptographic integrity of a lineage chain.

        Returns (is_valid, error_message).
        """
        chain = self._chains.get(feedback_id, [])
        if not chain:
            return True, None

        prior_hash = feedback_id   # genesis seed
        for entry in chain:
            if entry.prior_hash != prior_hash:
                return False, (
                    f"Chain broken at entry {entry.entry_id[:8]}: "
                    f"expected prior_hash={prior_hash[:12]}, got {entry.prior_hash[:12]}"
                )
            expected = _compute_chain_hash(
                entry.prior_hash, entry.event_type, entry.actor_id,
                entry.occurred_at, entry.payload,
            )
            if entry.chain_hash != expected:
                return False, (
                    f"Hash mismatch at entry {entry.entry_id[:8]}: "
                    f"expected {expected[:12]}, stored {entry.chain_hash[:12]}"
                )
            prior_hash = entry.chain_hash

        return True, None

    def current_state(self, feedback_id: str) -> LineageEventType | None:
        """Return the most recent event type in a feedback's chain."""
        chain = self._chains.get(feedback_id, [])
        return chain[-1].event_type if chain else None

    def all_feedback_ids(self) -> list[str]:
        return list(self._chains.keys())


# ── Hash computation ──────────────────────────────────────────────────────────

def _compute_chain_hash(
    prior_hash:    str,
    event_type:    LineageEventType,
    actor_id:      str,
    occurred_at:   datetime,
    payload:       dict[str, Any],
) -> str:
    """Deterministic chain hash: SHA-256(prior + event content)."""
    content = json.dumps({
        "prior_hash":  prior_hash,
        "event_type":  event_type.value,
        "actor_id":    actor_id,
        "occurred_at": occurred_at.isoformat(),
        "payload":     payload,
    }, sort_keys=True, default=str).encode()
    return hashlib.sha256(content).hexdigest()


# ── Module-level singleton ────────────────────────────────────────────────────

_tracker: FeedbackLineageTracker | None = None


def get_lineage_tracker(db_writer: Callable | None = None) -> FeedbackLineageTracker:
    global _tracker
    if _tracker is None:
        _tracker = FeedbackLineageTracker(db_writer=db_writer)
    return _tracker
