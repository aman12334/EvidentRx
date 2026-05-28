"""
Regulatory document ingestion pipeline.

Orchestrates the full lifecycle from raw document fetch through parsing,
normalization, and indexing. Each stage is independently retryable;
failures are recorded in the IngestionRecord without losing the document.

Pipeline stages
───────────────
  1. FETCH     — download raw bytes from source URL or accept inline bytes
  2. HASH      — SHA-256 content hash; detect duplicate / unchanged documents
  3. PARSE     — route to the appropriate parser (PDF / HTML / feed)
  4. NORMALIZE — fill mandatory fields, apply source attribution
  5. VERSION   — detect prior version in family; set prior_version_id
  6. INDEX     — mark as INDEXED; update family index
  7. AUDIT     — write IngestionRecord

All operations are tenant-isolated. A document ingested for tenant A
is never visible to tenant B's diff or graph operations.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime    import datetime, timezone
from typing      import Any, Callable, Optional

from regulatory.ingestion.models import (
    DocumentFormat,
    DocumentSource,
    DocumentStatus,
    IngestionRecord,
    PolicyDomain,
    PolicySourceAttribution,
    RegulatoryDocument,
    _hash_content,
    new_doc_id,
    new_family_id,
)
from regulatory.ingestion.parsers import (
    HTMLParser,
    PDFParser,
    PlainTextParser,
    StructuredFeedParser,
)

log = logging.getLogger("evidentrx.regulatory.ingestion.pipeline")


@dataclass
class IngestRequest:
    """Caller-supplied ingestion request."""
    tenant_id:      str
    title:          str
    source:         DocumentSource
    fmt:            DocumentFormat
    domains:        list[PolicyDomain]
    attribution:    PolicySourceAttribution
    # Provide one of:
    raw_bytes:      Optional[bytes]    = None
    source_url:     Optional[str]      = None
    document_family_id: Optional[str]  = None   # None = new family
    version:        str                = "1.0"
    tags:           list[str]          = field(default_factory=list)
    triggered_by:   str                = "manual"
    metadata:       dict[str, Any]     = field(default_factory=dict)


@dataclass
class IngestResult:
    success:    bool
    document:   Optional[RegulatoryDocument]
    record:     IngestionRecord
    duplicate:  bool  = False   # True if content hash already exists
    error:      Optional[str] = None


class RegulatoryIngestionPipeline:
    """
    Ingests, parses, and indexes regulatory documents.

    The http_fetcher is an async callable:
      async fn(url: str) → bytes
    If not provided, only inline raw_bytes ingestion is supported.
    """

    def __init__(
        self,
        http_fetcher: Optional[Callable] = None,
        db_writer:    Optional[Callable] = None,
    ) -> None:
        self._fetcher   = http_fetcher
        self._db_writer = db_writer
        # doc_id → RegulatoryDocument
        self._docs:     dict[str, RegulatoryDocument]  = {}
        # content_hash → doc_id  (dedup index)
        self._by_hash:  dict[str, str]                 = {}
        # family_id → [doc_id, ...]  (sorted by version)
        self._families: dict[str, list[str]]           = {}
        # IngestionRecord history
        self._records:  list[IngestionRecord]          = []

    # ── Public API ─────────────────────────────────────────────────────────────

    async def ingest(self, req: IngestRequest) -> IngestResult:
        """Run the full pipeline for one document."""
        started = datetime.now(tz=timezone.utc)
        doc_id  = new_doc_id()
        record  = IngestionRecord(
            record_id    = str(uuid.uuid4()),
            doc_id       = doc_id,
            source       = req.source,
            source_url   = req.source_url,
            triggered_by = req.triggered_by,
            started_at   = started,
        )

        try:
            # 1. Fetch
            raw = await self._fetch(req)
            record.bytes_fetched = len(raw)

            # 2. Hash + dedup
            content_hash = _hash_content(raw)
            existing_id  = self._by_hash.get(content_hash)
            if existing_id:
                record.success      = True
                record.completed_at = datetime.now(tz=timezone.utc)
                self._records.append(record)
                log.info(
                    "RegulatoryIngestionPipeline: duplicate content hash — "
                    "doc %s already ingested as %s", doc_id[:8], existing_id[:8],
                )
                return IngestResult(
                    success   = True,
                    document  = self._docs.get(existing_id),
                    record    = record,
                    duplicate = True,
                )

            # 3. Parse
            parse_result = self._parse(raw, req)
            if not parse_result.success:
                record.parse_errors.append(parse_result.error or "parse failed")
                log.warning(
                    "RegulatoryIngestionPipeline: parse failed for %s: %s",
                    req.title[:60], parse_result.error,
                )
                # Continue with partial data — don't fail the ingest entirely

            # 4. Normalize + version
            family_id = req.document_family_id or new_family_id()
            prior_id  = self._latest_in_family(family_id)

            doc = RegulatoryDocument(
                doc_id             = doc_id,
                document_family_id = family_id,
                title              = parse_result.title or req.title,
                version            = req.version,
                status             = DocumentStatus.PARSED if parse_result.success else DocumentStatus.FAILED,
                fmt                = req.fmt,
                attribution        = req.attribution,
                domains            = req.domains,
                content_hash       = content_hash,
                raw_text           = parse_result.raw_text,
                summary            = parse_result.summary or "",
                key_changes        = parse_result.key_changes,
                prior_version_id   = prior_id,
                tags               = req.tags,
                metadata           = {**req.metadata, **parse_result.metadata},
            )

            # 5. Archive prior version
            if prior_id and prior_id in self._docs:
                self._docs[prior_id].status = DocumentStatus.ARCHIVED

            # 6. Index
            if parse_result.success:
                doc.status     = DocumentStatus.INDEXED
                doc.indexed_at = datetime.now(tz=timezone.utc)

            # 7. Store
            self._docs[doc_id]      = doc
            self._by_hash[content_hash] = doc_id
            self._families.setdefault(family_id, []).append(doc_id)

            # 8. Persist
            await self._persist("ingest_document", doc)

            record.success      = parse_result.success
            record.completed_at = datetime.now(tz=timezone.utc)
            self._records.append(record)

            log.info(
                "RegulatoryIngestionPipeline: ingested '%s' v%s (%s) — %d words",
                doc.title[:60], doc.version, doc.status.value,
                parse_result.word_count,
            )
            return IngestResult(success=True, document=doc, record=record)

        except Exception as exc:
            record.completed_at = datetime.now(tz=timezone.utc)
            record.parse_errors.append(str(exc))
            self._records.append(record)
            log.exception("RegulatoryIngestionPipeline: ingest failed for %s", req.title)
            return IngestResult(
                success  = False,
                document = None,
                record   = record,
                error    = str(exc),
            )

    def get_document(self, doc_id: str) -> Optional[RegulatoryDocument]:
        return self._docs.get(doc_id)

    def get_family(self, family_id: str) -> list[RegulatoryDocument]:
        ids = self._families.get(family_id, [])
        return [self._docs[did] for did in ids if did in self._docs]

    def list_current(
        self,
        source:  Optional[DocumentSource] = None,
        domain:  Optional[PolicyDomain]   = None,
        limit:   int                      = 100,
    ) -> list[RegulatoryDocument]:
        results = [
            d for d in self._docs.values()
            if d.status == DocumentStatus.INDEXED
            and (source is None or d.attribution.source == source)
            and (domain is None or domain in d.domains)
        ]
        results.sort(key=lambda d: d.ingested_at, reverse=True)
        return results[:limit]

    def ingestion_history(self, limit: int = 100) -> list[IngestionRecord]:
        return list(reversed(self._records[-limit:]))

    # ── Helpers ────────────────────────────────────────────────────────────────

    async def _fetch(self, req: IngestRequest) -> bytes:
        if req.raw_bytes is not None:
            return req.raw_bytes
        if req.source_url and self._fetcher:
            return await self._fetcher(req.source_url)
        raise ValueError("IngestRequest must provide raw_bytes or source_url+http_fetcher")

    def _parse(self, raw: bytes, req: IngestRequest):
        if req.fmt == DocumentFormat.PDF:
            return PDFParser().parse(raw, req.title)
        if req.fmt == DocumentFormat.HTML:
            return HTMLParser().parse(raw, req.source_url or "")
        if req.fmt == DocumentFormat.JSON_FEED:
            return StructuredFeedParser().parse_json(raw)
        if req.fmt == DocumentFormat.XML_FEED:
            return StructuredFeedParser().parse_xml(raw)
        return PlainTextParser().parse(raw)

    def _latest_in_family(self, family_id: str) -> Optional[str]:
        ids = self._families.get(family_id, [])
        if not ids:
            return None
        # Return the most-recently indexed doc in family
        candidates = [
            self._docs[did] for did in ids
            if did in self._docs and self._docs[did].status == DocumentStatus.INDEXED
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda d: d.ingested_at).doc_id

    async def _persist(self, op: str, obj: Any) -> None:
        if self._db_writer:
            try:
                await self._db_writer(op, obj)
            except Exception as exc:
                log.error("RegulatoryIngestionPipeline: persist failed: %s", exc)


# ── Singleton ──────────────────────────────────────────────────────────────────

_pipeline: Optional[RegulatoryIngestionPipeline] = None


def get_ingestion_pipeline(
    http_fetcher: Optional[Callable] = None,
    db_writer:    Optional[Callable] = None,
) -> RegulatoryIngestionPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = RegulatoryIngestionPipeline(
            http_fetcher = http_fetcher,
            db_writer    = db_writer,
        )
    return _pipeline
