import type { GraphEdge } from "@/lib/types/graph";

interface EdgeTooltipProps {
  edge: GraphEdge;
}

export function EdgeTooltip({ edge }: EdgeTooltipProps) {
  return (
    <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 shadow-lg p-3 text-xs min-w-[220px]">
      <div className="flex items-center justify-between mb-2">
        <span className="font-semibold uppercase tracking-wide text-slate-500">
          {edge.relationship.replace(/_/g, " ")}
        </span>
        <span className="rounded bg-slate-100 dark:bg-slate-800 px-1.5 py-0.5 font-mono font-bold text-slate-700 dark:text-slate-300">
          {edge.weight.toFixed(3)}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-x-3 gap-y-1.5">
        <div>
          <p className="text-slate-400">Source</p>
          <p className="font-mono text-slate-700 dark:text-slate-300 truncate">
            {edge.source_id.slice(0, 12)}…
          </p>
          <p className="text-slate-400 capitalize">{edge.source_type.replace(/_/g, " ")}</p>
        </div>
        <div>
          <p className="text-slate-400">Target</p>
          <p className="font-mono text-slate-700 dark:text-slate-300 truncate">
            {edge.target_id.slice(0, 12)}…
          </p>
          <p className="text-slate-400 capitalize">{edge.target_type.replace(/_/g, " ")}</p>
        </div>
      </div>

      {Object.keys(edge.properties).length > 0 && (
        <div className="mt-2 border-t border-slate-100 dark:border-slate-800 pt-2">
          <p className="text-slate-400 mb-1">Properties</p>
          {Object.entries(edge.properties)
            .slice(0, 4)
            .map(([k, v]) => (
              <div key={k} className="flex justify-between gap-2">
                <span className="text-slate-500 truncate">{k}</span>
                <span className="font-medium text-slate-700 dark:text-slate-300 truncate">
                  {String(v)}
                </span>
              </div>
            ))}
        </div>
      )}
    </div>
  );
}
