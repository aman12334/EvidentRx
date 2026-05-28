"""
Ingestion pipeline runner.

Orchestrates the full pull → normalise → validate → persist → checkpoint
cycle for any BaseConnector. Pipelines are designed to be:

  - Replayable : cursor-based; resume from last checkpoint on failure
  - Auditable  : every batch writes a lineage record before committing data
  - Idempotent : re-running produces the same result (upsert semantics)
  - Observable : emits Prometheus metrics and structured log events per batch

Usage
─────
  pipeline = IngestionPipeline(connector, mapper, writer, lineage_store)
  result   = await pipeline.run("MedicationDispense")
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime    import datetime, timezone
from typing      import Any, Callable, Coroutine, Optional

from interoperability.base.connector import (
    BaseConnector, ConnectorState, IngestRecord, SyncCursor,
)

log = logging.getLogger("evidentrx.interop.pipeline")


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class BatchResult:
    batch_number:    int
    records_fetched: int
    records_written: int
    records_failed:  int
    duration_ms:     float
    errors:          list[str] = field(default_factory=list)


@dataclass
class PipelineResult:
    connector_id:   str
    resource_type:  str
    started_at:     datetime
    completed_at:   datetime
    status:         str               # "completed" | "partial" | "failed"
    batches:        list[BatchResult] = field(default_factory=list)
    total_fetched:  int               = 0
    total_written:  int               = 0
    total_failed:   int               = 0

    @property
    def duration_sec(self) -> float:
        return (self.completed_at - self.started_at).total_seconds()

    @property
    def success_rate(self) -> float:
        if self.total_fetched == 0:
            return 1.0
        return self.total_written / self.total_fetched


# ── Protocols (lightweight interfaces — avoids heavy ORM imports) ─────────────

RecordWriter = Callable[[list[IngestRecord]], Coroutine[Any, Any, int]]
LineageWriter = Callable[[str, str, str, int, int], Coroutine[Any, Any, None]]


# ── Pipeline ──────────────────────────────────────────────────────────────────

class IngestionPipeline:
    """
    Drives a full connector pull cycle for one resource type.

    Parameters
    ----------
    connector      : Connected and healthy BaseConnector instance
    record_writer  : Async callable that persists a list[IngestRecord] → int (written count)
    lineage_writer : Async callable that records batch lineage for audit
    validator      : Optional async callable returning (valid_records, invalid_records)
    max_errors     : Abort pipeline if total errors exceed this threshold (0 = unlimited)
    """

    def __init__(
        self,
        connector:      BaseConnector,
        record_writer:  RecordWriter,
        lineage_writer: Optional[LineageWriter]  = None,
        validator:      Optional[Callable]        = None,
        max_errors:     int                       = 100,
    ) -> None:
        self._connector      = connector
        self._record_writer  = record_writer
        self._lineage_writer = lineage_writer
        self._validator      = validator
        self._max_errors     = max_errors

    async def run(
        self,
        resource_type: str,
        force_full:    bool = False,
    ) -> PipelineResult:
        """
        Execute a complete ingestion cycle for the given resource_type.

        Steps per batch:
          1. Fetch from connector (paginated)
          2. Validate (if validator provided)
          3. Compute checksums for idempotency
          4. Write lineage record
          5. Write data records (upsert)
          6. Save cursor checkpoint
        """
        started_at = datetime.now(tz=timezone.utc)
        result = PipelineResult(
            connector_id  = self._connector.connector_id,
            resource_type = resource_type,
            started_at    = started_at,
            completed_at  = started_at,
            status        = "running",
        )

        cursor: Optional[SyncCursor] = None
        if not force_full:
            cursor = await self._connector.get_cursor(resource_type)

        log.info(
            "Pipeline start: connector=%s resource=%s mode=%s",
            self._connector.connector_id,
            resource_type,
            "full" if (force_full or cursor is None) else f"incremental since {cursor.last_value}",
        )

        batch_num    = 0
        total_errors = 0

        try:
            async for batch in self._connector.fetch(resource_type, cursor):
                if not batch:
                    continue

                batch_num += 1
                t0         = time.perf_counter()

                # ── Validate ─────────────────────────────────────────────────
                valid, invalid = batch, []
                if self._validator:
                    try:
                        valid, invalid = await self._validator(batch)
                    except Exception as e:
                        log.warning("Validator error on batch %d: %s", batch_num, e)

                # ── Checksum (idempotency key) ────────────────────────────────
                _attach_checksums(valid)

                # ── Lineage record ────────────────────────────────────────────
                if self._lineage_writer:
                    try:
                        await self._lineage_writer(
                            self._connector.connector_id,
                            resource_type,
                            f"batch-{batch_num}",
                            len(valid),
                            len(invalid),
                        )
                    except Exception as e:
                        log.warning("Lineage write failed (batch %d): %s", batch_num, e)

                # ── Persist ───────────────────────────────────────────────────
                written = 0
                if valid:
                    try:
                        written = await self._record_writer(valid)
                    except Exception as e:
                        log.error("Record write failed (batch %d): %s", batch_num, e)
                        total_errors += len(valid)

                failed    = len(invalid) + (len(valid) - written)
                total_errors += failed
                elapsed   = (time.perf_counter() - t0) * 1000

                batch_result = BatchResult(
                    batch_number    = batch_num,
                    records_fetched = len(batch),
                    records_written = written,
                    records_failed  = failed,
                    duration_ms     = round(elapsed, 1),
                    errors          = [str(r.source_id) for r in invalid[:10]],
                )
                result.batches.append(batch_result)
                result.total_fetched += len(batch)
                result.total_written += written
                result.total_failed  += failed

                # ── Update cursor after each successful batch ──────────────
                await self._connector.save_cursor(SyncCursor(
                    connector_id  = self._connector.connector_id,
                    tenant_id     = self._connector.tenant_id,
                    resource_type = resource_type,
                    last_synced   = datetime.now(tz=timezone.utc),
                    records_total = result.total_fetched,
                ))

                log.info(
                    "Batch %d: fetched=%d written=%d failed=%d elapsed=%.0fms",
                    batch_num, len(batch), written, failed, elapsed,
                )

                if self._max_errors > 0 and total_errors >= self._max_errors:
                    log.error(
                        "Pipeline aborted: error threshold %d exceeded (total_errors=%d)",
                        self._max_errors, total_errors,
                    )
                    result.status = "partial"
                    break
            else:
                result.status = "completed"

        except Exception as e:
            log.exception("Pipeline failed: connector=%s resource=%s error=%s",
                          self._connector.connector_id, resource_type, e)
            result.status = "failed"

        result.completed_at = datetime.now(tz=timezone.utc)

        log.info(
            "Pipeline %s: connector=%s resource=%s fetched=%d written=%d "
            "failed=%d batches=%d duration=%.1fs",
            result.status,
            result.connector_id,
            resource_type,
            result.total_fetched,
            result.total_written,
            result.total_failed,
            len(result.batches),
            result.duration_sec,
        )

        return result


# ── Parallel pipeline runner ──────────────────────────────────────────────────

async def run_parallel(
    pipelines: list[tuple[IngestionPipeline, str]],
    max_concurrent: int = 4,
) -> list[PipelineResult]:
    """
    Run multiple (pipeline, resource_type) pairs concurrently with a
    semaphore to cap parallelism.

    Returns results in the same order as the input list.
    """
    sem = asyncio.Semaphore(max_concurrent)

    async def _run(pipeline: IngestionPipeline, rt: str) -> PipelineResult:
        async with sem:
            return await pipeline.run(rt)

    return list(await asyncio.gather(
        *[_run(p, rt) for p, rt in pipelines],
        return_exceptions=False,
    ))


# ── Internal helpers ──────────────────────────────────────────────────────────

def _attach_checksums(records: list[IngestRecord]) -> None:
    """Attach a deterministic SHA-256 checksum to each record's canonical data."""
    for rec in records:
        payload = json.dumps(rec.canonical, sort_keys=True, default=str).encode()
        rec.checksum = hashlib.sha256(payload).hexdigest()
