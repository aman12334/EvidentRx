"""Typed API contracts for reasoning trace and agent run endpoints."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class ReasoningTraceSchema(BaseModel):
    trace_id:           UUID
    case_id:            UUID
    agent_id:           str
    agent_type:         str
    workflow_node:      str
    workflow_step:      int
    confidence_score:   Optional[float] = None
    input_context:      dict[str, Any] = Field(default_factory=dict)
    output_summary:     Optional[str] = None
    created_at:         Optional[datetime] = None

    model_config = {"from_attributes": True}


class AgentRunSchema(BaseModel):
    run_id:             UUID
    case_id:            UUID
    agent_type:         str
    status:             str
    input_tokens:       int = 0
    output_tokens:      int = 0
    cache_read_tokens:  int = 0
    latency_ms:         Optional[int] = None
    started_at:         Optional[datetime] = None
    completed_at:       Optional[datetime] = None
    output:             dict[str, Any] = Field(default_factory=dict)

    model_config = {"from_attributes": True}


class ConfidencePropagation(BaseModel):
    node:           str
    label:          str
    confidence:     Optional[float] = None
    delta:          Optional[float] = None


class WorkflowTrace(BaseModel):
    case_id:            UUID
    total_traces:       int
    agent_runs:         list[AgentRunSchema] = Field(default_factory=list)
    reasoning_traces:   list[ReasoningTraceSchema] = Field(default_factory=list)
    confidence_chain:   list[ConfidencePropagation] = Field(default_factory=list)
    total_input_tokens:  int = 0
    total_output_tokens: int = 0
    escalation_recommended: Optional[bool] = None
    executive_summary:  Optional[str] = None
