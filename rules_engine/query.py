"""
Batch query layer — fetches split_billing rows with all reference data needed
to populate a RuleContext without N+1 queries.

Returns rows in batches to avoid loading millions of records into memory.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Iterator
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from rules_engine.context import RuleContext

_BATCH_SQL = text("""
SELECT
    sb.split_billing_id,
    sb.covered_entity_id,
    sb.ndc_11,
    sb.service_date,
    sb.patient_id_hash,
    sb.purchase_id,
    sb.purchase_date,
    sb.dispense_id,
    sb.dispense_date,
    sb.claim_id,
    sb.claim_service_date,
    sb.is_340b_purchase,
    sb.is_medicaid_billed,
    sb.carve_in_flag,
    sb.accumulator_balance,
    sb.duplicate_discount_risk,
    sb.medicaid_overlap_risk,
    sb.carve_out_violation_risk,
    sb.ineligible_patient_risk,
    -- CE eligibility window
    ce.program_participation_start  AS ce_program_start,
    ce.program_termination_date     AS ce_program_end,
    -- Carve-out election: any active exclusion for this CE on service_date
    (
        SELECT COUNT(*) > 0
        FROM ref.medicaid_exclusions me
        WHERE me.hrsa_id = ce.hrsa_id
          AND me.exclusion_type = 'carve_out'
          AND me.period_start <= sb.service_date
          AND (me.period_end IS NULL OR me.period_end >= sb.service_date)
          AND me.is_current = TRUE
    )                               AS has_carve_out_election,
    -- NDC known in FDA directory
    (nd.ndc_11 IS NOT NULL)         AS ndc_known
FROM ops.split_billing sb
JOIN ref.covered_entities ce
    ON ce.ce_id = sb.covered_entity_id
    AND ce.is_current = TRUE
LEFT JOIN ref.ndc_drugs nd
    ON nd.ndc_11 = sb.ndc_11
WHERE sb.split_billing_id > :cursor
  AND (:batch_id IS NULL OR sb.batch_id = CAST(:batch_id AS uuid))
ORDER BY sb.split_billing_id
LIMIT :limit
""")


def iter_contexts(
    session: Session,
    batch_size: int = 5000,
    batch_id: str | None = None,
) -> Iterator[RuleContext]:
    """
    Yields RuleContext objects one at a time, paginating via keyset on split_billing_id.
    """
    cursor = "00000000-0000-0000-0000-000000000000"

    while True:
        rows = session.execute(
            _BATCH_SQL,
            {"cursor": cursor, "limit": batch_size, "batch_id": batch_id},
        ).fetchall()

        if not rows:
            break

        for row in rows:
            yield _row_to_context(row)

        cursor = str(rows[-1].split_billing_id)

        if len(rows) < batch_size:
            break


def _row_to_context(row) -> RuleContext:
    return RuleContext(
        split_billing_id=row.split_billing_id,
        covered_entity_id=row.covered_entity_id,
        ndc_11=row.ndc_11,
        service_date=row.service_date,
        patient_id_hash=row.patient_id_hash,
        purchase_id=row.purchase_id,
        purchase_date=row.purchase_date,
        dispense_id=row.dispense_id,
        dispense_date=row.dispense_date,
        claim_id=row.claim_id,
        claim_service_date=row.claim_service_date,
        is_340b_purchase=bool(row.is_340b_purchase),
        is_medicaid_billed=bool(row.is_medicaid_billed),
        carve_in_flag=row.carve_in_flag,
        accumulator_balance=Decimal(str(row.accumulator_balance)) if row.accumulator_balance is not None else None,
        duplicate_discount_risk=bool(row.duplicate_discount_risk),
        medicaid_overlap_risk=bool(row.medicaid_overlap_risk),
        carve_out_violation_risk=bool(row.carve_out_violation_risk),
        ineligible_patient_risk=bool(row.ineligible_patient_risk),
        ce_program_start=row.ce_program_start,
        ce_program_end=row.ce_program_end,
        has_carve_out_election=bool(row.has_carve_out_election) if row.has_carve_out_election is not None else None,
        extra={"ndc_known": bool(row.ndc_known)},
    )
