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
