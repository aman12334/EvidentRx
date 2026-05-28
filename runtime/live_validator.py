"""
LiveExecutionValidator — validates a single live LLM agent call.

Purpose:
  - Verify API keys work and the LLM responds before running a full case
  - Validate that a new model version produces schema-valid outputs
  - Smoke test agent prompts after role/instruction changes
  - Record model metadata and token accounting for compliance audit

Captures per invocation:
  - Model ID and provider actually used
  - Prompt token count (not raw text — preserves confidentiality)
  - Output token count and cache hit count
  - Latency in milliseconds
  - Output schema validation results (OutputValidator)
  - Hallucination check results (HallucinationDetector)

Does NOT store raw prompt/completion text — only sizes and metadata.
Raw text is stored separately by TraceWriter in audit.reasoning_traces.

Usage:
    validator = LiveExecutionValidator.from_env()
    report    = validator.run(session, case_id, agent_type="evidence_analysis")
    report.print_report()
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from evaluation.validators import HallucinationDetector, OutputValidator, ValidationIssue

logger = logging.getLogger(__name__)

_SUPPORTED_AGENTS = ("evidence_analysis", "risk_prioritization", "narrative_generation")


@dataclass
class LiveValidationReport:
    agent_type:     str
    case_id:        str
    model_id:       str | None
    provider:       str | None
    input_tokens:   int   = 0
    output_tokens:  int   = 0
    cache_tokens:   int   = 0
    latency_ms:     float = 0.0
    schema_issues:  list[ValidationIssue] = field(default_factory=list)
    hallucination_issues: list[str] = field(default_factory=list)
    output_keys:    list[str] = field(default_factory=list)
    confidence:     float | None = None
    error:          str | None = None

    @property
    def passed(self) -> bool:
        return (
            self.error is None
            and len(self.schema_issues) == 0
            and len(self.hallucination_issues) == 0
        )

    def print_report(self) -> None:
        w = 62
        status = "PASS ✓" if self.passed else "FAIL ✗"
        print(f"\n{'─' * w}")
        print(f"  Live Validation: {self.agent_type}  [{status}]")
        print(f"{'─' * w}")
        print(f"  Case         : {self.case_id[:8]}...")
        print(f"  Model        : {self.model_id or '—'}")
        print(f"  Provider     : {self.provider or '—'}")
        print(f"  Tokens in    : {self.input_tokens:,}")
        print(f"  Tokens out   : {self.output_tokens:,}")
        print(f"  Cache hits   : {self.cache_tokens:,}")
        print(f"  Latency      : {self.latency_ms:.0f} ms")
        print(f"  Confidence   : {self.confidence:.3f}" if self.confidence else "  Confidence   : —")
        print(f"  Output keys  : {', '.join(self.output_keys[:8])}")

        if self.schema_issues:
            print(f"\n  Schema issues ({len(self.schema_issues)}):")
            for issue in self.schema_issues:
                print(f"    ✗ {issue.field}: {issue.issue}")
        else:
            print("\n  Schema validation : OK")

        if self.hallucination_issues:
            print(f"\n  Hallucination issues ({len(self.hallucination_issues)}):")
            for h in self.hallucination_issues:
                print(f"    ⚠ {h}")
        else:
            print("  Hallucination check: OK")

        if self.error:
            print(f"\n  ERROR: {self.error}")

        print(f"{'─' * w}\n")

    def to_dict(self) -> dict:
        return {
            "agent_type":    self.agent_type,
            "case_id":       self.case_id,
            "model_id":      self.model_id,
            "provider":      self.provider,
            "input_tokens":  self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_tokens":  self.cache_tokens,
            "latency_ms":    self.latency_ms,
            "confidence":    self.confidence,
            "passed":        self.passed,
            "schema_issues": [
                {"field": i.field, "issue": i.issue} for i in self.schema_issues
            ],
            "hallucination_issues": self.hallucination_issues,
            "output_keys":   self.output_keys,
            "error":         self.error,
        }


class LiveExecutionValidator:
    """
    Runs a single agent against a real case and validates the live LLM output.
    """

    def __init__(self, runner) -> None:
        self._runner = runner
        self._schema_validator     = OutputValidator()
        self._hallucination_detector = HallucinationDetector()

    @classmethod
    def from_env(cls) -> LiveExecutionValidator:
        from agents.runner import InvestigationRunner
        return cls(runner=InvestigationRunner.from_env())

    def run(
        self,
        session: Session,
        case_id: UUID,
        agent_type: str = "evidence_analysis",
    ) -> LiveValidationReport:
        """
        Runs a single agent on the given case and validates the output.
        Only runs up to and including the requested agent's node.
        """
        if agent_type not in _SUPPORTED_AGENTS:
            raise ValueError(
                f"agent_type must be one of {_SUPPORTED_AGENTS}, got '{agent_type}'"
            )

        cid = str(case_id)
        report = LiveValidationReport(
            agent_type=agent_type,
            case_id=cid,
            model_id=None,
            provider=None,
        )

        try:
            # Load the case findings for hallucination context
            finding_codes = self._load_finding_codes(session, cid)

            # Run the full workflow but only validate the target agent's output
            t0 = time.monotonic()
            self._runner.run(session, case_id)
            elapsed_ms = (time.monotonic() - t0) * 1000
            session.commit()

            # Pull the most recent agent run record for the target agent
            meta = self._load_agent_run_meta(session, cid, agent_type)
            if meta:
                report.model_id    = meta.get("model_id")
                report.input_tokens  = meta.get("input_tokens") or 0
                report.output_tokens = meta.get("output_tokens") or 0
                report.cache_tokens  = meta.get("cache_read_tokens") or 0
                report.latency_ms    = float(meta.get("latency_ms") or elapsed_ms)
                report.provider      = self._infer_provider(meta.get("model_id"))

            # Extract the agent's output from run_result
            output = self._extract_agent_output(session, cid, agent_type)
            if not output:
                report.error = f"No output found for agent_type='{agent_type}' — agent may have failed"
                return report

            report.output_keys = list(output.keys())
            report.confidence  = output.get("confidence_score")

            # Schema validation
            report.schema_issues = self._schema_validator.validate(agent_type, output)

            # Financial exposure sub-object validation (risk only)
            if agent_type == "risk_prioritization":
                extra = self._schema_validator.check_financial_exposure(output)
                report.schema_issues.extend(extra)

            # Hallucination checks
            text_fields = {
                "evidence_analysis":   ["pattern_summary", "temporal_analysis",
                                        "severity_assessment", "analyst_notes"],
                "risk_prioritization": ["escalation_rationale",
                                        "resource_allocation_recommendation"],
                "narrative_generation":["executive_summary", "technical_findings",
                                        "regulatory_context", "audit_preparation_notes"],
            }.get(agent_type, [])

            report.hallucination_issues = self._hallucination_detector.run_all_checks(
                agent_type=agent_type,
                output=output,
                text_fields=text_fields,
                known_finding_codes=finding_codes,
            )

        except Exception as e:
            logger.exception("LiveExecutionValidator failed for case %s agent %s", cid, agent_type)
            report.error = str(e)

        return report

    def run_all_agents(
        self,
        session: Session,
        case_id: UUID,
    ) -> list[LiveValidationReport]:
        """Validates all three LLM agents in sequence on the same case."""
        reports = []
        for agent_type in _SUPPORTED_AGENTS:
            r = self.run(session, case_id, agent_type=agent_type)
            reports.append(r)
        return reports

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_finding_codes(self, session: Session, case_id: str) -> list[str]:
        rows = session.execute(text("""
            SELECT af.finding_code
            FROM audit.investigation_case_findings icf
            JOIN audit.audit_findings af ON icf.finding_id = af.finding_id
            WHERE icf.case_id = :cid
        """), {"cid": case_id}).fetchall()
        return [r[0] for r in rows if r[0]]

    def _load_agent_run_meta(self, session: Session, case_id: str, agent_type: str) -> dict | None:
        row = session.execute(text("""
            SELECT model_id, input_tokens, output_tokens, cache_read_tokens, latency_ms
            FROM audit.agent_runs
            WHERE case_id = :cid AND agent_type = :at AND status = 'completed'
            ORDER BY started_at DESC
            LIMIT 1
        """), {"cid": case_id, "at": agent_type}).mappings().first()
        return dict(row) if row else None

    def _extract_agent_output(
        self,
        session: Session,
        case_id: str,
        agent_type: str,
    ) -> dict | None:
        import json
        row = session.execute(text("""
            SELECT output_payload
            FROM audit.agent_runs
            WHERE case_id = :cid AND agent_type = :at AND status = 'completed'
            ORDER BY started_at DESC
            LIMIT 1
        """), {"cid": case_id, "at": agent_type}).mappings().first()
        if not row or not row["output_payload"]:
            return None
        payload = row["output_payload"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _infer_provider(model_id: str | None) -> str | None:
        if not model_id:
            return None
        m = model_id.lower()
        if "claude" in m or "anthropic" in m:
            return "anthropic"
        if "gpt" in m or "openai" in m:
            return "openai"
        return "unknown"
