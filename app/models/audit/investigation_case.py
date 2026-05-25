from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, List
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import AuditMixin, Base

if TYPE_CHECKING:
    from app.models.audit.audit_finding import AuditFinding


class InvestigationCase(Base, AuditMixin):
    """
    Investigation workflow state for a set of related audit findings.

    workflow_state stores serialized LangGraph graph state, enabling agent
    pause and resume across invocations. agent_workflow_id is the external
    run ID assigned by the orchestration engine.

    finding_count is a denormalized counter — increment / decrement in the
    application layer when findings are attached or detached.
    """

    __tablename__ = "investigation_cases"
    __table_args__ = {"schema": "audit"}

    case_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )

    # Identity
    case_number: Mapped[str] = mapped_column(
        String(50), nullable=False, unique=True,
        comment="Human-readable ID, e.g. INV-2025-00001",
    )
    covered_entity_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("ref.covered_entities.ce_id"),
        nullable=False,
    )
    case_type: Mapped[str] = mapped_column(String(50), nullable=False)

    # Workflow state
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="open")
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    assigned_to: Mapped[str | None] = mapped_column(String(255))
    escalated_to: Mapped[str | None] = mapped_column(String(255))

    # Dates
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    due_date: Mapped[date | None] = mapped_column(Date)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Financial exposure
    financial_exposure_estimate: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    financial_exposure_confirmed: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))

    # Aggregates
    finding_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # LangGraph agent orchestration
    agent_workflow_id: Mapped[str | None] = mapped_column(
        String(255), comment="External orchestration engine run ID"
    )
    workflow_state: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"),
        comment="Serialized LangGraph graph state — enables pause and resume",
    )
    workflow_checkpoint: Mapped[str | None] = mapped_column(
        Text, comment="LangGraph checkpoint identifier"
    )
    last_agent_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    findings: Mapped[List["AuditFinding"]] = relationship(
        "AuditFinding",
        foreign_keys="[AuditFinding.investigation_case_id]",
        back_populates="investigation_case",
        lazy="select",
    )

    def __repr__(self) -> str:
        return (
            f"<InvestigationCase {self.case_number} status={self.status} "
            f"priority={self.priority} findings={self.finding_count}>"
        )
