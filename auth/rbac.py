"""
Role-Based Access Control (RBAC) for EvidentRx.

Role hierarchy (least → most privileged):
  analyst < senior_analyst < auditor < admin < system

Permissions are additive — higher roles inherit all lower-role permissions.
Investigation-level access controls are enforced separately via tenant scoping.

Design: enums for type-safety, set-based lookup for O(1) permission checks.
"""

from __future__ import annotations

from enum import Enum


# ─── Roles ────────────────────────────────────────────────────────────────────

class Role(str, Enum):
    """Platform roles. Value is the string stored in JWT payload."""
    ANALYST        = "analyst"
    SENIOR_ANALYST = "senior_analyst"
    AUDITOR        = "auditor"
    ADMIN          = "admin"
    SYSTEM         = "system"   # internal service accounts only


# ─── Permissions ──────────────────────────────────────────────────────────────

class Permission(str, Enum):
    # Investigation read
    CASE_READ              = "case:read"
    CASE_LIST              = "case:list"
    FINDING_READ           = "finding:read"
    EVIDENCE_READ          = "evidence:read"
    TRACE_READ             = "trace:read"

    # Investigation write
    CASE_CREATE            = "case:create"
    CASE_UPDATE_STATUS     = "case:update_status"
    CASE_ASSIGN            = "case:assign"
    FINDING_ANNOTATE       = "finding:annotate"

    # Escalation
    CASE_ESCALATE          = "case:escalate"
    CASE_RESOLVE           = "case:resolve"
    CASE_CLOSE             = "case:close"

    # Intelligence
    INTELLIGENCE_READ      = "intelligence:read"
    RISK_SCORE_READ        = "risk_score:read"
    CORRELATION_READ       = "correlation:read"

    # Graph
    GRAPH_READ             = "graph:read"
    GRAPH_WRITE            = "graph:write"

    # Monitoring
    MONITORING_READ        = "monitoring:read"
    MONITORING_TRIGGER     = "monitoring:trigger"

    # Governance / audit
    AUDIT_LOG_READ         = "audit_log:read"
    WORKFLOW_REPLAY        = "workflow:replay"
    ARCHIVE_READ           = "archive:read"
    ARCHIVE_WRITE          = "archive:write"

    # Admin
    USER_MANAGE            = "user:manage"
    TENANT_CONFIG          = "tenant:config"
    RULE_PACK_MANAGE       = "rule_pack:manage"
    FEATURE_FLAG_MANAGE    = "feature_flag:manage"
    SECRET_ROTATE          = "secret:rotate"

    # System (internal only)
    SYSTEM_BOOTSTRAP       = "system:bootstrap"
    SYSTEM_MIGRATION       = "system:migration"


# ─── Role → Permission mapping ────────────────────────────────────────────────

_ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.ANALYST: {
        Permission.CASE_READ,
        Permission.CASE_LIST,
        Permission.FINDING_READ,
        Permission.EVIDENCE_READ,
        Permission.TRACE_READ,
        Permission.CASE_UPDATE_STATUS,
        Permission.FINDING_ANNOTATE,
        Permission.INTELLIGENCE_READ,
        Permission.RISK_SCORE_READ,
        Permission.CORRELATION_READ,
        Permission.GRAPH_READ,
        Permission.MONITORING_READ,
    },
    Role.SENIOR_ANALYST: {
        # All analyst permissions +
        Permission.CASE_CREATE,
        Permission.CASE_ASSIGN,
        Permission.CASE_ESCALATE,
        Permission.CASE_RESOLVE,
        Permission.MONITORING_TRIGGER,
        Permission.WORKFLOW_REPLAY,
    },
    Role.AUDITOR: {
        # All senior_analyst permissions +
        Permission.CASE_CLOSE,
        Permission.AUDIT_LOG_READ,
        Permission.ARCHIVE_READ,
        Permission.GRAPH_WRITE,
    },
    Role.ADMIN: {
        # All auditor permissions +
        Permission.ARCHIVE_WRITE,
        Permission.USER_MANAGE,
        Permission.TENANT_CONFIG,
        Permission.RULE_PACK_MANAGE,
        Permission.FEATURE_FLAG_MANAGE,
        Permission.SECRET_ROTATE,
    },
    Role.SYSTEM: {
        # Full access
        Permission.SYSTEM_BOOTSTRAP,
        Permission.SYSTEM_MIGRATION,
    },
}

# Build cumulative permission sets (higher roles inherit lower)
_ROLE_ORDER = [
    Role.ANALYST,
    Role.SENIOR_ANALYST,
    Role.AUDITOR,
    Role.ADMIN,
    Role.SYSTEM,
]

_CUMULATIVE_PERMISSIONS: dict[Role, set[Permission]] = {}
_accumulated: set[Permission] = set()
for _role in _ROLE_ORDER:
    _accumulated = _accumulated | _ROLE_PERMISSIONS.get(_role, set())
    _CUMULATIVE_PERMISSIONS[_role] = frozenset(_accumulated)  # type: ignore[assignment]


# ─── Permission Check API ─────────────────────────────────────────────────────

def get_permissions(role: Role | str) -> frozenset[Permission]:
    """Return all permissions granted to a role (cumulative)."""
    if isinstance(role, str):
        role = Role(role)
    return _CUMULATIVE_PERMISSIONS.get(role, frozenset())


def has_permission(role: Role | str, permission: Permission) -> bool:
    """Check whether a role has a specific permission."""
    return permission in get_permissions(role)


def require_any(role: Role | str, *permissions: Permission) -> bool:
    """Return True if the role has at least one of the given permissions."""
    perms = get_permissions(role)
    return any(p in perms for p in permissions)


def require_all(role: Role | str, *permissions: Permission) -> bool:
    """Return True if the role has ALL of the given permissions."""
    perms = get_permissions(role)
    return all(p in perms for p in permissions)
