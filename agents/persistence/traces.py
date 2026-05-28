"""
TraceWriter — persists every agent execution to:
  audit.reasoning_traces  — the immutable LLM reasoning log
  audit.agent_runs        — the execution status ledger

This is the primary auditability mechanism for all AI-generated reasoning.
The rules engine never writes here. Only agent invocations do.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from agents.llm.base import LLMResponse

logger = logging.getLogger(__name__)

UTC = UTC

# Maps internal agent_type identifiers to the values allowed by ck_trace_agent_type
_AGENT_TYPE_MAP: dict[str, str] = {
    "classification":       "classifier",
    "evidence_analysis":    "extractor",
    "risk_prioritization":  "prioritizer",
    "deep_analysis":        "investigator",
    "narrative_generation": "reporter",
    "case_summary":         "summarizer",
    "orchestration":        "orchestrator",
    "orchestrator":         "orchestrator",
    "classification_agent": "classifier",
    "pattern_analysis":     "investigator",
    "escalation_decision":  "orchestrator",
}


def _map_agent_type(raw: str | None) -> str | None:
    if raw is None:
        return None
    return _AGENT_TYPE_MAP.get(raw, raw)


class TraceWriter:
    def write_reasoning_trace(
        self,
        session: Session,
        *,
        session_id: UUID,
        case_id: UUID,
        agent_id: str,
        agent_type: str,
        workflow_node: str,
        workflow_step: int,
        parent_trace_id: UUID | None,
        input_context: dict,
        response: LLMResponse,
        confidence_score: float | None = None,
        human_review_required: bool = False,
        citations: list | None = None,
    ) -> UUID:
        """
        Writes an immutable reasoning trace row.
        Returns the new trace_id.
        """
        trace_id = uuid4()
        now = datetime.now(UTC)
        agent_type = _map_agent_type(agent_type)

        structured = response.structured or {}

        session.execute(text("""
            INSERT INTO audit.reasoning_traces (
                trace_id, session_id, investigation_case_id,
                agent_id, agent_type, workflow_node, workflow_step_sequence,
                model_id,
                input_context, reasoning_output, structured_output, citations,
                confidence_score,
                human_review_required,
                input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                latency_ms,
                created_at
            ) VALUES (
                :trace_id, :session_id, :case_id,
                :agent_id, :agent_type, :workflow_node, :step,
                :model_id,
                CAST(:input_ctx AS jsonb), :reasoning_output, CAST(:structured AS jsonb), CAST(:citations AS jsonb),
                :confidence,
                :human_review,
                :in_tokens, :out_tokens, :cache_read, :cache_write,
                :latency_ms,
                :created_at
            )
        """), {
            "trace_id":         str(trace_id),
            "session_id":       str(session_id),
            "case_id":          str(case_id),
            "agent_id":         agent_id,
            "agent_type":       agent_type,
            "workflow_node":    workflow_node,
            "step":             workflow_step,
            "model_id":         response.model_id,
            "input_ctx":        json.dumps(input_context),
            "reasoning_output": response.content,
            "structured":       json.dumps(structured),
            "citations":        json.dumps(citations or []),
            "confidence":       confidence_score,
            "human_review":     human_review_required,
            "in_tokens":        response.input_tokens,
            "out_tokens":       response.output_tokens,
            "cache_read":       response.cache_read_tokens,
            "cache_write":      response.cache_write_tokens,
            "latency_ms":       response.latency_ms,
            "created_at":       now,
        })

        return trace_id

    def create_agent_run(
        self,
        session: Session,
        *,
        case_id: UUID,
        agent_type: str,
        agent_name: str,
        input_payload: dict,
        workflow_run_id: str,
    ) -> UUID:
        """Creates an agent_run record in 'running' status. Returns agent_run_id."""
        agent_run_id = uuid4()
        now = datetime.now(UTC)
        agent_type = _map_agent_type(agent_type) or agent_type

        session.execute(text("""
            INSERT INTO audit.agent_runs (
                agent_run_id, case_id, agent_type, agent_name,
                status, input_payload, started_at, workflow_run_id, created_at
            ) VALUES (
                :run_id, :case_id, :agent_type, :agent_name,
                'running', CAST(:input AS jsonb), :started_at, :workflow_run_id, :created_at
            )
        """), {
            "run_id":          str(agent_run_id),
            "case_id":         str(case_id),
            "agent_type":      agent_type,
            "agent_name":      agent_name,
            "input":           json.dumps(input_payload),
            "started_at":      now,
            "workflow_run_id": workflow_run_id,
            "created_at":      now,
        })

        return agent_run_id

    def complete_agent_run(
        self,
        session: Session,
        agent_run_id: UUID,
        output_payload: dict,
        model_id: str,
        token_usage: dict,
    ) -> None:
        session.execute(text("""
            UPDATE audit.agent_runs
            SET status = 'completed',
                output_payload = CAST(:output AS jsonb),
                completed_at = NOW(),
                model_id = :model_id,
                token_usage = CAST(:usage AS jsonb)
            WHERE agent_run_id = :run_id
        """), {
            "run_id":   str(agent_run_id),
            "output":   json.dumps(output_payload),
            "model_id": model_id,
            "usage":    json.dumps(token_usage),
        })

    def fail_agent_run(
        self,
        session: Session,
        agent_run_id: UUID,
        error_message: str,
    ) -> None:
        session.execute(text("""
            UPDATE audit.agent_runs
            SET status = 'failed',
                completed_at = NOW(),
                error_message = :error
            WHERE agent_run_id = :run_id
        """), {"run_id": str(agent_run_id), "error": error_message})
