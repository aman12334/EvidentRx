"""
Unit tests for upload Pydantic response models.
"""
from __future__ import annotations

from api.routers.upload import (
    BatchHistoryItem,
    ColumnMapping,
    FindingSummary,
    UploadPreview,
    UploadResult,
)


class TestFindingSummary:
    def test_basic(self):
        f = FindingSummary(
            rule_code="DD-001",
            description="Duplicate discount on Medicaid claim",
            count=5,
            severity="critical",
        )
        assert f.rule_code == "DD-001"
        assert f.count == 5
        assert f.severity == "critical"


class TestUploadResult:
    def _make(self, **kwargs) -> UploadResult:
        defaults = {
            "upload_id": "test-upload-id",
            "batch_id": "test-batch-id",
            "status": "complete",
            "message": "Upload processed successfully.",
            "rows_parsed": 100,
            "dispenses_inserted": 80,
            "claims_inserted": 20,
            "split_billing_rows": 15,
            "cases_created": 2,
            "total_findings": 7,
            "critical_findings": 1,
            "high_findings": 3,
            "estimated_exposure": 4200.0,
            "findings_by_rule": [],
            "case_ids": ["case-001", "case-002"],
            "processing_ms": 342,
        }
        defaults.update(kwargs)
        return UploadResult(**defaults)

    def test_complete_result(self):
        r = self._make()
        assert r.status == "complete"
        assert r.rows_parsed == 100
        assert r.processing_ms == 342

    def test_no_findings(self):
        r = self._make(
            status="no_findings",
            total_findings=0,
            critical_findings=0,
            high_findings=0,
            estimated_exposure=None,
            case_ids=[],
        )
        assert r.status == "no_findings"
        assert r.estimated_exposure is None

    def test_with_finding_summaries(self):
        fs = FindingSummary(
            rule_code="MO-001",
            description="Medicaid overlap",
            count=3,
            severity="high",
        )
        r = self._make(findings_by_rule=[fs])
        assert len(r.findings_by_rule) == 1
        assert r.findings_by_rule[0].rule_code == "MO-001"


class TestColumnMapping:
    def test_mapped_column(self):
        m = ColumnMapping(
            source_column="NDC",
            target_field="ndc_11",
            mapped=True,
            sample_value="00069420030",
        )
        assert m.mapped is True
        assert m.sample_value == "00069420030"

    def test_unmapped_column(self):
        m = ColumnMapping(
            source_column="internal_code",
            target_field="(ignored)",
            mapped=False,
        )
        assert m.mapped is False
        assert m.sample_value is None


class TestUploadPreview:
    def test_dispense_preview(self):
        p = UploadPreview(
            filename="dispenses.csv",
            rows_detected=200,
            file_type="dispenses",
            column_mappings=[
                ColumnMapping(source_column="ndc_11", target_field="ndc_11", mapped=True),
                ColumnMapping(source_column="fill_date", target_field="dispense_date", mapped=True),
            ],
            unmapped_columns=[],
            sample_rows=[{"ndc_11": "00069420030", "fill_date": "2025-01-15"}],
            warnings=[],
        )
        assert p.file_type == "dispenses"
        assert p.rows_detected == 200
        assert len(p.warnings) == 0

    def test_preview_with_warnings(self):
        p = UploadPreview(
            filename="data.csv",
            rows_detected=50,
            file_type="dispenses",
            column_mappings=[],
            unmapped_columns=["unknown_col"],
            sample_rows=[],
            warnings=["No NDC column detected — 'ndc_11' or 'ndc' required."],
        )
        assert len(p.warnings) == 1
        assert "NDC" in p.warnings[0]


class TestBatchHistoryItem:
    def test_completed_batch(self):
        b = BatchHistoryItem(
            batch_id="abc-123",
            filename="dispenses_jan.csv",
            status="complete",
            record_count=500,
            started_at="2025-01-15T10:00:00",
            completed_at="2025-01-15T10:00:05",
            findings_count=12,
        )
        assert b.status == "complete"
        assert b.findings_count == 12

    def test_processing_batch(self):
        b = BatchHistoryItem(
            batch_id="def-456",
            filename="claims_jan.csv",
            status="processing",
            record_count=300,
            started_at="2025-01-15T11:00:00",
            completed_at=None,
            findings_count=None,
        )
        assert b.completed_at is None
        assert b.findings_count is None
