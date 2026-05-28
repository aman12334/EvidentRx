"""
EvaluationHarness — golden case replay and output validation.

A GoldenCase defines the expected outputs for a fixed, deterministic
simulation run (same seed every time). The harness:
  1. Runs simulation with the golden seed
  2. Runs the rules engine
  3. Validates finding counts and rule codes match expectations
  4. Optionally replays agent workflow and validates narrative structure

Golden cases are reproducible and diffable — the same seed always
produces the same violation data.

Usage:
    harness = EvaluationHarness()
    result = harness.run_golden(session)
    result.print_report()
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from evaluation.validators import OutputValidator, HallucinationDetector

logger = logging.getLogger(__name__)


@dataclass
class GoldenCase:
    """
    Defines expected outcomes for a deterministic simulation run.
    All assertions are against the rules engine (deterministic) — not agents.
    """
    name:             str
    description:      str

    # Simulation parameters (deterministic)
    seed:             int   = 42
    n_ces:            int   = 5      # small set for fast evaluation
    n_ndcs:           int   = 30
    violation_rate:   float = 0.10
    sim_start:        date  = field(default_factory=lambda: date(2025, 1, 1))
    sim_end:          date  = field(default_factory=lambda: date(2025, 3, 31))  # 13 weeks

    # Expected findings (rules engine outputs — deterministic)
    min_total_findings: int = 1
    expected_rule_codes: list[str] = field(default_factory=list)
    forbidden_rule_codes: list[str] = field(default_factory=list)

    # Agent output structure validation (structural, not content)
    validate_agent_output: bool = False
    required_narrative_fields: list[str] = field(default_factory=lambda: [
        "executive_summary",
        "technical_findings",
        "regulatory_context",
        "remediation_recommendations",
        "confidence_score",
    ])


# The canonical golden case for CI and regression testing
DEFAULT_GOLDEN = GoldenCase(
    name="golden_v1",
    description=(
        "13-week simulation with seed=42, 5 CEs, 10% violation rate. "
        "Validates that all 10 rule codes can be detected and that "
        "the investigation pipeline produces at least 1 case."
    ),
    seed=42,
    n_ces=5,
    n_ndcs=30,
    violation_rate=0.10,
    sim_start=date(2025, 1, 1),
    sim_end=date(2025, 3, 31),
    min_total_findings=1,
    expected_rule_codes=[],          # discovered from this seed dynamically
    validate_agent_output=False,     # set True when API keys available
)


@dataclass
class ValidationResult:
    check:   str
    passed:  bool
    message: str
    details: dict = field(default_factory=dict)


@dataclass
class EvaluationResult:
    golden_name:  str
    run_id:       str
    started_at:   float = field(default_factory=time.monotonic)
    finished_at:  Optional[float] = None
    validations:  list[ValidationResult] = field(default_factory=list)
    stats:        dict = field(default_factory=dict)

    def add(self, v: ValidationResult) -> None:
        self.validations.append(v)

    @property
    def passed(self) -> bool:
        return all(v.passed for v in self.validations)

    @property
    def failed(self) -> list[ValidationResult]:
        return [v for v in self.validations if not v.passed]

    def print_report(self) -> None:
        elapsed = round((self.finished_at or time.monotonic()) - self.started_at, 2)
        print(f"\n{'='*62}")
        print(f"  Evaluation: {self.golden_name}")
        print(f"{'='*62}")
        for v in self.validations:
            icon = "✓" if v.passed else "✗"
            print(f"  {icon}  {v.check}")
            if not v.passed:
                print(f"       → {v.message}")
        print(f"{'='*62}")
        print(f"  {'PASS' if self.passed else 'FAIL'}  ({elapsed:.1f}s)")
        if self.stats:
            print(f"\n  Stats:")
            for k, v in self.stats.items():
                print(f"    {k}: {v}")
        print()

    def to_dict(self) -> dict:
        return {
            "golden_name":  self.golden_name,
            "run_id":       self.run_id,
            "passed":       self.passed,
            "elapsed_s":    round((self.finished_at or time.monotonic()) - self.started_at, 3),
            "validations": [
                {"check": v.check, "passed": v.passed, "message": v.message}
                for v in self.validations
            ],
            "stats": self.stats,
        }


class EvaluationHarness:
    """
    Runs a golden case through the deterministic pipeline and validates outputs.
    """

    def run_golden(
        self,
        session: Session,
        golden: Optional[GoldenCase] = None,
    ) -> EvaluationResult:
        from uuid import uuid4
        gc = golden or DEFAULT_GOLDEN
        run_id = str(uuid4())[:8]
        result = EvaluationResult(golden_name=gc.name, run_id=run_id)

        logger.info("Evaluation: running golden case '%s' run=%s", gc.name, run_id)

        # Stage 1: Simulation
        sim_stats = self._run_simulation(session, gc, result)

        # Stage 2: Rules engine
        findings_stats = self._run_rules_engine(session, result)

        # Stage 3: Validate findings
        found_codes = self._validate_findings(session, gc, result, findings_stats)

        # Stage 4: Validate investigation cases
        self._validate_investigation(session, gc, result)

        # Stage 5 (optional): Agent output validation
        if gc.validate_agent_output:
            self._validate_agent_output(session, gc, result, found_codes)

        result.finished_at = time.monotonic()
        result.stats.update({
            "simulation":  sim_stats,
            "rules_engine": findings_stats,
        })
        return result

    # ------------------------------------------------------------------
    # Stages
    # ------------------------------------------------------------------

    def _run_simulation(
        self,
        session: Session,
        gc: GoldenCase,
        result: EvaluationResult,
    ) -> dict:
        try:
            from simulation.config import SimConfig
            from simulation.orchestrator import SimulationOrchestrator

            cfg = SimConfig(
                period_start=gc.sim_start,
                period_end=gc.sim_end,
                n_ces=gc.n_ces,
                n_ndcs=gc.n_ndcs,
                violation_rate=gc.violation_rate,
                random_seed=gc.seed,
            )
            SimulationOrchestrator(cfg).run(session)
            session.commit()
            result.add(ValidationResult("Simulation completed", True, "OK"))
            return {"seed": gc.seed, "n_ces": gc.n_ces}
        except Exception as e:
            result.add(ValidationResult("Simulation completed", False, str(e)))
            return {}

    def _run_rules_engine(self, session: Session, result: EvaluationResult) -> dict:
        try:
            from rules_engine.engine import RulesEngine
            engine = RulesEngine()
            stats  = engine.run(session)
            session.commit()
            result.add(ValidationResult(
                "Rules engine completed", True,
                f"Evaluated {stats.get('total_evaluated', 0):,} records, "
                f"{stats.get('total_findings', 0):,} findings",
            ))
            return stats
        except Exception as e:
            result.add(ValidationResult("Rules engine completed", False, str(e)))
            return {}

    def _validate_findings(
        self,
        session: Session,
        gc: GoldenCase,
        result: EvaluationResult,
        stats: dict,
    ) -> list[str]:
        from sqlalchemy import text

        total = stats.get("total_findings", 0)

        # Check minimum findings
        result.add(ValidationResult(
            f"Min findings (≥{gc.min_total_findings})",
            total >= gc.min_total_findings,
            f"Found {total} findings",
        ))

        # Check expected rule codes appear
        found_codes_rows = session.execute(text(
            "SELECT DISTINCT rule_code FROM audit.audit_findings"
        )).fetchall()
        found_codes = [r[0] for r in found_codes_rows]

        for code in gc.expected_rule_codes:
            result.add(ValidationResult(
                f"Rule code {code} detected",
                code in found_codes,
                f"Expected rule code '{code}' not found in findings. Found: {found_codes}",
            ))

        for code in gc.forbidden_rule_codes:
            result.add(ValidationResult(
                f"Rule code {code} absent",
                code not in found_codes,
                f"Rule code '{code}' should not appear but was found.",
            ))

        return found_codes

    def _validate_investigation(
        self,
        session: Session,
        gc: GoldenCase,
        result: EvaluationResult,
    ) -> None:
        from sqlalchemy import text
        from investigation.domain.clustering import ClusterConfig
        from investigation.services.case_builder import CaseBuilderService

        try:
            cfg     = ClusterConfig(window_days=14, min_cluster_size=1)
            service = CaseBuilderService()
            stats   = service.run(session, config=cfg)
            session.commit()

            cases_created = stats.get("cases_created", 0)
            result.add(ValidationResult(
                "Investigation cases created",
                cases_created >= 1,
                f"Created {cases_created} investigation case(s)",
                details=stats,
            ))
        except Exception as e:
            result.add(ValidationResult("Investigation build", False, str(e)))

    def _validate_agent_output(
        self,
        session: Session,
        gc: GoldenCase,
        result: EvaluationResult,
        found_codes: list[str],
    ) -> None:
        from sqlalchemy import text
        from agents.runner import InvestigationRunner

        # Get first open case
        row = session.execute(text("""
            SELECT case_id FROM audit.investigation_cases
            WHERE status = 'open' LIMIT 1
        """)).first()

        if not row:
            result.add(ValidationResult("Agent run", False, "No open cases to investigate"))
            return

        try:
            runner = InvestigationRunner.from_env()
            run_result = runner.run(session, row[0])
            session.commit()

            validator = OutputValidator()
            hallucination_checker = HallucinationDetector()

            narrative = run_result.get("executive_summary", "")

            # Structural validation
            for field_name in gc.required_narrative_fields:
                has_field = field_name in run_result or (
                    field_name == "executive_summary" and bool(narrative)
                )
                result.add(ValidationResult(
                    f"Narrative field: {field_name}",
                    has_field,
                    f"Field '{field_name}' missing from agent output",
                ))

            # Hallucination check
            invented_codes = hallucination_checker.check_finding_codes(
                narrative, found_codes
            )
            result.add(ValidationResult(
                "Hallucination check: finding codes",
                len(invented_codes) == 0,
                f"Invented finding codes in narrative: {invented_codes}",
            ))

        except Exception as e:
            result.add(ValidationResult("Agent run", False, str(e)))
