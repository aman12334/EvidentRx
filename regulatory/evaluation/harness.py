"""
Regulatory evaluation harness.

Provides a structured, repeatable evaluation framework for testing the
regulatory intelligence pipeline against known scenarios.  The harness
is used to:

  1. Validate that diffing, drift detection, impact analysis, and
     recommendation generation produce expected outputs for known inputs.
  2. Regression-test the regulatory pipeline when new document versions
     are ingested or scoring heuristics are updated.
  3. Generate audit evidence that the pipeline behaves deterministically
     across evaluation runs.

Evaluation scenarios are fully declarative — each scenario specifies:
  - Input documents (one or more RegulatoryDocuments)
  - Expected outcome assertions (presence, severity, domain coverage, etc.)
  - The services to exercise and the expected call sequence

Design constraints
──────────────────
- The harness NEVER modifies production state
- Scenarios are run in isolation with clean in-memory service instances
- All assertions are deterministic and reproducible
- Failures produce structured EvaluationFailure objects, not exceptions
- No LLM inference is invoked during evaluation
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

log = logging.getLogger("evidentrx.regulatory.evaluation.harness")


class AssertionType(str, Enum):
    DRIFT_SEVERITY_AT_LEAST   = "drift_severity_at_least"
    DRIFT_FINDING_COUNT       = "drift_finding_count"
    DRIFT_TYPE_PRESENT        = "drift_type_present"
    RECOMMENDATION_COUNT      = "recommendation_count"
    RECOMMENDATION_PRIORITY   = "recommendation_priority_at_least"
    DOMAIN_COVERED            = "domain_covered"
    READINESS_BAND            = "readiness_band"
    READINESS_SCORE_GTE       = "readiness_score_gte"
    READINESS_SCORE_LTE       = "readiness_score_lte"
    CITATION_COUNT            = "citation_count"
    IMPACT_SEVERITY_AT_LEAST  = "impact_severity_at_least"
    CUSTOM                    = "custom"


@dataclass
class ScenarioAssertion:
    """A single testable assertion within an evaluation scenario."""
    assertion_id: str
    assertion_type: AssertionType
    description:  str
    expected:     Any           # type depends on assertion_type
    # For CUSTOM assertions, provide a callable(result_bag) → bool
    custom_fn:    Callable[[dict[str, Any]], bool] | None = None


@dataclass
class EvaluationScenario:
    """
    A fully declarative evaluation scenario.

    The harness instantiates fresh service instances, runs the pipeline
    stages specified in `stages`, and checks all assertions against the
    collected results.
    """
    scenario_id:  str
    name:         str
    description:  str
    tenant_id:    str
    stages:       list[str]     # ordered pipeline stages to exercise
    assertions:   list[ScenarioAssertion]
    # Fixtures — provided by the caller
    documents:    list[Any]     = field(default_factory=list)   # RegulatoryDocument
    diffs:        list[Any]     = field(default_factory=list)   # PolicyDiff
    metadata:     dict[str, Any] = field(default_factory=dict)


@dataclass
class AssertionResult:
    """Result of evaluating a single assertion."""
    assertion_id:   str
    assertion_type: AssertionType
    description:    str
    passed:         bool
    actual:         Any
    expected:       Any
    message:        str         = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "assertion_id":   self.assertion_id,
            "assertion_type": self.assertion_type.value,
            "description":    self.description,
            "passed":         self.passed,
            "expected":       str(self.expected),
            "actual":         str(self.actual),
            "message":        self.message,
        }


@dataclass
class EvaluationResult:
    """Complete result for one evaluation scenario run."""
    run_id:         str
    scenario_id:    str
    scenario_name:  str
    tenant_id:      str
    run_at:         datetime
    passed:         bool
    assertion_results: list[AssertionResult]
    stages_executed:   list[str]
    result_bag:        dict[str, Any]    = field(default_factory=dict)
    error:             str | None     = None
    duration_ms:       float             = 0.0

    @property
    def pass_count(self) -> int:
        return sum(1 for r in self.assertion_results if r.passed)

    @property
    def fail_count(self) -> int:
        return sum(1 for r in self.assertion_results if not r.passed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id":          self.run_id,
            "scenario_id":     self.scenario_id,
            "scenario_name":   self.scenario_name,
            "tenant_id":       self.tenant_id,
            "run_at":          self.run_at.isoformat(),
            "passed":          self.passed,
            "pass_count":      self.pass_count,
            "fail_count":      self.fail_count,
            "stages_executed": self.stages_executed,
            "duration_ms":     round(self.duration_ms, 2),
            "error":           self.error,
            "assertions":      [r.to_dict() for r in self.assertion_results],
        }


class RegulatoryEvaluationHarness:
    """
    Runs regulatory pipeline evaluation scenarios in isolation.

    Each run uses fresh in-memory instances of all Phase 13 services to
    ensure complete isolation from production state.  The harness does
    not persist any state across runs.
    """

    def __init__(self) -> None:
        # run_id → EvaluationResult
        self._results: dict[str, EvaluationResult] = {}
        # scenario_id → EvaluationScenario
        self._scenarios: dict[str, EvaluationScenario] = {}

    def register(self, scenario: EvaluationScenario) -> None:
        """Register a scenario for later execution."""
        self._scenarios[scenario.scenario_id] = scenario
        log.debug(
            "RegulatoryEvaluationHarness: registered scenario '%s' (%d assertions)",
            scenario.name, len(scenario.assertions),
        )

    def run(self, scenario_id: str) -> EvaluationResult:
        """
        Execute a registered scenario against a fresh pipeline instance.

        Returns an EvaluationResult regardless of assertion outcomes.
        Raises HarnessError only on unexpected harness-level failures.
        """
        scenario = self._scenarios.get(scenario_id)
        if scenario is None:
            raise HarnessError(f"Scenario {scenario_id} not registered")

        import time
        t0 = time.monotonic()

        run_id = str(uuid.uuid4())
        run_at = datetime.now(tz=UTC)
        result_bag: dict[str, Any] = {}
        stages_executed: list[str] = []
        error: str | None = None

        try:
            result_bag, stages_executed = self._execute_stages(scenario)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            log.warning(
                "RegulatoryEvaluationHarness: scenario '%s' stage error: %s",
                scenario.name, error,
            )

        assertion_results = self._evaluate_assertions(scenario.assertions, result_bag)
        all_passed = all(r.passed for r in assertion_results) and error is None

        duration_ms = (time.monotonic() - t0) * 1000.0
        result = EvaluationResult(
            run_id             = run_id,
            scenario_id        = scenario_id,
            scenario_name      = scenario.name,
            tenant_id          = scenario.tenant_id,
            run_at             = run_at,
            passed             = all_passed,
            assertion_results  = assertion_results,
            stages_executed    = stages_executed,
            result_bag         = {k: str(v)[:200] for k, v in result_bag.items()},
            error              = error,
            duration_ms        = duration_ms,
        )
        self._results[run_id] = result
        log.info(
            "RegulatoryEvaluationHarness: scenario '%s' — %s (%d/%d passed, %.1fms)",
            scenario.name,
            "PASS" if all_passed else "FAIL",
            result.pass_count,
            len(assertion_results),
            duration_ms,
        )
        return result

    def run_all(self) -> list[EvaluationResult]:
        """Run every registered scenario and return all results."""
        results = []
        for sid in list(self._scenarios):
            results.append(self.run(sid))
        return results

    def result_history(self, scenario_id: str) -> list[EvaluationResult]:
        """Return all prior runs for a scenario, newest first."""
        runs = [r for r in self._results.values() if r.scenario_id == scenario_id]
        runs.sort(key=lambda r: r.run_at, reverse=True)
        return runs

    def summary(self) -> dict[str, Any]:
        """Aggregate pass/fail counts across all stored results."""
        total = len(self._results)
        passed = sum(1 for r in self._results.values() if r.passed)
        return {
            "total_runs":     total,
            "passed":         passed,
            "failed":         total - passed,
            "scenarios":      len(self._scenarios),
            "pass_rate":      round(passed / total, 4) if total else 0.0,
        }

    # ── Private ─────────────────────────────────────────────────────────────────

    def _execute_stages(
        self,
        scenario: EvaluationScenario,
    ) -> tuple[dict[str, Any], list[str]]:
        """
        Execute the pipeline stages specified in scenario.stages.

        Uses freshly instantiated service classes (not singletons) to
        guarantee isolation.  Collects results into a result_bag dict.
        """
        from regulatory.diff.drift import DriftDetectionService
        from regulatory.impact.analysis import ImpactAnalysisService
        from regulatory.intelligence.readiness import ComplianceReadinessService
        from regulatory.recommendations.engine import PolicyRecommendationService

        bag: dict[str, Any]    = {}
        executed: list[str]    = []
        tenant_id              = scenario.tenant_id

        drift_svc  = DriftDetectionService()
        impact_svc = ImpactAnalysisService()
        rec_svc    = PolicyRecommendationService()
        ready_svc  = ComplianceReadinessService()

        for stage in scenario.stages:

            if stage == "drift":
                report = drift_svc.detect(
                    tenant_id = tenant_id,
                    documents = scenario.documents,
                    diffs     = scenario.diffs or None,
                )
                bag["drift_report"]       = report
                bag["drift_finding_count"] = len(report.findings)
                bag["drift_severity"]      = report.overall_severity.value
                executed.append("drift")

            elif stage == "impact" and scenario.diffs:
                from regulatory.diff.engine import PolicyDiff
                reports = []
                for diff in scenario.diffs:
                    if isinstance(diff, PolicyDiff):
                        rep = impact_svc.analyze_diff(
                            tenant_id = tenant_id,
                            diff      = diff,
                        )
                        reports.append(rep)
                bag["impact_reports"] = reports
                bag["impact_count"]   = len(reports)
                executed.append("impact")

            elif stage == "recommendations":
                recs = []
                for rep in bag.get("impact_reports", []):
                    recs.extend(rec_svc.generate_from_impact(tenant_id, rep))
                drift_rep = bag.get("drift_report")
                if drift_rep:
                    recs.extend(rec_svc.generate_from_drift(tenant_id, drift_rep))
                bag["recommendations"]      = recs
                bag["recommendation_count"] = len(recs)
                executed.append("recommendations")

            elif stage == "readiness":
                drift_rep  = bag.get("drift_report")
                recs_list  = bag.get("recommendations", [])
                snapshot   = ready_svc.assess(
                    tenant_id       = tenant_id,
                    documents       = scenario.documents,
                    drift_findings  = drift_rep.findings if drift_rep else [],
                    recommendations = recs_list,
                )
                bag["readiness_snapshot"] = snapshot
                bag["readiness_score"]    = snapshot.score
                bag["readiness_band"]     = snapshot.band.value
                executed.append("readiness")

        return bag, executed

    def _evaluate_assertions(
        self,
        assertions: list[ScenarioAssertion],
        bag:        dict[str, Any],
    ) -> list[AssertionResult]:
        results: list[AssertionResult] = []
        for assertion in assertions:
            result = self._check_assertion(assertion, bag)
            results.append(result)
        return results

    def _check_assertion(
        self,
        assertion: ScenarioAssertion,
        bag:       dict[str, Any],
    ) -> AssertionResult:
        atype   = assertion.assertion_type
        exp     = assertion.expected
        passed  = False
        actual  = None
        message = ""

        try:
            if atype == AssertionType.DRIFT_FINDING_COUNT:
                actual = bag.get("drift_finding_count", 0)
                passed = actual == exp
                message = f"Expected {exp} drift findings, got {actual}"

            elif atype == AssertionType.DRIFT_SEVERITY_AT_LEAST:
                _order = ["informational", "low", "medium", "high", "critical"]
                actual = bag.get("drift_severity", "informational")
                passed = _order.index(actual) >= _order.index(exp)
                message = f"Expected severity ≥ {exp}, got {actual}"

            elif atype == AssertionType.DRIFT_TYPE_PRESENT:
                report = bag.get("drift_report")
                if report:
                    types = {f.drift_type.value for f in report.findings}
                    actual = list(types)
                    passed = exp in types
                    message = f"Expected drift type '{exp}' in {actual}"
                else:
                    actual = []
                    message = "No drift report in result bag"

            elif atype == AssertionType.RECOMMENDATION_COUNT:
                actual = bag.get("recommendation_count", 0)
                passed = actual == exp
                message = f"Expected {exp} recommendations, got {actual}"

            elif atype == AssertionType.RECOMMENDATION_PRIORITY:
                _order = ["low", "normal", "high", "urgent"]
                recs   = bag.get("recommendations", [])
                if recs:
                    top_priority = max(recs, key=lambda r: _order.index(r.priority.value))
                    actual       = top_priority.priority.value
                    passed       = _order.index(actual) >= _order.index(exp)
                    message      = f"Expected priority ≥ {exp}, got {actual}"
                else:
                    actual = "none"
                    message = "No recommendations in result bag"

            elif atype == AssertionType.DOMAIN_COVERED:
                snapshot = bag.get("readiness_snapshot")
                if snapshot:
                    actual = snapshot.domains_covered
                    passed = exp in actual
                    message = f"Expected domain '{exp}' in {actual}"
                else:
                    actual = []
                    message = "No readiness snapshot in result bag"

            elif atype == AssertionType.READINESS_BAND:
                actual = bag.get("readiness_band", "unknown")
                passed = actual == exp
                message = f"Expected readiness band '{exp}', got '{actual}'"

            elif atype == AssertionType.READINESS_SCORE_GTE:
                actual = bag.get("readiness_score", 0.0)
                passed = actual >= exp
                message = f"Expected score ≥ {exp}, got {actual:.4f}"

            elif atype == AssertionType.READINESS_SCORE_LTE:
                actual = bag.get("readiness_score", 1.0)
                passed = actual <= exp
                message = f"Expected score ≤ {exp}, got {actual:.4f}"

            elif atype == AssertionType.CUSTOM:
                if assertion.custom_fn:
                    passed = assertion.custom_fn(bag)
                    actual = "custom_fn"
                    message = "Custom assertion function returned False" if not passed else ""
                else:
                    message = "Custom assertion has no custom_fn"

            elif atype == AssertionType.IMPACT_SEVERITY_AT_LEAST:
                _order  = ["informational", "low", "medium", "high", "critical"]
                reports = bag.get("impact_reports", [])
                if reports:
                    top = max(reports, key=lambda r: _order.index(r.severity))
                    actual = top.severity
                    passed = _order.index(actual) >= _order.index(exp)
                    message = f"Expected impact severity ≥ {exp}, got {actual}"
                else:
                    actual = "none"
                    message = "No impact reports in result bag"

            elif atype == AssertionType.CITATION_COUNT:
                # Check contexts stored in bag if available
                contexts = bag.get("policy_contexts", [])
                total_cites = sum(len(ctx.citations) for ctx in contexts)
                actual = total_cites
                passed = actual == exp
                message = f"Expected {exp} citations, got {actual}"

        except Exception as exc:
            message = f"Assertion check error: {exc}"
            actual  = None

        return AssertionResult(
            assertion_id   = assertion.assertion_id,
            assertion_type = assertion.assertion_type,
            description    = assertion.description,
            passed         = passed,
            actual         = actual,
            expected       = exp,
            message        = message,
        )


# ── Helpers ─────────────────────────────────────────────────────────────────────

def make_scenario(
    name:        str,
    description: str,
    tenant_id:   str,
    stages:      list[str],
    documents:   list | None = None,
    diffs:       list | None = None,
    assertions:  list[ScenarioAssertion] | None = None,
) -> EvaluationScenario:
    """Convenience constructor for EvaluationScenario."""
    return EvaluationScenario(
        scenario_id  = str(uuid.uuid4()),
        name         = name,
        description  = description,
        tenant_id    = tenant_id,
        stages       = stages,
        assertions   = assertions or [],
        documents    = documents or [],
        diffs        = diffs or [],
    )


def make_assertion(
    assertion_type: AssertionType,
    expected:       Any,
    description:    str = "",
    custom_fn:      Callable[[dict], bool] | None = None,
) -> ScenarioAssertion:
    """Convenience constructor for ScenarioAssertion."""
    return ScenarioAssertion(
        assertion_id   = str(uuid.uuid4()),
        assertion_type = assertion_type,
        description    = description or assertion_type.value,
        expected       = expected,
        custom_fn      = custom_fn,
    )


# ── Exceptions ─────────────────────────────────────────────────────────────────

class HarnessError(Exception):
    pass


# ── Singleton ──────────────────────────────────────────────────────────────────

_harness: RegulatoryEvaluationHarness | None = None


def get_evaluation_harness() -> RegulatoryEvaluationHarness:
    global _harness
    if _harness is None:
        _harness = RegulatoryEvaluationHarness()
    return _harness
