"use client";
import { useParams } from "next/navigation";
import Link from "next/link";
import { PageHeader }   from "@/components/layout/PageHeader";
import { EvidencePanel } from "@/components/workspace/EvidencePanel";
import { Button }        from "@/components/ui/Button";

export default function EvidencePage() {
  const { id } = useParams<{ id: string }>();

  return (
    <div className="space-y-6">
      <PageHeader
        title="Evidence"
        description="All findings and evidence attached to this investigation case."
        actions={
          <Link href={`/investigations/${id}`}>
            <Button variant="secondary" size="sm">← Back to Case</Button>
          </Link>
        }
      />
      <EvidencePanel caseId={id} />
    </div>
  );
}
