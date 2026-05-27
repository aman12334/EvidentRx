"""
Rate-limiting middleware for the EvidentRx API.

Applies sliding-window rate limits per authenticated user (or IP for
unauthenticated requests). Auth endpoints use a stricter limit to
prevent brute-force credential attacks.

Limits:
  - Default API routes : 120 req / 60 s  per user
  - Auth endpoints     :  10 req / 60 s  per IP
  - Health / docs      :  unlimited (exempt)

On violation: 429 Too Many Requests with Retry-After header.
"""

from __future__ import annotations

import json
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests        import Request
from starlette.responses       import Response

from security.rate_limiter import RateLimiter
from auth.models           import AuthUser

log = logging.getLogger("evidentrx.rate_limit")

# Paths exempt from rate limiting
_EXEMPT_PREFIXES = frozenset([
    "/api/health",
    "/api/docs",
    "/api/redoc",
    "/api/openapi.json",
    "/",
])

# Auth paths get a stricter limit
_AUTH_PREFIX = "/api/v1/auth"

# Per-user default limit
_DEFAULT_LIMIT    = 120   # requests
_DEFAULT_WINDOW   = 60    # seconds

# Per-IP auth limit (brute-force protection)
_AUTH_LIMIT  = 10
_AUTH_WINDOW = 60

_rate_limiter = RateLimiter()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Sliding-window rate limiter applied before business logic.

    Key format:
      - Authenticated : "user:{user_id}"
      - Unauthenticated / auth routes : "ip:{client_ip}"
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path

        # Exempt paths — pass through immediately
        if any(path.startswith(p) for p in _EXEMPT_PREFIXES):
            return await call_next(request)

        is_auth_path = path.startswith(_AUTH_PREFIX)

        if is_auth_path:
            # Always rate-limit auth by IP regardless of token presence
            client_ip = (request.client.host if request.client else "unknown")
            key       = f"ip:{client_ip}"
            limit     = _AUTH_LIMIT
            window    = _AUTH_WINDOW
        else:
            # Prefer user_id key for authenticated requests, fall back to IP
            user: AuthUser | None = getattr(request.state, "user", None)
            if user is not None:
                key = f"user:{user.user_id}"
            else:
                client_ip = (request.client.host if request.client else "unknown")
                key       = f"ip:{client_ip}"
            limit  = _DEFAULT_LIMIT
            window = _DEFAULT_WINDOW

        allowed, remaining, retry_after = await _rate_limiter.check(
            key=key, limit=limit, window_sec=window,
        )

        if not allowed:
            log.warning("Rate limit exceeded: key=%s path=%s", key, path)
            body = json.dumps({
                "detail": "Rate limit exceeded. Please slow down.",
                "retry_after_seconds": retry_after,
            })
            return Response(
                content=body,
                status_code=429,
                headers={
                    "Content-Type":  "application/json",
                    "Retry-After":   str(retry_after),
                    "X-RateLimit-Limit":     str(limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset":     str(retry_after),
                },
            )

        response = await call_next(request)

        # Inject informational headers on successful responses
        response.headers["X-RateLimit-Limit"]     = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)

        return response
