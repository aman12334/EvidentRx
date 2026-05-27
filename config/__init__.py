"""
config — Centralized Enterprise Configuration System

Provides:
  - Environment-driven settings (pydantic-settings)
  - Tenant-level configuration
  - Runtime feature flags
  - Model routing policies
  - Workflow execution policies
  - Rule pack configuration

All configuration is read-only after startup. Mutations require restart or
feature flag updates through the admin API (which go through this module).
"""

from config.settings        import settings, Settings
from config.feature_flags   import feature_flags, FeatureFlags
from config.model_routing   import model_router, ModelRoutingConfig
from config.workflow_policy import workflow_policy, WorkflowPolicy

__all__ = [
    "settings",
    "Settings",
    "feature_flags",
    "FeatureFlags",
    "model_router",
    "ModelRoutingConfig",
    "workflow_policy",
    "WorkflowPolicy",
]
