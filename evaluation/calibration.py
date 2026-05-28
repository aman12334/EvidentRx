"""
ConfidenceCalibration — evaluates whether agent confidence scores are
well-calibrated against analyst override history.

A well-calibrated agent reports confidence ≈ (1 - override_rate).
Over-confident agents report high confidence but get frequently corrected.
Under-confident agents are unnecessarily cautious.

Calibration scores are computed per agent type and compared against
thresholds to produce pass/fail evaluation checks.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from agents.memory.evaluation import CalibrationData, EvaluationMemory

logger = logging.getLogger(__name__)

# Calibration thresholds
CALIBRATION_BIAS_WARN   = 0.10   # |bias| > this → warning
CALIBRATION_BIAS_FAIL   = 0.20   # |bias| > this → fail
OVERRIDE_RATE_WARN      = 0.15   # override_rate > this → warning
OVERRIDE_RATE_FAIL      = 0.30   # override_rate > this → fail


@dataclass
class CalibrationCheck:
    agent_type:         str
    check:              str
    passed:             bool
    level:              str           # "ok" | "warning" | "fail"
    calibration:        CalibrationData
    message:            str


class ConfidenceCalibration:
    """
    Evaluates agent confidence calibration against analyst override history.

    Usage::

        cal = ConfidenceCalibration()
        checks = cal.run(session)
        failures = [c for c in checks if not c.passed]
    """

    AGENT_TYPES = ["evidence_analysis", "risk_prioritization", "narrative_generation"]

    def __init__(self) -> None:
        self._mem = EvaluationMemory()

    def run(
        self,
        session: Session,
        lookback_days: int = 90,
        fail_on_bias: bool = True,
    ) -> list[CalibrationCheck]:
        """
        Runs calibration checks for all LLM agent types.
        """
        checks: list[CalibrationCheck] = []

        for agent_type in self.AGENT_TYPES:
            cal = self._mem.calibration_for_agent(
                session, agent_type, lookback_days=lookback_days
            )

            if cal.n_outputs == 0:
                checks.append(CalibrationCheck(
                    agent_type=agent_type,
                    check=f"calibration.{agent_type}.has_data",
                    passed=True,
                    level="ok",
                    calibration=cal,
                    message=f"No evaluation data available for {agent_type} (new agent).",
                ))
                continue

            # Bias check
            abs_bias    = abs(cal.calibration_bias)
            bias_level  = _level(abs_bias, CALIBRATION_BIAS_WARN, CALIBRATION_BIAS_FAIL)
            bias_passed = bias_level != "fail" or not fail_on_bias

            bias_direction = "over-confident" if cal.calibration_bias > 0 else "under-confident"
            checks.append(CalibrationCheck(
                agent_type=agent_type,
                check=f"calibration.{agent_type}.bias",
                passed=bias_passed,
                level=bias_level,
                calibration=cal,
                message=(
                    f"{agent_type} calibration bias={cal.calibration_bias:+.4f} "
                    f"({bias_direction}). "
                    f"Mean confidence={cal.mean_confidence:.3f}, "
                    f"override_rate={cal.override_rate:.1%}."
                ),
            ))

            # Override rate check
            or_level  = _level(cal.override_rate, OVERRIDE_RATE_WARN, OVERRIDE_RATE_FAIL)
            or_passed = or_level != "fail"
            checks.append(CalibrationCheck(
                agent_type=agent_type,
                check=f"calibration.{agent_type}.override_rate",
                passed=or_passed,
                level=or_level,
                calibration=cal,
                message=(
                    f"{agent_type} override_rate={cal.override_rate:.1%} "
                    f"(fp={cal.false_positive_rate:.1%}, fn={cal.false_negative_rate:.1%})."
                ),
            ))

        logger.info(
            "Calibration evaluation: %d checks, %d failed",
            len(checks),
            sum(1 for c in checks if not c.passed),
        )
        return checks

    def summary(self, checks: list[CalibrationCheck]) -> dict:
        return {
            "total_checks": len(checks),
            "passed":  sum(1 for c in checks if c.passed),
            "failed":  sum(1 for c in checks if not c.passed),
            "warnings": sum(1 for c in checks if c.level == "warning"),
            "by_agent": {
                agent: {
                    "passed": sum(1 for c in checks if c.agent_type == agent and c.passed),
                    "failed": sum(1 for c in checks if c.agent_type == agent and not c.passed),
                }
                for agent in self.AGENT_TYPES
            },
        }


def _level(value: float, warn_threshold: float, fail_threshold: float) -> str:
    if value >= fail_threshold:
        return "fail"
    if value >= warn_threshold:
        return "warning"
    return "ok"
