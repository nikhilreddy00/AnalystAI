# Dashboard Data Foundation — Design (Spec 1 of Gap 2)

**Status:** Approved for plan-writing
**Date:** 2026-06-11
**Scope:** Gap 2 (Dashboard as Interactive Workspace), Spec 1 of 3. This spec lays the data foundation: ship raw cleaned rows + column_roles to the frontend, set up a `DashboardDataContext`, refactor the existing cross-filter to re-aggregate from filtered raw rows, and delete the residual report-text panel.

Spec 2 (filter sidebar + KPI delta) and Spec 3 (per-chart customization + add-chart + server-side layout persistence) are explicit follow-ups.

---

## 1. Goal & Non-Goals

### Goal
Pivot the dashboard's filtering model from "filter aggregated chart-data arrays" to "filter raw rows, then re-aggregate each chart client-side." This is the architectural foundation that lets Spec 2 ship a working filter sidebar and KPI delta. Strip the report-text content (executive summary / expert review / methodology) off the dashboard so it is purely chart + filter + KPI — exploration only, not presentation.

### Non-goals (deferred to Spec 2 / Spec 3)
- Auto-built filter sidebar with pill toggles, range sliders, date pickers, binary toggles — Spec 2.
- KPI delta ("Survival Rate: 74.2% (vs 38.4% overall)") — Spec 2.
- Sum / mean aggregation inference beyond row counts — Spec 2.
- Per-chart "..." menu (change type, change X, change Y) — Spec 3.
- "Add chart" button in edit mode — Spec 3.
- Server-side layout persistence (currently localStorage) — Spec 3.
- Web worker for filtering large datasets — future, flagged when the row cap becomes a real constraint.
- Streaming the dashboard payload (NDJSON, server-sent events) — future.

---

## 2. Architecture Overview

### Topology

```
Backend:  GET /dashboard/latest/data
              ↓ resolves current user's latest completed session
              ↓ reads session.cleaned_data_path (parquet on disk from Gap 6)
              ↓ runs classify_columns(df) on the fly
              ↓ returns { session_id, rows: [{...}, ...], column_roles: {...}, row_count: N }
Frontend: <DashboardDataProvider>           (loads payload on mount, holds in state)
              ↓
          <DashboardFilterContext.Provider>  (existing cross-filter — repurposed)
              ↓
          <AutoDashboard>
              ↓
            filteredRows = filterRows(rows, filters)
              ↓ per chart:
            aggregateForChart(filteredRows, chartConfig)
              ↓
            <ChartRenderer config={{ ...chart, data: renderedData }} />
```

### Key decisions
- **Transport: JSON.** A new `/dashboard/latest/data` endpoint returns the cleaned rows as a JSON array. Avoids a parquet parser dependency in the browser. Re-running `classify_columns` server-side is fast (~ms) and keeps the wire format independent of any backend schema decisions.
- **Endpoint placement:** sibling of the existing `/dashboard/latest` route. Both are called by the dashboard page on initial load.
- **Aggregator: frontend inference (count-only for Spec 1).** A small client-side `aggregateForChart(rows, chart)` utility re-aggregates `BarChart`, `HorizontalBarChart`, `PieChart`, `DonutChart`, `LineChart`, `HistogramChart` as groupby-by-`x_key` + row count. `ScatterChart` passes filtered rows through. `ComposedChart`, `HeatmapChart`, and any chart without `x_key` fall back to the original pre-aggregated `chart.data` with a small "shows unfiltered data" pill.
- **No new contexts beyond `DashboardDataContext`.** The existing `DashboardFilterContext` (cross-filter state) is repurposed: same `{column: value}` shape, but `applyFilters` is replaced with `filterRows` that filters raw row dicts instead of aggregated arrays.
- **Row cap: 25,000.** JSON serialization of 25K × 30 columns is ~5 MB; above that the round-trip + main-thread filter cost becomes noticeable. Larger datasets get a 422 response with a friendly message redirecting to chat.
- **Report cleanup:** the collapsible "Analysis details" block in `AutoDashboard.tsx` is deleted along with its imports of `BriefingPanel`, `InsightPanel`, `ExpertReviewPanel`.

### What does NOT change in Spec 1
- The pinned-charts API (`/dashboard/pin`, `/dashboard/{chart_id}` DELETE) — untouched.
- The PPTX export route — untouched.
- Edit-layout mode (move / hide / restore / reset / localStorage layout) — untouched.
- `KPISection` rendering — untouched; KPIs continue to display the unfiltered briefing values regardless of filter state.
- `visualization_agent`, `classify_columns`, chart generation, KPI generation — backend logic untouched.
- `ChartConfig` Pydantic schema — no new fields.

---

## 3. Backend Changes

### 3.1 New endpoint: `GET /dashboard/latest/data`

Added to `api/routes/dashboard.py`. Auth-required, rate-limited.

```python
@router.get("/latest/data")
@limiter.limit("30/minute")
async def get_latest_dashboard_data(
    request: Request,
    current_user: UserProfile = Depends(get_current_user),
):
    """Return raw cleaned rows + column_roles for the current user's latest
    completed session. Used by the dashboard page to power client-side
    filtering + chart re-aggregation."""
    store = request.app.state.session_store
    if store is None:
        raise HTTPException(status_code=503, detail="Session store not available")

    session = await store.get_latest_analysis(current_user.uid)
    if not session:
        raise HTTPException(status_code=404, detail="No completed analysis yet")

    if not session.cleaned_data_path or not Path(session.cleaned_data_path).is_file():
        raise HTTPException(status_code=404, detail="Cleaned data is not cached for this session")

    import pandas as pd
    df = pd.read_parquet(session.cleaned_data_path)

    MAX_ROWS = 25_000
    if len(df) > MAX_ROWS:
        raise HTTPException(
            status_code=422,
            detail=f"Dataset too large for the interactive dashboard ({len(df):,} rows; limit is {MAX_ROWS:,}). Run a targeted question via chat instead.",
        )

    from agents.visualization_agent import classify_columns
    roles = classify_columns(df)

    # NaN → None so the JSON payload is valid
    rows = df.where(df.notna(), None).to_dict("records")

    return {
        "session_id": session.session_id,
        "row_count": len(rows),
        "column_roles": roles,
        "rows": rows,
    }
```

### 3.2 Why these decisions
- **`get_latest_analysis(current_user.uid)`** — already exists from Gap 6, returns the most-recent session with `insight_report` set, multi-tenant safe.
- **Cap at 25,000 rows** — JSON for 25K × ~30 columns ≈ 5 MB; main-thread filtering stays under 50 ms per change.
- **Re-run `classify_columns` on the fly** — fast, keeps the wire format independent of any backend schema persistence; avoids storing the full role-shape dict on the session.
- **Rate-limited at 30/minute** — matches the existing `/dashboard/latest` ceiling.

### 3.3 Error contract for the frontend
- **401** — not authenticated.
- **404** — no completed session OR no cached parquet (e.g., session predates Gap 6's parquet cache).
- **422** — dataset too large (> 25K rows).
- **200** — `{ session_id, row_count, column_roles, rows }`.

### 3.4 No other backend changes
`classify_columns`, the visualization agent, KPI generation, the chart generation flow, the session store schema, and the existing chat / connector / session routes are all untouched.

---

## 4. Frontend Changes

### 4.1 New types ([frontend/src/types/index.ts](../../../frontend/src/types/index.ts))

```ts
export interface ColumnRoles {
  id_cols: string[];
  target_col: string | null;
  binary_cols: string[];
  low_card_cat: string[];
  high_card_cat: string[];
  continuous: string[];
  datetime_cols: string[];
  ordinal_cols: string[];
  skip_cols: string[];
}

export interface DashboardDataPayload {
  session_id: string;
  row_count: number;
  column_roles: ColumnRoles;
  rows: Record<string, unknown>[];
}
```

### 4.2 Fetch wrapper ([frontend/src/lib/api.ts](../../../frontend/src/lib/api.ts))

A discriminated-union return lets the dashboard render distinct empty / too-large / error states.

```ts
export type DashboardDataResult =
  | { status: "ok"; payload: DashboardDataPayload }
  | { status: "empty" }
  | { status: "too_large"; detail: string }
  | { status: "error"; detail: string };

export async function getLatestDashboardData(): Promise<DashboardDataResult> {
  const res = await fetch(`${API_URL}/dashboard/latest/data`, { headers: getAuthHeaders() });
  if (res.status === 404) return { status: "empty" };
  if (res.status === 422) {
    const body = await res.json().catch(() => ({}));
    return { status: "too_large", detail: body.detail ?? "Dataset too large." };
  }
  if (!res.ok) return { status: "error", detail: `${res.status} ${res.statusText}` };
  return { status: "ok", payload: (await res.json()) as DashboardDataPayload };
}
```

### 4.3 New `DashboardDataContext` ([frontend/src/context/DashboardDataContext.tsx](../../../frontend/src/context/DashboardDataContext.tsx))

```tsx
"use client";
import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { getLatestDashboardData } from "@/lib/api";
import type { ColumnRoles } from "@/types";

type Status = "loading" | "ok" | "empty" | "too_large" | "error";

interface Value {
  status: Status;
  detail: string | null;
  rows: Record<string, unknown>[];
  columnRoles: ColumnRoles | null;
  sessionId: string | null;
  rowCount: number;
}

const Ctx = createContext<Value | null>(null);
const INITIAL: Value = { status: "loading", detail: null, rows: [], columnRoles: null, sessionId: null, rowCount: 0 };

export function DashboardDataProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<Value>(INITIAL);
  useEffect(() => {
    let cancelled = false;
    void getLatestDashboardData().then((result) => {
      if (cancelled) return;
      if (result.status === "ok") {
        setState({
          status: "ok", detail: null,
          rows: result.payload.rows,
          columnRoles: result.payload.column_roles,
          sessionId: result.payload.session_id,
          rowCount: result.payload.row_count,
        });
      } else if (result.status === "empty") {
        setState({ ...INITIAL, status: "empty" });
      } else {
        setState({ ...INITIAL, status: result.status, detail: result.detail });
      }
    });
    return () => { cancelled = true; };
  }, []);
  return <Ctx.Provider value={state}>{children}</Ctx.Provider>;
}

export function useDashboardData() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useDashboardData must be inside DashboardDataProvider");
  return v;
}
```

### 4.4 Aggregator ([frontend/src/lib/aggregateForChart.ts](../../../frontend/src/lib/aggregateForChart.ts))

```ts
import type { ChartConfig } from "@/types";

const GROUPBY_TYPES = new Set([
  "BarChart", "HorizontalBarChart", "PieChart", "DonutChart", "LineChart", "HistogramChart",
]);
const PASSTHROUGH_TYPES = new Set(["ScatterChart"]);

export type AggregationMode = "count" | "passthrough" | "unfiltered";

export interface AggregationResult {
  mode: AggregationMode;
  data: Record<string, unknown>[];  // populated when mode is "count" or "passthrough"
  countKey: string;                 // y-axis key used for the count value
}

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
```

Spec 1's aggregator is intentionally **count-only**. Spec 2 layers sum / mean inference (or explicit recipes) on top.

### 4.5 Existing `applyFilters` is reusable as-is

The existing utility at [DashboardFilterContext.tsx:50](../../../frontend/src/context/DashboardFilterContext.tsx#L50) already operates over any `Record<string, unknown>[]`:

```ts
export function applyFilters<T extends Record<string, unknown>>(rows: T[], filters: Filters): T[] {
  const entries = Object.entries(filters);
  if (entries.length === 0) return rows;
  return rows.filter((row) => {
    for (const [key, val] of entries) {
      if (!(key in row)) continue;                       // pass through if column absent
      if (String(row[key]) !== String(val)) return false;
    }
    return true;
  });
}
```

No rename or rewrite needed. The function works identically on raw rows (which always carry all columns from `data_contract`) — the pass-through branch never fires in that mode. Spec 1's change is purely at the **call site**: `AutoDashboard.tsx` stops calling `applyFilters(chart.data, filters)` per-chart, and instead calls `applyFilters(rows, filters)` once at the top of the render to derive `filteredRows`, then feeds those filtered rows into `aggregateForChart` per chart.

The hook for reading + setting the cross-filter state is `useDashboardFilter()` (existing, returns `{ filters, setFilter, clearFilters }`). The filter shape stays `Record<string, string | number | boolean>`. Range / multi-select filters land in Spec 2's sidebar.

---

## 5. `AutoDashboard` Integration

### 5.1 Wrap the dashboard route in the provider

`frontend/src/app/dashboard/page.tsx` currently renders `<AutoDashboard />` directly inside `<main>`. Wrap it:

```tsx
import { DashboardDataProvider } from "@/context/DashboardDataContext";
// ...
<main className="max-w-7xl mx-auto">
  <DashboardDataProvider>
    <AutoDashboard />
  </DashboardDataProvider>
</main>
```

The provider is a client component; the route page itself stays a server component (the `metadata` export is preserved).

### 5.2 Refactor `AutoDashboard` data flow

Inside `AutoDashboard.tsx`, read the new context, derive the filtered row set, and per chart re-aggregate from those filtered rows. Render status gates before the chart grid.

Pseudo-code for the relevant block:

```tsx
const { status, rows, rowCount, detail } = useDashboardData();
const { filters } = useDashboardFilter();
const filteredRows = useMemo(() => applyFilters(rows, filters), [rows, filters]);
const hasActiveFilters = Object.keys(filters).length > 0;

if (status === "loading") return <DashboardSkeleton />;
if (status === "empty")   return <EmptyState message="Run an analysis first to populate the dashboard." />;
if (status === "too_large") return <EmptyState message={detail ?? "Dataset too large for the interactive dashboard."} />;
if (status === "error")   return <EmptyState message={detail ?? "Couldn't load dashboard data."} />;
```

Per-chart in the grid, replace the existing `applyFilters` call with:

```tsx
const agg = aggregateForChart(filteredRows, chart);
const renderedData = agg.mode === "unfiltered" ? chart.data : agg.data;
const showUnfilteredPill = agg.mode === "unfiltered" && hasActiveFilters;
const showCountSublabel = agg.mode === "count" && hasActiveFilters;
<ChartRenderer config={{ ...chart, data: renderedData }} />
```

The pre-existing `/dashboard/latest` call (returns `LatestAnalysis` — charts, briefing, expert review) stays — we still need it to learn **which** charts to render. What changes is which `data` array each chart actually renders.

### 5.3 Chart-card adornments

When filters are active:
- `showUnfilteredPill` → a small zinc pill next to the chart title: "shows unfiltered data". Applies to ComposedChart, HeatmapChart, or any chart without an `x_key`.
- `showCountSublabel` → a small zinc sublabel under the title: "Filtered: row count". Communicates that the y-axis now shows counts (not the original aggregation).

Both adornments disappear entirely when there are no active filters — chart cards look exactly like today.

### 5.4 "Records shown" indicator

Add one line above the chart grid, only when filters are active:

```tsx
{hasActiveFilters && (
  <div className="text-xs text-zinc-500 mb-2">
    Showing {filteredRows.length.toLocaleString()} of {rowCount.toLocaleString()} records
  </div>
)}
```

Spec 2 will extend this slot to also host sidebar filter chips.

### 5.5 Delete the report-text panel

Lines 301–329 of `AutoDashboard.tsx` render a collapsible `<details>` element with `BriefingPanel`, `InsightPanel`, `ExpertReviewPanel`. Delete those lines outright. Also remove:
- The `detailsOpen` state and its setter.
- The toggle button / chevron.
- The `BriefingPanel`, `InsightPanel`, `ExpertReviewPanel` imports in `AutoDashboard.tsx`. (The component files themselves stay — they remain used by `ChatInterface`.)

The dashboard becomes purely chart + filter chips + KPI row. No prose content.

### 5.6 KPI row stays unfiltered (deferred to Spec 2)

`KPISection` keeps rendering the unfiltered KPIs from `/dashboard/latest`'s `briefing` payload. KPI delta lands in Spec 2.

---

## 6. Verification

### Auto-verifiable
- `uv run pytest tests/` → 25 passed (Spec 1 adds no Python tests; the new endpoint is verified manually).
- Backend boots clean.
- `npm run build` → compiled successfully, all routes static.
- `GET /dashboard/latest/data` without auth → 401.
- `GET /dashboard/latest/data` with auth, no completed session → 404.

### Manual verification (requires a completed analysis session)

| Action | Expected |
|---|---|
| Open `/dashboard` after running an OVERVIEW on any dataset | Charts render exactly as before; KPI row + chart grid present; **no** "Analysis details" panel anywhere. |
| Network tab during initial dashboard load | One call to `/dashboard/latest` (existing) and one call to `/dashboard/latest/data` (new). The latter response body has `rows`, `column_roles`, `row_count`, `session_id`. |
| Click a bar in any chart (e.g. "Region = West") | Filter chip appears; "Showing N of M records" appears; other groupby charts re-render with row counts of the filtered subset; chart cards that re-aggregated show "Filtered: row count" sublabel; ComposedChart / HeatmapChart show "shows unfiltered data" pill. |
| Click the same bar again | Filter chip disappears; charts return to original data; sublabels / pills disappear. |
| Upload a dataset larger than 25K rows and open the dashboard | Dashboard shows the 422 message verbatim ("Dataset too large for the interactive dashboard. Run a targeted question via chat instead."). |
| Cross-filter to a value with zero matching rows | Charts render with empty data arrays; "Showing 0 of M records"; no JS errors. |
| Refresh the dashboard page mid-load | Loading skeleton briefly; the `cancelled` flag in the effect prevents a state update if the fetch resolves after unmount. |
| Pinned charts | Existing pinned-chart feature still works (this spec doesn't touch `/dashboard/pin`). |
| PPTX export | Existing `/dashboard/latest/report.pptx` still works. |

---

## 7. Known Risks

- **Filter semantics are equality-only in Spec 1.** Range / multi-select / date-range filters arrive in Spec 2's sidebar. If a user clicks a continuous-column chart bar today, it'll filter by exact value — usually fine for binned / discrete bars; for raw scatter or histogram it can produce empty filtered sets. Mitigated when Spec 2's sliders ship.
- **Count-only aggregation can mislabel y-axes.** A chart whose title says "Average Revenue by Region" will keep that title but show row counts on the y-axis after filtering. The "Filtered: row count" sublabel partly mitigates this. Real fix lands in Spec 2 with sum / mean inference (or explicit recipes).
- **25K row cap is hard.** Datasets above this get redirected to chat. We'll lift the cap (likely with streaming + worker-based filtering) when this becomes a real constraint.
- **Main-thread filter cost.** At 25K × 30 columns, a filter pass runs in ~10–30 ms — invisible. As the cap rises, this becomes the first bottleneck worth measuring.

---

## 8. Open Questions

None. All design decisions ratified during brainstorming (3-spec split, Option B aggregator, JSON transport, 25K row cap, count-only Spec 1).
