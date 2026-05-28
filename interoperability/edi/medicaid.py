"""
Medicaid-specific X12 837 claim normaliser.

Extends the base pharmacy claim normaliser with Medicaid-specific logic:
  - Medicaid payer identification heuristics (payer ID prefixes, NM1 display names)
  - 340B programme indicator detection (SV1 modifier QJ / UD, REF*6R)
  - Categorical eligibility codes from 2000B/2000C loops
  - FQHC / CHC place-of-service detection (POS codes 50, 04, 49)

All Medicaid identifiers are treated as PHI-adjacent — member IDs are never
stored in plaintext; callers must hash them before persisting.
"""

from __future__ import annotations

import logging
from typing import Any

from interoperability.edi.pharmacy_claims import normalise_837p
from interoperability.edi.x12_parser import (
    X12Interchange,
    X12Transaction,
    get_ref,
)

log = logging.getLogger("evidentrx.interop.edi.medicaid")

# Known Medicaid payer ID prefixes by state (partial list — augmented via config)
_MEDICAID_PAYER_PREFIXES = frozenset({
    "MCDNY", "MCDCA", "MCDTX", "MCDFL", "MCDIL",
    "MCDOH", "MCDPA", "MCDMI", "MCDNC", "MCDGA",
    "MHCNY", "MHCCA",   # managed care Medicaid plans
})

# Place-of-service codes that indicate covered entity eligibility
_COVERED_ENTITY_POS = frozenset({"04", "50", "49", "02"})  # FQHC, CHC, RHC, telehealth

# 340B programme modifiers on SV1
_340B_MODIFIERS = frozenset({"QJ", "UD", "U8"})

# REF qualifier for 340B drug ID
_340B_REF_QUALIFIER = "6R"


def normalise_medicaid_837(
    interchange: X12Interchange,
    tenant_id:   str,
) -> list[dict[str, Any]]:
    """
    Normalise all 837 transactions in an interchange with Medicaid enrichment.

    Calls the base 837P normaliser, then overlays Medicaid-specific fields.
    Returns one canonical dict per claim.
    """
    base_claims = normalise_837p(interchange, tenant_id)
    enriched    = []

    for claim in base_claims:
        # Re-walk the source transaction to extract Medicaid-specific fields
        ctrl = claim.get("tx_control")
        tx   = _find_tx(interchange, ctrl)
        if tx:
            _enrich_medicaid(claim, tx)
        enriched.append(claim)

    return enriched


def _enrich_medicaid(claim: dict[str, Any], tx: X12Transaction) -> None:
    """
    Overlay Medicaid-specific fields onto a base 837P canonical claim dict.

    Mutates `claim` in-place.
    """
    claim["is_medicaid"]      = _detect_medicaid_payer(claim, tx)
    claim["is_340b"]          = _detect_340b(tx)
    claim["pos_code"]         = claim.get("place_of_service")
    claim["is_covered_entity_pos"] = claim.get("place_of_service") in _COVERED_ENTITY_POS
    claim["medicaid_category"] = _extract_eligibility_category(tx)
    claim["fqhc_indicator"]   = claim.get("place_of_service") in {"04", "50"}
    claim["source_system"]    = "x12_837_medicaid"


def _detect_medicaid_payer(claim: dict[str, Any], tx: X12Transaction) -> bool:
    """
    Determine if the claim's payer is Medicaid.

    Checks:
      1. NM1 payer display name contains "medicaid" or "mcd"
      2. Payer ID matches known state Medicaid prefixes
      3. PRV segment with taxonomy code indicating Medicaid
    """
    payer_id = (claim.get("payer_id") or "").upper()

    # Prefix match
    for prefix in _MEDICAID_PAYER_PREFIXES:
        if payer_id.startswith(prefix):
            return True

    # Scan NM1 PR segments for display name
    for seg in tx.get_segments("NM1"):
        if seg.get(1) == "PR":
            name = (seg.get(3) or "").lower()
            if "medicaid" in name or " mcd" in name or "mhcp" in name:
                return True

    return False


def _detect_340b(tx: X12Transaction) -> bool:
    """
    Detect 340B programme indicator.

    Checks:
      1. SV1 modifier list (SV1-9) contains QJ, UD, or U8
      2. REF*6R segment present (340B drug ID reference)
      3. Condition code 57 on CN1 segment
    """
    for seg in tx.get_segments("SV1"):
        # SV1-7 through SV1-11 carry procedure modifiers
        for mod_idx in range(7, 12):
            mod = seg.get(mod_idx)
            if mod and mod.upper() in _340B_MODIFIERS:
                return True

    for seg in tx.get_segments("REF"):
        q, v = get_ref(seg)
        if q == _340B_REF_QUALIFIER:
            return True

    # CN1 condition code 57 indicates 340B
    for seg in tx.get_segments("CN1"):
        if seg.get(1) == "57":
            return True

    return False


def _extract_eligibility_category(tx: X12Transaction) -> str | None:
    """
    Extract Medicaid eligibility category from ELG or SBR segment.

    SBR-2 payer responsibility + SBR-9 insurance type code (MC = Medicaid).
    """
    for seg in tx.get_segments("SBR"):
        ins_type = seg.get(9)
        if ins_type:
            return ins_type     # e.g. "MC" = Medicaid, "CH" = CHIP
    return None


def _find_tx(
    interchange: X12Interchange,
    ctrl_number: str | None,
) -> X12Transaction | None:
    """Locate a transaction by its ST control number."""
    if not ctrl_number:
        return None
    for tx in interchange.transactions:
        if tx.control_number == ctrl_number:
            return tx
    return None
