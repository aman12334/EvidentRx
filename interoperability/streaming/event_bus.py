"""
Event bus abstraction for healthcare interoperability streaming.

Provides a unified publish/subscribe interface over Kafka (production) or
an in-memory bus (testing / single-node). The abstraction ensures the
ingestion pipeline and downstream consumers are decoupled from the specific
message broker implementation.

Topic conventions
─────────────────
  evidentrx.{tenant_id}.canonical.{canonical_type}
    — normalised canonical records ready for persistence
  evidentrx.{tenant_id}.raw.{source_system}
    — raw records before normalisation (for replay)
  evidentrx.{tenant_id}.dlq
    — dead-letter records that failed processing
  evidentrx.{tenant_id}.lineage
    — transformation lineage events

Message format
──────────────
  Every message is a JSON-encoded dict with:
    _topic      : topic name
    _partition_key : used for Kafka key (tenant_id + canonical_type)
    _timestamp  : ISO 8601 UTC
    _event_id   : UUID
    _schema_ver : schema version (for evolution)
    payload     : the actual record dict
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger("evidentrx.interop.streaming.event_bus")

_SCHEMA_VERSION = "1.0"


# ── Message model ─────────────────────────────────────────────────────────────

@dataclass
class BusMessage:
    topic:         str
    payload:       dict[str, Any]
    partition_key: str             = ""
    event_id:      str             = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp:     datetime        = field(default_factory=lambda: datetime.now(tz=UTC))
    schema_ver:    str             = _SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "_topic":         self.topic,
            "_partition_key": self.partition_key,
            "_timestamp":     self.timestamp.isoformat(),
            "_event_id":      self.event_id,
            "_schema_ver":    self.schema_ver,
            "payload":        self.payload,
        }

    def to_bytes(self) -> bytes:
        return json.dumps(self.to_dict(), default=str).encode("utf-8")

    @classmethod
    def from_bytes(cls, data: bytes) -> BusMessage:
        d = json.loads(data.decode("utf-8"))
        return cls(
            topic         = d.get("_topic", ""),
            payload       = d.get("payload", {}),
            partition_key = d.get("_partition_key", ""),
            event_id      = d.get("_event_id", str(uuid.uuid4())),
            timestamp     = datetime.fromisoformat(d["_timestamp"]) if "_timestamp" in d else datetime.now(tz=UTC),
            schema_ver    = d.get("_schema_ver", _SCHEMA_VERSION),
        )


Handler = Callable[[BusMessage], Any]


# ── Abstract event bus ────────────────────────────────────────────────────────

class EventBus(ABC):
    """
    Abstract event bus interface.

    Concrete implementations: KafkaEventBus (production), InMemoryEventBus (test).
    """

    @abstractmethod
    async def publish(self, message: BusMessage) -> None:
        """Publish a single message to a topic. Idempotent on retry."""

    @abstractmethod
    async def publish_batch(self, messages: list[BusMessage]) -> None:
        """Publish a batch of messages atomically where possible."""

    @abstractmethod
    async def subscribe(
        self,
        topic:   str,
        handler: Handler,
        group:   str = "default",
    ) -> None:
        """Subscribe a handler to a topic. Handler is called for each message."""

    @abstractmethod
    async def close(self) -> None:
        """Flush pending messages and close connections."""


# ── In-memory event bus (testing / single-node) ───────────────────────────────

class InMemoryEventBus(EventBus):
    """
    Asyncio queue-based in-memory event bus.

    Suitable for:
      - Unit tests (no Kafka dependency)
      - Single-node development deployments
      - Integration testing with full pipeline

    Messages are delivered in order per topic. Subscribers receive all
    messages published after subscription. No persistence — messages are
    lost on restart.
    """

    def __init__(self) -> None:
        self._queues:   dict[str, asyncio.Queue] = defaultdict(asyncio.Queue)
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._history:  dict[str, list[BusMessage]] = defaultdict(list)
        self._running   = False
        self._dispatch_task: asyncio.Task | None = None

    async def publish(self, message: BusMessage) -> None:
        queue = self._queues[message.topic]
        await queue.put(message)
        self._history[message.topic].append(message)
        log.debug("InMemoryBus: published to %s (id=%s)", message.topic, message.event_id[:8])

    async def publish_batch(self, messages: list[BusMessage]) -> None:
        for msg in messages:
            await self.publish(msg)

    async def subscribe(
        self,
        topic:   str,
        handler: Handler,
        group:   str = "default",
    ) -> None:
        self._handlers[topic].append(handler)
        log.debug("InMemoryBus: subscribed handler to %s", topic)

    async def start(self) -> None:
        """Start background dispatch loop."""
        self._running = True
        self._dispatch_task = asyncio.create_task(self._dispatch_loop())

    async def close(self) -> None:
        self._running = False
        if self._dispatch_task:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass

    async def _dispatch_loop(self) -> None:
        """Drain queues and invoke handlers."""
        while self._running:
            dispatched = 0
            for topic, queue in list(self._queues.items()):
                handlers = self._handlers.get(topic, [])
                while not queue.empty():
                    msg = await queue.get()
                    for handler in handlers:
                        try:
                            result = handler(msg)
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception as exc:
                            log.error(
                                "InMemoryBus: handler error for %s: %s", topic, exc
                            )
                    dispatched += 1
            if dispatched == 0:
                await asyncio.sleep(0.01)

    def get_messages(self, topic: str) -> list[BusMessage]:
        """Return all messages ever published to a topic (for test assertions)."""
        return list(self._history.get(topic, []))

    def clear_history(self) -> None:
        """Reset message history (call between test cases)."""
        self._history.clear()


# ── Kafka event bus (production) ──────────────────────────────────────────────

class KafkaEventBus(EventBus):
    """
    Apache Kafka event bus (production implementation).

    Uses aiokafka for async Kafka producer/consumer.
    aiokafka must be installed: pip install aiokafka

    Configuration via constructor:
      bootstrap_servers : "kafka:9092" or comma-separated list
      security_protocol : "PLAINTEXT" | "SSL" | "SASL_SSL"
      sasl_mechanism    : "PLAIN" | "SCRAM-SHA-256" | "OAUTHBEARER"
      compression_type  : "gzip" | "snappy" | "lz4" (default: gzip)
    """

    def __init__(
        self,
        bootstrap_servers: str,
        security_protocol: str         = "PLAINTEXT",
        sasl_mechanism:    str | None = None,
        sasl_username:     str | None = None,
        sasl_password:     str | None = None,
        compression_type:  str         = "gzip",
        batch_size_bytes:  int         = 65536,
        linger_ms:         int         = 10,
    ) -> None:
        self._bootstrap    = bootstrap_servers
        self._security     = security_protocol
        self._sasl_mech    = sasl_mechanism
        self._sasl_user    = sasl_username
        self._sasl_pass    = sasl_password
        self._compression  = compression_type
        self._batch_bytes  = batch_size_bytes
        self._linger_ms    = linger_ms
        self._producer: Any | None = None

    async def _ensure_producer(self) -> Any:
        """Lazily initialise the aiokafka producer."""
        if self._producer is None:
            try:
                from aiokafka import AIOKafkaProducer  # type: ignore[import]
            except ImportError:
                raise RuntimeError(
                    "aiokafka is required for KafkaEventBus. "
                    "Install it: pip install aiokafka"
                )
            kwargs: dict[str, Any] = {
                "bootstrap_servers": self._bootstrap,
                "security_protocol": self._security,
                "compression_type":  self._compression,
                "max_batch_size":    self._batch_bytes,
                "linger_ms":         self._linger_ms,
                "value_serializer":  lambda v: v if isinstance(v, bytes) else v.encode(),
                "key_serializer":    lambda k: k.encode() if k else None,
            }
            if self._sasl_mech:
                kwargs["sasl_mechanism"] = self._sasl_mech
                kwargs["sasl_plain_username"] = self._sasl_user
                kwargs["sasl_plain_password"] = self._sasl_pass

            producer = AIOKafkaProducer(**kwargs)
            await producer.start()
            self._producer = producer
        return self._producer

    async def publish(self, message: BusMessage) -> None:
        producer = await self._ensure_producer()
        await producer.send(
            message.topic,
            value = message.to_bytes(),
            key   = message.partition_key or None,
        )
        log.debug("KafkaBus: published to %s", message.topic)

    async def publish_batch(self, messages: list[BusMessage]) -> None:
        producer = await self._ensure_producer()
        futures = [
            producer.send(
                msg.topic,
                value = msg.to_bytes(),
                key   = msg.partition_key or None,
            )
            for msg in messages
        ]
        await asyncio.gather(*futures)
        log.debug("KafkaBus: published batch of %d", len(messages))

    async def subscribe(
        self,
        topic:   str,
        handler: Handler,
        group:   str = "default",
    ) -> None:
        # Kafka subscription is handled by KafkaConsumer — not inline here
        raise NotImplementedError(
            "Use KafkaConsumer from streaming.consumer for Kafka subscriptions"
        )

    async def close(self) -> None:
        if self._producer:
            await self._producer.stop()
            self._producer = None


# ── Topic helpers ─────────────────────────────────────────────────────────────

def canonical_topic(tenant_id: str, canonical_type: str) -> str:
    return f"evidentrx.{tenant_id}.canonical.{canonical_type}"


def raw_topic(tenant_id: str, source_system: str) -> str:
    return f"evidentrx.{tenant_id}.raw.{source_system}"


def dlq_topic(tenant_id: str) -> str:
    return f"evidentrx.{tenant_id}.dlq"


def lineage_topic(tenant_id: str) -> str:
    return f"evidentrx.{tenant_id}.lineage"


# ── Factory ───────────────────────────────────────────────────────────────────

_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Return the configured event bus singleton."""
    global _bus
    if _bus is None:
        _bus = InMemoryEventBus()
        log.info("EventBus: using InMemoryEventBus (set KAFKA_BOOTSTRAP_SERVERS to use Kafka)")
    return _bus


def configure_kafka_bus(bootstrap_servers: str, **kwargs: Any) -> KafkaEventBus:
    """Configure and register the global Kafka event bus."""
    global _bus
    _bus = KafkaEventBus(bootstrap_servers=bootstrap_servers, **kwargs)
    return _bus  # type: ignore[return-value]
