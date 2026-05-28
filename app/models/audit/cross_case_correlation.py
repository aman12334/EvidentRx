from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CrossCaseCorrelation(Base):
    __tablename__ = "cross_case_correlations"
    __table_args__ = {"schema": "audit"}

    correlation_id:    Mapped[UUID]           = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    case_id_a:         Mapped[UUID]           = mapped_column(PGUUID(as_uuid=True), ForeignKey("audit.investigation_cases.case_id"), nullable=False)
    case_id_b:         Mapped[UUID]           = mapped_column(PGUUID(as_uuid=True), ForeignKey("audit.investigation_cases.case_id"), nullable=False)
    correlation_type:  Mapped[str]            = mapped_column(String(50), nullable=False)
    strength:          Mapped[Decimal]        = mapped_column(Numeric(5, 4), nullable=False)
    shared_entities:   Mapped[dict]           = mapped_column(JSONB(), nullable=False, server_default=text("'{}'::jsonb"))
    explanation:       Mapped[str | None]  = mapped_column(Text())
    detected_at:       Mapped[datetime]       = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    monitoring_run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("audit.monitoring_runs.run_id"))

    def __repr__(self) -> str:
        return f"<CrossCaseCorrelation {self.case_id_a}↔{self.case_id_b} type={self.correlation_type} strength={self.strength}>"
