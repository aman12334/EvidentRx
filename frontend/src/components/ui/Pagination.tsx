"use client";

interface PaginationProps {
  page:      number;
  limit:     number;
  total:     number;
  onPage:    (page: number) => void;
  className?: string;
}

export function Pagination({ page, limit, total, onPage, className = "" }: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / limit));
  const isFirst    = page <= 1;
  const isLast     = page >= totalPages;

  const from = total === 0 ? 0 : (page - 1) * limit + 1;
  const to   = Math.min(page * limit, total);

  // Build visible page numbers (window of ±2 around current)
  const pages: (number | "…")[] = [];
  if (totalPages <= 7) {
    for (let i = 1; i <= totalPages; i++) pages.push(i);
  } else {
    pages.push(1);
    if (page > 3)          pages.push("…");
    for (let i = Math.max(2, page - 1); i <= Math.min(totalPages - 1, page + 1); i++) {
      pages.push(i);
    }
    if (page < totalPages - 2) pages.push("…");
    pages.push(totalPages);
  }

  return (
    <div className={`flex flex-wrap items-center justify-between gap-3 ${className}`}>
      {/* Count summary */}
      <p className="text-xs text-slate-500 dark:text-slate-400">
        {total === 0
          ? "No results"
          : `${from}–${to} of ${total.toLocaleString()}`}
      </p>

      {/* Page buttons */}
      <nav className="flex items-center gap-1" aria-label="Pagination">
        <PageBtn
          label="‹ Prev"
          disabled={isFirst}
          onClick={() => onPage(page - 1)}
        />

        {pages.map((p, i) =>
          p === "…" ? (
            <span key={`ellipsis-${i}`} className="px-1 text-xs text-slate-400">
              …
            </span>
          ) : (
            <PageBtn
              key={p}
              label={String(p)}
              active={p === page}
              onClick={() => onPage(p as number)}
            />
          )
        )}

        <PageBtn
          label="Next ›"
          disabled={isLast}
          onClick={() => onPage(page + 1)}
        />
      </nav>
    </div>
  );
}

interface PageBtnProps {
  label:    string;
  active?:  boolean;
  disabled?: boolean;
  onClick:  () => void;
}

function PageBtn({ label, active, disabled, onClick }: PageBtnProps) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={`
        min-w-[2rem] rounded px-2 py-1 text-xs font-medium transition-colors
        ${active
          ? "bg-blue-600 text-white shadow-sm"
          : disabled
            ? "cursor-not-allowed text-slate-300 dark:text-slate-600"
            : "text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800 hover:text-slate-900 dark:hover:text-slate-100"
        }
      `}
    >
      {label}
    </button>
  );
}
