"""
tenant — Multi-Tenant Isolation Architecture

Implements structural tenant isolation so that no tenant can ever access
another tenant's data, even through indirect means.

Components:
  - context.py:    ContextVar-based tenant propagation (no thread-local pollution)
  - middleware.py: Extracts tenant_id from JWT, sets context
  - repository.py: Base repository class with automatic tenant_id filtering
  - isolation.py:  Runtime isolation enforcement and violation detection
"""

from tenant.context    import get_tenant_id, set_tenant_id, TenantContext
from tenant.isolation  import TenantIsolationError, assert_tenant_access

__all__ = [
    "get_tenant_id",
    "set_tenant_id",
    "TenantContext",
    "TenantIsolationError",
    "assert_tenant_access",
]
