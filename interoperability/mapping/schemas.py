"""
Canonical platform schema definitions.

Defines the intermediate representation (IR) that all source-specific
normalisers (FHIR, HL7, EDI, pharmacy) must produce. The canonical schema
is the lingua franca between the ingestion layer and the analytics / audit
engine.

Schema philosophy
─────────────────
  - Source-agnostic: downstream code never knows whether data came from FHIR,
    HL7, EDI, or direct DB — it operates on canonical dicts
  - PHI-safe: patient identifiers are hashed; no names, addresses, or full DOBs
  - Lineage-rich: every canonical record carries source traceability metadata
  - Typed: TypedDict definitions enforce key names at development time

Canonical types
───────────────
  CanonicalDispense         → ops.dispenses
  CanonicalClaim            → ops.claims
  CanonicalRemittance       → ops.remittances
  CanonicalPatient          → ops.patients (identifier index only)
  CanonicalEncounter        → ops.encounters
  CanonicalMedicationOrder  → ops.medication_orders
  CanonicalOrganization     → ops.organizations
  CanonicalCoverage         → ops.coverage
  CanonicalPractitioner     → ops.practitioners
  CanonicalObservation      → ops.observations
"""

from __future__ import annotations

from typing import Any, Optional


# ── Base lineage fields (present in every canonical record) ───────────────────

class CanonicalBase:
    """
    Mixin of lineage fields shared by all canonical records.

    Not a TypedDict (Python 3.9 compat) — used as documentation contract.
    All normaliser functions must include these keys.

    canonical_type   : One of the canonical type strings below
    source_system    : "fhir" | "hl7v2" | "x12_837p" | "x12_835" | "x12_837_medicaid"
                       | "ncpdp_batch" | "pbm_api" | "database_direct"
    tenant_id        : Platform tenant identifier
    fhir_id          : FHIR resource ID (if source is FHIR)
    fhir_version     : FHIR meta.versionId (for change tracking)
    message_id       : HL7 MSH-10 message control ID (if source is HL7)
    interchange_ctrl : X12 ISA control number (if source is EDI)
    raw_fhir_id      : Denormalised copy of fhir_id for audit trail
    raw_hl7_id       : Denormalised copy of message_id for audit trail
    raw_edi_ctrl      : Denormalised copy of interchange_ctrl for audit trail
    """


# ── Canonical type string constants ───────────────────────────────────────────

class CanonicalType:
    DISPENSE          = "dispense"
    CLAIM             = "claim"
    REMITTANCE        = "remittance"
    PATIENT           = "patient"
    ENCOUNTER         = "encounter"
    MEDICATION_ORDER  = "medication_order"
    ORGANIZATION      = "organization"
    COVERAGE          = "coverage"
    PRACTITIONER      = "practitioner"
    OBSERVATION       = "observation"


_ALL_CANONICAL_TYPES = frozenset({
    CanonicalType.DISPENSE,
    CanonicalType.CLAIM,
    CanonicalType.REMITTANCE,
    CanonicalType.PATIENT,
    CanonicalType.ENCOUNTER,
    CanonicalType.MEDICATION_ORDER,
    CanonicalType.ORGANIZATION,
    CanonicalType.COVERAGE,
    CanonicalType.PRACTITIONER,
    CanonicalType.OBSERVATION,
})


# ── Required fields per canonical type ───────────────────────────────────────

REQUIRED_FIELDS: dict[str, list[str]] = {
    CanonicalType.DISPENSE: [
        "canonical_type", "source_system", "tenant_id",
    ],
    CanonicalType.CLAIM: [
        "canonical_type", "source_system", "tenant_id",
    ],
    CanonicalType.REMITTANCE: [
        "canonical_type", "source_system", "tenant_id",
        "claim_submission_id",
    ],
    CanonicalType.PATIENT: [
        "canonical_type", "source_system", "tenant_id",
        "patient_id_hash",
    ],
    CanonicalType.ENCOUNTER: [
        "canonical_type", "source_system", "tenant_id",
    ],
    CanonicalType.MEDICATION_ORDER: [
        "canonical_type", "source_system", "tenant_id",
    ],
    CanonicalType.ORGANIZATION: [
        "canonical_type", "source_system", "tenant_id",
    ],
    CanonicalType.COVERAGE: [
        "canonical_type", "source_system", "tenant_id",
    ],
    CanonicalType.PRACTITIONER: [
        "canonical_type", "source_system", "tenant_id",
    ],
    CanonicalType.OBSERVATION: [
        "canonical_type", "source_system", "tenant_id",
    ],
}


# ── PHI fields (must never be logged or stored unmasked) ──────────────────────

PHI_FIELDS: frozenset[str] = frozenset({
    "patient_id_hash",      # already hashed — but still sensitive
    "member_id",            # raw payer member ID — hash before storing
    "patient_context",      # raw NM1 name dict from EDI
    "subscriber_ctx",
})


# ── Source system identifiers ──────────────────────────────────────────────────

class SourceSystem:
    FHIR                = "fhir"
    HL7V2               = "hl7v2"
    X12_837P            = "x12_837p"
    X12_835             = "x12_835"
    X12_837_MEDICAID    = "x12_837_medicaid"
    NCPDP_BATCH         = "ncpdp_batch"
    PBM_API             = "pbm_api"
    DATABASE_DIRECT     = "database_direct"


_ALL_SOURCE_SYSTEMS = frozenset({
    SourceSystem.FHIR,
    SourceSystem.HL7V2,
    SourceSystem.X12_837P,
    SourceSystem.X12_835,
    SourceSystem.X12_837_MEDICAID,
    SourceSystem.NCPDP_BATCH,
    SourceSystem.PBM_API,
    SourceSystem.DATABASE_DIRECT,
})


# ── Validation helpers ────────────────────────────────────────────────────────

def validate_canonical(record: dict[str, Any]) -> tuple[bool, list[str]]:
    """
    Validate a canonical record against the schema.

    Returns (is_valid, errors). Does not raise.
    """
    errors: list[str] = []
    ctype = record.get("canonical_type")

    if not ctype:
        return False, ["Missing required field: 'canonical_type'"]

    if ctype not in _ALL_CANONICAL_TYPES:
        errors.append(f"Unknown canonical_type: {ctype!r}")

    source = record.get("source_system")
    if not source:
        errors.append("Missing required field: 'source_system'")
    elif source not in _ALL_SOURCE_SYSTEMS:
        errors.append(f"Unknown source_system: {source!r}")

    if not record.get("tenant_id"):
        errors.append("Missing required field: 'tenant_id'")

    for req_field in REQUIRED_FIELDS.get(ctype or "", []):
        if req_field not in record:
            errors.append(f"Missing required field: {req_field!r}")

    return len(errors) == 0, errors


def scrub_phi(record: dict[str, Any]) -> dict[str, Any]:
    """
    Return a copy of the record with PHI fields redacted.

    Safe for logging and debug output. Never call on records being persisted —
    PHI fields that are already-hashed (patient_id_hash) should be kept.
    """
    scrubbed = dict(record)
    for key in PHI_FIELDS:
        if key in scrubbed:
            scrubbed[key] = "[REDACTED]"
    return scrubbed
