import type { ChartConfig } from "@/types";
import type { FilteredDashboard } from "./api.ts";

export interface ChartView {
  data: Record<string, unknown>[];
  /** The server's title for the filtered numbers, when it rebuilt the chart. */
  title?: string;
  /** The chart could not be recomputed for the filters (built from the model's own findings,
   * a suppressed small cell, or the request failed): it shows the whole data, and says so. */
  unfiltered: boolean;
  /** The server's numbers for the filters are on their way: the chart as built, marked. */
  updating: boolean;
}

/**
 * What one dashboard chart shows. No filters: the chart as built. Filters: only the server's
 * recomputation (the domain's rules, the same functions that built it). The browser never
 * computes a number: while the server works the chart as built is marked "updating", and a
 * chart the server couldn't rebuild says it shows unfiltered data.
 */
export function chartView(chart: ChartConfig, active: boolean, server: FilteredDashboard | undefined,
                          failed = false): ChartView {
  if (!active) return { data: chart.data, unfiltered: false, updating: false };
  if (!server) return { data: chart.data, unfiltered: failed, updating: !failed };
  const fresh = server.suppressed ? undefined : server.charts[chart.id];
  return fresh ? { data: fresh, title: server.titles?.[chart.id], unfiltered: false, updating: false }
    : { data: chart.data, unfiltered: true, updating: false };
}

/** A tile's value under filters: the server's, by label; null with no filters, while it loads,
 * or for a tile the server couldn't give. */
export function tileView(label: string, active: boolean, server: FilteredDashboard | undefined): string | null {
  return active && server && !server.suppressed ? (server.tiles[label] ?? null) : null;
}

/** A filtered tile's context line (its window under a date range), from the server; null when
 * the tile shows its value as built. */
export function tileContextView(label: string, active: boolean, server: FilteredDashboard | undefined): string | null {
  return tileView(label, active, server) != null ? (server?.contexts?.[label] || null) : null;
}
