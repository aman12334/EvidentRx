"""
X12 835 remittance advice normaliser.

Maps X12 835 (Health Care Claim Payment / Advice) transactions to the
platform's canonical_remittance schema so pharmacy payments can be
reconciled against submitted 837P claims.

835 structure overview
──────────────────────
  ISA → GS → ST(835) → BPR → TRN → DTM
    → 1000A NM1(PR)   — Payer
    → 1000B NM1(PE)   — Payee
    → 2000 Loop       — Claim payment loop
        → CLP         — Claim-level payment
          → NM1(QC)   — Patient name
          → NM1(IL)   — Insured
          → SVC       — Service line
            → DTM     — Service date
            → CAS     — Claim adjustment
            → REF     — Service line REF
  → PLB              — Provider-level adjustments
  → SE → GE → IEA

Key fields extracted
────────────────────
  BPR-2   payment amount
  BPR-16  payment date (effective date)
  TRN-2   check / EFT trace number
  CLP-1   claim submission ID (links back to 837P CLM-1)
  CLP-2   claim status code
  CLP-3   submitted charge amount
  CLP-4   payment amount
  SVC-1   service code composite (procedure / NDC)
  CAS-3   adjustment amount per adjustment group / reason
"""

from __future__ import annotations

import logging
from typing import Any

from interoperability.edi.pharmacy_claims import _date, _float, _normalise_ndc
from interoperability.edi.x12_parser import (
    X12Interchange,
    X12Segment,
    X12Transaction,
    X12TransactionType,
    get_nm1,
)

log = logging.getLogger("evidentrx.interop.edi.remittance")


# CLP-2 claim status codes
_CLAIM_STATUS = {
    "1":  "processed_as_primary",
    "2":  "processed_as_secondary",
    "3":  "processed_as_tertiary",
    "4":  "denied",
    "19": "processed_as_primary_forwarded",
    "20": "processed_as_secondary_forwarded",
    "21": "processed_as_tertiary_forwarded",
    "22": "reversal_of_previous",
    "23": "not_our_claim",
}

# CAS adjustment group codes
_ADJ_GROUPS = {
    "CO": "contractual_obligation",
    "CR": "correction",
    "OA": "other_adjustment",
    "PI": "payer_initiated",
    "PR": "patient_responsibility",
}


def normalise_835(
    interchange: X12Interchange,
    tenant_id:   str,
) -> list[dict[str, Any]]:
    """
    Normalise all 835 remittance transactions in an interchange.

    Returns one canonical dict per CLP segment (one per remitted claim).
    """
    results: list[dict[str, Any]] = []
    for tx in interchange.transactions:
        if tx.transaction_type != X12TransactionType.REMITTANCE:
            continue
        results.extend(_normalise_remittance_tx(tx, interchange, tenant_id))
    return results


def _normalise_remittance_tx(
    tx:          X12Transaction,
    interchange: X12Interchange,
    tenant_id:   str,
) -> list[dict[str, Any]]:
    """Extract all CLP-level payment records from one 835 ST…SE envelope."""
    segments = tx.segments
    results: list[dict[str, Any]] = []

    # Header fields
    payment_amount: float | None = None
    payment_date:   str | None   = None
    trace_number:   str | None   = None
    payer_id:       str | None   = None
    payee_npi:      str | None   = None

    for seg in segments:
        sid = seg.segment_id

        if sid == "BPR":
            payment_amount = _float(seg.get(2))
            payment_date   = _date(seg.get(16))

        elif sid == "TRN":
            trace_number = seg.get(2)       # EFT trace or check number

        elif sid == "NM1":
            qualifier = seg.get(1)
            nm1 = get_nm1(seg)
            if qualifier == "PR":           # payer
                payer_id = nm1["id"]
            elif qualifier == "PE":         # payee
                if nm1.get("id_code") == "XX":
                    payee_npi = nm1["id"]

        elif sid == "CLP":
            claim_rec = _extract_clp(
                segments      = segments,
                clp_seg       = seg,
                clp_idx       = segments.index(seg),
                payment_amount= payment_amount,
                payment_date  = payment_date,
                trace_number  = trace_number,
                payer_id      = payer_id,
                payee_npi     = payee_npi,
                interchange   = interchange,
                tx            = tx,
                tenant_id     = tenant_id,
            )
            results.append(claim_rec)

    return results


def _extract_clp(
    segments:       list[X12Segment],
    clp_seg:        X12Segment,
    clp_idx:        int,
    payment_amount: float | None,
    payment_date:   str | None,
    trace_number:   str | None,
    payer_id:       str | None,
    payee_npi:      str | None,
    interchange:    X12Interchange,
    tx:             X12Transaction,
    tenant_id:      str,
) -> dict[str, Any]:
    """Extract one CLP-anchored remittance record."""
    claim_submission_id = clp_seg.get(1)
    status_code         = clp_seg.get(2)
    submitted_amount    = _float(clp_seg.get(3))
    paid_amount         = _float(clp_seg.get(4))
    payer_claim_ctrl    = clp_seg.get(7)

    # Collect service lines and adjustments
    service_lines:  list[dict[str, Any]] = []
    adjustments:    list[dict[str, Any]] = []
    service_date:   str | None        = None
    member_id:      str | None        = None

    j = clp_idx + 1
    while j < len(segments):
        s = segments[j]
        sid = s.segment_id

        if sid == "CLP":    # next claim
            break

        elif sid == "NM1":
            qualifier = s.get(1)
            if qualifier in ("QC", "IL"):
                member_id = s.get(9) or member_id

        elif sid == "DTM" and s.get(1) in ("232", "233", "472"):
            service_date = _date(s.get(2))

        elif sid == "SVC":
            svc = _extract_svc(s, segments, j)
            service_lines.append(svc)

        elif sid == "CAS":
            adj = _extract_cas(s)
            adjustments.extend(adj)

        j += 1

    return {
        "canonical_type":        "remittance",
        "source_system":         "x12_835",
        "tenant_id":             tenant_id,
        "claim_submission_id":   claim_submission_id,
        "payer_claim_ctrl":      payer_claim_ctrl,
        "interchange_ctrl":      interchange.control_number,
        "tx_control":            tx.control_number,
        "status_code":           status_code,
        "status_label":          _CLAIM_STATUS.get(status_code or "", "unknown"),
        "submitted_amount":      submitted_amount,
        "paid_amount":           paid_amount,
        "payment_date":          payment_date,
        "service_date":          service_date,
        "trace_number":          trace_number,
        "payer_id":              payer_id,
        "payee_npi":             payee_npi,
        "member_id":             member_id,
        "service_lines":         service_lines,
        "adjustments":           adjustments,
        "total_adjustment":      sum(a["amount"] or 0 for a in adjustments),
        "raw_edi_ctrl":          claim_submission_id,
    }


def _extract_svc(svc_seg: X12Segment, segments: list[X12Segment], idx: int) -> dict[str, Any]:
    """Extract a SVC service line including inline NDC if present."""
    procedure_code  = svc_seg.get(1, component=1)  # e.g. HCPCS/CPT
    submitted       = _float(svc_seg.get(2))
    paid            = _float(svc_seg.get(3))
    ndc:            str | None = None

    # NDC may appear as SVC-1 qualifier N4
    svc1 = svc_seg.get(1, component=0)
    if svc1 == "N4":
        ndc = _normalise_ndc(svc_seg.get(1, component=1) or "")

    # Check adjacent REF*N4 for NDC
    for k in range(idx + 1, min(idx + 5, len(segments))):
        s = segments[k]
        if s.segment_id in ("SVC", "CLP"):
            break
        if s.segment_id == "REF" and s.get(1) == "N4":
            ndc = _normalise_ndc(s.get(2) or "")
            break

    return {
        "procedure_code":  procedure_code,
        "submitted":       submitted,
        "paid":            paid,
        "ndc_11":          ndc,
    }


def _extract_cas(cas_seg: X12Segment) -> list[dict[str, Any]]:
    """
    Extract all adjustment reason codes from a CAS segment.

    CAS-1: group code, then triplets of (reason_code, amount, quantity).
    """
    group_code = cas_seg.get(1)
    group_label = _ADJ_GROUPS.get(group_code or "", group_code or "unknown")
    adjustments = []

    # CAS can carry up to 6 reason/amount/qty triplets (elements 2-19)
    for start in range(2, 20, 3):
        reason = cas_seg.get(start)
        amount = _float(cas_seg.get(start + 1))
        if not reason:
            break
        adjustments.append({
            "group_code":   group_code,
            "group_label":  group_label,
            "reason_code":  reason,
            "amount":       amount,
        })

    return adjustments
