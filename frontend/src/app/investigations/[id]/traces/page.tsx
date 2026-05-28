"use client";
import { useParams } from "next/navigation";
import Link from "next/link";
import { PageHeader }  from "@/components/layout/PageHeader";
import { TraceViewer } from "@/components/workspace/TraceViewer";
import { Button }      from "@/components/ui/Button";

export default function TracesPage() {
  const { id } = useParams<{ id: string }>();

  return (
    <div className="space-y-6">
      <PageHeader
        title="Reasoning Traces"
        description="Full agent reasoning chain, confidence propagation, and token usage for this case."
        actions={
          <Link href={`/investigations/${id}`}>
            <Button variant="secondary" size="sm">← Back to Case</Button>
          </Link>
        }
      />
      <TraceViewer caseId={id} />
    </div>
  );
}
