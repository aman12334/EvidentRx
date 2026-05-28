from datetime import date
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Date, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import AuditMixin, Base


class ComplianceRule(Base, AuditMixin):
    """
    Versioned 340B compliance rule — deterministic source of truth for the rules engine.

    Rules are append-versioned: when logic changes, insert a new row with an
    incremented rule_version and set parent_rule_id to the prior version.
    Old rows are never updated — this preserves replay accuracy for historical findings.

    LLMs must never write to this table.
    """

    __tablename__ = "compliance_rules"
    __table_args__ = {"schema": "audit"}

    rule_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )

    # Identity
    rule_code: Mapped[str] = mapped_column(
        String(50), nullable=False, unique=True,
        comment="Short code used in finding_code prefixes, e.g. DD-001",
    )
    rule_name: Mapped[str] = mapped_column(String(255), nullable=False)
    rule_category: Mapped[str] = mapped_column(String(50), nullable=False)
    rule_version: Mapped[str] = mapped_column(
        String(20), nullable=False, default="1.0.0",
        comment="Semver — bump on any change to logic_definition or severity",
    )
    parent_rule_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("audit.compliance_rules.rule_id"),
        comment="Prior version — enables rule lineage traversal",
    )

    # Definition
    description: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    logic_definition: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"),
        comment="Engine-readable parameters — never read by LLMs",
    )
    regulatory_reference: Mapped[str | None] = mapped_column(
        Text, comment="340B statute / HRSA guidance citation"
    )

    # Lifecycle
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    expiration_date: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    # Self-referential relationship for version lineage
    parent_rule: Mapped["ComplianceRule | None"] = relationship(
        "ComplianceRule",
        remote_side="ComplianceRule.rule_id",
        foreign_keys=[parent_rule_id],
    )

    def __repr__(self) -> str:
        return f"<ComplianceRule {self.rule_code} v{self.rule_version} severity={self.severity}>"
