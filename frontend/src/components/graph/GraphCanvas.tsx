"use client";
import { useState, useCallback, useRef } from "react";
import dynamic from "next/dynamic";
import { Spinner } from "@/components/ui/Spinner";
import { EmptyState } from "@/components/ui/EmptyState";
import { NodeCard } from "./NodeCard";
import { EdgeTooltip } from "./EdgeTooltip";
import type { GraphNeighborhoodResponse, GraphNode, GraphEdge } from "@/lib/types/graph";

// react-force-graph-2d is browser-only — SSR-safe dynamic import
const ForceGraph2D = dynamic(
  () => import("react-force-graph-2d").then((m) => m.default),
  { ssr: false, loading: () => <div className="flex h-full items-center justify-center"><Spinner size="lg" /></div> }
);

const NODE_COLOR: Record<string, string> = {
  covered_entity:    "#3b82f6",
  contract_pharmacy: "#8b5cf6",
  provider:          "#22c55e",
  ndc:               "#eab308",
  case:              "#ef4444",
  finding:           "#f97316",
};

interface GraphCanvasProps {
  data:    GraphNeighborhoodResponse;
  height?: number;
}

export function GraphCanvas({ data, height = 520 }: GraphCanvasProps) {
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [hoveredEdge,  setHoveredEdge]  = useState<GraphEdge  | null>(null);
  const [tooltipPos,   setTooltipPos]   = useState({ x: 0, y: 0 });
  const containerRef = useRef<HTMLDivElement>(null);

  // Build force-graph compatible data
  const graphData = {
    nodes: data.nodes.map((n) => ({
      id:    n.id,
      label: n.label,
      type:  n.type,
      _raw:  n,
    })),
    links: data.edges.map((e) => ({
      source:   e.source_id,
      target:   e.target_id,
      label:    e.relationship,
      weight:   e.weight,
      _raw:     e,
    })),
  };

  const handleNodeClick = useCallback(
    (node: object) => setSelectedNode((node as { _raw: GraphNode })._raw),
    []
  );

  const handleLinkHover = useCallback(
    (link: object | null) => {
      setHoveredEdge(link ? (link as { _raw: GraphEdge })._raw : null);
    },
    []
  );

  const handleMouseMove = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (!containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      setTooltipPos({ x: e.clientX - rect.left, y: e.clientY - rect.top });
    },
    []
  );

  if (data.nodes.length === 0) {
    return (
      <div className="flex h-60 items-center justify-center">
        <EmptyState
          title="No graph data"
          description="No nodes found for this entity. Expand the depth or try a different node."
        />
      </div>
    );
  }

  return (
    <div className="relative" ref={containerRef}>
      {/* Stats bar */}
      <div className="mb-3 flex gap-4 text-xs text-slate-500">
        <span><strong className="text-slate-700 dark:text-slate-300">{data.total_nodes}</strong> nodes</span>
        <span><strong className="text-slate-700 dark:text-slate-300">{data.total_edges}</strong> edges</span>
        <span>depth: <strong className="text-slate-700 dark:text-slate-300">{data.depth}</strong></span>
        <span>root: <strong className="font-mono text-slate-700 dark:text-slate-300">{data.root_type}</strong></span>
      </div>

      {/* Canvas */}
      <div className="overflow-hidden rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900" style={{ height }} onMouseMove={handleMouseMove}>
        <ForceGraph2D
          graphData={graphData}
          nodeLabel="label"
          nodeColor={(n) => NODE_COLOR[(n as unknown as { type: string }).type] ?? "#94a3b8"}
          nodeRelSize={5}
          linkWidth={(l) => Math.max(1, ((l as unknown as { weight: number }).weight ?? 1) * 3)}
          linkColor={() => "#cbd5e1"}
          onNodeClick={handleNodeClick}
          onLinkHover={handleLinkHover}
          enableNodeDrag
          enableZoomInteraction
          backgroundColor="transparent"
          width={containerRef.current?.clientWidth}
          height={height}
        />
      </div>

      {/* Edge tooltip */}
      {hoveredEdge && (
        <div
          className="pointer-events-none absolute z-30"
          style={{ left: tooltipPos.x + 12, top: tooltipPos.y - 8 }}
        >
          <EdgeTooltip edge={hoveredEdge} />
        </div>
      )}

      {/* Node detail panel */}
      {selectedNode && (
        <div className="mt-3">
          <NodeCard node={selectedNode} selected onClick={() => setSelectedNode(null)} />
        </div>
      )}

      {/* Legend */}
      <div className="mt-3 flex flex-wrap gap-3">
        {Object.entries(NODE_COLOR).map(([type, color]) => (
          <div key={type} className="flex items-center gap-1.5 text-xs text-slate-500">
            <span
              className="inline-block h-3 w-3 rounded-full"
              style={{ backgroundColor: color }}
            />
            <span className="capitalize">{type.replace(/_/g, " ")}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
