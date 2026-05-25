from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, String, func, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """SQLAlchemy 2.0 declarative base — all models inherit from this."""
    pass


class AuditMixin:
    """Standard audit trail columns present on most tables."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class TemporalMixin:
    """SCD Type 2 temporal validity columns for slowly-changing reference data."""

    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    valid_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    is_current: Mapped[bool] = mapped_column(
        nullable=False,
        default=True,
        server_default=text("true"),
    )


class IngestionMixin:
    """Data ingestion lineage columns shared across reference and operational tables."""

    source_file: Mapped[str | None] = mapped_column(String, nullable=True)
    source_file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    batch_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
