"""
CaseMemory — DB-backed, case-scoped context that persists across runs.

When an investigation is resumed (e.g. after human review), the new run
reads CaseMemory to understand what prior agent runs already determined.
This prevents redundant LLM calls and preserves investigation continuity.

Built by reading audit.agent_runs and audit.reasoning_traces — the existing
audit trail becomes the memory source.
"""
from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class CaseMemory:
    """
    Loads and provides structured prior-run context for a case.
    Injected into agent prompts to avoid re-investigating known facts.
    """

    def __init__(self, case_id: UUID, session: Session) -> None:
        self._case_id = case_id
        self._prior_runs = self._load_prior_runs(session)
        self._prior_outputs = self._load_prior_outputs(session)

    def has_prior_analysis(self, agent_type: str) -> bool:
        return agent_type in self._prior_outputs

    def get_prior_output(self, agent_type: str) -> Optional[dict]:
        return self._prior_outputs.get(agent_type)

    def prior_run_count(self) -> int:
        return len(self._prior_runs)

    def to_prompt_context(self) -> str:
        """
        Returns a concise text block for injection into system prompts.
        Informs the agent of prior investigation history.
        """
        if not self._prior_runs:
            return "This is the first investigation run for this case."

        lines = [
            f"Prior investigation runs: {len(self._prior_runs)}",
        ]
        for agent_type, output in self._prior_outputs.items():
            summary = str(output)[:200]
            lines.append(f"Prior {agent_type} output (summary): {summary}...")

        return "\n".join(lines)

    def _load_prior_runs(self, session: Session) -> list[dict]:
        rows = session.execute(text("""
            SELECT agent_run_id, agent_type, status, completed_at
            FROM audit.agent_runs
            WHERE case_id = :case_id AND status = 'completed'
            ORDER BY completed_at ASC
        """), {"case_id": str(self._case_id)}).fetchall()

        return [
            {
                "agent_run_id": str(r.agent_run_id),
                "agent_type":   r.agent_type,
                "status":       r.status,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            }
            for r in rows
        ]

    def _load_prior_outputs(self, session: Session) -> dict[str, dict]:
        """Returns {agent_type: output_payload} for the most recent completed run per agent."""
        rows = session.execute(text("""
            SELECT DISTINCT ON (agent_type)
                agent_type, output_payload
            FROM audit.agent_runs
            WHERE case_id = :case_id AND status = 'completed'
              AND output_payload IS NOT NULL
            ORDER BY agent_type, completed_at DESC
        """), {"case_id": str(self._case_id)}).fetchall()

        return {r.agent_type: r.output_payload or {} for r in rows}
