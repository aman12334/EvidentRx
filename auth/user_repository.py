"""
User repository — tenant-scoped DB access for auth.users.

All queries are tenant-scoped: a user can only be resolved within their
own covered entity. Cross-tenant user lookup is structurally blocked.

Design: uses raw asyncpg/SQLAlchemy text() queries (not ORM) to stay
consistent with the TenantRepository pattern established in Phase 9.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger("evidentrx.auth.user_repository")

# Maximum failed login attempts before account lockout
_MAX_FAILURES   = 10
_LOCKOUT_MINUTES = 15


class UserRecord:
    """Lightweight value object — avoids ORM model dependency in auth layer."""
    __slots__ = (
        "user_id", "email", "full_name", "hashed_password",
        "role", "tenant_id", "is_active", "is_verified",
        "force_password_reset", "last_login_at",
        "failed_login_count", "locked_until",
    )

    def __init__(self, row: dict) -> None:
        self.user_id              = str(row["user_id"])
        self.email                = row["email"]
        self.full_name            = row.get("full_name")
        self.hashed_password      = row["hashed_password"]
        self.role                 = row["role"]
        self.tenant_id            = str(row["tenant_id"])
        self.is_active            = row["is_active"]
        self.is_verified          = row["is_verified"]
        self.force_password_reset = row["force_password_reset"]
        self.last_login_at        = row.get("last_login_at")
        self.failed_login_count   = row["failed_login_count"]
        self.locked_until         = row.get("locked_until")

    @property
    def is_locked(self) -> bool:
        if self.locked_until is None:
            return False
        return datetime.now(tz=UTC) < self.locked_until


class UserRepository:
    """
    Auth-scoped user queries.

    Unlike TenantRepository, this class does NOT inject tenant_id
    automatically — the caller must supply it explicitly as the
    isolation boundary for user lookups.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_email(
        self,
        email:     str,
        tenant_id: str,
    ) -> UserRecord | None:
        """
        Fetch a user by email within a specific tenant.
        Returns None if the user does not exist or belongs to a different tenant.
        """
        result = await self._session.execute(
            text("""
                SELECT
                    user_id, email, full_name, hashed_password, role,
                    tenant_id, is_active, is_verified, force_password_reset,
                    last_login_at, failed_login_count, locked_until
                FROM auth.users
                WHERE email     = :email
                  AND tenant_id = :tenant_id
                  AND is_active = TRUE
            """),
            {"email": email.lower().strip(), "tenant_id": tenant_id},
        )
        row = result.mappings().first()
        if row is None:
            return None
        return UserRecord(dict(row))

    async def get_by_id(
        self,
        user_id:   str,
        tenant_id: str,
    ) -> UserRecord | None:
        """Fetch a user by UUID, scoped to the tenant."""
        result = await self._session.execute(
            text("""
                SELECT
                    user_id, email, full_name, hashed_password, role,
                    tenant_id, is_active, is_verified, force_password_reset,
                    last_login_at, failed_login_count, locked_until
                FROM auth.users
                WHERE user_id   = :user_id
                  AND tenant_id = :tenant_id
                  AND is_active = TRUE
            """),
            {"user_id": user_id, "tenant_id": tenant_id},
        )
        row = result.mappings().first()
        if row is None:
            return None
        return UserRecord(dict(row))

    async def record_successful_login(self, user_id: str) -> None:
        """Reset failure counter and stamp last_login_at."""
        await self._session.execute(
            text("""
                UPDATE auth.users
                SET
                    last_login_at       = NOW(),
                    failed_login_count  = 0,
                    locked_until        = NULL,
                    updated_at          = NOW()
                WHERE user_id = :user_id
            """),
            {"user_id": user_id},
        )
        await self._session.commit()

    async def record_failed_login(self, user_id: str) -> int:
        """
        Increment failure counter. Locks the account for LOCKOUT_MINUTES
        once MAX_FAILURES is reached.
        Returns the new failure count.
        """
        result = await self._session.execute(
            text("""
                UPDATE auth.users
                SET
                    failed_login_count = failed_login_count + 1,
                    locked_until = CASE
                        WHEN failed_login_count + 1 >= :max_failures
                        THEN NOW() + INTERVAL ':lockout_minutes minutes'
                        ELSE NULL
                    END,
                    updated_at = NOW()
                WHERE user_id = :user_id
                RETURNING failed_login_count
            """),
            {
                "user_id":         user_id,
                "max_failures":    _MAX_FAILURES,
                "lockout_minutes": _LOCKOUT_MINUTES,
            },
        )
        await self._session.commit()
        row = result.first()
        return row[0] if row else 0

    async def create_user(
        self,
        email:           str,
        full_name:       str,
        hashed_password: str,
        role:            str,
        tenant_id:       str,
        created_by:      str | None = None,
    ) -> str:
        """
        Insert a new user. Returns the generated user_id (UUID string).
        Raises on duplicate email+tenant.
        """
        result = await self._session.execute(
            text("""
                INSERT INTO auth.users (
                    email, full_name, hashed_password, role,
                    tenant_id, created_by, is_verified
                )
                VALUES (
                    :email, :full_name, :hashed_password, :role,
                    :tenant_id, :created_by, TRUE
                )
                RETURNING user_id
            """),
            {
                "email":           email.lower().strip(),
                "full_name":       full_name,
                "hashed_password": hashed_password,
                "role":            role,
                "tenant_id":       tenant_id,
                "created_by":      created_by,
            },
        )
        await self._session.commit()
        row = result.first()
        return str(row[0])
