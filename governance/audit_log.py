"""
Immutable audit log service.

Every write is:
  1. Assigned a UUID4 event_id
  2. Timestamped in UTC
  3. HMAC-SHA256 signed (covers event_id + ts + actor + tenant + type + payload)
  4. Persisted to audit.audit_events (append-only; no UPDATE/DELETE ever issued)

The database table has a row-level trigger that blocks UPDATE and DELETE
(enforced at DB layer, not just application layer).

In-process buffer: events are batched in a deque and flushed every 100 events
or 5 seconds, whichever comes first, to reduce DB round-trips.
"""

from __future__ import annotations

import asyncio
import uuid
import logging
from collections import deque
from datetime    import datetime, timezone
from enum        import Enum
from typing      import Any, Deque, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from security.audit_signer import sign_audit_event

log = logging.getLogger(__name__)


class AuditEventType(str, Enum):
    # Investigation lifecycle
    CASE_CREATED         = "case.created"
    CASE_STATUS_CHANGED  = "case.status_changed"
    CASE_ASSIGNED        = "case.assigned"
    CASE_ESCALATED       = "case.escalated"
    CASE_RESOLVED        = "case.resolved"
    CASE_CLOSED          = "case.closed"
    CASE_ARCHIVED        = "case.archived"

    # Finding events
    FINDING_CREATED      = "finding.created"
    FINDING_ANNOTATED    = "finding.annotated"

    # Agent events
    AGENT_RUN_STARTED    = "agent.run_started"
    AGENT_RUN_COMPLETED  = "agent.run_completed"
    AGENT_RUN_FAILED     = "agent.run_failed"

    # Auth events
    USER_LOGIN           = "auth.login"
    USER_LOGOUT          = "auth.logout"
    USER_CREATED         = "auth.user_created"
    TOKEN_REFRESHED      = "auth.token_refreshed"
    SESSION_REVOKED      = "auth.session_revoked"

    # Access events
    RESOURCE_ACCESSED    = "access.resource_accessed"
    UNAUTHORIZED_ATTEMPT = "access.unauthorized"

    # Governance events
    AUDIT_LOG_READ       = "governance.audit_log_read"
    WORKFLOW_REPLAYED    = "governance.workflow_replayed"
    ARCHIVE_WRITTEN      = "governance.archive_written"

    # Admin events
    CONFIG_CHANGED       = "admin.config_changed"
    FLAG_TOGGLED         = "admin.flag_toggled"
    SECRET_ROTATED       = "admin.secret_rotated"
    USER_ROLE_CHANGED    = "admin.user_role_changed"


class AuditEvent:
    """An audit record ready for persistence."""

    __slots__ = (
        "event_id", "event_type", "actor_id", "tenant_id",
        "resource_id", "resource_type", "payload", "timestamp", "signature",
    )

    def __init__(
        self,
        event_type:    AuditEventType,
        actor_id:      str,
        tenant_id:     str,
        payload:       Dict[str, Any],
        resource_id:   Optional[str] = None,
        resource_type: Optional[str] = None,
    ) -> None:
        self.event_id     = str(uuid.uuid4())
        self.event_type   = event_type
        self.actor_id     = actor_id
        self.tenant_id    = tenant_id
        self.resource_id  = resource_id
        self.resource_type = resource_type
        self.payload      = payload
        self.timestamp    = datetime.now(tz=timezone.utc)
        self.signature    = sign_audit_event(
            self.event_id, self.timestamp, actor_id, tenant_id,
            event_type.value, payload,
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "event_id":     self.event_id,
            "event_type":   self.event_type.value,
            "actor_id":     self.actor_id,
            "tenant_id":    self.tenant_id,
            "resource_id":  self.resource_id,
            "resource_type": self.resource_type,
            "payload":      self.payload,
            "timestamp":    self.timestamp.isoformat(),
            "signature":    self.signature,
        }


class AuditLog:
    """
    Buffered, append-only audit log service.

    write() is synchronous (fire-and-forget into buffer).
    flush() persists the buffer to the database.
    Background task calls flush() periodically.
    """

    BUFFER_SIZE  = 100
    FLUSH_SEC    = 5.0

    def __init__(self) -> None:
        self._buffer: Deque[AuditEvent] = deque()
        self._lock   = asyncio.Lock()

    def write(
        self,
        event_type:    AuditEventType,
        actor_id:      str,
        tenant_id:     str,
        payload:       Dict[str, Any],
        resource_id:   Optional[str] = None,
        resource_type: Optional[str] = None,
    ) -> AuditEvent:
        """
        Create and buffer an audit event.
        Returns the event (including its signature) for immediate reference.
        """
        event = AuditEvent(
            event_type=event_type,
            actor_id=actor_id,
            tenant_id=tenant_id,
            payload=payload,
            resource_id=resource_id,
            resource_type=resource_type,
        )
        self._buffer.append(event)
        log.debug("Audit event buffered: %s actor=%s", event_type.value, actor_id)
        return event

    async def flush(self, session: Optional[AsyncSession] = None) -> int:
        """
        Flush buffered events to the database.
        Returns the number of events written.

        If no session is provided, logs to stderr (emergency fallback).
        """
        async with self._lock:
            if not self._buffer:
                return 0

            events: List[AuditEvent] = []
            while self._buffer:
                events.append(self._buffer.popleft())

        if session is None:
            # Emergency fallback — structured log output
            for e in events:
                log.warning("AUDIT (unflushed): %s", e.as_dict())
            return len(events)

        try:
            # Bulk INSERT — no ON CONFLICT UPDATE, no DELETE ever
            from sqlalchemy import text
            rows = [e.as_dict() for e in events]
            await session.execute(
                text("""
                    INSERT INTO audit.audit_events
                        (event_id, event_type, actor_id, tenant_id,
                         resource_id, resource_type, payload, timestamp, signature)
                    VALUES
                        (:event_id, :event_type, :actor_id, :tenant_id,
                         :resource_id, :resource_type, :payload::jsonb,
                         :timestamp, :signature)
                """),
                rows,
            )
            await session.commit()
            log.info("Audit log: flushed %d events", len(events))
            return len(events)

        except Exception as e:
            log.error("Audit log flush failed: %s — re-buffering events", e)
            async with self._lock:
                for event in reversed(events):
                    self._buffer.appendleft(event)
            raise


audit_log = AuditLog()
