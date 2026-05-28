"use client";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { Spinner } from "@/components/ui/Spinner";
import { EmptyState } from "@/components/ui/EmptyState";
import { useEntityRiskScores } from "@/lib/hooks/useMonitoring";
import type { EntityRiskScore } from "@/lib/types/monitoring";

/** Transforms a flat list of EntityRiskScore into chart-friendly daily points. */
function buildChartData(scores: EntityRiskScore[]) {
  // Group by score_date, average composite across entities
  const byDate: Record<string, number[]> = {};
  for (const s of scores) {
    const d = s.score_date.slice(0, 10);
    (byDate[d] ??= []).push(s.composite_score);
  }

  return Object.entries(byDate)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, vals]) => ({
      date,
      avg: parseFloat((vals.reduce((a, b) => a + b, 0) / vals.length).toFixed(4)),
      max: parseFloat(Math.max(...vals).toFixed(4)),
    }));
}

export function TrendChart() {
  const { data: scores, isLoading, isError } = useEntityRiskScores();

  if (isLoading) {
    return (
      <Card padding="md">
        <div className="flex justify-center py-12"><Spinner size="lg" /></div>
      </Card>
    );
  }

  if (isError || !scores || scores.length === 0) {
    return (
      <Card padding="md">
        <CardHeader>
          <CardTitle>Risk Score Trends</CardTitle>
        </CardHeader>
        <div className="mt-3">
          <EmptyState title="No trend data" description="Risk score history is not yet available." />
        </div>
      </Card>
    );
  }

  const chartData = buildChartData(scores);

  return (
    <Card padding="md">
      <CardHeader>
        <CardTitle>Risk Score Trends</CardTitle>
      </CardHeader>
      <p className="text-xs text-slate-500 mt-0.5 mb-4">
        Composite risk scores across all monitored entities — averaged and peak per day.
      </p>

      <div className="h-56">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chartData} margin={{ top: 4, right: 16, left: -16, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
            <XAxis
              dataKey="date"
              tick={{ fontSize: 10, fill: "#94a3b8" }}
              tickFormatter={(v: string) => {
                const d = new Date(v);
                return `${d.getMonth() + 1}/${d.getDate()}`;
              }}
            />
            <YAxis
              tick={{ fontSize: 10, fill: "#94a3b8" }}
              domain={[0, 1]}
              tickFormatter={(v: number) => v.toFixed(2)}
            />
            <Tooltip
              formatter={(v: number, name: string) => [
                v.toFixed(4),
                name === "avg" ? "Avg Score" : "Peak Score",
              ]}
              labelFormatter={(l: string) => new Date(l).toLocaleDateString()}
              contentStyle={{ fontSize: 11, borderRadius: 6 }}
            />
            <Legend
              formatter={(value: string) =>
                value === "avg" ? "Average Risk" : "Peak Risk"
              }
              wrapperStyle={{ fontSize: 11 }}
            />
            <Line
              type="monotone"
              dataKey="avg"
              stroke="#3b82f6"
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4 }}
            />
            <Line
              type="monotone"
              dataKey="max"
              stroke="#dc2626"
              strokeWidth={1.5}
              strokeDasharray="4 2"
              dot={false}
              activeDot={{ r: 4 }}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </Card>
  );
}
