"""
governance — Immutable Audit & Compliance Governance Infrastructure

Provides:
  - Immutable audit log with HMAC-signed entries
  - Append-only event store for workflow events
  - Analyst action logging with RBAC context
  - PHI-safe data handling abstractions
  - Data retention policies with soft deletion
  - Investigation archival workflows
  - Governance replay infrastructure

Invariant: once written, no audit record can be silently modified.
Any modification attempt is itself audited.
"""

from governance.action_logger import log_analyst_action
from governance.audit_log import AuditLog, audit_log
from governance.phi_handler import PHIHandler, phi_handler

__all__ = [
    "audit_log",
    "AuditLog",
    "log_analyst_action",
    "PHIHandler",
    "phi_handler",
]
