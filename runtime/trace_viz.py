"""
Reasoning trace visualization for the EvidentRx investigation workflow.

Renders the 7-node LangGraph topology with per-node:
  - Execution status (completed / failed / skipped / pending)
  - Agent assigned and model used
  - Token usage and latency
  - Confidence score
  - Confidence propagation across the pipeline

Also renders:
  - Evidence lineage: which findings fed which agent
  - Escalation flow: whether and why escalation was triggered

Output is structured text + JSON. No frontend required.

Usage:
    viz = TraceVisualizer()
    report = viz.build(session, case_id)
    viz.print_report(report)
    viz.to_json(report)  → dict
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Fixed workflow topology — node name → (display label, order index)
WORKFLOW_NODES: list[tuple[str, str]] = [
    ("case_intake",          "Case Intake"),
    ("evidence_aggregation", "Evidence Aggregation"),
    ("risk_prioritization",  "Risk Prioritization"),
    ("pattern_analysis",     "Pattern Analysis"),
    ("narrative_generation", "Narrative Generation"),
    ("escalation_decision",  "Escalation Decision"),
    ("case_summary",         "Case Summary"),
]

_NODE_ORDER = {name: i for i, (name, _) in enumerate(WORKFLOW_NODES)}

_STATUS_ICON = {
    "completed": "✓",
    "failed":    "✗",
    "skipped":   "○",
    "pending":   "·",
}

_CONF_BAR_WIDTH = 20


def _confidence_bar(score: float | None) -> str:
    if score is None:
        return "─" * _CONF_BAR_WIDTH + " (no data)"
    filled = round(score * _CONF_BAR_WIDTH)
    bar = "█" * filled + "░" * (_CONF_BAR_WIDTH - filled)
    return f"{bar} {score:.3f}"


@dataclass
class NodeTrace:
    node_name:     str
    display_label: str
    order:         int
    status:        str = "pending"       # completed | failed | skipped | pending
    agent_type:    str | None = None
    agent_name:    str | None = None
    model_id:      str | None = None
    input_tokens:  int = 0
    output_tokens: int = 0
    cache_tokens:  int = 0
    latency_ms:    float | None = None
    confidence:    float | None = None
    error:         str | None = None
    has_llm_call:  bool = False


@dataclass
class ConfidencePropagation:
    """Tracks how confidence flows from one agent to the next."""
    evidence_analysis:   float | None = None
    risk_prioritization: float | None = None
    narrative_generation: float | None = None

    def delta(self, a: float | None, b: float | None) -> str:
        if a is None or b is None:
            return "—"
        diff = b - a
        arrow = "↑" if diff > 0 else ("↓" if diff < 0 else "→")
        return f"{arrow} {diff:+.3f}"


@dataclass
class EvidenceLineage:
    total_findings:   int = 0
    findings_by_rule: dict = field(default_factory=dict)
    ndc_count:        int = 0
    unique_patients:  int = 0
    temporal_window:  dict | None = None
    agents_that_consumed: list[str] = field(default_factory=list)


@dataclass
class TraceVisualization:
    case_id:    str
    run_id:     str | None
    nodes:      list[NodeTrace]
    confidence: ConfidencePropagation
    lineage:    EvidenceLineage
    escalated:  bool = False
    escalation_rationale: str | None = None
    total_input_tokens:  int = 0
    total_output_tokens: int = 0
    total_cache_tokens:  int = 0


class TraceVisualizer:

    def build(self, session: Session, case_id: UUID) -> TraceVisualization:
        cid = str(case_id)

        traces   = self._load_traces(session, cid)
        runs     = self._load_agent_runs(session, cid)
        snapshot = self._load_snapshot(session, cid)

        # Index by node name
        trace_by_node: dict[str, dict] = {}
        for t in traces:
            node = t.get("workflow_node", "")
            if node not in trace_by_node or (
                t.get("workflow_step", 0) > trace_by_node[node].get("workflow_step", 0)
            ):
                trace_by_node[node] = t

        run_by_agent: dict[str, dict] = {}
        for r in runs:
            agent = r.get("agent_type", "")
            if agent not in run_by_agent:
                run_by_agent[agent] = r

        # Build node traces
        node_traces = []
        for node_name, display_label in WORKFLOW_NODES:
            nt = NodeTrace(
                node_name=node_name,
                display_label=display_label,
                order=_NODE_ORDER[node_name],
            )

            # Determine if this node has an LLM call
            nt.has_llm_call = node_name in (
                "evidence_aggregation", "risk_prioritization", "narrative_generation"
            )

            # Populate from agent_run if this is an LLM node
            agent_map = {
                "evidence_aggregation": "evidence_analysis",
                "risk_prioritization":  "risk_prioritization",
                "narrative_generation": "narrative_generation",
            }
            agent_type = agent_map.get(node_name)
            if agent_type and agent_type in run_by_agent:
                r = run_by_agent[agent_type]
                nt.agent_type  = agent_type
                nt.agent_name  = r.get("agent_name")
                nt.model_id    = r.get("model_id")
                nt.input_tokens  = r.get("input_tokens") or 0
                nt.output_tokens = r.get("output_tokens") or 0
                nt.cache_tokens  = r.get("cache_read_tokens") or 0
                nt.latency_ms    = r.get("latency_ms")
                nt.status        = r.get("status") or "completed"
                nt.error         = r.get("error_message")

            # Populate confidence from reasoning trace
            if node_name in trace_by_node:
                t = trace_by_node[node_name]
                nt.confidence = t.get("confidence_score")
                if not nt.has_llm_call:
                    nt.status = "completed"

            # Non-LLM nodes with no trace = completed (they don't write traces)
            elif not nt.has_llm_call:
                if node_name in ("case_intake", "pattern_analysis",
                                 "escalation_decision", "case_summary"):
                    nt.status = "pending"  # unknown — may or may not have run

            node_traces.append(nt)

        # Confidence propagation
        conf = ConfidencePropagation(
            evidence_analysis=trace_by_node.get("evidence_aggregation", {}).get("confidence_score"),
            risk_prioritization=trace_by_node.get("risk_prioritization", {}).get("confidence_score"),
            narrative_generation=trace_by_node.get("narrative_generation", {}).get("confidence_score"),
        )

        # Evidence lineage
        lineage = EvidenceLineage()
        if snapshot:
            lineage.total_findings  = snapshot.get("total_findings", 0)
            lineage.findings_by_rule = snapshot.get("findings_by_rule") or {}
            lineage.ndc_count       = len(snapshot.get("ndc_list") or [])
            lineage.unique_patients = snapshot.get("unique_patients") or 0
            lineage.temporal_window = snapshot.get("temporal_window")
        lineage.agents_that_consumed = [
            n for n in ("evidence_analysis", "risk_prioritization", "narrative_generation")
            if n in run_by_agent
        ]

        # Escalation
        escalated = False
        escalation_rationale = None
        for r in runs:
            if r.get("agent_type") == "risk_prioritization":
                output = self._load_agent_output(session, r.get("run_id"))
                if output:
                    escalated = bool(output.get("escalation_recommended", False))
                    escalation_rationale = output.get("escalation_rationale")

        total_in  = sum(r.get("input_tokens") or 0 for r in runs)
        total_out = sum(r.get("output_tokens") or 0 for r in runs)
        total_cac = sum(r.get("cache_read_tokens") or 0 for r in runs)

        run_id = traces[0].get("session_id") if traces else None

        return TraceVisualization(
            case_id=cid,
            run_id=run_id,
            nodes=node_traces,
            confidence=conf,
            lineage=lineage,
            escalated=escalated,
            escalation_rationale=escalation_rationale,
            total_input_tokens=total_in,
            total_output_tokens=total_out,
            total_cache_tokens=total_cac,
        )

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def print_report(self, viz: TraceVisualization) -> None:
        w = 68
        print(f"\n{'═' * w}")
        print(f"  Workflow Trace — Case {viz.case_id[:8]}...")
        print(f"{'═' * w}")

        # Node chain
        print(f"\n  {'NODE':<26} {'STATUS':<12} {'CONFIDENCE':<28} {'TOKENS IN/OUT'}")
        print(f"  {'─' * 26} {'─' * 12} {'─' * 28} {'─' * 18}")

        for i, node in enumerate(viz.nodes):
            icon   = _STATUS_ICON.get(node.status, "?")
            _confidence_bar(node.confidence) if node.has_llm_call else " " * 28
            tokens = f"{node.input_tokens:,}/{node.output_tokens:,}" if node.has_llm_call else "—"
            agent_tag = f" [{node.agent_type}]" if node.agent_type else ""
            label  = node.display_label + agent_tag

            if i > 0:
                print("  │")
            print(f"  {icon} {label:<34} {node.status:<12} {tokens:<18}")
            if node.has_llm_call:
                print(f"      confidence: {_confidence_bar(node.confidence)}")
                if node.model_id:
                    lat = f"  latency: {node.latency_ms:.0f}ms" if node.latency_ms else ""
                    print(f"      model: {node.model_id}{lat}")
            if node.error:
                print(f"      ✗ ERROR: {node.error[:80]}")

        # Confidence propagation
        print(f"\n  {'─' * 60}")
        print("  Confidence Propagation")
        print(f"  {'─' * 60}")
        cp = viz.confidence
        nodes = [
            ("Evidence Analysis",   cp.evidence_analysis),
            ("Risk Prioritization", cp.risk_prioritization),
            ("Narrative Generation",cp.narrative_generation),
        ]
        prev = None
        for label, score in nodes:
            bar    = _confidence_bar(score)
            delta  = cp.delta(prev, score) if prev is not None else "baseline"
            print(f"  {label:<24} {bar}   {delta}")
            prev = score

        # Evidence lineage
        lin = viz.lineage
        print(f"\n  {'─' * 60}")
        print("  Evidence Lineage")
        print(f"  {'─' * 60}")
        print(f"  Total findings in scope : {lin.total_findings}")
        print(f"  NDCs affected           : {lin.ndc_count}")
        print(f"  Unique patients         : {lin.unique_patients}")
        if lin.temporal_window:
            print(f"  Window                  : {lin.temporal_window.get('start','—')} → {lin.temporal_window.get('end','—')}")
        if lin.findings_by_rule:
            print("  Findings by rule:")
            for rule, count in sorted(lin.findings_by_rule.items()):
                print(f"    {rule:<12}: {count}")
        if lin.agents_that_consumed:
            print(f"  Consumed by agents      : {', '.join(lin.agents_that_consumed)}")

        # Escalation
        print(f"\n  {'─' * 60}")
        escl = "YES ⚠" if viz.escalated else "no"
        print(f"  Escalation recommended  : {escl}")
        if viz.escalation_rationale:
            print(f"  Rationale               : {viz.escalation_rationale[:120]}")

        # Token totals
        print(f"\n  {'─' * 60}")
        print(f"  Total tokens in   : {viz.total_input_tokens:,}")
        print(f"  Total tokens out  : {viz.total_output_tokens:,}")
        print(f"  Cache hits        : {viz.total_cache_tokens:,}")
        cache_pct = (
            round(viz.total_cache_tokens / viz.total_input_tokens * 100, 1)
            if viz.total_input_tokens else 0
        )
        print(f"  Cache hit rate    : {cache_pct}%")
        print(f"\n{'═' * w}\n")

    def to_json(self, viz: TraceVisualization) -> dict:
        return {
            "case_id": viz.case_id,
            "run_id":  viz.run_id,
            "workflow_nodes": [
                {
                    "node":         n.node_name,
                    "label":        n.display_label,
                    "status":       n.status,
                    "has_llm_call": n.has_llm_call,
                    "agent_type":   n.agent_type,
                    "model_id":     n.model_id,
                    "confidence":   n.confidence,
                    "input_tokens": n.input_tokens,
                    "output_tokens":n.output_tokens,
                    "cache_tokens": n.cache_tokens,
                    "latency_ms":   n.latency_ms,
                    "error":        n.error,
                }
                for n in viz.nodes
            ],
            "confidence_propagation": {
                "evidence_analysis":   viz.confidence.evidence_analysis,
                "risk_prioritization": viz.confidence.risk_prioritization,
                "narrative_generation":viz.confidence.narrative_generation,
            },
            "evidence_lineage": {
                "total_findings":  viz.lineage.total_findings,
                "findings_by_rule":viz.lineage.findings_by_rule,
                "ndc_count":       viz.lineage.ndc_count,
                "unique_patients": viz.lineage.unique_patients,
                "temporal_window": viz.lineage.temporal_window,
                "agents_that_consumed": viz.lineage.agents_that_consumed,
            },
            "escalation": {
                "recommended": viz.escalated,
                "rationale":   viz.escalation_rationale,
            },
            "token_totals": {
                "input":  viz.total_input_tokens,
                "output": viz.total_output_tokens,
                "cache":  viz.total_cache_tokens,
            },
        }

    # ------------------------------------------------------------------
    # DB queries
    # ------------------------------------------------------------------

    def _load_traces(self, session: Session, case_id: str) -> list[dict]:
        rows = session.execute(text("""
            SELECT trace_id, agent_id, agent_type, workflow_node,
                   workflow_step, confidence_score, session_id, input_context, created_at
            FROM audit.reasoning_traces
            WHERE case_id = :cid
            ORDER BY workflow_step ASC, created_at ASC
        """), {"cid": case_id}).mappings().fetchall()
        return [dict(r) for r in rows]

    def _load_agent_runs(self, session: Session, case_id: str) -> list[dict]:
        rows = session.execute(text("""
            SELECT run_id, agent_type, agent_name, status, model_id,
                   input_tokens, output_tokens, cache_read_tokens,
                   latency_ms, error_message, started_at
            FROM audit.agent_runs
            WHERE case_id = :cid
            ORDER BY started_at ASC
        """), {"cid": case_id}).mappings().fetchall()
        return [dict(r) for r in rows]

    def _load_snapshot(self, session: Session, case_id: str) -> dict | None:
        row = session.execute(text("""
            SELECT total_findings, by_severity, findings_by_rule,
                   ndc_list, unique_patients, temporal_window
            FROM audit.case_risk_snapshots
            WHERE case_id = :cid
            ORDER BY created_at DESC LIMIT 1
        """), {"cid": case_id}).mappings().first()
        if not row:
            return None
        d = dict(row)
        for f in ("by_severity", "findings_by_rule", "ndc_list", "temporal_window"):
            if isinstance(d.get(f), str):
                try:
                    d[f] = json.loads(d[f])
                except Exception:
                    pass
        return d

    def _load_agent_output(self, session: Session, run_id) -> dict | None:
        if not run_id:
            return None
        row = session.execute(text("""
            SELECT output_payload FROM audit.agent_runs WHERE run_id = :rid
        """), {"rid": str(run_id)}).mappings().first()
        if not row or not row["output_payload"]:
            return None
        payload = row["output_payload"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                return None
        return payload if isinstance(payload, dict) else None
