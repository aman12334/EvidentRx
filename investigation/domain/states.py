"""
Investigation Case Lifecycle State Machine.

States and valid transitions are the single source of truth for all
lifecycle operations. No service may mutate case.status without going
through validate_transition().
"""
from __future__ import annotations

from enum import Enum
from typing import FrozenSet


class CaseStatus(str, Enum):
    OPEN           = "open"
    TRIAGED        = "triaged"
    INVESTIGATING  = "investigating"
    ESCALATED      = "escalated"
    RESOLVED       = "resolved"
    FALSE_POSITIVE = "false_positive"


class CasePriority(str, Enum):
    LOW      = "low"
    MEDIUM   = "medium"
    HIGH     = "high"
    CRITICAL = "critical"


class ActorType(str, Enum):
    SYSTEM  = "system"
    HUMAN   = "human"
    AGENT   = "agent"


# ---------------------------------------------------------------------------
# Transition table — the only allowed moves
# ---------------------------------------------------------------------------
VALID_TRANSITIONS: dict[CaseStatus, FrozenSet[CaseStatus]] = {
    CaseStatus.OPEN: frozenset({
        CaseStatus.TRIAGED,
        CaseStatus.FALSE_POSITIVE,          # immediate dismiss on DQ-only cases
    }),
    CaseStatus.TRIAGED: frozenset({
        CaseStatus.INVESTIGATING,
        CaseStatus.ESCALATED,               # skip investigation if critical mass
        CaseStatus.FALSE_POSITIVE,
    }),
    CaseStatus.INVESTIGATING: frozenset({
        CaseStatus.ESCALATED,
        CaseStatus.RESOLVED,
        CaseStatus.FALSE_POSITIVE,
    }),
    CaseStatus.ESCALATED: frozenset({
        CaseStatus.INVESTIGATING,           # de-escalate
        CaseStatus.RESOLVED,
        CaseStatus.FALSE_POSITIVE,
    }),
    CaseStatus.RESOLVED: frozenset({
        CaseStatus.OPEN,                    # reopen
    }),
    CaseStatus.FALSE_POSITIVE: frozenset({
        CaseStatus.OPEN,                    # reclassify
    }),
}

# States that represent a closed case (closed_at should be set)
CLOSED_STATES: FrozenSet[CaseStatus] = frozenset({
    CaseStatus.RESOLVED,
    CaseStatus.FALSE_POSITIVE,
})

# States that require escalated_to to be populated
ESCALATED_STATES: FrozenSet[CaseStatus] = frozenset({
    CaseStatus.ESCALATED,
})


class InvalidTransitionError(ValueError):
    """Raised when a requested state transition is not permitted."""

    def __init__(self, current: CaseStatus, requested: CaseStatus) -> None:
        allowed = ", ".join(s.value for s in VALID_TRANSITIONS[current])
        super().__init__(
            f"Cannot transition from '{current.value}' to '{requested.value}'. "
            f"Allowed from '{current.value}': [{allowed}]"
        )
        self.current = current
        self.requested = requested


def validate_transition(current: CaseStatus, requested: CaseStatus) -> None:
    """Raises InvalidTransitionError if the transition is not permitted."""
    if requested not in VALID_TRANSITIONS.get(current, frozenset()):
        raise InvalidTransitionError(current, requested)


def get_valid_transitions(current: CaseStatus) -> FrozenSet[CaseStatus]:
    return VALID_TRANSITIONS.get(current, frozenset())


# ---------------------------------------------------------------------------
# Priority derivation from finding severities
# ---------------------------------------------------------------------------
_SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}
_RANK_TO_PRIORITY = {4: CasePriority.CRITICAL, 3: CasePriority.HIGH,
                     2: CasePriority.MEDIUM,   1: CasePriority.LOW}


def derive_priority(severities: list[str]) -> CasePriority:
    """
    Returns the case priority from the highest-severity finding in the cluster.
    Falls back to MEDIUM if severities list is empty.
    """
    if not severities:
        return CasePriority.MEDIUM
    max_rank = max(_SEVERITY_RANK.get(s, 1) for s in severities)
    return _RANK_TO_PRIORITY[max_rank]
