"""
Agent Investigation runner — Phase 5.

Prerequisites:
    ANTHROPIC_API_KEY must be set (OPENAI_API_KEY optional as fallback)

Usage:
    python run_agents.py <case_id>
    python run_agents.py <case_id> --dry-run
    python run_agents.py --batch --limit 10
    python run_agents.py <case_id> --resume
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)

logger = logging.getLogger(__name__)


def cmd_single(args) -> None:
    from uuid import UUID
    from app.database import SessionLocal
    from agents.runner import InvestigationRunner

    case_id = UUID(args.case_id)

    if args.dry_run:
        print(f"[dry-run] Would investigate case {case_id}")
        print("  Graph nodes: case_intake → evidence_aggregation → risk_prioritization")
        print("             → pattern_analysis → narrative_generation → escalation_decision → case_summary")
        return

    runner = InvestigationRunner.from_env()

    with SessionLocal() as session:
        if args.resume:
            result = runner.resume(session, case_id)
        else:
            result = runner.run(session, case_id)

    _print_result(result)


def cmd_batch(args) -> None:
    from app.database import SessionLocal
    from agents.runner import InvestigationRunner
    from sqlalchemy import text

    runner = InvestigationRunner.from_env()

    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT case_id FROM audit.investigation_cases
            WHERE status IN ('open', 'triaged')
            ORDER BY priority DESC, opened_at ASC
            LIMIT :limit
        """), {"limit": args.limit}).fetchall()

        case_ids = [r.case_id for r in rows]

    logger.info("Batch: %d cases to investigate", len(case_ids))

    results = []
    with SessionLocal() as session:
        for case_id in case_ids:
            try:
                logger.info("Investigating case %s", case_id)
                result = runner.run(session, case_id)
                results.append(result)
            except Exception as e:
                logger.error("Failed case %s: %s", case_id, e)
                results.append({"case_id": str(case_id), "error": str(e)})

    print(f"\nBatch complete — {len(results)} cases processed")
    for r in results:
        if "error" in r:
            print(f"  FAILED  {r['case_id']}: {r['error']}")
        else:
            print(
                f"  {'ESCALATED' if r.get('escalated') else 'COMPLETE'} "
                f"{r['case_id']} "
                f"risk={r.get('risk_level', 'unknown')} "
                f"tokens={r.get('total_input_tokens', 0)+r.get('total_output_tokens', 0)}"
            )


def _print_result(result: dict) -> None:
    print("\n" + "=" * 60)
    print(f"Investigation Complete — {result['case_id']}")
    print("=" * 60)
    print(f"  Status    : {'ESCALATED' if result.get('escalated') else 'COMPLETE'}")
    print(f"  Risk level: {result.get('risk_level', 'unknown')}")
    print(f"  Errors    : {len(result.get('errors', []))}")
    print(f"  Tokens in : {result.get('total_input_tokens', 0):,}")
    print(f"  Tokens out: {result.get('total_output_tokens', 0):,}")
    print(f"  Cache hits: {result.get('cache_read_tokens', 0):,}")
    if result.get("executive_summary"):
        print(f"\nExecutive Summary:\n{result['executive_summary'][:500]}...")
    if result.get("errors"):
        print(f"\nErrors encountered:")
        for e in result["errors"]:
            print(f"  [{e.get('node')}] {e.get('error')}")


def main() -> None:
    p = argparse.ArgumentParser(prog="run_agents")
    sub = p.add_subparsers(dest="command")

    # Single case
    single = sub.add_parser("run", help="Investigate a single case")
    single.add_argument("case_id")
    single.add_argument("--dry-run",  action="store_true")
    single.add_argument("--resume",   action="store_true", help="Resume from last checkpoint")

    # Batch
    batch = sub.add_parser("batch", help="Investigate multiple open cases")
    batch.add_argument("--limit", type=int, default=10)

    args = p.parse_args()

    # Allow: python run_agents.py <uuid>  (shorthand for 'run')
    if args.command is None and len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        args.command = "run"
        args.case_id = sys.argv[1]
        args.dry_run = "--dry-run" in sys.argv
        args.resume  = "--resume" in sys.argv

    if args.command == "run":
        cmd_single(args)
    elif args.command == "batch":
        cmd_batch(args)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
