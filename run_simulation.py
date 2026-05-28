"""
Simulation runner.

Usage:
    python run_simulation.py
    python run_simulation.py --ces 20 --weeks 12 --violation-rate 0.10 --seed 99
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)

from simulation.config import SimConfig
from simulation.orchestrator import SimulationOrchestrator


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--ces", type=int, default=50)
    p.add_argument("--ndcs", type=int, default=150)
    p.add_argument("--start", default="2025-01-01")
    p.add_argument("--end", default="2025-12-31")
    p.add_argument("--violation-rate", type=float, default=0.07)
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    cfg = SimConfig(
        period_start=date.fromisoformat(args.start),
        period_end=date.fromisoformat(args.end),
        n_ces=args.ces,
        n_ndcs=args.ndcs,
        violation_rate=args.violation_rate,
        random_seed=args.seed,
    )

    from app.database import SessionLocal
    with SessionLocal() as session:
        SimulationOrchestrator(cfg).run(session)


if __name__ == "__main__":
    main()
