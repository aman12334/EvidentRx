"""
Ingestion orchestrator — run all reference data loaders in dependency order.

Usage:
    python run_ingestion.py [--only ce|me|nppes|ndc]

Dependencies (must load in this order):
    1. covered_entities + contract_pharmacies  (CE JSON)
    2. medicaid_exclusions                     (needs CE FK)
    3. providers + provider_taxonomies         (NPPES)
    4. ndc_drugs                               (FDA NDC)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

# Make sure project root is on path
sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ingestion")

DOWNLOADS = os.path.expanduser("~/Downloads")

SOURCE_FILES = {
    "ce": f"{DOWNLOADS}/OPA_CE_DAILY_PUBLIC.JSON",
    "nppes_1": f"{DOWNLOADS}/NPPES_Data_Dissemination_050426_051026_Weekly_V2.zip",
    "nppes_2": f"{DOWNLOADS}/NPPES_Data_Dissemination_051126_051726_Weekly_V2.zip",
    "ndc": f"{DOWNLOADS}/ndctext.zip",
    "me": [
        f"{DOWNLOADS}/340B_Medicaid_Exclusion_File_for_20250401-20250630.xlsx",
        f"{DOWNLOADS}/340B_Medicaid_Exclusion_File_for_20250701-20250930.xlsx",
        f"{DOWNLOADS}/340B_Medicaid_Exclusion_File_for_20251001-20251231.xlsx",
        f"{DOWNLOADS}/340B_Medicaid_Exclusion_File_for_20260101-20260331.xlsx",
        f"{DOWNLOADS}/340B_Medicaid_Exclusion_File_for_20260401-20260630.xlsx",
    ],
}


def run(only: str | None = None) -> None:
    from app.database import SessionLocal
    from ingestion.loaders.covered_entity import CoveredEntityLoader
    from ingestion.loaders.medicaid_exclusion import MedicaidExclusionLoader
    from ingestion.loaders.ndc_drug import NdcDrugLoader
    from ingestion.loaders.provider import ProviderLoader

    with SessionLocal() as session:

        if only in (None, "ce"):
            logger.info("=== Step 1: Covered Entities + Contract Pharmacies ===")
            loader = CoveredEntityLoader(source_file=SOURCE_FILES["ce"])
            loader.load(session)
            session.commit()

        if only in (None, "me"):
            logger.info("=== Step 2: Medicaid Exclusions (5 quarters) ===")
            loader = MedicaidExclusionLoader(source_files=SOURCE_FILES["me"])
            loader.load(session)
            session.commit()

        if only in (None, "nppes"):
            logger.info("=== Step 3a: NPPES Week 1 ===")
            loader = ProviderLoader(source_file=SOURCE_FILES["nppes_1"])
            loader.load(session)
            session.commit()

            logger.info("=== Step 3b: NPPES Week 2 ===")
            loader = ProviderLoader(source_file=SOURCE_FILES["nppes_2"])
            loader.load(session)
            session.commit()

        if only in (None, "ndc"):
            logger.info("=== Step 4: FDA NDC Drug Directory ===")
            loader = NdcDrugLoader(source_file=SOURCE_FILES["ndc"])
            loader.load(session)
            session.commit()

    logger.info("=== Ingestion complete ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=["ce", "me", "nppes", "ndc"], default=None)
    args = parser.parse_args()
    run(only=args.only)
