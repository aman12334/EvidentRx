"""
Duplicate detection for canonical records.

Detects and suppresses duplicate records that arrive from multiple sources
or on message replay. Uses a combination of:
  1. Exact checksum match (SHA-256 of canonical JSON)
  2. Fuzzy business-key match (patient + NDC + date window)

Deduplication is performed before persistence to avoid polluting the
canonical store with redundant records. Suppressed duplicates are logged
for auditability but not written.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime    import datetime, timedelta, timezone
from enum        import Enum
from typing      import Any, Optional

log = logging.getLogger("evidentrx.interop.reconciliation.deduplication")


class DuplicateReason(str, Enum):
    EXACT_CHECKSUM   = "exact_checksum"    # identical canonical JSON
    BUSINESS_KEY     = "business_key"      # same patient/NDC/date
    CROSS_SOURCE     = "cross_source"      # same record from FHIR + HL7
    REPLAY           = "replay"            # re-processed from raw store


@dataclass
class DuplicateResult:
    is_duplicate:   bool
    reason:         Optional[DuplicateReason]
    original_id:    Optional[str]           # checksum or key of the first-seen record
    detail:         str                     = ""


class DeduplicationEngine:
    """
    In-memory duplicate detection with configurable TTL.

    For production, the seen_checksums set should be backed by Redis
    (for distributed deduplication across multiple pipeline workers).

    TTL-based expiry prevents unbounded memory growth on long-running pipelines.
    """

    def __init__(
        self,
        checksum_ttl_hours:    int   = 24,
        business_key_ttl_hours: int  = 6,
        fuzzy_date_window_days: int  = 1,
    ) -> None:
        self._checksums:    dict[str, datetime] = {}      # checksum → first_seen
        self._business_keys: dict[str, datetime] = {}     # key → first_seen
        self._checksum_ttl = timedelta(hours=checksum_ttl_hours)
        self._bkey_ttl     = timedelta(hours=business_key_ttl_hours)
        self._date_window  = timedelta(days=fuzzy_date_window_days)
        self._total_seen   = 0
        self._total_dupes  = 0

    # ── Public API ─────────────────────────────────────────────────────────────

    def check(self, record: dict[str, Any]) -> DuplicateResult:
        """
        Check a canonical record for duplicates.

        Call this BEFORE persisting the record. If is_duplicate=True,
        discard the record (or route to audit log).
        """
        self._evict_expired()
        self._total_seen += 1

        # 1. Exact checksum
        checksum = _compute_checksum(record)
        if checksum in self._checksums:
            self._total_dupes += 1
            return DuplicateResult(
                is_duplicate = True,
                reason       = DuplicateReason.EXACT_CHECKSUM,
                original_id  = checksum,
                detail       = f"Checksum {checksum[:12]} seen at {self._checksums[checksum].isoformat()}",
            )

        # 2. Business key (fuzzy)
        bkey = _business_key(record)
        if bkey and bkey in self._business_keys:
            self._total_dupes += 1
            return DuplicateResult(
                is_duplicate = True,
                reason       = DuplicateReason.BUSINESS_KEY,
                original_id  = bkey,
                detail       = f"Business key {bkey[:20]} matches record seen at {self._business_keys[bkey].isoformat()}",
            )

        # Not a duplicate — register
        now = datetime.now(tz=timezone.utc)
        self._checksums[checksum] = now
        if bkey:
            self._business_keys[bkey] = now

        return DuplicateResult(is_duplicate=False, reason=None, original_id=None)

    def check_batch(
        self,
        records: list[dict[str, Any]],
    ) -> list[tuple[dict[str, Any], DuplicateResult]]:
        """Check a batch of records. Returns (record, result) pairs."""
        return [(r, self.check(r)) for r in records]

    def filter_batch(
        self,
        records: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """
        Split a batch into (unique_records, duplicate_records).

        Unique records are safe to persist; duplicates should be discarded
        or routed to the audit log.
        """
        unique:     list[dict[str, Any]] = []
        duplicates: list[dict[str, Any]] = []

        for record, result in self.check_batch(records):
            if result.is_duplicate:
                log.debug(
                    "Dedup: skipping duplicate [%s] %s",
                    result.reason.value if result.reason else "?",
                    result.detail,
                )
                duplicates.append(record)
            else:
                unique.append(record)

        if duplicates:
            log.info(
                "Dedup: filtered %d/%d duplicates from batch",
                len(duplicates), len(records),
            )

        return unique, duplicates

    # ── TTL eviction ───────────────────────────────────────────────────────────

    def _evict_expired(self) -> None:
        now     = datetime.now(tz=timezone.utc)
        expired = [k for k, t in self._checksums.items() if now - t > self._checksum_ttl]
        for k in expired:
            del self._checksums[k]

        expired_bkeys = [k for k, t in self._business_keys.items() if now - t > self._bkey_ttl]
        for k in expired_bkeys:
            del self._business_keys[k]

    # ── Stats ──────────────────────────────────────────────────────────────────

    @property
    def duplicate_rate(self) -> float:
        return self._total_dupes / self._total_seen if self._total_seen > 0 else 0.0

    @property
    def cache_size(self) -> dict[str, int]:
        return {
            "checksums":    len(self._checksums),
            "business_keys": len(self._business_keys),
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _compute_checksum(record: dict[str, Any]) -> str:
    """SHA-256 checksum of the canonical record (stable across replays)."""
    # Exclude lineage/replay metadata from checksum computation
    clean = {
        k: v for k, v in record.items()
        if not k.startswith("_")  # strip _mapped_at, _replayed, etc.
    }
    payload = json.dumps(clean, sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()


def _business_key(record: dict[str, Any]) -> Optional[str]:
    """
    Derive a fuzzy business key for duplicate detection.

    Key is composed of: canonical_type + patient_id_hash + NDC + date (truncated)
    Returns None for record types without enough fields to form a meaningful key.
    """
    ctype = record.get("canonical_type", "")

    if ctype == "dispense":
        patient  = record.get("patient_id_hash", "")
        ndc      = record.get("ndc_11", "")
        date_str = (record.get("dispense_date") or "")[:10]
        if patient and ndc and date_str:
            return f"dispense:{patient}:{ndc}:{date_str}"

    elif ctype == "claim":
        patient  = record.get("patient_id_hash", "")
        date_str = (record.get("service_date") or "")[:10]
        ndc_list = record.get("ndc_list") or []
        ndc      = ndc_list[0] if ndc_list else ""
        if patient and date_str:
            return f"claim:{patient}:{ndc}:{date_str}"

    elif ctype == "medication_order":
        patient = record.get("patient_id_hash", "")
        ndc     = record.get("ndc_11", "")
        date_str= (record.get("authored_on") or "")[:10]
        if patient and ndc:
            return f"order:{patient}:{ndc}:{date_str}"

    return None
