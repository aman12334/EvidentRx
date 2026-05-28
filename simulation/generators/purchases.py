from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4

from simulation.config import SimConfig
from simulation.registry import CERecord, NDCRecord

WHOLESALERS = [
    "AmerisourceBergen Drug Corp",
    "Cardinal Health",
    "McKesson Corporation",
    "Smith Drug Company",
    "H.D. Smith",
]


def generate_purchases(
    ce: CERecord,
    ndcs: list[NDCRecord],
    week_start: date,
    cfg: SimConfig,
    rng: random.Random,
    batch_id: str,
) -> list[dict]:
    """
    Generate purchase events for one CE for one week.
    Each purchase = one invoice line for one NDC.
    """
    n = rng.randint(cfg.purchases_per_ce_week_min, cfg.purchases_per_ce_week_max)
    selected_ndcs = rng.choices(ndcs, k=n)
    rows = []

    for ndc in selected_ndcs:
        purchase_date = week_start + timedelta(days=rng.randint(0, 4))
        qty = Decimal(rng.randint(cfg.units_per_purchase_min, cfg.units_per_purchase_max))
        price = Decimal(str(round(rng.uniform(cfg.unit_price_min, cfg.unit_price_max), 4)))
        # 340B ceiling price is always lower than WAC
        ceiling = (price * Decimal("0.70")).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

        rows.append({
            "purchase_id": str(uuid4()),
            "purchase_date": str(purchase_date),
            "covered_entity_id": ce.ce_id,
            "ndc_11": ndc.ndc_11,
            "drug_id": ndc.drug_id,
            "wholesaler_name": rng.choice(WHOLESALERS),
            "invoice_number": f"INV-{rng.randint(100000, 999999)}",
            "quantity": str(qty),
            "unit_of_measure": "EA",
            "unit_price": str(price),
            "total_cost": str((qty * price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            "purchase_price_type": "340B",
            "is_340b_purchase": True,
            "ceiling_price": str(ceiling),
            "source_file": cfg.source_tag,
            "batch_id": batch_id,
            "created_at": "NOW()",
            "updated_at": "NOW()",
        })

    return rows
