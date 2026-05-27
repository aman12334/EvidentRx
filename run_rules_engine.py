"""
Rules Engine runner.

Usage:
    python run_rules_engine.py
    python run_rules_engine.py --batch-id <uuid>
    python run_rules_engine.py --query-batch-size 10000 --db-batch-size 1000
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

from app.database import SessionLocal
from rules_engine.engine import RulesEngine


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--batch-id", default=None, help="Limit evaluation to a specific ingestion batch UUID")
    p.add_argument("--query-batch-size", type=int, default=5000)
    p.add_argument("--db-batch-size", type=int, default=500)
    args = p.parse_args()

    engine = RulesEngine(db_batch_size=args.db_batch_size)

    with SessionLocal() as session:
        stats = engine.run(
            session,
            batch_id=args.batch_id,
            query_batch_size=args.query_batch_size,
        )

    print("\nRules Engine Summary")
    print(f"  Evaluated : {stats['total_evaluated']:,}")
    print(f"  Findings  : {stats['total_findings']:,}")
    for code in sorted(k for k in stats if k not in ("total_evaluated", "total_findings")):
        if stats[code] > 0:
            print(f"  {code:<10}: {stats[code]:,}")


if __name__ == "__main__":
    main()
