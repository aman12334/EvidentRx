"use client";
/**
 * ComplianceScoreCard — visual compliance risk score indicator.
 *
 * Displays a radial gauge (0–100) derived from the composite_risk_score
 * of recent investigation cases. Green = low risk, Red = high risk.
 */
import { Card } from "@/components/ui/Card";

interface Props {
  score:       number;   // 0–1 float from evidence service
  label?:      string;
  sublabel?:   string;
}

function scoreToPercent(s: number): number {
  return Math.min(100, Math.max(0, Math.round(s * 100)));
}

function scoreColor(pct: number): string {
  if (pct >= 80) return "text-red-600";
  if (pct >= 60) return "text-orange-500";
  if (pct >= 40) return "text-yellow-500";
  return "text-green-600";
}

function gaugeColor(pct: number): string {
  if (pct >= 80) return "#dc2626";   // red-600
  if (pct >= 60) return "#f97316";   // orange-500
  if (pct >= 40) return "#eab308";   // yellow-500
  return "#16a34a";                  // green-600
}

function riskLabel(pct: number): string {
  if (pct >= 80) return "Critical Risk";
  if (pct >= 60) return "High Risk";
  if (pct >= 40) return "Moderate Risk";
  return "Low Risk";
}

/**
 * SVG circular progress — radius 40, circumference 251.
 */
function Gauge({ pct }: { pct: number }) {
  const r   = 40;
  const circ = 2 * Math.PI * r;
  const dash = circ * (pct / 100);
  const color = gaugeColor(pct);

  return (
    <svg viewBox="0 0 100 100" className="w-28 h-28">
      {/* Track */}
      <circle cx="50" cy="50" r={r} fill="none"
        stroke="#e2e8f0" strokeWidth="10" />
      {/* Progress */}
      <circle cx="50" cy="50" r={r} fill="none"
        stroke={color} strokeWidth="10"
        strokeDasharray={`${dash} ${circ}`}
        strokeLinecap="round"
        transform="rotate(-90 50 50)"
        style={{ transition: "stroke-dasharray 0.6s ease" }}
      />
      {/* Label */}
      <text x="50" y="46" textAnchor="middle" fontSize="18" fontWeight="700"
        fill={color}>{pct}</text>
      <text x="50" y="60" textAnchor="middle" fontSize="8" fill="#94a3b8">/ 100</text>
    </svg>
  );
}

export function ComplianceScoreCard({ score, label, sublabel }: Props) {
  const pct = scoreToPercent(score);

  return (
    <Card padding="md" className="flex flex-col items-center text-center gap-2">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
        {label ?? "Composite Risk Score"}
      </p>
      <Gauge pct={pct} />
      <p className={`text-sm font-semibold ${scoreColor(pct)}`}>
        {riskLabel(pct)}
      </p>
      {sublabel && (
        <p className="text-xs text-slate-400">{sublabel}</p>
      )}
    </Card>
  );
}
