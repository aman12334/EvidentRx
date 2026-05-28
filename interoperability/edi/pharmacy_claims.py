"""
X12 837P pharmacy claim normaliser.

Maps an X12 837P transaction (professional pharmacy claim) to the platform's
canonical_claim schema. Designed for NCPDP-origin 837P files used by pharmacy
benefit managers and retail pharmacy chains submitting to Medicaid/commercial.

Key X12 837P loops extracted
────────────────────────────
  ISA / GS         — interchange / functional group metadata
  2000A            — billing provider hierarchy
  2000B            — subscriber hierarchy (insured / member)
  2000C            — patient hierarchy (when different from subscriber)
  2300             — claim information (CLM segment)
  2400             — service line items (SV1 + LX segments)

NDC extraction
──────────────
  NDC may appear in:
    - SV1-1 (CAS composite, system "N4" for NDC)
    - LIN segment (LIN*1*N4*{ndc})
    - REF*N4*{ndc} on service line
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from interoperability.edi.x12_parser import (
    X12Interchange,
    X12Segment,
    X12Transaction,
    X12TransactionType,
    get_nm1,
    get_ref,
)

log = logging.getLogger("evidentrx.interop.edi.pharmacy_claims")


def normalise_837p(
    interchange: X12Interchange,
    tenant_id:   str,
) -> list[dict[str, Any]]:
    """
    Normalise all 837P transactions in an interchange to canonical_claim dicts.

    Returns one canonical dict per CLM segment (one per claim). A single
    interchange may contain many claims.
    """
    results: list[dict[str, Any]] = []
    for tx in interchange.transactions:
        if tx.transaction_type not in (
            X12TransactionType.CLAIM_PROFESSIONAL,
            X12TransactionType.UNKNOWN,   # some 837P files come through as UNKNOWN
        ):
            continue
        results.extend(_normalise_transaction(tx, interchange, tenant_id))
    return results


def _normalise_transaction(
    tx:          X12Transaction,
    interchange: X12Interchange,
    tenant_id:   str,
) -> list[dict[str, Any]]:
    """Extract all CLM-level claims from one ST…SE envelope."""
    segments  = tx.segments
    results:  list[dict[str, Any]] = []

    # Walk linearly — build context as we encounter loop-opening segments
    billing_npi:  Optional[str] = None
    patient_ctx:  dict[str, Any] = {}
    subscriber_ctx: dict[str, Any] = {}

    i = 0
    while i < len(segments):
        seg = segments[i]
        sid = seg.segment_id

        # ── 2000A: Billing provider ───────────────────────────────────────────
        if sid == "NM1" and seg.get(1) == "85":   # billing provider
            nm1 = get_nm1(seg)
            if nm1["id_code"] == "XX":             # XX = NPI
                billing_npi = nm1["id"]

        # ── 2000B: Subscriber ─────────────────────────────────────────────────
        elif sid == "NM1" and seg.get(1) == "IL":  # insured/subscriber
            subscriber_ctx = get_nm1(seg)

        # ── 2000C: Patient ────────────────────────────────────────────────────
        elif sid == "NM1" and seg.get(1) == "QC":  # patient
            patient_ctx = get_nm1(seg)

        # ── 2300: Claim ───────────────────────────────────────────────────────
        elif sid == "CLM":
            claim = _extract_claim(
                segments      = segments,
                clm_index     = i,
                billing_npi   = billing_npi,
                patient_ctx   = patient_ctx or subscriber_ctx,
                interchange   = interchange,
                tx            = tx,
                tenant_id     = tenant_id,
            )
            results.append(claim)

        i += 1

    return results


def _extract_claim(
    segments:    list[X12Segment],
    clm_index:   int,
    billing_npi: Optional[str],
    patient_ctx: dict[str, Any],
    interchange: X12Interchange,
    tx:          X12Transaction,
    tenant_id:   str,
) -> dict[str, Any]:
    """Extract a single CLM-anchored claim from segment list."""
    clm = segments[clm_index]
    claim_id    = clm.get(1)
    total_amount= _float(clm.get(2))
    service_loc = clm.get(5, component=0)  # place of service
    claim_freq  = clm.get(5, component=1)  # claim frequency code

    # Walk forward from CLM to collect DTP, NM1, REF, SV1, LIN, NTE
    service_date:   Optional[str]  = None
    rendering_npi:  Optional[str]  = None
    diagnosis_codes: list[str]     = []
    ndcs:           list[str]      = []
    payer_id:       Optional[str]  = None
    member_id:      Optional[str]  = None

    j = clm_index + 1
    while j < len(segments):
        s = segments[j]
        sid = s.segment_id

        # Next CLM terminates this claim's scope
        if sid == "CLM":
            break

        # DTP — date segments
        if sid == "DTP":
            qualifier = s.get(1)
            if qualifier in ("472", "435"):  # service date / admission
                service_date = _date(s.get(3))

        # HI — diagnosis codes
        elif sid == "HI":
            for idx in range(1, 13):
                cc = s.get(idx)
                if cc:
                    parts = cc.split(":")
                    code  = parts[1].strip() if len(parts) > 1 else ""
                    if code:
                        diagnosis_codes.append(code)

        # NM1 — names in claim scope
        elif sid == "NM1":
            qualifier = s.get(1)
            if qualifier == "82" and s.get(8) == "XX":  # rendering provider
                rendering_npi = s.get(9)
            elif qualifier == "PR":                     # payer
                payer_id = s.get(9)
            elif qualifier in ("IL", "QC"):             # insured / patient
                member_id = s.get(9)

        # REF — reference numbers
        elif sid == "REF":
            q, v = get_ref(s)
            if q == "SY":      # member/subscriber ID
                member_id = v or member_id

        # LIN — line-level NDC
        elif sid == "LIN":
            if s.get(2) == "N4":
                ndc = _normalise_ndc(s.get(3) or "")
                if ndc:
                    ndcs.append(ndc)

        # SV1 — professional service line (may carry NDC in product qualifier)
        elif sid == "SV1":
            sv1_code = s.get(1)
            if sv1_code:
                parts = sv1_code.split(":")
                # CAS format: qualifier:code — qualifier N4 = NDC
                if len(parts) >= 2 and parts[0] == "N4":
                    ndc = _normalise_ndc(parts[1])
                    if ndc:
                        ndcs.append(ndc)

        j += 1

    return {
        "canonical_type":   "claim",
        "source_system":    "x12_837p",
        "tenant_id":        tenant_id,
        "claim_id":         claim_id,
        "interchange_ctrl": interchange.control_number,
        "tx_control":       tx.control_number,
        "total_amount":     total_amount,
        "service_date":     service_date,
        "place_of_service": service_loc,
        "claim_frequency":  claim_freq,
        "billing_npi":      billing_npi,
        "rendering_npi":    rendering_npi or billing_npi,
        "payer_id":         payer_id,
        "member_id":        member_id,
        "patient_context":  patient_ctx,
        "diagnosis_codes":  list(dict.fromkeys(diagnosis_codes)),   # deduplicated
        "ndc_list":         list(dict.fromkeys(ndcs)),
        "raw_edi_ctrl":     claim_id,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalise_ndc(raw: str) -> Optional[str]:
    """Normalise an NDC string to 11-digit zero-padded format."""
    digits = re.sub(r"[^0-9]", "", raw)
    if len(digits) in (10, 11):
        return digits.zfill(11)
    return None


def _float(val: Optional[str]) -> Optional[float]:
    try:
        return float(val) if val else None
    except (TypeError, ValueError):
        return None


def _date(val: Optional[str]) -> Optional[str]:
    """Convert CCYYMMDD or CCYY-MM-DD to YYYY-MM-DD."""
    if not val:
        return None
    val = val.replace("-", "").strip()
    if len(val) == 8:
        return f"{val[:4]}-{val[4:6]}-{val[6:8]}"
    return val
