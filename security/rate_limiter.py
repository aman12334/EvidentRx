"""
Sliding-window rate limiter for API abuse protection.

Algorithm: Token bucket with sliding window.
  - Global limit: configurable per-minute requests per IP
  - Per-user limit: separate bucket per authenticated user_id
  - Burst allowance: allows short bursts above sustained rate
  - 429 response with Retry-After header on limit exceeded

Production: back the store with Redis (INCR + EXPIRE pattern).
Development: in-process dict (single-worker only).
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from typing      import Deque, Dict, Optional, Tuple

from config.settings import settings


class _WindowBucket:
    """Sliding window counter using a deque of timestamps."""

    def __init__(self, limit: int, window_sec: float) -> None:
        self.limit      = limit
        self.window_sec = window_sec
        self._times: Deque[float] = deque()

    def is_allowed(self) -> Tuple[bool, int]:
        """
        Record a request attempt.
        Returns (allowed, remaining_requests).
        """
        now = time.monotonic()
        cutoff = now - self.window_sec

        # Evict old entries
        while self._times and self._times[0] < cutoff:
            self._times.popleft()

        count = len(self._times)
        if count >= self.limit:
            return False, 0

        self._times.append(now)
        return True, self.limit - count - 1

    def retry_after(self) -> float:
        """Seconds until the oldest request falls outside the window."""
        if not self._times:
            return 0.0
        now = time.monotonic()
        oldest = self._times[0]
        return max(0.0, self.window_sec - (now - oldest))


class RateLimiter:
    """
    Per-IP and per-user rate limiting.

    Usage:
        allowed, remaining, retry_after = await rate_limiter.check(
            key="192.168.1.1",
            limit=60,
            window_sec=60.0,
        )
    """

    def __init__(self) -> None:
        self._buckets: Dict[str, _WindowBucket] = defaultdict(
            lambda: _WindowBucket(
                limit=settings.rate_limit_per_minute,
                window_sec=60.0,
            )
        )
        self._lock = asyncio.Lock()

    async def check(
        self,
        key:        str,
        limit:      Optional[int] = None,
        window_sec: float = 60.0,
    ) -> Tuple[bool, int, float]:
        """
        Check and record a request for the given key.
        Returns (allowed, remaining, retry_after_seconds).
        """
        async with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None or bucket.limit != (limit or settings.rate_limit_per_minute):
                bucket = _WindowBucket(
                    limit=limit or settings.rate_limit_per_minute,
                    window_sec=window_sec,
                )
                self._buckets[key] = bucket

            allowed, remaining = bucket.is_allowed()
            retry_after = bucket.retry_after() if not allowed else 0.0
            return allowed, remaining, retry_after

    async def reset(self, key: str) -> None:
        """Reset the rate limit bucket for a key (admin use only)."""
        async with self._lock:
            self._buckets.pop(key, None)


rate_limiter = RateLimiter()
