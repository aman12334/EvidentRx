"use client";

/**
 * DataUploadPanel — drag-and-drop 340B data upload for hospitals.
 *
 * Accepts CSV files containing dispense or claim records, runs the full
 * compliance pipeline on the server, and shows a live findings summary.
 *
 * Integration: POST /api/v1/upload/claims
 *             GET  /api/v1/upload/history
 *             GET  /api/v1/upload/template?file_type=dispenses
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Card } from "@/components/ui/Card";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// ── Types ─────────────────────────────────────────────────────────────────────

interface BatchHistoryItem {
  batch_id:       string;
  filename:       string;
  status:         string;
  record_count:   number;
  started_at:     string;
  completed_at:   string | null;
  findings_count: number | null;
}

interface FindingSummary {
  rule_code:   string;
  description: string;
  count:       number;
  severity:    "critical" | "high" | "medium" | "low";
}

interface UploadResult {
  upload_id:          string;
  batch_id:           string;
  status:             "complete" | "partial" | "no_findings";
  message:            string;
  rows_parsed:        number;
  dispenses_inserted: number;
  claims_inserted:    number;
  split_billing_rows: number;
  cases_created:      number;
  total_findings:     number;
  critical_findings:  number;
  high_findings:      number;
  estimated_exposure: number | null;
  findings_by_rule:   FindingSummary[];
  case_ids:           string[];
  processing_ms:      number;
}

type UploadState = "idle" | "dragging" | "uploading" | "done" | "error";
type ActiveTab  = "upload" | "history";

// ── Severity colours ──────────────────────────────────────────────────────────

const SEVERITY_COLOR: Record<string, string> = {
  critical: "bg-red-100 text-red-800 border border-red-200",
  high:     "bg-orange-100 text-orange-800 border border-orange-200",
  medium:   "bg-yellow-100 text-yellow-800 border border-yellow-200",
  low:      "bg-blue-100 text-blue-800 border border-blue-200",
};

const SEVERITY_DOT: Record<string, string> = {
  critical: "bg-red-500",
  high:     "bg-orange-500",
  medium:   "bg-yellow-400",
  low:      "bg-blue-400",
};

// ── Component ─────────────────────────────────────────────────────────────────

export function DataUploadPanel() {
  const [state,    setState]    = useState<UploadState>("idle");
  const [progress, setProgress] = useState(0);
  const [result,   setResult]   = useState<UploadResult | null>(null);
  const [errorMsg, setErrorMsg] = useState<string>("");
  const [filename, setFilename] = useState<string>("");
  const [activeTab, setActiveTab] = useState<ActiveTab>("upload");
  const [history,  setHistory]  = useState<BatchHistoryItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const progressRef  = useRef<ReturnType<typeof setInterval> | null>(null);

  // Load history when tab switches
  useEffect(() => {
    if (activeTab !== "history") return;
    setHistoryLoading(true);
    fetch(`${API}/api/v1/upload/history`)
      .then(r => r.ok ? r.json() : [])
      .then(setHistory)
      .catch(() => setHistory([]))
      .finally(() => setHistoryLoading(false));
  }, [activeTab]);

  // -- Fake incremental progress while waiting for API ----------------------
  const startProgress = () => {
    setProgress(0);
    progressRef.current = setInterval(() => {
      setProgress(p => {
        if (p >= 88) {
          clearInterval(progressRef.current!);
          return 88;
        }
        // Accelerate early, slow near 88
        return p < 40 ? p + 6 : p < 70 ? p + 3 : p + 1;
      });
    }, 200);
  };

  const stopProgress = () => {
    clearInterval(progressRef.current!);
    setProgress(100);
  };

  // -- File upload -----------------------------------------------------------
  const processFile = useCallback(async (file: File) => {
    if (!file.name.match(/\.(csv|tsv|txt)$/i)) {
      setErrorMsg("Please upload a CSV file (.csv, .tsv, or .txt).");
      setState("error");
      return;
    }

    setFilename(file.name);
    setState("uploading");
    startProgress();

    const formData = new FormData();
    formData.append("file", file);

    try {
      const resp = await fetch(`${API}/api/v1/upload/claims`, {
        method: "POST",
        body:   formData,
      });

      stopProgress();

      if (!resp.ok) {
        const errBody = await resp.json().catch(() => ({ detail: resp.statusText }));
        throw new Error(errBody.detail || `Server error ${resp.status}`);
      }

      const data: UploadResult = await resp.json();
      setResult(data);
      setState("done");
    } catch (err: unknown) {
      stopProgress();
      setErrorMsg(err instanceof Error ? err.message : "Upload failed. Try again.");
      setState("error");
    }
  }, []);

  // -- Drag handlers ---------------------------------------------------------
  const onDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setState("dragging");
  };
  const onDragLeave = () => setState("idle");
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setState("idle");
    const file = e.dataTransfer.files?.[0];
    if (file) processFile(file);
  };
  const onFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) processFile(file);
  };
  const reset = () => {
    setState("idle");
    setResult(null);
    setErrorMsg("");
    setFilename("");
    setProgress(0);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <Card padding="lg" className="w-full">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-base font-semibold text-slate-900 dark:text-white">
            340B Data Upload
          </h2>
          <p className="text-xs text-slate-500 mt-0.5">
            Upload dispense or claim records — compliance checks run instantly.
          </p>
        </div>
        <a
          href={`${API}/api/v1/upload/template?file_type=dispenses`}
          className="text-xs text-indigo-600 hover:text-indigo-800 underline"
          target="_blank"
          rel="noreferrer"
        >
          Download template ↓
        </a>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-4 border-b border-slate-200 dark:border-slate-700">
        {(["upload", "history"] as ActiveTab[]).map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`
              px-3 py-1.5 text-xs font-medium rounded-t capitalize transition-colors
              ${activeTab === tab
                ? "border-b-2 border-indigo-600 text-indigo-700 dark:text-indigo-300"
                : "text-slate-500 hover:text-slate-700"
              }
            `}
          >
            {tab === "upload" ? "Upload" : "History"}
          </button>
        ))}
      </div>

      {/* ══ History tab ══════════════════════════════════════════════════ */}
      {activeTab === "history" && (
        <div>
          {historyLoading ? (
            <div className="flex items-center gap-2 text-sm text-slate-500 py-6 justify-center">
              <div className="w-4 h-4 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
              Loading history…
            </div>
          ) : history.length === 0 ? (
            <div className="text-center py-8 text-sm text-slate-400">
              No uploads yet. Switch to the Upload tab to get started.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-slate-200 dark:border-slate-700">
                    {["File", "Status", "Records", "Findings", "Uploaded"].map(h => (
                      <th key={h} className="text-left py-2 pr-4 text-slate-500 font-semibold uppercase tracking-wide">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {history.map(row => (
                    <tr key={row.batch_id}
                      className="border-b border-slate-100 dark:border-slate-800 hover:bg-slate-50 dark:hover:bg-slate-800/40">
                      <td className="py-2 pr-4 font-mono text-slate-700 dark:text-slate-300 max-w-[160px] truncate">
                        {row.filename}
                      </td>
                      <td className="py-2 pr-4">
                        <span className={`px-1.5 py-0.5 rounded text-xs ${
                          row.status === "complete" ? "bg-green-100 text-green-700" :
                          row.status === "processing" ? "bg-yellow-100 text-yellow-700" :
                          "bg-slate-100 text-slate-600"
                        }`}>
                          {row.status}
                        </span>
                      </td>
                      <td className="py-2 pr-4 text-slate-600 dark:text-slate-400">
                        {row.record_count.toLocaleString()}
                      </td>
                      <td className={`py-2 pr-4 font-semibold ${
                        (row.findings_count ?? 0) > 0 ? "text-red-600" : "text-green-600"
                      }`}>
                        {row.findings_count ?? 0}
                      </td>
                      <td className="py-2 text-slate-400">
                        {row.started_at ? new Date(row.started_at).toLocaleDateString() : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ══ Upload tab ═══════════════════════════════════════════════════ */}
      {activeTab === "upload" && <>

      {/* ── Upload zone ──────────────────────────────────────────────────── */}
      {(state === "idle" || state === "dragging") && (
        <div
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
          onClick={() => fileInputRef.current?.click()}
          className={`
            relative flex flex-col items-center justify-center gap-3
            rounded-xl border-2 border-dashed cursor-pointer transition-all
            py-10 px-6
            ${state === "dragging"
              ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-900/20"
              : "border-slate-300 dark:border-slate-600 bg-slate-50 dark:bg-slate-800/40 hover:border-indigo-400 hover:bg-indigo-50/50"
            }
          `}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept=".csv,.tsv,.txt"
            onChange={onFileChange}
            className="sr-only"
            aria-label="Upload CSV file"
          />

          {/* Icon */}
          <div className={`
            rounded-full p-4
            ${state === "dragging" ? "bg-indigo-100 dark:bg-indigo-800" : "bg-slate-100 dark:bg-slate-700"}
          `}>
            <svg
              className={`w-8 h-8 ${state === "dragging" ? "text-indigo-600" : "text-slate-400"}`}
              fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}
            >
              <path strokeLinecap="round" strokeLinejoin="round"
                d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
            </svg>
          </div>

          <div className="text-center">
            <p className={`text-sm font-medium ${state === "dragging" ? "text-indigo-700" : "text-slate-700 dark:text-slate-300"}`}>
              {state === "dragging" ? "Drop your file here" : "Drag & drop your CSV file"}
            </p>
            <p className="text-xs text-slate-400 mt-1">
              or <span className="text-indigo-600 font-medium underline">browse files</span>
              {" "}· Dispenses or claims · Max 20 MB
            </p>
          </div>

          {/* Accepted formats badge */}
          <div className="flex gap-2 mt-1">
            {["CSV", "TSV"].map(f => (
              <span key={f}
                className="px-2 py-0.5 rounded text-xs font-mono bg-slate-200 dark:bg-slate-700 text-slate-600 dark:text-slate-300">
                {f}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* ── Uploading / progress ─────────────────────────────────────────── */}
      {state === "uploading" && (
        <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800/40 p-8">
          <div className="flex items-center gap-3 mb-4">
            <div className="w-5 h-5 border-2 border-indigo-600 border-t-transparent rounded-full animate-spin" />
            <span className="text-sm font-medium text-slate-700 dark:text-slate-300">
              Analysing <span className="font-semibold text-indigo-600">{filename}</span>…
            </span>
          </div>
          <div className="w-full bg-slate-200 dark:bg-slate-700 rounded-full h-2 overflow-hidden">
            <div
              className="h-2 bg-indigo-500 rounded-full transition-all duration-300"
              style={{ width: `${progress}%` }}
            />
          </div>
          <p className="text-xs text-slate-400 mt-2 text-right">{progress}%</p>
          <div className="mt-4 space-y-1">
            {[
              { label: "Parsing records",       active: progress >= 10 },
              { label: "Normalising & hashing", active: progress >= 30 },
              { label: "Running compliance engine", active: progress >= 55 },
              { label: "Building investigation cases", active: progress >= 80 },
            ].map(step => (
              <div key={step.label} className="flex items-center gap-2">
                <span className={`w-3 h-3 rounded-full flex-shrink-0 transition-colors ${
                  step.active ? "bg-indigo-500" : "bg-slate-300 dark:bg-slate-600"
                }`} />
                <span className={`text-xs transition-colors ${
                  step.active ? "text-slate-700 dark:text-slate-300" : "text-slate-400"
                }`}>
                  {step.label}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Error ────────────────────────────────────────────────────────── */}
      {state === "error" && (
        <div className="rounded-xl border border-red-200 bg-red-50 dark:bg-red-900/20 p-6">
          <div className="flex items-start gap-3">
            <svg className="w-5 h-5 text-red-500 flex-shrink-0 mt-0.5" fill="currentColor" viewBox="0 0 20 20">
              <path fillRule="evenodd"
                d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z"
                clipRule="evenodd" />
            </svg>
            <div>
              <p className="text-sm font-semibold text-red-800 dark:text-red-200">Upload failed</p>
              <p className="text-xs text-red-600 dark:text-red-300 mt-1">{errorMsg}</p>
            </div>
          </div>
          <button
            onClick={reset}
            className="mt-4 text-xs text-red-700 dark:text-red-300 underline hover:no-underline"
          >
            Try again
          </button>
        </div>
      )}

      {/* ── Results ──────────────────────────────────────────────────────── */}
      {state === "done" && result && (
        <div className="space-y-4">
          {/* Summary header */}
          <div className={`
            rounded-xl border p-4
            ${result.status === "no_findings"
              ? "border-green-200 bg-green-50 dark:bg-green-900/20"
              : result.critical_findings > 0
                ? "border-red-200 bg-red-50 dark:bg-red-900/20"
                : "border-orange-200 bg-orange-50 dark:bg-orange-900/20"
            }
          `}>
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className={`text-sm font-semibold ${
                  result.status === "no_findings" ? "text-green-800" :
                  result.critical_findings > 0 ? "text-red-800" : "text-orange-800"
                }`}>
                  {result.status === "no_findings" ? "✓ No violations found" :
                   result.critical_findings > 0  ? "⚠ Critical violations detected" :
                   "⚠ Compliance issues found"}
                </p>
                <p className="text-xs text-slate-500 mt-0.5">{result.message}</p>
              </div>
              <button onClick={reset}
                className="text-xs text-slate-400 hover:text-slate-600 underline flex-shrink-0">
                Upload another
              </button>
            </div>
          </div>

          {/* Stats grid */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[
              { label: "Rows Parsed",    value: result.rows_parsed.toLocaleString() },
              { label: "Findings",       value: result.total_findings.toLocaleString(),
                urgent: result.total_findings > 0 },
              { label: "Cases Created",  value: result.cases_created.toString() },
              { label: "Est. Exposure",  value: result.estimated_exposure != null
                ? `$${(result.estimated_exposure / 1000).toFixed(1)}k` : "—",
                urgent: (result.estimated_exposure ?? 0) > 10000 },
            ].map(stat => (
              <div key={stat.label}
                className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2.5">
                <p className="text-xs text-slate-500 uppercase tracking-wide font-medium">{stat.label}</p>
                <p className={`text-xl font-bold mt-0.5 ${
                  stat.urgent ? "text-red-600" : "text-slate-900 dark:text-white"
                }`}>
                  {stat.value}
                </p>
              </div>
            ))}
          </div>

          {/* Findings by rule */}
          {result.findings_by_rule.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-slate-600 dark:text-slate-400 uppercase tracking-wide mb-2">
                Findings by Rule
              </p>
              <div className="space-y-1.5">
                {result.findings_by_rule.map(f => (
                  <div key={f.rule_code}
                    className="flex items-center justify-between rounded-lg px-3 py-2
                               bg-white dark:bg-slate-800
                               border border-slate-100 dark:border-slate-700">
                    <div className="flex items-center gap-2 min-w-0">
                      <span className={`w-2 h-2 rounded-full flex-shrink-0 ${SEVERITY_DOT[f.severity] ?? "bg-slate-400"}`} />
                      <span className="text-xs font-mono text-slate-600 dark:text-slate-400 flex-shrink-0">
                        {f.rule_code}
                      </span>
                      <span className="text-xs text-slate-500 truncate">{f.description}</span>
                    </div>
                    <div className="flex items-center gap-2 flex-shrink-0 ml-2">
                      <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${
                        SEVERITY_COLOR[f.severity] ?? "bg-slate-100 text-slate-700"
                      }`}>
                        {f.severity}
                      </span>
                      <span className="text-xs font-semibold text-slate-900 dark:text-white w-6 text-right">
                        {f.count}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Case links */}
          {result.case_ids.length > 0 && (
            <div className="pt-1">
              <a
                href="/investigations"
                className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-700
                           text-white text-sm font-medium px-4 py-2 transition-colors"
              >
                View {result.cases_created} investigation case{result.cases_created !== 1 ? "s" : ""}
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 4.5L21 12m0 0l-7.5 7.5M21 12H3" />
                </svg>
              </a>
            </div>
          )}

          {/* Processing time */}
          <p className="text-xs text-slate-400 text-right">
            Processed in {(result.processing_ms / 1000).toFixed(1)}s
            {" "}· Batch ID: <span className="font-mono">{result.batch_id.slice(0, 8)}…</span>
          </p>
        </div>
      )}

      </>}  {/* end upload tab */}
    </Card>
  );
}
