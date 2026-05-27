"""
Signed audit events — tamper-evident compliance log entries.

Every audit event written by governance/audit_log.py is cryptographically
signed before persistence. Verification confirms the event has not been
modified since creation.

Signature covers: event_id + timestamp + actor_id + tenant_id + event_type + payload_hash
This makes it structurally impossible to silently edit any audit record.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing   import Any, Dict

from security.crypto import sign_payload, verify_signature


def _canonical_event_string(
    event_id:   str,
    timestamp:  str,
    actor_id:   str,
    tenant_id:  str,
    event_type: str,
    payload:    Dict[str, Any],
) -> str:
    """
    Build a deterministic canonical string for signing.
    Uses sorted JSON keys to ensure consistent representation.
    """
    payload_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()

    return "|".join([
        event_id,
        timestamp,
        actor_id,
        tenant_id,
        event_type,
        payload_hash,
    ])


def sign_audit_event(
    event_id:   str,
    timestamp:  datetime,
    actor_id:   str,
    tenant_id:  str,
    event_type: str,
    payload:    Dict[str, Any],
) -> str:
    """
    Sign an audit event and return the HMAC-SHA256 hex signature.
    The signature should be stored alongside the event record.
    """
    ts_str = timestamp.astimezone(timezone.utc).isoformat()
    canonical = _canonical_event_string(
        event_id, ts_str, actor_id, tenant_id, event_type, payload
    )
    return sign_payload(canonical)


def verify_audit_event(
    event_id:   str,
    timestamp:  datetime,
    actor_id:   str,
    tenant_id:  str,
    event_type: str,
    payload:    Dict[str, Any],
    signature:  str,
) -> bool:
    """
    Verify the signature of a stored audit event.
    Returns True if the event is intact, False if tampered.
    """
    ts_str = timestamp.astimezone(timezone.utc).isoformat()
    canonical = _canonical_event_string(
        event_id, ts_str, actor_id, tenant_id, event_type, payload
    )
    return verify_signature(canonical, signature)
