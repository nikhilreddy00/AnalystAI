import type { ChartConfig } from "@/types";
import type { FilteredDashboard } from "./api.ts";
import { aggregateForChart } from "./aggregateForChart.ts";

export interface ChartView {
  data: Record<string, unknown>[];
  /** The chart could not be recomputed for the filters (built from the model's own findings). */
  unfiltered: boolean;
  /** Recounted in the browser as row counts (no server result for it). */
  counted: boolean;
}

/**
 * What one dashboard chart shows. No filters: the chart as built. Filters: the server's
 * recomputation (the domain's rules, the same functions that built it); while it loads, or for
 * a chart the server couldn't rebuild, a row-count chart is recounted from the filtered rows and
 * any other chart says it shows unfiltered data.
 */
export function chartView(chart: ChartConfig, active: boolean, server: FilteredDashboard | undefined,
                          filteredRows: Record<string, unknown>[]): ChartView {
  if (!active) return { data: chart.data, unfiltered: false, counted: false };
  const fresh = server?.charts[chart.id];
  if (fresh) return { data: fresh, unfiltered: false, counted: false };
  const agg = aggregateForChart(filteredRows, chart);
  if (agg.mode === "unfiltered") return { data: chart.data, unfiltered: true, counted: false };
  return { data: agg.data, unfiltered: false, counted: agg.mode === "count" };
}

/** A tile's value under filters: the server's, by label; null while it loads or for a tile the
 * filtered rows can't give. */
export function tileView(label: string, active: boolean, server: FilteredDashboard | undefined): string | null {
  return active && server ? (server.tiles[label] ?? null) : null;
}
