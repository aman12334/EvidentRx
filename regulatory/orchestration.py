"""
Regulatory intelligence orchestration façade.

Provides a single, unified entry point for executing the full Phase 13
regulatory intelligence pipeline.  The orchestration layer sequences
all eight services — ingestion → diff → drift → impact → recommendations
→ readiness → timeline → governance — with consistent error handling,
metrics recording, and timeline event emission at every stage.

Pipeline stages
───────────────
  INGEST      — fetch and normalize a regulatory document
  DIFF        — compute changes between the prior and new document version
  DRIFT       — detect regulatory drift across the tenant's active documents
  IMPACT      — analyze which workflows, rules, and entities are affected
  RECOMMEND   — generate governed recommendations from impact/drift findings
  READINESS   — produce a compliance readiness snapshot for the tenant
  TIMELINE    — append structured events for every stage outcome
  GOVERNANCE  — create an activation workflow for human review and approval

The orchestrator does NOT make compliance decisions.  It collects,
sequences, and surfaces information for human review.  Every generated
recommendation and readiness score is advisory — no changes are applied
autonomously.

Design constraints
──────────────────
- Orchestration is always triggered explicitly; never auto-triggered
- Every pipeline run produces an immutable PipelineRun record
- Partial failures are recorded as stage errors; downstream stages continue
- All service calls use the singleton instances (shared state across runs)
- Metrics are emitted for every stage; health monitor is not called inline
- No LLM inference at any point in the orchestration flow
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

log = logging.getLogger("evidentrx.regulatory.orchestration")


# ── Pipeline stage catalog ────────────────────────────────────────────────────

class PipelineStage(str, Enum):
    INGEST     = "ingest"
    DIFF       = "diff"
    DRIFT      = "drift"
    IMPACT     = "impact"
    RECOMMEND  = "recommend"
    READINESS  = "readiness"
    TIMELINE   = "timeline"
    GOVERNANCE = "governance"


# ── Run models ────────────────────────────────────────────────────────────────

@dataclass
class StageResult:
    """Outcome of a single pipeline stage."""
    stage:       PipelineStage
    success:     bool
    duration_ms: float
    summary:     str
    error:       str | None   = None
    entity_ids:  list[str]       = field(default_factory=list)   # IDs produced

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage":       self.stage.value,
            "success":     self.success,
            "duration_ms": round(self.duration_ms, 2),
            "summary":     self.summary,
            "error":       self.error,
            "entity_ids":  self.entity_ids[:10],
        }


@dataclass
class PipelineRun:
    """
    Immutable record of a single regulatory intelligence pipeline execution.

    Created at the start of each orchestration call; populated stage by
    stage.  Provides a complete, auditable trace of what happened and
    what was produced during the run.
    """
    run_id:      str
    tenant_id:   str
    triggered_by: str
    started_at:  datetime
    stages_requested: list[PipelineStage]
    # populated during execution
    stage_results:   list[StageResult]     = field(default_factory=list)
    completed_at:    datetime | None    = None
    success:         bool                  = False
    total_duration_ms: float               = 0.0
    # output entity IDs (for downstream consumption)
    ingested_doc_id:    str | None      = None
    diff_id:            str | None      = None
    drift_report_id:    str | None      = None
    impact_report_ids:  list[str]          = field(default_factory=list)
    recommendation_ids: list[str]          = field(default_factory=list)
    readiness_snapshot_id: str | None   = None
    workflow_id:        str | None      = None
    metadata:           dict[str, Any]     = field(default_factory=dict)

    @property
    def failed_stages(self) -> list[StageResult]:
        return [r for r in self.stage_results if not r.success]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id":               self.run_id,
            "tenant_id":            self.tenant_id,
            "triggered_by":         self.triggered_by,
            "started_at":           self.started_at.isoformat(),
            "completed_at":         self.completed_at.isoformat() if self.completed_at else None,
            "success":              self.success,
            "total_duration_ms":    round(self.total_duration_ms, 2),
            "stages_requested":     [s.value for s in self.stages_requested],
            "stages_completed":     [r.stage.value for r in self.stage_results if r.success],
            "stages_failed":        [r.stage.value for r in self.stage_results if not r.success],
            "ingested_doc_id":      self.ingested_doc_id,
            "diff_id":              self.diff_id,
            "drift_report_id":      self.drift_report_id,
            "impact_report_count":  len(self.impact_report_ids),
            "recommendation_count": len(self.recommendation_ids),
            "readiness_snapshot_id":self.readiness_snapshot_id,
            "workflow_id":          self.workflow_id,
            "stage_results":        [r.to_dict() for r in self.stage_results],
        }


# ── Pipeline configuration ────────────────────────────────────────────────────

@dataclass
class PipelineConfig:
    """
    Configuration for a single regulatory intelligence pipeline run.

    Callers specify which stages to execute and supply the document
    payload.  All fields that are not provided are skipped gracefully.
    """
    tenant_id:          str
    triggered_by:       str                = "system"
    # Ingest stage
    source_url:         str | None      = None
    raw_content:        bytes | None    = None
    doc_format:         str                = "pdf"      # "pdf"|"html"|"text"|"json"
    doc_source:         str                = "hrsa"     # DocumentSource enum value
    doc_title:          str | None      = None
    doc_domains:        list[str]          = field(default_factory=list)
    # Diff stage — prior doc to compare against
    prior_doc_id:       str | None      = None
    # Stages to execute (defaults to full pipeline)
    stages:             list[PipelineStage] = field(
        default_factory=lambda: list(PipelineStage)
    )
    # Governance stage
    workflow_priority:  str                = "normal"   # WorkflowPriority enum value
    workflow_deadline:  str | None      = None       # ISO-8601 date
    # Misc
    as_of:              datetime | None = None
    metadata:           dict[str, Any]     = field(default_factory=dict)


# ── Orchestrator ─────────────────────────────────────────────────────────────

class RegulatoryIntelligencePlatform:
    """
    Unified orchestration façade for the Phase 13 regulatory intelligence layer.

    Sequences all services, records metrics, emits timeline events, and
    returns an immutable PipelineRun that describes exactly what happened.

    Usage
    ─────
        platform = get_platform()
        run = await platform.run(PipelineConfig(
            tenant_id   = "tenant-abc",
            source_url  = "https://hrsa.gov/notices/...",
            raw_content = pdf_bytes,
            doc_format  = "pdf",
            doc_source  = "hrsa",
            doc_domains = ["drug_340b", "contract_pharmacy"],
        ))
        print(run.to_dict())

    Note: `run()` is synchronous; the pipeline wraps async ingestion via
    asyncio.run() internally when called from a synchronous context.
    The async variant `run_async()` is available for async callers.
    """

    def __init__(self) -> None:
        self._runs: dict[str, PipelineRun] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def run(self, config: PipelineConfig) -> PipelineRun:
        """Execute the regulatory intelligence pipeline synchronously."""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Called from an async context — use the existing loop
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(asyncio.run, self._execute(config))
                    return future.result()
            return loop.run_until_complete(self._execute(config))
        except RuntimeError:
            return asyncio.run(self._execute(config))

    async def run_async(self, config: PipelineConfig) -> PipelineRun:
        """Execute the regulatory intelligence pipeline asynchronously."""
        return await self._execute(config)

    def get_run(self, run_id: str) -> PipelineRun | None:
        return self._runs.get(run_id)

    def run_history(
        self,
        tenant_id: str,
        limit:     int = 20,
    ) -> list[PipelineRun]:
        runs = [r for r in self._runs.values() if r.tenant_id == tenant_id]
        runs.sort(key=lambda r: r.started_at, reverse=True)
        return runs[:limit]

    def run_summary(self) -> dict[str, Any]:
        total   = len(self._runs)
        success = sum(1 for r in self._runs.values() if r.success)
        return {
            "total_runs":  total,
            "success":     success,
            "failed":      total - success,
            "pass_rate":   round(success / total, 4) if total else 0.0,
        }

    # ── Internal orchestration ────────────────────────────────────────────────

    async def _execute(self, config: PipelineConfig) -> PipelineRun:
        from regulatory.observability import get_metrics

        metrics     = get_metrics()
        wall_start  = time.monotonic()
        run = PipelineRun(
            run_id           = str(uuid.uuid4()),
            tenant_id        = config.tenant_id,
            triggered_by     = config.triggered_by,
            started_at       = config.as_of or datetime.now(tz=UTC),
            stages_requested = list(config.stages),
        )
        self._runs[run.run_id] = run

        log.info(
            "RegulatoryIntelligencePlatform: run %s started for tenant %s (%d stages)",
            run.run_id[:8], config.tenant_id[:8], len(config.stages),
        )

        # ── INGEST ──────────────────────────────────────────────────────────
        if PipelineStage.INGEST in config.stages and config.raw_content:
            run.stage_results.append(
                await self._stage_ingest(run, config, metrics)
            )

        # ── DIFF ────────────────────────────────────────────────────────────
        if PipelineStage.DIFF in config.stages and run.ingested_doc_id and config.prior_doc_id:
            run.stage_results.append(
                self._stage_diff(run, config, metrics)
            )

        # ── DRIFT ────────────────────────────────────────────────────────────
        if PipelineStage.DRIFT in config.stages:
            run.stage_results.append(
                self._stage_drift(run, config, metrics)
            )

        # ── IMPACT ──────────────────────────────────────────────────────────
        if PipelineStage.IMPACT in config.stages:
            run.stage_results.append(
                self._stage_impact(run, config, metrics)
            )

        # ── RECOMMEND ───────────────────────────────────────────────────────
        if PipelineStage.RECOMMEND in config.stages:
            run.stage_results.append(
                self._stage_recommend(run, config, metrics)
            )

        # ── READINESS ───────────────────────────────────────────────────────
        if PipelineStage.READINESS in config.stages:
            run.stage_results.append(
                self._stage_readiness(run, config, metrics)
            )

        # ── TIMELINE ────────────────────────────────────────────────────────
        if PipelineStage.TIMELINE in config.stages:
            run.stage_results.append(
                self._stage_timeline(run, config)
            )

        # ── GOVERNANCE ──────────────────────────────────────────────────────
        if PipelineStage.GOVERNANCE in config.stages and run.ingested_doc_id:
            run.stage_results.append(
                self._stage_governance(run, config, metrics)
            )

        # ── Finalise ─────────────────────────────────────────────────────────
        run.completed_at      = datetime.now(tz=UTC)
        run.total_duration_ms = (time.monotonic() - wall_start) * 1000.0
        run.success = all(r.success for r in run.stage_results)

        log.info(
            "RegulatoryIntelligencePlatform: run %s %s in %.1fms (%d/%d stages succeeded)",
            run.run_id[:8],
            "SUCCEEDED" if run.success else "COMPLETED WITH ERRORS",
            run.total_duration_ms,
            sum(1 for r in run.stage_results if r.success),
            len(run.stage_results),
        )
        return run

    # ── Stage implementations ─────────────────────────────────────────────────

    async def _stage_ingest(
        self,
        run:     PipelineRun,
        config:  PipelineConfig,
        metrics: Any,
    ) -> StageResult:
        from regulatory.ingestion.models import DocumentFormat, DocumentSource, PolicyDomain
        from regulatory.ingestion.pipeline import get_ingestion_pipeline
        from regulatory.observability import time_ingestion

        t0 = time.monotonic()
        try:
            pipeline = get_ingestion_pipeline()
            from regulatory.ingestion.pipeline import IngestRequest

            # Map string domain names to PolicyDomain enums (best-effort)
            domain_enums = []
            for d in config.doc_domains:
                try:
                    domain_enums.append(PolicyDomain(d))
                except ValueError:
                    pass

            source_enum = DocumentSource(config.doc_source) if config.doc_source else DocumentSource.HRSA
            format_enum = DocumentFormat(config.doc_format) if config.doc_format else DocumentFormat.PDF

            request = IngestRequest(
                source_url   = config.source_url or "",
                raw_content  = config.raw_content,
                source       = source_enum,
                format       = format_enum,
                domains      = domain_enums,
                title_hint   = config.doc_title,
                tenant_id    = config.tenant_id,
                triggered_by = config.triggered_by,
            )
            with time_ingestion(metrics):
                result = await pipeline.ingest(request)

            metrics.ingestion_total.inc()
            if result.success:
                metrics.ingestion_success.inc()
                run.ingested_doc_id = result.doc_id
                if result.deduplicated:
                    metrics.ingestion_dedup_hits.inc()
            else:
                metrics.ingestion_errors.inc()

            duration_ms = (time.monotonic() - t0) * 1000.0
            return StageResult(
                stage       = PipelineStage.INGEST,
                success     = result.success,
                duration_ms = duration_ms,
                summary     = f"Ingested doc {result.doc_id[:8] if result.doc_id else 'N/A'}; dedup={result.deduplicated}",
                error       = result.error_message,
                entity_ids  = [result.doc_id] if result.doc_id else [],
            )
        except Exception as exc:
            metrics.ingestion_errors.inc()
            return StageResult(
                stage       = PipelineStage.INGEST,
                success     = False,
                duration_ms = (time.monotonic() - t0) * 1000.0,
                summary     = "Ingestion stage failed",
                error       = f"{type(exc).__name__}: {exc}",
            )

    def _stage_diff(
        self,
        run:     PipelineRun,
        config:  PipelineConfig,
        metrics: Any,
    ) -> StageResult:
        from regulatory.diff.engine import get_diff_engine
        from regulatory.ingestion.pipeline import get_ingestion_pipeline
        from regulatory.observability import time_diff

        t0 = time.monotonic()
        try:
            pipeline = get_ingestion_pipeline()
            engine   = get_diff_engine()

            prior_doc = pipeline.get_document(config.prior_doc_id)
            new_doc   = pipeline.get_document(run.ingested_doc_id)

            if not prior_doc or not new_doc:
                return StageResult(
                    stage       = PipelineStage.DIFF,
                    success     = False,
                    duration_ms = (time.monotonic() - t0) * 1000.0,
                    summary     = "Could not load prior or new document for diff",
                    error       = "Document not found",
                )

            with time_diff(metrics):
                diff = engine.diff(prior_doc, new_doc)

            metrics.diffs_computed.inc()
            from regulatory.diff.engine import ChangeSeverity
            if diff.overall_severity == ChangeSeverity.CRITICAL:
                metrics.diffs_critical.inc()
            elif diff.overall_severity == ChangeSeverity.HIGH:
                metrics.diffs_high.inc()

            run.diff_id = diff.diff_id
            return StageResult(
                stage       = PipelineStage.DIFF,
                success     = True,
                duration_ms = (time.monotonic() - t0) * 1000.0,
                summary     = (
                    f"{len(diff.changes)} change(s) detected; "
                    f"overall severity: {diff.overall_severity.value}"
                ),
                entity_ids  = [diff.diff_id],
            )
        except Exception as exc:
            return StageResult(
                stage       = PipelineStage.DIFF,
                success     = False,
                duration_ms = (time.monotonic() - t0) * 1000.0,
                summary     = "Diff stage failed",
                error       = f"{type(exc).__name__}: {exc}",
            )

    def _stage_drift(
        self,
        run:     PipelineRun,
        config:  PipelineConfig,
        metrics: Any,
    ) -> StageResult:
        from regulatory.diff.drift import get_drift_service
        from regulatory.ingestion.pipeline import get_ingestion_pipeline

        t0 = time.monotonic()
        try:
            pipeline   = get_ingestion_pipeline()
            drift_svc  = get_drift_service()
            all_docs   = pipeline.list_current(config.tenant_id)

            # Gather diffs from this run if available
            diffs = []
            if run.diff_id:
                from regulatory.diff.engine import get_diff_engine
                d = get_diff_engine()._diffs.get(run.diff_id)
                if d:
                    diffs = [d]

            report = drift_svc.detect(
                tenant_id = config.tenant_id,
                documents = all_docs,
                diffs     = diffs or None,
            )
            metrics.drift_scans_total.inc()
            metrics.drift_findings_total.inc(len(report.findings))
            metrics.drift_critical.inc(report.critical_count)
            metrics.drift_high.inc(report.high_count)
            from regulatory.diff.drift import DriftType
            gap_count = sum(1 for f in report.findings if f.drift_type == DriftType.COVERAGE_GAP)
            metrics.drift_coverage_gaps.inc(gap_count)

            run.drift_report_id = str(report.report_id)
            return StageResult(
                stage       = PipelineStage.DRIFT,
                success     = True,
                duration_ms = (time.monotonic() - t0) * 1000.0,
                summary     = report.summary,
                entity_ids  = [str(report.report_id)],
            )
        except Exception as exc:
            return StageResult(
                stage       = PipelineStage.DRIFT,
                success     = False,
                duration_ms = (time.monotonic() - t0) * 1000.0,
                summary     = "Drift detection stage failed",
                error       = f"{type(exc).__name__}: {exc}",
            )

    def _stage_impact(
        self,
        run:     PipelineRun,
        config:  PipelineConfig,
        metrics: Any,
    ) -> StageResult:
        from regulatory.impact.analysis import get_impact_service
        from regulatory.observability import time_impact

        t0 = time.monotonic()
        try:
            impact_svc = get_impact_service()
            reports    = []
            entity_ids = []

            if run.diff_id:
                from regulatory.diff.engine import get_diff_engine
                diff = get_diff_engine()._diffs.get(run.diff_id)
                if diff:
                    with time_impact(metrics):
                        r = impact_svc.analyze_diff(config.tenant_id, diff)
                    reports.append(r)
                    entity_ids.append(r.report_id)
                    metrics.impact_reports_total.inc()
                    if r.financial_risk:
                        metrics.impact_financial_flags.inc()

            if run.drift_report_id:
                from regulatory.diff.drift import get_drift_service
                drift_svc  = get_drift_service()
                drift_rep  = drift_svc.get_report(run.drift_report_id)
                if drift_rep:
                    with time_impact(metrics):
                        r = impact_svc.analyze_drift(config.tenant_id, drift_rep)
                    reports.append(r)
                    entity_ids.append(r.report_id)
                    metrics.impact_reports_total.inc()

            run.impact_report_ids = entity_ids
            return StageResult(
                stage       = PipelineStage.IMPACT,
                success     = True,
                duration_ms = (time.monotonic() - t0) * 1000.0,
                summary     = f"{len(reports)} impact report(s) generated",
                entity_ids  = entity_ids,
            )
        except Exception as exc:
            return StageResult(
                stage       = PipelineStage.IMPACT,
                success     = False,
                duration_ms = (time.monotonic() - t0) * 1000.0,
                summary     = "Impact analysis stage failed",
                error       = f"{type(exc).__name__}: {exc}",
            )

    def _stage_recommend(
        self,
        run:     PipelineRun,
        config:  PipelineConfig,
        metrics: Any,
    ) -> StageResult:
        from regulatory.diff.drift import get_drift_service
        from regulatory.impact.analysis import get_impact_service
        from regulatory.recommendations.engine import get_recommendation_service

        t0 = time.monotonic()
        try:
            rec_svc    = get_recommendation_service()
            impact_svc = get_impact_service()
            drift_svc  = get_drift_service()
            recs       = []

            for rep_id in run.impact_report_ids:
                rep = impact_svc._reports.get(rep_id)
                if rep:
                    new_recs = rec_svc.generate_from_impact(
                        config.tenant_id, rep, generated_by=config.triggered_by
                    )
                    recs.extend(new_recs)
                    metrics.recs_created.inc(len(new_recs))

            if run.drift_report_id:
                drift_rep = drift_svc.get_report(run.drift_report_id)
                if drift_rep:
                    new_recs = rec_svc.generate_from_drift(
                        config.tenant_id, drift_rep, generated_by=config.triggered_by
                    )
                    recs.extend(new_recs)
                    metrics.recs_created.inc(len(new_recs))

            run.recommendation_ids = [r.rec_id for r in recs]
            metrics.recs_pending.inc(len(recs))

            return StageResult(
                stage       = PipelineStage.RECOMMEND,
                success     = True,
                duration_ms = (time.monotonic() - t0) * 1000.0,
                summary     = f"{len(recs)} recommendation(s) generated",
                entity_ids  = run.recommendation_ids,
            )
        except Exception as exc:
            return StageResult(
                stage       = PipelineStage.RECOMMEND,
                success     = False,
                duration_ms = (time.monotonic() - t0) * 1000.0,
                summary     = "Recommendation generation stage failed",
                error       = f"{type(exc).__name__}: {exc}",
            )

    def _stage_readiness(
        self,
        run:     PipelineRun,
        config:  PipelineConfig,
        metrics: Any,
    ) -> StageResult:
        from regulatory.diff.drift import get_drift_service
        from regulatory.ingestion.pipeline import get_ingestion_pipeline
        from regulatory.intelligence.readiness import ReadinessBand, get_readiness_service
        from regulatory.recommendations.engine import get_recommendation_service

        t0 = time.monotonic()
        try:
            ready_svc = get_readiness_service()
            pipeline  = get_ingestion_pipeline()
            drift_svc = get_drift_service()
            rec_svc   = get_recommendation_service()

            docs      = pipeline.list_current(config.tenant_id)
            drift_rep = drift_svc.get_report(run.drift_report_id) if run.drift_report_id else None
            all_recs  = rec_svc.list_recommendations(config.tenant_id)

            snapshot = ready_svc.assess(
                tenant_id       = config.tenant_id,
                documents       = docs,
                drift_findings  = drift_rep.findings if drift_rep else [],
                recommendations = all_recs,
                generated_by    = config.triggered_by,
            )

            metrics.readiness_snapshots.inc()
            if snapshot.band == ReadinessBand.STRONG:
                metrics.readiness_strong.inc()
            elif snapshot.band == ReadinessBand.AT_RISK:
                metrics.readiness_at_risk.inc()
            elif snapshot.band == ReadinessBand.CRITICAL:
                metrics.readiness_critical.inc()

            run.readiness_snapshot_id = snapshot.snapshot_id
            return StageResult(
                stage       = PipelineStage.READINESS,
                success     = True,
                duration_ms = (time.monotonic() - t0) * 1000.0,
                summary     = f"Readiness: {snapshot.band.value} (score {snapshot.score:.3f})",
                entity_ids  = [snapshot.snapshot_id],
            )
        except Exception as exc:
            return StageResult(
                stage       = PipelineStage.READINESS,
                success     = False,
                duration_ms = (time.monotonic() - t0) * 1000.0,
                summary     = "Readiness assessment stage failed",
                error       = f"{type(exc).__name__}: {exc}",
            )

    def _stage_timeline(
        self,
        run:    PipelineRun,
        config: PipelineConfig,
    ) -> StageResult:
        from regulatory.observability import get_metrics
        from regulatory.timeline.service import (
            TimelineEventType,
            get_timeline_service,
        )

        t0 = time.monotonic()
        metrics = get_metrics()
        try:
            tl  = get_timeline_service()
            cnt = 0

            if run.ingested_doc_id:
                tl.record(
                    tenant_id     = config.tenant_id,
                    event_type    = TimelineEventType.DOCUMENT_INGESTED,
                    title         = f"Document ingested by pipeline run {run.run_id[:8]}",
                    description   = (
                        f"Regulatory document '{config.doc_title or run.ingested_doc_id[:8]}' "
                        f"ingested via {config.doc_source} source."
                    ),
                    external_id   = run.ingested_doc_id,
                    external_type = "document",
                    actor_id      = config.triggered_by,
                )
                cnt += 1

            if run.drift_report_id:
                from regulatory.diff.drift import get_drift_service
                drift_rep = get_drift_service().get_report(run.drift_report_id)
                if drift_rep:
                    tl.record_drift_detected(
                        tenant_id        = config.tenant_id,
                        report_id        = run.drift_report_id,
                        overall_severity = drift_rep.overall_severity.value,
                        finding_count    = len(drift_rep.findings),
                        summary          = drift_rep.summary,
                    )
                    cnt += 1

            for rec_id in run.recommendation_ids:
                from regulatory.recommendations.engine import get_recommendation_service
                rec = get_recommendation_service().get(rec_id)
                if rec:
                    tl.record_recommendation_event(
                        tenant_id = config.tenant_id,
                        rec_id    = rec_id,
                        event     = "created",
                        title     = rec.title,
                        actor_id  = config.triggered_by,
                        priority  = rec.priority.value,
                    )
                    cnt += 1

            if run.readiness_snapshot_id:
                from regulatory.intelligence.readiness import get_readiness_service
                snap = get_readiness_service().get_snapshot(run.readiness_snapshot_id)
                if snap:
                    tl.record_readiness_assessed(
                        tenant_id    = config.tenant_id,
                        snapshot_id  = run.readiness_snapshot_id,
                        score        = snap.score,
                        band         = snap.band.value,
                        signal_count = len(snap.signals),
                    )
                    cnt += 1

            metrics.timeline_events_total.inc(cnt)
            return StageResult(
                stage       = PipelineStage.TIMELINE,
                success     = True,
                duration_ms = (time.monotonic() - t0) * 1000.0,
                summary     = f"{cnt} timeline event(s) recorded",
            )
        except Exception as exc:
            return StageResult(
                stage       = PipelineStage.TIMELINE,
                success     = False,
                duration_ms = (time.monotonic() - t0) * 1000.0,
                summary     = "Timeline recording stage failed",
                error       = f"{type(exc).__name__}: {exc}",
            )

    def _stage_governance(
        self,
        run:     PipelineRun,
        config:  PipelineConfig,
        metrics: Any,
    ) -> StageResult:
        from regulatory.governance.workflows import (
            WorkflowPriority,
            get_review_workflow,
        )
        from regulatory.ingestion.pipeline import get_ingestion_pipeline

        t0 = time.monotonic()
        try:
            review_wf = get_review_workflow()
            pipeline  = get_ingestion_pipeline()
            doc       = pipeline.get_document(run.ingested_doc_id)

            if doc is None:
                return StageResult(
                    stage       = PipelineStage.GOVERNANCE,
                    success     = False,
                    duration_ms = (time.monotonic() - t0) * 1000.0,
                    summary     = "No document found for governance workflow",
                    error       = "Document not found",
                )

            priority = WorkflowPriority(config.workflow_priority)
            wf = review_wf.create(
                tenant_id          = config.tenant_id,
                doc_id             = doc.doc_id,
                doc_version        = doc.version,
                doc_title          = doc.title,
                created_by         = config.triggered_by,
                priority           = priority,
                action_required_by = config.workflow_deadline,
            )
            metrics.workflows_created.inc()
            metrics.workflows_open.inc()
            run.workflow_id = str(wf.workflow_id)

            return StageResult(
                stage       = PipelineStage.GOVERNANCE,
                success     = True,
                duration_ms = (time.monotonic() - t0) * 1000.0,
                summary     = (
                    f"Activation workflow {str(wf.workflow_id)[:8]} created — "
                    f"awaiting review (priority: {priority.value})"
                ),
                entity_ids  = [str(wf.workflow_id)],
            )
        except Exception as exc:
            return StageResult(
                stage       = PipelineStage.GOVERNANCE,
                success     = False,
                duration_ms = (time.monotonic() - t0) * 1000.0,
                summary     = "Governance workflow creation failed",
                error       = f"{type(exc).__name__}: {exc}",
            )


# ── Singleton ─────────────────────────────────────────────────────────────────

_platform: RegulatoryIntelligencePlatform | None = None


def get_platform() -> RegulatoryIntelligencePlatform:
    global _platform
    if _platform is None:
        _platform = RegulatoryIntelligencePlatform()
    return _platform
