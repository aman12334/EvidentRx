"""
Tenant-specific threshold configuration.

Manages per-tenant (and per-org) overrides to the platform's default
risk scoring thresholds. Overrides are versioned and auditable — every
change creates a new record and the prior value is retained for rollback.

Default thresholds come from the platform rule packs. A tenant override
raises or lowers the effective threshold for their environment without
modifying the platform default.

Override rules
──────────────
  - Thresholds can be relaxed (raised) or tightened (lowered)
  - A TIGHTEN direction can never go below the platform's minimum floor
  - A RELAX direction cannot exceed the platform's maximum ceiling
  - These floors/ceilings are set at the rule_pack level, not here
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

log = logging.getLogger("evidentrx.saas.config.thresholds")


class ThresholdDirection(str, Enum):
    TIGHTEN = "tighten"   # more sensitive (lower threshold → more alerts)
    RELAX   = "relax"     # less sensitive (higher threshold → fewer alerts)


@dataclass
class ThresholdOverride:
    """
    A single tenant-specific threshold override for a named rule.

    The override records the platform default at the time of creation
    so that drift from the default can be tracked over time.
    """
    override_id:      str
    tenant_id:        str
    rule_code:        str
    metric:           str           # "confidence_threshold" | "amount_threshold" | etc.
    platform_default: float
    tenant_value:     float
    direction:        ThresholdDirection
    org_id:           str | None     = None
    changed_by:       str               = "system"
    changed_at:       datetime          = field(default_factory=lambda: datetime.now(tz=UTC))
    change_reason:    str               = ""
    superseded:       bool              = False
    version:          int               = 1

    @property
    def delta(self) -> float:
        return round(self.tenant_value - self.platform_default, 6)

    def to_dict(self) -> dict[str, Any]:
        return {
            "override_id":      self.override_id,
            "tenant_id":        self.tenant_id,
            "rule_code":        self.rule_code,
            "metric":           self.metric,
            "platform_default": self.platform_default,
            "tenant_value":     self.tenant_value,
            "direction":        self.direction.value,
            "delta":            self.delta,
            "org_id":           self.org_id,
            "changed_by":       self.changed_by,
            "changed_at":       self.changed_at.isoformat(),
            "version":          self.version,
            "superseded":       self.superseded,
        }


class TenantThresholdConfig:
    """
    Manages threshold overrides for a tenant.

    Stores the full history of overrides per (tenant, org, rule, metric).
    The effective threshold for any key is the most recent non-superseded
    override, falling back to the platform default.
    """

    def __init__(self, db_writer: Callable | None = None) -> None:
        # (tenant_id, org_id, rule_code, metric) → [ThresholdOverride]
        self._overrides: dict[tuple, list[ThresholdOverride]] = {}
        self._db_writer  = db_writer

    # ── Write ──────────────────────────────────────────────────────────────────

    async def set_override(
        self,
        tenant_id:        str,
        rule_code:        str,
        metric:           str,
        tenant_value:     float,
        platform_default: float,
        changed_by:       str,
        change_reason:    str        = "",
        org_id:           str | None = None,
    ) -> ThresholdOverride:
        direction = (
            ThresholdDirection.TIGHTEN
            if tenant_value < platform_default
            else ThresholdDirection.RELAX
        )
        ck      = (tenant_id, org_id, rule_code, metric)
        history = self._overrides.get(ck, [])
        version = (history[-1].version + 1) if history else 1

        # Supersede current active override
        for ov in history:
            if not ov.superseded:
                ov.superseded = True

        override = ThresholdOverride(
            override_id      = str(uuid.uuid4()),
            tenant_id        = tenant_id,
            rule_code        = rule_code,
            metric           = metric,
            platform_default = platform_default,
            tenant_value     = tenant_value,
            direction        = direction,
            org_id           = org_id,
            changed_by       = changed_by,
            change_reason    = change_reason,
            version          = version,
        )
        self._overrides.setdefault(ck, []).append(override)
        await self._persist("create_override", override)
        log.info(
            "TenantThresholdConfig: set %s.%s = %.4f (was %.4f) for tenant %s",
            rule_code, metric, tenant_value, platform_default, tenant_id[:8],
        )
        return override

    async def clear_override(
        self,
        tenant_id:  str,
        rule_code:  str,
        metric:     str,
        cleared_by: str,
        org_id:     str | None = None,
    ) -> bool:
        ck = (tenant_id, org_id, rule_code, metric)
        history = self._overrides.get(ck, [])
        cleared = False
        for ov in history:
            if not ov.superseded:
                ov.superseded = True
                ov.change_reason = f"Cleared by {cleared_by}"
                cleared = True
        if cleared:
            await self._persist("clear_override", {"key": str(ck), "cleared_by": cleared_by})
        return cleared

    # ── Read ───────────────────────────────────────────────────────────────────

    def get_effective(
        self,
        tenant_id:        str,
        rule_code:        str,
        metric:           str,
        platform_default: float,
        org_id:           str | None = None,
    ) -> float:
        """
        Return the effective threshold value.

        Priority: org-specific override → tenant-wide override → platform default.
        """
        # Org-specific
        if org_id:
            ov = self._active_override(tenant_id, org_id, rule_code, metric)
            if ov:
                return ov.tenant_value

        # Tenant-wide
        ov = self._active_override(tenant_id, None, rule_code, metric)
        if ov:
            return ov.tenant_value

        return platform_default

    def get_all_overrides(
        self,
        tenant_id: str,
        org_id:    str | None = None,
    ) -> list[ThresholdOverride]:
        """Return all active overrides for a tenant scope."""
        result: list[ThresholdOverride] = []
        for (tid, oid, _, _), history in self._overrides.items():
            if tid != tenant_id:
                continue
            if org_id is not None and oid != org_id:
                continue
            active = next((ov for ov in reversed(history) if not ov.superseded), None)
            if active:
                result.append(active)
        return sorted(result, key=lambda ov: (ov.rule_code, ov.metric))

    def history(
        self,
        tenant_id: str,
        rule_code: str,
        metric:    str,
        org_id:    str | None = None,
    ) -> list[ThresholdOverride]:
        ck = (tenant_id, org_id, rule_code, metric)
        return list(self._overrides.get(ck, []))

    async def rollback(
        self,
        tenant_id:      str,
        rule_code:      str,
        metric:         str,
        target_version: int,
        rolled_by:      str,
        org_id:         str | None = None,
    ) -> ThresholdOverride:
        ck      = (tenant_id, org_id, rule_code, metric)
        history = self._overrides.get(ck, [])
        target  = next((ov for ov in history if ov.version == target_version), None)
        if target is None:
            raise ThresholdNotFoundError(
                f"Version {target_version} of {rule_code}.{metric} not found"
            )
        return await self.set_override(
            tenant_id        = tenant_id,
            rule_code        = rule_code,
            metric           = metric,
            tenant_value     = target.tenant_value,
            platform_default = target.platform_default,
            changed_by       = rolled_by,
            change_reason    = f"Rollback to v{target_version}",
            org_id           = org_id,
        )

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _active_override(
        self,
        tenant_id: str,
        org_id:    str | None,
        rule_code: str,
        metric:    str,
    ) -> ThresholdOverride | None:
        ck = (tenant_id, org_id, rule_code, metric)
        history = self._overrides.get(ck, [])
        return next((ov for ov in reversed(history) if not ov.superseded), None)

    async def _persist(self, op: str, obj: Any) -> None:
        if self._db_writer:
            try:
                await self._db_writer(op, obj)
            except Exception as exc:
                log.error("TenantThresholdConfig: persist failed: %s", exc)


# ── Exceptions ─────────────────────────────────────────────────────────────────

class ThresholdNotFoundError(Exception):
    pass


# ── Singleton ──────────────────────────────────────────────────────────────────

_config: TenantThresholdConfig | None = None


def get_threshold_config(db_writer: Callable | None = None) -> TenantThresholdConfig:
    global _config
    if _config is None:
        _config = TenantThresholdConfig(db_writer=db_writer)
    return _config
