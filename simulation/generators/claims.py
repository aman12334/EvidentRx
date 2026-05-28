from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4
from typing import Optional

from simulation.config import SimConfig


_PAYER_TO_CLAIM_TYPE = {
    "medicaid": "medicaid",
    "medicare_part_d": "medicare_part_d",
    "commercial": "commercial",
    "self_pay": None,   # no claim for self-pay
}

_STATE_CODES = [
    "AL", "AZ", "AR", "CA", "CO", "CT", "FL", "GA", "IL", "IN",
    "KY", "LA", "MD", "MA", "MI", "MN", "MS", "MO", "NJ", "NM",
    "NY", "NC", "OH", "OK", "OR", "PA", "SC", "TN", "TX", "VA",
    "WA", "WV", "WI",
]


def generate_claim(
    dispense: dict,
    cfg: SimConfig,
    rng: random.Random,
    batch_id: str,
    force_medicaid: bool = False,    # violation: force Medicaid claim even if wrong payer
    force_340b_billed: bool = True,
) -> Optional[dict]:
    """
    Generate one claim row from a dispense event.
    Returns None for self-pay dispenses (no claim filed) unless force_medicaid=True.
    """
    payer_type = dispense["payer_type"]
    claim_type = _PAYER_TO_CLAIM_TYPE.get(payer_type)

    if force_medicaid:
        claim_type = "medicaid"
        payer_type = "medicaid"

    if claim_type is None:
        return None

    service_date_raw = dispense.get("dispense_date_raw")
    if service_date_raw is None:
        service_date_raw = date.fromisoformat(dispense["dispense_date"])

    lag = timedelta(days=rng.randint(cfg.billing_lag_min, cfg.billing_lag_max))
    billing_date = service_date_raw + lag
    paid_date = billing_date + timedelta(days=rng.randint(14, 60))

    qty = Decimal(dispense["quantity"])
    billed = (qty * Decimal(str(round(rng.uniform(4.0, 180.0), 2)))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    paid = (billed * Decimal(str(round(rng.uniform(0.60, 0.95), 4)))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    is_medicaid = claim_type == "medicaid"

    return {
        "claim_id": str(uuid4()),
        "service_date": str(service_date_raw),
        "covered_entity_id": dispense["covered_entity_id"],
        "dispense_id": dispense["dispense_id"],
        "dispense_date": dispense["dispense_date"],
        "claim_type": claim_type,
        "claim_status": "paid",
        "payer_id": f"PAYER-{rng.randint(1000, 9999)}",
        "patient_id_hash": dispense["patient_id_hash"],
        "prescriber_npi": dispense.get("prescriber_npi"),
        "rx_number": dispense.get("rx_number"),
        "ndc_11": dispense["ndc_11"],
        "drug_id": dispense.get("drug_id"),
        "billing_date": str(billing_date),
        "paid_date": str(paid_date),
        "quantity": dispense["quantity"],
        "days_supply": dispense.get("days_supply"),
        "billed_amount": str(billed),
        "paid_amount": str(paid),
        "state_code": rng.choice(_STATE_CODES),
        "is_medicaid": is_medicaid,
        "is_340b_billed": force_340b_billed,
        "billing_modifier": "UD" if force_340b_billed else None,
        "source_file": cfg.source_tag,
        "batch_id": batch_id,
        "created_at": "NOW()",
        "updated_at": "NOW()",
        # Linkage helper
        "_service_date_raw": service_date_raw,
    }
