"""
Tenant-aware search index.

Provides an in-process inverted index for full-text and faceted search
across investigations, covered entities, workflow templates, and audit
records. All index reads and writes are tenant-isolated — a query can
never return documents belonging to a different tenant.

Architecture note
─────────────────
This module implements a lightweight in-memory index suitable for the
application tier. In production, the TenantAwareIndex delegates heavy
queries to an Elasticsearch / OpenSearch cluster; this implementation
is used for tests and single-tenant deployments.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

log = logging.getLogger("evidentrx.saas.search.index")


class DocumentType(str, Enum):
    INVESTIGATION  = "investigation"
    COVERED_ENTITY = "covered_entity"
    WORKFLOW_TEMPLATE = "workflow_template"
    AUDIT_RECORD   = "audit_record"
    ORG            = "org"
    PLAYBOOK_ENTRY = "playbook_entry"


@dataclass
class SearchDocument:
    """
    An indexed unit of content within a tenant's search space.

    Fields
    ──────
    doc_id       — stable identifier (maps back to the source entity)
    tenant_id    — hard partition key; never omitted
    doc_type     — type discriminator for faceted filtering
    title        — primary searchable text (highest weight)
    body         — secondary searchable text
    tags         — facet values (exact-match filtering)
    org_id       — optional org scope (used for RBAC-constrained search)
    score_boost  — static relevance multiplier (default 1.0)
    indexed_at   — when the document was last indexed
    fields       — arbitrary additional fields for display / sorting
    """
    doc_id:      str
    tenant_id:   str
    doc_type:    DocumentType
    title:       str
    body:        str                    = ""
    tags:        list[str]             = field(default_factory=list)
    org_id:      str | None         = None
    score_boost: float                 = 1.0
    indexed_at:  datetime              = field(default_factory=lambda: datetime.now(tz=UTC))
    fields:      dict[str, Any]        = field(default_factory=dict)

    def searchable_text(self) -> str:
        return f"{self.title} {self.body} {' '.join(self.tags)}".lower()

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id":     self.doc_id,
            "tenant_id":  self.tenant_id,
            "doc_type":   self.doc_type.value,
            "title":      self.title,
            "tags":       self.tags,
            "org_id":     self.org_id,
            "indexed_at": self.indexed_at.isoformat(),
            **self.fields,
        }


@dataclass
class SearchResult:
    document:  SearchDocument
    score:     float
    highlights: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.document.to_dict(),
            "_score":      round(self.score, 4),
            "_highlights": self.highlights,
        }


class SearchIndex:
    """
    In-memory full-text + faceted index for a single logical namespace.

    Scoring: TF-IDF approximation using term frequency in document text,
    multiplied by the document's score_boost.
    Title matches receive a 2× weight over body matches.
    """

    def __init__(self) -> None:
        # doc_id → SearchDocument
        self._documents: dict[str, SearchDocument] = {}
        # term → set of doc_ids  (inverted index)
        self._inverted: dict[str, set[str]] = {}

    def index(self, doc: SearchDocument) -> None:
        """Add or replace a document in the index."""
        if doc.doc_id in self._documents:
            self._remove_from_inverted(doc.doc_id)
        self._documents[doc.doc_id] = doc
        self._add_to_inverted(doc)

    def remove(self, doc_id: str) -> bool:
        if doc_id not in self._documents:
            return False
        self._remove_from_inverted(doc_id)
        del self._documents[doc_id]
        return True

    def get(self, doc_id: str) -> SearchDocument | None:
        return self._documents.get(doc_id)

    def search(
        self,
        query:        str,
        doc_type:     DocumentType | None = None,
        tags:         list[str] | None    = None,
        org_id:       str | None          = None,
        limit:        int                    = 20,
        offset:       int                    = 0,
    ) -> list[SearchResult]:
        terms = _tokenise(query)
        if not terms:
            # No-query browse: return recent documents
            candidates = list(self._documents.values())
        else:
            # Candidate set = union of posting lists
            candidate_ids: set[str] = set()
            for term in terms:
                candidate_ids |= self._inverted.get(term, set())
            candidates = [self._documents[did] for did in candidate_ids if did in self._documents]

        # Filter
        if doc_type:
            candidates = [d for d in candidates if d.doc_type == doc_type]
        if tags:
            candidates = [d for d in candidates if all(t in d.tags for t in tags)]
        if org_id:
            candidates = [d for d in candidates if d.org_id is None or d.org_id == org_id]

        # Score
        results: list[SearchResult] = []
        for doc in candidates:
            score, highlights = self._score(doc, terms)
            results.append(SearchResult(document=doc, score=score, highlights=highlights))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[offset : offset + limit]

    def count(self) -> int:
        return len(self._documents)

    # ── Private ────────────────────────────────────────────────────────────────

    def _add_to_inverted(self, doc: SearchDocument) -> None:
        for term in _tokenise(doc.searchable_text()):
            self._inverted.setdefault(term, set()).add(doc.doc_id)

    def _remove_from_inverted(self, doc_id: str) -> None:
        for posting in self._inverted.values():
            posting.discard(doc_id)

    def _score(
        self,
        doc:   SearchDocument,
        terms: list[str],
    ) -> tuple[float, list[str]]:
        if not terms:
            return doc.score_boost, []

        title_tokens = _tokenise(doc.title.lower())
        body_tokens  = _tokenise(doc.body.lower())
        score        = 0.0
        highlights: list[str] = []

        for term in terms:
            title_tf = title_tokens.count(term)
            body_tf  = body_tokens.count(term)
            score   += (title_tf * 2.0 + body_tf * 1.0)
            if title_tf > 0:
                highlights.append(doc.title)
            if body_tf > 0:
                # Extract a short snippet around the term
                idx = doc.body.lower().find(term)
                if idx >= 0:
                    start = max(0, idx - 40)
                    end   = min(len(doc.body), idx + len(term) + 40)
                    highlights.append(f"…{doc.body[start:end]}…")

        score *= doc.score_boost
        return score, list(dict.fromkeys(highlights))[:3]   # dedupe, max 3


class TenantAwareIndex:
    """
    Multi-tenant wrapper around SearchIndex.

    Maintains one SearchIndex shard per tenant. All public methods
    accept tenant_id and enforce that documents are scoped correctly.
    """

    def __init__(self) -> None:
        self._shards: dict[str, SearchIndex] = {}

    def _shard(self, tenant_id: str) -> SearchIndex:
        if tenant_id not in self._shards:
            self._shards[tenant_id] = SearchIndex()
        return self._shards[tenant_id]

    def index(self, doc: SearchDocument) -> None:
        if not doc.tenant_id:
            raise ValueError("SearchDocument.tenant_id must not be empty")
        self._shard(doc.tenant_id).index(doc)

    def remove(self, tenant_id: str, doc_id: str) -> bool:
        return self._shard(tenant_id).remove(doc_id)

    def search(
        self,
        tenant_id: str,
        query:     str,
        doc_type:  DocumentType | None = None,
        tags:      list[str] | None    = None,
        org_id:    str | None          = None,
        limit:     int                    = 20,
        offset:    int                    = 0,
    ) -> list[SearchResult]:
        return self._shard(tenant_id).search(
            query    = query,
            doc_type = doc_type,
            tags     = tags,
            org_id   = org_id,
            limit    = limit,
            offset   = offset,
        )

    def get(self, tenant_id: str, doc_id: str) -> SearchDocument | None:
        return self._shard(tenant_id).get(doc_id)

    def tenant_doc_count(self, tenant_id: str) -> int:
        return self._shard(tenant_id).count() if tenant_id in self._shards else 0


# ── Helpers ────────────────────────────────────────────────────────────────────

_TOKEN_RE = re.compile(r"[a-z0-9]+")

def _tokenise(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


# ── Singleton ──────────────────────────────────────────────────────────────────

_index: TenantAwareIndex | None = None


def get_search_index() -> TenantAwareIndex:
    global _index
    if _index is None:
        _index = TenantAwareIndex()
    return _index
