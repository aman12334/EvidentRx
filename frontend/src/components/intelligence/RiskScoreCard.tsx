import { useRouter } from "next/navigation";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import type { EntityRiskScore } from "@/lib/types/monitoring";

interface RiskScoreCardProps {
  score: EntityRiskScore;
}

const RISK_TIER_VARIANT: Record<string, "critical" | "high" | "medium" | "low"> = {
  critical: "critical",
  high:     "high",
  medium:   "medium",
  low:      "low",
};

const DIRECTION_META: Record<string, { icon: string; color: string }> = {
  worsening: { icon: "↑", color: "text-red-600" },
  improving: { icon: "↓", color: "text-green-600" },
  stable:    { icon: "→", color: "text-slate-500" },
  volatile:  { icon: "⟳", color: "text-orange-500" },
};

export function RiskScoreCard({ score }: RiskScoreCardProps) {
  const router = useRouter();
  const dir    = DIRECTION_META[score.trend_direction] ?? DIRECTION_META.stable;
  const tier   = RISK_TIER_VARIANT[score.risk_tier] ?? "low";

  return (
    <Card
      padding="sm"
      className="cursor-pointer transition-shadow hover:shadow-md"
      onClick={() => router.push(`/intelligence/risk`)}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-slate-900 dark:text-white">
            {score.entity_id}
          </p>
          <p className="text-xs text-slate-500 capitalize">{score.entity_type}</p>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <span className={`text-lg font-bold ${dir.color}`} title={score.trend_direction}>
            {dir.icon}
          </span>
          <Badge label={tier.toUpperCase()} variant={tier} />
        </div>
      </div>

      {/* Composite score bar */}
      <div className="mt-3">
        <div className="flex justify-between text-xs mb-0.5">
          <span className="text-slate-500">Composite Risk</span>
          <span className="font-bold text-slate-800 dark:text-slate-200">
            {score.composite_score.toFixed(3)}
          </span>
        </div>
        <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
          <div
            className={`h-full rounded-full transition-all ${
              tier === "critical" ? "bg-red-500"
              : tier === "high"   ? "bg-orange-500"
              : tier === "medium" ? "bg-yellow-500"
              : "bg-green-500"
            }`}
            style={{ width: `${Math.min(score.composite_score * 100, 100)}%` }}
          />
        </div>
      </div>

      {/* Sub-scores */}
      <div className="mt-3 grid grid-cols-3 gap-2 text-xs text-center">
        <div>
          <p className="text-slate-400">Velocity</p>
          <p className="font-semibold text-slate-700 dark:text-slate-300">
            {score.finding_velocity.toFixed(2)}/d
          </p>
        </div>
        <div>
          <p className="text-slate-400">Exposure Δ</p>
          <p className="font-semibold text-slate-700 dark:text-slate-300">
            {(score.exposure_trajectory * 100).toFixed(0)}%
          </p>
        </div>
        <div>
          <p className="text-slate-400">Escalation P</p>
          <p className="font-semibold text-slate-700 dark:text-slate-300">
            {(score.escalation_probability * 100).toFixed(0)}%
          </p>
        </div>
      </div>

      <p className="mt-2 text-right text-xs text-slate-400">
        {new Date(score.score_date).toLocaleDateString()}
      </p>
    </Card>
  );
}
