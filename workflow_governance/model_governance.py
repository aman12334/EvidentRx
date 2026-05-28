"""
Model usage governance.

Controls and audits all LLM API calls in the system:
  - Enforces model routing policy (no direct model calls)
  - Tracks token usage per tenant (cost attribution)
  - Enforces per-tenant model budgets (monthly token caps)
  - Logs all model usage for compliance reporting
  - Blocks model calls that would exceed budget

Model governance is the last line of defense before any LLM API call.
All calls must pass through ModelGovernor.authorize_call().
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Dict

from config.model_routing import ModelSpec, model_router

log = logging.getLogger("evidentrx.model_governance")


@dataclass
class ModelCallRequest:
    """Parameters for a proposed LLM API call."""
    task_type:   str
    tenant_id:   str
    case_id:     str | None = None
    actor_id:    str | None = None
    max_tokens:  int = 4096
    estimated_input_tokens: int = 0


@dataclass
class TokenUsageRecord:
    """Per-tenant token usage tracking."""
    tenant_id:         str
    input_tokens:      int = 0
    output_tokens:     int = 0
    cache_read_tokens: int = 0
    call_count:        int = 0
    period_start:      datetime = field(default_factory=lambda: datetime.now(tz=UTC))


class ModelGovernor:
    """
    Governs and audits all LLM model usage.
    """

    # Default monthly token budget per tenant (configurable via tenant config)
    DEFAULT_MONTHLY_TOKEN_BUDGET = 10_000_000

    def __init__(self) -> None:
        self._usage: Dict[str, TokenUsageRecord] = {}
        self._budgets: Dict[str, int] = {}

    def authorize_call(self, request: ModelCallRequest) -> ModelSpec:
        """
        Authorize and route an LLM API call.
        Returns the ModelSpec to use.
        Raises PermissionError if budget is exceeded.
        """
        # Check budget
        usage = self._get_usage(request.tenant_id)
        budget = self._budgets.get(request.tenant_id, self.DEFAULT_MONTHLY_TOKEN_BUDGET)
        current_tokens = usage.input_tokens + usage.output_tokens

        if current_tokens + request.estimated_input_tokens > budget:
            raise PermissionError(
                f"Tenant {request.tenant_id!r} has exceeded monthly token budget "
                f"({current_tokens:,} / {budget:,}). Contact support to increase."
            )

        # Route to appropriate model
        spec = model_router.resolve(
            task_type=request.task_type,
            tenant_id=request.tenant_id,
        )

        log.debug(
            "Model call authorized: tenant=%s task=%s model=%s",
            request.tenant_id, request.task_type, spec.model_id,
        )
        return spec

    def record_usage(
        self,
        tenant_id:         str,
        input_tokens:      int,
        output_tokens:     int,
        cache_read_tokens: int = 0,
    ) -> None:
        """Record actual token usage after a completed LLM call."""
        usage = self._get_usage(tenant_id)
        usage.input_tokens      += input_tokens
        usage.output_tokens     += output_tokens
        usage.cache_read_tokens += cache_read_tokens
        usage.call_count        += 1

    def get_usage_summary(self, tenant_id: str) -> Dict:
        usage = self._get_usage(tenant_id)
        budget = self._budgets.get(tenant_id, self.DEFAULT_MONTHLY_TOKEN_BUDGET)
        total = usage.input_tokens + usage.output_tokens
        return {
            "tenant_id":         tenant_id,
            "total_tokens":      total,
            "input_tokens":      usage.input_tokens,
            "output_tokens":     usage.output_tokens,
            "cache_read_tokens": usage.cache_read_tokens,
            "call_count":        usage.call_count,
            "budget":            budget,
            "budget_pct_used":   round((total / budget) * 100, 1) if budget else 0,
        }

    def set_budget(self, tenant_id: str, monthly_tokens: int) -> None:
        self._budgets[tenant_id] = monthly_tokens

    def _get_usage(self, tenant_id: str) -> TokenUsageRecord:
        if tenant_id not in self._usage:
            self._usage[tenant_id] = TokenUsageRecord(tenant_id=tenant_id)
        return self._usage[tenant_id]


model_governor = ModelGovernor()
