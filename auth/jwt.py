"""
JWT token creation, validation, and refresh workflow.

Access tokens:  short-lived (15 min)
Refresh tokens: long-lived (7 days), stored server-side
Signing:        HS256 via python-jose

Token payload includes: sub (user_id), tenant_id, role, jti (jwt id),
exp, iat, iss, aud — sufficient for RBAC and tenant isolation at the
token boundary without a DB round-trip per request.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from jose import JWTError, jwt
from pydantic import BaseModel

from config.settings import settings

# ─── Constants ────────────────────────────────────────────────────────────────

ALGORITHM              = "HS256"
ACCESS_TOKEN_EXPIRE    = timedelta(minutes=15)
REFRESH_TOKEN_EXPIRE   = timedelta(days=7)
ISSUER                 = "evidentrx"
AUDIENCE               = "evidentrx-api"


# ─── Token Payload ────────────────────────────────────────────────────────────

class TokenPayload(BaseModel):
    """Decoded JWT payload — validated on every authenticated request."""
    sub:       str            # user_id (UUID)
    tenant_id: str            # organization / tenant
    role:      str            # analyst | senior_analyst | auditor | admin
    jti:       str            # unique token ID (for revocation)
    iss:       str = ISSUER
    aud:       str = AUDIENCE
    exp:       int | None = None
    iat:       int | None = None
    is_refresh: bool = False


class TokenPairResponse(BaseModel):
    access_token:  str
    refresh_token: str
    token_type:    str = "bearer"
    expires_in:    int = int(ACCESS_TOKEN_EXPIRE.total_seconds())


# ─── Token Creation ───────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(tz=UTC)


def create_access_token(
    user_id:   str,
    tenant_id: str,
    role:      str,
    extra:     dict | None = None,
) -> str:
    """Create a signed JWT access token."""
    now = _now()
    payload: dict = {
        "sub":       user_id,
        "tenant_id": tenant_id,
        "role":      role,
        "jti":       str(uuid.uuid4()),
        "iss":       ISSUER,
        "aud":       AUDIENCE,
        "iat":       int(now.timestamp()),
        "exp":       int((now + ACCESS_TOKEN_EXPIRE).timestamp()),
        "is_refresh": False,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret_key.get_secret_value(), algorithm=ALGORITHM)


def create_refresh_token(
    user_id:   str,
    tenant_id: str,
    role:      str,
) -> tuple[str, str]:
    """
    Create a signed JWT refresh token.
    Returns (encoded_token, jti) — jti is stored server-side for revocation.
    """
    now = _now()
    jti = str(uuid.uuid4())
    payload: dict = {
        "sub":        user_id,
        "tenant_id":  tenant_id,
        "role":       role,
        "jti":        jti,
        "iss":        ISSUER,
        "aud":        AUDIENCE,
        "iat":        int(now.timestamp()),
        "exp":        int((now + REFRESH_TOKEN_EXPIRE).timestamp()),
        "is_refresh": True,
    }
    return jwt.encode(payload, settings.jwt_secret_key.get_secret_value(), algorithm=ALGORITHM), jti


def create_token_pair(
    user_id:   str,
    tenant_id: str,
    role:      str,
) -> tuple[str, str, str]:
    """
    Create access + refresh token pair.
    Returns (access_token, refresh_token, refresh_jti).
    """
    access  = create_access_token(user_id, tenant_id, role)
    refresh, jti = create_refresh_token(user_id, tenant_id, role)
    return access, refresh, jti


# ─── Token Validation ─────────────────────────────────────────────────────────

class TokenValidationError(Exception):
    """Raised when a JWT cannot be validated."""
    pass


def decode_access_token(token: str) -> TokenPayload:
    """
    Decode and validate a JWT access token.
    Raises TokenValidationError on any failure.
    """
    try:
        raw = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            issuer=ISSUER,
        )
    except JWTError as e:
        raise TokenValidationError(f"Invalid token: {e}") from e

    if raw.get("is_refresh"):
        raise TokenValidationError("Refresh token cannot be used as access token")

    try:
        return TokenPayload(**raw)
    except Exception as e:
        raise TokenValidationError(f"Malformed token payload: {e}") from e


def decode_refresh_token(token: str) -> TokenPayload:
    """
    Decode and validate a JWT refresh token.
    Raises TokenValidationError on any failure.
    """
    try:
        raw = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            issuer=ISSUER,
        )
    except JWTError as e:
        raise TokenValidationError(f"Invalid refresh token: {e}") from e

    if not raw.get("is_refresh"):
        raise TokenValidationError("Token is not a refresh token")

    try:
        return TokenPayload(**raw)
    except Exception as e:
        raise TokenValidationError(f"Malformed refresh token payload: {e}") from e
