"""
EntityMemory — persistent entity history for investigation agents.

Tracks a covered entity's risk history, recurring violations, and
recurring pharmacy/NDC involvement across investigation cases.

Agents READ this memory to inform their analysis context — they never
write compliance findings through this module.  All data sourced from
confirmed audit findings and persisted intelligence tables.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class EntityHistory:
    entity_id:            str
    entity_name:          str
    total_findings:       int
    critical_findings:    int
    high_findings:        int
    open_cases:           int
    escalated_cases:      int
    rule_code_frequency:  dict[str, int]     # rule_code → count, top violations
    recurring_pharmacies: list[str]          # pharmacy_ids seen 2+ times
    recurring_ndcs:       list[str]          # NDC-11s seen 2+ times
    risk_trend:           str                # improving / stable / worsening / critical
    latest_score:         float | None    # most recent composite score
    first_finding_date:   date | None
    latest_finding_date:  date | None
    lookback_days:        int = 90


@dataclass
class RecurringPattern:
    entity_id:   str
    pattern_type: str   # "pharmacy" | "ndc" | "rule_code"
    subject_id:  str    # pharmacy_id / ndc_11 / rule_code
    subject_label: str
    occurrence_count: int
    case_ids:    list[str]


class EntityMemory:
    """
    Loads and structures entity history for agent consumption.

    Usage::

        mem = EntityMemory()
        history = mem.load(session, entity_id="...", lookback_days=90)
        patterns = mem.recurring_patterns(session, entity_id="...")
    """

    def load(
        self,
        session: Session,
        entity_id: str,
        lookback_days: int = 90,
    ) -> EntityHistory:
        """
        Loads a comprehensive history for a covered entity.
        """
        since = (date.today() - timedelta(days=lookback_days)).isoformat()

        # Finding counts by severity
        counts = session.execute(text("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE severity = 'critical') AS critical,
                COUNT(*) FILTER (WHERE severity = 'high')     AS high,
                MIN(created_at::date) AS first_date,
                MAX(created_at::date) AS latest_date
            FROM audit.audit_findings
            WHERE covered_entity_id = :eid::uuid
              AND created_at::date >= :since::date
        """), {"eid": entity_id, "since": since}).mappings().fetchone()

        # Case counts
        case_counts = session.execute(text("""
            SELECT
                COUNT(*) AS open_cases,
                COUNT(*) FILTER (WHERE status = 'escalated') AS escalated_cases
            FROM audit.investigation_cases
            WHERE covered_entity_id = :eid::uuid
              AND status NOT IN ('closed', 'resolved')
        """), {"eid": entity_id}).mappings().fetchone()

        # Rule code frequency
        rule_rows = session.execute(text("""
            SELECT rule_code, COUNT(*) AS cnt
            FROM audit.audit_findings
            WHERE covered_entity_id = :eid::uuid
              AND created_at::date >= :since::date
            GROUP BY rule_code
            ORDER BY cnt DESC
            LIMIT 10
        """), {"eid": entity_id, "since": since}).mappings().fetchall()

        rule_freq = {r["rule_code"]: int(r["cnt"]) for r in rule_rows}

        # Recurring pharmacies (appear in 2+ findings)
        pharm_rows = session.execute(text("""
            SELECT ev.pharmacy_id, COUNT(*) AS cnt
            FROM audit.audit_findings af
            CROSS JOIN LATERAL (
                SELECT af.evidence_payload->>'pharmacy_id' AS pharmacy_id
            ) ev
            WHERE af.covered_entity_id = :eid::uuid
              AND ev.pharmacy_id IS NOT NULL
              AND af.created_at::date >= :since::date
            GROUP BY ev.pharmacy_id
            HAVING COUNT(*) >= 2
            ORDER BY cnt DESC
            LIMIT 10
        """), {"eid": entity_id, "since": since}).mappings().fetchall()
        recurring_pharmacies = [r["pharmacy_id"] for r in pharm_rows if r["pharmacy_id"]]

        # Recurring NDCs
        ndc_rows = session.execute(text("""
            SELECT ndc_11, COUNT(*) AS cnt
            FROM audit.audit_findings af
            LEFT JOIN ops.split_billing sb ON af.split_billing_id = sb.split_billing_id
            WHERE af.covered_entity_id = :eid::uuid
              AND sb.ndc_11 IS NOT NULL
              AND af.created_at::date >= :since::date
            GROUP BY sb.ndc_11
            HAVING COUNT(*) >= 2
            ORDER BY cnt DESC
            LIMIT 10
        """), {"eid": entity_id, "since": since}).mappings().fetchall()
        recurring_ndcs = [r["ndc_11"] for r in ndc_rows if r["ndc_11"]]

        # Latest composite score
        score_row = session.execute(text("""
            SELECT composite_score, trend_direction
            FROM audit.entity_risk_scores
            WHERE entity_id = :eid AND entity_type = 'covered_entity'
            ORDER BY score_date DESC
            LIMIT 1
        """), {"eid": entity_id}).mappings().fetchone()

        latest_score = float(score_row["composite_score"]) if score_row else None
        risk_trend   = score_row["trend_direction"] if score_row else "stable"

        # Entity name
        name_row = session.execute(text("""
            SELECT entity_name FROM ref.covered_entities
            WHERE ce_id = :eid::uuid AND is_current = TRUE
        """), {"eid": entity_id}).mappings().fetchone()
        entity_name = name_row["entity_name"] if name_row else entity_id

        return EntityHistory(
            entity_id=entity_id,
            entity_name=entity_name,
            total_findings=int(counts["total"] or 0),
            critical_findings=int(counts["critical"] or 0),
            high_findings=int(counts["high"] or 0),
            open_cases=int(case_counts["open_cases"] or 0),
            escalated_cases=int(case_counts["escalated_cases"] or 0),
            rule_code_frequency=rule_freq,
            recurring_pharmacies=recurring_pharmacies,
            recurring_ndcs=recurring_ndcs,
            risk_trend=risk_trend,
            latest_score=latest_score,
            first_finding_date=counts["first_date"],
            latest_finding_date=counts["latest_date"],
            lookback_days=lookback_days,
        )

    def recurring_patterns(
        self,
        session: Session,
        entity_id: str,
        min_occurrences: int = 2,
        lookback_days: int = 90,
    ) -> list[RecurringPattern]:
        """
        Returns structured recurring pattern records for an entity —
        useful for injecting into agent system prompts as structured context.
        """
        history = self.load(session, entity_id, lookback_days)
        patterns: list[RecurringPattern] = []

        for pid in history.recurring_pharmacies:
            patterns.append(RecurringPattern(
                entity_id=entity_id,
                pattern_type="pharmacy",
                subject_id=pid,
                subject_label=pid,
                occurrence_count=0,   # full count available via load()
                case_ids=[],
            ))

        for ndc in history.recurring_ndcs:
            patterns.append(RecurringPattern(
                entity_id=entity_id,
                pattern_type="ndc",
                subject_id=ndc,
                subject_label=ndc,
                occurrence_count=0,
                case_ids=[],
            ))

        for rule_code, cnt in history.rule_code_frequency.items():
            if cnt >= min_occurrences:
                patterns.append(RecurringPattern(
                    entity_id=entity_id,
                    pattern_type="rule_code",
                    subject_id=rule_code,
                    subject_label=rule_code,
                    occurrence_count=cnt,
                    case_ids=[],
                ))

        return patterns

    def to_agent_context(self, history: EntityHistory) -> dict:
        """
        Returns a dict suitable for injection into an agent's input context.
        Keys match the structured context blocks used in agent system prompts.
        """
        return {
            "entity_history": {
                "entity_id":     history.entity_id,
                "entity_name":   history.entity_name,
                "lookback_days": history.lookback_days,
                "findings": {
                    "total":    history.total_findings,
                    "critical": history.critical_findings,
                    "high":     history.high_findings,
                },
                "cases": {
                    "open":      history.open_cases,
                    "escalated": history.escalated_cases,
                },
                "top_rule_codes":        history.rule_code_frequency,
                "recurring_pharmacies":  history.recurring_pharmacies,
                "recurring_ndcs":        history.recurring_ndcs,
                "risk_trend":            history.risk_trend,
                "composite_score":       history.latest_score,
            }
        }
