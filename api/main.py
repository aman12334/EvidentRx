"""
EvidentRx FastAPI application — Phase 8 backend API.

Mounts all routers and middleware for the analyst workspace.
Run with: uvicorn api.main:app --reload --port 8000
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from api.middleware.cors import add_cors_middleware
from api.middleware.logging import RequestLoggingMiddleware
from api.routers import evidence, findings, graph, investigations, monitoring, traces

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

app = FastAPI(
    title="EvidentRx Compliance Intelligence API",
    description="Backend API for the 340B compliance analyst workspace.",
    version="8.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# Middleware
add_cors_middleware(app)
app.add_middleware(RequestLoggingMiddleware)

# Routers
app.include_router(investigations.router, prefix="/api/v1")
app.include_router(findings.router,       prefix="/api/v1")
app.include_router(evidence.router,       prefix="/api/v1")
app.include_router(traces.router,         prefix="/api/v1")
app.include_router(graph.router,          prefix="/api/v1")
app.include_router(monitoring.router,     prefix="/api/v1")


@app.get("/api/health")
def health():
    return JSONResponse({"status": "ok", "service": "evidentrx-api", "version": "8.0.0"})


@app.get("/")
def root():
    return JSONResponse({"message": "EvidentRx Compliance Intelligence API", "docs": "/api/docs"})
