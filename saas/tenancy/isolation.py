"""
Tenant isolation enforcement.

Provides the runtime guard that prevents cross-tenant data access. Every
data-layer operation must pass through the TenantIsolationGuard before
executing. The guard is fail-closed — an ambiguous or missing tenant_id
blocks the operation rather than allowing it.

Isolation model
───────────────
  - Structural: all tables carry a tenant_id column (enforced at schema level)
  - Runtime: TenantIsolationGuard validates tenant_id on every operation
  - Context: TenantContext propagates the active tenant through async call chains
  - Audit: every isolation violation is logged with full context

Cross-tenant references
───────────────────────
  Only platform-level operators (role=platform_admin) may read across tenant
  boundaries, and only via explicitly cross-tenant-safe query functions.
  Analyst and admin roles are always single-tenant.
"""

from __future__ import annotations

import hashlib
import logging
from contextvars import ContextVar
from dataclasses import dataclass
from datetime    import datetime, timezone
from typing      import Any, Callable, Optional

log = logging.getLogger("evidentrx.saas.tenancy.isolation")

# Thread/async-local context variable — carries the active tenant_id
# for the duration of a request or task.
_active_tenant_id: ContextVar[Optional[str]] = ContextVar(
    "active_tenant_id", default=None
)


@dataclass
class TenantContext:
    """
    Immutable snapshot of the active tenant context for one operation.

    Produced by TenantIsolationGuard.enter() and consumed by any
    downstream component that needs to scope its queries.
    """
    tenant_id:  str
    user_id:    str
    role:       str
    session_id: Optional[str]
    entered_at: datetime

    def assert_tenant(self, resource_tenant_id: str) -> None:
        """Raise IsolationViolation if resource belongs to a different tenant."""
        if resource_tenant_id != self.tenant_id:
            raise IsolationViolation(
                f"Cross-tenant access attempt: context={self.tenant_id[:8]}, "
                f"resource={resource_tenant_id[:8]}, user={self.user_id}"
            )

    def to_log_dict(self) -> dict[str, str]:
        return {
            "tenant_id":  self.tenant_id,
            "user_id":    self.user_id,
            "role":       self.role,
            "session_id": self.session_id or "",
        }


class TenantIsolationGuard:
    """
    Enforces tenant isolation at the runtime layer.

    Usage
    -----
    Wrap every request handler:

        ctx = guard.enter(tenant_id, user_id, role, session_id)
        try:
            ...
        finally:
            guard.exit()

    Or use as an async context manager:

        async with guard.context(tenant_id, user_id, role):
            ...
    """

    def __init__(
        self,
        violation_handler: Optional[Callable] = None,
    ) -> None:
        self._violation_handler = violation_handler
        self._violation_count:  dict[str, int] = {}   # tenant_id → count

    # ── Enter / exit ───────────────────────────────────────────────────────────

    def enter(
        self,
        tenant_id:  str,
        user_id:    str,
        role:       str,
        session_id: Optional[str] = None,
    ) -> TenantContext:
        """Activate the tenant context for the current async task."""
        if not tenant_id or not tenant_id.strip():
            raise IsolationViolation("tenant_id must be a non-empty string")

        ctx = TenantContext(
            tenant_id  = tenant_id,
            user_id    = user_id,
            role       = role,
            session_id = session_id,
            entered_at = datetime.now(tz=timezone.utc),
        )
        _active_tenant_id.set(tenant_id)
        log.debug("TenantIsolationGuard: entered context for tenant=%s user=%s",
                  tenant_id[:8], user_id[:8] if len(user_id) >= 8 else user_id)
        return ctx

    def exit(self) -> None:
        """Clear the tenant context for the current async task."""
        _active_tenant_id.set(None)

    class _ContextManager:
        def __init__(self, guard: "TenantIsolationGuard",
                     tenant_id: str, user_id: str, role: str,
                     session_id: Optional[str]) -> None:
            self._guard      = guard
            self._tenant_id  = tenant_id
            self._user_id    = user_id
            self._role       = role
            self._session_id = session_id
            self._ctx: Optional[TenantContext] = None

        async def __aenter__(self) -> TenantContext:
            self._ctx = self._guard.enter(
                self._tenant_id, self._user_id, self._role, self._session_id
            )
            return self._ctx

        async def __aexit__(self, *_: Any) -> None:
            self._guard.exit()

    def context(
        self,
        tenant_id:  str,
        user_id:    str,
        role:       str,
        session_id: Optional[str] = None,
    ) -> "_ContextManager":
        return self._ContextManager(self, tenant_id, user_id, role, session_id)

    # ── Validation helpers ─────────────────────────────────────────────────────

    def validate(self, resource_tenant_id: str) -> None:
        """
        Confirm the resource's tenant matches the active context.

        Raises IsolationViolation on mismatch. Call this in every
        data-access method before returning any record.
        """
        active = _active_tenant_id.get()
        if active is None:
            raise IsolationViolation(
                "No active tenant context — call guard.enter() before accessing data"
            )
        if resource_tenant_id != active:
            self._violation_count[active] = self._violation_count.get(active, 0) + 1
            log.error(
                "TenantIsolationGuard: VIOLATION — context=%s resource=%s",
                active[:8], resource_tenant_id[:8],
            )
            if self._violation_handler:
                try:
                    self._violation_handler(active, resource_tenant_id)
                except Exception:
                    pass
            raise IsolationViolation(
                f"Cross-tenant access: active={active[:8]}, "
                f"resource={resource_tenant_id[:8]}"
            )

    def validate_batch(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Filter a batch of records to only those belonging to the active tenant.

        Returns the filtered list. Never raises — silently drops alien records
        and logs a warning for each. Safe to use in bulk-load paths.
        """
        active = _active_tenant_id.get()
        if active is None:
            raise IsolationViolation("No active tenant context")

        safe: list[dict] = []
        for rec in records:
            rtid = rec.get("tenant_id", "")
            if rtid == active:
                safe.append(rec)
            else:
                log.warning(
                    "TenantIsolationGuard: batch record dropped — "
                    "context=%s record_tenant=%s",
                    active[:8], rtid[:8] if rtid else "none",
                )
        return safe

    @staticmethod
    def current_tenant_id() -> Optional[str]:
        """Return the currently active tenant_id, or None if no context set."""
        return _active_tenant_id.get()

    def violation_count(self, tenant_id: str) -> int:
        return self._violation_count.get(tenant_id, 0)


# ── Query filter helper ────────────────────────────────────────────────────────

def tenant_filter(tenant_id: str) -> dict[str, str]:
    """
    Return a minimal WHERE-clause dict for tenant-scoped ORM queries.

    Usage (SQLAlchemy example):
        session.query(Model).filter_by(**tenant_filter(ctx.tenant_id))
    """
    return {"tenant_id": tenant_id}


def anonymise_tenant_id(tenant_id: str) -> str:
    """One-way hash for logging tenant references without exposing the raw ID."""
    return hashlib.sha256(tenant_id.encode()).hexdigest()[:12]


# ── Exceptions ─────────────────────────────────────────────────────────────────

class IsolationViolation(Exception):
    """Raised when a cross-tenant data access attempt is detected."""


# ── Module-level singleton ─────────────────────────────────────────────────────

_guard: Optional[TenantIsolationGuard] = None


def get_isolation_guard(
    violation_handler: Optional[Callable] = None,
) -> TenantIsolationGuard:
    global _guard
    if _guard is None:
        _guard = TenantIsolationGuard(violation_handler=violation_handler)
    return _guard
