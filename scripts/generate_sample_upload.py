#!/usr/bin/env python3
"""
generate_sample_upload.py — Generate realistic sample CSV files for testing
the EvidentRx hospital data upload pipeline.

Creates two files:
  output/sample_dispenses.csv  — 100 dispense records
  output/sample_claims.csv     — 100 matching claim records

These files can be uploaded via the dashboard → "Upload Data" button
or via: curl -F "file=@output/sample_dispenses.csv" http://localhost:8000/api/v1/upload/claims

Usage:
    python scripts/generate_sample_upload.py
    python scripts/generate_sample_upload.py --rows 500 --violations 0.15
    python scripts/generate_sample_upload.py --out-dir /tmp/evidentrx-demo
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import random
from datetime import date, timedelta
from pathlib import Path

# ── Real NDC-11 codes (340B frequently audited drugs) ─────────────────────────
NDC_DRUGS = [
    ("00069420030", "Atorvastatin (Lipitor)"),
    ("00006001754", "Sitagliptin (Januvia)"),
    ("00085093501", "Infliximab (Remicade)"),
    ("00093315701", "Metformin HCl"),
    ("50242006001", "Rivaroxaban (Xarelto)"),
    ("00310056010", "Esomeprazole (Nexium)"),
    ("00054027825", "Imatinib (Gleevec)"),
    ("00054026625", "Ondansetron (Zofran)"),
    ("00069015530", "Azithromycin (Zithromax)"),
    ("00071015523", "Pregabalin (Lyrica)"),
    ("00006007154", "Montelukast (Singulair)"),
    ("00006043506", "Pembrolizumab (Keytruda)"),
    ("00088221905", "Insulin Glargine (Lantus)"),
    ("00002833201", "Dulaglutide (Trulicity)"),
    ("00378059310", "Lisinopril"),
    ("00093005001", "Simvastatin"),
    ("00069015730", "Sertraline (Zoloft)"),
    ("00071046723", "Gabapentin (Neurontin)"),
]

PAYERS = [
    ("medicaid", True),
    ("medicaid", True),
    ("commercial", False),
    ("commercial", False),
    ("medicare_part_d", False),
    ("commercial", False),
]


def _hash_patient(mrn: str) -> str:
    return hashlib.sha256(mrn.encode()).hexdigest()[:40]


def _rand_date(start: date, end: date) -> date:
    return start + timedelta(days=random.randint(0, (end - start).days))


def generate_files(
    out_dir: Path,
    n_rows: int = 100,
    violation_rate: float = 0.12,
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)

    today  = date.today()
    start  = today - timedelta(days=365)
    end    = today - timedelta(days=14)

    dispenses_path = out_dir / "sample_dispenses.csv"
    claims_path    = out_dir / "sample_claims.csv"

    dispense_rows = []
    claim_rows    = []

    for i in range(1, n_rows + 1):
        mrn     = f"MRN-{i:06d}"
        ndc, drug_name = random.choice(NDC_DRUGS)
        disp_date = _rand_date(start, end)

        # Decide violation pattern
        is_violation = random.random() < violation_rate
        payer_raw, _ = random.choice(PAYERS)
        if is_violation:
            payer_raw = "medicaid"  # DD-001: 340B dispense + Medicaid claim

        days = random.choice([30, 60, 90])
        qty  = random.randint(1, 90)

        dispense_rows.append({
            "ndc_11":          ndc,
            "patient_id":      mrn,
            "dispense_date":   disp_date.strftime("%Y-%m-%d"),
            "quantity":        qty,
            "days_supply":     days,
            "payer_type":      payer_raw,
            "covered_entity_id": "",     # blank → system auto-picks
            "drug_name":       drug_name,   # informational, ignored by parser
        })

        # Matching claim (service date within 3 days of dispense)
        svc_date = disp_date + timedelta(days=random.randint(0, 3))
        billed   = round(random.uniform(50, 1200), 2)
        paid     = round(billed * random.uniform(0.75, 0.95), 2)
        claim_rows.append({
            "ndc_11":          ndc,
            "patient_id":      mrn,
            "service_date":    svc_date.strftime("%Y-%m-%d"),
            "payer_type":      payer_raw,
            "billed_amount":   f"{billed:.2f}",
            "paid_amount":     f"{paid:.2f}",
            "claim_number":    f"CLM-{random.randint(100_000_000, 999_999_999)}",
            "covered_entity_id": "",
        })

    # Write dispenses
    disp_fields = list(dispense_rows[0].keys())
    with open(dispenses_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=disp_fields)
        writer.writeheader()
        writer.writerows(dispense_rows)

    # Write claims
    claim_fields = list(claim_rows[0].keys())
    with open(claims_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=claim_fields)
        writer.writeheader()
        writer.writerows(claim_rows)

    return dispenses_path, claims_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate sample CSV files for EvidentRx upload testing",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--rows",       type=int,   default=100,  help="Number of records per file")
    parser.add_argument("--violations", type=float, default=0.12, help="Fraction of rows that are violations (0–1)")
    parser.add_argument("--out-dir",    default="output",          help="Output directory")
    args = parser.parse_args()

    out = Path(args.out_dir)
    d_path, c_path = generate_files(out, n_rows=args.rows, violation_rate=args.violations)

    print()
    print(f"  Sample files written to:  {out.resolve()}/")
    print(f"    {d_path.name}  ({args.rows} dispense records)")
    print(f"    {c_path.name}  ({args.rows} claim records)")
    print()
    print("  Upload via dashboard:")
    print("    1. Go to http://localhost:3000/investigations")
    print("    2. Click 'Upload Data'")
    print("    3. Drag sample_dispenses.csv onto the panel")
    print()
    print("  Upload via curl:")
    print(f"    curl -F 'file=@{d_path}' http://localhost:8000/api/v1/upload/claims")
    print()


if __name__ == "__main__":
    main()
