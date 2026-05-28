"""
FHIR R4 resource type definitions and metadata.

Declares which FHIR resources the platform ingests, what search parameters
are used for incremental sync, and which fields carry patient identifiers
that require de-identification before persisting.

No business logic here — this is a pure data definition module consumed
by the FHIR normaliser, validator, and sync engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FHIRResourceType(str, Enum):
    """FHIR R4 resource types supported by the platform."""
    PATIENT              = "Patient"
    ENCOUNTER            = "Encounter"
    MEDICATION_REQUEST   = "MedicationRequest"
    MEDICATION_DISPENSE  = "MedicationDispense"
    CLAIM                = "Claim"
    ORGANIZATION         = "Organization"
    PRACTITIONER         = "Practitioner"
    COVERAGE             = "Coverage"
    # Extended resources — may be added in future phases
    CONDITION            = "Condition"
    PROCEDURE            = "Procedure"
    OBSERVATION          = "Observation"
    MEDICATION           = "Medication"


@dataclass(frozen=True)
class ResourceMeta:
    """
    Ingestion metadata for a FHIR resource type.

    sync_params     : Default search parameters for incremental sync
    phi_fields      : JSON paths that contain PHI — hashed before storage
    version_field   : Path to the resource version string (for change tracking)
    canonical_type  : Which canonical platform schema this maps to
    """
    resource_type:    FHIRResourceType
    sync_params:      dict[str, str]
    phi_fields:       list[str]
    version_field:    str                       = "meta.versionId"
    canonical_type:   str | None             = None
    supported:        bool                      = True


# ── Resource catalogue ────────────────────────────────────────────────────────

RESOURCE_CATALOGUE: dict[FHIRResourceType, ResourceMeta] = {

    FHIRResourceType.PATIENT: ResourceMeta(
        resource_type = FHIRResourceType.PATIENT,
        sync_params   = {"_sort": "_lastUpdated", "_elements": "id,meta,identifier,name,birthDate"},
        phi_fields    = [
            "name[*].family", "name[*].given[*]",
            "birthDate", "address[*]", "telecom[*].value",
            "identifier[*].value",
        ],
        canonical_type = "patient",
    ),

    FHIRResourceType.ENCOUNTER: ResourceMeta(
        resource_type = FHIRResourceType.ENCOUNTER,
        sync_params   = {"_sort": "_lastUpdated", "_include": "Encounter:patient"},
        phi_fields    = ["subject.reference"],
        canonical_type = "encounter",
    ),

    FHIRResourceType.MEDICATION_REQUEST: ResourceMeta(
        resource_type = FHIRResourceType.MEDICATION_REQUEST,
        sync_params   = {
            "_sort":    "_lastUpdated",
            "status":   "active,completed",
            "_include": "MedicationRequest:medication",
        },
        phi_fields    = ["subject.reference", "requester.reference"],
        canonical_type = "medication_order",
    ),

    FHIRResourceType.MEDICATION_DISPENSE: ResourceMeta(
        resource_type = FHIRResourceType.MEDICATION_DISPENSE,
        sync_params   = {
            "_sort":   "_lastUpdated",
            "_include":"MedicationDispense:medication,MedicationDispense:prescription",
        },
        phi_fields    = ["subject.reference", "performer[*].actor.reference"],
        canonical_type = "dispense",  # maps to ops.dispenses
    ),

    FHIRResourceType.CLAIM: ResourceMeta(
        resource_type = FHIRResourceType.CLAIM,
        sync_params   = {
            "_sort":  "_lastUpdated",
            "status": "active,cancelled,draft,entered-in-error",
        },
        phi_fields    = ["patient.reference", "provider.reference"],
        canonical_type = "claim",     # maps to ops.claims
    ),

    FHIRResourceType.ORGANIZATION: ResourceMeta(
        resource_type = FHIRResourceType.ORGANIZATION,
        sync_params   = {"_sort": "_lastUpdated", "active": "true"},
        phi_fields    = [],            # no PHI in org records
        canonical_type = "organization",
    ),

    FHIRResourceType.PRACTITIONER: ResourceMeta(
        resource_type = FHIRResourceType.PRACTITIONER,
        sync_params   = {"_sort": "_lastUpdated", "active": "true"},
        phi_fields    = ["name[*].family", "name[*].given[*]"],
        canonical_type = "practitioner",
    ),

    FHIRResourceType.COVERAGE: ResourceMeta(
        resource_type = FHIRResourceType.COVERAGE,
        sync_params   = {"_sort": "_lastUpdated", "status": "active"},
        phi_fields    = ["subscriber.reference", "beneficiary.reference"],
        canonical_type = "coverage",
    ),
}


def get_meta(resource_type: str) -> ResourceMeta:
    """Return ResourceMeta for a resource type string, raising KeyError if unknown."""
    try:
        return RESOURCE_CATALOGUE[FHIRResourceType(resource_type)]
    except ValueError:
        raise KeyError(f"Unknown FHIR resource type: {resource_type!r}") from None


def supported_types() -> list[str]:
    """Return all supported resource type strings."""
    return [rt.value for rt, meta in RESOURCE_CATALOGUE.items() if meta.supported]
