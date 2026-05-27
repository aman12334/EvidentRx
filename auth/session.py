"""
Server-side refresh token session store.

Refresh tokens are tracked in-process (Redis-backed in production) to enable:
  - Logout (token revocation)
  - Single-session enforcement
  - Suspicious re-use detection (refresh token rotation)
  - Audit trail of session creation/termination

In production, replace _InMemoryStore with a Redis-backed implementation.
The interface is intentionally identical to enable swap-out without API changes.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing   import Dict, Optional

from auth.models import RefreshTokenRecord


class _InMemoryStore:
    """
    Ephemeral in-process session store.
    PRODUCTION: replace with RedisSessionStore (see comments below).
    """

    def __init__(self) -> None:
        self._store: Dict[str, RefreshTokenRecord] = {}
        self._lock = asyncio.Lock()

    async def save(self, record: RefreshTokenRecord) -> None:
        async with self._lock:
            self._store[record.jti] = record

    async def get(self, jti: str) -> Optional[RefreshTokenRecord]:
        async with self._lock:
            return self._store.get(jti)

    async def revoke(self, jti: str) -> bool:
        """Mark a token as revoked. Returns True if it existed."""
        async with self._lock:
            record = self._store.get(jti)
            if not record:
                return False
            self._store[jti] = record.model_copy(
                update={"revoked": True, "revoked_at": datetime.now(tz=timezone.utc)}
            )
            return True

    async def revoke_all_for_user(self, user_id: str) -> int:
        """Revoke all active sessions for a user. Returns count revoked."""
        now = datetime.now(tz=timezone.utc)
        async with self._lock:
            count = 0
            for jti, record in self._store.items():
                if record.user_id == user_id and not record.revoked:
                    self._store[jti] = record.model_copy(
                        update={"revoked": True, "revoked_at": now}
                    )
                    count += 1
            return count

    async def is_valid(self, jti: str) -> bool:
        """Return True if token exists, is not revoked, and is not expired."""
        record = await self.get(jti)
        if not record or record.revoked:
            return False
        return datetime.now(tz=timezone.utc) < record.expires_at

    async def cleanup_expired(self) -> int:
        """Purge expired/revoked tokens. Should be called periodically."""
        now = datetime.now(tz=timezone.utc)
        async with self._lock:
            stale = [
                jti for jti, r in self._store.items()
                if r.revoked or r.expires_at <= now
            ]
            for jti in stale:
                del self._store[jti]
            return len(stale)


# ─── Singleton session store ───────────────────────────────────────────────────
# Production: swap to RedisSessionStore
#
# class RedisSessionStore:
#     def __init__(self, redis_url: str): ...
#     async def save(self, record): await redis.setex(f"session:{record.jti}", ttl, ...)
#     async def revoke(self, jti): await redis.delete(f"session:{jti}")
#     async def is_valid(self, jti): return bool(await redis.exists(f"session:{jti}"))

session_store = _InMemoryStore()
