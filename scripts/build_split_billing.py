"""
Build ops.split_billing from ops.dispenses + ops.claims + ops.purchases.

This script is the ETL bridge between raw ops tables and the split_billing
table that the rules engine reads. In production this would run as a
scheduled job after each ingestion batch.

Matching logic:
  1. Primary join: dispense ↔ claim on (covered_entity_id, ndc_11,
     patient_id_hash, dispense_date = service_date).
  2. Secondary: for MEO-001 records where claims may lack patient_id_hash,
     fall back to (covered_entity_id, ndc_11, service_date) with DISTINCT.
  3. Purchase linkage: nearest prior purchase for same (covered_entity_id, ndc_11).

Risk flags:
  - duplicate_discount_risk:  is_340b_dispense AND is_medicaid
  - medicaid_overlap_risk:    is_340b_dispense AND is_medicaid
  - carve_out_violation_risk: is_340b_dispense AND carve_in_election='carve_out'
  - ineligible_patient_risk:  FALSE (would require patient eligibility DB)

Run:
  .venv/bin/python3 scripts/build_split_billing.py [--wipe]
"""
from __future__ import annotations

import sys
import uuid
from datetime import UTC, datetime

sys.path.insert(0, ".")

from sqlalchemy import text

from app.database import SessionLocal

COVERED_ENTITY_ID = "f921a958-8613-4a88-a233-e1c9bdd79be4"


def wipe_split_billing(session):
    session.execute(text("DELETE FROM ops.split_billing"))
    session.commit()
    print("  Wiped ops.split_billing")


def build(session) -> int:
    now = datetime.now(tz=UTC)

    # ── Step 1: Load purchases indexed by (covered_entity_id, ndc_11) ─────────
    print("  Loading purchases...")
    purchases_raw = session.execute(text("""
        SELECT purchase_id, covered_entity_id, ndc_11, purchase_date,
               is_340b_purchase, unit_price
        FROM ops.purchases
        ORDER BY purchase_date ASC
    """)).fetchall()

    # Index: (ce_id, ndc) → sorted list of purchases
    from collections import defaultdict
    purch_index: dict[tuple, list] = defaultdict(list)
    for p in purchases_raw:
        purch_index[(str(p.covered_entity_id), p.ndc_11)].append(p)

    # ── Step 2: Join dispenses + claims ───────────────────────────────────────
    print("  Joining dispenses + claims...")
    rows = session.execute(text("""
        SELECT
            d.dispense_id,
            d.covered_entity_id,
            d.ndc_11                    AS dispense_ndc,
            d.patient_id_hash,
            d.dispense_date,
            d.is_340b_dispense,
            d.payer_type,
            d.carve_in_election,
            d.batch_id,

            c.claim_id,
            c.service_date,
            c.ndc_11                    AS claim_ndc,
            c.is_medicaid,
            c.is_340b_billed,
            c.billed_amount
        FROM ops.dispenses d
        JOIN ops.claims c
            ON  c.covered_entity_id = d.covered_entity_id
            AND c.ndc_11            = d.ndc_11
            AND c.patient_id_hash   = d.patient_id_hash
            AND c.service_date      = d.dispense_date
        WHERE d.covered_entity_id = CAST(:ce AS uuid)
        ORDER BY d.dispense_date, d.dispense_id
    """), {"ce": COVERED_ENTITY_ID}).fetchall()

    print(f"  Found {len(rows)} dispense+claim pairs")

    # ── Step 3: Build split_billing records ───────────────────────────────────
    records = []
    seen: set[tuple] = set()  # dedup on (dispense_id, claim_id)

    for row in rows:
        key = (str(row.dispense_id), str(row.claim_id))
        if key in seen:
            continue
        seen.add(key)

        # Find nearest prior purchase (same CE + NDC, date <= dispense_date)
        purch_list = purch_index.get(
            (str(row.covered_entity_id), row.dispense_ndc), []
        )
        purchase_id = None
        purchase_date = None
        is_340b_purchase = False

        for p in reversed(purch_list):  # sorted ASC, reversed → most recent first
            if p.purchase_date <= row.dispense_date:
                purchase_id = p.purchase_id
                purchase_date = p.purchase_date
                is_340b_purchase = bool(p.is_340b_purchase)
                break

        # Risk flags
        is_340b    = bool(row.is_340b_dispense)
        is_medicaid = bool(row.is_medicaid)

        # NDC mismatch (DQ-001): dispense_ndc != claim_ndc
        ndc_mismatch = (row.dispense_ndc != row.claim_ndc)

        duplicate_discount_risk  = is_340b and is_medicaid
        medicaid_overlap_risk    = is_340b and is_medicaid
        carve_out_violation_risk = (
            is_340b and row.carve_in_election == "carve_out"
        )
        ineligible_patient_risk  = False  # requires patient eligibility data

        # Simple risk score (0.0–1.0)
        risk_score = round(
            (0.4 if duplicate_discount_risk else 0.0)
            + (0.3 if medicaid_overlap_risk and not duplicate_discount_risk else 0.0)
            + (0.2 if carve_out_violation_risk else 0.0)
            + (0.1 if ndc_mismatch else 0.0),
            2,
        )

        records.append({
            "split_billing_id":        str(uuid.uuid4()),
            "covered_entity_id":       str(row.covered_entity_id),
            "ndc_11":                  row.dispense_ndc,
            "service_date":            row.service_date.isoformat(),
            "patient_id_hash":         row.patient_id_hash,
            "purchase_id":             str(purchase_id) if purchase_id else None,
            "purchase_date":           purchase_date.isoformat() if purchase_date else None,
            "dispense_id":             str(row.dispense_id),
            "dispense_date":           row.dispense_date.isoformat(),
            "claim_id":                str(row.claim_id),
            "claim_service_date":      row.service_date.isoformat(),
            "split_method":            "virtual_inventory",
            "carve_in_flag":           row.carve_in_election == "carve_in",
            "is_340b_purchase":        is_340b_purchase,
            "is_medicaid_billed":      is_medicaid,
            "accumulator_balance":     None,
            "duplicate_discount_risk": duplicate_discount_risk,
            "medicaid_overlap_risk":   medicaid_overlap_risk,
            "carve_out_violation_risk": carve_out_violation_risk,
            "ineligible_patient_risk": ineligible_patient_risk,
            "risk_score":              risk_score,
            "source_file":             "build_split_billing.py",
            "batch_id":                str(row.batch_id) if row.batch_id else None,
            "created_at":              now.isoformat(),
            "updated_at":              now.isoformat(),
        })

    # ── Step 4: Bulk insert ───────────────────────────────────────────────────
    print(f"  Inserting {len(records)} split_billing records...")
    CHUNK = 500
    for i in range(0, len(records), CHUNK):
        chunk = records[i : i + CHUNK]
        session.execute(
            text("""
                INSERT INTO ops.split_billing (
                    split_billing_id, covered_entity_id, ndc_11, service_date,
                    patient_id_hash, purchase_id, purchase_date,
                    dispense_id, dispense_date, claim_id, claim_service_date,
                    split_method, carve_in_flag, is_340b_purchase, is_medicaid_billed,
                    accumulator_balance, duplicate_discount_risk, medicaid_overlap_risk,
                    carve_out_violation_risk, ineligible_patient_risk, risk_score,
                    source_file, batch_id, created_at, updated_at
                ) VALUES (
                    CAST(:split_billing_id AS uuid),
                    CAST(:covered_entity_id AS uuid),
                    :ndc_11, CAST(:service_date AS date),
                    :patient_id_hash,
                    CAST(:purchase_id AS uuid),
                    CAST(:purchase_date AS date),
                    CAST(:dispense_id AS uuid),
                    CAST(:dispense_date AS date),
                    CAST(:claim_id AS uuid),
                    CAST(:claim_service_date AS date),
                    :split_method, :carve_in_flag, :is_340b_purchase, :is_medicaid_billed,
                    :accumulator_balance,
                    :duplicate_discount_risk, :medicaid_overlap_risk,
                    :carve_out_violation_risk, :ineligible_patient_risk,
                    :risk_score, :source_file,
                    CAST(:batch_id AS uuid),
                    CAST(:created_at AS timestamptz),
                    CAST(:updated_at AS timestamptz)
                )
                ON CONFLICT DO NOTHING
            """),
            chunk,
        )
        session.commit()
        print(f"    {min(i + CHUNK, len(records))}/{len(records)}")

    return len(records)


if __name__ == "__main__":
    wipe = "--wipe" in sys.argv

    print("Building ops.split_billing...")
    db = SessionLocal()
    try:
        if wipe:
            wipe_split_billing(db)

        inserted = build(db)

        # Verify
        total = db.execute(
            text("SELECT COUNT(*) FROM ops.split_billing")
        ).scalar()
        dd_risk = db.execute(
            text("SELECT COUNT(*) FROM ops.split_billing WHERE duplicate_discount_risk")
        ).scalar()
        meo_risk = db.execute(
            text("SELECT COUNT(*) FROM ops.split_billing WHERE medicaid_overlap_risk AND NOT duplicate_discount_risk")
        ).scalar()
        carve_out = db.execute(
            text("SELECT COUNT(*) FROM ops.split_billing WHERE carve_out_violation_risk")
        ).scalar()

        print()
        print("──────────────────────────────────────────────────")
        print(f"ops.split_billing total:         {total}")
        print(f"  duplicate_discount_risk (DD):  {dd_risk}")
        print(f"  medicaid_overlap_risk (MEO):   {meo_risk}")
        print(f"  carve_out_violation_risk (SB): {carve_out}")
        print()
        print("Ready for rules engine.")
    finally:
        db.close()
