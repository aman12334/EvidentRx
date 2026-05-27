"""
EvidentRx — Unified Platform CLI

Commands:
  bootstrap                    Validate environment and DB health
  pipeline                     Run full pipeline (simulation → agents)
  pipeline --resume <run_id>   Resume from a previous checkpoint
  pipeline --status <run_id>   Show checkpoint status for a run
  pipeline --list              List all pipeline runs
  export <case_id>             Export investigation reports (MD, JSON, HTML)
  traces <case_id>             Inspect reasoning traces
  checkpoints <case_id>        List workflow checkpoints
  inspect <case_id>            Full case summary
  eval                         Run evaluation harness (golden case)
  eval --seed <int>            Run evaluation with custom seed
  monitor                      Run continuous intelligence monitoring pass
  trends                       Show compliance trend analysis
  correlate                    Show cross-case correlation report
  risk-forecast                Show entity risk scores and 30-day forecasts
  copilot <case_id>            Run investigator copilot on a case

Usage:
    python run_platform.py bootstrap
    python run_platform.py pipeline
    python run_platform.py pipeline --resume <run_id>
    python run_platform.py export <case_id> [--format md|json|html|all]
    python run_platform.py traces <case_id>
    python run_platform.py checkpoints <case_id>
    python run_platform.py inspect <case_id>
    python run_platform.py eval
    python run_platform.py monitor [--persist]
    python run_platform.py trends [--window 30d]
    python run_platform.py correlate [--min-strength 0.2]
    python run_platform.py risk-forecast [--window 30d] [--top 10]
    python run_platform.py copilot <case_id> [--op summarize]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

# ─── Bootstrap logging before any imports ────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("platform")

_REPORTS_DIR = Path(os.path.dirname(__file__)) / "reports"


# =============================================================================
# Commands
# =============================================================================

def cmd_bootstrap(args) -> int:
    from runtime.bootstrap import HealthChecker
    report = HealthChecker().run()
    report.print_report()
    return 0 if report.passed else 1


def cmd_pipeline(args) -> int:
    from runtime.executor import PipelineExecutor, PipelineConfig
    from runtime.observability import RuntimeMetrics
    from uuid import uuid4

    executor = PipelineExecutor()

    if args.list:
        runs = executor.list_runs()
        if not runs:
            print("No pipeline runs found.")
            return 0
        print(f"\n{'Run ID':<38} {'Started':<22} Stages")
        print("─" * 80)
        for r in runs:
            stages_str = "  ".join(
                f"{s}={v}" for s, v in r["stages"].items()
            )
            print(f"{r['run_id']:<38} {str(r.get('started_at','—'))[:19]:<22} {stages_str}")
        return 0

    if args.status:
        data = executor.load_run(args.status)
        if not data:
            print(f"Run not found: {args.status}")
            return 1
        print(json.dumps(data, indent=2, default=str))
        return 0

    cfg = PipelineConfig(
        n_ces=args.ces,
        n_ndcs=args.ndcs,
        violation_rate=args.violation_rate,
        random_seed=args.seed,
        agent_batch_limit=args.agent_limit,
        skip_ingestion=not args.with_ingestion,
        skip_agents=args.skip_agents,
    )

    if args.resume:
        run = executor.resume(run_id=args.resume, config=cfg)
    else:
        # Pre-flight health check
        from runtime.bootstrap import HealthChecker
        report = HealthChecker().run()
        if not report.passed:
            report.print_report()
            print("Fix critical failures before running pipeline.")
            return 1

        run = executor.run(config=cfg)

    # Print stage summary
    print(f"\nPipeline run: {run.run_id}")
    for stage, info in run.stages.items():
        status = info.get("status", "unknown")
        elapsed = info.get("elapsed_s", 0)
        icon = {"completed": "✓", "failed": "✗", "skipped": "○"}.get(status, "?")
        print(f"  {icon}  {stage:<22} {status:<12} {elapsed:.1f}s")
        if info.get("error"):
            print(f"       ERROR: {info['error']}")

    has_failure = any(s.get("status") == "failed" for s in run.stages.values())
    return 1 if has_failure else 0


def cmd_export(args) -> int:
    from uuid import UUID
    from app.database import SessionLocal
    from reporting.base import ReportDataLoader
    from reporting.markdown_report import MarkdownReporter
    from reporting.json_export import JSONExporter
    from reporting.html_report import HTMLReporter

    try:
        case_id = UUID(args.case_id)
    except ValueError:
        print(f"Invalid case_id: {args.case_id}")
        return 1

    output_dir = Path(args.output_dir) if args.output_dir else _REPORTS_DIR

    with SessionLocal() as session:
        loader = ReportDataLoader()
        data   = loader.load(session, case_id)

    if not data.case:
        print(f"Case not found: {args.case_id}")
        return 1

    fmt = args.format.lower()
    written = []

    if fmt in ("md", "all"):
        p = MarkdownReporter().write(data, output_dir)
        written.append(("Markdown", p))

    if fmt in ("json", "all"):
        p = JSONExporter().write(data, output_dir)
        written.append(("JSON", p))

    if fmt in ("html", "all"):
        p = HTMLReporter().write(data, output_dir)
        written.append(("HTML", p))

    print(f"\nExports for case {args.case_id}:")
    for fmt_name, path in written:
        print(f"  {fmt_name:<8} → {path}")

    return 0


def cmd_traces(args) -> int:
    from uuid import UUID
    from sqlalchemy import text
    from app.database import SessionLocal

    try:
        case_id = UUID(args.case_id)
    except ValueError:
        print(f"Invalid case_id: {args.case_id}")
        return 1

    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT
                trace_id,
                agent_id,
                agent_type,
                workflow_node,
                workflow_step,
                confidence_score,
                input_context,
                created_at
            FROM audit.reasoning_traces
            WHERE case_id = :cid
            ORDER BY workflow_step ASC, created_at ASC
        """), {"cid": str(case_id)}).mappings().fetchall()

    if not rows:
        print(f"No reasoning traces found for case {args.case_id}")
        return 0

    print(f"\nReasoning traces for case {args.case_id} ({len(rows)} total)\n")
    for r in rows:
        conf = f"{r['confidence_score']:.3f}" if r["confidence_score"] is not None else "—"
        print(
            f"  Step {r['workflow_step']:>2}  "
            f"node={r['workflow_node']:<25} "
            f"agent={r['agent_id']:<35} "
            f"confidence={conf}"
        )
        if args.verbose and r["input_context"]:
            ctx = r["input_context"]
            if isinstance(ctx, str):
                import json as _j
                try:
                    ctx = _j.loads(ctx)
                except Exception:
                    pass
            print(f"            context: {json.dumps(ctx, default=str)}")

    return 0


def cmd_checkpoints(args) -> int:
    from uuid import UUID
    from sqlalchemy import text
    from app.database import SessionLocal

    try:
        case_id = UUID(args.case_id)
    except ValueError:
        print(f"Invalid case_id: {args.case_id}")
        return 1

    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT
                checkpoint_id,
                workflow_name,
                checkpoint_name,
                node_name,
                is_resumable,
                created_at
            FROM audit.workflow_checkpoints
            WHERE case_id = :cid
            ORDER BY created_at DESC
        """), {"cid": str(case_id)}).mappings().fetchall()

    if not rows:
        print(f"No checkpoints found for case {args.case_id}")
        return 0

    print(f"\nCheckpoints for case {args.case_id} ({len(rows)} total)\n")
    print(f"  {'Created':<22} {'Name':<30} {'Node':<25} Resumable")
    print("  " + "─" * 85)
    for r in rows:
        print(
            f"  {str(r['created_at'])[:19]:<22} "
            f"{r['checkpoint_name']:<30} "
            f"{r['node_name'] or '—':<25} "
            f"{'YES' if r['is_resumable'] else 'no'}"
        )

    return 0


def cmd_inspect(args) -> int:
    from uuid import UUID
    from app.database import SessionLocal
    from reporting.base import ReportDataLoader

    try:
        case_id = UUID(args.case_id)
    except ValueError:
        print(f"Invalid case_id: {args.case_id}")
        return 1

    with SessionLocal() as session:
        loader = ReportDataLoader()
        data   = loader.load(session, case_id)

    if not data.case:
        print(f"Case not found: {args.case_id}")
        return 1

    c    = data.case
    snap = data.risk_snapshot or {}
    by_sev = snap.get("by_severity", {})
    window = snap.get("temporal_window", {})

    print(f"\n{'='*62}")
    print(f"  Case: {c.get('case_number', args.case_id)}")
    print(f"{'='*62}")
    print(f"  Entity        : {data.ce_name}")
    print(f"  Category      : {c.get('violation_category','—')}")
    print(f"  Status        : {c.get('status','—').upper()}")
    print(f"  Priority      : {c.get('priority','—')}")
    print(f"  Opened        : {str(c.get('opened_at','—'))[:19]}")
    print(f"  Assigned      : {c.get('assigned_to') or 'Unassigned'}")
    print()
    print(f"  Total findings: {data.total_findings}")
    print(f"  Critical/High : {data.critical_count} / {data.high_count}")
    print(f"  Composite risk: {snap.get('composite_risk_score','—')}")
    print(f"  Financial exp.: ${data.financial_exposure:,.2f}" if data.financial_exposure else "  Financial exp.: —")
    print(f"  Window        : {window.get('start','—')} → {window.get('end','—')}")
    print(f"  NDCs affected : {len(snap.get('ndc_list',[]))}")
    print(f"  Unique pts    : {snap.get('unique_patients',0)}")
    print()

    findings_by_rule = snap.get("findings_by_rule", {})
    if findings_by_rule:
        print("  Findings by rule:")
        for rule, count in sorted(findings_by_rule.items()):
            print(f"    {rule:<12}: {count}")
        print()

    if data.reasoning_traces:
        print(f"  Reasoning traces : {len(data.reasoning_traces)}")
    if data.agent_runs:
        print(f"  Agent runs       : {len(data.agent_runs)}")
        total_tokens = sum(
            (r.get("input_tokens") or 0) + (r.get("output_tokens") or 0)
            for r in data.agent_runs
        )
        print(f"  Total tokens     : {total_tokens:,}")

    if data.narrative.get("executive_summary"):
        print(f"\n  Executive Summary (first 400 chars):")
        print(f"  {data.narrative['executive_summary'][:400]}...")

    print()
    return 0


def cmd_trace_viz(args) -> int:
    from uuid import UUID
    from app.database import SessionLocal
    from runtime.trace_viz import TraceVisualizer

    try:
        case_id = UUID(args.case_id)
    except ValueError:
        print(f"Invalid case_id: {args.case_id}")
        return 1

    viz = TraceVisualizer()
    with SessionLocal() as session:
        report = viz.build(session, case_id)

    if args.as_json:
        print(json.dumps(viz.to_json(report), indent=2, default=str))
    else:
        viz.print_report(report)

    return 0


def cmd_replay(args) -> int:
    from uuid import UUID
    from app.database import SessionLocal
    from runtime.replay import InvestigationReplayer

    try:
        case_id = UUID(args.case_id)
    except ValueError:
        print(f"Invalid case_id: {args.case_id}")
        return 1

    replayer = InvestigationReplayer.from_env()
    with SessionLocal() as session:
        report = replayer.replay(session, case_id, diff=args.diff)

    report.print_report()

    if args.output:
        Path(args.output).write_text(
            json.dumps(report.to_dict(), indent=2, default=str),
            encoding="utf-8",
        )
        print(f"Replay report written to: {args.output}")

    return 0 if report.passed else 1


def cmd_live_validate(args) -> int:
    from uuid import UUID
    from app.database import SessionLocal
    from runtime.live_validator import LiveExecutionValidator

    try:
        case_id = UUID(args.case_id)
    except ValueError:
        print(f"Invalid case_id: {args.case_id}")
        return 1

    validator = LiveExecutionValidator.from_env()
    with SessionLocal() as session:
        if args.all_agents:
            reports = validator.run_all_agents(session, case_id)
        else:
            reports = [validator.run(session, case_id, agent_type=args.agent_type)]

    all_passed = True
    results = []
    for r in reports:
        r.print_report()
        results.append(r.to_dict())
        if not r.passed:
            all_passed = False

    if args.output:
        output_data = results if len(results) > 1 else results[0]
        Path(args.output).write_text(
            json.dumps(output_data, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"Validation report written to: {args.output}")

    return 0 if all_passed else 1


def cmd_monitor(args) -> int:
    from app.database import SessionLocal
    from monitoring.engine import MonitoringEngine
    from monitoring.scheduler import MonitoringScheduler

    with SessionLocal() as session:
        scheduler = MonitoringScheduler()
        if not args.force:
            state = scheduler.check(session)
            if not state.is_due:
                print(f"[SKIP] Not due until {state.next_run_due}. Use --force to override.")
                return 0
            if scheduler.is_run_in_progress(session):
                print("[SKIP] A run is already in progress.")
                return 0

        engine = MonitoringEngine()
        result = engine.run(
            session,
            run_type="manual" if args.force else "scheduled",
            persist=args.persist,
        )
        session.commit()

        print(f"\n{'='*55}")
        print("MONITORING RUN COMPLETE")
        print(f"  Run ID:             {result.run_id}")
        print(f"  Status:             {result.status}")
        print(f"  Findings evaluated: {result.findings_evaluated}")
        print(f"  New findings:       {result.new_findings}")
        print(f"  Correlations found: {result.correlations_found}")
        print(f"  Drifts detected:    {result.drifts_detected}")
        if result.risk_report:
            print(f"  Risk — critical:    {result.risk_report.critical_count}")
            print(f"  Risk — high:        {result.risk_report.high_count}")
        if result.error_message:
            print(f"  Error:              {result.error_message}")
        print(f"{'='*55}\n")

    return 0 if result.status == "completed" else 1


def cmd_trends(args) -> int:
    from app.database import SessionLocal
    from intelligence.services.trend_analysis import TrendAnalysisService
    from intelligence.reports.trend_report import TrendReporter

    with SessionLocal() as session:
        svc      = TrendAnalysisService()
        summary  = svc.analyse(session, window_type=args.window)
        reporter = TrendReporter()

        if args.output:
            path = reporter.write(summary, args.output, fmt=args.format)
            print(f"Report written to: {path}")
        elif args.format == "json":
            print(json.dumps(reporter.render_json(summary), indent=2, default=str))
        else:
            print(reporter.render_markdown(summary))
    return 0


def cmd_correlate(args) -> int:
    from app.database import SessionLocal
    from intelligence.services.correlation import CorrelationEngine
    from intelligence.reports.correlation_report import CorrelationReporter

    with SessionLocal() as session:
        engine   = CorrelationEngine()
        report   = engine.run(session, min_strength=args.min_strength)
        reporter = CorrelationReporter()

        if args.output:
            path = reporter.write(report, args.output, fmt=args.format)
            print(f"Report written to: {path}")
        elif args.format == "json":
            print(json.dumps(reporter.render_json(report), indent=2, default=str))
        else:
            print(reporter.render_markdown(report))
    return 0


def cmd_risk_forecast(args) -> int:
    from app.database import SessionLocal
    from intelligence.services.predictive_risk import PredictiveRiskService
    from intelligence.reports.risk_forecast import RiskForecastReporter

    with SessionLocal() as session:
        svc      = PredictiveRiskService()
        report   = svc.score(session, window_type=args.window)
        reporter = RiskForecastReporter()

        if args.output:
            path = reporter.write(report, args.output, fmt=args.format)
            print(f"Report written to: {path}")
        elif args.format == "json":
            print(json.dumps(reporter.render_json(report), indent=2, default=str))
        else:
            top = report.top_risk(args.top)
            print(f"\nTop {len(top)} Risk Entities — {args.window} window")
            print(f"{'Tier':<10} {'Entity':<38} {'Score':>7} {'Velocity':>10} {'Direction'}")
            print("-" * 80)
            for s in top:
                print(
                    f"{s.risk_tier:<10} {s.entity_id:<38} "
                    f"{s.composite_score:>7.4f} {s.finding_velocity:>+10.4f} "
                    f"{s.trend_direction}"
                )
            print(
                f"\nTotal: {report.total_entities}  "
                f"Critical:{report.critical_count} High:{report.high_count} "
                f"Medium:{report.medium_count} Low:{report.low_count}"
            )
    return 0


def cmd_copilot(args) -> int:
    from uuid import UUID
    from app.database import SessionLocal
    from intelligence.services.copilot import InvestigatorCopilotService, CopilotOperation

    try:
        case_id = str(UUID(args.case_id))
    except ValueError:
        print(f"Invalid case_id: {args.case_id}")
        return 1

    op = args.operation
    investigator_id = args.investigator or "cli"

    with SessionLocal() as session:
        svc = InvestigatorCopilotService()

        if op == "summarize":
            resp = svc.summarize(session, case_id, investigator_id)
        elif op == "timeline":
            resp = svc.build_timeline(session, case_id, investigator_id)
        elif op == "recommend":
            resp = svc.recommend_next_steps(session, case_id, investigator_id,
                                            current_status=args.status or "open")
        elif op == "explain":
            resp = svc.explain_findings(session, case_id, investigator_id)
        else:
            print(f"Unknown operation: {op}")
            return 1

        session.commit()

    print(f"\n{'='*60}")
    print(f"COPILOT — {op.upper()} — Case {args.case_id[:16]}...")
    print(f"Session: {resp.session_id}")
    print(f"Confidence: {resp.confidence_score:.3f}  |  Tokens: {resp.input_tokens}in / {resp.output_tokens}out")
    print(f"{'='*60}")
    print(json.dumps(resp.output, indent=2, default=str))
    return 0


def cmd_eval(args) -> int:
    from app.database import SessionLocal
    from evaluation.harness import EvaluationHarness, GoldenCase
    from datetime import date

    gc = GoldenCase(
        name=f"golden_seed_{args.seed}",
        description="On-demand evaluation run",
        seed=args.seed,
        n_ces=args.ces,
        n_ndcs=args.ndcs,
        violation_rate=args.violation_rate,
        sim_start=date(2025, 1, 1),
        sim_end=date(2025, 3, 31),
        validate_agent_output=args.with_agents,
    )

    harness = EvaluationHarness()
    with SessionLocal() as session:
        result = harness.run_golden(session, gc)

    result.print_report()

    if args.output:
        Path(args.output).write_text(
            json.dumps(result.to_dict(), indent=2, default=str),
            encoding="utf-8",
        )
        print(f"Results written to: {args.output}")

    return 0 if result.passed else 1


# =============================================================================
# CLI wiring
# =============================================================================

def main() -> int:
    p = argparse.ArgumentParser(
        prog="run_platform",
        description="EvidentRx — 340B Compliance Platform CLI",
    )
    sub = p.add_subparsers(dest="command")

    # bootstrap
    sub.add_parser("bootstrap", help="Validate environment and DB health")

    # pipeline
    pp = sub.add_parser("pipeline", help="Run or resume the full operational pipeline")
    pp.add_argument("--resume",          default=None,  metavar="RUN_ID",
                    help="Resume from a previous checkpoint")
    pp.add_argument("--status",          default=None,  metavar="RUN_ID",
                    help="Show checkpoint status for a run")
    pp.add_argument("--list",            action="store_true",
                    help="List all pipeline runs")
    pp.add_argument("--ces",             type=int,   default=50)
    pp.add_argument("--ndcs",            type=int,   default=150)
    pp.add_argument("--violation-rate",  type=float, default=0.07, dest="violation_rate")
    pp.add_argument("--seed",            type=int,   default=42)
    pp.add_argument("--agent-limit",     type=int,   default=20, dest="agent_limit")
    pp.add_argument("--with-ingestion",  action="store_true", dest="with_ingestion",
                    help="Include real data ingestion stage (requires source files)")
    pp.add_argument("--skip-agents",     action="store_true", dest="skip_agents",
                    help="Skip agent investigation stage")

    # export
    ep = sub.add_parser("export", help="Export investigation reports")
    ep.add_argument("case_id")
    ep.add_argument("--format",     default="all", choices=["md", "json", "html", "all"])
    ep.add_argument("--output-dir", default=None, dest="output_dir")

    # traces
    tp = sub.add_parser("traces", help="Inspect reasoning traces for a case")
    tp.add_argument("case_id")
    tp.add_argument("--verbose", "-v", action="store_true")

    # checkpoints
    cp = sub.add_parser("checkpoints", help="List workflow checkpoints for a case")
    cp.add_argument("case_id")

    # inspect
    ip = sub.add_parser("inspect", help="Full case summary")
    ip.add_argument("case_id")

    # eval
    evp = sub.add_parser("eval", help="Run evaluation harness")
    evp.add_argument("--seed",           type=int,   default=42)
    evp.add_argument("--ces",            type=int,   default=5)
    evp.add_argument("--ndcs",           type=int,   default=30)
    evp.add_argument("--violation-rate", type=float, default=0.10, dest="violation_rate")
    evp.add_argument("--with-agents",    action="store_true", dest="with_agents",
                     help="Include agent output validation (requires API key)")
    evp.add_argument("--output",         default=None,
                     help="Write results JSON to this file")

    # trace-viz
    tvp = sub.add_parser("trace-viz", help="Visualize workflow topology and confidence propagation")
    tvp.add_argument("case_id")
    tvp.add_argument("--json", action="store_true", dest="as_json",
                     help="Output as JSON instead of text")

    # replay
    rpp = sub.add_parser("replay", help="Re-run agent investigation on an existing case")
    rpp.add_argument("case_id")
    rpp.add_argument("--diff", action="store_true",
                     help="Diff output against most recent prior run")
    rpp.add_argument("--output", default=None,
                     help="Write replay report JSON to this file")

    # live-validate
    lvp = sub.add_parser("live-validate", help="Validate a single live LLM agent call")
    lvp.add_argument("case_id")
    lvp.add_argument("--agent", default="evidence_analysis",
                     choices=["evidence_analysis", "risk_prioritization", "narrative_generation"],
                     dest="agent_type")
    lvp.add_argument("--all-agents", action="store_true", dest="all_agents",
                     help="Run validation for all three LLM agents")
    lvp.add_argument("--output", default=None,
                     help="Write validation report JSON to this file")

    # monitor
    mnp = sub.add_parser("monitor", help="Run intelligence monitoring pass")
    mnp.add_argument("--persist", action="store_true", help="Persist results to DB")
    mnp.add_argument("--force",   action="store_true", help="Run even if not scheduled")

    # trends
    trp = sub.add_parser("trends", help="Show compliance trend analysis")
    trp.add_argument("--window", default="30d", choices=["30d", "60d", "90d"])
    trp.add_argument("--output", default=None, help="Output directory")
    trp.add_argument("--format", default="markdown", choices=["markdown", "json"])

    # correlate
    cop = sub.add_parser("correlate", help="Show cross-case correlation report")
    cop.add_argument("--min-strength", type=float, default=0.15, dest="min_strength")
    cop.add_argument("--output", default=None, help="Output directory")
    cop.add_argument("--format", default="markdown", choices=["markdown", "json"])

    # risk-forecast
    rfp = sub.add_parser("risk-forecast", help="Show entity risk scores and 30-day forecasts")
    rfp.add_argument("--window", default="30d", choices=["30d", "60d", "90d"])
    rfp.add_argument("--top", type=int, default=10, help="Show top N entities")
    rfp.add_argument("--output", default=None, help="Output directory")
    rfp.add_argument("--format", default="markdown", choices=["markdown", "json"])

    # copilot
    cpp = sub.add_parser("copilot", help="Run investigator copilot on a case")
    cpp.add_argument("case_id")
    cpp.add_argument("--op", default="summarize",
                     choices=["summarize", "timeline", "recommend", "explain"],
                     dest="operation", metavar="OPERATION")
    cpp.add_argument("--investigator", default="cli", help="Investigator ID for audit trail")
    cpp.add_argument("--status", default="open", help="Case status (for recommend op)")

    args = p.parse_args()

    if args.command is None:
        p.print_help()
        return 0

    handlers = {
        "bootstrap":    cmd_bootstrap,
        "pipeline":     cmd_pipeline,
        "export":       cmd_export,
        "traces":       cmd_traces,
        "checkpoints":  cmd_checkpoints,
        "inspect":      cmd_inspect,
        "eval":         cmd_eval,
        "trace-viz":    cmd_trace_viz,
        "replay":       cmd_replay,
        "live-validate":cmd_live_validate,
        "monitor":      cmd_monitor,
        "trends":       cmd_trends,
        "correlate":    cmd_correlate,
        "risk-forecast":cmd_risk_forecast,
        "copilot":      cmd_copilot,
    }

    handler = handlers.get(args.command)
    if handler is None:
        print(f"Unknown command: {args.command}")
        return 1

    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
