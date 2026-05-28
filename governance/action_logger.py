"""
Analyst action logging — records every user-initiated action with full context.

This is the "who did what, when, and why" layer:
  - Analyst opens a case         → logged
  - Analyst changes case status  → logged (old_status → new_status)
  - Analyst adds annotation      → logged (finding_id + note text hash)
  - Analyst triggers copilot     → logged (operation + masked input)
  - Admin changes config         → logged (key + old/new values)

Action logs feed compliance reporting, SOC2 evidence, and HR audit trails.
They are distinct from audit_log.py (system events) — this is HUMAN actions.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Any, Dict

from governance.audit_log import AuditEventType, audit_log

log = logging.getLogger(__name__)


def log_analyst_action(
    actor_id:      str,
    tenant_id:     str,
    action:        str,
    resource_type: str,
    resource_id:   str | None = None,
    details:       Dict[str, Any] | None = None,
    ip_address:    str | None = None,
    user_agent:    str | None = None,
) -> None:
    """
    Log an analyst or admin action to the immutable audit log.

    action:       human-readable action string (e.g., "update_case_status")
    resource_type: type of resource affected (e.g., "investigation_case")
    resource_id:   UUID of the affected resource
    details:      structured context (status changes, old/new values, etc.)
    """
    payload: Dict[str, Any] = {
        "action":        action,
        "resource_type": resource_type,
        "occurred_at":   datetime.now(tz=UTC).isoformat(),
    }

    if ip_address:
        # Hash IP for minimal PII exposure in logs (still traceable with key)
        payload["ip_hash"] = hashlib.sha256(ip_address.encode()).hexdigest()[:16]

    if user_agent:
        payload["user_agent"] = user_agent[:200]  # truncate

    if details:
        payload["details"] = _sanitize_details(details)

    # Map to appropriate audit event type
    event_type = _action_to_event_type(action)

    audit_log.write(
        event_type=event_type,
        actor_id=actor_id,
        tenant_id=tenant_id,
        payload=payload,
        resource_id=resource_id,
        resource_type=resource_type,
    )


def _action_to_event_type(action: str) -> AuditEventType:
    """Map a human-readable action string to a structured AuditEventType."""
    _MAP = {
        "create_case":          AuditEventType.CASE_CREATED,
        "update_case_status":   AuditEventType.CASE_STATUS_CHANGED,
        "assign_case":          AuditEventType.CASE_ASSIGNED,
        "escalate_case":        AuditEventType.CASE_ESCALATED,
        "resolve_case":         AuditEventType.CASE_RESOLVED,
        "close_case":           AuditEventType.CASE_CLOSED,
        "annotate_finding":     AuditEventType.FINDING_ANNOTATED,
        "read_audit_log":       AuditEventType.AUDIT_LOG_READ,
        "replay_workflow":      AuditEventType.WORKFLOW_REPLAYED,
        "change_config":        AuditEventType.CONFIG_CHANGED,
        "toggle_flag":          AuditEventType.FLAG_TOGGLED,
        "rotate_secret":        AuditEventType.SECRET_ROTATED,
        "change_user_role":     AuditEventType.USER_ROLE_CHANGED,
    }
    return _MAP.get(action, AuditEventType.RESOURCE_ACCESSED)


def _sanitize_details(details: Dict[str, Any]) -> Dict[str, Any]:
    """
    Strip any PHI or secrets from action details before logging.
    Replaces suspicious keys with a redacted marker.
    """
    _SENSITIVE = frozenset({
        "password", "ssn", "dob", "date_of_birth", "npi",
        "patient_name", "mrn", "address", "phone", "email",
        "secret", "token", "key", "api_key",
    })
    sanitized: Dict[str, Any] = {}
    for k, v in details.items():
        if k.lower() in _SENSITIVE:
            sanitized[k] = "[REDACTED]"
        elif isinstance(v, str) and len(v) > 500:
            sanitized[k] = v[:500] + "…[truncated]"
        else:
            sanitized[k] = v
    return sanitized
