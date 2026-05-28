"use client";
/**
 * /entities — Covered Entity Directory
 *
 * Lists all active 340B covered entities with search, state filter,
 * and entity type filter. Links to per-entity investigation queue.
 */
import { useState } from "react";
import { PageHeader }        from "@/components/layout/PageHeader";
import { Card }              from "@/components/ui/Card";
import { Spinner }           from "@/components/ui/Spinner";
import { useEntityList }     from "@/lib/hooks/useEntities";

const ENTITY_TYPES = [
  { value: "",    label: "All Types" },
  { value: "DSH", label: "DSH — Disproportionate Share Hospital" },
  { value: "CHC", label: "CHC — Community Health Center (FQHC)" },
  { value: "CAH", label: "CAH — Critical Access Hospital" },
  { value: "PED", label: "PED — Children's Hospital" },
  { value: "CAN", label: "CAN — Cancer Hospital" },
  { value: "RRC", label: "RRC — Rural Referral Center" },
];

const US_STATES = [
  "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA",
  "HI","ID","IL","IN","IA","KS","KY","LA","ME","MD",
  "MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
  "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC",
  "SD","TN","TX","UT","VT","VA","WA","WV","WI","WY",
];

export default function EntitiesPage() {
  const [search,     setSearch]     = useState("");
  const [stateCode,  setStateCode]  = useState("");
  const [entityType, setEntityType] = useState("");
  const [page,       setPage]       = useState(1);

  const { entities, total, loading, error } = useEntityList({
    search:      search || undefined,
    state_code:  stateCode || undefined,
    entity_type: entityType || undefined,
    page,
    limit: 25,
  });

  const totalPages = Math.ceil(total / 25);

  return (
    <div>
      <PageHeader
        title="Covered Entity Directory"
        description={`${total.toLocaleString()} active 340B covered entities`}
      />

      {/* Filters */}
      <div className="flex flex-wrap gap-3 mb-5">
        <input
          type="text"
          placeholder="Search by name or HRSA ID…"
          value={search}
          onChange={e => { setSearch(e.target.value); setPage(1); }}
          className="rounded-lg border border-slate-300 dark:border-slate-600 px-3 py-2 text-sm
                     bg-white dark:bg-slate-800 text-slate-800 dark:text-slate-200
                     focus:outline-none focus:ring-2 focus:ring-indigo-500 w-64"
        />
        <select
          value={stateCode}
          onChange={e => { setStateCode(e.target.value); setPage(1); }}
          className="rounded-lg border border-slate-300 dark:border-slate-600 px-3 py-2 text-sm
                     bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-300"
        >
          <option value="">All States</option>
          {US_STATES.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
        <select
          value={entityType}
          onChange={e => { setEntityType(e.target.value); setPage(1); }}
          className="rounded-lg border border-slate-300 dark:border-slate-600 px-3 py-2 text-sm
                     bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-300"
        >
          {ENTITY_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
        </select>
      </div>

      {/* Table */}
      {loading ? (
        <div className="flex justify-center py-16"><Spinner size="lg" /></div>
      ) : error ? (
        <div className="text-center py-12 text-red-500 text-sm">{error}</div>
      ) : (
        <Card padding="none">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-slate-200 dark:border-slate-700">
                <tr>
                  {["Entity Name", "HRSA ID", "Type", "Location", "Program", "Status"].map(h => (
                    <th key={h} className="text-left py-3 px-4 text-xs font-semibold text-slate-500 uppercase tracking-wide">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {entities.map(ce => (
                  <tr key={ce.ce_id}
                    className="border-b border-slate-100 dark:border-slate-800 hover:bg-slate-50 dark:hover:bg-slate-800/40 cursor-pointer"
                    onClick={() => window.location.href = `/investigations?ce=${ce.ce_id}`}
                  >
                    <td className="py-3 px-4">
                      <p className="font-medium text-slate-900 dark:text-white">{ce.entity_name}</p>
                      <p className="text-xs text-slate-400">{ce.npi ? `NPI: ${ce.npi}` : ""}</p>
                    </td>
                    <td className="py-3 px-4 font-mono text-xs text-slate-500">{ce.hrsa_id}</td>
                    <td className="py-3 px-4">
                      <span className="px-1.5 py-0.5 rounded text-xs bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 font-mono">
                        {ce.entity_type_code || "—"}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-slate-600 dark:text-slate-400 text-xs">
                      {[ce.city, ce.state_code].filter(Boolean).join(", ") || "—"}
                    </td>
                    <td className="py-3 px-4 text-xs text-slate-500">{ce.primary_340b_program || "—"}</td>
                    <td className="py-3 px-4">
                      <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${
                        ce.program_status === "Active"
                          ? "bg-green-100 text-green-700"
                          : "bg-slate-100 text-slate-600"
                      }`}>
                        {ce.program_status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between px-4 py-3 border-t border-slate-200 dark:border-slate-700">
              <p className="text-xs text-slate-400">
                Showing {(page - 1) * 25 + 1}–{Math.min(page * 25, total)} of {total.toLocaleString()}
              </p>
              <div className="flex gap-2">
                <button
                  disabled={page === 1}
                  onClick={() => setPage(p => p - 1)}
                  className="px-3 py-1.5 text-xs rounded border border-slate-300 disabled:opacity-40"
                >
                  ← Prev
                </button>
                <button
                  disabled={page >= totalPages}
                  onClick={() => setPage(p => p + 1)}
                  className="px-3 py-1.5 text-xs rounded border border-slate-300 disabled:opacity-40"
                >
                  Next →
                </button>
              </div>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
