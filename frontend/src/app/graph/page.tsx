"use client";
import { useState } from "react";
import { PageHeader }   from "@/components/layout/PageHeader";
import { GraphCanvas }  from "@/components/graph/GraphCanvas";
import { Card }         from "@/components/ui/Card";
import { Button }       from "@/components/ui/Button";
import { Spinner }      from "@/components/ui/Spinner";
import { EmptyState }   from "@/components/ui/EmptyState";
import { useGraphStats, useNeighborhood } from "@/lib/hooks/useGraph";

export default function GraphPage() {
  const [nodeType,  setNodeType]  = useState("covered_entity");
  const [nodeId,    setNodeId]    = useState("");
  const [queryKey,  setQueryKey]  = useState<{ type: string; id: string } | null>(null);

  const { data: stats, isLoading: loadingStats } = useGraphStats();

  const {
    data:      neighborhood,
    isLoading: loadingNeighborhood,
    isError:   neighborhoodError,
  } = useNeighborhood(queryKey?.type ?? "", queryKey?.id ?? "");

  function handleSearch() {
    if (nodeId.trim()) {
      setQueryKey({ type: nodeType, id: nodeId.trim() });
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Compliance Graph"
        description="Knowledge graph of entities, pharmacies, providers, claims, and compliance relationships."
      />

      {/* Graph stats overview */}
      {loadingStats ? (
        <div className="flex justify-center py-6"><Spinner /></div>
      ) : stats ? (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <StatTile label="Total Nodes" value={stats.total_nodes.toLocaleString()} />
          <StatTile label="Total Edges" value={stats.total_edges.toLocaleString()} />
          <StatTile label="Node Types"  value={Object.keys(stats.nodes_by_type).length} />
          <StatTile label="Edge Types"  value={Object.keys(stats.edges_by_relationship).length} />
        </div>
      ) : null}

      {/* Node search */}
      <Card padding="md">
        <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
          Explore Neighborhood
        </p>
        <div className="flex flex-wrap gap-2">
          <select
            value={nodeType}
            onChange={(e) => setNodeType(e.target.value)}
            className="rounded border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-1.5 text-sm text-slate-800 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value="covered_entity">Covered Entity</option>
            <option value="contract_pharmacy">Contract Pharmacy</option>
            <option value="provider">Provider</option>
            <option value="case">Case</option>
            <option value="ndc">NDC</option>
          </select>

          <input
            type="text"
            value={nodeId}
            onChange={(e) => setNodeId(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSearch()}
            placeholder="Enter entity ID or UUID…"
            className="flex-1 min-w-48 rounded border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-1.5 text-sm text-slate-800 dark:text-slate-200 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />

          <Button
            variant="primary"
            size="sm"
            onClick={handleSearch}
            disabled={!nodeId.trim()}
          >
            Load Graph
          </Button>
        </div>

        {/* Top central nodes (quick links) */}
        {stats && stats.top_central_nodes.length > 0 && (
          <div className="mt-3">
            <p className="text-xs text-slate-400 mb-1.5">Top central nodes:</p>
            <div className="flex flex-wrap gap-1.5">
              {stats.top_central_nodes.slice(0, 8).map((n) => (
                <button
                  key={n.node_id}
                  onClick={() => {
                    setNodeType(n.node_type);
                    setNodeId(n.node_id);
                    setQueryKey({ type: n.node_type, id: n.node_id });
                  }}
                  className="rounded border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-2 py-0.5 text-xs text-slate-600 dark:text-slate-400 hover:border-blue-400 hover:text-blue-600 transition-colors"
                  title={`${n.node_type}: ${n.node_id} (degree ${n.degree})`}
                >
                  {n.label}
                </button>
              ))}
            </div>
          </div>
        )}
      </Card>

      {/* Graph canvas */}
      {loadingNeighborhood ? (
        <div className="flex justify-center py-16"><Spinner size="lg" /></div>
      ) : neighborhoodError ? (
        <EmptyState
          title="Node not found"
          description="No neighborhood data returned. Verify the node type and ID."
        />
      ) : neighborhood ? (
        <Card padding="md">
          <GraphCanvas data={neighborhood} />
        </Card>
      ) : (
        <div className="rounded-lg border border-dashed border-slate-300 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 py-16 text-center">
          <p className="text-sm text-slate-400">
            Enter a node ID above to visualize its compliance graph neighborhood.
          </p>
        </div>
      )}
    </div>
  );
}

function StatTile({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-4 py-3 text-center">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">{label}</p>
      <p className="mt-1 text-2xl font-bold text-slate-900 dark:text-white">{value}</p>
    </div>
  );
}
