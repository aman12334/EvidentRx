"""
Loads active compliance rules from DB and builds a rule registry keyed by rule_code.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit.compliance_rule import ComplianceRule


@dataclass
class RuleRecord:
    rule_id: UUID
    rule_code: str
    rule_version: str
    rule_category: str
    severity: str
    is_active: bool


def load_rules(session: Session) -> dict[str, RuleRecord]:
    rows = session.execute(
        select(ComplianceRule).where(ComplianceRule.is_active == True)
    ).scalars().all()

    return {
        r.rule_code: RuleRecord(
            rule_id=r.rule_id,
            rule_code=r.rule_code,
            rule_version=r.rule_version,
            rule_category=r.rule_category,
            severity=r.severity,
            is_active=r.is_active,
        )
        for r in rows
    }
