from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Purchase(Base):
    """
    340B drug purchase from a wholesaler.

    Table is range-partitioned on purchase_date in the DB — the composite PK
    (purchase_id, purchase_date) is required by PostgreSQL partitioning.

    WARNING: Do not navigate SQLAlchemy relationships *from* this model — the
    parent table is partitioned and cross-partition FK enforcement is disabled.
    Use explicit queries with both purchase_id and purchase_date.
    """

    __tablename__ = "purchases"
    __table_args__ = {"schema": "ops"}

    # Composite PK — required by range partitioning
    purchase_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    purchase_date: Mapped[date] = mapped_column(Date, primary_key=True, nullable=False)

    # Source identity
    external_id: Mapped[str | None] = mapped_column(String(255))

    # Entity & drug linkage
    covered_entity_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("ref.covered_entities.ce_id"),
        nullable=False,
    )
    contract_pharmacy_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("ref.contract_pharmacies.cp_id"),
    )
    ndc_11: Mapped[str] = mapped_column(String(11), nullable=False)
    drug_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("ref.ndc_drugs.drug_id"),
    )

    # Wholesaler
    wholesaler_name: Mapped[str | None] = mapped_column(Text)
    wholesaler_dea: Mapped[str | None] = mapped_column(String(20))
    invoice_number: Mapped[str | None] = mapped_column(String(100))
    lot_number: Mapped[str | None] = mapped_column(String(100))

    # Quantity & pricing
    quantity: Mapped[Decimal] = mapped_column(Numeric(15, 4), nullable=False)
    unit_of_measure: Mapped[str | None] = mapped_column(String(20))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(15, 6))
    total_cost: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    purchase_price_type: Mapped[str | None] = mapped_column(String(20))
    is_340b_purchase: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    ceiling_price: Mapped[Decimal | None] = mapped_column(
        Numeric(15, 6),
        comment="340B ceiling price snapshot at purchase time",
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
            f"<Purchase id={self.purchase_id} date={self.purchase_date} "
            f"ndc={self.ndc_11} 340b={self.is_340b_purchase}>"
        )
