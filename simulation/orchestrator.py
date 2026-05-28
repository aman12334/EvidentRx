"""
Simulation Orchestrator

Event-driven 340B pharmacy workflow simulation.

Causal chain per week per CE:
  PurchaseEvent → InventoryPool → DispenseEvent → ClaimEvent → SplitBillingRecord

Violations are injected at generation time — modifying the causal chain
at the correct point so the resulting data is internally consistent.
"""
from __future__ import annotations

import logging
import random
from datetime import UTC, date, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

import simulation.violations.contract_violation as viol_cp
import simulation.violations.duplicate_discount as viol_dd
import simulation.violations.split_billing_mismatch as viol_sb
import simulation.violations.temporal_mismatch as viol_tm
from ingestion.base import bulk_insert
from simulation.config import SimConfig
from simulation.generators.claims import generate_claim
from simulation.generators.dispenses import generate_dispenses
from simulation.generators.purchases import generate_purchases
from simulation.generators.split_billing import build_split_billing
from simulation.registry import ReferenceRegistry
from simulation.state import InventoryPool, PatientPool

logger = logging.getLogger(__name__)

_VIOLATION_TYPES = list(SimConfig().violation_mix.keys())


class SimulationOrchestrator:
    def __init__(self, cfg: SimConfig):
        self.cfg = cfg
        self.rng = random.Random(cfg.random_seed)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, session: Session) -> None:
        cfg = self.cfg

        logger.info("Loading reference registry...")
        registry = ReferenceRegistry.load(session, n_ces=cfg.n_ces, n_ndcs=cfg.n_ndcs)

        if not registry.ces:
            raise RuntimeError("No covered entities in DB — run ingestion first.")
        if not registry.ndcs:
            raise RuntimeError("No NDC drugs in DB — run NDC ingestion first.")

        # Build per-CE patient pools
        patient_pools = {
            ce.ce_id: PatientPool(ce.ce_id, cfg.n_patients_per_ce, self.rng)
            for ce in registry.ces
        }

        # Shared inventory pool across all CEs
        inventory = InventoryPool()

        # Simulation batch record
        batch_id = self._create_sim_batch(session)

        # Week-by-week iteration
        weeks = list(_week_starts(cfg.period_start, cfg.period_end))
        logger.info("Simulating %d weeks × %d CEs", len(weeks), len(registry.ces))

        all_purchases: list[dict] = []
        all_dispenses: list[dict] = []
        all_claims: list[dict] = []
        all_split: list[dict] = []

        for week_idx, week_start in enumerate(weeks):
            for ce in registry.ces:
                violation_type = self._sample_violation()
                active_cps = registry.active_cps_for_ce(ce.ce_id, week_start)
                patient_pool = patient_pools[ce.ce_id]

                # --- PURCHASES ---
                purchase_rows = generate_purchases(
                    ce, registry.ndcs, week_start, cfg, self.rng, str(batch_id)
                )
                for p in purchase_rows:
                    inventory.add_purchase(
                        ce.ce_id,
                        p["ndc_11"],
                        p["purchase_id"],
                        date.fromisoformat(p["purchase_date"]),
                        Decimal(p["quantity"]),
                    )
                all_purchases.extend(purchase_rows)

                # --- DISPENSES + CLAIMS + SPLIT BILLING ---
                for p in purchase_rows:
                    ndc_11 = p["ndc_11"]
                    drug_id = p["drug_id"]
                    qty = Decimal(p["quantity"])

                    force_pharmacy_id: str | None = None
                    force_payer: str | None = None
                    force_medicaid_claim = False

                    # Violation injection — modify generation parameters
                    if violation_type == "contract_pharmacy_eligibility":
                        # Will be injected per-dispense below
                        pass
                    elif violation_type == "duplicate_discount":
                        force_payer = "medicaid"
                        force_medicaid_claim = True

                    # Use force-consume for split billing mismatch
                    use_force_consume = violation_type == "split_billing_mismatch"

                    dispense_rows = generate_dispenses(
                        ce=ce,
                        active_cps=active_cps,
                        inventory=inventory,
                        patient_pool=patient_pool,
                        ndc_11=ndc_11,
                        drug_id=drug_id,
                        purchase_id=p["purchase_id"],
                        purchase_date=date.fromisoformat(p["purchase_date"]),
                        purchased_qty=qty,
                        week_start=week_start,
                        cfg=cfg,
                        rng=self.rng,
                        batch_id=str(batch_id),
                        force_pharmacy_id=force_pharmacy_id,
                        force_payer=force_payer,
                    )

                    for dispense in dispense_rows:
                        # Apply violation mutations
                        if violation_type == "duplicate_discount":
                            viol_dd.inject(dispense, self.rng)
                        elif violation_type == "contract_pharmacy_eligibility":
                            viol_cp.inject(dispense, self.rng)
                        elif violation_type == "temporal_mismatch":
                            viol_tm.inject(dispense, ce.program_participation_start, self.rng)

                        if use_force_consume:
                            extra_qty = Decimal(str(self.rng.randint(50, 200)))
                            pid, pdate = viol_sb.inject_consume(
                                inventory, ce.ce_id, ndc_11, extra_qty
                            )
                            if pid:
                                dispense["_purchase_id"] = pid
                                dispense["_purchase_date"] = str(pdate)

                        # Generate claim
                        claim = generate_claim(
                            dispense, cfg, self.rng, str(batch_id),
                            force_medicaid=force_medicaid_claim,
                        )

                        # Build split billing record
                        balance = inventory.available(ce.ce_id, ndc_11)
                        sb = build_split_billing(
                            dispense, claim, balance, cfg.source_tag, str(batch_id)
                        )

                        all_dispenses.append(dispense)
                        if claim:
                            all_claims.append(claim)
                        all_split.append(sb)

            # Flush to DB every 4 weeks to manage memory
            if (week_idx + 1) % 4 == 0 or week_idx == len(weeks) - 1:
                logger.info(
                    "Week %d/%d — flushing: %d purchases %d dispenses %d claims %d split",
                    week_idx + 1, len(weeks),
                    len(all_purchases), len(all_dispenses), len(all_claims), len(all_split),
                )
                self._flush(session, all_purchases, all_dispenses, all_claims, all_split)
                all_purchases.clear()
                all_dispenses.clear()
                all_claims.clear()
                all_split.clear()
                session.commit()

        self._complete_sim_batch(session, batch_id)
        session.commit()
        logger.info("Simulation complete.")

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _create_sim_batch(self, session: Session) -> object:
        from datetime import datetime

        from app.models.meta.ingestion_batch import IngestionBatch
        batch = IngestionBatch(
            batch_name="synthetic_operational_simulation",
            source_type="other",
            source_file=self.cfg.source_tag,
            status="processing",
            started_at=datetime.now(UTC),
        )
        session.add(batch)
        session.flush()
        return batch.batch_id

    def _complete_sim_batch(self, session: Session, batch_id) -> None:
        session.execute(text("""
            UPDATE meta.ingestion_batches
            SET status = 'completed', completed_at = NOW()
            WHERE batch_id = :bid
        """), {"bid": str(batch_id)})

    def _flush(
        self,
        session: Session,
        purchases: list[dict],
        dispenses: list[dict],
        claims: list[dict],
        split: list[dict],
    ) -> None:
        cfg = self.cfg

        def _clean(rows: list[dict], drop_keys: list[str]) -> list[dict]:
            return [{k: v for k, v in r.items() if k not in drop_keys} for r in rows]

        _internal = ["dispense_date_raw", "_purchase_id", "_purchase_date",
                     "_violation_type", "_service_date_raw"]

        if purchases:
            for i in range(0, len(purchases), cfg.db_batch_size):
                _bulk_insert_with_now(session, "ops.purchases", purchases[i:i+cfg.db_batch_size])

        if dispenses:
            clean_d = _clean(dispenses, _internal)
            for i in range(0, len(clean_d), cfg.db_batch_size):
                _bulk_insert_with_now(session, "ops.dispenses", clean_d[i:i+cfg.db_batch_size])

        if claims:
            clean_c = _clean(claims, ["_service_date_raw"])
            for i in range(0, len(clean_c), cfg.db_batch_size):
                _bulk_insert_with_now(session, "ops.claims", clean_c[i:i+cfg.db_batch_size])

        if split:
            for i in range(0, len(split), cfg.db_batch_size):
                _bulk_insert_with_now(session, "ops.split_billing", split[i:i+cfg.db_batch_size])

    # ------------------------------------------------------------------
    # Violation sampling
    # ------------------------------------------------------------------

    def _sample_violation(self) -> str | None:
        if self.rng.random() > self.cfg.violation_rate:
            return None
        types = list(self.cfg.violation_mix.keys())
        weights = list(self.cfg.violation_mix.values())
        return self.rng.choices(types, weights=weights, k=1)[0]


# ------------------------------------------------------------------
# Utilities
# ------------------------------------------------------------------

def _week_starts(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(weeks=1)


def _bulk_insert_with_now(session: Session, table: str, rows: list[dict]) -> None:
    """Insert rows, replacing 'NOW()' string values with actual timestamp."""
    if not rows:
        return
    clean = []
    for row in rows:
        clean.append({
            k: None if v == "NOW()" else v
            for k, v in row.items()
        })
    bulk_insert(session, table, clean)
