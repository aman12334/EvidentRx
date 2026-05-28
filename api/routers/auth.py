"""
Authentication API router.

Endpoints:
  POST /api/v1/auth/login    — issue token pair on valid credentials
  POST /api/v1/auth/refresh  — exchange refresh token for new access token
  POST /api/v1/auth/logout   — revoke refresh token session
  GET  /api/v1/auth/me       — return current authenticated user info

All auth endpoints are rate-limited (stricter than standard API limits)
to prevent brute-force attacks.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import require_auth
from auth.jwt import (
    ACCESS_TOKEN_EXPIRE,
    REFRESH_TOKEN_EXPIRE,
    TokenValidationError,
    create_token_pair,
    decode_refresh_token,
)
from auth.models import (
    AuthUser,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RefreshTokenRecord,
    TokenPair,
)
from auth.password import verify_password
from auth.session import session_store
from auth.user_repository import UserRepository
from database.session import get_async_session
from governance.audit_log import AuditEventType, audit_log

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/login", response_model=TokenPair)
async def login(
    request: Request,
    body:    LoginRequest,
    session: AsyncSession = Depends(get_async_session),
) -> TokenPair:
    """
    Issue an access + refresh token pair on valid credentials.

    Validates against auth.users via DB lookup.
    Enforces account lockout after 10 consecutive failures.
    """
    repo = UserRepository(session)

    # Normalise inputs early so errors don't leak timing info
    email     = (body.email     or "").strip().lower()
    password  = (body.password  or "").strip()
    tenant_id = (body.tenant_id or "").strip()

    if not email or not password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email and password are required",
        )

    # ── DB lookup ─────────────────────────────────────────────────────────────
    user = await repo.get_by_email(email=email, tenant_id=tenant_id)

    # Constant-time path: always run verify_password to prevent user enumeration
    # via timing differences. We pass a dummy hash if the user was not found.
    _DUMMY_HASH = "$2b$12$KIX/J6nVcP3.VTG7K3jsFuNjD9Cq7IKP3JXd7iOdHDTQJnCO1Cxq"
    stored_hash = user.hashed_password if user else _DUMMY_HASH
    password_ok = verify_password(password, stored_hash)

    if user is None or not password_ok:
        # Record failure only if user exists (to avoid creating noise for nonexistent accounts)
        if user is not None:
            await repo.record_failed_login(user.user_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # ── Account state checks ──────────────────────────────────────────────────
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated — contact your administrator",
        )

    if user.is_locked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is temporarily locked due to repeated failed logins. Try again later.",
        )

    # ── Issue tokens ──────────────────────────────────────────────────────────
    access_token, refresh_token, refresh_jti = create_token_pair(
        user_id=user.user_id,
        tenant_id=user.tenant_id,
        role=user.role,
    )

    now = datetime.now(tz=UTC)
    await session_store.save(RefreshTokenRecord(
        jti=refresh_jti,
        user_id=user.user_id,
        tenant_id=user.tenant_id,
        issued_at=now,
        expires_at=now + REFRESH_TOKEN_EXPIRE,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("User-Agent"),
    ))

    # Stamp successful login on the user record
    await repo.record_successful_login(user.user_id)

    audit_log.write(
        event_type=AuditEventType.USER_LOGIN,
        actor_id=user.user_id,
        tenant_id=user.tenant_id,
        payload={
            "email": email,
            "ip":    request.client.host if request.client else None,
            "role":  user.role,
        },
    )

    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=int(ACCESS_TOKEN_EXPIRE.total_seconds()),
    )


@router.post("/refresh", response_model=TokenPair)
async def refresh(body: RefreshRequest) -> TokenPair:
    """
    Exchange a valid refresh token for a new access + refresh token pair.
    The old refresh token is revoked on use (rotation).
    """
    try:
        payload = decode_refresh_token(body.refresh_token)
    except TokenValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        ) from e

    if not await session_store.is_valid(payload.jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked",
        )

    # Revoke old → issue new (rotation)
    await session_store.revoke(payload.jti)

    access_token, refresh_token, new_jti = create_token_pair(
        user_id=payload.sub,
        tenant_id=payload.tenant_id,
        role=payload.role,
    )

    now = datetime.now(tz=UTC)
    await session_store.save(RefreshTokenRecord(
        jti=new_jti,
        user_id=payload.sub,
        tenant_id=payload.tenant_id,
        issued_at=now,
        expires_at=now + REFRESH_TOKEN_EXPIRE,
    ))

    audit_log.write(
        event_type=AuditEventType.TOKEN_REFRESHED,
        actor_id=payload.sub,
        tenant_id=payload.tenant_id,
        payload={"old_jti": payload.jti, "new_jti": new_jti},
    )

    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=int(ACCESS_TOKEN_EXPIRE.total_seconds()),
    )


@router.post("/logout")
async def logout(
    body: LogoutRequest,
    user: AuthUser = Depends(require_auth),
) -> dict:
    """Revoke the provided refresh token and terminate the session."""
    try:
        payload = decode_refresh_token(body.refresh_token)
    except TokenValidationError:
        pass
    else:
        await session_store.revoke(payload.jti)

    audit_log.write(
        event_type=AuditEventType.USER_LOGOUT,
        actor_id=user.user_id,
        tenant_id=user.tenant_id,
        payload={"jti": user.jti},
    )

    return {"message": "Logged out successfully"}


@router.get("/me")
async def me(user: AuthUser = Depends(require_auth)) -> dict:
    """Return the current authenticated user's identity and role."""
    return {
        "user_id":   user.user_id,
        "tenant_id": user.tenant_id,
        "role":      user.role.value,
        "jti":       user.jti,
    }
