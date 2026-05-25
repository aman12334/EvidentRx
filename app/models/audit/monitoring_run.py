from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MonitoringRun(Base):
    __tablename__ = "monitoring_runs"
    __table_args__ = {"schema": "audit"}

    run_id:             Mapped[UUID]     = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    run_type:           Mapped[str]      = mapped_column(String(50),  nullable=False)
    window_type:        Mapped[str]      = mapped_column(String(20),  nullable=False)
    window_start:       Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end:         Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status:             Mapped[str]      = mapped_column(String(20),  nullable=False, default="running")
    findings_evaluated: Mapped[int]      = mapped_column(Integer(),   nullable=False, default=0)
    new_findings:       Mapped[int]      = mapped_column(Integer(),   nullable=False, default=0)
    drifts_detected:    Mapped[int]      = mapped_column(Integer(),   nullable=False, default=0)
    correlations_found: Mapped[int]      = mapped_column(Integer(),   nullable=False, default=0)
    run_metadata:       Mapped[dict]     = mapped_column(JSONB(),     nullable=False, server_default=text("'{}'::jsonb"))
    error_message:      Mapped[Optional[str]] = mapped_column(Text())
    started_at:         Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at:       Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<MonitoringRun {self.run_id} type={self.run_type} status={self.status}>"
