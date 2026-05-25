from datetime import date
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Date, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import AuditMixin, Base, IngestionMixin, TemporalMixin

if TYPE_CHECKING:
    from app.models.reference.covered_entity import CoveredEntity


class ContractPharmacy(Base, AuditMixin, TemporalMixin, IngestionMixin):
    """
    340B contract pharmacy registration.
    SCD Type 2 — partial unique index on (pharmacy_npi, hrsa_id WHERE is_current)
    is defined in DDL.
    """

    __tablename__ = "contract_pharmacies"
    __table_args__ = {"schema": "ref"}

    cp_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )

    # Relationships
    covered_entity_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("ref.covered_entities.ce_id"),
        nullable=False,
    )
    hrsa_id: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True,
        comment="Denormalized CE HRSA ID — avoids traversing SCD history on joins",
    )

    # Pharmacy identity
    pharmacy_name: Mapped[str] = mapped_column(Text, nullable=False)
    pharmacy_npi: Mapped[str | None] = mapped_column(String(10))
    pharmacy_ncpdp: Mapped[str | None] = mapped_column(String(7))
    chain_name: Mapped[str | None] = mapped_column(Text)
    pharmacy_type: Mapped[str | None] = mapped_column(String(50))

    # Location
    street_address: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(100))
    state_code: Mapped[str | None] = mapped_column(String(2))
    zip_code: Mapped[str | None] = mapped_column(String(10))

    # Registration lifecycle
    registration_date: Mapped[date | None] = mapped_column(Date)
    termination_date: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    # Relationships
    covered_entity: Mapped["CoveredEntity"] = relationship(
        "CoveredEntity",
        foreign_keys=[covered_entity_id],
        back_populates="contract_pharmacies",
    )

    def __repr__(self) -> str:
        return f"<ContractPharmacy npi={self.pharmacy_npi} name={self.pharmacy_name!r} ce={self.hrsa_id}>"
