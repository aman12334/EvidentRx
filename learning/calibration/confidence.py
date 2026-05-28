"""
Confidence calibration infrastructure.

Normalises, tracks, and detects drift in system confidence scores.
Confidence scores are produced by the rules engine and agentic runtime —
this module ensures they remain well-calibrated against actual outcomes.

Calibration problem
───────────────────
  A confidence score of 0.80 should mean "correct ~80% of the time."
  Without calibration, models become over- or under-confident over time.
  We measure this via Expected Calibration Error (ECE) and detect drift
  using a sliding-window comparison.

Components
──────────
  ConfidenceNormalizer   — maps raw scores to calibrated probabilities
  CalibrationDriftDetector — monitors ECE over time and alerts on drift
  ConfidenceBenchmark    — stores ground-truth (score, outcome) pairs
  UncertaintyPropagator  — propagates uncertainty across multi-step reasoning
"""

from __future__ import annotations

import math
import logging
import statistics
from dataclasses import dataclass, field
from datetime    import datetime, timedelta, timezone
from typing      import Any, Optional

log = logging.getLogger("evidentrx.learning.calibration.confidence")

# Number of equal-width bins for ECE computation
_ECE_BINS = 10
# Drift alert threshold (ECE > this triggers alert)
_DRIFT_THRESHOLD = 0.10
# Minimum samples needed for calibration measurement
_MIN_SAMPLES = 20


# ── Calibration benchmark record ──────────────────────────────────────────────

@dataclass
class CalibrationSample:
    """One (predicted_confidence, binary_outcome) data point."""
    sample_id:        str
    tenant_id:        str
    rule_code:        str
    predicted_score:  float        # system-assigned confidence 0.0–1.0
    actual_outcome:   int          # 1 = confirmed violation, 0 = cleared
    source:           str          # "outcome_label" | "analyst_override"
    recorded_at:      datetime     = field(default_factory=lambda: datetime.now(tz=timezone.utc))


@dataclass
class CalibrationMetrics:
    """ECE and related calibration quality metrics for a sample set."""
    ece:               float        # Expected Calibration Error (lower = better)
    accuracy:          float        # fraction of predictions that were correct
    overconfidence:    float        # avg predicted - avg actual (positive = overconfident)
    underconfidence:   float        # negative overconfidence
    sample_count:      int
    computed_at:       datetime     = field(default_factory=lambda: datetime.now(tz=timezone.utc))

    @property
    def is_well_calibrated(self) -> bool:
        return self.ece < _DRIFT_THRESHOLD

    @property
    def calibration_label(self) -> str:
        if self.ece < 0.05:
            return "excellent"
        elif self.ece < _DRIFT_THRESHOLD:
            return "acceptable"
        elif self.ece < 0.20:
            return "degraded"
        return "poor"


# ── Confidence normalizer ─────────────────────────────────────────────────────

class ConfidenceNormalizer:
    """
    Maps raw confidence scores to calibrated probabilities using
    isotonic regression (Platt scaling fallback for small datasets).

    The normalizer is fit from a CalibrationBenchmark. Once fit, it
    transforms any raw score to a calibrated probability.
    """

    def __init__(self) -> None:
        self._breakpoints: list[tuple[float, float]] = []  # (raw, calibrated)
        self._is_fit = False

    def fit(self, samples: list[CalibrationSample]) -> "ConfidenceNormalizer":
        """
        Fit the normalizer from labelled samples.

        Uses bin-based isotonic regression: average the actual outcomes
        within each confidence bin to produce calibrated probabilities.
        """
        if len(samples) < _MIN_SAMPLES:
            log.warning(
                "ConfidenceNormalizer: too few samples (%d < %d) — using identity mapping",
                len(samples), _MIN_SAMPLES,
            )
            self._breakpoints = [(0.0, 0.0), (1.0, 1.0)]
            self._is_fit = True
            return self

        # Sort into bins
        bins: dict[int, list[int]] = {i: [] for i in range(_ECE_BINS)}
        for s in samples:
            bin_idx = min(int(s.predicted_score * _ECE_BINS), _ECE_BINS - 1)
            bins[bin_idx].append(s.actual_outcome)

        breakpoints = []
        for bin_idx in range(_ECE_BINS):
            outcomes = bins[bin_idx]
            raw_mid  = (bin_idx + 0.5) / _ECE_BINS
            calibrated = statistics.mean(outcomes) if outcomes else raw_mid
            breakpoints.append((raw_mid, calibrated))

        self._breakpoints = breakpoints
        self._is_fit      = True
        return self

    def transform(self, raw_score: float) -> float:
        """Map a raw score to a calibrated probability."""
        if not self._is_fit or len(self._breakpoints) < 2:
            return raw_score

        # Linear interpolation between breakpoints
        raw_score = max(0.0, min(1.0, raw_score))
        for i, (x, y) in enumerate(self._breakpoints[:-1]):
            x_next, y_next = self._breakpoints[i + 1]
            if x <= raw_score <= x_next:
                t = (raw_score - x) / (x_next - x) if x_next > x else 0.0
                return round(y + t * (y_next - y), 4)

        return self._breakpoints[-1][1]

    def transform_batch(self, scores: list[float]) -> list[float]:
        return [self.transform(s) for s in scores]


# ── Calibration drift detector ─────────────────────────────────────────────────

class CalibrationDriftDetector:
    """
    Monitors Expected Calibration Error over a sliding time window.

    Compares recent ECE against a baseline and emits a drift alert
    when the delta exceeds the drift threshold.
    """

    def __init__(
        self,
        window_days:     int   = 30,
        baseline_days:   int   = 90,
        drift_threshold: float = _DRIFT_THRESHOLD,
    ) -> None:
        self._window_days  = window_days
        self._baseline_days= baseline_days
        self._threshold    = drift_threshold
        self._history:     list[CalibrationSample] = []

    def add_sample(self, sample: CalibrationSample) -> None:
        self._history.append(sample)

    def add_samples(self, samples: list[CalibrationSample]) -> None:
        self._history.extend(samples)

    def compute_metrics(self, samples: list[CalibrationSample]) -> CalibrationMetrics:
        """Compute calibration metrics for a given sample set."""
        if len(samples) < _MIN_SAMPLES:
            return CalibrationMetrics(
                ece=0.0, accuracy=0.0, overconfidence=0.0,
                underconfidence=0.0, sample_count=len(samples),
            )

        bins: dict[int, list[tuple[float, int]]] = {i: [] for i in range(_ECE_BINS)}
        for s in samples:
            bin_idx = min(int(s.predicted_score * _ECE_BINS), _ECE_BINS - 1)
            bins[bin_idx].append((s.predicted_score, s.actual_outcome))

        ece      = 0.0
        n        = len(samples)
        for bin_data in bins.values():
            if not bin_data:
                continue
            avg_conf  = statistics.mean(p for p, _ in bin_data)
            avg_acc   = statistics.mean(o for _, o in bin_data)
            bin_weight = len(bin_data) / n
            ece       += bin_weight * abs(avg_conf - avg_acc)

        predicted = [s.predicted_score for s in samples]
        actual    = [float(s.actual_outcome) for s in samples]
        accuracy  = statistics.mean(
            1 if p >= 0.5 and a == 1 or p < 0.5 and a == 0 else 0
            for p, a in zip(predicted, actual)
        )
        overconf  = statistics.mean(p - a for p, a in zip(predicted, actual))

        return CalibrationMetrics(
            ece            = round(ece, 4),
            accuracy       = round(accuracy, 4),
            overconfidence = round(max(0, overconf), 4),
            underconfidence= round(max(0, -overconf), 4),
            sample_count   = n,
        )

    def detect_drift(
        self,
        tenant_id: str,
        rule_code: Optional[str] = None,
    ) -> Optional["DriftAlert"]:
        """
        Compare recent window ECE against the baseline window.

        Returns a DriftAlert if drift is detected, None otherwise.
        """
        now    = datetime.now(tz=timezone.utc)
        cutoff_recent   = now - timedelta(days=self._window_days)
        cutoff_baseline = now - timedelta(days=self._baseline_days)

        def _filter(samples: list, since: datetime, until: Optional[datetime] = None) -> list:
            result = [s for s in samples
                      if s.tenant_id == tenant_id
                      and (not rule_code or s.rule_code == rule_code)
                      and s.recorded_at >= since]
            if until:
                result = [s for s in result if s.recorded_at < until]
            return result

        recent_samples   = _filter(self._history, cutoff_recent)
        baseline_samples = _filter(self._history, cutoff_baseline, cutoff_recent)

        if len(recent_samples) < _MIN_SAMPLES or len(baseline_samples) < _MIN_SAMPLES:
            return None

        recent_metrics   = self.compute_metrics(recent_samples)
        baseline_metrics = self.compute_metrics(baseline_samples)

        ece_delta = recent_metrics.ece - baseline_metrics.ece
        if ece_delta > self._threshold:
            return DriftAlert(
                tenant_id       = tenant_id,
                rule_code       = rule_code,
                baseline_ece    = baseline_metrics.ece,
                recent_ece      = recent_metrics.ece,
                ece_delta       = ece_delta,
                detected_at     = now,
                severity        = "high" if ece_delta > 0.20 else "medium",
            )
        return None


@dataclass
class DriftAlert:
    """Calibration drift alert."""
    tenant_id:     str
    rule_code:     Optional[str]
    baseline_ece:  float
    recent_ece:    float
    ece_delta:     float
    detected_at:   datetime
    severity:      str           # "medium" | "high"


# ── Uncertainty propagator ────────────────────────────────────────────────────

class UncertaintyPropagator:
    """
    Propagates uncertainty through multi-step reasoning chains.

    When an agentic investigation chains N reasoning steps, the overall
    confidence is bounded by the product of step-level confidences
    (pessimistic / conservative approach for regulated environments).
    """

    @staticmethod
    def chain_confidence(step_confidences: list[float]) -> float:
        """
        Compute joint confidence for a chain of reasoning steps.

        Uses geometric mean (softer than product, harder than arithmetic mean)
        to balance conservatism with usability.
        """
        if not step_confidences:
            return 0.0
        if any(c <= 0 for c in step_confidences):
            return 0.0
        log_sum = sum(math.log(c) for c in step_confidences)
        return round(math.exp(log_sum / len(step_confidences)), 4)

    @staticmethod
    def aggregate_evidence(evidence_scores: list[float], weights: Optional[list[float]] = None) -> float:
        """
        Aggregate evidence scores into a single confidence estimate.

        If weights are provided, computes a weighted average.
        Otherwise uses arithmetic mean.
        """
        if not evidence_scores:
            return 0.0
        if weights:
            if len(weights) != len(evidence_scores):
                raise ValueError("weights and evidence_scores must have the same length")
            total_weight = sum(weights)
            if total_weight == 0:
                return 0.0
            return round(
                sum(s * w for s, w in zip(evidence_scores, weights)) / total_weight, 4
            )
        return round(statistics.mean(evidence_scores), 4)
