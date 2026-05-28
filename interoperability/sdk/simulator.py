"""
Ingestion simulators for development and integration testing.

Generates realistic synthetic healthcare records for each source format
without requiring live EHR / pharmacy connections. Designed for:
  - Local development without external dependencies
  - CI/CD pipeline integration testing
  - Hackathon demos and product showcases
  - Load testing the ingestion pipeline

Simulators produce records indistinguishable from real source data
(same structure, valid field formats, realistic values), but all
patient identifiers are synthetic and carry no real PHI.
"""

from __future__ import annotations

import hashlib
import random
import string
import uuid
from datetime import date, datetime, timedelta, timezone
from typing   import Any, Iterator, Optional


# ── Base simulator ────────────────────────────────────────────────────────────

class BaseSimulator:
    """Shared utilities for all simulators."""

    def __init__(self, seed: Optional[int] = None) -> None:
        self._rng = random.Random(seed)

    def _rand_ndc(self) -> str:
        """Generate a random 11-digit NDC."""
        return "".join(self._rng.choices(string.digits, k=11))

    def _rand_npi(self) -> str:
        """Generate a random 10-digit NPI."""
        return "".join(self._rng.choices(string.digits, k=10))

    def _rand_date(self, days_back: int = 365) -> str:
        """Random date within the last N days."""
        delta = timedelta(days=self._rng.randint(0, days_back))
        d = date.today() - delta
        return d.strftime("%Y-%m-%d")

    def _rand_patient_hash(self, tenant_id: str) -> str:
        """Generate a synthetic patient ID hash."""
        patient_id = f"SIM-{self._rng.randint(100000, 999999)}"
        payload    = f"{tenant_id}:Patient/{patient_id}:evidentrx_phi_hash_v1".encode()
        return hashlib.sha256(payload).hexdigest()[:32]

    def _rand_amount(self, low: float = 1.0, high: float = 500.0) -> float:
        return round(self._rng.uniform(low, high), 2)

    def _rand_qty(self) -> float:
        return float(self._rng.choice([30, 60, 90]))

    def _rand_days_supply(self) -> int:
        return self._rng.choice([30, 60, 90])

    def _rand_status(self, valid: list[str]) -> str:
        return self._rng.choice(valid)


# ── FHIR simulator ────────────────────────────────────────────────────────────

class FHIRSimulator(BaseSimulator):
    """Generates synthetic FHIR R4 resource dicts."""

    def medication_dispense(self, tenant_id: str) -> dict[str, Any]:
        ndc   = self._rand_ndc()
        fhir_id = str(uuid.uuid4())
        return {
            "resourceType":    "MedicationDispense",
            "id":              fhir_id,
            "meta":            {"versionId": "1", "lastUpdated": datetime.now(tz=timezone.utc).isoformat()},
            "status":          self._rand_status(["completed", "in-progress"]),
            "medicationCodeableConcept": {
                "coding": [{
                    "system":  "http://hl7.org/fhir/sid/ndc",
                    "code":    ndc,
                    "display": f"Drug {ndc[:5]}",
                }]
            },
            "subject":         {"reference": f"Patient/{uuid.uuid4()}"},
            "quantity":        {"value": self._rand_qty(), "unit": "tablet"},
            "daysSupply":      {"value": self._rand_days_supply(), "unit": "days"},
            "whenHandedOver":  self._rand_date() + "T00:00:00Z",
            "performer":       [{"actor": {"reference": f"Organization/{uuid.uuid4()}"}}],
        }

    def claim(self, tenant_id: str) -> dict[str, Any]:
        fhir_id = str(uuid.uuid4())
        return {
            "resourceType":  "Claim",
            "id":            fhir_id,
            "meta":          {"versionId": "1"},
            "status":        "active",
            "use":           "claim",
            "patient":       {"reference": f"Patient/{uuid.uuid4()}"},
            "insurer":       {"display": "Medicaid" if self._rng.random() < 0.4 else "BCBS"},
            "provider":      {
                "identifier": [{"system": "http://hl7.org/fhir/sid/us-npi", "value": self._rand_npi()}]
            },
            "billablePeriod": {
                "start": self._rand_date(),
                "end":   self._rand_date(30),
            },
            "total":         {"value": self._rand_amount(), "currency": "USD"},
            "diagnosis":     [],
            "item":          [{
                "sequence":        1,
                "productOrService": {
                    "coding": [{
                        "system": "http://hl7.org/fhir/sid/ndc",
                        "code":   self._rand_ndc(),
                    }]
                },
            }],
        }

    def patient(self, tenant_id: str) -> dict[str, Any]:
        return {
            "resourceType": "Patient",
            "id":           str(uuid.uuid4()),
            "meta":         {"versionId": "1"},
            "identifier":   [{"value": f"MRN{self._rng.randint(10000, 99999)}"}],
            "gender":       self._rng.choice(["male", "female", "unknown"]),
            "birthDate":    f"{self._rng.randint(1940, 2005)}-01-01",
            "active":       True,
        }

    def batch(
        self,
        resource_type: str,
        count:         int,
        tenant_id:     str,
    ) -> list[dict[str, Any]]:
        """Generate a batch of synthetic FHIR resources."""
        generators = {
            "MedicationDispense": self.medication_dispense,
            "Claim":              self.claim,
            "Patient":            self.patient,
        }
        gen = generators.get(resource_type)
        if not gen:
            raise ValueError(f"No simulator for FHIR resource type {resource_type!r}")
        return [gen(tenant_id) for _ in range(count)]


# ── HL7 v2 simulator ──────────────────────────────────────────────────────────

class HL7Simulator(BaseSimulator):
    """Generates synthetic HL7 v2.5 message strings."""

    def adt_a01(self, tenant_id: str) -> str:
        """Generate a synthetic ADT^A01 admit message."""
        now     = datetime.now(tz=timezone.utc).strftime("%Y%m%d%H%M%S")
        mrn     = f"SIM{self._rng.randint(100000, 999999)}"
        msg_id  = f"SIM{uuid.uuid4().hex[:8].upper()}"
        npi     = self._rand_npi()

        return (
            f"MSH|^~\\&|SIMEHR|SIMFAC|EVIDENTRX|HQ|{now}||ADT^A01|{msg_id}|P|2.5\r"
            f"EVN|A01|{now}\r"
            f"PID|1||{mrn}^^^SIMFAC^MRN||DOE^JANE|||F|||123 MAIN ST^^ANYTOWN^CA^90210\r"
            f"PV1|1|I|2NORTH^201^A|||{npi}^Smith^John^^^Dr.^MD^NPI||"
            f"{npi}^Smith^John^^^Dr.^MD^NPI|||||||{npi}^Smith^John^^^Dr.^MD^NPI"
            f"|IP||1|||||||||||||||||||{now}\r"
        )

    def rde_o11(self, tenant_id: str) -> str:
        """Generate a synthetic RDE^O11 dispense message."""
        now    = datetime.now(tz=timezone.utc).strftime("%Y%m%d%H%M%S")
        mrn    = f"SIM{self._rng.randint(100000, 999999)}"
        msg_id = f"SIM{uuid.uuid4().hex[:8].upper()}"
        ndc    = self._rand_ndc()
        npi    = self._rand_npi()
        qty    = int(self._rand_qty())
        days   = self._rand_days_supply()

        return (
            f"MSH|^~\\&|SIMRX|SIMPHARM|EVIDENTRX|HQ|{now}||RDE^O11|{msg_id}|P|2.5\r"
            f"PID|1||{mrn}^^^SIMPHARM^MRN\r"
            f"ORC|RE|{msg_id}|||CM||||{now}|||{npi}^Prescriber^Test^^^Dr.^^NPI\r"
            f"RXE|{now}|{ndc}^SimDrug^NDC|{qty}||TAB||||{npi}^SimPharm^Test^^^NPI\r"
            f"RXD|1|{ndc}^SimDrug^NDC|{now}|{qty}|TAB|0|||{days}|{msg_id}\r"
        )

    def batch(self, msg_type: str, count: int, tenant_id: str) -> list[str]:
        """Generate a batch of synthetic HL7 messages."""
        generators = {
            "ADT^A01": self.adt_a01,
            "RDE^O11": self.rde_o11,
        }
        gen = generators.get(msg_type)
        if not gen:
            raise ValueError(f"No HL7 simulator for {msg_type!r}")
        return [gen(tenant_id) for _ in range(count)]


# ── EDI X12 simulator ─────────────────────────────────────────────────────────

class EDISimulator(BaseSimulator):
    """Generates synthetic X12 837P EDI claim strings."""

    def claim_837p(self, tenant_id: str) -> str:
        """Generate a minimal valid 837P pharmacy claim."""
        now       = datetime.now(tz=timezone.utc).strftime("%y%m%d")
        ctrl      = f"{self._rng.randint(100000000, 999999999):09d}"
        gs_ctrl   = f"{self._rng.randint(10000, 99999)}"
        st_ctrl   = f"{self._rng.randint(1000, 9999):04d}"
        claim_id  = f"CLM{self._rng.randint(100000, 999999)}"
        npi       = self._rand_npi()
        ndc       = self._rand_ndc()
        amount    = f"{self._rand_amount():.2f}"
        svc_date  = date.today().strftime("%Y%m%d")
        member_id = f"MBR{self._rng.randint(100000, 999999)}"

        return (
            f"ISA*00*          *00*          *ZZ*SIMSUBMITTER    *ZZ*SIMRECEIVER    "
            f"*{now}*1200*^*00501*{ctrl}*0*P*:~\n"
            f"GS*HC*SIMSUBMITTER*SIMRECEIVER*{now}*1200*{gs_ctrl}*X*005010X222A1~\n"
            f"ST*837*{st_ctrl}*005010X222A1~\n"
            f"BHT*0019*00*{ctrl}*{svc_date}*1200*CH~\n"
            f"NM1*41*2*SIM PHARMACY LLC*****46*{npi}~\n"
            f"NM1*40*2*MEDICAID PAYER*****46*MCDXX~\n"
            f"NM1*85*2*SIM PHARMACY LLC*****XX*{npi}~\n"
            f"NM1*IL*1*DOE*JANE****MI*{member_id}~\n"
            f"CLM*{claim_id}*{amount}***11:B:1*Y*A*Y*I~\n"
            f"DTP*472*D8*{svc_date}~\n"
            f"LIN*1*N4*{ndc}~\n"
            f"SV1*N4:{ndc}*{amount}*UN*1***1~\n"
            f"SE*12*{st_ctrl}~\n"
            f"GE*1*{gs_ctrl}~\n"
            f"IEA*1*{ctrl}~\n"
        )

    def batch(self, count: int, tenant_id: str) -> list[str]:
        """Generate a batch of synthetic 837P EDI strings."""
        return [self.claim_837p(tenant_id) for _ in range(count)]
