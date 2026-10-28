"use client";

// Phase A: compact card for targeted intents (correlation, top-N, distribution, etc).
// Rendered in the report canvas. Chart + optional table + per-chart pin button.
// Fallback path renders unanswerable_reason + clickable suggestion buttons that
// send the suggestion back through the chat input via onFollowUp.

import ChartRenderer from "./ChartRenderer";
import TableRenderer from "./TableRenderer";
import PinButton from "./PinButton";
import type { TargetedResponse } from "@/types";

interface Props {
  response: TargetedResponse;
  sessionId: string | null;
  onFollowUp: (q: string) => void;
  /** Prefills the chat box (a chart's "Change variables…"). */
  onDraft?: (q: string) => void;
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

export default function TargetedResponsePanel({ response, sessionId, onFollowUp, onDraft }: Props) {
  const isUnanswerable = !!response.unanswerable_reason;

  if (isUnanswerable) {
    return (
      <div className="w-full rounded-md border border-border border-l-[3px] border-l-warning bg-card p-4 space-y-3">
        <h3 className="text-sm font-medium text-foreground">Couldn&apos;t answer that one</h3>
        <p className="text-sm text-foreground leading-relaxed">{response.unanswerable_reason}</p>
        {response.suggestions.length > 0 && (
          <div className="pt-1 space-y-2">
            <p className="text-xs uppercase tracking-wider text-muted-foreground font-semibold">Try one of these</p>
            <div className="flex flex-wrap gap-1.5">
              {response.suggestions.map((s, i) => (
                <button
                  key={i}
                  onClick={() => onFollowUp(s)}
                  type="button"
                  className="text-left text-xs text-foreground bg-card hover:bg-accent border border-border rounded-full px-3 py-1.5"
                >
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
        <span className="text-[11px] uppercase tracking-wider text-muted-foreground font-semibold">
          {INTENT_LABEL[response.intent]}
        </span>
      </div>

      {response.chart_configs.map((chart) => (
        <ChartRenderer
          key={chart.id}
          config={chart}
          height={240}
          sessionId={sessionId}
          onDraft={onDraft}
          action={(current) => sessionId && <PinButton sessionId={sessionId} chart={current} />}
        />
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
