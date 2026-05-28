"""
case_intake node — loads case context from DB into workflow state.
Validates that the case has findings and a risk snapshot before proceeding.
No LLM call. Pure DB read.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from sqlalchemy import text

from agents.state import InvestigationState

logger = logging.getLogger(__name__)


def case_intake(state: InvestigationState, config: RunnableConfig) -> dict:
    session = config["configurable"]["session"]
    orchestrator = config["configurable"]["orchestrator"]
    case_id = state["case_id"]

    try:
        # Load case metadata
        case_row = session.execute(text("""
            SELECT case_id, case_number, covered_entity_id, case_type,
                   status, priority, title, finding_count,
                   opened_at, financial_exposure_estimate
            FROM audit.investigation_cases
            WHERE case_id = :case_id
        """), {"case_id": case_id}).fetchone()

        if not case_row:
            return {
                "errors": [{"node": "case_intake", "error": f"Case {case_id} not found"}],
                "current_node": "case_intake",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_cache_read_tokens": 0,
            }

        case_dict = {
            "case_id":           str(case_row.case_id),
            "case_number":       case_row.case_number,
            "covered_entity_id": str(case_row.covered_entity_id),
            "case_type":         case_row.case_type,
            "status":            case_row.status,
            "priority":          case_row.priority,
            "title":             case_row.title,
            "finding_count":     case_row.finding_count,
            "opened_at":         case_row.opened_at.isoformat() if case_row.opened_at else None,
        }

        # Load findings
        finding_rows = session.execute(text("""
            SELECT af.finding_id, af.finding_code, af.rule_code, af.finding_type,
                   af.severity, af.status, af.confidence_score, af.financial_exposure,
                   af.violation_period_start, af.evidence_payload, af.entity_references
            FROM audit.investigation_case_findings icf
            JOIN audit.audit_findings af ON af.finding_id = icf.finding_id
            WHERE icf.case_id = :case_id
            ORDER BY af.severity DESC, af.violation_period_start ASC
        """), {"case_id": case_id}).fetchall()

        findings = [
            {
                "finding_id":            str(r.finding_id),
                "finding_code":          r.finding_code,
                "rule_code":             r.rule_code,
                "finding_type":          r.finding_type,
                "severity":              r.severity,
                "status":                r.status,
                "confidence_score":      float(r.confidence_score) if r.confidence_score else None,
                "financial_exposure":    float(r.financial_exposure) if r.financial_exposure else None,
                "violation_period_start":r.violation_period_start.isoformat() if r.violation_period_start else None,
                "evidence_payload":      r.evidence_payload or {},
                "entity_references":     r.entity_references or {},
            }
            for r in finding_rows
        ]

        # Load latest risk snapshot (built by EvidenceAggregationService in Phase 4)
        snap_row = session.execute(text("""
            SELECT snapshot_id, snapshot_trigger, total_findings,
                   critical_findings, high_findings, medium_findings, low_findings,
                   total_financial_exposure, composite_risk_score,
                   findings_by_rule, ndc_list,
                   temporal_window_start, temporal_window_end, unique_patients
            FROM audit.case_risk_snapshots
            WHERE case_id = :case_id
            ORDER BY snapshot_at DESC
            LIMIT 1
        """), {"case_id": case_id}).fetchone()

        risk_snapshot = {}
        if snap_row:
            risk_snapshot = {
                "total_findings":          snap_row.total_findings,
                "by_severity": {
                    "critical": snap_row.critical_findings,
                    "high":     snap_row.high_findings,
                    "medium":   snap_row.medium_findings,
                    "low":      snap_row.low_findings,
                },
                "total_financial_exposure": float(snap_row.total_financial_exposure) if snap_row.total_financial_exposure else None,
                "composite_risk_score":     float(snap_row.composite_risk_score) if snap_row.composite_risk_score else None,
                "findings_by_rule":         snap_row.findings_by_rule or {},
                "ndc_list":                 snap_row.ndc_list or [],
                "temporal_window": {
                    "start": snap_row.temporal_window_start.isoformat() if snap_row.temporal_window_start else None,
                    "end":   snap_row.temporal_window_end.isoformat() if snap_row.temporal_window_end else None,
                },
                "unique_patients": snap_row.unique_patients,
            }

        logger.info(
            "case_intake: case=%s findings=%d snapshot=%s",
            case_id, len(findings), "loaded" if risk_snapshot else "missing",
        )

        return {
            "case":          case_dict,
            "findings":      findings,
            "risk_snapshot": risk_snapshot,
            "evidence_summary": {},
            "current_node":  "case_intake",
            "started_at":    datetime.now(timezone.utc).isoformat(),
            "total_input_tokens":      0,
            "total_output_tokens":     0,
            "total_cache_read_tokens": 0,
        }

    except Exception as e:
        logger.exception("case_intake failed for case %s", case_id)
        return {
            "errors": [{"node": "case_intake", "error": str(e), "error_type": type(e).__name__}],
            "current_node": "case_intake",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "total_input_tokens":      0,
            "total_output_tokens":     0,
            "total_cache_read_tokens": 0,
        }
