"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ChartConfig } from "@/types";
import type { FilterValue } from "@/context/DashboardFilterContext";

interface Props {
  config: ChartConfig;
  height?: number;
  /** Optional slot rendered top-right of the card (e.g. a pin button). */
  action?: React.ReactNode;
  onElementClick?: (key: string, value: FilterValue) => void;
}

// Phase 3.5: the categorical slots live in globals.css (--chart-1..8, validated for both
// themes), so charts re-colour with the theme. Assign in order, never cycle past 8.
const SERIES = Array.from({ length: 8 }, (_, i) => `var(--chart-${i + 1})`);

// The backend fills `colors` even when they mean nothing: its Tableau-10 cycle, neutral
// teal and indigo (mirrors _PALETTE / _NEUTRAL_TEAL in agents/visualization_agent.py).
// Those give way to the theme slots; anything else (target green/red, ordinal ramp,
// below-average) encodes meaning and is kept.
const GENERIC = new Set([
  "#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
  "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac", "#6366f1",
]);
function meaningful(config: ChartConfig) {
  return config.colors.some((c) => !GENERIC.has(c.toLowerCase()));
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
        onClick={(state) => {
          if (state && state.activeLabel && onElementClick) {
            onElementClick(config.x_key, { kind: "equality", value: state.activeLabel });
          }
        }}
        style={{ cursor: onElementClick ? "pointer" : "default" }}
      >
        {config.show_grid && <CartesianGrid {...t.grid} />}
        <XAxis dataKey={config.x_key} {...t.axis} label={config.x_label ? { value: config.x_label, position: "insideBottom", fill: t.labelFill, fontSize: 11 } : undefined} />
        <YAxis {...t.axis} tickFormatter={tickFormatter(config)} label={config.y_label ? { value: config.y_label, angle: -90, position: "insideLeft", fill: t.labelFill, fontSize: 11 } : undefined} />
        <Tooltip {...t.tooltip} formatter={tooltipFormatter(config)} />
        {config.show_legend && config.y_keys.length > 1 && <Legend {...LEGEND_PROPS} wrapperStyle={{ fontSize: 12, color: t.legendColor }} />}
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
            <XAxis type="number" {...t.axis} tickFormatter={tickFormatter(config)} label={config.y_label ? { value: config.y_label, position: "insideBottom", offset: -5, fill: t.labelFill, fontSize: 11 } : undefined} />
            <YAxis dataKey={config.x_key} type="category" {...t.axis} width={110} label={config.x_label ? { value: config.x_label, angle: -90, position: "insideLeft", fill: t.labelFill, fontSize: 11 } : undefined} />
          </>
        ) : (
          <>
            <XAxis dataKey={config.x_key} {...t.axis} label={config.x_label ? { value: config.x_label, position: "insideBottom", offset: -5, fill: t.labelFill, fontSize: 11 } : undefined} />
            <YAxis {...t.axis} tickFormatter={tickFormatter(config)} label={config.y_label ? { value: config.y_label, angle: -90, position: "insideLeft", fill: t.labelFill, fontSize: 11 } : undefined} />
          </>
        )}
        <Tooltip {...t.tooltip} cursor={{ fill: "var(--muted)" }} formatter={tooltipFormatter(config)} />
        {config.show_legend && config.y_keys.length > 1 && <Legend {...LEGEND_PROPS} wrapperStyle={{ fontSize: 12, color: t.legendColor }} />}
        {config.y_keys.map((key, i) => (
          <Bar key={key} dataKey={key} fill={clrs[i % clrs.length]} radius={horizontal ? [0, 3, 3, 0] : [3, 3, 0, 0]} maxBarSize={44}>
            {perBar && config.data.map((_, di) => (
              <Cell key={di} fill={clrs[di % clrs.length]} />
            ))}
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
        <YAxis type="number" dataKey={config.y_keys[0]} name={config.y_label ?? config.y_keys[0]} domain={["auto", "auto"]} {...t.axis} tickFormatter={tickFormatter(config)} />
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

export default function ChartRenderer({ config, height = 260, action, onElementClick }: Props) {
  if (!config.data || config.data.length === 0) return null;

  const t = T;

  return (
    <figure className="m-0 rounded-lg border border-border bg-card p-4 space-y-2 min-w-0">
      <figcaption className="flex items-start justify-between gap-3">
        <h3 className="text-sm font-medium text-foreground">{config.title}</h3>
        {action}
      </figcaption>
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
    </figure>
  );
}
