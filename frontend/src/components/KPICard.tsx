"use client";

// One cell of a KPI strip (see KPIStrip): label, big tabular value, one line of context.
// When dashboard filters are active the filtered value leads and the overall one follows.

import type { ReactNode } from "react";

interface Props {
  label: string;
  value: ReactNode;
  sublabel?: string;
  filteredValue?: string | null;
  hasActiveFilters?: boolean;
  /** Phase 5: how the number was computed, in words. */
  how?: string;
}

export default function KPICard({ label, value, sublabel, filteredValue, hasActiveFilters, how }: Props) {
  const showDelta = hasActiveFilters && filteredValue != null && filteredValue !== value;
  const primary = showDelta ? filteredValue : value;

  return (
    <div className="bg-card px-4 py-3 flex flex-col gap-0.5 min-w-0">
      <span className="text-xs text-muted-foreground truncate">{label}</span>
      <span className="font-mono text-xl font-medium tabular-nums tracking-tight text-foreground truncate">
        {primary}
      </span>
      {showDelta ? (
        <span className="text-xs text-muted-foreground">vs {value} overall</span>
      ) : (
        sublabel && <span className="text-xs text-muted-foreground truncate">{sublabel}</span>
      )}
      {how && (
        <span className="text-[11px] leading-snug text-muted-foreground" title={how}>
          {how}
        </span>
      )}
    </div>
  );
}

/** Cells joined by 1px gaps; 2 columns on phones. */
export function KPIStrip({ children, count }: { children: ReactNode; count: number }) {
  const lg = ["", "lg:grid-cols-1", "lg:grid-cols-2", "lg:grid-cols-3", "lg:grid-cols-4", "lg:grid-cols-5"][
    Math.min(Math.max(count, 1), 5)
  ];
  return (
    <div className={`grid grid-cols-2 ${lg} gap-px overflow-hidden rounded-md border border-border bg-border`}>
      {children}
    </div>
  );
}
