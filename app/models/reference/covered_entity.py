from datetime import date
from typing import TYPE_CHECKING, List
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Date, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import AuditMixin, Base, IngestionMixin, TemporalMixin

if TYPE_CHECKING:
    from app.models.reference.contract_pharmacy import ContractPharmacy
    from app.models.reference.medicaid_exclusion import MedicaidExclusion


class CoveredEntity(Base, AuditMixin, TemporalMixin, IngestionMixin):
    """
    HRSA-registered 340B covered entity.
    SCD Type 2 — use is_current=True rows for current state.
    Partial unique index on (hrsa_id WHERE is_current) is defined in DDL.
    """

    __tablename__ = "covered_entities"
    __table_args__ = {"schema": "ref"}

    ce_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )

    # HRSA identity
    hrsa_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    entity_name: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type_code: Mapped[str | None] = mapped_column(String(20))
    entity_type_description: Mapped[str | None] = mapped_column(Text)

    # Location
    street_address: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(100))
    state_code: Mapped[str | None] = mapped_column(String(2))
    zip_code: Mapped[str | None] = mapped_column(String(10))
    county: Mapped[str | None] = mapped_column(String(100))

    # Provider identifiers
    npi: Mapped[str | None] = mapped_column(String(10))
    primary_340b_program: Mapped[str | None] = mapped_column(String(50))
    outpatient_facility_name: Mapped[str | None] = mapped_column(Text)
    parent_site_name: Mapped[str | None] = mapped_column(Text)
    grantee_number: Mapped[str | None] = mapped_column(String(50))

    # Program dates and status
    program_participation_start: Mapped[date | None] = mapped_column(Date)
    program_termination_date: Mapped[date | None] = mapped_column(Date)
    program_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Active"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    # Relationships
    contract_pharmacies: Mapped[List["ContractPharmacy"]] = relationship(
        "ContractPharmacy",
        foreign_keys="[ContractPharmacy.covered_entity_id]",
        back_populates="covered_entity",
        lazy="select",
    )
    medicaid_exclusions: Mapped[List["MedicaidExclusion"]] = relationship(
        "MedicaidExclusion",
        foreign_keys="[MedicaidExclusion.covered_entity_id]",
        back_populates="covered_entity",
        lazy="select",
    )

    def __repr__(self) -> str:
        return f"<CoveredEntity hrsa_id={self.hrsa_id} name={self.entity_name!r} current={self.is_current}>"
