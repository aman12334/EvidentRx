"""
Entity and workflow discovery services.

Discovery layers provide curated, purpose-built search experiences on
top of the generic TenantAwareIndex. Rather than exposing raw search
results, they return domain-typed objects with pre-resolved metadata.

EntityDiscovery    — find covered entities by name, NPI, taxonomy code,
                     or 340B ID across the tenant's registered orgs
WorkflowDiscovery  — find published workflow templates and installed
                     playbook entries matching a use-case description
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
from saas.search.query import (
    SearchFilter,
    SearchQuery,
    SearchQueryExecutor,
    get_query_executor,
)

log = logging.getLogger("evidentrx.saas.search.discovery")


# ── Entity discovery ───────────────────────────────────────────────────────────

@dataclass
class EntityMatch:
    """A covered entity returned by EntityDiscovery."""
    entity_id:    str
    tenant_id:    str
    org_id:       Optional[str]
    name:         str
    npi:          Optional[str]
    taxonomy:     Optional[str]
    entity_340b_id: Optional[str]
    score:        float
    tags:         list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id":      self.entity_id,
            "tenant_id":      self.tenant_id,
            "org_id":         self.org_id,
            "name":           self.name,
            "npi":            self.npi,
            "taxonomy":       self.taxonomy,
            "entity_340b_id": self.entity_340b_id,
            "score":          round(self.score, 4),
            "tags":           self.tags,
        }


class EntityDiscovery:
    """
    Searches the covered-entity slice of the tenant's search index.

    Indexing convention
    ───────────────────
    Covered entities must be indexed as SearchDocuments with:
      doc_type = DocumentType.COVERED_ENTITY
      fields   = {"npi": ..., "taxonomy": ..., "entity_340b_id": ...}
    """

    def __init__(
        self,
        executor: Optional[SearchQueryExecutor] = None,
    ) -> None:
        self._executor = executor or get_query_executor()

    def find(
        self,
        tenant_id:   str,
        query:       str,
        org_id:      Optional[str] = None,
        tags:        Optional[list[str]] = None,
        user_role:   str           = "analyst",
        user_org_id: Optional[str] = None,
        limit:       int           = 20,
    ) -> list[EntityMatch]:
        sq = SearchQuery(
            tenant_id = tenant_id,
            text      = query,
            filters   = SearchFilter(
                doc_type = DocumentType.COVERED_ENTITY,
                org_id   = org_id,
                tags     = tags,
            ),
            limit  = limit,
        )
        response = self._executor.execute(sq, user_role=user_role, user_org_id=user_org_id)
        return [self._to_match(r) for r in response.results]

    def find_by_npi(
        self,
        tenant_id: str,
        npi:       str,
        user_role: str           = "analyst",
        user_org_id: Optional[str] = None,
    ) -> list[EntityMatch]:
        return self.find(
            tenant_id   = tenant_id,
            query       = npi,
            tags        = [f"npi:{npi}"],
            user_role   = user_role,
            user_org_id = user_org_id,
            limit       = 5,
        )

    def find_by_340b_id(
        self,
        tenant_id:    str,
        entity_340b_id: str,
        user_role:    str           = "analyst",
        user_org_id:  Optional[str] = None,
    ) -> list[EntityMatch]:
        return self.find(
            tenant_id   = tenant_id,
            query       = entity_340b_id,
            tags        = [f"340b:{entity_340b_id}"],
            user_role   = user_role,
            user_org_id = user_org_id,
            limit       = 5,
        )

    @staticmethod
    def make_document(
        entity_id:      str,
        tenant_id:      str,
        name:           str,
        org_id:         Optional[str] = None,
        npi:            Optional[str] = None,
        taxonomy:       Optional[str] = None,
        entity_340b_id: Optional[str] = None,
        tags:           Optional[list[str]] = None,
    ) -> SearchDocument:
        """Build a SearchDocument ready for indexing."""
        all_tags = list(tags or [])
        if npi:
            all_tags.append(f"npi:{npi}")
        if entity_340b_id:
            all_tags.append(f"340b:{entity_340b_id}")
        if taxonomy:
            all_tags.append(f"taxonomy:{taxonomy}")

        return SearchDocument(
            doc_id    = entity_id,
            tenant_id = tenant_id,
            doc_type  = DocumentType.COVERED_ENTITY,
            title     = name,
            body      = " ".join(filter(None, [npi, taxonomy, entity_340b_id])),
            tags      = all_tags,
            org_id    = org_id,
            fields    = {
                "npi":            npi,
                "taxonomy":       taxonomy,
                "entity_340b_id": entity_340b_id,
            },
        )

    @staticmethod
    def _to_match(r: SearchResult) -> EntityMatch:
        f = r.document.fields
        return EntityMatch(
            entity_id      = r.document.doc_id,
            tenant_id      = r.document.tenant_id,
            org_id         = r.document.org_id,
            name           = r.document.title,
            npi            = f.get("npi"),
            taxonomy       = f.get("taxonomy"),
            entity_340b_id = f.get("entity_340b_id"),
            score          = r.score,
            tags           = r.document.tags,
        )


# ── Workflow discovery ─────────────────────────────────────────────────────────

@dataclass
class WorkflowMatch:
    """A workflow template or playbook entry returned by WorkflowDiscovery."""
    doc_id:        str
    tenant_id:     str
    doc_type:      str
    title:         str
    description:   str
    tags:          list[str]
    score:         float
    template_type: Optional[str]
    version:       Optional[str]
    status:        Optional[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id":        self.doc_id,
            "tenant_id":     self.tenant_id,
            "doc_type":      self.doc_type,
            "title":         self.title,
            "description":   self.description,
            "tags":          self.tags,
            "score":         round(self.score, 4),
            "template_type": self.template_type,
            "version":       self.version,
            "status":        self.status,
        }


class WorkflowDiscovery:
    """
    Searches the workflow-template and playbook-entry slices of the index.

    Indexing convention
    ───────────────────
    Templates: doc_type = WORKFLOW_TEMPLATE
      fields = {"template_type": ..., "version": ..., "status": ...}
    Playbook entries: doc_type = PLAYBOOK_ENTRY
      fields = {"template_id": ..., "template_version": ..., "active": ...}
    """

    def __init__(
        self,
        executor: Optional[SearchQueryExecutor] = None,
    ) -> None:
        self._executor = executor or get_query_executor()

    def find_templates(
        self,
        tenant_id:     str,
        query:         str,
        template_type: Optional[str]      = None,
        tags:          Optional[list[str]] = None,
        user_role:     str                 = "analyst",
        user_org_id:   Optional[str]       = None,
        limit:         int                 = 20,
    ) -> list[WorkflowMatch]:
        filter_tags = list(tags or [])
        if template_type:
            filter_tags.append(f"type:{template_type}")

        sq = SearchQuery(
            tenant_id = tenant_id,
            text      = query,
            filters   = SearchFilter(
                doc_type = DocumentType.WORKFLOW_TEMPLATE,
                tags     = filter_tags or None,
            ),
            limit = limit,
        )
        response = self._executor.execute(sq, user_role=user_role, user_org_id=user_org_id)
        return [self._to_match(r) for r in response.results]

    def find_installed_playbooks(
        self,
        tenant_id:   str,
        query:       str,
        org_id:      Optional[str] = None,
        user_role:   str           = "analyst",
        user_org_id: Optional[str] = None,
        limit:       int           = 20,
    ) -> list[WorkflowMatch]:
        sq = SearchQuery(
            tenant_id = tenant_id,
            text      = query,
            filters   = SearchFilter(
                doc_type = DocumentType.PLAYBOOK_ENTRY,
                org_id   = org_id,
            ),
            limit = limit,
        )
        response = self._executor.execute(sq, user_role=user_role, user_org_id=user_org_id)
        return [self._to_match(r) for r in response.results]

    @staticmethod
    def make_template_document(
        template_id:    str,
        tenant_id:      str,
        title:          str,
        description:    str,
        template_type:  str,
        version:        str,
        status:         str,
        tags:           Optional[list[str]] = None,
    ) -> SearchDocument:
        all_tags = list(tags or [])
        all_tags.append(f"type:{template_type}")
        all_tags.append(f"status:{status}")
        return SearchDocument(
            doc_id    = template_id,
            tenant_id = tenant_id,
            doc_type  = DocumentType.WORKFLOW_TEMPLATE,
            title     = title,
            body      = description,
            tags      = all_tags,
            fields    = {
                "template_type": template_type,
                "version":       version,
                "status":        status,
            },
        )

    @staticmethod
    def make_playbook_document(
        entry_id:         str,
        tenant_id:        str,
        name:             str,
        template_id:      str,
        template_version: str,
        org_id:           Optional[str] = None,
        tags:             Optional[list[str]] = None,
    ) -> SearchDocument:
        return SearchDocument(
            doc_id    = entry_id,
            tenant_id = tenant_id,
            doc_type  = DocumentType.PLAYBOOK_ENTRY,
            title     = name,
            org_id    = org_id,
            tags      = list(tags or []),
            fields    = {
                "template_id":      template_id,
                "template_version": template_version,
                "active":           True,
            },
        )

    @staticmethod
    def _to_match(r: SearchResult) -> WorkflowMatch:
        f = r.document.fields
        return WorkflowMatch(
            doc_id        = r.document.doc_id,
            tenant_id     = r.document.tenant_id,
            doc_type      = r.document.doc_type.value,
            title         = r.document.title,
            description   = r.document.body,
            tags          = r.document.tags,
            score         = r.score,
            template_type = f.get("template_type"),
            version       = f.get("version") or f.get("template_version"),
            status        = f.get("status"),
        )
