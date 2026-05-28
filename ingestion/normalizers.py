import hashlib
import re
from datetime import date, datetime
from typing import Any, Optional

import pandas as pd


def normalize_ndc_11(ndc: Any) -> Optional[str]:
    """Any FDA NDC format → 11-digit 5-4-2, no hyphens."""
    if ndc is None or (isinstance(ndc, float) and pd.isna(ndc)):
        return None
    parts = str(ndc).strip().split("-")
    if len(parts) == 3:
        return parts[0].zfill(5) + parts[1].zfill(4) + parts[2].zfill(2)
    if len(parts) == 2:
        return parts[0].zfill(5) + parts[1].zfill(4) + "00"
    digits = re.sub(r"\D", "", str(ndc))
    return digits.zfill(11)[:11] if digits else None


def record_hash(*fields: Any) -> str:
    val = "|".join("" if f is None else str(f) for f in fields)
    return hashlib.sha256(val.encode()).hexdigest()[:20]


def parse_date(val: Any) -> Optional[date]:
    if val is None:
        return None
    if isinstance(val, float) and pd.isna(val):
        return None
    if hasattr(val, "date"):
        return val.date()
    if isinstance(val, date):
        return val
    try:
        return datetime.fromisoformat(str(val).replace("Z", "+00:00")).date()
    except Exception:
        return None


def clean_str(val: Any, maxlen: Optional[int] = None) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, float) and pd.isna(val):
        return None
    s = str(val).strip()
    if not s or s.lower() in ("nan", "none", "nat"):
        return None
    return s[:maxlen] if maxlen else s


def filing_period_from_filename(path: str) -> tuple[str, date, Optional[date]]:
    """
    Extract filing_period label and start/end dates from filename like:
    340B_Medicaid_Exclusion_File_for_20260401-20260630.xlsx → ('2026Q2', date(2026,4,1), date(2026,6,30))
    """
    m = re.search(r"(\d{8})-(\d{8})", path)
    if not m:
        raise ValueError(f"Cannot extract period from filename: {path}")
    start = datetime.strptime(m.group(1), "%Y%m%d").date()
    end = datetime.strptime(m.group(2), "%Y%m%d").date()
    q = (start.month - 1) // 3 + 1
    label = f"{start.year}Q{q}"
    return label, start, end
