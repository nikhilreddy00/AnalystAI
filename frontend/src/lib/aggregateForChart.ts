import type { ChartConfig } from "@/types";

const GROUPBY_TYPES = new Set([
  "BarChart",
  "HorizontalBarChart",
  "PieChart",
  "DonutChart",
  "LineChart",
  "HistogramChart",
]);
const PASSTHROUGH_TYPES = new Set(["ScatterChart"]);

export type AggregationMode = "count" | "passthrough" | "unfiltered";

export interface AggregationResult {
  mode: AggregationMode;
  /** Populated when mode is "count" or "passthrough". For "unfiltered" mode,
   * carries the chart's original pre-aggregated data so callers can render
   * the chart unmodified. */
  data: Record<string, unknown>[];
  /** y-axis key used for the count value when mode === "count". Empty string
   * otherwise. */
  countKey: string;
}

/**
 * Re-aggregate a chart's data from filtered raw rows.
 *
 * Spec 1 of Gap 2 ships count-only aggregation. Groupby-shaped charts
 * (Bar/HorizontalBar/Pie/Donut/Line/Histogram) re-render as row counts by
 * their x_key. ScatterChart passes filtered rows through. ComposedChart,
 * HeatmapChart, or any chart without an x_key fall back to the original
 * pre-aggregated data — caller renders a "shows unfiltered data" pill.
 */
export function aggregateForChart(
  rows: Record<string, unknown>[],
  chart: ChartConfig,
): AggregationResult {
  if (PASSTHROUGH_TYPES.has(chart.chart_type)) {
    return { mode: "passthrough", data: rows, countKey: chart.y_keys[0] ?? "" };
  }
  if (!GROUPBY_TYPES.has(chart.chart_type) || !chart.x_key) {
    return { mode: "unfiltered", data: chart.data, countKey: "" };
  }

  const counts = new Map<unknown, number>();
  for (const row of rows) {
    const key = row[chart.x_key];
    if (key == null || key === "") continue;
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }

  const countKey = chart.y_keys[0] ?? "count";
  const data = Array.from(counts.entries()).map(([k, n]) => ({
    [chart.x_key]: k,
    [countKey]: n,
  }));

  if (chart.chart_type === "LineChart") {
    data.sort((a, b) => {
      const ax = a[chart.x_key] as number | string;
      const bx = b[chart.x_key] as number | string;
      return ax < bx ? -1 : ax > bx ? 1 : 0;
    });
  }

  return { mode: "count", data, countKey };
}
