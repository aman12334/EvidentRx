from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, Numeric, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class EntityRiskScore(Base):
    __tablename__ = "entity_risk_scores"
    __table_args__ = {"schema": "audit"}

    score_id:               Mapped[UUID]            = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    entity_id:              Mapped[UUID]            = mapped_column(PGUUID(as_uuid=True), nullable=False)
    entity_type:            Mapped[str]             = mapped_column(String(50), nullable=False)
    score_date:             Mapped[date]            = mapped_column(Date(), nullable=False)
    composite_score:        Mapped[Decimal]         = mapped_column(Numeric(5, 4), nullable=False)
    finding_velocity:       Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))
    exposure_trajectory:    Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    escalation_probability: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4))
    trend_direction:        Mapped[Optional[str]]   = mapped_column(String(20))
    score_components:       Mapped[dict]            = mapped_column(JSONB(), nullable=False, server_default=text("'{}'::jsonb"))
    computed_at:            Mapped[datetime]        = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    def __repr__(self) -> str:
        return f"<EntityRiskScore {self.entity_type}/{self.entity_id} date={self.score_date} score={self.composite_score}>"
