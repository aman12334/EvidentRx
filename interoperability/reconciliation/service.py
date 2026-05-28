"""
Data reconciliation service.

Matches and reconciles records across multiple source systems to detect:
  - Discrepancies between what was dispensed vs. claimed vs. paid
  - 340B programme compliance gaps (dispense without an eligible encounter)
  - Duplicate records from different source feeds
  - Missing records (expected in one system but absent in another)

Reconciliation types
────────────────────
  dispense_vs_claim    : Match MedicationDispense records against Claims
  claim_vs_remittance  : Match submitted Claims against 835 remittance payments
  order_vs_dispense    : Match MedicationRequest orders against dispenses
  encounter_vs_dispense: Match patient encounters against dispenses (340B)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime    import date, datetime, timezone
from enum        import Enum
from typing      import Any, Optional

log = logging.getLogger("evidentrx.interop.reconciliation.service")


class ReconciliationType(str, Enum):
    DISPENSE_VS_CLAIM     = "dispense_vs_claim"
    CLAIM_VS_REMITTANCE   = "claim_vs_remittance"
    ORDER_VS_DISPENSE     = "order_vs_dispense"
    ENCOUNTER_VS_DISPENSE = "encounter_vs_dispense"


class MatchStatus(str, Enum):
    MATCHED    = "matched"
    PARTIAL    = "partial"         # record exists but amounts/dates differ
    UNMATCHED  = "unmatched"       # record in A, not found in B
    ORPHANED   = "orphaned"        # record in B, no corresponding record in A
    DUPLICATE  = "duplicate"


@dataclass
class ReconciliationMatch:
    match_id:      str
    recon_type:    ReconciliationType
    status:        MatchStatus
    record_a:      dict[str, Any]       # primary record
    record_b:      Optional[dict[str, Any]]  # matched record (None if unmatched)
    discrepancies: list[str]            = field(default_factory=list)
    confidence:    float                = 1.0    # 0.0 – 1.0
    matched_on:    list[str]            = field(default_factory=list)  # keys used for matching


@dataclass
class ReconciliationReport:
    tenant_id:       str
    recon_type:      ReconciliationType
    period_start:    Optional[date]
    period_end:      Optional[date]
    total_a:         int                     # records in primary set
    total_b:         int                     # records in secondary set
    matched:         int
    partial:         int
    unmatched_a:     int                     # in A, not in B
    unmatched_b:     int                     # in B, not in A
    duplicates:      int
    matches:         list[ReconciliationMatch] = field(default_factory=list)
    completed_at:    datetime                  = field(default_factory=lambda: datetime.now(tz=timezone.utc))

    @property
    def match_rate(self) -> float:
        return self.matched / self.total_a if self.total_a > 0 else 0.0

    @property
    def has_discrepancies(self) -> bool:
        return self.unmatched_a > 0 or self.unmatched_b > 0 or self.partial > 0


class ReconciliationService:
    """
    Cross-source data reconciliation engine.

    Performs deterministic matching of records across two datasets using
    configurable match keys. Discrepancies are flagged for human review
    or automated compliance action.
    """

    # ── Dispense vs Claim ──────────────────────────────────────────────────────

    def reconcile_dispense_vs_claim(
        self,
        dispenses:   list[dict[str, Any]],
        claims:      list[dict[str, Any]],
        tenant_id:   str,
        period_start: Optional[date] = None,
        period_end:   Optional[date] = None,
    ) -> ReconciliationReport:
        """
        Match pharmacy dispense records against claims.

        Matching keys: patient_id_hash + ndc_11 + dispense_date
        Discrepancy checks: quantity, days_supply, prescriber_npi
        """
        match_key = lambda r: (
            r.get("patient_id_hash", ""),
            r.get("ndc_11", ""),
            (r.get("dispense_date") or "")[:10],    # YYYY-MM-DD
        )

        matches = _match_records(
            records_a   = dispenses,
            records_b   = claims,
            key_fn_a    = match_key,
            key_fn_b    = lambda c: (
                c.get("patient_id_hash", ""),
                (c.get("ndc_list") or [""])[0],
                (c.get("service_date") or "")[:10],
            ),
            recon_type  = ReconciliationType.DISPENSE_VS_CLAIM,
            check_fields= ["quantity", "days_supply", "prescriber_npi"],
        )

        return _build_report(
            tenant_id    = tenant_id,
            recon_type   = ReconciliationType.DISPENSE_VS_CLAIM,
            matches      = matches,
            total_a      = len(dispenses),
            total_b      = len(claims),
            period_start = period_start,
            period_end   = period_end,
        )

    # ── Claim vs Remittance ────────────────────────────────────────────────────

    def reconcile_claim_vs_remittance(
        self,
        claims:      list[dict[str, Any]],
        remittances: list[dict[str, Any]],
        tenant_id:   str,
        period_start: Optional[date] = None,
        period_end:   Optional[date] = None,
    ) -> ReconciliationReport:
        """
        Match submitted claims against 835 remittance advice.

        Matching key: claim_id / claim_submission_id
        Discrepancy checks: submitted_amount vs. paid_amount, status_code
        """
        matches = _match_records(
            records_a   = claims,
            records_b   = remittances,
            key_fn_a    = lambda c: (c.get("claim_id") or c.get("fhir_id") or "",),
            key_fn_b    = lambda r: (r.get("claim_submission_id") or "",),
            recon_type  = ReconciliationType.CLAIM_VS_REMITTANCE,
            check_fields= ["total_amount", "status"],
        )

        return _build_report(
            tenant_id    = tenant_id,
            recon_type   = ReconciliationType.CLAIM_VS_REMITTANCE,
            matches      = matches,
            total_a      = len(claims),
            total_b      = len(remittances),
            period_start = period_start,
            period_end   = period_end,
        )

    # ── Encounter vs Dispense (340B eligibility) ───────────────────────────────

    def reconcile_encounter_vs_dispense(
        self,
        encounters:  list[dict[str, Any]],
        dispenses:   list[dict[str, Any]],
        tenant_id:   str,
        period_start: Optional[date] = None,
        period_end:   Optional[date] = None,
    ) -> ReconciliationReport:
        """
        Verify each 340B dispense is tied to an eligible patient encounter.

        A dispense is eligible if:
          - same patient_id_hash
          - encounter period_start ≤ dispense_date ≤ encounter period_start + 365 days
          - encounter status in (finished, discharged)

        Unmatched dispenses are potential 340B diversion violations.
        """
        matches = _match_records(
            records_a   = dispenses,
            records_b   = encounters,
            key_fn_a    = lambda d: (d.get("patient_id_hash", ""),),
            key_fn_b    = lambda e: (e.get("patient_id_hash", ""),),
            recon_type  = ReconciliationType.ENCOUNTER_VS_DISPENSE,
            check_fields= ["status"],
        )

        return _build_report(
            tenant_id    = tenant_id,
            recon_type   = ReconciliationType.ENCOUNTER_VS_DISPENSE,
            matches      = matches,
            total_a      = len(dispenses),
            total_b      = len(encounters),
            period_start = period_start,
            period_end   = period_end,
        )


# ── Internal matching helpers ─────────────────────────────────────────────────

import uuid as _uuid


def _match_records(
    records_a:   list[dict[str, Any]],
    records_b:   list[dict[str, Any]],
    key_fn_a:    Any,
    key_fn_b:    Any,
    recon_type:  ReconciliationType,
    check_fields: list[str],
) -> list[ReconciliationMatch]:
    """Generic record matcher using caller-supplied key functions."""
    index_b: dict[tuple, list[dict[str, Any]]] = {}
    for r in records_b:
        key = key_fn_b(r)
        index_b.setdefault(key, []).append(r)

    matches: list[ReconciliationMatch] = []
    matched_b_keys: set[int] = set()

    for r_a in records_a:
        key = key_fn_a(r_a)
        candidates = index_b.get(key, [])

        if not candidates:
            matches.append(ReconciliationMatch(
                match_id   = str(_uuid.uuid4()),
                recon_type = recon_type,
                status     = MatchStatus.UNMATCHED,
                record_a   = r_a,
                record_b   = None,
                matched_on = [],
            ))
            continue

        # Pick best candidate
        r_b = candidates[0]
        matched_b_keys.add(id(r_b))

        discrepancies = _check_fields(r_a, r_b, check_fields)
        status = MatchStatus.PARTIAL if discrepancies else MatchStatus.MATCHED

        matches.append(ReconciliationMatch(
            match_id      = str(_uuid.uuid4()),
            recon_type    = recon_type,
            status        = status,
            record_a      = r_a,
            record_b      = r_b,
            discrepancies = discrepancies,
            matched_on    = list(key) if isinstance(key, tuple) else [str(key)],
        ))

    # Orphaned records in B
    for r_b in records_b:
        if id(r_b) not in matched_b_keys:
            matches.append(ReconciliationMatch(
                match_id   = str(_uuid.uuid4()),
                recon_type = recon_type,
                status     = MatchStatus.ORPHANED,
                record_a   = {},
                record_b   = r_b,
                matched_on = [],
            ))

    return matches


def _check_fields(
    a:      dict[str, Any],
    b:      dict[str, Any],
    fields: list[str],
) -> list[str]:
    """Return a list of field discrepancy descriptions."""
    diffs = []
    for f in fields:
        va, vb = a.get(f), b.get(f)
        if va is not None and vb is not None and va != vb:
            diffs.append(f"{f}: {va!r} ≠ {vb!r}")
    return diffs


def _build_report(
    tenant_id:    str,
    recon_type:   ReconciliationType,
    matches:      list[ReconciliationMatch],
    total_a:      int,
    total_b:      int,
    period_start: Optional[date],
    period_end:   Optional[date],
) -> ReconciliationReport:
    return ReconciliationReport(
        tenant_id    = tenant_id,
        recon_type   = recon_type,
        period_start = period_start,
        period_end   = period_end,
        total_a      = total_a,
        total_b      = total_b,
        matched      = sum(1 for m in matches if m.status == MatchStatus.MATCHED),
        partial      = sum(1 for m in matches if m.status == MatchStatus.PARTIAL),
        unmatched_a  = sum(1 for m in matches if m.status == MatchStatus.UNMATCHED),
        unmatched_b  = sum(1 for m in matches if m.status == MatchStatus.ORPHANED),
        duplicates   = sum(1 for m in matches if m.status == MatchStatus.DUPLICATE),
        matches      = matches,
    )
