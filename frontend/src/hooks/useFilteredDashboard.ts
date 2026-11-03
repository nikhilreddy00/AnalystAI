"use client";

import { useQuery } from "@tanstack/react-query";
import { getFilteredDashboard, type FilteredDashboard } from "@/lib/api";
import type { FilterState } from "@/context/DashboardFilterContext";

/** The latest dashboard recomputed on the server for the active filters: one cached query the
 * charts and the KPI strip share, keyed by the session (a new analysis never shows the last
 * one's numbers). `data` is undefined with no filters or while it loads; `failed` says why a
 * number is missing instead of "recomputing" forever. */
export function useFilteredDashboard(filters: FilterState, sessionId: string | undefined):
    { data: FilteredDashboard | undefined; failed: boolean } {
  const active = Object.keys(filters).length > 0;
  const { data, isError } = useQuery({
    queryKey: ["dashboard-filtered", sessionId, filters],
    queryFn: () => getFilteredDashboard(filters),
    enabled: active && !!sessionId,
    staleTime: 60_000,
    retry: false,
  });
  return { data: active ? data : undefined, failed: active && isError };
}
