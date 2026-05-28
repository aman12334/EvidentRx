"""
PipelineExecutor — end-to-end pipeline orchestration with stage checkpoints.

Pipeline stages (in order):
  1. ingestion      — load reference data (CE, NDC, NPPES, Medicaid exclusions)
  2. simulation     — generate synthetic 340B transactions
  3. rules_engine   — run deterministic compliance rules
  4. investigation  — cluster findings into investigation cases
  5. agents         — AI-powered investigation on open cases

Stage checkpoints are persisted as JSON in runtime_state/{run_id}.json.
A failed or interrupted pipeline can be resumed: completed stages are skipped.

Usage:
    executor = PipelineExecutor()
    result   = executor.run(PipelineConfig())

    # Resume
    result = executor.resume(run_id="<prior-run-id>")
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

logger = logging.getLogger(__name__)

_STATE_DIR = Path(os.path.dirname(__file__)).parent / "runtime_state"

STAGE_ORDER = [
    "ingestion",
    "simulation",
    "rules_engine",
    "investigation",
    "agents",
]


@dataclass
class PipelineConfig:
    # Simulation
    n_ces:          int   = 50
    n_ndcs:         int   = 150
    sim_start:      date  = field(default_factory=lambda: date(2025, 1, 1))
    sim_end:        date  = field(default_factory=lambda: date(2025, 12, 31))
    violation_rate: float = 0.07
    random_seed:    int   = 42

    # Rules engine
    query_batch_size: int = 5000
    db_batch_size:    int = 500

    # Investigation
    window_days:      int = 14
    min_cluster_size: int = 1

    # Agents
    agent_batch_limit: int = 20

    # Execution
    skip_ingestion: bool  = True   # requires external source files; off by default
    skip_agents:    bool  = False


@dataclass
class StageResult:
    stage:       str
    status:      str    # completed | failed | skipped
    elapsed_s:   float  = 0.0
    stats:       dict   = field(default_factory=dict)
    error:       str | None = None

    def to_dict(self) -> dict:
        return {
            "stage":     self.stage,
            "status":    self.status,
            "elapsed_s": round(self.elapsed_s, 3),
            "stats":     self.stats,
            "error":     self.error,
        }


@dataclass
class PipelineRun:
    run_id:     str
    started_at: str
    config:     dict
    stages:     dict[str, dict] = field(default_factory=dict)

    def is_stage_done(self, stage: str) -> bool:
        return self.stages.get(stage, {}).get("status") == "completed"

    def record(self, result: StageResult) -> None:
        self.stages[result.stage] = result.to_dict()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump({
                "run_id":     self.run_id,
                "started_at": self.started_at,
                "config":     self.config,
                "stages":     self.stages,
            }, f, indent=2, default=str)

    @classmethod
    def load(cls, path: Path) -> PipelineRun:
        with open(path) as f:
            data = json.load(f)
        obj = cls(
            run_id=data["run_id"],
            started_at=data["started_at"],
            config=data.get("config", {}),
        )
        obj.stages = data.get("stages", {})
        return obj


class PipelineExecutor:
    """
    Runs the full EvidentRx pipeline with stage-level checkpointing.
    Each stage is isolated: a failure in one stage does not prevent
    partial progress from being saved.
    """

    def run(self, config: PipelineConfig | None = None) -> PipelineRun:
        cfg    = config or PipelineConfig()
        run_id = str(uuid4())
        run    = PipelineRun(
            run_id=run_id,
            started_at=datetime.now(UTC).isoformat(),
            config=cfg.__dict__ if hasattr(cfg, "__dict__") else {},
        )
        checkpoint_path = _STATE_DIR / f"{run_id}.json"
        return self._execute(run, cfg, checkpoint_path)

    def resume(self, run_id: str, config: PipelineConfig | None = None) -> PipelineRun:
        checkpoint_path = _STATE_DIR / f"{run_id}.json"
        if not checkpoint_path.exists():
            raise FileNotFoundError(f"No checkpoint found for run_id={run_id}")
        run = PipelineRun.load(checkpoint_path)
        cfg = config or PipelineConfig()
        logger.info("Resuming pipeline run=%s", run_id)
        return self._execute(run, cfg, checkpoint_path)

    def _execute(
        self,
        run: PipelineRun,
        cfg: PipelineConfig,
        checkpoint_path: Path,
    ) -> PipelineRun:
        logger.info("Pipeline run=%s starting", run.run_id)

        for stage in STAGE_ORDER:
            if run.is_stage_done(stage):
                logger.info("Stage %s — already completed, skipping", stage)
                continue

            result = self._run_stage(stage, cfg, run)
            run.record(result)
            run.save(checkpoint_path)

            if result.status == "failed":
                logger.error(
                    "Stage %s FAILED: %s — pipeline halted. Resume with run_id=%s",
                    stage, result.error, run.run_id,
                )
                break
            else:
                logger.info(
                    "Stage %s completed in %.1fs %s",
                    stage, result.elapsed_s, result.stats,
                )

        run.save(checkpoint_path)
        logger.info("Pipeline run=%s finished. Checkpoint: %s", run.run_id, checkpoint_path)
        return run

    def _run_stage(self, stage: str, cfg: PipelineConfig, run: PipelineRun) -> StageResult:
        handler = {
            "ingestion":   self._stage_ingestion,
            "simulation":  self._stage_simulation,
            "rules_engine": self._stage_rules_engine,
            "investigation": self._stage_investigation,
            "agents":      self._stage_agents,
        }.get(stage)

        if handler is None:
            return StageResult(stage=stage, status="failed", error=f"Unknown stage: {stage}")

        t0 = time.monotonic()
        try:
            stats = handler(cfg)
            return StageResult(
                stage=stage,
                status="completed",
                elapsed_s=time.monotonic() - t0,
                stats=stats or {},
            )
        except Exception as e:
            logger.exception("Stage %s raised", stage)
            return StageResult(
                stage=stage,
                status="failed",
                elapsed_s=time.monotonic() - t0,
                error=str(e),
            )

    # ------------------------------------------------------------------
    # Stage implementations
    # ------------------------------------------------------------------

    def _stage_ingestion(self, cfg: PipelineConfig) -> dict:
        if cfg.skip_ingestion:
            logger.info("Ingestion skipped (skip_ingestion=True). "
                        "Run python run_ingestion.py manually when source files are available.")
            return {"skipped": True, "reason": "skip_ingestion=True"}

        # Check source files exist before attempting load
        downloads = os.path.expanduser("~/Downloads")
        ce_file   = os.path.join(downloads, "OPA_CE_DAILY_PUBLIC.JSON")
        if not os.path.exists(ce_file):
            raise FileNotFoundError(
                f"CE source file not found: {ce_file}\n"
                "Download OPA_CE_DAILY_PUBLIC.JSON from HRSA 340B OPAIS and place in ~/Downloads"
            )

        import run_ingestion as _ri
        _ri.run()
        return {"status": "ingestion complete"}

    def _stage_simulation(self, cfg: PipelineConfig) -> dict:
        from app.database import SessionLocal
        from simulation.config import SimConfig
        from simulation.orchestrator import SimulationOrchestrator

        sim_cfg = SimConfig(
            period_start=cfg.sim_start,
            period_end=cfg.sim_end,
            n_ces=cfg.n_ces,
            n_ndcs=cfg.n_ndcs,
            violation_rate=cfg.violation_rate,
            random_seed=cfg.random_seed,
        )
        with SessionLocal() as session:
            SimulationOrchestrator(sim_cfg).run(session)

        return {
            "n_ces":          cfg.n_ces,
            "n_ndcs":         cfg.n_ndcs,
            "violation_rate": cfg.violation_rate,
            "seed":           cfg.random_seed,
        }

    def _stage_rules_engine(self, cfg: PipelineConfig) -> dict:
        from app.database import SessionLocal
        from rules_engine.engine import RulesEngine

        engine = RulesEngine(db_batch_size=cfg.db_batch_size)
        with SessionLocal() as session:
            stats = engine.run(
                session,
                query_batch_size=cfg.query_batch_size,
            )
        return stats

    def _stage_investigation(self, cfg: PipelineConfig) -> dict:
        from app.database import SessionLocal
        from investigation.domain.clustering import ClusterConfig
        from investigation.services.case_builder import CaseBuilderService

        cluster_cfg = ClusterConfig(
            window_days=cfg.window_days,
            min_cluster_size=cfg.min_cluster_size,
        )
        service = CaseBuilderService()
        with SessionLocal() as session:
            stats = service.run(session, config=cluster_cfg)
        return stats

    def _stage_agents(self, cfg: PipelineConfig) -> dict:
        if cfg.skip_agents:
            return {"skipped": True, "reason": "skip_agents=True"}

        from sqlalchemy import text

        from agents.runner import InvestigationRunner
        from app.database import SessionLocal

        runner = InvestigationRunner.from_env()

        with SessionLocal() as session:
            rows = session.execute(text("""
                SELECT case_id FROM audit.investigation_cases
                WHERE status IN ('open', 'triaged')
                ORDER BY priority DESC, opened_at ASC
                LIMIT :lim
            """), {"lim": cfg.agent_batch_limit}).fetchall()
            case_ids = [r.case_id for r in rows]

        logger.info("Agent stage: %d cases to investigate", len(case_ids))

        results     = []
        error_count = 0
        with SessionLocal() as session:
            for case_id in case_ids:
                try:
                    result = runner.run(session, case_id)
                    results.append(result)
                except Exception as e:
                    logger.error("Agent failed for case %s: %s", case_id, e)
                    error_count += 1

        total_tokens = sum(
            r.get("total_input_tokens", 0) + r.get("total_output_tokens", 0)
            for r in results
        )
        return {
            "cases_processed": len(results),
            "errors":          error_count,
            "total_tokens":    total_tokens,
        }

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @staticmethod
    def list_runs() -> list[dict]:
        """Returns all pipeline run checkpoints, most recent first."""
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        runs = []
        for p in sorted(_STATE_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                with open(p) as f:
                    data = json.load(f)
                stage_statuses = {k: v["status"] for k, v in data.get("stages", {}).items()}
                runs.append({
                    "run_id":     data["run_id"],
                    "started_at": data.get("started_at"),
                    "stages":     stage_statuses,
                })
            except Exception:
                pass
        return runs

    @staticmethod
    def load_run(run_id: str) -> dict | None:
        p = _STATE_DIR / f"{run_id}.json"
        if not p.exists():
            return None
        with open(p) as f:
            return json.load(f)
