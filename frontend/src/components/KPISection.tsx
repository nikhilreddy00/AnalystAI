"use client";

// PROMPT-2: KPI strip is backend-driven. The visualization agent runs
// `generate_smart_kpis(df, column_roles, query)` and ships pre-formatted hero
// cards on briefing.kpis. Under filters every value is the server's recomputation
// (POST /dashboard/latest/filtered): the browser never computes a number.

import KPICard, { KPIStrip } from "./KPICard";
import type { BriefingData, KPI } from "@/types";
import { useDashboardData } from "@/context/DashboardDataContext";
import { useDashboardFilter } from "@/context/DashboardFilterContext";
import { useFilteredDashboard } from "@/hooks/useFilteredDashboard";
import { tileView } from "@/lib/filteredView";

interface Props {
  briefing: BriefingData;
  sessionId?: string;
}

export default function KPISection({ briefing, sessionId }: Props) {
  const kpis: KPI[] = briefing.kpis ?? [];

  const { rowCount } = useDashboardData();
  const { filters } = useDashboardFilter();
  const hasActiveFilters = Object.keys(filters).length > 0;
  const { data: server, failed } = useFilteredDashboard(filters, sessionId);
  const loading = hasActiveFilters && !server && !failed;
  // why a filtered tile has no number of its own
  const missing = server?.suppressed ? "Fewer than 11 records: not shown (privacy)"
    : failed ? "Couldn't recompute for these filters" : "Not available for these filters";

  if (kpis.length === 0) {
    // Older sessions persisted before PROMPT-2 may not have kpis: a records card, the server's count.
    const filtered = server?.suppressed ? null : server?.rows.toLocaleString() ?? null;
    return (
      <KPIStrip count={1}>
        <KPICard label="Records" value={rowCount.toLocaleString()}
          sublabel={hasActiveFilters && !loading && filtered == null ? missing : "Total rows in dataset"}
          filteredValue={filtered} hasActiveFilters={hasActiveFilters} loading={loading} />
      </KPIStrip>
    );
  }

  return (
    <KPIStrip count={kpis.length}>
      {kpis.map((kpi, i) => {
        const filteredValue = tileView(kpi.label, hasActiveFilters, server);
        return (
          <KPICard
            key={`${kpi.label}-${i}`}
            label={kpi.label}
            value={kpi.value}
            sublabel={hasActiveFilters && !loading && filteredValue == null
              ? [missing, kpi.context].filter(Boolean).join(" · ")
              : kpi.context || undefined}
            filteredValue={filteredValue}
            hasActiveFilters={hasActiveFilters}
            loading={loading}
          />
        );
      })}
    </KPIStrip>
  );
}
