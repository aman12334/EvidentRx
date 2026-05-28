"""
Policy-aware investigation intelligence.

Extends investigation workflows with regulation-aware reasoning,
policy citation support, and compliance rationale linking. Each
investigation can be annotated with the specific regulatory documents
that governed its execution — enabling temporal policy applicability
tracking and explainable citation generation.

Design constraints
──────────────────
- Policy citations are READ-ONLY annotations on investigations
- Citations do not modify or override deterministic compliance logic
- All citations are linked to specific, versioned document versions
- Historical investigations can be replayed with the policy set that
  was active at the time (temporal applicability)
- Citation confidence is always explicit and explainable
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from regulatory.ingestion.models import PolicyDomain, RegulatoryDocument

log = logging.getLogger("evidentrx.regulatory.intelligence.investigation")


class CitationStrength(str, Enum):
    DIRECT     = "direct"      # regulation explicitly governs this case
    INFERRED   = "inferred"    # regulation applies by scope/domain overlap
    CONTEXTUAL = "contextual"  # regulation provides relevant background
    HISTORICAL = "historical"  # regulation was active at time but may not be current


@dataclass
class PolicyCitation:
    """
    A link from an investigation to a specific regulatory document version.

    Each citation includes the section of the document that is relevant,
    the rationale for the linkage, and whether this citation was asserted
    by a human analyst or inferred automatically.
    """
    citation_id:      str
    investigation_id: str
    doc_id:           str
    doc_version:      str
    doc_title:        str
    section:          str                # specific section cited
    excerpt:          str                # relevant text excerpt (≤500 chars)
    rationale:        str                # why this doc applies to this investigation
    strength:         CitationStrength
    domain:           PolicyDomain | None
    asserted_by:      str                # analyst_id or "system"
    asserted_at:      datetime           = field(default_factory=lambda: datetime.now(tz=UTC))
    effective_at:     str | None      = None   # date when this regulation was active
    confidence:       float              = 1.0
    human_verified:   bool               = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "citation_id":      self.citation_id,
            "investigation_id": self.investigation_id,
            "doc_id":           self.doc_id,
            "doc_version":      self.doc_version,
            "doc_title":        self.doc_title,
            "section":          self.section,
            "excerpt":          self.excerpt,
            "rationale":        self.rationale,
            "strength":         self.strength.value,
            "domain":           self.domain.value if self.domain else None,
            "asserted_by":      self.asserted_by,
            "asserted_at":      self.asserted_at.isoformat(),
            "confidence":       round(self.confidence, 3),
            "human_verified":   self.human_verified,
        }


@dataclass
class InvestigationPolicyContext:
    """
    The complete regulatory context for one investigation.

    Captures the policy documents that were active and relevant at the
    time of the investigation, enabling historical replay with correct
    policy applicability.
    """
    context_id:       str
    investigation_id: str
    tenant_id:        str
    context_as_of:    datetime        # point-in-time snapshot of applicable policies
    citations:        list[PolicyCitation]
    applicable_domains: list[PolicyDomain]
    escalation_policy_notes: str      = ""
    compliance_rationale:    str      = ""    # synthesised compliance basis
    created_at:       datetime        = field(default_factory=lambda: datetime.now(tz=UTC))
    created_by:       str             = "system"
    metadata:         dict[str, Any]  = field(default_factory=dict)

    @property
    def direct_citations(self) -> list[PolicyCitation]:
        return [c for c in self.citations if c.strength == CitationStrength.DIRECT]

    @property
    def inferred_citations(self) -> list[PolicyCitation]:
        return [c for c in self.citations if c.strength == CitationStrength.INFERRED]

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_id":              self.context_id,
            "investigation_id":        self.investigation_id,
            "tenant_id":               self.tenant_id,
            "context_as_of":           self.context_as_of.isoformat(),
            "direct_citation_count":   len(self.direct_citations),
            "inferred_citation_count": len(self.inferred_citations),
            "applicable_domains":      [d.value for d in self.applicable_domains],
            "escalation_policy_notes": self.escalation_policy_notes,
            "compliance_rationale":    self.compliance_rationale,
            "citations":               [c.to_dict() for c in self.citations],
        }


class PolicyAwareInvestigationService:
    """
    Links investigations to applicable regulatory documents.

    All matching is deterministic and domain-based. No LLM inference
    is used — matching is based on policy domain overlap between the
    investigation's compliance domain and active regulatory documents.
    """

    def __init__(self) -> None:
        # context_id → InvestigationPolicyContext
        self._contexts: dict[str, InvestigationPolicyContext] = {}
        # citation_id → PolicyCitation
        self._citations: dict[str, PolicyCitation] = {}

    def build_context(
        self,
        investigation_id: str,
        tenant_id:        str,
        investigation_domains: list[PolicyDomain],
        active_documents: list[RegulatoryDocument],
        created_by:       str               = "system",
        as_of:            datetime | None = None,
    ) -> InvestigationPolicyContext:
        """
        Build the policy context for an investigation.

        Matches active regulatory documents to the investigation's compliance
        domains and generates appropriate citations.
        """
        now = as_of or datetime.now(tz=UTC)
        citations: list[PolicyCitation] = []

        for doc in active_documents:
            overlap_domains = [d for d in doc.domains if d in investigation_domains]
            if not overlap_domains:
                continue

            # Determine citation strength
            strength = (
                CitationStrength.DIRECT
                if len(overlap_domains) >= 2 or PolicyDomain.DRUG_340B in overlap_domains
                else CitationStrength.INFERRED
            )

            # Generate excerpt from document summary or raw_text
            excerpt_source = doc.summary or doc.raw_text
            excerpt = excerpt_source[:400] if excerpt_source else f"[{doc.title}]"

            citation = PolicyCitation(
                citation_id      = str(uuid.uuid4()),
                investigation_id = investigation_id,
                doc_id           = doc.doc_id,
                doc_version      = doc.version,
                doc_title        = doc.title,
                section          = "full document",
                excerpt          = excerpt,
                rationale        = (
                    f"Document covers {', '.join(d.value for d in overlap_domains)} "
                    f"which overlaps with this investigation's compliance domain."
                ),
                strength         = strength,
                domain           = overlap_domains[0],
                asserted_by      = created_by,
                effective_at     = doc.attribution.effective_date,
                confidence       = 0.90 if strength == CitationStrength.DIRECT else 0.65,
            )
            self._citations[citation.citation_id] = citation
            citations.append(citation)

        # Build rationale from top citations
        rationale_parts = [
            f"{c.doc_title} v{c.doc_version} ({c.strength.value})"
            for c in citations[:3]
        ]
        rationale = (
            "This investigation is governed by: " + "; ".join(rationale_parts)
            if rationale_parts
            else "No active regulatory documents found for this investigation's domains."
        )

        context = InvestigationPolicyContext(
            context_id          = str(uuid.uuid4()),
            investigation_id    = investigation_id,
            tenant_id           = tenant_id,
            context_as_of       = now,
            citations           = citations,
            applicable_domains  = investigation_domains,
            compliance_rationale = rationale,
            created_by          = created_by,
        )
        self._contexts[context.context_id] = context
        log.info(
            "PolicyAwareInvestigationService: built context for investigation %s "
            "— %d citations across %d domains",
            investigation_id[:8], len(citations), len(investigation_domains),
        )
        return context

    def add_manual_citation(
        self,
        context_id:       str,
        doc_id:           str,
        doc_version:      str,
        doc_title:        str,
        section:          str,
        excerpt:          str,
        rationale:        str,
        asserted_by:      str,
        domain:           PolicyDomain | None = None,
    ) -> PolicyCitation:
        """Allow an analyst to manually assert a citation."""
        ctx = self._contexts.get(context_id)
        if ctx is None:
            raise InvestigationIntelligenceError(f"Context {context_id} not found")

        citation = PolicyCitation(
            citation_id      = str(uuid.uuid4()),
            investigation_id = ctx.investigation_id,
            doc_id           = doc_id,
            doc_version      = doc_version,
            doc_title        = doc_title,
            section          = section,
            excerpt          = excerpt[:500],
            rationale        = rationale,
            strength         = CitationStrength.DIRECT,
            domain           = domain,
            asserted_by      = asserted_by,
            confidence       = 1.0,
            human_verified   = True,
        )
        self._citations[citation.citation_id] = citation
        ctx.citations.append(citation)
        return citation

    def get_context(self, context_id: str) -> InvestigationPolicyContext | None:
        return self._contexts.get(context_id)

    def get_contexts_for_investigation(
        self,
        investigation_id: str,
    ) -> list[InvestigationPolicyContext]:
        return [
            c for c in self._contexts.values()
            if c.investigation_id == investigation_id
        ]

    def temporal_policy_check(
        self,
        investigation_id: str,
        as_of:            datetime,
        active_documents: list[RegulatoryDocument],
        investigation_domains: list[PolicyDomain],
        tenant_id:        str,
    ) -> InvestigationPolicyContext:
        """
        Replay the policy context as it was at a specific historical point in time.

        Filters the document list to only those that were INDEXED (active)
        at as_of, enabling historical replay for audit purposes.
        """
        historically_active = [
            d for d in active_documents
            if d.ingested_at <= as_of
            and (d.indexed_at is None or d.indexed_at <= as_of)
        ]
        return self.build_context(
            investigation_id       = investigation_id,
            tenant_id              = tenant_id,
            investigation_domains  = investigation_domains,
            active_documents       = historically_active,
            created_by             = "temporal_replay",
            as_of                  = as_of,
        )


# ── Exceptions ─────────────────────────────────────────────────────────────────

class InvestigationIntelligenceError(Exception):
    pass


# ── Singleton ──────────────────────────────────────────────────────────────────

_service: PolicyAwareInvestigationService | None = None


def get_investigation_intelligence() -> PolicyAwareInvestigationService:
    global _service
    if _service is None:
        _service = PolicyAwareInvestigationService()
    return _service
