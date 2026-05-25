from datetime import date, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Date, ForeignKey, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IngestionMixin

if TYPE_CHECKING:
    from app.models.reference.covered_entity import CoveredEntity


class MedicaidExclusion(Base, IngestionMixin):
    """
    Covered entity Medicaid carve-in / carve-out election for a given filing period.
    Sourced from HRSA quarterly Medicaid exclusion files.
    One row per CE per quarter per state.
    """

    __tablename__ = "medicaid_exclusions"
    __table_args__ = {"schema": "ref"}

    exclusion_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )

    # Entity linkage
    covered_entity_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("ref.covered_entities.ce_id"),
        nullable=True,
    )
    hrsa_id: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True,
        comment="Denormalized — CE may not exist in covered_entities when file is loaded",
    )

    # Exclusion attributes
    state_code: Mapped[str] = mapped_column(String(2), nullable=False)
    exclusion_type: Mapped[str] = mapped_column(
        String(20), nullable=False,
        comment="carve_in | carve_out | not_elected",
    )
    carve_type_detail: Mapped[str | None] = mapped_column(Text)

    # Temporal period
    filing_period: Mapped[str] = mapped_column(
        String(10), nullable=False,
        comment="Quarter identifier, e.g. 2025Q4",
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date | None] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )

    # Relationships
    covered_entity: Mapped["CoveredEntity | None"] = relationship(
        "CoveredEntity",
        foreign_keys=[covered_entity_id],
        back_populates="medicaid_exclusions",
    )

    def __repr__(self) -> str:
        return (
            f"<MedicaidExclusion hrsa_id={self.hrsa_id} "
            f"period={self.filing_period} type={self.exclusion_type}>"
        )
