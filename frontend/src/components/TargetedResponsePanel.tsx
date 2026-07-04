"use client";

// Phase A: compact card for targeted intents (correlation, top-N, distribution, etc).
// Rendered below the assistant bubble. Chart + optional table + per-chart pin button.
// Fallback path renders unanswerable_reason + clickable suggestion buttons that
// send the suggestion back through the chat input via onFollowUp.

import ChartRenderer from "./ChartRenderer";
import TableRenderer from "./TableRenderer";
import { pinChart } from "@/lib/api";
import type { TargetedResponse } from "@/types";

interface Props {
  response: TargetedResponse;
  sessionId: string | null;
  onFollowUp: (q: string) => void;
}

const INTENT_LABEL: Record<TargetedResponse["intent"], string> = {
  overview: "Overview",
  deep_dive: "Deep dive",
  correlation: "Correlation",
  distribution: "Distribution",
  top_n: "Top-N",
  time_series: "Time series",
  segment_compare: "Segment comparison",
  hypothesis_test: "Hypothesis test",
  ab_significance: "A/B significance",
  outlier_scan: "Outlier scan",
};

export default function TargetedResponsePanel({ response, sessionId, onFollowUp }: Props) {
  const isUnanswerable = !!response.unanswerable_reason;

  if (isUnanswerable) {
    return (
      <div className="w-full rounded-2xl border border-amber-200 bg-gradient-to-br from-amber-50 to-white p-5 space-y-3 shadow-sm">
        <div className="flex items-center gap-2 text-amber-700 text-xs font-semibold uppercase tracking-wider">
          <span className="text-base leading-none">⚠</span>
          <span>Couldn&apos;t answer that one</span>
        </div>
        <p className="text-sm text-zinc-800 leading-relaxed">{response.unanswerable_reason}</p>
        {response.suggestions.length > 0 && (
          <div className="pt-1 space-y-2">
            <p className="text-[11px] uppercase tracking-wide text-amber-700/80 font-semibold">Try one of these</p>
            <div className="flex flex-col gap-1.5">
              {response.suggestions.map((s, i) => (
                <button
                  key={i}
                  onClick={() => onFollowUp(s)}
                  className="text-left text-sm text-zinc-800 bg-white hover:bg-amber-50 border border-amber-200 hover:border-amber-300 transition-colors rounded-lg px-3 py-2 group"
                >
                  <span className="text-amber-600 mr-2 group-hover:text-amber-700">→</span>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="w-full space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-[10px] uppercase tracking-wide text-indigo-700 bg-indigo-50 border border-indigo-200 rounded-full px-2 py-0.5">
          {INTENT_LABEL[response.intent]}
        </span>
      </div>

      {response.chart_configs.map((chart) => (
        <div key={chart.id} className="relative group/chart">
          <ChartRenderer config={chart} height={240} />
          {sessionId && (
            <button
              onClick={() => pinChart(sessionId, chart as unknown as object)}
              className="absolute top-3 right-3 opacity-0 group-hover/chart:opacity-100 transition-all duration-200 text-xs bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 text-white px-3 py-1.5 rounded-lg shadow-lg shadow-indigo-500/30 font-medium"
            >
              📌 Pin to Dashboard
            </button>
          )}
        </div>
      ))}

      {/* TableRenderer now has its own light card chrome — no outer wrapper needed */}
      {response.table_data.length > 0 && response.table_columns.length > 0 && (
        <TableRenderer
          rows={response.table_data}
          columns={response.table_columns}
          maxRows={15}
        />
      )}
    </div>
  );
}
