"""
Medicaid Exclusion loader.

Source: 340B_Medicaid_Exclusion_File_for_<period>.xlsx  (one or many)

These files are the HRSA "Medicaid Exclusion Report" — they list which CE-state
combinations are enrolled in Medicaid under the 340B program (meaning 340B-priced
drugs for Medicaid patients are excluded from manufacturer rebates = carve-out).

Schema mapping:
  Program Code  → (used for entity_type_code context, not stored)
  340BID        → hrsa_id
  State         → state_code
  Start Date    → period_start  (CE's 340B participation start)
  Termination Date → period_end (NULL = still active)

filing_period and period dates are derived from the filename.
One row per unique (hrsa_id, state_code) per filing period is stored.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Sequence
from uuid import uuid4

import pandas as pd
from sqlalchemy.orm import Session

from ingestion.base import BaseLoader, bulk_insert
from ingestion.normalizers import clean_str, filing_period_from_filename, parse_date

logger = logging.getLogger(__name__)
UTC = timezone.utc


class MedicaidExclusionLoader(BaseLoader):
    source_type = "medicaid_exclusion"

    def __init__(self, source_files: Sequence[str]):
        # Multiple files per loader instance
        super().__init__(source_file=", ".join(source_files), batch_name="medicaid_exclusions")
        self.source_files = list(source_files)

    def load(self, session: Session) -> None:
        now = datetime.now(UTC).isoformat()

        # Build existing (hrsa_id, state_code, filing_period) set to avoid dupes
        existing = set(
            session.execute(
                __import__("sqlalchemy").text(
                    "SELECT hrsa_id, state_code, filing_period FROM ref.medicaid_exclusions"
                )
            ).fetchall()
        )

        # Build hrsa_id → ce_id lookup
        ce_map: dict[str, str] = {
            r[0]: str(r[1])
            for r in session.execute(
                __import__("sqlalchemy").text(
                    "SELECT hrsa_id, ce_id FROM ref.covered_entities WHERE is_current = TRUE"
                )
            ).fetchall()
        }

        total_records = 0
        for path in self.source_files:
            try:
                filing_period, period_start, period_end = filing_period_from_filename(path)
            except ValueError as e:
                logger.error(str(e))
                continue

            batch_id = self._create_batch(session)
            logger.info("Loading %s → %s", path.split("/")[-1], filing_period)

            df = pd.read_excel(path, header=3, dtype=str)
            df = df.rename(columns=lambda c: c.strip())

            # Deduplicate: one record per CE per state per filing period
            df = df.drop_duplicates(subset=["340BID", "State"])
            df = df.dropna(subset=["340BID", "State"])

            rows = []
            for _, row in df.iterrows():
                hrsa_id = clean_str(row.get("340BID"), 20)
                state = clean_str(row.get("State"), 2)
                if not hrsa_id or not state:
                    continue
                if (hrsa_id, state, filing_period) in existing:
                    continue

                term_raw = row.get("Termination Date")
                term_date = parse_date(term_raw)

                rows.append({
                    "exclusion_id": str(uuid4()),
                    "covered_entity_id": ce_map.get(hrsa_id),
                    "hrsa_id": hrsa_id,
                    "state_code": state,
                    # Presence in this file = CE has carved out 340B drugs from Medicaid rebates
                    "exclusion_type": "carve_out",
                    "carve_type_detail": "HRSA Medicaid Exclusion Report enrollment",
                    "filing_period": filing_period,
                    "period_start": str(period_start),
                    "period_end": str(period_end),
                    "is_current": True,
                    "source_file": path,
                    "batch_id": str(batch_id),
                    "created_at": now,
                })
                existing.add((hrsa_id, state, filing_period))

            if rows:
                for i in range(0, len(rows), self.batch_size):
                    bulk_insert(session, "ref.medicaid_exclusions", rows[i : i + self.batch_size])
                    session.flush()

            self._processed += len(rows)
            total_records += len(rows)
            logger.info("%s: %d records inserted", filing_period, len(rows))
            self._finish_batch(session)

        logger.info("Medicaid exclusions total inserted: %d", total_records)
