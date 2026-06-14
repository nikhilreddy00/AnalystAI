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

type Theme = "dark" | "light";

interface Props {
  config: ChartConfig;
  height?: number;
  theme?: Theme;
  onElementClick?: (key: string, value: FilterValue) => void;
}

// B1: Tableau 10 categorical palette — distinct from indigo/purple UI accents.
const FALLBACK_COLORS = [
  "#4E79A7", "#F28E2B", "#E15759", "#76B7B2", "#59A14F",
  "#EDC948", "#B07AA1", "#FF9DA7", "#9C755F", "#BAB0AC",
];

function colors(config: ChartConfig) {
  return config.colors.length > 0 ? config.colors : FALLBACK_COLORS;
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

// D1: dual-theme styles. Dark variant powers the chat surfaces (chart-as-dark-card
// on dark canvas). Light variant powers the new Power BI dashboard at /dashboard.
const THEMES = {
  dark: {
    cardClass:
      "rounded-2xl border border-zinc-700/40 bg-zinc-900/60 backdrop-blur-sm p-5 space-y-3 shadow-xl hover:border-indigo-500/20 transition-colors duration-300",
    titleClass: "text-sm font-semibold text-zinc-200",
    badgeClass: "text-xs text-zinc-600 bg-zinc-800 px-2 py-0.5 rounded-full",
    tooltip: {
      contentStyle: {
        background: "#18181b",
        border: "1px solid rgba(99,102,241,0.2)",
        borderRadius: 10,
        boxShadow: "0 8px 32px rgba(0,0,0,0.4)",
        fontSize: 12,
        color: "#e4e4e7",
      },
      itemStyle: { color: "#a5b4fc" },
      labelStyle: { color: "#71717a", marginBottom: 4 },
    },
    axis: {
      tick: { fill: "#71717a", fontSize: 11 },
      axisLine: { stroke: "#3f3f46" },
      tickLine: { stroke: "#3f3f46" },
    },
    grid: { strokeDasharray: "3 3", stroke: "#27272a", opacity: 0.6 },
    legendColor: "#71717a",
    labelFill: "#52525b",
  },
  light: {
    cardClass:
      "rounded-2xl border border-zinc-200 bg-white p-5 space-y-3 shadow-sm hover:border-zinc-300 transition-colors duration-300",
    titleClass: "text-sm font-semibold text-zinc-900",
    badgeClass: "text-xs text-zinc-500 bg-zinc-100 px-2 py-0.5 rounded-full",
    tooltip: {
      contentStyle: {
        background: "#ffffff",
        border: "1px solid #e4e4e7",
        borderRadius: 10,
        boxShadow: "0 8px 24px rgba(0,0,0,0.08)",
        fontSize: 12,
        color: "#18181b",
      },
      itemStyle: { color: "#4f46e5" },
      labelStyle: { color: "#71717a", marginBottom: 4 },
    },
    axis: {
      tick: { fill: "#71717a", fontSize: 11 },
      axisLine: { stroke: "#d4d4d8" },
      tickLine: { stroke: "#d4d4d8" },
    },
    grid: { strokeDasharray: "3 3", stroke: "#e4e4e7", opacity: 1 },
    legendColor: "#52525b",
    labelFill: "#71717a",
  },
} as const;

type ThemeStyles = (typeof THEMES)[Theme];

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
        {config.show_legend && <Legend {...LEGEND_PROPS} wrapperStyle={{ fontSize: 12, color: t.legendColor }} />}
        {config.y_keys.map((key, i) => (
          <Line
            key={key}
            type="monotone"
            dataKey={key}
            stroke={colors(config)[i % colors(config).length]}
            dot={false}
            strokeWidth={2.5}
            activeDot={{ r: 5, strokeWidth: 0 }}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

function BarChartView({ config, height, t, horizontal, onElementClick }: ViewProps & { horizontal?: boolean }) {
  const clrs = colors(config);
  const isMultiColor = clrs.length > 1 || config.y_keys.length === 1;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart
        data={config.data}
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
        <Tooltip {...t.tooltip} formatter={tooltipFormatter(config)} />
        {config.show_legend && <Legend {...LEGEND_PROPS} wrapperStyle={{ fontSize: 12, color: t.legendColor }} />}
        {config.y_keys.map((key, i) => (
          <Bar key={key} dataKey={key} fill={clrs[i % clrs.length]} radius={horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]} maxBarSize={48}>
            {isMultiColor && clrs.length > 1 && config.data.map((_, di) => (
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
          label={({ name, percent }) => `${name} ${((percent ?? 0) * 100).toFixed(0)}%`}
          labelLine={false}
          onClick={(data: any) => {
            if (data && onElementClick) {
              onElementClick(config.x_key, { kind: "equality", value: data.name || data[config.x_key] });
            }
          }}
        >
          {config.data.map((_, i) => (
            <Cell key={i} fill={pieColors[i % pieColors.length]} strokeWidth={0} />
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
      <ScatterChart style={{ cursor: onElementClick ? "pointer" : "default" }}>
        {config.show_grid && <CartesianGrid {...t.grid} />}
        <XAxis dataKey={config.x_key} name={config.x_label ?? config.x_key} {...t.axis} />
        <YAxis dataKey={config.y_keys[0]} name={config.y_label ?? config.y_keys[0]} {...t.axis} tickFormatter={tickFormatter(config)} />
        <Tooltip cursor={{ strokeDasharray: "3 3" }} {...t.tooltip} />
        <Scatter
          data={config.data}
          fill={colors(config)[0]}
          opacity={0.8}
          onClick={(data: any) => {
            if (data && onElementClick) {
              onElementClick(config.x_key, { kind: "equality", value: data[config.x_key] });
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
          <Line key={key} type="monotone" dataKey={key} stroke={colors(config)[(i + 1) % colors(config).length]} dot={false} strokeWidth={2.5} />
        ))}
      </ComposedChart>
    </ResponsiveContainer>
  );
}

// PROMPT-6 fix 1: real heatmap renderer. The backend ships `data` as a flat
// list of {row, col, value} records; we pivot into a grid and color each cell
// by interpolating r ∈ [-1, +1] from red → white → green.
function HeatmapView({ config, theme }: { config: ChartConfig; theme: Theme }) {
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

  const cellColor = (r: number): string => {
    // Clamp to [-1, +1], interpolate between the project's _NEGATIVE and _POSITIVE.
    const x = Math.max(-1, Math.min(1, r));
    // Anchors (matches Python _NEGATIVE / _POSITIVE):
    //   r = -1 → rgb(163, 45, 45)
    //   r =  0 → rgb(255, 255, 255)
    //   r = +1 → rgb(29, 158, 117)
    const lerp = (a: number, b: number, t: number) => Math.round(a + (b - a) * t);
    if (x < 0) {
      const t = -x;
      return `rgb(${lerp(255, 163, t)}, ${lerp(255, 45, t)}, ${lerp(255, 45, t)})`;
    }
    const t = x;
    return `rgb(${lerp(255, 29, t)}, ${lerp(255, 158, t)}, ${lerp(255, 117, t)})`;
  };

  const textColor = (r: number): string => (Math.abs(r) > 0.55 ? "#ffffff" : "#1f2937");
  const headerCls = theme === "light"
    ? "text-zinc-700 bg-zinc-50 border-zinc-200"
    : "text-zinc-300 bg-zinc-800/60 border-zinc-700";
  const borderCls = theme === "light" ? "border-zinc-200" : "border-zinc-700";

  return (
    <div className="overflow-x-auto">
      <table className={`text-xs border-collapse ${theme === "light" ? "text-zinc-800" : "text-zinc-200"}`}>
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
                const bg = isDiagonal ? (theme === "light" ? "#f4f4f5" : "#27272a") : cellColor(v);
                const fg = isDiagonal ? (theme === "light" ? "#a1a1aa" : "#71717a") : textColor(v);
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

export default function ChartRenderer({ config, height = 280, theme = "dark", onElementClick }: Props) {
  if (!config.data || config.data.length === 0) return null;

  const t = THEMES[theme];

  return (
    <div className={t.cardClass}>
      <div className="flex items-center justify-between">
        <h3 className={t.titleClass}>{config.title}</h3>
        <span className={t.badgeClass}>{config.chart_type.replace("Chart", "")}</span>
      </div>
      <div className="pt-1">
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
              return <HeatmapView config={config} theme={theme} />;
            case "ComposedChart":
              return <ComposedChartView config={config} height={height} t={t} onElementClick={onElementClick} />;
            default:
              return <BarChartView config={config} height={height} t={t} onElementClick={onElementClick} />;
          }
        })()}
      </div>
    </div>
  );
}
