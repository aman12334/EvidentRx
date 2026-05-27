"""
Tenant isolation at the authentication layer.

Every authenticated request carries a tenant_id from the JWT payload.
This module enforces that cross-tenant access is structurally impossible —
the tenant_id in the token cannot be elevated or overridden by query params.

Tenant validation rules:
  1. tenant_id must be present in the JWT (enforced by decode_access_token)
  2. tenant_id must be a valid UUID4 (format validation)
  3. URL path parameters referencing case/entity IDs are cross-checked
     against the requesting tenant's scope at the repository layer
  4. Admin role may only switch tenant scope with explicit re-authentication
"""

from __future__ import annotations

import re
import uuid
from typing import Optional

from auth.models import AuthUser
from auth.rbac   import Role

_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class TenantViolation(Exception):
    """Raised when a request attempts cross-tenant data access."""
    pass


def validate_tenant_id(tenant_id: str) -> str:
    """
    Ensure tenant_id is a well-formed UUID4.
    Raises ValueError on invalid format.
    """
    if not _UUID4_RE.match(tenant_id):
        raise ValueError(f"Invalid tenant_id format: {tenant_id!r}")
    return tenant_id.lower()


def assert_tenant_scope(
    user:       AuthUser,
    resource_tenant_id: Optional[str],
) -> None:
    """
    Assert that the authenticated user belongs to the same tenant as the
    requested resource. Raises TenantViolation on mismatch.

    Admins can access any tenant only via system-level service accounts;
    user-facing admin tokens are still scoped to their own tenant.
    """
    if resource_tenant_id is None:
        return  # unscoped resource (e.g., global reference data)

    if user.role == Role.SYSTEM:
        return  # system accounts bypass tenant scoping

    if user.tenant_id.lower() != resource_tenant_id.lower():
        raise TenantViolation(
            f"Cross-tenant access denied: user tenant={user.tenant_id!r} "
            f"resource tenant={resource_tenant_id!r}"
        )


def new_tenant_id() -> str:
    """Generate a new unique tenant identifier (UUID4)."""
    return str(uuid.uuid4())
