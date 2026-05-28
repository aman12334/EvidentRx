"""
FHIR and canonical schema validator.

Two layers of validation:
  1. Raw FHIR resource — structural checks before normalisation
  2. Canonical record  — business-rule checks after normalisation

Returns (is_valid, list[error_message]) rather than raising, so callers
can choose to quarantine invalid records or abort the batch.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("evidentrx.interop.fhir.validator")

# Required FHIR fields per resource type
_REQUIRED_FHIR_FIELDS: dict[str, list[str]] = {
    "Patient":             ["id", "resourceType"],
    "Encounter":           ["id", "resourceType", "status", "class"],
    "MedicationRequest":   ["id", "resourceType", "status", "intent", "subject"],
    "MedicationDispense":  ["id", "resourceType", "status"],
    "Claim":               ["id", "resourceType", "status", "use", "patient"],
    "Organization":        ["id", "resourceType"],
    "Practitioner":        ["id", "resourceType"],
    "Coverage":            ["id", "resourceType", "status", "beneficiary"],
}

# Required canonical fields per canonical type
_REQUIRED_CANONICAL: dict[str, list[str]] = {
    "dispense":         ["fhir_id", "tenant_id", "status"],
    "claim":            ["fhir_id", "tenant_id", "status"],
    "patient":          ["fhir_id", "tenant_id", "patient_id_hash"],
    "organization":     ["fhir_id", "tenant_id"],
    "coverage":         ["fhir_id", "tenant_id", "status"],
    "encounter":        ["fhir_id", "tenant_id", "status"],
    "medication_order": ["fhir_id", "tenant_id", "status"],
    "practitioner":     ["fhir_id", "tenant_id"],
}


class FHIRValidator:
    """Stateless FHIR and canonical validator."""

    def validate_raw(
        self,
        resource:      dict[str, Any],
        resource_type: str,
    ) -> tuple[bool, list[str]]:
        """
        Validate a raw FHIR resource before normalisation.
        Returns (is_valid, errors).
        """
        errors: list[str] = []
        actual_type = resource.get("resourceType")

        if actual_type != resource_type:
            errors.append(
                f"resourceType mismatch: expected {resource_type!r}, got {actual_type!r}"
            )

        for field in _REQUIRED_FHIR_FIELDS.get(resource_type, []):
            if _nested_get(resource, field) is None:
                errors.append(f"Missing required field: {field!r}")

        return len(errors) == 0, errors

    def validate_canonical(
        self,
        canonical:     dict[str, Any],
        resource_type: str,
    ) -> tuple[bool, list[str]]:
        """
        Validate a canonical record after normalisation.
        Returns (is_valid, errors).
        """
        errors: list[str] = []
        ctype   = canonical.get("canonical_type", "")
        required = _REQUIRED_CANONICAL.get(ctype, [])

        for field in required:
            if not canonical.get(field):
                errors.append(f"Canonical missing required field: {field!r}")

        # Business-rule checks
        if ctype == "dispense":
            if not canonical.get("patient_id_hash"):
                errors.append("Dispense missing patient_id_hash")
            date_val = canonical.get("dispense_date")
            if date_val and not _valid_date(date_val):
                errors.append(f"Invalid dispense_date: {date_val!r}")

        if ctype == "claim":
            if not canonical.get("patient_id_hash"):
                errors.append("Claim missing patient_id_hash")

        return len(errors) == 0, errors


# ── Helpers ───────────────────────────────────────────────────────────────────

def _nested_get(obj: dict, path: str) -> Any:
    """Traverse dot-separated path in a nested dict."""
    parts = path.split(".")
    cur   = obj
    for p in parts:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def _valid_date(val: str) -> bool:
    """Check that a date string is in YYYY-MM-DD format."""
    import re
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", val))
