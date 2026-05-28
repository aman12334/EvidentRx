from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.meta.ingestion_batch import IngestionBatch

logger = logging.getLogger(__name__)
UTC = timezone.utc


class BaseLoader:
    source_type: str = "other"
    batch_size: int = 1000

    def __init__(self, source_file: str, batch_name: str | None = None):
        self.source_file = source_file
        self.batch_name = batch_name or source_file.split("/")[-1]
        self.batch_id: UUID | None = None
        self._processed = 0
        self._failed = 0

    def _create_batch(self, session: Session, record_count: int | None = None) -> UUID:
        batch = IngestionBatch(
            batch_name=self.batch_name,
            source_type=self.source_type,
            source_file=self.source_file,
            record_count=record_count,
            status="processing",
            started_at=datetime.now(UTC),
        )
        session.add(batch)
        session.flush()
        self.batch_id = batch.batch_id
        logger.info("[%s] batch %s — %s records", self.source_type, self.batch_id, record_count)
        return batch.batch_id

    def _finish_batch(self, session: Session, success: bool = True) -> None:
        session.execute(
            text("""
                UPDATE meta.ingestion_batches
                SET status            = :status,
                    records_processed = :processed,
                    records_failed    = :failed,
                    completed_at      = NOW()
                WHERE batch_id = :bid
            """),
            {
                "status": "completed" if success else "failed",
                "processed": self._processed,
                "failed": self._failed,
                "bid": str(self.batch_id),
            },
        )
        logger.info(
            "[%s] done — processed=%d failed=%d", self.source_type, self._processed, self._failed
        )

    def load(self, session: Session) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------

def field_hash(rec: dict, fields: list[str]) -> str:
    val = "|".join("" if rec.get(f) is None else str(rec[f]) for f in fields)
    return hashlib.sha256(val.encode()).hexdigest()[:20]


def bulk_insert(session: Session, table: str, rows: list[dict]) -> None:
    """Bulk insert a list of row dicts into a fully-qualified table name."""
    if not rows:
        return
    cols = list(rows[0].keys())
    placeholders = ", ".join(f":{c}" for c in cols)
    col_list = ", ".join(cols)
    session.execute(text(f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"), rows)


def scd2_upsert(
    session: Session,
    table: str,           # schema.table
    pk_col: str,          # UUID PK column name
    nk_col: str,          # natural key column for SCD2 matching
    hash_fields: list[str],
    incoming: list[dict],
) -> tuple[int, int]:
    """
    SCD Type 2 upsert.

    Returns (inserted, closed) counts.

    Algorithm:
      1. Fetch all current rows {natural_key → (pk, field_hash)}
      2. Compare incoming:
         - New key     → INSERT with is_current=True
         - Key changed → close old row + INSERT new
         - Unchanged   → skip
    """
    if not incoming:
        return 0, 0

    # Fetch current snapshot — compute hash of the same fields in Python
    current_rows = session.execute(
        text(f"SELECT {pk_col}, {nk_col} FROM {table} WHERE is_current = TRUE")
    ).fetchall()

    # Also fetch field values for comparison
    # Simpler: fetch all fields we need to hash
    if current_rows:
        current_pks = {r[1]: str(r[0]) for r in current_rows}
    else:
        current_pks = {}

    # Fetch full rows for changed-detection only if there are existing records
    current_hashes: dict[str, str] = {}
    if current_pks:
        fields_csv = ", ".join(hash_fields)
        rows = session.execute(
            text(f"SELECT {pk_col}, {nk_col}, {fields_csv} FROM {table} WHERE is_current = TRUE")
        ).fetchall()
        col_names = [pk_col, nk_col] + hash_fields
        for row in rows:
            row_dict = dict(zip(col_names, row))
            nk = row_dict[nk_col]
            current_hashes[nk] = field_hash(row_dict, hash_fields)

    to_close: list[str] = []
    to_insert: list[dict] = []

    for rec in incoming:
        nk = rec[nk_col]
        h = field_hash(rec, hash_fields)

        if nk not in current_pks:
            to_insert.append(rec)
        elif current_hashes.get(nk) != h:
            to_close.append(current_pks[nk])
            to_insert.append(rec)
        # else: unchanged

    closed = 0
    if to_close:
        session.execute(
            text(f"""
                UPDATE {table}
                SET is_current = FALSE, valid_to = NOW(), updated_at = NOW()
                WHERE {pk_col} = ANY(:ids::uuid[])
            """),
            {"ids": to_close},
        )
        closed = len(to_close)

    inserted = 0
    if to_insert:
        bulk_insert(session, table, to_insert)
        inserted = len(to_insert)

    return inserted, closed
