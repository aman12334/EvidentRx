"use client";
import { useRef } from "react";

interface SearchInputProps {
  value:         string;
  onChange:      (value: string) => void;
  placeholder?:  string;
  className?:    string;
  autoFocus?:    boolean;
  onClear?:      () => void;
}

export function SearchInput({
  value,
  onChange,
  placeholder = "Search…",
  className = "",
  autoFocus = false,
  onClear,
}: SearchInputProps) {
  const ref = useRef<HTMLInputElement>(null);

  const handleClear = () => {
    onChange("");
    onClear?.();
    ref.current?.focus();
  };

  return (
    <div className={`relative flex items-center ${className}`}>
      {/* Search icon */}
      <svg
        className="pointer-events-none absolute left-3 h-4 w-4 text-slate-400"
        xmlns="http://www.w3.org/2000/svg"
        fill="none"
        viewBox="0 0 24 24"
        strokeWidth={1.8}
        stroke="currentColor"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z"
        />
      </svg>

      <input
        ref={ref}
        type="search"
        value={value}
        autoFocus={autoFocus}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className={`
          w-full rounded-md border border-slate-200 dark:border-slate-700
          bg-white dark:bg-slate-900
          pl-9 pr-${value ? "8" : "3"} py-2
          text-sm text-slate-800 dark:text-slate-200
          placeholder:text-slate-400
          outline-none
          focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20
          transition-colors
        `}
      />

      {/* Clear button */}
      {value && (
        <button
          type="button"
          onClick={handleClear}
          aria-label="Clear search"
          className="absolute right-2.5 flex h-5 w-5 items-center justify-center rounded-full text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800 hover:text-slate-600 transition-colors"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 20 20"
            fill="currentColor"
            className="h-3.5 w-3.5"
          >
            <path d="M6.28 5.22a.75.75 0 0 0-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 1 0 1.06 1.06L10 11.06l3.72 3.72a.75.75 0 1 0 1.06-1.06L11.06 10l3.72-3.72a.75.75 0 0 0-1.06-1.06L10 8.94 6.28 5.22Z" />
          </svg>
        </button>
      )}
    </div>
  );
}
