"""
HTMLReporter — renders an investigation case as a self-contained HTML report.

No external CSS frameworks or CDN dependencies. All styles are inline.
Designed to be opened directly in a browser or attached to email.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from reporting.base import ReportData

_SEVERITY_COLOR = {
    "critical": "#dc2626",
    "high":     "#ea580c",
    "medium":   "#ca8a04",
    "low":      "#16a34a",
}

_STATUS_COLOR = {
    "open":           "#2563eb",
    "triaged":        "#7c3aed",
    "investigating":  "#b45309",
    "escalated":      "#dc2626",
    "resolved":       "#16a34a",
    "false_positive": "#6b7280",
}

_CSS = """
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         margin: 0; background: #f8fafc; color: #1e293b; }
  .page { max-width: 1100px; margin: 32px auto; padding: 0 24px; }
  .header { background: #0f172a; color: white; padding: 24px 32px;
            border-radius: 8px; margin-bottom: 24px; }
  .header h1 { margin: 0 0 8px; font-size: 22px; }
  .header .meta { font-size: 13px; opacity: 0.7; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px;
           font-size: 12px; font-weight: 600; color: white; }
  .section { background: white; border: 1px solid #e2e8f0; border-radius: 8px;
             padding: 24px; margin-bottom: 20px; }
  .section h2 { margin: 0 0 16px; font-size: 16px; color: #0f172a;
                border-bottom: 1px solid #e2e8f0; padding-bottom: 10px; }
  .section h3 { font-size: 14px; color: #334155; margin: 16px 0 8px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { background: #f1f5f9; text-align: left; padding: 8px 12px;
       font-weight: 600; color: #475569; border-bottom: 1px solid #e2e8f0; }
  td { padding: 7px 12px; border-bottom: 1px solid #f1f5f9; vertical-align: top; }
  tr:last-child td { border-bottom: none; }
  .mono { font-family: 'SF Mono', 'Fira Code', monospace; font-size: 12px;
          background: #f8fafc; padding: 1px 5px; border-radius: 3px; }
  .prose { line-height: 1.7; font-size: 14px; color: #334155; }
  .rec { border-left: 3px solid #3b82f6; padding: 10px 16px;
         margin-bottom: 12px; background: #eff6ff; border-radius: 0 6px 6px 0; }
  .rec .priority { font-size: 11px; font-weight: 700; text-transform: uppercase;
                   color: #1d4ed8; margin-bottom: 4px; }
  .rec .action { font-weight: 600; font-size: 14px; margin-bottom: 4px; }
  .rec .rationale { font-size: 13px; color: #475569; }
  .kv { display: grid; grid-template-columns: 180px 1fr; gap: 8px 16px;
        font-size: 13px; }
  .kv .key { color: #64748b; font-weight: 500; }
  .kv .val { color: #1e293b; }
  .metric-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
                 gap: 12px; }
  .metric { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px;
            padding: 12px 16px; }
  .metric .label { font-size: 11px; color: #64748b; text-transform: uppercase;
                   letter-spacing: 0.05em; margin-bottom: 4px; }
  .metric .value { font-size: 22px; font-weight: 700; color: #0f172a; }
  .timeline-item { display: flex; gap: 16px; padding: 8px 0;
                   border-bottom: 1px solid #f1f5f9; font-size: 13px; }
  .timeline-item .ts { color: #64748b; min-width: 150px; font-family: monospace; }
  .confidential { font-size: 11px; color: #dc2626; font-weight: 700; letter-spacing: 0.05em; }
  .footer { text-align: center; font-size: 12px; color: #94a3b8; padding: 20px 0; }
"""


class HTMLReporter:

    def render(self, data: ReportData) -> str:
        case_num = data.case.get("case_number", data.case_id)
        status   = data.case.get("status", "unknown")
        status_color = _STATUS_COLOR.get(status, "#6b7280")

        body = "\n".join(filter(None, [
            self._header_section(data, case_num, status, status_color),
            self._metrics_section(data),
            self._executive_summary_section(data),
            self._metadata_section(data),
            self._findings_section(data),
            self._risk_section(data),
            self._timeline_section(data),
            self._agent_runs_section(data),
            self._remediation_section(data),
            self._regulatory_section(data),
        ]))

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Investigation Report — {case_num}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="page">
{body}
<div class="footer">
  EvidentRx | Case {data.case_id} | Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
</div>
</div>
</body>
</html>"""

    def write(self, data: ReportData, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        case_num = data.case.get("case_number", data.case_id)
        path = output_dir / f"{case_num}.html"
        path.write_text(self.render(data), encoding="utf-8")
        return path

    # ------------------------------------------------------------------
    # Sections
    # ------------------------------------------------------------------

    def _header_section(self, data: ReportData, case_num: str, status: str, status_color: str) -> str:
        return f"""
<div class="header">
  <div class="confidential">⚑ CONFIDENTIAL — COMPLIANCE PRIVILEGED</div>
  <h1>340B Compliance Investigation Report</h1>
  <div class="meta">
    Case <strong>{case_num}</strong> &nbsp;·&nbsp;
    {data.ce_name} &nbsp;·&nbsp;
    <span style="color:{status_color};font-weight:700">{status.upper()}</span>
    &nbsp;·&nbsp;
    {datetime.utcnow().strftime('%B %d, %Y')}
  </div>
</div>"""

    def _metrics_section(self, data: ReportData) -> str:
        snap = data.risk_snapshot or {}
        by_sev = snap.get("by_severity", {})
        exposure = data.financial_exposure

        metrics = [
            ("Total Findings",    data.total_findings,                 "#0f172a"),
            ("Critical",          data.critical_count,                 _SEVERITY_COLOR["critical"]),
            ("High",              data.high_count,                     _SEVERITY_COLOR["high"]),
            ("Risk Score",        snap.get("composite_risk_score", "—"), "#7c3aed"),
            ("Unique Patients",   snap.get("unique_patients", 0),       "#0f172a"),
            ("Exposure Est.",
             f"${exposure:,.0f}" if exposure else "—",                  "#dc2626"),
        ]

        cards = "".join(
            f'<div class="metric">'
            f'<div class="label">{label}</div>'
            f'<div class="value" style="color:{color}">{value}</div>'
            f'</div>'
            for label, value, color in metrics
        )

        return f'<div class="section"><div class="metric-grid">{cards}</div></div>'

    def _executive_summary_section(self, data: ReportData) -> str:
        text = data.narrative.get("executive_summary", "")
        if not text:
            snap = data.risk_snapshot or {}
            by_sev = snap.get("by_severity", {})
            text = (
                f"This investigation case covers {data.total_findings} confirmed 340B compliance "
                f"findings for {data.ce_name}. The finding set includes {data.critical_count} "
                f"critical and {data.high_count} high severity violations."
            )
        text_html = "".join(f"<p>{p}</p>" for p in text.split("\n\n") if p.strip())
        return (
            f'<div class="section">'
            f'<h2>Executive Summary</h2>'
            f'<div class="prose">{text_html}</div>'
            f'</div>'
        )

    def _metadata_section(self, data: ReportData) -> str:
        c = data.case
        rows = [
            ("Case Number",         c.get("case_number", "—")),
            ("Covered Entity ID",   str(c.get("covered_entity_id", "—"))),
            ("Violation Category",  c.get("violation_category", "—")),
            ("Priority",            str(c.get("priority", "—"))),
            ("Opened At",           str(c.get("opened_at", "—"))[:19]),
            ("Closed At",           str(c.get("closed_at", "—"))[:19] if c.get("closed_at") else "Open"),
            ("Assigned To",         c.get("assigned_to") or "Unassigned"),
        ]
        items = "".join(
            f'<div class="key">{k}</div><div class="val">{v}</div>'
            for k, v in rows
        )
        return (
            f'<div class="section">'
            f'<h2>Case Metadata</h2>'
            f'<div class="kv">{items}</div>'
            f'</div>'
        )

    def _findings_section(self, data: ReportData) -> str:
        if not data.findings:
            return ""
        rows_html = ""
        for f in data.findings[:50]:
            sev   = f.get("severity", "")
            color = _SEVERITY_COLOR.get(sev, "#6b7280")
            badge = f'<span class="badge" style="background:{color}">{sev.upper()}</span>'
            rows_html += (
                f"<tr>"
                f"<td><span class='mono'>{f.get('finding_code','—')}</span></td>"
                f"<td><span class='mono'>{f.get('rule_code','—')}</span></td>"
                f"<td>{badge}</td>"
                f"<td><span class='mono'>{f.get('ndc_11','—')}</span></td>"
                f"<td>{str(f.get('service_date','—'))[:10]}</td>"
                f"<td>{'✓' if f.get('is_primary') else ''}</td>"
                f"</tr>"
            )
        note = (
            f"<p style='font-size:12px;color:#64748b'>"
            f"Showing {min(len(data.findings),50)} of {data.total_findings} findings. "
            f"All findings are deterministic — not AI-generated.</p>"
        )
        return (
            f'<div class="section">'
            f'<h2>Confirmed Findings</h2>'
            f'{note}'
            f'<table><tr>'
            f'<th>Finding Code</th><th>Rule</th><th>Severity</th>'
            f'<th>NDC</th><th>Service Date</th><th>Primary</th>'
            f'</tr>{rows_html}</table>'
            f'</div>'
        )

    def _risk_section(self, data: ReportData) -> str:
        snap = data.risk_snapshot
        if not snap:
            return ""
        window  = snap.get("temporal_window", {})
        by_rule = snap.get("findings_by_rule", {})
        rule_rows = "".join(
            f"<tr><td><span class='mono'>{r}</span></td><td>{c}</td></tr>"
            for r, c in sorted(by_rule.items())
        )
        return (
            f'<div class="section">'
            f'<h2>Risk Snapshot</h2>'
            f'<div class="kv">'
            f'<div class="key">Temporal Window</div>'
            f'<div class="val">{window.get("start","—")} → {window.get("end","—")}</div>'
            f'<div class="key">NDCs Affected</div>'
            f'<div class="val">{len(snap.get("ndc_list",[]))}</div>'
            f'</div>'
            f'{("<h3>Findings by Rule</h3><table><tr><th>Rule</th><th>Count</th></tr>" + rule_rows + "</table>") if rule_rows else ""}'
            f'</div>'
        )

    def _timeline_section(self, data: ReportData) -> str:
        if not data.timeline:
            return ""
        items = "".join(
            f'<div class="timeline-item">'
            f'<span class="ts">{str(e.get("occurred_at","—"))[:19]}</span>'
            f'<span><span class="mono">{e.get("event_type","—")}</span> — '
            f'{e.get("actor_id","—")} ({e.get("actor_type","—")})</span>'
            f'</div>'
            for e in data.timeline
        )
        return f'<div class="section"><h2>Investigation Timeline</h2>{items}</div>'

    def _agent_runs_section(self, data: ReportData) -> str:
        if not data.agent_runs:
            return ""
        rows_html = "".join(
            f"<tr>"
            f"<td><span class='mono'>{ar.get('agent_type','—')}</span></td>"
            f"<td>{ar.get('status','—')}</td>"
            f"<td>{ar.get('model_id') or '—'}</td>"
            f"<td>{ar.get('input_tokens') or 0:,}</td>"
            f"<td>{ar.get('output_tokens') or 0:,}</td>"
            f"<td>{ar.get('latency_ms') or '—'}</td>"
            f"</tr>"
            for ar in data.agent_runs
        )
        tech = data.narrative.get("technical_findings", "")
        tech_html = ""
        if tech:
            tech_html = (
                f'<h3>Technical Findings Narrative</h3>'
                f'<div class="prose">{"".join(f"<p>{p}</p>" for p in tech.split(chr(10)+chr(10)) if p.strip())}</div>'
            )
        return (
            f'<div class="section">'
            f'<h2>Agent Execution & Reasoning</h2>'
            f'<table><tr>'
            f'<th>Agent</th><th>Status</th><th>Model</th>'
            f'<th>Tokens In</th><th>Tokens Out</th><th>Latency (ms)</th>'
            f'</tr>{rows_html}</table>'
            f'{tech_html}'
            f'</div>'
        )

    def _remediation_section(self, data: ReportData) -> str:
        recs = data.narrative.get("remediation_recommendations", [])
        if not recs:
            return ""
        priority_order = {"immediate": 0, "short_term": 1, "long_term": 2}
        sorted_recs = sorted(recs, key=lambda r: priority_order.get(r.get("priority", ""), 99))
        items = "".join(
            f'<div class="rec">'
            f'<div class="priority">{r.get("priority","—").upper().replace("_"," ")}</div>'
            f'<div class="action">{r.get("action","")}</div>'
            f'<div class="rationale">{r.get("rationale","")}</div>'
            f'</div>'
            for r in sorted_recs
        )
        return f'<div class="section"><h2>Remediation Recommendations</h2>{items}</div>'

    def _regulatory_section(self, data: ReportData) -> str:
        reg = data.narrative.get("regulatory_context", "")
        notes = data.narrative.get("audit_preparation_notes", "")
        if not reg and not notes:
            return ""
        reg_html = (
            f'<h3>Regulatory Context</h3>'
            f'<div class="prose">{"".join(f"<p>{p}</p>" for p in reg.split(chr(10)+chr(10)) if p.strip())}</div>'
            if reg else ""
        )
        notes_html = (
            f'<h3>Audit Preparation Notes</h3>'
            f'<div class="prose"><p>{notes}</p></div>'
            if notes else ""
        )
        return f'<div class="section"><h2>Regulatory & Audit</h2>{reg_html}{notes_html}</div>'
