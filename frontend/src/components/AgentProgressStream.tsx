"use client";

// Vertical checklist of pipeline stages (Phase 3.5: fits the narrow chat column).
// Auto-switches between the full 7-agent pipeline and the 3-step targeted
// shortcut so the user sees the actual run, not a phantom skeleton.

import { Check } from "lucide-react";
import type { AgentEvent } from "@/types";

interface AgentMeta {
  key: string;
  label: string;
}

const FULL_PIPELINE: AgentMeta[] = [
  { key: "ingestion", label: "Load data" },
  { key: "intake", label: "Understand question" },
  { key: "cleaning", label: "Clean" },
  { key: "analysis", label: "Analyze" },
  { key: "visualization", label: "Build charts" },
  { key: "insight", label: "Write report" },
  { key: "quality_check", label: "Review" },
];

const TARGETED_PIPELINE: AgentMeta[] = [
  { key: "ingestion", label: "Load data" },
  { key: "intake", label: "Understand question" },
  { key: "targeted_analysis", label: "Answer question" },
];

interface Props {
  events: AgentEvent[];
  isLoading: boolean;
}

export default function AgentProgressStream({ events, isLoading }: Props) {
  if (!isLoading && events.length === 0) return null;

  // Detect mode: if a targeted_analysis event has fired, switch to the 3-step strip.
  const isTargetedRun = events.some(
    (e) => (e.type === "agent_start" || e.type === "agent_complete") && e.agent === "targeted_analysis"
  );
  const agents = isTargetedRun ? TARGETED_PIPELINE : FULL_PIPELINE;

  const activeAgent = [...events].reverse().find((e) => e.type === "agent_start")?.agent;
  const completedAgents = new Set(
    events.filter((e) => e.type === "agent_complete").map((e) => e.agent),
  );
  const activeMessage = [...events].reverse().find(
    (e) => e.type === "agent_start" || e.type === "agent_complete",
  )?.message;

  // Quality-retry banner — only relevant on full-pipeline runs.
  const lastInsightCompleteIdx = events.findLastIndex(
    (e) => e.type === "agent_complete" && e.agent === "insight",
  );
  const lastQualityRetryIdx = events.findLastIndex((e) => e.type === "quality_retry");
  const retryEvent = events[lastQualityRetryIdx];
  const retryMessage =
    lastQualityRetryIdx > lastInsightCompleteIdx && retryEvent?.type === "quality_retry"
      ? retryEvent.message
      : undefined;

  return (
    <div
      className="rounded-lg border border-border bg-card p-3 space-y-2.5"
      role="status"
      aria-live="polite"
    >
      <ol className="space-y-1.5 text-sm">
        {agents.map(({ key, label }) => {
          const done = completedAgents.has(key);
          const active = !done && activeAgent === key;
          return (
            <li
              key={key}
              className={`flex items-center gap-2.5 ${done || active ? "text-foreground" : "text-muted-foreground"}`}
            >
              <span
                className={`flex h-4 w-4 flex-shrink-0 items-center justify-center rounded-full border-[1.5px] ${
                  done
                    ? "border-success bg-success text-card"
                    : active
                    ? "border-primary border-t-transparent animate-spin"
                    : "border-border"
                }`}
                aria-hidden
              >
                {done && <Check className="h-2.5 w-2.5" strokeWidth={3} />}
              </span>
              <span>{label}</span>
              <span className="sr-only">{done ? "done" : active ? "running" : "waiting"}</span>
            </li>
          );
        })}
      </ol>

      {activeMessage && (
        <p className="text-xs text-muted-foreground border-t border-border pt-2">{activeMessage}</p>
      )}

      {retryMessage && (
        <p className="text-xs text-foreground bg-warning/15 border border-warning/40 rounded-md px-2.5 py-1.5">
          {retryMessage}
        </p>
      )}
    </div>
  );
}
