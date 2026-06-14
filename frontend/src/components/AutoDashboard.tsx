"use client";

// D3 + D4 + D5: auto-built Power-BI-style dashboard with cross-filter wiring + edit mode.
// Pool = latest.charts ∪ pinned-from-chat (deduped by id).
// Layout (order + hidden) persists in localStorage so reorder/hide survives reloads.

import { useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowUp,
  Check,
  Download,
  EyeOff,
  FileText,
  Filter,
  Pencil,
  Plus,
  RotateCcw,
  Sparkles,
  X,
} from "lucide-react";
import {
  downloadLatestReportPptx,
  getDashboard,
  getLatestAnalysis,
  type LatestAnalysis,
} from "@/lib/api";
import { downloadElementAsPng } from "@/lib/downloadAsPng";
import {
  DashboardFilterProvider,
  applyFilters,
  useDashboardFilter,
  type FilterValue,
} from "@/context/DashboardFilterContext";
import { FilterSidebar } from "@/components/FilterSidebar";

function formatFilterChip(f: FilterValue): string {
  switch (f.kind) {
    case "equality":
      return String(f.value);
    case "in":
      return f.values.length <= 3
        ? f.values.map(String).join(", ")
        : `${f.values.length} selected`;
    case "range":
      return `${f.min}–${f.max}`;
    case "date_range":
      return `${f.from} → ${f.to}`;
  }
}
import { useDashboardData } from "@/context/DashboardDataContext";
import { aggregateForChart } from "@/lib/aggregateForChart";
import { resolveLayout, useDashboardLayout } from "@/hooks/useDashboardLayout";
import ChartRenderer from "./ChartRenderer";
import KPISection from "./KPISection";
import type { ChartConfig } from "@/types";

export default function AutoDashboard() {
  return (
    <DashboardFilterProvider>
      <AutoDashboardInner />
    </DashboardFilterProvider>
  );
}

function AutoDashboardInner() {
  const sectionRef = useRef<HTMLDivElement>(null);
  const chartRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const [busy, setBusy] = useState<string | null>(null);
  const [editMode, setEditMode] = useState(false);

  const { filters, setFilter, clearFilters } = useDashboardFilter();
  const { status, rows, rowCount, detail } = useDashboardData();

  // NEW: Early status gates from useDashboardData() — MUST appear before latestLoading check
  if (status === "loading") {
    return (
      <div className="px-6 py-12 text-sm text-zinc-500">Loading dashboard…</div>
    );
  }
  if (status === "empty") {
    return (
      <div className="px-6 py-12 text-sm text-zinc-500">
        Run an analysis first to populate the dashboard.
      </div>
    );
  }
  if (status === "too_large") {
    return (
      <div className="px-6 py-12 text-sm text-zinc-500">
        {detail ?? "Dataset too large for the interactive dashboard."}
      </div>
    );
  }
  if (status === "error") {
    return (
      <div className="px-6 py-12 text-sm text-zinc-500">
        {detail ?? "Couldn't load dashboard data."}
      </div>
    );
  }

  const filteredRows = useMemo(
    () => applyFilters(rows, filters),
    [rows, filters],
  );
  const hasActiveFilters = Object.keys(filters).length > 0;

  const { layout, moveUp, moveDown, hide, restore, reset } = useDashboardLayout();

  const { data: latest, isLoading: latestLoading } = useQuery<LatestAnalysis | null>({
    queryKey: ["dashboard-latest"],
    queryFn: getLatestAnalysis,
    refetchInterval: 30_000,
  });
  const { data: pinned } = useQuery<ChartConfig[]>({
    queryKey: ["dashboard-pinned"],
    queryFn: () => getDashboard("default"),
    refetchInterval: 30_000,
  });

  // Merge latest.charts + manually-pinned into a single deduped pool, then apply user layout.
  const { visible, hidden, visibleOrder } = useMemo(() => {
    const pool: ChartConfig[] = [];
    const seen = new Set<string>();
    for (const c of latest?.charts ?? []) {
      if (!seen.has(c.id)) { pool.push(c); seen.add(c.id); }
    }
    for (const c of pinned ?? []) {
      if (!seen.has(c.id)) { pool.push(c); seen.add(c.id); }
    }
    return resolveLayout(pool, layout);
  }, [latest, pinned, layout]);

  if (latestLoading) {
    return (
      <div className="border border-zinc-200 bg-white rounded-2xl p-8 m-6 shadow-sm">
        <p className="text-zinc-500 text-sm">Loading latest analysis...</p>
      </div>
    );
  }

  if (!latest && visible.length === 0 && hidden.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center text-center space-y-5 p-12 m-6 rounded-2xl border border-dashed border-zinc-300 bg-white">
        <div className="w-16 h-16 rounded-2xl bg-indigo-50 border border-indigo-200 flex items-center justify-center text-indigo-600">
          <Sparkles className="w-7 h-7" />
        </div>
        <div className="max-w-md">
          <h3 className="text-zinc-900 font-bold text-lg mb-1">No analysis yet</h3>
          <p className="text-zinc-600 text-sm">
            Run an overview in the chat to auto-build a dashboard here. Targeted follow-ups can then refine specific cards.
          </p>
        </div>
        <a
          href="/chat"
          className="bg-zinc-900 hover:bg-zinc-800 text-white px-6 py-3 rounded-xl text-sm font-semibold transition-colors shadow-md active:scale-95"
        >
          Go to chat →
        </a>
      </div>
    );
  }

  const handleDownloadAll = async () => {
    if (!sectionRef.current) return;
    setBusy("section");
    try {
      await downloadElementAsPng(sectionRef.current, "auto-dashboard");
    } finally {
      setBusy(null);
    }
  };

  const handleDownloadChart = async (chartId: string, title: string) => {
    const el = chartRefs.current.get(chartId);
    if (!el) return;
    setBusy(chartId);
    try {
      await downloadElementAsPng(el, title);
    } finally {
      setBusy(null);
    }
  };

  const hasBriefing = !!latest?.briefing;
  const hasDetails = !!latest?.insight || !!latest?.expert_review;
  const activeFilterCount = Object.keys(filters).length;
  const totalPool = visible.length + hidden.length;

  return (
    <section className="space-y-5 p-6 max-w-7xl mx-auto print:p-0">
      {/* Header */}
      <div className="flex items-start justify-between gap-4" data-html2canvas-ignore>
        <div className="min-w-0">
          <h2 className="text-xl font-bold text-zinc-900 flex items-center gap-2">
            <Sparkles className="w-5 h-5 text-indigo-600" />
            Auto dashboard
          </h2>
          <p className="text-xs text-zinc-500 mt-0.5 truncate">
            {latest?.raw_query ? `"${latest.raw_query}"` : "Most recent analysis"}
            {latest && ` — updated ${new Date(latest.updated_at).toLocaleString()}`}
          </p>
        </div>
        <div className="flex gap-2 flex-shrink-0 flex-wrap">
          <button
            onClick={() => setEditMode((v) => !v)}
            className={`inline-flex items-center gap-1.5 text-xs px-4 py-2 rounded-xl font-semibold transition-colors shadow-sm border ${
              editMode
                ? "bg-indigo-600 hover:bg-indigo-500 text-white border-indigo-600"
                : "bg-white hover:bg-zinc-50 text-zinc-800 border-zinc-200 hover:border-zinc-300"
            }`}
          >
            {editMode ? <Check className="w-3.5 h-3.5" /> : <Pencil className="w-3.5 h-3.5" />}
            {editMode ? "Done editing" : "Edit layout"}
          </button>
          {editMode && (
            <button
              onClick={reset}
              className="inline-flex items-center gap-1.5 text-xs bg-white hover:bg-zinc-50 border border-zinc-200 text-zinc-700 px-4 py-2 rounded-xl font-semibold transition-colors shadow-sm"
              title="Restore the default layout"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              Reset layout
            </button>
          )}
          <button
            onClick={async () => {
              setBusy("pptx");
              try {
                await downloadLatestReportPptx();
              } catch (e) {
                alert(`PowerPoint download failed: ${e instanceof Error ? e.message : "unknown error"}`);
              } finally {
                setBusy(null);
              }
            }}
            disabled={busy !== null || !latest}
            className="inline-flex items-center gap-1.5 text-xs bg-white hover:bg-zinc-50 border border-zinc-200 hover:border-zinc-300 disabled:opacity-40 text-zinc-800 px-4 py-2 rounded-xl font-semibold transition-colors shadow-sm"
          >
            <FileText className="w-3.5 h-3.5" />
            {busy === "pptx" ? "Building..." : "PowerPoint"}
          </button>
          <button
            onClick={handleDownloadAll}
            disabled={busy !== null}
            className="inline-flex items-center gap-1.5 text-xs bg-zinc-900 hover:bg-zinc-800 disabled:opacity-40 text-white px-4 py-2 rounded-xl font-semibold transition-colors shadow-md"
          >
            <Download className="w-3.5 h-3.5" />
            {busy === "section" ? "Rendering..." : "PNG"}
          </button>
        </div>
      </div>

      {/* The snapshot region */}
      <div ref={sectionRef} className="space-y-4">
        {/* Filter row */}
        <div
          className="flex items-center justify-between gap-3 rounded-2xl border border-zinc-200 bg-white p-4 shadow-sm flex-wrap"
          data-html2canvas-ignore
        >
          <div className="flex items-center gap-2 text-sm text-zinc-700 flex-wrap">
            <Filter className="w-4 h-4 text-indigo-600 flex-shrink-0" />
            <span className="font-semibold">Cross-filter</span>
            {activeFilterCount === 0 ? (
              <span className="text-xs text-zinc-500 hidden sm:inline">
                · click a bar or pie slice to filter all other charts
              </span>
            ) : (
              <div className="flex items-center gap-1.5 flex-wrap pl-1">
                {Object.entries(filters).map(([key, f]) => (
                  <button
                    key={key}
                    onClick={() => setFilter(key, null)}
                    className="inline-flex items-center gap-1.5 bg-indigo-50 border border-indigo-200 text-indigo-700 text-xs font-medium px-2.5 py-1 rounded-full hover:bg-indigo-100 transition-colors"
                    title="Click to remove this filter"
                  >
                    <span className="text-indigo-500">{key}</span>
                    <span>:</span>
                    <span className="font-semibold">{formatFilterChip(f)}</span>
                    <X className="w-3 h-3" />
                  </button>
                ))}
              </div>
            )}
          </div>
          <button
            onClick={clearFilters}
            disabled={activeFilterCount === 0}
            className="inline-flex items-center gap-1.5 text-xs bg-zinc-50 hover:bg-zinc-100 disabled:hover:bg-zinc-50 border border-zinc-200 text-zinc-700 disabled:text-zinc-400 disabled:cursor-not-allowed px-3 py-1.5 rounded-lg font-semibold transition-colors"
          >
            <RotateCcw className="w-3 h-3" />
            Reset
          </button>
        </div>

        <div className="flex gap-4">
          <FilterSidebar />
          <div className="flex-1 min-w-0 space-y-4">
            {/* KPI row */}
            {hasBriefing && latest?.briefing && <KPISection briefing={latest.briefing} />}

            {/* Records indicator */}
            {hasActiveFilters && (
              <div className="text-xs text-zinc-500 mb-2 px-6">
                Showing {filteredRows.length.toLocaleString()} of {rowCount.toLocaleString()} records
              </div>
            )}

            {/* Chart grid */}
            {visible.length > 0 ? (
              <ChartGrid
                charts={visible}
                filteredRows={filteredRows}
                hasActiveFilters={hasActiveFilters}
                onElementClick={setFilter}
                chartRefs={chartRefs}
                busy={busy}
                onDownloadChart={handleDownloadChart}
                editMode={editMode}
                visibleOrder={visibleOrder}
                onMoveUp={(id) => moveUp(id, visibleOrder)}
                onMoveDown={(id) => moveDown(id, visibleOrder)}
                onHide={(id) => hide(id, visibleOrder)}
              />
            ) : (
              <div className="rounded-2xl border border-dashed border-zinc-300 bg-white p-8 text-center text-sm text-zinc-500">
                {totalPool > 0
                  ? "All charts are hidden. Restore some below in edit mode."
                  : "No charts on the latest analysis. Run an overview in the chat to populate the grid."}
              </div>
            )}

            {/* Hidden-chart restore panel — only shows in edit mode */}
            {editMode && hidden.length > 0 && (
              <div
                className="rounded-2xl border border-zinc-200 bg-white shadow-sm overflow-hidden"
                data-html2canvas-ignore
              >
                <div className="px-5 py-3 border-b border-zinc-200 flex items-center gap-2 text-sm font-semibold text-zinc-800">
                  <EyeOff className="w-4 h-4 text-zinc-500" />
                  Hidden charts ({hidden.length})
                </div>
                <div className="p-5 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                  {hidden.map((c) => (
                    <button
                      key={c.id}
                      onClick={() => restore(c.id, visibleOrder)}
                      className="text-left text-xs bg-zinc-50 hover:bg-indigo-50 border border-zinc-200 hover:border-indigo-300 text-zinc-700 hover:text-indigo-700 px-3 py-2.5 rounded-lg transition-colors flex items-center gap-2"
                    >
                      <Plus className="w-3 h-3 flex-shrink-0" />
                      <span className="truncate">{c.title}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

      </div>
    </section>
  );
}

function ChartGrid({
  charts,
  filteredRows,
  hasActiveFilters,
  onElementClick,
  chartRefs,
  busy,
  onDownloadChart,
  editMode,
  visibleOrder,
  onMoveUp,
  onMoveDown,
  onHide,
}: {
  charts: ChartConfig[];
  filteredRows: Record<string, unknown>[];
  hasActiveFilters: boolean;
  onElementClick: (key: string, value: FilterValue) => void;
  chartRefs: React.RefObject<Map<string, HTMLDivElement>>;
  busy: string | null;
  onDownloadChart: (id: string, title: string) => void;
  editMode: boolean;
  visibleOrder: string[];
  onMoveUp: (id: string) => void;
  onMoveDown: (id: string) => void;
  onHide: (id: string) => void;
}) {
  const derivedCharts = useMemo(
    () =>
      charts.map((c) => {
        const agg = aggregateForChart(filteredRows, c);
        const renderedData = agg.mode === "unfiltered" ? c.data : agg.data;
        const showUnfilteredPill = agg.mode === "unfiltered" && hasActiveFilters;
        const showCountSublabel = agg.mode === "count" && hasActiveFilters;
        return { ...c, renderedData, showUnfilteredPill, showCountSublabel };
      }),
    [charts, filteredRows, hasActiveFilters],
  );

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      {derivedCharts.map((chart, idx) => {
        const isFirst = idx === 0;
        const isLast = idx === derivedCharts.length - 1;
        const chartConfig = { ...chart, data: chart.renderedData };
        return (
          <div
            key={chart.id}
            ref={(el) => {
              if (!chartRefs.current) return;
              if (el) chartRefs.current.set(chart.id, el);
              else chartRefs.current.delete(chart.id);
            }}
            className={`relative group/chart ${
              chart.layout_hint === "full_width" ? "lg:col-span-2" : ""
            } ${editMode ? "ring-2 ring-indigo-200 rounded-2xl" : ""}`}
          >
            {/* Chart adornments: unfiltered pill + count sublabel */}
            {chart.showUnfilteredPill && (
              <span className="inline-block text-[10px] uppercase tracking-wide bg-zinc-100 text-zinc-600 px-2 py-0.5 rounded-full mb-1 ml-1">
                shows unfiltered data
              </span>
            )}
            {chart.showCountSublabel && (
              <p className="text-[11px] text-zinc-500 mb-1 ml-1">
                Filtered: row count
              </p>
            )}

            <ChartRenderer
              config={chartConfig}
              height={280}
              theme="light"
              onElementClick={editMode ? undefined : (key, value) => onElementClick(key, value)}
            />

            {/* Edit-mode overlay: move/hide controls always visible */}
            {editMode && (
              <div
                className="absolute top-3 right-3 flex gap-1 bg-white border border-zinc-200 rounded-lg shadow-md p-1"
                data-html2canvas-ignore
              >
                <button
                  onClick={() => onMoveUp(chart.id)}
                  disabled={isFirst}
                  title="Move up"
                  className="w-7 h-7 inline-flex items-center justify-center text-zinc-600 hover:text-zinc-900 hover:bg-zinc-100 disabled:text-zinc-300 disabled:hover:bg-transparent disabled:cursor-not-allowed rounded transition-colors"
                >
                  <ArrowUp className="w-3.5 h-3.5" />
                </button>
                <button
                  onClick={() => onMoveDown(chart.id)}
                  disabled={isLast}
                  title="Move down"
                  className="w-7 h-7 inline-flex items-center justify-center text-zinc-600 hover:text-zinc-900 hover:bg-zinc-100 disabled:text-zinc-300 disabled:hover:bg-transparent disabled:cursor-not-allowed rounded transition-colors"
                >
                  <ArrowDown className="w-3.5 h-3.5" />
                </button>
                <button
                  onClick={() => onHide(chart.id)}
                  title="Hide from dashboard"
                  className="w-7 h-7 inline-flex items-center justify-center text-zinc-600 hover:text-red-600 hover:bg-red-50 rounded transition-colors"
                >
                  <EyeOff className="w-3.5 h-3.5" />
                </button>
              </div>
            )}

            {/* Standard hover PNG button — hidden in edit mode (overlap with overlay) */}
            {!editMode && (
              <button
                onClick={() => onDownloadChart(chart.id, chart.title)}
                disabled={busy !== null}
                data-html2canvas-ignore
                className="absolute top-3 right-3 opacity-0 group-hover/chart:opacity-100 transition-all duration-200 text-[10px] inline-flex items-center gap-1 bg-white hover:bg-zinc-50 text-zinc-700 border border-zinc-200 px-2.5 py-1.5 rounded-lg font-semibold shadow-md disabled:opacity-40"
              >
                <Download className="w-3 h-3" />
                {busy === chart.id ? "..." : "PNG"}
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}
