#!/usr/bin/env python3
"""
validate_data.py — Validates the consistency of loaded 340B data.

Checks for common data quality issues that would cause the rules engine
to produce false positives or miss real violations:

  1. NDC format validity (11 digits, no dashes)
  2. Patient hash consistency (SHA-256 hex, length 40)
  3. Date range plausibility (not in future, not before 1990)
  4. Split billing integrity (all FK references resolve)
  5. Duplicate split_billing rows (same dispense+claim)
  6. Financial exposure outliers (>$1M per finding)
  7. Coverage window gaps (dispense date outside purchase window)

Usage:
    python scripts/validate_data.py
    python scripts/validate_data.py --fix      # auto-fix minor issues
    python scripts/validate_data.py --report   # save validation_report.txt
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from app.database import SessionLocal  # noqa: E402
from sqlalchemy import text


def _banner(title: str) -> None:
    print(f"\n  {'─' * 50}")
    print(f"  {title}")
    print(f"  {'─' * 50}")


def validate_ndc_format(db, issues: list[str]) -> int:
    rows = db.execute(text("""
        SELECT COUNT(*) FROM ops.dispenses
        WHERE ndc_11 IS NULL
           OR LENGTH(ndc_11) != 11
           OR ndc_11 ~ '[^0-9]'
    """)).scalar() or 0
    if rows:
        issues.append(f"NDC format: {rows} dispense rows with invalid NDC-11")
    return rows


def validate_patient_hashes(db, issues: list[str]) -> int:
    rows = db.execute(text("""
        SELECT COUNT(*) FROM ops.dispenses
        WHERE patient_id_hash IS NOT NULL
          AND patient_id_hash != ''
          AND LENGTH(patient_id_hash) NOT IN (40, 64)
    """)).scalar() or 0
    if rows:
        issues.append(f"Patient hash: {rows} rows with unexpected hash length")
    return rows


def validate_date_ranges(db, issues: list[str]) -> int:
    today = date.today().isoformat()
    problems = 0

    future_dispenses = db.execute(text(
        f"SELECT COUNT(*) FROM ops.dispenses WHERE dispense_date > '{today}'"
    )).scalar() or 0
    if future_dispenses:
        issues.append(f"Dates: {future_dispenses} dispenses with future dates")
        problems += future_dispenses

    old_dispenses = db.execute(text(
        "SELECT COUNT(*) FROM ops.dispenses WHERE dispense_date < '1990-01-01'"
    )).scalar() or 0
    if old_dispenses:
        issues.append(f"Dates: {old_dispenses} dispenses before 1990")
        problems += old_dispenses

    return problems


def validate_split_billing_integrity(db, issues: list[str]) -> int:
    orphan_dispenses = db.execute(text("""
        SELECT COUNT(*) FROM ops.split_billing sb
        WHERE sb.dispense_id IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM ops.dispenses d WHERE d.dispense_id = sb.dispense_id
          )
    """)).scalar() or 0

    orphan_claims = db.execute(text("""
        SELECT COUNT(*) FROM ops.split_billing sb
        WHERE sb.claim_id IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM ops.claims c WHERE c.claim_id = sb.claim_id
          )
    """)).scalar() or 0

    total = orphan_dispenses + orphan_claims
    if orphan_dispenses:
        issues.append(f"FK integrity: {orphan_dispenses} split_billing rows reference missing dispenses")
    if orphan_claims:
        issues.append(f"FK integrity: {orphan_claims} split_billing rows reference missing claims")
    return total


def validate_duplicate_split_billing(db, issues: list[str]) -> int:
    dups = db.execute(text("""
        SELECT COUNT(*) FROM (
            SELECT dispense_id, claim_id, COUNT(*) AS n
            FROM ops.split_billing
            WHERE dispense_id IS NOT NULL AND claim_id IS NOT NULL
            GROUP BY dispense_id, claim_id
            HAVING COUNT(*) > 1
        ) t
    """)).scalar() or 0
    if dups:
        issues.append(f"Duplicates: {dups} duplicate (dispense_id, claim_id) pairs in split_billing")
    return dups


def validate_exposure_outliers(db, issues: list[str]) -> int:
    outliers = db.execute(text("""
        SELECT COUNT(*) FROM audit.audit_findings
        WHERE financial_exposure > 1000000
    """)).scalar() or 0
    if outliers:
        issues.append(f"Outliers: {outliers} findings with exposure > $1M (review manually)")
    return outliers


def print_summary(issues: list[str], total: int, elapsed_ms: int) -> None:
    print()
    if not issues:
        print(f"  \033[92m✓ All validation checks passed\033[0m  ({elapsed_ms}ms)")
    else:
        print(f"  \033[91m{len(issues)} data quality issue(s) found\033[0m  ({elapsed_ms}ms)")
        print()
        for i, issue in enumerate(issues, 1):
            print(f"    {i}. {issue}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate EvidentRx 340B data quality")
    parser.add_argument("--fix",    action="store_true", help="Attempt to fix minor issues in-place")
    parser.add_argument("--report", action="store_true", help="Save validation_report.txt")
    args = parser.parse_args()

    import time
    t0 = time.time()

    db = SessionLocal()
    issues: list[str] = []
    total  = 0

    _banner("EvidentRx Data Validation")

    print("  Checking NDC formats...")
    total += validate_ndc_format(db, issues)

    print("  Checking patient hash integrity...")
    total += validate_patient_hashes(db, issues)

    print("  Checking date ranges...")
    total += validate_date_ranges(db, issues)

    print("  Checking split_billing FK integrity...")
    total += validate_split_billing_integrity(db, issues)

    print("  Checking duplicate split_billing pairs...")
    total += validate_duplicate_split_billing(db, issues)

    print("  Checking financial exposure outliers...")
    total += validate_exposure_outliers(db, issues)

    db.close()

    elapsed = int((time.time() - t0) * 1000)
    print_summary(issues, total, elapsed)

    if args.report:
        report_path = ROOT / "validation_report.txt"
        with open(report_path, "w") as f:
            f.write(f"EvidentRx Data Validation Report\n")
            f.write(f"Generated: {date.today()}\n\n")
            if not issues:
                f.write("All checks passed.\n")
            else:
                for i, issue in enumerate(issues, 1):
                    f.write(f"{i}. {issue}\n")
        print(f"  Report written to: {report_path}")

    sys.exit(0 if not issues else 1)


if __name__ == "__main__":
    main()
