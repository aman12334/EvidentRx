"""
API gateway — rate limiting, request authentication, and usage tracking.

The gateway sits in front of all external API endpoints and enforces:
  - API key authentication (via APIKeyStore)
  - Per-tenant / per-key rate limits (sliding window)
  - Per-endpoint usage metering (feeds UsageMeter)
  - Request logging for audit and observability

Rate limiting algorithm
───────────────────────
Sliding window counter: for a window_seconds period, count requests
issued by the same (tenant_id, key_id) pair. If count exceeds the
configured limit, the request is rejected with HTTP 429.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Deque

from saas.api.keys import APIKey, APIKeyStore, get_api_key_store

log = logging.getLogger("evidentrx.saas.api.gateway")


@dataclass
class RateLimitConfig:
    """Rate limit policy for a tier or specific endpoint."""
    requests_per_window: int    # max requests allowed in window
    window_seconds:      int    = 60
    burst_multiplier:    float  = 1.5   # short burst allowance


@dataclass
class GatewayRequest:
    """Minimal request context passed to the gateway."""
    raw_key:    str            # API key from Authorization header
    endpoint:   str            # e.g. "/api/v1/investigations"
    method:     str            = "GET"
    org_id:     str | None = None
    client_ip:  str | None = None
    request_id: str            = field(default_factory=lambda: f"req_{int(time.monotonic()*1e6)}")


@dataclass
class GatewayResponse:
    """Outcome of a gateway check."""
    allowed:      bool
    api_key:      APIKey | None
    tenant_id:    str | None
    request_id:   str
    deny_reason:  str | None          = None
    rate_limit_remaining: int | None  = None
    rate_limit_reset_at:  int | None  = None  # Unix timestamp


class _SlidingWindow:
    """Thread-unsafe sliding window counter (single-process use)."""

    def __init__(self, window_seconds: int) -> None:
        self._window = window_seconds
        self._times: Deque[float] = deque()

    def record_and_count(self) -> int:
        now = time.monotonic()
        cutoff = now - self._window
        while self._times and self._times[0] < cutoff:
            self._times.popleft()
        self._times.append(now)
        return len(self._times)

    def current_count(self) -> int:
        now = time.monotonic()
        cutoff = now - self._window
        return sum(1 for t in self._times if t >= cutoff)

    def reset_at(self) -> float:
        """Approximate Unix timestamp when the oldest request ages out."""
        if not self._times:
            return time.time()
        return time.time() + (self._times[0] + self._window - time.monotonic())


class APIGateway:
    """
    Stateful API gateway enforcing authentication and rate limits.

    Rate limit configs are looked up per tenant_id; if none is configured
    the platform default is applied.
    """

    _DEFAULT_LIMIT = RateLimitConfig(requests_per_window=1000, window_seconds=60)

    def __init__(
        self,
        key_store:       APIKeyStore | None                    = None,
        usage_recorder:  Callable | None                       = None,
        platform_config: RateLimitConfig | None                = None,
    ) -> None:
        self._key_store      = key_store or get_api_key_store()
        self._usage_recorder = usage_recorder     # async fn(tenant_id, endpoint, org_id)
        self._platform_cfg   = platform_config or self._DEFAULT_LIMIT
        # Per-tenant rate limit overrides
        self._tenant_configs: dict[str, RateLimitConfig] = {}
        # (tenant_id, key_id) → _SlidingWindow
        self._windows: dict[tuple[str, str], _SlidingWindow] = {}
        # Running request stats
        self._request_count: int = 0
        self._deny_count:    int = 0

    # ── Configuration ──────────────────────────────────────────────────────────

    def set_tenant_rate_limit(
        self,
        tenant_id: str,
        config:    RateLimitConfig,
    ) -> None:
        self._tenant_configs[tenant_id] = config
        log.info(
            "APIGateway: set rate limit for tenant %s: %d req/%ds",
            tenant_id[:8], config.requests_per_window, config.window_seconds,
        )

    # ── Request processing ─────────────────────────────────────────────────────

    async def process(self, req: GatewayRequest) -> GatewayResponse:
        """
        Authenticate and rate-limit an inbound API request.

        Returns a GatewayResponse indicating whether the request is
        allowed. Callers must check ``allowed`` before proceeding.
        """
        self._request_count += 1

        # 1. Authenticate
        api_key = self._key_store.authenticate(req.raw_key)
        if api_key is None:
            self._deny_count += 1
            log.warning(
                "APIGateway: auth failed for request %s from %s",
                req.request_id, req.client_ip or "unknown",
            )
            return GatewayResponse(
                allowed    = False,
                api_key    = None,
                tenant_id  = None,
                request_id = req.request_id,
                deny_reason = "invalid_api_key",
            )

        # 2. Rate limit
        cfg    = self._tenant_configs.get(api_key.tenant_id, self._platform_cfg)
        window = self._get_window(api_key.tenant_id, api_key.key_id, cfg.window_seconds)
        count  = window.record_and_count()
        burst_limit = int(cfg.requests_per_window * cfg.burst_multiplier)

        if count > burst_limit:
            self._deny_count += 1
            log.warning(
                "APIGateway: rate limit exceeded for tenant %s key %s (%d/%d)",
                api_key.tenant_id[:8], api_key.key_id[:8], count, burst_limit,
            )
            return GatewayResponse(
                allowed    = False,
                api_key    = api_key,
                tenant_id  = api_key.tenant_id,
                request_id = req.request_id,
                deny_reason = "rate_limit_exceeded",
                rate_limit_remaining = 0,
                rate_limit_reset_at  = int(window.reset_at()),
            )

        # 3. Record usage (fire-and-forget; errors must not block request)
        if self._usage_recorder:
            try:
                await self._usage_recorder(
                    api_key.tenant_id,
                    req.endpoint,
                    req.org_id or api_key.org_id,
                )
            except Exception as exc:
                log.error("APIGateway: usage recording failed: %s", exc)

        remaining = max(0, cfg.requests_per_window - count)
        return GatewayResponse(
            allowed               = True,
            api_key               = api_key,
            tenant_id             = api_key.tenant_id,
            request_id            = req.request_id,
            rate_limit_remaining  = remaining,
            rate_limit_reset_at   = int(window.reset_at()),
        )

    # ── Stats ──────────────────────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        return {
            "total_requests":  self._request_count,
            "denied_requests": self._deny_count,
            "allow_rate": (
                round((self._request_count - self._deny_count) / self._request_count, 4)
                if self._request_count > 0 else 1.0
            ),
            "active_windows": len(self._windows),
        }

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _get_window(
        self,
        tenant_id:      str,
        key_id:         str,
        window_seconds: int,
    ) -> _SlidingWindow:
        k = (tenant_id, key_id)
        if k not in self._windows:
            self._windows[k] = _SlidingWindow(window_seconds)
        return self._windows[k]


# ── Singleton ──────────────────────────────────────────────────────────────────

_gateway: APIGateway | None = None


def get_api_gateway(
    key_store:      APIKeyStore | None   = None,
    usage_recorder: Callable | None      = None,
    platform_config: RateLimitConfig | None = None,
) -> APIGateway:
    global _gateway
    if _gateway is None:
        _gateway = APIGateway(
            key_store       = key_store,
            usage_recorder  = usage_recorder,
            platform_config = platform_config,
        )
    return _gateway
