"""
Investigation & Case Orchestration runner — Phase 4.

Typical usage (after run_rules_engine.py has populated audit.audit_findings):

    python run_investigation.py
    python run_investigation.py --batch-id <uuid> --window-days 14
    python run_investigation.py --min-cluster-size 3

Subcommands:
    build   (default) — cluster findings into investigation cases
    status  <case_id> — print lifecycle info and latest risk snapshot for a case
    history <case_id> — print timeline for a case
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)

from app.database import SessionLocal
from investigation.domain.clustering import ClusterConfig
from investigation.services.case_builder import CaseBuilderService
from investigation.services.evidence import EvidenceAggregationService
from investigation.services.timeline import TimelineService


def cmd_build(args) -> None:
    config = ClusterConfig(
        window_days=args.window_days,
        min_cluster_size=args.min_cluster_size,
    )
    service = CaseBuilderService(commit_every=args.commit_every)

    with SessionLocal() as session:
        stats = service.run(session, batch_id=args.batch_id, config=config)

    print("\nCase Builder Summary")
    print(f"  Clusters formed  : {stats['clusters']:,}")
    print(f"  Cases created    : {stats['cases_created']:,}")
    print(f"  Findings linked  : {stats['findings_clustered']:,}")


def cmd_status(args) -> None:
    from uuid import UUID
    case_id = UUID(args.case_id)
    ev = EvidenceAggregationService()

    with SessionLocal() as session:
        snap = ev.latest_snapshot(session, case_id)

    if snap is None:
        print(f"No snapshot found for case {args.case_id}")
        return

    print(f"\nCase {args.case_id} — Risk Snapshot ({snap['trigger']})")
    print(f"  Total findings   : {snap['total_findings']}")
    print(f"  Critical / High  : {snap['by_severity']['critical']} / {snap['by_severity']['high']}")
    print(f"  Composite risk   : {snap['composite_risk_score']}")
    print(f"  Financial exp.   : {snap['total_financial_exposure']}")
    print(f"  Window           : {snap['temporal_window']['start']} → {snap['temporal_window']['end']}")
    print(f"  NDCs             : {', '.join(snap['ndc_list']) or 'N/A'}")
    print(f"  Unique patients  : {snap['unique_patients']}")
    print(f"  By rule          : {json.dumps(snap['findings_by_rule'], indent=4)}")


def cmd_history(args) -> None:
    from uuid import UUID
    case_id = UUID(args.case_id)
    tl = TimelineService()

    with SessionLocal() as session:
        events = tl.get_timeline(session, case_id)

    print(f"\nTimeline for case {args.case_id} ({len(events)} events)")
    for e in events:
        print(f"  [{e['occurred_at']}] {e['event_type']:<25} actor={e['actor_id']} ({e['actor_type']})")
        if e['event_data']:
            for k, v in e['event_data'].items():
                if v is not None:
                    print(f"      {k}: {v}")


def main() -> None:
    p = argparse.ArgumentParser(prog="run_investigation")
    sub = p.add_subparsers(dest="command")

    # build
    b = sub.add_parser("build", help="Cluster findings into investigation cases")
    b.add_argument("--batch-id",         default=None)
    b.add_argument("--window-days",      type=int, default=14)
    b.add_argument("--min-cluster-size", type=int, default=1)
    b.add_argument("--commit-every",     type=int, default=100)

    # status
    s = sub.add_parser("status", help="Show risk snapshot for a case")
    s.add_argument("case_id")

    # history
    h = sub.add_parser("history", help="Show timeline for a case")
    h.add_argument("case_id")

    args = p.parse_args()

    if args.command is None or args.command == "build":
        if args.command is None:
            # default: build with defaults
            args.batch_id = None
            args.window_days = 14
            args.min_cluster_size = 1
            args.commit_every = 100
        cmd_build(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "history":
        cmd_history(args)


if __name__ == "__main__":
    main()
