"""
Contract Pharmacy Eligibility violation injector.

Violation: 340B drug dispensed at a pharmacy not registered as a
contract pharmacy for the covered entity at the time of service.

Injection strategy:
  - Replace contract_pharmacy_id with a synthetic non-registered pharmacy ID
  - This creates a CP_ID that does NOT exist in ref.contract_pharmacies for this CE
"""
from __future__ import annotations

import random
from uuid import uuid4


# Fake pharmacy IDs that will not match any registered CP
_UNREGISTERED_CP_IDS = [str(uuid4()) for _ in range(20)]


def inject(dispense: dict, rng: random.Random) -> dict:
    """
    Route dispense to an unregistered pharmacy.
    """
    dispense["contract_pharmacy_id"] = rng.choice(_UNREGISTERED_CP_IDS)
    dispense["_violation_type"] = "contract_pharmacy_eligibility"
    return dispense
