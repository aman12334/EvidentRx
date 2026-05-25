from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CopilotSession(Base):
    """
    Read-only investigator copilot assistance session.
    Copilot never modifies case data — it only reads and explains.
    """
    __tablename__ = "copilot_sessions"
    __table_args__ = {"schema": "audit"}

    session_id:      Mapped[UUID]           = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    case_id:         Mapped[UUID]           = mapped_column(PGUUID(as_uuid=True), ForeignKey("audit.investigation_cases.case_id"), nullable=False)
    investigator_id: Mapped[Optional[str]]  = mapped_column(String(255))
    session_type:    Mapped[str]            = mapped_column(String(50), nullable=False)
    input_context:   Mapped[dict]           = mapped_column(JSONB(), nullable=False, server_default=text("'{}'::jsonb"))
    output:          Mapped[dict]           = mapped_column(JSONB(), nullable=False, server_default=text("'{}'::jsonb"))
    model_id:        Mapped[Optional[str]]  = mapped_column(String(100))
    input_tokens:    Mapped[int]            = mapped_column(Integer(), nullable=False, default=0)
    output_tokens:   Mapped[int]            = mapped_column(Integer(), nullable=False, default=0)
    cache_tokens:    Mapped[int]            = mapped_column(Integer(), nullable=False, default=0)
    latency_ms:      Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    confidence_score:Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4))
    created_at:      Mapped[datetime]       = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    def __repr__(self) -> str:
        return f"<CopilotSession {self.session_id} type={self.session_type} case={self.case_id}>"
