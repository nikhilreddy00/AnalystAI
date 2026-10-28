"use client";

// PROMPT-10 (2): "Schema & data quality" — name | dtype | missing # | missing %
// | unique count | 3 sample values. Pre/post cleaning missing % shown side by
// side when they differ. Missing % cell color-coded; sortable by missing %
// desc (default), name, or original order.

import { useMemo, useState } from "react";
import { AlertTriangle } from "lucide-react";

// Role → badge visual config.
const ROLE_BADGE: Record<string, { label: string; cls: string }> = {
  id:            { label: "id",           cls: "bg-muted text-muted-foreground border-border" },
  binary:        { label: "binary",       cls: "bg-brand-soft text-primary border-primary/30" },
  continuous:    { label: "continuous",   cls: "bg-brand-soft text-primary border-primary/30" },
  datetime:      { label: "datetime",     cls: "bg-brand-soft text-primary border-primary/30" },
  low_card_cat:  { label: "low-card cat", cls: "bg-warning/10 text-warning border-warning/30" },
  high_card_cat: { label: "high-card cat",cls: "bg-warning/10 text-warning border-warning/30" },
  ordinal_cols:  { label: "ordinal",      cls: "bg-brand-soft text-primary border-primary/30" },
  ordinal:       { label: "ordinal",      cls: "bg-brand-soft text-primary border-primary/30" },
  id_cols:       { label: "id",           cls: "bg-muted text-muted-foreground border-border" },
  datetime_cols: { label: "datetime",     cls: "bg-brand-soft text-primary border-primary/30" },
  skip_cols:     { label: "skipped",      cls: "bg-muted text-muted-foreground border-border" },
  skipped:       { label: "skipped",      cls: "bg-muted text-muted-foreground border-border" },
};

interface Props {
  dtypes: Record<string, string>;
  missingPct: Record<string, number>;                   // post-cleaning
  preCleaningMissingPct?: Record<string, number>;       // optional
  uniqueCounts?: Record<string, number>;
  sampleValues?: Record<string, unknown[]>;
  totalRows?: number;                                   // for "missing #" = pct * total / 100
  columnRoles?: Record<string, string>;                 // column → role label from column_roles_summary
}

type SortKey = "missing-desc" | "alpha" | "original";

function missingTint(pct: number): { bg: string; text: string; icon: boolean } {
  if (pct === 0)       return { bg: "bg-success/10",  text: "text-success", icon: false };
  if (pct <= 10)       return { bg: "bg-warning/10",   text: "text-warning",  icon: false };
  if (pct <= 40)       return { bg: "bg-warning/10",   text: "text-warning",  icon: false };
  return                      { bg: "bg-destructive/10",     text: "text-destructive",     icon: true };
}

function previewValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") {
    return v.length > 24 ? v.slice(0, 24) + "…" : v;
  }
  if (typeof v === "number") {
    return Number.isInteger(v) ? v.toLocaleString() : v.toFixed(2);
  }
  return String(v);
}

export default function SchemaQualityTable({
  dtypes,
  missingPct,
  preCleaningMissingPct,
  uniqueCounts = {},
  sampleValues = {},
  totalRows,
  columnRoles = {},
}: Props) {
  const [sortKey, setSortKey] = useState<SortKey>("missing-desc");

  const originalOrder = useMemo(() => Object.keys(dtypes), [dtypes]);
  const hasPreCleaning =
    preCleaningMissingPct &&
    Object.keys(preCleaningMissingPct).some((k) => (preCleaningMissingPct![k] ?? 0) !== (missingPct[k] ?? 0));

  const rows = useMemo(() => {
    const base = originalOrder.map((col, i) => ({
      idx: i,
      column: col,
      dtype: dtypes[col] ?? "",
      missingPct: missingPct[col] ?? 0,
      preMissingPct: preCleaningMissingPct?.[col],
      missingCount: totalRows != null ? Math.round(((missingPct[col] ?? 0) / 100) * totalRows) : null,
      unique: uniqueCounts[col],
      samples: sampleValues[col] ?? [],
    }));
    const sorted = [...base];
    if (sortKey === "missing-desc") {
      sorted.sort((a, b) => b.missingPct - a.missingPct);
    } else if (sortKey === "alpha") {
      sorted.sort((a, b) => a.column.localeCompare(b.column));
    }
    return sorted;
  }, [originalOrder, dtypes, missingPct, preCleaningMissingPct, uniqueCounts, sampleValues, totalRows, sortKey]);

  if (rows.length === 0) return null;

  return (
    <div className="rounded-2xl border border-border bg-card p-4 space-y-2 shadow-sm">
      <div className="flex items-baseline justify-between gap-3">
        <h4 className="text-foreground text-sm font-semibold">Schema &amp; data quality</h4>
        <div className="flex items-center gap-2 text-xs">
          <span className="text-muted-foreground">{rows.length} column{rows.length === 1 ? "" : "s"}</span>
          <span className="text-muted-foreground">·</span>
          <label className="text-muted-foreground">Sort:</label>
          <select
            value={sortKey}
            onChange={(e) => setSortKey(e.target.value as SortKey)}
            className="text-xs bg-muted border border-border rounded-md px-1.5 py-0.5 text-foreground focus:outline-none focus:ring-1 focus:ring-primary/40"
          >
            <option value="missing-desc">Missing % ↓</option>
            <option value="alpha">A–Z</option>
            <option value="original">Original order</option>
          </select>
        </div>
      </div>

      <div className="overflow-x-auto -mx-1 px-1">
        <table className="w-full text-xs border-collapse">
          <thead>
            <tr className="border-b border-border text-left text-muted-foreground">
              <th className="px-2 py-1.5 font-medium">Column</th>
              <th className="px-2 py-1.5 font-medium">Dtype</th>
              {totalRows != null && <th className="px-2 py-1.5 font-medium text-right">Missing #</th>}
              <th className="px-2 py-1.5 font-medium text-right">
                {hasPreCleaning ? "Missing % (before → after)" : "Missing %"}
              </th>
              <th className="px-2 py-1.5 font-medium text-right">Unique</th>
              <th className="px-2 py-1.5 font-medium">Samples</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const tint = missingTint(r.missingPct);
              return (
                <tr key={r.column} className={i % 2 === 1 ? "bg-muted" : ""}>
                  <td className="px-2 py-1.5 text-foreground font-mono whitespace-nowrap">
                    <span className="inline-flex items-center gap-1.5">
                      {r.column}
                      {(() => {
                        const role = columnRoles[r.column];
                        const badge = role ? ROLE_BADGE[role] : undefined;
                        if (!badge) return null;
                        return (
                          <span className={`inline-block rounded border px-1 py-px text-[10px] font-sans font-medium leading-none ${badge.cls}`}>
                            {badge.label}
                          </span>
                        );
                      })()}
                    </span>
                  </td>
                  <td className="px-2 py-1.5 text-muted-foreground font-mono text-[11px] whitespace-nowrap">{r.dtype}</td>
                  {totalRows != null && (
                    <td className="px-2 py-1.5 text-right text-foreground font-mono whitespace-nowrap">
                      {r.missingCount != null ? r.missingCount.toLocaleString() : "—"}
                    </td>
                  )}
                  <td className={`px-2 py-1.5 text-right font-mono whitespace-nowrap ${tint.bg} ${tint.text}`}>
                    <span className="inline-flex items-center justify-end gap-1">
                      {tint.icon && <AlertTriangle className="w-3 h-3" />}
                      {hasPreCleaning && r.preMissingPct !== undefined && r.preMissingPct !== r.missingPct ? (
                        <>
                          <span className="text-muted-foreground">{r.preMissingPct.toFixed(1)}%</span>
                          <span className="text-muted-foreground">→</span>
                          <span>{r.missingPct.toFixed(1)}%</span>
                        </>
                      ) : (
                        <span>{r.missingPct.toFixed(1)}%</span>
                      )}
                    </span>
                  </td>
                  <td className="px-2 py-1.5 text-right text-foreground font-mono whitespace-nowrap">
                    {r.unique != null ? r.unique.toLocaleString() : "—"}
                  </td>
                  <td className="px-2 py-1.5 text-muted-foreground whitespace-nowrap max-w-[28ch] overflow-hidden text-ellipsis">
                    {r.samples.length > 0
                      ? r.samples.map(previewValue).join(", ")
                      : <span className="text-muted-foreground">—</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
