"""
Synthetic 340B claims data generator.

Generates realistic dispenses, claims, and purchases that mirror
what a TPA (Third Party Administrator) would send to a covered entity.

Embeds deliberate violation patterns so the rules engine has
real data to fire on:
  DD-001  Duplicate dispensing        (same patient + NDC + date, 2 pharmacies)
  MEO-001 Medicaid exclusion overlap  (340B dispense + Medicaid claim same day)
  SB-001  Split billing               (340B + WAC billed for same fill)
  DQ-001  NDC mismatch                (brand billed, generic dispensed)
  EE-001  No qualifying encounter     (dispense with no covered entity claim)

Run:
    .venv/bin/python3 scripts/seed_claims_data.py
    .venv/bin/python3 scripts/seed_claims_data.py --wipe
"""
from __future__ import annotations

import hashlib
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import date, timedelta
from uuid import uuid4

from sqlalchemy import text

from app.database import SessionLocal

random.seed(99)

# ── Reference data ────────────────────────────────────────────────────────────

CE_ID = "f921a958-8613-4a88-a233-e1c9bdd79be4"

# Real high-cost 340B drugs (NDC: brand name, WAC price, 340B ceiling price)
DRUGS = [
    {"ndc": "00006302602", "brand": "Keytruda",    "generic": "pembrolizumab",   "wac": 10_680.00, "ceiling": 3_560.00},
    {"ndc": "00074379922", "brand": "Humira",       "generic": "adalimumab",      "wac": 6_922.00,  "ceiling": 2_040.00},
    {"ndc": "59148001962", "brand": "Revlimid",     "generic": "lenalidomide",    "wac": 19_800.00, "ceiling": 6_200.00},
    {"ndc": "00003377211", "brand": "Opdivo",       "generic": "nivolumab",       "wac": 9_480.00,  "ceiling": 3_100.00},
    {"ndc": "50242013468", "brand": "Herceptin",    "generic": "trastuzumab",     "wac": 5_100.00,  "ceiling": 1_650.00},
    {"ndc": "00074058722", "brand": "Imbruvica",    "generic": "ibrutinib",       "wac": 15_600.00, "ceiling": 4_900.00},
    {"ndc": "00310021030", "brand": "Xarelto",      "generic": "rivaroxaban",     "wac": 540.00,    "ceiling": 168.00},
    {"ndc": "00003089311", "brand": "Eliquis",      "generic": "apixaban",        "wac": 510.00,    "ceiling": 158.00},
    {"ndc": "68084019901", "brand": "Abilify",      "generic": "aripiprazole",    "wac": 890.00,    "ceiling": 280.00},
    {"ndc": "00093727498", "brand": "Synthroid",    "generic": "levothyroxine",   "wac": 45.00,     "ceiling": 14.00},
    {"ndc": "16714044201", "brand": "Lisinopril",   "generic": "lisinopril",      "wac": 18.00,     "ceiling": 5.50},
    {"ndc": "68382004916", "brand": "Metformin",    "generic": "metformin",       "wac": 12.00,     "ceiling": 3.75},
]

# Fake pharmacies (NPI + name)
PHARMACIES = [
    {"npi": "1234567890", "name": "CVS Pharmacy #4421"},
    {"npi": "1234567891", "name": "Walgreens #8832"},
    {"npi": "1234567892", "name": "Rite Aid #2291"},
    {"npi": "1234567893", "name": "Walmart Pharmacy #5512"},
    {"npi": "1234567894", "name": "Kroger Pharmacy #771"},
    {"npi": "1234567895", "name": "Costco Pharmacy #221"},
    {"npi": "1234567896", "name": "Target Pharmacy #991"},
    {"npi": "1234567897", "name": "Publix Pharmacy #334"},
]

# Fake prescribers (NPI + specialty)
PRESCRIBERS = [
    {"npi": "9876543210", "specialty": "Oncology"},
    {"npi": "9876543211", "specialty": "Internal Medicine"},
    {"npi": "9876543212", "specialty": "Cardiology"},
    {"npi": "9876543213", "specialty": "Rheumatology"},
    {"npi": "9876543214", "specialty": "Psychiatry"},
    {"npi": "9876543215", "specialty": "Endocrinology"},
    {"npi": "9876543216", "specialty": "Hematology"},
    {"npi": "9876543217", "specialty": "Gastroenterology"},
]

PAYER_TYPES = ["medicaid", "medicare_part_d", "commercial", "self_pay"]
STATES = ["TX", "CA", "FL", "NY", "OH", "PA", "IL", "GA", "NC", "MI"]


def patient_hash(name: str, dob: str) -> str:
    return hashlib.sha256(f"{name}|{dob}".encode()).hexdigest()


def random_date(start_days_ago: int = 90, end_days_ago: int = 1) -> date:
    days = random.randint(end_days_ago, start_days_ago)
    return date.today() - timedelta(days=days)


def ndc_fmt(ndc: str) -> str:
    """Format NDC as 11-digit no-hyphen string."""
    return ndc.replace("-", "").zfill(11)


# ── Generate 200 fake patients ────────────────────────────────────────────────

FIRST_NAMES = ["James","Mary","Robert","Patricia","John","Jennifer","Michael",
               "Linda","William","Barbara","David","Susan","Richard","Jessica",
               "Joseph","Sarah","Thomas","Karen","Charles","Lisa"]
LAST_NAMES  = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller",
               "Davis","Rodriguez","Martinez","Hernandez","Lopez","Gonzalez",
               "Wilson","Anderson","Taylor","Moore","Jackson","Martin","Lee"]

PATIENTS = []
for i in range(200):
    dob = date(random.randint(1940, 2000), random.randint(1, 12), random.randint(1, 28))
    name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
    PATIENTS.append({
        "hash": patient_hash(name, dob.isoformat()),
        "name": name,
        "dob":  dob,
        "state": random.choice(STATES),
    })


# ── Record builders ───────────────────────────────────────────────────────────

def make_dispense(
    patient: dict,
    drug: dict,
    pharmacy: dict,
    prescriber: dict,
    svc_date: date,
    is_340b: bool = True,
    payer_type: str = "commercial",
    source: str = "tpa_feed",
) -> dict:
    return {
        "dispense_id":           str(uuid4()),
        "dispense_date":         svc_date.isoformat(),
        "external_id":           f"RX-{random.randint(100000, 999999)}",
        "covered_entity_id":     CE_ID,
        "ndc_11":                ndc_fmt(drug["ndc"]),
        "patient_id_hash":       patient["hash"],
        "prescriber_npi":        prescriber["npi"],
        "dispenser_npi":         pharmacy["npi"],
        "rx_number":             f"RX{random.randint(1000000, 9999999)}",
        "days_supply":           random.choice([30, 60, 90]),
        "quantity":              round(random.uniform(1, 90), 2),
        "payer_type":            payer_type,
        "is_340b_dispense":      is_340b,
        "source_file":           source,
    }


def make_claim(
    patient: dict,
    drug: dict,
    pharmacy: dict,
    prescriber: dict,
    svc_date: date,
    claim_type: str = "commercial",
    is_medicaid: bool = False,
    is_340b_billed: bool = True,
    billed_amount: float | None = None,
    source: str = "tpa_feed",
    rx_number: str | None = None,
) -> dict:
    wac = drug["wac"]
    ceil = drug["ceiling"]
    billed = billed_amount if billed_amount is not None else (ceil if is_340b_billed else wac)
    return {
        "claim_id":          str(uuid4()),
        "service_date":      svc_date.isoformat(),
        "external_id":       f"CLM-{random.randint(100000, 999999)}",
        "covered_entity_id": CE_ID,
        "claim_type":        claim_type,
        "claim_status":      "paid",
        "patient_id_hash":   patient["hash"],
        "prescriber_npi":    prescriber["npi"],
        "dispenser_npi":     pharmacy["npi"],
        "rx_number":         rx_number or f"RX{random.randint(1000000, 9999999)}",
        "ndc_11":            ndc_fmt(drug["ndc"]),
        "quantity":          round(random.uniform(1, 90), 2),
        "days_supply":       30,
        "billed_amount":     round(billed, 2),
        "allowed_amount":    round(billed * 0.92, 2),
        "paid_amount":       round(billed * 0.88, 2),
        "patient_pay_amount": round(billed * 0.05, 2),
        "state_code":        patient["state"],
        "is_medicaid":       is_medicaid,
        "is_340b_billed":    is_340b_billed,
        "source_file":       source,
    }


def make_purchase(
    drug: dict,
    svc_date: date,
    is_340b: bool = True,
    qty: float = 10.0,
) -> dict:
    price = drug["ceiling"] if is_340b else drug["wac"]
    return {
        "purchase_id":        str(uuid4()),
        "purchase_date":      svc_date.isoformat(),
        "covered_entity_id":  CE_ID,
        "ndc_11":             ndc_fmt(drug["ndc"]),
        "wholesaler_name":    random.choice(["McKesson", "AmerisourceBergen", "Cardinal Health"]),
        "quantity":           round(qty, 2),
        "unit_price":         round(price, 6),
        "total_cost":         round(price * qty, 2),
        "purchase_price_type": "340B" if is_340b else "WAC",
        "is_340b_purchase":   is_340b,
        "ceiling_price":      round(drug["ceiling"], 6),
        "source_file":        "wholesaler_340b_feed",
    }


# ── Data generation ───────────────────────────────────────────────────────────

dispenses = []
claims    = []
purchases = []

print("Generating synthetic 340B data...\n")


# ── CLEAN RECORDS: 400 normal 340B dispenses with matching claims ──────────────
print("  [1/6] Clean 340B dispenses + claims (400 records)...")
for _ in range(400):
    patient    = random.choice(PATIENTS)
    drug       = random.choice(DRUGS)
    pharmacy   = random.choice(PHARMACIES)
    prescriber = random.choice(PRESCRIBERS)
    svc_date   = random_date(90, 2)
    payer      = random.choice(["commercial", "medicare_part_d"])

    d = make_dispense(patient, drug, pharmacy, prescriber, svc_date,
                      is_340b=True, payer_type=payer)
    c = make_claim(patient, drug, pharmacy, prescriber, svc_date,
                   claim_type=payer, is_medicaid=False, is_340b_billed=True,
                   rx_number=d["rx_number"])
    dispenses.append(d)
    claims.append(c)


# ── VIOLATION 1: Duplicate Dispensing (DD-001) ────────────────────────────────
# Same patient + NDC + date → two different pharmacies
print("  [2/6] DD-001 Duplicate dispensing violations (60 pairs)...")
for _ in range(60):
    patient    = random.choice(PATIENTS[:80])   # subset for clustering
    drug       = random.choice(DRUGS[:5])        # high-cost drugs
    pharm_a, pharm_b = random.sample(PHARMACIES, 2)
    prescriber = random.choice(PRESCRIBERS)
    svc_date   = random_date(60, 2)

    # Two dispenses same day, same patient, same NDC → DD-001
    d1 = make_dispense(patient, drug, pharm_a, prescriber, svc_date,
                       is_340b=True, payer_type="commercial", source="tpa_dd001")
    d2 = make_dispense(patient, drug, pharm_b, prescriber, svc_date,
                       is_340b=True, payer_type="commercial", source="tpa_dd001")
    c1 = make_claim(patient, drug, pharm_a, prescriber, svc_date,
                    claim_type="commercial", rx_number=d1["rx_number"])
    c2 = make_claim(patient, drug, pharm_b, prescriber, svc_date,
                    claim_type="commercial", rx_number=d2["rx_number"])
    dispenses.extend([d1, d2])
    claims.extend([c1, c2])


# ── VIOLATION 2: Medicaid Exclusion Overlap (MEO-001) ────────────────────────
# 340B dispense + Medicaid FFS claim same patient/NDC/date
print("  [3/6] MEO-001 Medicaid exclusion violations (80 records)...")
for _ in range(80):
    patient    = random.choice(PATIENTS[50:130])  # Medicaid-likely cohort
    drug       = random.choice(DRUGS)
    pharmacy   = random.choice(PHARMACIES)
    prescriber = random.choice(PRESCRIBERS)
    svc_date   = random_date(90, 2)

    d = make_dispense(patient, drug, pharmacy, prescriber, svc_date,
                      is_340b=True, payer_type="medicaid", source="tpa_meo001")
    # Medicaid FFS claim = duplicate discount violation
    c = make_claim(patient, drug, pharmacy, prescriber, svc_date,
                   claim_type="medicaid", is_medicaid=True, is_340b_billed=True,
                   rx_number=d["rx_number"], source="medicaid_remit_meo001")
    dispenses.append(d)
    claims.append(c)


# ── VIOLATION 3: Split Billing (SB-001) ──────────────────────────────────────
# 340B dispense but WAC (non-340B) amount billed
print("  [4/6] SB-001 Split billing violations (40 records)...")
for _ in range(40):
    patient    = random.choice(PATIENTS)
    drug       = random.choice(DRUGS[:6])   # higher-cost drugs
    pharmacy   = random.choice(PHARMACIES)
    prescriber = random.choice(PRESCRIBERS)
    svc_date   = random_date(60, 2)

    d = make_dispense(patient, drug, pharmacy, prescriber, svc_date,
                      is_340b=True, payer_type="commercial", source="tpa_sb001")
    # Billed at WAC despite 340B dispense
    c = make_claim(patient, drug, pharmacy, prescriber, svc_date,
                   claim_type="commercial", is_340b_billed=False,
                   billed_amount=drug["wac"],   # WAC price billed
                   rx_number=d["rx_number"], source="tpa_sb001")
    dispenses.append(d)
    claims.append(c)


# ── VIOLATION 4: NDC Mismatch (DQ-001) ───────────────────────────────────────
# Brand NDC dispensed, generic NDC billed
print("  [5/6] DQ-001 NDC mismatch violations (50 records)...")
brand_generic_pairs = [
    (DRUGS[0], DRUGS[10]),   # Keytruda → generic NDC
    (DRUGS[1], DRUGS[11]),   # Humira → generic NDC
    (DRUGS[6], DRUGS[10]),   # Xarelto → generic
    (DRUGS[7], DRUGS[11]),   # Eliquis → generic
]
for _ in range(50):
    patient    = random.choice(PATIENTS)
    brand_drug, generic_drug = random.choice(brand_generic_pairs)
    pharmacy   = random.choice(PHARMACIES)
    prescriber = random.choice(PRESCRIBERS)
    svc_date   = random_date(90, 2)

    # Dispensed as brand
    d = make_dispense(patient, brand_drug, pharmacy, prescriber, svc_date,
                      is_340b=True, payer_type="commercial", source="tpa_dq001")
    # Billed as generic → NDC mismatch
    c = make_claim(patient, generic_drug, pharmacy, prescriber, svc_date,
                   claim_type="commercial", is_340b_billed=True,
                   rx_number=d["rx_number"], source="tpa_dq001")
    dispenses.append(d)
    claims.append(c)


# ── PURCHASES: 340B and WAC (200 records) ─────────────────────────────────────
print("  [6/6] Purchase records (200 records)...")
for _ in range(200):
    drug     = random.choice(DRUGS)
    pur_date = random_date(90, 2)
    qty      = round(random.uniform(5, 100), 2)
    is_340b  = random.random() > 0.3   # 70% 340B purchases
    purchases.append(make_purchase(drug, pur_date, is_340b=is_340b, qty=qty))


# ── Summary ───────────────────────────────────────────────────────────────────
print("\nGenerated:")
print(f"  Dispenses: {len(dispenses)}")
print(f"  Claims:    {len(claims)}")
print(f"  Purchases: {len(purchases)}")
print("\n  Violation breakdown:")
print("    DD-001 (duplicate dispensing): 120 records (60 pairs)")
print("    MEO-001 (medicaid overlap):     80 records")
print("    SB-001 (split billing):         40 records")
print("    DQ-001 (NDC mismatch):          50 records")
print(f"    Clean records:                 {400*2} records")


# ── Insert ────────────────────────────────────────────────────────────────────

def run():
    wipe = "--wipe" in sys.argv

    db = SessionLocal()
    try:
        if wipe:
            print("\nWiping ops tables...")
            db.execute(text("DELETE FROM ops.claims"))
            db.execute(text("DELETE FROM ops.dispenses"))
            db.execute(text("DELETE FROM ops.purchases"))
            db.commit()
            print("Wiped.\n")

        print("\nInserting dispenses...")
        for batch_start in range(0, len(dispenses), 100):
            batch = dispenses[batch_start:batch_start + 100]
            for d in batch:
                db.execute(text("""
                    INSERT INTO ops.dispenses
                        (dispense_id, dispense_date, external_id, covered_entity_id,
                         ndc_11, patient_id_hash, prescriber_npi, dispenser_npi,
                         rx_number, days_supply, quantity, payer_type,
                         is_340b_dispense, source_file)
                    VALUES
                        (CAST(:dispense_id AS uuid), CAST(:dispense_date AS date),
                         :external_id, CAST(:covered_entity_id AS uuid),
                         :ndc_11, :patient_id_hash, :prescriber_npi, :dispenser_npi,
                         :rx_number, :days_supply, :quantity, :payer_type,
                         :is_340b_dispense, :source_file)
                    ON CONFLICT DO NOTHING
                """), d)
            db.commit()
            print(f"  Dispenses: {min(batch_start + 100, len(dispenses))}/{len(dispenses)}")

        print("\nInserting claims...")
        for batch_start in range(0, len(claims), 100):
            batch = claims[batch_start:batch_start + 100]
            for c in batch:
                db.execute(text("""
                    INSERT INTO ops.claims
                        (claim_id, service_date, external_id, covered_entity_id,
                         claim_type, claim_status, patient_id_hash,
                         prescriber_npi, dispenser_npi, rx_number, ndc_11,
                         quantity, days_supply, billed_amount, allowed_amount,
                         paid_amount, patient_pay_amount, state_code,
                         is_medicaid, is_340b_billed, source_file)
                    VALUES
                        (CAST(:claim_id AS uuid), CAST(:service_date AS date),
                         :external_id, CAST(:covered_entity_id AS uuid),
                         :claim_type, :claim_status, :patient_id_hash,
                         :prescriber_npi, :dispenser_npi, :rx_number, :ndc_11,
                         :quantity, :days_supply, :billed_amount, :allowed_amount,
                         :paid_amount, :patient_pay_amount, :state_code,
                         :is_medicaid, :is_340b_billed, :source_file)
                    ON CONFLICT DO NOTHING
                """), c)
            db.commit()
            print(f"  Claims:    {min(batch_start + 100, len(claims))}/{len(claims)}")

        print("\nInserting purchases...")
        for p in purchases:
            db.execute(text("""
                INSERT INTO ops.purchases
                    (purchase_id, purchase_date, covered_entity_id, ndc_11,
                     wholesaler_name, quantity, unit_price, total_cost,
                     purchase_price_type, is_340b_purchase, ceiling_price, source_file)
                VALUES
                    (CAST(:purchase_id AS uuid), CAST(:purchase_date AS date),
                     CAST(:covered_entity_id AS uuid), :ndc_11,
                     :wholesaler_name, :quantity, :unit_price, :total_cost,
                     :purchase_price_type, :is_340b_purchase, :ceiling_price, :source_file)
                ON CONFLICT DO NOTHING
            """), p)
        db.commit()
        print(f"  Purchases: {len(purchases)}/{len(purchases)}")

        # ── Final counts ───────────────────────────────────────────────────────
        print("\n" + "─" * 50)
        d_count = db.execute(text("SELECT COUNT(*) FROM ops.dispenses")).scalar()
        c_count = db.execute(text("SELECT COUNT(*) FROM ops.claims")).scalar()
        p_count = db.execute(text("SELECT COUNT(*) FROM ops.purchases")).scalar()

        print("Total in DB:")
        print(f"  ops.dispenses:  {d_count:>6}")
        print(f"  ops.claims:     {c_count:>6}")
        print(f"  ops.purchases:  {p_count:>6}")

        # ── Violation pattern check ────────────────────────────────────────────
        print("\nViolation pattern verification:")
        dup = db.execute(text("""
            SELECT COUNT(*) FROM (
                SELECT patient_id_hash, ndc_11, dispense_date, COUNT(*) as n
                FROM ops.dispenses
                WHERE is_340b_dispense = TRUE
                GROUP BY patient_id_hash, ndc_11, dispense_date
                HAVING COUNT(*) > 1
            ) x
        """)).scalar()
        print(f"  DD-001 candidates (dup dispense same day):  {dup} patient-NDC-date groups")

        meo = db.execute(text("""
            SELECT COUNT(*) FROM ops.dispenses d
            JOIN ops.claims c
              ON c.patient_id_hash = d.patient_id_hash
             AND c.ndc_11 = d.ndc_11
             AND c.service_date = d.dispense_date
            WHERE d.is_340b_dispense = TRUE
              AND c.is_medicaid = TRUE
        """)).scalar()
        print(f"  MEO-001 candidates (340B + Medicaid same day): {meo} records")

        sb = db.execute(text("""
            SELECT COUNT(*) FROM ops.dispenses d
            JOIN ops.claims c
              ON c.rx_number = d.rx_number
            WHERE d.is_340b_dispense = TRUE
              AND c.is_340b_billed = FALSE
        """)).scalar()
        print(f"  SB-001 candidates (340B dispense, WAC billed): {sb} records")

        ndc_mm = db.execute(text("""
            SELECT COUNT(*) FROM ops.dispenses d
            JOIN ops.claims c
              ON c.rx_number = d.rx_number
            WHERE d.ndc_11 != c.ndc_11
        """)).scalar()
        print(f"  DQ-001 candidates (NDC mismatch):              {ndc_mm} records")

    except Exception as e:
        db.rollback()
        print(f"\nERROR: {e}", file=sys.stderr)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()
