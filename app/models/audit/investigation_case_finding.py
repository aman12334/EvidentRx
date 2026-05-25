from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class InvestigationCaseFinding(Base):
    """
    Explicit junction between investigation cases and audit findings.
    Carries provenance: who linked this finding, when, and whether it is
    the primary (trigger) finding that caused case creation.

    A finding may belong to only one case at a time — enforced by the
    UNIQUE constraint and by the case_builder service.
    """

    __tablename__ = "investigation_case_findings"
    __table_args__ = (
        UniqueConstraint("case_id", "finding_id", name="uq_icf_case_finding"),
        {"schema": "audit"},
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    case_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("audit.investigation_cases.case_id", ondelete="CASCADE"),
        nullable=False,
    )
    finding_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("audit.audit_findings.finding_id"),
        nullable=False,
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"),
        comment="True for the finding that triggered case creation",
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    added_by: Mapped[str] = mapped_column(
        String(100), nullable=False, default="case_builder"
    )

    def __repr__(self) -> str:
        return f"<InvestigationCaseFinding case={self.case_id} finding={self.finding_id} primary={self.is_primary}>"
