"""
Organisation-level governance settings.

Each organisation within a tenant may have governance rules that go
beyond the platform defaults — approval quorum requirements, maximum
open case age before automatic escalation, mandatory second-review
triggers, and reporting cadence overrides.

Settings are versioned and immutable once superseded; the active
version is always the most recent non-superseded entry.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime    import datetime, timezone
from typing      import Any, Optional

log = logging.getLogger("evidentrx.saas.governance.settings")


@dataclass
class OrgGovernanceSettings:
    """
    Governance configuration for a single organisation.

    Validation rules
    ────────────────
    - min_reviewers must be ≥ 1
    - auto_escalate_hours must be ≥ 24
    - second_review_threshold (risk score 0.0–1.0): above this value a
      second analyst review is mandatory before closing
    """
    settings_id:            str
    tenant_id:              str
    org_id:                 str
    version:                int
    min_reviewers:          int            = 1
    auto_escalate_hours:    int            = 72    # open case age before auto-escalate
    second_review_threshold: float         = 0.80  # risk score threshold
    mandatory_fields:       list[str]      = field(default_factory=list)  # required on close
    reporting_cadence_days: int            = 30    # compliance report frequency
    allow_self_close:       bool           = False  # analyst can close own cases?
    require_evidence_upload: bool          = True
    created_by:             str            = "system"
    created_at:             datetime       = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    superseded:             bool           = False
    content_hash:           str            = ""
    notes:                  str            = ""

    def __post_init__(self) -> None:
        if self.min_reviewers < 1:
            raise ValueError("min_reviewers must be at least 1")
        if self.auto_escalate_hours < 24:
            raise ValueError("auto_escalate_hours must be at least 24")
        if not (0.0 <= self.second_review_threshold <= 1.0):
            raise ValueError("second_review_threshold must be in [0.0, 1.0]")
        if not self.content_hash:
            self.content_hash = self._compute_hash()

    def _compute_hash(self) -> str:
        payload = {
            "min_reviewers":           self.min_reviewers,
            "auto_escalate_hours":     self.auto_escalate_hours,
            "second_review_threshold": self.second_review_threshold,
            "mandatory_fields":        sorted(self.mandatory_fields),
            "reporting_cadence_days":  self.reporting_cadence_days,
            "allow_self_close":        self.allow_self_close,
            "require_evidence_upload": self.require_evidence_upload,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def requires_second_review(self, risk_score: float) -> bool:
        return risk_score >= self.second_review_threshold

    def to_dict(self) -> dict[str, Any]:
        return {
            "settings_id":             self.settings_id,
            "tenant_id":               self.tenant_id,
            "org_id":                  self.org_id,
            "version":                 self.version,
            "min_reviewers":           self.min_reviewers,
            "auto_escalate_hours":     self.auto_escalate_hours,
            "second_review_threshold": self.second_review_threshold,
            "mandatory_fields":        self.mandatory_fields,
            "reporting_cadence_days":  self.reporting_cadence_days,
            "allow_self_close":        self.allow_self_close,
            "require_evidence_upload": self.require_evidence_upload,
            "superseded":              self.superseded,
            "content_hash":            self.content_hash,
            "created_at":              self.created_at.isoformat(),
        }


class GovernanceSettingsRegistry:
    """
    Manages versioned governance settings for all orgs within all tenants.

    On each update, the previous setting entry is marked superseded and a
    new entry (version + 1) is created. History is never deleted.
    """

    def __init__(self) -> None:
        # settings_id → OrgGovernanceSettings
        self._settings: dict[str, OrgGovernanceSettings] = {}
        # (tenant_id, org_id) → settings_id  (active entry)
        self._active: dict[tuple[str, str], str] = {}

    def set(
        self,
        tenant_id:              str,
        org_id:                 str,
        created_by:             str,
        min_reviewers:          int   = 1,
        auto_escalate_hours:    int   = 72,
        second_review_threshold: float = 0.80,
        mandatory_fields:       Optional[list[str]] = None,
        reporting_cadence_days: int   = 30,
        allow_self_close:       bool  = False,
        require_evidence_upload: bool = True,
        notes:                  str   = "",
    ) -> OrgGovernanceSettings:
        key     = (tenant_id, org_id)
        version = 1
        current = self._current(tenant_id, org_id)
        if current:
            if (
                current.min_reviewers           == min_reviewers
                and current.auto_escalate_hours == auto_escalate_hours
                and current.second_review_threshold == second_review_threshold
                and current.mandatory_fields    == (mandatory_fields or [])
                and current.reporting_cadence_days == reporting_cadence_days
                and current.allow_self_close    == allow_self_close
                and current.require_evidence_upload == require_evidence_upload
            ):
                return current   # no change
            version = current.version + 1
            current.superseded = True

        new_settings = OrgGovernanceSettings(
            settings_id             = str(uuid.uuid4()),
            tenant_id               = tenant_id,
            org_id                  = org_id,
            version                 = version,
            min_reviewers           = min_reviewers,
            auto_escalate_hours     = auto_escalate_hours,
            second_review_threshold = second_review_threshold,
            mandatory_fields        = mandatory_fields or [],
            reporting_cadence_days  = reporting_cadence_days,
            allow_self_close        = allow_self_close,
            require_evidence_upload = require_evidence_upload,
            created_by              = created_by,
            notes                   = notes,
        )
        self._settings[new_settings.settings_id] = new_settings
        self._active[key] = new_settings.settings_id
        log.info(
            "GovernanceSettingsRegistry: updated settings for org %s (v%d)",
            org_id[:8], version,
        )
        return new_settings

    def get(
        self,
        tenant_id: str,
        org_id:    str,
    ) -> Optional[OrgGovernanceSettings]:
        return self._current(tenant_id, org_id)

    def get_or_default(
        self,
        tenant_id: str,
        org_id:    str,
    ) -> OrgGovernanceSettings:
        """Return the current settings or platform defaults if none configured."""
        current = self._current(tenant_id, org_id)
        if current:
            return current
        # Return an ephemeral default (not stored)
        return OrgGovernanceSettings(
            settings_id = "platform_default",
            tenant_id   = tenant_id,
            org_id      = org_id,
            version     = 0,
            created_by  = "platform",
        )

    def history(
        self,
        tenant_id: str,
        org_id:    str,
    ) -> list[OrgGovernanceSettings]:
        return sorted(
            [
                s for s in self._settings.values()
                if s.tenant_id == tenant_id and s.org_id == org_id
            ],
            key=lambda s: s.version,
            reverse=True,
        )

    def rollback(
        self,
        tenant_id:  str,
        org_id:     str,
        to_version: int,
        rolled_back_by: str,
    ) -> OrgGovernanceSettings:
        """Re-apply a previous version's settings as a new version entry."""
        hist = self.history(tenant_id, org_id)
        target = next((s for s in hist if s.version == to_version), None)
        if target is None:
            raise GovernanceSettingsError(
                f"Version {to_version} not found for org {org_id[:8]}"
            )
        return self.set(
            tenant_id               = tenant_id,
            org_id                  = org_id,
            created_by              = rolled_back_by,
            min_reviewers           = target.min_reviewers,
            auto_escalate_hours     = target.auto_escalate_hours,
            second_review_threshold = target.second_review_threshold,
            mandatory_fields        = target.mandatory_fields,
            reporting_cadence_days  = target.reporting_cadence_days,
            allow_self_close        = target.allow_self_close,
            require_evidence_upload = target.require_evidence_upload,
            notes                   = f"Rollback to v{to_version}",
        )

    def list_orgs_with_settings(self, tenant_id: str) -> list[str]:
        return [
            org_id for (tid, org_id) in self._active
            if tid == tenant_id
        ]

    def _current(
        self,
        tenant_id: str,
        org_id:    str,
    ) -> Optional[OrgGovernanceSettings]:
        sid = self._active.get((tenant_id, org_id))
        if sid is None:
            return None
        return self._settings.get(sid)


# ── Exceptions ─────────────────────────────────────────────────────────────────

class GovernanceSettingsError(Exception):
    pass


# ── Singleton ──────────────────────────────────────────────────────────────────

_registry: Optional[GovernanceSettingsRegistry] = None


def get_governance_settings_registry() -> GovernanceSettingsRegistry:
    global _registry
    if _registry is None:
        _registry = GovernanceSettingsRegistry()
    return _registry
