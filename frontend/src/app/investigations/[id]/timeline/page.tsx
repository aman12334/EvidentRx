"use client";
import { useParams } from "next/navigation";
import Link from "next/link";
import { PageHeader }   from "@/components/layout/PageHeader";
import { TimelineView } from "@/components/workspace/TimelineView";
import { Button }       from "@/components/ui/Button";

export default function TimelinePage() {
  const { id } = useParams<{ id: string }>();

  return (
    <div className="space-y-6">
      <PageHeader
        title="Investigation Timeline"
        description="Chronological audit trail of all agent activity and reasoning steps."
        actions={
          <Link href={`/investigations/${id}`}>
            <Button variant="secondary" size="sm">← Back to Case</Button>
          </Link>
        }
      />
      <TimelineView caseId={id} />
    </div>
  );
}
