"""
Organization-level RBAC and delegated administration.

Extends the platform's base RBAC (Phase 9) with organization-scoped
role assignments and delegated admin capabilities. An org admin can
manage users and permissions within their organization without having
platform-wide admin rights.

Role hierarchy (ascending privilege)
──────────────────────────────────────
  viewer          — read-only access to their org's cases and reports
  analyst         — submit feedback, work cases
  senior_analyst  — approve escalations, override thresholds
  org_admin       — manage users within org, configure org settings
  tenant_admin    — manage all orgs within tenant, full config access
  platform_admin  — cross-tenant read, platform operations (internal only)

Delegation rules
────────────────
  - An org_admin can grant roles up to org_admin within their org
  - A tenant_admin can grant any role within their tenant
  - No admin can grant a role higher than their own
  - Platform_admin cannot be granted via this system (provisioned separately)
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

log = logging.getLogger("evidentrx.saas.organizations.rbac")


class OrgRole(str, Enum):
    VIEWER         = "viewer"
    ANALYST        = "analyst"
    SENIOR_ANALYST = "senior_analyst"
    ORG_ADMIN      = "org_admin"
    TENANT_ADMIN   = "tenant_admin"
    PLATFORM_ADMIN = "platform_admin"   # internal only, never assignable via API


# Numeric privilege level for comparison
_ROLE_LEVEL: dict[OrgRole, int] = {
    OrgRole.VIEWER:         1,
    OrgRole.ANALYST:        2,
    OrgRole.SENIOR_ANALYST: 3,
    OrgRole.ORG_ADMIN:      4,
    OrgRole.TENANT_ADMIN:   5,
    OrgRole.PLATFORM_ADMIN: 10,
}


class OrgPermission(str, Enum):
    """Fine-grained permissions checked within an org context."""
    # Cases
    VIEW_CASES             = "view_cases"
    WORK_CASES             = "work_cases"
    CLOSE_CASES            = "close_cases"
    ESCALATE_CASES         = "escalate_cases"
    # Feedback
    SUBMIT_FEEDBACK        = "submit_feedback"
    VIEW_FEEDBACK          = "view_feedback"
    # Configuration
    VIEW_CONFIG            = "view_config"
    EDIT_CONFIG            = "edit_config"
    MANAGE_RULE_PACKS      = "manage_rule_packs"
    # Users
    VIEW_USERS             = "view_users"
    INVITE_USERS           = "invite_users"
    MANAGE_USERS           = "manage_users"
    # Reports
    VIEW_REPORTS           = "view_reports"
    EXPORT_REPORTS         = "export_reports"
    # API
    MANAGE_API_KEYS        = "manage_api_keys"
    # Billing
    VIEW_BILLING           = "view_billing"


# Permission set granted to each role (cumulative — higher roles include lower)
_ROLE_PERMISSIONS: dict[OrgRole, frozenset] = {
    OrgRole.VIEWER: frozenset({
        OrgPermission.VIEW_CASES,
        OrgPermission.VIEW_REPORTS,
        OrgPermission.VIEW_FEEDBACK,
    }),
    OrgRole.ANALYST: frozenset({
        OrgPermission.VIEW_CASES, OrgPermission.WORK_CASES,
        OrgPermission.ESCALATE_CASES, OrgPermission.SUBMIT_FEEDBACK,
        OrgPermission.VIEW_FEEDBACK, OrgPermission.VIEW_REPORTS,
    }),
    OrgRole.SENIOR_ANALYST: frozenset({
        OrgPermission.VIEW_CASES, OrgPermission.WORK_CASES,
        OrgPermission.CLOSE_CASES, OrgPermission.ESCALATE_CASES,
        OrgPermission.SUBMIT_FEEDBACK, OrgPermission.VIEW_FEEDBACK,
        OrgPermission.VIEW_REPORTS, OrgPermission.EXPORT_REPORTS,
        OrgPermission.VIEW_CONFIG,
    }),
    OrgRole.ORG_ADMIN: frozenset({
        OrgPermission.VIEW_CASES, OrgPermission.WORK_CASES,
        OrgPermission.CLOSE_CASES, OrgPermission.ESCALATE_CASES,
        OrgPermission.SUBMIT_FEEDBACK, OrgPermission.VIEW_FEEDBACK,
        OrgPermission.VIEW_REPORTS, OrgPermission.EXPORT_REPORTS,
        OrgPermission.VIEW_CONFIG, OrgPermission.EDIT_CONFIG,
        OrgPermission.VIEW_USERS, OrgPermission.INVITE_USERS,
        OrgPermission.MANAGE_USERS, OrgPermission.MANAGE_RULE_PACKS,
        OrgPermission.MANAGE_API_KEYS,
    }),
    OrgRole.TENANT_ADMIN: frozenset(OrgPermission),   # all permissions
    OrgRole.PLATFORM_ADMIN: frozenset(OrgPermission),
}


@dataclass
class RoleAssignment:
    """A user's role assignment within an org scope."""
    assignment_id: str
    tenant_id:     str
    org_id:        str | None    # None = tenant-wide assignment
    user_id:       str
    role:          OrgRole
    granted_by:    str
    granted_at:    datetime
    expires_at:    datetime | None = None
    metadata:      dict[str, Any]     = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now(tz=UTC) > self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "assignment_id": self.assignment_id,
            "tenant_id":     self.tenant_id,
            "org_id":        self.org_id,
            "user_id":       self.user_id,
            "role":          self.role.value,
            "granted_by":    self.granted_by,
            "granted_at":    self.granted_at.isoformat(),
            "expires_at":    self.expires_at.isoformat() if self.expires_at else None,
        }


class OrgRBACEngine:
    """
    Evaluates role assignments and permission checks for org-scoped access.

    Assignment lookup order:
    1. Org-specific assignment (most specific)
    2. Tenant-wide assignment (fallback)
    3. No assignment → deny
    """

    def __init__(self, db_writer: Callable | None = None) -> None:
        self._assignments: dict[str, RoleAssignment] = {}
        # (tenant_id, user_id, org_id) → assignment_id
        self._index: dict[tuple[str, str, str | None], str] = {}
        self._db_writer = db_writer

    # ── Assign / revoke ────────────────────────────────────────────────────────

    async def assign_role(
        self,
        tenant_id:  str,
        user_id:    str,
        role:       OrgRole,
        granted_by: str,
        org_id:     str | None      = None,
        expires_at: datetime | None = None,
    ) -> RoleAssignment:
        """
        Grant a role to a user within an org (or tenant-wide if org_id=None).

        Self-grant and privilege escalation are blocked.
        """
        if user_id == granted_by:
            raise RBACError("Users cannot assign roles to themselves")

        granter_role = self.effective_role(tenant_id, granted_by, org_id)
        if granter_role is None:
            raise RBACError(f"Granter {granted_by} has no role in this scope")

        granter_level = _ROLE_LEVEL.get(granter_role, 0)
        target_level  = _ROLE_LEVEL.get(role, 0)
        if target_level >= granter_level:
            raise RBACError(
                f"Cannot grant {role.value} — granter has {granter_role.value} "
                f"(level {granter_level}); target requires level < {granter_level}"
            )
        if role == OrgRole.PLATFORM_ADMIN:
            raise RBACError("PLATFORM_ADMIN cannot be granted via the RBAC API")

        assignment = RoleAssignment(
            assignment_id = str(uuid.uuid4()),
            tenant_id     = tenant_id,
            org_id        = org_id,
            user_id       = user_id,
            role          = role,
            granted_by    = granted_by,
            granted_at    = datetime.now(tz=UTC),
            expires_at    = expires_at,
        )
        self._assignments[assignment.assignment_id] = assignment
        self._index[(tenant_id, user_id, org_id)] = assignment.assignment_id
        await self._persist("create_assignment", assignment)
        log.info(
            "OrgRBACEngine: granted %s to %s in scope org=%s by %s",
            role.value, user_id[:8], org_id[:8] if org_id else "tenant",
            granted_by[:8],
        )
        return assignment

    async def revoke_role(
        self,
        tenant_id:  str,
        user_id:    str,
        org_id:     str | None,
        revoked_by: str,
    ) -> bool:
        key = (tenant_id, user_id, org_id)
        aid = self._index.pop(key, None)
        if aid is None:
            return False
        assignment = self._assignments.pop(aid, None)
        if assignment:
            assignment.metadata["revoked_by"] = revoked_by
            assignment.metadata["revoked_at"] = datetime.now(tz=UTC).isoformat()
            await self._persist("revoke_assignment", assignment)
        return True

    # ── Check ──────────────────────────────────────────────────────────────────

    def effective_role(
        self,
        tenant_id: str,
        user_id:   str,
        org_id:    str | None = None,
    ) -> OrgRole | None:
        """
        Return the most specific role for a user in a given scope.

        Checks org-specific first, falls back to tenant-wide.
        Returns None if no role found.
        """
        # Org-specific assignment
        if org_id:
            aid = self._index.get((tenant_id, user_id, org_id))
            if aid:
                a = self._assignments.get(aid)
                if a and not a.is_expired:
                    return a.role

        # Tenant-wide assignment
        aid = self._index.get((tenant_id, user_id, None))
        if aid:
            a = self._assignments.get(aid)
            if a and not a.is_expired:
                return a.role

        return None

    def has_permission(
        self,
        tenant_id:  str,
        user_id:    str,
        permission: OrgPermission,
        org_id:     str | None = None,
    ) -> bool:
        role = self.effective_role(tenant_id, user_id, org_id)
        if role is None:
            return False
        return permission in _ROLE_PERMISSIONS.get(role, frozenset())

    def require_permission(
        self,
        tenant_id:  str,
        user_id:    str,
        permission: OrgPermission,
        org_id:     str | None = None,
    ) -> None:
        if not self.has_permission(tenant_id, user_id, permission, org_id):
            raise PermissionDeniedError(
                f"User {user_id} lacks {permission.value} in org={org_id or 'tenant'}"
            )

    def list_users_in_org(
        self,
        tenant_id: str,
        org_id:    str,
    ) -> list[RoleAssignment]:
        return [
            a for a in self._assignments.values()
            if a.tenant_id == tenant_id
            and (a.org_id == org_id or a.org_id is None)
            and not a.is_expired
        ]

    async def _persist(self, op: str, obj: Any) -> None:
        if self._db_writer:
            try:
                await self._db_writer(op, obj)
            except Exception as exc:
                log.error("OrgRBACEngine: persist failed: %s", exc)


# ── Exceptions ─────────────────────────────────────────────────────────────────

class RBACError(Exception):
    pass

class PermissionDeniedError(Exception):
    pass


# ── Singleton ──────────────────────────────────────────────────────────────────

_engine: OrgRBACEngine | None = None


def get_rbac_engine(db_writer: Callable | None = None) -> OrgRBACEngine:
    global _engine
    if _engine is None:
        _engine = OrgRBACEngine(db_writer=db_writer)
    return _engine
