from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import AuditMixin, Base

if TYPE_CHECKING:
    from app.models.audit.investigation_case import InvestigationCase


class AuditFinding(Base, AuditMixin):
    """
    Individual compliance violation detected by the deterministic rules engine.

    Key design constraints:
    - evidence_payload is an immutable snapshot captured at detection time.
      Do not update it after creation — it is the canonical evidence record.
    - rule_version is denormalized to ensure historical replay uses the exact
      rule logic that produced the finding, even after rules are versioned forward.
    - Links to partitioned ops tables (purchase_id, dispense_id, claim_id) are
      logical — not DB-enforced. Include the companion date column for efficient
      partition-aware lookups.
    - LLMs write only to audit.reasoning_traces, never to this table directly.
      An AI-suggested status change must be confirmed by a human or rules engine.
    """

    __tablename__ = "audit_findings"
    __table_args__ = {"schema": "audit"}

    finding_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )

    # Identity
    finding_code: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="Human-readable code, e.g. DD-2025-001234",
    )
    rule_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("audit.compliance_rules.rule_id"),
        nullable=False,
    )
    rule_code: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="Denormalized for fast filtering without joining compliance_rules",
    )
    rule_version: Mapped[str] = mapped_column(
        String(20), nullable=False,
        comment="Denormalized — historical replay uses this version, not the current rule",
    )

    # Entity
    covered_entity_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("ref.covered_entities.ce_id"),
        nullable=False,
    )
    investigation_case_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("audit.investigation_cases.case_id"),
    )

    # Classification
    finding_type: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="open")

    # Detection metadata
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    detection_method: Mapped[str] = mapped_column(
        String(30), nullable=False, default="rules_engine"
    )
    confidence_score: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 4),
        comment="1.0 = deterministic; <1.0 = probabilistic",
    )

    # Financial exposure
    financial_exposure: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    financial_exposure_methodology: Mapped[str | None] = mapped_column(Text)

    # Logical links to partitioned ops tables (include date column for partition routing)
    purchase_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    purchase_date: Mapped[date | None] = mapped_column(Date)
    dispense_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    dispense_date: Mapped[date | None] = mapped_column(Date)
    claim_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    claim_service_date: Mapped[date | None] = mapped_column(Date)
    split_billing_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("ops.split_billing.split_billing_id"),
    )

    # Evidence snapshot (immutable after creation)
    evidence_payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"),
        comment="Full evidence snapshot at detection time — never update after creation",
    )
    entity_references: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # Violation period
    violation_period_start: Mapped[date | None] = mapped_column(Date)
    violation_period_end: Mapped[date | None] = mapped_column(Date)

    # Resolution
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[str | None] = mapped_column(String(255))
    resolution_type: Mapped[str | None] = mapped_column(String(30))
    resolution_notes: Mapped[str | None] = mapped_column(Text)

    # Relationships
    investigation_case: Mapped["InvestigationCase | None"] = relationship(
        "InvestigationCase",
        foreign_keys=[investigation_case_id],
        back_populates="findings",
    )

    def __repr__(self) -> str:
        return (
            f"<AuditFinding {self.finding_code} type={self.finding_type} "
            f"severity={self.severity} status={self.status}>"
        )
