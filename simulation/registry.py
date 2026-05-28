"""
ReferenceRegistry — loads real reference data from DB into memory
for use by the simulation generators.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class CERecord:
    ce_id: str
    hrsa_id: str
    state_code: str | None
    program_participation_start: date | None
    program_termination_date: date | None
    is_active: bool


@dataclass
class CPRecord:
    cp_id: str
    covered_entity_id: str
    hrsa_id: str
    pharmacy_name: str
    state_code: str | None
    registration_date: date | None
    termination_date: date | None


@dataclass
class NDCRecord:
    drug_id: str
    ndc_11: str
    nonproprietary_name: str | None
    dea_schedule: str | None


@dataclass
class ExclusionRecord:
    hrsa_id: str
    state_code: str
    exclusion_type: str   # carve_out | carve_in | not_elected
    period_start: date
    period_end: date | None


@dataclass
class ReferenceRegistry:
    ces: list[CERecord] = field(default_factory=list)
    cps_by_ce: dict[str, list[CPRecord]] = field(default_factory=dict)
    ndcs: list[NDCRecord] = field(default_factory=list)
    exclusions_by_hrsa: dict[str, list[ExclusionRecord]] = field(default_factory=dict)

    @classmethod
    def load(cls, session: Session, n_ces: int, n_ndcs: int) -> ReferenceRegistry:
        reg = cls()

        # Active covered entities
        rows = session.execute(text("""
            SELECT ce_id::text, hrsa_id, state_code,
                   program_participation_start, program_termination_date, is_active
            FROM ref.covered_entities
            WHERE is_current = TRUE AND is_active = TRUE
            ORDER BY random()
            LIMIT :n
        """), {"n": n_ces}).fetchall()

        for r in rows:
            reg.ces.append(CERecord(
                ce_id=r[0], hrsa_id=r[1], state_code=r[2],
                program_participation_start=r[3], program_termination_date=r[4],
                is_active=r[5],
            ))

        ce_ids = [c.ce_id for c in reg.ces]
        logger.info("Registry: loaded %d CEs", len(reg.ces))

        # Contract pharmacies for selected CEs
        if ce_ids:
            rows = session.execute(text("""
                SELECT cp_id::text, covered_entity_id::text, hrsa_id,
                       pharmacy_name, state_code, registration_date, termination_date
                FROM ref.contract_pharmacies
                WHERE covered_entity_id = ANY(:ids::uuid[])
                  AND is_current = TRUE AND is_active = TRUE
            """), {"ids": ce_ids}).fetchall()

            for r in rows:
                cp = CPRecord(
                    cp_id=r[0], covered_entity_id=r[1], hrsa_id=r[2],
                    pharmacy_name=r[3], state_code=r[4],
                    registration_date=r[5], termination_date=r[6],
                )
                reg.cps_by_ce.setdefault(r[1], []).append(cp)

        total_cp = sum(len(v) for v in reg.cps_by_ce.values())
        logger.info("Registry: loaded %d contract pharmacies", total_cp)

        # Active NDC drugs — prefer those with known names
        rows = session.execute(text("""
            SELECT drug_id::text, ndc_11, nonproprietary_name, dea_schedule
            FROM ref.ndc_drugs
            WHERE is_active = TRUE AND nonproprietary_name IS NOT NULL
            ORDER BY random()
            LIMIT :n
        """), {"n": n_ndcs}).fetchall()

        for r in rows:
            reg.ndcs.append(NDCRecord(
                drug_id=r[0], ndc_11=r[1],
                nonproprietary_name=r[2], dea_schedule=r[3],
            ))

        logger.info("Registry: loaded %d NDCs", len(reg.ndcs))

        # Medicaid exclusions for selected CEs
        hrsa_ids = [c.hrsa_id for c in reg.ces]
        if hrsa_ids:
            rows = session.execute(text("""
                SELECT hrsa_id, state_code, exclusion_type, period_start, period_end
                FROM ref.medicaid_exclusions
                WHERE hrsa_id = ANY(:ids)
            """), {"ids": hrsa_ids}).fetchall()

            for r in rows:
                ex = ExclusionRecord(
                    hrsa_id=r[0], state_code=r[1], exclusion_type=r[2],
                    period_start=r[3], period_end=r[4],
                )
                reg.exclusions_by_hrsa.setdefault(r[0], []).append(ex)

        logger.info("Registry: loaded exclusions for %d CEs", len(reg.exclusions_by_hrsa))
        return reg

    def ce_carve_out_on(self, hrsa_id: str, check_date: date) -> bool:
        """Returns True if the CE has an active carve-out election on check_date."""
        for ex in self.exclusions_by_hrsa.get(hrsa_id, []):
            if ex.exclusion_type == "carve_out":
                if ex.period_start <= check_date:
                    if ex.period_end is None or ex.period_end >= check_date:
                        return True
        return False

    def active_cps_for_ce(self, ce_id: str, on_date: date) -> list[CPRecord]:
        """Contract pharmacies active on a given date."""
        result = []
        for cp in self.cps_by_ce.get(ce_id, []):
            if cp.registration_date and cp.registration_date > on_date:
                continue
            if cp.termination_date and cp.termination_date < on_date:
                continue
            result.append(cp)
        return result
