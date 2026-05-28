"""
NPPES Provider loader.

Source: NPPES_Data_Dissemination_<week>_Weekly_V2.zip

Weekly delta file — upserts by NPI (SCD Type 2).
330 columns; we extract the core fields + up to 15 taxonomy codes per provider.
"""

from __future__ import annotations

import logging
import zipfile
from datetime import UTC, datetime
from uuid import uuid4

import pandas as pd
from sqlalchemy.orm import Session

from ingestion.base import BaseLoader, bulk_insert, scd2_upsert
from ingestion.normalizers import clean_str, parse_date

logger = logging.getLogger(__name__)
UTC = UTC

_HASH_FIELDS = [
    "entity_type_code", "provider_last_name", "provider_first_name",
    "organization_name", "street_address", "city", "state_code",
    "zip_code", "is_active",
]

# NPPES has 15 taxonomy slots
_N_TAX = 15


def _extract_npi_csv_name(zf: zipfile.ZipFile) -> str:
    for name in zf.namelist():
        if name.startswith("npidata_pfile") and name.endswith(".csv"):
            return name
    raise FileNotFoundError("npidata_pfile CSV not found in zip")


class ProviderLoader(BaseLoader):
    source_type = "nppes"

    def load(self, session: Session) -> None:
        now = datetime.now(UTC).isoformat()

        with zipfile.ZipFile(self.source_file) as zf:
            csv_name = _extract_npi_csv_name(zf)
            week_tag = csv_name.split("_pfile_")[-1].replace(".csv", "")
            logger.info("NPPES: reading %s", csv_name)

            with zf.open(csv_name) as f:
                df = pd.read_csv(f, dtype=str, low_memory=False)

        batch_id = self._create_batch(session, record_count=len(df))

        provider_rows: list[dict] = []
        taxonomy_rows: list[dict] = []

        for _, row in df.iterrows():
            npi = clean_str(row.get("NPI"), 10)
            if not npi:
                self._failed += 1
                continue

            entity_code = clean_str(row.get("Entity Type Code"), 1) or "2"
            deact_date = parse_date(row.get("NPI Deactivation Date"))
            is_active = deact_date is None

            provider_id = str(uuid4())
            provider_row = {
                "provider_id": provider_id,
                "npi": npi,
                "entity_type_code": entity_code,
                "provider_last_name": clean_str(row.get("Provider Last Name (Legal Name)"), 100),
                "provider_first_name": clean_str(row.get("Provider First Name"), 100),
                "provider_middle_name": clean_str(row.get("Provider Middle Name"), 100),
                "provider_credential": clean_str(row.get("Provider Credential Text"), 50),
                "organization_name": clean_str(row.get("Provider Organization Name (Legal Business Name)")),
                "doing_business_as": clean_str(row.get("Provider Other Organization Name")),
                "street_address": clean_str(row.get("Provider First Line Business Practice Location Address")),
                "city": clean_str(row.get("Provider Business Practice Location Address City Name"), 100),
                "state_code": clean_str(row.get("Provider Business Practice Location Address State Name"), 2),
                "zip_code": clean_str(row.get("Provider Business Practice Location Address Postal Code"), 10),
                "phone": clean_str(row.get("Provider Business Practice Location Address Telephone Number"), 20),
                "enumeration_date": str(parse_date(row.get("Provider Enumeration Date"))) if parse_date(row.get("Provider Enumeration Date")) else None,
                "last_update_date": str(parse_date(row.get("Last Update Date"))) if parse_date(row.get("Last Update Date")) else None,
                "deactivation_date": str(deact_date) if deact_date else None,
                "deactivation_reason": clean_str(row.get("NPI Deactivation Reason Code"), 2),
                "reactivation_date": str(parse_date(row.get("NPI Reactivation Date"))) if parse_date(row.get("NPI Reactivation Date")) else None,
                "is_active": is_active,
                "valid_from": now,
                "valid_to": None,
                "is_current": True,
                "source_week": week_tag,
                "source_file": self.source_file,
                "batch_id": str(batch_id),
                "created_at": now,
                "updated_at": now,
            }
            provider_rows.append(provider_row)

            # Taxonomy codes
            for i in range(1, _N_TAX + 1):
                code = clean_str(row.get(f"Healthcare Provider Taxonomy Code_{i}"), 20)
                if not code:
                    break
                is_primary = str(row.get(f"Healthcare Provider Primary Taxonomy Switch_{i}", "")).upper() == "Y"
                taxonomy_rows.append({
                    "taxonomy_id": str(uuid4()),
                    "provider_id": provider_id,
                    "taxonomy_code": code,
                    "taxonomy_description": None,
                    "license_number": clean_str(row.get(f"Provider License Number_{i}"), 50),
                    "license_state": clean_str(row.get(f"Provider License Number State Code_{i}"), 2),
                    "is_primary": is_primary,
                    "created_at": now,
                })

        # SCD2 upsert providers
        inserted, closed = scd2_upsert(
            session,
            table="ref.providers",
            pk_col="provider_id",
            nk_col="npi",
            hash_fields=_HASH_FIELDS,
            incoming=provider_rows,
        )
        logger.info("providers: inserted=%d closed=%d", inserted, closed)
        session.flush()

        # Build npi → provider_id map for newly inserted rows
        npi_to_id = {
            r[0]: str(r[1])
            for r in session.execute(
                __import__("sqlalchemy").text(
                    "SELECT npi, provider_id FROM ref.providers WHERE is_current = TRUE"
                )
            ).fetchall()
        }

        # Fix taxonomy provider_id to point to current (possibly new) row
        for t in taxonomy_rows:
            npi = next(
                (p["npi"] for p in provider_rows if p["provider_id"] == t["provider_id"]), None
            )
            if npi and npi in npi_to_id:
                t["provider_id"] = npi_to_id[npi]

        # Delete stale taxonomies for providers we just updated, then re-insert
        updated_provider_ids = list(npi_to_id.values())
        if updated_provider_ids and closed > 0:
            pass  # Taxonomy cascade delete handled by ON DELETE CASCADE on provider FK

        for i in range(0, len(taxonomy_rows), self.batch_size):
            bulk_insert(session, "ref.provider_taxonomies", taxonomy_rows[i : i + self.batch_size])
            session.flush()

        self._processed = len(provider_rows)
        self._finish_batch(session)
