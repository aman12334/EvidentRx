"""
Temporal Mismatch violation injector.

Violation: Dispense or claim occurs outside the CE's valid 340B program window
— either before participation_start or after program_termination_date.

Injection strategy:
  - Shift dispense_date to before the CE's program start date
  - The rules engine EE-001 checks dispense_date vs CE program dates
"""
from __future__ import annotations

import random
from datetime import date, timedelta


def inject(
    dispense: dict,
    ce_start: date | None,
    rng: random.Random,
) -> dict:
    """
    Backdates the dispense to before the CE's participation start date.
    Falls back to a fixed early date if ce_start is unknown.
    """
    anchor = ce_start or date(2000, 1, 1)
    # Place dispense 1-365 days before participation start
    bad_date = anchor - timedelta(days=rng.randint(1, 365))
    dispense["dispense_date"] = str(bad_date)
    dispense["dispense_date_raw"] = bad_date
    dispense["_violation_type"] = "temporal_mismatch"
    return dispense
