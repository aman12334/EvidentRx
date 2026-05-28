from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, func, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ComplianceTrend(Base):
    __tablename__ = "compliance_trends"
    __table_args__ = {"schema": "audit"}

    trend_id:           Mapped[UUID]            = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    entity_id:          Mapped[UUID]            = mapped_column(PGUUID(as_uuid=True), nullable=False)
    entity_type:        Mapped[str]             = mapped_column(String(50), nullable=False)
    rule_code:          Mapped[str | None]   = mapped_column(String(20))
    window_type:        Mapped[str]             = mapped_column(String(20), nullable=False)
    window_start:       Mapped[date]            = mapped_column(Date(), nullable=False)
    window_end:         Mapped[date]            = mapped_column(Date(), nullable=False)
    finding_count:      Mapped[int]             = mapped_column(Integer(), nullable=False, default=0)
    critical_count:     Mapped[int]             = mapped_column(Integer(), nullable=False, default=0)
    high_count:         Mapped[int]             = mapped_column(Integer(), nullable=False, default=0)
    financial_exposure: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    risk_score:         Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    trend_direction:    Mapped[str | None]   = mapped_column(String(20))
    velocity:           Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    acceleration:       Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    prior_period_count: Mapped[int | None]   = mapped_column(Integer())
    computed_at:        Mapped[datetime]        = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    monitoring_run_id:  Mapped[UUID | None]  = mapped_column(PGUUID(as_uuid=True), ForeignKey("audit.monitoring_runs.run_id"))

    def __repr__(self) -> str:
        return f"<ComplianceTrend {self.entity_type}/{self.entity_id} rule={self.rule_code} dir={self.trend_direction}>"
