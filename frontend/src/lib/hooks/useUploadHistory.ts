/**
 * useUploadHistory — React hook for fetching and refreshing the upload batch history.
 *
 * Polls the GET /api/v1/upload/history endpoint when the history tab is active
 * and refreshes on demand.
 */
"use client";

import { useCallback, useEffect, useState } from "react";
import { fetchUploadHistory, type BatchHistoryItem } from "@/lib/api/upload";

export interface UseUploadHistoryReturn {
  history:  BatchHistoryItem[];
  loading:  boolean;
  error:    string | null;
  refresh:  () => void;
}

export function useUploadHistory(enabled = true, limit = 20): UseUploadHistoryReturn {
  const [history, setHistory] = useState<BatchHistoryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState<string | null>(null);

  const load = useCallback(() => {
    if (!enabled) return;
    setLoading(true);
    setError(null);
    fetchUploadHistory(limit)
      .then(setHistory)
      .catch(e => setError(e instanceof Error ? e.message : "Failed to load history"))
      .finally(() => setLoading(false));
  }, [enabled, limit]);

  useEffect(() => { load(); }, [load]);

  return { history, loading, error, refresh: load };
}
