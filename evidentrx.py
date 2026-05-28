#!/usr/bin/env python3
"""
EvidentRx Platform CLI
======================
Single entry point for all platform operations.

Usage:
  python evidentrx.py setup          # First-time full setup (venv + DB + seed)
  python evidentrx.py start          # Start API + frontend servers
  python evidentrx.py start --api-only
  python evidentrx.py start --ui-only
  python evidentrx.py seed           # Reseed all demo/test data
  python evidentrx.py seed --real    # Load real HRSA/CMS dataset
  python evidentrx.py run-agent <case_id>   # Run agent workflow on a case
  python evidentrx.py rules          # Run rules engine on current data
  python evidentrx.py status         # Check system health
  python evidentrx.py reset          # Wipe and reseed (dev only)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
VENV = ROOT / ".venv"
PYTHON = VENV / "bin" / "python3"
PIP = VENV / "bin" / "pip"

# ── Colours ──────────────────────────────────────────────────────────────────
G = "\033[92m"   # green
Y = "\033[93m"   # yellow
R = "\033[91m"   # red
B = "\033[94m"   # blue
BOLD = "\033[1m"
RESET = "\033[0m"


def ok(msg: str) -> None:
    print(f"{G}✓{RESET}  {msg}")


def info(msg: str) -> None:
    print(f"{B}→{RESET}  {msg}")


def warn(msg: str) -> None:
    print(f"{Y}⚠{RESET}  {msg}")


def err(msg: str) -> None:
    print(f"{R}✗{RESET}  {msg}", file=sys.stderr)


def header(title: str) -> None:
    print(f"\n{BOLD}{B}{'═' * 58}{RESET}")
    print(f"{BOLD}{B}  {title}{RESET}")
    print(f"{BOLD}{B}{'═' * 58}{RESET}\n")


def run(cmd: list[str], cwd: Path | None = None, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    result = subprocess.run(
        cmd, cwd=cwd or ROOT,
        capture_output=capture,
        text=True,
    )
    if check and result.returncode != 0:
        if capture:
            err(result.stderr or result.stdout)
        sys.exit(result.returncode)
    return result


# ── Prerequisite checks ───────────────────────────────────────────────────────

def check_python_version() -> None:
    if sys.version_info < (3, 11):
        err(f"Python 3.11+ required, found {sys.version}")
        sys.exit(1)
    ok(f"Python {sys.version.split()[0]}")


def check_postgres() -> bool:
    result = subprocess.run(
        ["pg_isready", "-h", "localhost", "-p", "5432"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        ok("PostgreSQL is running")
        return True
    warn("PostgreSQL not responding — trying to start...")
    # macOS Homebrew
    subprocess.run(["brew", "services", "start", "postgresql@14"], capture_output=True)
    subprocess.run(["brew", "services", "start", "postgresql@15"], capture_output=True)
    subprocess.run(["brew", "services", "start", "postgresql"],    capture_output=True)
    time.sleep(2)
    result2 = subprocess.run(
        ["pg_isready", "-h", "localhost", "-p", "5432"],
        capture_output=True, text=True,
    )
    if result2.returncode == 0:
        ok("PostgreSQL started")
        return True
    err("PostgreSQL is not running. Please start it manually:")
    err("  brew services start postgresql@15")
    err("  OR: pg_ctl -D /opt/homebrew/var/postgresql@15 start")
    return False


def check_node() -> bool:
    result = subprocess.run(["node", "--version"], capture_output=True, text=True)
    if result.returncode == 0:
        ok(f"Node.js {result.stdout.strip()}")
        return True
    warn("Node.js not found — frontend will not start")
    return False


def check_dotenv() -> bool:
    env_file = ROOT / ".env"
    if env_file.exists():
        ok(".env file present")
        return True
    warn(".env not found — copying from .env.example")
    example = ROOT / ".env.example"
    if example.exists():
        import shutil
        shutil.copy(example, env_file)
        warn("Edit .env and add your GROQ_API_KEY before using AI features")
        return True
    err(".env.example not found — please create .env manually")
    return False


# ── Setup steps ───────────────────────────────────────────────────────────────

def step_venv() -> None:
    if PYTHON.exists():
        ok("Virtual environment already exists")
        return
    info("Creating virtual environment...")
    run([sys.executable, "-m", "venv", str(VENV)])
    ok("Virtual environment created")


def step_install() -> None:
    info("Installing Python dependencies...")
    run([str(PIP), "install", "-e", ".", "--quiet"])
    ok("Dependencies installed")


def step_migrate() -> None:
    info("Running database migrations...")
    result = run(
        [str(PYTHON), "-m", "alembic", "upgrade", "head"],
        check=False, capture=True,
    )
    if result.returncode != 0:
        warn(f"Migration issue: {result.stderr[:200]}")
        warn("Database may already be up-to-date")
    else:
        ok("Database schema up-to-date")


def step_seed_rules() -> None:
    """Seed the 10 compliance rules into audit.compliance_rules."""
    info("Seeding compliance rules...")
    _run_python_inline(_SEED_RULES_CODE)
    ok("Compliance rules seeded (10 rules)")


def step_seed_data(real: bool = False) -> None:
    if real:
        info("Loading real HRSA/CMS dataset...")
        run([str(PYTHON), "scripts/load_real_data.py"])
        ok("Real dataset loaded")
    else:
        info("Seeding demo investigation cases...")
        run([str(PYTHON), "scripts/seed_demo_cases.py"], check=False)
        info("Seeding synthetic 340B claims data...")
        run([str(PYTHON), "scripts/seed_claims_data.py"], check=False)
        info("Building split_billing table...")
        run([str(PYTHON), "scripts/build_split_billing.py"], check=False)
        ok("Demo data seeded")


def step_rules_engine() -> None:
    info("Running rules engine on seeded data...")
    _run_python_inline(_RUN_RULES_CODE)
    ok("Rules engine complete")


def _run_python_inline(code: str) -> None:
    result = subprocess.run(
        [str(PYTHON), "-c", code],
        cwd=ROOT, capture_output=False, text=True,
    )
    if result.returncode != 0:
        warn("Step completed with warnings (may be expected)")


# ── Server management ─────────────────────────────────────────────────────────

def start_api(background: bool = True) -> subprocess.Popen | None:
    info("Starting FastAPI (port 8000)...")
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    # Load .env into environment
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()

    cmd = [
        str(VENV / "bin" / "uvicorn"),
        "api.main:app",
        "--reload",
        "--port", "8000",
        "--host", "0.0.0.0",
        "--log-level", "warning",
    ]
    if background:
        proc = subprocess.Popen(cmd, cwd=ROOT, env=env)
        time.sleep(2)
        if proc.poll() is None:
            ok(f"API server running  →  http://localhost:8000")
            ok(f"API docs            →  http://localhost:8000/docs")
            return proc
        else:
            err("API server failed to start")
            return None
    else:
        subprocess.run(cmd, cwd=ROOT, env=env)
        return None


def start_frontend(background: bool = True) -> subprocess.Popen | None:
    frontend = ROOT / "frontend"
    if not (frontend / "node_modules").exists():
        info("Installing npm dependencies (first run — this takes ~30s)...")
        subprocess.run(["npm", "install"], cwd=frontend, capture_output=False)
    info("Starting Next.js dev server (port 3000)...")
    cmd = ["npm", "run", "dev"]
    if background:
        proc = subprocess.Popen(cmd, cwd=frontend)
        time.sleep(3)
        if proc.poll() is None:
            ok(f"Dashboard running   →  http://localhost:3000")
            return proc
        else:
            warn("Frontend may have failed — check manually with: cd frontend && npm run dev")
            return None
    else:
        subprocess.run(cmd, cwd=frontend)
        return None


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_setup(args: argparse.Namespace) -> None:
    header("EvidentRx — First-Time Setup")

    check_python_version()
    pg_ok = check_postgres()
    if not pg_ok:
        sys.exit(1)
    check_dotenv()

    step_venv()
    step_install()
    step_migrate()
    step_seed_rules()
    step_seed_data(real=getattr(args, "real", False))
    step_rules_engine()

    print()
    print(f"{BOLD}{G}Setup complete!{RESET}")
    print()
    print(f"  Start servers:  {BOLD}python evidentrx.py start{RESET}")
    print(f"  API only:       {BOLD}python evidentrx.py start --api-only{RESET}")
    print(f"  Load real data: {BOLD}python evidentrx.py seed --real{RESET}")
    print()


def cmd_start(args: argparse.Namespace) -> None:
    header("EvidentRx — Starting Platform")

    pg_ok = check_postgres()
    if not pg_ok:
        sys.exit(1)

    api_only = getattr(args, "api_only", False)
    ui_only  = getattr(args, "ui_only", False)
    has_node = check_node()

    procs: list[subprocess.Popen] = []

    if not ui_only:
        proc = start_api(background=not api_only)
        if proc:
            procs.append(proc)

    if not api_only and has_node:
        proc = start_frontend(background=True)
        if proc:
            procs.append(proc)

    if procs:
        print()
        print(f"{BOLD}Platform is running. Press Ctrl+C to stop all servers.{RESET}")
        print()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nShutting down...")
            for p in procs:
                p.terminate()
            print("Done.")


def cmd_seed(args: argparse.Namespace) -> None:
    header("EvidentRx — Seeding Data")
    pg_ok = check_postgres()
    if not pg_ok:
        sys.exit(1)
    step_seed_rules()
    step_seed_data(real=getattr(args, "real", False))
    step_rules_engine()
    ok("Seed complete")


def cmd_rules(args: argparse.Namespace) -> None:
    header("EvidentRx — Running Rules Engine")
    pg_ok = check_postgres()
    if not pg_ok:
        sys.exit(1)
    step_rules_engine()


def cmd_run_agent(args: argparse.Namespace) -> None:
    header("EvidentRx — Running Agent Workflow")

    case_id = args.case_id
    info(f"Dispatching agent workflow for case: {case_id}")

    code = f"""
import os
from dotenv import load_dotenv
load_dotenv('{ROOT / ".env"}')
from app.database import SessionLocal
from agents.router import AgentRouter
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s: %(message)s')
logging.getLogger('httpcore').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('openai').setLevel(logging.WARNING)
db = SessionLocal()
try:
    router = AgentRouter()
    result = router.dispatch(case_id='{case_id}', session=db)
    route = result.get('escalation_route', 'unknown')
    risk  = (result.get('risk_assessment') or {{}}).get('overall_risk_level', 'unknown')
    tin   = result.get('total_input_tokens', 0)
    tout  = result.get('total_output_tokens', 0)
    print(f'\\nWorkflow complete: route={{route}} risk={{risk}} tokens={{tin}}/{{tout}}')
finally:
    db.close()
"""
    subprocess.run([str(PYTHON), "-c", code], cwd=ROOT)


def cmd_status(args: argparse.Namespace) -> None:
    header("EvidentRx — System Status")

    check_postgres()

    # DB stats
    code = """
from app.database import SessionLocal
from sqlalchemy import text
db = SessionLocal()
try:
    cases     = db.execute(text('SELECT COUNT(*) FROM audit.investigation_cases')).scalar()
    findings  = db.execute(text('SELECT COUNT(*) FROM audit.audit_findings')).scalar()
    dispenses = db.execute(text('SELECT COUNT(*) FROM ops.dispenses')).scalar()
    splits    = db.execute(text('SELECT COUNT(*) FROM ops.split_billing')).scalar()
    rules     = db.execute(text('SELECT COUNT(*) FROM audit.compliance_rules WHERE is_active')).scalar()
    print(f'  Investigation cases:  {cases}')
    print(f'  Audit findings:       {findings}')
    print(f'  Dispenses:            {dispenses}')
    print(f'  Split billing rows:   {splits}')
    print(f'  Active rules:         {rules}')
finally:
    db.close()
"""
    subprocess.run([str(PYTHON), "-c", code], cwd=ROOT)

    # API health
    result = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "http://localhost:8000/health"],
        capture_output=True, text=True,
    )
    if result.stdout.strip() == "200":
        ok("API server is up  →  http://localhost:8000")
    else:
        warn("API server is NOT running — start with: python evidentrx.py start")

    # Frontend
    result2 = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "http://localhost:3000"],
        capture_output=True, text=True,
    )
    if result2.stdout.strip() in ("200", "307"):
        ok("Dashboard is up   →  http://localhost:3000")
    else:
        warn("Dashboard is NOT running")


def cmd_validate(args: argparse.Namespace) -> None:
    header("EvidentRx — Data Validation")
    cmd = [str(PYTHON), "scripts/validate_data.py"]
    if getattr(args, "report", False):
        cmd.append("--report")
    run(cmd, check=False)


def cmd_health(args: argparse.Namespace) -> None:
    header("EvidentRx — Health Check")
    cmd = [str(PYTHON), "scripts/health_check.py"]
    if getattr(args, "verbose", False):
        cmd.append("--verbose")
    if getattr(args, "json", False):
        cmd.append("--json")
    if getattr(args, "skip_api", False):
        cmd.append("--skip-api")
    if getattr(args, "skip_llm", False):
        cmd.append("--skip-llm")
    run(cmd, check=False)


def cmd_generate_samples(args: argparse.Namespace) -> None:
    header("EvidentRx — Generating Sample Upload Files")
    run([
        str(PYTHON), "scripts/generate_sample_upload.py",
        "--rows",       str(getattr(args, "rows", 100)),
        "--violations", str(getattr(args, "violations", 0.12)),
        "--out-dir",    str(getattr(args, "out_dir", "output")),
    ])


def cmd_export(args: argparse.Namespace) -> None:
    header("EvidentRx — Exporting Findings")
    cmd = [str(PYTHON), "scripts/export_findings.py"]
    if getattr(args, "status", None):
        cmd += ["--status"] + args.status
    if getattr(args, "severity", None):
        cmd += ["--severity"] + args.severity
    if getattr(args, "case", None):
        cmd += ["--case", args.case]
    if getattr(args, "out", None):
        cmd += ["--out", args.out]
    run(cmd)


def cmd_reset(args: argparse.Namespace) -> None:
    header("EvidentRx — Reset (Wipe + Reseed)")
    warn("This will DELETE all data and reseed. Are you sure? [y/N] ")
    if input().strip().lower() != "y":
        print("Aborted.")
        return
    pg_ok = check_postgres()
    if not pg_ok:
        sys.exit(1)
    info("Wiping data tables...")
    _run_python_inline(_WIPE_CODE)
    step_seed_rules()
    step_seed_data(real=getattr(args, "real", False))
    step_rules_engine()
    ok("Reset complete")


# ── Inline Python code blocks ─────────────────────────────────────────────────

_SEED_RULES_CODE = """
import sys, os
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv()
from app.database import SessionLocal
from sqlalchemy import text
import uuid

RULES = [
    ('DD-001', 'Duplicate Discount — 340B Purchase + Medicaid Claim Same Drug/Patient',
     'duplicate_discount', 'critical', '1.0.0',
     '{"trigger": "is_340b_purchase AND is_medicaid_billed AND patient_id_hash"}',
     '42 U.S.C. § 256b; HRSA OPA 2013-1'),
    ('DD-002', 'Duplicate Discount — Accumulator Imbalance Signal',
     'duplicate_discount', 'high', '1.0.0',
     '{"trigger": "accumulator_balance < -threshold"}',
     'HRSA OPA Audit Guide 2023'),
    ('MEO-001','Medicaid Carve-Out Violation — 340B Drug to Medicaid Under Carve-Out',
     'medicaid_overlap', 'critical', '1.0.0',
     '{"trigger": "has_carve_out_election AND is_medicaid_billed AND is_340b_purchase"}',
     '42 U.S.C. § 1396r-8; HRSA OPA 2020'),
    ('MEO-002','Medicaid Carve-In Violation — WAC Drug Billed Under Carve-In Election',
     'medicaid_overlap', 'high', '1.0.0',
     '{"trigger": "carve_in_flag AND NOT is_340b_purchase AND is_medicaid_billed"}',
     'HRSA OPA Carve-In Policy 2020'),
    ('CPE-001','Covered Entity Eligibility — Service Outside Program Window',
     'entity_eligibility', 'high', '1.0.0',
     '{"trigger": "service_date outside ce_program window"}',
     '42 U.S.C. § 256b(a)(4)'),
    ('CPE-002','Covered Entity Eligibility — CE Program Terminated Before Service',
     'entity_eligibility', 'critical', '1.0.0',
     '{"trigger": "ce_program_end IS NOT NULL AND service_date > ce_program_end"}',
     '42 U.S.C. § 256b(a)(4)'),
    ('SB-001','Accumulator Imbalance — Dispenses Exceed 340B Purchases',
     'split_billing', 'high', '1.0.0',
     '{"trigger": "accumulator_balance < 0"}',
     'HRSA OPA Replenishment Model 2021'),
    ('EE-001','Patient Eligibility — Ineligible Patient Risk Flag',
     'entity_eligibility', 'high', '1.0.0',
     '{"trigger": "ineligible_patient_risk IS TRUE"}',
     'HRSA OPA Patient Definition 2010'),
    ('DQ-001','Data Quality — Missing Patient Identifier on 340B Dispense',
     'data_quality', 'medium', '1.0.0',
     '{"trigger": "is_340b_purchase AND NOT patient_id_hash"}',
     'HRSA OPA Patient Integrity Policy'),
    ('DQ-002','Data Quality — NDC Not Found in FDA Drug Directory',
     'data_quality', 'low', '1.0.0',
     '{"trigger": "ndc_not_in_fda_directory"}',
     '21 C.F.R. § 207; FDA NDC Directory'),
]

db = SessionLocal()
try:
    for (code, name, cat, sev, ver, logic, ref) in RULES:
        db.execute(text("""
            INSERT INTO audit.compliance_rules (
                rule_id, rule_code, rule_name, rule_category, rule_version,
                severity, logic_definition, regulatory_reference,
                effective_date, is_active, created_at, updated_at
            ) VALUES (
                gen_random_uuid(), :code, :name, :cat, :ver,
                :sev, CAST(:logic AS jsonb), :ref,
                '2020-01-01', TRUE, NOW(), NOW()
            ) ON CONFLICT (rule_code) DO NOTHING
        """), {'code': code, 'name': name, 'cat': cat, 'ver': ver,
               'sev': sev, 'logic': logic, 'ref': ref})
    db.commit()
    n = db.execute(text('SELECT COUNT(*) FROM audit.compliance_rules')).scalar()
    print(f'  {n} compliance rules active')
finally:
    db.close()
"""

_RUN_RULES_CODE = """
import sys, os
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv()
import logging
logging.basicConfig(level=logging.WARNING)
from app.database import SessionLocal
from rules_engine.engine import RulesEngine
db = SessionLocal()
try:
    engine = RulesEngine()
    stats = engine.run(db)
    print(f'  Evaluated: {stats[\"total_evaluated\"]} records')
    print(f'  Findings:  {stats[\"total_findings\"]} generated')
    for code in ['DD-001','DD-002','MEO-001','MEO-002','SB-001','DQ-001','DQ-002']:
        cnt = stats.get(code, 0)
        if cnt: print(f'    {code}: {cnt}')
finally:
    db.close()
"""

_WIPE_CODE = """
import sys, os
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv()
from app.database import SessionLocal
from sqlalchemy import text
db = SessionLocal()
try:
    db.execute(text('DELETE FROM audit.audit_findings'))
    db.execute(text('DELETE FROM audit.investigation_case_findings'))
    db.execute(text('DELETE FROM audit.case_risk_snapshots'))
    db.execute(text('DELETE FROM audit.investigation_cases'))
    db.execute(text('DELETE FROM ops.split_billing'))
    db.execute(text('DELETE FROM ops.claims'))
    db.execute(text('DELETE FROM ops.dispenses'))
    db.execute(text('DELETE FROM ops.purchases'))
    db.commit()
    print('  All data tables wiped')
finally:
    db.close()
"""

# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="evidentrx",
        description="EvidentRx 340B Compliance Platform CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python evidentrx.py setup                    # First-time setup from scratch
  python evidentrx.py setup --real             # Setup with real HRSA/FDA data
  python evidentrx.py start                    # Start API + dashboard
  python evidentrx.py start --api-only         # API only (no frontend)
  python evidentrx.py seed --real              # Reload real HRSA/CMS data
  python evidentrx.py rules                    # Run compliance rules engine
  python evidentrx.py run-agent <id>           # Dispatch AI workflow on a case
  python evidentrx.py generate-samples         # Generate sample CSV for upload
  python evidentrx.py export --severity critical high  # Export findings CSV
  python evidentrx.py validate                 # Check data quality
  python evidentrx.py health                   # Full system health check
  python evidentrx.py health --json            # CI-friendly health check
  python evidentrx.py status                   # Quick system stats
  python evidentrx.py reset                    # Wipe and reseed (dev)
        """,
    )

    sub = parser.add_subparsers(dest="command")

    # setup
    p_setup = sub.add_parser("setup", help="First-time platform setup")
    p_setup.add_argument("--real", action="store_true", help="Load real HRSA/CMS data instead of synthetic")

    # start
    p_start = sub.add_parser("start", help="Start API and/or frontend servers")
    p_start.add_argument("--api-only", action="store_true", help="Start API server only")
    p_start.add_argument("--ui-only",  action="store_true", help="Start frontend only")

    # seed
    p_seed = sub.add_parser("seed", help="Seed / refresh demo data")
    p_seed.add_argument("--real", action="store_true", help="Load real HRSA/CMS dataset")

    # rules
    sub.add_parser("rules", help="Run deterministic rules engine")

    # run-agent
    p_agent = sub.add_parser("run-agent", help="Run AI agent workflow on a case")
    p_agent.add_argument("case_id", help="Investigation case UUID")

    # status
    sub.add_parser("status", help="System health and data stats")

    # reset
    p_reset = sub.add_parser("reset", help="Wipe and reseed all data (dev only)")
    p_reset.add_argument("--real", action="store_true")

    # generate-samples
    p_samples = sub.add_parser("generate-samples", help="Generate sample CSV files for upload testing")
    p_samples.add_argument("--rows",      type=int,   default=100,  help="Rows per file")
    p_samples.add_argument("--out-dir",   default="output",         help="Output directory")
    p_samples.add_argument("--violations",type=float, default=0.12, help="Violation rate 0-1")

    # export
    p_export = sub.add_parser("export", help="Export audit findings to CSV")
    p_export.add_argument("--status",   nargs="+", default=["open"], help="Finding statuses to include")
    p_export.add_argument("--severity", nargs="+", default=None,     help="Severities to include")
    p_export.add_argument("--case",     default=None,                 help="Filter by case number")
    p_export.add_argument("--out",      default=None,                 help="Output file path")

    # validate
    p_validate = sub.add_parser("validate", help="Validate data quality")
    p_validate.add_argument("--report", action="store_true", help="Save validation_report.txt")

    # health
    p_health = sub.add_parser("health", help="Full system health check")
    p_health.add_argument("--verbose",   "-v", action="store_true")
    p_health.add_argument("--json",            action="store_true", help="Machine-readable JSON output")
    p_health.add_argument("--skip-api",        action="store_true")
    p_health.add_argument("--skip-llm",        action="store_true")

    args = parser.parse_args()

    dispatch = {
        "setup":            cmd_setup,
        "start":            cmd_start,
        "seed":             cmd_seed,
        "rules":            cmd_rules,
        "run-agent":        cmd_run_agent,
        "status":           cmd_status,
        "reset":            cmd_reset,
        "generate-samples": cmd_generate_samples,
        "export":           cmd_export,
        "validate":         cmd_validate,
        "health":           cmd_health,
    }

    if args.command is None:
        parser.print_help()
        return

    dispatch[args.command](args)


if __name__ == "__main__":
    main()
