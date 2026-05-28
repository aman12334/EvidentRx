"""
InvestigationLifecycleService — validates and executes case status transitions.

All status mutations must go through this service. Direct writes to
InvestigationCase.status are prohibited in the application layer.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.audit.investigation_case import InvestigationCase
from investigation.domain.states import (
    CaseStatus,
    CLOSED_STATES,
    ESCALATED_STATES,
    InvalidTransitionError,
    validate_transition,
    get_valid_transitions,
)
from investigation.services.evidence import EvidenceAggregationService
from investigation.services.timeline import TimelineService

logger = logging.getLogger(__name__)

_timeline = TimelineService()
_evidence = EvidenceAggregationService()


class InvestigationLifecycleService:
    def transition(
        self,
        session: Session,
        case_id: UUID,
        new_status: str,
        actor_id: str,
        actor_type: str = "human",
        notes: Optional[str] = None,
        escalated_to: Optional[str] = None,
    ) -> InvestigationCase:
        """
        Validate and execute a lifecycle transition.

        Raises InvalidTransitionError if the move is not permitted.
        Records a STATUS_CHANGED timeline event and triggers a risk snapshot.
        """
        case = session.get(InvestigationCase, case_id)
        if case is None:
            raise ValueError(f"InvestigationCase {case_id} not found")

        current = CaseStatus(case.status)
        requested = CaseStatus(new_status)
        validate_transition(current, requested)   # raises if invalid

        old_status = case.status
        case.status = requested.value
        now = datetime.now(timezone.utc)

        # Side-effects of specific transitions
        if requested in CLOSED_STATES:
            case.closed_at = now
        elif requested == CaseStatus.OPEN and case.closed_at is not None:
            case.closed_at = None      # reopen

        if requested in ESCALATED_STATES and escalated_to:
            case.escalated_to = escalated_to

        session.flush()

        # Immutable timeline record
        _timeline.record(
            session, case_id, "STATUS_CHANGED",
            {"from": old_status, "to": requested.value, "notes": notes},
            actor_id=actor_id, actor_type=actor_type,
        )

        # Snapshot risk state at each transition
        _evidence.take_risk_snapshot(session, case_id, trigger="status_changed")

        logger.info(
            "Case %s transitioned %s → %s by %s (%s)",
            case_id, old_status, requested.value, actor_id, actor_type,
        )
        return case

    def get_valid_transitions(self, case: InvestigationCase) -> list[str]:
        """Returns allowed next statuses for a case — useful for UI / API."""
        return [s.value for s in get_valid_transitions(CaseStatus(case.status))]

    def assign(
        self,
        session: Session,
        case_id: UUID,
        assigned_to: str,
        actor_id: str,
        actor_type: str = "human",
    ) -> InvestigationCase:
        """Assign case to an analyst. Does not change status."""
        case = session.get(InvestigationCase, case_id)
        if case is None:
            raise ValueError(f"InvestigationCase {case_id} not found")

        old_assignee = case.assigned_to
        case.assigned_to = assigned_to
        case.assigned_at = datetime.now(timezone.utc)
        session.flush()

        _timeline.record(
            session, case_id, "ASSIGNMENT_CHANGED",
            {"from": old_assignee, "to": assigned_to},
            actor_id=actor_id, actor_type=actor_type,
        )
        return case

    def set_priority(
        self,
        session: Session,
        case_id: UUID,
        priority: str,
        actor_id: str,
        actor_type: str = "human",
    ) -> InvestigationCase:
        """Manually override case priority."""
        case = session.get(InvestigationCase, case_id)
        if case is None:
            raise ValueError(f"InvestigationCase {case_id} not found")

        old_priority = case.priority
        case.priority = priority
        session.flush()

        _timeline.record(
            session, case_id, "PRIORITY_CHANGED",
            {"from": old_priority, "to": priority},
            actor_id=actor_id, actor_type=actor_type,
        )
        return case
