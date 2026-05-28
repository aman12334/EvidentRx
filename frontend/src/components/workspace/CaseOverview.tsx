"use client";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { StatusChip } from "@/components/investigation/StatusChip";
import { SeverityBadge } from "@/components/investigation/SeverityBadge";
import { Button } from "@/components/ui/Button";
import { useUpdateCaseStatus } from "@/lib/hooks/useInvestigation";
import type { InvestigationCaseDetail, CaseStatus, Severity } from "@/lib/types/investigation";

const STATUS_TRANSITIONS: Record<CaseStatus, CaseStatus[]> = {
  open:          ["triaged", "escalated"],
  triaged:       ["investigating", "escalated"],
  investigating: ["escalated", "resolved"],
  escalated:     ["investigating", "resolved"],
  resolved:      ["closed"],
  closed:        [],
};

interface CaseOverviewProps {
  case_: InvestigationCaseDetail;
}

export function CaseOverview({ case_: c }: CaseOverviewProps) {
  const { mutate: updateStatus, isPending } = useUpdateCaseStatus(c.case_id);
  const transitions = STATUS_TRANSITIONS[c.status] ?? [];

  return (
    <Card padding="md">
      <CardHeader>
        <CardTitle>Case Overview</CardTitle>
      </CardHeader>

      <div className="mt-4 grid grid-cols-2 gap-x-8 gap-y-4 sm:grid-cols-3">
        <Field label="Case Number"   value={c.case_number} mono />
        <Field label="Status">
          <StatusChip status={c.status} />
        </Field>
        <Field label="Priority">
          <SeverityBadge severity={c.priority as Severity} />
        </Field>
        <Field label="Entity"        value={c.entity_name} />
        <Field label="Category"      value={c.violation_category} />
        <Field label="Assigned To"   value={c.assigned_to ?? "Unassigned"} />
        <Field label="Total Findings" value={c.total_findings.toString()} />
        <Field label="Critical"      value={c.critical_findings.toString()} />
        <Field label="Exposure"      value={`$${c.financial_exposure.toLocaleString()}`} />
        {c.composite_score != null && (
          <Field label="Risk Score"  value={c.composite_score.toFixed(4)} mono />
        )}
        {c.opened_at && (
          <Field label="Opened"      value={new Date(c.opened_at).toLocaleDateString()} />
        )}
        {c.unique_patients > 0 && (
          <Field label="Patients"    value={c.unique_patients.toString()} />
        )}
      </div>

      {/* Resolution notes */}
      {c.resolution_notes && (
        <div className="mt-4 rounded-md bg-slate-50 dark:bg-slate-800 p-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-1">
            Resolution Notes
          </p>
          <p className="text-sm text-slate-700 dark:text-slate-300">{c.resolution_notes}</p>
        </div>
      )}

      {/* Status transition actions */}
      {transitions.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-2">
          <p className="w-full text-xs text-slate-500 font-semibold uppercase tracking-wide">
            Move to
          </p>
          {transitions.map((next) => (
            <Button
              key={next}
              variant={next === "escalated" ? "destructive" : "secondary"}
              size="sm"
              loading={isPending}
              onClick={() => updateStatus({ status: next })}
            >
              {next.charAt(0).toUpperCase() + next.slice(1)}
            </Button>
          ))}
        </div>
      )}
    </Card>
  );
}

interface FieldProps {
  label:    string;
  value?:   string;
  mono?:    boolean;
  children?: React.ReactNode;
}

function Field({ label, value, mono, children }: FieldProps) {
  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">{label}</p>
      {children ? (
        <div className="mt-0.5">{children}</div>
      ) : (
        <p className={`mt-0.5 text-sm text-slate-800 dark:text-slate-200 ${mono ? "font-mono" : ""}`}>
          {value ?? "—"}
        </p>
      )}
    </div>
  );
}
