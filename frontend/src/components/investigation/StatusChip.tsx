import { clsx } from "clsx";
import type { CaseStatus } from "@/lib/types/investigation";

const statusStyles: Record<CaseStatus, string> = {
  open:          "bg-blue-50   text-blue-700   border-blue-200",
  triaged:       "bg-purple-50 text-purple-700 border-purple-200",
  investigating: "bg-orange-50 text-orange-700 border-orange-200",
  escalated:     "bg-red-50    text-red-700    border-red-200",
  resolved:      "bg-green-50  text-green-700  border-green-200",
  closed:        "bg-slate-50  text-slate-600  border-slate-200",
};

export function StatusChip({ status }: { status: CaseStatus }) {
  return (
    <span
      className={clsx(
        "inline-flex items-center rounded border px-2 py-0.5 text-xs font-semibold uppercase tracking-wide",
        statusStyles[status]
      )}
    >
      {status}
    </span>
  );
}
