"""
Connector access control.

Governs which users and services can read, write, configure, and trigger
syncs on healthcare data connectors. Enforces:
  - Tenant isolation: connectors are never accessible across tenant boundaries
  - Role-based access: minimum required role per action
  - Audit logging: every access decision is recorded
  - Source whitelist: only approved source systems can write canonical data

Access control model
────────────────────
  Principal   : user (JWT identity) or service account
  Resource    : connector (identified by connector_id + tenant_id)
  Action      : read_data | trigger_sync | configure | delete

  Rules:
    read_data     → analyst+
    trigger_sync  → senior_analyst+
    configure     → admin+
    delete        → admin+ (soft delete only; hard delete requires system role)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime    import datetime, timezone
from enum        import Enum
from typing      import Any, Optional

log = logging.getLogger("evidentrx.interop.governance.access_control")


class ConnectorAction(str, Enum):
    READ_DATA     = "read_data"
    TRIGGER_SYNC  = "trigger_sync"
    CONFIGURE     = "configure"
    DELETE        = "delete"
    VIEW_LINEAGE  = "view_lineage"
    REPLAY        = "replay"


class UserRole(str, Enum):
    ANALYST        = "analyst"
    SENIOR_ANALYST = "senior_analyst"
    AUDITOR        = "auditor"
    ADMIN          = "admin"
    SYSTEM         = "system"


# Role hierarchy (higher index = more privileged)
_ROLE_LEVELS = {
    UserRole.ANALYST:        1,
    UserRole.SENIOR_ANALYST: 2,
    UserRole.AUDITOR:        3,
    UserRole.ADMIN:          4,
    UserRole.SYSTEM:         5,
}

# Minimum role required for each connector action
_ACTION_MIN_ROLE: dict[ConnectorAction, UserRole] = {
    ConnectorAction.READ_DATA:    UserRole.ANALYST,
    ConnectorAction.VIEW_LINEAGE: UserRole.ANALYST,
    ConnectorAction.TRIGGER_SYNC: UserRole.SENIOR_ANALYST,
    ConnectorAction.REPLAY:       UserRole.SENIOR_ANALYST,
    ConnectorAction.CONFIGURE:    UserRole.ADMIN,
    ConnectorAction.DELETE:       UserRole.ADMIN,
}


@dataclass
class Principal:
    """Represents the calling user or service account."""
    id:        str
    tenant_id: str
    role:      UserRole
    is_service: bool = False


@dataclass
class AccessDecision:
    allowed:      bool
    action:       ConnectorAction
    connector_id: str
    principal_id: str
    reason:       str
    decided_at:   datetime = None    # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.decided_at is None:
            self.decided_at = datetime.now(tz=timezone.utc)


class ConnectorAccessControl:
    """
    Stateless connector access controller.

    All decisions are deterministic given the same inputs (no DB calls).
    Callers are responsible for recording the AccessDecision in the audit log.
    """

    def check(
        self,
        principal:    Principal,
        action:       ConnectorAction,
        connector_id: str,
        connector_tenant_id: str,
    ) -> AccessDecision:
        """
        Evaluate whether a principal may perform an action on a connector.

        Rules (checked in order):
          1. Tenant isolation: principal.tenant_id must equal connector's tenant_id
          2. Role check: principal.role >= minimum role for the action
        """
        # ── Tenant isolation ──────────────────────────────────────────────────
        if principal.tenant_id != connector_tenant_id:
            decision = AccessDecision(
                allowed      = False,
                action       = action,
                connector_id = connector_id,
                principal_id = principal.id,
                reason       = (
                    f"Tenant isolation violation: principal tenant {principal.tenant_id!r} "
                    f"cannot access connector on tenant {connector_tenant_id!r}"
                ),
            )
            log.warning(
                "ACCESS DENIED [tenant isolation]: %s → %s/%s",
                principal.id, connector_id, action.value,
            )
            return decision

        # ── Role check ────────────────────────────────────────────────────────
        required_role  = _ACTION_MIN_ROLE.get(action, UserRole.ADMIN)
        principal_level = _ROLE_LEVELS.get(principal.role, 0)
        required_level  = _ROLE_LEVELS.get(required_role, 99)

        if principal_level < required_level:
            decision = AccessDecision(
                allowed      = False,
                action       = action,
                connector_id = connector_id,
                principal_id = principal.id,
                reason       = (
                    f"Insufficient role: {principal.role.value!r} < {required_role.value!r} "
                    f"required for {action.value!r}"
                ),
            )
            log.warning(
                "ACCESS DENIED [role]: %s (%s) → %s/%s (requires %s)",
                principal.id, principal.role.value,
                connector_id, action.value, required_role.value,
            )
            return decision

        # ── Allowed ───────────────────────────────────────────────────────────
        decision = AccessDecision(
            allowed      = True,
            action       = action,
            connector_id = connector_id,
            principal_id = principal.id,
            reason       = "OK",
        )
        log.debug(
            "ACCESS ALLOWED: %s (%s) → %s/%s",
            principal.id, principal.role.value, connector_id, action.value,
        )
        return decision

    def require(
        self,
        principal:    Principal,
        action:       ConnectorAction,
        connector_id: str,
        connector_tenant_id: str,
    ) -> None:
        """
        Like check(), but raises ConnectorAccessDenied if access is not allowed.

        Use in FastAPI endpoints / pipeline code where a denied access should
        abort the operation immediately.
        """
        decision = self.check(principal, action, connector_id, connector_tenant_id)
        if not decision.allowed:
            raise ConnectorAccessDenied(decision.reason)


class ConnectorAccessDenied(Exception):
    """Raised when a principal lacks permission for a connector action."""


# ── Source system whitelist ───────────────────────────────────────────────────

# Only these source systems are permitted to write canonical records.
# Any canonical record with a source_system not in this set is rejected.
_APPROVED_SOURCES = frozenset({
    "fhir",
    "hl7v2",
    "x12_837p",
    "x12_835",
    "x12_837_medicaid",
    "ncpdp_batch",
    "pbm_api",
    "database_direct",
})


def validate_source_system(source_system: str) -> bool:
    """Return True if the source system is on the approved whitelist."""
    return source_system in _APPROVED_SOURCES


# ── Module-level singleton ────────────────────────────────────────────────────

_acl = ConnectorAccessControl()


def get_access_control() -> ConnectorAccessControl:
    """Return the module-level ConnectorAccessControl singleton."""
    return _acl
