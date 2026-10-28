"use client";

// PROMPT-2: KPI strip is now backend-driven. The visualization agent runs
// `generate_smart_kpis(df, column_roles, query)` and ships pre-formatted hero
// cards on briefing.kpis. This component just renders them.

import { useMemo } from "react";
import KPICard, { KPIStrip } from "./KPICard";
import type { BriefingData, KPI } from "@/types";
import { useDashboardData } from "@/context/DashboardDataContext";
import { useDashboardFilter, applyFilters } from "@/context/DashboardFilterContext";
import { evaluateKPI } from "@/lib/evaluateKPI";

interface Props {
  briefing: BriefingData;
}

export default function KPISection({ briefing }: Props) {
  const kpis: KPI[] = briefing.kpis ?? [];

  const { rows } = useDashboardData();
  const { filters } = useDashboardFilter();
  const filteredRows = useMemo(
    () => applyFilters(rows, filters),
    [rows, filters],
  );
  const hasActiveFilters = Object.keys(filters).length > 0;

  if (kpis.length === 0) {
    // Older sessions persisted before PROMPT-2 may not have kpis. Show a
    // minimum-viable row count card so the dashboard still has a hero strip.
    const recordCount = Object.values(briefing.describe_stats || {})
      .map((s) => {
        const v = (s as Record<string, unknown>).count;
        return typeof v === "number" ? v : 0;
      })
      .reduce((a, b) => Math.max(a, b), 0);
    return (
      <KPIStrip count={1}>
        <KPICard label="Records" value={recordCount.toLocaleString()} sublabel="Total rows in dataset" />
      </KPIStrip>
    );
  }

  return (
    <KPIStrip count={kpis.length}>
      {kpis.map((kpi, i) => {
        const evalResult = evaluateKPI(kpi.recipe, filteredRows);
        const filteredValue = evalResult.ok ? evalResult.value : null;
        return (
          <KPICard
            key={`${kpi.label}-${i}`}
            label={kpi.label}
            value={kpi.value}
            sublabel={kpi.context || undefined}
            filteredValue={filteredValue}
            hasActiveFilters={hasActiveFilters}
          />
        );
      })}
    </KPIStrip>
  );
}
