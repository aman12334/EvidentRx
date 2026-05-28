"""
Duplicate Discount violation injector.

Violation: CE purchases drug at 340B price AND files a Medicaid claim
for the same drug/patient — prohibited under 42 U.S.C. § 256b(a)(5)(A).

Injection strategy:
  - Override payer to medicaid on the dispense
  - Force a Medicaid claim generation regardless of CE carve-out election
  - Set carve_in_election = 'carve_out' so the rules engine catches the conflict
"""
from __future__ import annotations

import random


def inject(
    dispense: dict,
    rng: random.Random,
) -> dict:
    """
    Mutate dispense in-place to create a duplicate discount condition.
    Returns the modified dispense. Caller must also force_medicaid=True on claim.
    """
    dispense["payer_type"] = "medicaid"
    dispense["carve_in_election"] = "carve_out"  # CE has carve-out but Medicaid claim will be filed
    dispense["_violation_type"] = "duplicate_discount"
    return dispense
