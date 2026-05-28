"""
MonitoringEngine — orchestrates the full intelligence monitoring run.

A monitoring run is an atomic unit of continuous compliance intelligence:
  1. Open a monitoring_run record (status=running)
  2. Build the compliance knowledge graph from current DB state
  3. Run trend analysis across all rolling windows
  4. Run correlation engine
  5. Run predictive risk scoring
  6. Run drift detection
  7. Persist all results to their respective tables
  8. Close the monitoring_run record (status=completed or failed)

Each run is idempotent by run_id — safe to retry on failure.
The engine is deterministic: given the same DB state it produces the
same output.  No LLMs are invoked in the monitoring engine itself.
"""
from __future__ import annotations

import logging
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from intelligence.graph.builder import ComplianceGraphBuilder
from intelligence.services.correlation import CorrelationEngine
from intelligence.services.drift_detection import DriftDetectionService, DriftReport
from intelligence.services.predictive_risk import PredictiveRiskService, RiskScoringReport
from intelligence.services.trend_analysis import TrendAnalysisService, TrendSummary
from monitoring.windows import ALL_WINDOWS, RollingWindow, resolve_windows

logger = logging.getLogger(__name__)


@dataclass
class MonitoringRunResult:
    run_id:               str
    run_type:             str
    status:               str            # running | completed | failed
    started_at:           datetime
    completed_at:         Optional[datetime] = None
    findings_evaluated:   int = 0
    new_findings:         int = 0        # findings added since last run
    drifts_detected:      int = 0
    correlations_found:   int = 0
    trends_30d:           Optional[TrendSummary] = None
    trends_60d:           Optional[TrendSummary] = None
    trends_90d:           Optional[TrendSummary] = None
    risk_report:          Optional[RiskScoringReport] = None
    drift_report:         Optional[DriftReport] = None
    error_message:        Optional[str] = None
    metadata:             dict = field(default_factory=dict)


class MonitoringEngine:
    """
    Runs the full intelligence monitoring pipeline.

    Usage::

        engine = MonitoringEngine()
        result = engine.run(session)
        print(result.status, result.correlations_found)
    """

    def __init__(self) -> None:
        self._graph_builder = ComplianceGraphBuilder()
        self._trend_svc     = TrendAnalysisService()
        self._correlation   = CorrelationEngine()
        self._risk_svc      = PredictiveRiskService()
        self._drift_svc     = DriftDetectionService()

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def run(
        self,
        session: Session,
        run_type: str = "scheduled",
        as_of: Optional[date] = None,
        persist: bool = True,
    ) -> MonitoringRunResult:
        """
        Executes a full monitoring run.

        run_type: "scheduled" | "manual" | "delta"
        persist:  if True, writes all results to DB tables + monitoring_runs record.
        """
        run_id    = str(uuid4())
        as_of     = as_of or date.today()
        started   = datetime.utcnow()

        result = MonitoringRunResult(
            run_id=run_id,
            run_type=run_type,
            status="running",
            started_at=started,
        )

        if persist:
            self._open_run(session, result)

        try:
            result = self._execute(session, result, as_of, persist)
            result.status       = "completed"
            result.completed_at = datetime.utcnow()

        except Exception as exc:
            result.status        = "failed"
            result.completed_at  = datetime.utcnow()
            result.error_message = str(exc)
            logger.error("Monitoring run %s failed: %s\n%s", run_id, exc, traceback.format_exc())

        finally:
            if persist:
                self._close_run(session, result)

        logger.info(
            "Monitoring run %s %s  trends=%d correlations=%d drifts=%d",
            run_id, result.status,
            len(result.trends_30d.records) if result.trends_30d else 0,
            result.correlations_found,
            result.drifts_detected,
        )
        return result

    def run_delta(
        self,
        session: Session,
        since_run_id: str,
        persist: bool = True,
    ) -> MonitoringRunResult:
        """
        Runs a lightweight delta check since a previous run.
        Only runs trend + drift; skips full correlation rebuild.
        """
        return self.run(session, run_type="delta", persist=persist)

    # ------------------------------------------------------------------ #
    # Pipeline steps                                                       #
    # ------------------------------------------------------------------ #

    def _execute(
        self,
        session: Session,
        result: MonitoringRunResult,
        as_of: date,
        persist: bool,
    ) -> MonitoringRunResult:

        # Step 1: Build compliance graph
        logger.info("[%s] Building compliance graph", result.run_id)
        graph = self._graph_builder.build(session, persist_edges=persist)
        result.findings_evaluated = sum(
            1 for k in graph.nodes if k.startswith("finding:")
        )

        # Step 2: Trend analysis — all windows
        logger.info("[%s] Running trend analysis", result.run_id)
        for window in ALL_WINDOWS:
            summary = self._trend_svc.analyse(
                session,
                window_type=window.name,
                as_of=as_of,
                monitoring_run_id=result.run_id,
            )
            if window.name == "30d":
                result.trends_30d = summary
            elif window.name == "60d":
                result.trends_60d = summary
            elif window.name == "90d":
                result.trends_90d = summary

            if persist:
                self._trend_svc.persist(session, summary, monitoring_run_id=result.run_id)

        # Step 3: Correlation engine (uses pre-built graph for efficiency)
        logger.info("[%s] Running correlation engine", result.run_id)
        corr_report = self._correlation.run(
            session, graph=graph, monitoring_run_id=result.run_id
        )
        result.correlations_found = corr_report.total_correlations
        if persist:
            self._correlation.persist(session, corr_report, monitoring_run_id=result.run_id)

        # Step 4: Predictive risk scoring (uses 30d window as primary)
        logger.info("[%s] Running risk scoring", result.run_id)
        risk_report = self._risk_svc.score(
            session, as_of=as_of, window_type="30d",
            monitoring_run_id=result.run_id,
        )
        result.risk_report = risk_report
        if persist:
            self._risk_svc.persist(session, risk_report, monitoring_run_id=result.run_id)

        # Step 5: Drift detection
        logger.info("[%s] Running drift detection", result.run_id)
        drift_report = self._drift_svc.detect(session, as_of=as_of)
        result.drift_report = drift_report
        result.drifts_detected = drift_report.total_signals

        # Step 6: Count new findings since last successful run
        result.new_findings = self._count_new_findings(session, result.run_id)

        # Metadata summary
        result.metadata = {
            "as_of":            as_of.isoformat(),
            "graph_nodes":      len(graph.nodes),
            "graph_edges":      len(graph.edges),
            "trend_30d_records": len(result.trends_30d.records) if result.trends_30d else 0,
            "risk_critical":    risk_report.critical_count,
            "risk_high":        risk_report.high_count,
            "drift_critical":   drift_report.critical_count,
            "drift_high":       drift_report.high_count,
        }

        return result

    # ------------------------------------------------------------------ #
    # Monitoring run DB lifecycle                                          #
    # ------------------------------------------------------------------ #

    def _open_run(self, session: Session, result: MonitoringRunResult) -> None:
        session.execute(text("""
            INSERT INTO audit.monitoring_runs
                (run_id, run_type, window_type, window_start, window_end,
                 status, started_at)
            VALUES
                (:run_id::uuid, :run_type, 'all', NOW()::date - 90, NOW()::date,
                 'running', :started_at)
        """), {
            "run_id":     result.run_id,
            "run_type":   result.run_type,
            "started_at": result.started_at.isoformat(),
        })

    def _close_run(self, session: Session, result: MonitoringRunResult) -> None:
        import json
        session.execute(text("""
            UPDATE audit.monitoring_runs SET
                status               = :status,
                findings_evaluated   = :findings_evaluated,
                new_findings         = :new_findings,
                drifts_detected      = :drifts_detected,
                correlations_found   = :correlations_found,
                run_metadata         = :metadata::jsonb,
                error_message        = :error_message,
                completed_at         = :completed_at
            WHERE run_id = :run_id::uuid
        """), {
            "run_id":             result.run_id,
            "status":             result.status,
            "findings_evaluated": result.findings_evaluated,
            "new_findings":       result.new_findings,
            "drifts_detected":    result.drifts_detected,
            "correlations_found": result.correlations_found,
            "metadata":           json.dumps(result.metadata),
            "error_message":      result.error_message,
            "completed_at":       result.completed_at.isoformat() if result.completed_at else None,
        })

    def _count_new_findings(self, session: Session, current_run_id: str) -> int:
        """
        Counts findings created since the previous successful monitoring run.
        Returns 0 if no prior run exists.
        """
        row = session.execute(text("""
            SELECT MAX(completed_at) AS last_completed
            FROM audit.monitoring_runs
            WHERE status = 'completed'
              AND run_id != :rid::uuid
        """), {"rid": current_run_id}).mappings().fetchone()

        if not row or not row["last_completed"]:
            return 0

        count_row = session.execute(text("""
            SELECT COUNT(*) AS cnt
            FROM audit.audit_findings
            WHERE created_at > :since
        """), {"since": row["last_completed"]}).mappings().fetchone()

        return int(count_row["cnt"]) if count_row else 0
