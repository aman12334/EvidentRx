#!/usr/bin/env python3
"""
health_check.py — Comprehensive system health verification.

Checks:
  1. PostgreSQL connectivity and schema completeness
  2. All required tables present with expected row counts
  3. Compliance rules seeded (10 rules active)
  4. API server reachable (if running)
  5. LLM provider configured and responsive
  6. Environment variables complete

Usage:
    python scripts/health_check.py
    python scripts/health_check.py --verbose
    python scripts/health_check.py --json           # machine-readable output
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

G = "\033[92m" if sys.stdout.isatty() else ""
R = "\033[91m" if sys.stdout.isatty() else ""
Y = "\033[93m" if sys.stdout.isatty() else ""
B = "\033[94m" if sys.stdout.isatty() else ""
RESET = "\033[0m" if sys.stdout.isatty() else ""


@dataclass
class Check:
    name:     str
    passed:   bool
    detail:   str = ""
    warning:  bool = False


@dataclass
class HealthReport:
    checks:     list[Check] = field(default_factory=list)
    start_time: float       = field(default_factory=time.time)

    @property
    def passed(self) -> bool:
        return all(c.passed or c.warning for c in self.checks)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.passed and not c.warning]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.warning and not c.passed]

    def add(self, name: str, passed: bool, detail: str = "", warning: bool = False) -> None:
        self.checks.append(Check(name=name, passed=passed, detail=detail, warning=warning))

    def elapsed_ms(self) -> int:
        return int((time.time() - self.start_time) * 1000)


# ── Individual checks ──────────────────────────────────────────────────────────

def check_env_vars(report: HealthReport) -> None:
    required   = ["DATABASE_URL", "SECRET_KEY"]
    optional   = ["GROQ_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"]
    llm_key_ok = any(os.getenv(k) for k in optional)

    for var in required:
        val = os.getenv(var)
        report.add(f"ENV:{var}", bool(val), "" if val else f"{var} not set")

    report.add(
        "ENV:LLM_KEY",
        llm_key_ok,
        "Set GROQ_API_KEY, ANTHROPIC_API_KEY, or OPENAI_API_KEY" if not llm_key_ok else "",
        warning=not llm_key_ok,
    )


def check_database(report: HealthReport, verbose: bool = False) -> Optional[object]:
    try:
        from app.database import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        report.add("DB:connectivity", True)
        return db
    except Exception as exc:
        report.add("DB:connectivity", False, str(exc)[:100])
        return None


def check_tables(db, report: HealthReport) -> None:
    from sqlalchemy import text

    REQUIRED_TABLES = [
        ("ref",   "covered_entities"),
        ("ref",   "ndc_drugs"),
        ("ops",   "dispenses"),
        ("ops",   "claims"),
        ("ops",   "purchases"),
        ("ops",   "split_billing"),
        ("audit", "compliance_rules"),
        ("audit", "audit_findings"),
        ("audit", "investigation_cases"),
        ("audit", "investigation_case_findings"),
        ("audit", "case_risk_snapshots"),
        ("audit", "investigation_timelines"),
        ("meta",  "ingestion_batches"),
    ]

    for schema, table in REQUIRED_TABLES:
        try:
            n = db.execute(
                text(f"SELECT COUNT(*) FROM {schema}.{table}")
            ).scalar()
            report.add(f"TABLE:{schema}.{table}", True, f"{n:,} rows")
        except Exception as exc:
            report.add(f"TABLE:{schema}.{table}", False, str(exc)[:80])


def check_compliance_rules(db, report: HealthReport) -> None:
    from sqlalchemy import text
    try:
        n = db.execute(
            text("SELECT COUNT(*) FROM audit.compliance_rules WHERE is_active = TRUE")
        ).scalar() or 0
        report.add(
            "RULES:active_count", n >= 10,
            f"{n} active rules (expected ≥ 10)",
            warning=(0 < n < 10),
        )
    except Exception as exc:
        report.add("RULES:active_count", False, str(exc)[:80])


def check_data_populated(db, report: HealthReport) -> None:
    from sqlalchemy import text
    try:
        dispenses = db.execute(text("SELECT COUNT(*) FROM ops.dispenses")).scalar() or 0
        findings  = db.execute(text("SELECT COUNT(*) FROM audit.audit_findings")).scalar() or 0
        ces       = db.execute(text("SELECT COUNT(*) FROM ref.covered_entities WHERE is_current")).scalar() or 0

        report.add("DATA:covered_entities", ces > 0, f"{ces} covered entities", warning=(ces == 0))
        report.add("DATA:dispenses",        dispenses > 0, f"{dispenses:,} dispenses", warning=(dispenses == 0))
        report.add("DATA:findings",         findings >= 0, f"{findings:,} audit findings",
                   warning=(findings == 0 and dispenses > 0))
    except Exception as exc:
        report.add("DATA:populated", False, str(exc)[:80])


def check_api(report: HealthReport) -> None:
    import urllib.request
    try:
        with urllib.request.urlopen("http://localhost:8000/api/health", timeout=3) as resp:
            body = json.loads(resp.read())
            ok   = body.get("status") == "ok"
            report.add("API:health", ok, body.get("version", ""))
    except Exception as exc:
        report.add("API:health", False, str(exc)[:60], warning=True)


def check_llm(report: HealthReport) -> None:
    groq_key = os.getenv("GROQ_API_KEY")
    if not groq_key:
        report.add("LLM:groq_reachable", False, "GROQ_API_KEY not set", warning=True)
        return
    import urllib.request
    try:
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {groq_key}"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            n_models = len(data.get("data", []))
            report.add("LLM:groq_reachable", True, f"{n_models} models available")
    except Exception as exc:
        report.add("LLM:groq_reachable", False, str(exc)[:80], warning=True)


# ── Formatting ─────────────────────────────────────────────────────────────────

def print_report(report: HealthReport, verbose: bool = False) -> None:
    print()
    print(f"{B}EvidentRx Health Check{RESET}")
    print("─" * 50)

    for check in report.checks:
        if check.passed:
            icon = f"{G}✓{RESET}"
        elif check.warning:
            icon = f"{Y}⚠{RESET}"
        else:
            icon = f"{R}✗{RESET}"

        line = f"  {icon}  {check.name}"
        if check.detail and (verbose or not check.passed):
            line += f"  {Y if not check.passed else ''}{check.detail}{RESET}"
        print(line)

    print()
    elapsed = report.elapsed_ms()
    if report.passed:
        print(f"{G}All checks passed{RESET}  ({elapsed}ms)")
    else:
        fail_count = len(report.failures)
        warn_count = len(report.warnings)
        print(f"{R}{fail_count} failure(s){RESET}  {Y}{warn_count} warning(s){RESET}  ({elapsed}ms)")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="EvidentRx system health check")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show all check details")
    parser.add_argument("--json",          action="store_true", help="Output JSON (CI-friendly)")
    parser.add_argument("--skip-api",      action="store_true", help="Skip API server check")
    parser.add_argument("--skip-llm",      action="store_true", help="Skip LLM provider check")
    args = parser.parse_args()

    report = HealthReport()

    check_env_vars(report)

    db = check_database(report, verbose=args.verbose)
    if db:
        check_tables(report, db)
        check_compliance_rules(db, report)
        check_data_populated(db, report)
        db.close()

    if not args.skip_api:
        check_api(report)

    if not args.skip_llm:
        check_llm(report)

    if args.json:
        output = {
            "passed":    report.passed,
            "elapsed_ms": report.elapsed_ms(),
            "checks": [asdict(c) for c in report.checks],
        }
        print(json.dumps(output, indent=2))
    else:
        print_report(report, verbose=args.verbose)

    sys.exit(0 if report.passed else 1)


if __name__ == "__main__":
    main()
