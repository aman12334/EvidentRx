"""
Seed demo investigation cases and risk snapshots.
Run from the project root:  python scripts/seed_demo_cases.py
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import text
from app.database import SessionLocal

CE_ID = "f921a958-8613-4a88-a233-e1c9bdd79be4"  # EvidentRx Dev Hospital
ANALYST = "admin@evidentrx.dev"

def ts(days_ago: int = 0, hours: int = 0) -> str:
    t = datetime.now(tz=timezone.utc) - timedelta(days=days_ago, hours=hours)
    return t.isoformat()

CASES = [
    # ── Escalated / critical ──────────────────────────────────────────────────
    {
        "case_id":         str(uuid4()),
        "case_number":     "INV-2026-001",
        "covered_entity_id": CE_ID,
        "case_type":       "targeted_investigation",
        "status":          "escalated",
        "priority":        "critical",
        "title":           "Duplicate Dispensing — High-Cost Oncology NDCs",
        "description":     "Pattern of same-day duplicate fills for Keytruda (pembrolizumab) at two contract pharmacies. 340B and WAC claims submitted for identical patient/date/NDC combinations. Financial exposure estimated at $412K.",
        "assigned_to":     ANALYST,
        "opened_at":       ts(14),
        "finding_count":   23,
        "financial_exposure_estimate": 412500.00,
        "risk": {
            "composite_risk_score":  0.92,
            "total_findings":        23,
            "critical_findings":     8,
            "high_findings":         11,
            "medium_findings":       3,
            "low_findings":          1,
            "total_financial_exposure": 412500.00,
            "unique_patients":       7,
            "unique_pharmacies":     2,
            "findings_by_rule":      {"DUPE_DISP_SAME_DAY": 8, "SPLIT_BILLING": 11, "STACKING": 4},
            "ndc_list":              ["00006-3026-02", "00006-3026-04", "59148-0019-62"],
        },
    },
    {
        "case_id":         str(uuid4()),
        "case_number":     "INV-2026-002",
        "covered_entity_id": CE_ID,
        "case_type":       "targeted_investigation",
        "status":          "escalated",
        "priority":        "critical",
        "title":           "Medicaid Exclusion — Non-Enrolled Pharmacy Network",
        "description":     "Contract pharmacy dispensing Medicaid FFS claims routed through 340B without exclusion file enforcement. 340B statute prohibits duplicate discounts. 31 unique Medicaid patients affected over 90 days.",
        "assigned_to":     ANALYST,
        "opened_at":       ts(21),
        "finding_count":   31,
        "financial_exposure_estimate": 287300.00,
        "risk": {
            "composite_risk_score":  0.89,
            "total_findings":        31,
            "critical_findings":     12,
            "high_findings":         14,
            "medium_findings":       5,
            "low_findings":          0,
            "total_financial_exposure": 287300.00,
            "unique_patients":       31,
            "unique_pharmacies":     3,
            "findings_by_rule":      {"MEDICAID_EXCL_MISSING": 12, "DUPE_DISCOUNT": 14, "INELIGIBLE_PATIENT": 5},
            "ndc_list":              ["00002-7992-01", "00169-4060-12", "50090-4387-0"],
        },
    },

    # ── In progress / high ─────────────────────────────────────────────────────
    {
        "case_id":         str(uuid4()),
        "case_number":     "INV-2026-003",
        "covered_entity_id": CE_ID,
        "case_type":       "routine_audit",
        "status":          "in_progress",
        "priority":        "high",
        "title":           "Eligible Patient Definition — Specialist Referral Chain",
        "description":     "Patients referred from non-hospital providers dispensed 340B drugs without qualifying encounter in the covered entity's systems. Referral chain documentation incomplete for 18 patients.",
        "assigned_to":     ANALYST,
        "opened_at":       ts(7),
        "finding_count":   18,
        "financial_exposure_estimate": 94200.00,
        "risk": {
            "composite_risk_score":  0.71,
            "total_findings":        18,
            "critical_findings":     0,
            "high_findings":         9,
            "medium_findings":       7,
            "low_findings":          2,
            "total_financial_exposure": 94200.00,
            "unique_patients":       18,
            "unique_pharmacies":     1,
            "findings_by_rule":      {"PATIENT_ELIGIBILITY": 9, "ENCOUNTER_MISSING": 7, "REFERRAL_CHAIN": 2},
            "ndc_list":              ["00074-3799-22", "00074-0587-22"],
        },
    },
    {
        "case_id":         str(uuid4()),
        "case_number":     "INV-2026-004",
        "covered_entity_id": CE_ID,
        "case_type":       "routine_audit",
        "status":          "in_progress",
        "priority":        "high",
        "title":           "Child Site Registration Gap — Satellite Clinic",
        "description":     "Dispensing activity identified at satellite clinic location not registered as 340B child site. HRSA OPA registration does not include this address. 60-day retroactive exposure window.",
        "assigned_to":     ANALYST,
        "opened_at":       ts(5),
        "finding_count":   9,
        "financial_exposure_estimate": 58100.00,
        "risk": {
            "composite_risk_score":  0.66,
            "total_findings":        9,
            "critical_findings":     0,
            "high_findings":         6,
            "medium_findings":       3,
            "low_findings":          0,
            "total_financial_exposure": 58100.00,
            "unique_patients":       9,
            "unique_pharmacies":     1,
            "findings_by_rule":      {"CHILD_SITE_UNREGISTERED": 6, "LOCATION_MISMATCH": 3},
            "ndc_list":              ["00310-0210-30", "68084-0199-01"],
        },
    },

    # ── Pending review / medium ────────────────────────────────────────────────
    {
        "case_id":         str(uuid4()),
        "case_number":     "INV-2026-005",
        "covered_entity_id": CE_ID,
        "case_type":       "data_quality",
        "status":          "pending_review",
        "priority":        "medium",
        "title":           "NDC Mapping Discrepancy — Generic Substitution Flags",
        "description":     "TPA claim feed shows brand NDC billed but dispensing records show generic substitution. 340B price applied to brand NDC. Potential overstatement of discount captured.",
        "assigned_to":     ANALYST,
        "opened_at":       ts(3),
        "finding_count":   44,
        "financial_exposure_estimate": 21800.00,
        "risk": {
            "composite_risk_score":  0.48,
            "total_findings":        44,
            "critical_findings":     0,
            "high_findings":         4,
            "medium_findings":       28,
            "low_findings":          12,
            "total_financial_exposure": 21800.00,
            "unique_patients":       32,
            "unique_pharmacies":     4,
            "findings_by_rule":      {"NDC_MISMATCH": 28, "GENERIC_SUB_UNBILLED": 4, "DATA_QUALITY": 12},
            "ndc_list":              ["00093-7274-98", "16714-0442-01", "68382-0049-16"],
        },
    },
    {
        "case_id":         str(uuid4()),
        "case_number":     "INV-2026-006",
        "covered_entity_id": CE_ID,
        "case_type":       "regulatory_inquiry",
        "status":          "pending_review",
        "priority":        "medium",
        "title":           "HRSA Audit Response — Contract Pharmacy Agreement Review",
        "description":     "HRSA OPA requested documentation of all contract pharmacy arrangements entered since Jan 2024. Three agreements lack required written contract provisions per 61 FR 43549.",
        "assigned_to":     ANALYST,
        "opened_at":       ts(2),
        "finding_count":   3,
        "financial_exposure_estimate": 0.00,
        "risk": {
            "composite_risk_score":  0.41,
            "total_findings":        3,
            "critical_findings":     0,
            "high_findings":         0,
            "medium_findings":       3,
            "low_findings":          0,
            "total_financial_exposure": 0.00,
            "unique_patients":       0,
            "unique_pharmacies":     3,
            "findings_by_rule":      {"CONTRACT_DEFICIENCY": 3},
            "ndc_list":              [],
        },
    },

    # ── Open / medium ──────────────────────────────────────────────────────────
    {
        "case_id":         str(uuid4()),
        "case_number":     "INV-2026-007",
        "covered_entity_id": CE_ID,
        "case_type":       "routine_audit",
        "status":          "open",
        "priority":        "medium",
        "title":           "Quarterly Compliance Review — Q1 2026",
        "description":     "Scheduled quarterly audit of 340B program integrity controls. Initial data pull complete. Flagged 12 records for manual review.",
        "assigned_to":     None,
        "opened_at":       ts(1),
        "finding_count":   12,
        "financial_exposure_estimate": 15400.00,
        "risk": {
            "composite_risk_score":  0.35,
            "total_findings":        12,
            "critical_findings":     0,
            "high_findings":         2,
            "medium_findings":       7,
            "low_findings":          3,
            "total_financial_exposure": 15400.00,
            "unique_patients":       12,
            "unique_pharmacies":     2,
            "findings_by_rule":      {"DISPENSING_PATTERN": 7, "ELIGIBILITY_EDGE": 2, "DATA_QUALITY": 3},
            "ndc_list":              ["00006-0085-31", "00054-8527-13"],
        },
    },
    {
        "case_id":         str(uuid4()),
        "case_number":     "INV-2026-008",
        "covered_entity_id": CE_ID,
        "case_type":       "self_disclosure",
        "status":          "open",
        "priority":        "low",
        "title":           "Self-Disclosed: Retroactive Price Adjustment — Prior Period",
        "description":     "Entity identified potential overpayment from prior period WAC pricing error. Self-disclosure filed with HRSA. Awaiting acknowledgement. No patient harm identified.",
        "assigned_to":     None,
        "opened_at":       ts(0, hours=4),
        "finding_count":   2,
        "financial_exposure_estimate": 4200.00,
        "risk": {
            "composite_risk_score":  0.21,
            "total_findings":        2,
            "critical_findings":     0,
            "high_findings":         0,
            "medium_findings":       1,
            "low_findings":          1,
            "total_financial_exposure": 4200.00,
            "unique_patients":       2,
            "unique_pharmacies":     1,
            "findings_by_rule":      {"PRICING_ERROR": 1, "REPAYMENT_PENDING": 1},
            "ndc_list":              ["00074-4336-13"],
        },
    },
]


def run() -> None:
    db = SessionLocal()
    try:
        inserted = 0
        for c in CASES:
            risk = c.pop("risk")

            # Insert case
            db.execute(text("""
                INSERT INTO audit.investigation_cases
                    (case_id, case_number, covered_entity_id, case_type,
                     status, priority, title, description, assigned_to,
                     opened_at, finding_count, financial_exposure_estimate)
                VALUES
                    (CAST(:case_id AS uuid), :case_number, CAST(:covered_entity_id AS uuid), :case_type,
                     :status, :priority, :title, :description, :assigned_to,
                     CAST(:opened_at AS timestamptz), :finding_count, :financial_exposure_estimate)
                ON CONFLICT (case_number) DO NOTHING
            """), c)

            # Insert risk snapshot
            import json
            db.execute(text("""
                INSERT INTO audit.case_risk_snapshots
                    (case_id, snapshot_trigger,
                     total_findings, critical_findings, high_findings,
                     medium_findings, low_findings, total_financial_exposure,
                     composite_risk_score, unique_patients, unique_pharmacies,
                     findings_by_rule, ndc_list)
                VALUES
                    (CAST(:case_id AS uuid), 'case_created',
                     :total_findings, :critical_findings, :high_findings,
                     :medium_findings, :low_findings, :total_financial_exposure,
                     :composite_risk_score, :unique_patients, :unique_pharmacies,
                     CAST(:findings_by_rule AS jsonb), CAST(:ndc_list AS jsonb))
            """), {
                "case_id": c["case_id"],
                **{k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
                   for k, v in risk.items()},
            })

            inserted += 1
            print(f"  ✓ {c['case_number']}  [{c['status'].upper():15s}]  {c['title'][:55]}")

        db.commit()
        print(f"\nSeeded {inserted} investigation cases with risk snapshots.")
    except Exception as e:
        db.rollback()
        print(f"ERROR: {e}", file=sys.stderr)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()
