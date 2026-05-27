"""
FastAPI authentication middleware.

Extracts Bearer token from Authorization header, validates JWT, and
attaches AuthUser to request.state for downstream dependency injection.

Unauthenticated routes (health, /docs, /openapi.json) bypass this middleware.
All /api/v1/* routes require a valid access token by default.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests        import Request
from starlette.responses       import JSONResponse

from auth.jwt    import decode_access_token, TokenValidationError
from auth.models import AuthUser
from auth.rbac   import Role

# Routes that do NOT require authentication
_PUBLIC_PATHS = frozenset({
    "/health",
    "/api/health",           # registered path in api/main.py
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/docs",
    "/api/redoc",
    "/api/openapi.json",
    "/api/metrics",          # Prometheus scrape endpoint
    "/metrics",
    "/",
})


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Stateless JWT authentication middleware.

    On success:  request.state.user = AuthUser(...)
    On failure:  returns 401 JSON with WWW-Authenticate header
    On bypass:   request.state.user = None (public routes)
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Allow public routes without a token
        if path in _PUBLIC_PATHS or path.startswith("/static"):
            request.state.user = None
            return await call_next(request)

        # Extract Bearer token
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return _unauthorized("Missing or malformed Authorization header")

        token = auth_header[len("Bearer "):]

        # Validate JWT
        try:
            payload = decode_access_token(token)
        except TokenValidationError as e:
            return _unauthorized(str(e))

        # Attach principal to request state
        request.state.user = AuthUser(
            user_id=payload.sub,
            tenant_id=payload.tenant_id,
            role=Role(payload.role),
            jti=payload.jti,
        )

        return await call_next(request)


def _unauthorized(detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"detail": detail},
        headers={"WWW-Authenticate": "Bearer"},
    )
