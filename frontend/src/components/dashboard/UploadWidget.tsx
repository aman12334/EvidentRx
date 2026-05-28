"use client";
/**
 * UploadWidget — compact upload CTA for the dashboard sidebar / overview grid.
 *
 * Shows a summary of recent upload activity and a quick-upload button.
 * Clicking "Upload Data" expands to the full DataUploadPanel.
 */
import { useEffect, useState } from "react";
import { Card }              from "@/components/ui/Card";
import { fetchUploadHistory, type BatchHistoryItem } from "@/lib/api/upload";

export function UploadWidget() {
  const [recent, setRecent]   = useState<BatchHistoryItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchUploadHistory(5)
      .then(setRecent)
      .catch(() => setRecent([]))
      .finally(() => setLoading(false));
  }, []);

  const totalFindings = recent.reduce((s, r) => s + (r.findings_count ?? 0), 0);
  const lastUpload    = recent[0];

  return (
    <Card padding="md">
      <div className="flex items-start justify-between mb-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Data Uploads
          </p>
          <p className="mt-1 text-2xl font-bold text-slate-900 dark:text-white">
            {loading ? "—" : recent.length}
          </p>
          <p className="text-xs text-slate-400 mt-0.5">
            {loading ? "loading…" : `${totalFindings} findings from last ${recent.length} uploads`}
          </p>
        </div>
        <div className="rounded-lg bg-indigo-50 dark:bg-indigo-900/30 p-2.5">
          <svg className="w-5 h-5 text-indigo-600" fill="none" viewBox="0 0 24 24"
            stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round"
              d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
          </svg>
        </div>
      </div>

      {lastUpload && (
        <div className="mb-3 rounded-lg bg-slate-50 dark:bg-slate-800/50 px-3 py-2 text-xs">
          <p className="text-slate-500">Last upload</p>
          <p className="font-medium text-slate-700 dark:text-slate-300 truncate mt-0.5">
            {lastUpload.filename}
          </p>
          <p className="text-slate-400 mt-0.5">
            {new Date(lastUpload.started_at).toLocaleDateString()}
            {" · "}{lastUpload.record_count.toLocaleString()} records
            {" · "}
            <span className={lastUpload.findings_count ? "text-red-600 font-semibold" : "text-green-600"}>
              {lastUpload.findings_count ?? 0} findings
            </span>
          </p>
        </div>
      )}

      <a
        href="/investigations"
        className="block w-full rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white
                   text-xs font-medium text-center py-2 transition-colors"
      >
        Upload New Data
      </a>
    </Card>
  );
}
