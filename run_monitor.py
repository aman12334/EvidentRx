#!/usr/bin/env python3
"""
run_monitor.py — monitoring CLI runner for EvidentRx intelligence layer.

Commands:
  run          Run a full monitoring pass (trend + correlation + risk + drift)
  status       Show schedule status and recent run history
  trends       Show trend analysis for a given window
  correlate    Show cross-case correlations
  risk         Show entity risk scores and 30-day forecasts
  drift        Show drift detection report
  benchmark    Run the intelligence benchmark suite

Usage examples:
  python run_monitor.py run
  python run_monitor.py run --persist
  python run_monitor.py status
  python run_monitor.py trends --window 30d
  python run_monitor.py correlate --min-strength 0.3
  python run_monitor.py risk --top 10
  python run_monitor.py drift --window 90d
  python run_monitor.py benchmark
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from app.database import get_session_factory
from monitoring.engine import MonitoringEngine
from monitoring.scheduler import MonitoringScheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("run_monitor")


def cmd_run(args: argparse.Namespace, session_factory) -> int:
    with session_factory() as session:
        scheduler = MonitoringScheduler()

        if not args.force:
            state = scheduler.check(session)
            if not state.is_due:
                print(f"[SKIP] Monitoring not due until {state.next_run_due}. Use --force to override.")
                return 0

            if scheduler.is_run_in_progress(session):
                print("[SKIP] A monitoring run is already in progress.")
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
        print(f"{'='*55}")
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

        if args.json:
            print(json.dumps(result.metadata, indent=2, default=str))

        return 0 if result.status == "completed" else 1


def cmd_status(args: argparse.Namespace, session_factory) -> int:
    with session_factory() as session:
        scheduler = MonitoringScheduler()
        scheduler.print_status(session)

        recent = scheduler.list_recent_runs(session, limit=args.limit)
        if recent:
            print(f"{'Run ID':<38} {'Type':<12} {'Status':<12} {'Findings':>9} {'Correlations':>13} {'Started'}")
            print("-" * 100)
            for r in recent:
                started = str(r.get("started_at", ""))[:16]
                print(
                    f"{str(r['run_id']):<38} "
                    f"{str(r.get('run_type','')):<12} "
                    f"{str(r.get('status','')):<12} "
                    f"{r.get('findings_evaluated', 0):>9} "
                    f"{r.get('correlations_found', 0):>13} "
                    f"{started}"
                )
        return 0


def cmd_trends(args: argparse.Namespace, session_factory) -> int:
    from intelligence.reports.trend_report import TrendReporter
    from intelligence.services.trend_analysis import TrendAnalysisService

    with session_factory() as session:
        svc     = TrendAnalysisService()
        summary = svc.analyse(session, window_type=args.window)
        reporter = TrendReporter()

        if args.output:
            path = reporter.write(summary, args.output, fmt=args.format)
            print(f"Report written to: {path}")
        else:
            if args.format == "json":
                print(json.dumps(reporter.render_json(summary), indent=2, default=str))
            else:
                print(reporter.render_markdown(summary))
    return 0


def cmd_correlate(args: argparse.Namespace, session_factory) -> int:
    from intelligence.reports.correlation_report import CorrelationReporter
    from intelligence.services.correlation import CorrelationEngine

    with session_factory() as session:
        engine = CorrelationEngine()
        report = engine.run(session, min_strength=args.min_strength)
        reporter = CorrelationReporter()

        if args.output:
            path = reporter.write(report, args.output, fmt=args.format)
            print(f"Report written to: {path}")
        else:
            if args.format == "json":
                print(json.dumps(reporter.render_json(report), indent=2, default=str))
            else:
                print(reporter.render_markdown(report))
    return 0


def cmd_risk(args: argparse.Namespace, session_factory) -> int:
    from intelligence.reports.risk_forecast import RiskForecastReporter
    from intelligence.services.predictive_risk import PredictiveRiskService

    with session_factory() as session:
        svc     = PredictiveRiskService()
        report  = svc.score(session, window_type=args.window)
        reporter = RiskForecastReporter()

        if args.output:
            path = reporter.write(report, args.output, fmt=args.format)
            print(f"Report written to: {path}")
        else:
            if args.format == "json":
                print(json.dumps(reporter.render_json(report), indent=2, default=str))
            else:
                # Truncated CLI view for top N
                top = report.top_risk(args.top)
                print(f"\nTop {len(top)} Risk Entities (window={args.window}):")
                print(f"{'Tier':<10} {'Entity ID':<38} {'Score':>7} {'Velocity':>10} {'Direction'}")
                print("-" * 80)
                for s in top:
                    print(
                        f"{s.risk_tier:<10} {s.entity_id:<38} "
                        f"{s.composite_score:>7.4f} {s.finding_velocity:>+10.4f} "
                        f"{s.trend_direction}"
                    )
                print(f"\nTotal: {report.total_entities} entities | "
                      f"Critical:{report.critical_count} High:{report.high_count} "
                      f"Medium:{report.medium_count} Low:{report.low_count}")
    return 0


def cmd_drift(args: argparse.Namespace, session_factory) -> int:
    from intelligence.services.drift_detection import DriftDetectionService

    with session_factory() as session:
        svc    = DriftDetectionService()
        report = svc.detect(session, window_type=args.window)

        print(f"\nDrift Detection Report — {args.window} window")
        print(f"Total signals: {report.total_signals}  Critical: {report.critical_count}  High: {report.high_count}")

        for category, signals in [
            ("RULE DRIFT",   report.rule_drift),
            ("ENTITY DRIFT", report.entity_drift),
            ("MODEL DRIFT",  report.model_drift),
        ]:
            if not signals:
                continue
            print(f"\n{category}:")
            for s in signals[:10]:
                print(f"  [{s.magnitude.upper():<8}] {s.subject_label[:40]:<40} {s.change_pct:+.1f}%")
                print(f"             {s.explanation}")

        if args.json:
            data = {
                "total_signals": report.total_signals,
                "critical":      report.critical_count,
                "high":          report.high_count,
                "signals":       [
                    {"type": s.drift_type, "subject": s.subject_id,
                     "magnitude": s.magnitude, "change_pct": s.change_pct,
                     "explanation": s.explanation}
                    for s in report.all_signals()
                ],
            }
            print(json.dumps(data, indent=2))
    return 0


def cmd_benchmark(args: argparse.Namespace, session_factory) -> int:
    from evaluation.benchmark import BenchmarkSuite

    with session_factory() as session:
        suite  = BenchmarkSuite()
        result = suite.run(session, persist=args.persist)
        session.commit()
        result.print_report()

        if args.json:
            print(json.dumps(result.to_dict(), indent=2))

        return 0 if result.all_passed else 1


# ------------------------------------------------------------------ #
# Argument parsing                                                     #
# ------------------------------------------------------------------ #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_monitor",
        description="EvidentRx Intelligence Monitoring Runner",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # run
    p_run = sub.add_parser("run", help="Execute a monitoring run")
    p_run.add_argument("--persist", action="store_true", help="Persist results to DB")
    p_run.add_argument("--force", action="store_true", help="Run even if not scheduled")
    p_run.add_argument("--json", action="store_true", help="Output metadata as JSON")

    # status
    p_status = sub.add_parser("status", help="Show schedule status and recent runs")
    p_status.add_argument("--limit", type=int, default=10, help="Number of recent runs to show")

    # trends
    p_trends = sub.add_parser("trends", help="Show compliance trend analysis")
    p_trends.add_argument("--window", default="30d", choices=["30d", "60d", "90d"])
    p_trends.add_argument("--output", help="Output directory (writes file)")
    p_trends.add_argument("--format", default="markdown", choices=["markdown", "json"])

    # correlate
    p_corr = sub.add_parser("correlate", help="Show cross-case correlations")
    p_corr.add_argument("--min-strength", type=float, default=0.15, dest="min_strength")
    p_corr.add_argument("--output", help="Output directory")
    p_corr.add_argument("--format", default="markdown", choices=["markdown", "json"])

    # risk
    p_risk = sub.add_parser("risk", help="Show entity risk scores and forecasts")
    p_risk.add_argument("--window", default="30d", choices=["30d", "60d", "90d"])
    p_risk.add_argument("--top", type=int, default=10, help="Show top N entities")
    p_risk.add_argument("--output", help="Output directory")
    p_risk.add_argument("--format", default="markdown", choices=["markdown", "json"])

    # drift
    p_drift = sub.add_parser("drift", help="Show drift detection report")
    p_drift.add_argument("--window", default="30d", choices=["30d", "60d", "90d"])
    p_drift.add_argument("--json", action="store_true")

    # benchmark
    p_bench = sub.add_parser("benchmark", help="Run intelligence benchmark suite")
    p_bench.add_argument("--persist", action="store_true", help="Persist to evaluation_runs")
    p_bench.add_argument("--json", action="store_true")

    return parser


def main() -> int:
    parser = build_parser()
    args   = parser.parse_args()

    session_factory = get_session_factory()

    dispatch = {
        "run":       cmd_run,
        "status":    cmd_status,
        "trends":    cmd_trends,
        "correlate": cmd_correlate,
        "risk":      cmd_risk,
        "drift":     cmd_drift,
        "benchmark": cmd_benchmark,
    }

    handler = dispatch.get(args.command)
    if not handler:
        parser.print_help()
        return 1

    return handler(args, session_factory)


if __name__ == "__main__":
    sys.exit(main())
