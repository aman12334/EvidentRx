from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, Integer, Numeric, String, func, text
from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CaseRiskSnapshot(Base):
    """
    Immutable point-in-time risk aggregation for an investigation case.

    A new snapshot is created on:
      - case_created   : initial state at case creation
      - finding_added  : when a new finding is linked to the case
      - status_changed : when case status transitions
      - manual         : on-demand refresh by a user or agent
      - scheduled      : periodic batch aggregation

    Snapshots are never updated — each trigger creates a new row.
    The most recent snapshot (snapshot_at DESC) is the current view.

    findings_by_rule : {"DD-001": 12, "DD-002": 5, ...}
    ndc_list         : ["00002015201", "00074321402"]
    """

    __tablename__ = "case_risk_snapshots"
    __table_args__ = {"schema": "audit"}

    snapshot_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    case_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("audit.investigation_cases.case_id"),
        nullable=False,
    )
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    snapshot_trigger: Mapped[str] = mapped_column(
        String(50), nullable=False, default="manual"
    )

    # Severity breakdown
    total_findings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    critical_findings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    high_findings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    medium_findings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    low_findings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Financial
    total_financial_exposure: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    composite_risk_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))

    # Dimensional breakdowns (JSONB for flexibility)
    findings_by_rule: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    ndc_list: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    # Temporal coverage of findings in this case
    temporal_window_start: Mapped[date | None] = mapped_column(Date)
    temporal_window_end: Mapped[date | None] = mapped_column(Date)

    # Entity counts
    unique_patients: Mapped[int | None] = mapped_column(Integer)
    unique_pharmacies: Mapped[int | None] = mapped_column(Integer)

    def __repr__(self) -> str:
        return (
            f"<CaseRiskSnapshot case={self.case_id} "
            f"findings={self.total_findings} trigger={self.snapshot_trigger}>"
        )
