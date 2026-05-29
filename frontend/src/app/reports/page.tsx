"use client";
import { useState } from "react";
import { PageHeader }         from "@/components/layout/PageHeader";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button }             from "@/components/ui/Button";
import { Spinner }            from "@/components/ui/Spinner";
import { useDashboard }       from "@/lib/hooks/useDashboard";

type ReportFormat = "csv" | "json";
type ReportType   =
  | "investigation_queue"
  | "findings_by_rule"
  | "exposure_summary"
  | "entity_risk_scores";

interface ReportDefinition {
  id:          ReportType;
  title:       string;
  description: string;
  endpoint:    string;
}

const REPORTS: ReportDefinition[] = [
  {
    id:          "investigation_queue",
    title:       "Investigation Queue",
    description: "All open/active cases with status, priority, entity, exposure, and risk score.",
    endpoint:    "/api/v1/investigations/queue?limit=1000",
  },
  {
    id:          "findings_by_rule",
    title:       "Findings by Rule",
    description: "Total finding counts grouped by compliance rule code and severity.",
    endpoint:    "/api/v1/findings/summary",
  },
  {
    id:          "exposure_summary",
    title:       "Financial Exposure Summary",
    description: "Rolling 90-day financial exposure trend across all investigation cases.",
    endpoint:    "/api/v1/dashboard/exposure-trend",
  },
  {
    id:          "entity_risk_scores",
    title:       "Entity Risk Scores",
    description: "Composite risk scores for all monitored covered entities and contract pharmacies.",
    endpoint:    "/api/v1/intelligence/risk-scores",
  },
];

function downloadBlob(data: unknown, filename: string, format: ReportFormat) {
  let blob: Blob;
  if (format === "json") {
    blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  } else {
    // Flatten the first array we find in the response
    const rows: Record<string, unknown>[] = Array.isArray(data)
      ? data
      : (data as Record<string, unknown[]>)[
          Object.keys(data as object).find((k) =>
            Array.isArray((data as Record<string, unknown>)[k])
          ) ?? ""
        ] ?? [];

    if (rows.length === 0) {
      blob = new Blob(["(no data)"], { type: "text/csv" });
    } else {
      const headers = Object.keys(rows[0]).join(",");
      const body = rows
        .map((r) =>
          Object.values(r)
            .map((v) => (v == null ? "" : `"${String(v).replace(/"/g, '""')}"`))
            .join(",")
        )
        .join("\n");
      blob = new Blob([`${headers}\n${body}`], { type: "text/csv" });
    }
  }
  const url = URL.createObjectURL(blob);
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export default function ReportsPage() {
  const { data: metrics } = useDashboard();
  const [downloading, setDownloading] = useState<ReportType | null>(null);
  const [format, setFormat] = useState<ReportFormat>("csv");

  const handleDownload = async (report: ReportDefinition) => {
    setDownloading(report.id);
    try {
      const res = await fetch(report.endpoint);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const ts   = new Date().toISOString().slice(0, 10);
      downloadBlob(data, `evidentrx_${report.id}_${ts}.${format}`, format);
    } catch {
      alert("Failed to generate report. Ensure the API server is running.");
    } finally {
      setDownloading(null);
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Reports"
        description="Export compliance data for audit, review, or external reporting."
      />

      {/* KPI strip */}
      {metrics && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <KpiChip label="Open Cases"      value={metrics.open_cases} />
          <KpiChip label="Total Findings"  value={metrics.total_findings} />
          <KpiChip label="Critical"        value={metrics.critical_findings} color="red" />
          <KpiChip
            label="Exposure"
            value={`$${(metrics.total_exposure ?? 0).toLocaleString()}`}
          />
        </div>
      )}

      {/* Format selector */}
      <Card padding="md">
        <CardHeader><CardTitle>Export Format</CardTitle></CardHeader>
        <div className="mt-3 flex gap-3">
          {(["csv", "json"] as ReportFormat[]).map((f) => (
            <label
              key={f}
              className={`flex cursor-pointer items-center gap-2 rounded-md border px-4 py-2 text-sm font-medium transition-colors ${
                format === f
                  ? "border-blue-500 bg-blue-50 dark:bg-blue-950/30 text-blue-700 dark:text-blue-300"
                  : "border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-800"
              }`}
            >
              <input
                type="radio"
                name="format"
                value={f}
                checked={format === f}
                onChange={() => setFormat(f)}
                className="sr-only"
              />
              {f.toUpperCase()}
            </label>
          ))}
        </div>
      </Card>

      {/* Report cards */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {REPORTS.map((report) => (
          <Card key={report.id} padding="md">
            <div className="flex flex-col h-full">
              <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-200">
                {report.title}
              </h3>
              <p className="mt-1 text-xs text-slate-500 dark:text-slate-400 flex-1">
                {report.description}
              </p>
              <div className="mt-4">
                <Button
                  size="sm"
                  variant="secondary"
                  loading={downloading === report.id}
                  onClick={() => handleDownload(report)}
                >
                  {downloading === report.id ? (
                    <span className="flex items-center gap-2">
                      <Spinner size="sm" />
                      Generating…
                    </span>
                  ) : (
                    `↓ Export ${format.toUpperCase()}`
                  )}
                </Button>
              </div>
            </div>
          </Card>
        ))}
      </div>

      <p className="text-xs text-slate-400">
        All exports are point-in-time snapshots. Timestamps are UTC. Data is not
        modified by downloading — this is a read-only operation.
      </p>
    </div>
  );
}

function KpiChip({
  label,
  value,
  color = "default",
}: {
  label: string;
  value: number | string;
  color?: "default" | "red";
}) {
  return (
    <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-4 py-3">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">{label}</p>
      <p
        className={`mt-1 text-xl font-bold ${
          color === "red"
            ? "text-red-600 dark:text-red-400"
            : "text-slate-900 dark:text-white"
        }`}
      >
        {value}
      </p>
    </div>
  );
}
