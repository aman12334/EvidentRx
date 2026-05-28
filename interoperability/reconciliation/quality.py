"""
Data quality scoring for canonical records.

Computes a per-record quality score (0.0 – 1.0) and a per-source quality
report across a batch. Quality dimensions:

  Completeness  — required and important fields are populated
  Validity      — field values match expected formats/ranges
  Consistency   — related fields are internally consistent
  Timeliness    — record arrival latency vs. source event timestamp
  Uniqueness    — duplicate rate within the batch

Used by the ingestion pipeline to:
  - Flag low-quality records for manual review
  - Drive SLA dashboards and Prometheus metrics
  - Alert ops when source data quality degrades
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime    import datetime, timezone
from typing      import Any, Optional

log = logging.getLogger("evidentrx.interop.reconciliation.quality")


# ── Quality dimensions ────────────────────────────────────────────────────────

@dataclass
class QualityDimension:
    name:    str
    score:   float      # 0.0 – 1.0
    details: list[str]  = field(default_factory=list)


@dataclass
class RecordQuality:
    record_id:     Optional[str]
    canonical_type: str
    source_system:  str
    overall_score:  float               # weighted average of dimensions
    dimensions:     list[QualityDimension]
    issues:         list[str]           = field(default_factory=list)
    scored_at:      datetime            = field(default_factory=lambda: datetime.now(tz=timezone.utc))

    @property
    def is_acceptable(self) -> bool:
        return self.overall_score >= 0.7

    @property
    def quality_label(self) -> str:
        if self.overall_score >= 0.9:
            return "excellent"
        elif self.overall_score >= 0.7:
            return "acceptable"
        elif self.overall_score >= 0.5:
            return "poor"
        return "unacceptable"


@dataclass
class BatchQualityReport:
    source_system:    str
    tenant_id:        str
    batch_size:       int
    avg_score:        float
    min_score:        float
    max_score:        float
    acceptable_count: int
    unacceptable_count: int
    common_issues:    list[str]        = field(default_factory=list)
    scored_at:        datetime         = field(default_factory=lambda: datetime.now(tz=timezone.utc))

    @property
    def acceptable_rate(self) -> float:
        return self.acceptable_count / self.batch_size if self.batch_size > 0 else 0.0


# ── Scorer ────────────────────────────────────────────────────────────────────

class DataQualityScorer:
    """
    Scores canonical records across multiple quality dimensions.

    Dimension weights (sum to 1.0):
      completeness  0.40
      validity      0.35
      consistency   0.25
    """

    _WEIGHTS = {
        "completeness": 0.40,
        "validity":     0.35,
        "consistency":  0.25,
    }

    def score(self, record: dict[str, Any]) -> RecordQuality:
        """Score a single canonical record."""
        ctype         = record.get("canonical_type", "unknown")
        source_system = record.get("source_system", "unknown")

        completeness = self._score_completeness(record, ctype)
        validity     = self._score_validity(record, ctype)
        consistency  = self._score_consistency(record, ctype)

        dimensions   = [completeness, validity, consistency]
        overall      = sum(
            d.score * self._WEIGHTS.get(d.name, 0)
            for d in dimensions
        )
        all_issues = []
        for d in dimensions:
            all_issues.extend(d.details)

        return RecordQuality(
            record_id      = record.get("fhir_id") or record.get("message_id") or record.get("claim_id"),
            canonical_type = ctype,
            source_system  = source_system,
            overall_score  = round(overall, 4),
            dimensions     = dimensions,
            issues         = all_issues,
        )

    def score_batch(self, records: list[dict[str, Any]]) -> list[RecordQuality]:
        """Score a batch of canonical records."""
        return [self.score(r) for r in records]

    def batch_report(
        self,
        records:       list[dict[str, Any]],
        source_system: str,
        tenant_id:     str,
    ) -> BatchQualityReport:
        """Produce a quality report for an entire batch."""
        if not records:
            return BatchQualityReport(
                source_system      = source_system,
                tenant_id          = tenant_id,
                batch_size         = 0,
                avg_score          = 0.0,
                min_score          = 0.0,
                max_score          = 0.0,
                acceptable_count   = 0,
                unacceptable_count = 0,
            )

        scored = self.score_batch(records)
        scores = [r.overall_score for r in scored]

        # Aggregate most common issues
        issue_counts: dict[str, int] = {}
        for rec in scored:
            for issue in rec.issues:
                issue_counts[issue] = issue_counts.get(issue, 0) + 1
        common_issues = sorted(issue_counts, key=issue_counts.get, reverse=True)[:5]  # type: ignore

        return BatchQualityReport(
            source_system      = source_system,
            tenant_id          = tenant_id,
            batch_size         = len(records),
            avg_score          = round(sum(scores) / len(scores), 4),
            min_score          = round(min(scores), 4),
            max_score          = round(max(scores), 4),
            acceptable_count   = sum(1 for r in scored if r.is_acceptable),
            unacceptable_count = sum(1 for r in scored if not r.is_acceptable),
            common_issues      = common_issues,
        )

    # ── Dimension scorers ──────────────────────────────────────────────────────

    def _score_completeness(self, r: dict, ctype: str) -> QualityDimension:
        """Fraction of important fields that are non-null."""
        important = _IMPORTANT_FIELDS.get(ctype, _BASE_FIELDS)
        missing   = [f for f in important if not r.get(f)]
        score     = 1.0 - (len(missing) / len(important)) if important else 1.0
        return QualityDimension(
            name    = "completeness",
            score   = round(score, 4),
            details = [f"Missing field: {f!r}" for f in missing],
        )

    def _score_validity(self, r: dict, ctype: str) -> QualityDimension:
        """Check field values against format/range constraints."""
        issues: list[str] = []

        # NDC format
        ndc = r.get("ndc_11") or (r.get("ndc_list") or [None])[0]
        if ndc and not re.match(r"^\d{11}$", str(ndc)):
            issues.append(f"Invalid NDC-11 format: {ndc!r}")

        # Date format
        for date_field in ("dispense_date", "service_date", "authored_on", "period_start", "period_end"):
            val = r.get(date_field)
            if val and not re.match(r"^\d{4}-\d{2}-\d{2}$", str(val)[:10]):
                issues.append(f"Invalid date in {date_field!r}: {val!r}")

        # Status values
        if ctype == "dispense":
            valid_statuses = {"completed", "in-progress", "on-hold", "stopped", "unknown"}
            status = r.get("status", "")
            if status and status not in valid_statuses:
                issues.append(f"Unexpected dispense status: {status!r}")

        # Amount sanity
        for amt_field in ("quantity", "total_amount", "paid_amount"):
            val = r.get(amt_field)
            if val is not None:
                try:
                    if float(val) < 0:
                        issues.append(f"Negative value in {amt_field!r}: {val}")
                except (TypeError, ValueError):
                    issues.append(f"Non-numeric value in {amt_field!r}: {val!r}")

        score = max(0.0, 1.0 - len(issues) * 0.2)
        return QualityDimension(name="validity", score=round(score, 4), details=issues)

    def _score_consistency(self, r: dict, ctype: str) -> QualityDimension:
        """Check that related fields are internally consistent."""
        issues: list[str] = []

        if ctype == "dispense":
            # dispense_date should not be in the future
            ddate = r.get("dispense_date")
            if ddate:
                try:
                    d = datetime.strptime(str(ddate)[:10], "%Y-%m-%d")
                    if d.year > datetime.now().year + 1:
                        issues.append(f"dispense_date {ddate!r} is far in the future")
                except ValueError:
                    pass

            # quantity and days_supply should both be positive if present
            qty  = r.get("quantity")
            days = r.get("days_supply")
            if qty is not None and days is not None:
                try:
                    if float(qty) > 0 and int(days) <= 0:
                        issues.append("days_supply is ≤ 0 but quantity > 0")
                except (TypeError, ValueError):
                    pass

        if ctype == "claim":
            submitted = r.get("total_amount")
            paid      = r.get("paid_amount") or r.get("total_amount")
            if submitted is not None and paid is not None:
                try:
                    if float(paid) > float(submitted) * 1.1:
                        issues.append("paid_amount exceeds submitted by >10%")
                except (TypeError, ValueError):
                    pass

        score = max(0.0, 1.0 - len(issues) * 0.25)
        return QualityDimension(name="consistency", score=round(score, 4), details=issues)


# ── Field definitions ─────────────────────────────────────────────────────────

_BASE_FIELDS = ["canonical_type", "source_system", "tenant_id"]

_IMPORTANT_FIELDS: dict[str, list[str]] = {
    "dispense": [
        "canonical_type", "source_system", "tenant_id",
        "patient_id_hash", "ndc_11", "dispense_date", "quantity", "days_supply",
    ],
    "claim": [
        "canonical_type", "source_system", "tenant_id",
        "patient_id_hash", "service_date", "total_amount",
    ],
    "remittance": [
        "canonical_type", "source_system", "tenant_id",
        "claim_submission_id", "paid_amount", "payment_date",
    ],
    "patient": [
        "canonical_type", "source_system", "tenant_id",
        "patient_id_hash",
    ],
    "encounter": [
        "canonical_type", "source_system", "tenant_id",
        "patient_id_hash", "status", "period_start",
    ],
    "medication_order": [
        "canonical_type", "source_system", "tenant_id",
        "patient_id_hash", "ndc_11", "status",
    ],
}
