"use client";

// D3 + D4 + D5: auto-built Power-BI-style dashboard with cross-filter wiring + edit mode.
// Pool = latest.charts ∪ pinned-from-chat (deduped by id).
// Layout (order + hidden) persists in localStorage so reorder/hide survives reloads.

import Link from "next/link";
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
  const [exportError, setExportError] = useState<string | null>(null);
  const [showFilters, setShowFilters] = useState(false); // phones only; always shown from lg

  const { filters, setFilter, clearFilters } = useDashboardFilter();
  const { status, rows, rowCount, detail } = useDashboardData();

  // All hooks must be called unconditionally on every render (Rules of Hooks).
  // Early returns for status/loading gates live BELOW this hook block.
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

  // === Early returns (after all hooks) ===
  if (status === "loading") {
    return (
      <div className="px-6 py-12 text-sm text-muted-foreground">Loading dashboard…</div>
    );
  }
  if (status === "empty" || (!latestLoading && !latest && visible.length === 0 && hidden.length === 0)) {
    return (
      <div className="mx-auto mt-10 max-w-md space-y-3 rounded-lg border border-dashed border-border bg-card p-8 text-center">
        <h2 className="text-base font-semibold text-foreground">No analysis yet</h2>
        <p className="text-sm text-muted-foreground">
          Run an overview in the chat and the dashboard builds itself here. Follow-up questions can add charts.
        </p>
        <Link
          href="/chat"
          className="inline-flex rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90"
        >
          Go to analysis
        </Link>
      </div>
    );
  }
  if (status === "too_large" || status === "error") {
    return (
      <div role="alert" className="mx-auto mt-10 max-w-md rounded-lg border border-border border-l-[3px] border-l-warning bg-card p-4 text-sm text-foreground">
        {detail ?? (status === "too_large" ? "Dataset too large for the interactive dashboard." : "Couldn't load dashboard data.")}
      </div>
    );
  }
  if (latestLoading) {
    return <div className="px-6 py-12 text-sm text-muted-foreground">Loading latest analysis…</div>;
  }

  const handleDownloadAll = async () => {
    if (!sectionRef.current) return;
    setBusy("section");
    try {
      const rawName = latest?.raw_query ? latest.raw_query.slice(0, 60) : "analysis";
      const today = new Date().toISOString().split("T")[0];
      const footer = `${rawName} · Generated by AnalystAI · ${today}`;
      const slug =
        rawName
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, "-")
          .replace(/^-|-$/g, "") || "analysis";
      const filename = `dashboard_${slug}_${today}`;
      await downloadElementAsPng(sectionRef.current, filename, { footer });
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
  const activeFilterCount = Object.keys(filters).length;
  const totalPool = visible.length + hidden.length;

  return (
    <section className="space-y-4 p-4 sm:p-6 max-w-7xl mx-auto print:p-0">
      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-3" data-html2canvas-ignore>
        <div className="min-w-0">
          <h1 className="text-lg font-semibold text-foreground">Dashboard</h1>
          <p className="text-xs text-muted-foreground mt-0.5 truncate">
            {latest?.raw_query ? `"${latest.raw_query}"` : "Most recent analysis"}
            {latest && ` — updated ${new Date(latest.updated_at).toLocaleString()}`}
          </p>
        </div>
        <div className="flex gap-2 flex-shrink-0 flex-wrap">
          <button
            onClick={() => setEditMode((v) => !v)}
            type="button"
            aria-pressed={editMode}
            className={`inline-flex items-center gap-1.5 text-sm px-3 py-1.5 rounded-md border ${
              editMode
                ? "bg-primary hover:bg-primary/90 text-primary-foreground border-primary/30"
                : "bg-card hover:bg-muted text-foreground border-border hover:border-border"
            }`}
          >
            {editMode ? <Check className="w-3.5 h-3.5" /> : <Pencil className="w-3.5 h-3.5" />}
            {editMode ? "Done editing" : "Edit layout"}
          </button>
          {editMode && (
            <button
              onClick={reset}
              type="button"
              className="inline-flex items-center gap-1.5 text-sm px-3 py-1.5 rounded-md border border-border bg-card text-foreground hover:bg-accent"
              title="Restore the default layout"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              Reset layout
            </button>
          )}
          <button
            type="button"
            onClick={async () => {
              setExportError(null);
              setBusy("pptx");
              try {
                await downloadLatestReportPptx();
              } catch (e) {
                setExportError(`PowerPoint download failed: ${e instanceof Error ? e.message : "unknown error"}`);
              } finally {
                setBusy(null);
              }
            }}
            disabled={busy !== null || !latest}
            className="inline-flex items-center gap-1.5 text-sm px-3 py-1.5 rounded-md border border-border bg-card text-foreground hover:bg-accent disabled:opacity-40"
          >
            <FileText className="w-3.5 h-3.5" />
            {busy === "pptx" ? "Building..." : "PowerPoint"}
          </button>
          <button
            type="button"
            onClick={handleDownloadAll}
            disabled={busy !== null}
            className="inline-flex items-center gap-1.5 text-sm px-3 py-1.5 rounded-md border border-primary bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-40"
          >
            <Download className="w-3.5 h-3.5" />
            {busy === "section" ? "Rendering..." : "PNG"}
          </button>
        </div>
        {exportError && (
          <p role="alert" className="basis-full text-xs text-destructive">{exportError}</p>
        )}
      </div>

      {/* Snapshot region restructure (Gap 3):
          - FilterSidebar lives OUTSIDE sectionRef (and is also marked
            data-html2canvas-ignore as belt-and-suspenders).
          - sectionRef now wraps ONLY the right column (KPIs + chart grid).
          - The cross-filter chip row renders conditionally INSIDE sectionRef
            when filters are active. Interactive chrome (X icons, Reset button)
            is marked data-html2canvas-ignore so the PNG sees flat chip labels. */}
      <div className="flex flex-col gap-4 lg:flex-row">
        <button
          type="button"
          onClick={() => setShowFilters((v) => !v)}
          aria-expanded={showFilters}
          aria-controls="dashboard-filters"
          className="inline-flex items-center gap-1.5 self-start rounded-md border border-border bg-card px-3 py-1.5 text-sm text-foreground hover:bg-accent lg:hidden"
          data-html2canvas-ignore
        >
          <Filter className="h-3.5 w-3.5" />
          {showFilters ? "Hide filters" : "Filters"}
          {activeFilterCount > 0 && <span className="font-mono text-xs text-muted-foreground">{activeFilterCount}</span>}
        </button>
        <div id="dashboard-filters" data-html2canvas-ignore className={`lg:block lg:flex-shrink-0 ${showFilters ? "block" : "hidden"}`}>
          <FilterSidebar />
        </div>
        <div ref={sectionRef} className="flex-1 min-w-0 space-y-4">
          {/* Cross-filter chip row — only in DOM (and PNG) when filters active */}
          {activeFilterCount > 0 && (
            <div className="flex items-center justify-between gap-3 rounded-lg border border-border bg-card p-3 flex-wrap">
              <div className="flex items-center gap-2 text-sm text-foreground flex-wrap">
                <Filter className="w-4 h-4 text-primary flex-shrink-0" />
                <span className="font-semibold">Filtered by</span>
                <div className="flex items-center gap-1.5 flex-wrap pl-1">
                  {Object.entries(filters).map(([key, f]) => (
                    <button
                      key={key}
                      onClick={() => setFilter(key, null)}
                      className="inline-flex items-center gap-1.5 bg-brand-soft border border-primary/30 text-primary text-xs font-medium px-2.5 py-1 rounded-full hover:bg-brand-soft transition-colors"
                      title="Click to remove this filter"
                    >
                      <span className="text-primary">{key}</span>
                      <span>:</span>
                      <span className="font-semibold">{formatFilterChip(f)}</span>
                      <span data-html2canvas-ignore className="inline-flex">
                        <X className="w-3 h-3" />
                      </span>
                    </button>
                  ))}
                </div>
              </div>
              <button
                onClick={clearFilters}
                data-html2canvas-ignore
                className="inline-flex items-center gap-1.5 text-xs bg-muted hover:bg-muted border border-border text-foreground px-3 py-1.5 rounded-lg font-semibold transition-colors"
              >
                <RotateCcw className="w-3 h-3" />
                Reset
              </button>
            </div>
          )}

          {/* KPI row */}
          {hasBriefing && latest?.briefing && <KPISection briefing={latest.briefing} />}

          {/* Records indicator — excluded from PNG */}
          {hasActiveFilters && (
            <div data-html2canvas-ignore className="text-xs text-muted-foreground mb-2 px-6">
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
              onMoveUp={(id) => moveUp(id, visibleOrder)}
              onMoveDown={(id) => moveDown(id, visibleOrder)}
              onHide={(id) => hide(id, visibleOrder)}
            />
          ) : (
            <div className="rounded-lg border border-dashed border-border bg-card p-8 text-center text-sm text-muted-foreground">
              {totalPool > 0
                ? "All charts are hidden. Restore some below in edit mode."
                : "No charts on the latest analysis. Run an overview in the chat to populate the grid."}
            </div>
          )}

          {/* Hidden-chart restore panel — only shows in edit mode, excluded from PNG */}
          {editMode && hidden.length > 0 && (
            <div
              className="rounded-lg border border-border bg-card overflow-hidden"
              data-html2canvas-ignore
            >
              <div className="px-5 py-3 border-b border-border flex items-center gap-2 text-sm font-semibold text-foreground">
                <EyeOff className="w-4 h-4 text-muted-foreground" />
                Hidden charts ({hidden.length})
              </div>
              <div className="p-5 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                {hidden.map((c) => (
                  <button
                    key={c.id}
                    onClick={() => restore(c.id, visibleOrder)}
                    className="text-left text-xs bg-muted hover:bg-brand-soft border border-border hover:border-primary/30 text-foreground hover:text-primary px-3 py-2.5 rounded-lg transition-colors flex items-center gap-2"
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
            } ${editMode ? "ring-2 ring-primary/40 rounded-lg" : ""}`}
          >
            {/* Chart adornments: unfiltered pill + count sublabel */}
            {chart.showUnfilteredPill && (
              <span className="inline-block text-[10px] uppercase tracking-wide bg-muted text-muted-foreground px-2 py-0.5 rounded-full mb-1 ml-1">
                shows unfiltered data
              </span>
            )}
            {chart.showCountSublabel && (
              <p className="text-[11px] text-muted-foreground mb-1 ml-1">
                Filtered: row count
              </p>
            )}

            <ChartRenderer
              config={chartConfig}
              height={280}
              onElementClick={editMode ? undefined : (key, value) => onElementClick(key, value)}
              action={
                !editMode && (
                  <button
                    type="button"
                    onClick={() => onDownloadChart(chart.id, chart.title)}
                    disabled={busy !== null}
                    data-html2canvas-ignore
                    aria-label={`Download ${chart.title} as PNG`}
                    className="inline-flex flex-shrink-0 items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-40"
                  >
                    <Download className="h-3.5 w-3.5" />
                    {busy === chart.id ? "…" : "PNG"}
                  </button>
                )
              }
            />

            {/* Edit-mode overlay: move/hide controls always visible */}
            {editMode && (
              <div
                className="absolute top-3 right-3 flex gap-1 bg-card border border-border rounded-md shadow-sm p-1"
                data-html2canvas-ignore
              >
                <button
                  onClick={() => onMoveUp(chart.id)}
                  disabled={isFirst}
                  title="Move up"
                  aria-label="Move up"
                  className="w-7 h-7 inline-flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-muted disabled:text-muted-foreground disabled:hover:bg-transparent disabled:cursor-not-allowed rounded transition-colors"
                >
                  <ArrowUp className="w-3.5 h-3.5" />
                </button>
                <button
                  onClick={() => onMoveDown(chart.id)}
                  disabled={isLast}
                  title="Move down"
                  aria-label="Move down"
                  className="w-7 h-7 inline-flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-muted disabled:text-muted-foreground disabled:hover:bg-transparent disabled:cursor-not-allowed rounded transition-colors"
                >
                  <ArrowDown className="w-3.5 h-3.5" />
                </button>
                <button
                  onClick={() => onHide(chart.id)}
                  title="Hide from dashboard"
                  aria-label="Hide from dashboard"
                  className="w-7 h-7 inline-flex items-center justify-center text-muted-foreground hover:text-destructive hover:bg-destructive/10 rounded transition-colors"
                >
                  <EyeOff className="w-3.5 h-3.5" />
                </button>
              </div>
            )}

          </div>
        );
      })}
    </div>
  );
}
