"use client";

import type { BriefingData } from "@/types";
import DataPreviewTable from "./briefing/DataPreviewTable";
import SchemaQualityTable from "./briefing/SchemaQualityTable";
import DescriptiveStatsTable from "./briefing/DescriptiveStatsTable";

interface Props {
  data: BriefingData;
}

export default function BriefingPanel({ data }: Props) {
  const {
    head_preview,
    dtypes_summary,
    missing_pct,
    describe_stats,
    pre_cleaning_missing_pct,
    unique_counts,
    sample_values_per_col,
    skewness,
    target_correlations,
    target_col,
    column_roles_summary,
  } = data;

  // Estimate total rows from describe_stats[col].count — handy for the
  // "missing #" column on the schema-quality table.
  const totalRows = (() => {
    for (const stats of Object.values(describe_stats || {})) {
      const v = (stats as Record<string, unknown>)?.count;
      if (typeof v === "number" && Number.isFinite(v) && v > 0) return v;
    }
    return undefined;
  })();

  const hasAnything =
    (head_preview?.length ?? 0) > 0 ||
    Object.keys(dtypes_summary || {}).length > 0 ||
    Object.keys(describe_stats || {}).length > 0;
  if (!hasAnything) return null;

  return (
    <div className="w-full space-y-3">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Dataset briefing</h3>

      {head_preview && head_preview.length > 0 && (
        <DataPreviewTable rows={head_preview} dtypes={dtypes_summary || {}} />
      )}

      {Object.keys(dtypes_summary || {}).length > 0 && (
        <SchemaQualityTable
          dtypes={dtypes_summary}
          missingPct={missing_pct || {}}
          preCleaningMissingPct={pre_cleaning_missing_pct}
          uniqueCounts={unique_counts}
          sampleValues={sample_values_per_col}
          totalRows={totalRows}
          columnRoles={column_roles_summary}
        />
      )}

      {Object.keys(describe_stats || {}).length > 0 && (
        <DescriptiveStatsTable
          describe={describe_stats}
          skewness={skewness}
          columnRoles={column_roles_summary}
          targetCorrelations={target_correlations}
          targetCol={target_col || undefined}
        />
      )}
    </div>
  );
}
