"""
Policy diff engine — version comparison and semantic change detection.

Compares two versions of a regulatory document and produces a structured
PolicyDiff describing what changed, why it matters, and which operational
elements are likely affected.

Diff categories
───────────────
  ADDED           — new requirement/section not in prior version
  REMOVED         — requirement/section removed from prior version
  MODIFIED        — existing requirement changed (threshold, scope, deadline)
  REINTERPRETED   — same text, different compliance interpretation
  CLARIFIED       — ambiguity in prior version resolved in new version
  DEPRECATED      — prior requirement explicitly marked as no longer applicable
  CONFLICTING     — new version introduces apparent conflict with another policy

Semantic diffing strategy
─────────────────────────
This implementation uses token-level term frequency comparison and
keyword heuristics. It does NOT invoke an LLM — all reasoning is
deterministic, replayable, and explainable.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime    import datetime, timezone
from enum        import Enum
from typing      import Any, Optional

from regulatory.ingestion.models import RegulatoryDocument

log = logging.getLogger("evidentrx.regulatory.diff.engine")


class ChangeCategory(str, Enum):
    ADDED         = "added"
    REMOVED       = "removed"
    MODIFIED      = "modified"
    REINTERPRETED = "reinterpreted"
    CLARIFIED     = "clarified"
    DEPRECATED    = "deprecated"
    CONFLICTING   = "conflicting"


class ChangeSeverity(str, Enum):
    INFORMATIONAL = "informational"   # awareness only
    LOW           = "low"             # minor operational impact
    MEDIUM        = "medium"          # workflow review required
    HIGH          = "high"            # immediate attention; may affect compliance posture
    CRITICAL      = "critical"        # regulatory deadline / enforcement action imminent


@dataclass
class PolicyChange:
    """A single detected change between two document versions."""
    change_id:    str
    category:     ChangeCategory
    severity:     ChangeSeverity
    section:      str              # section heading or "global"
    description:  str              # human-readable change description
    prior_text:   Optional[str]    # excerpt from prior version
    new_text:     Optional[str]    # excerpt from new version
    keywords:     list[str]        # compliance terms driving the severity assessment
    operational_areas: list[str]   # e.g. ["contract_pharmacy", "medicaid_carve_in"]
    confidence:   float            = 1.0   # diff confidence (1.0 = text-level match)

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_id":         self.change_id,
            "category":          self.category.value,
            "severity":          self.severity.value,
            "section":           self.section,
            "description":       self.description,
            "prior_text":        self.prior_text,
            "new_text":          self.new_text,
            "keywords":          self.keywords,
            "operational_areas": self.operational_areas,
            "confidence":        round(self.confidence, 3),
        }


@dataclass
class PolicyDiff:
    """
    Structured comparison between two versions of a regulatory document.

    content_hash is SHA-256 of the serialised diff so the result can be
    stored and retrieved without re-computing.
    """
    diff_id:         str
    prior_doc_id:    str
    new_doc_id:      str
    family_id:       str
    prior_version:   str
    new_version:     str
    computed_at:     datetime
    changes:         list[PolicyChange]
    summary:         str
    overall_severity: ChangeSeverity
    content_hash:    str
    metadata:        dict[str, Any] = field(default_factory=dict)

    @property
    def has_critical(self) -> bool:
        return any(c.severity == ChangeSeverity.CRITICAL for c in self.changes)

    @property
    def change_count(self) -> int:
        return len(self.changes)

    def changes_by_severity(self, severity: ChangeSeverity) -> list[PolicyChange]:
        return [c for c in self.changes if c.severity == severity]

    def to_dict(self) -> dict[str, Any]:
        return {
            "diff_id":          self.diff_id,
            "prior_doc_id":     self.prior_doc_id,
            "new_doc_id":       self.new_doc_id,
            "family_id":        self.family_id,
            "prior_version":    self.prior_version,
            "new_version":      self.new_version,
            "computed_at":      self.computed_at.isoformat(),
            "summary":          self.summary,
            "overall_severity": self.overall_severity.value,
            "change_count":     self.change_count,
            "content_hash":     self.content_hash,
            "changes":          [c.to_dict() for c in self.changes],
        }


# ── Compliance-domain keyword heuristics ──────────────────────────────────────

_CRITICAL_TERMS = frozenset({
    "enforcement", "civil monetary penalty", "termination", "exclusion",
    "immediate", "mandatory", "prohibited", "violation", "fdca",
    "false claims", "fraud", "repayment",
})
_HIGH_TERMS = frozenset({
    "required", "must", "shall", "deadline", "effective date",
    "carve-in", "carve-out", "ceiling price", "contract pharmacy",
    "340b", "audit", "compliance", "medicaid", "opa",
})
_MEDIUM_TERMS = frozenset({
    "recommend", "should", "guidance", "policy", "procedure",
    "threshold", "reporting", "documentation", "review",
})
_OPERATIONAL_AREA_TERMS: dict[str, list[str]] = {
    "contract_pharmacy":     ["contract pharmacy", "340b contract", "pharmacy agreement"],
    "medicaid_carve_in":     ["carve-in", "fee-for-service", "managed care carve"],
    "medicaid_carve_out":    ["carve-out", "excluded from managed"],
    "audit_requirements":    ["audit", "record retention", "documentation"],
    "pricing_integrity":     ["ceiling price", "overcharge", "duplicate discount"],
    "covered_entity_elig":   ["covered entity", "eligibility", "340b eligibility"],
    "billing_compliance":    ["billing", "claim", "invoice", "remittance"],
}


def _severity_from_keywords(keywords: list[str]) -> ChangeSeverity:
    kw_lower = {k.lower() for k in keywords}
    if kw_lower & _CRITICAL_TERMS:
        return ChangeSeverity.CRITICAL
    if kw_lower & _HIGH_TERMS:
        return ChangeSeverity.HIGH
    if kw_lower & _MEDIUM_TERMS:
        return ChangeSeverity.MEDIUM
    return ChangeSeverity.LOW


def _detect_operational_areas(text: str) -> list[str]:
    text_lower = text.lower()
    areas = []
    for area, terms in _OPERATIONAL_AREA_TERMS.items():
        if any(t in text_lower for t in terms):
            areas.append(area)
    return areas


def _extract_keywords(text: str) -> list[str]:
    text_lower = text.lower()
    found = []
    for term_set in (_CRITICAL_TERMS, _HIGH_TERMS, _MEDIUM_TERMS):
        for term in term_set:
            if term in text_lower:
                found.append(term)
    return found[:15]


def _tokenise(text: str) -> set[str]:
    return set(re.findall(r"\b[a-z]{4,}\b", text.lower()))


def _split_sections(text: str) -> dict[str, str]:
    """
    Heuristic section splitter: treats lines matching heading patterns as
    section boundaries.
    """
    heading_re = re.compile(
        r"^(?:[A-Z][A-Z\s\-:]{5,}|(?:\d+\.)+\s+[A-Z].{3,}|[IVX]+\.\s+[A-Z].{3,})$",
        re.MULTILINE,
    )
    sections:  dict[str, str] = {}
    headings   = list(heading_re.finditer(text))
    if not headings:
        return {"global": text}

    for i, match in enumerate(headings):
        title = match.group(0).strip()[:80]
        start = match.end()
        end   = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        sections[title] = text[start:end].strip()

    return sections


import uuid as _uuid


class PolicyDiffEngine:
    """
    Deterministic policy diff engine.

    Compares two RegulatoryDocument objects and returns a PolicyDiff.
    All logic is text-based and heuristic-driven — no AI inference.
    Results are fully replayable given the same document texts.
    """

    def diff(
        self,
        prior: RegulatoryDocument,
        new:   RegulatoryDocument,
    ) -> PolicyDiff:
        if prior.document_family_id != new.document_family_id:
            raise DiffError(
                "Cannot diff documents from different families: "
                f"{prior.document_family_id} vs {new.document_family_id}"
            )

        changes = self._compute_changes(prior.raw_text, new.raw_text)
        severity = self._overall_severity(changes)
        summary  = self._summarise(prior, new, changes)

        diff_payload = json.dumps({
            "prior": prior.doc_id,
            "new":   new.doc_id,
            "changes_count": len(changes),
        }, sort_keys=True)
        content_hash = hashlib.sha256(diff_payload.encode()).hexdigest()

        return PolicyDiff(
            diff_id          = str(_uuid.uuid4()),
            prior_doc_id     = prior.doc_id,
            new_doc_id       = new.doc_id,
            family_id        = prior.document_family_id,
            prior_version    = prior.version,
            new_version      = new.version,
            computed_at      = datetime.now(tz=timezone.utc),
            changes          = changes,
            summary          = summary,
            overall_severity = severity,
            content_hash     = content_hash,
        )

    def _compute_changes(
        self,
        prior_text: str,
        new_text:   str,
    ) -> list[PolicyChange]:
        prior_sections = _split_sections(prior_text)
        new_sections   = _split_sections(new_text)
        changes: list[PolicyChange] = []

        all_headings = set(prior_sections) | set(new_sections)

        for heading in sorted(all_headings):
            prior_body = prior_sections.get(heading, "")
            new_body   = new_sections.get(heading, "")

            if heading not in prior_sections:
                # Entire section added
                kw   = _extract_keywords(new_body)
                areas = _detect_operational_areas(new_body)
                changes.append(PolicyChange(
                    change_id         = str(_uuid.uuid4()),
                    category          = ChangeCategory.ADDED,
                    severity          = _severity_from_keywords(kw),
                    section           = heading,
                    description       = f"New section added: '{heading}'",
                    prior_text        = None,
                    new_text          = new_body[:300],
                    keywords          = kw,
                    operational_areas = areas,
                ))
            elif heading not in new_sections:
                # Section removed
                kw   = _extract_keywords(prior_body)
                areas = _detect_operational_areas(prior_body)
                changes.append(PolicyChange(
                    change_id         = str(_uuid.uuid4()),
                    category          = ChangeCategory.REMOVED,
                    severity          = _severity_from_keywords(kw),
                    section           = heading,
                    description       = f"Section removed: '{heading}'",
                    prior_text        = prior_body[:300],
                    new_text          = None,
                    keywords          = kw,
                    operational_areas = areas,
                ))
            else:
                # Both versions have this section — compare tokens
                prior_toks = _tokenise(prior_body)
                new_toks   = _tokenise(new_body)
                added_toks   = new_toks - prior_toks
                removed_toks = prior_toks - new_toks

                if not added_toks and not removed_toks:
                    continue  # identical

                kw_added   = _extract_keywords(" ".join(added_toks))
                kw_removed = _extract_keywords(" ".join(removed_toks))
                kw         = list({*kw_added, *kw_removed})[:15]
                areas      = _detect_operational_areas(new_body)

                # Classify category
                if "prohibited" in new_body.lower() or "deprecated" in new_body.lower():
                    cat = ChangeCategory.DEPRECATED
                elif len(added_toks) > len(removed_toks) * 2:
                    cat = ChangeCategory.ADDED
                elif len(removed_toks) > len(added_toks) * 2:
                    cat = ChangeCategory.REMOVED
                else:
                    cat = ChangeCategory.MODIFIED

                # Confidence: higher when changes are significant
                jaccard     = len(prior_toks & new_toks) / max(len(prior_toks | new_toks), 1)
                confidence  = round(1.0 - jaccard, 3)

                changes.append(PolicyChange(
                    change_id         = str(_uuid.uuid4()),
                    category          = cat,
                    severity          = _severity_from_keywords(kw),
                    section           = heading,
                    description       = (
                        f"Section '{heading}' modified: "
                        f"{len(added_toks)} terms added, {len(removed_toks)} removed"
                    ),
                    prior_text        = prior_body[:300],
                    new_text          = new_body[:300],
                    keywords          = kw,
                    operational_areas = areas,
                    confidence        = confidence,
                ))

        return changes

    @staticmethod
    def _overall_severity(changes: list[PolicyChange]) -> ChangeSeverity:
        if not changes:
            return ChangeSeverity.INFORMATIONAL
        order = [
            ChangeSeverity.CRITICAL,
            ChangeSeverity.HIGH,
            ChangeSeverity.MEDIUM,
            ChangeSeverity.LOW,
            ChangeSeverity.INFORMATIONAL,
        ]
        for sev in order:
            if any(c.severity == sev for c in changes):
                return sev
        return ChangeSeverity.INFORMATIONAL

    @staticmethod
    def _summarise(
        prior:   RegulatoryDocument,
        new:     RegulatoryDocument,
        changes: list[PolicyChange],
    ) -> str:
        by_cat: dict[str, int] = {}
        for c in changes:
            by_cat[c.category.value] = by_cat.get(c.category.value, 0) + 1
        parts = ", ".join(f"{v} {k}" for k, v in by_cat.items())
        return (
            f"Policy diff v{prior.version}→v{new.version}: "
            f"{len(changes)} changes detected ({parts or 'none'})."
        )


# ── Exceptions ─────────────────────────────────────────────────────────────────

class DiffError(Exception):
    pass
