"""
Unit tests for evidence lineage and chain Pydantic schemas.

All tests are pure — no database or HTTP fixtures required.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest

from api.schemas.evidence import (
    ClaimEvent,
    DispenseEvent,
    EvidenceChain,
    EvidenceSnapshot,
    PurchaseEvent,
)

_CE_ID      = uuid.uuid4()
_FINDING_ID = uuid.uuid4()
_PH_ID      = uuid.uuid4()
_TODAY      = date.today()


# ── PurchaseEvent ─────────────────────────────────────────────────────────────

class TestPurchaseEvent:
    def _make(self, **kw) -> PurchaseEvent:
        defaults: dict = {
            "purchase_id":       uuid.uuid4(),
            "covered_entity_id": _CE_ID,
            "ndc_11":            "00069420030",
            "quantity":          100.0,
            "unit_cost":         4.25,
            "purchase_date":     _TODAY,
        }
        defaults.update(kw)
        return PurchaseEvent(**defaults)

    def test_basic_construction(self):
        p = self._make()
        assert p.ndc_11 == "00069420030"
        assert p.quantity == pytest.approx(100.0)
        assert p.unit_cost == pytest.approx(4.25)

    def test_vendor_name_optional(self):
        p = self._make(vendor_name="AmerisourceBergen")
        assert p.vendor_name == "AmerisourceBergen"

    def test_vendor_name_none_by_default(self):
        p = self._make()
        assert p.vendor_name is None

    def test_purchase_date_is_date_type(self):
        p = self._make()
        assert isinstance(p.purchase_date, date)

    def test_from_attributes_enabled(self):
        assert PurchaseEvent.model_config.get("from_attributes") is True


# ── DispenseEvent ─────────────────────────────────────────────────────────────

class TestDispenseEvent:
    def _make(self, **kw) -> DispenseEvent:
        defaults: dict = {
            "dispense_id":       uuid.uuid4(),
            "covered_entity_id": _CE_ID,
            "ndc_11":            "00069420030",
            "quantity":          30.0,
            "dispense_date":     _TODAY,
        }
        defaults.update(kw)
        return DispenseEvent(**defaults)

    def test_basic_construction(self):
        d = self._make()
        assert d.ndc_11 == "00069420030"
        assert d.quantity == pytest.approx(30.0)

    def test_pharmacy_id_optional(self):
        ph_id = uuid.uuid4()
        d = self._make(pharmacy_id=ph_id)
        assert d.pharmacy_id == ph_id

    def test_pharmacy_id_none_by_default(self):
        d = self._make()
        assert d.pharmacy_id is None


# ── ClaimEvent ────────────────────────────────────────────────────────────────

class TestClaimEvent:
    def _make(self, **kw) -> ClaimEvent:
        defaults: dict = {
            "claim_id":          uuid.uuid4(),
            "covered_entity_id": _CE_ID,
            "ndc_11":            "00069420030",
            "claim_date":        _TODAY,
            "billed_amount":     850.00,
            "paid_amount":       700.00,
        }
        defaults.update(kw)
        return ClaimEvent(**defaults)

    def test_basic_construction(self):
        c = self._make()
        assert c.billed_amount == pytest.approx(850.00)
        assert c.paid_amount == pytest.approx(700.00)

    def test_payer_type_optional(self):
        c = self._make(payer_type="medicaid")
        assert c.payer_type == "medicaid"

    def test_payer_type_none_by_default(self):
        c = self._make()
        assert c.payer_type is None

    def test_amounts_can_be_zero(self):
        c = self._make(billed_amount=0.0, paid_amount=0.0)
        assert c.billed_amount == 0.0
        assert c.paid_amount == 0.0


# ── EvidenceChain ─────────────────────────────────────────────────────────────

class TestEvidenceChain:
    def _make(self, **kw) -> EvidenceChain:
        defaults: dict = {
            "finding_id": _FINDING_ID,
            "rule_code":  "DD-001",
            "severity":   "critical",
        }
        defaults.update(kw)
        return EvidenceChain(**defaults)

    def test_minimal_chain(self):
        c = self._make()
        assert c.rule_code == "DD-001"
        assert c.severity == "critical"
        assert c.purchase is None
        assert c.dispense is None
        assert c.claim is None

    def test_notes_defaults_empty_string(self):
        c = self._make()
        assert c.notes == ""

    def test_with_pharmacy_info(self):
        c = self._make(pharmacy_id=str(_PH_ID), pharmacy_name="CVS #4421")
        assert c.pharmacy_name == "CVS #4421"
        assert c.pharmacy_id == str(_PH_ID)

    def test_with_ndc(self):
        c = self._make(ndc_11="00069420030")
        assert c.ndc_11 == "00069420030"

    def test_split_billing_id_optional(self):
        sb_id = uuid.uuid4()
        c = self._make(split_billing_id=sb_id)
        assert c.split_billing_id == sb_id

    def test_full_chain_with_events(self):
        purchase = PurchaseEvent(
            purchase_id=uuid.uuid4(), covered_entity_id=_CE_ID,
            ndc_11="00069420030", quantity=100.0, unit_cost=4.25,
            purchase_date=_TODAY, vendor_name="AmerisourceBergen",
        )
        dispense = DispenseEvent(
            dispense_id=uuid.uuid4(), covered_entity_id=_CE_ID,
            ndc_11="00069420030", quantity=30.0, dispense_date=_TODAY,
        )
        claim = ClaimEvent(
            claim_id=uuid.uuid4(), covered_entity_id=_CE_ID,
            ndc_11="00069420030", claim_date=_TODAY,
            billed_amount=850.0, paid_amount=700.0, payer_type="medicaid",
        )
        c = self._make(purchase=purchase, dispense=dispense, claim=claim,
                       ndc_11="00069420030", pharmacy_name="CVS #4421",
                       notes="Duplicate discount confirmed on Medicaid.")
        assert c.purchase.vendor_name == "AmerisourceBergen"
        assert c.claim.payer_type == "medicaid"
        assert "Duplicate" in c.notes


# ── EvidenceSnapshot ──────────────────────────────────────────────────────────

class TestEvidenceSnapshot:
    def _make_chain(self, rule_code: str = "DD-001") -> EvidenceChain:
        return EvidenceChain(
            finding_id=uuid.uuid4(), rule_code=rule_code, severity="high"
        )

    def test_empty_snapshot(self):
        snap = EvidenceSnapshot(case_id=uuid.uuid4(), total_findings=0)
        assert snap.total_findings == 0
        assert snap.chains == []
        assert snap.linked_pharmacies == []
        assert snap.linked_ndcs == []
        assert snap.linked_entities == []

    def test_with_chains(self):
        chains = [self._make_chain("DD-001"), self._make_chain("MO-002")]
        snap = EvidenceSnapshot(
            case_id=uuid.uuid4(),
            total_findings=2,
            chains=chains,
        )
        assert len(snap.chains) == 2
        assert snap.chains[0].rule_code == "DD-001"

    def test_linked_fields_populated(self):
        snap = EvidenceSnapshot(
            case_id=uuid.uuid4(),
            total_findings=3,
            chains=[self._make_chain()],
            linked_pharmacies=["ph-001", "ph-002"],
            linked_ndcs=["00069420030"],
            linked_entities=[str(_CE_ID)],
        )
        assert len(snap.linked_pharmacies) == 2
        assert "00069420030" in snap.linked_ndcs
        assert str(_CE_ID) in snap.linked_entities

    def test_total_findings_matches_chain_length(self):
        chains = [self._make_chain() for _ in range(5)]
        snap = EvidenceSnapshot(case_id=uuid.uuid4(), total_findings=5, chains=chains)
        assert snap.total_findings == len(snap.chains)
