"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { clsx } from "clsx";
import { useUIStore } from "@/lib/store/uiStore";

interface NavItem {
  href:  string;
  label: string;
  icon:  React.ReactNode;
  exact?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  {
    href:  "/overview",
    label: "Overview",
    icon: (
      <svg className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
        <rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" />
        <rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" />
      </svg>
    ),
  },
  {
    href:  "/investigations",
    label: "Investigations",
    icon: (
      <svg className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
      </svg>
    ),
  },
  {
    href:  "/intelligence",
    label: "Intelligence",
    icon: (
      <svg className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
      </svg>
    ),
  },
  {
    href:  "/graph",
    label: "Compliance Graph",
    icon: (
      <svg className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
        <circle cx="12" cy="12" r="2" /><circle cx="4" cy="6" r="2" /><circle cx="20" cy="6" r="2" />
        <circle cx="4" cy="18" r="2" /><circle cx="20" cy="18" r="2" />
        <line x1="6" y1="7" x2="10" y2="11" /><line x1="18" y1="7" x2="14" y2="11" />
        <line x1="6" y1="17" x2="10" y2="13" /><line x1="18" y1="17" x2="14" y2="13" />
      </svg>
    ),
  },
];

const INTELLIGENCE_SUB: NavItem[] = [
  { href: "/intelligence/correlations", label: "Correlations", icon: null },
  { href: "/intelligence/risk",         label: "Risk Scores",  icon: null },
];

export function Sidebar() {
  const pathname         = usePathname();
  const { sidebarCollapsed, toggleSidebar, darkMode, toggleDarkMode } = useUIStore();

  function isActive(item: NavItem): boolean {
    return item.exact ? pathname === item.href : pathname.startsWith(item.href);
  }

  return (
    <aside
      className={clsx(
        "flex h-full flex-col border-r border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-950 transition-all duration-200",
        sidebarCollapsed ? "w-14" : "w-56"
      )}
    >
      {/* Logo / Wordmark */}
      <div className="flex h-14 items-center gap-2 border-b border-slate-100 dark:border-slate-800 px-3">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-blue-600 text-white text-xs font-bold">
          Rx
        </div>
        {!sidebarCollapsed && (
          <div className="min-w-0">
            <p className="text-sm font-bold text-slate-900 dark:text-white leading-none">EvidentRx</p>
            <p className="text-xs text-slate-400 leading-none mt-0.5">340B Audit Platform</p>
          </div>
        )}
      </div>

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto py-3 px-1.5">
        {NAV_ITEMS.map((item) => {
          const active = isActive(item);
          return (
            <div key={item.href}>
              <Link
                href={item.href}
                className={clsx(
                  "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm font-medium transition-colors",
                  active
                    ? "bg-blue-50 dark:bg-blue-950/40 text-blue-700 dark:text-blue-400"
                    : "text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800 hover:text-slate-900 dark:hover:text-white"
                )}
                title={sidebarCollapsed ? item.label : undefined}
              >
                <span className="shrink-0">{item.icon}</span>
                {!sidebarCollapsed && <span className="truncate">{item.label}</span>}
              </Link>

              {/* Intelligence sub-nav */}
              {!sidebarCollapsed && item.href === "/intelligence" && active && (
                <div className="ml-7 mt-1 flex flex-col gap-0.5">
                  {INTELLIGENCE_SUB.map((sub) => (
                    <Link
                      key={sub.href}
                      href={sub.href}
                      className={clsx(
                        "rounded px-2 py-1 text-xs font-medium transition-colors",
                        pathname === sub.href
                          ? "text-blue-700 dark:text-blue-400"
                          : "text-slate-500 hover:text-slate-800 dark:hover:text-slate-300"
                      )}
                    >
                      {sub.label}
                    </Link>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </nav>

      {/* Bottom controls */}
      <div className="border-t border-slate-100 dark:border-slate-800 p-2 flex flex-col gap-1">
        {/* Dark mode */}
        <button
          onClick={toggleDarkMode}
          className="flex items-center gap-2.5 rounded-md px-2.5 py-2 text-xs text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
          title={darkMode ? "Light mode" : "Dark mode"}
        >
          <span className="text-base">{darkMode ? "☀️" : "🌙"}</span>
          {!sidebarCollapsed && <span>{darkMode ? "Light Mode" : "Dark Mode"}</span>}
        </button>

        {/* Collapse toggle */}
        <button
          onClick={toggleSidebar}
          className="flex items-center gap-2.5 rounded-md px-2.5 py-2 text-xs text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
          title={sidebarCollapsed ? "Expand" : "Collapse"}
        >
          <svg
            className={`h-4 w-4 transition-transform ${sidebarCollapsed ? "rotate-180" : ""}`}
            fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24"
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M11 19l-7-7 7-7M18 19l-7-7 7-7" />
          </svg>
          {!sidebarCollapsed && <span>Collapse</span>}
        </button>
      </div>
    </aside>
  );
}
