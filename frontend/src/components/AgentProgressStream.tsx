"use client";

// C3: per-stage Lucide icons + Tableau 10 tinting + targeted-mode awareness.
// The strip auto-switches between the full 7-agent pipeline and the 3-step
// targeted shortcut so the user sees the actual run, not a phantom skeleton.

import type { ComponentType } from "react";
import {
  BarChart3,
  Brush,
  Database,
  GraduationCap,
  Lightbulb,
  PieChart,
  Search,
  Target,
} from "lucide-react";
import type { AgentEvent } from "@/types";

interface AgentMeta {
  key: string;
  label: string;
  Icon: ComponentType<{ className?: string }>;
  tint: string;     // Tableau 10 — matches landing page pipeline colors
}

const FULL_PIPELINE: AgentMeta[] = [
  { key: "ingestion",     label: "Load",      Icon: Database,       tint: "#F28E2B" },
  { key: "intake",        label: "Understand", Icon: Search,        tint: "#4E79A7" },
  { key: "cleaning",      label: "Clean",     Icon: Brush,          tint: "#E15759" },
  { key: "analysis",      label: "Analyze",   Icon: BarChart3,      tint: "#76B7B2" },
  { key: "visualization", label: "Visualize", Icon: PieChart,       tint: "#59A14F" },
  { key: "insight",       label: "Insight",   Icon: Lightbulb,      tint: "#EDC948" },
  { key: "quality_check", label: "Review",    Icon: GraduationCap,  tint: "#B07AA1" },
];

const TARGETED_PIPELINE: AgentMeta[] = [
  { key: "ingestion",          label: "Load",       Icon: Database, tint: "#F28E2B" },
  { key: "intake",             label: "Understand", Icon: Search,   tint: "#4E79A7" },
  { key: "targeted_analysis",  label: "Answer",     Icon: Target,   tint: "#76B7B2" },
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
    <div className="rounded-2xl border border-zinc-700/40 bg-zinc-900/60 backdrop-blur-sm p-4 space-y-3 shadow-lg max-w-2xl">
      {/* Step indicators */}
      <div className="flex items-center gap-1 flex-wrap">
        {agents.map(({ key, label, Icon, tint }, idx) => {
          const done = completedAgents.has(key);
          const active = activeAgent === key;
          return (
            <div key={key} className="flex items-center gap-1">
              <div
                className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium transition-all duration-300 border ${
                  done
                    ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/30"
                    : active
                    ? "border-transparent shadow-sm"
                    : "bg-zinc-800/60 text-zinc-500 border-zinc-700/30"
                }`}
                style={
                  active
                    ? { backgroundColor: `${tint}20`, color: tint, borderColor: `${tint}40` }
                    : undefined
                }
              >
                <Icon className={`w-3.5 h-3.5 ${active ? "animate-pulse" : ""}`} />
                <span>{label}</span>
                {done && <span className="text-emerald-400">✓</span>}
                {active && <span className="w-1 h-1 rounded-full bg-current animate-ping" />}
              </div>
              {idx < agents.length - 1 && (
                <span className={`text-xs ${done ? "text-emerald-600" : "text-zinc-700"}`}>›</span>
              )}
            </div>
          );
        })}
      </div>

      {/* Active message */}
      {activeMessage && (
        <div className="flex items-center gap-2 text-xs text-zinc-400">
          <span className="w-1.5 h-1.5 rounded-full bg-indigo-400 animate-pulse flex-shrink-0" />
          <span>{activeMessage}</span>
        </div>
      )}

      {/* Quality-check retry banner */}
      {retryMessage && (
        <div className="flex items-start gap-2 text-xs text-amber-300 bg-amber-500/10 border border-amber-500/20 rounded-lg px-2.5 py-1.5">
          <span className="text-amber-400 flex-shrink-0">↻</span>
          <span>{retryMessage}</span>
        </div>
      )}
    </div>
  );
}
