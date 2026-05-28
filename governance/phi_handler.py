"""
PHI-safe data handling abstractions.

HIPAA-oriented patterns for handling Protected Health Information in a
340B compliance platform:
  - PHI field identification
  - Masking / de-identification at the API boundary
  - Audit trail for PHI access (who accessed what, when)
  - Retention-aware data exposure
  - Minimal necessary access principle

Approach: de-identify at query time, not storage time.
Raw data stays encrypted at rest; this layer masks on read.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Set

# ─── PHI Field Definitions ────────────────────────────────────────────────────

# Fields that may contain PHI and must be masked for non-PHI-authorized roles
PHI_FIELDS: Set[str] = frozenset({
    "patient_name",
    "date_of_birth",
    "dob",
    "ssn",
    "mrn",                # medical record number
    "address",
    "street_address",
    "phone",
    "phone_number",
    "email",
    "npi",                # National Provider Identifier
    "prescriber_name",
    "pharmacy_contact",
})

# Roles authorized to see unmasked PHI
PHI_AUTHORIZED_ROLES: Set[str] = frozenset({"auditor", "admin", "system"})


class PHIHandler:
    """
    PHI masking and de-identification service.

    Usage:
        handler = PHIHandler(role="analyst", tenant_id="abc")
        safe_record = handler.mask_record(raw_db_record)
    """

    def __init__(self, role: str, tenant_id: str) -> None:
        self.role            = role
        self.tenant_id       = tenant_id
        self._phi_authorized = role in PHI_AUTHORIZED_ROLES

    def mask_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """
        Return a copy of record with PHI fields masked if the caller is not
        PHI-authorized.
        """
        if self._phi_authorized:
            return record  # no masking for authorized roles

        masked = {}
        for key, value in record.items():
            if key.lower() in PHI_FIELDS:
                masked[key] = self._mask_value(key, value)
            elif isinstance(value, dict):
                masked[key] = self.mask_record(value)
            elif isinstance(value, list):
                masked[key] = [
                    self.mask_record(v) if isinstance(v, dict) else v
                    for v in value
                ]
            else:
                masked[key] = value
        return masked

    def mask_records(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Batch mask a list of records."""
        return [self.mask_record(r) for r in records]

    def _mask_value(self, field: str, value: Any) -> str:
        """Produce a consistent masked token for a PHI value."""
        if value is None:
            return None  # type: ignore[return-value]

        # Produce a deterministic pseudo-anonymous token
        # Hash allows de-identification tracking without exposing value
        raw = f"{field}:{self.tenant_id}:{str(value)}"
        token = hashlib.sha256(raw.encode()).hexdigest()[:12]
        return f"[PHI:{token}]"

    @staticmethod
    def detect_phi_in_text(text: str) -> List[str]:
        """
        Heuristic PHI detection in free text.
        Returns list of detected PHI pattern types.
        Useful for validating that AI outputs don't leak PHI.
        """
        detected: List[str] = []

        # SSN pattern (XXX-XX-XXXX)
        if re.search(r"\b\d{3}-\d{2}-\d{4}\b", text):
            detected.append("ssn")

        # US phone patterns
        if re.search(r"\b(\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b", text):
            detected.append("phone")

        # Email
        if re.search(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", text):
            detected.append("email")

        # Date of birth patterns
        if re.search(
            r"\b(DOB|date of birth|born)[:\s]+\d{1,2}/\d{1,2}/\d{2,4}\b",
            text, re.IGNORECASE
        ):
            detected.append("date_of_birth")

        # NPI (10-digit)
        if re.search(r"\bNPI[:\s]+\d{10}\b", text, re.IGNORECASE):
            detected.append("npi")

        return detected


# Singleton with default (non-PHI) masking — replaced per-request with role context
phi_handler = PHIHandler(role="analyst", tenant_id="system")
