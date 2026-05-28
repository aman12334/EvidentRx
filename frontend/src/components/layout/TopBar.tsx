"use client";
import { usePathname } from "next/navigation";
import { useUIStore } from "@/lib/store/uiStore";

const ROUTE_LABELS: Record<string, string> = {
  "/":                          "Dashboard",
  "/investigations":            "Investigations",
  "/intelligence":              "Intelligence Summary",
  "/intelligence/correlations": "Cross-Case Correlations",
  "/intelligence/risk":         "Risk Scores",
  "/graph":                     "Compliance Graph",
};

function resolveLabel(pathname: string): string {
  if (ROUTE_LABELS[pathname]) return ROUTE_LABELS[pathname];
  if (pathname.startsWith("/investigations/") && pathname.endsWith("/evidence"))  return "Evidence";
  if (pathname.startsWith("/investigations/") && pathname.endsWith("/timeline"))  return "Timeline";
  if (pathname.startsWith("/investigations/") && pathname.endsWith("/traces"))    return "Traces";
  if (pathname.startsWith("/investigations/"))                                    return "Case Detail";
  return "EvidentRx";
}

export function TopBar() {
  const pathname  = usePathname();
  const { darkMode } = useUIStore();
  const pageLabel = resolveLabel(pathname);

  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-950 px-5">
      {/* Page title */}
      <div>
        <h1 className="text-sm font-bold text-slate-900 dark:text-white">{pageLabel}</h1>
        <p className="text-xs text-slate-400">{new Date().toLocaleDateString("en-US", { weekday: "long", year: "numeric", month: "long", day: "numeric" })}</p>
      </div>

      {/* Right side */}
      <div className="flex items-center gap-3">
        {/* Platform badge */}
        <span className="hidden sm:inline-flex items-center rounded-full border border-blue-200 dark:border-blue-800 bg-blue-50 dark:bg-blue-950/40 px-2.5 py-0.5 text-xs font-semibold text-blue-700 dark:text-blue-400">
          340B Compliance
        </span>

        {/* Environment badge */}
        <span className="rounded border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 px-2 py-0.5 text-xs font-semibold text-amber-700 dark:text-amber-400">
          AUDIT MODE
        </span>

        {/* User avatar */}
        <div className="flex h-7 w-7 items-center justify-center rounded-full bg-slate-200 dark:bg-slate-700 text-xs font-bold text-slate-700 dark:text-slate-300">
          A
        </div>
      </div>
    </header>
  );
}
