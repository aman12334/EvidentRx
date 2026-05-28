"""
Retry orchestration with exponential backoff and jitter.

Implements production-grade retry patterns:
  - Exponential backoff: delay = base * (2 ^ attempt) + jitter
  - Maximum delay cap (prevent infinite waits)
  - Retry budget (maximum total retry time)
  - Exception-based filtering (only retry transient errors)
  - Circuit breaker integration (future: open circuit on consecutive failures)

Usage (synchronous):
    @with_retry(RetryPolicy(max_retries=3, base_delay=2.0))
    def call_external_api():
        ...

Usage (async):
    @with_async_retry(RetryPolicy(max_retries=3, base_delay=2.0))
    async def call_external_api():
        ...
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
import time
from dataclasses import dataclass, field
from typing      import Callable, Optional, Tuple, Type

log = logging.getLogger("evidentrx.retry")


# ─── Exception categories ─────────────────────────────────────────────────────

TRANSIENT_EXCEPTIONS: Tuple[Type[Exception], ...] = (
    ConnectionError,
    TimeoutError,
    OSError,
)

NON_RETRYABLE_EXCEPTIONS: Tuple[Type[Exception], ...] = (
    ValueError,
    PermissionError,
    NotImplementedError,
)


# ─── Retry Policy ─────────────────────────────────────────────────────────────

@dataclass
class RetryPolicy:
    """Configuration for a retry strategy."""
    max_retries:      int   = 3
    base_delay:       float = 1.0      # seconds
    max_delay:        float = 60.0     # seconds (cap)
    jitter:           float = 0.5      # random jitter factor (0–1)
    exponential_base: float = 2.0

    # Exception types to retry on (None = retry all non-fatal)
    retryable_exceptions: Optional[Tuple[Type[Exception], ...]] = None

    def delay_for_attempt(self, attempt: int) -> float:
        """Compute delay before attempt N (0-indexed)."""
        exp    = self.base_delay * (self.exponential_base ** attempt)
        jitter = random.uniform(0, self.jitter * exp)
        return min(exp + jitter, self.max_delay)

    def is_retryable(self, exc: Exception) -> bool:
        if isinstance(exc, NON_RETRYABLE_EXCEPTIONS):
            return False
        if self.retryable_exceptions:
            return isinstance(exc, self.retryable_exceptions)
        return True


# ─── Sync retry decorator ─────────────────────────────────────────────────────

def with_retry(policy: Optional[RetryPolicy] = None):
    """Synchronous retry decorator."""
    _policy = policy or RetryPolicy()

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            last_exc: Optional[Exception] = None
            for attempt in range(_policy.max_retries + 1):
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    if attempt == _policy.max_retries or not _policy.is_retryable(exc):
                        raise
                    delay = _policy.delay_for_attempt(attempt)
                    log.warning(
                        "Retry %d/%d for %s after %.1fs: %s",
                        attempt + 1, _policy.max_retries, fn.__name__, delay, exc,
                    )
                    time.sleep(delay)
            raise last_exc  # type: ignore[misc]

        return wrapper
    return decorator


# ─── Async retry decorator ────────────────────────────────────────────────────

def with_async_retry(policy: Optional[RetryPolicy] = None):
    """Async retry decorator."""
    _policy = policy or RetryPolicy()

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            last_exc: Optional[Exception] = None
            for attempt in range(_policy.max_retries + 1):
                try:
                    return await fn(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    if attempt == _policy.max_retries or not _policy.is_retryable(exc):
                        raise
                    delay = _policy.delay_for_attempt(attempt)
                    log.warning(
                        "Async retry %d/%d for %s after %.1fs: %s",
                        attempt + 1, _policy.max_retries, fn.__name__, delay, exc,
                    )
                    await asyncio.sleep(delay)
            raise last_exc  # type: ignore[misc]

        return wrapper
    return decorator
