"use client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, useEffect } from "react";
import { useUIStore } from "@/lib/store/uiStore";

function DarkModeSync({ children }: { children: React.ReactNode }) {
  const darkMode = useUIStore((s) => s.darkMode);

  useEffect(() => {
    const root = document.documentElement;
    if (darkMode) {
      root.classList.add("dark");
    } else {
      root.classList.remove("dark");
    }
  }, [darkMode]);

  return <>{children}</>;
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime:            60 * 1000,      // 1 min
            refetchOnWindowFocus: false,
            retry:                1,
          },
        },
      })
  );

  return (
    <QueryClientProvider client={queryClient}>
      <DarkModeSync>{children}</DarkModeSync>
    </QueryClientProvider>
  );
}
