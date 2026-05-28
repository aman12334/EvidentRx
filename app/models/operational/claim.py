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


class Claim(Base):
    """
    Insurance / Medicaid reimbursement claim.

    dispense_id is a logical FK — not enforced by DB because ops.dispenses is
    partitioned. Always supply dispense_date alongside dispense_id for efficient
    partition-aware lookups.

    Table is range-partitioned on service_date — composite PK required.
    """

    __tablename__ = "claims"
    __table_args__ = {"schema": "ops"}

    # Composite PK
    claim_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    service_date: Mapped[date] = mapped_column(Date, primary_key=True, nullable=False)

    # Source identity
    external_id: Mapped[str | None] = mapped_column(String(255))

    # Entity linkage
    covered_entity_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("ref.covered_entities.ce_id"),
        nullable=False,
    )

    # Logical link to ops.dispenses (not DB-enforced)
    dispense_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    dispense_date: Mapped[date | None] = mapped_column(
        Date, comment="Partition key of the linked dispense — needed for cross-partition lookup"
    )

    # Claim attributes
    claim_type: Mapped[str] = mapped_column(String(30), nullable=False)
    claim_status: Mapped[str] = mapped_column(String(20), nullable=False, default="submitted")
    payer_id: Mapped[str | None] = mapped_column(String(50))
    payer_name: Mapped[str | None] = mapped_column(Text)
    plan_id: Mapped[str | None] = mapped_column(String(50))

    # Patient (hashed)
    patient_id_hash: Mapped[str | None] = mapped_column(String(64))

    # Provider
    prescriber_npi: Mapped[str | None] = mapped_column(String(10))
    dispenser_npi: Mapped[str | None] = mapped_column(String(10))

    # Drug
    rx_number: Mapped[str | None] = mapped_column(String(50))
    ndc_11: Mapped[str] = mapped_column(String(11), nullable=False)
    drug_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("ref.ndc_drugs.drug_id")
    )

    # Dates
    billing_date: Mapped[date | None] = mapped_column(Date)
    paid_date: Mapped[date | None] = mapped_column(Date)

    # Amounts
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(15, 4))
    days_supply: Mapped[int | None] = mapped_column(SmallInteger)
    billed_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    allowed_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    paid_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    patient_pay_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))

    # Flags
    state_code: Mapped[str | None] = mapped_column(String(2))
    is_medicaid: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    is_340b_billed: Mapped[bool | None] = mapped_column(Boolean)
    billing_modifier: Mapped[str | None] = mapped_column(
        String(10), comment="UD modifier signals 340B drug to payer"
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
            f"<Claim id={self.claim_id} date={self.service_date} "
            f"type={self.claim_type} medicaid={self.is_medicaid}>"
        )
