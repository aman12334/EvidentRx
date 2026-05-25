from datetime import date
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Date, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import AuditMixin, Base, IngestionMixin


class NdcDrug(Base, AuditMixin, IngestionMixin):
    """
    FDA NDC drug directory entry.
    ndc_11 is the canonical 11-digit zero-padded NDC (5-4-2, no hyphens)
    used as the join key across all operational tables.
    """

    __tablename__ = "ndc_drugs"
    __table_args__ = {"schema": "ref"}

    drug_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )

    # NDC identifiers
    ndc_11: Mapped[str] = mapped_column(
        String(11), nullable=False, unique=True,
        comment="Canonical 11-digit zero-padded NDC, 5-4-2 format without hyphens",
    )
    ndc_raw: Mapped[str | None] = mapped_column(String(20))
    application_number: Mapped[str | None] = mapped_column(String(20))
    product_ndc: Mapped[str | None] = mapped_column(String(12))
    package_ndc: Mapped[str | None] = mapped_column(String(12))
    labeler_code: Mapped[str | None] = mapped_column(String(5))
    product_code: Mapped[str | None] = mapped_column(String(4))
    package_code: Mapped[str | None] = mapped_column(String(2))

    # Drug identity
    proprietary_name: Mapped[str | None] = mapped_column(Text)
    proprietary_name_suffix: Mapped[str | None] = mapped_column(Text)
    nonproprietary_name: Mapped[str | None] = mapped_column(Text)
    labeler_name: Mapped[str | None] = mapped_column(Text)
    substance_name: Mapped[str | None] = mapped_column(Text)
    strength: Mapped[str | None] = mapped_column(Text)
    dosage_form: Mapped[str | None] = mapped_column(String(100))
    route: Mapped[str | None] = mapped_column(Text)

    # Classification
    marketing_category: Mapped[str | None] = mapped_column(String(100))
    application_type: Mapped[str | None] = mapped_column(String(50))
    product_type: Mapped[str | None] = mapped_column(String(50))
    dea_schedule: Mapped[str | None] = mapped_column(String(10))

    # Lifecycle
    listing_expiration_date: Mapped[date | None] = mapped_column(Date)
    marketing_start_date: Mapped[date | None] = mapped_column(Date)
    marketing_end_date: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    def __repr__(self) -> str:
        return f"<NdcDrug ndc_11={self.ndc_11} name={self.nonproprietary_name!r}>"
