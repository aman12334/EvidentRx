"""
upload.py — Hospital data ingestion endpoint.

Allows hospitals / covered entities to upload CSV files containing their
340B dispense and claim records. The file flows through the full compliance
pipeline and returns a findings summary visible on the dashboard immediately.

Accepted file format (CSV, UTF-8):
  dispenses.csv  — columns: ndc_11, patient_id, dispense_date, quantity,
                             days_supply, dispense_type, covered_entity_id [optional]
  claims.csv     — columns: ndc_11, patient_id, service_date, payer_type,
                             billed_amount, covered_entity_id [optional]

The endpoint auto-detects whether the file contains dispenses or claims
based on column headers. Mixed files (both dispenses and claims in one CSV)
are also supported.

Pipeline per upload:
  1. Parse CSV / Excel → validate columns
  2. Normalise & hash patient identifiers (SHA-256, irreversible)
  3. Insert into ops.dispenses + ops.claims
  4. Build ops.split_billing rows for uploaded records
  5. Run RulesEngine (scoped to the upload batch_id)
  6. Create investigation cases via CaseBuilderService
  7. Return UploadResult with finding counts, exposure estimate, and case links
"""
from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/upload", tags=["Data Upload"])

# ── Column name maps (accept variations hospitals might use) ──────────────────

_DISPENSE_COLS = {
    "ndc",     "ndc_11",  "ndc11",  "national_drug_code",
    "drug_code", "drug_ndc",
}
_CLAIM_COLS = {
    "claim_number", "claim_id", "service_date", "payer_type",
    "billed_amount", "paid_amount", "is_medicaid", "payer",
}
_DATE_FMTS = [
    "%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y",
    "%m/%d/%y", "%Y%m%d", "%d-%b-%Y",
]

# Max upload size: 20 MB
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# ── Pydantic response models ──────────────────────────────────────────────────


class FindingSummary(BaseModel):
    rule_code:  str
    description: str
    count:       int
    severity:    str


class UploadResult(BaseModel):
    upload_id:          str
    batch_id:           str
    status:             str          # "complete" | "partial" | "no_findings"
    message:            str
    rows_parsed:        int
    dispenses_inserted: int
    claims_inserted:    int
    split_billing_rows: int
    cases_created:      int
    total_findings:     int
    critical_findings:  int
    high_findings:      int
    estimated_exposure: Optional[float]
    findings_by_rule:   list[FindingSummary]
    case_ids:           list[str]
    processing_ms:      int


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_date(val: str) -> Optional[date]:
    if not val or not isinstance(val, str):
        return None
    val = val.strip()
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(val, fmt).date()
        except ValueError:
            pass
    return None


def _parse_decimal(val: str) -> Optional[Decimal]:
    if not val:
        return None
    try:
        clean = re.sub(r"[,$\s]", "", str(val))
        return Decimal(clean)
    except InvalidOperation:
        return None


def _hash_patient(raw_id: str) -> str:
    """One-way SHA-256 hash of patient identifier — HIPAA-safe."""
    return hashlib.sha256(raw_id.strip().lower().encode()).hexdigest()[:40]


def _normalise_col(name: str) -> str:
    return name.strip().lower().replace(" ", "_").replace("-", "_")


def _detect_file_type(headers: list[str]) -> tuple[bool, bool]:
    """Returns (has_dispenses, has_claims) based on column names."""
    normalised = {_normalise_col(h) for h in headers}
    has_dispenses = bool(normalised & _DISPENSE_COLS) and (
        "dispense_date" in normalised or "dispensed_date" in normalised
        or "fill_date" in normalised or "dispense_date" in normalised
    )
    has_claims = bool(normalised & _CLAIM_COLS)
    # If both dispense+claim date columns present — treat as mixed
    if not has_dispenses and not has_claims:
        # Best-guess: if ndc + date columns, treat as dispenses
        has_dispenses = bool(normalised & _DISPENSE_COLS)
    return has_dispenses, has_claims


def _read_csv_rows(content: bytes) -> tuple[list[str], list[dict]]:
    """Parse CSV bytes → (headers, list of row dicts)."""
    text_content = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text_content))
    if reader.fieldnames is None:
        raise ValueError("CSV has no headers")
    headers = list(reader.fieldnames)
    rows = list(reader)
    return headers, rows


def _get_or_create_ce(db: Session, ce_id_raw: Optional[str]) -> Optional[str]:
    """Return a valid CE UUID, or pick the first active CE from DB."""
    if ce_id_raw:
        try:
            UUID(str(ce_id_raw))
            # Verify it exists
            exists = db.execute(
                text("SELECT 1 FROM ref.covered_entities WHERE ce_id = :id"),
                {"id": ce_id_raw},
            ).fetchone()
            if exists:
                return ce_id_raw
        except (ValueError, Exception):
            pass

    # Fallback: pick the first active CE
    row = db.execute(
        text("SELECT ce_id FROM ref.covered_entities WHERE is_current = TRUE LIMIT 1")
    ).fetchone()
    if row:
        return str(row.ce_id)

    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="No covered entities registered. Run setup first.",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Row insertion
# ═══════════════════════════════════════════════════════════════════════════════

_PAYER_TYPE_MAP = {
    "medicaid": "medicaid", "mcd": "medicaid", "medi-cal": "medicaid",
    "medicare_part_d": "medicare_part_d", "part d": "medicare_part_d",
    "medicare part d": "medicare_part_d",
    "commercial": "commercial", "private": "commercial",
    "self_pay": "self_pay", "self pay": "self_pay", "cash": "self_pay",
}

def _normalise_payer(raw: str) -> str:
    """Map raw payer strings to valid DB enum values."""
    clean = raw.strip().lower()
    return _PAYER_TYPE_MAP.get(clean, "other")

_CLAIM_TYPE_MAP = {
    "medicaid": "medicaid", "mcd": "medicaid", "medi-cal": "medicaid",
    "medicare_part_d": "medicare_part_d", "part d": "medicare_part_d",
    "medicare part d": "medicare_part_d",
    "medicare_part_b": "medicare_part_b", "part b": "medicare_part_b",
    "medicare part b": "medicare_part_b",
    "commercial": "commercial", "private": "commercial",
}

def _normalise_claim_type(raw: str) -> str:
    clean = raw.strip().lower()
    return _CLAIM_TYPE_MAP.get(clean, "other")


def _insert_dispenses(
    db: Session,
    rows: list[dict],
    ce_id: str,
    batch_id: str,
) -> int:
    inserted = 0
    for row in rows:
        col = {_normalise_col(k): v for k, v in row.items()}

        ndc = (
            col.get("ndc_11") or col.get("ndc") or col.get("national_drug_code")
            or col.get("drug_ndc") or col.get("drug_code") or ""
        ).replace("-", "").strip().zfill(11)[:11]

        raw_patient = (
            col.get("patient_id") or col.get("patient_id_hash")
            or col.get("mrn") or col.get("member_id") or col.get("patient") or ""
        )
        patient_hash = _hash_patient(raw_patient) if raw_patient else ""

        disp_date = _parse_date(
            col.get("dispense_date") or col.get("fill_date") or col.get("dispensed_date") or ""
        )
        if not disp_date:
            continue  # skip rows without a date

        qty = int(float(col.get("quantity") or col.get("qty") or 1))
        days = int(float(col.get("days_supply") or col.get("days") or 30))
        raw_payer = col.get("payer_type") or col.get("payer") or col.get("insurance_type") or "commercial"
        payer_type = _normalise_payer(raw_payer)
        entity_ce = col.get("covered_entity_id") or ce_id

        try:
            db.execute(text("""
                INSERT INTO ops.dispenses (
                    dispense_id, covered_entity_id, ndc_11,
                    patient_id_hash, dispense_date, quantity, days_supply,
                    payer_type, is_340b_dispense,
                    batch_id, created_at
                ) VALUES (
                    :did, :ce, :ndc, :pat, :ddate, :qty, :days,
                    :payer, TRUE,
                    CAST(:batch_id AS uuid), NOW()
                )
            """), {
                "did":      str(uuid4()),
                "ce":       str(entity_ce),
                "ndc":      ndc,
                "pat":      patient_hash,
                "ddate":    disp_date,
                "qty":      qty,
                "days":     days,
                "payer":    payer_type,
                "batch_id": batch_id,
            })
            inserted += 1
        except Exception as exc:
            logger.debug("Dispense row skipped: %s", exc)
            db.rollback()

    return inserted


def _insert_claims(
    db: Session,
    rows: list[dict],
    ce_id: str,
    batch_id: str,
) -> int:
    inserted = 0
    for row in rows:
        col = {_normalise_col(k): v for k, v in row.items()}

        ndc = (
            col.get("ndc_11") or col.get("ndc") or col.get("drug_ndc") or ""
        ).replace("-", "").strip().zfill(11)[:11]

        raw_patient = (
            col.get("patient_id") or col.get("mrn") or col.get("member_id")
            or col.get("patient") or ""
        )
        patient_hash = _hash_patient(raw_patient) if raw_patient else ""

        svc_date = _parse_date(
            col.get("service_date") or col.get("claim_date") or col.get("date_of_service") or ""
        )
        if not svc_date:
            continue

        raw_payer = (col.get("payer_type") or col.get("payer") or col.get("insurance_type") or "commercial").lower()
        claim_type = _normalise_claim_type(raw_payer)
        is_medicaid = claim_type == "medicaid"

        billed = _parse_decimal(col.get("billed_amount") or col.get("charge_amount") or "0") or Decimal("0")
        paid   = _parse_decimal(col.get("paid_amount")   or col.get("payment_amount")  or "0") or Decimal("0")
        external_id = (
            col.get("claim_number") or col.get("claim_id") or col.get("claim_no")
            or f"CLM-{uuid4().hex[:8].upper()}"
        )
        entity_ce = col.get("covered_entity_id") or ce_id

        try:
            db.execute(text("""
                INSERT INTO ops.claims (
                    claim_id, covered_entity_id, ndc_11,
                    patient_id_hash, service_date, external_id,
                    claim_type, is_medicaid, billed_amount, paid_amount,
                    claim_status, batch_id, created_at
                ) VALUES (
                    :cid, :ce, :ndc, :pat, :sdate, :ext_id,
                    :ctype, :medic, :billed, :paid,
                    'paid', CAST(:batch_id AS uuid), NOW()
                )
            """), {
                "cid":      str(uuid4()),
                "ce":       str(entity_ce),
                "ndc":      ndc,
                "pat":      patient_hash,
                "sdate":    svc_date,
                "ext_id":   external_id,
                "ctype":    claim_type,
                "medic":    is_medicaid,
                "billed":   float(billed),
                "paid":     float(paid),
                "batch_id": batch_id,
            })
            inserted += 1
        except Exception as exc:
            logger.debug("Claim row skipped: %s", exc)
            db.rollback()

    return inserted


# ═══════════════════════════════════════════════════════════════════════════════
# Split billing + rules engine (scoped to batch)
# ═══════════════════════════════════════════════════════════════════════════════

def _build_split_billing_for_batch(db: Session, batch_id: str) -> int:
    """Build split_billing rows for the just-uploaded batch."""
    db.execute(text("""
        INSERT INTO ops.split_billing (
            split_billing_id, covered_entity_id, ndc_11,
            service_date,
            patient_id_hash, dispense_id, dispense_date,
            claim_id, claim_service_date,
            purchase_id, purchase_date,
            is_340b_purchase, is_medicaid_billed,
            duplicate_discount_risk, medicaid_overlap_risk,
            carve_out_violation_risk, batch_id,
            created_at
        )
        SELECT
            gen_random_uuid(),
            d.covered_entity_id,
            d.ndc_11,
            c.service_date,
            d.patient_id_hash,
            d.dispense_id,
            d.dispense_date,
            c.claim_id,
            c.service_date,
            p.purchase_id,
            p.purchase_date,
            TRUE,
            c.is_medicaid,
            (c.is_medicaid AND d.patient_id_hash != '' AND d.patient_id_hash = c.patient_id_hash),
            c.is_medicaid,
            FALSE,
            CAST(:batch_id AS uuid),
            NOW()
        FROM ops.dispenses d
        JOIN ops.claims c
          ON c.covered_entity_id = d.covered_entity_id
         AND c.ndc_11 = d.ndc_11
         AND c.patient_id_hash = d.patient_id_hash
         AND c.service_date BETWEEN d.dispense_date AND d.dispense_date + INTERVAL '5 days'
        LEFT JOIN LATERAL (
            SELECT purchase_id, purchase_date
            FROM ops.purchases p2
            WHERE p2.covered_entity_id = d.covered_entity_id
              AND p2.ndc_11 = d.ndc_11
              AND p2.purchase_date <= d.dispense_date
            ORDER BY p2.purchase_date DESC
            LIMIT 1
        ) p ON TRUE
        WHERE d.batch_id = CAST(:batch_id AS uuid)
        ON CONFLICT DO NOTHING
    """), {"batch_id": batch_id})

    count = db.execute(
        text("SELECT COUNT(*) FROM ops.split_billing WHERE batch_id = CAST(:b AS uuid)"),
        {"b": batch_id},
    ).scalar() or 0
    return count


def _run_rules_for_batch(db: Session, batch_id: str) -> dict:
    """Run RulesEngine scoped to a single batch_id."""
    try:
        from rules_engine.engine import RulesEngine
        engine = RulesEngine()
        result = engine.run(db, batch_id=batch_id)
        db.commit()
        return result
    except Exception as exc:
        logger.warning("Rules engine failed for batch %s: %s", batch_id, exc)
        db.rollback()
        return {"findings_created": 0}


def _run_case_builder_for_batch(db: Session, batch_id: str) -> dict:
    """Build investigation cases from the new findings."""
    try:
        from investigation.services.case_builder import CaseBuilderService
        builder = CaseBuilderService()
        result = builder.run(db, batch_id=batch_id)
        db.commit()
        return result
    except Exception as exc:
        logger.warning("Case builder failed for batch %s: %s", batch_id, exc)
        db.rollback()
        return {"cases_created": 0}


def _get_batch_findings_summary(db: Session, batch_id: str) -> dict:
    """Aggregate finding stats for the given batch."""
    rows = db.execute(text("""
        SELECT
            af.rule_code,
            af.severity,
            cr.rule_name,
            COUNT(*) AS cnt,
            SUM(af.financial_exposure) AS exposure
        FROM audit.audit_findings af
        JOIN ops.split_billing sb ON sb.split_billing_id = af.split_billing_id
        LEFT JOIN audit.compliance_rules cr ON cr.rule_code = af.rule_code
        WHERE sb.batch_id = CAST(:batch_id AS uuid)
        GROUP BY af.rule_code, af.severity, cr.rule_name
        ORDER BY
            CASE af.severity
                WHEN 'critical' THEN 1 WHEN 'high' THEN 2
                WHEN 'medium' THEN 3 ELSE 4 END,
            cnt DESC
    """), {"batch_id": batch_id}).fetchall()

    findings_by_rule = [
        FindingSummary(
            rule_code=r.rule_code,
            description=r.rule_name or r.rule_code,
            count=r.cnt,
            severity=r.severity,
        )
        for r in rows
    ]

    total   = sum(r.count for r in findings_by_rule)
    critical = sum(r.count for r in findings_by_rule if r.severity == "critical")
    high     = sum(r.count for r in findings_by_rule if r.severity == "high")

    total_exposure = db.execute(text("""
        SELECT SUM(af.financial_exposure)
        FROM audit.audit_findings af
        JOIN ops.split_billing sb ON sb.split_billing_id = af.split_billing_id
        WHERE sb.batch_id = CAST(:batch_id AS uuid)
    """), {"batch_id": batch_id}).scalar()

    case_rows = db.execute(text("""
        SELECT DISTINCT ic.case_id::text
        FROM audit.investigation_cases ic
        JOIN audit.investigation_case_findings icf ON icf.case_id = ic.case_id
        JOIN audit.audit_findings af ON af.finding_id = icf.finding_id
        JOIN ops.split_billing sb ON sb.split_billing_id = af.split_billing_id
        WHERE sb.batch_id = CAST(:batch_id AS uuid)
    """), {"batch_id": batch_id}).fetchall()

    return {
        "total_findings":     total,
        "critical_findings":  critical,
        "high_findings":      high,
        "estimated_exposure": float(total_exposure) if total_exposure else None,
        "findings_by_rule":   findings_by_rule,
        "case_ids":           [r.case_id for r in case_rows],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Route
# ═══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/claims",
    response_model=UploadResult,
    summary="Upload 340B dispense/claim data",
    description=(
        "Accepts a CSV file containing 340B dispense or claim records. "
        "Runs the full compliance pipeline and returns a findings summary. "
        "Max file size: 20 MB."
    ),
)
async def upload_claims_file(
    file: UploadFile = File(..., description="CSV file with dispense or claim records"),
    covered_entity_id: Optional[str] = Form(
        None,
        description="UUID of the covered entity. Auto-detected from first active CE if omitted.",
    ),
    db: Session = Depends(get_db),
) -> UploadResult:
    t_start = datetime.now(timezone.utc)

    # --- Validate file --------------------------------------------------------
    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No file provided")

    ext = (file.filename or "").lower().rsplit(".", 1)[-1]
    if ext not in ("csv", "tsv", "txt"):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Unsupported file type '.{ext}'. Upload a CSV file.",
        )

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File exceeds {MAX_UPLOAD_BYTES // (1024*1024)} MB limit",
        )
    if not content.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Uploaded file is empty")

    logger.info("Upload received: file=%s size=%d bytes", file.filename, len(content))

    # --- Parse ----------------------------------------------------------------
    try:
        headers, rows = _read_csv_rows(content)
    except Exception as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"CSV parse error: {exc}")

    if not rows:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "CSV has no data rows")

    has_dispenses, has_claims = _detect_file_type(headers)
    # If ambiguous, default to treating as dispenses
    if not has_dispenses and not has_claims:
        has_dispenses = True

    # --- Resolve covered entity -----------------------------------------------
    try:
        ce_id = _get_or_create_ce(db, covered_entity_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"CE lookup failed: {exc}")

    # --- Generate batch_id ----------------------------------------------------
    batch_id = str(uuid4())

    # Insert into meta.ingestion_batches so the FK is satisfied
    try:
        db.execute(text("""
            INSERT INTO meta.ingestion_batches (
                batch_id, source_system, source_file_name,
                record_count, status, started_at, created_at
            ) VALUES (
                CAST(:bid AS uuid), 'upload', :fname,
                :cnt, 'processing', NOW(), NOW()
            )
        """), {"bid": batch_id, "fname": file.filename, "cnt": len(rows)})
        db.commit()
    except Exception:
        # meta.ingestion_batches may not exist in all migrations — proceed anyway
        db.rollback()
        batch_id = str(uuid4())

    # --- Insert records -------------------------------------------------------
    dispenses_inserted = 0
    claims_inserted    = 0

    if has_dispenses:
        dispenses_inserted = _insert_dispenses(db, rows, ce_id, batch_id)
        db.commit()
        logger.info("Inserted %d dispenses", dispenses_inserted)

    if has_claims:
        claims_inserted = _insert_claims(db, rows, ce_id, batch_id)
        db.commit()
        logger.info("Inserted %d claims", claims_inserted)

    if dispenses_inserted == 0 and claims_inserted == 0:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "No valid rows could be parsed. Check your CSV column names.",
        )

    # --- Pipeline: split billing → rules → cases ------------------------------
    split_rows = _build_split_billing_for_batch(db, batch_id)
    db.commit()

    rules_result  = _run_rules_for_batch(db, batch_id)
    cases_result  = _run_case_builder_for_batch(db, batch_id)
    summary       = _get_batch_findings_summary(db, batch_id)

    # --- Mark batch complete --------------------------------------------------
    try:
        db.execute(text("""
            UPDATE meta.ingestion_batches
            SET status = 'complete', completed_at = NOW()
            WHERE batch_id = CAST(:bid AS uuid)
        """), {"bid": batch_id})
        db.commit()
    except Exception:
        db.rollback()

    t_ms = int((datetime.now(timezone.utc) - t_start).total_seconds() * 1000)

    total_findings = summary["total_findings"]
    if total_findings == 0:
        upload_status = "no_findings"
        message = "Upload processed. No compliance findings detected in this data."
    elif summary["critical_findings"] > 0:
        upload_status = "complete"
        message = (
            f"Upload complete. Found {total_findings} compliance issues "
            f"({summary['critical_findings']} critical). Review cases immediately."
        )
    else:
        upload_status = "complete"
        message = (
            f"Upload complete. Found {total_findings} compliance issues "
            f"across {cases_result.get('cases_created', 0)} investigation case(s)."
        )

    logger.info(
        "Upload pipeline complete: batch=%s dispenses=%d claims=%d findings=%d cases=%d ms=%d",
        batch_id, dispenses_inserted, claims_inserted,
        total_findings, cases_result.get("cases_created", 0), t_ms,
    )

    return UploadResult(
        upload_id=str(uuid4()),
        batch_id=batch_id,
        status=upload_status,
        message=message,
        rows_parsed=len(rows),
        dispenses_inserted=dispenses_inserted,
        claims_inserted=claims_inserted,
        split_billing_rows=split_rows,
        cases_created=cases_result.get("cases_created", 0),
        total_findings=total_findings,
        critical_findings=summary["critical_findings"],
        high_findings=summary["high_findings"],
        estimated_exposure=summary["estimated_exposure"],
        findings_by_rule=summary["findings_by_rule"],
        case_ids=summary["case_ids"],
        processing_ms=t_ms,
    )


@router.get(
    "/template",
    summary="Download CSV upload template",
    description="Returns a sample CSV template hospitals can use to format their data.",
)
def download_template(file_type: str = "dispenses"):
    """Return a sample CSV template."""
    from fastapi.responses import StreamingResponse

    if file_type == "claims":
        header = "ndc_11,patient_id,service_date,payer_type,billed_amount,paid_amount,claim_number,covered_entity_id"
        sample = (
            "00069420030,MRN-000001,2025-01-15,medicaid,245.00,196.00,CLM-100000001,\n"
            "00006001754,MRN-000002,2025-01-16,commercial,560.00,504.00,CLM-100000002,\n"
        )
    else:
        header = "ndc_11,patient_id,dispense_date,quantity,days_supply,dispense_type,covered_entity_id"
        sample = (
            "00069420030,MRN-000001,2025-01-14,30,30,retail,\n"
            "00006001754,MRN-000002,2025-01-15,90,90,mail_order,\n"
        )

    content = f"{header}\n{sample}"

    return StreamingResponse(
        io.StringIO(content),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="evidentrx_{file_type}_template.csv"'},
    )
