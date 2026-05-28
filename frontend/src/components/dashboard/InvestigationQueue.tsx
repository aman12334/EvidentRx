"use client";
import { useState } from "react";
import { CaseCard } from "@/components/investigation/CaseCard";
import { Spinner } from "@/components/ui/Spinner";
import { EmptyState } from "@/components/ui/EmptyState";
import { Button } from "@/components/ui/Button";
import { useInvestigationQueue } from "@/lib/hooks/useInvestigation";
import { useInvestigationStore } from "@/lib/store/investigationStore";
import type { CaseStatus } from "@/lib/types/investigation";

const STATUS_TABS: { label: string; value: CaseStatus | "all" }[] = [
  { label: "All",           value: "all" },
  { label: "Open",          value: "open" },
  { label: "Triaged",       value: "triaged" },
  { label: "Investigating", value: "investigating" },
  { label: "Escalated",     value: "escalated" },
];

export function InvestigationQueue() {
  const { queueStatusFilter, setStatusFilter, queuePage, setQueuePage } =
    useInvestigationStore();

  const [activeTab, setActiveTab] = useState<CaseStatus | "all">(
    queueStatusFilter ?? "all"
  );

  const { data, isLoading, isError } = useInvestigationQueue({
    status: activeTab === "all" ? undefined : activeTab,
    page:   queuePage,
    limit:  20,
  });

  function handleTabChange(tab: CaseStatus | "all") {
    setActiveTab(tab);
    setStatusFilter(tab === "all" ? null : tab);
    setQueuePage(1);
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Status filter tabs */}
      <div className="flex gap-1 border-b border-slate-200 dark:border-slate-700">
        {STATUS_TABS.map((t) => (
          <button
            key={t.value}
            onClick={() => handleTabChange(t.value)}
            className={`px-3 py-2 text-xs font-semibold uppercase tracking-wide transition-colors ${
              activeTab === t.value
                ? "border-b-2 border-blue-600 text-blue-600"
                : "text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Case list */}
      {isLoading ? (
        <div className="flex justify-center py-12">
          <Spinner size="lg" />
        </div>
      ) : isError ? (
        <EmptyState
          title="Failed to load cases"
          description="Unable to fetch the investigation queue. Check the API connection."
        />
      ) : !data || data.items.length === 0 ? (
        <EmptyState
          title="No cases"
          description={
            activeTab === "all"
              ? "No investigation cases have been opened yet."
              : `No cases with status "${activeTab}".`
          }
        />
      ) : (
        <>
          <div className="flex flex-col gap-3">
            {data.items.map((c) => (
              <CaseCard key={c.case_id} case_={c} />
            ))}
          </div>

          {/* Pagination */}
          {data.total > data.limit && (
            <div className="flex items-center justify-between pt-2 text-xs text-slate-500">
              <span>
                {(data.page - 1) * data.limit + 1}–
                {Math.min(data.page * data.limit, data.total)} of {data.total}
              </span>
              <div className="flex gap-2">
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={data.page <= 1}
                  onClick={() => setQueuePage(data.page - 1)}
                >
                  Prev
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={data.page * data.limit >= data.total}
                  onClick={() => setQueuePage(data.page + 1)}
                >
                  Next
                </Button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
