"""
DD-001: Duplicate Discount — 340B Purchase + Medicaid Claim Same Drug/Patient
"""
from __future__ import annotations

from uuid import UUID

from rules_engine.context import RuleContext
from rules_engine.finding_builder import build_finding


def evaluate(ctx: RuleContext, rule_id: UUID, rule_version: str) -> dict | None:
    if ctx.is_340b_purchase and ctx.is_medicaid_billed and ctx.patient_id_hash:
        return build_finding(
            ctx, rule_id, "DD-001", rule_version, "duplicate_discount", "critical",
            evidence_extra={"trigger": "340b_purchase_and_medicaid_claim_same_patient"},
        )
    return None
