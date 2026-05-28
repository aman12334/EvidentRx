"use client";
import { useState } from "react";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { SeverityBadge } from "@/components/investigation/SeverityBadge";
import { FindingRow } from "@/components/investigation/FindingRow";
import { Spinner } from "@/components/ui/Spinner";
import { EmptyState } from "@/components/ui/EmptyState";
import { useCaseFindings, useFindingsByRule } from "@/lib/hooks/useFindings";
import type { Severity } from "@/lib/types/investigation";
import type { Finding } from "@/lib/types/finding";

interface EvidencePanelProps {
  caseId: string;
}

export function EvidencePanel({ caseId }: EvidencePanelProps) {
  const [selectedFinding, setSelectedFinding] = useState<Finding | null>(null);
  const [view, setView] = useState<"list" | "by-rule">("list");

  const {
    data: findings,
    isLoading: loadingFindings,
    isError: errorFindings,
  } = useCaseFindings(caseId);

  const {
    data: byRule,
    isLoading: loadingByRule,
  } = useFindingsByRule(caseId);

  return (
    <div className="flex flex-col gap-4">
      {/* View toggle */}
      <div className="flex gap-2">
        <button
          onClick={() => setView("list")}
          className={`text-xs font-semibold px-3 py-1.5 rounded border transition-colors ${
            view === "list"
              ? "bg-blue-600 text-white border-blue-600"
              : "bg-white dark:bg-slate-900 text-slate-600 border-slate-200 dark:border-slate-700 hover:border-slate-400"
          }`}
        >
          All Findings
        </button>
        <button
          onClick={() => setView("by-rule")}
          className={`text-xs font-semibold px-3 py-1.5 rounded border transition-colors ${
            view === "by-rule"
              ? "bg-blue-600 text-white border-blue-600"
              : "bg-white dark:bg-slate-900 text-slate-600 border-slate-200 dark:border-slate-700 hover:border-slate-400"
          }`}
        >
          By Rule
        </button>
      </div>

      {view === "list" ? (
        <Card padding="none">
          <CardHeader>
            <CardTitle className="px-4 pt-3">Findings</CardTitle>
          </CardHeader>

          {loadingFindings ? (
            <div className="flex justify-center py-10">
              <Spinner />
            </div>
          ) : errorFindings ? (
            <div className="p-4">
              <EmptyState title="Failed to load findings" description="API error." />
            </div>
          ) : !findings || findings.items.length === 0 ? (
            <div className="p-4">
              <EmptyState title="No findings" description="This case has no attached findings." />
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-100 dark:border-slate-800">
                    <th className="py-2 pl-4 pr-2 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide">Severity</th>
                    <th className="py-2 px-2 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide">Rule</th>
                    <th className="py-2 px-2 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide">Code</th>
                    <th className="py-2 px-2 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide">Entity</th>
                    <th className="py-2 pl-2 pr-4 text-right text-xs font-semibold text-slate-500 uppercase tracking-wide">Date</th>
                  </tr>
                </thead>
                <tbody>
                  {findings.items.map((f) => (
                    <FindingRow
                      key={f.finding_id}
                      finding={f}
                      onClick={setSelectedFinding}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      ) : (
        <Card padding="md">
          <CardHeader>
            <CardTitle>Findings by Rule</CardTitle>
          </CardHeader>

          {loadingByRule ? (
            <div className="flex justify-center py-10">
              <Spinner />
            </div>
          ) : !byRule || byRule.length === 0 ? (
            <EmptyState title="No rule breakdown" description="No aggregated rule data available." />
          ) : (
            <div className="mt-3 flex flex-col gap-2">
              {byRule.map((r) => (
                <div
                  key={r.rule_code}
                  className="flex items-center justify-between rounded border border-slate-100 dark:border-slate-800 px-3 py-2"
                >
                  <div>
                    <span className="font-mono text-xs text-slate-800 dark:text-slate-200">{r.rule_code}</span>
                    <div className="mt-0.5 flex gap-1.5">
                      {r.critical > 0 && <SeverityBadge severity={"critical" as Severity} />}
                      {r.high     > 0 && <SeverityBadge severity={"high"     as Severity} />}
                      {r.medium   > 0 && <SeverityBadge severity={"medium"   as Severity} />}
                      {r.low      > 0 && <SeverityBadge severity={"low"      as Severity} />}
                    </div>
                  </div>
                  <div className="text-right">
                    <p className="text-sm font-bold text-slate-900 dark:text-white">{r.count}</p>
                    {r.exposure > 0 && (
                      <p className="text-xs text-slate-500">${r.exposure.toLocaleString()}</p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
      )}

      {/* Finding detail drawer */}
      {selectedFinding && (
        <FindingDetailDrawer
          finding={selectedFinding}
          onClose={() => setSelectedFinding(null)}
        />
      )}
    </div>
  );
}

interface FindingDetailDrawerProps {
  finding: Finding;
  onClose: () => void;
}

function FindingDetailDrawer({ finding, onClose }: FindingDetailDrawerProps) {
  return (
    <div className="fixed inset-0 z-40 flex justify-end" onClick={onClose}>
      <div
        className="relative w-full max-w-md bg-white dark:bg-slate-900 shadow-2xl p-6 overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-slate-400 hover:text-slate-600 text-lg"
        >
          ✕
        </button>

        <p className="text-xs font-semibold uppercase tracking-wide text-slate-400 mb-1">Finding Detail</p>
        <p className="font-mono text-sm text-slate-800 dark:text-slate-200">{finding.finding_code}</p>

        <div className="mt-4 flex gap-2 flex-wrap">
          <SeverityBadge severity={finding.severity} />
          <span className="font-mono text-xs text-slate-500 bg-slate-100 dark:bg-slate-800 px-2 py-0.5 rounded">
            {finding.rule_code}
          </span>
        </div>

        <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
          <div>
            <p className="text-xs text-slate-400">Entity</p>
            <p className="font-medium text-slate-800 dark:text-slate-200">
              {finding.entity_name ?? finding.covered_entity_id}
            </p>
          </div>
          {finding.created_at && (
            <div>
              <p className="text-xs text-slate-400">Detected</p>
              <p className="font-medium text-slate-800 dark:text-slate-200">
                {new Date(finding.created_at).toLocaleDateString()}
              </p>
            </div>
          )}
        </div>

        {Object.keys(finding.evidence_payload).length > 0 && (
          <div className="mt-4">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-400 mb-2">
              Evidence Payload
            </p>
            <pre className="text-xs text-slate-700 dark:text-slate-300 bg-slate-50 dark:bg-slate-800 rounded p-3 overflow-x-auto">
              {JSON.stringify(finding.evidence_payload, null, 2)}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}
