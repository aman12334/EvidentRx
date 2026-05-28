import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { Spinner } from "@/components/ui/Spinner";
import { EmptyState } from "@/components/ui/EmptyState";
import { useWorkflowTrace } from "@/lib/hooks/useTraces";

interface TimelineViewProps {
  caseId: string;
}

export function TimelineView({ caseId }: TimelineViewProps) {
  const { data, isLoading, isError } = useWorkflowTrace(caseId);

  if (isLoading) {
    return (
      <Card padding="md">
        <div className="flex justify-center py-12">
          <Spinner size="lg" />
        </div>
      </Card>
    );
  }

  if (isError || !data) {
    return (
      <Card padding="md">
        <EmptyState title="Timeline unavailable" description="Could not load workflow trace data." />
      </Card>
    );
  }

  // Merge agent runs + reasoning traces into a unified timeline
  type TimelineEvent =
    | { kind: "agent";   ts: string; label: string; status: string; latency: number | null }
    | { kind: "trace";   ts: string; label: string; node: string; confidence: number | null; summary: string | null };

  const events: TimelineEvent[] = [
    ...data.agent_runs.map((r) => ({
      kind:    "agent" as const,
      ts:      r.started_at ?? "",
      label:   r.agent_type,
      status:  r.status,
      latency: r.latency_ms,
    })),
    ...data.reasoning_traces.map((t) => ({
      kind:       "trace" as const,
      ts:         t.created_at ?? "",
      label:      t.agent_type,
      node:       t.workflow_node,
      confidence: t.confidence_score,
      summary:    t.output_summary,
    })),
  ].sort((a, b) => a.ts.localeCompare(b.ts));

  return (
    <Card padding="md">
      <CardHeader>
        <CardTitle>Investigation Timeline</CardTitle>
      </CardHeader>

      {events.length === 0 ? (
        <div className="mt-4">
          <EmptyState title="No events" description="No agent runs or reasoning traces found for this case." />
        </div>
      ) : (
        <ol className="relative mt-4 ml-3 border-l border-slate-200 dark:border-slate-700">
          {events.map((e, i) => (
            <li key={i} className="mb-6 ml-5">
              {/* Dot */}
              <span
                className={`absolute -left-2.5 flex h-5 w-5 items-center justify-center rounded-full ring-4 ring-white dark:ring-slate-900 ${
                  e.kind === "agent" ? "bg-blue-500" : "bg-slate-400"
                }`}
              >
                {e.kind === "agent" ? (
                  <svg className="h-2.5 w-2.5 text-white" fill="currentColor" viewBox="0 0 20 20">
                    <path d="M10 9a3 3 0 100-6 3 3 0 000 6z" />
                    <path fillRule="evenodd" d="M10 11c-4.418 0-8 1.79-8 4v1h16v-1c0-2.21-3.582-4-8-4z" clipRule="evenodd" />
                  </svg>
                ) : (
                  <svg className="h-2.5 w-2.5 text-white" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-8-3a1 1 0 00-.867.5 1 1 0 11-1.731-1A3 3 0 0113 8a3.001 3.001 0 01-2 2.83V11a1 1 0 11-2 0v-1a1 1 0 011-1 1 1 0 100-2zm0 8a1 1 0 100-2 1 1 0 000 2z" clipRule="evenodd" />
                  </svg>
                )}
              </span>

              {/* Event content */}
              <div className="rounded border border-slate-100 dark:border-slate-800 bg-slate-50 dark:bg-slate-800/50 px-3 py-2">
                <div className="flex items-center justify-between gap-2 flex-wrap">
                  <p className="text-xs font-semibold text-slate-800 dark:text-slate-200">
                    {e.label}
                    {e.kind === "trace" && (
                      <span className="ml-1.5 font-mono text-slate-500">→ {e.node}</span>
                    )}
                  </p>
                  {e.ts && (
                    <time className="text-xs text-slate-400">
                      {new Date(e.ts).toLocaleString()}
                    </time>
                  )}
                </div>

                {e.kind === "agent" && (
                  <div className="mt-1 flex gap-3 text-xs text-slate-500">
                    <span
                      className={`font-semibold ${
                        e.status === "completed" ? "text-green-600"
                        : e.status === "failed"    ? "text-red-600"
                        : "text-slate-500"
                      }`}
                    >
                      {e.status}
                    </span>
                    {e.latency != null && <span>{(e.latency / 1000).toFixed(2)}s</span>}
                  </div>
                )}

                {e.kind === "trace" && (
                  <>
                    {e.confidence != null && (
                      <p className="mt-0.5 text-xs text-slate-500">
                        confidence: <span className="font-semibold text-slate-700 dark:text-slate-300">{(e.confidence * 100).toFixed(0)}%</span>
                      </p>
                    )}
                    {e.summary && (
                      <p className="mt-1 text-xs text-slate-600 dark:text-slate-400 line-clamp-2">
                        {e.summary}
                      </p>
                    )}
                  </>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}
