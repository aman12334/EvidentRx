/**
 * useDashboard — React hooks for the compliance dashboard data layer.
 *
 * Provides:
 *   useDashboardSummary()   — top-level KPIs, refreshes every 60s
 *   useRuleBreakdown()      — findings by rule code
 *   useExposureTrend()      — rolling financial exposure
 */
"use client";

import { useEffect, useState } from "react";
import {
  fetchDashboardSummary,
  fetchRuleBreakdown,
  fetchExposureTrend,
  type DashboardSummary,
  type RuleBreakdownItem,
  type ExposureTrendPoint,
} from "@/lib/api/dashboard";

interface AsyncState<T> {
  data:    T | null;
  loading: boolean;
  error:   string | null;
}

// ── Summary ────────────────────────────────────────────────────────────────────

export function useDashboardSummary(refreshMs = 60_000) {
  const [state, setState] = useState<AsyncState<DashboardSummary>>({
    data: null, loading: true, error: null,
  });

  useEffect(() => {
    let alive = true;
    const load = () => {
      fetchDashboardSummary()
        .then(d  => { if (alive) setState({ data: d, loading: false, error: null }); })
        .catch(e => { if (alive) setState(s => ({ ...s, loading: false,
          error: e instanceof Error ? e.message : "Failed to load" })); });
    };
    load();
    const id = setInterval(load, refreshMs);
    return () => { alive = false; clearInterval(id); };
  }, [refreshMs]);

  return state;
}

// ── Rule breakdown ─────────────────────────────────────────────────────────────

export function useRuleBreakdown(limit = 10) {
  const [state, setState] = useState<AsyncState<RuleBreakdownItem[]>>({
    data: null, loading: true, error: null,
  });

  useEffect(() => {
    fetchRuleBreakdown(limit)
      .then(d  => setState({ data: d, loading: false, error: null }))
      .catch(e => setState({ data: null, loading: false,
        error: e instanceof Error ? e.message : "Failed" }));
  }, [limit]);

  return state;
}

// ── Exposure trend ─────────────────────────────────────────────────────────────

export function useExposureTrend(days = 90) {
  const [state, setState] = useState<AsyncState<ExposureTrendPoint[]>>({
    data: null, loading: true, error: null,
  });

  useEffect(() => {
    fetchExposureTrend(days)
      .then(d  => setState({ data: d, loading: false, error: null }))
      .catch(e => setState({ data: null, loading: false,
        error: e instanceof Error ? e.message : "Failed" }));
  }, [days]);

  return state;
}
