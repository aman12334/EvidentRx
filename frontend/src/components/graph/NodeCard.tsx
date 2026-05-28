import type { GraphNode } from "@/lib/types/graph";

interface NodeCardProps {
  node:     GraphNode;
  selected?: boolean;
  onClick?:  (node: GraphNode) => void;
}

const NODE_TYPE_COLOR: Record<string, string> = {
  covered_entity:     "bg-blue-100  text-blue-800  border-blue-300",
  contract_pharmacy:  "bg-purple-100 text-purple-800 border-purple-300",
  provider:           "bg-green-100 text-green-800  border-green-300",
  ndc:                "bg-yellow-100 text-yellow-800 border-yellow-300",
  case:               "bg-red-100   text-red-800    border-red-300",
  finding:            "bg-orange-100 text-orange-800 border-orange-300",
};

export function NodeCard({ node, selected, onClick }: NodeCardProps) {
  const colorClass = NODE_TYPE_COLOR[node.type] ?? "bg-slate-100 text-slate-800 border-slate-200";

  return (
    <div
      onClick={() => onClick?.(node)}
      className={`rounded-lg border p-3 cursor-pointer transition-all ${colorClass} ${
        selected ? "ring-2 ring-blue-500 shadow-md" : "hover:shadow-sm"
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold">{node.label}</p>
          <p className="text-xs capitalize opacity-75">{node.type.replace(/_/g, " ")}</p>
        </div>
        <span className="shrink-0 rounded bg-black/10 px-1.5 py-0.5 text-xs font-mono">
          {node.id.slice(0, 8)}
        </span>
      </div>

      {Object.keys(node.properties).length > 0 && (
        <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1">
          {Object.entries(node.properties)
            .slice(0, 4)
            .map(([k, v]) => (
              <div key={k}>
                <p className="text-xs opacity-60 truncate">{k}</p>
                <p className="text-xs font-medium truncate">{String(v)}</p>
              </div>
            ))}
        </div>
      )}
    </div>
  );
}
