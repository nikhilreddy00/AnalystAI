"use client";

// D5: persists the user's dashboard layout in localStorage.
// `order`  = explicit chart-id ordering (charts not listed fall to the end).
// `hidden` = chart ids the user has hidden from the layout (still in the pool, just not rendered).

import { useCallback, useState } from "react";

export interface DashboardLayout {
  order: string[];
  hidden: string[];
}

const STORAGE_KEY = "analystai_dashboard_layout";
const EMPTY: DashboardLayout = { order: [], hidden: [] };

function read(): DashboardLayout {
  if (typeof window === "undefined") return EMPTY;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return EMPTY;
    const parsed = JSON.parse(raw);
    return {
      order: Array.isArray(parsed.order) ? parsed.order : [],
      hidden: Array.isArray(parsed.hidden) ? parsed.hidden : [],
    };
  } catch {
    return EMPTY;
  }
}

function write(layout: DashboardLayout) {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(layout));
  } catch {
    // localStorage may be unavailable in some environments (private mode, quota) —
    // failing silently is fine, layout just won't persist across reloads.
  }
}

export function useDashboardLayout() {
  const [layout, setLayoutState] = useState<DashboardLayout>(read);

  const update = useCallback((next: DashboardLayout) => {
    setLayoutState(next);
    write(next);
  }, []);

  const moveUp = useCallback((id: string, visibleOrder: string[]) => {
    const idx = visibleOrder.indexOf(id);
    if (idx <= 0) return;
    const next = [...visibleOrder];
    [next[idx - 1], next[idx]] = [next[idx], next[idx - 1]];
    update({ order: next, hidden: layout.hidden });
  }, [layout.hidden, update]);

  const moveDown = useCallback((id: string, visibleOrder: string[]) => {
    const idx = visibleOrder.indexOf(id);
    if (idx < 0 || idx >= visibleOrder.length - 1) return;
    const next = [...visibleOrder];
    [next[idx], next[idx + 1]] = [next[idx + 1], next[idx]];
    update({ order: next, hidden: layout.hidden });
  }, [layout.hidden, update]);

  const hide = useCallback((id: string, visibleOrder: string[]) => {
    update({
      order: visibleOrder.filter((x) => x !== id),
      hidden: layout.hidden.includes(id) ? layout.hidden : [...layout.hidden, id],
    });
  }, [layout.hidden, update]);

  const restore = useCallback((id: string, visibleOrder: string[]) => {
    update({
      order: [...visibleOrder, id],
      hidden: layout.hidden.filter((x) => x !== id),
    });
  }, [layout.hidden, update]);

  const reset = useCallback(() => update(EMPTY), [update]);

  return { layout, moveUp, moveDown, hide, restore, reset };
}

// Given a chart pool, compute the visible order (explicit order first, then new charts
// in their original sequence) and the hidden charts.
export function resolveLayout<T extends { id: string }>(
  pool: T[],
  layout: DashboardLayout,
): { visible: T[]; hidden: T[]; visibleOrder: string[] } {
  const byId = new Map(pool.map((c) => [c.id, c]));
  const hiddenSet = new Set(layout.hidden);

  const orderedExplicit = layout.order
    .map((id) => byId.get(id))
    .filter((c): c is T => !!c && !hiddenSet.has(c.id));

  const explicitIds = new Set(layout.order);
  const orderedNew = pool.filter((c) => !explicitIds.has(c.id) && !hiddenSet.has(c.id));

  const visible = [...orderedExplicit, ...orderedNew];
  const hidden = pool.filter((c) => hiddenSet.has(c.id));

  return { visible, hidden, visibleOrder: visible.map((c) => c.id) };
}
