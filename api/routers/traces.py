"""Reasoning trace and agent workflow API endpoints."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.schemas.trace import WorkflowTrace
from app.database import get_db

router = APIRouter(prefix="/traces", tags=["Traces"])


@router.get("/case/{case_id}", response_model=WorkflowTrace)
def get_workflow_trace(case_id: UUID, db: Session = Depends(get_db)):
    """
    Returns the full workflow trace for a case —
    agent runs, reasoning traces, confidence propagation chain,
    and final escalation decision.
    """
    traces = db.execute(text("""
        SELECT trace_id, case_id, agent_id, agent_type, workflow_node,
               workflow_step, confidence_score, input_context, output_summary, created_at
        FROM audit.reasoning_traces
        WHERE case_id = :cid::uuid
        ORDER BY workflow_step ASC, created_at ASC
    """), {"cid": str(case_id)}).mappings().fetchall()

    runs = db.execute(text("""
        SELECT run_id, case_id, agent_type, status,
               input_tokens, output_tokens, cache_read_tokens,
               latency_ms, started_at, completed_at, output
        FROM audit.agent_runs
        WHERE case_id = :cid::uuid
        ORDER BY started_at ASC
    """), {"cid": str(case_id)}).mappings().fetchall()

    if not traces and not runs:
        raise HTTPException(status_code=404, detail=f"No traces found for case {case_id}")

    # Build confidence propagation chain from traces
    confidence_chain = []
    seen_nodes = set()
    prev_confidence = None

    for t in traces:
        node = t["workflow_node"]
        if node in seen_nodes:
            continue
        seen_nodes.add(node)
        conf = float(t["confidence_score"]) if t["confidence_score"] is not None else None
        delta = round(conf - prev_confidence, 4) if (conf is not None and prev_confidence is not None) else None
        confidence_chain.append({
            "node":       node,
            "label":      node.replace("_", " ").title(),
            "confidence": conf,
            "delta":      delta,
        })
        if conf is not None:
            prev_confidence = conf

    # Pull escalation + summary from last narrative run
    escalation = None
    summary = None
    for run in reversed(list(runs)):
        out = run["output"] or {}
        if isinstance(out, str):
            import json
            try:
                out = json.loads(out)
            except Exception:
                out = {}
        if "escalation_recommended" in out:
            escalation = bool(out["escalation_recommended"])
        if "executive_summary" in out and not summary:
            summary = out["executive_summary"]
        if escalation is not None and summary:
            break

    total_in  = sum(int(r["input_tokens"] or 0) for r in runs)
    total_out = sum(int(r["output_tokens"] or 0) for r in runs)

    return WorkflowTrace(
        case_id=case_id,
        total_traces=len(traces),
        agent_runs=[dict(r) for r in runs],
        reasoning_traces=[dict(t) for t in traces],
        confidence_chain=confidence_chain,
        total_input_tokens=total_in,
        total_output_tokens=total_out,
        escalation_recommended=escalation,
        executive_summary=summary,
    )
