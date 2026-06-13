# Dashboard Filters + KPI Delta Implementation Plan (Spec 2 of Gap 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an auto-built filter sidebar that drives client-side filtering on the dashboard and a KPI delta computation that recomputes each KPI from the filtered subset. Unify the filter state into a discriminated-union shape so cross-filter clicks and sidebar controls share one source of truth.

**Architecture:** Single phase, 10 tasks. Backend adds a `KPIRecipe` Pydantic field to `KPI`. Frontend unifies `DashboardFilterContext` to a `FilterValue` discriminated union (`equality` / `in` / `range` / `date_range`), adds a pure `evaluateKPI` utility, extends `KPICard` with a delta subline, and ships a new `FilterSidebar` component built from `column_roles`. Chart re-aggregation stays count-only (Spec 1 behavior); only KPIs gain numeric-aware recompute in Spec 2. Source spec: [docs/superpowers/specs/2026-06-12-dashboard-filters-and-kpi-delta-design.md](../specs/2026-06-12-dashboard-filters-and-kpi-delta-design.md).

**Tech Stack:** Python 3.11+, Pydantic v2, pandas. Frontend: Next.js 16 + React + Recharts (existing), shadcn/ui v4 (existing button; adds slider / popover / calendar), `@radix-ui/react-slider` + `@radix-ui/react-popover` + `react-day-picker` (installed by shadcn CLI).

**Testing convention.** No new Python tests (matches Gap 6 / Gap 1 / Spec 1 convention — backend changes exercised via manual full-pipeline run). Frontend verification via `npm run build` for typecheck + manual UI smoke at the verification gate.

---

## Task 1: Backend — `KPIRecipe` Pydantic model + `KPI.recipe` field + populate in `generate_smart_kpis`

**Files:**
- Modify: `orchestrator/state.py`
- Modify: `agents/visualization_agent.py`

- [ ] **Step 1: Read the existing `KPI` definition and `generate_smart_kpis`**

Run: `grep -n "^class KPI\b\|kpis.append(KPI(" /Users/uvnikhil/Desktop/AnalystAI/orchestrator/state.py /Users/uvnikhil/Desktop/AnalystAI/agents/visualization_agent.py | head -20`

Note:
- The exact position of `class KPI(BaseModel)` in `state.py` (around line 266).
- The 6 `kpis.append(KPI(...))` call sites in `visualization_agent.py` (5 KPI cards; one of the comparison split / highest CV branches fires for slot #5).

- [ ] **Step 2: Add `KPIRecipe` to `orchestrator/state.py`**

In `orchestrator/state.py`, immediately ABOVE `class KPI(BaseModel)`, add:

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
    column: str | None = None
    target_column: str | None = None
    segment_column: str | None = None
    format_kind: str | None = None  # "currency" | "percent" | "count" | "number"
```

`Literal` should already be imported (added during Gap 1 Spec 1). Verify with `grep -n "from typing" /Users/uvnikhil/Desktop/AnalystAI/orchestrator/state.py | head -3`. If absent, add `Literal` to the existing `from typing import …` line.

- [ ] **Step 3: Extend `KPI` with `recipe` field**

Inside the existing `class KPI(BaseModel):` block, add one new field at the end (after `context: str = ""`):

```python
    recipe: KPIRecipe | None = None
```

- [ ] **Step 4: Populate the recipe in `generate_smart_kpis`**

In `agents/visualization_agent.py` (around line 1104+), each of the 6 `kpis.append(KPI(...))` calls gets a `recipe=KPIRecipe(...)` argument. Map each call to its recipe kind by reading the surrounding logic (which variable holds the column name).

Add `KPIRecipe` to the existing `from orchestrator.state import …` line at the top of `visualization_agent.py`.

The 6 patched call sites (preserve existing `label=`, `value=`, `context=` kwargs — only add `recipe=`):

**Call site 1 — row count** (around line 1134):
```python
kpis.append(KPI(
    label=f"Total {entity_pl.title()}",
    value=_format_count(len(df)),
    context=f"{len(df):,} {entity_sg} records",
    recipe=KPIRecipe(kind="row_count"),
))
```

**Call site 2 — target rate** (around line 1148, inside `if target_col and target_col in df.columns:` block):
```python
kpis.append(KPI(
    label=_rate_label(target_col, narrative),
    value=f"{rate * 100:.1f}%",
    context=f"{pos_count:,} of {total:,} {entity_pl} {pos_phrase}",
    recipe=KPIRecipe(kind="target_rate", column=target_col),
))
```

**Call site 3 — primary metric avg** (around line 1163, inside `if primary:` block):
```python
kpis.append(KPI(
    label=f"Avg {_display(primary, narrative)}",
    value=_format_value(mean, kind),
    context=f"Range {_format_value(mn, kind)} – {_format_value(mx, kind)}",
    recipe=KPIRecipe(kind="metric_avg", column=primary, format_kind=kind),
))
```

**Call site 4 — top category** (around line 1177, inside `if top_cat:` block):
```python
kpis.append(KPI(
    label=f"Top {_display(top_cat, narrative)}",
    value=_humanize_value(top_val, top_cat, df, ordinal_cols, narrative),
    context=f"{share:.1f}% of {entity_pl}",
    recipe=KPIRecipe(kind="top_cat", column=top_cat),
))
```

**Call sites 5a + 5b — comparison split OR highest CV**: these live in the helper functions `_comparison_kpi_chi2` and `_highest_cv_kpi` (around lines 1017 and 1064). Read those helpers, find their `KPI(label=..., value=..., context=...)` constructions, and add the recipe:

- In `_comparison_kpi_chi2`, the returned KPI uses a `target` column and a `seg_col`. Add: `recipe=KPIRecipe(kind="comparison_split", target_column=target, segment_column=seg_col)`. The variable names inside the helper may differ slightly — use whatever the helper passes to chi2 (target series + segment column). Read the function body carefully.
- In `_highest_cv_kpi`, the returned KPI is computed for a specific `col` with format `kind`. Add: `recipe=KPIRecipe(kind="highest_cv", column=col, format_kind=kind)`.

- [ ] **Step 5: Smoke-check imports parse**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI && uv run python -c "from orchestrator.state import KPI, KPIRecipe; r = KPIRecipe(kind='metric_avg', column='Age', format_kind='number'); print(r.kind, r.column); k = KPI(label='x', value='y', recipe=r); print(k.recipe.kind)"`
Expected: `metric_avg Age` and `metric_avg`.

- [ ] **Step 6: Verify all existing tests still pass**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI && uv run pytest tests/ -v`
Expected: `25 passed`.

- [ ] **Step 7: Commit**

```bash
cd /Users/uvnikhil/Desktop/AnalystAI
git add orchestrator/state.py agents/visualization_agent.py
git commit -m "feat(kpi): add structured KPIRecipe + populate in generate_smart_kpis"
```

---

## Task 2: Frontend types — `KPIRecipe` interface + `KPI.recipe` field

**Files:**
- Modify: `frontend/src/types/index.ts`

- [ ] **Step 1: Locate the existing `KPI` interface**

Run: `grep -n "interface KPI\b\|interface KPIRecipe" /Users/uvnikhil/Desktop/AnalystAI/frontend/src/types/index.ts`
Note the current shape of `KPI` (3 fields: label, value, context).

- [ ] **Step 2: Add `KPIRecipe` and extend `KPI`**

In `frontend/src/types/index.ts`, IMMEDIATELY ABOVE the existing `export interface KPI`, add:

```ts
export interface KPIRecipe {
  kind: "row_count" | "target_rate" | "metric_avg" | "top_cat" | "comparison_split" | "highest_cv";
  column?: string | null;
  target_column?: string | null;
  segment_column?: string | null;
  format_kind?: "currency" | "percent" | "count" | "number" | null;
}
```

Then extend the existing `KPI` interface — add ONE new optional field at the end (preserve existing `label` / `value` / `context`):

```ts
export interface KPI {
  label: string;
  value: string;
  context: string;
  recipe?: KPIRecipe | null;  // NEW
}
```

- [ ] **Step 3: Verify the frontend compiles**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI/frontend && npm run build 2>&1 | tail -10`
Expected: `✓ Compiled successfully`.

- [ ] **Step 4: Commit**

```bash
cd /Users/uvnikhil/Desktop/AnalystAI
git add frontend/src/types/index.ts
git commit -m "feat(types): KPIRecipe interface + KPI.recipe optional field"
```

---

## Task 3: Filter context refactor — unified `FilterValue` discriminated union

**Files:**
- Modify: `frontend/src/context/DashboardFilterContext.tsx`

- [ ] **Step 1: Read the current context file**

Run: `cat /Users/uvnikhil/Desktop/AnalystAI/frontend/src/context/DashboardFilterContext.tsx`

Confirm the current shape: `Filters = Record<string, string | number | boolean>`, `setFilter(key, value: FilterValue)` with toggle-on-same-equality semantics, exported utility `applyFilters<T>(rows, filters): T[]`.

- [ ] **Step 2: Replace the file contents**

Replace the entire contents of `frontend/src/context/DashboardFilterContext.tsx` with the new unified implementation:

```tsx
"use client";

// D4 + Gap 2 Spec 2: dashboard-wide filter state.
// Cross-filter clicks write {kind: "equality"}; sidebar controls write
// {kind: "in" | "range" | "date_range"}. applyFilters evaluates per kind
// across active filters with AND semantics.

import {
  createContext,
  useCallback,
  useContext,
  useState,
  type ReactNode,
} from "react";

export type FilterValue =
  | { kind: "equality"; value: string | number | boolean }
  | { kind: "in"; values: (string | number | boolean)[] }
  | { kind: "range"; min: number; max: number }
  | { kind: "date_range"; from: string; to: string };

export type FilterState = Record<string, FilterValue>;

interface FilterContextValue {
  filters: FilterState;
  setFilter: (key: string, value: FilterValue | null) => void;
  clearFilters: () => void;
}

const Ctx = createContext<FilterContextValue | null>(null);

export function DashboardFilterProvider({ children }: { children: ReactNode }) {
  const [filters, setFilters] = useState<FilterState>({});

  const setFilter = useCallback(
    (key: string, value: FilterValue | null) => {
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
    },
    [],
  );

  const clearFilters = useCallback(() => setFilters({}), []);

  return (
    <Ctx.Provider value={{ filters, setFilter, clearFilters }}>
      {children}
    </Ctx.Provider>
  );
}

export function useDashboardFilter() {
  const v = useContext(Ctx);
  if (!v)
    throw new Error(
      "useDashboardFilter must be used within DashboardFilterProvider",
    );
  return v;
}

// Pure utility — apply active filters to a raw-row array. AND across columns.
function matchOne(cell: unknown, f: FilterValue): boolean {
  if (cell == null) return false;
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

The previous `Filters` type alias is gone (replaced by `FilterState`). The `applyFilters` signature is unchanged from a caller's perspective — they still pass `(rows, filters)` and get back filtered rows.

- [ ] **Step 3: Smoke-check the new exports compile (everything else breaks until Task 4 fixes call sites)**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI/frontend && npm run build 2>&1 | tail -20`

Expected: TypeScript errors at the `setFilter(column, value)` cross-filter call sites in `ChartRenderer.tsx` (5 of them) because they now pass a scalar where a `FilterValue | null` is expected. Note the error count for verification in Task 4.

If you see errors elsewhere (besides `ChartRenderer.tsx` and `AutoDashboard.tsx`'s chip-format block), STOP and report — those are unexpected consumers that need handling.

- [ ] **Step 4: Commit (build is intentionally broken; Task 4 fixes the call sites)**

```bash
cd /Users/uvnikhil/Desktop/AnalystAI
git add frontend/src/context/DashboardFilterContext.tsx
git commit -m "refactor(filter-context): unified FilterValue discriminated union"
```

---

## Task 4: Update cross-filter call sites + chip rendering for new `FilterState`

**Files:**
- Modify: `frontend/src/components/ChartRenderer.tsx`
- Modify: `frontend/src/components/AutoDashboard.tsx`

- [ ] **Step 1: Locate every `setFilter(` call site**

Run: `grep -n "setFilter(" /Users/uvnikhil/Desktop/AnalystAI/frontend/src/components/ChartRenderer.tsx /Users/uvnikhil/Desktop/AnalystAI/frontend/src/components/AutoDashboard.tsx`

Per the Spec 1 audit, there are 5 onClick handlers in `ChartRenderer.tsx` (BarChart, LineChart/Composed, PieChart, ScatterChart, plus one in the chart-card layer). There's also the chip-rendering block in `AutoDashboard.tsx` that calls `setFilter(key, null)` to clear (this already matches the new API — no change needed there).

- [ ] **Step 2: Update each `ChartRenderer.tsx` call site**

For each call that currently looks like:
```tsx
setFilter(columnName, value)
```

Change to:
```tsx
setFilter(columnName, { kind: "equality", value })
```

Where `value` is whatever scalar the chart's onClick handler currently extracts (the bar label, pie slice name, etc.). The values can be `string | number | boolean` — the `FilterValue` union accepts all three.

A common pattern in Recharts onClick handlers: `setFilter(columnName, state.activeLabel)`. Change to: `setFilter(columnName, { kind: "equality", value: state.activeLabel })`.

If a particular onClick is conditionally called only when a value exists (a common Recharts pattern: `if (state?.activeLabel) setFilter(...)`), the conditional stays — only the call itself changes.

- [ ] **Step 3: Update the chip-formatter in `AutoDashboard.tsx`**

Find the JSX block that maps over `Object.entries(filters)` to render chips (per the Spec 1 audit, around lines 222–237). It currently renders something like `<button>{key} = {String(value)} ✕</button>`. Replace the value formatter with a per-kind helper.

Add this helper at the top of `AutoDashboard.tsx` (after the imports, before the component definitions):

```tsx
import type { FilterValue } from "@/context/DashboardFilterContext";

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
```

(If `import type` from `@/context/DashboardFilterContext` already exists for some other type, extend the existing line.)

Then in the chip-rendering block, change the inner text from `{String(value)}` (or `{value}`) to `{formatFilterChip(f)}`. The map signature becomes `Object.entries(filters).map(([key, f]) => ...)` (use `f` instead of `value` to make the discriminated union readable).

- [ ] **Step 4: Verify the frontend compiles**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI/frontend && npm run build 2>&1 | tail -15`
Expected: `✓ Compiled successfully`. Build is now healthy again after Task 3's intentional break.

If TypeScript flags any remaining call sites (a setFilter you missed), search for them with `grep -rn "setFilter(" /Users/uvnikhil/Desktop/AnalystAI/frontend/src/` and update.

- [ ] **Step 5: Commit**

```bash
cd /Users/uvnikhil/Desktop/AnalystAI
git add frontend/src/components/ChartRenderer.tsx frontend/src/components/AutoDashboard.tsx
git commit -m "refactor(charts): cross-filter call sites + chip formatter for FilterValue union"
```

---

## Task 5: `evaluateKPI` utility

**Files:**
- Create: `frontend/src/lib/evaluateKPI.ts`

- [ ] **Step 1: Create the file**

Create `frontend/src/lib/evaluateKPI.ts`:

```ts
import type { KPIRecipe } from "@/types";

const POSITIVE = new Set(["1", "true", "yes", "y", "t"]);
const NEGATIVE = new Set(["0", "false", "no", "n", "f"]);

function toBinary(v: unknown): 0 | 1 | null {
  if (v == null) return null;
  if (typeof v === "boolean") return v ? 1 : 0;
  if (typeof v === "number") return v === 1 ? 1 : v === 0 ? 0 : null;
  const s = String(v).trim().toLowerCase();
  if (POSITIVE.has(s)) return 1;
  if (NEGATIVE.has(s)) return 0;
  return null;
}

function numeric(rows: Record<string, unknown>[], col: string): number[] {
  const out: number[] = [];
  for (const r of rows) {
    const v = r[col];
    if (v == null) continue;
    const n = typeof v === "number" ? v : Number(v);
    if (Number.isFinite(n)) out.push(n);
  }
  return out;
}

function formatValue(n: number, kind: KPIRecipe["format_kind"]): string {
  if (!Number.isFinite(n)) return "—";
  switch (kind) {
    case "currency":
      return n.toLocaleString("en-US", {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: 2,
      });
    case "percent":
      return `${(n * 100).toFixed(1)}%`;
    case "count":
      return Math.round(n).toLocaleString("en-US");
    case "number":
    default:
      return n.toLocaleString("en-US", { maximumFractionDigits: 2 });
  }
}

export interface KPIEvalResult {
  value: string;
  ok: boolean;
}

/**
 * Recompute a KPI's value from a filtered subset of rows.
 *
 * Returns ok=true when the recipe + data are sufficient to produce a value.
 * Returns ok=false (and value="—") when the recipe is missing, the column is
 * absent / empty, or the data type doesn't match the recipe kind.
 *
 * Spec 2 of Gap 2: count-only for chart aggregation, but KPIs use proper
 * numeric recompute (mean, rate, top-cat, comparison split, CV).
 */
export function evaluateKPI(
  recipe: KPIRecipe | null | undefined,
  rows: Record<string, unknown>[],
): KPIEvalResult {
  if (!recipe) return { value: "—", ok: false };
  if (rows.length === 0) return { value: "—", ok: false };

  switch (recipe.kind) {
    case "row_count":
      return { value: rows.length.toLocaleString("en-US"), ok: true };

    case "target_rate": {
      if (!recipe.column) return { value: "—", ok: false };
      let pos = 0;
      let tot = 0;
      for (const r of rows) {
        const b = toBinary(r[recipe.column]);
        if (b == null) continue;
        tot += 1;
        if (b === 1) pos += 1;
      }
      if (tot === 0) return { value: "—", ok: false };
      return { value: `${((pos / tot) * 100).toFixed(1)}%`, ok: true };
    }

    case "metric_avg": {
      if (!recipe.column) return { value: "—", ok: false };
      const vals = numeric(rows, recipe.column);
      if (vals.length === 0) return { value: "—", ok: false };
      const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
      return { value: formatValue(mean, recipe.format_kind), ok: true };
    }

    case "top_cat": {
      if (!recipe.column) return { value: "—", ok: false };
      const counts = new Map<string, number>();
      for (const r of rows) {
        const v = r[recipe.column];
        if (v == null || v === "") continue;
        const k = String(v);
        counts.set(k, (counts.get(k) ?? 0) + 1);
      }
      if (counts.size === 0) return { value: "—", ok: false };
      let topKey = "";
      let topN = -1;
      for (const [k, n] of counts) {
        if (n > topN) {
          topKey = k;
          topN = n;
        }
      }
      return { value: topKey, ok: true };
    }

    case "comparison_split": {
      if (!recipe.target_column || !recipe.segment_column) {
        return { value: "—", ok: false };
      }
      const segs = new Map<string, { pos: number; tot: number }>();
      for (const r of rows) {
        const seg = r[recipe.segment_column];
        const b = toBinary(r[recipe.target_column]);
        if (seg == null || seg === "" || b == null) continue;
        const k = String(seg);
        const e = segs.get(k) ?? { pos: 0, tot: 0 };
        e.tot += 1;
        if (b === 1) e.pos += 1;
        segs.set(k, e);
      }
      if (segs.size === 0) return { value: "—", ok: false };
      let topKey = "";
      let topRate = -1;
      for (const [k, e] of segs) {
        if (e.tot === 0) continue;
        const rate = e.pos / e.tot;
        if (rate > topRate) {
          topKey = k;
          topRate = rate;
        }
      }
      if (topRate < 0) return { value: "—", ok: false };
      return {
        value: `${topKey}: ${(topRate * 100).toFixed(1)}%`,
        ok: true,
      };
    }

    case "highest_cv": {
      if (!recipe.column) return { value: "—", ok: false };
      const vals = numeric(rows, recipe.column);
      if (vals.length < 2) return { value: "—", ok: false };
      const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
      if (Math.abs(mean) < 1e-12) return { value: "—", ok: false };
      const variance =
        vals.reduce((s, v) => s + (v - mean) ** 2, 0) / vals.length;
      const cv = Math.sqrt(variance) / Math.abs(mean);
      return { value: formatValue(cv, "percent"), ok: true };
    }
  }
}
```

- [ ] **Step 2: Verify the frontend compiles**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI/frontend && npm run build 2>&1 | tail -10`
Expected: `✓ Compiled successfully`. The utility is unused at this point — Task 7 wires it.

- [ ] **Step 3: Smoke-check via a one-off tsx eval**

```bash
cd /Users/uvnikhil/Desktop/AnalystAI/frontend && npx tsx -e "
import { evaluateKPI } from './src/lib/evaluateKPI';
const rows = [
  { Survived: 1, Age: 22 },
  { Survived: 0, Age: 38 },
  { Survived: 1, Age: 26 },
  { Survived: 1, Age: 35 },
];
console.log('row_count:', evaluateKPI({ kind: 'row_count' } as any, rows));
console.log('target_rate:', evaluateKPI({ kind: 'target_rate', column: 'Survived' } as any, rows));
console.log('metric_avg:', evaluateKPI({ kind: 'metric_avg', column: 'Age', format_kind: 'number' } as any, rows));
" 2>&1 | tail -5
```

Expected:
- `row_count: { value: '4', ok: true }`
- `target_rate: { value: '75.0%', ok: true }`
- `metric_avg: { value: '30.25', ok: true }` (mean of 22, 38, 26, 35 = 30.25)

If `tsx` isn't installed, skip this step — `npm run build` already typechecks.

- [ ] **Step 4: Commit**

```bash
cd /Users/uvnikhil/Desktop/AnalystAI
git add frontend/src/lib/evaluateKPI.ts
git commit -m "feat(kpi): evaluateKPI utility — pure recomputation from filtered rows"
```

---

## Task 6: `KPICard` extension — `filteredValue` + delta render

**Files:**
- Modify: `frontend/src/components/KPICard.tsx`

- [ ] **Step 1: Read the current `KPICard`**

Run: `cat /Users/uvnikhil/Desktop/AnalystAI/frontend/src/components/KPICard.tsx`

Note the current Props interface (likely `{ label, value, context?, icon? }` — see what's actually there).

- [ ] **Step 2: Extend Props and render**

Update `frontend/src/components/KPICard.tsx`. The Props interface gains two new optional fields:

```tsx
interface Props {
  label: string;
  value: string;                      // overall value (existing — keep)
  context?: string;                   // existing — keep
  filteredValue?: string | null;      // NEW
  hasActiveFilters?: boolean;         // NEW
  // ...preserve any other existing props (icon, etc.)
}
```

In the component's return JSX, compute the delta logic and update the rendering:

```tsx
const showDelta =
  hasActiveFilters && filteredValue != null && filteredValue !== value;
const primary = showDelta ? filteredValue : value;

return (
  <div className="...existing card classes...">
    {/* preserve existing label / icon section */}
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

Preserve every other styling concern and any existing icon / accent treatment — Spec 2 only changes the value rendering and adds the delta subline.

- [ ] **Step 3: Verify the frontend compiles**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI/frontend && npm run build 2>&1 | tail -10`
Expected: `✓ Compiled successfully`.

- [ ] **Step 4: Commit**

```bash
cd /Users/uvnikhil/Desktop/AnalystAI
git add frontend/src/components/KPICard.tsx
git commit -m "feat(kpi-card): optional filteredValue + delta subline"
```

---

## Task 7: `KPISection` orchestration — call `evaluateKPI` per card

**Files:**
- Modify: `frontend/src/components/KPISection.tsx`

- [ ] **Step 1: Read the current `KPISection`**

Run: `cat /Users/uvnikhil/Desktop/AnalystAI/frontend/src/components/KPISection.tsx`

Note: the component is server-component-friendly today (no `"use client"`) and receives `briefing` via props. Adding hooks (`useMemo`, `useDashboardData`, `useDashboardFilter`) requires marking it `"use client"`.

- [ ] **Step 2: Update `KPISection.tsx`**

Add `"use client";` at the top. Replace the body with hook-driven logic:

```tsx
"use client";

import { useMemo } from "react";
import { useDashboardData } from "@/context/DashboardDataContext";
import {
  useDashboardFilter,
  applyFilters,
} from "@/context/DashboardFilterContext";
import { evaluateKPI } from "@/lib/evaluateKPI";
import KPICard from "./KPICard";
import type { BriefingData, KPI } from "@/types";

interface Props {
  briefing: BriefingData | null | undefined;
}

export default function KPISection({ briefing }: Props) {
  const { rows } = useDashboardData();
  const { filters } = useDashboardFilter();
  const filteredRows = useMemo(
    () => applyFilters(rows, filters),
    [rows, filters],
  );
  const hasActiveFilters = Object.keys(filters).length > 0;

  const kpis: KPI[] = briefing?.kpis ?? [];

  if (kpis.length === 0) {
    return null;
  }

  const cols = Math.min(Math.max(kpis.length, 1), 5);

  return (
    <div className={`grid gap-3 grid-cols-1 sm:grid-cols-2 lg:grid-cols-${cols} mb-4`}>
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
    </div>
  );
}
```

**IMPORTANT:** Preserve any existing fallback rendering the current file does for old sessions (e.g., the "Older sessions persisted before PROMPT-2 may not have kpis" branch at line 28 of the existing file — keep that empty-state if present). Adapt the snippet above to whatever structure already exists. The minimum changes are: add the hooks at the top, pass `filteredValue` + `hasActiveFilters` to each `<KPICard />`.

The exact import path for `KPICard` (`./KPICard` vs `"@/components/KPICard"`) and whether it's a default vs named export should match the existing file's pattern — don't change those.

- [ ] **Step 3: Verify the frontend compiles**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI/frontend && npm run build 2>&1 | tail -10`
Expected: `✓ Compiled successfully`.

- [ ] **Step 4: Commit**

```bash
cd /Users/uvnikhil/Desktop/AnalystAI
git add frontend/src/components/KPISection.tsx
git commit -m "feat(kpi-section): orchestrate evaluateKPI per card; pass filtered values"
```

---

## Task 8: Install shadcn primitives (slider, popover, calendar)

**Files:**
- New: `frontend/src/components/ui/slider.tsx`
- New: `frontend/src/components/ui/popover.tsx`
- New: `frontend/src/components/ui/calendar.tsx`
- Modify: `frontend/package.json` (auto-updated by shadcn CLI)
- Modify: `frontend/package-lock.json` (auto-updated)

- [ ] **Step 1: Install the three primitives via shadcn CLI**

Run:

```bash
cd /Users/uvnikhil/Desktop/AnalystAI/frontend
npx shadcn@latest add slider popover calendar
```

The CLI prompts may ask about overwrite / configure. Accept defaults; the project's `components.json` already has the path conventions set up (per the existing button.tsx).

This should:
- Create the three `src/components/ui/` files.
- Add `@radix-ui/react-slider`, `@radix-ui/react-popover`, `react-day-picker` to `package.json` dependencies.
- Update `package-lock.json` accordingly.

If the CLI fails or asks unexpected questions, STOP and report — don't manually create the files.

- [ ] **Step 2: Verify the new primitives exist**

Run: `ls /Users/uvnikhil/Desktop/AnalystAI/frontend/src/components/ui/`
Expected: `button.tsx calendar.tsx popover.tsx slider.tsx` (in some order).

- [ ] **Step 3: Verify the frontend compiles**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI/frontend && npm run build 2>&1 | tail -10`
Expected: `✓ Compiled successfully`. The new primitives are unused at this point — Task 9 wires them.

- [ ] **Step 4: Commit**

```bash
cd /Users/uvnikhil/Desktop/AnalystAI
git add frontend/src/components/ui/slider.tsx frontend/src/components/ui/popover.tsx frontend/src/components/ui/calendar.tsx frontend/package.json frontend/package-lock.json
git commit -m "chore(ui): install shadcn slider, popover, calendar primitives"
```

---

## Task 9: `FilterSidebar` component

**Files:**
- Create: `frontend/src/components/FilterSidebar.tsx`

- [ ] **Step 1: Create the file**

Create `frontend/src/components/FilterSidebar.tsx`:

```tsx
"use client";

import { useMemo, useState } from "react";
import { useDashboardData } from "@/context/DashboardDataContext";
import {
  useDashboardFilter,
  type FilterValue,
} from "@/context/DashboardFilterContext";
import { Slider } from "@/components/ui/slider";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Calendar } from "@/components/ui/calendar";
import type { DateRange } from "react-day-picker";

type SetFilter = (key: string, value: FilterValue | null) => void;

interface ControlProps {
  col: string;
  rows: Record<string, unknown>[];
  filter: FilterValue | undefined;
  setFilter: SetFilter;
}

// ── Sidebar root ──────────────────────────────────────────────────────────────

export function FilterSidebar() {
  const { columnRoles, rows, status } = useDashboardData();
  const { filters, setFilter } = useDashboardFilter();

  if (status !== "ok" || !columnRoles || rows.length === 0) return null;

  const binary = columnRoles.binary_cols ?? [];
  const lowCard = (columnRoles.low_card_cat ?? []).slice(0, 8);
  const continuous = (columnRoles.continuous ?? []).slice(0, 3);
  const datetime = columnRoles.datetime_cols ?? [];

  const total =
    binary.length + lowCard.length + continuous.length + datetime.length;
  if (total === 0) return null;

  return (
    <aside className="w-60 flex-shrink-0 px-4 py-3 border-r border-zinc-200 space-y-5">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
        Filters
      </h3>
      {binary.map((col) => (
        <BinaryToggle
          key={col}
          col={col}
          rows={rows}
          filter={filters[col]}
          setFilter={setFilter}
        />
      ))}
      {lowCard.map((col) => (
        <PillSelect
          key={col}
          col={col}
          rows={rows}
          filter={filters[col]}
          setFilter={setFilter}
        />
      ))}
      {continuous.map((col) => (
        <RangeSliderControl
          key={col}
          col={col}
          rows={rows}
          filter={filters[col]}
          setFilter={setFilter}
        />
      ))}
      {datetime.map((col) => (
        <DateRangeControl
          key={col}
          col={col}
          rows={rows}
          filter={filters[col]}
          setFilter={setFilter}
        />
      ))}
    </aside>
  );
}

// ── BinaryToggle ──────────────────────────────────────────────────────────────

function BinaryToggle({ col, rows, filter, setFilter }: ControlProps) {
  const values = useMemo(() => {
    const seen = new Set<string>();
    for (const r of rows) {
      const v = r[col];
      if (v == null || v === "") continue;
      seen.add(String(v));
      if (seen.size >= 2) break;
    }
    return Array.from(seen);
  }, [rows, col]);

  if (values.length < 2) return null;

  const current =
    filter?.kind === "equality" ? String(filter.value) : null;

  function pick(val: string | null) {
    if (val === null) setFilter(col, null);
    else setFilter(col, { kind: "equality", value: val });
  }

  return (
    <div className="space-y-1.5">
      <p className="text-xs font-medium text-zinc-700">{col}</p>
      <div className="flex flex-wrap gap-1">
        <button
          type="button"
          onClick={() => pick(null)}
          className={`text-[11px] px-2 py-0.5 rounded-full border ${
            current === null
              ? "bg-indigo-600 border-indigo-600 text-white"
              : "border-zinc-300 text-zinc-700 hover:bg-zinc-100"
          }`}
        >
          All
        </button>
        {values.map((v) => (
          <button
            key={v}
            type="button"
            onClick={() => pick(v)}
            className={`text-[11px] px-2 py-0.5 rounded-full border ${
              current === v
                ? "bg-indigo-600 border-indigo-600 text-white"
                : "border-zinc-300 text-zinc-700 hover:bg-zinc-100"
            }`}
          >
            {v}
          </button>
        ))}
      </div>
    </div>
  );
}

// ── PillSelect ────────────────────────────────────────────────────────────────

function PillSelect({ col, rows, filter, setFilter }: ControlProps) {
  const values = useMemo(() => {
    const seen = new Set<string>();
    for (const r of rows) {
      const v = r[col];
      if (v == null || v === "") continue;
      seen.add(String(v));
      if (seen.size >= 12) break;
    }
    return Array.from(seen).sort();
  }, [rows, col]);

  if (values.length === 0) return null;

  const selected = useMemo(() => {
    if (filter?.kind === "in") return new Set(filter.values.map(String));
    return new Set<string>();
  }, [filter]);

  function toggle(v: string) {
    const next = new Set(selected);
    next.has(v) ? next.delete(v) : next.add(v);
    if (next.size === 0) setFilter(col, null);
    else setFilter(col, { kind: "in", values: Array.from(next) });
  }

  return (
    <div className="space-y-1.5">
      <p className="text-xs font-medium text-zinc-700">{col}</p>
      <div className="flex flex-wrap gap-1">
        {values.map((v) => {
          const isSel = selected.has(v);
          return (
            <button
              key={v}
              type="button"
              onClick={() => toggle(v)}
              className={`text-[11px] px-2 py-0.5 rounded-full border ${
                isSel
                  ? "bg-indigo-600 border-indigo-600 text-white"
                  : "border-zinc-300 text-zinc-700 hover:bg-zinc-100"
              }`}
            >
              {v}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ── RangeSliderControl ────────────────────────────────────────────────────────

function RangeSliderControl({ col, rows, filter, setFilter }: ControlProps) {
  const extent = useMemo(() => {
    let mn = Number.POSITIVE_INFINITY;
    let mx = Number.NEGATIVE_INFINITY;
    for (const r of rows) {
      const v = r[col];
      if (v == null) continue;
      const n = typeof v === "number" ? v : Number(v);
      if (!Number.isFinite(n)) continue;
      if (n < mn) mn = n;
      if (n > mx) mx = n;
    }
    return Number.isFinite(mn) && Number.isFinite(mx) ? [mn, mx] : null;
  }, [rows, col]);

  if (!extent) return null;
  const [mn, mx] = extent;
  if (mn === mx) return null;

  const current =
    filter?.kind === "range" ? [filter.min, filter.max] : [mn, mx];

  function commit([lo, hi]: number[]) {
    if (lo <= mn && hi >= mx) setFilter(col, null);
    else setFilter(col, { kind: "range", min: lo, max: hi });
  }

  return (
    <div className="space-y-1.5">
      <p className="text-xs font-medium text-zinc-700">{col}</p>
      <p className="text-[11px] text-zinc-500">
        {current[0]} – {current[1]}
      </p>
      <Slider
        min={mn}
        max={mx}
        step={(mx - mn) / 100 || 1}
        value={current}
        onValueChange={commit}
      />
    </div>
  );
}

// ── DateRangeControl ──────────────────────────────────────────────────────────

function DateRangeControl({ col, rows, filter, setFilter }: ControlProps) {
  const extent = useMemo(() => {
    let mn = Number.POSITIVE_INFINITY;
    let mx = Number.NEGATIVE_INFINITY;
    for (const r of rows) {
      const v = r[col];
      if (v == null) continue;
      const t = new Date(String(v)).getTime();
      if (!Number.isFinite(t) || isNaN(t)) continue;
      if (t < mn) mn = t;
      if (t > mx) mx = t;
    }
    if (!Number.isFinite(mn) || !Number.isFinite(mx)) return null;
    return { from: new Date(mn), to: new Date(mx) };
  }, [rows, col]);

  const [open, setOpen] = useState(false);

  if (!extent) return null;

  const selected: DateRange | undefined =
    filter?.kind === "date_range"
      ? { from: new Date(filter.from), to: new Date(filter.to) }
      : undefined;

  const label = selected?.from && selected?.to
    ? `${selected.from.toISOString().slice(0, 10)} → ${selected.to.toISOString().slice(0, 10)}`
    : "Any date";

  return (
    <div className="space-y-1.5">
      <p className="text-xs font-medium text-zinc-700">{col}</p>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button
            type="button"
            className="text-[11px] px-2 py-1 rounded border border-zinc-300 text-zinc-700 hover:bg-zinc-100 w-full text-left"
          >
            {label}
          </button>
        </PopoverTrigger>
        <PopoverContent className="w-auto p-0">
          <Calendar
            mode="range"
            selected={selected}
            onSelect={(range) => {
              if (range?.from && range?.to) {
                setFilter(col, {
                  kind: "date_range",
                  from: range.from.toISOString().slice(0, 10),
                  to: range.to.toISOString().slice(0, 10),
                });
              }
            }}
            fromDate={extent.from}
            toDate={extent.to}
          />
          <div className="flex justify-end p-2 border-t border-zinc-200">
            <button
              type="button"
              onClick={() => {
                setFilter(col, null);
                setOpen(false);
              }}
              className="text-[11px] text-zinc-600 hover:text-zinc-900"
            >
              Clear
            </button>
          </div>
        </PopoverContent>
      </Popover>
    </div>
  );
}
```

If `react-day-picker`'s `DateRange` type isn't where the import expects, `grep -r "DateRange" /Users/uvnikhil/Desktop/AnalystAI/frontend/node_modules/react-day-picker/dist/ | head -3` to find the correct path. The shadcn-installed `Calendar` typically re-exports `DateRange`; if so, import from `@/components/ui/calendar` instead.

- [ ] **Step 2: Verify the frontend compiles**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI/frontend && npm run build 2>&1 | tail -15`
Expected: `✓ Compiled successfully`. The sidebar is unused at this point — Task 10 wires it.

If TypeScript flags issues with `<Slider />` or `<Popover />` prop shapes (shadcn versions occasionally vary), check the relevant `src/components/ui/*.tsx` for the actual prop signatures and adapt the call sites accordingly.

- [ ] **Step 3: Commit**

```bash
cd /Users/uvnikhil/Desktop/AnalystAI
git add frontend/src/components/FilterSidebar.tsx
git commit -m "feat(dashboard): FilterSidebar with 4 auto-built control kinds"
```

---

## Task 10: Wire `FilterSidebar` into `AutoDashboard` layout

**Files:**
- Modify: `frontend/src/components/AutoDashboard.tsx`

- [ ] **Step 1: Add the import**

In `frontend/src/components/AutoDashboard.tsx`, add to the import block:

```tsx
import { FilterSidebar } from "@/components/FilterSidebar";
```

- [ ] **Step 2: Wrap the existing content in a flex container**

Find the existing JSX root of the dashboard body (where the KPI section + chart grid render — after the status gates). Wrap that content in a flex container with the sidebar on the left:

```tsx
return (
  <div className="flex gap-4">
    <FilterSidebar />
    <div className="flex-1 min-w-0">
      {/* existing content: KPISection, "Showing N of M records" indicator, chart grid */}
    </div>
  </div>
);
```

If the existing root has more than one top-level JSX element (a header + main content, etc.), only wrap the chart-area portion in the flex layout — keep the header / above-the-fold content outside the flex container.

- [ ] **Step 3: Verify the frontend compiles**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI/frontend && npm run build 2>&1 | tail -10`
Expected: `✓ Compiled successfully`.

- [ ] **Step 4: Commit**

```bash
cd /Users/uvnikhil/Desktop/AnalystAI
git add frontend/src/components/AutoDashboard.tsx
git commit -m "feat(dashboard): wire FilterSidebar into AutoDashboard layout"
```

---

## Verification Gate

Before declaring Spec 2 done, confirm:

### Auto-verifiable

- `cd /Users/uvnikhil/Desktop/AnalystAI && uv run pytest tests/ -v` → 25 passed (no regressions).
- `cd /Users/uvnikhil/Desktop/AnalystAI/frontend && npm run build` → compiled successfully; all routes static.
- Backend boots clean:
  ```bash
  pkill -f "uvicorn api.main" 2>/dev/null; sleep 1
  cd /Users/uvnikhil/Desktop/AnalystAI
  uv run uvicorn api.main:app --port 8797 > /tmp/uv-spec2-gate.log 2>&1 &
  UV=$!
  sleep 7
  curl -s -o /dev/null -w "/health: %{http_code}\n" http://localhost:8797/health
  curl -s -o /dev/null -w "/dashboard/latest/data (no auth): %{http_code}\n" http://localhost:8797/dashboard/latest/data
  kill $UV 2>/dev/null
  wait $UV 2>/dev/null
  grep -iE "error|attributeerror|importerror" /tmp/uv-spec2-gate.log | grep -vE "PostgreSQL unavailable|Qdrant" | head -5 || echo "(no unexpected errors)"
  ```
  Expected: `/health: 200`, `/dashboard/latest/data: 401`, no unexpected errors.

### Manual UI smoke (real auth + a completed analysis session)

| Action | Expected |
|---|---|
| Open `/dashboard` after running OVERVIEW on any dataset | Sidebar appears on the left; KPI cards render as before (no visual change with no filters); chart grid present. |
| Click a pill in the sidebar's `low_card_cat` block | Pill fills indigo; chart grid re-renders with filtered rows; "Showing N of M records" appears above the chart grid; KPI cards swap to filtered values + "vs X overall" subline. |
| Click the same pill again | Pill un-fills; filter clears; KPI cards return to overall; "Showing N of M records" disappears. |
| Drag a range slider | Range filter applies live; charts + KPIs re-render with each value change. |
| Drag slider back to full extent | Filter clears entirely (range filter with min/max matching extent → null). |
| Open a date-range picker | Calendar popover appears; selecting a range writes the filter; "Clear" button inside the popover clears it. |
| Combine multiple sidebar + cross-filter filters | AND semantics across columns; "Showing X of Y" reflects the cumulative subset; KPI values shift to that subset. |
| Click an existing chart bar (cross-filter, Spec 1 behavior) | Filter chip appears; KPI delta + sidebar still work alongside; clicking the same bar again clears that filter. |
| Older session (`briefing.kpis` lacking `recipe`) | KPI cards show overall values only — no delta even with filters. No crash. |
| Dataset with no low_card_cat / continuous / datetime / binary columns | Sidebar returns null; layout collapses to single column. |

### Spec coverage check

- Spec §3 (Backend: `KPIRecipe` + `KPI.recipe` + populate) → Task 1.
- Spec §4 (Filter state unification: `FilterValue`, `setFilter`, `applyFilters`, chip rendering) → Tasks 3, 4.
- Spec §5.1 (Frontend `KPIRecipe` mirror) → Task 2.
- Spec §5.2 (`evaluateKPI` utility) → Task 5.
- Spec §5.3 (`KPICard` extension) → Task 6.
- Spec §5.4 (`KPISection` orchestration) → Task 7.
- Spec §6.3 (Install shadcn primitives) → Task 8.
- Spec §6.1–6.2 (`FilterSidebar` component + sub-controls) → Task 9.
- Spec §6.4 (Layout integration) → Task 10.
- Spec §7 (Verification) → this gate.
- Spec §1 / §5.5 / §6.5 (Non-goals — sum/mean for charts, mobile, importance ranking, persistence) — explicitly preserved as non-goals; no tasks created.
