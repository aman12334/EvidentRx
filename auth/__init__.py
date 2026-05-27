"""
auth — Enterprise Authentication & Authorization Infrastructure

Provides JWT-based authentication, refresh token workflows, RBAC authorization,
organization/tenant isolation, and role-based investigation permissions.

Roles:  analyst | senior_analyst | auditor | admin | system
"""

from auth.jwt         import create_access_token, decode_access_token, TokenPayload
from auth.rbac        import Permission, Role, has_permission
from auth.models      import AuthUser, TokenPair
from auth.dependencies import require_auth, require_role, require_permission

__all__ = [
    "create_access_token",
    "decode_access_token",
    "TokenPayload",
    "Permission",
    "Role",
    "has_permission",
    "AuthUser",
    "TokenPair",
    "require_auth",
    "require_role",
    "require_permission",
]
