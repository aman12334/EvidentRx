"""
Deterministic experiment arm assignment.

Maps entities (cases, tenants, analysts) to experiment arms using a
stable hash function. The same entity_id + experiment_id always produces
the same arm, ensuring:

  - Consistent user experience: an analyst always sees the same arm
  - Reproducible analysis: replaying events gives identical assignments
  - No server-side state required for assignment lookup

Assignment algorithm
────────────────────
  1. Compute SHA-256(experiment_id + ":" + entity_id)
  2. Convert first 8 bytes to unsigned int (big-endian)
  3. Modulo 10,000 → value in [0, 9999]
  4. If value < traffic_fraction * 10,000 → in experiment
  5. If in experiment: value % 2 == 0 → CONTROL, else TREATMENT
     (50/50 split within experiment traffic)
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from learning.experimentation.framework import (
    ABExperiment,
    ArmConfiguration,
    ExperimentArm,
    ExperimentState,
)

log = logging.getLogger("evidentrx.learning.experimentation.assignment")

# Precision: 1 part in 10,000 (0.01% granularity)
_HASH_MODULUS = 10_000


@dataclass
class AssignmentResult:
    """
    The result of arm assignment for a specific entity.

    Records enough context for offline analysis and audit: which
    experiment, which arm, what configuration was applied, and
    whether the entity was included in the experiment at all.
    """
    experiment_id:    str
    entity_id:        str
    entity_type:      str           # "case" | "analyst" | "tenant" | "request"
    in_experiment:    bool          # False → entity not sampled into experiment
    arm:              ExperimentArm | None
    arm_config:       ArmConfiguration | None
    hash_value:       int           # raw hash value for audit/replay
    assigned_at:      datetime


class ExperimentAssigner:
    """
    Stateless deterministic arm assigner.

    No database queries — all logic is derived from the experiment
    definition and the entity_id. Suitable for high-throughput paths.
    """

    def assign(
        self,
        experiment: ABExperiment,
        entity_id:  str,
        entity_type: str = "case",
    ) -> AssignmentResult:
        """
        Assign an entity to an experiment arm.

        Parameters
        ----------
        experiment  : The active ABExperiment definition
        entity_id   : Stable ID for the entity (case_id, analyst_id, etc.)
        entity_type : Human-readable entity category for audit records
        """
        if experiment.state != ExperimentState.RUNNING:
            return AssignmentResult(
                experiment_id = experiment.experiment_id,
                entity_id     = entity_id,
                entity_type   = entity_type,
                in_experiment = False,
                arm           = None,
                arm_config    = None,
                hash_value    = 0,
                assigned_at   = datetime.now(tz=UTC),
            )

        hash_value  = _stable_hash(experiment.experiment_id, entity_id)
        bucket      = hash_value % _HASH_MODULUS
        threshold   = int(experiment.traffic_fraction * _HASH_MODULUS)
        in_exp      = bucket < threshold

        arm = None
        arm_config = None
        if in_exp:
            arm        = ExperimentArm.CONTROL if bucket % 2 == 0 else ExperimentArm.TREATMENT
            arm_config = experiment.control if arm == ExperimentArm.CONTROL else experiment.treatment
            log.debug(
                "ExperimentAssigner: entity %s → %s (bucket=%d, exp=%s)",
                entity_id[:8], arm.value, bucket, experiment.experiment_id[:8],
            )

        return AssignmentResult(
            experiment_id = experiment.experiment_id,
            entity_id     = entity_id,
            entity_type   = entity_type,
            in_experiment = in_exp,
            arm           = arm,
            arm_config    = arm_config,
            hash_value    = bucket,
            assigned_at   = datetime.now(tz=UTC),
        )

    def bulk_assign(
        self,
        experiment:   ABExperiment,
        entity_ids:   list[str],
        entity_type:  str = "case",
    ) -> list[AssignmentResult]:
        """Assign multiple entities efficiently."""
        return [self.assign(experiment, eid, entity_type) for eid in entity_ids]

    def arm_counts(
        self,
        experiment:  ABExperiment,
        entity_ids:  list[str],
        entity_type: str = "case",
    ) -> dict[str, int]:
        """
        Count how many entities would fall into each arm.

        Useful for pre-flight balance checks before launching an experiment.
        """
        results = self.bulk_assign(experiment, entity_ids, entity_type)
        counts = {"control": 0, "treatment": 0, "excluded": 0}
        for r in results:
            if not r.in_experiment:
                counts["excluded"] += 1
            elif r.arm == ExperimentArm.CONTROL:
                counts["control"] += 1
            else:
                counts["treatment"] += 1
        return counts

    def verify_balance(
        self,
        experiment:  ABExperiment,
        entity_ids:  list[str],
        tolerance:   float = 0.10,
    ) -> tuple[bool, dict[str, Any]]:
        """
        Check that in-experiment entities are balanced ≈ 50/50 across arms.

        Returns (is_balanced, stats_dict). Useful for pre-launch validation.
        """
        counts = self.arm_counts(experiment, entity_ids)
        ctrl   = counts["control"]
        treat  = counts["treatment"]
        total  = ctrl + treat

        if total == 0:
            return True, {"total_in_experiment": 0, "balance_ok": True}

        imbalance = abs(ctrl - treat) / total
        is_balanced = imbalance <= tolerance

        return is_balanced, {
            "control_count":   ctrl,
            "treatment_count": treat,
            "total_in_experiment": total,
            "excluded":        counts["excluded"],
            "imbalance_ratio": round(imbalance, 4),
            "balance_ok":      is_balanced,
        }


# ── Hash helper ────────────────────────────────────────────────────────────────

def _stable_hash(experiment_id: str, entity_id: str) -> int:
    """Stable, deterministic hash using first 8 bytes of SHA-256."""
    digest = hashlib.sha256(f"{experiment_id}:{entity_id}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big")


# ── Module-level singleton ─────────────────────────────────────────────────────

_assigner: ExperimentAssigner | None = None


def get_assigner() -> ExperimentAssigner:
    global _assigner
    if _assigner is None:
        _assigner = ExperimentAssigner()
    return _assigner
