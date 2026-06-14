"use client";

// D2: Power BI–style KPI card. Big value on top of a 1-line label, optional
// trend delta + Lucide icon. Light theme only (lives on the light dashboard).

import type { ComponentType, ReactNode } from "react";

interface Props {
  label: string;
  value: ReactNode;
  sublabel?: string;
  Icon?: ComponentType<{ className?: string }>;
  accent?: string;  // hex; tints the icon chip — defaults to indigo
  filteredValue?: string | null;
  hasActiveFilters?: boolean;
}

export default function KPICard({ label, value, sublabel, Icon, accent = "#4f46e5", filteredValue, hasActiveFilters }: Props) {
  const showDelta =
    hasActiveFilters && filteredValue != null && filteredValue !== value;
  const primary = showDelta ? filteredValue : value;

  return (
    <div className="rounded-2xl border border-zinc-200 bg-white p-5 shadow-sm hover:border-zinc-300 transition-colors flex flex-col gap-2 min-h-[120px] justify-between">
      <div className="flex items-start justify-between gap-2">
        <span className="text-[11px] font-semibold text-zinc-500 uppercase tracking-wider leading-tight">
          {label}
        </span>
        {Icon && (
          <div
            className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
            style={{ backgroundColor: `${accent}15`, color: accent }}
          >
            <Icon className="w-4 h-4" />
          </div>
        )}
      </div>
      <div className="space-y-0.5">
        <div className="text-2xl font-bold tracking-tight text-zinc-900 truncate">{primary}</div>
        {showDelta && (
          <p className="text-[11px] text-zinc-500">vs {value} overall</p>
        )}
        {!showDelta && sublabel && <p className="text-[11px] text-zinc-500 truncate">{sublabel}</p>}
      </div>
    </div>
  );
}
