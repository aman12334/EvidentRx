from datetime import date
from typing import List
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Date, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import AuditMixin, Base, IngestionMixin, TemporalMixin


class Provider(Base, AuditMixin, TemporalMixin, IngestionMixin):
    """
    NPPES provider registry record.
    SCD Type 2 from weekly NPPES dissemination files.
    Taxonomy codes are in the child ProviderTaxonomy table.
    """

    __tablename__ = "providers"
    __table_args__ = {"schema": "ref"}

    provider_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )

    # NPI identity
    npi: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    entity_type_code: Mapped[str] = mapped_column(
        String(1), nullable=False,
        comment="1=Individual, 2=Organization",
    )

    # Individual name
    provider_last_name: Mapped[str | None] = mapped_column(String(100))
    provider_first_name: Mapped[str | None] = mapped_column(String(100))
    provider_middle_name: Mapped[str | None] = mapped_column(String(100))
    provider_credential: Mapped[str | None] = mapped_column(String(50))

    # Organization
    organization_name: Mapped[str | None] = mapped_column(Text)
    doing_business_as: Mapped[str | None] = mapped_column(Text)

    # Practice location
    street_address: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(100))
    state_code: Mapped[str | None] = mapped_column(String(2))
    zip_code: Mapped[str | None] = mapped_column(String(10))
    phone: Mapped[str | None] = mapped_column(String(20))

    # NPPES status
    enumeration_date: Mapped[date | None] = mapped_column(Date)
    last_update_date: Mapped[date | None] = mapped_column(Date)
    deactivation_date: Mapped[date | None] = mapped_column(Date)
    deactivation_reason: Mapped[str | None] = mapped_column(String(2))
    reactivation_date: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    # Source week from NPPES filename (e.g. '051126_051726')
    source_week: Mapped[str | None] = mapped_column(String(20))

    # Relationships
    taxonomies: Mapped[List["ProviderTaxonomy"]] = relationship(
        "ProviderTaxonomy",
        back_populates="provider",
        cascade="all, delete-orphan",
        lazy="select",
    )

    def __repr__(self) -> str:
        name = self.organization_name or f"{self.provider_last_name}, {self.provider_first_name}"
        return f"<Provider npi={self.npi} name={name!r} current={self.is_current}>"


class ProviderTaxonomy(Base):
    """NPPES taxonomy codes — up to 15 per provider, stored in child table."""

    __tablename__ = "provider_taxonomies"
    __table_args__ = {"schema": "ref"}

    taxonomy_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    provider_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("ref.providers.provider_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    taxonomy_code: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    taxonomy_description: Mapped[str | None] = mapped_column(Text)
    license_number: Mapped[str | None] = mapped_column(String(50))
    license_state: Mapped[str | None] = mapped_column(String(2))
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    # Relationships
    provider: Mapped["Provider"] = relationship("Provider", back_populates="taxonomies")

    def __repr__(self) -> str:
        return f"<ProviderTaxonomy code={self.taxonomy_code} primary={self.is_primary}>"
