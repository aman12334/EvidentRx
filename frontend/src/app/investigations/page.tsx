"use client";
import { useState } from "react";
import { PageHeader }         from "@/components/layout/PageHeader";
import { InvestigationQueue } from "@/components/dashboard/InvestigationQueue";
import { DataUploadPanel }    from "@/components/dashboard/DataUploadPanel";

export default function InvestigationsPage() {
  const [showUpload, setShowUpload] = useState(false);

  return (
    <div>
      <PageHeader
        title="Investigation Queue"
        description="All open 340B compliance cases across covered entities."
        actions={
          <button
            onClick={() => setShowUpload(v => !v)}
            className={`
              inline-flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium
              transition-colors
              ${showUpload
                ? "bg-indigo-100 text-indigo-700 hover:bg-indigo-200"
                : "bg-indigo-600 text-white hover:bg-indigo-700"
              }
            `}
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round"
                d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
            </svg>
            {showUpload ? "Hide Upload" : "Upload Data"}
          </button>
        }
      />

      {showUpload && (
        <div className="mb-6">
          <DataUploadPanel />
        </div>
      )}

      <InvestigationQueue />
    </div>
  );
}
