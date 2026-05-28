"use client";
/**
 * FindingsSeverityBadge — coloured pill for compliance finding severity.
 * Used across the dashboard, investigation queue, and upload result panel.
 */

type Severity = "critical" | "high" | "medium" | "low";

const STYLES: Record<Severity, string> = {
  critical: "bg-red-100   text-red-800   dark:bg-red-900/30  dark:text-red-300  border border-red-200",
  high:     "bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-300 border border-orange-200",
  medium:   "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-300 border border-yellow-200",
  low:      "bg-blue-100  text-blue-800  dark:bg-blue-900/30  dark:text-blue-300  border border-blue-200",
};

const DOT: Record<Severity, string> = {
  critical: "bg-red-500",
  high:     "bg-orange-500",
  medium:   "bg-yellow-400",
  low:      "bg-blue-400",
};

interface Props {
  severity:  Severity;
  showDot?:  boolean;
  compact?:  boolean;
}

export function FindingsSeverityBadge({ severity, showDot = true, compact = false }: Props) {
  return (
    <span className={`inline-flex items-center gap-1 rounded font-medium
      ${compact ? "px-1.5 py-0.5 text-xs" : "px-2 py-0.5 text-xs"}
      ${STYLES[severity] ?? "bg-slate-100 text-slate-700"}`}>
      {showDot && (
        <span className={`w-1.5 h-1.5 rounded-full ${DOT[severity] ?? "bg-slate-400"}`} />
      )}
      {severity}
    </span>
  );
}
