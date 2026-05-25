from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WorkflowCheckpoint(Base):
    """
    Serialized LangGraph workflow state enabling agent pause and resume.

    When a LangGraph graph reaches a human-in-the-loop interrupt node, it
    serializes the full graph state into state_data and sets is_resumable=True.
    The orchestration layer queries for resumable checkpoints per case to
    continue paused workflows.

    checkpoint_name maps to a LangGraph node name or a human-readable
    pause point (e.g. 'awaiting_human_review', 'evidence_collected').

    Only one checkpoint per (case_id, workflow_name) should be is_resumable=True
    at a time — enforced at the application layer, not DB level (the workflow
    manager marks the previous checkpoint as is_resumable=False on resume).
    """

    __tablename__ = "workflow_checkpoints"
    __table_args__ = {"schema": "audit"}

    checkpoint_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    case_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("audit.investigation_cases.case_id"),
        nullable=False,
    )
    agent_run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("audit.agent_runs.agent_run_id"),
    )
    workflow_name: Mapped[str] = mapped_column(String(255), nullable=False)
    checkpoint_name: Mapped[str] = mapped_column(String(255), nullable=False)
    state_data: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"),
        comment="Full serialized LangGraph graph state",
    )
    is_resumable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<WorkflowCheckpoint case={self.case_id} "
            f"workflow={self.workflow_name} node={self.checkpoint_name} resumable={self.is_resumable}>"
        )
