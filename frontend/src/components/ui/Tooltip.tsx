"use client";
import { useState, useRef } from "react";

type TooltipPosition = "top" | "bottom" | "left" | "right";

interface TooltipProps {
  content:    React.ReactNode;
  position?:  TooltipPosition;
  children:   React.ReactElement;
  delay?:     number;
  className?: string;
}

export function Tooltip({
  content,
  position = "top",
  children,
  delay = 120,
  className = "",
}: TooltipProps) {
  const [visible, setVisible]   = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const show = () => {
    timerRef.current = setTimeout(() => setVisible(true), delay);
  };
  const hide = () => {
    if (timerRef.current) clearTimeout(timerRef.current);
    setVisible(false);
  };

  const positionClasses: Record<TooltipPosition, string> = {
    top:    "bottom-full left-1/2 -translate-x-1/2 mb-2",
    bottom: "top-full left-1/2 -translate-x-1/2 mt-2",
    left:   "right-full top-1/2 -translate-y-1/2 mr-2",
    right:  "left-full top-1/2 -translate-y-1/2 ml-2",
  };

  return (
    <span
      className={`relative inline-block ${className}`}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
    >
      {children}

      {visible && (
        <span
          role="tooltip"
          className={`
            pointer-events-none absolute z-50 whitespace-nowrap
            rounded-md bg-slate-900 dark:bg-slate-700
            px-2.5 py-1.5 text-xs text-white shadow-lg
            ${positionClasses[position]}
          `}
        >
          {content}
        </span>
      )}
    </span>
  );
}
