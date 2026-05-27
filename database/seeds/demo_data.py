"""
EvidentRx Demo Seed Script
==========================
Loads a complete, realistic demo dataset so the platform is immediately
explorable after ``docker compose up`` — no manual API calls required.

What gets created
-----------------
  Covered entities (tenants)    2
  Users                         5   (admin / auditor / senior_analyst / analyst × 2)
  Investigation cases           12  (mix of statuses, priorities, types)
  Audit findings                12  (attached to cases)
  Agent runs                     8
  Entity risk scores            42  (21 days × 2 entities)
  Compliance trends              6  (3 rule codes × 2 entities)

Demo credentials  (password for all accounts: ``EvidentRx2024!``)
-----------------------------------------------------------------
  Email                           Role
  ─────────────────────────────── ──────────────
  admin@demo-hospital.org         admin
  auditor@demo-hospital.org       auditor
  senior@demo-hospital.org        senior_analyst
  analyst@demo-hospital.org       analyst
  analyst2@demo-hospital.org      analyst

Usage
-----
  python -m database.seeds.demo_data          # from project root
  python database/seeds/demo_data.py          # same
  docker compose run --rm api seed-demo       # via entrypoint
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import asyncpg

log = logging.getLogger("evidentrx.seed")
logging.basicConfig(level=logging.INFO, format="[seed] %(message)s")

# ── Connection ────────────────────────────────────────────────────────────────

DATABASE_URL = (
    os.environ.get("DATABASE_URL", "postgresql://evidentrx:evidentrx@localhost:5432/evidentrx")
    .replace("postgresql+asyncpg://", "postgresql://")
    .replace("postgresql+psycopg2://", "postgresql://")
)

DEMO_PASSWORD = "EvidentRx2024!"


# ── Helpers ───────────────────────────────────────────────────────────────────

def uid() -> str:
    return str(uuid.uuid4())


def now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def ago(days: float) -> datetime:
    return now_utc() - timedelta(days=days)


def ago_date(days: int) -> date:
    return (now_utc() - timedelta(days=days)).date()


def hash_pw(password: str) -> str:
    try:
        from auth.password import hash_password
        return hash_password(password)
    except Exception:
        from passlib.context import CryptContext
        ctx = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)
        return ctx.hash(password)


# ── Seeder ────────────────────────────────────────────────────────────────────

async def seed(conn: asyncpg.Connection) -> None:
    log.info("Starting demo seed…")

    # ─────────────────────────────────────────────────────────────────────────
    # 1.  Covered entities (tenants)
    # ─────────────────────────────────────────────────────────────────────────
    log.info("Step 1/7 — covered entities")

    ce_id   = uid()   # General Memorial Hospital  (primary demo tenant)
    ce_id_2 = uid()   # Riverside Community HC     (secondary)

    await conn.execute("""
        INSERT INTO ref.covered_entities (
            ce_id, hrsa_id, entity_name, entity_type_code, entity_type_description,
            street_address, city, state_code, zip_code, npi,
            program_participation_start, program_status,
            is_current, is_active, valid_from, created_at, updated_at
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,'Active',TRUE,TRUE,$12,NOW(),NOW())
        ON CONFLICT (ce_id) DO NOTHING
    """,
        ce_id, "CE-99001", "General Memorial Hospital", "DSH",
        "Disproportionate Share Hospital",
        "1000 Medical Center Dr", "Springfield", "IL", "62701", "1234567890",
        ago_date(1095), ago(1095),
    )

    await conn.execute("""
        INSERT INTO ref.covered_entities (
            ce_id, hrsa_id, entity_name, entity_type_code, entity_type_description,
            street_address, city, state_code, zip_code, npi,
            program_participation_start, program_status,
            is_current, is_active, valid_from, created_at, updated_at
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,'Active',TRUE,TRUE,$12,NOW(),NOW())
        ON CONFLICT (ce_id) DO NOTHING
    """,
        ce_id_2, "CE-99002", "Riverside Community Health Center", "FQHC",
        "Federally Qualified Health Center",
        "250 River Road", "Chicago", "IL", "60601", "0987654321",
        ago_date(730), ago(730),
    )

    # ─────────────────────────────────────────────────────────────────────────
    # 2.  Users
    # ─────────────────────────────────────────────────────────────────────────
    log.info("Step 2/7 — users (bcrypt hashing — ~5 seconds)…")

    pw_hash = hash_pw(DEMO_PASSWORD)

    admin_id    = uid()
    auditor_id  = uid()
    senior_id   = uid()
    analyst_id  = uid()
    analyst2_id = uid()

    users = [
        (admin_id,    "admin@demo-hospital.org",   "Alex Admin",     "admin",          ce_id),
        (auditor_id,  "auditor@demo-hospital.org", "Dana Auditor",   "auditor",        ce_id),
        (senior_id,   "senior@demo-hospital.org",  "Sam Senior",     "senior_analyst", ce_id),
        (analyst_id,  "analyst@demo-hospital.org", "Alex Analyst",   "analyst",        ce_id),
        (analyst2_id, "analyst2@demo-hospital.org","Jordan Analyst",  "analyst",        ce_id),
    ]

    for u_id, email, name, role, tid in users:
        await conn.execute("""
            INSERT INTO auth.users (
                user_id, email, full_name, hashed_password, role,
                tenant_id, is_active, is_verified, created_at, updated_at
            ) VALUES ($1,$2,$3,$4,$5,$6,TRUE,TRUE,NOW(),NOW())
            ON CONFLICT (email, tenant_id) DO NOTHING
        """, u_id, email, name, pw_hash, role, tid)

    log.info("  Created 5 users")

    # ─────────────────────────────────────────────────────────────────────────
    # 3.  Investigation cases
    # ─────────────────────────────────────────────────────────────────────────
    log.info("Step 3/7 — investigation cases")

    # (number, type, status, priority, title, exposure_usd, opened_days_ago)
    case_specs = [
        ("INV-2025-00001", "targeted_investigation", "escalated",     "critical",
         "Duplicate Discount Pattern — Insulin Products Q1 2025",         185_400, 45),
        ("INV-2025-00002", "routine_audit",          "in_progress",   "high",
         "Medicaid Overlap Analysis — Cardiovascular Medications",          92_750, 38),
        ("INV-2025-00003", "targeted_investigation", "pending_review", "critical",
         "Contract Pharmacy Eligibility Failure — CVS Network",            241_800, 30),
        ("INV-2025-00004", "routine_audit",          "open",          "high",
         "Split Billing Inconsistencies — Oncology Department",             67_300, 22),
        ("INV-2025-00005", "routine_audit",          "in_progress",   "medium",
         "Carve-In / Carve-Out Compliance Review — Medicaid MCO",           31_200, 18),
        ("INV-2025-00006", "data_quality",           "open",          "medium",
         "NDC Data Quality — Dispense Records Validation",                   8_900, 15),
        ("INV-2025-00007", "targeted_investigation", "escalated",     "critical",
         "Retroactive Medicaid Exclusion Violation — Q4 2024",             312_500, 60),
        ("INV-2025-00008", "routine_audit",          "closed",        "low",
         "Annual 340B Eligibility Verification — Contract Pharmacies",       2_100, 90),
        ("INV-2025-00009", "self_disclosure",        "pending_review", "high",
         "Self-Disclosed Diversion Event — Outpatient Pharmacy",            48_600, 12),
        ("INV-2025-00010", "regulatory_inquiry",     "in_progress",   "critical",
         "HRSA Program Integrity Review Response — FY 2025",               428_000,  8),
        ("INV-2025-00011", "routine_audit",          "open",          "medium",
         "Patient Eligibility Recertification — Rural Health Clinics",      14_700,  5),
        ("INV-2025-00012", "data_quality",           "open",          "low",
         "Ingestion Reconciliation — Claims Data Gap February 2025",         1_800,  3),
    ]

    case_ids: list[str] = []
    for (num, ctype, status, priority, title, exposure, days) in case_specs:
        c_id = uid()
        case_ids.append(c_id)
        closed_at = ago(10) if status == "closed" else None
        await conn.execute("""
            INSERT INTO audit.investigation_cases (
                case_id, case_number, covered_entity_id, case_type,
                status, priority, title, assigned_to,
                financial_exposure_estimate,
                finding_count, opened_at, closed_at, created_at, updated_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,0,$10,$11,NOW(),NOW())
            ON CONFLICT (case_number) DO NOTHING
        """,
            c_id, num, ce_id, ctype,
            status, priority, title, analyst_id,
            Decimal(exposure),
            ago(days), closed_at,
        )

    log.info("  Created %d cases", len(case_ids))

    # ─────────────────────────────────────────────────────────────────────────
    # 4.  Audit findings  (matched to actual schema columns)
    # ─────────────────────────────────────────────────────────────────────────
    log.info("Step 4/7 — audit findings")

    # Fetch live rule metadata so we get the right rule_id + rule_version
    rules = {
        r["rule_code"]: {"rule_id": str(r["rule_id"]), "rule_version": r["rule_version"]}
        for r in await conn.fetch("""
            SELECT rule_id, rule_code, rule_version
            FROM audit.compliance_rules
            WHERE rule_code IN ('DD-001','DD-002','MEO-001','MEO-002',
                                'CPE-001','CPE-003','SB-001','DQ-001')
              AND is_active = TRUE
        """)
    }

    if not rules:
        log.warning("  No compliance rules found — run compliance_rules.sql seed first")

    # (case_idx, rule_code, severity, status, exposure, description)
    finding_specs = [
        (0, "DD-001", "critical", "confirmed", 24_600,
         "Insulin glargine (NDC 00088-2220-33) dispensed at 340B price on 2025-01-14 "
         "with concurrent Medicaid claim for same patient on same date. "
         "Patient ID hash: 7f3a9c2e. Duplicate discount confirmed."),
        (0, "DD-001", "critical", "confirmed", 18_900,
         "Metformin ER 500mg — 340B dispense matches Medicaid FFS claim within 0-day "
         "tolerance for patient hash d4b21f7a. Third occurrence this quarter."),
        (0, "DD-002", "high", "open", 9_200,
         "Potential duplicate discount via managed care carve-in arrangement. "
         "Claim submitted to Medicaid MCO while 340B pricing applied. "
         "Requires payer confirmation."),
        (1, "MEO-001", "critical", "confirmed", 31_400,
         "Patient listed on Illinois Medicaid exclusion file (effective 2024-10-01) "
         "received 340B-priced lisinopril dispenses for 3 subsequent months."),
        (1, "MEO-002", "high", "open", 14_800,
         "Medicaid exclusion status change not reflected in eligibility check. "
         "12 dispenses across 4 NDCs during exclusion window."),
        (2, "CPE-001", "critical", "confirmed", 88_500,
         "Contract pharmacy (CVS #4821, NPI 1982736450) failed HRSA proximity "
         "waiver requirement. 340B dispenses for 214 patients during non-eligible period."),
        (2, "CPE-001", "critical", "confirmed", 76_300,
         "CVS #5103 — ownership change not reported to HRSA within 30-day window. "
         "Contract pharmacy status lapsed. 340B pricing applied during gap."),
        (2, "CPE-003", "high", "open", 22_100,
         "Split billing arrangement at CVS #4821 lacks required written agreement."),
        (3, "SB-001", "high", "open", 28_700,
         "Split billing records for oncology infusion center show misaligned "
         "inventory allocation across 340B and WAC purchase pools."),
        (3, "SB-001", "medium", "open", 9_400,
         "Bevacizumab inventory reconciliation gap — 340B pool shows negative "
         "balance on 3 dates. Possible mixed-pool dispense without tracking."),
        (6, "MEO-001", "critical", "confirmed", 142_000,
         "Retroactive Medicaid exclusion covers 847 dispenses across Q4 2024. "
         "Full repayment liability confirmed."),
        (6, "DD-001", "critical", "confirmed", 98_500,
         "Q4 2024 Medicaid FFS cross-reference confirms 312 duplicate discount "
         "transactions in oncology during exclusion window."),
    ]

    finding_counts: dict[int, int] = {}
    for (c_idx, rule_code, severity, f_status, exposure, description) in finding_specs:
        if c_idx >= len(case_ids) or rule_code not in rules:
            continue

        r        = rules[rule_code]
        f_id     = uid()
        fc       = f"{rule_code}-{uid()[:8].upper()}"
        cat      = rule_code.split("-")[0].lower()  # dd | meo | cpe | sb | dq

        await conn.execute("""
            INSERT INTO audit.audit_findings (
                finding_id, finding_code,
                rule_id, rule_code, rule_version,
                covered_entity_id, investigation_case_id,
                finding_type, severity, status,
                description, financial_exposure,
                confidence_score, evidence_payload,
                detected_at, created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5,
                $6, $7,
                $8, $9, $10,
                $11, $12,
                1.0, $13,
                $14, NOW(), NOW()
            )
            ON CONFLICT DO NOTHING
        """,
            f_id, fc,
            r["rule_id"], rule_code, r["rule_version"],
            ce_id, case_ids[c_idx],
            cat, severity, f_status,
            description, Decimal(exposure),
            '{"source": "rules_engine", "version": "3.0.0", "deterministic": true}',
            ago(30 + c_idx * 3),
        )
        finding_counts[c_idx] = finding_counts.get(c_idx, 0) + 1

    # Sync denormalized counter
    for c_idx, count in finding_counts.items():
        await conn.execute("""
            UPDATE audit.investigation_cases
            SET finding_count = $1, updated_at = NOW()
            WHERE case_id = $2
        """, count, case_ids[c_idx])

    log.info("  Created %d findings", sum(finding_counts.values()))

    # ─────────────────────────────────────────────────────────────────────────
    # 5.  Agent runs
    # ─────────────────────────────────────────────────────────────────────────
    log.info("Step 5/7 — agent runs")

    agent_specs = [
        (0, "evidence_analysis",   "completed", 42_000, "claude-sonnet-4-5"),
        (0, "pattern_analysis",    "completed", 38_000, "claude-sonnet-4-5"),
        (1, "evidence_analysis",   "completed", 31_000, "claude-sonnet-4-5"),
        (2, "risk_prioritization", "completed", 18_000, "claude-sonnet-4-5"),
        (2, "pattern_analysis",    "completed", 55_000, "claude-opus-4-5"),
        (3, "evidence_analysis",   "running",       0,  "claude-sonnet-4-5"),
        (6, "narrative_generation","completed", 71_000, "claude-opus-4-5"),
        (9, "evidence_analysis",   "completed", 63_000, "claude-opus-4-5"),
    ]

    for (c_idx, agent_type, ar_status, tokens, model) in agent_specs:
        if c_idx >= len(case_ids):
            continue
        completed_at = ago(20) if ar_status == "completed" else None
        await conn.execute("""
            INSERT INTO audit.agent_runs (
                agent_run_id, case_id, agent_type, agent_name,
                status, model_id,
                token_usage, input_payload,
                started_at, completed_at, created_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,NOW())
            ON CONFLICT DO NOTHING
        """,
            uid(), case_ids[c_idx], agent_type, f"{agent_type}_agent",
            ar_status, model,
            f'{{"total": {tokens}, "input": {int(tokens*0.6)}, "output": {int(tokens*0.4)}}}',
            "{}",
            ago(20), completed_at,
        )

    log.info("  Created %d agent runs", len(agent_specs))

    # ─────────────────────────────────────────────────────────────────────────
    # 6.  Entity risk scores  (21 days × 2 entities)
    #     composite_score is NUMERIC(5,4) → range 0.0000–0.9999
    # ─────────────────────────────────────────────────────────────────────────
    log.info("Step 6/7 — entity risk scores (21 days × 2 entities)")

    import random
    random.seed(42)

    entity_configs = [
        (ce_id,   "covered_entity", 0.72),   # GMH — elevated risk
        (ce_id_2, "covered_entity", 0.41),   # Riverside — lower risk
    ]

    for (eid, etype, base) in entity_configs:
        for day_offset in range(21, 0, -1):
            score_date  = ago_date(day_offset)
            composite   = max(0.0, min(0.9999, base + random.uniform(-0.08, 0.08)))
            velocity    = round(random.uniform(-0.025, 0.025), 4)
            exposure    = round(composite * 500_000, 2)
            escalation  = round(composite * 0.65, 4)
            direction   = "increasing" if velocity > 0.005 else (
                          "decreasing" if velocity < -0.005 else "stable")

            await conn.execute("""
                INSERT INTO audit.entity_risk_scores (
                    score_id, entity_id, entity_type, score_date,
                    composite_score, finding_velocity,
                    exposure_trajectory, escalation_probability,
                    trend_direction,
                    score_components, computed_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,NOW())
                ON CONFLICT (entity_id, entity_type, score_date) DO NOTHING
            """,
                uid(), eid, etype, score_date,
                round(composite, 4), velocity,
                Decimal(str(exposure)), round(escalation, 4),
                direction,
                '{"rules_engine": 0.6, "historical": 0.25, "exposure": 0.15}',
            )

    log.info("  Created risk score history")

    # ─────────────────────────────────────────────────────────────────────────
    # 7.  Compliance trends
    #     Schema: entity_id, entity_type, rule_code, window_type, window_start,
    #             window_end, finding_count, ..., computed_at
    # ─────────────────────────────────────────────────────────────────────────
    log.info("Step 7/7 — compliance trends")

    trend_rule_codes = ["DD-001", "MEO-001", "CPE-001"]
    window_end   = ago_date(0)
    window_start = ago_date(30)

    for rule_code in trend_rule_codes:
        if rule_code not in rules:
            continue
        for (eid, etype, base) in entity_configs:
            finding_cnt = random.randint(3, 18)
            exposure    = Decimal(str(round(finding_cnt * random.uniform(8_000, 35_000), 2)))
            risk        = round(base + random.uniform(-0.1, 0.1), 4)
            direction   = random.choice(["increasing", "stable", "decreasing"])

            await conn.execute("""
                INSERT INTO audit.compliance_trends (
                    trend_id, entity_id, entity_type, rule_code,
                    window_type, window_start, window_end,
                    finding_count, financial_exposure,
                    risk_score, trend_direction, computed_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,NOW())
                ON CONFLICT (entity_id, entity_type, rule_code, window_type, window_start)
                DO NOTHING
            """,
                uid(), eid, etype, rule_code,
                "rolling_30d", window_start, window_end,
                finding_cnt, exposure,
                round(max(0.0, min(0.9999, risk)), 4), direction,
            )

    log.info("  Created compliance trends")

    # ─────────────────────────────────────────────────────────────────────────
    # Summary
    # ─────────────────────────────────────────────────────────────────────────
    log.info("")
    log.info("=" * 62)
    log.info("  EvidentRx Demo Seed Complete")
    log.info("=" * 62)
    log.info("")
    log.info("  Demo credentials  (password: EvidentRx2024!)")
    log.info("  ─────────────────────────────────────────────")
    log.info("  admin@demo-hospital.org     →  admin")
    log.info("  auditor@demo-hospital.org   →  auditor")
    log.info("  senior@demo-hospital.org    →  senior_analyst")
    log.info("  analyst@demo-hospital.org   →  analyst")
    log.info("  analyst2@demo-hospital.org  →  analyst")
    log.info("")
    log.info("  Tenant ID  : %s", ce_id)
    log.info("")
    log.info("  API docs   : http://localhost:8000/api/docs")
    log.info("  Frontend   : http://localhost:3000")
    log.info("=" * 62)


# ── Entry point ───────────────────────────────────────────────────────────────

async def main() -> None:
    masked = DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL else DATABASE_URL
    log.info("Connecting to: %s", masked)
    try:
        conn = await asyncpg.connect(DATABASE_URL)
    except Exception as e:
        log.error("Cannot connect to database: %s", e)
        sys.exit(1)
    try:
        await seed(conn)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
