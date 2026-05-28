"""
Auth domain models — Pydantic v2 schemas for users, tokens, and sessions.
These are API-layer models; DB models live in app/models/.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from auth.rbac import Role

# ─── User Models ──────────────────────────────────────────────────────────────

class AuthUser(BaseModel):
    """Authenticated principal derived from JWT payload."""
    user_id:   str
    tenant_id: str
    email:     str | None = None
    role:      Role
    jti:       str           # token ID, used for revocation checks


class UserCreate(BaseModel):
    """Request body for creating a new user."""
    email:     EmailStr
    password:  str = Field(min_length=12, max_length=128)
    role:      Role = Role.ANALYST
    tenant_id: str

    @field_validator("password")
    @classmethod
    def password_complexity(cls, v: str) -> str:
        has_upper  = any(c.isupper() for c in v)
        has_lower  = any(c.islower() for c in v)
        has_digit  = any(c.isdigit() for c in v)
        has_symbol = any(c in "!@#$%^&*()_+-=[]{}|;':\",./<>?" for c in v)
        if not all([has_upper, has_lower, has_digit, has_symbol]):
            raise ValueError(
                "Password must contain uppercase, lowercase, digit, and symbol"
            )
        return v


class UserResponse(BaseModel):
    """Safe user representation — never exposes password hash."""
    user_id:    str
    email:      str
    role:       Role
    tenant_id:  str
    is_active:  bool
    created_at: datetime


# ─── Token Models ─────────────────────────────────────────────────────────────

class TokenPair(BaseModel):
    """Issued token pair returned to the client on login or refresh."""
    access_token:  str
    refresh_token: str
    token_type:    str = "bearer"
    expires_in:    int   # seconds until access token expires


class LoginRequest(BaseModel):
    """Credentials submitted by the analyst/admin."""
    email:     EmailStr
    password:  str
    tenant_id: str          # explicit tenant scoping on login


class RefreshRequest(BaseModel):
    """Refresh token submitted to obtain a new access token."""
    refresh_token: str


class LogoutRequest(BaseModel):
    """Explicit logout — revokes the provided refresh token JTI."""
    refresh_token: str


# ─── Session Models ───────────────────────────────────────────────────────────

class RefreshTokenRecord(BaseModel):
    """Server-side record of an issued refresh token."""
    jti:        str
    user_id:    str
    tenant_id:  str
    issued_at:  datetime
    expires_at: datetime
    revoked:    bool = False
    revoked_at: datetime | None = None
    user_agent: str | None = None
    ip_address: str | None = None
