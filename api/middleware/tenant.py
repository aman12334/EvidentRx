"""
Tenant context wiring for the API middleware stack.

Re-exports TenantMiddleware from tenant/middleware.py for use in
api/main.py so that all middleware imports come from api.middleware.

Stack order (outermost → innermost):
  1. CORSMiddleware
  2. SecurityHeadersMiddleware
  3. ObservabilityMiddleware      (request tracing + metrics)
  4. RequestLoggingMiddleware
  5. AuthMiddleware               (decodes JWT → request.state.user)
  6. TenantMiddleware             (reads user → sets ContextVar)
  7. RateLimitMiddleware          (per-user/IP sliding window)
  8. Route handler

TenantMiddleware MUST run after AuthMiddleware (step 5) because it reads
request.state.user which is set by AuthMiddleware.
"""

from __future__ import annotations

# Re-export for api/main.py convenience
from tenant.middleware import TenantMiddleware

__all__ = ["TenantMiddleware"]
