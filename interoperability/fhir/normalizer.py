"""
FHIR R4 → canonical platform schema normaliser.

Maps raw FHIR resource JSON into the platform's canonical intermediate
representation (see interoperability/mapping/schemas.py).

Design principles
─────────────────
  - Deterministic: same input always produces same output
  - PHI-safe: patient identifiers are SHA-256 hashed before leaving this module
  - Lineage-preserving: the raw FHIR resource is always retained alongside canonical
  - Strict typing: returns typed canonical dicts — no arbitrary keys

FHIR resource → canonical mapping
──────────────────────────────────
  MedicationDispense → CanonicalDispense  (→ ops.dispenses)
  Claim              → CanonicalClaim     (→ ops.claims)
  Patient            → CanonicalPatient   (hashed identifier only)
  MedicationRequest  → CanonicalOrder
  Encounter          → CanonicalEncounter
  Organization       → CanonicalOrg
  Coverage           → CanonicalCoverage
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from interoperability.fhir.resources import FHIRResourceType

log = logging.getLogger("evidentrx.interop.fhir.normalizer")

# Salt for patient identifier hashing — loaded from settings in production
_PHI_SALT = "evidentrx_phi_hash_v1"


# ── Public entry point ────────────────────────────────────────────────────────

def normalise(resource: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    """
    Dispatch a raw FHIR resource to the appropriate normaliser.

    Returns a canonical dict. Raises NormalisationError on unrecognised types.
    The raw resource is NOT included here; the pipeline attaches it separately.
    """
    rtype = resource.get("resourceType", "")
    try:
        rt = FHIRResourceType(rtype)
    except ValueError:
        raise NormalisationError(f"Unsupported FHIR resource type: {rtype!r}")

    normaliser = _DISPATCH.get(rt)
    if normaliser is None:
        raise NormalisationError(f"No normaliser for {rtype!r}")

    try:
        return normaliser(resource, tenant_id)
    except Exception as e:
        raise NormalisationError(f"Failed to normalise {rtype} {resource.get('id')}: {e}") from e


# ── Per-resource normalisers ──────────────────────────────────────────────────

def _normalise_medication_dispense(r: dict, tenant_id: str) -> dict:
    """MedicationDispense → CanonicalDispense (maps to ops.dispenses)."""
    medication_ref = _coding_display(r.get("medicationCodeableConcept")) or \
                     _ref(r.get("medicationReference"))
    ndc = _extract_ndc(r.get("medicationCodeableConcept", {}))

    return {
        "canonical_type":    "dispense",
        "source_system":     "fhir",
        "fhir_id":           r.get("id"),
        "fhir_version":      _version(r),
        "tenant_id":         tenant_id,
        "status":            r.get("status"),
        "patient_id_hash":   _hash_ref(r.get("subject", {}).get("reference", ""), tenant_id),
        "ndc_11":            ndc,
        "medication_name":   medication_ref,
        "quantity":          _quantity(r.get("quantity")),
        "days_supply":       _int(r.get("daysSupply", {}).get("value")),
        "dispense_date":     _date(r.get("whenHandedOver") or r.get("whenPrepared")),
        "pharmacy_npi":      _extract_npi(r.get("location", {})),
        "prescriber_npi":    _extract_npi(r.get("authorizingPrescription", [{}])[0] if r.get("authorizingPrescription") else {}),
        "is_340b":           _is_340b(r),
        "raw_fhir_id":       r.get("id"),
    }


def _normalise_claim(r: dict, tenant_id: str) -> dict:
    """Claim → CanonicalClaim (maps to ops.claims)."""
    return {
        "canonical_type":    "claim",
        "source_system":     "fhir",
        "fhir_id":           r.get("id"),
        "fhir_version":      _version(r),
        "tenant_id":         tenant_id,
        "status":            r.get("status"),
        "use":               r.get("use"),                     # claim | preauthorization | predetermination
        "patient_id_hash":   _hash_ref(r.get("patient", {}).get("reference", ""), tenant_id),
        "provider_npi":      _extract_npi(r.get("provider", {})),
        "payer_id":          _ref(r.get("insurer")),
        "service_date":      _date(r.get("billablePeriod", {}).get("start")),
        "total_amount":      _money(r.get("total")),
        "is_medicaid":       _is_medicaid(r),
        "diagnosis_codes":   _icd_codes(r.get("diagnosis", [])),
        "ndc_list":          _claim_ndcs(r.get("item", [])),
        "raw_fhir_id":       r.get("id"),
    }


def _normalise_patient(r: dict, tenant_id: str) -> dict:
    """Patient → CanonicalPatient (PHI-hashed identifiers only)."""
    return {
        "canonical_type":    "patient",
        "source_system":     "fhir",
        "fhir_id":           r.get("id"),
        "fhir_version":      _version(r),
        "tenant_id":         tenant_id,
        # PHI: only store stable hash — never name, DOB, address
        "patient_id_hash":   _hash_patient_id(r, tenant_id),
        "gender":            r.get("gender"),
        "birth_year":        _birth_year(r.get("birthDate")),  # year only — no full DOB
        "active":            r.get("active", True),
        "raw_fhir_id":       r.get("id"),
    }


def _normalise_organization(r: dict, tenant_id: str) -> dict:
    """Organization → CanonicalOrg."""
    return {
        "canonical_type":  "organization",
        "source_system":   "fhir",
        "fhir_id":         r.get("id"),
        "fhir_version":    _version(r),
        "tenant_id":       tenant_id,
        "name":            r.get("name"),
        "active":          r.get("active", True),
        "npi":             _extract_identifier(r, "http://hl7.org/fhir/sid/us-npi"),
        "type_code":       _coding_code(r.get("type", [{}])[0] if r.get("type") else {}),
        "raw_fhir_id":     r.get("id"),
    }


def _normalise_coverage(r: dict, tenant_id: str) -> dict:
    """Coverage → CanonicalCoverage."""
    return {
        "canonical_type":  "coverage",
        "source_system":   "fhir",
        "fhir_id":         r.get("id"),
        "fhir_version":    _version(r),
        "tenant_id":       tenant_id,
        "status":          r.get("status"),
        "patient_id_hash": _hash_ref(r.get("beneficiary", {}).get("reference", ""), tenant_id),
        "payer_id":        _ref(r.get("payor", [{}])[0] if r.get("payor") else {}),
        "is_medicaid":     _coverage_is_medicaid(r),
        "period_start":    _date(r.get("period", {}).get("start")),
        "period_end":      _date(r.get("period", {}).get("end")),
        "raw_fhir_id":     r.get("id"),
    }


def _normalise_encounter(r: dict, tenant_id: str) -> dict:
    """Encounter → CanonicalEncounter."""
    return {
        "canonical_type":  "encounter",
        "source_system":   "fhir",
        "fhir_id":         r.get("id"),
        "fhir_version":    _version(r),
        "tenant_id":       tenant_id,
        "status":          r.get("status"),
        "class_code":      r.get("class", {}).get("code"),
        "patient_id_hash": _hash_ref(r.get("subject", {}).get("reference", ""), tenant_id),
        "period_start":    _date(r.get("period", {}).get("start")),
        "period_end":      _date(r.get("period", {}).get("end")),
        "raw_fhir_id":     r.get("id"),
    }


def _normalise_medication_request(r: dict, tenant_id: str) -> dict:
    """MedicationRequest → CanonicalOrder."""
    ndc = _extract_ndc(r.get("medicationCodeableConcept", {}))
    return {
        "canonical_type":    "medication_order",
        "source_system":     "fhir",
        "fhir_id":           r.get("id"),
        "fhir_version":      _version(r),
        "tenant_id":         tenant_id,
        "status":            r.get("status"),
        "intent":            r.get("intent"),
        "patient_id_hash":   _hash_ref(r.get("subject", {}).get("reference", ""), tenant_id),
        "prescriber_npi":    _extract_npi(r.get("requester", {})),
        "ndc_11":            ndc,
        "authored_on":       _date(r.get("authoredOn")),
        "raw_fhir_id":       r.get("id"),
    }


def _normalise_practitioner(r: dict, tenant_id: str) -> dict:
    """Practitioner → CanonicalPractitioner."""
    return {
        "canonical_type":  "practitioner",
        "source_system":   "fhir",
        "fhir_id":         r.get("id"),
        "fhir_version":    _version(r),
        "tenant_id":       tenant_id,
        "npi":             _extract_identifier(r, "http://hl7.org/fhir/sid/us-npi"),
        "active":          r.get("active", True),
        # No name stored — PHI
        "raw_fhir_id":     r.get("id"),
    }


# ── Dispatch table ────────────────────────────────────────────────────────────

_DISPATCH = {
    FHIRResourceType.MEDICATION_DISPENSE: _normalise_medication_dispense,
    FHIRResourceType.CLAIM:               _normalise_claim,
    FHIRResourceType.PATIENT:             _normalise_patient,
    FHIRResourceType.ORGANIZATION:        _normalise_organization,
    FHIRResourceType.COVERAGE:            _normalise_coverage,
    FHIRResourceType.ENCOUNTER:           _normalise_encounter,
    FHIRResourceType.MEDICATION_REQUEST:  _normalise_medication_request,
    FHIRResourceType.PRACTITIONER:        _normalise_practitioner,
}


# ── PHI helpers ───────────────────────────────────────────────────────────────

def _hash_ref(ref: str, tenant_id: str) -> str:
    """Hash a FHIR reference string (e.g. 'Patient/12345') into a stable anonymous ID."""
    if not ref:
        return ""
    payload = f"{tenant_id}:{ref}:{_PHI_SALT}".encode()
    return hashlib.sha256(payload).hexdigest()[:32]


def _hash_patient_id(r: dict, tenant_id: str) -> str:
    """Derive a stable anonymous patient hash from identifiers + id."""
    # Prefer MRN or SSN if present (then hash it)
    for identifier in r.get("identifier", []):
        val = identifier.get("value", "")
        if val:
            return _hash_ref(f"Patient/{val}", tenant_id)
    return _hash_ref(f"Patient/{r.get('id', '')}", tenant_id)


def _birth_year(birth_date: str | None) -> int | None:
    """Extract year only from FHIR birthDate (never store full DOB)."""
    if not birth_date:
        return None
    try:
        return int(birth_date[:4])
    except (ValueError, TypeError):
        return None


# ── FHIR field extractors ─────────────────────────────────────────────────────

def _version(r: dict) -> str | None:
    return r.get("meta", {}).get("versionId")


def _ref(obj: dict | None) -> str | None:
    if not obj:
        return None
    return obj.get("reference") or obj.get("display")


def _date(val: str | None) -> str | None:
    if not val:
        return None
    # Truncate to date portion regardless of format
    return val[:10] if len(val) >= 10 else val


def _quantity(q: dict | None) -> float | None:
    if not q:
        return None
    try:
        return float(q.get("value", 0))
    except (TypeError, ValueError):
        return None


def _money(m: dict | None) -> float | None:
    if not m:
        return None
    try:
        return float(m.get("value", 0))
    except (TypeError, ValueError):
        return None


def _int(val: Any) -> int | None:
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _coding_display(cc: dict | None) -> str | None:
    if not cc:
        return None
    for coding in cc.get("coding", []):
        if coding.get("display"):
            return coding["display"]
    return cc.get("text")


def _coding_code(cc: dict) -> str | None:
    for coding in cc.get("coding", []):
        if coding.get("code"):
            return coding["code"]
    return None


def _extract_ndc(cc: dict) -> str | None:
    """Extract NDC-11 from a CodeableConcept."""
    for coding in cc.get("coding", []):
        system = coding.get("system", "")
        if "ndc" in system.lower() or "rxnorm" not in system.lower():
            code = coding.get("code", "")
            if re.match(r"^\d{10,11}$", code.replace("-", "")):
                return code.replace("-", "").zfill(11)
    return None


def _extract_npi(ref: dict) -> str | None:
    """Extract NPI from an identifier list on an embedded resource or reference."""
    for identifier in ref.get("identifier", []):
        if "npi" in identifier.get("system", "").lower():
            return identifier.get("value")
    return None


def _extract_identifier(r: dict, system: str) -> str | None:
    for identifier in r.get("identifier", []):
        if identifier.get("system") == system:
            return identifier.get("value")
    return None


def _icd_codes(diagnoses: list[dict]) -> list[str]:
    codes = []
    for d in diagnoses:
        cc = d.get("diagnosisCodeableConcept", {})
        for coding in cc.get("coding", []):
            if coding.get("code"):
                codes.append(coding["code"])
    return codes


def _claim_ndcs(items: list[dict]) -> list[str]:
    ndcs = []
    for item in items:
        ndc = _extract_ndc(item.get("productOrService", {}))
        if ndc:
            ndcs.append(ndc)
    return ndcs


def _is_340b(r: dict) -> bool:
    """Heuristic: check for 340B extension or tag."""
    for ext in r.get("extension", []):
        if "340b" in ext.get("url", "").lower():
            return True
    for tag in r.get("meta", {}).get("tag", []):
        if "340b" in tag.get("code", "").lower():
            return True
    return False


def _is_medicaid(r: dict) -> bool:
    """Check if claim payer is Medicaid."""
    insurer = r.get("insurer", {})
    display = (insurer.get("display") or insurer.get("reference") or "").lower()
    return "medicaid" in display


def _coverage_is_medicaid(r: dict) -> bool:
    for payor in r.get("payor", []):
        display = (payor.get("display") or payor.get("reference") or "").lower()
        if "medicaid" in display:
            return True
    return False


# ── Exception ─────────────────────────────────────────────────────────────────

class NormalisationError(Exception):
    """Raised when a FHIR resource cannot be mapped to a canonical schema."""
