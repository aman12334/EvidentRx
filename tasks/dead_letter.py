"""
Dead-letter queue (DLQ) handling.

When a task fails all retry attempts, it is routed to the dead-letter queue
for human review and manual replay. This prevents silent data loss and ensures
every failed task is accounted for.

DLQ workflow:
  1. Task fails → retried N times → sent to dead_letter queue
  2. Alerting rule fires → analyst notified
  3. Analyst reviews DLQ via admin API (GET /api/v1/admin/dlq)
  4. Analyst replays or discards the task
  5. DLQ item marked resolved with actor_id in audit log

Storage:
  - In-process deque (development)
  - Redis list "evidentrx:dlq" (production)
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import deque
from datetime    import datetime, timezone
from typing      import Any, Deque, Dict, List, Optional

log = logging.getLogger("evidentrx.dlq")


class DLQItem:
    """A dead-letter queue entry."""

    __slots__ = (
        "dlq_id", "task_name", "task_args", "task_kwargs",
        "error", "failed_at", "attempt_count", "tenant_id",
        "resolved", "resolved_at", "resolved_by",
    )

    def __init__(
        self,
        task_name:     str,
        task_args:     list,
        task_kwargs:   dict,
        error:         str,
        attempt_count: int,
        tenant_id:     Optional[str] = None,
    ) -> None:
        self.dlq_id        = str(uuid.uuid4())
        self.task_name     = task_name
        self.task_args     = task_args
        self.task_kwargs   = task_kwargs
        self.error         = error
        self.failed_at     = datetime.now(tz=timezone.utc)
        self.attempt_count = attempt_count
        self.tenant_id     = tenant_id
        self.resolved      = False
        self.resolved_at:  Optional[datetime] = None
        self.resolved_by:  Optional[str]      = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "dlq_id":        self.dlq_id,
            "task_name":     self.task_name,
            "task_args":     self.task_args,
            "task_kwargs":   self.task_kwargs,
            "error":         self.error,
            "failed_at":     self.failed_at.isoformat(),
            "attempt_count": self.attempt_count,
            "tenant_id":     self.tenant_id,
            "resolved":      self.resolved,
            "resolved_at":   self.resolved_at.isoformat() if self.resolved_at else None,
            "resolved_by":   self.resolved_by,
        }


class DeadLetterQueue:
    """
    In-process DLQ with audit trail.
    Production: implement RedisDeadLetterQueue using LPUSH/LRANGE.
    """

    def __init__(self, max_size: int = 1000) -> None:
        self._queue:    Deque[DLQItem] = deque(maxlen=max_size)
        self._resolved: List[DLQItem]  = []

    def enqueue(
        self,
        task_name:     str,
        task_args:     list,
        task_kwargs:   dict,
        error:         str,
        attempt_count: int,
        tenant_id:     Optional[str] = None,
    ) -> DLQItem:
        item = DLQItem(
            task_name=task_name,
            task_args=task_args,
            task_kwargs=task_kwargs,
            error=error,
            attempt_count=attempt_count,
            tenant_id=tenant_id,
        )
        self._queue.append(item)
        log.error(
            "Task sent to DLQ: %s attempts=%d error=%s dlq_id=%s",
            task_name, attempt_count, error[:200], item.dlq_id,
        )
        return item

    def list_pending(
        self, tenant_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        items = [i for i in self._queue if not i.resolved]
        if tenant_id:
            items = [i for i in items if i.tenant_id == tenant_id]
        return [i.as_dict() for i in items]

    def resolve(self, dlq_id: str, actor_id: str) -> bool:
        """Mark a DLQ item as resolved (no replay)."""
        for item in self._queue:
            if item.dlq_id == dlq_id and not item.resolved:
                item.resolved    = True
                item.resolved_at = datetime.now(tz=timezone.utc)
                item.resolved_by = actor_id
                self._resolved.append(item)
                log.info("DLQ item %s resolved by %s", dlq_id, actor_id)
                return True
        return False

    def size(self) -> int:
        return sum(1 for i in self._queue if not i.resolved)


dead_letter_queue = DeadLetterQueue()
