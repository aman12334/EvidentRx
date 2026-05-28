from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4
from typing import Optional

from simulation.config import SimConfig
from simulation.registry import CERecord, CPRecord
from simulation.state import InventoryPool, PatientPool


def generate_dispenses(
    ce: CERecord,
    active_cps: list[CPRecord],
    inventory: InventoryPool,
    patient_pool: PatientPool,
    ndc_11: str,
    drug_id: str,
    purchase_id: str,
    purchase_date: date,
    purchased_qty: Decimal,
    week_start: date,
    cfg: SimConfig,
    rng: random.Random,
    batch_id: str,
    force_pharmacy_id: Optional[str] = None,   # for violation injection
    force_payer: Optional[str] = None,         # for violation injection
    force_340b: bool = True,
) -> list[dict]:
    """
    Generate dispense events consuming from inventory.
    Returns list of dispense row dicts.
    """
    target_qty = purchased_qty * Decimal(str(cfg.dispense_rate))
    rows = []
    dispensed = Decimal("0")

    while dispensed < target_qty:
        patient = patient_pool.sample()
        days_supply = rng.choice(cfg.days_supply_choices)
        qty = Decimal(str(rng.randint(1, max(1, days_supply // 10))))

        result = inventory.consume(ce.ce_id, ndc_11, qty)
        if result is None:
            break
        consumed_purchase_id, consumed_purchase_date = result

        dispense_date = week_start + timedelta(days=rng.randint(0, 6))
        payer = force_payer or patient["payer_type"]

        # Route to contract pharmacy or CE directly
        cp_id: Optional[str] = None
        if force_pharmacy_id:
            cp_id = force_pharmacy_id
        elif active_cps and rng.random() < cfg.cp_dispense_rate:
            cp_id = rng.choice(active_cps).cp_id

        rows.append({
            "dispense_id": str(uuid4()),
            "dispense_date": str(dispense_date),
            "covered_entity_id": ce.ce_id,
            "contract_pharmacy_id": cp_id,
            "ndc_11": ndc_11,
            "drug_id": drug_id,
            "patient_id_hash": patient["patient_id_hash"],
            "prescriber_npi": _random_npi(rng),
            "rx_number": f"RX{rng.randint(1000000, 9999999)}",
            "fill_number": rng.randint(0, 5),
            "dispense_date_raw": dispense_date,   # keep for split billing linkage
            "days_supply": days_supply,
            "quantity": str(qty),
            "unit_of_measure": "EA",
            "payer_type": payer,
            "is_340b_dispense": force_340b,
            "carve_in_election": "carve_out" if payer == "medicaid" else "not_applicable",
            # Linkage for split billing builder
            "_purchase_id": consumed_purchase_id,
            "_purchase_date": str(consumed_purchase_date),
            "source_file": cfg.source_tag,
            "batch_id": batch_id,
            "created_at": "NOW()",
            "updated_at": "NOW()",
        })
        dispensed += qty

    return rows


def _random_npi(rng: random.Random) -> str:
    return str(rng.randint(1_000_000_000, 1_999_999_999))
