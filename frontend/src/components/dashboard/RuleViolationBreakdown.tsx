"use client";
/**
 * RuleViolationBreakdown — horizontal bar chart of findings by rule code.
 *
 * Renders a sorted breakdown of compliance rule violations, with coloured
 * bars proportional to finding count. Suitable for the dashboard overview.
 */
import { Card } from "@/components/ui/Card";

interface RuleCount {
  rule_code:   string;
  rule_name:   string;
  count:       number;
  severity:    "critical" | "high" | "medium" | "low";
}

interface Props {
  rules:    RuleCount[];
  maxBars?: number;
}

const BAR_COLOR: Record<string, string> = {
  critical: "bg-red-500",
  high:     "bg-orange-500",
  medium:   "bg-yellow-400",
  low:      "bg-blue-400",
};

export function RuleViolationBreakdown({ rules, maxBars = 8 }: Props) {
  if (!rules || rules.length === 0) {
    return (
      <Card padding="md">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-3">
          Violations by Rule
        </p>
        <p className="text-sm text-slate-400 text-center py-6">
          No findings yet — upload data or run the rules engine.
        </p>
      </Card>
    );
  }

  const sorted = [...rules]
    .sort((a, b) => b.count - a.count)
    .slice(0, maxBars);

  const maxCount = sorted[0]?.count ?? 1;

  return (
    <Card padding="md">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-4">
        Violations by Rule
      </p>
      <div className="space-y-3">
        {sorted.map(rule => {
          const pct = Math.max(4, (rule.count / maxCount) * 100);
          return (
            <div key={rule.rule_code}>
              <div className="flex items-center justify-between mb-1">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="text-xs font-mono text-slate-600 dark:text-slate-400 flex-shrink-0">
                    {rule.rule_code}
                  </span>
                  <span className="text-xs text-slate-500 truncate">
                    {rule.rule_name}
                  </span>
                </div>
                <span className="text-xs font-bold text-slate-800 dark:text-slate-200 ml-2 flex-shrink-0">
                  {rule.count.toLocaleString()}
                </span>
              </div>
              <div className="h-2 bg-slate-100 dark:bg-slate-800 rounded-full overflow-hidden">
                <div
                  className={`h-2 rounded-full transition-all duration-500 ${BAR_COLOR[rule.severity] ?? "bg-slate-400"}`}
                  style={{ width: `${pct}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}
