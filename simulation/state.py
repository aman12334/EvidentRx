"""
Simulation state — inventory pool and patient pool.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional


@dataclass
class InventoryLot:
    purchase_id: str
    purchase_date: object      # date
    quantity_remaining: Decimal


class InventoryPool:
    """
    FIFO inventory pool per (ce_id, ndc_11).
    Dispenses consume oldest lots first — mirrors real 340B accumulator logic.
    """

    def __init__(self) -> None:
        # ce_id → ndc_11 → list[InventoryLot] (FIFO order)
        self._pool: dict[str, dict[str, list[InventoryLot]]] = defaultdict(lambda: defaultdict(list))

    def add_purchase(self, ce_id: str, ndc_11: str, purchase_id: str,
                     purchase_date: object, quantity: Decimal) -> None:
        self._pool[ce_id][ndc_11].append(
            InventoryLot(purchase_id=purchase_id, purchase_date=purchase_date,
                         quantity_remaining=quantity)
        )

    def consume(self, ce_id: str, ndc_11: str,
                quantity: Decimal) -> Optional[tuple[str, object]]:
        """
        Consume quantity from FIFO lots.
        Returns (purchase_id, purchase_date) of the lot consumed, or None if no inventory.
        """
        lots = self._pool[ce_id][ndc_11]
        for lot in lots:
            if lot.quantity_remaining >= quantity:
                lot.quantity_remaining -= quantity
                return lot.purchase_id, lot.purchase_date
        return None

    def available(self, ce_id: str, ndc_11: str) -> Decimal:
        return sum(
            lot.quantity_remaining
            for lot in self._pool[ce_id][ndc_11]
        )

    def force_consume(self, ce_id: str, ndc_11: str,
                      quantity: Decimal) -> tuple[Optional[str], Optional[object]]:
        """
        Consume even if insufficient inventory (creates negative balance = mismatch violation).
        Returns (purchase_id, purchase_date) of last lot touched, or (None, None).
        """
        lots = self._pool[ce_id][ndc_11]
        if not lots:
            return None, None
        # Drain all lots, allow to go negative on last
        remaining = quantity
        last_id, last_date = None, None
        for lot in lots:
            if lot.quantity_remaining > 0:
                take = min(lot.quantity_remaining, remaining)
                lot.quantity_remaining -= take
                remaining -= take
                last_id, last_date = lot.purchase_id, lot.purchase_date
            if remaining <= 0:
                break
        # Record deficit on last lot
        if remaining > 0 and lots:
            lots[-1].quantity_remaining -= remaining
            last_id = lots[-1].purchase_id
            last_date = lots[-1].purchase_date
        return last_id, last_date


class PatientPool:
    """
    Synthetic patient panel — deterministic, privacy-safe hashed IDs.
    Each patient has a stable payer_type drawn from the CE's payer mix.
    """

    def __init__(self, ce_id: str, n_patients: int, rng) -> None:
        self._patients = [
            _make_patient(ce_id, i, rng)
            for i in range(n_patients)
        ]
        self._rng = rng

    def sample(self) -> dict:
        return self._rng.choice(self._patients)


def _make_patient(ce_id: str, idx: int, rng) -> dict:
    raw = f"evidentrx:{ce_id}:{idx}"
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    payer = rng.choice(
        ["medicaid", "medicaid", "medicaid",        # ~35%
         "medicare_part_d", "medicare_part_d",      # ~25%
         "commercial", "commercial", "commercial",  # ~30%
         "self_pay",                                # ~10%
         "medicaid"],
    )
    return {"patient_id_hash": hashed, "payer_type": payer}
