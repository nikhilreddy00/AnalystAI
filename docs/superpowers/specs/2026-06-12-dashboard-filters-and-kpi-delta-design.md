# Dashboard Filters + KPI Delta — Design (Spec 2 of Gap 2)

**Status:** Approved for plan-writing
**Date:** 2026-06-12
**Scope:** Gap 2 (Dashboard as Interactive Workspace), Spec 2 of 3. This spec adds an auto-built filter sidebar that drives client-side filtering, KPI delta computation (filtered value vs overall), and unifies the dashboard's filter state shape so cross-filter clicks and sidebar controls write into a single source of truth.

Spec 1 ([2026-06-11](2026-06-11-dashboard-data-foundation-design.md)) shipped the data layer (raw rows + `DashboardDataContext`) and report-text cleanup. Spec 3 (per-chart customization + add chart + server-side layout persistence) is the explicit follow-up.

---

## 1. Goal & Non-Goals

### Goal
Make the dashboard usable as an exploratory workspace: drop a column-aware filter sidebar on the left, and recompute every KPI from the filtered subset so each card shows its current value alongside the overall value ("Survival Rate: 74.2% (vs 38.4% overall)"). Unify the filter state shape so cross-filter clicks (Spec 1) and sidebar controls share one filter map.

### Non-goals (deferred to Spec 3)
- Per-chart "..." menu (change type / change X / change Y / remove).
- "Add chart" button in edit mode.
- Server-side layout persistence.
- Sum / mean aggregation inference for charts — charts stay count-only when filters are active. KPI delta is the only "numeric-aware" recompute Spec 2 ships.

### Out of scope (broader future)
- Mobile / responsive sidebar layout — desktop only.
- Continuous-column importance ranking for sidebar (Spec 2 just takes the first 3 from `column_roles.continuous`).
- Filter persistence across page reloads — `DashboardFilterContext` remains in-memory only.
- Web worker for filtering large datasets.

---

## 2. Architecture Overview

### Three layers of change

**Backend (small).** Add a structured `recipe: KPIRecipe | None` field to the `KPI` Pydantic model. Extend `generate_smart_kpis` to populate the recipe for each KPI it produces. The function emits up to 5 KPI cards per dataset, spanning 6 recipe kinds (`row_count`, `target_rate`, `metric_avg`, `top_cat`, `comparison_split`, `highest_cv`) — the 5th card slot is either `comparison_split` OR `highest_cv` depending on data shape. The existing `value` and `context` display strings stay — they still represent the **unfiltered** baseline.

**Frontend filter state (medium refactor).** Replace `DashboardFilterContext`'s flat `Record<string, string | number | boolean>` with a unified discriminated-union state. Cross-filter clicks write `{kind: "equality"}` (semantically drop-in); sidebar writes `in` / `range` / `date_range`. The existing `applyFilters` utility gets rewritten to evaluate per `kind`. `setFilter` preserves toggle-on-same-equality behavior for cross-filter UX.

**Frontend KPI + sidebar (new).**
- New `evaluateKPI(recipe, rows)` utility — pure function returning a formatted value string + an `ok` flag.
- `KPICard` gets two new optional props (`filteredValue`, `hasActiveFilters`); when filters are active and `filteredValue` differs from `value`, the card renders the filtered value as primary and the overall value as a small "vs {overall} overall" subline.
- New `FilterSidebar.tsx` component — renders four control kinds from `column_roles`: pill toggles for `low_card_cat` (≤ 8 columns), range sliders for top 3 `continuous`, date range pickers for `datetime_cols`, binary toggles for `binary_cols`. Writes into the unified `DashboardFilterContext`.

### What does NOT change in Spec 2
- Chart re-aggregation stays count-only (Spec 1 behavior). Sum/mean for charts is deferred to Spec 3 — KPI delta is the visible "real numeric" recompute in Spec 2.
- The `/dashboard/latest/data` endpoint is unchanged — KPI recipes ship via the existing `briefing.kpis` payload from `/dashboard/latest`.
- Spec 1's chart adornments (`Filtered: row count` sublabel, `shows unfiltered data` pill) stay.
- The pinned-charts API, PPTX export, edit-layout mode, and localStorage layout persistence are untouched.

---

## 3. Backend: KPI Recipe Additions

### 3.1 New `KPIRecipe` model

Flat schema (simpler than a 6-variant discriminated union; 5 KPI kinds + small optional field set):

```python
class KPIRecipe(BaseModel):
    """Structured recipe describing how a KPI is computed.
    Lets the frontend recompute the filtered value deterministically from raw rows."""
    kind: Literal[
        "row_count",
        "target_rate",
        "metric_avg",
        "top_cat",
        "comparison_split",
        "highest_cv",
    ]
    column: str | None = None             # primary column for most kinds
    target_column: str | None = None      # comparison_split: the 0/1 outcome
    segment_column: str | None = None     # comparison_split: the categorical split
    format_kind: str | None = None        # "currency" | "percent" | "count" | "number"
```

The 5 KPI kinds in `generate_smart_kpis` and how each maps:

| KPI | `kind` | `column` | Other fields |
|---|---|---|---|
| Total {entity} | `row_count` | — | — |
| {Rate} (target positive-class %) | `target_rate` | target column | — |
| Avg {primary metric} | `metric_avg` | primary continuous column | `format_kind` |
| Top {category} | `top_cat` | low-card cat column | — |
| Comparison split (target × cat) | `comparison_split` | — | `target_column`, `segment_column` |
| Highest CV (fallback for #5) | `highest_cv` | continuous column | `format_kind` |

### 3.2 Add `recipe` field to `KPI`

```python
class KPI(BaseModel):
    label: str
    value: str
    context: str = ""
    recipe: KPIRecipe | None = None    # NEW; None for legacy persisted sessions
```

`recipe` is optional so legacy `AnalysisSession` JSON blobs persisted before this spec deserialize cleanly. The frontend treats `recipe=None` as "delta not supported for this KPI" and renders the unfiltered value only.

### 3.3 `generate_smart_kpis` updates

In [agents/visualization_agent.py:1104](../../../agents/visualization_agent.py#L1104), each of the 5 `kpis.append(KPI(...))` call sites gets a `recipe=KPIRecipe(...)` argument. Concretely:

```python
# KPI 1: row count
kpis.append(KPI(label=..., value=..., context=..., recipe=KPIRecipe(kind="row_count")))

# KPI 2: target rate (target_col is captured in scope)
kpis.append(KPI(label=..., value=..., context=..., recipe=KPIRecipe(kind="target_rate", column=target_col)))

# KPI 3: primary metric average
kpis.append(KPI(label=..., value=..., context=..., recipe=KPIRecipe(kind="metric_avg", column=primary, format_kind=kind)))

# KPI 4: top category
kpis.append(KPI(label=..., value=..., context=..., recipe=KPIRecipe(kind="top_cat", column=top_cat)))

# KPI 5a: comparison split (when applicable)
kpis.append(KPI(label=..., value=..., context=..., recipe=KPIRecipe(kind="comparison_split", target_column=tgt, segment_column=seg)))

# KPI 5b: highest CV fallback
kpis.append(KPI(label=..., value=..., context=..., recipe=KPIRecipe(kind="highest_cv", column=col, format_kind=kind)))
```

The `kind` variable from `_kpi_format_kind(...)` (already computed for the display string) is reused for `format_kind` in the recipe — no new computation.

### 3.4 Wire format

`/dashboard/latest` already returns `briefing.kpis` inside the `LatestAnalysis` payload; the new `recipe` field rides along automatically via Pydantic serialization. The `/dashboard/latest/data` endpoint (Spec 1) is unchanged. No new endpoints, no migration.

### 3.5 Backward compatibility

- Persisted sessions from before this spec: `KPI.recipe` deserializes as `None`. Frontend KPI evaluator returns `{ok: false}` and falls back to the unfiltered display.
- Newly generated KPIs carry the recipe from this point forward.

---

## 4. Frontend: Filter State Unification

### 4.1 New `FilterValue` discriminated union

In [frontend/src/context/DashboardFilterContext.tsx](../../../frontend/src/context/DashboardFilterContext.tsx):

```ts
export type FilterValue =
  | { kind: "equality"; value: string | number | boolean }
  | { kind: "in"; values: (string | number | boolean)[] }
  | { kind: "range"; min: number; max: number }
  | { kind: "date_range"; from: string; to: string };   // ISO YYYY-MM-DD

export type FilterState = Record<string, FilterValue>;
```

### 4.2 Refactored `setFilter` API

```ts
interface FilterContextValue {
  filters: FilterState;
  setFilter: (key: string, value: FilterValue | null) => void;
  clearFilters: () => void;
}

const setFilter = useCallback((key: string, value: FilterValue | null) => {
  setFilters((prev) => {
    if (value === null) {
      const next = { ...prev };
      delete next[key];
      return next;
    }
    const existing = prev[key];
    // Toggle-off: same-key equality with same scalar → clear (cross-filter UX)
    if (
      existing?.kind === "equality" &&
      value.kind === "equality" &&
      String(existing.value) === String(value.value)
    ) {
      const next = { ...prev };
      delete next[key];
      return next;
    }
    return { ...prev, [key]: value };
  });
}, []);
```

Cross-filter call sites in [frontend/src/components/ChartRenderer.tsx](../../../frontend/src/components/ChartRenderer.tsx) (5 of them per the Spec 1 audit) update from `setFilter(column, value)` to `setFilter(column, { kind: "equality", value })`. Mechanical edit per call site.

### 4.3 Refactored `applyFilters` utility

```ts
function matchOne(cell: unknown, f: FilterValue): boolean {
  if (cell == null) return false;             // null/undefined cells never match
  switch (f.kind) {
    case "equality":
      return String(cell) === String(f.value);
    case "in":
      return f.values.some((v) => String(cell) === String(v));
    case "range": {
      const n = typeof cell === "number" ? cell : Number(cell);
      return Number.isFinite(n) && n >= f.min && n <= f.max;
    }
    case "date_range": {
      const t = new Date(String(cell)).getTime();
      const lo = new Date(f.from).getTime();
      const hi = new Date(f.to).getTime();
      return !isNaN(t) && t >= lo && t <= hi;
    }
  }
}

export function applyFilters<T extends Record<string, unknown>>(
  rows: T[],
  filters: FilterState,
): T[] {
  const entries = Object.entries(filters);
  if (entries.length === 0) return rows;
  return rows.filter((row) => entries.every(([col, f]) => matchOne(row[col], f)));
}
```

The legacy "pass through if column missing" branch is dropped — raw rows always carry every column from `data_contract`. Spec 1's call site in `AutoDashboard.tsx` (`applyFilters(rows, filters)`) continues to work — `filters` from the context now has the new shape.

### 4.4 Chip rendering

The existing chip rendering in `AutoDashboard.tsx` (Spec 1 audit: lines 222–237) uses `Object.entries(filters).map(...)`. Update the per-chip formatter:

```tsx
function formatFilterChip(f: FilterValue): string {
  switch (f.kind) {
    case "equality":   return String(f.value);
    case "in":         return f.values.length <= 3 ? f.values.join(", ") : `${f.values.length} selected`;
    case "range":      return `${f.min}–${f.max}`;
    case "date_range": return `${f.from} → ${f.to}`;
  }
}
```

### 4.5 Migration / backward-compat

`DashboardFilterContext` was never persisted (in-memory React state only). No migration needed. The only consumers of the old filter shape are `AutoDashboard.tsx` and `ChartRenderer.tsx` (5 onClick call sites) — both update in lock-step within this spec.

---

## 5. Frontend: KPI Evaluator + Delta UI

### 5.1 TypeScript mirror of `KPIRecipe`

Append to [frontend/src/types/index.ts](../../../frontend/src/types/index.ts), near the existing `KPI` interface:

```ts
export interface KPIRecipe {
  kind: "row_count" | "target_rate" | "metric_avg" | "top_cat" | "comparison_split" | "highest_cv";
  column?: string | null;
  target_column?: string | null;
  segment_column?: string | null;
  format_kind?: "currency" | "percent" | "count" | "number" | null;
}
```

And extend the existing `KPI` interface with one new optional field:

```ts
export interface KPI {
  label: string;
  value: string;
  context: string;
  recipe?: KPIRecipe | null;       // NEW
}
```

### 5.2 New evaluator utility ([frontend/src/lib/evaluateKPI.ts](../../../frontend/src/lib/evaluateKPI.ts))

Pure function — no React, no side effects. Implementation overview (full code in plan):

```ts
export interface KPIEvalResult {
  value: string;
  ok: boolean;     // false → unable to recompute (recipe missing / column absent / empty filtered)
}

export function evaluateKPI(
  recipe: KPIRecipe | null | undefined,
  rows: Record<string, unknown>[],
): KPIEvalResult;
```

Each of the 6 `kind` values gets its own branch:
- **`row_count`** → `rows.length` formatted as a count.
- **`target_rate`** → coerce target column cells to 0/1 (`toBinary` helper accepts `1`/`0`/`true`/`false`/`"yes"`/`"no"`/etc.), compute `pos / tot`, format as percent.
- **`metric_avg`** → numeric coerce + filter finite + mean; format per `format_kind`.
- **`top_cat`** → groupby count on `column`, pick max-frequency value, return raw mode string.
- **`comparison_split`** → groupby on `segment_column`, compute mean of `toBinary(target_column)` per segment, pick max-rate segment, format as `"{segment}: {rate}%"`.
- **`highest_cv`** → numeric coerce + filter finite + compute `std / |mean|`, format as percent.

Edge handling: empty filtered set, empty column, all-null column, zero-mean column for CV — all return `{ok: false, value: "—"}`.

Format helper (`formatValue(n, kind)`):
- `"currency"` → `n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 })`
- `"percent"` → `${(n * 100).toFixed(1)}%`
- `"count"` → `Math.round(n).toLocaleString("en-US")`
- `"number"` → `n.toLocaleString("en-US", { maximumFractionDigits: 2 })`

### 5.3 `KPICard` extension

Extend [frontend/src/components/KPICard.tsx](../../../frontend/src/components/KPICard.tsx)'s `Props` interface:

```tsx
interface Props {
  label: string;
  value: string;                      // overall (existing field, unchanged)
  context?: string;
  filteredValue?: string | null;      // NEW
  hasActiveFilters?: boolean;         // NEW
}
```

Render logic:

```tsx
const showDelta = hasActiveFilters && filteredValue && filteredValue !== value;
const primary = showDelta ? filteredValue : value;

return (
  <div className="…existing card classes…">
    <p className="text-xs uppercase tracking-wide text-zinc-500">{label}</p>
    <p className="text-2xl font-bold text-zinc-900">{primary}</p>
    {showDelta && (
      <p className="text-[11px] text-zinc-500">vs {value} overall</p>
    )}
    {!showDelta && context && (
      <p className="text-xs text-zinc-500">{context}</p>
    )}
  </div>
);
```

### 5.4 `KPISection` orchestration

In [frontend/src/components/KPISection.tsx](../../../frontend/src/components/KPISection.tsx), wire the evaluator. The section runs inside both `DashboardDataProvider` (Spec 1) and `DashboardFilterContext.Provider`, so the hooks resolve without prop drilling:

```tsx
"use client";
import { useMemo } from "react";
import { useDashboardData } from "@/context/DashboardDataContext";
import { useDashboardFilter, applyFilters } from "@/context/DashboardFilterContext";
import { evaluateKPI } from "@/lib/evaluateKPI";

// Inside the component:
const { rows } = useDashboardData();
const { filters } = useDashboardFilter();
const filteredRows = useMemo(() => applyFilters(rows, filters), [rows, filters]);
const hasActiveFilters = Object.keys(filters).length > 0;

// Per KPI:
{kpis.map((kpi, i) => {
  const evalResult = evaluateKPI(kpi.recipe, filteredRows);
  const filteredValue = evalResult.ok ? evalResult.value : null;
  return (
    <KPICard
      key={i}
      label={kpi.label}
      value={kpi.value}
      context={kpi.context}
      filteredValue={filteredValue}
      hasActiveFilters={hasActiveFilters}
    />
  );
})}
```

### 5.5 Intentional simplifications

- **`top_cat`** returns the raw mode string. The original `kpi.value` (carrying narrative humanization) remains as the "vs X overall" reference, so the user still sees the canonical labeling there.
- **`comparison_split`** uses simple max-rate selection rather than the chi-squared significance scoring in the backend's `_comparison_kpi_chi2`. At filter-subset scale the statistical reranking is moot.
- **Empty filtered set** → `{ok: false}` and the card falls back to overall display.

---

## 6. Frontend: Filter Sidebar

### 6.1 New component file: [frontend/src/components/FilterSidebar.tsx](../../../frontend/src/components/FilterSidebar.tsx)

Presentational component that auto-builds controls from `column_roles`. Reads rows + roles from `useDashboardData()` and filter state from `useDashboardFilter()`.

```tsx
"use client";
import { useDashboardData } from "@/context/DashboardDataContext";
import { useDashboardFilter } from "@/context/DashboardFilterContext";

export function FilterSidebar() {
  const { columnRoles, rows, status } = useDashboardData();
  const { filters, setFilter } = useDashboardFilter();

  if (status !== "ok" || !columnRoles || rows.length === 0) return null;

  const binary = columnRoles.binary_cols ?? [];
  const lowCard = (columnRoles.low_card_cat ?? []).slice(0, 8);
  const continuous = (columnRoles.continuous ?? []).slice(0, 3);
  const datetime = columnRoles.datetime_cols ?? [];

  if (binary.length + lowCard.length + continuous.length + datetime.length === 0) {
    return null;
  }

  return (
    <aside className="w-60 flex-shrink-0 px-4 py-3 border-r border-zinc-200 space-y-5">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">Filters</h3>
      {binary.map((col) => <BinaryToggle key={col} col={col} rows={rows} filter={filters[col]} setFilter={setFilter} />)}
      {lowCard.map((col) => <PillSelect key={col} col={col} rows={rows} filter={filters[col]} setFilter={setFilter} />)}
      {continuous.map((col) => <RangeSlider key={col} col={col} rows={rows} filter={filters[col]} setFilter={setFilter} />)}
      {datetime.map((col) => <DateRangeControl key={col} col={col} rows={rows} filter={filters[col]} setFilter={setFilter} />)}
    </aside>
  );
}
```

### 6.2 Four control behaviors

All four are colocated inside `FilterSidebar.tsx`. Total file size ~250 lines. If it grows, split into `FilterSidebar/` directory with one file per control (deferred).

**`<BinaryToggle col rows filter setFilter />`**
- Derive 2 unique values from rows (`new Set(rows.map(r => r[col]))`).
- Render three pills: `[All] [Value1] [Value2]`.
- `All` → `setFilter(col, null)`. A specific value → `setFilter(col, { kind: "equality", value })`.
- Highlight the pill matching `filter?.kind === "equality" && filter.value === pill.value`. If no filter, highlight "All".

**`<PillSelect col rows filter setFilter />`**
- Derive unique values (string-coerced, sorted), capped at 12.
- Current selected set: `filter?.kind === "in" ? new Set(filter.values.map(String)) : new Set()`.
- Each pill click toggles membership:
  ```ts
  const next = new Set(current);
  next.has(val) ? next.delete(val) : next.add(val);
  if (next.size === 0) setFilter(col, null);
  else setFilter(col, { kind: "in", values: Array.from(next) });
  ```
- Render styles: filled when selected, outlined otherwise.

**`<RangeSlider col rows filter setFilter />`**
- Derive `[min, max]` extent from numeric cells (`useMemo`).
- Current range: `filter?.kind === "range" ? [filter.min, filter.max] : [min, max]`.
- Uses shadcn `<Slider />` (wraps `@radix-ui/react-slider`, dual-thumb).
- `onValueChange([lo, hi])`:
  ```ts
  if (lo <= min && hi >= max) setFilter(col, null);
  else setFilter(col, { kind: "range", min: lo, max: hi });
  ```
- Display the current `lo` / `hi` above the slider.

**`<DateRangeControl col rows filter setFilter />`**
- Derive `[earliest, latest]` from rows (`useMemo`, parse via `new Date()`, drop NaN, min/max).
- Trigger button shows the current range or `"Any date"`.
- Click trigger → shadcn `<Popover />` containing shadcn `<Calendar mode="range" />`.
- `onSelect({from, to})` → `setFilter(col, { kind: "date_range", from: from.toISOString().slice(0, 10), to: to.toISOString().slice(0, 10) })`.
- "Clear" button inside the popover → `setFilter(col, null)`.

### 6.3 New shadcn primitives

The project currently only has `frontend/src/components/ui/button.tsx`. Three new primitives:

```bash
cd /Users/uvnikhil/Desktop/AnalystAI/frontend
npx shadcn@latest add slider popover calendar
```

Adds:
- `src/components/ui/slider.tsx` (Radix slider, dual-thumb)
- `src/components/ui/popover.tsx` (Radix popover)
- `src/components/ui/calendar.tsx` (react-day-picker, range mode)

`react-day-picker` and `@radix-ui/react-slider` / `@radix-ui/react-popover` get added to `package.json` automatically.

### 6.4 Layout integration in `AutoDashboard.tsx`

Wrap the existing content in a flex container with the sidebar on the left:

```tsx
return (
  <div className="flex gap-4">
    <FilterSidebar />
    <div className="flex-1 min-w-0">
      {/* existing chart grid + KPI section + "Showing X of Y" indicator */}
    </div>
  </div>
);
```

`FilterSidebar` returns `null` when there are no eligible columns or `status !== "ok"`, so empty / loading / error states collapse gracefully to single column (Spec 1's status gates short-circuit earlier anyway).

`min-w-0` on the inner div prevents flex children from forcing the dashboard wider than the viewport.

### 6.5 Mobile / responsive — explicit non-goal

Sidebar takes `w-60` on all viewports; below ~700px the chart area gets squeezed. Mobile responsive layout is out of scope for Spec 2.

---

## 7. Verification

### Auto-verifiable

- `uv run pytest tests/` → 25 passed (no Python regressions; this spec adds no new tests).
- `npm run build` → compiled successfully; all 5 routes static.
- Backend boots clean; `/health` → 200; `/dashboard/latest` → 401 (unchanged); `/dashboard/latest/data` → 401 (unchanged).
- TypeScript: `FilterValue` union, `KPIRecipe` mirror, `evaluateKPI` signatures all typecheck.

### Manual UI smoke (real auth + a completed analysis session)

**KPI delta:**

| Action | Expected |
|---|---|
| Open `/dashboard` after running OVERVIEW on any dataset | KPI cards render exactly as before (no visual change with no filters active). |
| Click a bar to cross-filter "Region = West" | KPI cards swap to filtered values; each shows a small "vs {overall} overall" subline; `context` subline is hidden. |
| Click same bar again to clear cross-filter | KPI cards return to overall values; delta sublines disappear; context returns. |
| Older session with `briefing.kpis` lacking `recipe` (persisted before this spec) | KPI cards show overall values only — no delta even with filters active. No crash. |

**Filter sidebar:**

| Action | Expected |
|---|---|
| Open dashboard on a dataset with low_card_cat columns | Sidebar appears on the left; one pill-row block per low_card_cat column (capped at 8). |
| Click a pill to select | Pill fills; chart grid re-renders with filtered rows; KPI cards swap to filtered + delta; "Showing N of M records" updates. |
| Click the same pill again | Pill un-fills; if it was the only selected pill in the column, the filter clears entirely. |
| Drag a range slider | Range filter applies live; charts + KPIs re-render. |
| Drag slider back to full extent | Filter clears. |
| Open the date-range picker on a `datetime_cols` column | Calendar popover; selecting a range writes the date_range filter; charts + KPIs re-render. |
| Combine multiple filters across columns | AND semantics; "Showing X of Y records" reflects cumulative subset. |
| Clear all filters via "Reset" chip | All sidebar selections clear visually; chart grid and KPIs return to overall. |
| Dataset with no low_card_cat / continuous / datetime / binary columns | Sidebar returns `null`, layout collapses to single column. |

**Backward-compat:**

| Action | Expected |
|---|---|
| Spec 1 cross-filter clicks | Still work — they now write `{kind: "equality"}` and toggle correctly when same value is clicked twice. |
| Spec 1 chart re-aggregation (count-only) | Unchanged. Filters drive `filteredRows`; charts re-aggregate as today. |
| Pinned charts, edit-layout, PPTX export | Unaffected. |

### Known risks

- **`top_cat` and `comparison_split` filtered values differ slightly from backend semantics.** Frontend uses simple max-frequency / max-rate; backend uses narrative humanization and chi-squared scoring. The overall (unfiltered) display still comes from `kpi.value` — the canonical backend string — so the user sees correct labels in the "vs X overall" subline. Filtered values may show the raw mode (e.g. `"3"` instead of `"Class 3"`). Acceptable trade-off; documented.
- **Filter chip rendering is summary-only.** Multi-select chips show `"3 selected"` for 4+ items, not per-pill mini-chips.
- **No web worker for filtering.** At 25K rows × 4 active filters + KPI evaluators, total render-loop cost is ~30–80ms per filter change. Visible but not jarring.
- **Date range picker depends on parseable date strings.** Most datasets ship ISO or `YYYY-MM-DD HH:MM:SS`; rare formats may fail `new Date()` parsing and silently skip those rows. Mitigated because `classify_columns` vets datetime_cols with an >80% parse rate.

---

## 8. Open Questions

None. All design decisions ratified during brainstorming (Option A KPI recipe, Option A unified filter state, four sidebar control kinds, single-phase verification).
