"use client";

import { useQuery } from "@tanstack/react-query";
import { getFilteredDashboard, type FilteredDashboard } from "@/lib/api";
import type { FilterState } from "@/context/DashboardFilterContext";

/** The latest dashboard recomputed on the server for the active filters (one cached query the
 * charts and the KPI strip share); undefined with no filters or while it loads. */
export function useFilteredDashboard(filters: FilterState): FilteredDashboard | undefined {
  const active = Object.keys(filters).length > 0;
  const { data } = useQuery({
    queryKey: ["dashboard-filtered", filters],
    queryFn: () => getFilteredDashboard(filters),
    enabled: active,
    staleTime: 60_000,
  });
  return active ? data : undefined;
}
