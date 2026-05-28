"use client";
import { useParams } from "next/navigation";
import { Spinner }              from "@/components/ui/Spinner";
import { EmptyState }           from "@/components/ui/EmptyState";
import { PageHeader }           from "@/components/layout/PageHeader";
import { CaseOverview }         from "@/components/workspace/CaseOverview";
import { EvidencePanel }        from "@/components/workspace/EvidencePanel";
import { EscalationControls }   from "@/components/workspace/EscalationControls";
import { RemediationPanel }     from "@/components/workspace/RemediationPanel";
import { useCaseDetail }        from "@/lib/hooks/useInvestigation";
import { Button }               from "@/components/ui/Button";
import Link                     from "next/link";

export default function CaseDetailPage() {
  const { id }             = useParams<{ id: string }>();
  const { data, isLoading, isError } = useCaseDetail(id);

  if (isLoading) {
    return (
      <div className="flex h-96 items-center justify-center">
        <Spinner size="lg" />
      </div>
    );
  }

  if (isError || !data) {
    return (
      <EmptyState
        title="Case not found"
        description="This investigation case could not be loaded."
        action={
          <Link href="/investigations">
            <Button variant="secondary">Back to Queue</Button>
          </Link>
        }
      />
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={data.case_number}
        description={`${data.entity_name} · ${data.violation_category}`}
        actions={
          <div className="flex gap-2">
            <Link href={`/investigations/${id}/timeline`}>
              <Button variant="secondary" size="sm">Timeline</Button>
            </Link>
            <Link href={`/investigations/${id}/traces`}>
              <Button variant="secondary" size="sm">Traces</Button>
            </Link>
            <Link href={`/investigations/${id}/evidence`}>
              <Button variant="secondary" size="sm">Evidence</Button>
            </Link>
          </div>
        }
      />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* Main column */}
        <div className="space-y-6 lg:col-span-2">
          <CaseOverview case_={data} />
          <EvidencePanel caseId={id} />
        </div>

        {/* Side column */}
        <div className="space-y-6">
          <EscalationControls case_={data} />
          <RemediationPanel case_={data} />
        </div>
      </div>
    </div>
  );
}
