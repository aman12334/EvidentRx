from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SplitBilling(Base):
    """
    Core unit of 340B compliance analysis — links a purchase, dispense, and
    claim for a single patient encounter.

    Risk flags (duplicate_discount_risk, etc.) are set by the deterministic
    rules engine on ingestion, NOT by AI. The AI layer reads these flags and
    writes to audit.reasoning_traces.

    All links to partitioned tables (purchases, dispenses, claims) are logical —
    they store both the record UUID and the partition key date column.
    """

    __tablename__ = "split_billing"
    __table_args__ = {"schema": "ops"}

    split_billing_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )

    # Entity & drug
    covered_entity_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("ref.covered_entities.ce_id"),
        nullable=False,
    )
    ndc_11: Mapped[str] = mapped_column(String(11), nullable=False)
    service_date: Mapped[date] = mapped_column(Date, nullable=False)
    patient_id_hash: Mapped[str | None] = mapped_column(String(64))

    # Logical links to partitioned parents
    purchase_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    purchase_date: Mapped[date | None] = mapped_column(Date)
    dispense_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    dispense_date: Mapped[date | None] = mapped_column(Date)
    claim_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    claim_service_date: Mapped[date | None] = mapped_column(Date)

    # Split billing attributes
    split_method: Mapped[str | None] = mapped_column(String(50))
    carve_in_flag: Mapped[bool | None] = mapped_column(Boolean)
    is_340b_purchase: Mapped[bool | None] = mapped_column(Boolean)
    is_medicaid_billed: Mapped[bool | None] = mapped_column(Boolean)
    accumulator_balance: Mapped[Decimal | None] = mapped_column(
        Numeric(15, 4),
        comment="340B inventory accumulator balance at dispense time",
    )

    # Pre-computed risk signals — set by rules engine, read by AI layer
    duplicate_discount_risk: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    medicaid_overlap_risk: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    carve_out_violation_risk: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    ineligible_patient_risk: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    risk_score: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 4),
        comment="Composite risk score 0-1 computed deterministically by rules engine",
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
            f"<SplitBilling id={self.split_billing_id} date={self.service_date} "
            f"risk={self.risk_score} dd={self.duplicate_discount_risk}>"
        )
