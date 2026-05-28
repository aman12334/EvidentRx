"""
Tenant isolation enforcement.

Runtime checks that make cross-tenant data access structurally impossible.

Enforcement layers:
  1. JWT payload (tenant_id cannot be forged without signing key)
  2. ContextVar propagation (no thread-local leakage)
  3. TenantRepository.verify_tenant() (post-fetch row check)
  4. assert_tenant_access() (explicit check at service layer)

If any check fails, TenantIsolationError is raised and logged as a
SECURITY event — triggering alerting rules in the monitoring stack.
"""

from __future__ import annotations

import logging

log = logging.getLogger("evidentrx.isolation")


class TenantIsolationError(PermissionError):
    """
    Raised when a request attempts to access data belonging to another tenant.
    This is always a security event — it should never happen in normal operation.
    """
    pass


def assert_tenant_access(
    resource_tenant_id:   str,
    requesting_tenant_id: str,
    resource_type:        str = "resource",
    resource_id:          str = "unknown",
) -> None:
    """
    Assert that the requesting tenant owns the resource.
    Logs a SECURITY-level event and raises TenantIsolationError on violation.
    """
    if resource_tenant_id == requesting_tenant_id:
        return  # access granted

    log.critical(
        "TENANT ISOLATION VIOLATION: requester=%s attempted access to %s=%s owned by %s",
        requesting_tenant_id,
        resource_type,
        resource_id,
        resource_tenant_id,
    )

    raise TenantIsolationError(
        f"Tenant isolation violation: "
        f"tenant {requesting_tenant_id!r} cannot access "
        f"{resource_type} {resource_id!r} (owner: {resource_tenant_id!r})"
    )


def validate_tenant_scope(
    requesting_tenant_id: str,
    resource_tenant_ids:  list[str],
) -> list[str]:
    """
    Filter a list of tenant IDs to only those matching the requesting tenant.
    Used when bulk-loading resources to ensure isolation without raising.
    """
    return [t for t in resource_tenant_ids if t == requesting_tenant_id]
