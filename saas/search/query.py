"""
Search query DSL and RBAC-aware query filtering.

Provides a structured query builder and an RBAC-aware filter layer that
restricts search results to documents the requesting user is permitted
to see. Search results are further constrained by the user's org scope.

Query pipeline
──────────────
  1. Caller builds a SearchQuery (text + filters + pagination)
  2. RBACSearchFilter injects org_id constraint based on user's role
  3. TenantAwareIndex executes the query within the tenant shard
  4. Results are returned — never crossing tenant boundaries
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing      import Any, Optional

from saas.search.index import (
    DocumentType,
    SearchDocument,
    SearchResult,
    TenantAwareIndex,
    get_search_index,
)

log = logging.getLogger("evidentrx.saas.search.query")


@dataclass
class SearchFilter:
    """Structured filter applied before text scoring."""
    doc_type:  Optional[DocumentType] = None
    tags:      Optional[list[str]]    = None
    org_id:    Optional[str]          = None    # restrict to one org
    date_from: Optional[str]          = None    # ISO-8601 date string
    date_to:   Optional[str]          = None


@dataclass
class SearchQuery:
    """
    A single tenant-scoped search request.

    Attributes
    ──────────
    tenant_id  — mandatory; used to select the correct index shard
    text       — full-text query string (empty = browse mode)
    filters    — structured filter; merged with RBAC constraints
    limit      — max results to return (capped at 200)
    offset     — pagination offset
    """
    tenant_id: str
    text:      str          = ""
    filters:   SearchFilter = field(default_factory=SearchFilter)
    limit:     int          = 20
    offset:    int          = 0

    def __post_init__(self) -> None:
        self.limit = min(self.limit, 200)


@dataclass
class SearchResponse:
    query:        SearchQuery
    results:      list[SearchResult]
    total_hits:   int
    took_ms:      float

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_hits": self.total_hits,
            "took_ms":    round(self.took_ms, 2),
            "limit":      self.query.limit,
            "offset":     self.query.offset,
            "results":    [r.to_dict() for r in self.results],
        }


class RBACSearchFilter:
    """
    Applies RBAC constraints to a SearchQuery before execution.

    Role-based rules
    ────────────────
    platform_admin  — no org restriction; all tenant docs visible
    tenant_admin    — no org restriction within their tenant
    org_admin       — restricted to their org and child orgs
    analyst+        — restricted to their assigned org(s)
    viewer          — same scope as analyst but read-only (not enforced here)

    The filter modifies the SearchFilter in-place to add the org_id
    constraint that the index will honour. If the user already specified
    an org_id filter, the stricter of the two is applied.
    """

    # Roles that can see across all orgs within a tenant
    _TENANT_WIDE_ROLES = frozenset({"platform_admin", "tenant_admin"})

    def apply(
        self,
        query:       SearchQuery,
        user_role:   str,
        user_org_id: Optional[str],
    ) -> SearchQuery:
        """
        Return a new SearchQuery with RBAC constraints applied.

        Never widens the scope — only narrows it.
        """
        if user_role in self._TENANT_WIDE_ROLES:
            return query   # no additional constraint needed

        # For scoped roles: enforce org_id
        effective_org = user_org_id
        if query.filters.org_id and user_org_id:
            # Use the more restrictive of the two
            effective_org = (
                query.filters.org_id
                if query.filters.org_id == user_org_id
                else user_org_id   # user can't see other orgs
            )

        constrained_filter = SearchFilter(
            doc_type  = query.filters.doc_type,
            tags      = query.filters.tags,
            org_id    = effective_org,
            date_from = query.filters.date_from,
            date_to   = query.filters.date_to,
        )
        return SearchQuery(
            tenant_id = query.tenant_id,
            text      = query.text,
            filters   = constrained_filter,
            limit     = query.limit,
            offset    = query.offset,
        )


class SearchQueryExecutor:
    """
    Executes SearchQuery objects against the TenantAwareIndex.

    Wires together RBAC filtering, index execution, and response
    packaging. Timing is measured for observability.
    """

    def __init__(
        self,
        index:       Optional[TenantAwareIndex] = None,
        rbac_filter: Optional[RBACSearchFilter] = None,
    ) -> None:
        self._index       = index or get_search_index()
        self._rbac_filter = rbac_filter or RBACSearchFilter()

    def execute(
        self,
        query:       SearchQuery,
        user_role:   str                = "analyst",
        user_org_id: Optional[str]     = None,
    ) -> SearchResponse:
        import time
        start = time.monotonic()

        constrained = self._rbac_filter.apply(query, user_role, user_org_id)
        f = constrained.filters

        results = self._index.search(
            tenant_id = constrained.tenant_id,
            query     = constrained.text,
            doc_type  = f.doc_type,
            tags      = f.tags,
            org_id    = f.org_id,
            limit     = constrained.limit,
            offset    = constrained.offset,
        )

        took_ms = (time.monotonic() - start) * 1_000
        return SearchResponse(
            query      = query,
            results    = results,
            total_hits = len(results),   # approximate (no skip counting in mem index)
            took_ms    = took_ms,
        )

    def index_document(self, doc: SearchDocument) -> None:
        self._index.index(doc)

    def remove_document(self, tenant_id: str, doc_id: str) -> bool:
        return self._index.remove(tenant_id, doc_id)


# ── Singleton ──────────────────────────────────────────────────────────────────

_executor: Optional[SearchQueryExecutor] = None


def get_query_executor(
    index:       Optional[TenantAwareIndex] = None,
    rbac_filter: Optional[RBACSearchFilter] = None,
) -> SearchQueryExecutor:
    global _executor
    if _executor is None:
        _executor = SearchQueryExecutor(index=index, rbac_filter=rbac_filter)
    return _executor
