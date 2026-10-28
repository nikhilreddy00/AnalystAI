"use client";

import { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  ErrorBar,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ChartConfig, ChartType } from "@/types";
import type { FilterValue } from "@/context/DashboardFilterContext";
import { sendChartFeedback } from "@/lib/api";

interface Props {
  config: ChartConfig;
  height?: number;
  /** Optional slot rendered top-right of the card (e.g. a pin button). A function receives the
   *  chart as the user currently sees it (after a type switch), so pinning keeps their choice. */
  action?: React.ReactNode | ((current: ChartConfig) => React.ReactNode);
  onElementClick?: (key: string, value: FilterValue) => void;
  /** Phase 5: opens the evidence item a chart cites. */
  onEvidence?: (id: string) => void;
  /** Phase 5: prefills the chat box ("Change variables…"). */
  onDraft?: (text: string) => void;
  /** Phase 5: set to persist a chart-type switch (and log it as feedback). */
  sessionId?: string | null;
}

const TYPE_NAME: Record<string, string> = {
  BarChart: "Bars",
  HorizontalBarChart: "Horizontal bars",
  LineChart: "Line",
  DonutChart: "Donut",
  PieChart: "Pie",
};

/** Types a user can switch to without misleading: bars ↔ horizontal bars, a line only over an
 *  ordered x, a pie/donut only for ≤ 5 additive parts. Mirrors swap_options() in
 *  agents/visualization_agent.py, which the server checks again. */
export function swapOptions(c: ChartConfig): ChartType[] {
  if (["ScatterChart", "HistogramChart", "HeatmapChart", "ComposedChart"].includes(c.chart_type)) return [];
  const xs = c.data.map((d) => d[c.x_key]);
  const ordered = c.x_key === "period" || (xs.length > 0 && xs.every((x) => x !== null && x !== "" && !Number.isNaN(Number(x))));
  const agg = c.rationale?.aggregation;
  const parts = c.y_keys.length === 1 && c.data.length <= 5 && (agg === "sum" || agg === "count");
  const opts: ChartType[] = ["BarChart", "HorizontalBarChart", ...(ordered ? (["LineChart"] as ChartType[]) : []),
    ...(parts ? (["DonutChart", "PieChart"] as ChartType[]) : [])];
  return opts.includes(c.chart_type) ? opts : [c.chart_type, ...opts];
}

// Bars always start at zero (a truncated baseline exaggerates differences); negatives still show.
const ZERO_BASE = [(min: number) => Math.min(0, min), "auto"] as [(min: number) => number, "auto"];

// Phase 3.5: the categorical slots live in globals.css (--chart-1..8, validated for both
// themes), so charts re-colour with the theme. Assign in order, never cycle past 8.
const SERIES = Array.from({ length: 8 }, (_, i) => `var(--chart-${i + 1})`);

// Phase 5: the backend sends `colors: []` unless a colour encodes meaning (target green/red,
// ordinal ramp, below-average), so any colour it sends is kept.
function meaningful(config: ChartConfig) {
  return config.colors.length > 0;
}
function colors(config: ChartConfig) {
  return meaningful(config) ? config.colors : SERIES;
}

// Category bars whose labels are all numbers (units 1..8) read in numeric order, not by count.
function orderedData(config: ChartConfig) {
  const xs = config.data.map((d) => d[config.x_key]);
  const numeric = xs.length > 1 && xs.every((x) => x !== null && x !== "" && !Number.isNaN(Number(x)));
  return numeric ? [...config.data].sort((a, b) => Number(a[config.x_key]) - Number(b[config.x_key])) : config.data;
}

// PROMPT-2 fix 1: when config.value_format === "percent", values are pre-multiplied
// to 0–100 backend-side; we just suffix "%" on tick labels and tooltip readouts.
function tickFormatter(config: ChartConfig) {
  if (config.value_format === "percent") return (v: number) => `${v}%`;
  return undefined;
}
function tooltipFormatter(config: ChartConfig) {
  if (config.value_format === "percent") {
    // Recharts types `value` as `ValueType | undefined`; widen to unknown and
    // rely on the runtime check.
    return ((value: unknown, name: unknown) => {
      const formatted = typeof value === "number" ? `${value.toFixed(1)}%` : String(value ?? "");
      return [formatted, String(name ?? "")] as [string, string];
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    }) as any;
  }
  return undefined;
}
// PROMPT-2 fix 6: legends now sit top-right so they no longer overlap the x-axis label.
const LEGEND_PROPS = {
  verticalAlign: "top" as const,
  align: "right" as const,
  iconType: "circle" as const,
  iconSize: 8,
  height: 28,
};

// Recessive axes and grid; text wears text tokens, never the series colour.
const T = {
  tooltip: {
    contentStyle: {
      background: "var(--popover)",
      border: "1px solid var(--border)",
      borderRadius: 6,
      boxShadow: "0 6px 20px -8px rgb(0 0 0 / 0.25)",
      fontSize: 12,
      color: "var(--popover-foreground)",
    },
    itemStyle: { color: "var(--popover-foreground)" },
    labelStyle: { color: "var(--muted-foreground)", marginBottom: 4 },
  },
  axis: {
    tick: { fill: "var(--muted-foreground)", fontSize: 11 },
    axisLine: { stroke: "var(--border)" },
    tickLine: false as const,
  },
  grid: { strokeDasharray: "3 3", stroke: "var(--border)", vertical: false },
  legendColor: "var(--muted-foreground)",
  labelFill: "var(--muted-foreground)",
};

type ThemeStyles = typeof T;

function referenceLines(config: ChartConfig) {
  return (config.reference_lines ?? []).map((r, i) => (
    <ReferenceLine
      key={`ref-${i}`}
      {...(r.axis === "y" ? { y: r.value } : { x: r.value })}
      stroke="var(--destructive)"
      strokeDasharray="4 3"
      label={{ value: r.label, position: "top", fill: "var(--muted-foreground)", fontSize: 10 }}
    />
  ));
}

interface ViewProps {
  config: ChartConfig;
  height: number;
  t: ThemeStyles;
  onElementClick?: (key: string, value: FilterValue) => void;
}

function LineChartView({ config, height, t, onElementClick }: ViewProps) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart
        data={config.data}
        margin={{ bottom: 12 }}
        onClick={(state) => {
          if (state && state.activeLabel && onElementClick) {
            onElementClick(config.x_key, { kind: "equality", value: state.activeLabel });
          }
        }}
        style={{ cursor: onElementClick ? "pointer" : "default" }}
      >
        {config.show_grid && <CartesianGrid {...t.grid} />}
        <XAxis dataKey={config.x_key} {...t.axis} label={config.x_label ? { value: config.x_label, position: "insideBottom", offset: -8, fill: t.labelFill, fontSize: 11 } : undefined} />
        <YAxis {...t.axis} tickFormatter={tickFormatter(config)} label={config.y_label ? { value: config.y_label, angle: -90, position: "insideLeft", fill: t.labelFill, fontSize: 11 } : undefined} />
        <Tooltip {...t.tooltip} formatter={tooltipFormatter(config)} />
        {config.show_legend && config.y_keys.length > 1 && <Legend {...LEGEND_PROPS} wrapperStyle={{ fontSize: 12, color: t.legendColor }} />}
        {referenceLines(config)}
        {config.y_keys.map((key, i) => (
          <Line
            key={key}
            type="monotone"
            dataKey={key}
            stroke={colors(config)[i % colors(config).length]}
            dot={false}
            strokeWidth={2}
            activeDot={{ r: 5, strokeWidth: 0 }}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

function BarChartView({ config, height, t, horizontal, onElementClick }: ViewProps & { horizontal?: boolean }) {
  const clrs = colors(config);
  // Per-bar colours only when they encode something (backend-supplied); a single
  // series otherwise stays one colour.
  const perBar = meaningful(config) && config.colors.length > 1 && config.y_keys.length === 1;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart
        data={config.chart_type === "HistogramChart" ? config.data : orderedData(config)}
        layout={horizontal ? "vertical" : "horizontal"}
        barCategoryGap="25%"
        onClick={(state) => {
          if (state && state.activeLabel && onElementClick) {
            onElementClick(config.x_key, { kind: "equality", value: state.activeLabel });
          }
        }}
        style={{ cursor: onElementClick ? "pointer" : "default" }}
      >
        {config.show_grid && <CartesianGrid {...t.grid} />}
        {horizontal ? (
          <>
            <XAxis type="number" domain={ZERO_BASE} {...t.axis} tickFormatter={tickFormatter(config)} label={config.y_label ? { value: config.y_label, position: "insideBottom", offset: -5, fill: t.labelFill, fontSize: 11 } : undefined} />
            <YAxis dataKey={config.x_key} type="category" {...t.axis} width={110} label={config.x_label ? { value: config.x_label, angle: -90, position: "insideLeft", fill: t.labelFill, fontSize: 11 } : undefined} />
          </>
        ) : (
          <>
            <XAxis dataKey={config.x_key} {...t.axis} label={config.x_label ? { value: config.x_label, position: "insideBottom", offset: -5, fill: t.labelFill, fontSize: 11 } : undefined} />
            <YAxis domain={ZERO_BASE} {...t.axis} tickFormatter={tickFormatter(config)} label={config.y_label ? { value: config.y_label, angle: -90, position: "insideLeft", fill: t.labelFill, fontSize: 11 } : undefined} />
          </>
        )}
        <Tooltip {...t.tooltip} cursor={{ fill: "var(--muted)" }} formatter={tooltipFormatter(config)} />
        {config.show_legend && config.y_keys.length > 1 && <Legend {...LEGEND_PROPS} wrapperStyle={{ fontSize: 12, color: t.legendColor }} />}
        {referenceLines(config)}
        {config.y_keys.map((key, i) => (
          <Bar key={key} dataKey={key} fill={clrs[i % clrs.length]} radius={horizontal ? [0, 3, 3, 0] : [3, 3, 0, 0]} maxBarSize={44}>
            {perBar && config.data.map((_, di) => (
              <Cell key={di} fill={clrs[di % clrs.length]} />
            ))}
            {config.error_key && (
              <ErrorBar dataKey={`${key}${config.error_key}`} direction={horizontal ? "x" : "y"} width={5} stroke="var(--foreground)" strokeOpacity={0.55} />
            )}
          </Bar>
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}

function PieChartView({ config, height, t, donut, onElementClick }: ViewProps & { donut?: boolean }) {
  const pieColors = colors(config);
  return (
    <ResponsiveContainer width="100%" height={height}>
      <PieChart style={{ cursor: onElementClick ? "pointer" : "default" }}>
        <Pie
          data={config.data}
          dataKey={config.y_keys[0] ?? "value"}
          nameKey={config.x_key}
          cx="50%"
          cy="50%"
          innerRadius={donut ? "52%" : 0}
          outerRadius="72%"
          paddingAngle={2}
          stroke="var(--card)"
          label={({ name, percent }) => `${name} ${((percent ?? 0) * 100).toFixed(0)}%`}
          labelLine={false}
          onClick={(data: unknown) => {
            const d = data as Record<string, string> | null;
            if (d && onElementClick) {
              onElementClick(config.x_key, { kind: "equality", value: d.name || d[config.x_key] });
            }
          }}
        >
          {config.data.map((_, i) => (
            <Cell key={i} fill={pieColors[i % pieColors.length]} strokeWidth={2} />
          ))}
        </Pie>
        <Tooltip {...t.tooltip} formatter={tooltipFormatter(config)} />
        {config.show_legend && <Legend {...LEGEND_PROPS} wrapperStyle={{ fontSize: 12, color: t.legendColor }} />}
      </PieChart>
    </ResponsiveContainer>
  );
}

function ScatterChartView({ config, height, t, onElementClick }: ViewProps) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ScatterChart margin={{ bottom: 12 }} style={{ cursor: onElementClick ? "pointer" : "default" }}>
        {config.show_grid && <CartesianGrid {...t.grid} />}
        <XAxis type="number" dataKey={config.x_key} name={config.x_label ?? config.x_key} domain={["auto", "auto"]} {...t.axis} label={{ value: config.x_label ?? config.x_key, position: "insideBottom", offset: -4, fill: t.labelFill, fontSize: 11 }} />
        <YAxis type="number" dataKey={config.y_keys[0]} name={config.y_label ?? config.y_keys[0]} domain={["auto", "auto"]} {...t.axis} tickFormatter={tickFormatter(config)} label={{ value: config.y_label ?? config.y_keys[0], angle: -90, position: "insideLeft", fill: t.labelFill, fontSize: 11 }} />
        <Tooltip cursor={{ strokeDasharray: "3 3", stroke: "var(--border)" }} {...t.tooltip} />
        <Scatter
          data={config.data}
          fill={colors(config)[0]}
          fillOpacity={0.5}
          onClick={(data: unknown) => {
            const d = data as Record<string, string> | null;
            if (d && onElementClick) {
              onElementClick(config.x_key, { kind: "equality", value: d[config.x_key] });
            }
          }}
        />
      </ScatterChart>
    </ResponsiveContainer>
  );
}

function ComposedChartView({ config, height, t, onElementClick }: ViewProps) {
  const [firstKey, ...restKeys] = config.y_keys;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart
        data={config.data}
        onClick={(state) => {
          if (state && state.activeLabel && onElementClick) {
            onElementClick(config.x_key, { kind: "equality", value: state.activeLabel });
          }
        }}
        style={{ cursor: onElementClick ? "pointer" : "default" }}
      >
        {config.show_grid && <CartesianGrid {...t.grid} />}
        <XAxis dataKey={config.x_key} {...t.axis} label={config.x_label ? { value: config.x_label, position: "insideBottom", offset: -5, fill: t.labelFill, fontSize: 11 } : undefined} />
        <YAxis {...t.axis} tickFormatter={tickFormatter(config)} label={config.y_label ? { value: config.y_label, angle: -90, position: "insideLeft", fill: t.labelFill, fontSize: 11 } : undefined} />
        <Tooltip {...t.tooltip} formatter={tooltipFormatter(config)} />
        {config.show_legend && <Legend {...LEGEND_PROPS} wrapperStyle={{ fontSize: 12, color: t.legendColor }} />}
        {firstKey && <Bar dataKey={firstKey} fill={colors(config)[0]} radius={[4, 4, 0, 0]} maxBarSize={40} />}
        {restKeys.map((key, i) => (
          <Line key={key} type="monotone" dataKey={key} stroke={colors(config)[(i + 1) % colors(config).length]} dot={false} strokeWidth={2} />
        ))}
      </ComposedChart>
    </ResponsiveContainer>
  );
}

// PROMPT-6 fix 1: real heatmap renderer. The backend ships `data` as a flat
// list of {row, col, value} records; we pivot into a grid and color each cell
// by r ∈ [-1, +1] on a diverging scale.
function HeatmapView({ config }: { config: ChartConfig }) {
  const data = config.data as { row: string; col: string; value: number }[];
  const rows: string[] = [];
  const cols: string[] = [];
  const seenRow = new Set<string>();
  const seenCol = new Set<string>();
  for (const d of data) {
    if (!seenRow.has(d.row)) { rows.push(d.row); seenRow.add(d.row); }
    if (!seenCol.has(d.col)) { cols.push(d.col); seenCol.add(d.col); }
  }
  const lookup = new Map<string, number>();
  for (const d of data) lookup.set(`${d.row}|${d.col}`, d.value);

  // Diverging: two hues (chart blue / chart red) through the card colour at r = 0.
  // Capped at 60% so the foreground text stays readable in both themes.
  const cellColor = (r: number): string => {
    const x = Math.max(-1, Math.min(1, r));
    const pole = x >= 0 ? "var(--chart-1)" : "var(--chart-8)";
    return `color-mix(in oklch, ${pole} ${Math.round(Math.abs(x) * 60)}%, var(--card))`;
  };
  const headerCls = "text-foreground bg-muted border-border";
  const borderCls = "border-border";

  return (
    <div className="overflow-x-auto">
      <table className="text-xs border-collapse text-foreground">
        <thead>
          <tr>
            <th className={`sticky left-0 z-10 px-2.5 py-1.5 border ${headerCls}`}></th>
            {cols.map((c) => (
              <th key={c} className={`px-2.5 py-1.5 border font-medium whitespace-nowrap ${headerCls}`}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r}>
              <th className={`sticky left-0 z-10 px-2.5 py-1.5 border text-right font-medium whitespace-nowrap ${headerCls}`}>{r}</th>
              {cols.map((c) => {
                const v = lookup.get(`${r}|${c}`);
                if (v === undefined) {
                  return <td key={c} className={`px-2.5 py-1.5 border ${borderCls}`} />;
                }
                const isDiagonal = r === c;
                const bg = isDiagonal ? "var(--muted)" : cellColor(v);
                const fg = isDiagonal ? "var(--muted-foreground)" : "var(--foreground)";
                return (
                  <td
                    key={c}
                    className={`px-2.5 py-1.5 border text-center font-mono tabular-nums ${borderCls}`}
                    style={{ background: bg, color: fg, minWidth: 56 }}
                    title={`${r} × ${c}: ${v.toFixed(3)}`}
                  >
                    {v.toFixed(2)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const CONFIDENCE_DOT = { high: "bg-success", medium: "bg-warning", low: "bg-muted-foreground/60" };

export default function ChartRenderer({ config: original, height = 260, action, onElementClick, onEvidence, onDraft, sessionId }: Props) {
  // The user's on-the-spot switch; the server's copy (with any lint note) replaces it when saved.
  const [config, setConfig] = useState(original);
  if (!config.data || config.data.length === 0) return null;

  const t = T;
  const why = config.rationale;
  const options = swapOptions(original);

  async function switchType(type: ChartType) {
    setConfig({ ...config, chart_type: type, lint_warnings: [] });
    if (sessionId) {
      const saved = await sendChartFeedback(sessionId, config.id, type).catch(() => null);
      if (saved) setConfig(saved);
    }
  }

  return (
    <figure className="m-0 rounded-lg border border-border bg-card p-4 space-y-2 min-w-0">
      <figcaption className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-0.5">
          <h3 className="text-sm font-medium text-foreground">{config.title}</h3>
          {why && (
            <p className="text-xs text-muted-foreground">
              {why.n_rows.toLocaleString()} rows
              {why.confidence && (
                <span className="ml-2 inline-flex items-center gap-1">
                  <span className={`h-1.5 w-1.5 rounded-full ${CONFIDENCE_DOT[why.confidence]}`} aria-hidden />
                  {why.confidence} confidence
                </span>
              )}
            </p>
          )}
        </div>
        {typeof action === "function" ? action(config) : action}
      </figcaption>
      {!!config.lint_warnings?.length && (
        <p role="note" className="rounded-md border border-border bg-muted px-2.5 py-1.5 text-xs text-foreground">
          <span className="mr-1.5 inline-block h-1.5 w-1.5 rounded-full bg-warning align-middle" aria-hidden />
          {config.lint_warnings.join(" ")}
        </p>
      )}
      <div>
        {(() => {
          switch (config.chart_type) {
            case "LineChart":
              return <LineChartView config={config} height={height} t={t} onElementClick={onElementClick} />;
            case "BarChart":
            case "HistogramChart":
              return <BarChartView config={config} height={height} t={t} onElementClick={onElementClick} />;
            case "HorizontalBarChart":
              return <BarChartView config={config} height={height} t={t} horizontal onElementClick={onElementClick} />;
            case "PieChart":
              return <PieChartView config={config} height={height} t={t} onElementClick={onElementClick} />;
            case "DonutChart":
              return <PieChartView config={config} height={height} t={t} donut onElementClick={onElementClick} />;
            case "ScatterChart":
              return <ScatterChartView config={config} height={height} t={t} onElementClick={onElementClick} />;
            case "HeatmapChart":
              return <HeatmapView config={config} />;
            case "ComposedChart":
              return <ComposedChartView config={config} height={height} t={t} onElementClick={onElementClick} />;
            default:
              return <BarChartView config={config} height={height} t={t} onElementClick={onElementClick} />;
          }
        })()}
      </div>
      {(options.length > 1 || onDraft) && (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          {options.length > 1 && (
            <label className="inline-flex items-center gap-1.5 text-muted-foreground">
              Chart
              <select
                value={config.chart_type}
                onChange={(e) => void switchType(e.target.value as ChartType)}
                className="rounded-md border border-border bg-card px-1.5 py-1 text-foreground"
              >
                {options.map((o) => (
                  <option key={o} value={o}>{TYPE_NAME[o] ?? o}</option>
                ))}
              </select>
            </label>
          )}
          {onDraft && why && (
            <button
              type="button"
              onClick={() => onDraft(`Show ${why.y === "count" ? "rows" : why.y} by ${why.x} as a ${(TYPE_NAME[config.chart_type] ?? "bar").toLowerCase()} chart`)}
              className="rounded-md border border-border px-2 py-1 text-foreground hover:bg-accent"
            >
              Change variables…
            </button>
          )}
        </div>
      )}
      {why && (
        <details className="text-xs">
          <summary className="cursor-pointer text-muted-foreground hover:text-foreground">Why this chart</summary>
          <div className="mt-2 space-y-1.5 text-foreground">
            <p>{why.reason}</p>
            <p className="text-muted-foreground">
              Shows {why.aggregation === "none" ? "" : `${why.aggregation} of `}
              <span className="font-mono">{why.y}</span> by <span className="font-mono">{why.x}</span>
              {why.split && <> per <span className="font-mono">{why.split}</span></>} · {why.n_rows.toLocaleString()} rows ·
              rule <span className="font-mono">{why.rule}</span>
            </p>
            {why.code && (
              <pre className="overflow-x-auto rounded-md bg-muted p-2 font-mono text-[11px]">{why.code}</pre>
            )}
            {onEvidence && why.evidence_ids.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {why.evidence_ids.map((id) => (
                  <button
                    key={id}
                    type="button"
                    onClick={() => onEvidence(id)}
                    className="rounded-md border border-border px-2 py-0.5 text-foreground hover:bg-accent"
                  >
                    See evidence: <span className="font-mono">{id}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </details>
      )}
    </figure>
  );
}
