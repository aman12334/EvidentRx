from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AnalystOverride(Base):
    """
    Records analyst overrides: false positives, risk level adjustments,
    escalation decisions. Used for confidence calibration and evaluation memory.
    """
    __tablename__ = "analyst_overrides"
    __table_args__ = {"schema": "audit"}

    override_id:    Mapped[UUID]          = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    finding_id:     Mapped[Optional[UUID]]= mapped_column(PGUUID(as_uuid=True), ForeignKey("audit.audit_findings.finding_id"))
    case_id:        Mapped[Optional[UUID]]= mapped_column(PGUUID(as_uuid=True), ForeignKey("audit.investigation_cases.case_id"))
    analyst_id:     Mapped[str]           = mapped_column(String(255), nullable=False)
    override_type:  Mapped[str]           = mapped_column(String(50),  nullable=False)
    original_value: Mapped[dict]          = mapped_column(JSONB(), nullable=False, server_default=text("'{}'::jsonb"))
    override_value: Mapped[dict]          = mapped_column(JSONB(), nullable=False, server_default=text("'{}'::jsonb"))
    rationale:      Mapped[Optional[str]] = mapped_column(Text())
    created_at:     Mapped[datetime]      = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    def __repr__(self) -> str:
        return f"<AnalystOverride {self.override_type} analyst={self.analyst_id}>"
