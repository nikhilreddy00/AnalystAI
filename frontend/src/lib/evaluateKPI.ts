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
