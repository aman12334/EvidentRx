from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class ReasoningTrace(Base):
    """
    Append-only log of every LLM reasoning step — the auditability backbone
    for all AI-assisted investigation decisions.

    Design constraints:
    - No updated_at column — records are immutable after creation.
    - parent_trace_id supports hierarchical reasoning chains:
        orchestrator → investigator → validator patterns.
    - workflow_node maps to a LangGraph node name for per-node analytics.
    - cache_read_tokens / cache_write_tokens enable cost attribution per case.
    - human_review_required triggers human-in-the-loop escalation workflows.

    This table must NEVER be written to by the rules engine — it is exclusively
    for LLM-generated reasoning. The rules engine writes to audit.audit_findings.
    """

    __tablename__ = "reasoning_traces"
    __table_args__ = {"schema": "audit"}

    trace_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )

    # Session grouping
    session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False,
        comment="Groups all traces from a single agent workflow run",
    )
    investigation_case_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("audit.investigation_cases.case_id"),
    )
    finding_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("audit.audit_findings.finding_id"),
    )

    # Agent hierarchy
    parent_trace_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("audit.reasoning_traces.trace_id"),
        comment="Supports orchestrator → sub-agent hierarchical chains",
    )
    agent_id: Mapped[str | None] = mapped_column(String(100))
    agent_type: Mapped[str | None] = mapped_column(String(50))
    workflow_node: Mapped[str | None] = mapped_column(
        String(100), comment="LangGraph node name"
    )
    workflow_step_sequence: Mapped[int | None] = mapped_column(
        Integer, comment="Execution order within a graph run"
    )

    # Model provenance
    model_id: Mapped[str | None] = mapped_column(
        String(100), comment="e.g. claude-opus-4-7"
    )
    prompt_template_id: Mapped[str | None] = mapped_column(String(100))
    prompt_template_version: Mapped[str | None] = mapped_column(String(20))

    # Input / Output (immutable)
    input_context: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    reasoning_output: Mapped[str | None] = mapped_column(Text)
    structured_output: Mapped[dict | None] = mapped_column(JSONB)
    citations: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    # Quality signals
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    human_review_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    human_review_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    human_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    human_reviewer: Mapped[str | None] = mapped_column(String(255))
    human_review_notes: Mapped[str | None] = mapped_column(Text)

    # Performance telemetry
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_read_tokens: Mapped[int | None] = mapped_column(
        Integer, comment="Anthropic prompt cache hit tokens"
    )
    cache_write_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)

    # Append-only — no updated_at
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    parent_trace: Mapped["ReasoningTrace | None"] = relationship(
        "ReasoningTrace",
        remote_side="ReasoningTrace.trace_id",
        foreign_keys=[parent_trace_id],
    )

    def __repr__(self) -> str:
        return (
            f"<ReasoningTrace id={self.trace_id} agent={self.agent_type} "
            f"node={self.workflow_node} session={self.session_id}>"
        )
