import { useRouter } from "next/navigation";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import type { Correlation } from "@/lib/types/monitoring";

interface CorrelationMatrixProps {
  correlations: Correlation[];
}

function strengthColor(s: number): string {
  if (s >= 0.8) return "text-red-600 font-bold";
  if (s >= 0.6) return "text-orange-600 font-semibold";
  if (s >= 0.4) return "text-yellow-600";
  return "text-slate-500";
}

function strengthBar(s: number): string {
  if (s >= 0.8) return "bg-red-500";
  if (s >= 0.6) return "bg-orange-400";
  if (s >= 0.4) return "bg-yellow-400";
  return "bg-slate-300";
}

export function CorrelationMatrix({ correlations }: CorrelationMatrixProps) {
  const router = useRouter();

  return (
    <Card padding="md">
      <CardHeader>
        <CardTitle>Cross-Case Correlations</CardTitle>
      </CardHeader>

      {correlations.length === 0 ? (
        <div className="mt-3">
          <EmptyState
            title="No correlations detected"
            description="No statistically significant cross-case correlations found in the current window."
          />
        </div>
      ) : (
        <div className="mt-3 flex flex-col gap-3">
          {correlations.map((c, i) => (
            <div
              key={i}
              className="rounded border border-slate-100 dark:border-slate-800 p-3 hover:bg-slate-50 dark:hover:bg-slate-800/50 cursor-pointer transition-colors"
              onClick={() => router.push(`/investigations/${c.case_id_a}`)}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex gap-2 flex-wrap text-xs text-slate-500">
                    <span className="font-mono text-slate-700 dark:text-slate-300">{c.case_id_a.slice(0, 8)}…</span>
                    <span>↔</span>
                    <span className="font-mono text-slate-700 dark:text-slate-300">{c.case_id_b.slice(0, 8)}…</span>
                  </div>
                  <p className="mt-1 text-xs text-slate-600 dark:text-slate-400 capitalize">
                    {c.correlation_type.replace(/_/g, " ")}
                  </p>
                </div>
                <div className="text-right shrink-0">
                  <p className={`text-sm ${strengthColor(c.strength)}`}>
                    {(c.strength * 100).toFixed(0)}%
                  </p>
                  <p className="text-xs text-slate-400">strength</p>
                </div>
              </div>

              {/* Strength bar */}
              <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                <div
                  className={`h-full rounded-full transition-all ${strengthBar(c.strength)}`}
                  style={{ width: `${c.strength * 100}%` }}
                />
              </div>

              {c.explanation && (
                <p className="mt-1.5 text-xs text-slate-500 italic">{c.explanation}</p>
              )}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
