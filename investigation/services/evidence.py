"""
EvidenceAggregationService — builds evidence summaries and risk snapshots
from all findings linked to an investigation case.

Reads from audit.investigation_case_findings joined to audit.audit_findings
and ops.split_billing. Writes immutable snapshots to audit.case_risk_snapshots.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_SEVERITY_WEIGHT = {"critical": 1.0, "high": 0.8, "medium": 0.5, "low": 0.2}

_FINDINGS_QUERY = text("""
    SELECT
        af.finding_id,
        af.rule_code,
        af.finding_type,
        af.severity,
        af.financial_exposure,
        af.violation_period_start   AS service_date,
        af.split_billing_id,
        sb.ndc_11,
        sb.patient_id_hash,
        sb.dispense_id
    FROM audit.investigation_case_findings icf
    JOIN audit.audit_findings af ON af.finding_id = icf.finding_id
    LEFT JOIN ops.split_billing sb ON sb.split_billing_id = af.split_billing_id
    WHERE icf.case_id = :case_id
""")


class EvidenceAggregationService:
    def build_summary(self, session: Session, case_id: UUID) -> dict:
        """
        Aggregates all evidence for a case into a summary dict.
        Does not write to DB — call take_risk_snapshot() to persist.
        """
        rows = session.execute(_FINDINGS_QUERY, {"case_id": str(case_id)}).fetchall()

        if not rows:
            return _empty_summary(case_id)

        severity_counts = Counter(r.severity for r in rows)
        rule_counts = Counter(r.rule_code for r in rows)
        ndcs = sorted(set(r.ndc_11 for r in rows if r.ndc_11))
        patients = {r.patient_id_hash for r in rows if r.patient_id_hash}
        dispenses = {r.dispense_id for r in rows if r.dispense_id}
        service_dates = [r.service_date for r in rows if r.service_date]

        raw_exposure = [r.financial_exposure for r in rows if r.financial_exposure is not None]
        total_exposure = sum(Decimal(str(e)) for e in raw_exposure) if raw_exposure else None

        # Composite risk score: weighted average of per-finding severity weights
        weights = [_SEVERITY_WEIGHT.get(r.severity, 0.5) for r in rows]
        composite_risk = Decimal(str(sum(weights) / len(weights))).quantize(Decimal("0.0001"))

        return {
            "case_id": str(case_id),
            "total_findings": len(rows),
            "by_severity": {
                "critical": severity_counts.get("critical", 0),
                "high":     severity_counts.get("high", 0),
                "medium":   severity_counts.get("medium", 0),
                "low":      severity_counts.get("low", 0),
            },
            "by_rule": dict(rule_counts),
            "total_financial_exposure": float(total_exposure) if total_exposure else None,
            "composite_risk_score": float(composite_risk),
            "temporal_window": {
                "start": min(service_dates).isoformat() if service_dates else None,
                "end":   max(service_dates).isoformat() if service_dates else None,
            },
            "ndc_list": ndcs,
            "unique_patients": len(patients),
            "unique_dispensing_locations": len(dispenses),
        }

    def take_risk_snapshot(
        self,
        session: Session,
        case_id: UUID,
        trigger: str,
    ) -> UUID:
        """
        Builds a summary and writes an immutable snapshot row.
        Returns the new snapshot_id.
        """
        summary = self.build_summary(session, case_id)
        snapshot_id = uuid4()
        now = datetime.now(UTC)

        tw = summary.get("temporal_window", {})

        session.execute(text("""
            INSERT INTO audit.case_risk_snapshots (
                snapshot_id, case_id, snapshot_at, snapshot_trigger,
                total_findings, critical_findings, high_findings,
                medium_findings, low_findings,
                total_financial_exposure, composite_risk_score,
                findings_by_rule, ndc_list,
                temporal_window_start, temporal_window_end,
                unique_patients
            ) VALUES (
                :snapshot_id, :case_id, :snapshot_at, :trigger,
                :total, :critical, :high, :medium, :low,
                :exposure, :risk_score,
                CAST(:by_rule AS jsonb), CAST(:ndc_list AS jsonb),
                :tw_start, :tw_end,
                :unique_patients
            )
        """), {
            "snapshot_id":     str(snapshot_id),
            "case_id":         str(case_id),
            "snapshot_at":     now,
            "trigger":         trigger,
            "total":           summary["total_findings"],
            "critical":        summary["by_severity"]["critical"],
            "high":            summary["by_severity"]["high"],
            "medium":          summary["by_severity"]["medium"],
            "low":             summary["by_severity"]["low"],
            "exposure":        summary["total_financial_exposure"],
            "risk_score":      summary["composite_risk_score"],
            "by_rule":         json.dumps(summary["by_rule"]),
            "ndc_list":        json.dumps(summary["ndc_list"]),
            "tw_start":        tw.get("start"),
            "tw_end":          tw.get("end"),
            "unique_patients": summary["unique_patients"],
        })

        logger.debug("Snapshot %s taken for case %s (trigger=%s)", snapshot_id, case_id, trigger)
        return snapshot_id

    def latest_snapshot(self, session: Session, case_id: UUID) -> dict | None:
        """Returns the most recent risk snapshot for a case, or None."""
        row = session.execute(text("""
            SELECT snapshot_id, snapshot_at, snapshot_trigger,
                   total_findings, critical_findings, high_findings,
                   medium_findings, low_findings,
                   total_financial_exposure, composite_risk_score,
                   findings_by_rule, ndc_list,
                   temporal_window_start, temporal_window_end, unique_patients
            FROM audit.case_risk_snapshots
            WHERE case_id = :case_id
            ORDER BY snapshot_at DESC
            LIMIT 1
        """), {"case_id": str(case_id)}).fetchone()

        if not row:
            return None

        return {
            "snapshot_id":             str(row.snapshot_id),
            "snapshot_at":             row.snapshot_at.isoformat(),
            "trigger":                 row.snapshot_trigger,
            "total_findings":          row.total_findings,
            "by_severity": {
                "critical": row.critical_findings,
                "high":     row.high_findings,
                "medium":   row.medium_findings,
                "low":      row.low_findings,
            },
            "total_financial_exposure": float(row.total_financial_exposure)
                                        if row.total_financial_exposure else None,
            "composite_risk_score":    float(row.composite_risk_score)
                                        if row.composite_risk_score else None,
            "findings_by_rule":        row.findings_by_rule,
            "ndc_list":                row.ndc_list,
            "temporal_window": {
                "start": row.temporal_window_start.isoformat() if row.temporal_window_start else None,
                "end":   row.temporal_window_end.isoformat()   if row.temporal_window_end   else None,
            },
            "unique_patients": row.unique_patients,
        }


def _empty_summary(case_id: UUID) -> dict:
    return {
        "case_id": str(case_id),
        "total_findings": 0,
        "by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 0},
        "by_rule": {},
        "total_financial_exposure": None,
        "composite_risk_score": 0.0,
        "temporal_window": {"start": None, "end": None},
        "ndc_list": [],
        "unique_patients": 0,
        "unique_dispensing_locations": 0,
    }
