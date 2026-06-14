"use client";

// PROMPT-2: KPI strip is now backend-driven. The visualization agent runs
// `generate_smart_kpis(df, column_roles, query)` and ships pre-formatted hero
// cards on briefing.kpis. This component just renders them.

import { useMemo } from "react";
import { Activity, BarChart3, Database, GitCompare, TrendingUp } from "lucide-react";
import type { ComponentType } from "react";
import KPICard from "./KPICard";
import type { BriefingData, KPI } from "@/types";
import { useDashboardData } from "@/context/DashboardDataContext";
import { useDashboardFilter, applyFilters } from "@/context/DashboardFilterContext";
import { evaluateKPI } from "@/lib/evaluateKPI";

interface Props {
  briefing: BriefingData;
}

const _ACCENTS = ["#4E79A7", "#F28E2B", "#76B7B2", "#59A14F", "#B07AA1"];
const _ICONS: ComponentType<{ className?: string }>[] = [
  Database,
  TrendingUp,
  Activity,
  BarChart3,
  GitCompare,
];

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
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4">
        <KPICard
          label="Records"
          value={recordCount.toLocaleString()}
          sublabel="Total rows in dataset"
          Icon={Database}
          accent={_ACCENTS[0]}
        />
      </div>
    );
  }

  const cols = Math.min(Math.max(kpis.length, 1), 5);
  const lgCols: Record<number, string> = {
    1: "lg:grid-cols-1",
    2: "lg:grid-cols-2",
    3: "lg:grid-cols-3",
    4: "lg:grid-cols-4",
    5: "lg:grid-cols-5",
  };

  return (
    <div className={`grid grid-cols-1 sm:grid-cols-2 ${lgCols[cols]} gap-4`}>
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
            Icon={_ICONS[i % _ICONS.length]}
            accent={_ACCENTS[i % _ACCENTS.length]}
          />
        );
      })}
    </div>
  );
}
