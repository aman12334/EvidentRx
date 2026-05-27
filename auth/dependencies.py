"""
FastAPI dependency injection for authentication and authorization.

Usage:

    @router.get("/cases/{case_id}")
    async def get_case(
        case_id: str,
        user: AuthUser = Depends(require_auth),
    ): ...

    @router.patch("/cases/{case_id}/status")
    async def update_status(
        case_id: str,
        user: AuthUser = Depends(require_role(Role.SENIOR_ANALYST)),
    ): ...

    @router.post("/cases/{case_id}/escalate")
    async def escalate(
        case_id: str,
        user: AuthUser = Depends(require_permission(Permission.CASE_ESCALATE)),
    ): ...
"""

from __future__ import annotations

from fastapi          import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from auth.jwt         import decode_access_token, TokenValidationError
from auth.models      import AuthUser
from auth.rbac        import Permission, Role, has_permission
from auth.session     import session_store

_bearer = HTTPBearer(auto_error=False)


# ─── Core auth dependency ─────────────────────────────────────────────────────

async def require_auth(
    request:     Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> AuthUser:
    """
    FastAPI dependency — returns the authenticated AuthUser or raises 401.
    Validates JWT and checks the session store for revocation.
    """
    # Auth middleware already populated request.state.user
    user: AuthUser | None = getattr(request.state, "user", None)
    if user is not None:
        return user

    # Fallback: extract from credentials (for use outside middleware context)
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_access_token(credentials.credentials)
    except TokenValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        ) from e

    # Session revocation check
    if not await session_store.is_valid(payload.jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return AuthUser(
        user_id=payload.sub,
        tenant_id=payload.tenant_id,
        role=Role(payload.role),
        jti=payload.jti,
    )


# ─── Role-based dependency factory ────────────────────────────────────────────

def require_role(minimum_role: Role):
    """
    Dependency factory — enforces that the authenticated user has at least the
    specified role in the hierarchy.

    Usage:  user: AuthUser = Depends(require_role(Role.AUDITOR))
    """
    _ORDER = {
        Role.ANALYST:        0,
        Role.SENIOR_ANALYST: 1,
        Role.AUDITOR:        2,
        Role.ADMIN:          3,
        Role.SYSTEM:         4,
    }
    required_level = _ORDER[minimum_role]

    async def _dep(user: AuthUser = Depends(require_auth)) -> AuthUser:
        if _ORDER.get(user.role, -1) < required_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{minimum_role.value}' or higher required",
            )
        return user

    return _dep


# ─── Permission-based dependency factory ──────────────────────────────────────

def require_permission(permission: Permission):
    """
    Dependency factory — enforces that the authenticated user's role grants
    the specified permission.

    Usage:  user: AuthUser = Depends(require_permission(Permission.CASE_ESCALATE))
    """
    async def _dep(user: AuthUser = Depends(require_auth)) -> AuthUser:
        if not has_permission(user.role, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission '{permission.value}' required",
            )
        return user

    return _dep


# ─── Tenant-scoped auth dependency ────────────────────────────────────────────

async def get_current_user(user: AuthUser = Depends(require_auth)) -> AuthUser:
    """Alias for require_auth — explicit naming for router-level clarity."""
    return user
