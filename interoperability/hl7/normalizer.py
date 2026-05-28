"""
HL7 v2 → canonical platform schema normaliser.

Maps parsed HL7 messages into the platform's canonical intermediate
representation, mirroring the FHIR normaliser's contract so the ingestion
pipeline can treat both sources uniformly.

Supported message types → canonical types
─────────────────────────────────────────
  ADT^A01/A04  → canonical_encounter   (patient admit / registration)
  ADT^A08      → canonical_patient     (patient update — demographic refresh)
  ADT^A02/A03  → canonical_encounter   (transfer / discharge)
  ORM^O01      → canonical_medication_order
  RDE^O11      → canonical_dispense
  ORU^R01      → canonical_observation  (lab / pharmacy observation)
  DFT^P03      → canonical_claim        (financial transaction → billing claim)

Design
──────
  - PHI-safe: patient identifiers SHA-256 hashed (same _hash_ref as FHIR layer)
  - Resilient: segments missing from a message yield None, never raise
  - Deterministic: same HL7 bytes → same canonical dict
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from typing import Any

from interoperability.hl7.parser import (
    HL7Message,
    HL7MessageType,
    HL7Segment,
    extract_ndc,
    extract_npi,
)

log = logging.getLogger("evidentrx.interop.hl7.normalizer")

# Salt mirrors FHIR normaliser — must stay in sync with _PHI_SALT there
_PHI_SALT = "evidentrx_phi_hash_v1"


# ── Public entry point ────────────────────────────────────────────────────────

def normalise_hl7(msg: HL7Message, tenant_id: str) -> dict[str, Any] | None:
    """
    Map a parsed HL7Message to a canonical dict.

    Returns None if the message type is not mappable (e.g. ACK responses).
    Raises HL7NormalisationError on unrecoverable structural failures.
    """
    normaliser = _DISPATCH.get(msg.message_type)
    if normaliser is None:
        log.debug("No normaliser for HL7 message type %s", msg.message_type)
        return None

    try:
        return normaliser(msg, tenant_id)
    except Exception as e:
        raise HL7NormalisationError(
            f"Failed to normalise {msg.message_type}^{msg.trigger_event} "
            f"[{msg.message_id}]: {e}"
        ) from e


# ── Per-type normalisers ──────────────────────────────────────────────────────

def _normalise_adt(msg: HL7Message, tenant_id: str) -> dict[str, Any]:
    """ADT^A01/A02/A03/A04/A08 → canonical_encounter or canonical_patient."""
    pid = msg.get_segment("PID")
    pv1 = msg.get_segment("PV1")
    evn = msg.get_segment("EVN")

    trigger = msg.trigger_event.upper()

    # A08 = patient update — emit as patient refresh
    if trigger == "A08":
        return _pid_to_patient(pid, tenant_id, msg)

    # All other ADT triggers → encounter event
    return {
        "canonical_type":    "encounter",
        "source_system":     "hl7v2",
        "source_msg_type":   f"ADT^{trigger}",
        "message_id":        msg.message_id,
        "sending_facility":  msg.sending_facility,
        "tenant_id":         tenant_id,
        "patient_id_hash":   _hash_patient(pid, tenant_id),
        "event_type":        _adt_event_type(trigger),
        "visit_number":      _seg_get(pv1, 19),             # PV1-19 visit number
        "patient_class":     _seg_get(pv1, 2),              # PV1-2 patient class
        "admit_datetime":    _seg_get(evn, 2) or _seg_get(pv1, 44),
        "discharge_datetime":_seg_get(pv1, 45),
        "attending_npi":     _extract_xcn_npi(pv1, 7),      # PV1-7 attending doctor
        "admitting_dx":      _seg_get(msg.get_segment("DG1"), 3, component=1) if msg.get_segment("DG1") else None,
        "version":           msg.version,
        "raw_hl7_id":        msg.message_id,
    }


def _normalise_orm(msg: HL7Message, tenant_id: str) -> dict[str, Any]:
    """ORM^O01 → canonical_medication_order."""
    pid  = msg.get_segment("PID")
    orc  = msg.get_segment("ORC")
    rxo  = msg.get_segment("RXO")

    return {
        "canonical_type":   "medication_order",
        "source_system":    "hl7v2",
        "source_msg_type":  "ORM^O01",
        "message_id":       msg.message_id,
        "sending_facility": msg.sending_facility,
        "tenant_id":        tenant_id,
        "patient_id_hash":  _hash_patient(pid, tenant_id),
        "order_number":     _seg_get(orc, 2),               # ORC-2 placer order number
        "order_status":     _seg_get(orc, 5),               # ORC-5 order status
        "order_datetime":   _seg_get(orc, 9, component=0),  # ORC-9 date/time of transaction
        "prescriber_npi":   extract_npi(msg),               # ORC-12 ordering provider NPI
        "ndc_11":           extract_ndc(msg),               # RXO-2 NDC
        "drug_name":        _seg_get(rxo, 1, component=1) if rxo else None,
        "quantity":         _float(_seg_get(rxo, 2)) if rxo else None,
        "refills":          _int(_seg_get(rxo, 12)) if rxo else None,
        "version":          msg.version,
        "raw_hl7_id":       msg.message_id,
    }


def _normalise_rde(msg: HL7Message, tenant_id: str) -> dict[str, Any]:
    """RDE^O11 → canonical_dispense."""
    pid  = msg.get_segment("PID")
    orc  = msg.get_segment("ORC")
    rxe  = msg.get_segment("RXE")
    rxd  = msg.get_segment("RXD")

    return {
        "canonical_type":   "dispense",
        "source_system":    "hl7v2",
        "source_msg_type":  "RDE^O11",
        "message_id":       msg.message_id,
        "sending_facility": msg.sending_facility,
        "tenant_id":        tenant_id,
        "patient_id_hash":  _hash_patient(pid, tenant_id),
        "ndc_11":           extract_ndc(msg),
        "quantity":         _float(_seg_get(rxe, 10)) if rxe else _float(_seg_get(rxd, 4)),
        "days_supply":      _int(_seg_get(rxe, 23)) if rxe else _int(_seg_get(rxd, 24)),
        "dispense_date":    _date(_seg_get(rxd, 3)) if rxd else None,
        "pharmacy_npi":     _seg_get(orc, 21, component=9) if orc else None,
        "prescriber_npi":   extract_npi(msg),
        "fill_number":      _int(_seg_get(rxd, 7)) if rxd else None,
        "version":          msg.version,
        "raw_hl7_id":       msg.message_id,
    }


def _normalise_oru(msg: HL7Message, tenant_id: str) -> dict[str, Any]:
    """ORU^R01 → canonical_observation."""
    pid  = msg.get_segment("PID")
    obr  = msg.get_segment("OBR")
    obx  = msg.get_segment("OBX")

    return {
        "canonical_type":    "observation",
        "source_system":     "hl7v2",
        "source_msg_type":   "ORU^R01",
        "message_id":        msg.message_id,
        "sending_facility":  msg.sending_facility,
        "tenant_id":         tenant_id,
        "patient_id_hash":   _hash_patient(pid, tenant_id),
        "order_number":      _seg_get(obr, 2) if obr else None,
        "observation_id":    _seg_get(obx, 3, component=0) if obx else None,
        "observation_name":  _seg_get(obx, 3, component=1) if obx else None,
        "value":             _seg_get(obx, 5) if obx else None,
        "units":             _seg_get(obx, 6, component=0) if obx else None,
        "status":            _seg_get(obx, 11) if obx else None,
        "observation_datetime": _seg_get(obx, 14) if obx else None,
        "version":           msg.version,
        "raw_hl7_id":        msg.message_id,
    }


def _normalise_dft(msg: HL7Message, tenant_id: str) -> dict[str, Any]:
    """DFT^P03 → canonical_claim (financial transaction)."""
    pid  = msg.get_segment("PID")
    ft1  = msg.get_segment("FT1")

    return {
        "canonical_type":   "claim",
        "source_system":    "hl7v2",
        "source_msg_type":  "DFT^P03",
        "message_id":       msg.message_id,
        "sending_facility": msg.sending_facility,
        "tenant_id":        tenant_id,
        "patient_id_hash":  _hash_patient(pid, tenant_id),
        "transaction_id":   _seg_get(ft1, 1) if ft1 else None,
        "transaction_date": _date(_seg_get(ft1, 4)) if ft1 else None,
        "transaction_type": _seg_get(ft1, 5, component=0) if ft1 else None,
        "service_code":     _seg_get(ft1, 7, component=0) if ft1 else None,
        "quantity":         _float(_seg_get(ft1, 10)) if ft1 else None,
        "unit_cost":        _float(_seg_get(ft1, 11)) if ft1 else None,
        "total_amount":     _float(_seg_get(ft1, 22)) if ft1 else None,
        "ndc_11":           extract_ndc(msg),
        "version":          msg.version,
        "raw_hl7_id":       msg.message_id,
    }


# ── Patient canonical (from PID segment) ─────────────────────────────────────

def _pid_to_patient(
    pid:       HL7Segment | None,
    tenant_id: str,
    msg:       HL7Message,
) -> dict[str, Any]:
    """Produce canonical_patient from a PID segment."""
    return {
        "canonical_type":   "patient",
        "source_system":    "hl7v2",
        "source_msg_type":  f"{msg.message_type.value}^{msg.trigger_event}",
        "message_id":       msg.message_id,
        "sending_facility": msg.sending_facility,
        "tenant_id":        tenant_id,
        "patient_id_hash":  _hash_patient(pid, tenant_id),
        "gender":           _seg_get(pid, 8),               # PID-8 sex
        "birth_year":       _birth_year(_seg_get(pid, 7)),  # PID-7 DOB (year only)
        "version":          msg.version,
        "raw_hl7_id":       msg.message_id,
    }


# ── Dispatch table ─────────────────────────────────────────────────────────────

_DISPATCH: dict[HL7MessageType, Callable[[HL7Message, str], dict[str, Any]]] = {
    HL7MessageType.ADT: _normalise_adt,
    HL7MessageType.ORM: _normalise_orm,
    HL7MessageType.RDE: _normalise_rde,
    HL7MessageType.ORU: _normalise_oru,
    HL7MessageType.DFT: _normalise_dft,
}


# ── PHI helpers ───────────────────────────────────────────────────────────────

def _hash_patient(pid: HL7Segment | None, tenant_id: str) -> str:
    """
    Hash PID-3 (patient identifier list) into a stable anonymous ID.
    Falls back to PID-2 (external patient ID) if PID-3 is absent.
    """
    if pid is None:
        return _hash_ref("Patient/unknown", tenant_id)

    # PID-3 is a repeating field; use the first non-empty CX.1 value
    for repeat in pid.get_repeating(3):
        parts = repeat.split("^")
        if parts[0].strip():
            return _hash_ref(f"Patient/{parts[0].strip()}", tenant_id)

    # Fallback to PID-2
    ext_id = pid.get(2)
    if ext_id:
        return _hash_ref(f"Patient/{ext_id}", tenant_id)

    return _hash_ref("Patient/unknown", tenant_id)


def _hash_ref(ref: str, tenant_id: str) -> str:
    payload = f"{tenant_id}:{ref}:{_PHI_SALT}".encode()
    return hashlib.sha256(payload).hexdigest()[:32]


# ── Segment helpers ───────────────────────────────────────────────────────────

def _seg_get(seg: HL7Segment | None, index: int, component: int = 0) -> str | None:
    """Safe field access on a potentially-None segment."""
    if seg is None:
        return None
    return seg.get(index, component)


def _extract_xcn_npi(seg: HL7Segment | None, field_index: int) -> str | None:
    """
    Extract NPI from an XCN field.

    XCN component layout: .1=ID .2=family .3=given ... .9=assigning authority
    NPI is stored as ID (component 1) when assigning authority is "NPI".
    """
    if seg is None:
        return None
    components = seg.get_all_components(field_index)
    if len(components) >= 9 and "NPI" in components[8].upper():
        return components[0].strip() or None
    return None


def _adt_event_type(trigger: str) -> str:
    """Map ADT trigger event code to a human-readable event type."""
    _MAP = {
        "A01": "admit",
        "A02": "transfer",
        "A03": "discharge",
        "A04": "registration",
        "A08": "update",
    }
    return _MAP.get(trigger, f"adt_{trigger.lower()}")


def _birth_year(dob: str | None) -> int | None:
    """Extract year-only from HL7 DTM birthdate (never store full DOB)."""
    if not dob:
        return None
    try:
        return int(dob[:4])
    except (ValueError, TypeError):
        return None


def _date(val: str | None) -> str | None:
    """Truncate HL7 DTM to YYYY-MM-DD if possible."""
    if not val:
        return None
    val = val.strip()
    if len(val) >= 8:
        raw = val[:8]
        try:
            return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
        except Exception:
            return None
    return None


def _float(val: str | None) -> float | None:
    try:
        return float(val) if val else None
    except (TypeError, ValueError):
        return None


def _int(val: str | None) -> int | None:
    try:
        return int(val) if val else None
    except (TypeError, ValueError):
        return None


# ── Exception ─────────────────────────────────────────────────────────────────

class HL7NormalisationError(Exception):
    """Raised when an HL7 message cannot be mapped to a canonical schema."""
