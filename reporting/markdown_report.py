"""
MarkdownReporter — renders an investigation case as a Markdown report.

Output is audit-ready: structured, traceable, versioned.
Suitable for email, Confluence, PDF export, or filing with HRSA response.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from reporting.base import ReportData

_SEVERITY_ORDER = ["critical", "high", "medium", "low"]
_SEVERITY_BADGE = {
    "critical": "🔴 CRITICAL",
    "high":     "🟠 HIGH",
    "medium":   "🟡 MEDIUM",
    "low":      "🟢 LOW",
}


class MarkdownReporter:

    def render(self, data: ReportData) -> str:
        parts = [
            self._header(data),
            self._executive_summary(data),
            self._case_metadata(data),
            self._risk_snapshot(data),
            self._findings_table(data),
            self._timeline(data),
            self._agent_reasoning(data),
            self._remediation(data),
            self._audit_preparation(data),
            self._footer(data),
        ]
        return "\n\n".join(p for p in parts if p)

    def write(self, data: ReportData, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"{data.case.get('case_number', data.case_id)}.md"
        path.write_text(self.render(data), encoding="utf-8")
        return path

    # ------------------------------------------------------------------
    # Sections
    # ------------------------------------------------------------------

    def _header(self, data: ReportData) -> str:
        case_num = data.case.get("case_number", data.case_id)
        return (
            f"# 340B Compliance Investigation Report\n\n"
            f"**Case:** `{case_num}`  \n"
            f"**Covered Entity:** {data.ce_name}  \n"
            f"**Status:** {data.case.get('status', 'unknown').upper()}  \n"
            f"**Generated:** {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}  \n"
            f"**Classification:** CONFIDENTIAL — COMPLIANCE PRIVILEGED"
        )

    def _executive_summary(self, data: ReportData) -> str:
        narrative = data.narrative
        summary_text = narrative.get("executive_summary", "")
        if not summary_text:
            # Fallback when agents haven't run
            snap = data.risk_snapshot or {}
            snap.get("by_severity", {})
            summary_text = (
                f"This investigation case covers {data.total_findings} confirmed 340B compliance "
                f"findings for {data.ce_name} in the {data.case.get('violation_category', 'unknown')} "
                f"category. The finding set includes {data.critical_count} critical and "
                f"{data.high_count} high severity violations."
            )

        return f"## Executive Summary\n\n{summary_text}"

    def _case_metadata(self, data: ReportData) -> str:
        c = data.case
        rows = [
            ("Case Number",         c.get("case_number", "—")),
            ("Covered Entity ID",   str(c.get("covered_entity_id", "—"))),
            ("Violation Category",  c.get("violation_category", "—")),
            ("Priority",            str(c.get("priority", "—"))),
            ("Status",              c.get("status", "—")),
            ("Opened At",           str(c.get("opened_at", "—"))[:19]),
            ("Closed At",           str(c.get("closed_at", "—"))[:19] if c.get("closed_at") else "Open"),
            ("Assigned To",         c.get("assigned_to") or "Unassigned"),
            ("Total Findings",      str(data.total_findings)),
        ]
        if data.financial_exposure is not None:
            rows.append(("Financial Exposure Est.", f"${data.financial_exposure:,.2f}"))

        table = "| Field | Value |\n|---|---|\n"
        for k, v in rows:
            table += f"| {k} | {v} |\n"

        return f"## Case Metadata\n\n{table}"

    def _risk_snapshot(self, data: ReportData) -> str:
        snap = data.risk_snapshot
        if not snap:
            return ""

        by_sev = snap.get("by_severity", {})
        window = snap.get("temporal_window", {})
        ndcs   = snap.get("ndc_list", [])

        lines = [
            "## Risk Snapshot\n",
            "| Metric | Value |",
            "|---|---|",
            f"| Composite Risk Score | `{snap.get('composite_risk_score', '—')}` |",
            f"| Total Findings | {snap.get('total_findings', 0)} |",
            f"| Critical | {by_sev.get('critical', 0)} |",
            f"| High | {by_sev.get('high', 0)} |",
            f"| Medium | {by_sev.get('medium', 0)} |",
            f"| Low | {by_sev.get('low', 0)} |",
            f"| Financial Exposure | ${snap.get('total_financial_exposure') or 0:,.2f} |",
            f"| Unique Patients | {snap.get('unique_patients', 0)} |",
            f"| Temporal Window | {window.get('start', '—')} → {window.get('end', '—')} |",
            f"| NDCs Affected | {len(ndcs)} |",
        ]

        if ndcs:
            lines.append(f"\n**NDCs:** `{'`, `'.join(ndcs[:10])}`{'...' if len(ndcs) > 10 else ''}")

        findings_by_rule = snap.get("findings_by_rule", {})
        if findings_by_rule:
            lines.append("\n**Findings by Rule:**\n")
            lines.append("| Rule Code | Count |")
            lines.append("|---|---|")
            for rule, count in sorted(findings_by_rule.items()):
                lines.append(f"| `{rule}` | {count} |")

        return "\n".join(lines)

    def _findings_table(self, data: ReportData) -> str:
        if not data.findings:
            return "## Findings\n\n_No findings loaded._"

        lines = [
            "## Confirmed Findings\n",
            f"_Showing {min(len(data.findings), 50)} of {data.total_findings} findings "
            f"(deterministic rules engine — not AI-generated)_\n",
            "| Finding Code | Rule | Severity | NDC | Service Date | Primary |",
            "|---|---|---|---|---|---|",
        ]

        for f in data.findings[:50]:
            badge   = _SEVERITY_BADGE.get(f.get("severity", ""), f.get("severity", ""))
            primary = "✓" if f.get("is_primary") else ""
            lines.append(
                f"| `{f.get('finding_code', '—')}` "
                f"| `{f.get('rule_code', '—')}` "
                f"| {badge} "
                f"| `{f.get('ndc_11', '—')}` "
                f"| {str(f.get('service_date', '—'))[:10]} "
                f"| {primary} |"
            )

        if len(data.findings) > 50:
            lines.append(f"\n_... and {len(data.findings) - 50} more findings (see JSON export)_")

        return "\n".join(lines)

    def _timeline(self, data: ReportData) -> str:
        if not data.timeline:
            return ""

        lines = [
            "## Investigation Timeline\n",
            "| Timestamp | Event | Actor |",
            "|---|---|---|",
        ]
        for e in data.timeline:
            ts    = str(e.get("occurred_at", "—"))[:19]
            etype = e.get("event_type", "—")
            actor = f"{e.get('actor_id', '—')} ({e.get('actor_type', '—')})"
            lines.append(f"| {ts} | `{etype}` | {actor} |")

        return "\n".join(lines)

    def _agent_reasoning(self, data: ReportData) -> str:
        if not data.reasoning_traces and not data.agent_runs:
            return ""

        lines = ["## Agent Reasoning Traces\n"]

        # Agent runs summary
        if data.agent_runs:
            lines += [
                "### Agent Execution Summary\n",
                "| Agent | Status | Model | Tokens In | Tokens Out | Latency (ms) |",
                "|---|---|---|---|---|---|",
            ]
            for ar in data.agent_runs:
                lines.append(
                    f"| `{ar.get('agent_type', '—')}` "
                    f"| {ar.get('status', '—')} "
                    f"| {ar.get('model_id') or '—'} "
                    f"| {ar.get('input_tokens') or 0:,} "
                    f"| {ar.get('output_tokens') or 0:,} "
                    f"| {ar.get('latency_ms') or '—'} |"
                )

        # Reasoning traces
        if data.reasoning_traces:
            lines.append("\n### Reasoning Trace Chain\n")
            for t in data.reasoning_traces:
                confidence = t.get("confidence_score")
                conf_str   = f" (confidence: {confidence:.2f})" if confidence is not None else ""
                lines.append(
                    f"**Step {t.get('workflow_step', '?')} — `{t.get('workflow_node', '—')}`**"
                    f"{conf_str}  \n"
                    f"Agent: `{t.get('agent_id', '—')}` | "
                    f"Trace ID: `{str(t.get('trace_id', '—'))[:8]}...`  \n"
                )

        # Technical findings from narrative
        tech = data.narrative.get("technical_findings", "")
        if tech:
            lines.append("\n### Technical Findings Narrative\n")
            lines.append(tech)

        return "\n".join(lines)

    def _remediation(self, data: ReportData) -> str:
        recs = data.narrative.get("remediation_recommendations", [])
        if not recs:
            return ""

        lines = ["## Remediation Recommendations\n"]
        priority_order = {"immediate": 0, "short_term": 1, "long_term": 2}
        sorted_recs = sorted(recs, key=lambda r: priority_order.get(r.get("priority", ""), 99))

        for i, rec in enumerate(sorted_recs, 1):
            priority = rec.get("priority", "unknown").upper().replace("_", " ")
            lines.append(f"### {i}. [{priority}] {rec.get('action', 'Action not specified')}\n")
            rationale = rec.get("rationale", "")
            if rationale:
                lines.append(f"**Rationale:** {rationale}\n")

        return "\n".join(lines)

    def _audit_preparation(self, data: ReportData) -> str:
        reg_ctx = data.narrative.get("regulatory_context", "")
        audit_notes = data.narrative.get("audit_preparation_notes", "")

        parts = []
        if reg_ctx:
            parts.append(f"## Regulatory Context\n\n{reg_ctx}")
        if audit_notes:
            parts.append(f"## Audit Preparation Notes\n\n{audit_notes}")

        return "\n\n".join(parts)

    def _footer(self, data: ReportData) -> str:
        conf = data.narrative.get("confidence_score")
        conf_str = f"{conf:.2f}" if conf is not None else "N/A"
        return (
            f"---\n\n"
            f"_Generated by EvidentRx | Case `{data.case_id}` | "
            f"AI confidence score: {conf_str} | "
            f"{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}_"
        )
