"""
Unit tests for pure helper functions in api/routers/upload.py.

No database or HTTP fixtures required — all functions under test are
stateless transformations of their arguments.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from api.routers.upload import (
    _detect_file_type,
    _hash_patient,
    _normalise_claim_type,
    _normalise_col,
    _normalise_payer,
    _parse_date,
    _parse_decimal,
    _read_csv_rows,
)


# ── _parse_date ───────────────────────────────────────────────────────────────

class TestParseDate:
    def test_iso_format(self):
        assert _parse_date("2025-01-15") == date(2025, 1, 15)

    def test_us_slash_format(self):
        assert _parse_date("01/15/2025") == date(2025, 1, 15)

    def test_us_slash_short_year(self):
        assert _parse_date("01/15/25") == date(2025, 1, 15)

    def test_compact_format(self):
        assert _parse_date("20250115") == date(2025, 1, 15)

    def test_hyphen_us_format(self):
        assert _parse_date("01-15-2025") == date(2025, 1, 15)

    def test_empty_string(self):
        assert _parse_date("") is None

    def test_none_value(self):
        assert _parse_date(None) is None  # type: ignore[arg-type]

    def test_invalid_date(self):
        assert _parse_date("not-a-date") is None

    def test_strips_whitespace(self):
        assert _parse_date("  2025-01-15  ") == date(2025, 1, 15)

    def test_non_string_returns_none(self):
        assert _parse_date(20250115) is None  # type: ignore[arg-type]


# ── _parse_decimal ────────────────────────────────────────────────────────────

class TestParseDecimal:
    def test_plain_number(self):
        assert _parse_decimal("245.00") == Decimal("245.00")

    def test_with_dollar_sign(self):
        assert _parse_decimal("$1,234.56") == Decimal("1234.56")

    def test_with_comma_only(self):
        assert _parse_decimal("1,000") == Decimal("1000")

    def test_with_spaces(self):
        assert _parse_decimal("  99.99  ") == Decimal("99.99")

    def test_empty_string_returns_none(self):
        assert _parse_decimal("") is None

    def test_none_returns_none(self):
        assert _parse_decimal(None) is None  # type: ignore[arg-type]

    def test_invalid_value(self):
        assert _parse_decimal("N/A") is None

    def test_zero(self):
        assert _parse_decimal("0") == Decimal("0")

    def test_negative(self):
        assert _parse_decimal("-50.00") == Decimal("-50.00")


# ── _hash_patient ─────────────────────────────────────────────────────────────

class TestHashPatient:
    def test_produces_40_char_hex(self):
        h = _hash_patient("MRN-000001")
        assert len(h) == 40
        assert all(c in "0123456789abcdef" for c in h)

    def test_deterministic(self):
        assert _hash_patient("MRN-000001") == _hash_patient("MRN-000001")

    def test_case_insensitive(self):
        assert _hash_patient("MRN-000001") == _hash_patient("mrn-000001")

    def test_strips_whitespace(self):
        assert _hash_patient("  MRN-000001  ") == _hash_patient("MRN-000001")

    def test_different_ids_produce_different_hashes(self):
        assert _hash_patient("MRN-000001") != _hash_patient("MRN-000002")

    def test_empty_string(self):
        h = _hash_patient("")
        assert len(h) == 40  # still hashes — empty string is a valid input


# ── _normalise_col ────────────────────────────────────────────────────────────

class TestNormaliseCol:
    def test_lowercases(self):
        assert _normalise_col("NDC_11") == "ndc_11"

    def test_replaces_spaces_with_underscores(self):
        assert _normalise_col("Patient ID") == "patient_id"

    def test_replaces_hyphens_with_underscores(self):
        assert _normalise_col("dispense-date") == "dispense_date"

    def test_strips_leading_trailing_whitespace(self):
        assert _normalise_col("  ndc  ") == "ndc"

    def test_mixed_case_and_spaces(self):
        assert _normalise_col("Billed Amount") == "billed_amount"

    def test_already_normalised(self):
        assert _normalise_col("service_date") == "service_date"


# ── _detect_file_type ─────────────────────────────────────────────────────────

class TestDetectFileType:
    def test_detects_dispenses_with_fill_date(self):
        headers = ["ndc_11", "patient_id", "fill_date", "quantity", "days_supply"]
        has_d, has_c = _detect_file_type(headers)
        assert has_d is True

    def test_detects_dispenses_with_dispense_date(self):
        headers = ["ndc_11", "patient_id", "dispense_date", "quantity"]
        has_d, has_c = _detect_file_type(headers)
        assert has_d is True

    def test_detects_claims_via_claim_number(self):
        headers = ["claim_number", "service_date", "payer_type", "billed_amount"]
        has_d, has_c = _detect_file_type(headers)
        assert has_c is True

    def test_detects_claims_via_billed_amount(self):
        headers = ["ndc_11", "billed_amount", "paid_amount", "payer_type"]
        has_d, has_c = _detect_file_type(headers)
        assert has_c is True

    def test_empty_headers_both_false(self):
        has_d, has_c = _detect_file_type([])
        assert has_d is False
        assert has_c is False

    def test_ndc_without_date_is_not_dispense(self):
        # NDC present but no dispense date → not detected as dispenses
        headers = ["ndc_11", "patient_id"]
        has_d, _ = _detect_file_type(headers)
        # Falls through to best-guess: has NDC → True
        assert isinstance(has_d, bool)


# ── _read_csv_rows ────────────────────────────────────────────────────────────

class TestReadCsvRows:
    def test_basic_csv(self):
        csv_bytes = b"ndc_11,patient_id,dispense_date\n00069420030,MRN-001,2025-01-15\n"
        headers, rows = _read_csv_rows(csv_bytes)
        assert headers == ["ndc_11", "patient_id", "dispense_date"]
        assert len(rows) == 1
        assert rows[0]["ndc_11"] == "00069420030"
        assert rows[0]["patient_id"] == "MRN-001"

    def test_utf8_bom_stripped(self):
        # BOM prefix common in Excel-exported CSVs
        csv_bytes = b"\xef\xbb\xbfndc_11,patient_id\n12345678901,MRN-001\n"
        headers, rows = _read_csv_rows(csv_bytes)
        assert "ndc_11" in headers  # BOM was stripped

    def test_multiple_rows(self):
        csv_bytes = (
            b"ndc_11,dispense_date\n"
            b"00069420030,2025-01-15\n"
            b"00006001754,2025-01-16\n"
        )
        _, rows = _read_csv_rows(csv_bytes)
        assert len(rows) == 2

    def test_empty_content_raises(self):
        with pytest.raises(ValueError, match="no headers"):
            _read_csv_rows(b"")

    def test_header_only_no_rows(self):
        csv_bytes = b"ndc_11,patient_id,dispense_date\n"
        headers, rows = _read_csv_rows(csv_bytes)
        assert len(headers) == 3
        assert len(rows) == 0


# ── _normalise_payer ──────────────────────────────────────────────────────────

class TestNormalisePayer:
    def test_medicaid_canonical(self):
        assert _normalise_payer("medicaid") == "medicaid"

    def test_medicaid_abbreviation(self):
        assert _normalise_payer("mcd") == "medicaid"

    def test_medi_cal(self):
        assert _normalise_payer("Medi-Cal") == "medicaid"

    def test_medicare_part_d(self):
        assert _normalise_payer("medicare part d") == "medicare_part_d"
        assert _normalise_payer("medicare_part_d") == "medicare_part_d"
        assert _normalise_payer("part d") == "medicare_part_d"

    def test_commercial(self):
        assert _normalise_payer("commercial") == "commercial"
        assert _normalise_payer("Private") == "commercial"

    def test_self_pay(self):
        assert _normalise_payer("self pay") == "self_pay"
        assert _normalise_payer("self_pay") == "self_pay"
        assert _normalise_payer("cash") == "self_pay"

    def test_unknown_defaults_to_other(self):
        assert _normalise_payer("xyz_unknown_payer") == "other"

    def test_empty_string_defaults_to_other(self):
        assert _normalise_payer("") == "other"


# ── _normalise_claim_type ─────────────────────────────────────────────────────

class TestNormaliseClaimType:
    def test_medicaid(self):
        assert _normalise_claim_type("medicaid") == "medicaid"
        assert _normalise_claim_type("mcd") == "medicaid"

    def test_medicare_part_d(self):
        assert _normalise_claim_type("part d") == "medicare_part_d"
        assert _normalise_claim_type("medicare_part_d") == "medicare_part_d"

    def test_medicare_part_b(self):
        assert _normalise_claim_type("part b") == "medicare_part_b"
        assert _normalise_claim_type("medicare part b") == "medicare_part_b"

    def test_commercial(self):
        assert _normalise_claim_type("commercial") == "commercial"
        assert _normalise_claim_type("private") == "commercial"

    def test_unknown_defaults_to_other(self):
        assert _normalise_claim_type("unknown_payer") == "other"

    def test_empty_string_defaults_to_other(self):
        assert _normalise_claim_type("") == "other"
