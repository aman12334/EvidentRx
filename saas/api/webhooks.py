"""
Webhook delivery — tenant-registered endpoints for push event delivery.

Tenants register HTTPS endpoints to receive real-time events (case
escalations, compliance alerts, batch completions). The dispatcher
signs each payload with a per-endpoint secret (HMAC-SHA256) so the
receiver can verify authenticity.

Delivery guarantees
───────────────────
- At-least-once: failed deliveries are retried up to MAX_RETRIES times
- Retry backoff: exponential with base 5 s, capped at 5 minutes
- Dead-letter: permanently failed deliveries are stored for inspection
- Tenant isolation: an endpoint registered by tenant A can never receive
  events from tenant B
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import uuid
from dataclasses import dataclass, field
from datetime    import datetime, timezone
from enum        import Enum
from typing      import Any, Callable, Optional

log = logging.getLogger("evidentrx.saas.api.webhooks")

_MAX_RETRIES     = 5
_BASE_BACKOFF_S  = 5
_MAX_BACKOFF_S   = 300


class WebhookEventType(str, Enum):
    INVESTIGATION_ESCALATED   = "investigation.escalated"
    INVESTIGATION_RESOLVED    = "investigation.resolved"
    INVESTIGATION_ASSIGNED    = "investigation.assigned"
    COMPLIANCE_ALERT          = "compliance.alert"
    BATCH_COMPLETED           = "batch.completed"
    QUOTA_WARNING             = "quota.warning"
    QUOTA_CRITICAL            = "quota.critical"
    TEMPLATE_UPGRADE          = "marketplace.template_upgrade"
    RULE_PACK_UPDATED         = "config.rule_pack_updated"
    API_KEY_EXPIRING          = "api.key_expiring"


class DeliveryStatus(str, Enum):
    PENDING     = "pending"
    DELIVERED   = "delivered"
    FAILED      = "failed"
    RETRYING    = "retrying"
    DEAD_LETTER = "dead_letter"


@dataclass
class WebhookEndpoint:
    """A tenant-registered webhook destination."""
    endpoint_id:  str
    tenant_id:    str
    url:          str
    secret:       str                   # HMAC signing secret (stored hashed in prod)
    name:         str
    event_types:  list[WebhookEventType] = field(default_factory=list)  # [] = all events
    active:       bool                  = True
    created_by:   str                   = "system"
    created_at:   datetime              = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    failure_count: int                  = 0
    last_success_at: Optional[datetime] = None
    metadata:     dict[str, Any]        = field(default_factory=dict)

    def subscribes_to(self, event_type: WebhookEventType) -> bool:
        return not self.event_types or event_type in self.event_types

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoint_id":  self.endpoint_id,
            "tenant_id":    self.tenant_id,
            "url":          self.url,
            "name":         self.name,
            "event_types":  [e.value for e in self.event_types],
            "active":       self.active,
            "created_at":   self.created_at.isoformat(),
            "failure_count":self.failure_count,
        }


@dataclass
class WebhookEvent:
    """A single event to be delivered to one or more endpoints."""
    event_id:    str
    tenant_id:   str
    event_type:  WebhookEventType
    payload:     dict[str, Any]
    occurred_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id":    self.event_id,
            "tenant_id":   self.tenant_id,
            "event_type":  self.event_type.value,
            "payload":     self.payload,
            "occurred_at": self.occurred_at.isoformat(),
        }


@dataclass
class DeliveryAttempt:
    """One delivery attempt for a WebhookEvent → WebhookEndpoint pair."""
    attempt_id:   str
    event_id:     str
    endpoint_id:  str
    tenant_id:    str
    attempt_num:  int
    status:       DeliveryStatus
    attempted_at: datetime
    response_code: Optional[int]  = None
    error:         Optional[str]  = None
    next_retry_at: Optional[datetime] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_id":   self.attempt_id,
            "event_id":     self.event_id,
            "endpoint_id":  self.endpoint_id,
            "attempt_num":  self.attempt_num,
            "status":       self.status.value,
            "attempted_at": self.attempted_at.isoformat(),
            "response_code":self.response_code,
            "error":        self.error,
        }


def _sign_payload(payload_bytes: bytes, secret: str) -> str:
    """HMAC-SHA256 signature for payload verification."""
    return hmac.new(
        secret.encode(),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()


class WebhookDispatcher:
    """
    Manages endpoint registration and event delivery.

    The HTTP transport is injected as a callable so the dispatcher
    remains testable without live HTTP connections.

    http_client signature: async fn(url, headers, body_bytes) → (status_code, error_str)
    """

    def __init__(
        self,
        http_client: Optional[Callable] = None,
    ) -> None:
        # endpoint_id → WebhookEndpoint
        self._endpoints: dict[str, WebhookEndpoint] = {}
        # event_id → WebhookEvent
        self._events:    dict[str, WebhookEvent]    = {}
        # attempt_id → DeliveryAttempt
        self._attempts:  dict[str, DeliveryAttempt] = {}
        self._http       = http_client

    # ── Endpoint management ────────────────────────────────────────────────────

    def register_endpoint(
        self,
        tenant_id:    str,
        url:          str,
        name:         str,
        created_by:   str,
        event_types:  Optional[list[WebhookEventType]] = None,
        metadata:     Optional[dict[str, Any]]         = None,
    ) -> WebhookEndpoint:
        secret = secrets.token_hex(32)
        ep = WebhookEndpoint(
            endpoint_id = str(uuid.uuid4()),
            tenant_id   = tenant_id,
            url         = url,
            secret      = secret,
            name        = name,
            event_types = event_types or [],
            created_by  = created_by,
            metadata    = metadata or {},
        )
        self._endpoints[ep.endpoint_id] = ep
        log.info(
            "WebhookDispatcher: registered endpoint '%s' for tenant %s → %s",
            name, tenant_id[:8], url,
        )
        return ep

    def deactivate_endpoint(self, tenant_id: str, endpoint_id: str) -> None:
        ep = self._get_endpoint(tenant_id, endpoint_id)
        ep.active = False

    def list_endpoints(
        self,
        tenant_id:   str,
        active_only: bool = True,
    ) -> list[WebhookEndpoint]:
        return [
            ep for ep in self._endpoints.values()
            if ep.tenant_id == tenant_id
            and (not active_only or ep.active)
        ]

    # ── Event dispatch ─────────────────────────────────────────────────────────

    async def dispatch_event(
        self,
        tenant_id:  str,
        event_type: WebhookEventType,
        payload:    dict[str, Any],
    ) -> list[DeliveryAttempt]:
        event = WebhookEvent(
            event_id   = str(uuid.uuid4()),
            tenant_id  = tenant_id,
            event_type = event_type,
            payload    = payload,
        )
        self._events[event.event_id] = event

        targets = [
            ep for ep in self._endpoints.values()
            if ep.tenant_id == tenant_id
            and ep.active
            and ep.subscribes_to(event_type)
        ]

        attempts: list[DeliveryAttempt] = []
        for ep in targets:
            attempt = await self._deliver(event, ep, attempt_num=1)
            attempts.append(attempt)

        return attempts

    async def retry_pending(self) -> list[DeliveryAttempt]:
        """Re-attempt deliveries that are in RETRYING status and due."""
        now     = datetime.now(tz=timezone.utc)
        retried: list[DeliveryAttempt] = []
        for attempt in list(self._attempts.values()):
            if attempt.status != DeliveryStatus.RETRYING:
                continue
            if attempt.next_retry_at and attempt.next_retry_at > now:
                continue
            event = self._events.get(attempt.event_id)
            ep    = self._endpoints.get(attempt.endpoint_id)
            if not event or not ep:
                continue
            new_attempt = await self._deliver(event, ep, attempt.attempt_num + 1)
            retried.append(new_attempt)
        return retried

    async def _deliver(
        self,
        event:       WebhookEvent,
        ep:          WebhookEndpoint,
        attempt_num: int,
    ) -> DeliveryAttempt:
        body    = json.dumps(event.to_dict()).encode("utf-8")
        sig     = _sign_payload(body, ep.secret)
        headers = {
            "Content-Type":           "application/json",
            "X-EvidentRx-Signature":  f"sha256={sig}",
            "X-EvidentRx-Event":      event.event_type.value,
            "X-EvidentRx-Event-ID":   event.event_id,
        }

        status_code: Optional[int] = None
        error:       Optional[str] = None

        if self._http:
            try:
                status_code, error = await self._http(ep.url, headers, body)
            except Exception as exc:
                error = str(exc)
        else:
            # No HTTP client configured — log and mark as failed
            log.debug("WebhookDispatcher: no HTTP client; skipping delivery to %s", ep.url)
            error = "no_http_client"

        delivered  = status_code is not None and 200 <= status_code < 300
        now        = datetime.now(tz=timezone.utc)

        if delivered:
            ep.failure_count   = 0
            ep.last_success_at = now
            del_status = DeliveryStatus.DELIVERED
            next_retry = None
        elif attempt_num >= _MAX_RETRIES:
            ep.failure_count += 1
            del_status = DeliveryStatus.DEAD_LETTER
            next_retry = None
            log.error(
                "WebhookDispatcher: dead-letter for endpoint %s event %s",
                ep.endpoint_id[:8], event.event_id[:8],
            )
        else:
            ep.failure_count += 1
            del_status = DeliveryStatus.RETRYING
            backoff    = min(_BASE_BACKOFF_S * (2 ** (attempt_num - 1)), _MAX_BACKOFF_S)
            from datetime import timedelta
            next_retry = now + timedelta(seconds=backoff)

        attempt = DeliveryAttempt(
            attempt_id    = str(uuid.uuid4()),
            event_id      = event.event_id,
            endpoint_id   = ep.endpoint_id,
            tenant_id     = ep.tenant_id,
            attempt_num   = attempt_num,
            status        = del_status,
            attempted_at  = now,
            response_code = status_code,
            error         = error,
            next_retry_at = next_retry,
        )
        self._attempts[attempt.attempt_id] = attempt
        return attempt

    # ── Queries ────────────────────────────────────────────────────────────────

    def delivery_history(
        self,
        tenant_id:   str,
        endpoint_id: Optional[str] = None,
        limit:       int           = 50,
    ) -> list[DeliveryAttempt]:
        attempts = [
            a for a in self._attempts.values()
            if a.tenant_id == tenant_id
            and (endpoint_id is None or a.endpoint_id == endpoint_id)
        ]
        attempts.sort(key=lambda a: a.attempted_at, reverse=True)
        return attempts[:limit]

    def dead_letters(self, tenant_id: str) -> list[DeliveryAttempt]:
        return [
            a for a in self._attempts.values()
            if a.tenant_id == tenant_id and a.status == DeliveryStatus.DEAD_LETTER
        ]

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _get_endpoint(self, tenant_id: str, endpoint_id: str) -> WebhookEndpoint:
        ep = self._endpoints.get(endpoint_id)
        if ep is None or ep.tenant_id != tenant_id:
            raise WebhookError(f"Endpoint {endpoint_id} not found")
        return ep


# ── Exceptions ─────────────────────────────────────────────────────────────────

class WebhookError(Exception):
    pass


# ── Singleton ──────────────────────────────────────────────────────────────────

_dispatcher: Optional[WebhookDispatcher] = None


def get_webhook_dispatcher(
    http_client: Optional[Callable] = None,
) -> WebhookDispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = WebhookDispatcher(http_client=http_client)
    return _dispatcher
