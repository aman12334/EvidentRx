from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, Numeric, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class IntelligenceGraphEdge(Base):
    __tablename__ = "intelligence_graph_edges"
    __table_args__ = {"schema": "audit"}

    edge_id:      Mapped[UUID]           = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    source_type:  Mapped[str]            = mapped_column(String(50),  nullable=False)
    source_id:    Mapped[str]            = mapped_column(String(255), nullable=False)
    target_type:  Mapped[str]            = mapped_column(String(50),  nullable=False)
    target_id:    Mapped[str]            = mapped_column(String(255), nullable=False)
    relationship: Mapped[str]            = mapped_column(String(50),  nullable=False)
    weight:       Mapped[Decimal]        = mapped_column(Numeric(8, 4), nullable=False, default=Decimal("1.0"))
    properties:   Mapped[dict]           = mapped_column(JSONB(), nullable=False, server_default=text("'{}'::jsonb"))
    valid_from:   Mapped[date]           = mapped_column(Date(), nullable=False)
    valid_to:     Mapped[date | None] = mapped_column(Date())
    created_at:   Mapped[datetime]       = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    def __repr__(self) -> str:
        return f"<GraphEdge {self.source_type}/{self.source_id} -{self.relationship}-> {self.target_type}/{self.target_id}>"
