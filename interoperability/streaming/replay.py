"""
Event replay support.

Re-processes raw records stored in the event bus (or a raw record store)
through the full normalisation pipeline. Used for:
  - Recovering from normaliser bugs (re-run after a fix)
  - Schema migrations (re-map old records to new canonical format)
  - Audit trail reconstruction (prove what was ingested and when)
  - Dead-letter replay (retry records that failed normalisation)

Replay guarantees
─────────────────
  - Idempotent: replayed records produce the same canonical checksum
    as the original run (deterministic normalisation contract)
  - Non-destructive: replay writes to a separate canonical topic suffix
    (_replay) unless promote=True, which writes to the live canonical topic
  - Lineage-tagged: all replayed records carry _replayed=True and
    _replay_id in their lineage metadata
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime    import datetime, timezone
from typing      import Any, AsyncIterator, Callable, Optional

from interoperability.streaming.event_bus import (
    BusMessage,
    EventBus,
    InMemoryEventBus,
    get_event_bus,
    canonical_topic,
    raw_topic,
)
from interoperability.streaming.producer import InteropProducer, get_producer

log = logging.getLogger("evidentrx.interop.streaming.replay")


@dataclass
class ReplaySpec:
    """Specifies what to replay and how."""
    tenant_id:      str
    source_system:  str                     # which raw topic to replay
    resource_type:  Optional[str]   = None  # filter by resource type (None = all)
    since:          Optional[datetime] = None
    until:          Optional[datetime] = None
    max_records:    Optional[int]   = None
    promote:        bool            = False  # write to live topic vs. _replay topic
    dry_run:        bool            = False  # normalise but do not publish


@dataclass
class ReplayResult:
    replay_id:     str
    spec:          ReplaySpec
    started_at:    datetime
    finished_at:   Optional[datetime]   = None
    replayed:      int                  = 0
    failed:        int                  = 0
    skipped:       int                  = 0
    errors:        list[str]            = field(default_factory=list)

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None

    @property
    def success_rate(self) -> float:
        total = self.replayed + self.failed
        return self.replayed / total if total > 0 else 1.0


class ReplayEngine:
    """
    Replays raw records through the normalisation pipeline.

    Sources:
      1. InMemoryEventBus._history — for development / test replay
      2. DB raw_records table — for production replay from persistent store
      3. S3 raw archive — for long-term audit replay

    The engine reads raw records, re-normalises them via the MappingEngine,
    and publishes results to the appropriate topic.
    """

    def __init__(
        self,
        event_bus:    Optional[EventBus]       = None,
        producer:     Optional[InteropProducer] = None,
        raw_loader:   Optional[Callable]        = None,
    ) -> None:
        """
        Parameters
        ----------
        raw_loader : async callable(spec) → AsyncIterator[dict]
            Custom raw record loader. Falls back to in-memory bus history
            if not provided.
        """
        self._bus        = event_bus or get_event_bus()
        self._producer   = producer  or get_producer()
        self._raw_loader = raw_loader

    async def replay(self, spec: ReplaySpec) -> ReplayResult:
        """
        Execute a replay according to the given spec.

        Returns a ReplayResult with counts and any errors encountered.
        """
        replay_id = str(uuid.uuid4())
        started   = datetime.now(tz=timezone.utc)
        result    = ReplayResult(
            replay_id  = replay_id,
            spec       = spec,
            started_at = started,
        )

        log.info(
            "Replay [%s]: starting %s/%s tenant=%s dry_run=%s",
            replay_id[:8],
            spec.source_system,
            spec.resource_type or "*",
            spec.tenant_id,
            spec.dry_run,
        )

        async for raw_record in self._load_raw(spec):
            if spec.max_records and (result.replayed + result.failed) >= spec.max_records:
                break

            try:
                canonical = await self._normalise(raw_record, spec)
                if canonical is None:
                    result.skipped += 1
                    continue

                # Tag as replay
                canonical["_replayed"]  = True
                canonical["_replay_id"] = replay_id

                if not spec.dry_run:
                    topic_suffix = "" if spec.promote else "_replay"
                    ctype = canonical.get("canonical_type", "unknown")
                    topic = canonical_topic(spec.tenant_id, ctype) + topic_suffix
                    msg   = BusMessage(
                        topic         = topic,
                        payload       = canonical,
                        partition_key = f"{spec.tenant_id}:{ctype}",
                    )
                    await self._bus.publish(msg)

                result.replayed += 1

            except Exception as exc:
                result.failed += 1
                err_msg = f"Replay failed for record: {exc}"
                result.errors.append(err_msg)
                log.warning("Replay [%s]: %s", replay_id[:8], err_msg)

        result.finished_at = datetime.now(tz=timezone.utc)
        log.info(
            "Replay [%s]: done — replayed=%d failed=%d skipped=%d duration=%.1fs",
            replay_id[:8],
            result.replayed,
            result.failed,
            result.skipped,
            result.duration_seconds or 0,
        )
        return result

    async def _load_raw(self, spec: ReplaySpec) -> AsyncIterator[dict[str, Any]]:
        """Load raw records for replay."""
        if self._raw_loader is not None:
            async for record in self._raw_loader(spec):
                yield record
            return

        # Fallback: replay from in-memory bus history
        if isinstance(self._bus, InMemoryEventBus):
            topic  = raw_topic(spec.tenant_id, spec.source_system)
            history = self._bus.get_messages(topic)
            for msg in history:
                record = msg.payload
                # Apply time filter
                if spec.since and msg.timestamp < spec.since:
                    continue
                if spec.until and msg.timestamp > spec.until:
                    continue
                yield record
        else:
            log.warning(
                "Replay [%s]: no raw_loader provided and bus is not InMemory — no records to replay",
                spec.tenant_id,
            )

    async def _normalise(
        self,
        raw: dict[str, Any],
        spec: ReplaySpec,
    ) -> Optional[dict[str, Any]]:
        """Re-normalise a raw record via the mapping engine."""
        from interoperability.mapping.engine import get_mapping_engine
        engine = get_mapping_engine()
        result = engine.map(
            raw_record    = raw,
            source        = spec.source_system,
            tenant_id     = spec.tenant_id,
            resource_type = spec.resource_type,
        )
        if not result.success:
            raise RuntimeError(f"Normalisation failed: {result.errors}")
        return result.canonical
