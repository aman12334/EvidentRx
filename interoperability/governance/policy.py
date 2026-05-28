"""
Ingestion policy enforcement.

Defines and enforces data governance policies for the interoperability layer.
Policies are evaluated before a canonical record is written to the database.

Policy types
────────────
  DataRetentionPolicy    : Ensure records carry the correct retention tag
  PHISafetyPolicy        : Block records with unmasked PHI fields
  SourceApprovalPolicy   : Only approved source systems can write data
  RecordFreshnessPolicy  : Reject stale records (older than configured threshold)
  TenantIsolationPolicy  : Verify tenant_id matches the connector's tenant

Policy evaluation is fail-closed: if ANY policy fails, the record is blocked
and routed to the dead-letter queue with the policy violation reason.
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from interoperability.governance.access_control import validate_source_system

log = logging.getLogger("evidentrx.interop.governance.policy")

# HRSA 340B minimum audit retention: 7 years = 2555 days
_HRSA_RETENTION_DAYS = 2555


# ── Policy result ─────────────────────────────────────────────────────────────

@dataclass
class PolicyResult:
    passed:       bool
    policy_name:  str
    violations:   list[str]           = field(default_factory=list)
    detail:       str                  = ""


# ── Abstract policy ───────────────────────────────────────────────────────────

class IngestionPolicy(ABC):
    """Abstract base for all ingestion policies."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short policy identifier."""

    @abstractmethod
    def evaluate(
        self,
        record:    dict[str, Any],
        context:   dict[str, Any],
    ) -> PolicyResult:
        """
        Evaluate the policy against a canonical record.

        Parameters
        ----------
        record  : The canonical record about to be persisted
        context : Pipeline context (connector_id, tenant_id, source_system, etc.)
        """


# ── Concrete policies ─────────────────────────────────────────────────────────

class PHISafetyPolicy(IngestionPolicy):
    """
    Block records that contain unmasked PHI.

    Checks that patient_id_hash is a 32-char hex string (SHA-256 truncated),
    and that no plaintext name, DOB, SSN, or address fields are present.
    """

    _PHI_PATTERNS = [
        # SSN
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        # Full name (heuristic: "firstname lastname")
        re.compile(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b"),
        # Phone numbers
        re.compile(r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b"),
    ]

    @property
    def name(self) -> str:
        return "phi_safety"

    def evaluate(self, record: dict, context: dict) -> PolicyResult:
        violations = []

        # patient_id_hash must be 32-char hex if present
        hash_val = record.get("patient_id_hash")
        if hash_val and not re.match(r"^[0-9a-f]{32}$", str(hash_val)):
            violations.append(
                f"patient_id_hash is not a 32-char hex string: {str(hash_val)[:20]!r}"
            )

        # Check for plaintext name/DOB fields
        phi_keys = {"name", "family_name", "given_name", "dob", "ssn", "address", "phone"}
        for key in phi_keys:
            if record.get(key):
                violations.append(f"Plaintext PHI field present: {key!r}")

        # Scan string values for PHI-looking patterns
        for key, val in record.items():
            if isinstance(val, str) and len(val) > 5:
                for pattern in self._PHI_PATTERNS:
                    if pattern.search(val):
                        violations.append(f"PHI pattern detected in field {key!r}")
                        break

        return PolicyResult(
            passed      = len(violations) == 0,
            policy_name = self.name,
            violations  = violations,
        )


class SourceApprovalPolicy(IngestionPolicy):
    """Only approved source systems may write canonical records."""

    @property
    def name(self) -> str:
        return "source_approval"

    def evaluate(self, record: dict, context: dict) -> PolicyResult:
        source = record.get("source_system") or context.get("source_system", "")
        if not validate_source_system(source):
            return PolicyResult(
                passed      = False,
                policy_name = self.name,
                violations  = [f"Source system {source!r} is not on the approved whitelist"],
            )
        return PolicyResult(passed=True, policy_name=self.name)


class TenantIsolationPolicy(IngestionPolicy):
    """The record's tenant_id must match the pipeline's expected tenant."""

    @property
    def name(self) -> str:
        return "tenant_isolation"

    def evaluate(self, record: dict, context: dict) -> PolicyResult:
        record_tenant  = record.get("tenant_id", "")
        context_tenant = context.get("tenant_id", "")

        if not context_tenant:
            return PolicyResult(passed=True, policy_name=self.name)  # no expectation set

        if record_tenant != context_tenant:
            return PolicyResult(
                passed      = False,
                policy_name = self.name,
                violations  = [
                    f"Record tenant_id {record_tenant!r} ≠ pipeline tenant {context_tenant!r}"
                ],
            )
        return PolicyResult(passed=True, policy_name=self.name)


class RecordFreshnessPolicy(IngestionPolicy):
    """
    Reject records that are stale (event date too far in the past).

    Default threshold: 365 days. Override via context["freshness_days"].
    """

    @property
    def name(self) -> str:
        return "record_freshness"

    def evaluate(self, record: dict, context: dict) -> PolicyResult:
        threshold_days = int(context.get("freshness_days", 365))
        cutoff = datetime.now(tz=UTC) - timedelta(days=threshold_days)

        for date_field in ("dispense_date", "service_date", "period_start", "authored_on"):
            raw = record.get(date_field)
            if raw:
                try:
                    dt = datetime.strptime(str(raw)[:10], "%Y-%m-%d").replace(tzinfo=UTC)
                    if dt < cutoff:
                        return PolicyResult(
                            passed      = False,
                            policy_name = self.name,
                            violations  = [
                                f"{date_field} {raw!r} is older than {threshold_days}-day threshold"
                            ],
                        )
                except ValueError:
                    pass   # invalid date format caught by DataQualityScorer

        return PolicyResult(passed=True, policy_name=self.name)


class CanonicalTypePolicy(IngestionPolicy):
    """Reject records with missing or unknown canonical_type."""

    _KNOWN_TYPES = frozenset({
        "dispense", "claim", "remittance", "patient",
        "encounter", "medication_order", "organization",
        "coverage", "practitioner", "observation",
    })

    @property
    def name(self) -> str:
        return "canonical_type"

    def evaluate(self, record: dict, context: dict) -> PolicyResult:
        ctype = record.get("canonical_type")
        if not ctype:
            return PolicyResult(
                passed      = False,
                policy_name = self.name,
                violations  = ["Missing required field: canonical_type"],
            )
        if ctype not in self._KNOWN_TYPES:
            return PolicyResult(
                passed      = False,
                policy_name = self.name,
                violations  = [f"Unknown canonical_type: {ctype!r}"],
            )
        return PolicyResult(passed=True, policy_name=self.name)


# ── Policy engine ─────────────────────────────────────────────────────────────

class PolicyEngine:
    """
    Evaluates a set of policies against a canonical record.

    Fail-closed: first policy violation blocks the record.
    All policies are evaluated (not short-circuit) to collect all violations.
    """

    def __init__(self, policies: list[IngestionPolicy] | None = None) -> None:
        self._policies = policies or _DEFAULT_POLICIES

    def evaluate(
        self,
        record:  dict[str, Any],
        context: dict[str, Any],
    ) -> tuple[bool, list[PolicyResult]]:
        """
        Evaluate all policies.

        Returns (all_passed, list_of_results).
        """
        results = [p.evaluate(record, context) for p in self._policies]
        all_passed = all(r.passed for r in results)

        if not all_passed:
            violations = [v for r in results for v in r.violations]
            log.warning(
                "Policy violations for %s record [tenant=%s]: %s",
                record.get("canonical_type", "unknown"),
                context.get("tenant_id", "?"),
                "; ".join(violations[:3]),
            )

        return all_passed, results

    def evaluate_batch(
        self,
        records: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], list[PolicyResult]]]]:
        """
        Filter a batch through all policies.

        Returns (approved_records, [(rejected_record, results), ...]).
        """
        approved:  list[dict[str, Any]] = []
        rejected:  list[tuple[dict[str, Any], list[PolicyResult]]] = []

        for record in records:
            passed, results = self.evaluate(record, context)
            if passed:
                approved.append(record)
            else:
                rejected.append((record, results))

        return approved, rejected


# ── Default policy set ────────────────────────────────────────────────────────

_DEFAULT_POLICIES: list[IngestionPolicy] = [
    CanonicalTypePolicy(),
    TenantIsolationPolicy(),
    SourceApprovalPolicy(),
    PHISafetyPolicy(),
]


def get_policy_engine(
    extra_policies: list[IngestionPolicy] | None = None,
) -> PolicyEngine:
    """Return a PolicyEngine with the default policy set + any extras."""
    policies = list(_DEFAULT_POLICIES)
    if extra_policies:
        policies.extend(extra_policies)
    return PolicyEngine(policies)
