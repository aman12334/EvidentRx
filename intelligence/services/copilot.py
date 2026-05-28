"""
InvestigatorCopilotService — structured LLM-powered investigation assistance.

THIS IS NOT A CHATBOT.  It is a structured investigation assistance tool that
accepts typed, validated queries and returns deterministic structured output.
Each operation loads fresh DB data before calling the LLM — no session memory,
no multi-turn dialogue, no free-form conversation.

LLM role: summarise, explain, recommend next steps, identify patterns.
LLM prohibitions: never creates/modifies findings, never overrides risk levels,
  never writes to compliance rules, never persists investigation decisions.

All LLM calls are persisted to audit.copilot_sessions for full auditability.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

import anthropic
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "claude-opus-4-5"
_MAX_TOKENS    = 1024
_TEMPERATURE   = 0.1   # low — we want consistent structured output


class CopilotOperation(str, Enum):
    SUMMARIZE       = "summarize"        # concise case summary
    TIMELINE        = "timeline"         # chronological narrative
    RECOMMEND       = "recommend"        # next investigation steps
    NAVIGATE        = "navigate"         # explain findings for investigator
    RELATED_CASES   = "related_cases"    # summarise correlated case context


# ------------------------------------------------------------------ #
# Structured inputs — each operation has its own typed request         #
# ------------------------------------------------------------------ #

@dataclass(frozen=True)
class CopilotRequest:
    operation:   CopilotOperation
    case_id:     str
    investigator_id: str
    context:     dict[str, Any]   # operation-specific payload


@dataclass
class CopilotResponse:
    session_id:       str
    operation:        str
    case_id:          str
    output:           dict[str, Any]
    confidence_score: float
    model_id:         str
    input_tokens:     int
    output_tokens:    int
    cache_read_tokens: int
    latency_ms:       int
    created_at:       datetime

    @property
    def summary(self) -> str:
        return self.output.get("summary", self.output.get("narrative", ""))


# ------------------------------------------------------------------ #
# Service                                                              #
# ------------------------------------------------------------------ #

class InvestigatorCopilotService:
    """
    Provides structured, auditable LLM assistance for investigators.

    Each method corresponds to exactly one CopilotOperation.  All calls
    persist a copilot_session row — investigators can audit every question
    asked and every answer returned.

    Usage::

        svc = InvestigatorCopilotService(model_id="claude-opus-4-5")
        response = svc.summarize(session, case_id="...", investigator_id="...")
    """

    def __init__(self, model_id: str = _DEFAULT_MODEL) -> None:
        self.model_id = model_id
        self._client  = anthropic.Anthropic()

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def summarize(
        self,
        session: Session,
        case_id: str,
        investigator_id: str,
    ) -> CopilotResponse:
        """
        Returns a concise structured summary of a case: key facts,
        severity breakdown, top rule violations, financial exposure.
        """
        context = self._load_case_context(session, case_id)
        req = CopilotRequest(
            operation=CopilotOperation.SUMMARIZE,
            case_id=case_id,
            investigator_id=investigator_id,
            context=context,
        )
        return self._call(session, req, self._summarize_prompt(context))

    def build_timeline(
        self,
        session: Session,
        case_id: str,
        investigator_id: str,
    ) -> CopilotResponse:
        """
        Returns a chronological narrative of investigation events,
        finding creation, status changes, and agent actions.
        """
        context = self._load_timeline_context(session, case_id)
        req = CopilotRequest(
            operation=CopilotOperation.TIMELINE,
            case_id=case_id,
            investigator_id=investigator_id,
            context=context,
        )
        return self._call(session, req, self._timeline_prompt(context))

    def recommend_next_steps(
        self,
        session: Session,
        case_id: str,
        investigator_id: str,
        current_status: str,
    ) -> CopilotResponse:
        """
        Returns a prioritised list of recommended investigation actions
        based on the case's current findings and status.
        """
        context = self._load_case_context(session, case_id)
        context["current_status"] = current_status
        req = CopilotRequest(
            operation=CopilotOperation.RECOMMEND,
            case_id=case_id,
            investigator_id=investigator_id,
            context=context,
        )
        return self._call(session, req, self._recommend_prompt(context))

    def explain_findings(
        self,
        session: Session,
        case_id: str,
        investigator_id: str,
        finding_ids: list[str] | None = None,
    ) -> CopilotResponse:
        """
        Explains what the findings mean in plain English — what rule was
        triggered, why it matters in 340B context, what evidence supports it.
        """
        context = self._load_case_context(session, case_id)
        if finding_ids:
            context["findings"] = [
                f for f in context.get("findings", [])
                if str(f.get("finding_id")) in finding_ids
            ]
        req = CopilotRequest(
            operation=CopilotOperation.NAVIGATE,
            case_id=case_id,
            investigator_id=investigator_id,
            context=context,
        )
        return self._call(session, req, self._navigate_prompt(context))

    def summarize_related_cases(
        self,
        session: Session,
        case_id: str,
        investigator_id: str,
        correlation_records: list[dict],
    ) -> CopilotResponse:
        """
        Given pre-computed correlation records (from CorrelationEngine),
        produces a structured summary of what the related cases have in common
        and what that implies for the current investigation.
        """
        context = self._load_case_context(session, case_id)
        context["correlation_records"] = correlation_records[:10]  # cap to avoid token overflow
        req = CopilotRequest(
            operation=CopilotOperation.RELATED_CASES,
            case_id=case_id,
            investigator_id=investigator_id,
            context=context,
        )
        return self._call(session, req, self._related_cases_prompt(context))

    # ------------------------------------------------------------------ #
    # Core LLM call + persistence                                          #
    # ------------------------------------------------------------------ #

    def _call(
        self,
        session: Session,
        req: CopilotRequest,
        prompt: str,
    ) -> CopilotResponse:
        session_id = str(uuid4())
        t0 = time.perf_counter()

        raw = self._client.messages.create(
            model=self.model_id,
            max_tokens=_MAX_TOKENS,
            temperature=_TEMPERATURE,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        latency_ms = int((time.perf_counter() - t0) * 1000)
        usage      = raw.usage

        # Parse JSON output from the model
        content_text = raw.content[0].text if raw.content else "{}"
        try:
            output = json.loads(content_text)
        except json.JSONDecodeError:
            # If model didn't return valid JSON, wrap raw text
            output = {"raw": content_text, "parse_error": True}
            logger.warning("Copilot response not valid JSON for session %s", session_id)

        confidence = float(output.get("confidence", 0.7))

        resp = CopilotResponse(
            session_id=session_id,
            operation=req.operation.value,
            case_id=req.case_id,
            output=output,
            confidence_score=confidence,
            model_id=self.model_id,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0),
            latency_ms=latency_ms,
            created_at=datetime.utcnow(),
        )

        self._persist_session(session, req, resp)
        return resp

    def _persist_session(
        self,
        session: Session,
        req: CopilotRequest,
        resp: CopilotResponse,
    ) -> None:
        try:
            session.execute(text("""
                INSERT INTO audit.copilot_sessions
                    (session_id, case_id, investigator_id, session_type,
                     input_context, output, model_id,
                     input_tokens, output_tokens, cache_read_tokens,
                     latency_ms, confidence_score, created_at)
                VALUES
                    (:sid::uuid, :cid::uuid, :iid, :stype,
                     :input_ctx::jsonb, :output::jsonb, :model,
                     :in_tok, :out_tok, :cache_tok,
                     :latency, :confidence, :created_at)
            """), {
                "sid":        resp.session_id,
                "cid":        req.case_id,
                "iid":        req.investigator_id,
                "stype":      req.operation.value,
                "input_ctx":  json.dumps(req.context),
                "output":     json.dumps(resp.output),
                "model":      resp.model_id,
                "in_tok":     resp.input_tokens,
                "out_tok":    resp.output_tokens,
                "cache_tok":  resp.cache_read_tokens,
                "latency":    resp.latency_ms,
                "confidence": resp.confidence_score,
                "created_at": resp.created_at.isoformat(),
            })
        except Exception as exc:
            logger.error("Failed to persist copilot session %s: %s", resp.session_id, exc)

    # ------------------------------------------------------------------ #
    # DB data loaders                                                      #
    # ------------------------------------------------------------------ #

    def _load_case_context(self, session: Session, case_id: str) -> dict:
        """Loads case + findings for LLM context."""
        case_row = session.execute(text("""
            SELECT ic.case_id, ic.case_number, ic.status, ic.priority,
                   ic.violation_category,
                   ce.entity_name,
                   crs.risk_level, crs.composite_score,
                   crs.total_findings, crs.critical_findings,
                   crs.high_findings, crs.estimated_financial_exposure
            FROM audit.investigation_cases ic
            LEFT JOIN ref.covered_entities ce
                   ON ic.covered_entity_id = ce.ce_id AND ce.is_current = TRUE
            LEFT JOIN audit.case_risk_snapshots crs
                   ON crs.case_id = ic.case_id
            WHERE ic.case_id = :cid::uuid
            ORDER BY crs.created_at DESC
            LIMIT 1
        """), {"cid": case_id}).mappings().fetchone()

        findings_rows = session.execute(text("""
            SELECT af.finding_id, af.finding_code, af.rule_code,
                   af.severity, af.evidence_payload, af.created_at
            FROM audit.investigation_case_findings icf
            JOIN audit.audit_findings af ON icf.finding_id = af.finding_id
            WHERE icf.case_id = :cid::uuid
            ORDER BY af.severity DESC, af.created_at DESC
            LIMIT 30
        """), {"cid": case_id}).mappings().fetchall()

        findings = []
        for f in findings_rows:
            findings.append({
                "finding_id":   str(f["finding_id"]),
                "finding_code": f["finding_code"],
                "rule_code":    f["rule_code"],
                "severity":     f["severity"],
                "created_at":   str(f["created_at"]),
            })

        result = {"case_id": case_id, "findings": findings}
        if case_row:
            result.update({
                "case_number":     case_row["case_number"],
                "status":          case_row["status"],
                "priority":        case_row["priority"],
                "violation_category": case_row["violation_category"],
                "entity_name":     case_row["entity_name"],
                "risk_level":      case_row["risk_level"],
                "composite_score": float(case_row["composite_score"] or 0),
                "total_findings":  int(case_row["total_findings"] or 0),
                "critical_findings": int(case_row["critical_findings"] or 0),
                "financial_exposure": float(case_row["estimated_financial_exposure"] or 0),
            })
        return result

    def _load_timeline_context(self, session: Session, case_id: str) -> dict:
        """Loads investigation timeline events."""
        events = session.execute(text("""
            SELECT event_type, event_description, created_at, created_by
            FROM audit.investigation_timeline
            WHERE case_id = :cid::uuid
            ORDER BY created_at ASC
            LIMIT 50
        """), {"cid": case_id}).mappings().fetchall()

        return {
            "case_id": case_id,
            "events": [
                {
                    "event_type":    e["event_type"],
                    "description":   e["event_description"],
                    "created_at":    str(e["created_at"]),
                    "created_by":    e["created_by"],
                }
                for e in events
            ],
        }

    # ------------------------------------------------------------------ #
    # Prompt builders                                                      #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _summarize_prompt(ctx: dict) -> str:
        return f"""You are reviewing a 340B compliance investigation case.

Case data:
{json.dumps(ctx, indent=2, default=str)}

Produce a structured JSON summary with these exact keys:
{{
  "summary": "<2-3 sentence executive summary>",
  "key_violations": ["<rule_code>: <brief explanation>", ...],
  "severity_breakdown": {{"critical": N, "high": N, "medium": N, "low": N}},
  "financial_exposure_usd": <number or null>,
  "top_risk_factors": ["<factor>", ...],
  "confidence": <0.0-1.0>
}}

Return ONLY the JSON object. Do not invent rule codes or findings not present in the data."""

    @staticmethod
    def _timeline_prompt(ctx: dict) -> str:
        return f"""You are reviewing the investigation timeline for a 340B compliance case.

Timeline data:
{json.dumps(ctx, indent=2, default=str)}

Produce a structured JSON chronological narrative:
{{
  "narrative": "<2-4 sentence chronological summary of investigation progression>",
  "key_milestones": [
    {{"date": "<ISO date>", "event": "<brief description>"}},
    ...
  ],
  "current_phase": "<one of: initial_triage / active_investigation / escalated / near_resolution>",
  "days_active": <integer>,
  "confidence": <0.0-1.0>
}}

Return ONLY the JSON object."""

    @staticmethod
    def _recommend_prompt(ctx: dict) -> str:
        return f"""You are an expert 340B compliance investigator.

Case context:
{json.dumps(ctx, indent=2, default=str)}

Based on the confirmed findings and current status, recommend the next investigation steps.

Return a structured JSON object:
{{
  "recommended_actions": [
    {{
      "priority": "high|medium|low",
      "action": "<specific investigative action>",
      "rationale": "<why this action is relevant given the evidence>"
    }},
    ...
  ],
  "escalation_warranted": true|false,
  "escalation_rationale": "<reason or null>",
  "estimated_resolution_path": "<brief description>",
  "confidence": <0.0-1.0>
}}

Return ONLY the JSON object. Do not recommend actions that create findings or override deterministic rule results."""

    @staticmethod
    def _navigate_prompt(ctx: dict) -> str:
        return f"""You are explaining 340B compliance findings to an investigator.

Findings data:
{json.dumps(ctx, indent=2, default=str)}

For each finding, explain what it means in plain English.

Return a structured JSON object:
{{
  "explanations": [
    {{
      "finding_code": "<code>",
      "rule_code": "<code>",
      "plain_english": "<what this finding means>",
      "regulatory_basis": "<relevant 340B program rule>",
      "evidence_note": "<what evidence supports this finding>"
    }},
    ...
  ],
  "overall_pattern": "<what pattern do these findings collectively suggest>",
  "confidence": <0.0-1.0>
}}

Return ONLY the JSON object. Do not modify, dispute, or dismiss any findings."""

    @staticmethod
    def _related_cases_prompt(ctx: dict) -> str:
        return f"""You are analyzing cross-case correlations in a 340B compliance investigation.

Primary case context:
{json.dumps({k: v for k, v in ctx.items() if k != 'correlation_records'}, indent=2, default=str)}

Correlated cases:
{json.dumps(ctx.get('correlation_records', []), indent=2, default=str)}

Summarize what the related cases have in common and what this implies.

Return a structured JSON object:
{{
  "pattern_summary": "<2-3 sentences on what the cross-case patterns suggest>",
  "shared_risk_factors": ["<factor>", ...],
  "systemic_indicator": true|false,
  "systemic_rationale": "<why systemic or not>",
  "recommended_joint_actions": ["<action if systemic investigation is warranted>", ...],
  "confidence": <0.0-1.0>
}}

Return ONLY the JSON object."""


# ------------------------------------------------------------------ #
# System prompt constant                                               #
# ------------------------------------------------------------------ #

_SYSTEM_PROMPT = """You are an expert 340B pharmaceutical compliance intelligence system.

Your role is structured investigation assistance — NOT a general-purpose chatbot.

STRICT PROHIBITIONS:
- You MUST NOT create, invent, or infer compliance findings not present in the data
- You MUST NOT override, dispute, or reweight deterministic rule engine findings
- You MUST NOT suggest changes to compliance rules
- You MUST NOT make medication, clinical, or patient care recommendations
- You MUST NOT fabricate regulation citations, case numbers, or NDC codes

YOUR AUTHORITIES:
- Summarize and explain confirmed findings using only data provided
- Identify patterns across provided findings
- Recommend investigation process steps (not findings outcomes)
- Explain regulatory context for finding types already present

OUTPUT FORMAT: Always return a valid JSON object matching the specified schema.
Return ONLY the JSON — no markdown fences, no preamble, no commentary."""
