/**
 * useEntities — React hooks for covered entity data.
 */
"use client";

import { useEffect, useState } from "react";
import {
  fetchEntities,
  fetchEntitySummary,
  type CoveredEntity,
  type EntitySummary,
  type EntityListParams,
} from "@/lib/api/entities";

export function useEntityList(params: EntityListParams = {}) {
  const [entities, setEntities] = useState<CoveredEntity[]>([]);
  const [total,    setTotal]    = useState(0);
  const [loading,  setLoading]  = useState(true);
  const [error,    setError]    = useState<string | null>(null);

  const key = JSON.stringify(params);

  useEffect(() => {
    setLoading(true);
    fetchEntities(params)
      .then(r => { setEntities(r.entities); setTotal(r.total); setError(null); })
      .catch(e => setError(e instanceof Error ? e.message : "Failed"))
      .finally(() => setLoading(false));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return { entities, total, loading, error };
}

export function useEntitySummary(ceId: string | null) {
  const [summary, setSummary] = useState<EntitySummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState<string | null>(null);

  useEffect(() => {
    if (!ceId) return;
    setLoading(true);
    fetchEntitySummary(ceId)
      .then(s => { setSummary(s); setError(null); })
      .catch(e => setError(e instanceof Error ? e.message : "Failed"))
      .finally(() => setLoading(false));
  }, [ceId]);

  return { summary, loading, error };
}
