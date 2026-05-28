"""
Deterministic Rules Engine — Phase 3.

Reads ops.split_billing, evaluates each active compliance rule, writes
confirmed violations to audit.audit_findings.

Architecture constraints:
  - No AI/LLM involvement in this layer.
  - Evidence payloads are immutable after write.
  - Rules are evaluated independently (a single record can trigger multiple rules).
  - Findings are idempotent: existing open findings for the same (split_billing_id, rule_code)
    are skipped to prevent duplicates on re-runs.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from ingestion.base import bulk_insert
from rules_engine.context import RuleContext
from rules_engine.finding_builder import reset_counters
from rules_engine.loader import load_rules, RuleRecord
from rules_engine.query import iter_contexts

import rules_engine.rules.dd_001 as dd_001
import rules_engine.rules.dd_002 as dd_002
import rules_engine.rules.meo_001 as meo_001
import rules_engine.rules.meo_002 as meo_002
import rules_engine.rules.cpe_001 as cpe_001
import rules_engine.rules.cpe_002 as cpe_002
import rules_engine.rules.sb_001 as sb_001
import rules_engine.rules.ee_001 as ee_001
import rules_engine.rules.dq_001 as dq_001
import rules_engine.rules.dq_002 as dq_002

logger = logging.getLogger(__name__)

_RULE_EVALUATORS = {
    "DD-001": dd_001.evaluate,
    "DD-002": dd_002.evaluate,
    "MEO-001": meo_001.evaluate,
    "MEO-002": meo_002.evaluate,
    "CPE-001": cpe_001.evaluate,
    "CPE-002": cpe_002.evaluate,
    "SB-001": sb_001.evaluate,
    "EE-001": ee_001.evaluate,
    "DQ-001": dq_001.evaluate,
    "DQ-002": dq_002.evaluate,
}


class RulesEngine:
    def __init__(self, db_batch_size: int = 500):
        self.db_batch_size = db_batch_size

    def run(
        self,
        session: Session,
        batch_id: Optional[str] = None,
        query_batch_size: int = 5000,
    ) -> dict:
        """
        Full evaluation pass. If batch_id is given, only evaluates records
        from that ingestion/simulation batch.

        Returns summary stats dict.
        """
        reset_counters()

        rules = load_rules(session)
        logger.info("Loaded %d active compliance rules", len(rules))

        existing = self._load_existing_finding_keys(session, batch_id)
        logger.info("Skipping %d existing open findings", len(existing))

        stats = {r: 0 for r in _RULE_EVALUATORS}
        stats["total_evaluated"] = 0
        stats["total_findings"] = 0

        pending: list[dict] = []

        for ctx in iter_contexts(session, batch_size=query_batch_size, batch_id=batch_id):
            stats["total_evaluated"] += 1

            for rule_code, evaluator in _RULE_EVALUATORS.items():
                rule = rules.get(rule_code)
                if rule is None:
                    continue

                dedup_key = (str(ctx.split_billing_id), rule_code)
                if dedup_key in existing:
                    continue

                finding = evaluator(ctx, rule.rule_id, rule.rule_version)
                if finding is not None:
                    pending.append(finding)
                    existing.add(dedup_key)
                    stats[rule_code] += 1
                    stats["total_findings"] += 1

            if len(pending) >= self.db_batch_size:
                self._flush(session, pending)
                pending.clear()

        if pending:
            self._flush(session, pending)

        session.commit()

        logger.info(
            "Rules engine complete — evaluated %d records, generated %d findings",
            stats["total_evaluated"], stats["total_findings"],
        )
        for code, count in stats.items():
            if code not in ("total_evaluated", "total_findings") and count > 0:
                logger.info("  %s: %d findings", code, count)

        return stats

    def _flush(self, session: Session, findings: list[dict]) -> None:
        bulk_insert(session, "audit.audit_findings", findings)

    def _load_existing_finding_keys(
        self, session: Session, batch_id: Optional[str]
    ) -> set[tuple[str, str]]:
        """
        Load (split_billing_id, rule_code) for all open findings to prevent duplicates.
        Scoped to batch if provided.
        """
        if batch_id:
            rows = session.execute(text("""
                SELECT af.split_billing_id::text, af.rule_code
                FROM audit.audit_findings af
                JOIN ops.split_billing sb ON sb.split_billing_id = af.split_billing_id
                WHERE sb.batch_id = :bid::uuid
                  AND af.status = 'open'
            """), {"bid": batch_id}).fetchall()
        else:
            rows = session.execute(text("""
                SELECT split_billing_id::text, rule_code
                FROM audit.audit_findings
                WHERE status = 'open'
            """)).fetchall()

        return {(r[0], r[1]) for r in rows}
