"""
Experiment tracking and lineage for versioning.

Records which prompt/workflow versions were active during each evaluation
run and investigation period. Links evaluation results back to the exact
versions used so improvements and regressions can be traced to specific
changes.

Design principles
─────────────────
  - Every experiment run pins the exact prompt + workflow + calibration
    version in use at start time (version snapshot).
  - Results are never modified; analysis creates new summary records.
  - Lineage links runs to their parent experiment definition.
  - No experiment can modify production traffic without explicit promotion.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

log = logging.getLogger("evidentrx.learning.versioning.experiment")


class ExperimentStatus(str, Enum):
    PLANNED   = "planned"
    RUNNING   = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED    = "failed"


class ExperimentType(str, Enum):
    PROMPT_AB          = "prompt_ab"           # A/B test two prompt versions
    WORKFLOW_AB        = "workflow_ab"          # A/B test two workflow versions
    CALIBRATION_EVAL   = "calibration_eval"    # evaluate a calibration snapshot
    REGRESSION_CHECK   = "regression_check"    # verify no quality degradation
    MODEL_COMPARISON   = "model_comparison"    # compare model routing configs
    BASELINE_CAPTURE   = "baseline_capture"    # record baseline metrics


@dataclass
class VersionSnapshot:
    """
    Pinned set of versions active at experiment start.

    Immutable once recorded. Provides complete reproducibility: given the
    same snapshot + input data, the same outputs can be reproduced.
    """
    snapshot_id:          str
    prompt_versions:      dict[str, str]  # prompt_name → version string
    workflow_versions:    dict[str, str]  # workflow_name → version string
    calibration_snapshot: str | None  # CalibrationSnapshot.snapshot_id
    model_config:         str            # model routing config identifier
    captured_at:          datetime
    content_hash:         str

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id":          self.snapshot_id,
            "prompt_versions":      self.prompt_versions,
            "workflow_versions":    self.workflow_versions,
            "calibration_snapshot": self.calibration_snapshot,
            "model_config":         self.model_config,
            "captured_at":          self.captured_at.isoformat(),
            "content_hash":         self.content_hash,
        }


@dataclass
class ExperimentRun:
    """
    A single execution of an experiment against a benchmark.

    Produced by ExperimentTracker.start_run(). The run records which
    version snapshot was used and links back to evaluation harness results.
    """
    run_id:          str
    experiment_id:   str
    tenant_id:       str
    snapshot:        VersionSnapshot
    benchmark_id:    str
    evaluation_run_id: str | None   = None   # set when harness run completes
    status:          ExperimentStatus  = ExperimentStatus.RUNNING
    started_at:      datetime          = field(default_factory=lambda: datetime.now(tz=UTC))
    completed_at:    datetime | None= None
    summary_metrics: dict[str, Any]   = field(default_factory=dict)
    notes:           str               = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id":            self.run_id,
            "experiment_id":     self.experiment_id,
            "tenant_id":         self.tenant_id,
            "snapshot_id":       self.snapshot.snapshot_id,
            "benchmark_id":      self.benchmark_id,
            "evaluation_run_id": self.evaluation_run_id,
            "status":            self.status.value,
            "started_at":        self.started_at.isoformat(),
            "completed_at":      self.completed_at.isoformat() if self.completed_at else None,
            "summary_metrics":   self.summary_metrics,
        }


@dataclass
class Experiment:
    """
    Experiment definition (the plan, not the execution).

    Describes what is being tested, which benchmark to use, and success
    criteria. Runs are attached after creation.
    """
    experiment_id:     str
    tenant_id:         str
    name:              str
    experiment_type:   ExperimentType
    description:       str
    hypothesis:        str              # what we expect to improve/verify
    benchmark_id:      str             # benchmark suite used for evaluation
    success_criteria:  dict[str, Any]  # e.g. {"outcome_accuracy": {"min": 0.85}}
    status:            ExperimentStatus
    created_at:        datetime
    created_by:        str
    runs:              list[ExperimentRun] = field(default_factory=list)
    concluded_at:      datetime | None  = None
    conclusion:        str                 = ""
    metadata:          dict[str, Any]      = field(default_factory=dict)

    @property
    def run_count(self) -> int:
        return len(self.runs)

    @property
    def completed_runs(self) -> list[ExperimentRun]:
        return [r for r in self.runs if r.status == ExperimentStatus.COMPLETED]

    def meets_success_criteria(self, metrics: dict[str, Any]) -> bool:
        """Check whether given metrics satisfy the experiment's success criteria."""
        for metric, spec in self.success_criteria.items():
            val = metrics.get(metric)
            if val is None:
                return False
            if "min" in spec and val < spec["min"]:
                return False
            if "max" in spec and val > spec["max"]:
                return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id":   self.experiment_id,
            "tenant_id":       self.tenant_id,
            "name":            self.name,
            "experiment_type": self.experiment_type.value,
            "description":     self.description,
            "hypothesis":      self.hypothesis,
            "benchmark_id":    self.benchmark_id,
            "success_criteria":self.success_criteria,
            "status":          self.status.value,
            "created_at":      self.created_at.isoformat(),
            "created_by":      self.created_by,
            "run_count":       self.run_count,
            "concluded_at":    self.concluded_at.isoformat() if self.concluded_at else None,
            "conclusion":      self.conclusion,
        }


class ExperimentTracker:
    """
    Creates and tracks experiments and their runs.

    Provides the version snapshot mechanism that pins exactly which prompt,
    workflow, and calibration versions were in use when a run starts.
    """

    def __init__(self, db_writer: Callable | None = None) -> None:
        self._experiments: dict[str, Experiment] = {}
        self._runs:        dict[str, ExperimentRun] = {}
        self._snapshots:   dict[str, VersionSnapshot] = {}
        self._db_writer    = db_writer

    # ── Experiment CRUD ────────────────────────────────────────────────────────

    async def create_experiment(
        self,
        tenant_id:        str,
        name:             str,
        experiment_type:  ExperimentType,
        description:      str,
        hypothesis:       str,
        benchmark_id:     str,
        success_criteria: dict[str, Any],
        created_by:       str,
        metadata:         dict | None = None,
    ) -> Experiment:
        exp = Experiment(
            experiment_id   = str(uuid.uuid4()),
            tenant_id       = tenant_id,
            name            = name,
            experiment_type = experiment_type,
            description     = description,
            hypothesis      = hypothesis,
            benchmark_id    = benchmark_id,
            success_criteria= success_criteria,
            status          = ExperimentStatus.PLANNED,
            created_at      = datetime.now(tz=UTC),
            created_by      = created_by,
            metadata        = metadata or {},
        )
        self._experiments[exp.experiment_id] = exp
        await self._persist("create_experiment", exp)
        log.info(
            "ExperimentTracker: created experiment '%s' [%s]",
            name, exp.experiment_id[:8],
        )
        return exp

    async def conclude(
        self,
        experiment_id: str,
        conclusion:    str,
        success:       bool,
    ) -> Experiment:
        exp = self._get_experiment(experiment_id)
        exp.status       = ExperimentStatus.COMPLETED if success else ExperimentStatus.FAILED
        exp.concluded_at = datetime.now(tz=UTC)
        exp.conclusion   = conclusion
        await self._persist("update_experiment", exp)
        return exp

    # ── Version snapshot ───────────────────────────────────────────────────────

    def capture_snapshot(
        self,
        prompt_versions:      dict[str, str],
        workflow_versions:    dict[str, str],
        model_config:         str,
        calibration_snapshot: str | None = None,
    ) -> VersionSnapshot:
        """
        Capture the currently active set of versions.

        Called at the start of each experiment run to freeze the version
        context. The content_hash detects accidental mutation.
        """
        payload = json.dumps(
            {
                "prompts":        prompt_versions,
                "workflows":      workflow_versions,
                "calibration":    calibration_snapshot,
                "model_config":   model_config,
            },
            sort_keys=True,
        ).encode()
        content_hash = hashlib.sha256(payload).hexdigest()

        snap = VersionSnapshot(
            snapshot_id          = str(uuid.uuid4()),
            prompt_versions      = dict(prompt_versions),
            workflow_versions    = dict(workflow_versions),
            calibration_snapshot = calibration_snapshot,
            model_config         = model_config,
            captured_at          = datetime.now(tz=UTC),
            content_hash         = content_hash,
        )
        self._snapshots[snap.snapshot_id] = snap
        return snap

    # ── Run management ─────────────────────────────────────────────────────────

    async def start_run(
        self,
        experiment_id: str,
        snapshot:      VersionSnapshot,
        benchmark_id:  str,
        notes:         str = "",
    ) -> ExperimentRun:
        """Start a new run for an experiment."""
        exp = self._get_experiment(experiment_id)

        if exp.status == ExperimentStatus.PLANNED:
            exp.status = ExperimentStatus.RUNNING
            await self._persist("update_experiment", exp)

        run = ExperimentRun(
            run_id        = str(uuid.uuid4()),
            experiment_id = experiment_id,
            tenant_id     = exp.tenant_id,
            snapshot      = snapshot,
            benchmark_id  = benchmark_id,
            notes         = notes,
        )
        self._runs[run.run_id] = run
        exp.runs.append(run)
        await self._persist("create_run", run)
        log.info(
            "ExperimentTracker: started run %s for experiment %s",
            run.run_id[:8], experiment_id[:8],
        )
        return run

    async def complete_run(
        self,
        run_id:            str,
        evaluation_run_id: str | None,
        summary_metrics:   dict[str, Any],
    ) -> ExperimentRun:
        """Mark a run as completed and attach evaluation results."""
        run = self._get_run(run_id)
        run.status            = ExperimentStatus.COMPLETED
        run.completed_at      = datetime.now(tz=UTC)
        run.evaluation_run_id = evaluation_run_id
        run.summary_metrics   = summary_metrics
        await self._persist("update_run", run)

        # Check success criteria for the parent experiment
        exp = self._experiments.get(run.experiment_id)
        if exp and exp.meets_success_criteria(summary_metrics):
            log.info(
                "ExperimentTracker: run %s meets success criteria for '%s'",
                run_id[:8], exp.name,
            )
        return run

    async def fail_run(self, run_id: str, reason: str) -> ExperimentRun:
        run = self._get_run(run_id)
        run.status             = ExperimentStatus.FAILED
        run.completed_at       = datetime.now(tz=UTC)
        run.summary_metrics["failure_reason"] = reason
        await self._persist("update_run", run)
        return run

    # ── Queries ────────────────────────────────────────────────────────────────

    def get_experiment(self, experiment_id: str) -> Experiment | None:
        return self._experiments.get(experiment_id)

    def get_run(self, run_id: str) -> ExperimentRun | None:
        return self._runs.get(run_id)

    def get_snapshot(self, snapshot_id: str) -> VersionSnapshot | None:
        return self._snapshots.get(snapshot_id)

    def list_experiments(
        self,
        tenant_id: str,
        status:    ExperimentStatus | None = None,
    ) -> list[Experiment]:
        result = [
            e for e in self._experiments.values()
            if e.tenant_id == tenant_id
            and (status is None or e.status == status)
        ]
        return sorted(result, key=lambda e: e.created_at, reverse=True)

    def runs_for_experiment(self, experiment_id: str) -> list[ExperimentRun]:
        return [r for r in self._runs.values() if r.experiment_id == experiment_id]

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _get_experiment(self, experiment_id: str) -> Experiment:
        exp = self._experiments.get(experiment_id)
        if exp is None:
            raise ExperimentNotFoundError(experiment_id)
        return exp

    def _get_run(self, run_id: str) -> ExperimentRun:
        run = self._runs.get(run_id)
        if run is None:
            raise ExperimentNotFoundError(f"run:{run_id}")
        return run

    async def _persist(self, op: str, obj: Any) -> None:
        if self._db_writer:
            try:
                await self._db_writer(op, obj)
            except Exception as exc:
                log.error("ExperimentTracker: persist failed: %s", exc)


# ── Exceptions ─────────────────────────────────────────────────────────────────

class ExperimentNotFoundError(Exception):
    pass


# ── Module-level singleton ─────────────────────────────────────────────────────

_tracker: ExperimentTracker | None = None


def get_experiment_tracker(db_writer: Callable | None = None) -> ExperimentTracker:
    global _tracker
    if _tracker is None:
        _tracker = ExperimentTracker(db_writer=db_writer)
    return _tracker
