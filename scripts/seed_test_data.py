"""
Comprehensive test data seed — 150 investigation cases.

Covers:
  • Every status × priority × case_type combination (systematic grid)
  • ~40 explicit edge cases (zero findings, extreme exposure, conflicting signals, etc.)
  • Time distribution spanning 365 days
  • Multiple analyst assignments
  • Varied risk profiles to exercise all dashboard buckets

Run from project root:
    .venv/bin/python3 scripts/seed_test_data.py
    .venv/bin/python3 scripts/seed_test_data.py --wipe   # clear existing first
"""
from __future__ import annotations

import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import text

from app.database import SessionLocal

# ── Constants ──────────────────────────────────────────────────────────────────
CE_ID      = "f921a958-8613-4a88-a233-e1c9bdd79be4"
ANALYSTS   = [
    "admin@evidentrx.dev",
    "sarah.chen@evidentrx.dev",
    "marcus.obi@evidentrx.dev",
    "priya.nair@evidentrx.dev",
    None,   # unassigned
]
STATUSES   = ["open", "in_progress", "pending_review", "escalated", "closed", "dismissed", "on_hold"]
PRIORITIES = ["critical", "high", "medium", "low"]
CASE_TYPES = ["routine_audit", "targeted_investigation", "self_disclosure", "regulatory_inquiry", "data_quality"]
RULES      = ["DD-001", "DD-002", "MEO-001", "MEO-002", "CPE-001", "CPE-002", "EE-001", "SB-001", "DQ-001", "DQ-002"]
NDCS       = [
    "00006-3026-02", "00006-3026-04", "59148-0019-62",
    "00002-7992-01", "00169-4060-12", "50090-4387-0",
    "00074-3799-22", "00074-0587-22", "00310-0210-30",
    "68084-0199-01", "00093-7274-98", "16714-0442-01",
    "68382-0049-16", "00006-0085-31", "00054-8527-13",
    "00074-4336-13", "00310-0455-39", "00781-5077-92",
]

random.seed(42)   # reproducible

def ts(days_ago: float = 0, hours: float = 0) -> str:
    t = datetime.now(tz=UTC) - timedelta(days=days_ago, hours=hours)
    return t.isoformat()

def risk_from_priority(priority: str, jitter: float = 0.0) -> float:
    base = {"critical": 0.85, "high": 0.68, "medium": 0.45, "low": 0.22}[priority]
    return round(min(0.9999, max(0.0001, base + jitter)), 4)

def findings_dist(total: int, critical_pct: float, high_pct: float):
    critical = int(total * critical_pct)
    high     = int(total * high_pct)
    medium   = int(total * 0.3)
    low      = max(0, total - critical - high - medium)
    return critical, high, medium, low

def random_ndcs(n: int = 3):
    return random.sample(NDCS, min(n, len(NDCS)))

def random_rules(n: int = 3):
    sample = random.sample(RULES, min(n, len(RULES)))
    return {r: random.randint(1, 15) for r in sample}

# ── Case builders ─────────────────────────────────────────────────────────────

def make_case(
    seq: int,
    status: str,
    priority: str,
    case_type: str,
    title: str,
    description: str,
    days_ago: float,
    finding_count: int,
    exposure: float,
    risk_score: float,
    critical_pct: float = 0.0,
    high_pct: float = 0.3,
    assigned_to: str | None = "admin@evidentrx.dev",
    unique_patients: int | None = None,
    unique_pharmacies: int | None = None,
    ndc_count: int = 3,
    has_snapshot: bool = True,
) -> dict:
    case_id = str(uuid4())
    critical, high, medium, low = findings_dist(finding_count, critical_pct, high_pct)
    return {
        "case": {
            "case_id":                   case_id,
            "case_number":               f"INV-TEST-{seq:04d}",
            "covered_entity_id":         CE_ID,
            "case_type":                 case_type,
            "status":                    status,
            "priority":                  priority,
            "title":                     title,
            "description":               description,
            "assigned_to":               assigned_to,
            "opened_at":                 ts(days_ago),
            "finding_count":             finding_count,
            "financial_exposure_estimate": exposure,
        },
        "risk": {
            "case_id":                   case_id,
            "composite_risk_score":      risk_score,
            "total_findings":            finding_count,
            "critical_findings":         critical,
            "high_findings":             high,
            "medium_findings":           medium,
            "low_findings":              low,
            "total_financial_exposure":  exposure,
            "unique_patients":           unique_patients or max(1, finding_count // 3),
            "unique_pharmacies":         unique_pharmacies or random.randint(1, 5),
            "findings_by_rule":          random_rules(3),
            "ndc_list":                  random_ndcs(ndc_count),
        } if has_snapshot else None,
    }


# ── 1. SYSTEMATIC GRID: all status × priority × case_type combos ──────────────

SYSTEMATIC = []
seq = 1

VIOLATION_TITLES = {
    "routine_audit":          "Quarterly Routine Audit — {priority} Priority",
    "targeted_investigation": "Targeted Investigation — {violation}",
    "self_disclosure":        "Self-Disclosed: {violation} ({priority})",
    "regulatory_inquiry":     "HRSA Inquiry Response — {priority} Case",
    "data_quality":           "Data Quality Review — {priority} Priority",
}
VIOLATIONS = [
    "Duplicate Dispensing Pattern",
    "Medicaid Exclusion Gap",
    "Patient Eligibility Issue",
    "Contract Pharmacy Overlap",
    "Split Billing Mismatch",
]

EXPOSURE_BY_PRIORITY = {
    "critical": lambda: round(random.uniform(200_000, 800_000), 2),
    "high":     lambda: round(random.uniform(50_000,  200_000), 2),
    "medium":   lambda: round(random.uniform(10_000,  50_000),  2),
    "low":      lambda: round(random.uniform(0,       10_000),  2),
}
FINDINGS_BY_PRIORITY = {
    "critical": lambda: random.randint(15, 60),
    "high":     lambda: random.randint(8,  25),
    "medium":   lambda: random.randint(3,  12),
    "low":      lambda: random.randint(1,  6),
}
CRITICAL_PCT_BY_PRIORITY = {
    "critical": 0.35,
    "high":     0.10,
    "medium":   0.02,
    "low":      0.0,
}

for status in STATUSES:
    for priority in PRIORITIES:
        for case_type in CASE_TYPES[:3]:   # 3 types per combination = 84 systematic cases
            violation = random.choice(VIOLATIONS)
            title = VIOLATION_TITLES[case_type].format(
                priority=priority.title(), violation=violation
            )
            exposure    = EXPOSURE_BY_PRIORITY[priority]()
            findings    = FINDINGS_BY_PRIORITY[priority]()
            risk_jitter = random.uniform(-0.05, 0.05)
            days_ago    = random.uniform(1, 365)

            # Closed/dismissed cases have a closed_at and lower exposure
            if status in ("closed", "dismissed"):
                exposure = round(exposure * 0.6, 2)

            SYSTEMATIC.append(make_case(
                seq=seq,
                status=status,
                priority=priority,
                case_type=case_type,
                title=title,
                description=f"Systematic test case: {status} / {priority} / {case_type}. {violation}.",
                days_ago=days_ago,
                finding_count=findings,
                exposure=exposure,
                risk_score=risk_from_priority(priority, risk_jitter),
                critical_pct=CRITICAL_PCT_BY_PRIORITY[priority],
                assigned_to=random.choice(ANALYSTS),
                ndc_count=random.randint(1, 5),
            ))
            seq += 1


# ── 2. EXPLICIT EDGE CASES ────────────────────────────────────────────────────

EDGE_CASES = []

def ec(title, description, **kwargs):
    EDGE_CASES.append(make_case(seq=seq + len(EDGE_CASES), title=title, description=description, **kwargs))

# Edge 1: Zero findings, open, critical — misconfigured case
ec("EDGE: Zero Findings — Critical Priority",
   "Critical priority case with zero linked findings. Should surface in data quality checks.",
   status="open", priority="critical", case_type="data_quality",
   days_ago=2, finding_count=0, exposure=0, risk_score=0.0,
   critical_pct=0, high_pct=0, unique_patients=0, unique_pharmacies=0)

# Edge 2: Single finding, extreme exposure
ec("EDGE: Single Finding — $2.1M Exposure",
   "One critical finding with catastrophic financial exposure. Tests exposure-driven escalation.",
   status="escalated", priority="critical", case_type="targeted_investigation",
   days_ago=5, finding_count=1, exposure=2_100_000, risk_score=0.97,
   critical_pct=1.0, high_pct=0.0, unique_patients=1, unique_pharmacies=1)

# Edge 3: Many findings, zero exposure
ec("EDGE: 50 Findings — $0 Exposure",
   "High finding count but no quantifiable financial exposure. Data quality issue.",
   status="in_progress", priority="high", case_type="data_quality",
   days_ago=10, finding_count=50, exposure=0, risk_score=0.61,
   critical_pct=0, high_pct=0.4, unique_patients=25, unique_pharmacies=3)

# Edge 4: All findings critical
ec("EDGE: 100% Critical Findings",
   "Every single finding is critical severity. Maximum risk profile.",
   status="escalated", priority="critical", case_type="targeted_investigation",
   days_ago=7, finding_count=20, exposure=650_000, risk_score=0.98,
   critical_pct=1.0, high_pct=0.0, unique_patients=8, unique_pharmacies=2)

# Edge 5: All findings low severity, but flagged critical priority
ec("EDGE: All Low Severity — Critical Priority Label",
   "Priority was manually set critical but all findings are low severity. Conflicting signals.",
   status="pending_review", priority="critical", case_type="routine_audit",
   days_ago=3, finding_count=30, exposure=5_000, risk_score=0.25,
   critical_pct=0.0, high_pct=0.0)

# Edge 6: Very old case, never closed
ec("EDGE: 365-Day-Old Open Case",
   "Investigation case that has been open for a full year without resolution.",
   status="open", priority="medium", case_type="routine_audit",
   days_ago=365, finding_count=8, exposure=22_000, risk_score=0.42)

# Edge 7: Brand new case (minutes old)
ec("EDGE: Freshly Opened Case (Minutes Old)",
   "Case opened moments ago. Tests dashboard real-time refresh.",
   status="open", priority="high", case_type="targeted_investigation",
   days_ago=0, finding_count=0, exposure=0, risk_score=0.0,
   has_snapshot=False)

# Edge 8: No risk snapshot
ec("EDGE: No Risk Snapshot",
   "Case exists in investigation_cases but has no corresponding risk snapshot.",
   status="open", priority="medium", case_type="routine_audit",
   days_ago=1, finding_count=5, exposure=15_000, risk_score=0.35,
   has_snapshot=False)

# Edge 9: Risk score/priority mismatch — high score, low priority
ec("EDGE: High Risk Score (0.88) — Low Priority Label",
   "Composite risk score is 0.88 (critical range) but case is labeled low priority. Mismatch test.",
   status="open", priority="low", case_type="data_quality",
   days_ago=4, finding_count=12, exposure=180_000, risk_score=0.88,
   critical_pct=0.5, high_pct=0.3)

# Edge 10: On-hold with critical findings
ec("EDGE: On-Hold With Critical Findings",
   "Case put on hold despite having 8 critical findings. Should surface in monitoring.",
   status="on_hold", priority="critical", case_type="targeted_investigation",
   days_ago=20, finding_count=15, exposure=320_000, risk_score=0.91,
   critical_pct=0.53, high_pct=0.3)

# Edge 11: Dismissed with high exposure
ec("EDGE: Dismissed — $400K Exposure",
   "Case dismissed despite $400K financial exposure estimate. Tests dismissed case filtering.",
   status="dismissed", priority="high", case_type="self_disclosure",
   days_ago=60, finding_count=18, exposure=400_000, risk_score=0.72)

# Edge 12: Self-disclosure, zero exposure
ec("EDGE: Self-Disclosure — $0 Exposure",
   "Self-disclosure filed proactively. No quantifiable exposure. Regulatory inquiry pending.",
   status="pending_review", priority="low", case_type="self_disclosure",
   days_ago=1, finding_count=2, exposure=0, risk_score=0.18,
   unique_patients=0, unique_pharmacies=1)

# Edge 13: Maximum finding count
ec("EDGE: Maximum Findings (100)",
   "Stress test — 100 findings attached to a single case. Tests pagination and performance.",
   status="in_progress", priority="critical", case_type="targeted_investigation",
   days_ago=30, finding_count=100, exposure=1_250_000, risk_score=0.95,
   critical_pct=0.25, high_pct=0.35, unique_patients=45, unique_pharmacies=8,
   ndc_count=10)

# Edge 14: Many unique pharmacies
ec("EDGE: 15 Unique Contract Pharmacies",
   "Single case spanning 15 contract pharmacy locations. Tests graph intelligence layer.",
   status="escalated", priority="critical", case_type="targeted_investigation",
   days_ago=12, finding_count=45, exposure=890_000, risk_score=0.93,
   critical_pct=0.2, high_pct=0.4, unique_patients=30, unique_pharmacies=15)

# Edge 15: Many unique patients
ec("EDGE: 200 Unique Patients",
   "Single case affecting 200 unique patients. High patient impact score.",
   status="escalated", priority="critical", case_type="targeted_investigation",
   days_ago=8, finding_count=60, exposure=760_000, risk_score=0.94,
   critical_pct=0.3, high_pct=0.35, unique_patients=200, unique_pharmacies=4)

# Edge 16: Unassigned + critical
ec("EDGE: Unassigned Critical Case",
   "Critical priority case with no analyst assigned. Should trigger assignment alert.",
   status="open", priority="critical", case_type="targeted_investigation",
   days_ago=3, finding_count=22, exposure=490_000, risk_score=0.89,
   critical_pct=0.4, assigned_to=None)

# Edge 17: Regulatory inquiry — HRSA audit response
ec("EDGE: Active HRSA Audit Response",
   "HRSA OPA formal audit response required within 30 days. Regulatory deadline active.",
   status="in_progress", priority="critical", case_type="regulatory_inquiry",
   days_ago=5, finding_count=6, exposure=0, risk_score=0.78,
   critical_pct=0, high_pct=0.5)

# Edge 18: Closed case — reference only
ec("EDGE: Recently Closed Critical Case",
   "Critical case closed 2 days ago. Tests that closed cases are excluded from active queue.",
   status="closed", priority="critical", case_type="targeted_investigation",
   days_ago=2, finding_count=25, exposure=530_000, risk_score=0.92,
   critical_pct=0.4, high_pct=0.3)

# Edge 19: Minimum viable risk score (just above 0)
ec("EDGE: Risk Score 0.01 — Near Zero",
   "Case with near-zero risk score. Tests lower boundary of risk level classification.",
   status="open", priority="low", case_type="data_quality",
   days_ago=1, finding_count=1, exposure=50, risk_score=0.01,
   critical_pct=0, high_pct=0)

# Edge 20: Risk score exactly at boundary (0.8 — critical threshold)
ec("EDGE: Risk Score 0.80 — Critical Boundary",
   "Risk score exactly at 0.80 critical threshold. Tests boundary condition in risk classification.",
   status="open", priority="high", case_type="routine_audit",
   days_ago=2, finding_count=14, exposure=95_000, risk_score=0.8000,
   critical_pct=0.1, high_pct=0.4)

# Edge 21: Risk score exactly 0.60 (high boundary)
ec("EDGE: Risk Score 0.60 — High Boundary",
   "Risk score exactly at 0.60 high threshold. Tests high/medium boundary.",
   status="open", priority="medium", case_type="routine_audit",
   days_ago=2, finding_count=10, exposure=45_000, risk_score=0.6000,
   critical_pct=0, high_pct=0.3)

# Edge 22: Risk score exactly 0.30 (medium boundary)
ec("EDGE: Risk Score 0.30 — Medium Boundary",
   "Risk score exactly at 0.30 medium threshold. Tests medium/low boundary.",
   status="open", priority="low", case_type="routine_audit",
   days_ago=2, finding_count=5, exposure=8_000, risk_score=0.3000,
   critical_pct=0, high_pct=0)

# Edge 23: Split billing edge case
ec("EDGE: Split Billing — Same-Day Dual Claims",
   "Same patient, same day, both 340B and WAC claims submitted. Classic split billing pattern.",
   status="in_progress", priority="high", case_type="targeted_investigation",
   days_ago=6, finding_count=16, exposure=87_000, risk_score=0.69,
   critical_pct=0, high_pct=0.6, unique_patients=5, unique_pharmacies=2)

# Edge 24: Data quality — NDC mismatch only
ec("EDGE: Data Quality — NDC Mismatch Only (No Real Violations)",
   "All findings are DQ-001 (NDC mismatch). No actual 340B violations confirmed.",
   status="pending_review", priority="low", case_type="data_quality",
   days_ago=3, finding_count=28, exposure=0, risk_score=0.28,
   critical_pct=0, high_pct=0, unique_patients=0, unique_pharmacies=0)

# Edge 25: Long-running in_progress
ec("EDGE: 90-Day In-Progress — Stalled Investigation",
   "Investigation has been in_progress for 90 days. Tests stall detection in monitoring.",
   status="in_progress", priority="high", case_type="targeted_investigation",
   days_ago=90, finding_count=19, exposure=145_000, risk_score=0.73,
   critical_pct=0.05, high_pct=0.5)

print(f"Systematic cases: {len(SYSTEMATIC)}")
print(f"Edge cases:       {len(EDGE_CASES)}")
print(f"Total:            {len(SYSTEMATIC) + len(EDGE_CASES)}")


# ── Insert ────────────────────────────────────────────────────────────────────

def insert_case(db, record: dict) -> bool:
    c = record["case"]
    r = record.get("risk")

    result = db.execute(text("""
        INSERT INTO audit.investigation_cases
            (case_id, case_number, covered_entity_id, case_type,
             status, priority, title, description, assigned_to,
             opened_at, finding_count, financial_exposure_estimate)
        VALUES
            (CAST(:case_id AS uuid), :case_number, CAST(:covered_entity_id AS uuid), :case_type,
             :status, :priority, :title, :description, :assigned_to,
             CAST(:opened_at AS timestamptz), :finding_count, :financial_exposure_estimate)
        ON CONFLICT (case_number) DO NOTHING
        RETURNING case_id
    """), c)

    if result.rowcount == 0:
        return False   # already exists

    if r:
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
            **{k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
               for k, v in r.items()},
        })
    return True


def run():
    wipe = "--wipe" in sys.argv

    db = SessionLocal()
    try:
        if wipe:
            confirm = input("Delete ALL investigation_cases and risk_snapshots? (yes/no): ")
            if confirm.strip().lower() == "yes":
                db.execute(text("DELETE FROM audit.case_risk_snapshots"))
                db.execute(text("DELETE FROM audit.investigation_cases"))
                db.commit()
                print("Wiped existing data.\n")
            else:
                print("Aborted.")
                return

        inserted = skipped = 0
        all_cases = SYSTEMATIC + EDGE_CASES

        print(f"\nInserting {len(all_cases)} cases...\n")

        # Print header
        print(f"  {'#':<6} {'Case Number':<18} {'Status':<15} {'Priority':<10} {'Risk':>6}  Title")
        print("  " + "─" * 90)

        for record in all_cases:
            ok = insert_case(db, record)
            c  = record["case"]
            r  = record.get("risk") or {}
            if ok:
                inserted += 1
                score_str = f"{r.get('composite_risk_score', 0):.2f}" if r else "  N/A"
                title_short = c["title"][:45]
                print(f"  {'✓':<6} {c['case_number']:<18} {c['status']:<15} {c['priority']:<10} {score_str:>6}  {title_short}")
            else:
                skipped += 1

        db.commit()
        print(f"\n{'─'*95}")
        print(f"Inserted: {inserted}  |  Skipped (already exist): {skipped}")
        print(f"Total cases in DB: {db.execute(text('SELECT COUNT(*) FROM audit.investigation_cases')).scalar()}")

        # ── Summary by status ──────────────────────────────────────────────
        print("\nDistribution by status:")
        rows = db.execute(text("""
            SELECT status, COUNT(*) as n
            FROM audit.investigation_cases
            GROUP BY status ORDER BY n DESC
        """)).fetchall()
        for row in rows:
            print(f"  {row[0]:<20} {row[1]}")

        print("\nDistribution by priority:")
        rows = db.execute(text("""
            SELECT priority, COUNT(*) as n
            FROM audit.investigation_cases
            GROUP BY priority ORDER BY n DESC
        """)).fetchall()
        for row in rows:
            print(f"  {row[0]:<20} {row[1]}")

    except Exception as e:
        db.rollback()
        print(f"\nERROR: {e}", file=sys.stderr)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()
