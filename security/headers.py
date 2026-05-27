"""
Security headers middleware.

Adds enterprise-grade HTTP security headers to all API responses:
  - Strict-Transport-Security (HSTS)
  - Content-Security-Policy (CSP)
  - X-Content-Type-Options (nosniff)
  - X-Frame-Options (DENY)
  - Referrer-Policy
  - Permissions-Policy
  - Cache-Control (no-store for API responses)

These headers are required for HIPAA-adjacent regulated environments and
OWASP API Security Top 10 compliance.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests        import Request
from starlette.responses       import Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Injects security headers into every API response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        # Transport security (1 year, include subdomains)
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains; preload"
        )

        # Content type sniffing protection
        response.headers["X-Content-Type-Options"] = "nosniff"

        # Clickjacking protection
        response.headers["X-Frame-Options"] = "DENY"

        # Referrer information leakage
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Permissions policy (disable unnecessary browser features)
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )

        # Content Security Policy — API-only (no embedded content)
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; "
            "frame-ancestors 'none'; "
            "base-uri 'none';"
        )

        # API responses must not be cached
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = (
                "no-store, no-cache, must-revalidate, max-age=0"
            )
            response.headers["Pragma"] = "no-cache"

        # Remove server identification header
        # MutableHeaders has no .pop(); use del with existence check
        for _hdr in ("server", "x-powered-by"):
            if _hdr in response.headers:
                del response.headers[_hdr]

        return response
