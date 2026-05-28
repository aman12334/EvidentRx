"""
Tenant context propagation via Python contextvars.

ContextVars propagate automatically through async tasks and are isolated
per-request in async frameworks like FastAPI/Starlette — unlike threading.local()
which breaks with async concurrency.

Usage:
    # In middleware (set once per request):
    set_tenant_id("abc-123")

    # Anywhere in the call stack (read):
    tenant_id = get_tenant_id()   # returns "abc-123"

    # Or use the context manager:
    with TenantContext("abc-123"):
        result = await some_service.query()
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

_tenant_id_var: ContextVar[str | None] = ContextVar("tenant_id", default=None)
_actor_id_var:  ContextVar[str | None] = ContextVar("actor_id",  default=None)
_role_var:      ContextVar[str | None] = ContextVar("role",      default=None)


def set_tenant_id(tenant_id: str) -> None:
    """Set the current tenant ID in the async context."""
    _tenant_id_var.set(tenant_id)


def get_tenant_id() -> str | None:
    """Return the current tenant ID, or None if not set."""
    return _tenant_id_var.get()


def require_tenant_id() -> str:
    """Return the current tenant ID or raise RuntimeError if not set."""
    tid = _tenant_id_var.get()
    if not tid:
        raise RuntimeError(
            "No tenant_id in context — request did not pass through tenant middleware"
        )
    return tid


def set_actor(actor_id: str, role: str) -> None:
    """Set the current actor (user) in the async context."""
    _actor_id_var.set(actor_id)
    _role_var.set(role)


def get_actor_id() -> str | None:
    return _actor_id_var.get()


def get_role() -> str | None:
    return _role_var.get()


@contextmanager
def TenantContext(
    tenant_id: str,
    actor_id:  str | None = None,
    role:      str | None = None,
) -> Generator[None, None, None]:
    """
    Context manager for setting tenant context.
    Restores previous context on exit (safe for nested use).
    """
    prev_tenant = _tenant_id_var.get()
    prev_actor  = _actor_id_var.get()
    prev_role   = _role_var.get()

    _tenant_id_var.set(tenant_id)
    if actor_id:
        _actor_id_var.set(actor_id)
    if role:
        _role_var.set(role)

    try:
        yield
    finally:
        _tenant_id_var.set(prev_tenant)
        _actor_id_var.set(prev_actor)
        _role_var.set(prev_role)
