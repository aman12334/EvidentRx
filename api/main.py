"""
EvidentRx FastAPI application — Phase 9 production hardening.

Middleware stack (outermost → innermost):
  1. CORSMiddleware
  2. SecurityHeadersMiddleware   (HSTS, CSP, X-Frame-Options, …)
  3. ObservabilityMiddleware     (X-Request-Id, Prometheus metrics)
  4. RequestLoggingMiddleware    (structured JSON request logs)
  5. AuthMiddleware              (JWT decode → request.state.user)
  6. TenantMiddleware            (tenant ContextVar propagation)
  7. RateLimitMiddleware         (sliding-window per user/IP)

Run with:
  uvicorn api.main:app --reload --port 8000
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from config.settings import settings

from api.middleware.cors       import add_cors_middleware
from api.middleware.logging    import RequestLoggingMiddleware
from api.middleware.rate_limit import RateLimitMiddleware
from api.middleware.tenant     import TenantMiddleware

from auth.middleware           import AuthMiddleware
from security.headers          import SecurityHeadersMiddleware
from observability.middleware  import ObservabilityMiddleware
from observability.logging     import configure_logging
from observability.tracing     import setup_tracing

from api.routers import (
    auth,
    evidence,
    findings,
    graph,
    investigations,
    monitoring,
    traces,
    upload,
)

# ── Bootstrap ────────────────────────────────────────────────────────────────

configure_logging(level=settings.log_level, json_output=settings.structured_logging)
setup_tracing(service_name="evidentrx-api")

log = logging.getLogger("evidentrx.api")

# ── Application ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="EvidentRx Compliance Intelligence API",
    description=(
        "Backend API for the 340B compliance analyst workspace. "
        "Provides investigation management, evidence retrieval, "
        "risk intelligence, and audit governance."
    ),
    version="9.0.0",
    docs_url="/api/docs"    if not settings.is_production else None,
    redoc_url="/api/redoc"  if not settings.is_production else None,
    openapi_url="/api/openapi.json" if not settings.is_production else None,
)

# ── Middleware (Starlette adds middleware in reverse — last added runs first) ─
# To achieve the desired top-down order above we add from bottom to top.

app.add_middleware(RateLimitMiddleware)
app.add_middleware(TenantMiddleware)
app.add_middleware(AuthMiddleware)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(ObservabilityMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
add_cors_middleware(app)   # adds CORSMiddleware — outermost layer

# ── Routers ──────────────────────────────────────────────────────────────────

app.include_router(auth.router,            prefix="/api/v1")
app.include_router(investigations.router,  prefix="/api/v1")
app.include_router(findings.router,        prefix="/api/v1")
app.include_router(evidence.router,        prefix="/api/v1")
app.include_router(traces.router,          prefix="/api/v1")
app.include_router(graph.router,           prefix="/api/v1")
app.include_router(monitoring.router,      prefix="/api/v1")
app.include_router(upload.router,          prefix="/api/v1")

# ── Health & root ─────────────────────────────────────────────────────────────

@app.get("/api/health", include_in_schema=False)
def health() -> JSONResponse:
    return JSONResponse({
        "status":      "ok",
        "service":     "evidentrx-api",
        "version":     "9.0.0",
        "environment": settings.environment,
    })


@app.get("/", include_in_schema=False)
def root() -> JSONResponse:
    return JSONResponse({
        "message": "EvidentRx Compliance Intelligence API",
        "version": "9.0.0",
        "docs":    "/api/docs" if not settings.is_production else "disabled in production",
    })


log.info(
    "EvidentRx API v9.0.0 initialised",
    extra={"environment": settings.environment},
)
