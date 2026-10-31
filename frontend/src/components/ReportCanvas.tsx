"use client";

// Phase 3.5 (direction "Workbench"): the right half of /chat. Shows one assistant
// message's deliverables as a report — Summary, Evidence, Data, Review — while the
// chat column on the left drives it.

import { useId, useRef, useState } from "react";
import { Download, FileText, RefreshCw } from "lucide-react";
import BriefingPanel from "./BriefingPanel";
import ChartRenderer from "./ChartRenderer";
import EvidencePanel from "./EvidencePanel";
import ExpertReviewPanel from "./ExpertReviewPanel";
import InsightPanel from "./InsightPanel";
import KPICard, { KPIStrip } from "./KPICard";
import PinButton from "./PinButton";
import TargetedResponsePanel from "./TargetedResponsePanel";
import { downloadLatestReportPptx, downloadReportPptx, downloadThreadMarkdown } from "@/lib/api";
import type { ChatMessage } from "@/types";

type Tab = "summary" | "evidence" | "data" | "review";

interface Props {
  message: ChatMessage;
  question: string;
  sessionId: string | null;
  /** Only the newest report has a server-side PowerPoint. */
  isLatest: boolean;
  onFollowUp: (q: string) => void;
  /** Prefills the chat box (a chart's "Change variables…"). */
  onDraft?: (q: string) => void;
  /** "Re-run on current data" (a stale report): a new report beside this one. */
  onRerun?: () => void;
  /** A thread's title by id (the earlier analyses a report consulted). */
  titleFor?: (sessionId: string) => string | null;
}

function day(iso: string | null | undefined) {
  return iso ? new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "";
}

export function hasReport(m: ChatMessage) {
  return !m.isStreaming && !!(m.insight || m.charts?.length || m.targetedResponse || m.briefing || m.evidence?.length);
}

export default function ReportCanvas({ message, question, sessionId, isLatest, onFollowUp, onDraft, onRerun, titleFor }: Props) {
  const delivered = message.delivered;
  // Phase 5: row-count / profile charts describe the table, so they live in the Data tab.
  const summaryCharts = (message.charts ?? []).filter((c) => c.placement !== "data");
  const dataCharts = (message.charts ?? []).filter((c) => c.placement === "data");
  const [evidenceId, setEvidenceId] = useState<string | null>(null);
  const flagCount = message.expertReview?.flags?.length ?? 0;
  const tabs: { id: Tab; label: string }[] = [
    { id: "summary" as const, label: "Summary" },
    ...(message.evidence?.length ? [{ id: "evidence" as const, label: `Evidence · ${message.evidence.length}` }] : []),
    ...(message.briefing || dataCharts.length ? [{ id: "data" as const, label: "Data" }] : []),
    ...(message.expertReview
      ? [{ id: "review" as const, label: `Review${flagCount ? ` · ${flagCount} flagged` : ""}` }]
      : []),
  ];
  const [tab, setTab] = useState<Tab>("summary");
  const [exportError, setExportError] = useState(false);
  const active = tabs.some((t) => t.id === tab) ? tab : "summary";
  const ids = useId();
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});

  function onTabKey(e: React.KeyboardEvent) {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const i = tabs.findIndex((t) => t.id === active);
    const next = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
    setTab(next.id);
    tabRefs.current[next.id]?.focus();
  }

  async function exportWith(fn: () => Promise<void>) {
    setExportError(false);
    try {
      await fn();
    } catch {
      setExportError(true);
    }
  }
  // A stored report exports itself; a live one is the thread's latest on the server.
  const pptx = delivered ? () => downloadReportPptx(delivered.runId) : isLatest ? downloadLatestReportPptx : null;
  const memory = message.insight?.memory_used ?? [];

  const kpis = message.briefing?.kpis ?? [];
  const hasEvidence = !!message.evidence?.length;

  function openEvidence(id: string) {
    setEvidenceId(id);
    setTab("evidence");
  }

  const chartCard = (chart: (typeof summaryCharts)[number]) => (
    <ChartRenderer
      key={chart.id}
      config={chart}
      // A delivered report never changes: switching a chart type on it stays on screen only.
      sessionId={delivered ? null : sessionId}
      onEvidence={hasEvidence ? openEvidence : undefined}
      onDraft={onDraft}
      action={(current) => sessionId && <PinButton sessionId={sessionId} chart={current} />}
    />
  );

  return (
    <article className="mx-auto w-full max-w-5xl space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <p className="text-xs text-muted-foreground">Report</p>
          <h2 className="text-lg font-semibold leading-snug text-foreground text-balance">{question || "Analysis"}</h2>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {exportError && <span className="text-xs text-destructive">Export failed. Try again.</span>}
          {pptx && message.insight && (
            <button
              type="button"
              onClick={() => void exportWith(pptx)}
              className="inline-flex items-center gap-1.5 rounded-md border border-border bg-card px-2.5 py-1.5 text-sm text-foreground hover:bg-accent"
            >
              <Download className="h-3.5 w-3.5" />
              PowerPoint
            </button>
          )}
          {sessionId && !message.isStreaming && (
            <button
              type="button"
              onClick={() => void exportWith(() => downloadThreadMarkdown(sessionId))}
              title="Every question in this analysis, with its evidence"
              className="inline-flex items-center gap-1.5 rounded-md border border-border bg-card px-2.5 py-1.5 text-sm text-foreground hover:bg-accent"
            >
              <FileText className="h-3.5 w-3.5" />
              Markdown
            </button>
          )}
        </div>
      </header>

      {delivered?.stale && (
        <div role="status" className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-sm">
          <span className="text-foreground">The data has changed since this report. It shows the data as it was then.</span>
          {onRerun && (
            <button
              type="button"
              onClick={onRerun}
              className="inline-flex items-center gap-1.5 rounded-md border border-border bg-card px-2.5 py-1 text-xs text-foreground hover:bg-accent"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Re-run on current data
            </button>
          )}
        </div>
      )}
      {delivered && !delivered.intact && (
        <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          This stored report doesn&apos;t match the seal recorded when it was delivered.
        </p>
      )}

      {tabs.length > 1 && (
        <div role="tablist" aria-label="Report sections" className="flex gap-1 overflow-x-auto overflow-y-hidden border-b border-border [scrollbar-width:none]" onKeyDown={onTabKey}>
          {tabs.map((t) => (
            <button
              key={t.id}
              ref={(el) => {
                tabRefs.current[t.id] = el;
              }}
              type="button"
              role="tab"
              id={`${ids}-${t.id}`}
              aria-selected={active === t.id}
              aria-controls={`${ids}-panel`}
              tabIndex={active === t.id ? 0 : -1}
              onClick={() => setTab(t.id)}
              className={`-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm ${
                active === t.id
                  ? "border-primary font-medium text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      )}

      <div role="tabpanel" id={`${ids}-panel`} aria-labelledby={`${ids}-${active}`} className="space-y-4">
        {active === "summary" && (
          <>
            {kpis.length > 0 && (
              <KPIStrip count={kpis.length}>
                {kpis.map((k, i) => (
                  <KPICard key={`${k.label}-${i}`} label={k.label} value={k.value} sublabel={k.context || undefined} how={k.how} />
                ))}
              </KPIStrip>
            )}
            {message.targetedResponse && (
              <TargetedResponsePanel response={message.targetedResponse} sessionId={sessionId} onFollowUp={onFollowUp} onDraft={onDraft} />
            )}
            {message.insight && (
              <div className="rounded-lg border border-border bg-card p-4 sm:p-5">
                <InsightPanel
                  report={message.insight}
                  onEvidence={hasEvidence ? openEvidence : undefined}
                  flags={message.expertReview?.flags}
                  onFlag={() => setTab("review")}
                />
              </div>
            )}
            {memory.length > 0 && (
              <p className="text-xs text-muted-foreground">
                Also considered {memory.length === 1 ? "an earlier analysis" : `${memory.length} earlier analyses`} of this dataset:{" "}
                {memory.map((m, i) => (
                  <span key={m.session_id}>
                    {i > 0 && "; "}
                    {titleFor?.(m.session_id) ?? "a deleted analysis"} ({day(m.generated_at)})
                  </span>
                ))}
                . They are context, not evidence.
              </p>
            )}
            {summaryCharts.length > 0 && (
              <div className="grid gap-3 xl:grid-cols-2">{summaryCharts.map(chartCard)}</div>
            )}
            {delivered && (
              <p className="text-xs text-muted-foreground" data-testid="provenance">
                Delivered {day(delivered.provenance.finished_at)} · {delivered.provenance.model ?? "model unknown"} ·{" "}
                {delivered.provenance.llm_calls} model calls · ${delivered.provenance.cost_usd.toFixed(4)}
                {delivered.reportSha256 && <> · sha256 {delivered.reportSha256.slice(0, 12)}</>}
              </p>
            )}
          </>
        )}
        {active === "evidence" && message.evidence && (
          <EvidencePanel items={message.evidence} openId={evidenceId} onClosed={() => setEvidenceId(null)} />
        )}
        {active === "data" && (
          <>
            {message.briefing && <BriefingPanel data={message.briefing} />}
            {dataCharts.length > 0 && (
              <section aria-label="Data profile charts" className="space-y-2">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Rows by category</h3>
                <div className="grid gap-3 xl:grid-cols-2">{dataCharts.map(chartCard)}</div>
              </section>
            )}
          </>
        )}
        {active === "review" && message.expertReview && (
          <ExpertReviewPanel review={message.expertReview} onEvidence={hasEvidence ? openEvidence : undefined} />
        )}
      </div>
    </article>
  );
}
