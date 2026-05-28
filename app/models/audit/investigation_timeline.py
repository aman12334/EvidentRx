from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class InvestigationTimeline(Base):
    """
    Append-only event log for an investigation case.

    Design constraints:
    - No updated_at column — rows are immutable after insertion.
    - sequence_number is a BIGSERIAL managed by the DB for total ordering.
      It is not mapped here; use occurred_at + event_id for ORM ordering.
    - actor_type distinguishes system (rules_engine, case_builder),
      human (analyst, reviewer), and agent (future LangGraph nodes).

    Valid event_types:
        CASE_CREATED, FINDING_ADDED, FINDING_REMOVED,
        STATUS_CHANGED, PRIORITY_CHANGED, ASSIGNMENT_CHANGED,
        ESCALATED, SNAPSHOT_TAKEN,
        AGENT_TRIGGERED, AGENT_COMPLETED, AGENT_FAILED,
        CHECKPOINT_SAVED, HUMAN_ACTION, NOTE_ADDED
    """

    __tablename__ = "investigation_timelines"
    __table_args__ = {"schema": "audit"}

    event_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    case_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("audit.investigation_cases.case_id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    event_data: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    actor_id: Mapped[str | None] = mapped_column(String(255))
    actor_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="system"
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<InvestigationTimeline case={self.case_id} type={self.event_type} at={self.occurred_at}>"
