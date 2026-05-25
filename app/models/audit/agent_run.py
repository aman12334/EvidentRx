from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AgentRun(Base):
    """
    One row per agent invocation — the execution ledger for all future
    LangGraph / multi-agent workflows.

    Designed to be populated by the orchestration layer (Phase 5).
    The Phase 4 infrastructure creates this table and its indexes so
    LangGraph agents can write execution records without schema changes.

    agent_type examples : investigator, summarizer, escalation_advisor,
                          evidence_reviewer, document_drafter
    status              : pending → running → completed | failed | cancelled
    workflow_run_id     : LangGraph correlation ID (external)
    token_usage         : {input, output, cache_read, cache_write, total_cost_usd}
    """

    __tablename__ = "agent_runs"
    __table_args__ = {"schema": "audit"}

    agent_run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    case_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("audit.investigation_cases.case_id"),
        nullable=False,
    )
    agent_type: Mapped[str] = mapped_column(String(100), nullable=False)
    agent_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending"
    )

    # Input / Output
    input_payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    output_payload: Mapped[dict | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)

    # Execution window
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Model provenance
    model_id: Mapped[str | None] = mapped_column(
        String(100), comment="e.g. claude-opus-4-7"
    )
    token_usage: Mapped[dict | None] = mapped_column(
        JSONB, comment="{input, output, cache_read, cache_write, total_cost_usd}"
    )

    # Orchestration correlation
    workflow_run_id: Mapped[str | None] = mapped_column(
        String(255), comment="LangGraph run ID for correlation"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<AgentRun id={self.agent_run_id} type={self.agent_type} status={self.status}>"
