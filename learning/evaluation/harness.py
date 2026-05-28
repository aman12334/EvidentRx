"""
Continuous evaluation harness.

Provides enterprise-grade evaluation infrastructure for the investigation
runtime. Supports:
  - Historical replay evaluation (re-run prior investigations)
  - Regression testing (verify behaviour didn't degrade after changes)
  - Longitudinal comparison (track quality across time)
  - Cross-model benchmarking (compare model routing configurations)
  - Prompt evaluation (score prompt versions against golden datasets)
  - Workflow evaluation (measure end-to-end investigation quality)

Evaluation philosophy
─────────────────────
  All evaluations are deterministic when given the same:
    - Input case / evidence snapshot
    - Prompt version
    - Model routing configuration
    - Calibration snapshot version

  Results are versioned and stored in evaluation_runs. No evaluation
  run is ever overwritten — new runs create new records.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime    import datetime, timezone
from enum        import Enum
from typing      import Any, Callable, Optional

log = logging.getLogger("evidentrx.learning.evaluation.harness")


class EvaluationType(str, Enum):
    REPLAY          = "replay"           # re-run against historical case
    REGRESSION      = "regression"       # verify no quality degradation
    LONGITUDINAL    = "longitudinal"     # track quality over time
    CROSS_MODEL     = "cross_model"      # compare model configurations
    PROMPT          = "prompt"           # score a prompt version
    WORKFLOW        = "workflow"         # end-to-end workflow quality


class EvaluationStatus(str, Enum):
    PENDING  = "pending"
    RUNNING  = "running"
    COMPLETED= "completed"
    FAILED   = "failed"
    CANCELLED= "cancelled"


@dataclass
class EvaluationCase:
    """
    A single test case for evaluation.

    Contains the frozen input state and the expected (ground-truth)
    outputs for scoring.
    """
    case_id:         str
    tenant_id:       str
    input_snapshot:  dict[str, Any]     # frozen case + evidence state
    expected_outputs: dict[str, Any]    # ground-truth labels
    metadata:        dict[str, Any]     = field(default_factory=dict)
    created_at:      datetime           = field(default_factory=lambda: datetime.now(tz=timezone.utc))


@dataclass
class EvaluationMetrics:
    """Metrics computed for a single evaluation case."""
    case_id:              str
    reasoning_score:      Optional[float]    # 0–1: quality of reasoning chain
    outcome_accuracy:     Optional[float]    # 1 if outcome matches expected, 0 otherwise
    confidence_calibration: Optional[float]  # |predicted – actual| for this case
    recommendation_match: Optional[float]    # fraction of expected recs present
    hallucination_detected: bool             = False
    latency_seconds:      Optional[float]    = None
    extra:                dict[str, Any]     = field(default_factory=dict)


@dataclass
class EvaluationRun:
    """
    A complete evaluation run against a benchmark suite.

    One run covers N cases and produces per-case and aggregate metrics.
    """
    run_id:            str
    evaluation_type:   EvaluationType
    tenant_id:         str
    benchmark_id:      str
    prompt_version:    Optional[str]
    model_config:      Optional[str]
    calibration_version: Optional[str]
    status:            EvaluationStatus
    started_at:        datetime
    finished_at:       Optional[datetime]    = None
    case_metrics:      list[EvaluationMetrics] = field(default_factory=list)
    aggregate:         dict[str, Any]          = field(default_factory=dict)
    triggered_by:      str                     = "system"
    run_config:        dict[str, Any]          = field(default_factory=dict)
    content_hash:      str                     = ""

    @property
    def avg_reasoning_score(self) -> Optional[float]:
        scores = [m.reasoning_score for m in self.case_metrics if m.reasoning_score is not None]
        return round(sum(scores) / len(scores), 4) if scores else None

    @property
    def outcome_accuracy(self) -> Optional[float]:
        accs = [m.outcome_accuracy for m in self.case_metrics if m.outcome_accuracy is not None]
        return round(sum(accs) / len(accs), 4) if accs else None

    @property
    def hallucination_rate(self) -> float:
        total = len(self.case_metrics)
        return sum(1 for m in self.case_metrics if m.hallucination_detected) / total if total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id":               self.run_id,
            "evaluation_type":      self.evaluation_type.value,
            "tenant_id":            self.tenant_id,
            "benchmark_id":         self.benchmark_id,
            "prompt_version":       self.prompt_version,
            "model_config":         self.model_config,
            "calibration_version":  self.calibration_version,
            "status":               self.status.value,
            "started_at":           self.started_at.isoformat(),
            "finished_at":          self.finished_at.isoformat() if self.finished_at else None,
            "case_count":           len(self.case_metrics),
            "avg_reasoning_score":  self.avg_reasoning_score,
            "outcome_accuracy":     self.outcome_accuracy,
            "hallucination_rate":   self.hallucination_rate,
            "content_hash":         self.content_hash,
            "triggered_by":         self.triggered_by,
        }


class EvaluationHarness:
    """
    Orchestrates evaluation runs against benchmark suites.

    The harness:
      1. Loads the benchmark cases
      2. Runs each case through the evaluator function
      3. Scores each result against expected outputs
      4. Persists the run record with full metrics
    """

    def __init__(
        self,
        db_writer:     Optional[Callable] = None,
        event_emitter: Optional[Callable] = None,
    ) -> None:
        self._runs:     dict[str, EvaluationRun] = {}
        self._db_writer = db_writer
        self._emitter   = event_emitter

    async def run(
        self,
        benchmark_id:       str,
        cases:              list[EvaluationCase],
        evaluator:          Callable,          # async (EvaluationCase) → dict
        evaluation_type:    EvaluationType,
        tenant_id:          str,
        triggered_by:       str               = "system",
        prompt_version:     Optional[str]     = None,
        model_config:       Optional[str]     = None,
        calibration_version: Optional[str]   = None,
        run_config:         Optional[dict]    = None,
    ) -> EvaluationRun:
        """
        Execute an evaluation run.

        Parameters
        ----------
        cases      : List of test cases to evaluate
        evaluator  : Async function that takes an EvaluationCase and returns
                     a dict of {reasoning_trace, outcome, recommendations, ...}
        """
        run_id = str(uuid.uuid4())
        run    = EvaluationRun(
            run_id               = run_id,
            evaluation_type      = evaluation_type,
            tenant_id            = tenant_id,
            benchmark_id         = benchmark_id,
            prompt_version       = prompt_version,
            model_config         = model_config,
            calibration_version  = calibration_version,
            status               = EvaluationStatus.RUNNING,
            started_at           = datetime.now(tz=timezone.utc),
            triggered_by         = triggered_by,
            run_config           = run_config or {},
        )
        self._runs[run_id] = run

        log.info(
            "EvaluationHarness: starting run %s (%s, %d cases)",
            run_id[:8], evaluation_type.value, len(cases),
        )

        try:
            for case in cases:
                metrics = await self._evaluate_case(case, evaluator)
                run.case_metrics.append(metrics)

            run.aggregate    = self._compute_aggregate(run)
            run.content_hash = _hash_run(run)
            run.status       = EvaluationStatus.COMPLETED
            run.finished_at  = datetime.now(tz=timezone.utc)

            log.info(
                "EvaluationHarness: run %s COMPLETED — accuracy=%.3f hallucinations=%.3f",
                run_id[:8],
                run.outcome_accuracy or 0.0,
                run.hallucination_rate,
            )
        except Exception as exc:
            run.status      = EvaluationStatus.FAILED
            run.finished_at = datetime.now(tz=timezone.utc)
            run.aggregate["error"] = str(exc)
            log.error("EvaluationHarness: run %s FAILED: %s", run_id[:8], exc)

        # Persist
        if self._db_writer:
            try:
                await self._db_writer(run)
            except Exception as exc:
                log.error("EvaluationHarness: persist failed: %s", exc)

        return run

    async def _evaluate_case(
        self,
        case:      EvaluationCase,
        evaluator: Callable,
    ) -> EvaluationMetrics:
        """Run one case through the evaluator and score the result."""
        import time
        start = time.perf_counter()
        try:
            result = await evaluator(case)
        except Exception as exc:
            log.warning("EvaluationHarness: case %s failed: %s", case.case_id[:8], exc)
            return EvaluationMetrics(case_id=case.case_id, reasoning_score=0.0,
                                     outcome_accuracy=0.0, confidence_calibration=1.0,
                                     recommendation_match=0.0)
        latency = time.perf_counter() - start

        # Score against expected outputs
        expected = case.expected_outputs
        reasoning_score      = _score_reasoning(result, expected)
        outcome_accuracy     = _score_outcome(result, expected)
        confidence_calibration= _score_calibration(result, expected)
        recommendation_match = _score_recommendations(result, expected)
        hallucination        = result.get("hallucination_detected", False)

        return EvaluationMetrics(
            case_id               = case.case_id,
            reasoning_score       = reasoning_score,
            outcome_accuracy      = outcome_accuracy,
            confidence_calibration= confidence_calibration,
            recommendation_match  = recommendation_match,
            hallucination_detected= hallucination,
            latency_seconds       = round(latency, 3),
        )

    def _compute_aggregate(self, run: EvaluationRun) -> dict[str, Any]:
        """Compute aggregate statistics across all case metrics."""
        metrics = run.case_metrics
        if not metrics:
            return {}

        def _avg(vals: list) -> Optional[float]:
            clean = [v for v in vals if v is not None]
            return round(sum(clean) / len(clean), 4) if clean else None

        return {
            "total_cases":          len(metrics),
            "avg_reasoning_score":  _avg([m.reasoning_score for m in metrics]),
            "outcome_accuracy":     _avg([m.outcome_accuracy for m in metrics]),
            "avg_calibration_error":_avg([m.confidence_calibration for m in metrics]),
            "avg_rec_match":        _avg([m.recommendation_match for m in metrics]),
            "hallucination_rate":   run.hallucination_rate,
            "avg_latency_seconds":  _avg([m.latency_seconds for m in metrics]),
        }

    def get_run(self, run_id: str) -> Optional[EvaluationRun]:
        return self._runs.get(run_id)

    def list_runs(
        self,
        tenant_id:        str,
        evaluation_type:  Optional[EvaluationType] = None,
        limit:            int = 50,
    ) -> list[EvaluationRun]:
        runs = [r for r in self._runs.values() if r.tenant_id == tenant_id]
        if evaluation_type:
            runs = [r for r in runs if r.evaluation_type == evaluation_type]
        return sorted(runs, key=lambda r: r.started_at, reverse=True)[:limit]


# ── Scoring helpers ───────────────────────────────────────────────────────────

def _score_reasoning(result: dict, expected: dict) -> Optional[float]:
    """Compare reasoning steps against expected reasoning patterns."""
    expected_steps = expected.get("reasoning_keywords", [])
    actual_trace   = result.get("reasoning_trace", "")
    if not expected_steps:
        return None
    matched = sum(1 for kw in expected_steps if kw.lower() in actual_trace.lower())
    return round(matched / len(expected_steps), 4)


def _score_outcome(result: dict, expected: dict) -> Optional[float]:
    """Binary accuracy: did the outcome match?"""
    expected_outcome = expected.get("outcome")
    actual_outcome   = result.get("outcome")
    if expected_outcome is None:
        return None
    return 1.0 if expected_outcome == actual_outcome else 0.0


def _score_calibration(result: dict, expected: dict) -> Optional[float]:
    """Confidence calibration error: |predicted - actual|."""
    predicted = result.get("confidence")
    actual    = expected.get("actual_probability")
    if predicted is None or actual is None:
        return None
    return round(abs(predicted - actual), 4)


def _score_recommendations(result: dict, expected: dict) -> Optional[float]:
    """Fraction of expected recommendations present in actual output."""
    expected_recs = set(expected.get("expected_recommendations", []))
    actual_recs   = set(result.get("recommendations", []))
    if not expected_recs:
        return None
    return round(len(expected_recs & actual_recs) / len(expected_recs), 4)


def _hash_run(run: EvaluationRun) -> str:
    payload = json.dumps(
        {
            "benchmark_id":    run.benchmark_id,
            "prompt_version":  run.prompt_version,
            "model_config":    run.model_config,
            "calibration":     run.calibration_version,
            "case_count":      len(run.case_metrics),
            "aggregate":       run.aggregate,
        },
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()
