"""
EvaluationDriftDetector — wraps DriftDetectionService for evaluation pipelines.

Provides a targeted view of drift signals relevant to the evaluation harness:
specifically model drift (confidence, escalation rate) surfaced as evaluation
check results that can be included in EvaluationResult.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from intelligence.services.drift_detection import DriftDetectionService, DriftSignal

logger = logging.getLogger(__name__)


@dataclass
class DriftCheck:
    check:       str
    passed:      bool
    signal:      Optional[DriftSignal]
    message:     str


class EvaluationDriftDetector:
    """
    Runs drift detection as part of an evaluation pipeline and returns
    structured pass/fail checks compatible with EvaluationResult.

    Usage::

        detector = EvaluationDriftDetector()
        checks = detector.run(session)
        failed = [c for c in checks if not c.passed]
    """

    def __init__(self, fail_on_critical: bool = True, fail_on_high: bool = False) -> None:
        self._svc           = DriftDetectionService()
        self.fail_on_critical = fail_on_critical
        self.fail_on_high    = fail_on_high

    def run(
        self,
        session: Session,
        as_of: Optional[date] = None,
        window_type: str = "30d",
    ) -> list[DriftCheck]:
        """
        Runs drift detection and converts signals to evaluation checks.
        """
        report = self._svc.detect(
            session, as_of=as_of, window_type=window_type, min_magnitude="medium"
        )

        checks: list[DriftCheck] = []

        # Model drift checks (most relevant to evaluation)
        for signal in report.model_drift:
            is_critical = signal.magnitude == "critical"
            is_high     = signal.magnitude == "high"

            should_fail = (
                (is_critical and self.fail_on_critical) or
                (is_high and self.fail_on_high)
            )

            checks.append(DriftCheck(
                check=f"model_drift.{signal.subject_id}",
                passed=not should_fail,
                signal=signal,
                message=(
                    f"{signal.magnitude.upper()} model drift detected: "
                    f"{signal.explanation}"
                ),
            ))

        # Rule drift checks (informational unless critical)
        for signal in report.rule_drift[:5]:   # top 5 only
            is_critical = signal.magnitude == "critical"
            checks.append(DriftCheck(
                check=f"rule_drift.{signal.subject_id}",
                passed=not (is_critical and self.fail_on_critical),
                signal=signal,
                message=(
                    f"Rule drift ({signal.magnitude}): {signal.explanation}"
                ),
            ))

        # Summary check — overall drift health
        no_critical_drift = not report.has_critical()
        checks.append(DriftCheck(
            check="drift_health_overall",
            passed=no_critical_drift,
            signal=None,
            message=(
                f"Drift health: {report.total_signals} signals "
                f"({report.critical_count} critical, {report.high_count} high)"
            ),
        ))

        logger.info(
            "Drift evaluation: %d checks, %d failed",
            len(checks),
            sum(1 for c in checks if not c.passed),
        )
        return checks
