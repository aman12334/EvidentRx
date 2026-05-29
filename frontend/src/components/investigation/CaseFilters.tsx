"use client";
import { SearchInput } from "@/components/ui/SearchInput";

type CaseStatus   = "open" | "triaged" | "investigating" | "escalated" | "resolved" | "closed" | "";
type CasePriority = "critical" | "high" | "medium" | "low" | "";

interface CaseFiltersProps {
  search:       string;
  status:       CaseStatus;
  priority:     CasePriority;
  onSearch:     (v: string) => void;
  onStatus:     (v: CaseStatus) => void;
  onPriority:   (v: CasePriority) => void;
  onReset:      () => void;
  resultCount?: number;
  loading?:     boolean;
}

const STATUS_OPTIONS: { value: CaseStatus; label: string }[] = [
  { value: "",              label: "All statuses"  },
  { value: "open",          label: "Open"          },
  { value: "triaged",       label: "Triaged"       },
  { value: "investigating", label: "Investigating" },
  { value: "escalated",     label: "Escalated"     },
  { value: "resolved",      label: "Resolved"      },
];

const PRIORITY_OPTIONS: { value: CasePriority; label: string }[] = [
  { value: "",         label: "All priorities" },
  { value: "critical", label: "Critical"       },
  { value: "high",     label: "High"           },
  { value: "medium",   label: "Medium"         },
  { value: "low",      label: "Low"            },
];

export function CaseFilters({
  search,
  status,
  priority,
  onSearch,
  onStatus,
  onPriority,
  onReset,
  resultCount,
  loading = false,
}: CaseFiltersProps) {
  const isFiltered = search !== "" || status !== "" || priority !== "";

  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:gap-4">
      {/* Search */}
      <SearchInput
        value={search}
        onChange={onSearch}
        placeholder="Search cases, entities…"
        className="w-full sm:max-w-xs"
      />

      {/* Status filter */}
      <select
        value={status}
        onChange={(e) => onStatus(e.target.value as CaseStatus)}
        className="rounded-md border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2 text-sm text-slate-700 dark:text-slate-300 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 transition-colors"
      >
        {STATUS_OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>

      {/* Priority filter */}
      <select
        value={priority}
        onChange={(e) => onPriority(e.target.value as CasePriority)}
        className="rounded-md border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2 text-sm text-slate-700 dark:text-slate-300 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 transition-colors"
      >
        {PRIORITY_OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>

      {/* Reset */}
      {isFiltered && (
        <button
          type="button"
          onClick={onReset}
          className="shrink-0 text-xs font-medium text-slate-500 hover:text-blue-600 transition-colors underline-offset-2 hover:underline"
        >
          Reset filters
        </button>
      )}

      {/* Result count */}
      {resultCount !== undefined && !loading && (
        <p className="ml-auto shrink-0 text-xs text-slate-400">
          {resultCount.toLocaleString()} {resultCount === 1 ? "case" : "cases"}
        </p>
      )}
      {loading && (
        <div className="ml-auto h-4 w-16 animate-pulse rounded bg-slate-100 dark:bg-slate-800" />
      )}
    </div>
  );
}
