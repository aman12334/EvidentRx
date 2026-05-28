import { clsx } from "clsx";
import type { Severity } from "@/lib/types/investigation";

interface BadgeProps {
  label:   string;
  variant: Severity | "neutral" | "brand";
  size?:   "sm" | "md";
}

const variants: Record<string, string> = {
  critical: "bg-red-50   text-red-700   border border-red-200   dark:bg-red-950   dark:text-red-300",
  high:     "bg-orange-50 text-orange-700 border border-orange-200 dark:bg-orange-950 dark:text-orange-300",
  medium:   "bg-yellow-50 text-yellow-700 border border-yellow-200 dark:bg-yellow-950 dark:text-yellow-300",
  low:      "bg-green-50  text-green-700  border border-green-200  dark:bg-green-950  dark:text-green-300",
  neutral:  "bg-slate-100 text-slate-600  border border-slate-200  dark:bg-slate-800  dark:text-slate-300",
  brand:    "bg-blue-50   text-blue-700   border border-blue-200   dark:bg-blue-950   dark:text-blue-300",
};

export function Badge({ label, variant, size = "sm" }: BadgeProps) {
  return (
    <span
      className={clsx(
        "inline-flex items-center rounded font-medium uppercase tracking-wide",
        size === "sm" ? "px-2 py-0.5 text-xs" : "px-2.5 py-1 text-sm",
        variants[variant] ?? variants.neutral
      )}
    >
      {label}
    </span>
  );
}
