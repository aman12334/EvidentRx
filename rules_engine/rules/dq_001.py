"""
DQ-001: Missing Patient Identifier on 340B Dispense
"""
from __future__ import annotations

from uuid import UUID

from rules_engine.context import RuleContext
from rules_engine.finding_builder import build_finding


def evaluate(ctx: RuleContext, rule_id: UUID, rule_version: str) -> dict | None:
    if ctx.is_340b_purchase and not ctx.patient_id_hash:
        return build_finding(
            ctx, rule_id, "DQ-001", rule_version, "data_quality", "medium",
            evidence_extra={"trigger": "missing_patient_id_hash"},
        )
    return None
