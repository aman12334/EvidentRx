"""
Admin action audit log.

Records every administrative action taken on a tenant — provisioning,
configuration changes, user management, rule pack assignments — with
full attribution and context. Immutable append-only records.

This is a separate audit trail from the learning governance audit
(Phase 11) and the interoperability audit (Phase 10). Each layer
maintains its own purpose-built audit log:

  saas/admin/audit.py         — platform admin operations
  interoperability/governance/audit.py — data ingestion governance
  learning/governance/audit.py        — learning system governance
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime    import datetime, timezone
from enum        import Enum
from typing      import Any, Callable, Optional

log = logging.getLogger("evidentrx.saas.admin.audit")


class AdminEventType(str, Enum):
    # Tenant lifecycle
    TENANT_CREATED       = "tenant_created"
    TENANT_ACTIVATED     = "tenant_activated"
    TENANT_SUSPENDED     = "tenant_suspended"
    TENANT_ARCHIVED      = "tenant_archived"
    TENANT_TIER_CHANGED  = "tenant_tier_changed"
    # Organization management
    ORG_CREATED          = "org_created"
    ORG_UPDATED          = "org_updated"
    ORG_DEACTIVATED      = "org_deactivated"
    ENTITY_ADDED_TO_ORG  = "entity_added_to_org"
    # User management
    USER_INVITED         = "user_invited"
    USER_ROLE_GRANTED    = "user_role_granted"
    USER_ROLE_REVOKED    = "user_role_revoked"
    USER_SUSPENDED       = "user_suspended"
    # Configuration
    CONFIG_CHANGED       = "config_changed"
    CONFIG_ROLLED_BACK   = "config_rolled_back"
    RULE_PACK_ASSIGNED   = "rule_pack_assigned"
    RULE_PACK_REVOKED    = "rule_pack_revoked"
    # API keys
    API_KEY_CREATED      = "api_key_created"
    API_KEY_ROTATED      = "api_key_rotated"
    API_KEY_REVOKED      = "api_key_revoked"
    # Billing / usage
    BILLING_PLAN_CHANGED = "billing_plan_changed"
    TRIAL_EXTENDED       = "trial_extended"
    # Governance
    LEGAL_HOLD_PLACED    = "legal_hold_placed"
    LEGAL_HOLD_RELEASED  = "legal_hold_released"
    RETENTION_CHANGED    = "retention_changed"
    DATA_EXPORT_INITIATED= "data_export_initiated"
    # Access control
    ACCESS_DENIED        = "access_denied"
    ISOLATION_VIOLATION  = "isolation_violation"


@dataclass
class AdminAuditRecord:
    """An immutable administrative audit event."""
    audit_id:     str
    tenant_id:    str
    event_type:   AdminEventType
    actor:        str             # user_id of the admin performing the action
    target_id:    Optional[str]   # tenant_id, org_id, user_id, etc.
    target_type:  Optional[str]   # "tenant" | "org" | "user" | "config" | …
    payload:      dict[str, Any]  # event-specific context (no raw credentials)
    occurred_at:  datetime
    content_hash: str
    source_ip:    Optional[str]   = None
    session_id:   Optional[str]   = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_id":    self.audit_id,
            "tenant_id":   self.tenant_id,
            "event_type":  self.event_type.value,
            "actor":       self.actor,
            "target_id":   self.target_id,
            "target_type": self.target_type,
            "occurred_at": self.occurred_at.isoformat(),
            "content_hash":self.content_hash,
        }


class AdminAuditLog:
    """
    Append-only administrative audit log.

    All platform admin actions — whether performed by tenant admins,
    platform operators, or automation — must be recorded here before
    the action takes effect (log-then-act pattern).
    """

    def __init__(self, db_writer: Optional[Callable] = None) -> None:
        self._records:   list[AdminAuditRecord] = []
        self._by_tenant: dict[str, list[str]]   = {}   # tenant_id → [audit_id]
        self._buffer:    list[AdminAuditRecord]  = []
        self._db_writer  = db_writer

    # ── Log ────────────────────────────────────────────────────────────────────

    async def log(
        self,
        tenant_id:   str,
        event_type:  AdminEventType,
        actor:       str,
        payload:     dict[str, Any],
        target_id:   Optional[str] = None,
        target_type: Optional[str] = None,
        source_ip:   Optional[str] = None,
        session_id:  Optional[str] = None,
    ) -> AdminAuditRecord:
        content_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()

        record = AdminAuditRecord(
            audit_id     = str(uuid.uuid4()),
            tenant_id    = tenant_id,
            event_type   = event_type,
            actor        = actor,
            target_id    = target_id,
            target_type  = target_type,
            payload      = payload,
            occurred_at  = datetime.now(tz=timezone.utc),
            content_hash = content_hash,
            source_ip    = source_ip,
            session_id   = session_id,
        )
        self._records.append(record)
        self._by_tenant.setdefault(tenant_id, []).append(record.audit_id)
        self._buffer.append(record)

        if len(self._buffer) >= 100:
            await self.flush()

        return record

    async def flush(self) -> None:
        if not self._buffer or not self._db_writer:
            self._buffer.clear()
            return
        try:
            for rec in self._buffer:
                await self._db_writer("create_admin_audit", rec)
        except Exception as exc:
            log.error("AdminAuditLog: flush failed: %s", exc)
        finally:
            self._buffer.clear()

    # ── Convenience wrappers ───────────────────────────────────────────────────

    async def log_config_change(
        self,
        tenant_id:  str,
        actor:      str,
        namespace:  str,
        key:        str,
        version:    int,
    ) -> AdminAuditRecord:
        return await self.log(
            tenant_id   = tenant_id,
            event_type  = AdminEventType.CONFIG_CHANGED,
            actor       = actor,
            payload     = {"namespace": namespace, "key": key, "version": version},
            target_type = "config",
        )

    async def log_role_granted(
        self,
        tenant_id:    str,
        actor:        str,
        target_user:  str,
        role:         str,
        org_id:       Optional[str] = None,
    ) -> AdminAuditRecord:
        return await self.log(
            tenant_id   = tenant_id,
            event_type  = AdminEventType.USER_ROLE_GRANTED,
            actor       = actor,
            payload     = {"role": role, "org_id": org_id},
            target_id   = target_user,
            target_type = "user",
        )

    async def log_api_key_event(
        self,
        tenant_id:  str,
        actor:      str,
        key_id:     str,
        event_type: AdminEventType,
    ) -> AdminAuditRecord:
        return await self.log(
            tenant_id   = tenant_id,
            event_type  = event_type,
            actor       = actor,
            payload     = {"key_id": key_id},
            target_id   = key_id,
            target_type = "api_key",
        )

    async def log_isolation_violation(
        self,
        tenant_id:          str,
        actor:              str,
        resource_tenant_id: str,
    ) -> AdminAuditRecord:
        return await self.log(
            tenant_id   = tenant_id,
            event_type  = AdminEventType.ISOLATION_VIOLATION,
            actor       = actor,
            payload     = {"resource_tenant_id": resource_tenant_id},
            target_type = "isolation",
        )

    # ── Query ──────────────────────────────────────────────────────────────────

    def query(
        self,
        tenant_id:  str,
        event_type: Optional[AdminEventType] = None,
        actor:      Optional[str]            = None,
        since:      Optional[datetime]       = None,
        limit:      int                      = 200,
    ) -> list[AdminAuditRecord]:
        result = [
            r for r in self._records
            if r.tenant_id == tenant_id
            and (event_type is None or r.event_type == event_type)
            and (actor is None or r.actor == actor)
            and (since is None or r.occurred_at >= since)
        ]
        return sorted(result, key=lambda r: r.occurred_at, reverse=True)[:limit]


# ── Singleton ──────────────────────────────────────────────────────────────────

_audit_log: Optional[AdminAuditLog] = None


def get_admin_audit_log(db_writer: Optional[Callable] = None) -> AdminAuditLog:
    global _audit_log
    if _audit_log is None:
        _audit_log = AdminAuditLog(db_writer=db_writer)
    return _audit_log
