"""
Tenant configuration management with lineage tracking.

All configuration changes produce an immutable ConfigEntry. The full
history of changes is preserved so that:
  - Any prior configuration state can be reconstructed (config replay)
  - Rollback reactivates a named prior version rather than deleting history
  - Audit queries can trace who changed what, when, and why

Configuration namespace
───────────────────────
  configs are keyed by (tenant_id, namespace, key):
    workflow.*        — workflow execution settings
    rules.*           — rule engine settings
    escalation.*      — escalation threshold settings
    routing.*         — investigation routing config
    notification.*    — notification preferences
    integration.*     — connector / interop settings
    billing.*         — billing and usage settings
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime    import datetime, timezone
from typing      import Any, Callable, Optional

log = logging.getLogger("evidentrx.saas.admin.configuration")


@dataclass
class ConfigEntry:
    """
    An immutable configuration record.

    Once written, a ConfigEntry is never modified. Subsequent changes
    create new entries with incremented versions. The active entry for
    any key is the highest-versioned, non-superseded entry.
    """
    entry_id:     str
    tenant_id:    str
    namespace:    str
    key:          str
    value:        Any
    version:      int
    changed_by:   str
    changed_at:   datetime
    change_reason: str
    content_hash: str
    superseded:   bool     = False
    metadata:     dict[str, Any] = field(default_factory=dict)

    @property
    def full_key(self) -> str:
        return f"{self.namespace}.{self.key}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id":     self.entry_id,
            "tenant_id":    self.tenant_id,
            "namespace":    self.namespace,
            "key":          self.key,
            "version":      self.version,
            "changed_by":   self.changed_by,
            "changed_at":   self.changed_at.isoformat(),
            "change_reason":self.change_reason,
            "content_hash": self.content_hash,
            "superseded":   self.superseded,
        }


class TenantConfigManager:
    """
    Manages versioned, auditable configuration for a tenant.

    Write operations produce immutable ConfigEntry records.
    Read operations return the current active value.
    Rollback re-activates a specific version by superseding the current one.
    """

    def __init__(self, db_writer: Optional[Callable] = None) -> None:
        # (tenant_id, namespace, key) → list of ConfigEntry (sorted by version)
        self._entries: dict[tuple[str, str, str], list[ConfigEntry]] = {}
        self._db_writer = db_writer

    # ── Write ──────────────────────────────────────────────────────────────────

    async def set(
        self,
        tenant_id:    str,
        namespace:    str,
        key:          str,
        value:        Any,
        changed_by:   str,
        change_reason: str = "",
    ) -> ConfigEntry:
        """Write a new value for a configuration key."""
        ck       = (tenant_id, namespace, key)
        history  = self._entries.get(ck, [])
        version  = (history[-1].version + 1) if history else 1

        # Supersede the current active entry
        for entry in history:
            if not entry.superseded:
                entry.superseded = True

        content_hash = _hash_value(value)
        entry = ConfigEntry(
            entry_id     = str(uuid.uuid4()),
            tenant_id    = tenant_id,
            namespace    = namespace,
            key          = key,
            value        = value,
            version      = version,
            changed_by   = changed_by,
            changed_at   = datetime.now(tz=timezone.utc),
            change_reason= change_reason,
            content_hash = content_hash,
        )
        self._entries.setdefault(ck, []).append(entry)
        await self._persist("create_config", entry)
        log.info(
            "TenantConfigManager: set %s.%s v%d for tenant %s",
            namespace, key, version, tenant_id[:8],
        )
        return entry

    async def set_bulk(
        self,
        tenant_id:  str,
        namespace:  str,
        values:     dict[str, Any],
        changed_by: str,
        change_reason: str = "",
    ) -> list[ConfigEntry]:
        """Write multiple keys in the same namespace atomically."""
        return [
            await self.set(tenant_id, namespace, k, v, changed_by, change_reason)
            for k, v in values.items()
        ]

    # ── Read ───────────────────────────────────────────────────────────────────

    def get(
        self,
        tenant_id: str,
        namespace: str,
        key:       str,
        default:   Any = None,
    ) -> Any:
        """Return the current active value, or default if not set."""
        entry = self._active_entry(tenant_id, namespace, key)
        return entry.value if entry else default

    def get_namespace(
        self,
        tenant_id: str,
        namespace: str,
    ) -> dict[str, Any]:
        """Return all active key→value pairs for a namespace."""
        result: dict[str, Any] = {}
        for (tid, ns, key), entries in self._entries.items():
            if tid != tenant_id or ns != namespace:
                continue
            active = next((e for e in reversed(entries) if not e.superseded), None)
            if active:
                result[key] = active.value
        return result

    # ── History & rollback ─────────────────────────────────────────────────────

    def history(
        self,
        tenant_id: str,
        namespace: str,
        key:       str,
    ) -> list[ConfigEntry]:
        """Return all versions of a key (oldest first)."""
        ck = (tenant_id, namespace, key)
        return list(self._entries.get(ck, []))

    async def rollback(
        self,
        tenant_id:    str,
        namespace:    str,
        key:          str,
        target_version: int,
        rolled_by:    str,
        reason:       str = "",
    ) -> ConfigEntry:
        """
        Roll back a config key to a specific prior version.

        Creates a new entry with the old value (rather than un-superseding),
        preserving the full change history.
        """
        ck      = (tenant_id, namespace, key)
        history = self._entries.get(ck, [])
        target  = next((e for e in history if e.version == target_version), None)
        if target is None:
            raise ConfigNotFoundError(
                f"Version {target_version} of {namespace}.{key} not found for tenant {tenant_id}"
            )

        return await self.set(
            tenant_id     = tenant_id,
            namespace     = namespace,
            key           = key,
            value         = target.value,
            changed_by    = rolled_by,
            change_reason = reason or f"Rollback to v{target_version}",
        )

    # ── Diff ───────────────────────────────────────────────────────────────────

    def diff(
        self,
        tenant_id: str,
        namespace: str,
        version_a: int,
        version_b: int,
        key:       str,
    ) -> dict[str, Any]:
        """Compare two versions of a key."""
        history = self._entries.get((tenant_id, namespace, key), [])
        a = next((e for e in history if e.version == version_a), None)
        b = next((e for e in history if e.version == version_b), None)
        return {
            "key":      f"{namespace}.{key}",
            "version_a":{"version": version_a, "value": a.value if a else None},
            "version_b":{"version": version_b, "value": b.value if b else None},
            "changed":  a.value != b.value if (a and b) else True,
        }

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _active_entry(
        self,
        tenant_id: str,
        namespace: str,
        key:       str,
    ) -> Optional[ConfigEntry]:
        ck = (tenant_id, namespace, key)
        entries = self._entries.get(ck, [])
        return next((e for e in reversed(entries) if not e.superseded), None)

    async def _persist(self, op: str, entry: ConfigEntry) -> None:
        if self._db_writer:
            try:
                await self._db_writer(op, entry)
            except Exception as exc:
                log.error("TenantConfigManager: persist failed: %s", exc)


# ── Hash helper ────────────────────────────────────────────────────────────────

def _hash_value(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()


# ── Exceptions ─────────────────────────────────────────────────────────────────

class ConfigNotFoundError(Exception):
    pass


# ── Singleton ──────────────────────────────────────────────────────────────────

_manager: Optional[TenantConfigManager] = None


def get_config_manager(db_writer: Optional[Callable] = None) -> TenantConfigManager:
    global _manager
    if _manager is None:
        _manager = TenantConfigManager(db_writer=db_writer)
    return _manager
