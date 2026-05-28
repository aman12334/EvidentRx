"use client";
import { useState } from "react";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { useUpdateCaseStatus } from "@/lib/hooks/useInvestigation";
import type { InvestigationCaseDetail } from "@/lib/types/investigation";

interface EscalationControlsProps {
  case_: InvestigationCaseDetail;
}

export function EscalationControls({ case_: c }: EscalationControlsProps) {
  const { mutate: updateStatus, isPending } = useUpdateCaseStatus(c.case_id);
  const [notes, setNotes] = useState("");
  const [submitted, setSubmitted] = useState(false);

  const canEscalate   = !["escalated", "resolved", "closed"].includes(c.status);
  const canResolve    = ["escalated", "investigating"].includes(c.status);
  const canClose      = c.status === "resolved";

  function handleAction(status: "escalated" | "resolved" | "closed") {
    updateStatus(
      { status, resolution_notes: notes.trim() || undefined },
      {
        onSuccess: () => {
          setSubmitted(true);
          setNotes("");
        },
      }
    );
  }

  if (submitted) {
    return (
      <Card padding="md">
        <div className="py-4 text-center text-sm text-green-700 dark:text-green-400">
          ✓ Status updated successfully
        </div>
      </Card>
    );
  }

  return (
    <Card padding="md">
      <CardHeader>
        <CardTitle>Case Actions</CardTitle>
      </CardHeader>

      <div className="mt-4 space-y-4">
        {/* Notes field */}
        <div>
          <label
            htmlFor="escalation-notes"
            className="block text-xs font-semibold uppercase tracking-wide text-slate-500 mb-1"
          >
            Notes (optional)
          </label>
          <textarea
            id="escalation-notes"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={3}
            placeholder="Reason for status change, evidence reference, or resolution summary…"
            className="w-full rounded border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2 text-sm text-slate-800 dark:text-slate-200 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>

        {/* Action buttons */}
        <div className="flex flex-col gap-2">
          {canEscalate && (
            <div className="flex items-start gap-3 rounded-md border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950/20 p-3">
              <div className="flex-1">
                <p className="text-sm font-semibold text-red-800 dark:text-red-300">Escalate Case</p>
                <p className="text-xs text-red-600 dark:text-red-400 mt-0.5">
                  Flag for immediate senior analyst review. Triggers priority queue placement.
                </p>
              </div>
              <Button
                variant="destructive"
                size="sm"
                loading={isPending}
                onClick={() => handleAction("escalated")}
              >
                Escalate
              </Button>
            </div>
          )}

          {canResolve && (
            <div className="flex items-start gap-3 rounded-md border border-green-200 dark:border-green-900 bg-green-50 dark:bg-green-950/20 p-3">
              <div className="flex-1">
                <p className="text-sm font-semibold text-green-800 dark:text-green-300">Resolve Case</p>
                <p className="text-xs text-green-600 dark:text-green-400 mt-0.5">
                  Mark investigation complete. All findings remain in audit record.
                </p>
              </div>
              <Button
                variant="secondary"
                size="sm"
                loading={isPending}
                onClick={() => handleAction("resolved")}
              >
                Resolve
              </Button>
            </div>
          )}

          {canClose && (
            <div className="flex items-start gap-3 rounded-md border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800/30 p-3">
              <div className="flex-1">
                <p className="text-sm font-semibold text-slate-700 dark:text-slate-300">Close Case</p>
                <p className="text-xs text-slate-500 mt-0.5">
                  Archive this case. No further workflow actions will be taken.
                </p>
              </div>
              <Button
                variant="ghost"
                size="sm"
                loading={isPending}
                onClick={() => handleAction("closed")}
              >
                Close
              </Button>
            </div>
          )}

          {!canEscalate && !canResolve && !canClose && (
            <p className="text-sm text-slate-500 text-center py-2">
              No actions available for status <strong>{c.status}</strong>.
            </p>
          )}
        </div>
      </div>
    </Card>
  );
}
