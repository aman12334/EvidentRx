"""
ReportDataLoader — loads all data for an investigation case from the DB.

Returns a ReportData dataclass that reporters (Markdown, JSON, HTML)
consume to render output. Centralizes all DB queries so reporters are
pure rendering logic.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass
class ReportData:
    case_id:          str
    case:             dict                  # investigation_cases row
    findings:         list[dict]            # audit_findings rows
    risk_snapshot:    dict | None        # latest case_risk_snapshots row
    timeline:         list[dict]            # investigation_timeline rows (ordered)
    reasoning_traces: list[dict]            # reasoning_traces rows
    agent_runs:       list[dict]            # agent_runs rows
    narrative:        dict                  # narrative JSON from final agent run
    checkpoints:      list[dict]            # workflow_checkpoints rows

    @property
    def ce_name(self) -> str:
        return self.case.get("entity_name") or self.case.get("covered_entity_id", "Unknown CE")

    @property
    def risk_level(self) -> str:
        return (self.risk_snapshot or {}).get("composite_risk_score") or "unknown"

    @property
    def total_findings(self) -> int:
        return len(self.findings)

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.get("severity") == "critical")

    @property
    def high_count(self) -> int:
        return sum(1 for f in self.findings if f.get("severity") == "high")

    @property
    def financial_exposure(self) -> float | None:
        snap = self.risk_snapshot or {}
        return snap.get("total_financial_exposure")


class ReportDataLoader:
    """
    Loads all report data for a case from PostgreSQL in a single session.
    """

    def load(self, session: Session, case_id: UUID) -> ReportData:
        cid = str(case_id)

        case             = self._load_case(session, cid)
        findings         = self._load_findings(session, cid)
        risk_snapshot    = self._load_risk_snapshot(session, cid)
        timeline         = self._load_timeline(session, cid)
        reasoning_traces = self._load_reasoning_traces(session, cid)
        agent_runs       = self._load_agent_runs(session, cid)
        narrative        = self._load_narrative(session, cid, agent_runs)
        checkpoints      = self._load_checkpoints(session, cid)

        return ReportData(
            case_id=cid,
            case=case,
            findings=findings,
            risk_snapshot=risk_snapshot,
            timeline=timeline,
            reasoning_traces=reasoning_traces,
            agent_runs=agent_runs,
            narrative=narrative,
            checkpoints=checkpoints,
        )

    # ------------------------------------------------------------------
    # Individual queries
    # ------------------------------------------------------------------

    def _load_case(self, session: Session, case_id: str) -> dict:
        row = session.execute(text("""
            SELECT
                ic.case_id,
                ic.case_number,
                ic.covered_entity_id,
                ce.entity_name,
                ic.violation_category,
                ic.status,
                ic.priority,
                ic.total_findings_count,
                ic.financial_exposure_estimate,
                ic.opened_at,
                ic.closed_at,
                ic.assigned_to
            FROM audit.investigation_cases ic
            LEFT JOIN ref.covered_entities ce
                   ON ic.covered_entity_id = ce.ce_id
                  AND ce.is_current = TRUE
            WHERE ic.case_id = :cid
        """), {"cid": case_id}).mappings().first()
        return dict(row) if row else {}

    def _load_findings(self, session: Session, case_id: str) -> list[dict]:
        rows = session.execute(text("""
            SELECT
                af.finding_id,
                af.finding_code,
                af.rule_code,
                af.severity,
                af.violation_category,
                af.split_billing_id,
                af.ndc_11,
                af.service_date,
                af.evidence_payload,
                af.detected_at,
                icf.is_primary
            FROM audit.investigation_case_findings icf
            JOIN audit.audit_findings af ON icf.finding_id = af.finding_id
            WHERE icf.case_id = :cid
            ORDER BY af.severity DESC, af.service_date
        """), {"cid": case_id}).mappings().fetchall()
        result = []
        for r in rows:
            d = dict(r)
            # Parse evidence_payload if stored as string
            if isinstance(d.get("evidence_payload"), str):
                try:
                    d["evidence_payload"] = json.loads(d["evidence_payload"])
                except Exception:
                    pass
            result.append(d)
        return result

    def _load_risk_snapshot(self, session: Session, case_id: str) -> dict | None:
        row = session.execute(text("""
            SELECT
                snapshot_id,
                trigger,
                total_findings,
                by_severity,
                findings_by_rule,
                composite_risk_score,
                total_financial_exposure,
                temporal_window,
                ndc_list,
                unique_patients,
                created_at
            FROM audit.case_risk_snapshots
            WHERE case_id = :cid
            ORDER BY created_at DESC
            LIMIT 1
        """), {"cid": case_id}).mappings().first()
        if not row:
            return None
        d = dict(row)
        for json_field in ("by_severity", "findings_by_rule", "temporal_window", "ndc_list"):
            if isinstance(d.get(json_field), str):
                try:
                    d[json_field] = json.loads(d[json_field])
                except Exception:
                    pass
        return d

    def _load_timeline(self, session: Session, case_id: str) -> list[dict]:
        rows = session.execute(text("""
            SELECT
                event_type,
                actor_id,
                actor_type,
                event_data,
                occurred_at,
                sequence_number
            FROM audit.investigation_timeline
            WHERE case_id = :cid
            ORDER BY sequence_number ASC
        """), {"cid": case_id}).mappings().fetchall()
        result = []
        for r in rows:
            d = dict(r)
            if isinstance(d.get("event_data"), str):
                try:
                    d["event_data"] = json.loads(d["event_data"])
                except Exception:
                    pass
            result.append(d)
        return result

    def _load_reasoning_traces(self, session: Session, case_id: str) -> list[dict]:
        rows = session.execute(text("""
            SELECT
                trace_id,
                agent_id,
                agent_type,
                workflow_node,
                workflow_step,
                confidence_score,
                input_context,
                created_at
            FROM audit.reasoning_traces
            WHERE case_id = :cid
            ORDER BY workflow_step ASC, created_at ASC
        """), {"cid": case_id}).mappings().fetchall()
        result = []
        for r in rows:
            d = dict(r)
            if isinstance(d.get("input_context"), str):
                try:
                    d["input_context"] = json.loads(d["input_context"])
                except Exception:
                    pass
            result.append(d)
        return result

    def _load_agent_runs(self, session: Session, case_id: str) -> list[dict]:
        rows = session.execute(text("""
            SELECT
                run_id,
                agent_type,
                agent_name,
                status,
                model_id,
                input_tokens,
                output_tokens,
                cache_read_tokens,
                latency_ms,
                started_at,
                completed_at,
                error_message
            FROM audit.agent_runs
            WHERE case_id = :cid
            ORDER BY started_at ASC
        """), {"cid": case_id}).mappings().fetchall()
        return [dict(r) for r in rows]

    def _load_narrative(self, session: Session, case_id: str, agent_runs: list[dict]) -> dict:
        """Extract the narrative from the narrative_generation agent run output."""
        narrative_run = next(
            (r for r in reversed(agent_runs)
             if r.get("agent_type") == "narrative_generation" and r.get("status") == "completed"),
            None,
        )
        if not narrative_run:
            return {}

        row = session.execute(text("""
            SELECT output_payload
            FROM audit.agent_runs
            WHERE run_id = :rid
        """), {"rid": str(narrative_run["run_id"])}).mappings().first()

        if not row or not row["output_payload"]:
            return {}

        payload = row["output_payload"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                return {}
        return payload if isinstance(payload, dict) else {}

    def _load_checkpoints(self, session: Session, case_id: str) -> list[dict]:
        rows = session.execute(text("""
            SELECT
                checkpoint_id,
                workflow_name,
                checkpoint_name,
                node_name,
                is_resumable,
                created_at
            FROM audit.workflow_checkpoints
            WHERE case_id = :cid
            ORDER BY created_at DESC
        """), {"cid": case_id}).mappings().fetchall()
        return [dict(r) for r in rows]
