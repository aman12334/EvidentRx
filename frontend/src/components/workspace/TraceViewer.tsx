"use client";
import { useState } from "react";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { Spinner } from "@/components/ui/Spinner";
import { EmptyState } from "@/components/ui/EmptyState";
import { useWorkflowTrace } from "@/lib/hooks/useTraces";
import type { ReasoningTrace, AgentRun } from "@/lib/types/trace";

interface TraceViewerProps {
  caseId: string;
}

export function TraceViewer({ caseId }: TraceViewerProps) {
  const { data, isLoading, isError } = useWorkflowTrace(caseId);
  const [activeTab, setActiveTab] = useState<"traces" | "runs" | "confidence">("traces");

  if (isLoading) {
    return (
      <Card padding="md">
        <div className="flex justify-center py-12"><Spinner size="lg" /></div>
      </Card>
    );
  }

  if (isError || !data) {
    return (
      <Card padding="md">
        <EmptyState title="Trace data unavailable" description="Could not load reasoning traces." />
      </Card>
    );
  }

  return (
    <Card padding="md">
      <CardHeader>
        <CardTitle>
          <span className="flex items-center gap-3">
            Reasoning Traces
            {data.escalation_recommended != null && (
              <span
                className={`text-xs font-semibold px-2 py-0.5 rounded border ${
                  data.escalation_recommended
                    ? "bg-red-50 text-red-700 border-red-200"
                    : "bg-green-50 text-green-700 border-green-200"
                }`}
              >
                {data.escalation_recommended ? "Escalation Recommended" : "No Escalation"}
              </span>
            )}
          </span>
        </CardTitle>
      </CardHeader>

      {/* Token usage summary */}
      <div className="mt-3 flex gap-4 text-xs text-slate-500">
        <span>Input tokens: <strong className="text-slate-700 dark:text-slate-300">{data.total_input_tokens.toLocaleString()}</strong></span>
        <span>Output tokens: <strong className="text-slate-700 dark:text-slate-300">{data.total_output_tokens.toLocaleString()}</strong></span>
        <span>Traces: <strong className="text-slate-700 dark:text-slate-300">{data.total_traces}</strong></span>
      </div>

      {/* Executive summary */}
      {data.executive_summary && (
        <div className="mt-3 rounded bg-blue-50 dark:bg-blue-950/30 border border-blue-200 dark:border-blue-800 p-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-blue-600 dark:text-blue-400 mb-1">
            Executive Summary
          </p>
          <p className="text-sm text-blue-900 dark:text-blue-100">{data.executive_summary}</p>
        </div>
      )}

      {/* Tabs */}
      <div className="mt-4 flex gap-1 border-b border-slate-200 dark:border-slate-700">
        {(["traces", "runs", "confidence"] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-3 py-2 text-xs font-semibold uppercase tracking-wide transition-colors ${
              activeTab === tab
                ? "border-b-2 border-blue-600 text-blue-600"
                : "text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"
            }`}
          >
            {tab === "traces" ? "Reasoning" : tab === "runs" ? "Agent Runs" : "Confidence Chain"}
          </button>
        ))}
      </div>

      <div className="mt-4">
        {activeTab === "traces" && (
          <ReasoningTraceList traces={data.reasoning_traces} />
        )}
        {activeTab === "runs" && (
          <AgentRunList runs={data.agent_runs} />
        )}
        {activeTab === "confidence" && (
          <ConfidenceChain chain={data.confidence_chain} />
        )}
      </div>
    </Card>
  );
}

function ReasoningTraceList({ traces }: { traces: ReasoningTrace[] }) {
  if (traces.length === 0) {
    return <EmptyState title="No traces" description="No reasoning traces recorded for this case." />;
  }

  return (
    <div className="flex flex-col gap-3">
      {traces.map((t) => (
        <div
          key={t.trace_id}
          className="rounded border border-slate-100 dark:border-slate-800 p-3"
        >
          <div className="flex items-start justify-between gap-2">
            <div>
              <span className="text-xs font-semibold text-slate-700 dark:text-slate-300">{t.agent_type}</span>
              <span className="mx-1.5 text-slate-300">·</span>
              <span className="font-mono text-xs text-slate-500">{t.workflow_node}</span>
              <span className="ml-1 text-xs text-slate-400">step {t.workflow_step}</span>
            </div>
            {t.confidence_score != null && (
              <ConfidencePill value={t.confidence_score} />
            )}
          </div>
          {t.output_summary && (
            <p className="mt-2 text-sm text-slate-700 dark:text-slate-300">{t.output_summary}</p>
          )}
          {t.created_at && (
            <p className="mt-1 text-xs text-slate-400">
              {new Date(t.created_at).toLocaleString()}
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

function AgentRunList({ runs }: { runs: AgentRun[] }) {
  if (runs.length === 0) {
    return <EmptyState title="No agent runs" description="No agent runs recorded for this case." />;
  }

  return (
    <div className="flex flex-col gap-2">
      {runs.map((r) => (
        <div
          key={r.run_id}
          className="flex items-center justify-between gap-4 rounded border border-slate-100 dark:border-slate-800 px-3 py-2.5"
        >
          <div>
            <p className="text-xs font-semibold text-slate-800 dark:text-slate-200">{r.agent_type}</p>
            <p className="text-xs text-slate-500">
              {r.input_tokens.toLocaleString()} in · {r.output_tokens.toLocaleString()} out
              {r.cache_read_tokens > 0 && ` · ${r.cache_read_tokens.toLocaleString()} cached`}
            </p>
          </div>
          <div className="text-right shrink-0">
            <span
              className={`text-xs font-semibold ${
                r.status === "completed" ? "text-green-600" : r.status === "failed" ? "text-red-600" : "text-slate-500"
              }`}
            >
              {r.status}
            </span>
            {r.latency_ms != null && (
              <p className="text-xs text-slate-400">{(r.latency_ms / 1000).toFixed(2)}s</p>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function ConfidenceChain({ chain }: { chain: import("@/lib/types/trace").ConfidencePropagation[] }) {
  if (chain.length === 0) {
    return <EmptyState title="No confidence data" description="No confidence propagation chain available." />;
  }

  return (
    <div className="flex flex-col gap-2">
      {chain.map((node, i) => (
        <div key={i} className="flex items-center gap-3">
          <div className="w-28 shrink-0">
            <p className="text-xs text-slate-500 truncate">{node.label}</p>
            <p className="font-mono text-xs text-slate-400">{node.node}</p>
          </div>
          <div className="flex-1 h-2 bg-slate-100 dark:bg-slate-800 rounded-full overflow-hidden">
            <div
              className="h-full rounded-full bg-blue-500 transition-all"
              style={{ width: `${(node.confidence ?? 0) * 100}%` }}
            />
          </div>
          <div className="w-16 text-right shrink-0">
            <p className="text-xs font-bold text-slate-700 dark:text-slate-300">
              {node.confidence != null ? `${(node.confidence * 100).toFixed(0)}%` : "—"}
            </p>
            {node.delta != null && (
              <p className={`text-xs ${node.delta >= 0 ? "text-green-600" : "text-red-500"}`}>
                {node.delta >= 0 ? "+" : ""}{(node.delta * 100).toFixed(0)}%
              </p>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function ConfidencePill({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color =
    pct >= 80 ? "bg-green-100 text-green-700"
    : pct >= 60 ? "bg-yellow-100 text-yellow-700"
    : "bg-red-100 text-red-700";
  return (
    <span className={`text-xs font-bold px-1.5 py-0.5 rounded ${color}`}>
      {pct}%
    </span>
  );
}
