"""
Model routing configuration — governs which LLM is used for each workflow node.

Design:
  - Each agent workflow node declares a "task type" (summarize, risk, escalate…)
  - The router selects a provider+model based on: task type, tenant tier, cost cap
  - Tenant-level overrides allow enterprise customers to specify preferred models
  - Fallback chain ensures no single model failure breaks the workflow

This is governance-layer routing: all AI calls must pass through this router.
No direct model calls are permitted from workflow nodes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing      import Dict, List, Optional


@dataclass(frozen=True)
class ModelSpec:
    """Specification for a single LLM model."""
    provider:      str     # "anthropic" | "openai"
    model_id:      str
    max_tokens:    int     = 4096
    temperature:   float   = 0.0    # deterministic by default
    cost_per_1k:   float   = 0.0    # USD (for cost tracking)
    supports_cache: bool   = False  # prompt caching support


# ── Available models ──────────────────────────────────────────────────────────

MODELS: Dict[str, ModelSpec] = {
    # Anthropic
    "claude-3-5-sonnet": ModelSpec(
        provider="anthropic",
        model_id="claude-3-5-sonnet-20241022",
        max_tokens=8192,
        cost_per_1k=0.003,
        supports_cache=True,
    ),
    "claude-3-haiku": ModelSpec(
        provider="anthropic",
        model_id="claude-3-haiku-20240307",
        max_tokens=4096,
        cost_per_1k=0.00025,
        supports_cache=True,
    ),
    "claude-3-opus": ModelSpec(
        provider="anthropic",
        model_id="claude-3-opus-20240229",
        max_tokens=4096,
        cost_per_1k=0.015,
        supports_cache=True,
    ),
    # OpenAI
    "gpt-4o": ModelSpec(
        provider="openai",
        model_id="gpt-4o-2024-08-06",
        max_tokens=4096,
        cost_per_1k=0.005,
    ),
    "gpt-4o-mini": ModelSpec(
        provider="openai",
        model_id="gpt-4o-mini-2024-07-18",
        max_tokens=4096,
        cost_per_1k=0.00015,
    ),
}

# ── Task type → model routing ──────────────────────────────────────────────────

# Task types map to (primary_model, fallback_models)
_TASK_ROUTING: Dict[str, List[str]] = {
    "summarize":         ["claude-3-5-sonnet", "gpt-4o"],
    "risk_assess":       ["claude-3-5-sonnet", "gpt-4o"],
    "escalation_decide": ["claude-3-5-sonnet", "claude-3-opus"],
    "timeline":          ["claude-3-haiku",    "gpt-4o-mini"],
    "recommend":         ["claude-3-5-sonnet", "gpt-4o"],
    "explain":           ["claude-3-haiku",    "gpt-4o-mini"],
    "related_cases":     ["claude-3-haiku",    "gpt-4o-mini"],
    "narrative":         ["claude-3-5-sonnet", "gpt-4o"],
    "pattern_analysis":  ["claude-3-5-sonnet", "gpt-4o"],
    "copilot":           ["claude-3-5-sonnet", "gpt-4o"],
}


@dataclass
class ModelRoutingConfig:
    """
    Tenant-aware model routing.
    Selects the appropriate model spec for a given task type and tenant.
    """

    _tenant_overrides: Dict[str, Dict[str, str]] = field(default_factory=dict)

    def resolve(
        self,
        task_type:  str,
        tenant_id:  Optional[str] = None,
        index:      int = 0,        # 0=primary, 1=first fallback, etc.
    ) -> ModelSpec:
        """
        Resolve the ModelSpec for a task type, applying tenant overrides.
        Returns fallback if index > 0 and primary fails.
        """
        # Tenant-level override
        if tenant_id and tenant_id in self._tenant_overrides:
            overrides = self._tenant_overrides[tenant_id]
            if task_type in overrides:
                model_key = overrides[task_type]
                if model_key in MODELS:
                    return MODELS[model_key]

        # Default routing
        candidates = _TASK_ROUTING.get(task_type, ["claude-3-5-sonnet"])
        if index >= len(candidates):
            index = len(candidates) - 1
        return MODELS.get(candidates[index], MODELS["claude-3-5-sonnet"])

    def set_tenant_override(
        self, tenant_id: str, task_type: str, model_key: str
    ) -> None:
        """Set a tenant-level model override."""
        if model_key not in MODELS:
            raise ValueError(f"Unknown model key: {model_key!r}")
        self._tenant_overrides.setdefault(tenant_id, {})[task_type] = model_key

    def get_fallback_chain(self, task_type: str) -> List[ModelSpec]:
        """Return the full fallback chain for a task type."""
        candidates = _TASK_ROUTING.get(task_type, ["claude-3-5-sonnet"])
        return [MODELS[k] for k in candidates if k in MODELS]


model_router = ModelRoutingConfig()
