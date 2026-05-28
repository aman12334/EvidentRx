from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Dispense(Base):
    """
    Drug dispense at a covered entity or contract pharmacy.

    patient_id_hash MUST be a one-way hash of the source patient identifier.
    Raw PII must never be stored. Use SHA-256(patient_id + salt) at ingestion time.

    Table is range-partitioned on dispense_date — composite PK required.
    """

    __tablename__ = "dispenses"
    __table_args__ = {"schema": "ops"}

    # Composite PK
    dispense_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    dispense_date: Mapped[date] = mapped_column(Date, primary_key=True, nullable=False)

    # Source identity
    external_id: Mapped[str | None] = mapped_column(String(255))

    # Entity & pharmacy linkage
    covered_entity_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("ref.covered_entities.ce_id"),
        nullable=False,
    )
    contract_pharmacy_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("ref.contract_pharmacies.cp_id"),
    )

    # Drug
    ndc_11: Mapped[str] = mapped_column(String(11), nullable=False)
    drug_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("ref.ndc_drugs.drug_id")
    )

    # Privacy-preserving patient identifier
    patient_id_hash: Mapped[str] = mapped_column(
        String(64), nullable=False,
        comment="SHA-256 of source patient identifier — raw PII must never be stored",
    )

    # Provider
    prescriber_npi: Mapped[str | None] = mapped_column(String(10))
    prescriber_provider_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("ref.providers.provider_id")
    )
    dispenser_npi: Mapped[str | None] = mapped_column(String(10))
    dispenser_provider_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("ref.providers.provider_id")
    )

    # Prescription
    rx_number: Mapped[str | None] = mapped_column(String(50))
    fill_number: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    written_date: Mapped[date | None] = mapped_column(Date)
    days_supply: Mapped[int | None] = mapped_column(SmallInteger)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(15, 4))
    unit_of_measure: Mapped[str | None] = mapped_column(String(20))
    dispense_as_written: Mapped[bool | None] = mapped_column(Boolean)

    # Payer
    payer_type: Mapped[str | None] = mapped_column(String(30))
    payer_id: Mapped[str | None] = mapped_column(String(50))
    payer_name: Mapped[str | None] = mapped_column(Text)

    # 340B status
    is_340b_dispense: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    carve_in_election: Mapped[str | None] = mapped_column(
        String(20),
        comment="CE carve-in/out election at dispense date — critical for compliance checks",
    )

    # Ingestion lineage
    source_file: Mapped[str | None] = mapped_column(Text)
    batch_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<Dispense id={self.dispense_id} date={self.dispense_date} "
            f"ndc={self.ndc_11} payer={self.payer_type}>"
        )
