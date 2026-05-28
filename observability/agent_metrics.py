"""
Agent-level metrics collection — latency, token usage, and failure tracking.

Wraps every agent run with standardized metric recording so the monitoring
dashboard always has complete visibility into:
  - Which agents are running (frequency per agent type)
  - How long each agent takes (p50/p95/p99 latency histograms)
  - How many tokens each agent consumes (input, output, cache_read)
  - Agent failure rates and error types
  - Cost estimation (based on model + token counts)

Consumed by:  Prometheus scrape → Grafana dashboard
Also logged:  structured log output for alerting rules
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing      import Generator, Optional

from observability.metrics import metrics_registry
from config.model_routing  import MODELS

log = logging.getLogger("evidentrx.agent_metrics")


@dataclass
class AgentRunMetrics:
    """Accumulated metrics for a single agent run."""
    agent_type:        str
    model_key:         str
    input_tokens:      int = 0
    output_tokens:     int = 0
    cache_read_tokens: int = 0
    latency_ms:        Optional[float] = None
    status:            str = "running"
    tenant_id:         str = "unknown"

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read_tokens

    @property
    def estimated_cost_usd(self) -> float:
        """Rough cost estimate based on model pricing."""
        spec = MODELS.get(self.model_key)
        if not spec:
            return 0.0
        return (self.total_tokens / 1000) * spec.cost_per_1k

    def record(self) -> None:
        """Push metrics to Prometheus and structured log."""
        # Prometheus counters
        metrics_registry.agent_runs_total.labels(
            agent_type=self.agent_type,
            status=self.status,
            tenant_id=self.tenant_id,
        ).inc()

        metrics_registry.agent_tokens_total.labels(
            agent_type=self.agent_type,
            token_type="input",
        ).inc(self.input_tokens)

        metrics_registry.agent_tokens_total.labels(
            agent_type=self.agent_type,
            token_type="output",
        ).inc(self.output_tokens)

        if self.cache_read_tokens:
            metrics_registry.agent_tokens_total.labels(
                agent_type=self.agent_type,
                token_type="cache_read",
            ).inc(self.cache_read_tokens)

        if self.latency_ms is not None:
            metrics_registry.agent_run_duration_seconds.labels(
                agent_type=self.agent_type,
            ).observe(self.latency_ms / 1000)

        # Structured log
        log.info(
            "Agent run: type=%s status=%s tokens=%d latency=%.0fms cost=~$%.4f",
            self.agent_type, self.status, self.total_tokens,
            self.latency_ms or 0, self.estimated_cost_usd,
            extra={
                "agent_type":        self.agent_type,
                "status":            self.status,
                "input_tokens":      self.input_tokens,
                "output_tokens":     self.output_tokens,
                "cache_read_tokens": self.cache_read_tokens,
                "latency_ms":        round(self.latency_ms or 0, 2),
                "estimated_cost":    round(self.estimated_cost_usd, 5),
                "tenant_id":         self.tenant_id,
            },
        )


@contextmanager
def track_agent_run(
    agent_type: str,
    model_key:  str = "claude-3-5-sonnet",
    tenant_id:  str = "unknown",
) -> Generator[AgentRunMetrics, None, None]:
    """
    Context manager that automatically records agent run metrics on exit.

    Usage:
        async with track_agent_run("evidence_analysis", "claude-3-5-sonnet") as m:
            result = await llm.invoke(...)
            m.input_tokens  = result.usage.input_tokens
            m.output_tokens = result.usage.output_tokens
    """
    run = AgentRunMetrics(
        agent_type=agent_type,
        model_key=model_key,
        tenant_id=tenant_id,
    )
    start = time.perf_counter()
    try:
        yield run
        run.status = "completed"
    except Exception:
        run.status = "failed"
        raise
    finally:
        run.latency_ms = (time.perf_counter() - start) * 1000
        run.record()
