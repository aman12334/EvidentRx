"""
Split Billing Mismatch violation injector.

Violation: Units dispensed exceed units purchased at 340B pricing in
the accumulation period — negative accumulator balance.

Injection strategy:
  - Force inventory.force_consume() instead of normal consume()
  - The resulting negative balance in split_billing.accumulator_balance
    triggers the SB-001 rule
"""
from __future__ import annotations

import random
from decimal import Decimal

from simulation.state import InventoryPool


def inject_consume(
    inventory: InventoryPool,
    ce_id: str,
    ndc_11: str,
    quantity: Decimal,
) -> tuple:
    """
    Force-consume from inventory beyond available balance.
    Returns (purchase_id, purchase_date) — may be None if no lots exist.
    """
    return inventory.force_consume(ce_id, ndc_11, quantity)
