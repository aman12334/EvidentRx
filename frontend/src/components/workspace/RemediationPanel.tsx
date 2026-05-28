import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { SeverityBadge } from "@/components/investigation/SeverityBadge";
import type { InvestigationCaseDetail, Severity } from "@/lib/types/investigation";

interface RemediationPanelProps {
  case_: InvestigationCaseDetail;
}

interface RemediationStep {
  step:        number;
  action:      string;
  description: string;
  priority:    Severity;
  reference?:  string;
}

/**
 * Generates deterministic remediation guidance from case data.
 * These are rule-based recommendations derived from violation category
 * and finding counts — NOT AI-generated. Deterministic source of truth.
 */
function buildRemediationSteps(c: InvestigationCaseDetail): RemediationStep[] {
  const steps: RemediationStep[] = [];
  let n = 1;

  if (c.critical_findings > 0) {
    steps.push({
      step:        n++,
      action:      "Quarantine critical findings for immediate review",
      description: `${c.critical_findings} critical finding(s) require immediate analyst verification before any remediation proceeds. Do not allow self-certification.`,
      priority:    "critical",
      reference:   "340B Program HRSA Policy Release 2011-1",
    });
  }

  if (c.financial_exposure > 50_000) {
    steps.push({
      step:        n++,
      action:      "Initiate financial exposure review",
      description: `Estimated exposure of $${c.financial_exposure.toLocaleString()} exceeds threshold. Engage compliance officer and document repayment calculation methodology.`,
      priority:    "high",
      reference:   "340B Audit Guide §4.2 — Financial Impact Assessment",
    });
  }

  if (c.violation_category.toLowerCase().includes("diversion")) {
    steps.push({
      step:        n++,
      action:      "Validate patient eligibility records",
      description: "Diversion violations require retrospective patient eligibility confirmation. Pull encounter records for the implicated claim window.",
      priority:    "high",
      reference:   "340B ACE Program Integrity §2.3",
    });
  }

  if (c.violation_category.toLowerCase().includes("duplicate")) {
    steps.push({
      step:        n++,
      action:      "Cross-reference Medicaid exclusion file",
      description: "Duplicate discount violations require reconciliation against the active Medicaid exclusion list at time of dispense.",
      priority:    "high",
      reference:   "OPA Medicaid Exclusion File Requirements",
    });
  }

  if (c.high_findings > 0) {
    steps.push({
      step:        n++,
      action:      "Document audit trail for high-severity findings",
      description: `${c.high_findings} high-severity finding(s) must be individually documented in the covered entity's audit log with root-cause notes.`,
      priority:    "medium",
    });
  }

  if (c.ndc_list.length > 0) {
    steps.push({
      step:        n++,
      action:      "Review NDC procurement contracts",
      description: `Findings reference ${c.ndc_list.length} NDC(s). Verify manufacturer contract terms and confirm 340B ceiling price applicability for each.`,
      priority:    "medium",
    });
  }

  steps.push({
    step:        n++,
    action:      "Implement corrective action plan (CAP)",
    description: "Submit a written CAP to HRSA within 60 days of investigation close. Document process changes, staff training, and future monitoring controls.",
    priority:    "low",
    reference:   "HRSA 340B Program Termination Criteria §1.5",
  });

  return steps;
}

export function RemediationPanel({ case_: c }: RemediationPanelProps) {
  const steps = buildRemediationSteps(c);

  return (
    <Card padding="md">
      <CardHeader>
        <CardTitle>Remediation Guidance</CardTitle>
      </CardHeader>

      <div className="mt-1 mb-3">
        <span className="inline-flex items-center gap-1 rounded border border-amber-200 bg-amber-50 dark:bg-amber-950/20 dark:border-amber-800 px-2 py-0.5 text-xs text-amber-700 dark:text-amber-400">
          <svg className="h-3 w-3" fill="currentColor" viewBox="0 0 20 20">
            <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
          </svg>
          Deterministic guidance — rule-based, not AI-generated
        </span>
      </div>

      <ol className="flex flex-col gap-4">
        {steps.map((s) => (
          <li key={s.step} className="flex gap-3">
            <div className="shrink-0 flex h-6 w-6 items-center justify-center rounded-full bg-slate-100 dark:bg-slate-800 text-xs font-bold text-slate-600 dark:text-slate-400">
              {s.step}
            </div>
            <div className="flex-1">
              <div className="flex items-start justify-between gap-2">
                <p className="text-sm font-semibold text-slate-800 dark:text-slate-200">{s.action}</p>
                <SeverityBadge severity={s.priority as Severity} />
              </div>
              <p className="mt-1 text-xs text-slate-600 dark:text-slate-400">{s.description}</p>
              {s.reference && (
                <p className="mt-1 text-xs text-blue-600 dark:text-blue-400 italic">{s.reference}</p>
              )}
            </div>
          </li>
        ))}
      </ol>
    </Card>
  );
}
