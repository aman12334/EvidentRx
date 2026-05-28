import { clsx } from "clsx";
import type { ButtonHTMLAttributes } from "react";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "destructive" | "ghost";
  size?:    "sm" | "md" | "lg";
  loading?: boolean;
}

const variants = {
  primary:     "bg-blue-600 text-white hover:bg-blue-700 focus:ring-blue-500",
  secondary:   "bg-white text-slate-700 border border-slate-300 hover:bg-slate-50 focus:ring-slate-500",
  destructive: "bg-red-600 text-white hover:bg-red-700 focus:ring-red-500",
  ghost:       "text-slate-600 hover:bg-slate-100 focus:ring-slate-400",
};

const sizes = {
  sm:  "px-3 py-1.5 text-sm",
  md:  "px-4 py-2 text-sm",
  lg:  "px-6 py-3 text-base",
};

export function Button({
  variant = "secondary",
  size = "md",
  loading = false,
  disabled,
  className,
  children,
  ...props
}: ButtonProps) {
  return (
    <button
      disabled={disabled || loading}
      className={clsx(
        "inline-flex items-center gap-2 rounded font-medium",
        "focus:outline-none focus:ring-2 focus:ring-offset-2",
        "disabled:opacity-50 disabled:cursor-not-allowed",
        "transition-colors duration-150",
        variants[variant],
        sizes[size],
        className
      )}
      {...props}
    >
      {loading && (
        <span className="h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
      )}
      {children}
    </button>
  );
}
