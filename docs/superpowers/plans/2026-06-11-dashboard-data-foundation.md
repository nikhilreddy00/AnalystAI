# Dashboard Data Foundation Implementation Plan (Spec 1 of Gap 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship raw cleaned rows + column_roles from a new backend endpoint to a new `DashboardDataContext`, refactor `AutoDashboard` to filter raw rows and re-aggregate per chart client-side, and delete the residual report-text panel.

**Architecture:** Single-phase, 8 tasks. New JSON endpoint at `/dashboard/latest/data` (re-runs `classify_columns` on the fly, 25K row cap). New `DashboardDataContext` loads the payload on mount. New `aggregateForChart` utility re-aggregates groupby-shaped charts as row counts from filtered rows; complex types show an "unfiltered" pill. The existing `applyFilters` helper is reused as-is — it already operates on arbitrary `Record<string, unknown>[]`. Source spec: [docs/superpowers/specs/2026-06-11-dashboard-data-foundation-design.md](../specs/2026-06-11-dashboard-data-foundation-design.md).

**Tech Stack:** FastAPI + Pydantic v2, pandas (already loaded), `classify_columns` (existing). Frontend: Next.js 16 + React, Recharts (already used).

**Testing convention.** Backend has pytest + pytest-asyncio configured. Frontend has no JS test runner. Verification matches Gap 6 / Gap 1 conventions: pytest where it pays off (this spec adds no Python tests — the new endpoint is exercised via manual curl), `npm run build` for TypeScript verification, manual UI smoke for the user-visible flow.

---

## Part 1: Tasks 1–4

(Part 2 follows with Tasks 5–8 and the verification gate.)

## Task 1: Backend endpoint `/dashboard/latest/data`

**Files:**
- Modify: `api/routes/dashboard.py`

- [ ] **Step 1: Read the existing dashboard router**

Run: `grep -n "^@router\|^async def" /Users/uvnikhil/Desktop/AnalystAI/api/routes/dashboard.py | head -15`

Note the existing decorators and the import block. The new endpoint reuses `Request`, `Depends`, `HTTPException`, `UserProfile`, `get_current_user`, `limiter` — all already imported (verify with a quick grep).

- [ ] **Step 2: Add the new endpoint at the bottom of the file**

Append to `api/routes/dashboard.py`:

```python
@router.get("/latest/data")
@limiter.limit("30/minute")
async def get_latest_dashboard_data(
    request: Request,
    current_user: UserProfile = Depends(get_current_user),
):
    """Return raw cleaned rows + column_roles for the user's latest completed session.
    Used by the dashboard page for client-side filtering + chart re-aggregation."""
    from pathlib import Path

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
            detail=(
                f"Dataset too large for the interactive dashboard "
                f"({len(df):,} rows; limit is {MAX_ROWS:,}). "
                f"Run a targeted question via chat instead."
            ),
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

If `pathlib.Path` is already imported at the top of `dashboard.py` (it probably is — used by existing routes), drop the inline `from pathlib import Path` and use the module-level one. Quick check: `grep -n "from pathlib" /Users/uvnikhil/Desktop/AnalystAI/api/routes/dashboard.py`.

If `pd` and `classify_columns` are already imported at top, hoist those too. They likely aren't (lazy imports keep cold-start lean for routes that don't use them — see the `from api.lib.report_pptx import build_pptx` lazy-import precedent in the PPTX route).

- [ ] **Step 3: Smoke-check the import**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI && uv run python -c "from api.routes.dashboard import get_latest_dashboard_data; print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Verify all existing tests still pass**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI && uv run pytest tests/ -v`
Expected: `25 passed` (no new tests in this task; this confirms the import doesn't break anything).

- [ ] **Step 5: Boot the app and curl the new endpoint**

```bash
cd /Users/uvnikhil/Desktop/AnalystAI
pkill -f "uvicorn api.main" 2>/dev/null; sleep 1
uv run uvicorn api.main:app --port 8793 > /tmp/uv-task1.log 2>&1 &
UV=$!
sleep 6
echo "=== Endpoint without auth (expect 401)"
curl -s -o /dev/null -w "GET /dashboard/latest/data: %{http_code}\n" http://localhost:8793/dashboard/latest/data
echo "=== Health check still works"
curl -s -o /dev/null -w "/health: %{http_code}\n" http://localhost:8793/health
kill $UV 2>/dev/null
wait $UV 2>/dev/null
grep -iE "error|attributeerror|importerror" /tmp/uv-task1.log | grep -vE "PostgreSQL unavailable|Qdrant" | head -5 || echo "(no unexpected errors)"
```

Expected:
- `GET /dashboard/latest/data: 401`
- `/health: 200`
- No unexpected errors in the log.

- [ ] **Step 6: Commit**

```bash
cd /Users/uvnikhil/Desktop/AnalystAI
git add api/routes/dashboard.py
git commit -m "feat(dashboard): /latest/data endpoint serving raw rows + column_roles JSON"
```

---

## Task 2: Frontend types

**Files:**
- Modify: `frontend/src/types/index.ts`

- [ ] **Step 1: Append the new types**

At the bottom of `frontend/src/types/index.ts`, add:

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

- [ ] **Step 2: Verify the frontend compiles**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI/frontend && npm run build 2>&1 | tail -10`
Expected: `✓ Compiled successfully`.

- [ ] **Step 3: Commit**

```bash
cd /Users/uvnikhil/Desktop/AnalystAI
git add frontend/src/types/index.ts
git commit -m "feat(types): ColumnRoles + DashboardDataPayload for dashboard data layer"
```

---

## Task 3: Frontend API wrapper

**Files:**
- Modify: `frontend/src/lib/api.ts`

- [ ] **Step 1: Read the existing api.ts to find the right insertion point**

Run: `grep -n "^export\|^export async function" /Users/uvnikhil/Desktop/AnalystAI/frontend/src/lib/api.ts | head -20`

Note where existing exports live (likely near the bottom, before the `export { API_URL };` line, matching the Gap 6 convention).

- [ ] **Step 2: Add the discriminated-union result type + fetch wrapper**

In `frontend/src/lib/api.ts`, extend the existing `import type { ... } from "@/types";` line to include the two new types:

```ts
import type {
  // ...existing imports preserved...
  DashboardDataPayload,
  ColumnRoles,
} from "@/types";
```

Then add the new exports (placement matches the existing pattern — adjacent to the other dashboard-data helpers, before `export { API_URL };`):

```ts
export type DashboardDataResult =
  | { status: "ok"; payload: DashboardDataPayload }
  | { status: "empty" }
  | { status: "too_large"; detail: string }
  | { status: "error"; detail: string };

export async function getLatestDashboardData(): Promise<DashboardDataResult> {
  const res = await fetch(`${API_URL}/dashboard/latest/data`, {
    headers: getAuthHeaders(),
  });
  if (res.status === 404) return { status: "empty" };
  if (res.status === 422) {
    const body = await res.json().catch(() => ({}));
    return { status: "too_large", detail: body.detail ?? "Dataset too large." };
  }
  if (!res.ok) return { status: "error", detail: `${res.status} ${res.statusText}` };
  return { status: "ok", payload: (await res.json()) as DashboardDataPayload };
}
```

`ColumnRoles` is imported even though this file doesn't directly reference it — `DashboardDataPayload` carries it; the import is for future call sites importing `getLatestDashboardData` to have the role type ergonomically available. If the linter flags it as unused (it's referenced transitively via `DashboardDataPayload`), drop the explicit `ColumnRoles` import.

- [ ] **Step 3: Verify the frontend compiles**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI/frontend && npm run build 2>&1 | tail -10`
Expected: `✓ Compiled successfully`.

- [ ] **Step 4: Commit**

```bash
cd /Users/uvnikhil/Desktop/AnalystAI
git add frontend/src/lib/api.ts
git commit -m "feat(api): getLatestDashboardData wrapper with discriminated-union result"
```

---

## Task 4: `DashboardDataContext`

**Files:**
- Create: `frontend/src/context/DashboardDataContext.tsx`

- [ ] **Step 1: Create the context file**

Create `frontend/src/context/DashboardDataContext.tsx`:

```tsx
"use client";

import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { getLatestDashboardData } from "@/lib/api";
import type { ColumnRoles } from "@/types";

type Status = "loading" | "ok" | "empty" | "too_large" | "error";

interface DashboardDataValue {
  status: Status;
  detail: string | null;
  rows: Record<string, unknown>[];
  columnRoles: ColumnRoles | null;
  sessionId: string | null;
  rowCount: number;
}

const Ctx = createContext<DashboardDataValue | null>(null);

const INITIAL: DashboardDataValue = {
  status: "loading",
  detail: null,
  rows: [],
  columnRoles: null,
  sessionId: null,
  rowCount: 0,
};

export function DashboardDataProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<DashboardDataValue>(INITIAL);

  useEffect(() => {
    let cancelled = false;
    void getLatestDashboardData().then((result) => {
      if (cancelled) return;
      if (result.status === "ok") {
        setState({
          status: "ok",
          detail: null,
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
    return () => {
      cancelled = true;
    };
  }, []);

  return <Ctx.Provider value={state}>{children}</Ctx.Provider>;
}

export function useDashboardData(): DashboardDataValue {
  const v = useContext(Ctx);
  if (!v)
    throw new Error("useDashboardData must be used inside DashboardDataProvider");
  return v;
}
```

The `cancelled` flag prevents a state update if the fetch resolves after the component unmounts (e.g., a fast nav away from the dashboard route).

- [ ] **Step 2: Verify the frontend compiles**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI/frontend && npm run build 2>&1 | tail -10`
Expected: `✓ Compiled successfully`. The provider is unused at this point — that's expected; Task 6 wires it in.

- [ ] **Step 3: Smoke-check the hook signature**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI/frontend && node -e "console.log(require('fs').readFileSync('src/context/DashboardDataContext.tsx', 'utf8').match(/export function (\w+)/g))"`
Expected: an array containing `'export function DashboardDataProvider'` and `'export function useDashboardData'`.

- [ ] **Step 4: Commit**

```bash
cd /Users/uvnikhil/Desktop/AnalystAI
git add frontend/src/context/DashboardDataContext.tsx
git commit -m "feat(context): DashboardDataContext loads rows + column_roles on mount"
```

---

## Part 2: Tasks 5–8 + Verification Gate

---

## Task 5: `aggregateForChart` utility

**Files:**
- Create: `frontend/src/lib/aggregateForChart.ts`

- [ ] **Step 1: Create the file**

Create `frontend/src/lib/aggregateForChart.ts`:

```ts
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
 * pre-aggregated data — caller renders an "shows unfiltered data" pill.
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
```

- [ ] **Step 2: Verify the frontend compiles**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI/frontend && npm run build 2>&1 | tail -10`
Expected: `✓ Compiled successfully`.

- [ ] **Step 3: Smoke-check via a one-off node script**

Run from the frontend dir:

```bash
cd /Users/uvnikhil/Desktop/AnalystAI/frontend && npx tsx -e "
import { aggregateForChart } from './src/lib/aggregateForChart';
const rows = [
  { Region: 'West', Revenue: 10 },
  { Region: 'East', Revenue: 20 },
  { Region: 'West', Revenue: 15 },
];
const bar = { id: 'c1', chart_type: 'BarChart', title: 't', data: [], x_key: 'Region', y_keys: ['Revenue'], colors: [], show_legend: true, show_grid: true, source_finding: 'x' };
console.log('count:', JSON.stringify(aggregateForChart(rows, bar as any)));
const composed = { id: 'c2', chart_type: 'ComposedChart', title: 't', data: [{Region:'X'}], x_key: 'Region', y_keys: [], colors: [], show_legend: true, show_grid: true, source_finding: 'x' };
console.log('unfiltered:', JSON.stringify(aggregateForChart(rows, composed as any)));
const scatter = { id: 'c3', chart_type: 'ScatterChart', title: 't', data: [], x_key: 'Revenue', y_keys: ['Revenue'], colors: [], show_legend: true, show_grid: true, source_finding: 'x' };
console.log('passthrough:', JSON.stringify(aggregateForChart(rows, scatter as any)));
" 2>&1 | tail -10
```

Expected output contains:
- `count: {"mode":"count","data":[{"Region":"West","Revenue":2},{"Region":"East","Revenue":1}],"countKey":"Revenue"}`
- `unfiltered: {"mode":"unfiltered","data":[{"Region":"X"}],"countKey":""}`
- `passthrough: {"mode":"passthrough","data":[...],"countKey":"Revenue"}`

(Map iteration order is insertion order in JS, so West comes before East. If `tsx` isn't installed globally, `npx tsx` resolves the dev-dep. If it's still missing, skip this step — the `npm run build` already typechecks the file and the manual UI smoke at the verification gate will exercise it.)

- [ ] **Step 4: Commit**

```bash
cd /Users/uvnikhil/Desktop/AnalystAI
git add frontend/src/lib/aggregateForChart.ts
git commit -m "feat(dashboard): aggregateForChart utility for client-side chart re-aggregation"
```

---

## Task 6: Wrap dashboard route with `DashboardDataProvider`

**Files:**
- Modify: `frontend/src/app/dashboard/page.tsx`

- [ ] **Step 1: Read the existing dashboard page**

Run: `cat /Users/uvnikhil/Desktop/AnalystAI/frontend/src/app/dashboard/page.tsx`

It's ~40 lines, a server component with a `metadata` export and a `<main>` containing `<AutoDashboard />`.

- [ ] **Step 2: Add the provider wrap**

In `frontend/src/app/dashboard/page.tsx`, add the import near the top alongside the existing `AutoDashboard` import:

```tsx
import { DashboardDataProvider } from "@/context/DashboardDataContext";
```

Then wrap the existing `<AutoDashboard />` element:

```tsx
<main className="max-w-7xl mx-auto">
  <DashboardDataProvider>
    <AutoDashboard />
  </DashboardDataProvider>
</main>
```

Preserve everything else in the file (the header, `metadata` export, the `Link` to `/chat`). The route stays a server component — the provider is a client component but Next.js handles the boundary automatically when a client component is rendered as a child of a server component.

- [ ] **Step 3: Verify the frontend compiles**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI/frontend && npm run build 2>&1 | tail -10`
Expected: `✓ Compiled successfully`.

- [ ] **Step 4: Commit**

```bash
cd /Users/uvnikhil/Desktop/AnalystAI
git add frontend/src/app/dashboard/page.tsx
git commit -m "feat(dashboard): wrap dashboard route with DashboardDataProvider"
```

---

## Task 7: Refactor `AutoDashboard` data flow + adornments + status gates

This is the largest task. The file is ~438 lines. Read it end-to-end before editing.

**Files:**
- Modify: `frontend/src/components/AutoDashboard.tsx`

- [ ] **Step 1: Read the file in full and identify the four insertion points**

Run: `wc -l /Users/uvnikhil/Desktop/AnalystAI/frontend/src/components/AutoDashboard.tsx`

Find:
- The existing imports block (top of file).
- The `useDashboardFilter()` call inside the component.
- The per-chart loop that currently calls `applyFilters(c.data, filters)` to derive each chart's rendered data.
- Where to insert the "Showing N of M records" indicator (above the chart grid).

- [ ] **Step 2: Add the new imports**

At the top of `AutoDashboard.tsx`, alongside the existing imports, add:

```tsx
import { useDashboardData } from "@/context/DashboardDataContext";
import { aggregateForChart } from "@/lib/aggregateForChart";
import { useMemo } from "react";  // skip if already imported
```

Check whether `useMemo` is already imported from `react` — if so, don't duplicate the import; otherwise extend the existing `import { ... } from "react"` line to include it.

- [ ] **Step 3: Wire the context + derive filtered rows**

Inside the `AutoDashboard` component body, immediately after the existing `useDashboardFilter()` destructure (likely something like `const { filters, setFilter, clearFilters } = useDashboardFilter();`), add:

```tsx
const { status, rows, rowCount, detail } = useDashboardData();

const filteredRows = useMemo(
  () => applyFilters(rows, filters),
  [rows, filters],
);
const hasActiveFilters = Object.keys(filters).length > 0;
```

`applyFilters` is already imported from `@/context/DashboardFilterContext` (verify with grep on the file). The existing implementation operates on any `Record<string, unknown>[]`, including raw rows — no change to the utility itself.

- [ ] **Step 4: Add status gates**

Before the existing JSX return, add four early-return branches. Find the existing return statement (likely after the data-loading effects); the gates go IMMEDIATELY before it:

```tsx
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
```

Match the surrounding Tailwind palette — `text-zinc-500` is the muted-secondary color used by the existing empty-state slots in the file. Adapt if the file uses a different convention.

- [ ] **Step 5: Add the "Records shown" indicator above the chart grid**

Find the existing chart grid (the wrapper around the per-chart map — likely a `<div className="grid ...">` or a wrapper passed into a `ChartGrid` component). Just above that block, add:

```tsx
{hasActiveFilters && (
  <div className="text-xs text-zinc-500 mb-2 px-6">
    Showing {filteredRows.length.toLocaleString()} of {rowCount.toLocaleString()} records
  </div>
)}
```

The `px-6` padding matches the existing chart-grid padding — verify against the surrounding container and adapt if different.

- [ ] **Step 6: Replace the per-chart data derivation**

Find the existing per-chart `<ChartRenderer config={{ ...c, data: applyFilters(c.data, filters) }} />` (or whatever the current call site looks like — see the audit's reference: `applyFilters(c.data, filters)` at line ~361). Replace with:

```tsx
{(() => {
  const agg = aggregateForChart(filteredRows, c);
  const renderedData = agg.mode === "unfiltered" ? c.data : agg.data;
  const showUnfilteredPill = agg.mode === "unfiltered" && hasActiveFilters;
  const showCountSublabel = agg.mode === "count" && hasActiveFilters;
  return (
    <div className="relative">
      {showUnfilteredPill && (
        <span className="absolute top-2 right-12 text-[10px] uppercase tracking-wide bg-zinc-100 text-zinc-600 px-2 py-0.5 rounded-full">
          shows unfiltered data
        </span>
      )}
      {showCountSublabel && (
        <p className="text-[11px] text-zinc-500 px-3 -mb-1">
          Filtered: row count
        </p>
      )}
      <ChartRenderer config={{ ...c, data: renderedData }} />
    </div>
  );
})()}
```

The exact JSX wrapper depends on what currently surrounds `<ChartRenderer>` in the file. If the chart card already has a `<div>` wrapper with title + download button + drag handle, place `showUnfilteredPill` and `showCountSublabel` INSIDE that existing wrapper rather than introducing a new one. The IIFE wrapper above is illustrative; adapt to the existing structure.

If the existing per-chart code is in a separate child component (e.g., `ChartGrid` from the file), the same logic applies inside that child — push the `agg`, `renderedData`, `showUnfilteredPill`, `showCountSublabel` derivation down into the child or precompute and pass via props. Prefer precompute + pass via props to keep the child simple.

- [ ] **Step 7: Verify the frontend compiles**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI/frontend && npm run build 2>&1 | tail -15`
Expected: `✓ Compiled successfully`.

- [ ] **Step 8: Commit**

```bash
cd /Users/uvnikhil/Desktop/AnalystAI
git add frontend/src/components/AutoDashboard.tsx
git commit -m "feat(dashboard): refactor to filter raw rows + re-aggregate per chart"
```

---

## Task 8: Delete the report-text panel

**Files:**
- Modify: `frontend/src/components/AutoDashboard.tsx`

- [ ] **Step 1: Find the report panel**

Per the audit, the collapsible "Analysis details" block lives at roughly lines 301–329 of `AutoDashboard.tsx` (line numbers shift slightly after Task 7's edits). Find it via:

```bash
grep -n "BriefingPanel\|InsightPanel\|ExpertReviewPanel\|Analysis details\|detailsOpen" /Users/uvnikhil/Desktop/AnalystAI/frontend/src/components/AutoDashboard.tsx
```

This will show:
- The imports for `BriefingPanel`, `InsightPanel`, `ExpertReviewPanel`.
- The `useState(... )` line for `detailsOpen`.
- The `<details>` or button that toggles `detailsOpen`.
- The JSX that renders the three panels.

- [ ] **Step 2: Delete the panel JSX**

Remove the entire `<details>` / toggle-button block and the three `<BriefingPanel />` / `<InsightPanel />` / `<ExpertReviewPanel />` invocations from the JSX. The exact range depends on the existing structure — delete from the opening element of the "Analysis details" block through its closing element.

- [ ] **Step 3: Delete the `detailsOpen` state**

Remove the line `const [detailsOpen, setDetailsOpen] = useState(false);` (or similarly named). Remove any handler functions that referenced `setDetailsOpen`.

- [ ] **Step 4: Delete the imports**

Remove these three imports from the top of `AutoDashboard.tsx`:

```tsx
import BriefingPanel from "./BriefingPanel";
import InsightPanel from "./InsightPanel";
import ExpertReviewPanel from "./ExpertReviewPanel";
```

(Or however they're named in the existing import block.) The component files themselves stay — they remain used by `ChatInterface.tsx`.

- [ ] **Step 5: Verify no stale references**

Run: `grep -n "BriefingPanel\|InsightPanel\|ExpertReviewPanel\|detailsOpen\|setDetailsOpen" /Users/uvnikhil/Desktop/AnalystAI/frontend/src/components/AutoDashboard.tsx`
Expected: no matches.

- [ ] **Step 6: Verify the frontend compiles**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI/frontend && npm run build 2>&1 | tail -10`
Expected: `✓ Compiled successfully`. If TypeScript flags unused-import warnings on the surviving imports, no action needed — the build should still pass; otherwise remove the flagged imports.

- [ ] **Step 7: Commit**

```bash
cd /Users/uvnikhil/Desktop/AnalystAI
git add frontend/src/components/AutoDashboard.tsx
git commit -m "feat(dashboard): remove report-text panel from dashboard view"
```

---

## Verification Gate

Before declaring Spec 1 done, confirm:

### Auto-verifiable

- `cd /Users/uvnikhil/Desktop/AnalystAI && uv run pytest tests/ -v` → 25 passed (no regressions).
- `cd /Users/uvnikhil/Desktop/AnalystAI/frontend && npm run build` → compiled successfully, all routes static.
- Backend boots clean:
  ```bash
  pkill -f "uvicorn api.main" 2>/dev/null; sleep 1
  cd /Users/uvnikhil/Desktop/AnalystAI
  uv run uvicorn api.main:app --port 8794 > /tmp/uv-spec1-gate.log 2>&1 &
  UV=$!
  sleep 6
  curl -s -o /dev/null -w "/health: %{http_code}\n" http://localhost:8794/health
  curl -s -o /dev/null -w "/dashboard/latest/data (no auth): %{http_code}\n" http://localhost:8794/dashboard/latest/data
  kill $UV 2>/dev/null
  wait $UV 2>/dev/null
  grep -iE "error|attributeerror|importerror" /tmp/uv-spec1-gate.log | grep -vE "PostgreSQL unavailable|Qdrant" | head -5 || echo "(no unexpected errors)"
  ```
  Expected: `/health: 200`, `/dashboard/latest/data: 401`, no unexpected errors.

### Manual UI smoke (requires real auth + a completed analysis session)

| Action | Expected |
|---|---|
| Open `/dashboard` after running an OVERVIEW on any dataset | Charts render exactly as before; KPI row + chart grid present; **no** "Analysis details" panel anywhere. |
| Network tab during initial dashboard load | One call to `/dashboard/latest` (existing) and one to `/dashboard/latest/data` (new). The latter response has `rows`, `column_roles`, `row_count`, `session_id`. |
| Click a bar in any chart (e.g. "Region = West") | Filter chip appears; "Showing N of M records" appears above the chart grid; other groupby charts re-render with row counts of the filtered subset; cards that re-aggregated show "Filtered: row count" sublabel; ComposedChart / HeatmapChart show "shows unfiltered data" pill. |
| Click the same bar again | Filter chip disappears; charts return to original data; sublabels / pills disappear; "Showing X of Y records" line disappears. |
| Cross-filter to a value with zero matching rows | Charts render with empty data arrays; "Showing 0 of M records" line is visible; no JS errors. |
| Refresh the dashboard page mid-load | Loading state ("Loading dashboard…") visible briefly; React `cancelled` flag prevents stale-state writes. |
| Upload a dataset > 25K rows and open `/dashboard` | Dashboard shows the 422 message verbatim — "Dataset too large for the interactive dashboard. Run a targeted question via chat instead." |
| Existing pinned-charts feature | Still works — unaffected by this spec. |
| Existing PPTX export | Still works — unaffected by this spec. |
| Existing edit-layout mode (move / hide / restore / reset) | Still works — unaffected by this spec. |

### Spec coverage check

- Spec §3 (Backend) → Task 1.
- Spec §4.1–4.2 (Types + fetch wrapper) → Tasks 2, 3.
- Spec §4.3 (`DashboardDataContext`) → Task 4.
- Spec §4.4 (Aggregator) → Task 5.
- Spec §4.5 (Reuse existing `applyFilters`) → confirmed in Task 7 (no rename; same utility, new call site).
- Spec §5.1 (Wrap with provider) → Task 6.
- Spec §5.2 (Refactor `AutoDashboard` data flow) → Task 7.
- Spec §5.3–5.4 (Chart adornments + records indicator) → Task 7.
- Spec §5.5 (Delete report panel) → Task 8.
- Spec §5.6 (KPI row stays unfiltered) → confirmed by absence — no task modifies `KPISection`.
- Spec §6 (Verification) → this gate.
- Spec §7 (Known risks) — documented; no tasks required.

### Non-goals (explicitly out of scope)

- Filter sidebar with auto-built controls — Spec 2.
- KPI delta — Spec 2.
- Sum / mean aggregation inference — Spec 2.
- Per-chart "..." menu, "Add chart", server-side layout persistence — Spec 3.
- Web worker for filtering — future.
