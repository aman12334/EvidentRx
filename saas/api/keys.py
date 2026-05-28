"""
API key management — issuance, rotation, and revocation.

Every programmatic API consumer is issued an API key scoped to a tenant
and optional org. Keys carry a permission scope (subset of OrgPermission
values) and a configurable expiry. Rotation creates a new key while
keeping the old one valid for a short overlap window (grace period).

Security design
───────────────
- Keys are stored as SHA-256 hashes; the plaintext is only returned
  once at issuance (caller must store it securely)
- Lookup is O(1) via hash index
- Rotation never permanently deletes the old key immediately — a
  configurable grace window (default 24 h) allows seamless cutover
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

log = logging.getLogger("evidentrx.saas.api.keys")

_KEY_BYTES      = 32      # 256-bit random key
_DEFAULT_EXPIRY_DAYS   = 365
_DEFAULT_GRACE_HOURS   = 24


class APIKeyStatus(str, Enum):
    ACTIVE   = "active"
    EXPIRED  = "expired"
    REVOKED  = "revoked"
    ROTATING = "rotating"   # old key during grace window


@dataclass
class APIKey:
    """
    A tenant-scoped API key record.

    ``key_hash`` is the SHA-256 hex digest of the raw key. The raw key
    is never stored — it is returned only at creation / rotation time.
    """
    key_id:      str
    tenant_id:   str
    name:        str
    key_hash:    str                    # SHA-256 of raw key
    key_prefix:  str                    # first 8 chars of raw key (for display)
    status:      APIKeyStatus
    scopes:      list[str]             = field(default_factory=list)   # OrgPermission values
    org_id:      str | None         = None
    created_by:  str                   = "system"
    created_at:  datetime              = field(default_factory=lambda: datetime.now(tz=UTC))
    expires_at:  datetime | None    = None
    last_used_at: datetime | None   = None
    grace_until: datetime | None    = None   # rotation grace-window end
    rotated_to:  str | None         = None   # key_id of replacement
    metadata:    dict[str, Any]        = field(default_factory=dict)

    @property
    def is_valid(self) -> bool:
        if self.status == APIKeyStatus.REVOKED:
            return False
        if self.status == APIKeyStatus.ROTATING:
            # Valid only within grace window
            return self.grace_until is not None and datetime.now(tz=UTC) <= self.grace_until
        if self.expires_at and datetime.now(tz=UTC) > self.expires_at:
            return False
        return self.status == APIKeyStatus.ACTIVE

    def to_dict(self) -> dict[str, Any]:
        return {
            "key_id":       self.key_id,
            "tenant_id":    self.tenant_id,
            "name":         self.name,
            "key_prefix":   self.key_prefix,
            "status":       self.status.value,
            "scopes":       self.scopes,
            "org_id":       self.org_id,
            "created_by":   self.created_by,
            "created_at":   self.created_at.isoformat(),
            "expires_at":   self.expires_at.isoformat() if self.expires_at else None,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
        }


@dataclass
class KeyIssuanceResult:
    """Returned once at key creation or rotation — plaintext key not stored."""
    api_key:    APIKey
    raw_key:    str    # caller must store this securely; never repeated


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def _generate_key() -> str:
    return secrets.token_urlsafe(_KEY_BYTES)


class APIKeyStore:
    """
    Stores and manages API keys for all tenants.

    Keys are indexed by key_hash for O(1) authentication lookups.
    """

    def __init__(self) -> None:
        # key_id → APIKey
        self._keys:    dict[str, APIKey] = {}
        # key_hash → key_id  (for authenticate())
        self._by_hash: dict[str, str]    = {}

    # ── Issuance ───────────────────────────────────────────────────────────────

    def issue(
        self,
        tenant_id:    str,
        name:         str,
        created_by:   str,
        scopes:       list[str] | None  = None,
        org_id:       str | None        = None,
        expiry_days:  int                  = _DEFAULT_EXPIRY_DAYS,
        metadata:     dict[str, Any] | None = None,
    ) -> KeyIssuanceResult:
        raw     = _generate_key()
        khash   = _hash_key(raw)
        key = APIKey(
            key_id     = str(uuid.uuid4()),
            tenant_id  = tenant_id,
            name       = name,
            key_hash   = khash,
            key_prefix = raw[:8],
            status     = APIKeyStatus.ACTIVE,
            scopes     = scopes or [],
            org_id     = org_id,
            created_by = created_by,
            expires_at = datetime.now(tz=UTC) + timedelta(days=expiry_days),
            metadata   = metadata or {},
        )
        self._keys[key.key_id]   = key
        self._by_hash[khash]     = key.key_id
        log.info(
            "APIKeyStore: issued key '%s' for tenant %s (prefix %s)",
            name, tenant_id[:8], raw[:8],
        )
        return KeyIssuanceResult(api_key=key, raw_key=raw)

    # ── Authentication ─────────────────────────────────────────────────────────

    def authenticate(self, raw_key: str) -> APIKey | None:
        """
        Validate a raw key. Returns the APIKey if valid, else None.

        Updates last_used_at on success.
        """
        khash = _hash_key(raw_key)
        key_id = self._by_hash.get(khash)
        if key_id is None:
            return None
        key = self._keys.get(key_id)
        if key is None or not key.is_valid:
            return None
        key.last_used_at = datetime.now(tz=UTC)
        return key

    # ── Rotation ───────────────────────────────────────────────────────────────

    def rotate(
        self,
        tenant_id:   str,
        key_id:      str,
        rotated_by:  str,
        grace_hours: int = _DEFAULT_GRACE_HOURS,
    ) -> KeyIssuanceResult:
        """
        Issue a replacement key and put the old key into ROTATING status.

        The old key remains valid for ``grace_hours`` to allow callers to
        migrate. After the grace period it becomes EXPIRED automatically
        (enforced on next authenticate() call).
        """
        old = self._get_owned(tenant_id, key_id)
        if not old.is_valid:
            raise KeyError(f"Key {key_id[:8]} is not active")

        # Issue new key with same parameters
        result = self.issue(
            tenant_id   = tenant_id,
            name        = f"{old.name} (rotated)",
            created_by  = rotated_by,
            scopes      = old.scopes,
            org_id      = old.org_id,
            expiry_days = _DEFAULT_EXPIRY_DAYS,
            metadata    = {**old.metadata, "rotated_from": key_id},
        )
        # Mark old key as rotating
        old.status      = APIKeyStatus.ROTATING
        old.grace_until = datetime.now(tz=UTC) + timedelta(hours=grace_hours)
        old.rotated_to  = result.api_key.key_id
        old.metadata["rotated_by"] = rotated_by
        log.info(
            "APIKeyStore: rotated key %s → %s (grace %dh)",
            key_id[:8], result.api_key.key_id[:8], grace_hours,
        )
        return result

    # ── Revocation ─────────────────────────────────────────────────────────────

    def revoke(
        self,
        tenant_id:  str,
        key_id:     str,
        revoked_by: str,
        reason:     str = "",
    ) -> APIKey:
        key = self._get_owned(tenant_id, key_id)
        key.status = APIKeyStatus.REVOKED
        key.metadata["revoked_by"] = revoked_by
        key.metadata["revoke_reason"] = reason
        key.metadata["revoked_at"] = datetime.now(tz=UTC).isoformat()
        log.info("APIKeyStore: revoked key %s", key_id[:8])
        return key

    # ── Queries ────────────────────────────────────────────────────────────────

    def list_keys(
        self,
        tenant_id:   str,
        active_only: bool = True,
        org_id:      str | None = None,
    ) -> list[APIKey]:
        return [
            k for k in self._keys.values()
            if k.tenant_id == tenant_id
            and (not active_only or k.is_valid)
            and (org_id is None or k.org_id == org_id)
        ]

    def get_key(self, tenant_id: str, key_id: str) -> APIKey | None:
        k = self._keys.get(key_id)
        return k if k and k.tenant_id == tenant_id else None

    def expiring_soon(
        self,
        tenant_id:   str,
        within_days: int = 30,
    ) -> list[APIKey]:
        threshold = datetime.now(tz=UTC) + timedelta(days=within_days)
        return [
            k for k in self._keys.values()
            if k.tenant_id == tenant_id
            and k.status == APIKeyStatus.ACTIVE
            and k.expires_at is not None
            and k.expires_at <= threshold
        ]

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _get_owned(self, tenant_id: str, key_id: str) -> APIKey:
        k = self._keys.get(key_id)
        if k is None or k.tenant_id != tenant_id:
            raise APIKeyNotFoundError(key_id)
        return k


# ── Exceptions ─────────────────────────────────────────────────────────────────

class APIKeyNotFoundError(Exception):
    pass


# ── Singleton ──────────────────────────────────────────────────────────────────

_store: APIKeyStore | None = None


def get_api_key_store() -> APIKeyStore:
    global _store
    if _store is None:
        _store = APIKeyStore()
    return _store
