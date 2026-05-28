"""
FDA NDC Drug Directory loader.

Source: ndctext.zip
  product.txt  — drug identity (proprietary name, substance, form, route, etc.)
  package.txt  — package-level NDC codes (NDC_PACKAGE_CODE → our ndc_11)

Strategy:
  - Join product ← package on PRODUCTID
  - One row per package NDC (ndc_11) — this is what appears on prescriptions/claims
  - Upsert on ndc_11 (no SCD2 needed — FDA NDC entries are additive)
"""

from __future__ import annotations

import logging
import zipfile
from datetime import UTC, datetime
from uuid import uuid4

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from ingestion.base import BaseLoader, bulk_insert
from ingestion.normalizers import clean_str, normalize_ndc_11

logger = logging.getLogger(__name__)
UTC = UTC


class NdcDrugLoader(BaseLoader):
    source_type = "ndc_fda"

    def load(self, session: Session) -> None:
        now = datetime.now(UTC).isoformat()

        with zipfile.ZipFile(self.source_file) as zf:
            with zf.open("product.txt") as f:
                products = pd.read_csv(f, sep="\t", dtype=str, encoding="latin1")
            with zf.open("package.txt") as f:
                packages = pd.read_csv(f, sep="\t", dtype=str, encoding="latin1")

        logger.info("NDC: %d products, %d packages", len(products), len(packages))

        # Merge on PRODUCTID
        merged = packages.merge(products, on="PRODUCTID", how="left", suffixes=("_pkg", "_prod"))

        batch_id = self._create_batch(session, record_count=len(merged))

        # Fetch existing ndc_11 set for upsert
        existing_ndcs: set[str] = {
            r[0]
            for r in session.execute(text("SELECT ndc_11 FROM ref.ndc_drugs")).fetchall()
        }

        rows: list[dict] = []
        for _, row in merged.iterrows():
            raw_ndc = clean_str(row.get("NDCPACKAGECODE"))
            if not raw_ndc:
                self._failed += 1
                continue

            ndc_11 = normalize_ndc_11(raw_ndc)
            if not ndc_11 or ndc_11 in existing_ndcs:
                continue

            prod_ndc = clean_str(row.get("PRODUCTNDC"))
            parts = prod_ndc.split("-") if prod_ndc else []

            # Parse marketing dates (stored as YYYYMMDD integers)
            def _mdate(col: str):
                v = clean_str(row.get(col))
                if not v or v == "nan":
                    return None
                try:
                    from datetime import date
                    s = str(int(float(v)))
                    return str(date(int(s[:4]), int(s[4:6]), int(s[6:8])))
                except Exception:
                    return None

            exclude_flag = str(row.get("NDC_EXCLUDE_FLAG", "N")).strip().upper()

            rows.append({
                "drug_id": str(uuid4()),
                "ndc_11": ndc_11,
                "ndc_raw": raw_ndc,
                "application_number": clean_str(row.get("APPLICATIONNUMBER"), 20),
                "product_ndc": prod_ndc,
                "package_ndc": raw_ndc,
                "labeler_code": parts[0].zfill(5)[:5] if len(parts) >= 1 else None,
                "product_code": parts[1].zfill(4)[:4] if len(parts) >= 2 else None,
                "package_code": parts[2].zfill(2)[:2] if len(parts) >= 3 else None,
                "proprietary_name": clean_str(row.get("PROPRIETARYNAME")),
                "proprietary_name_suffix": clean_str(row.get("PROPRIETARYNAMESUFFIX")),
                "nonproprietary_name": clean_str(row.get("NONPROPRIETARYNAME")),
                "labeler_name": clean_str(row.get("LABELERNAME")),
                "substance_name": clean_str(row.get("SUBSTANCENAME")),
                "strength": clean_str(row.get("ACTIVE_NUMERATOR_STRENGTH")),
                "dosage_form": clean_str(row.get("DOSAGEFORMNAME"), 100),
                "route": clean_str(row.get("ROUTENAME")),
                "marketing_category": clean_str(row.get("MARKETINGCATEGORYNAME"), 100),
                "application_type": clean_str(row.get("PRODUCTTYPENAME"), 50),
                "product_type": clean_str(row.get("PRODUCTTYPENAME"), 50),
                "dea_schedule": clean_str(row.get("DEASCHEDULE"), 10),
                "listing_expiration_date": _mdate("LISTING_RECORD_CERTIFIED_THROUGH"),
                "marketing_start_date": _mdate("STARTMARKETINGDATE"),
                "marketing_end_date": _mdate("ENDMARKETINGDATE_pkg") or _mdate("ENDMARKETINGDATE_prod"),
                "is_active": exclude_flag != "E",
                "source_file": self.source_file,
                "batch_id": str(batch_id),
                "created_at": now,
                "updated_at": now,
            })
            existing_ndcs.add(ndc_11)

        logger.info("NDC: inserting %d new records", len(rows))
        for i in range(0, len(rows), self.batch_size):
            bulk_insert(session, "ref.ndc_drugs", rows[i : i + self.batch_size])
            session.flush()
            logger.info("NDC: chunk %d/%d", i + self.batch_size, len(rows))

        self._processed = len(rows)
        self._finish_batch(session)
