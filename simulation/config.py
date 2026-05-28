from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class SimConfig:
    # Simulation window
    period_start: date = date(2025, 1, 1)
    period_end: date = date(2025, 12, 31)

    # Reference data sampling
    n_ces: int = 50           # covered entities to simulate
    n_ndcs: int = 150         # drug formulary size drawn from real NDC table
    n_patients_per_ce: int = 400  # synthetic patient panel per CE

    # Purchase parameters (per CE per week)
    purchases_per_ce_week_min: int = 3
    purchases_per_ce_week_max: int = 15
    units_per_purchase_min: int = 50
    units_per_purchase_max: int = 600
    unit_price_min: float = 1.20
    unit_price_max: float = 480.00

    # Dispense parameters
    dispense_rate: float = 0.88       # fraction of purchased inventory that gets dispensed
    cp_dispense_rate: float = 0.60    # fraction of dispenses routed through contract pharmacies
    days_supply_choices: list[int] = field(default_factory=lambda: [30, 30, 60, 90])

    # Payer mix — must sum to 1.0
    payer_mix: dict[str, float] = field(default_factory=lambda: {
        "medicaid": 0.34,
        "medicare_part_d": 0.24,
        "commercial": 0.32,
        "self_pay": 0.10,
    })

    # Claim billing lag (days after dispense date)
    billing_lag_min: int = 1
    billing_lag_max: int = 45

    # Violation injection
    violation_rate: float = 0.07   # fraction of CE-weeks that receive a violation
    violation_mix: dict[str, float] = field(default_factory=lambda: {
        "duplicate_discount": 0.45,
        "contract_pharmacy_eligibility": 0.25,
        "split_billing_mismatch": 0.20,
        "temporal_mismatch": 0.10,
    })

    random_seed: int = 42
    db_batch_size: int = 500       # rows per DB flush
    source_tag: str = "simulation" # used in source_file column for lineage
