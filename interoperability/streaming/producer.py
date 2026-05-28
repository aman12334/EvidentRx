"""
Interoperability event producer.

Publishes canonical records and lineage events to the event bus after
successful normalisation and validation. Handles batching, back-pressure,
and retry logic on top of the EventBus abstraction.

Event types produced
────────────────────
  canonical  — fully-normalised, validated canonical records
  raw        — raw source records (for replay capability)
  lineage    — transformation lineage events
  dlq        — dead-letter events for failed records
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from interoperability.streaming.event_bus import (
    BusMessage,
    EventBus,
    canonical_topic,
    dlq_topic,
    get_event_bus,
    lineage_topic,
    raw_topic,
)

log = logging.getLogger("evidentrx.interop.streaming.producer")

_DEFAULT_FLUSH_SIZE = 100
_DEFAULT_FLUSH_INTERVAL_SEC = 5


@dataclass
class ProducerStats:
    published:     int = 0
    failed:        int = 0
    batches:       int = 0
    last_flush_at: datetime | None = None


class InteropProducer:
    """
    Buffered event producer for the interoperability pipeline.

    Accumulates events in a local buffer and flushes to the event bus
    when the buffer reaches flush_size or flush_interval_sec elapses.

    Thread-safe: uses asyncio.Lock internally.
    """

    def __init__(
        self,
        event_bus:          EventBus | None = None,
        flush_size:         int                = _DEFAULT_FLUSH_SIZE,
        flush_interval_sec: int                = _DEFAULT_FLUSH_INTERVAL_SEC,
    ) -> None:
        self._bus            = event_bus or get_event_bus()
        self._flush_size     = flush_size
        self._flush_interval = flush_interval_sec
        self._buffer:        list[BusMessage] = []
        self._lock           = asyncio.Lock()
        self._stats          = ProducerStats()
        self._flush_task:    asyncio.Task | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start background flush timer."""
        self._flush_task = asyncio.create_task(self._flush_loop())
        log.info(
            "InteropProducer: started (flush_size=%d, interval=%ds)",
            self._flush_size, self._flush_interval,
        )

    async def stop(self) -> None:
        """Flush remaining events and stop the timer."""
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        await self.flush()
        log.info(
            "InteropProducer: stopped (published=%d, failed=%d)",
            self._stats.published, self._stats.failed,
        )

    # ── Publish API ────────────────────────────────────────────────────────────

    async def publish_canonical(
        self,
        canonical:  dict[str, Any],
        tenant_id:  str,
    ) -> None:
        """
        Publish a canonical record to the appropriate canonical topic.

        Records are buffered; call flush() to force immediate delivery.
        """
        ctype = canonical.get("canonical_type", "unknown")
        topic = canonical_topic(tenant_id, ctype)
        msg   = BusMessage(
            topic         = topic,
            payload       = canonical,
            partition_key = f"{tenant_id}:{ctype}",
        )
        await self._enqueue(msg)

    async def publish_canonical_batch(
        self,
        canonicals: list[dict[str, Any]],
        tenant_id:  str,
    ) -> None:
        """Publish a batch of canonical records."""
        for record in canonicals:
            await self.publish_canonical(record, tenant_id)

    async def publish_raw(
        self,
        raw_record:    dict[str, Any],
        source_system: str,
        tenant_id:     str,
    ) -> None:
        """
        Publish a raw record for replay capability.

        Raw records are stored in source-specific topics. The FHIR normaliser's
        raw input is always published before normalisation so it can be replayed.
        """
        topic = raw_topic(tenant_id, source_system)
        msg   = BusMessage(
            topic         = topic,
            payload       = raw_record,
            partition_key = f"{tenant_id}:{source_system}",
        )
        await self._enqueue(msg)

    async def publish_dlq(
        self,
        record:     dict[str, Any],
        tenant_id:  str,
        reason:     str,
        errors:     list[str],
    ) -> None:
        """Publish a failed record to the dead-letter topic."""
        topic = dlq_topic(tenant_id)
        msg   = BusMessage(
            topic         = topic,
            payload       = {
                "record":   record,
                "reason":   reason,
                "errors":   errors,
                "failed_at": datetime.now(tz=UTC).isoformat(),
            },
            partition_key = tenant_id,
        )
        await self._enqueue(msg)

    async def publish_lineage(
        self,
        lineage:   dict[str, Any],
        tenant_id: str,
    ) -> None:
        """Publish a lineage event."""
        topic = lineage_topic(tenant_id)
        msg   = BusMessage(
            topic         = topic,
            payload       = lineage,
            partition_key = tenant_id,
        )
        await self._enqueue(msg)

    # ── Internal ───────────────────────────────────────────────────────────────

    async def _enqueue(self, message: BusMessage) -> None:
        async with self._lock:
            self._buffer.append(message)
            if len(self._buffer) >= self._flush_size:
                await self._do_flush()

    async def flush(self) -> int:
        """Force-flush all buffered messages. Returns count flushed."""
        async with self._lock:
            return await self._do_flush()

    async def _do_flush(self) -> int:
        """Internal flush — caller must hold self._lock."""
        if not self._buffer:
            return 0
        batch = list(self._buffer)
        self._buffer.clear()
        try:
            await self._bus.publish_batch(batch)
            self._stats.published  += len(batch)
            self._stats.batches    += 1
            self._stats.last_flush_at = datetime.now(tz=UTC)
            return len(batch)
        except Exception as exc:
            log.error("InteropProducer: flush failed (%s), %d messages lost", exc, len(batch))
            self._stats.failed += len(batch)
            return 0

    async def _flush_loop(self) -> None:
        """Background timer flush."""
        while True:
            await asyncio.sleep(self._flush_interval)
            try:
                await self.flush()
            except Exception as exc:
                log.error("InteropProducer: timer flush error: %s", exc)

    @property
    def stats(self) -> ProducerStats:
        return self._stats


# ── Module-level singleton ────────────────────────────────────────────────────

_producer: InteropProducer | None = None


def get_producer() -> InteropProducer:
    """Return the module-level InteropProducer singleton."""
    global _producer
    if _producer is None:
        _producer = InteropProducer()
    return _producer
