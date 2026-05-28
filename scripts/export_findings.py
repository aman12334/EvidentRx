#!/usr/bin/env python3
"""
export_findings.py — Export audit findings to CSV for external review.

Produces a compliance report CSV that investigators and covered entities
can share with HRSA auditors or use in remediation workflows.

Output columns:
    finding_id, case_number, covered_entity, rule_code, rule_name,
    severity, financial_exposure, violation_period_start,
    violation_period_end, status, ndc_11, patient_id_hash,
    dispense_date, claim_service_date, created_at

Usage:
    python scripts/export_findings.py
    python scripts/export_findings.py --status open --severity critical high
    python scripts/export_findings.py --case INV-2025-00001
    python scripts/export_findings.py --out findings_report_2025.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from app.database import SessionLocal  # noqa: E402
from sqlalchemy import text


def export_findings(
    output_path: Path,
    status_filter: list[str] | None = None,
    severity_filter: list[str] | None = None,
    case_number: str | None = None,
    limit: int = 10_000,
) -> int:
    """Export findings to CSV. Returns row count."""
    db = SessionLocal()
    try:
        conditions = []
        params: dict = {"limit": limit}

        if status_filter:
            conditions.append("af.status = ANY(:statuses)")
            params["statuses"] = status_filter

        if severity_filter:
            conditions.append("af.severity = ANY(:severities)")
            params["severities"] = severity_filter

        if case_number:
            conditions.append("ic.case_number = :case_number")
            params["case_number"] = case_number

        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        sql = text(f"""
            SELECT
                af.finding_id::text,
                COALESCE(ic.case_number, '—')           AS case_number,
                COALESCE(ce.entity_name, 'Unknown CE')  AS covered_entity,
                af.rule_code,
                COALESCE(cr.rule_name, af.rule_code)    AS rule_name,
                af.severity,
                COALESCE(af.financial_exposure::text, '') AS financial_exposure,
                COALESCE(af.violation_period_start::text, '') AS violation_period_start,
                COALESCE(af.violation_period_end::text,   '') AS violation_period_end,
                af.status,
                COALESCE(sb.ndc_11, '')                 AS ndc_11,
                COALESCE(sb.patient_id_hash, '')        AS patient_id_hash,
                COALESCE(sb.dispense_date::text, '')    AS dispense_date,
                COALESCE(sb.claim_service_date::text,   '') AS claim_service_date,
                af.created_at::text                     AS created_at
            FROM audit.audit_findings af
            LEFT JOIN audit.investigation_cases ic
                   ON ic.case_id = af.investigation_case_id
            LEFT JOIN ref.covered_entities ce
                   ON ce.ce_id = af.covered_entity_id AND ce.is_current = TRUE
            LEFT JOIN audit.compliance_rules cr
                   ON cr.rule_code = af.rule_code
            LEFT JOIN ops.split_billing sb
                   ON sb.split_billing_id = af.split_billing_id
            {where_clause}
            ORDER BY
                CASE af.severity
                    WHEN 'critical' THEN 1 WHEN 'high' THEN 2
                    WHEN 'medium' THEN 3 ELSE 4
                END,
                af.created_at DESC
            LIMIT :limit
        """)

        rows = db.execute(sql, params).fetchall()
        if not rows:
            print("No findings matched the filters.")
            return 0

        fields = [
            "finding_id", "case_number", "covered_entity",
            "rule_code", "rule_name", "severity",
            "financial_exposure", "violation_period_start", "violation_period_end",
            "status", "ndc_11", "patient_id_hash",
            "dispense_date", "claim_service_date", "created_at",
        ]

        with open(output_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(fields)
            for row in rows:
                writer.writerow([getattr(row, col, "") for col in fields])

        return len(rows)

    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export EvidentRx audit findings to CSV",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--status", nargs="+", default=["open"],
        choices=["open", "closed", "in_review", "remediated", "dismissed"],
        help="Filter by finding status",
    )
    parser.add_argument(
        "--severity", nargs="+", default=None,
        choices=["critical", "high", "medium", "low"],
        help="Filter by severity (default: all)",
    )
    parser.add_argument(
        "--case", default=None, metavar="CASE_NUMBER",
        help="Export findings for a specific case, e.g. INV-2025-00001",
    )
    parser.add_argument(
        "--limit", type=int, default=10_000,
        help="Maximum rows to export",
    )
    parser.add_argument(
        "--out", default=None,
        help="Output file path (default: findings_<timestamp>.csv)",
    )
    args = parser.parse_args()

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = Path(args.out) if args.out else Path(f"findings_{ts}.csv")

    print(f"Exporting findings → {out_path}")
    count = export_findings(
        output_path=out_path,
        status_filter=args.status,
        severity_filter=args.severity,
        case_number=args.case,
        limit=args.limit,
    )
    if count:
        print(f"  Exported {count:,} findings to {out_path}")


if __name__ == "__main__":
    main()
