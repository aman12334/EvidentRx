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
from typing import Dict, List


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
    # ── Groq (free tier — hackathon) ─────────────────────────────────────────
    "groq-compound": ModelSpec(
        provider="groq",
        model_id="groq/compound",
        max_tokens=4096,
        cost_per_1k=0.0,          # free
    ),
    "groq-gpt-oss-120b": ModelSpec(
        provider="groq",
        model_id="openai/gpt-oss-120b",
        max_tokens=8192,
        cost_per_1k=0.0,          # free
    ),
    "groq-gpt-oss-20b": ModelSpec(
        provider="groq",
        model_id="openai/gpt-oss-20b",
        max_tokens=8192,
        cost_per_1k=0.0,          # free
    ),
    "groq-qwen3-32b": ModelSpec(
        provider="groq",
        model_id="qwen/qwen3-32b",
        max_tokens=4096,
        cost_per_1k=0.0,          # free
    ),
    "groq-llama-70b": ModelSpec(
        provider="groq",
        model_id="llama-3.3-70b-versatile",
        max_tokens=8192,
        cost_per_1k=0.0,          # free
    ),
    "groq-llama-8b": ModelSpec(
        provider="groq",
        model_id="llama-3.1-8b-instant",
        max_tokens=1024,
        cost_per_1k=0.0,          # free
    ),
    # ── Anthropic (production upgrade path) ───────────────────────────────────
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
    # ── OpenAI (production upgrade path) ──────────────────────────────────────
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
    # ── Groq free tier (primary) ──────────────────────────────────────────────
    "orchestration":      ["groq-compound",      "groq-llama-70b"],
    "evidence_analysis":  ["groq-gpt-oss-120b",  "groq-llama-70b"],
    "risk_assess":        ["groq-qwen3-32b",      "groq-gpt-oss-20b"],
    "pattern_analysis":   ["groq-gpt-oss-20b",   "groq-llama-70b"],
    "narrative":          ["groq-llama-70b",      "groq-gpt-oss-20b"],
    "classification":     ["groq-llama-8b",       "groq-llama-70b"],
    "escalation_decide":  ["groq-qwen3-32b",      "groq-llama-70b"],
    "summarize":          ["groq-llama-70b",      "groq-gpt-oss-20b"],
    "timeline":           ["groq-llama-8b",       "groq-llama-70b"],
    "recommend":          ["groq-llama-70b",      "groq-gpt-oss-20b"],
    "explain":            ["groq-llama-8b",       "groq-llama-70b"],
    "related_cases":      ["groq-gpt-oss-20b",   "groq-llama-70b"],
    "copilot":            ["groq-llama-70b",      "groq-gpt-oss-120b"],
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
        tenant_id:  str | None = None,
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
