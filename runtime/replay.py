"""
InvestigationReplayer — re-runs the agent workflow on an existing case.

Unlike the evaluation harness (fixed golden seed), replay re-investigates
a real case that already has findings in the DB. Use this to:

  - Re-run after agent prompt or model updates
  - Verify workflow produces consistent outputs across runs
  - Diagnose a prior failed investigation run
  - Diff outputs before/after a model change

The replay starts fresh (new run_id, new session_id) but reads the same
case data — findings and risk snapshot are already in the DB from prior
pipeline stages. The case_intake node loads them again.

Usage:
    replayer = InvestigationReplayer.from_env()
    result   = replayer.replay(session, case_id)
    result.print_report()

    # With diff against most recent prior run
    result = replayer.replay(session, case_id, diff=True)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class ReplayDiff:
    """Structural diff between two agent investigation runs on the same case."""
    field:     str
    prior:     object
    current:   object
    changed:   bool
    delta_str: str = ""


@dataclass
class ReplayReport:
    case_id:       str
    prior_run_id:  Optional[str]
    current_run_id: str
    started_at:    str
    completed_at:  Optional[str] = None
    run_result:    dict = field(default_factory=dict)
    diffs:         list[ReplayDiff] = field(default_factory=list)
    errors:        list[dict] = field(default_factory=list)

    @property
    def has_diffs(self) -> bool:
        return any(d.changed for d in self.diffs)

    @property
    def passed(self) -> bool:
        return not self.errors and self.run_result.get("is_complete", False)

    def print_report(self) -> None:
        w = 62
        print(f"\n{'═' * w}")
        print(f"  Investigation Replay — Case {self.case_id[:8]}...")
        print(f"{'═' * w}")
        print(f"  Run ID    : {self.current_run_id}")
        print(f"  Started   : {self.started_at}")
        print(f"  Completed : {self.completed_at or '—'}")
        print()

        result = self.run_result
        print(f"  Status    : {'ESCALATED' if result.get('escalated') else 'COMPLETE'}")
        print(f"  Risk level: {result.get('risk_level', 'unknown')}")
        print(f"  Errors    : {len(result.get('errors', []))}")
        print(f"  Tokens in : {result.get('total_input_tokens', 0):,}")
        print(f"  Tokens out: {result.get('total_output_tokens', 0):,}")
        print(f"  Cache hits: {result.get('cache_read_tokens', 0):,}")

        if result.get("executive_summary"):
            print(f"\n  Executive Summary (first 300 chars):")
            print(f"  {result['executive_summary'][:300]}...")

        if self.diffs:
            print(f"\n  {'─' * 58}")
            print(f"  Diff vs prior run ({self.prior_run_id or 'none'})")
            print(f"  {'─' * 58}")
            for d in self.diffs:
                changed_marker = "  ← CHANGED" if d.changed else ""
                print(f"  {d.field:<30} {d.delta_str or str(d.current)}{changed_marker}")
        elif self.prior_run_id:
            print(f"\n  No diff computed (no prior run data available).")

        if self.errors:
            print(f"\n  Errors:")
            for e in self.errors:
                print(f"    [{e.get('node')}] {e.get('error')}")

        print(f"\n{'═' * w}\n")

    def to_dict(self) -> dict:
        return {
            "case_id":        self.case_id,
            "prior_run_id":   self.prior_run_id,
            "current_run_id": self.current_run_id,
            "started_at":     self.started_at,
            "completed_at":   self.completed_at,
            "passed":         self.passed,
            "run_result":     self.run_result,
            "diffs": [
                {
                    "field":   d.field,
                    "prior":   str(d.prior),
                    "current": str(d.current),
                    "changed": d.changed,
                }
                for d in self.diffs
            ],
            "errors": self.errors,
        }


class InvestigationReplayer:

    def __init__(self, runner) -> None:
        self._runner = runner

    @classmethod
    def from_env(cls) -> "InvestigationReplayer":
        from agents.runner import InvestigationRunner
        return cls(runner=InvestigationRunner.from_env())

    def replay(
        self,
        session: Session,
        case_id: UUID,
        diff: bool = False,
    ) -> ReplayReport:
        """
        Re-runs the investigation workflow on an existing case.
        If diff=True, compares key output fields against the most recent prior run.
        """
        cid = str(case_id)
        started_at = datetime.now(timezone.utc).isoformat()

        # Load prior run data before running (so we can diff)
        prior_run_id  = None
        prior_outputs = {}
        if diff:
            prior_run_id, prior_outputs = self._load_prior_outputs(session, cid)

        logger.info("Replaying investigation for case %s", cid)

        # Run the workflow fresh
        result = self._runner.run(session, case_id)
        session.commit()

        completed_at = datetime.now(timezone.utc).isoformat()

        report = ReplayReport(
            case_id=cid,
            prior_run_id=prior_run_id,
            current_run_id=result.get("run_id", ""),
            started_at=started_at,
            completed_at=completed_at,
            run_result=result,
            errors=result.get("errors", []),
        )

        # Build diff
        if diff and prior_outputs:
            report.diffs = self._compute_diff(prior_outputs, result)

        return report

    # ------------------------------------------------------------------
    # Diff logic
    # ------------------------------------------------------------------

    _DIFF_FIELDS: list[tuple[str, str]] = [
        # (display name, key in run_result)
        ("Risk level",             "risk_level"),
        ("Escalated",              "escalated"),
        ("Total input tokens",     "total_input_tokens"),
        ("Total output tokens",    "total_output_tokens"),
        ("Cache read tokens",      "cache_read_tokens"),
        ("Error count",            "_error_count"),
    ]

    def _compute_diff(self, prior: dict, current: dict) -> list[ReplayDiff]:
        diffs = []

        for display_name, key in self._DIFF_FIELDS:
            if key == "_error_count":
                prior_val   = len(prior.get("errors", []))
                current_val = len(current.get("errors", []))
            else:
                prior_val   = prior.get(key)
                current_val = current.get(key)

            changed = (prior_val != current_val)

            # Build delta string
            delta_str = str(current_val)
            if changed:
                if isinstance(prior_val, (int, float)) and isinstance(current_val, (int, float)):
                    diff = current_val - prior_val
                    delta_str = f"{current_val} (was {prior_val}, Δ{diff:+})"
                else:
                    delta_str = f"{current_val!r} (was {prior_val!r})"

            diffs.append(ReplayDiff(
                field=display_name,
                prior=prior_val,
                current=current_val,
                changed=changed,
                delta_str=delta_str,
            ))

        # Executive summary length change (structural, not semantic)
        prior_summ   = prior.get("executive_summary", "") or ""
        current_summ = current.get("executive_summary", "") or ""
        summ_delta   = len(current_summ) - len(prior_summ)
        diffs.append(ReplayDiff(
            field="Exec summary length",
            prior=len(prior_summ),
            current=len(current_summ),
            changed=(summ_delta != 0),
            delta_str=f"{len(current_summ)} chars (was {len(prior_summ)}, Δ{summ_delta:+})",
        ))

        return diffs

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _load_prior_outputs(self, session: Session, case_id: str) -> tuple[Optional[str], dict]:
        """
        Loads the most recently completed agent run batch for this case
        and reconstructs a run_result-like dict for diffing.
        """
        # Get the most recent workflow_run_id that has at least one completed narrative run
        row = session.execute(text("""
            SELECT workflow_run_id
            FROM audit.agent_runs
            WHERE case_id = :cid
              AND agent_type = 'narrative_generation'
              AND status = 'completed'
            ORDER BY started_at DESC
            LIMIT 1
        """), {"cid": case_id}).first()

        if not row:
            return None, {}

        workflow_run_id = str(row[0])

        # Load all agent runs from that workflow run
        runs = session.execute(text("""
            SELECT agent_type, output_payload, input_tokens, output_tokens,
                   cache_read_tokens
            FROM audit.agent_runs
            WHERE case_id = :cid
              AND workflow_run_id = :wid
        """), {"cid": case_id, "wid": workflow_run_id}).mappings().fetchall()

        prior = {
            "run_id":              workflow_run_id,
            "errors":              [],
            "total_input_tokens":  0,
            "total_output_tokens": 0,
            "cache_read_tokens":   0,
        }

        for r in runs:
            prior["total_input_tokens"]  += r.get("input_tokens") or 0
            prior["total_output_tokens"] += r.get("output_tokens") or 0
            prior["cache_read_tokens"]   += r.get("cache_read_tokens") or 0

            if r.get("agent_type") == "risk_prioritization":
                payload = r.get("output_payload") or {}
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except Exception:
                        payload = {}
                prior["risk_level"] = payload.get("overall_risk_level")
                prior["escalated"]  = bool(payload.get("escalation_recommended", False))

            elif r.get("agent_type") == "narrative_generation":
                payload = r.get("output_payload") or {}
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except Exception:
                        payload = {}
                prior["executive_summary"] = payload.get("executive_summary", "")

        return workflow_run_id, prior
