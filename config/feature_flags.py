"""
Runtime feature flags — toggle capabilities without code deployment.

Flags are read from environment/settings at startup and can be updated at
runtime via the admin API (PUT /api/v1/admin/flags/{flag}). All flag changes
are written to the audit log with the acting admin's user_id and tenant_id.

Design principles:
  - No flag is "default on" in production without explicit enablement
  - Flags can be tenant-scoped (override per tenant)
  - AI/copilot features are guarded; deterministic compliance never flagged off
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict

from config.settings import settings


@dataclass
class FeatureFlags:
    """
    Global feature flag registry.

    Thread-safe: flag updates use a lock so concurrent requests see consistent
    state. Each update emits an audit event (wired up in governance layer).
    """

    # ── Intelligence features ─────────────────────────────────────────────
    copilot_enabled:            bool = field(default_factory=lambda: settings.enable_copilot)
    graph_intelligence_enabled: bool = field(default_factory=lambda: settings.enable_graph_intelligence)
    predictive_risk_enabled:    bool = field(default_factory=lambda: settings.enable_predictive_risk)

    # ── Async / task features ──────────────────────────────────────────────
    async_tasks_enabled:        bool = field(default_factory=lambda: settings.enable_async_tasks)
    background_monitoring:      bool = True
    scheduled_risk_scoring:     bool = True

    # ── Governance features ────────────────────────────────────────────────
    phi_masking_enabled:        bool = field(default_factory=lambda: settings.phi_masking_enabled)
    audit_signing_enabled:      bool = True
    immutable_audit_log:        bool = True

    # ── API features ──────────────────────────────────────────────────────
    rate_limiting_enabled:      bool = True
    tenant_isolation_strict:    bool = True    # False = warn-only (staging)
    api_versioning_strict:      bool = False   # deprecated endpoint removal

    # ── Experimental ──────────────────────────────────────────────────────
    multi_model_routing:        bool = False
    workflow_approval_gates:    bool = False

    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _tenant_overrides: Dict[str, Dict[str, bool]] = field(
        default_factory=dict, init=False, repr=False
    )

    def is_enabled(self, flag: str, tenant_id: str | None = None) -> bool:
        """
        Return the effective value of a flag, with tenant-level override support.
        Tenant overrides take precedence over global settings.
        """
        with self._lock:
            if tenant_id and tenant_id in self._tenant_overrides:
                tenant_flags = self._tenant_overrides[tenant_id]
                if flag in tenant_flags:
                    return tenant_flags[flag]
            return getattr(self, flag, False)

    def set_flag(self, flag: str, value: bool, tenant_id: str | None = None) -> None:
        """
        Update a flag value. If tenant_id is provided, sets a tenant-level override.
        Raises AttributeError if the flag does not exist.
        """
        with self._lock:
            if not hasattr(self, flag):
                raise AttributeError(f"Unknown feature flag: {flag!r}")
            if tenant_id:
                self._tenant_overrides.setdefault(tenant_id, {})[flag] = value
            else:
                setattr(self, flag, value)

    def snapshot(self, tenant_id: str | None = None) -> Dict[str, bool]:
        """Return all flags as a dict, with tenant overrides applied."""
        with self._lock:
            base = {
                k: v for k, v in self.__dict__.items()
                if not k.startswith("_") and isinstance(v, bool)
            }
            if tenant_id and tenant_id in self._tenant_overrides:
                base.update(self._tenant_overrides[tenant_id])
            return base


feature_flags = FeatureFlags()
