"""
Runtime bootstrap and health check infrastructure.

Validates everything the platform needs before a single line of business
logic executes:
  - Required environment variables
  - Database connectivity
  - Alembic migration status
  - Required schema tables
  - LLM provider availability

Usage:
    from runtime.bootstrap import HealthChecker
    report = HealthChecker().run()
    report.print_report()
    if not report.passed:
        sys.exit(1)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# Tables that MUST exist for the platform to operate
_REQUIRED_TABLES: list[tuple[str, str]] = [
    ("ref",   "covered_entities"),
    ("ref",   "contract_pharmacies"),
    ("ref",   "ndc_drugs"),
    ("ops",   "purchases"),
    ("ops",   "dispenses"),
    ("ops",   "claims"),
    ("ops",   "split_billing"),
    ("audit", "compliance_rules"),
    ("audit", "audit_findings"),
    ("audit", "investigation_cases"),
    ("audit", "investigation_case_findings"),
    ("audit", "investigation_timeline"),
    ("audit", "case_risk_snapshots"),
    ("audit", "reasoning_traces"),
    ("audit", "agent_runs"),
    ("audit", "workflow_checkpoints"),
    ("meta",  "ingestion_batches"),
]


@dataclass
class CheckResult:
    name: str
    passed: bool
    message: str
    critical: bool = True


@dataclass
class HealthReport:
    checks: list[CheckResult] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    def add(self, result: CheckResult) -> None:
        self.checks.append(result)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks if c.critical)

    @property
    def failed_critical(self) -> list[CheckResult]:
        return [c for c in self.checks if c.critical and not c.passed]

    @property
    def warnings(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.critical and not c.passed]

    def summary(self) -> dict:
        return {
            "passed":          self.passed,
            "total_checks":    len(self.checks),
            "failed_critical": len(self.failed_critical),
            "warnings":        len(self.warnings),
            "started_at":      self.started_at.isoformat(),
            "finished_at":     self.finished_at.isoformat() if self.finished_at else None,
            "checks": [
                {"name": c.name, "passed": c.passed, "critical": c.critical, "message": c.message}
                for c in self.checks
            ],
        }

    def print_report(self) -> None:
        print("\n" + "=" * 62)
        print("  EvidentRx — Runtime Health Check")
        print("=" * 62)
        for c in self.checks:
            icon = "✓" if c.passed else ("✗" if c.critical else "⚠")
            tag  = " [CRITICAL]" if (not c.passed and c.critical) else (
                   " [warn]"    if (not c.passed and not c.critical) else "")
            print(f"  {icon}  {c.name}{tag}")
            if not c.passed:
                print(f"       → {c.message}")
        print("=" * 62)
        if self.passed:
            print("  Platform ready.\n")
        else:
            n = len(self.failed_critical)
            print(f"  {n} critical check(s) failed — platform cannot start.\n")
            for c in self.failed_critical:
                print(f"  FIX: {c.message}")
            print()


class HealthChecker:
    """
    Runs all pre-flight checks and returns a HealthReport.
    Never raises — all errors are captured as CheckResult entries.
    """

    def run(self) -> HealthReport:
        report = HealthReport()

        self._check_env_vars(report)

        db_url = os.getenv("DATABASE_URL")
        if db_url:
            self._check_db_connectivity(report, db_url)
            self._check_migration_status(report, db_url)
            self._check_required_tables(report, db_url)
        else:
            for name in ("DB connectivity", "Migration status", "Required tables"):
                report.add(CheckResult(name, False, "Skipped — DATABASE_URL not set", critical=True))

        self._check_llm_providers(report)
        self._check_python_deps(report)

        report.finished_at = datetime.now(UTC)
        return report

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_env_vars(self, report: HealthReport) -> None:
        db_url = os.getenv("DATABASE_URL")
        if db_url:
            # Mask password for display
            display = db_url.split("@")[-1] if "@" in db_url else db_url
            report.add(CheckResult("DATABASE_URL", True, f"postgresql://...@{display}"))
        else:
            report.add(CheckResult(
                "DATABASE_URL", False,
                "Not set. Copy .env.example → .env and set DATABASE_URL=postgresql+psycopg2://...",
                critical=True,
            ))

    def _check_db_connectivity(self, report: HealthReport, db_url: str) -> None:
        try:
            from sqlalchemy import create_engine, text
            engine = create_engine(db_url, pool_pre_ping=True, pool_size=1, max_overflow=0)
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            engine.dispose()
            report.add(CheckResult("DB connectivity", True, "Connected successfully"))
        except Exception as e:
            report.add(CheckResult(
                "DB connectivity", False,
                f"Connection failed: {e}. Verify PostgreSQL is running and DATABASE_URL is correct.",
                critical=True,
            ))

    def _check_migration_status(self, report: HealthReport, db_url: str) -> None:
        try:
            from alembic.runtime.migration import MigrationContext
            from sqlalchemy import create_engine
            engine = create_engine(db_url)
            with engine.connect() as conn:
                ctx = MigrationContext.configure(conn)
                current = ctx.get_current_revision()
            engine.dispose()
            if current is None:
                report.add(CheckResult(
                    "Migration status", False,
                    "No migrations applied. Run: alembic upgrade head",
                    critical=True,
                ))
            else:
                report.add(CheckResult("Migration status", True, f"Current revision: {current}"))
        except Exception as e:
            report.add(CheckResult(
                "Migration status", False, str(e), critical=False,
            ))

    def _check_required_tables(self, report: HealthReport, db_url: str) -> None:
        try:
            from sqlalchemy import create_engine, text
            engine = create_engine(db_url)
            missing = []
            with engine.connect() as conn:
                for schema, table in _REQUIRED_TABLES:
                    exists = conn.execute(text("""
                        SELECT EXISTS (
                            SELECT 1 FROM information_schema.tables
                            WHERE table_schema = :s AND table_name = :t
                        )
                    """), {"s": schema, "t": table}).scalar()
                    if not exists:
                        missing.append(f"{schema}.{table}")
            engine.dispose()

            if missing:
                report.add(CheckResult(
                    "Required tables", False,
                    f"Missing: {', '.join(missing)}. Run: alembic upgrade head",
                    critical=True,
                ))
            else:
                report.add(CheckResult(
                    "Required tables", True,
                    f"All {len(_REQUIRED_TABLES)} tables verified",
                ))
        except Exception as e:
            report.add(CheckResult("Required tables", False, str(e), critical=False))

    def _check_llm_providers(self, report: HealthReport) -> None:
        anthropic = bool(os.getenv("ANTHROPIC_API_KEY"))
        openai    = bool(os.getenv("OPENAI_API_KEY"))

        if anthropic:
            report.add(CheckResult("Anthropic API key", True, "Set (primary provider)"))
        else:
            report.add(CheckResult(
                "Anthropic API key", False,
                "Not set — Phase 5 agent runtime will be unavailable",
                critical=False,
            ))

        if openai:
            report.add(CheckResult("OpenAI API key", True, "Set (fallback provider)"))
        else:
            report.add(CheckResult(
                "OpenAI API key", False,
                "Not set (optional — used as LLM fallback only)",
                critical=False,
            ))

        if not anthropic and not openai:
            report.add(CheckResult(
                "LLM provider", False,
                "No LLM provider configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY to enable Phase 5.",
                critical=False,
            ))

    def _check_python_deps(self, report: HealthReport) -> None:
        required = {
            "sqlalchemy":   "SQLAlchemy",
            "alembic":      "Alembic",
            "pydantic":     "Pydantic",
            "langgraph":    "LangGraph",
            "anthropic":    "Anthropic SDK",
            "tenacity":     "Tenacity",
        }
        missing = []
        for module, display in required.items():
            try:
                __import__(module)
            except ImportError:
                missing.append(display)

        if missing:
            report.add(CheckResult(
                "Python dependencies", False,
                f"Missing: {', '.join(missing)}. Run: pip install -e .",
                critical=True,
            ))
        else:
            report.add(CheckResult(
                "Python dependencies", True,
                f"All {len(required)} required packages available",
            ))
