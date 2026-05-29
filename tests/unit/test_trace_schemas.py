"""
Unit tests for reasoning trace and agent run Pydantic schemas.

All tests are pure — no database or HTTP fixtures required.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from pydantic import ValidationError

from api.schemas.trace import (
    AgentRunSchema,
    ConfidencePropagation,
    ReasoningTraceSchema,
    WorkflowTrace,
)

_CASE_ID  = uuid.uuid4()
_TRACE_ID = uuid.uuid4()
_RUN_ID   = uuid.uuid4()
_NOW      = datetime.utcnow()


# ── ReasoningTraceSchema ──────────────────────────────────────────────────────

class TestReasoningTraceSchema:
    def _make(self, **kw) -> ReasoningTraceSchema:
        defaults: dict = {
            "trace_id":      _TRACE_ID,
            "case_id":       _CASE_ID,
            "agent_id":      "agent-compliance-v1",
            "agent_type":    "compliance_reviewer",
            "workflow_node": "evidence_evaluation",
            "workflow_step": 2,
        }
        defaults.update(kw)
        return ReasoningTraceSchema(**defaults)

    def test_basic_construction(self):
        t = self._make()
        assert t.agent_type == "compliance_reviewer"
        assert t.workflow_step == 2

    def test_confidence_score_optional(self):
        t = self._make(confidence_score=0.87)
        assert t.confidence_score == pytest.approx(0.87)

    def test_confidence_none_by_default(self):
        t = self._make()
        assert t.confidence_score is None

    def test_output_summary_optional(self):
        t = self._make(output_summary="Duplicate discount confirmed on 12 claims.")
        assert "Duplicate" in t.output_summary

    def test_created_at_optional(self):
        t = self._make(created_at=_NOW)
        assert isinstance(t.created_at, datetime)

    def test_input_context_defaults_empty_dict(self):
        t = self._make()
        assert t.input_context == {}

    def test_input_context_populated(self):
        ctx = {"ndc": "00069420030", "claim_count": 5}
        t = self._make(input_context=ctx)
        assert t.input_context["ndc"] == "00069420030"

    def test_from_attributes_enabled(self):
        assert ReasoningTraceSchema.model_config.get("from_attributes") is True


# ── AgentRunSchema ────────────────────────────────────────────────────────────

class TestAgentRunSchema:
    def _make(self, **kw) -> AgentRunSchema:
        defaults: dict = {
            "run_id":     _RUN_ID,
            "case_id":    _CASE_ID,
            "agent_type": "risk_scorer",
            "status":     "completed",
        }
        defaults.update(kw)
        return AgentRunSchema(**defaults)

    def test_basic_construction(self):
        r = self._make()
        assert r.agent_type == "risk_scorer"
        assert r.status == "completed"

    def test_token_counts_default_zero(self):
        r = self._make()
        assert r.input_tokens == 0
        assert r.output_tokens == 0
        assert r.cache_read_tokens == 0

    def test_token_counts_populated(self):
        r = self._make(input_tokens=2048, output_tokens=512, cache_read_tokens=1024)
        assert r.input_tokens == 2048
        assert r.cache_read_tokens == 1024

    def test_latency_ms_optional(self):
        r = self._make(latency_ms=4230)
        assert r.latency_ms == 4230

    def test_latency_ms_none_by_default(self):
        r = self._make()
        assert r.latency_ms is None

    def test_started_at_optional(self):
        r = self._make(started_at=_NOW)
        assert isinstance(r.started_at, datetime)

    def test_completed_at_optional(self):
        r = self._make(completed_at=_NOW)
        assert isinstance(r.completed_at, datetime)

    def test_output_defaults_empty_dict(self):
        r = self._make()
        assert r.output == {}

    def test_output_populated(self):
        r = self._make(output={"escalate": True, "risk_score": 0.92})
        assert r.output["escalate"] is True


# ── ConfidencePropagation ─────────────────────────────────────────────────────

class TestConfidencePropagation:
    def test_basic(self):
        c = ConfidencePropagation(
            node="evidence_evaluation",
            label="Evidence Evaluation",
            confidence=0.85,
            delta=0.05,
        )
        assert c.confidence == pytest.approx(0.85)
        assert c.delta == pytest.approx(0.05)

    def test_confidence_optional(self):
        c = ConfidencePropagation(node="intake", label="Intake")
        assert c.confidence is None

    def test_delta_optional(self):
        c = ConfidencePropagation(node="intake", label="Intake", confidence=0.7)
        assert c.delta is None

    def test_negative_delta(self):
        c = ConfidencePropagation(
            node="pattern_match", label="Pattern Match",
            confidence=0.55, delta=-0.15,
        )
        assert c.delta == pytest.approx(-0.15)


# ── WorkflowTrace ─────────────────────────────────────────────────────────────

class TestWorkflowTrace:
    def _make_trace(self) -> ReasoningTraceSchema:
        return ReasoningTraceSchema(
            trace_id=uuid.uuid4(), case_id=_CASE_ID,
            agent_id="agent-v1", agent_type="compliance_reviewer",
            workflow_node="evidence_evaluation", workflow_step=1,
            confidence_score=0.82,
        )

    def _make_run(self) -> AgentRunSchema:
        return AgentRunSchema(
            run_id=uuid.uuid4(), case_id=_CASE_ID,
            agent_type="risk_scorer", status="completed",
            input_tokens=1024, output_tokens=256,
        )

    def test_minimal_workflow_trace(self):
        wt = WorkflowTrace(case_id=_CASE_ID, total_traces=0)
        assert wt.total_traces == 0
        assert wt.agent_runs == []
        assert wt.reasoning_traces == []
        assert wt.confidence_chain == []

    def test_token_totals_default_zero(self):
        wt = WorkflowTrace(case_id=_CASE_ID, total_traces=0)
        assert wt.total_input_tokens == 0
        assert wt.total_output_tokens == 0

    def test_escalation_recommended_optional(self):
        wt = WorkflowTrace(case_id=_CASE_ID, total_traces=2,
                           escalation_recommended=True)
        assert wt.escalation_recommended is True

    def test_executive_summary_optional(self):
        wt = WorkflowTrace(case_id=_CASE_ID, total_traces=2,
                           executive_summary="High risk: duplicate discount pattern.")
        assert "duplicate" in wt.executive_summary

    def test_full_workflow_trace(self):
        traces = [self._make_trace(), self._make_trace()]
        runs   = [self._make_run()]
        chain  = [
            ConfidencePropagation(node="intake",     label="Intake",     confidence=0.70),
            ConfidencePropagation(node="evaluation", label="Evaluation", confidence=0.85, delta=0.15),
        ]
        wt = WorkflowTrace(
            case_id=_CASE_ID,
            total_traces=2,
            reasoning_traces=traces,
            agent_runs=runs,
            confidence_chain=chain,
            total_input_tokens=2048,
            total_output_tokens=512,
            escalation_recommended=True,
            executive_summary="Escalation warranted.",
        )
        assert len(wt.reasoning_traces) == 2
        assert len(wt.agent_runs) == 1
        assert len(wt.confidence_chain) == 2
        assert wt.total_input_tokens == 2048
        assert wt.escalation_recommended is True

    def test_confidence_scores_in_chain(self):
        chain = [
            ConfidencePropagation(node="n1", label="N1", confidence=0.60),
            ConfidencePropagation(node="n2", label="N2", confidence=0.80, delta=0.20),
            ConfidencePropagation(node="n3", label="N3", confidence=0.75, delta=-0.05),
        ]
        wt = WorkflowTrace(case_id=_CASE_ID, total_traces=3, confidence_chain=chain)
        assert wt.confidence_chain[2].delta == pytest.approx(-0.05)
