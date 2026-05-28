"""
Tenant extraction middleware.

Reads tenant_id from the authenticated JWT payload (already decoded by
auth/middleware.py) and sets it in the ContextVar for the request lifecycle.

Also sets actor_id and role so any service in the call stack can read the
current principal without passing it as function arguments.

This middleware MUST run after AuthMiddleware in the stack order.
"""

from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests        import Request

from tenant.context  import set_tenant_id, set_actor
from auth.models     import AuthUser

log = logging.getLogger("evidentrx.tenant")


class TenantMiddleware(BaseHTTPMiddleware):
    """
    Sets tenant context from the authenticated user on every request.
    Routes without authentication (public paths) set context to "public".
    """

    async def dispatch(self, request: Request, call_next):
        user: AuthUser | None = getattr(request.state, "user", None)

        if user is not None:
            set_tenant_id(user.tenant_id)
            set_actor(user.user_id, user.role.value)
        else:
            # Public/unauthenticated request — no tenant context
            set_tenant_id("__public__")

        response = await call_next(request)
        return response
