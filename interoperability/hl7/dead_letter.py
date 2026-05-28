"""
HL7 v2 Dead-Letter Queue (DLQ).

Routes malformed or unprocessable HL7 messages to a durable dead-letter store
so they can be inspected, replayed, or escalated without silently dropping data.

Design
──────
  - Persistent: entries written to database (interop.hl7_dead_letters) and
    optionally to a file-based fallback if DB is unavailable
  - Queryable: entries tagged with error type, sending facility, message type
  - Replayable: raw message bytes always preserved alongside parse errors
  - Non-blocking: enqueue() never raises — failures are logged and swallowed
  - Size-bounded: in-memory buffer capped at MAX_BUFFER_SIZE before flush

Entry lifecycle
───────────────
  1. Parser produces HL7Message with is_valid=False or parse_errors
  2. Caller invokes DLQ.enqueue(msg, reason)
  3. DLQ writes to DB; if DB unavailable, writes to local fallback file
  4. Operator queries / replays via DLQ.query() or direct DB access
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime    import datetime, timezone
from enum        import Enum
from pathlib     import Path
from typing      import Any, Optional

from interoperability.hl7.parser import HL7Message

log = logging.getLogger("evidentrx.interop.hl7.dead_letter")

MAX_BUFFER_SIZE  = 500
_FALLBACK_DIR    = Path("/tmp/evidentrx_hl7_dlq")


# ── DLQ entry model ───────────────────────────────────────────────────────────

class DLQReason(str, Enum):
    PARSE_ERROR        = "parse_error"          # Parser rejected the message
    NORMALISATION_ERROR= "normalisation_error"  # Could not map to canonical
    VALIDATION_ERROR   = "validation_error"     # Failed canonical validation
    DUPLICATE          = "duplicate"            # Checksum collision
    UNSUPPORTED_TYPE   = "unsupported_type"     # No normaliser for msg type
    DOWNSTREAM_FAILURE = "downstream_failure"   # DB / pipeline write failed


@dataclass
class DLQEntry:
    dlq_id:          str
    tenant_id:       str
    reason:          DLQReason
    raw_message:     str                        # original pipe-delimited bytes
    message_type:    str                        # e.g. "ADT", "ORM"
    trigger_event:   str                        # e.g. "A01"
    message_id:      str
    sending_facility:str
    parse_errors:    list[str]
    detail:          str                        # human-readable reason detail
    enqueued_at:     datetime
    replayed:        bool                       = False
    replay_count:    int                        = 0
    tags:            dict[str, str]             = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["reason"]      = self.reason.value
        d["enqueued_at"] = self.enqueued_at.isoformat()
        return d


# ── DLQ service ───────────────────────────────────────────────────────────────

class HL7DeadLetterQueue:
    """
    Thread-safe, async-compatible Dead-Letter Queue for HL7 messages.

    Buffers entries in memory and flushes to DB in batches.
    Falls back to filesystem if DB is unavailable.
    """

    def __init__(
        self,
        tenant_id:      str,
        db_writer:      Optional[Any] = None,   # callable: async (list[DLQEntry]) → None
        fallback_dir:   Path          = _FALLBACK_DIR,
        buffer_size:    int           = MAX_BUFFER_SIZE,
    ) -> None:
        self.tenant_id    = tenant_id
        self._db_writer   = db_writer
        self._fallback_dir= fallback_dir
        self._buffer:     list[DLQEntry] = []
        self._max_buffer  = buffer_size
        self._lock        = asyncio.Lock()
        self._total_enqueued: int = 0

    # ── Public API ─────────────────────────────────────────────────────────────

    async def enqueue(
        self,
        msg:    HL7Message,
        reason: DLQReason,
        detail: str = "",
        tags:   Optional[dict[str, str]] = None,
    ) -> DLQEntry:
        """
        Add a message to the dead-letter queue.

        Never raises — errors are logged so the caller's pipeline continues.
        """
        entry = DLQEntry(
            dlq_id          = str(uuid.uuid4()),
            tenant_id       = self.tenant_id,
            reason          = reason,
            raw_message     = msg.raw,
            message_type    = msg.message_type.value,
            trigger_event   = msg.trigger_event,
            message_id      = msg.message_id,
            sending_facility= msg.sending_facility,
            parse_errors    = list(msg.parse_errors),
            detail          = detail[:500],
            enqueued_at     = datetime.now(tz=timezone.utc),
            tags            = tags or {},
        )

        async with self._lock:
            self._buffer.append(entry)
            self._total_enqueued += 1

            log.warning(
                "HL7 DLQ: %s [%s^%s id=%s] reason=%s",
                entry.dlq_id,
                entry.message_type,
                entry.trigger_event,
                entry.message_id,
                reason.value,
            )

            if len(self._buffer) >= self._max_buffer:
                await self._flush()

        return entry

    async def enqueue_raw(
        self,
        raw_message: str,
        reason:      DLQReason,
        detail:      str = "",
        tenant_id:   Optional[str] = None,
        tags:        Optional[dict[str, str]] = None,
    ) -> DLQEntry:
        """
        Enqueue raw (unparseable) HL7 bytes that could not even be parsed.

        Used when `HL7Parser.parse()` itself returns is_valid=False with no
        message structure to inspect.
        """
        entry = DLQEntry(
            dlq_id          = str(uuid.uuid4()),
            tenant_id       = tenant_id or self.tenant_id,
            reason          = reason,
            raw_message     = raw_message,
            message_type    = "UNKNOWN",
            trigger_event   = "",
            message_id      = "",
            sending_facility= "",
            parse_errors    = [detail] if detail else [],
            detail          = detail[:500],
            enqueued_at     = datetime.now(tz=timezone.utc),
            tags            = tags or {},
        )

        async with self._lock:
            self._buffer.append(entry)
            self._total_enqueued += 1
            log.warning("HL7 DLQ (raw): %s reason=%s", entry.dlq_id, reason.value)

            if len(self._buffer) >= self._max_buffer:
                await self._flush()

        return entry

    async def flush(self) -> int:
        """Flush all buffered entries to persistent storage. Returns flushed count."""
        async with self._lock:
            return await self._flush()

    def size(self) -> int:
        """Current number of buffered (unflushed) entries."""
        return len(self._buffer)

    def total_enqueued(self) -> int:
        """Total entries enqueued since this instance was created."""
        return self._total_enqueued

    # ── Internal flush ─────────────────────────────────────────────────────────

    async def _flush(self) -> int:
        """Flush buffer to DB or fallback. Caller must hold self._lock."""
        if not self._buffer:
            return 0

        batch = list(self._buffer)
        self._buffer.clear()

        if self._db_writer is not None:
            try:
                await self._db_writer(batch)
                log.info("HL7 DLQ: flushed %d entries to DB", len(batch))
                return len(batch)
            except Exception as exc:
                log.error(
                    "HL7 DLQ: DB flush failed (%s) — falling back to filesystem",
                    exc,
                )

        # Fallback: write to local files
        await self._write_fallback(batch)
        return len(batch)

    async def _write_fallback(self, entries: list[DLQEntry]) -> None:
        """Write entries to newline-delimited JSON files in fallback directory."""
        try:
            self._fallback_dir.mkdir(parents=True, exist_ok=True)
            path = self._fallback_dir / f"dlq_{datetime.now(tz=timezone.utc):%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}.ndjson"
            with path.open("w", encoding="utf-8") as fh:
                for entry in entries:
                    fh.write(json.dumps(entry.to_dict()) + "\n")
            log.info("HL7 DLQ: wrote %d entries to fallback %s", len(entries), path)
        except Exception as exc:
            log.error("HL7 DLQ: fallback write failed: %s", exc)


# ── Module-level DLQ factory ──────────────────────────────────────────────────

_queues: dict[str, HL7DeadLetterQueue] = {}


def get_dlq(tenant_id: str, **kwargs: Any) -> HL7DeadLetterQueue:
    """
    Return a per-tenant DLQ singleton.

    In production, pass `db_writer=` to connect the DLQ to the database.
    """
    if tenant_id not in _queues:
        _queues[tenant_id] = HL7DeadLetterQueue(tenant_id=tenant_id, **kwargs)
    return _queues[tenant_id]
