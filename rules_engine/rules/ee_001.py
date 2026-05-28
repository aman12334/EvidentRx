"""
EE-001: Dispense After Entity Termination from 340B Program
"""
from __future__ import annotations

from uuid import UUID

from rules_engine.context import RuleContext
from rules_engine.finding_builder import build_finding


def evaluate(ctx: RuleContext, rule_id: UUID, rule_version: str) -> dict | None:
    if (
        ctx.is_340b_purchase
        and ctx.ce_program_end is not None
        and ctx.service_date > ctx.ce_program_end
    ):
        return build_finding(
            ctx, rule_id, "EE-001", rule_version, "entity_eligibility", "critical",
            evidence_extra={
                "trigger": "dispense_after_ce_termination",
                "ce_program_end": ctx.ce_program_end.isoformat(),
                "service_date": ctx.service_date.isoformat(),
                "days_past_termination": (ctx.service_date - ctx.ce_program_end).days,
            },
        )
    return None
