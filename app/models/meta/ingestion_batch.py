from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class IngestionBatch(Base):
    """
    Tracks every data ingestion job — primary lineage anchor for all imported records.
    Every reference and operational row carries a batch_id pointing here.
    """

    __tablename__ = "ingestion_batches"
    __table_args__ = {"schema": "meta"}

    batch_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    batch_name: Mapped[str | None] = mapped_column(String(255))
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_file: Mapped[str] = mapped_column(Text, nullable=False)
    source_file_hash: Mapped[str | None] = mapped_column(String(64))
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    record_count: Mapped[int | None] = mapped_column(Integer)
    records_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    error_details: Mapped[dict | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<IngestionBatch {self.batch_id} source={self.source_type} status={self.status}>"
