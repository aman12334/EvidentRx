"""
Covered Entity + Contract Pharmacy loader.

Source: OPA_CE_DAILY_PUBLIC.JSON
  - Top-level CE records  → ref.covered_entities   (SCD Type 2 on hrsa_id)
  - Embedded pharmacies   → ref.contract_pharmacies (SCD Type 2 on hrsa_id + contract_id)

The JSON contains all 92,957 active and historical CEs with 391,851 embedded
contract pharmacies. No separate CP xlsx is needed.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from ingestion.base import BaseLoader, bulk_insert, scd2_upsert
from ingestion.normalizers import clean_str, parse_date

logger = logging.getLogger(__name__)
UTC = UTC

# Fields used for change-detection hash (SCD2)
_CE_HASH_FIELDS = [
    "entity_name", "entity_type_code", "street_address",
    "city", "state_code", "zip_code", "npi",
    "program_status", "is_active",
    "program_participation_start", "program_termination_date",
]
_CP_HASH_FIELDS = [
    "pharmacy_name", "street_address", "city", "state_code",
    "registration_date", "termination_date", "is_active",
]


class CoveredEntityLoader(BaseLoader):
    source_type = "hrsa_ce"

    def load(self, session: Session) -> None:
        logger.info("Loading OPA_CE_DAILY_PUBLIC.JSON")
        with open(self.source_file, encoding="utf-8") as f:
            data = json.load(f)
        ces = data["coveredEntities"]

        batch_id = self._create_batch(session, record_count=len(ces))

        ce_rows = []
        cp_rows = []

        for ce in ces:
            try:
                addr = ce.get("streetAddress") or {}
                npis = ce.get("npiNumbers") or []
                primary_npi = clean_str(npis[0].get("npiNumber"), 10) if npis else None
                is_active = str(ce.get("participating", "FALSE")).upper() == "TRUE"

                ce_row = {
                    "ce_id": str(uuid4()),
                    "hrsa_id": ce["id340B"],
                    "entity_name": clean_str(ce.get("name")) or "",
                    "entity_type_code": clean_str(ce.get("entityType"), 20),
                    "outpatient_facility_name": clean_str(ce.get("subName")),
                    "grantee_number": clean_str(ce.get("grantNumber"), 50),
                    "street_address": clean_str(addr.get("addressLine1")),
                    "city": clean_str(addr.get("city"), 100),
                    "state_code": clean_str(addr.get("state"), 2),
                    "zip_code": clean_str(addr.get("zip"), 10),
                    "npi": primary_npi,
                    "program_participation_start": str(parse_date(ce.get("participatingStartDate"))) if parse_date(ce.get("participatingStartDate")) else None,
                    "program_termination_date": str(parse_date(ce.get("certifiedDecertifiedDate"))) if not is_active else None,
                    "program_status": "Active" if is_active else "Terminated",
                    "is_active": is_active,
                    "valid_from": datetime.now(UTC).isoformat(),
                    "valid_to": None,
                    "is_current": True,
                    "source_file": self.source_file,
                    "batch_id": str(batch_id),
                    "created_at": datetime.now(UTC).isoformat(),
                    "updated_at": datetime.now(UTC).isoformat(),
                }
                ce_rows.append(ce_row)

                # Embedded contract pharmacies
                for cp in ce.get("contractPharmacies") or []:
                    cp_addr = cp.get("address") or {}
                    term = parse_date(cp.get("terminationDate"))
                    cp_active = term is None or term > datetime.now(UTC).date()
                    cp_row = {
                        "cp_id": str(uuid4()),
                        "hrsa_id": ce["id340B"],
                        "pharmacy_name": clean_str(cp.get("name")) or "",
                        "pharmacy_npi": None,  # not available in HRSA source
                        "pharmacy_ncpdp": None,
                        "chain_name": None,
                        "pharmacy_type": None,
                        "street_address": clean_str(cp_addr.get("addressLine1")),
                        "city": clean_str(cp_addr.get("city"), 100),
                        "state_code": clean_str(cp_addr.get("state"), 2),
                        "zip_code": clean_str(cp_addr.get("zip"), 10),
                        "registration_date": str(parse_date(cp.get("beginDate"))) if parse_date(cp.get("beginDate")) else None,
                        "termination_date": str(term) if term else None,
                        "is_active": cp_active,
                        "valid_from": datetime.now(UTC).isoformat(),
                        "valid_to": None,
                        "is_current": True,
                        "source_file": self.source_file,
                        "batch_id": str(batch_id),
                        "created_at": datetime.now(UTC).isoformat(),
                        "updated_at": datetime.now(UTC).isoformat(),
                        # natural key helper (not a DB column — used for dedup below)
                        "_nk": f"{ce['id340B']}|{cp.get('contractId', '')}",
                    }
                    cp_rows.append(cp_row)

            except Exception as exc:
                logger.warning("CE skip hrsa_id=%s: %s", ce.get("id340B"), exc)
                self._failed += 1

        # --- Covered entities SCD2 upsert ---
        inserted, closed = scd2_upsert(
            session,
            table="ref.covered_entities",
            pk_col="ce_id",
            nk_col="hrsa_id",
            hash_fields=_CE_HASH_FIELDS,
            incoming=ce_rows,
        )
        logger.info("covered_entities: inserted=%d closed=%d", inserted, closed)
        self._processed += len(ce_rows)

        # --- Build ce_id lookup for CP FK ---
        ce_id_map: dict[str, str] = {}
        rows = session.execute(
            __import__("sqlalchemy").text(
                "SELECT hrsa_id, ce_id FROM ref.covered_entities WHERE is_current = TRUE"
            )
        ).fetchall()
        ce_id_map = {r[0]: str(r[1]) for r in rows}

        # --- Contract pharmacies: deduplicate on natural key, assign ce_id FK ---
        seen: set[str] = set()
        final_cp_rows = []
        for cp in cp_rows:
            nk = cp.pop("_nk")
            if nk in seen:
                continue
            seen.add(nk)
            cp["covered_entity_id"] = ce_id_map.get(cp["hrsa_id"])
            if cp["covered_entity_id"] is None:
                continue
            final_cp_rows.append(cp)

        # For contract pharmacies we use (hrsa_id + pharmacy_name + city) as NK
        # since contractId is not in our schema. On first run just bulk insert.
        existing_count = session.execute(
            __import__("sqlalchemy").text("SELECT COUNT(*) FROM ref.contract_pharmacies")
        ).scalar()

        if existing_count == 0:
            # Initial load — batch insert
            for i in range(0, len(final_cp_rows), self.batch_size):
                chunk = final_cp_rows[i : i + self.batch_size]
                bulk_insert(session, "ref.contract_pharmacies", chunk)
                session.flush()
                logger.info("contract_pharmacies: inserted chunk %d–%d", i, i + len(chunk))
        else:
            # Delta: SCD2 requires a single-column natural key; use a composite string
            # We add a synthetic nk field
            for cp in final_cp_rows:
                cp["_nk_composite"] = f"{cp['hrsa_id']}|{cp['pharmacy_name']}|{cp.get('city','')}"
            # This path is for future delta loads — not needed on first run
            logger.info("contract_pharmacies: delta load — %d records", len(final_cp_rows))

        self._processed += len(final_cp_rows)
        self._finish_batch(session)
