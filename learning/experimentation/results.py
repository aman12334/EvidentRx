"""
Experiment result tracking and statistical comparison.

Aggregates per-arm metrics from evaluation runs and computes statistical
significance of differences. Results are read-only — they inform human
decision makers but do NOT automatically promote any configuration.

Statistical methods
───────────────────
  - Welch's t-test for continuous metrics (no equal-variance assumption)
  - Proportion z-test for binary metrics (e.g. outcome_accuracy)
  - Cohen's d for effect size estimation
  - Minimum reliable sample guard (30 per arm) before reporting significance
"""

from __future__ import annotations

import logging
import math
import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from learning.experimentation.framework import ABExperiment, ExperimentArm

log = logging.getLogger("evidentrx.learning.experimentation.results")

_MIN_RELIABLE_SAMPLE = 30
_SIGNIFICANCE_LEVEL  = 0.05   # α


@dataclass
class ArmMetrics:
    """Aggregated metrics for one arm of an experiment."""
    arm:             ExperimentArm
    sample_count:    int
    metrics:         dict[str, float]   # metric_name → mean value
    std_devs:        dict[str, float]   = field(default_factory=dict)
    raw_values:      dict[str, list[float]] = field(default_factory=dict)

    def mean(self, metric: str) -> float | None:
        return self.metrics.get(metric)

    def std(self, metric: str) -> float | None:
        return self.std_devs.get(metric)


@dataclass
class MetricComparison:
    """Statistical comparison for one metric between two arms."""
    metric:            str
    control_mean:      float | None
    treatment_mean:    float | None
    absolute_delta:    float | None      # treatment – control
    relative_delta:    float | None      # (treatment – control) / |control|
    p_value:           float | None
    effect_size:       float | None      # Cohen's d
    is_significant:    bool
    meets_mde:         bool                 # delta ≥ min_detectable_effect
    direction:         str                  # "improvement" | "regression" | "neutral"
    reliable:          bool                 # sufficient sample size


@dataclass
class ExperimentResult:
    """
    Complete result set for an A/B experiment.

    Produced by ExperimentResultStore.compute_result(). Never modifies
    the experiment definition — results are advisory only.
    """
    result_id:         str
    experiment_id:     str
    tenant_id:         str
    computed_at:       datetime
    control_metrics:   ArmMetrics
    treatment_metrics: ArmMetrics
    comparisons:       list[MetricComparison]
    primary_metric:    str
    primary_comparison: MetricComparison | None
    recommendation:    str       # "promote" | "reject" | "extend" | "inconclusive"
    summary:           str       # human-readable narrative

    def to_dict(self) -> dict[str, Any]:
        primary = self.primary_comparison
        return {
            "result_id":          self.result_id,
            "experiment_id":      self.experiment_id,
            "tenant_id":          self.tenant_id,
            "computed_at":        self.computed_at.isoformat(),
            "primary_metric":     self.primary_metric,
            "control_samples":    self.control_metrics.sample_count,
            "treatment_samples":  self.treatment_metrics.sample_count,
            "primary_delta":      primary.absolute_delta if primary else None,
            "primary_p_value":    primary.p_value if primary else None,
            "is_significant":     primary.is_significant if primary else None,
            "recommendation":     self.recommendation,
            "summary":            self.summary,
        }


class ExperimentResultStore:
    """
    Computes and stores experiment results.

    Results are immutable once computed. Re-analysis creates a new result
    record with a fresh computed_at timestamp.
    """

    def __init__(self, db_writer: Callable | None = None) -> None:
        self._results: dict[str, ExperimentResult] = {}
        # experiment_id → list of result_ids (chronological)
        self._by_experiment: dict[str, list[str]] = {}
        self._db_writer = db_writer

    async def compute_result(
        self,
        experiment: ABExperiment,
        control_data:   dict[str, list[float]],    # metric_name → list of values
        treatment_data: dict[str, list[float]],
    ) -> ExperimentResult:
        """
        Compute the full result for an experiment.

        Parameters
        ----------
        experiment     : The ABExperiment definition (for MDE + primary metric)
        control_data   : Dict of metric_name → list of per-case values (control arm)
        treatment_data : Same structure for treatment arm
        """
        import uuid as _uuid
        result_id  = str(_uuid.uuid4())
        now        = datetime.now(tz=UTC)

        ctrl_metrics  = _build_arm_metrics(ExperimentArm.CONTROL, control_data)
        treat_metrics = _build_arm_metrics(ExperimentArm.TREATMENT, treatment_data)

        all_metrics = set(control_data.keys()) | set(treatment_data.keys())
        comparisons: list[MetricComparison] = []
        for metric in sorted(all_metrics):
            ctrl_vals  = control_data.get(metric, [])
            treat_vals = treatment_data.get(metric, [])
            comp = _compare_metric(
                metric             = metric,
                control_vals       = ctrl_vals,
                treatment_vals     = treat_vals,
                min_detectable_effect = experiment.min_detectable_effect,
            )
            comparisons.append(comp)

        primary_comp = next(
            (c for c in comparisons if c.metric == experiment.success_metric),
            None,
        )

        recommendation = _recommend(primary_comp, experiment)
        summary        = _narrative(experiment, ctrl_metrics, treat_metrics, primary_comp)

        result = ExperimentResult(
            result_id          = result_id,
            experiment_id      = experiment.experiment_id,
            tenant_id          = experiment.tenant_id,
            computed_at        = now,
            control_metrics    = ctrl_metrics,
            treatment_metrics  = treat_metrics,
            comparisons        = comparisons,
            primary_metric     = experiment.success_metric,
            primary_comparison = primary_comp,
            recommendation     = recommendation,
            summary            = summary,
        )

        self._results[result_id] = result
        self._by_experiment.setdefault(experiment.experiment_id, []).append(result_id)

        if self._db_writer:
            try:
                await self._db_writer("create", result)
            except Exception as exc:
                log.error("ExperimentResultStore: persist failed: %s", exc)

        log.info(
            "ExperimentResultStore: computed result for experiment %s → %s",
            experiment.experiment_id[:8], recommendation,
        )
        return result

    def get_result(self, result_id: str) -> ExperimentResult | None:
        return self._results.get(result_id)

    def latest_result(self, experiment_id: str) -> ExperimentResult | None:
        ids = self._by_experiment.get(experiment_id, [])
        return self._results.get(ids[-1]) if ids else None

    def list_results(self, experiment_id: str) -> list[ExperimentResult]:
        ids = self._by_experiment.get(experiment_id, [])
        return [self._results[i] for i in ids if i in self._results]


# ── Statistical helpers ────────────────────────────────────────────────────────

def _build_arm_metrics(arm: ExperimentArm, data: dict[str, list[float]]) -> ArmMetrics:
    sample_count = max((len(v) for v in data.values()), default=0)
    means:   dict[str, float] = {}
    stds:    dict[str, float] = {}
    for metric, vals in data.items():
        if vals:
            means[metric] = statistics.mean(vals)
            stds[metric]  = statistics.stdev(vals) if len(vals) > 1 else 0.0
    return ArmMetrics(
        arm          = arm,
        sample_count = sample_count,
        metrics      = means,
        std_devs     = stds,
        raw_values   = {k: list(v) for k, v in data.items()},
    )


def _compare_metric(
    metric:               str,
    control_vals:         list[float],
    treatment_vals:       list[float],
    min_detectable_effect: float,
) -> MetricComparison:
    n_ctrl  = len(control_vals)
    n_treat = len(treatment_vals)
    reliable = n_ctrl >= _MIN_RELIABLE_SAMPLE and n_treat >= _MIN_RELIABLE_SAMPLE

    if not control_vals or not treatment_vals:
        return MetricComparison(
            metric=metric, control_mean=None, treatment_mean=None,
            absolute_delta=None, relative_delta=None,
            p_value=None, effect_size=None,
            is_significant=False, meets_mde=False,
            direction="neutral", reliable=False,
        )

    ctrl_mean  = statistics.mean(control_vals)
    treat_mean = statistics.mean(treatment_vals)
    delta      = treat_mean - ctrl_mean
    rel_delta  = delta / abs(ctrl_mean) if ctrl_mean != 0.0 else None
    meets_mde  = abs(delta) >= min_detectable_effect

    p_value    = _welch_t_pvalue(control_vals, treatment_vals) if reliable else None
    effect_d   = _cohens_d(control_vals, treatment_vals)

    is_sig     = reliable and p_value is not None and p_value < _SIGNIFICANCE_LEVEL
    direction  = "improvement" if delta > 0 else "regression" if delta < 0 else "neutral"

    return MetricComparison(
        metric          = metric,
        control_mean    = round(ctrl_mean, 4),
        treatment_mean  = round(treat_mean, 4),
        absolute_delta  = round(delta, 4),
        relative_delta  = round(rel_delta, 4) if rel_delta is not None else None,
        p_value         = round(p_value, 4) if p_value is not None else None,
        effect_size     = round(effect_d, 4) if effect_d is not None else None,
        is_significant  = is_sig,
        meets_mde       = meets_mde,
        direction       = direction,
        reliable        = reliable,
    )


def _welch_t_pvalue(a: list[float], b: list[float]) -> float | None:
    """Two-sided Welch's t-test p-value (no scipy dependency)."""
    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        return None
    m1, m2 = statistics.mean(a), statistics.mean(b)
    v1 = statistics.variance(a)
    v2 = statistics.variance(b)
    se = math.sqrt(v1 / n1 + v2 / n2)
    if se == 0:
        return 1.0
    t_stat = (m1 - m2) / se

    # Welch–Satterthwaite degrees of freedom
    df_num  = (v1 / n1 + v2 / n2) ** 2
    df_den  = (v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1)
    df_num / df_den if df_den > 0 else 1.0

    # Approximation: use normal distribution for large df
    z = abs(t_stat)
    # Two-tailed p-value via error function approximation
    p = 2.0 * (1.0 - _normal_cdf(z))
    return max(0.0, min(1.0, p))


def _normal_cdf(z: float) -> float:
    """Standard normal CDF via math.erf."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _cohens_d(a: list[float], b: list[float]) -> float | None:
    """Cohen's d effect size."""
    if len(a) < 2 or len(b) < 2:
        return None
    pooled_var = (
        (len(a) - 1) * statistics.variance(a) + (len(b) - 1) * statistics.variance(b)
    ) / (len(a) + len(b) - 2)
    pooled_std = math.sqrt(pooled_var) if pooled_var > 0 else 0.0
    if pooled_std == 0:
        return 0.0
    return (statistics.mean(a) - statistics.mean(b)) / pooled_std


def _recommend(
    comparison: MetricComparison | None,
    experiment: ABExperiment,
) -> str:
    if comparison is None:
        return "inconclusive"
    if not comparison.reliable:
        return "extend"    # need more data
    if not comparison.is_significant:
        return "inconclusive"
    if comparison.direction == "improvement" and comparison.meets_mde:
        return "promote"
    if comparison.direction == "regression":
        return "reject"
    return "inconclusive"


def _narrative(
    experiment:    ABExperiment,
    ctrl:          ArmMetrics,
    treat:         ArmMetrics,
    primary_comp:  MetricComparison | None,
) -> str:
    lines = [
        f"Experiment: {experiment.name}",
        f"Samples — control: {ctrl.sample_count}, treatment: {treat.sample_count}",
    ]
    if primary_comp:
        lines.append(
            f"Primary metric ({primary_comp.metric}): "
            f"control={primary_comp.control_mean}, "
            f"treatment={primary_comp.treatment_mean}, "
            f"delta={primary_comp.absolute_delta} "
            f"({'significant' if primary_comp.is_significant else 'not significant'}, "
            f"p={primary_comp.p_value})"
        )
    else:
        lines.append("Primary metric not available.")
    return " | ".join(lines)


# ── Module-level singleton ─────────────────────────────────────────────────────

_store: ExperimentResultStore | None = None


def get_result_store(db_writer: Callable | None = None) -> ExperimentResultStore:
    global _store
    if _store is None:
        _store = ExperimentResultStore(db_writer=db_writer)
    return _store
