from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = {"schema": "audit"}

    eval_id:       Mapped[UUID]           = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    eval_type:     Mapped[str]            = mapped_column(String(50),  nullable=False)
    eval_name:     Mapped[str | None]  = mapped_column(String(255))
    status:        Mapped[str]            = mapped_column(String(20),  nullable=False, default="running")
    passed:        Mapped[bool | None] = mapped_column(Boolean())
    total_checks:  Mapped[int]            = mapped_column(Integer(),   nullable=False, default=0)
    failed_checks: Mapped[int]            = mapped_column(Integer(),   nullable=False, default=0)
    eval_metadata: Mapped[dict]           = mapped_column(JSONB(), nullable=False, server_default=text("'{}'::jsonb"))
    started_at:    Mapped[datetime]       = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at:  Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<EvaluationRun {self.eval_type} status={self.status} passed={self.passed}>"
