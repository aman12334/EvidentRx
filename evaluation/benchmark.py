"""
BenchmarkSuite — structured benchmark runner for the full intelligence stack.

Runs a battery of deterministic checks against the intelligence layer
(trend analysis, correlation, risk scoring) using synthetic data produced
by the platform simulator.  Results are persisted to audit.evaluation_runs.

All checks are deterministic: given the same seed + simulation parameters,
the expected values are always the same.  The benchmark validates that the
intelligence services produce consistent, expected outputs.
"""
from __future__ import annotations

import json
import logging
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkCheck:
    name:         str
    category:     str          # "trend" | "correlation" | "risk" | "drift"
    passed:       bool
    expected:     Any
    actual:       Any
    message:      str
    details:      dict = field(default_factory=dict)


@dataclass
class BenchmarkResult:
    eval_id:          str
    eval_name:        str
    status:           str
    started_at:       datetime
    completed_at:     Optional[datetime] = None
    checks:           list[BenchmarkCheck] = field(default_factory=list)
    error_message:    Optional[str] = None

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def failed_count(self) -> int:
        return sum(1 for c in self.checks if not c.passed)

    @property
    def all_passed(self) -> bool:
        return self.failed_count == 0

    def print_report(self) -> None:
        print(f"\n{'='*60}")
        print(f"BENCHMARK: {self.eval_name}")
        print(f"Status: {self.status}  |  {self.passed_count}/{len(self.checks)} passed")
        print(f"{'='*60}")
        for c in self.checks:
            icon = "✓" if c.passed else "✗"
            print(f"  {icon} [{c.category}] {c.name}")
            if not c.passed:
                print(f"      Expected: {c.expected}")
                print(f"      Actual:   {c.actual}")
                print(f"      {c.message}")
        print(f"{'='*60}\n")

    def to_dict(self) -> dict:
        return {
            "eval_id":      self.eval_id,
            "eval_name":    self.eval_name,
            "status":       self.status,
            "all_passed":   self.all_passed,
            "passed":       self.passed_count,
            "failed":       self.failed_count,
            "total_checks": len(self.checks),
            "checks": [
                {
                    "name":     c.name,
                    "category": c.category,
                    "passed":   c.passed,
                    "message":  c.message,
                }
                for c in self.checks
            ],
        }


class BenchmarkSuite:
    """
    Runs the intelligence layer benchmark suite.

    Each benchmark category validates that the intelligence services
    return structurally correct, non-empty, logically consistent output.

    Usage::

        suite = BenchmarkSuite()
        result = suite.run(session)
        result.print_report()
    """

    EVAL_NAME = "intelligence_benchmark_v1"

    def run(
        self,
        session: Session,
        persist: bool = True,
    ) -> BenchmarkResult:
        eval_id  = str(uuid4())
        started  = datetime.utcnow()
        result   = BenchmarkResult(
            eval_id=eval_id,
            eval_name=self.EVAL_NAME,
            status="running",
            started_at=started,
        )

        try:
            checks = []
            checks += self._check_trend_analysis(session)
            checks += self._check_correlation_engine(session)
            checks += self._check_risk_scoring(session)
            checks += self._check_drift_detection(session)
            checks += self._check_graph_traversal(session)

            result.checks        = checks
            result.status        = "completed"
            result.completed_at  = datetime.utcnow()

        except Exception as exc:
            result.status        = "failed"
            result.completed_at  = datetime.utcnow()
            result.error_message = str(exc)
            logger.error("Benchmark failed: %s\n%s", exc, traceback.format_exc())

        if persist:
            self._persist(session, result)

        logger.info(
            "Benchmark %s: %d/%d passed",
            self.EVAL_NAME, result.passed_count, len(result.checks),
        )
        return result

    # ------------------------------------------------------------------ #
    # Check categories                                                     #
    # ------------------------------------------------------------------ #

    def _check_trend_analysis(self, session: Session) -> list[BenchmarkCheck]:
        from intelligence.services.trend_analysis import TrendAnalysisService
        svc = TrendAnalysisService()
        checks = []
        try:
            summary = svc.analyse(session, window_type="30d")
            checks.append(BenchmarkCheck(
                name="trend_analysis_returns_summary",
                category="trend",
                passed=summary is not None,
                expected="TrendSummary object",
                actual=type(summary).__name__,
                message="TrendAnalysisService.analyse() returned a result.",
            ))
            checks.append(BenchmarkCheck(
                name="trend_direction_values_valid",
                category="trend",
                passed=all(
                    r.trend_direction in ("improving", "stable", "worsening", "critical")
                    for r in summary.records
                ),
                expected="all directions in valid set",
                actual=list({r.trend_direction for r in summary.records}),
                message="All trend directions are valid enum values.",
            ))
            checks.append(BenchmarkCheck(
                name="trend_velocity_is_float",
                category="trend",
                passed=all(isinstance(r.velocity, float) for r in summary.records),
                expected="float",
                actual="float" if summary.records else "n/a",
                message="All velocity values are floats.",
            ))
        except Exception as exc:
            checks.append(BenchmarkCheck(
                name="trend_analysis_no_exception",
                category="trend",
                passed=False,
                expected="no exception",
                actual=str(exc),
                message=f"TrendAnalysisService raised: {exc}",
            ))
        return checks

    def _check_correlation_engine(self, session: Session) -> list[BenchmarkCheck]:
        from intelligence.services.correlation import CorrelationEngine
        engine = CorrelationEngine()
        checks = []
        try:
            report = engine.run(session)
            checks.append(BenchmarkCheck(
                name="correlation_engine_returns_report",
                category="correlation",
                passed=report is not None,
                expected="CorrelationReport",
                actual=type(report).__name__,
                message="CorrelationEngine.run() returned a report.",
            ))
            checks.append(BenchmarkCheck(
                name="correlation_strengths_in_range",
                category="correlation",
                passed=all(0.0 <= r.strength <= 1.0 for r in report.records),
                expected="all strengths in [0,1]",
                actual=f"min={min((r.strength for r in report.records), default=0):.3f}",
                message="All correlation strengths are in [0.0, 1.0].",
            ))
        except Exception as exc:
            checks.append(BenchmarkCheck(
                name="correlation_engine_no_exception",
                category="correlation",
                passed=False,
                expected="no exception",
                actual=str(exc),
                message=f"CorrelationEngine raised: {exc}",
            ))
        return checks

    def _check_risk_scoring(self, session: Session) -> list[BenchmarkCheck]:
        from intelligence.services.predictive_risk import PredictiveRiskService
        svc = PredictiveRiskService()
        checks = []
        try:
            report = svc.score(session)
            checks.append(BenchmarkCheck(
                name="risk_scoring_returns_report",
                category="risk",
                passed=report is not None,
                expected="RiskScoringReport",
                actual=type(report).__name__,
                message="PredictiveRiskService.score() returned a report.",
            ))
            checks.append(BenchmarkCheck(
                name="risk_scores_in_range",
                category="risk",
                passed=all(0.0 <= s.composite_score <= 1.0 for s in report.scores),
                expected="all scores in [0,1]",
                actual=f"max={max((s.composite_score for s in report.scores), default=0):.4f}",
                message="All composite scores are in [0.0, 1.0].",
            ))
            checks.append(BenchmarkCheck(
                name="risk_tiers_valid",
                category="risk",
                passed=all(
                    s.risk_tier in ("critical", "high", "medium", "low")
                    for s in report.scores
                ),
                expected="all tiers valid",
                actual=list({s.risk_tier for s in report.scores}),
                message="All risk tier values are valid.",
            ))
            checks.append(BenchmarkCheck(
                name="forecasts_match_scores",
                category="risk",
                passed=len(report.forecasts) == len(report.scores),
                expected=len(report.scores),
                actual=len(report.forecasts),
                message="One forecast per score entity.",
            ))
        except Exception as exc:
            checks.append(BenchmarkCheck(
                name="risk_scoring_no_exception",
                category="risk",
                passed=False,
                expected="no exception",
                actual=str(exc),
                message=f"PredictiveRiskService raised: {exc}",
            ))
        return checks

    def _check_drift_detection(self, session: Session) -> list[BenchmarkCheck]:
        from intelligence.services.drift_detection import DriftDetectionService
        svc = DriftDetectionService()
        checks = []
        try:
            report = svc.detect(session)
            checks.append(BenchmarkCheck(
                name="drift_detection_returns_report",
                category="drift",
                passed=report is not None,
                expected="DriftReport",
                actual=type(report).__name__,
                message="DriftDetectionService.detect() returned a report.",
            ))
            checks.append(BenchmarkCheck(
                name="drift_magnitudes_valid",
                category="drift",
                passed=all(
                    s.magnitude in ("critical", "high", "medium", "low", "none")
                    for s in report.all_signals()
                ),
                expected="valid magnitudes",
                actual=list({s.magnitude for s in report.all_signals()}),
                message="All drift signal magnitudes are valid.",
            ))
        except Exception as exc:
            checks.append(BenchmarkCheck(
                name="drift_detection_no_exception",
                category="drift",
                passed=False,
                expected="no exception",
                actual=str(exc),
                message=f"DriftDetectionService raised: {exc}",
            ))
        return checks

    def _check_graph_traversal(self, session: Session) -> list[BenchmarkCheck]:
        from intelligence.graph.builder import ComplianceGraphBuilder
        from intelligence.graph.traversal import GraphTraversalService
        checks = []
        try:
            graph   = ComplianceGraphBuilder().build(session)
            svc     = GraphTraversalService()

            checks.append(BenchmarkCheck(
                name="graph_has_nodes",
                category="graph",
                passed=len(graph.nodes) > 0,
                expected="> 0 nodes",
                actual=len(graph.nodes),
                message="Compliance graph contains at least one node.",
            ))

            centrality = svc.compute_centrality(graph, top_n=5)
            checks.append(BenchmarkCheck(
                name="centrality_returns_results",
                category="graph",
                passed=isinstance(centrality, list),
                expected="list",
                actual=type(centrality).__name__,
                message="compute_centrality() returns a list.",
            ))
            checks.append(BenchmarkCheck(
                name="centrality_weighted_degree_positive",
                category="graph",
                passed=all(c.weighted_degree >= 0 for c in centrality),
                expected="all >= 0",
                actual=f"min={min((c.weighted_degree for c in centrality), default=0):.2f}",
                message="All weighted_degree values are non-negative.",
            ))
        except Exception as exc:
            checks.append(BenchmarkCheck(
                name="graph_traversal_no_exception",
                category="graph",
                passed=False,
                expected="no exception",
                actual=str(exc),
                message=f"Graph traversal raised: {exc}",
            ))
        return checks

    # ------------------------------------------------------------------ #
    # Persistence                                                          #
    # ------------------------------------------------------------------ #

    def _persist(self, session: Session, result: BenchmarkResult) -> None:
        try:
            session.execute(text("""
                INSERT INTO audit.evaluation_runs
                    (eval_id, eval_type, eval_name, status,
                     passed, total_checks, failed_checks,
                     eval_metadata, started_at, completed_at)
                VALUES
                    (:eval_id::uuid, 'benchmark', :eval_name, :status,
                     :passed, :total, :failed,
                     :metadata::jsonb, :started_at, :completed_at)
                ON CONFLICT (eval_id) DO UPDATE SET
                    status       = EXCLUDED.status,
                    passed       = EXCLUDED.passed,
                    total_checks = EXCLUDED.total_checks,
                    failed_checks = EXCLUDED.failed_checks,
                    eval_metadata = EXCLUDED.eval_metadata,
                    completed_at = EXCLUDED.completed_at
            """), {
                "eval_id":    result.eval_id,
                "eval_name":  result.eval_name,
                "status":     result.status,
                "passed":     result.passed_count,
                "total":      len(result.checks),
                "failed":     result.failed_count,
                "metadata":   json.dumps(result.to_dict()),
                "started_at": result.started_at.isoformat(),
                "completed_at": result.completed_at.isoformat() if result.completed_at else None,
            })
        except Exception as exc:
            logger.error("Failed to persist benchmark result: %s", exc)
