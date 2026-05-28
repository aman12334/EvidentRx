"""
LangGraph workflow execution tracing.

Instruments LangGraph node execution with:
  - OpenTelemetry spans (node name, case_id, agent_type)
  - Prometheus histograms (node duration)
  - Structured log output (node entry/exit, confidence, tokens)
  - Error capture with stack traces

Usage (in workflow nodes):

    from observability.langgraph_tracer import trace_node

    @trace_node("risk_prioritization")
    async def risk_prioritization_node(state: WorkflowState) -> WorkflowState:
        ...
"""

from __future__ import annotations

import functools
import logging
import time
from typing import Any, Callable, TypeVar

from observability.tracing import start_span
from observability.metrics import metrics_registry

log     = logging.getLogger("evidentrx.workflow")
F       = TypeVar("F", bound=Callable[..., Any])


def trace_node(node_name: str) -> Callable[[F], F]:
    """
    Decorator that wraps a LangGraph node function with tracing.
    Works for both sync and async node functions.

    Usage:
        @trace_node("evidence_aggregation")
        async def evidence_aggregation_node(state):
            ...
    """
    def decorator(fn: F) -> F:
        if _is_async(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                state = args[0] if args else kwargs.get("state", {})
                case_id = _extract_case_id(state)
                start = time.perf_counter()

                with start_span(
                    f"langgraph.{node_name}",
                    {"case_id": case_id, "node": node_name},
                ) as span:
                    log.info(
                        "Node started: %s case=%s",
                        node_name, case_id,
                        extra={"case_id": case_id, "node": node_name},
                    )
                    try:
                        result = await fn(*args, **kwargs)
                        elapsed = time.perf_counter() - start
                        _record_success(node_name, case_id, result, elapsed, span)
                        return result
                    except Exception as exc:
                        elapsed = time.perf_counter() - start
                        _record_failure(node_name, case_id, exc, elapsed, span)
                        raise

            return async_wrapper  # type: ignore[return-value]
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args, **kwargs):
                state = args[0] if args else kwargs.get("state", {})
                case_id = _extract_case_id(state)
                start = time.perf_counter()

                with start_span(
                    f"langgraph.{node_name}",
                    {"case_id": case_id, "node": node_name},
                ) as span:
                    try:
                        result = fn(*args, **kwargs)
                        elapsed = time.perf_counter() - start
                        _record_success(node_name, case_id, result, elapsed, span)
                        return result
                    except Exception as exc:
                        elapsed = time.perf_counter() - start
                        _record_failure(node_name, case_id, exc, elapsed, span)
                        raise

            return sync_wrapper  # type: ignore[return-value]

    return decorator


def _record_success(
    node_name: str,
    case_id:   str,
    result:    Any,
    elapsed:   float,
    span:      Any,
) -> None:
    metrics_registry.workflow_node_duration_seconds.labels(
        node_name=node_name
    ).observe(elapsed)

    # Extract confidence if state carries it
    confidence = None
    if hasattr(result, "get"):
        confidence = result.get("confidence_score") or result.get("confidence")
    if confidence is not None:
        span.set_attribute("confidence", float(confidence))

    log.info(
        "Node completed: %s case=%s elapsed=%.3fs",
        node_name, case_id, elapsed,
        extra={"case_id": case_id, "node": node_name, "elapsed_s": round(elapsed, 3)},
    )


def _record_failure(
    node_name: str,
    case_id:   str,
    exc:       Exception,
    elapsed:   float,
    span:      Any,
) -> None:
    metrics_registry.workflow_node_duration_seconds.labels(
        node_name=node_name
    ).observe(elapsed)

    span.record_exception(exc)

    log.error(
        "Node failed: %s case=%s elapsed=%.3fs error=%s",
        node_name, case_id, elapsed, str(exc),
        exc_info=True,
        extra={"case_id": case_id, "node": node_name},
    )


def _extract_case_id(state: Any) -> str:
    if isinstance(state, dict):
        return str(state.get("case_id") or state.get("investigation_id") or "unknown")
    return str(getattr(state, "case_id", "unknown"))


def _is_async(fn: Callable) -> bool:
    import asyncio
    import inspect
    return asyncio.iscoroutinefunction(fn) or inspect.iscoroutinefunction(fn)
