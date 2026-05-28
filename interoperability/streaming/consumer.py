"""
Interoperability event consumer.

Subscribes to event bus topics and routes incoming messages to registered
handlers. Supports both in-memory (InMemoryEventBus) and Kafka consumption
with consumer group coordination.

Consumer group semantics
────────────────────────
  Each consumer is assigned to a named group. For Kafka, multiple consumers
  in the same group share partitions (parallel processing). For the in-memory
  bus, all consumers in a group receive all messages (fan-out).

Usage
─────
  consumer = InteropConsumer(group="audit_engine")
  consumer.register("evidentrx.t_001.canonical.dispense", handle_dispense)
  await consumer.start()
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Union

from interoperability.streaming.event_bus import (
    BusMessage,
    EventBus,
    InMemoryEventBus,
    KafkaEventBus,
    get_event_bus,
)

log = logging.getLogger("evidentrx.interop.streaming.consumer")

AsyncHandler = Callable[[BusMessage], Coroutine[Any, Any, None]]
SyncHandler  = Callable[[BusMessage], None]
AnyHandler   = Union[AsyncHandler, SyncHandler]


@dataclass
class ConsumerStats:
    received:     int = 0
    processed:    int = 0
    failed:       int = 0
    last_recv_at: datetime | None = None


class InteropConsumer:
    """
    Event consumer that subscribes to one or more topics.

    Dispatches incoming BusMessages to registered async or sync handlers.
    Handlers are called sequentially per message; errors are logged but
    do not stop consumption.
    """

    def __init__(
        self,
        group:     str         = "default",
        event_bus: EventBus | None = None,
    ) -> None:
        self._group       = group
        self._bus         = event_bus or get_event_bus()
        self._handlers:   dict[str, list[AnyHandler]] = {}
        self._running     = False
        self._stats       = ConsumerStats()
        self._kafka_consumers: list[Any] = []

    # ── Handler registration ───────────────────────────────────────────────────

    def register(self, topic: str, handler: AnyHandler) -> None:
        """Register a handler for a topic. Multiple handlers per topic allowed."""
        if topic not in self._handlers:
            self._handlers[topic] = []
        self._handlers[topic].append(handler)
        log.debug("Consumer [%s]: registered handler for %s", self._group, topic)

    def register_many(self, topic_handlers: dict[str, AnyHandler]) -> None:
        """Register multiple topic→handler pairs at once."""
        for topic, handler in topic_handlers.items():
            self.register(topic, handler)

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start consuming from all registered topics."""
        self._running = True

        if isinstance(self._bus, InMemoryEventBus):
            await self._start_in_memory()
        elif isinstance(self._bus, KafkaEventBus):
            await self._start_kafka()
        else:
            # Generic: use EventBus.subscribe for each topic
            for topic, handlers in self._handlers.items():
                for handler in handlers:
                    await self._bus.subscribe(topic, handler, group=self._group)

        log.info(
            "InteropConsumer [%s]: started on %d topics",
            self._group, len(self._handlers),
        )

    async def stop(self) -> None:
        """Stop all consumers gracefully."""
        self._running = False

        for kc in self._kafka_consumers:
            try:
                await kc.stop()
            except Exception as exc:
                log.warning("Consumer [%s]: error stopping Kafka consumer: %s", self._group, exc)

        self._kafka_consumers.clear()
        log.info(
            "Consumer [%s]: stopped (received=%d, failed=%d)",
            self._group, self._stats.received, self._stats.failed,
        )

    # ── In-memory consumption ──────────────────────────────────────────────────

    async def _start_in_memory(self) -> None:
        """Wire handlers directly into the InMemoryEventBus."""
        bus = self._bus
        assert isinstance(bus, InMemoryEventBus)
        for topic, handlers in self._handlers.items():
            for handler in handlers:
                await bus.subscribe(topic, self._wrap_handler(handler), group=self._group)

        if not bus._running:
            await bus.start()

    # ── Kafka consumption ──────────────────────────────────────────────────────

    async def _start_kafka(self) -> None:
        """Start aiokafka consumers for each registered topic."""
        try:
            from aiokafka import AIOKafkaConsumer  # type: ignore[import]
        except ImportError:
            raise RuntimeError("aiokafka is required for Kafka consumption. pip install aiokafka")

        bus = self._bus
        assert isinstance(bus, KafkaEventBus)

        topics = list(self._handlers.keys())
        if not topics:
            return

        consumer = AIOKafkaConsumer(
            *topics,
            bootstrap_servers = bus._bootstrap,
            group_id          = self._group,
            auto_offset_reset = "earliest",
        )
        await consumer.start()
        self._kafka_consumers.append(consumer)
        asyncio.create_task(self._kafka_poll_loop(consumer))

    async def _kafka_poll_loop(self, consumer: Any) -> None:
        """Poll the Kafka consumer and dispatch messages to handlers."""
        try:
            async for kafka_msg in consumer:
                if not self._running:
                    break
                try:
                    msg = BusMessage.from_bytes(kafka_msg.value)
                    await self._dispatch(msg)
                except Exception as exc:
                    log.error(
                        "Consumer [%s]: error processing Kafka message: %s",
                        self._group, exc,
                    )
                    self._stats.failed += 1
        except asyncio.CancelledError:
            pass

    # ── Dispatch ───────────────────────────────────────────────────────────────

    async def _dispatch(self, msg: BusMessage) -> None:
        self._stats.received += 1
        self._stats.last_recv_at = datetime.now(tz=UTC)

        handlers = self._handlers.get(msg.topic, [])
        for handler in handlers:
            try:
                result = handler(msg)
                if asyncio.iscoroutine(result):
                    await result
                self._stats.processed += 1
            except Exception as exc:
                log.error(
                    "Consumer [%s]: handler error for %s: %s",
                    self._group, msg.topic, exc,
                )
                self._stats.failed += 1

    def _wrap_handler(self, handler: AnyHandler) -> AnyHandler:
        """Wrap a handler to update stats on each invocation."""
        async def _wrapped(msg: BusMessage) -> None:
            await self._dispatch(msg)
        return _wrapped

    @property
    def stats(self) -> ConsumerStats:
        return self._stats
